"""extract_osm.py の出力と、DB から書き出した場所・駅で、#31 の1（範囲と入口）・4（駅の出入口）を集計する。

例：python analyze.py out/ issue31_spot.csv issue31_station.csv results/
  issue31_spot.csv    ：reach.spot（spot_id, source, ext_id, name, kinds, lon, lat）
  issue31_station.csv ：reach.station（station_code, station_name, group_code, line_id, lon, lat）

出力（RESULT_DIR）：
  spot_access.csv     場所ごとの範囲・入口の数・到達点（次の 2 で DB に入れる）
  station_exit.csv    駅グループごとの代表点（ホームの重心）と、OSM の出入口
  entrance_osm.md     集計（README に写す）
"""
import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import shapely
from shapely import wkb
from shapely.strtree import STRtree

# 関東の OSM の範囲（Geofabrik kanto）。外の場所・駅は数えない
KANTO = shapely.box(138.39, 34.85, 140.90, 37.16)

# 歩いて入れない道
NO_ACCESS = {"private", "no"}

# 場所の種類のまとめ方（Wikidata の種類の名前に含まれる語）。上から順に当てる
KIND_GROUPS = [
    ("湖・池・沼", ("湖", "池", "沼", "貯水池", "ダム湖")),
    ("公園・庭園", ("公園", "庭園", "緑地", "植物園")),
    ("神社", ("神社",)),
    ("寺", ("寺", "仏教")),
    ("城・城跡", ("城",)),
    ("博物館・美術館", ("博物館", "美術館", "記念館", "資料館", "ギャラリー")),
    ("動物園・水族館", ("動物園", "水族館")),
    ("山・滝・自然", ("山", "滝", "峠", "渓谷", "島", "浜", "海岸", "岬")),
]

# 点を含む範囲で結ぶとき、場所の種類と合う範囲の種類（キーの前方一致）。合わない範囲（公園の中の神社 → 公園）は結ばない
COMPAT = {
    "湖・池・沼": ("natural=water", "water=", "natural=wetland"),
    "公園・庭園": ("leisure=", "landuse=recreation_ground", "landuse=village_green", "tourism=attraction"),
    "神社": ("amenity=place_of_worship", "landuse=religious"),
    "寺": ("amenity=place_of_worship", "landuse=religious"),
    "城・城跡": ("historic=", "leisure=park"),
    "博物館・美術館": ("tourism=museum", "tourism=gallery", "tourism=zoo", "tourism=aquarium", "tourism=attraction"),
    "動物園・水族館": ("tourism=zoo", "tourism=aquarium", "leisure=zoo"),
}

M_PER_DEG_LON = 91000.0   # 北緯35度あたり
M_PER_DEG_LAT = 111000.0


def dist_m(a, b) -> float:
    return math.hypot((a[0] - b[0]) * M_PER_DEG_LON, (a[1] - b[1]) * M_PER_DEG_LAT)


def to_m(g):
    return shapely.transform(g, lambda c: c * [M_PER_DEG_LON, M_PER_DEG_LAT])


def kind_group(kinds: str) -> str:
    for g, words in KIND_GROUPS:
        if any(w in kinds for w in words):
            return g
    return "その他"


EDGE_STEP_M = 100   # 範囲の端の点の間隔
EDGE_MAX = 200      # 1つの範囲あたりの上限（大きな湖でも 200 点）


