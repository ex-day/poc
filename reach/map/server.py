"""到達圏の地図を手元で見るための小さなサーバー（Issue #40）。

使い方：
  python reach/map/server.py            # http://127.0.0.1:8040/ を開く
  EXDAY_DSN=postgresql://... python reach/map/server.py --port 8040

- GET /                         … index.html（片道で行ける範囲の地図。#40）
- GET /route                    … route.html（帰り道の寄り道の地図。#42）
- GET /api/reach?from=横浜&max=60 … reach.reach_map_json の結果（帯の面・駅・場所の GeoJSON）と、かかった時間
- GET /api/detour?from=新大阪&to=横浜&avail=300
                                … reach.detour_map_json の結果。avail は使える時間（分）。to を省くと from に戻る（往復）。
                                  avail の代わりに extra（寄り道に使う時間）でもよい。使える時間 ＝ まっすぐ行く時間 ＋ extra
- GET /api/stations?q=横         … 駅の名前の候補（入力の補完用）

先に reach/sql/11_isochrone.sql と 12_detour.sql を流しておくこと。標準ライブラリの http.server と psycopg だけで動く（PoC 用。本番の作りではない）。
"""
import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psycopg

DSN = os.environ.get("EXDAY_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/ex_day_poc")
HERE = Path(__file__).resolve().parent


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if url.path in ("/route", "/route.html"):
            return self._send(200, (HERE / "route.html").read_bytes(), "text/html; charset=utf-8")
        if url.path == "/api/detour":
            return self._detour(qs)
        if url.path == "/api/reach":
            return self._reach(qs)
        if url.path == "/api/stations":
            return self._stations(qs)
        return self._send(404, json.dumps({"error": "not found"}))

    def _reach(self, qs):
        origin = (qs.get("from") or ["横浜"])[0].strip()
        try:
            max_min = float((qs.get("max") or ["60"])[0])
        except ValueError:
            return self._send(400, json.dumps({"error": "max は数で指定する"}, ensure_ascii=False))
        max_min = max(15.0, min(max_min, 90.0))
        t0 = time.perf_counter()
        try:
            with psycopg.connect(DSN) as conn, conn.cursor() as cur:
                cur.execute("SELECT reach.reach_map_json(%s, %s)", (origin, max_min))
                result = cur.fetchone()[0]
        except psycopg.Error as e:
            msg = (e.diag.message_primary if getattr(e, "diag", None) else None) or str(e)
            return self._send(400, json.dumps({"error": msg}, ensure_ascii=False))
        result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
        self._send(200, json.dumps(result, ensure_ascii=False))

    def _detour(self, qs):
        origin = (qs.get("from") or ["新大阪"])[0].strip()
        dest = (qs.get("to") or [""])[0].strip() or origin   # 着く駅を省いたら、起点に戻る（往復）
        try:
            avail = float(qs["avail"][0]) if qs.get("avail") else None
            extra = float(qs["extra"][0]) if qs.get("extra") else 60.0
        except ValueError:
            return self._send(400, json.dumps({"error": "avail・extra は数で指定する"}, ensure_ascii=False))
        t0 = time.perf_counter()
        try:
            with psycopg.connect(DSN) as conn, conn.cursor() as cur:
                # まっすぐ行く時間（経路の最後の駅の t1）。往復なら0
                cur.execute("SELECT max(t1_min) FROM reach.detour_route(%s, %s)", (origin, dest))
                min_total = cur.fetchone()[0]
                if min_total is None:
                    return self._send(400, json.dumps({"error": "経路が見つからない"}, ensure_ascii=False))
                min_total = float(min_total)
                if avail is None:
                    avail = min_total + max(0.0, extra)
                avail = min(avail, 720.0)
                if avail < min_total:
                    return self._send(400, json.dumps(
                        {"error": f"まっすぐ行っても {round(min_total)}分かかる（使える時間 {round(avail)}分）",
                         "min_total": min_total}, ensure_ascii=False))
                cur.execute("SELECT reach.detour_map_json(%s, %s, %s)", (origin, dest, avail))
                result = cur.fetchone()[0]
        except psycopg.Error as e:
            msg = (e.diag.message_primary if getattr(e, "diag", None) else None) or str(e)
            return self._send(400, json.dumps({"error": msg}, ensure_ascii=False))
        result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
        self._send(200, json.dumps(result, ensure_ascii=False, default=float))

    def _stations(self, qs):
        q = (qs.get("q") or [""])[0].strip()
        if not q:
            return self._send(200, "[]")
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT station_name FROM reach.station WHERE station_name LIKE %s "
                "ORDER BY station_name LIMIT 20",
                (q + "%",),
            )
            names = [r[0] for r in cur.fetchall()]
        self._send(200, json.dumps(names, ensure_ascii=False))

    def log_message(self, fmt, *args):
        print("%s %s" % (self.log_date_time_string(), fmt % args))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8040)
    p.add_argument("--host", default="127.0.0.1")
    a = p.parse_args()
    print(f"http://{a.host}:{a.port}/ を開く（止めるのは Ctrl+C）")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
