"""到達点（入口・範囲の端）と駅の出入口で、駅から場所までの歩きがどう変わるかを、R5（OSM の道）で比べる（Issue #31 の 2・4）。

使い方（リポジトリ直下の .venv で。JAVA_HOME を設定しておく。OTP は止めておく）：
  python reach/entrance/compare_access_r5.py <OSM の pbf> <station_exit_point.csv>

  station_exit_point.csv は analyze.py が書く（駅グループごとの OSM の出入口）。

前提：09_access_point.sql と 10_build_access_walk.sql を流してあること（pgRouting の数字と並べるため）。

比べること（歩きだけ。電車は使わない）：
  - 駅の起点：ホームの重心（今）／OSM の出入口（駅グループの出入口のうち、いちばん短いもの）
  - 場所の終点：場所の点（今）／到達点（いちばん近いもの）
  - 対象：到達点を持つ場所と、その点から直線 2km 以内の駅グループの組
  - pgRouting（N13）の station_spot_walk・station_spot_walk_ap とも並べる

結果は reach/results/access_walk_r5.md。
"""
import csv
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ARGS = sys.argv[1:]
sys.argv = [sys.argv[0], "--max-memory", os.environ.get("EXDAY_R5_MEMORY", "10G")]

import geopandas as gpd  # noqa: E402
import r5py  # noqa: E402
from shapely.geometry import Point  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "vector"))
from common import connect  # noqa: E402

DEPARTURE = datetime(2026, 11, 10, 10, 0)
MAX_MIN = 30
NEAR_M = 2000
CHUNK = 40
RESULT = HERE.parent / "results" / "access_walk_r5.md"
EXAMPLES = ["東京ステーションギャラリー", "三菱一号館美術館", "新宿御苑", "旧古河庭園", "六義園", "象の鼻パーク", "井の頭恩賜公園", "千鳥ケ淵"]


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else float("nan")


def gdf(rows):
    return gpd.GeoDataFrame({"id": [r[0] for r in rows]},
                            geometry=[Point(r[1], r[2]) for r in rows], crs="EPSG:4326")


def walk_times(net, origins, dests, near_pairs):
    """origins・dests は (id, lon, lat)。near_pairs(o_id) は、その起点から測る終点の id の集合。"""
    res = {}
    dmap = {d[0]: d for d in dests}
    for i in range(0, len(origins), CHUNK):
        chunk = origins[i:i + CHUNK]
        want = set().union(*(near_pairs(o[0]) for o in chunk))
        if not want:
            continue
        m = r5py.TravelTimeMatrix(
            net, origins=gdf(chunk), destinations=gdf([dmap[d] for d in want]), departure=DEPARTURE,
            transport_modes=[r5py.TransportMode.WALK], speed_walking=4.8,
            max_time=timedelta(minutes=MAX_MIN), snap_to_network=True)
        for r in m.itertuples():
            if r.travel_time == r.travel_time:
                res[(r.from_id, r.to_id)] = float(r.travel_time)
    return res


