"""OSM（pbf）から、範囲のある場所・入口・駅の出入口を抜き出す（Issue #31）。

2回読む。
  1回目：範囲（閉じた way・multipolygon）と、入口になりうる点（entrance・門・駅の出入口・駅）
  2回目：歩ける道（highway）が範囲の境界と交わる点（OSM に入口の印が無いときの代わり）

出力（OUT_DIR）：
  areas.jsonl      範囲。osm_type/osm_id、名前、wikidata、種類のタグ、面積、境界の点の数、geom（WKB の16進）
  points.csv       入口になりうる点（ノード）。entrance・barrier・railway・public_transport など
  ring_points.csv  範囲の境界に乗っている入口の点（境界の way の点そのもの）
  crossings.csv    歩ける道と範囲の境界が交わる点（道の種類、access・foot）

例：python extract_osm.py ~/kanto-261006.osm.pbf out/ pass1
    python extract_osm.py ~/kanto-261006.osm.pbf out/ pass2 0/1
  位置の索引は OUT_DIR/loc.idx（関東で 0.6GB 前後。終わったら消してよい）。メモリは 1GB 程度。
  関東の 1回目は約2分。
"""
import csv
import json
import sys
import time
from pathlib import Path

import osmium
import osmium.filter
import shapely
from shapely import wkb
from shapely.strtree import STRtree

# 範囲として拾う種類（名前があるもの）。wikidata タグがあれば種類を問わず拾う
AREA_TAGS = {
    "leisure": {"park", "garden", "nature_reserve", "common", "zoo", "dog_park", "marina"},
    "natural": {"water", "wetland", "beach", "wood", "scrub", "heath", "peak", "valley", "bay"},
    "water": None,  # lake・pond・reservoir など、全部
    "amenity": {"place_of_worship", "grave_yard"},
    "landuse": {"religious", "cemetery", "recreation_ground", "village_green"},
    "historic": None,
    "tourism": {"museum", "zoo", "theme_park", "attraction", "aquarium", "gallery", "viewpoint", "picnic_site"},
    "boundary": {"protected_area", "national_park"},
}
AREA_KEYS = list(AREA_TAGS)
KEEP_TAGS = ["name", "name:ja", "wikidata", "access", "opening_hours", "fee"] + AREA_KEYS

# 歩ける道。motorway 系と、自動車専用（motorroad=yes）は除く
WALK_HW = {
    "footway", "path", "pedestrian", "steps", "residential", "service", "unclassified", "tertiary",
    "tertiary_link", "secondary", "secondary_link", "primary", "primary_link", "trunk", "trunk_link",
    "living_street", "track", "cycleway", "corridor", "bridleway", "road",
}

POINT_KEYS = ("entrance", "barrier", "railway", "public_transport", "door", "highway")
POINT_VALUES = {
    "barrier": {"gate", "entrance", "turnstile", "kissing_gate", "swing_gate", "lift_gate", "bollard", "stile"},
    "railway": {"subway_entrance", "train_station_entrance", "station", "halt", "stop"},
    "public_transport": {"station", "stop_position"},
    "highway": {"elevator"},
}


def area_kind(tags) -> str | None:
    for k in AREA_KEYS:
        v = tags.get(k)
        if v is None:
            continue
        allowed = AREA_TAGS[k]
        if allowed is None or v in allowed:
            return f"{k}={v}"
    return None


def is_point(tags) -> bool:
    if "entrance" in tags:
        return True
    for k, vals in POINT_VALUES.items():
        if tags.get(k) in vals:
            return True
    return False


def pass1(pbf: Path, out: Path):
    wkbf = osmium.geom.WKBFactory()
    areas = []
    ring_nodes = {}  # 入口になりうる点の node_id -> [area index]（境界に乗っているもの）
    point_ids = set()  # pbf ではノードが先に来るので、範囲を組むときには揃っている
    n_area_seen = 0
    t0 = time.time()
    fp = (osmium.FileProcessor(str(pbf))
          .with_locations(f"sparse_file_array,{out / 'loc.idx'}")
          .with_areas(osmium.filter.KeyFilter(*AREA_KEYS, "wikidata")))
    pts = open(out / "points.csv", "w", newline="", encoding="utf-8")
    pw = csv.writer(pts)
    pw.writerow(["node_id", "lon", "lat", "entrance", "barrier", "railway", "public_transport",
                 "name", "access", "level", "ref", "wikidata"])
    for o in fp:
        if o.is_node():
            t = o.tags
            if len(t) and is_point(t):
                point_ids.add(o.id)
                pw.writerow([o.id, f"{o.location.lon:.7f}", f"{o.location.lat:.7f}",
                             t.get("entrance", ""), t.get("barrier", ""), t.get("railway", ""),
                             t.get("public_transport", ""), t.get("name", ""), t.get("access", ""),
                             t.get("level", ""), t.get("ref", ""), t.get("wikidata", "")])
        elif o.is_area():
            n_area_seen += 1
            t = o.tags
            kind = area_kind(t)
            name = t.get("name") or t.get("name:ja")
            if not (t.get("wikidata") or (kind and name)):
                continue
            # 行政界・地名・建物は場所の範囲ではない
            if t.get("boundary") in ("administrative", "political", "census", "postal_code") \
                    or "place" in t or ("building" in t and not kind):
                continue
            try:
                g = wkb.loads(wkbf.create_multipolygon(o), hex=True)
            except Exception:
                continue
            # 国立公園・湾のような広すぎる範囲は除く（30km² 超。関東の緯度で概算）
            if shapely.area(shapely.transform(g, lambda c: c * [91000.0, 111000.0])) > 30e6:
                continue
            idx = len(areas)
            nodes = set()
            for outer in o.outer_rings():
                for nr in outer:
                    nodes.add(nr.ref)
                for inner in o.inner_rings(outer):
                    for nr in inner:
                        nodes.add(nr.ref)
            for nid in nodes & point_ids:
                ring_nodes.setdefault(nid, []).append(idx)
            areas.append({
                "osm_type": "way" if o.from_way() else "relation",
                "osm_id": o.orig_id(),
                "name": name or "",
                "wikidata": t.get("wikidata", ""),
                "kind": kind or "",
                "tags": {k: t.get(k) for k in KEEP_TAGS if t.get(k) is not None},
                "n_ring_nodes": len(nodes),
                "geom": g,
            })
    pts.close()
    print(f"1回目：範囲の候補 {n_area_seen:,} のうち {len(areas):,} を残した（{time.time() - t0:.0f}秒）", flush=True)
    return areas, ring_nodes


