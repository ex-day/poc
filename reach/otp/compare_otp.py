"""pgRouting（#5・#23）と OpenTripPlanner（仮の GTFS・OSM）を比べ、results/otp_compare.md に書く（Issue #7）。

使い方（OTP を起動してから）：
  python compare_otp.py

比べること：
  A. 電車（行き）：新横浜から各駅まで。OTP の所要時間と reach.reachable の t1
  B. 電車（帰り）：各駅から東京に 13:00 に着くまで（arriveBy）。OTP の所要時間と t2
  C. 歩き：駅から場所まで。OTP（OSM、歩きだけ）と reach.station_spot_walk（N13）

OTP の GraphQL（GTFS API）の plan を使う。接続先は環境変数 EXDAY_OTP（既定 http://127.0.0.1:8080/otp/gtfs/v1）。
"""
import json
import os
import random
import statistics
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

import psycopg

DSN = os.environ.get("EXDAY_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/ex_day_poc")
OTP = os.environ.get("EXDAY_OTP", "http://127.0.0.1:8080/otp/gtfs/v1")
HERE = Path(__file__).resolve().parent
DATE = "2026-11-10"   # 平日（仮の時刻表は毎日同じ）

QUERY = """
query($from: InputCoordinates!, $to: InputCoordinates!, $date: String!, $time: String!,
      $arriveBy: Boolean!, $modes: [TransportMode]) {
  plan(from: $from, to: $to, date: $date, time: $time, arriveBy: $arriveBy,
       transportModes: $modes, numItineraries: 3) {
    itineraries { duration walkTime waitingTime
                  legs { mode duration distance from { name } to { name } route { shortName longName } } }
  }
}
"""
TRANSIT = [{"mode": "TRANSIT"}, {"mode": "WALK"}]
WALK = [{"mode": "WALK"}]