def edge_points(g):
    """範囲の外周（外側の輪）を、だいたい EDGE_STEP_M ごとに区切った点。"""
    pts = []
    polys = getattr(g, "geoms", [g])
    total = sum(to_m(p.exterior).length for p in polys)
    step = max(EDGE_STEP_M, total / EDGE_MAX)
    for p in polys:
        ring_m = to_m(p.exterior)
        n = max(1, int(ring_m.length // step))
        for i in range(n):
            q_ = ring_m.interpolate(i * ring_m.length / n)
            pts.append((q_.x / M_PER_DEG_LON, q_.y / M_PER_DEG_LAT))
    return pts


def pct(n, d):
    return f"{100 * n / d:.0f}%" if d else "—"


def q(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def load_areas(out: Path):
    areas = []
    with open(out / "areas.jsonl", encoding="utf-8") as f:
        for line in f:
            a = json.loads(line)
            a["geom"] = wkb.loads(a["geom"], hex=True)
            areas.append(a)
    return areas


def main():
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    out, spot_csv, station_csv, res = (Path(x).expanduser() for x in sys.argv[1:])
    res.mkdir(parents=True, exist_ok=True)

    areas = load_areas(out)
    by_qid = defaultdict(list)
    for a in areas:
        for qid in a["wikidata"].split(";"):
            if qid.strip():
                by_qid[qid.strip()].append(a["area_idx"])

    points = {}
    with open(out / "points.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            points[int(r["node_id"])] = r

    # 範囲ごとの入口：OSM の印（境界上の entrance・門）と、歩ける道が境界と交わる点
    tagged = defaultdict(list)
    with open(out / "ring_points.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            p = points[int(r["node_id"])]
            if p["access"] in NO_ACCESS:
                continue
            if p["entrance"] or p["barrier"] in ("gate", "entrance", "turnstile", "kissing_gate", "swing_gate", "stile"):
                tagged[int(r["area_idx"])].append((float(p["lon"]), float(p["lat"]), p["entrance"] or p["barrier"]))
    crossing = defaultdict(list)
    with open(out / "crossings.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["access"] in NO_ACCESS or r["foot"] in ("no", "private"):
                continue
            crossing[int(r["area_idx"])].append((float(r["lon"]), float(r["lat"]), r["highway"]))

    # 名前つきの範囲の索引（wikidata タグで結べなかった場所を、点を含む範囲で拾う）
    named = [a for a in areas if a["kind"] and a["name"]]
    tree = STRtree([a["geom"] for a in named])

    spots = []
    with open(spot_csv, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            pt = (float(r["lon"]), float(r["lat"]))
            if not KANTO.contains(shapely.Point(pt)):
                continue
            spots.append({**r, "pt": pt, "group": kind_group(r["kinds"] or "")})

    rows = []
    for s in spots:
        p = shapely.Point(s["pt"])
        match, area = "", None
        cands = by_qid.get(s["ext_id"], []) if s["source"] == "wikidata" else []
        if cands:
            # 同じ Q 番号の範囲が複数あれば、いちばん広いもの
            area = max((areas[i] for i in cands), key=lambda a: a["area_m2"])
            match = "wikidata"
        else:
            ok = COMPAT.get(s["group"])
            hits = [named[int(i)] for i in tree.query(p, predicate="within")]
            if ok:
                hits = [a for a in hits if a["kind"].startswith(ok)]
            if hits:
                area = min(hits, key=lambda a: a["area_m2"])  # いちばん狭い範囲（公園の中の寺なら寺）
                match = "contains"
        rec = {
            "spot_id": s["spot_id"], "ext_id": s["ext_id"], "name": s["name"], "group": s["group"],
            "lon": s["pt"][0], "lat": s["pt"][1], "match": match,
            "osm": "", "osm_name": "", "osm_kind": "", "area_m2": "", "inside": "",
            "n_tagged": 0, "n_cross": 0, "point_to_edge_m": "",
            "near_tagged_m": "", "near_cross_m": "",
        }
        if area is not None:
            ai = area["area_idx"]
            g = area["geom"]
            ts, cs = tagged.get(ai, []), crossing.get(ai, [])
            rec.update(
                osm=f"{area['osm_type']}/{area['osm_id']}", osm_name=area["name"], osm_kind=area["kind"],
                area_m2=area["area_m2"], inside=int(g.covers(p)),
                n_tagged=len(ts), n_cross=len(cs),
                point_to_edge_m=round(to_m(g.boundary).distance(to_m(p))),
            )
            if ts:
                rec["near_tagged_m"] = round(min(dist_m(s["pt"], t[:2]) for t in ts))
            if cs:
                rec["near_cross_m"] = round(min(dist_m(s["pt"], c[:2]) for c in cs))
            rec["_tagged"], rec["_cross"] = ts, cs
            # 入口の印も道との交わりも無いときの代わり：範囲の境界を EDGE_STEP_M ごとに区切った点
            if not ts and not cs:
                rec["_edge"] = edge_points(g)
        rows.append(rec)

    # 到達点の一覧（2 で DB に入れる）：OSM の入口の印、無ければ道との交わり
    with open(res / "spot_access_point.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["spot_id", "source", "kind", "lon", "lat"])
        for r in rows:
            for lon, lat, k in r.get("_tagged", []):
                w.writerow([r["spot_id"], "osm_entrance", k, f"{lon:.7f}", f"{lat:.7f}"])
            if not r.get("_tagged"):
                for lon, lat, k in r.get("_cross", []):
                    w.writerow([r["spot_id"], "osm_crossing", k, f"{lon:.7f}", f"{lat:.7f}"])
            for lon, lat in r.get("_edge", []):
                w.writerow([r["spot_id"], "area_edge", "", f"{lon:.7f}", f"{lat:.7f}"])

    cols = [k for k in rows[0] if not k.startswith("_")] if rows else []
    with open(res / "spot_access.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    md = ["# #31 OSM の範囲と入口（関東）", ""]
    md += spot_summary(rows)
    md += station_summary(out, points, station_csv, res, spots)
    (res / "entrance_osm.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


def spot_summary(rows):
    md = ["## 1. 場所（Wikidata）の範囲と入口", ""]
    md.append(f"関東の場所 {len(rows):,} 件。範囲は wikidata タグで結んだもの（wikidata）と、場所の点を含む名前つきの範囲（contains）。")
    md.append("入口：OSM の印＝範囲の境界に乗っている `entrance=*`・門（`barrier=gate` など）。道との交わり＝歩ける道（highway、access=private/no を除く）が境界と交わる点。")
    md.append("")
    md.append("| 種類 | 件数 | 範囲あり（wikidata / contains） | 入口の印あり | 印は無いが道と交わる | どちらも無い |")
    md.append("|---|---|---|---|---|---|")
    groups = [g for g, _ in KIND_GROUPS] + ["その他"]
    tot = Counter()
    for g in groups + ["計"]:
        rs = rows if g == "計" else [r for r in rows if r["group"] == g]
        n = len(rs)
        wd = sum(r["match"] == "wikidata" for r in rs)
        ct = sum(r["match"] == "contains" for r in rs)
        ar = wd + ct
        tg = sum(bool(r["match"]) and r["n_tagged"] > 0 for r in rs)
        cr = sum(bool(r["match"]) and r["n_tagged"] == 0 and r["n_cross"] > 0 for r in rs)
        no = ar - tg - cr
        md.append(f"| {g} | {n:,} | {ar:,}（{pct(ar, n)}：{wd:,} / {ct:,}） | {tg:,}（{pct(tg, ar)}） | {cr:,}（{pct(cr, ar)}） | {no:,}（{pct(no, ar)}） |")
    md.append("")
    md.append("割合：範囲ありは件数に対して、入口の列は範囲ありに対して。")
    md.append("")

    # 点と入口のずれ（範囲が 1 万 m² 以上：点と入口がずれて困りそうな広さ）
    big = [r for r in rows if r["match"] and r["area_m2"] and r["area_m2"] >= 10000]
    md.append(f"### 広い範囲（1万m² 以上、{len(big):,} 件）での、場所の点と入口のずれ")
    md.append("")
    md.append("| | 件数 | 中央値 | 80パーセンタイル | 最大 |")
    md.append("|---|---|---|---|---|")
    for label, key in (("点 → いちばん近い入口の印", "near_tagged_m"), ("点 → いちばん近い道との交わり", "near_cross_m"), ("点 → 範囲の境界", "point_to_edge_m")):
        xs = [r[key] for r in big if r[key] != ""]
        if xs:
            md.append(f"| {label} | {len(xs):,} | {q(xs, .5):,.0f}m | {q(xs, .8):,.0f}m | {max(xs):,.0f}m |")
    out_pt = sum(1 for r in big if r["inside"] == 0)
    md.append("")
    md.append(f"- 場所の点が範囲の外にある：{out_pt:,} 件（{pct(out_pt, len(big))}）。wikidata で結んだ範囲で、Wikidata の座標が範囲から外れているもの")
    md.append("")
    return md


def station_summary(out, points, station_csv, res, spots):
    md = ["## 4. 駅の出入口", ""]
    groups = defaultdict(list)
    names = {}
    with open(station_csv, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["lon"] in ("", None):
                continue
            groups[r["group_code"]].append((float(r["lon"]), float(r["lat"])))
            names.setdefault(r["group_code"], r["station_name"])
    cent = {g: (statistics.fmean(p[0] for p in ps), statistics.fmean(p[1] for p in ps)) for g, ps in groups.items()}
    cent = {g: c for g, c in cent.items() if KANTO.contains(shapely.Point(c))}

    exits = [(float(p["lon"]), float(p["lat"]), p["railway"], p["name"], nid) for nid, p in points.items()
             if p["railway"] in ("subway_entrance", "train_station_entrance") and p["access"] not in NO_ACCESS]
    st_nodes = [(float(p["lon"]), float(p["lat"]), p["name"]) for p in points.values()
                if p["railway"] in ("station", "halt") and p["name"]]

    gl = list(cent)
    gtree = STRtree([shapely.Point(cent[g]) for g in gl])
    # 出入口は、500m 以内でいちばん近い駅グループに割り当てる
    by_group = defaultdict(list)
    for e in exits:
        i = gtree.nearest(shapely.Point(e[:2]))
        g = gl[int(i)]
        d = dist_m(e[:2], cent[g])
        if d <= 500:
            by_group[g].append((e, d))
    # 同じ名前の OSM の駅（railway=station）の点
    st_by_name = defaultdict(list)
    for lon, lat, n in st_nodes:
        st_by_name[n].append((lon, lat))

    with open(res / "station_exit.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group_code", "station_name", "lon", "lat", "n_exit", "near_exit_m", "osm_station_m"])
        rows = []
        for g in gl:
            c = cent[g]
            es = by_group.get(g, [])
            near = round(min(d for _, d in es)) if es else ""
            ss = [dist_m(c, s) for s in st_by_name.get(names[g], [])]
            osd = round(min(ss)) if ss and min(ss) <= 1000 else ""
            w.writerow([g, names[g], f"{c[0]:.7f}", f"{c[1]:.7f}", len(es), near, osd])
            rows.append((g, len(es), near, osd))
    with open(res / "station_exit_point.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group_code", "node_id", "railway", "name", "lon", "lat", "dist_m"])
        for g, es in by_group.items():
            for e, d in es:
                w.writerow([g, e[4], e[2], e[3], f"{e[0]:.7f}", f"{e[1]:.7f}", round(d)])

    n = len(rows)
    has = [r for r in rows if r[1] > 0]
    md.append(f"関東の駅グループ {n:,}。OSM の `railway=subway_entrance`・`railway=train_station_entrance` は {len(exits):,} 点で、500m 以内のいちばん近い駅グループに割り当てた。")
    md.append("")
    md.append(f"- 出入口が1つ以上ある駅グループ：{len(has):,}（{pct(len(has), n)}）。1グループあたりの数の中央値 {q([r[1] for r in has], .5):.0f}、最大 {max([r[1] for r in has], default=0)}")
    ds = [r[2] for r in has]
    md.append(f"- ホームの重心 → いちばん近い出入口：中央値 {q(ds, .5):.0f}m、80パーセンタイル {q(ds, .8):.0f}m")
    os_ = [r[3] for r in rows if r[3] != ""]
    md.append(f"- 同じ名前の OSM の駅の点（railway=station）が 1km 以内にある：{len(os_):,}（{pct(len(os_), n)}）。重心との距離の中央値 {q(os_, .5):.0f}m")
    md.append("")

    # 出入口の多い駅
    top = sorted(rows, key=lambda r: -r[1])[:10]
    md.append("出入口の多い駅グループ：" + "、".join(f"{names[g]} {k}" for g, k, _, _ in top))
    md.append("")

    # 東京ステーションギャラリーなど、駅の中・すぐそばの場所の例
    md.append("### 例：駅の代表点を出入口に変えると、場所までの直線距離がどう変わるか")
    md.append("")
    md.append("| 場所 | 駅 | ホームの重心から | いちばん近い出入口から | 出入口 |")
    md.append("|---|---|---|---|---|")
    for target in ("東京ステーションギャラリー", "三菱一号館美術館", "新宿御苑", "旧古河庭園", "象の鼻パーク"):
        s = next((s for s in spots if s["name"] == target), None)
        if not s:
            continue
        g = min(cent, key=lambda g: dist_m(cent[g], s["pt"]))
        es = by_group.get(g, [])
        d0 = dist_m(cent[g], s["pt"])
        if es:
            e, _ = min(es, key=lambda ed: dist_m(ed[0][:2], s["pt"]))
            md.append(f"| {target} | {names[g]} | {d0:.0f}m | {dist_m(e[:2], s['pt']):.0f}m | {e[3] or e[2]} |")
        else:
            md.append(f"| {target} | {names[g]} | {d0:.0f}m | — | 出入口なし |")
    md.append("")
    return md


if __name__ == "__main__":
    main()
