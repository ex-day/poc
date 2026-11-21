"""N13 と OSM の歩きのグラフ（pgRouting）を、#23・#31 と同じ条件で比べる（Issue #46）。

使い方：
  python compare_osm_walk.py <N13 の DB の DSN> <OSM の DB の DSN> <結果の .md>

どちらの DB にも、base.sql・歩きのグラフ・walk_precompute.sql・09・10 を流してあること（README.md）。
"""
import statistics
import sys

import psycopg

TOKYO_GALLERY = "東京ステーションギャラリー"


def q(conn, sql, *args):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def pct(values, p):
    s = sorted(values)
    if not s:
        return float("nan")
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def size(conn):
    (nodes, edges, km, node_mb, edge_mb), = q(conn, """
        SELECT (SELECT count(*) FROM reach.walk_node), (SELECT count(*) FROM reach.walk_edge),
               (SELECT round(sum(length_m) / 1000) FROM reach.walk_edge),
               round(pg_total_relation_size('reach.walk_node') / 1048576.0),
               round(pg_total_relation_size('reach.walk_edge') / 1048576.0)""")
    return nodes, edges, km, node_mb, edge_mb


def snapped(conn):
    (groups, spots, aps, ap_spots), = q(conn, """
        SELECT (SELECT count(*) FROM reach.station_walk), (SELECT count(*) FROM reach.spot_walk),
               (SELECT count(DISTINCT ap_id) FROM reach.spot_walk_ap WHERE ap_id IS NOT NULL),
               (SELECT count(DISTINCT spot_id) FROM reach.spot_walk_ap WHERE ap_id IS NOT NULL)""")
    return groups, spots, aps, ap_spots


def pairs(conn, table):
    return {(g, s): m for g, s, m in q(conn, f"SELECT group_code, spot_id, walk_min FROM reach.{table}")}


def nearest(p):
    best = {}
    for (g, s), m in p.items():
        if s not in best or m < best[s][1]:
            best[s] = (g, m)
    return best