def otp_plan(frm, to, t, arrive_by, modes):
    body = json.dumps({"query": QUERY, "variables": {
        "from": {"lat": frm[1], "lon": frm[0]}, "to": {"lat": to[1], "lon": to[0]},
        "date": DATE, "time": t, "arriveBy": arrive_by, "modes": modes}}).encode()
    req = urllib.request.Request(OTP, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.load(r)
    if res.get("errors"):
        raise RuntimeError(json.dumps(res["errors"], ensure_ascii=False)[:500])
    its = res["data"]["plan"]["itineraries"]
    if not its:
        return None, None
    best = min(its, key=lambda i: i["duration"])
    modes_used = "→".join(l["mode"] for l in best["legs"] if l["mode"] != "WALK")
    return best["duration"] / 60.0, modes_used


def breakdown(it):
    """所要時間を、出発の歩き・乗換の歩き・到着の歩き・待ち・乗車（分）に分ける"""
    legs = it["legs"]
    ride = sum(l["duration"] for l in legs if l["mode"] != "WALK")
    walks = [i for i, l in enumerate(legs) if l["mode"] == "WALK"]
    first = legs[0]["duration"] if legs and legs[0]["mode"] == "WALK" else 0
    last = legs[-1]["duration"] if len(legs) > 1 and legs[-1]["mode"] == "WALK" else 0
    xfer = sum(legs[i]["duration"] for i in walks) - first - last
    m = lambda x: x / 60.0  # noqa: E731
    return {"total": m(it["duration"]), "access": m(first), "xfer": m(xfer), "egress": m(last),
            "wait": m(it["waitingTime"]), "ride": m(ride)}


def route_text(it):
    parts = []
    for l in it["legs"]:
        d = l["duration"] / 60.0
        if l["mode"] == "WALK":
            parts.append(f"歩{d:.0f}分({l['distance']:.0f}m)")
        else:
            r = l.get("route") or {}
            name = r.get("shortName") or r.get("longName") or l["mode"]
            parts.append(f"{name} {l['from']['name']}→{l['to']['name']} {d:.0f}分")
    return " / ".join(parts)


def otp_best(frm, to, t, arrive_by, modes):
    """いちばん短い経路をそのまま返す（内訳を見るため）"""
    body = json.dumps({"query": QUERY, "variables": {
        "from": {"lat": frm[1], "lon": frm[0]}, "to": {"lat": to[1], "lon": to[0]},
        "date": DATE, "time": t, "arriveBy": arrive_by, "modes": modes}}).encode()
    req = urllib.request.Request(OTP, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.load(r)
    if res.get("errors"):
        raise RuntimeError(json.dumps(res["errors"], ensure_ascii=False)[:500])
    its = res["data"]["plan"]["itineraries"]
    return min(its, key=lambda i: i["duration"]) if its else None


def stats(diffs):
    if not diffs:
        return "（比べられた組なし）"
    s = sorted(diffs)
    return (f"{len(s)} 組。差の中央値 {statistics.median(s):+.1f}分、"
            f"最小 {s[0]:+.1f}分、最大 {s[-1]:+.1f}分")


def main() -> None:
    random.seed(7)
    out = ["# pgRouting と OpenTripPlanner の比較（Issue #7）", "",
           f"- OTP：{OTP}、日付 {DATE}（仮の時刻表なので毎日同じ）",
           "- OTP の所要時間は、駅の座標（ホームの中ほど）から駅の座標までの最短（歩きを含む）", ""]
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT group_code, station_name, t1_min, t2_min, ST_X(geom), ST_Y(geom)
            FROM reach.reachable('新横浜', '003766', 180, 60)""")
        rows = cur.fetchall()
        cur.execute("""SELECT group_code, ST_X(ST_Centroid(ST_Collect(geom))), ST_Y(ST_Centroid(ST_Collect(geom)))
                       FROM reach.station WHERE group_code IN ('004501', '003766') GROUP BY 1""")
        pts = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        shin_yokohama, tokyo = pts["004501"], pts["003766"]

        # A・B：行ける駅から、t1 の短い順・長い順・まんなかを混ぜて30駅
        rows.sort(key=lambda r: r[2])
        pick = rows[:10] + random.sample(rows[10:-10], min(10, max(0, len(rows) - 20))) + rows[-10:]
        out += ["## A・B. 電車：新横浜 → 各駅（10:00 発）、各駅 → 東京（13:00 着）", "",
                "差 ＝ OTP − pgRouting（プラスなら OTP のほうが長い）。pgRouting は乗り始めの待ち5分・乗換10分を含む。", "",
                "| 駅 | t1（pg） | 行き（OTP） | 差 | t2（pg） | 帰り（OTP） | 差 | OTP の乗り物 |",
                "|---|---|---|---|---|---|---|---|"]
        d1, d2 = [], []
        t0 = time.perf_counter()
        n_calls = 0
        for g, name, t1, t2, lon, lat in pick:
            try:
                o1, m1 = otp_plan(shin_yokohama, (lon, lat), "10:00", False, TRANSIT)
                o2, _ = otp_plan((lon, lat), tokyo, "13:00", True, TRANSIT)
                n_calls += 2
            except Exception as e:  # noqa: BLE001
                sys.exit(f"OTP への問い合わせに失敗しました：{e}")
            if o1 is not None:
                d1.append(o1 - float(t1))
            if o2 is not None:
                d2.append(o2 - float(t2))
            f = lambda x: "—" if x is None else f"{x:.0f}"  # noqa: E731
            g1 = "—" if o1 is None else f"{o1 - float(t1):+.0f}"
            g2 = "—" if o2 is None else f"{o2 - float(t2):+.0f}"
            out.append(f"| {name} | {t1} | {f(o1)} | {g1} | {t2} | {f(o2)} | {g2} | {m1 or '—'} |")
        sec = time.perf_counter() - t0
        out += ["", f"- 行き：{stats(d1)}", f"- 帰り：{stats(d2)}",
                f"- OTP の問い合わせ {n_calls} 回で {sec:.1f}秒（1回 {sec / max(n_calls, 1):.2f}秒）", ""]

        # A の内訳：OTP の時間を、歩き（出発・乗換・到着）・待ち・乗車に分ける
        out += ["## A の内訳（新横浜 → 各駅、10:00 発）", "",
                "OTP の所要時間の内訳（分）。pg は pgRouting の t1（乗り始めの待ち5分・乗換10分を含む）。", "",
                "| 駅 | pg | OTP 計 | 出発の歩き | 乗換の歩き | 到着の歩き | 待ち | 乗車 |",
                "|---|---|---|---|---|---|---|---|"]
        sums = defaultdict(list)
        routes = []
        for g, name, t1, t2, lon, lat in pick:
            it = otp_best(shin_yokohama, (lon, lat), "10:00", False, TRANSIT)
            if it is None:
                out.append(f"| {name} | {t1} | — | | | | | |")
                continue
            b = breakdown(it)
            for k, v in b.items():
                sums[k].append(v)
            out.append(f"| {name} | {t1} | {b['total']:.0f} | {b['access']:.0f} | {b['xfer']:.0f} | "
                       f"{b['egress']:.0f} | {b['wait']:.0f} | {b['ride']:.0f} |")
            routes.append(f"- {name}（pg {t1}分・OTP {b['total']:.0f}分）：{route_text(it)}")
        if sums["total"]:
            md = lambda k: statistics.median(sums[k])  # noqa: E731
            out += ["", f"- 中央値：計 {md('total'):.1f}、出発の歩き {md('access'):.1f}、乗換の歩き {md('xfer'):.1f}、"
                        f"到着の歩き {md('egress'):.1f}、待ち {md('wait'):.1f}、乗車 {md('ride'):.1f}", ""]
        out += ["### 経路（OTP）", ""] + routes + [""]

        # C：歩き。N13 で道のりと直線の差が大きい組10と、ランダムな20組
        cur.execute("""
            SELECT g.station_name, s.name, w.walk_min,
                   ST_X(g.p), ST_Y(g.p), ST_X(s.geom), ST_Y(s.geom),
                   ST_Distance(s.geom::geography, g.p::geography) AS line_m
            FROM reach.station_spot_walk w
            JOIN reach.spot s USING (spot_id)
            JOIN (SELECT group_code, min(station_name) AS station_name, ST_Centroid(ST_Collect(geom)) AS p
                  FROM reach.station GROUP BY group_code) g USING (group_code)
            WHERE ST_Y(s.geom) BETWEEN 35.4 AND 35.8""")
        wrows = [r for r in cur.fetchall() if r[7] >= 300]
        wrows.sort(key=lambda r: -(r[2] * 80 / r[7]))
        wpick = wrows[:10] + random.sample(wrows[10:], min(20, max(0, len(wrows) - 10)))
        out += ["## C. 歩き：駅 → 場所", "",
                "差 ＝ OTP（OSM）− N13（#23）。どちらも分速80m 相当ではなく、OTP は OTP の既定の歩く速さ。", "",
                "| 駅 → 場所 | 直線 | N13 | OTP | 差 |", "|---|---|---|---|---|"]
        d3 = []
        for st, sp, wm, slon, slat, plon, plat, line_m in wpick:
            o, _ = otp_plan((slon, slat), (plon, plat), "10:00", False, WALK)
            if o is not None:
                d3.append(o - wm)
            out.append(f"| {st} → {sp} | {line_m:.0f}m | {wm:.0f}分 | "
                       f"{'—' if o is None else f'{o:.0f}分'} | {'—' if o is None else f'{o - wm:+.0f}'} |")
        out += ["", f"- 歩き：{stats(d3)}（上の10組は、N13 で道のりと直線の差が大きい組）", ""]

    path = HERE.parent / "results" / "otp_compare.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"書いた：{path}")


if __name__ == "__main__":
    main()
