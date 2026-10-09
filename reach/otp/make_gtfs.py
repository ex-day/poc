"""N02 と表定速度（reach の表）から、仮の GTFS を作る（Issue #7）。

時刻表を使わずに OpenTripPlanner（OTP）を動かすためのもの。実在のダイヤではない。
- 駅：reach.station の駅のまとまり（group_code）ごとに1つの停留所。座標はまとまりの中の駅（ホームの中ほど）の重心。
  #5 の乗換（同じまとまりの中で乗り換える）に合わせ、乗換で駅の中を歩く分は無し。乗換の手間は OTP の transferSlack（既定2分）
- 路線の走る順番：reach.edge（乗車の辺）を路線ごとにたどり、どの辺もちょうど1つの系統に入るように分ける（cover_trails）。
  環状線・環状に枝が付いた路線も覆う。分かれ目の駅は、その先の系統にも最初（最後）の駅として入れる（Issue #26）
- 駅と駅の間の所要時間：reach.edge.cost（距離 ÷ 表定速度。区間の上書きを含む）。#5 の reach.reachable と同じ値
- 本数：種別ごとに「何分おき」を仮に決め、frequencies.txt に書く（05:00〜24:00）。
  exact_times=1（決まった時刻に出る列車）にする。0 だと OTP は毎回「1本逃した直後」の待ち（間隔そのもの）を見るため。
  発車の位相は路線・向きごとにずらす（Issue #26）
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
from collections import Counter, defaultdict
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


def inside(lon, lat):
    return BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]


def chain_cost(adj, u, first_edge, covered):
    """u から first_edge に入り、次の分かれ目（次数が2でない点）か覆った辺に当たるまでの所要時間"""
    v, w, e = first_edge
    total, prev, seen = w, u, {e}
    while len(adj[v]) == 2:
        nxt = [x for x in adj[v] if x[2] not in seen and x[2] not in covered]
        if not nxt:
            break
        prev, (v, w, e) = v, nxt[0]
        seen.add(e)
        total += w
    return total


def nearest_station(adj, node, exclude, station_by_edge):
    """node から、exclude に入っていない辺をたどって、いちばん近い駅（ホームの辺の真ん中）と、そこまでの所要時間"""
    dist = {node: 0.0}
    q = [(0.0, node)]
    while q:
        d, u = heapq.heappop(q)
        if d > dist.get(u, 1e18):
            continue
        for v, w, e in adj[u]:
            if e in exclude:
                continue
            if e in station_by_edge:
                return station_by_edge[e], d + w / 2
            if d + w < dist.get(v, 1e18):
                dist[v] = d + w
                heapq.heappush(q, (d + w, v))
    return None


def cover_trails(adj):
    """路線の辺を、重ならないたどり（系統）に分ける（Issue #26）。

    どの辺もちょうど1つの系統に入るので、区間ごとの本数は設定した間隔のとおりになる。
    たどり始めは、まだ覆っていない辺の数が奇数の点（行き止まりを先に）。分かれ目では、先が長いほうへ進む。
    環状線や、環状に枝が付いた路線（大江戸線・ユーカリが丘線）も、辺を残さず覆う。
    """
    covered = set()
    trails = []
    all_edges = {e for u in adj for _, _, e in adj[u]}
    while len(covered) < len(all_edges):
        def free(u):
            return [x for x in adj[u] if x[2] not in covered]
        nodes = [u for u in adj if free(u)]
        odd = [u for u in nodes if len(free(u)) % 2 == 1]
        if odd:
            start = min(odd, key=lambda u: (len(adj[u]) != 1, len(adj[u])))
        else:
            start = nodes[0]
        trail, u = [], start
        while True:
            cand = free(u)
            if not cand:
                break
            v, w, e = max(cand, key=lambda x: chain_cost(adj, u, x, covered))
            covered.add(e)
            trail.append((u, v, e))
            u = v
        trails.append(trail)
    return trails


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
        patterns = cover_trails(adj)

        route_id = f"L{line_id}"
        route_added = False
        for p in patterns:
            # 系統の上の駅と、出発からの所要時間（駅のホームの辺の真ん中）
            t = 0.0
            seq = []
            # 系統の始まり・終わりの点から、ほかの系統のいちばん近い駅を、最初・最後の駅として足す
            # （分かれ目の駅が別の系統に入っていても乗り継げるように。ホームの辺だけの短い系統も、2駅以上になる）
            trail_edges = {e for _, _, e in p}
            # 隣の系統の駅：始まりの点から、この系統の辺を通らずに行ける、いちばん近い駅
            h = nearest_station(adj, p[0][0], trail_edges, station_by_edge)
            head = [(h[0], -h[1])] if h else []
            if head:
                seq.append(head[0])
            for u, v, eid in p:
                c = edge_cost[eid]
                code = station_by_edge.get(eid)
                if code and (not seq or seq[-1][0] != code):
                    seq.append((code, t + c / 2))
                t += c
            tl = nearest_station(adj, p[-1][1], trail_edges, station_by_edge)
            tail = [(tl[0], t + tl[1])] if tl else []
            if tail and seq and seq[-1][0] != tail[0][0]:
                seq.append(tail[0])
            if head and len(seq) > 1 and seq[0][0] == seq[1][0]:
                seq.pop(0)
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
                    hw = HEADWAY_MIN.get(service_type, 15)
                    # 発車の位相：路線・向きごとにずらす（全系統が 05:00 ちょうどに出ると、乗換の待ちが偏る。PR #25 のレビュー）
                    off = (line_id * 7 + direction * 3) % hw
                    freqs.append((trip_id, f"05:{off:02d}:00", "24:00:00", hw * 60, 1))
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
    report = coverage(stations, lines, stop_rows, trips, stop_times)
    path = Path(__file__).resolve().parent.parent / "results" / "otp_gtfs_coverage.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(report, encoding="utf-8")
    print(f"網羅性の検査：{path}")


def coverage(stations, lines, stop_rows, trips, stop_times) -> str:
    """仮 GTFS が、四角の中の駅・路線をどれだけ覆っているか、区間ごとに系統がいくつ重なっているかを調べる（PR #25 のレビュー）"""
    trip_route = {t[2]: t[0] for t in trips}
    seq = defaultdict(list)
    for trip_id, _, _, stop_id, _ in stop_times:
        seq[trip_id].append(stop_id)
    # 四角の中の駅のまとまりと、（まとまり, 路線）の組
    inside_groups = {g for g, (_, _, lat, lon, _) in stop_rows.items() if inside(lon, lat)}
    pairs = {(group, line_id) for _, _, group, line_id, _, _, _ in stations if group in inside_groups}
    covered_pairs = {(s, int(trip_route[t][1:])) for t, ss in seq.items() for s in ss}
    used = {s for ss in seq.values() for s in ss}
    missing_groups = sorted(inside_groups - used)
    missing_pairs = sorted(pairs - covered_pairs)
    # 区間（向きつき）ごとに、何系統が通るか。2以上なら、設定した間隔より本数が多い
    seg = Counter()
    for t, ss in seq.items():
        for a, b in zip(ss, ss[1:]):
            seg[(trip_route[t], a, b)] += 1
    vals = sorted(seg.values())
    n = len(vals)
    name = lambda g: stop_rows[g][1] if g in stop_rows else g  # noqa: E731
    line_name = lambda lid: " ".join(lines[lid][:2]) if lid in lines else str(lid)  # noqa: E731
    out = ["# 仮 GTFS の網羅性（make_gtfs.py が書く）", "",
           f"- 範囲（経度・緯度）：{BBOX}", "",
           "## 駅", "",
           f"- 四角の中の駅のまとまり {len(inside_groups):,} のうち、GTFS に入らなかったもの {len(missing_groups):,}",
           f"- （まとまり, 路線）の組 {len(pairs):,} のうち、どの系統も止まらないもの {len(missing_pairs):,}", ""]
    by_line = defaultdict(list)
    for g, lid in missing_pairs:
        by_line[lid].append(name(g))
    if by_line:
        out += ["| 路線 | 止まらない駅 |", "|---|---|"]
        out += [f"| {line_name(lid)} | {'、'.join(sorted(v))} |" for lid, v in sorted(by_line.items(), key=lambda x: -len(x[1]))]
        out.append("")
    out += ["## 区間ごとの系統の重なり", "",
            "向きつきの区間（隣り合う停留所）ごとに、通る系統の数。どの系統も同じ間隔で走らせているので、重なった分だけ本数が多くなる。", ""]
    if n:
        out += [f"- 区間 {n:,}。1系統だけ {sum(v == 1 for v in vals) / n:.1%}、2系統以上 {sum(v > 1 for v in vals) / n:.1%}、"
                f"中央値 {vals[n // 2]}、上位10% {vals[int(n * 0.9)]}、最大 {vals[-1]}", ""]
        worst = Counter()
        for (rid, _, _), c in seg.items():
            worst[rid] = max(worst[rid], c)
        out += ["重なりの多い路線：", ""]
        out += [f"- {line_name(int(rid[1:]))}：最大 {c} 系統" for rid, c in worst.most_common(10)]
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    main()