def stats_row(label, diffs):
    return (f"| {label} | {len(diffs):,} | {statistics.median(diffs):+.1f} | {pct(diffs, 0.2):+.1f} | "
            f"{pct(diffs, 0.8):+.1f} | {min(diffs):+.1f} | {max(diffs):+.1f} | "
            f"{sum(1 for d in diffs if d <= -3):,} | {sum(1 for d in diffs if d >= 3):,} |")


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    n13 = psycopg.connect(sys.argv[1])
    osm = psycopg.connect(sys.argv[2])
    out = []
    w = out.append

    spot = {sid: (name, kinds) for sid, name, kinds in q(osm, "SELECT spot_id, name, kinds FROM reach.spot")}
    group_name = dict(q(osm, "SELECT group_code, min(station_name) FROM reach.station GROUP BY group_code"))
    (ap_total,), = q(osm, "SELECT count(*) FROM reach.spot_access_point")

    w("# pgRouting の歩きを N13 と OSM で比べる（Issue #46、platform #163）\n")
    w("条件は #23・#31 と同じ：2次メッシュ 5339、駅グループの代表点（ホームの重心）・場所・到達点を 300m 以内の道の点に結ぶ、"
      "分速80m、片道25分まで、向きは見ない。OSM は kanto-261119（2026-11-19 21:22 UTC 時点）。\n")

    w("## 1. グラフの大きさ\n")
    w("| | 点 | 辺 | 道の長さ（km） | 点の表（MB） | 辺の表（MB） |")
    w("|---|---|---|---|---|---|")
    for label, conn in (("N13", n13), ("OSM", osm)):
        nodes, edges, km, nmb, emb = size(conn)
        w(f"| {label} | {nodes:,} | {edges:,} | {km:,} | {nmb} | {emb} |")
    # 駅グループ（道に結び付いたもの）の代表点から 2.5km 以内にかかる辺。索引の効く箱で絞ってから球面の距離で確かめる
    rows = q(osm, """
        WITH g AS (
            SELECT ST_Transform(ST_Centroid(ST_Collect(s.geom)), 6668) AS p
              FROM reach.station s WHERE s.group_code IN (SELECT group_code FROM reach.station_walk)
             GROUP BY s.group_code),
        e AS (
            SELECT DISTINCT e.id, e.length_m
              FROM g JOIN reach.walk_edge e ON e.geom && ST_Expand(g.p, 0.032)
             WHERE ST_DWithin(e.geom::geography, g.p::geography, 2500))
        SELECT count(*), round(sum(length_m) / 1000) FROM e""")
    w(f"\n- OSM で、駅グループの代表点から 2.5km 以内にかかる辺：{rows[0][0]:,} 本（{rows[0][1]:,} km）")
    by_hw = q(osm, """
        SELECT highway, count(*), round(sum(length_m) / 1000) FROM reach.walk_edge GROUP BY highway ORDER BY 3 DESC LIMIT 12""")
    w("- OSM の辺の種類（長さの上位）：" + "、".join(f"{h} {km:,}km" for h, _, km in by_hw) + "\n")

    w("## 2. 道に結び付いたもの（300m 以内）\n")
    w(f"| | 駅グループ | 場所 | 到達点（全 {ap_total:,}） | 到達点を持つ場所 |")
    w("|---|---|---|---|---|")
    snaps = {}
    for label, conn in (("N13", n13), ("OSM", osm)):
        snaps[label] = snapped(conn)
        g, s, a, as_ = snaps[label]
        w(f"| {label} | {g:,} | {s:,} | {a:,} | {as_:,} |")
    gained = q(osm, "SELECT DISTINCT ap_id FROM reach.spot_walk_ap WHERE ap_id IS NOT NULL")
    n13_aps = {r[0] for r in q(n13, "SELECT DISTINCT ap_id FROM reach.spot_walk_ap WHERE ap_id IS NOT NULL")}
    osm_aps = {r[0] for r in gained}
    w(f"\n- N13 で結べず OSM で結べた到達点：{len(osm_aps - n13_aps):,}、その逆：{len(n13_aps - osm_aps):,}")
    w(f"- OSM でも結べない到達点：{ap_total - len(osm_aps):,}\n")

    for title, table in (("場所の点", "station_spot_walk"), ("到達点", "station_spot_walk_ap")):
        pn, po = pairs(n13, table), pairs(osm, table)
        both = pn.keys() & po.keys()
        w(f"## 3. 25分までに歩いて行ける（駅グループ, 場所）の組（{title}）\n")
        w(f"- 両方：{len(both):,}、N13 だけ：{len(pn.keys() - po.keys()):,}、OSM だけ：{len(po.keys() - pn.keys()):,}\n")
        w("| 比べたもの | 組 | 中央値（分） | 20% | 80% | 最小 | 最大 | 3分以上短い | 3分以上長い |")
        w("|---|---|---|---|---|---|---|---|---|")
        w(stats_row("OSM − N13（両方にある組）", [po[k] - pn[k] for k in both]))
        bn, bo = nearest(pn), nearest(po)
        common = bn.keys() & bo.keys()
        w(stats_row("いちばん近い駅グループからの歩き：OSM − N13", [bo[s][1] - bn[s][1] for s in common]))
        w("")
        w(f"- いちばん近い駅グループの歩きを持つ場所：N13 {len(bn):,}、OSM {len(bo):,}、両方 {len(common):,}"
          f"（N13 だけ {len(bn.keys() - bo.keys()):,}、OSM だけ {len(bo.keys() - bn.keys()):,}）")
        same_group = sum(1 for s in common if bn[s][0] == bo[s][0])
        w(f"- いちばん近い駅グループが同じ場所：{same_group:,} / {len(common):,}\n")
        if table == "station_spot_walk_ap":
            diffs = sorted(((bo[s][1] - bn[s][1], s) for s in common))
            for head, items in (("短くなった例（上位15件）", diffs[:15]), ("長くなった例（上位10件）", diffs[::-1][:10])):
                w(f"{head}：\n")
                w("| 場所 | 種類 | N13 | OSM | 差 |")
                w("|---|---|---|---|---|")
                for d, s in items:
                    name, kinds = spot[s]
                    w(f"| {name} | {kinds} | {group_name.get(bn[s][0])}から{bn[s][1]:.0f}分 | "
                      f"{group_name.get(bo[s][0])}から{bo[s][1]:.0f}分 | {d:+.0f} |")
                w("")
            only = sorted(bo.keys() - bn.keys(), key=lambda s: bo[s][1])
            temples = [s for s in only if spot[s][1] and any(k in spot[s][1] for k in ("寺", "神社", "仏教"))]
            w(f"OSM だけで25分以内の駅がある場所：{len(only):,}（うち寺社 {len(temples):,}）。寺社の例（歩きの短い順に15件）：\n")
            w("| 場所 | 種類 | OSM |")
            w("|---|---|---|")
            for s in temples[:15]:
                w(f"| {spot[s][0]} | {spot[s][1]} | {group_name.get(bo[s][0])}から{bo[s][1]:.0f}分 |")
            w("")

    # 駅の代表点：ホームの重心と OSM の出入口（OSM の歩き、到達点の版）。出入口のある駅グループだけで比べる
    pe = pairs(osm, "station_spot_walk_exit")
    pc = pairs(osm, "station_spot_walk_ap")
    exit_groups = {g for g, in q(osm, "SELECT DISTINCT group_code FROM reach.station_exit_walk")}
    pc_e = {k: v for k, v in pc.items() if k[0] in exit_groups}
    both = pe.keys() & pc_e.keys()
    w("## 5. 駅の代表点を OSM の出入口にしたとき（OSM の歩き、到達点）\n")
    w(f"- 出入口が道に結び付いた駅グループ：{len(exit_groups):,}。（駅グループ, 場所）の組は 両方 {len(both):,}、"
      f"重心だけ {len(pc_e.keys() - pe.keys()):,}、出入口だけ {len(pe.keys() - pc_e.keys()):,}")
    w("- 出入口からの歩きには、ホームから出入口までの駅の中の歩きを足していない\n")
    w("| 比べたもの | 組 | 中央値（分） | 20% | 80% | 最小 | 最大 | 3分以上短い | 3分以上長い |")
    w("|---|---|---|---|---|---|---|---|---|")
    w(stats_row("出入口 − 重心", [pe[k] - pc_e[k] for k in both]))
    w("")

    w(f"## 4. {TOKYO_GALLERY}\n")
    w("| 駅グループ | N13（到達点） | OSM（到達点） | OSM（出入口から） |")
    w("|---|---|---|---|")
    sid = [k for k, v in spot.items() if v[0] == TOKYO_GALLERY]
    if sid:
        pn, po = pairs(n13, "station_spot_walk_ap"), pairs(osm, "station_spot_walk_ap")
        px = pairs(osm, "station_spot_walk_exit")
        groups = sorted({g for (g, s) in list(pn) + list(po) if s == sid[0]},
                        key=lambda g: min(pn.get((g, sid[0]), 99), po.get((g, sid[0]), 99)))
        for g in groups[:6]:
            a, b, c = pn.get((g, sid[0])), po.get((g, sid[0])), px.get((g, sid[0]))
            fmt = lambda v: "—" if v is None else f"{v:.1f}分"
            w(f"| {group_name.get(g)}（{g}） | {fmt(a)} | {fmt(b)} | {fmt(c)} |")
    w("")
    sys.stdout.write("")
    with open(sys.argv[3], "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