def pass2(pbf: Path, out: Path, areas, part: int, nparts: int):
    """歩ける道が範囲の境界と交わる点（境界の点を共有している場合も、幾何で交わる場合も）。"""
    t0 = time.time()
    bounds = [a["geom"].boundary for a in areas]
    tree = STRtree(bounds)
    cw = csv.writer(open(out / ("crossings.csv" if nparts == 1 else f"crossings.{part}.csv"), "w", newline="", encoding="utf-8"))
    cw.writerow(["area_idx", "way_id", "highway", "access", "foot", "lon", "lat"])
    n_way = n_hit = 0
    # 1回目で作った位置の索引を使い回す（ノードは読まない）
    locs = osmium.index.create_map(f"sparse_file_array,{out / 'loc.idx'}")
    fp = osmium.FileProcessor(str(pbf), osmium.osm.WAY).with_filter(osmium.filter.KeyFilter("highway"))
    for w in fp:
        if w.id % nparts != part:
            continue
        t = w.tags
        hw = t.get("highway")
        if hw not in WALK_HW or t.get("motorroad") == "yes" or t.get("area") == "yes":
            continue
        try:
            coords = [(l.lon, l.lat) for l in (locs.get(n.ref) for n in w.nodes)]
        except (osmium.InvalidLocationError, KeyError):
            continue
        if len(coords) < 2:
            continue
        n_way += 1
        line = shapely.LineString(coords)
        for ai in tree.query(line, predicate="intersects"):
            ai = int(ai)
            inter = line.intersection(bounds[ai])
            for p in getattr(inter, "geoms", [inter]):
                if p.is_empty:
                    continue
                p = p if p.geom_type == "Point" else p.representative_point()
                cw.writerow([ai, w.id, hw, t.get("access", ""), t.get("foot", ""), f"{p.x:.7f}", f"{p.y:.7f}"])
                n_hit += 1
    print(f"2回目：歩ける道 {n_way:,} 本、境界との交わり {n_hit:,}（{time.time() - t0:.0f}秒）", flush=True)


def main():
    """1回目と2回目は別々に流せる（1回の実行時間を短くするため）。
      python extract_osm.py PBF OUT_DIR pass1
      python extract_osm.py PBF OUT_DIR pass2 K/N   （道を way id の剰余で N 分割した K 番目。全部終わったら crossings.*.csv をつなぐ）
    """
    if len(sys.argv) < 4:
        sys.exit(__doc__ + main.__doc__)
    pbf, out = Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    if sys.argv[3] == "pass1":
        areas, ring_nodes = pass1(pbf, out)
        # 境界に乗っている入口の点
        with open(out / "points.csv", encoding="utf-8") as f, \
             open(out / "ring_points.csv", "w", newline="", encoding="utf-8") as g:
            r = csv.DictReader(f)
            wr = csv.writer(g)
            wr.writerow(["area_idx", "node_id"])
            for row in r:
                for ai in ring_nodes.get(int(row["node_id"]), ()):
                    wr.writerow([ai, row["node_id"]])
        with open(out / "areas.jsonl", "w", encoding="utf-8") as f:
            for i, a in enumerate(areas):
                g = a["geom"]
                area_m2 = shapely.area(shapely.transform(g, lambda c: c * [91000.0, 111000.0]))  # 関東の緯度で概算
                rec = {k: v for k, v in a.items() if k != "geom"}
                rec.update(area_idx=i, area_m2=round(float(area_m2)), geom=g.wkb_hex)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    elif sys.argv[3] == "pass2":
        part, nparts = (int(x) for x in sys.argv[4].split("/"))
        areas = []
        with open(out / "areas.jsonl", encoding="utf-8") as f:
            for line in f:
                a = json.loads(line)
                areas.append({"geom": wkb.loads(a["geom"], hex=True)})
        pass2(pbf, out, areas, part, nparts)
    else:
        sys.exit(main.__doc__)


if __name__ == "__main__":
    main()
