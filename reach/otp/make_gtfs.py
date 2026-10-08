"""N02 と表定速度（reach の表）から、仮の GTFS を作る（Issue #7）。

時刻表を使わずに OpenTripPlanner（OTP）を動かすためのもの。実在のダイヤではない。
- 駅：reach.station の駅のまとまり（group_code）ごとに1つの停留所。座標はまとまりの中の駅（ホームの中ほど）の重心。
  #5 の乗換（同じまとまりの中で乗り換える）に合わせ、乗換で駅の中を歩く分は無し。乗換の手間は OTP の transferSlack（既定2分）
- 路線の走る順番：reach.edge（乗車の辺）を路線ごとにたどる。路線の端（行き止まりの点）どうしを結ぶ最短の経路を、それぞれ1つの系統とする。
  環状線（端がない路線）は1周を1系統にする
- 駅と駅の間の所要時間：reach.edge.cost（距離 ÷ 表定速度。区間の上書きを含む）。#5 の reach.reachable と同じ値
- 本数：種別ごとに「何分おき」を仮に決め、frequencies.txt に書く（05:00〜24:00）。
  exact_times=1（決まった時刻に出る列車）にする。0 だと OTP は毎回「1本逃した直後」の待ち（間隔そのもの）を見るため
- 範囲：経度・緯度の四角（既定は関東）の中の駅だけ。四角の外に出る系統は、中にある連続した部分だけを使う

使い方：
  python make_gtfs.py <出力する zip>
  例：python make_gtfs.py ../../docker/otp/data/exday-pseudo-gtfs.zip

ファイル名に「gtfs」を含めること（OTP の決まり）。
"""
import csv
import heapq
import io
import os
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import psycopg

DSN = os.environ.get("EXDAY_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/ex_day_poc")

# 範囲（経度・緯度）。既定は関東の OSM（Geofabrik の kanto）をおおよそ覆う四角
BBOX = tuple(float(x) for x in os.environ.get("EXDAY_GTFS_BBOX", "138.4,34.8,140.9,37.2").split(","))

# 何分おきに走るか（仮）。待ち時間はこの半分くらいになる
HEADWAY_MIN = {
    "shinkansen": 10, "conventional": 10, "subway": 5, "monorail": 8,
    "agt": 8, "tram": 10, "cable": 20, "other": 15,
}
# GTFS の route_type
ROUTE_TYPE = {
    "shinkansen": 2, "conventional": 2, "subway": 1, "monorail": 12,
    "agt": 12, "tram": 0, "cable": 7, "other": 2,
}
MAX_PATTERNS_PER_LINE = 15  # 端が多すぎる路線で、系統が増えすぎないようにする


def inside(lon, lat):
    return BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]


def dijkstra(adj, src):
    dist = {src: 0.0}
    prev = {}
    q = [(0.0, src)]
    while q:
        d, u = heapq.heappop(q)
        if d > dist.get(u, 1e18):
            continue
        for v, w, eid in adj[u]:
            nd = d + w
            if nd < dist.get(v, 1e18):
                dist[v] = nd
                prev[v] = (u, eid)
                heapq.heappush(q, (nd, v))
    return dist, prev


def path_edges(prev, src, dst):
    edges = []
    v = dst
    while v != src:
        u, eid = prev[v]
        edges.append((u, v, eid))
        v = u
    return list(reversed(edges))


