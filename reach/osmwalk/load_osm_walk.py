"""OSM の歩ける道を、交差点で区切って歩きのグラフ（reach.walk_node・reach.walk_edge）に入れる（Issue #46）。

使い方：
  python load_osm_walk.py <highway の道を切り出した .osm.pbf>
  例：python load_osm_walk.py ~/Downloads/kanto-261119-5339-highway.osm.pbf

接続先は環境変数 EXDAY_DSN（既定：postgresql://postgres:postgres@127.0.0.1:5432/ex_day_poc）。
先に schema_osm.sql を流しておくこと。

- 歩ける道：highway のうち、motorway 系・建設中・計画・廃止などを除く。access=private/no は foot=yes 等が無ければ除き、
  foot=no も除く（#31 の歩ける道と同じ考え方）。幹線道路（trunk・primary 等）は歩道がある前提で歩けるとみなす
- 区切り方：歩ける道の2本以上が使う点（交差点）と、道の端で区切る。辺1本が区切った区間1つ
- 点の ID は OSM の node の ID、座標は JGD2011（EPSG:6668）として持つ（N13 の表とそろえる。WGS84 との差は数十cm 以下）
- cost は分（分速80m）。向きは見ない（#23 と同じ）
"""
import math
import os
import sys
import time
from collections import Counter

import osmium
import psycopg

DSN = os.environ.get("EXDAY_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/ex_day_poc")

EXCLUDED_HIGHWAY = {
    "motorway", "motorway_link", "construction", "proposed", "abandoned", "disused", "razed",
    "raceway", "bus_guideway", "escape", "rest_area", "services", "emergency_bay",
}
FOOT_OK = {"yes", "designated", "permissive", "official"}


def walkable(tags) -> bool:
    hw = tags.get("highway")
    if hw is None or hw in EXCLUDED_HIGHWAY:
        return False
    foot = tags.get("foot")
    if foot == "no":
        return False
    if tags.get("access") in ("private", "no") and foot not in FOOT_OK:
        return False
    return True


def meters(lon1, lat1, lon2, lat2) -> float:
    # 球面の距離（半径 6,371km）。辺は短いので十分
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(a))


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    src = os.path.expanduser(sys.argv[1])
    t0 = time.time()

    # 1回目：歩ける道が使う点の数を数える（2本以上で使う点・道の端で区切る）
    use = Counter()
    n_ways = 0
    for w in osmium.FileProcessor(src, osmium.osm.WAY):
        if not walkable(w.tags):
            continue
        refs = [n.ref for n in w.nodes]
        if len(refs) < 2:
            continue
        n_ways += 1
        use.update(set(refs))
        use[refs[0]] += 1
        use[refs[-1]] += 1
    print(f"歩ける道 {n_ways} 本（{time.time() - t0:.0f}秒）", flush=True)

    # 2回目：区切って辺にする
    nodes = {}
    n_edges = 0
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE reach.walk_edge, reach.walk_node RESTART IDENTITY CASCADE")
        with cur.copy(
            "COPY reach.walk_edge (osm_way_id, highway, source, target, length_m, cost, reverse_cost, geom) FROM STDIN"
        ) as copy:
            fp = osmium.FileProcessor(src, osmium.osm.NODE | osmium.osm.WAY).with_locations()
            for o in fp:
                if not o.is_way() or not walkable(o.tags):
                    continue
                pts = [(n.ref, n.location) for n in o.nodes if n.location.valid()]
                if len(pts) < 2:
                    continue
                hw = o.tags.get("highway")
                seg = [pts[0]]
                for ref, loc in pts[1:]:
                    seg.append((ref, loc))
                    if use[ref] > 1 or ref == pts[-1][0]:
                        if len(seg) >= 2 and seg[0][0] != seg[-1][0]:
                            length = sum(meters(a[1].lon, a[1].lat, b[1].lon, b[1].lat) for a, b in zip(seg, seg[1:]))
                            coords = ", ".join(f"{l.lon} {l.lat}" for _, l in seg)
                            copy.write_row((o.id, hw, seg[0][0], seg[-1][0], length, length / 80.0, length / 80.0,
                                            f"SRID=6668;LINESTRING({coords})"))
                            n_edges += 1
                            for r, l in (seg[0], seg[-1]):
                                nodes[r] = (l.lon, l.lat)
                        seg = [(ref, loc)]
        print(f"辺 {n_edges} 本（{time.time() - t0:.0f}秒）", flush=True)
        with cur.copy("COPY reach.walk_node (node_id, geom) FROM STDIN") as copy:
            for r, (lon, lat) in nodes.items():
                copy.write_row((r, f"SRID=6668;POINT({lon} {lat})"))
    print(f"点 {len(nodes)} 個を入れた（{time.time() - t0:.0f}秒）")


if __name__ == "__main__":
    main()