def main():
    if len(ARGS) != 2:
        sys.exit(__doc__)
    pbf, exit_csv = Path(ARGS[0]).expanduser(), Path(ARGS[1]).expanduser()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT group_code, min(station_name), ST_X(ST_Centroid(ST_Collect(geom))), ST_Y(ST_Centroid(ST_Collect(geom)))
            FROM reach.station GROUP BY group_code""")
        groups = {g: (n, x, y) for g, n, x, y in cur.fetchall()}
        cur.execute("""
            SELECT s.spot_id, s.name, ST_X(s.geom), ST_Y(s.geom),
                   array_agg(DISTINCT st.group_code)
            FROM reach.spot s
            JOIN (SELECT DISTINCT spot_id FROM reach.spot_access_point) a USING (spot_id)
            JOIN (SELECT group_code, ST_Centroid(ST_Collect(geom)) AS g FROM reach.station GROUP BY group_code) st
              ON ST_DWithin(st.g::geography, s.geom::geography, %s)
            GROUP BY s.spot_id, s.name, s.geom""", (NEAR_M,))
        spots = {sid: (name, x, y, set(gs)) for sid, name, x, y, gs in cur.fetchall()}
        cur.execute("SELECT ap_id, spot_id, lon, lat FROM reach.spot_access_point")
        aps = defaultdict(list)
        for ap_id, sid, x, y in cur.fetchall():
            if sid in spots:
                aps[sid].append((f"a{ap_id}", x, y))
        cur.execute("SELECT group_code, spot_id, walk_min FROM reach.station_spot_walk")
        pg_pt = {(g, s): w for g, s, w in cur.fetchall() if s in spots}
        cur.execute("SELECT group_code, spot_id, walk_min FROM reach.station_spot_walk_ap")
        pg_ap = {(g, s): w for g, s, w in cur.fetchall() if s in spots}

    exits = defaultdict(list)
    with open(exit_csv, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            exits[r["group_code"]].append((f"e{r['node_id']}", float(r["lon"]), float(r["lat"])))

    by_group = defaultdict(set)
    for sid, (_, _, _, gs) in spots.items():
        for g in gs:
            by_group[g].add(sid)
    print(f"場所 {len(spots):,}、到達点 {sum(map(len, aps.values())):,}、駅グループ {len(by_group):,}", flush=True)

    t0 = time.time()
    net = r5py.TransportNetwork(str(pbf), [])
    t_net = time.time() - t0

    dests = [(f"p{sid}", x, y) for sid, (_, x, y, _) in spots.items()] + [a for v in aps.values() for a in v]

    def targets(sids):
        out = set()
        for sid in sids:
            out.add(f"p{sid}")
            out.update(a[0] for a in aps[sid])
        return out

    t0 = time.time()
    origins_c = [(g, groups[g][1], groups[g][2]) for g in by_group if g in groups]
    tt_c = walk_times(net, origins_c, dests, lambda g: targets(by_group[g]))
    exit_owner = {}
    origins_e = []
    for g in by_group:
        for e in exits.get(g, []):
            exit_owner[e[0]] = g
            origins_e.append(e)
    tt_e = walk_times(net, origins_e, dests, lambda e: targets(by_group[exit_owner[e]]))
    t_tt = time.time() - t0

    def best(tt, origin_ids, sid, use_ap):
        ds = [a[0] for a in aps[sid]] if use_ap else [f"p{sid}"]
        vals = [tt[(o, d)] for o in origin_ids for d in ds if (o, d) in tt]
        return min(vals) if vals else None

    rows = []
    for sid, (name, _, _, gs) in spots.items():
        for g in gs:
            ex = [e[0] for e in exits.get(g, [])]
            rows.append({
                "spot_id": sid, "name": name, "group": g, "station": groups[g][0],
                "r5_c_pt": best(tt_c, [g], sid, False), "r5_c_ap": best(tt_c, [g], sid, True),
                "r5_e_pt": best(tt_e, ex, sid, False) if ex else None,
                "r5_e_ap": best(tt_e, ex, sid, True) if ex else None,
                "pg_pt": pg_pt.get((g, sid)), "pg_ap": pg_ap.get((g, sid)),
            })

    def diff(a, b, cond=lambda r: True):
        return [r[b] - r[a] for r in rows if r[a] is not None and r[b] is not None and cond(r)]

    out = ["# 到達点と駅の出入口で、駅から場所までの歩きを比べる（Issue #31 の 2・4、R5・OSM）", "",
           f"- 場所：到達点を持つ場所のうち、直線 {NEAR_M}m 以内に駅グループがあるもの {len(spots):,} 件。（場所, 駅グループ）の組 {len(rows):,}",
           f"- 歩きだけ（分速80m）、{MAX_MIN}分まで。交通網（歩きだけ）の作成 {t_net:.0f}秒、所要時間の表 {t_tt:.0f}秒",
           f"- 駅の出入口：OSM の出入口がある駅グループ {sum(1 for g in by_group if exits.get(g)):,} / {len(by_group):,}",
           "",
           "## 組ごとの差（分）", "",
           "| 比べるもの | 組 | 中央値 | 20パーセンタイル | 80パーセンタイル | 3分以上短く | 3分以上長く |",
           "|---|---|---|---|---|---|---|"]
    for label, a, b in [
        ("R5：場所の点 → 到達点（駅はホームの重心）", "r5_c_pt", "r5_c_ap"),
        ("R5：ホームの重心 → 出入口（場所は点）", "r5_c_pt", "r5_e_pt"),
        ("R5：今（重心・点） → 両方（出入口・到達点）", "r5_c_pt", "r5_e_ap"),
        ("pgRouting：場所の点 → 到達点", "pg_pt", "pg_ap"),
        ("今の形で R5 − pgRouting（重心・点）", "pg_pt", "r5_c_pt"),
        ("到達点で R5 − pgRouting（重心・到達点）", "pg_ap", "r5_c_ap"),
    ]:
        d = diff(a, b)
        out.append(f"| {label} | {len(d):,} | {q(d, .5):+.1f} | {q(d, .2):+.1f} | {q(d, .8):+.1f} | "
                   f"{sum(x <= -3 for x in d):,} | {sum(x >= 3 for x in d):,} |")
    out += ["", "## 例（いちばん近い駅グループ）", "",
            "| 場所 | 駅 | R5 今 | R5 到達点 | R5 出入口 | R5 両方 | pg 今 | pg 到達点 |", "|---|---|---|---|---|---|---|---|"]

    def f(v):
        return "—" if v is None else f"{v:.0f}分"
    for name in EXAMPLES:
        rs = [r for r in rows if r["name"] == name]
        if not rs:
            continue
        r = min(rs, key=lambda r: min(v for v in (r["r5_c_pt"], r["r5_e_ap"], r["pg_pt"], 99) if v is not None))
        out.append(f"| {name} | {r['station']} | {f(r['r5_c_pt'])} | {f(r['r5_c_ap'])} | {f(r['r5_e_pt'])} | "
                   f"{f(r['r5_e_ap'])} | {f(r['pg_pt'])} | {f(r['pg_ap'])} |")
    out.append("")
    RESULT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