def cycle_edges(adj):
    """端のない路線（環状線）：どこかの点から1周たどる。"""
    start = next(iter(adj))
    edges, prev_node, u = [], None, start
    seen = set()
    while True:
        nxt = [(v, w, e) for v, w, e in adj[u] if e not in seen and v != prev_node] or \
              [(v, w, e) for v, w, e in adj[u] if e not in seen]
        if not nxt:
            break
        v, w, e = nxt[0]
        seen.add(e)
        edges.append((u, v, e))
        prev_node, u = u, v
        if u == start:
            break
    return edges


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out = Path(sys.argv[1])
    if "gtfs" not in out.name:
        sys.exit("出力するファイル名に「gtfs」を含めてください（OTP の決まり）")

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT line_id, operator, line_name, service_type FROM reach.line")
        lines = {r[0]: r[1:] for r in cur.fetchall()}
        cur.execute("SELECT id, line_id, source, target, cost FROM reach.edge WHERE kind = 'ride'")
        edges = cur.fetchall()
        cur.execute("""SELECT station_code, station_name, group_code, line_id, edge_id,
                              ST_X(geom), ST_Y(geom) FROM reach.station WHERE edge_id IS NOT NULL""")
        stations = cur.fetchall()

    adj_by_line = defaultdict(lambda: defaultdict(list))
    edge_cost = {}
    for eid, line_id, s, t, cost in edges:
        adj_by_line[line_id][s].append((t, cost, eid))
        adj_by_line[line_id][t].append((s, cost, eid))
        edge_cost[eid] = cost
    # 駅のまとまりごとに1つの停留所（座標は重心、名前はまとまりの中でいちばん小さい名前）
    station_by_edge = {}
    members = defaultdict(list)
    for code, name, group, line_id, eid, lon, lat in stations:
        station_by_edge[eid] = group
        members[group].append((name, lon, lat))
    stop_rows = {}
    for group, ms in members.items():
        lon = sum(m[1] for m in ms) / len(ms)
        lat = sum(m[2] for m in ms) / len(ms)
        stop_rows[group] = (group, min(m[0] for m in ms), lat, lon, group)

    routes, trips, stop_times, freqs = [], [], [], []
    used_stops = set()
    n_patterns = 0
    for line_id, adj in adj_by_line.items():
        operator, line_name, service_type = lines[line_id]
        # 路線のつながった塊ごとに、端（次数1の点）を集める
        seen_nodes = set()
        patterns = []
        for start in list(adj):
            if start in seen_nodes:
                continue
            comp, stack = [], [start]
            seen_nodes.add(start)
            while stack:
                u = stack.pop()
                comp.append(u)
                for v, _, _ in adj[u]:
                    if v not in seen_nodes:
                        seen_nodes.add(v)
                        stack.append(v)
            ends = [u for u in comp if len(adj[u]) == 1]
            if not ends:
                sub = {u: adj[u] for u in comp}
                patterns.append(cycle_edges(sub))
                continue
            pairs = [(a, b) for i, a in enumerate(ends) for b in ends[i + 1:]]
            for a, b in pairs:
                dist, prev = dijkstra(adj, a)
                if b in dist:
                    patterns.append(path_edges(prev, a, b))
        # 長い系統から使い、上限で打ち切る
        patterns.sort(key=lambda p: -len(p))
        patterns = patterns[:MAX_PATTERNS_PER_LINE]

        route_id = f"L{line_id}"
        route_added = False
        for p in patterns:
            # 系統の上の駅と、出発からの所要時間（駅のホームの辺の真ん中）
            t = 0.0
            seq = []
            for u, v, eid in p:
                c = edge_cost[eid]
                code = station_by_edge.get(eid)
                if code and (not seq or seq[-1][0] != code):
                    seq.append((code, t + c / 2))
                t += c
            # 四角の中の、連続した部分に分ける
            runs, cur_run = [], []
            for code, tm in seq:
                _, _, lat, lon, _ = stop_rows[code]
                if inside(lon, lat):
                    cur_run.append((code, tm))
                else:
                    if len(cur_run) >= 2:
                        runs.append(cur_run)
                    cur_run = []
            if len(cur_run) >= 2:
                runs.append(cur_run)
            for run in runs:
                for direction, r in ((0, run), (1, list(reversed(run)))):
                    n_patterns += 1
                    trip_id = f"T{n_patterns}"
                    base = r[0][1]
                    trips.append((route_id, "everyday", trip_id, direction))
                    for i, (code, tm) in enumerate(r, 1):
                        sec = int(round(abs(tm - base) * 60))
                        hh = f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
                        stop_times.append((trip_id, hh, hh, code, i))
                        used_stops.add(code)
                    freqs.append((trip_id, "05:00:00", "24:00:00", HEADWAY_MIN.get(service_type, 15) * 60, 1))
                    route_added = True
        if route_added:
            routes.append((route_id, "exday", line_name[:20], f"{operator} {line_name}",
                           ROUTE_TYPE.get(service_type, 2)))

    def table(header, rows):
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
        return buf.getvalue()

    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("agency.txt", table(
            ["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_lang"],
            [("exday", "ex-day 仮の時刻表（実在しない）", "https://github.com/ex-day/poc", "Asia/Tokyo", "ja")]))
        z.writestr("stops.txt", table(
            ["stop_id", "stop_name", "stop_lat", "stop_lon"],
            [(c, stop_rows[c][1], f"{stop_rows[c][2]:.7f}", f"{stop_rows[c][3]:.7f}") for c in sorted(used_stops)]))
        z.writestr("routes.txt", table(
            ["route_id", "agency_id", "route_short_name", "route_long_name", "route_type"], routes))
        z.writestr("trips.txt", table(["route_id", "service_id", "trip_id", "direction_id"], trips))
        z.writestr("stop_times.txt", table(
            ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"], stop_times))
        z.writestr("frequencies.txt", table(
            ["trip_id", "start_time", "end_time", "headway_secs", "exact_times"], freqs))
        z.writestr("calendar.txt", table(
            ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
             "start_date", "end_date"],
            [("everyday", 1, 1, 1, 1, 1, 1, 1, "20260101", "20271231")]))
    print(f"路線 {len(routes)}、系統（片道） {len(trips)}、駅 {len(used_stops)}、停車 {len(stop_times)} → {out}")


if __name__ == "__main__":
    main()
