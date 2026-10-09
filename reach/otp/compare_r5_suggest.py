"""寄り道の提案を R5 の時間でやり直し、pgRouting（#23 の reach.suggest_walk）と比べる（Issue #26、実験4）。

R5 は駅だけでなく、場所（reach.spot）への時間も直接出せる。出発地 → 全ての場所（行き）と、
帰着駅 → 全ての場所（帰りの近似）を一対多で1回ずつ出し、使える時間に収まる場所を候補にする。
駅を経由しないので、代表の駅や駅から場所への歩きの事前計算（station_spot_walk）が要らない。

使い方（リポジトリ直下の .venv で。JAVA_HOME を設定しておく）：
  EXDAY_MODEL=cl-nagoya/ruri-v3-30m python compare_r5_suggest.py <OSM の pbf> <仮 GTFS の zip>

比べること（条件と興味は #6・#23 と同じ）：
  1. 候補の集合：両方／pgRouting だけ／R5 だけ
  2. 並べ方（#6 と同じ「近さ − 遠回り1時間につき0.05」）の上位10件の重なり
  3. 上位10件の降りる駅：R5 の詳しい経路（DetailedItineraries）で、最後に降りる駅を出し、pgRouting の代表の駅と比べる

時間の扱い：
  - pgRouting（suggest_walk）は、片道10分（往復20分）までの歩きを滞在時間の中に含める（「駅を出て戻るまでの寄り道の枠」）
  - R5 の時間は、駅から場所までの歩きを含む（戸口から戸口）。pgRouting と同じ扱いで比べるため、
    R5 も往復20分までの歩きは滞在に含めたとみなし、t1 ＋ t2 ＋ 滞在 ≦ 使える時間 ＋ 20 を候補とする
  - 比べる場所は、#23 の歩きのグラフ（N13、5339）に結び付いた場所だけ（reach.spot_walk）
"""
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ARGS = sys.argv[1:]
sys.argv = [sys.argv[0], "--max-memory", os.environ.get("EXDAY_R5_MEMORY", "10G")]

import geopandas as gpd  # noqa: E402
import r5py  # noqa: E402
from shapely.geometry import Point  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "vector"))
from common import Embedder, connect  # noqa: E402

DATE = "2026-11-10"
WINDOW_MIN = 10
MODES = [r5py.TransportMode.TRANSIT, r5py.TransportMode.WALK]
SCENARIO = ("新横浜 → 東京（3時間、滞在60分）", "004501", "003766", "10:00", 180, 60)
INTERESTS = ["城を見たい", "温泉に入りたい", "湖を見たい", "庭園を散歩したい", "魚料理を食べたい", "港町の歴史を知りたい"]
TOP_N = 10
C_ALPHA = 0.05      # 遠回り1時間につき0.05を引く（#6 と同じ）
WALK_IN_STAY = 20   # 滞在に含める歩き（往復）。suggest_walk と同じ

PG_COLS = ["spot_id", "source", "name", "kinds", "group_code", "station_name", "dist_m",
           "t1_min", "t2_min", "slack_min", "detour_min", "sim", "walk_min"]


def at(hhmm, add_min=0):
    return datetime.fromisoformat(f"{DATE}T{hhmm}:00") + timedelta(minutes=add_min)


def ttm(net, origins, destinations, departure, max_min):
    m = r5py.TravelTimeMatrix(
        net, origins=origins, destinations=destinations, departure=departure,
        departure_time_window=timedelta(minutes=WINDOW_MIN), percentiles=[50],
        transport_modes=MODES, max_time=timedelta(minutes=max_min), snap_to_network=True)
    col = "travel_time_p50" if "travel_time_p50" in m.columns else "travel_time"
    return {r.to_id: float(getattr(r, col)) for r in m.itertuples() if getattr(r, col) == getattr(r, col)}


def last_stop(net, origin, dests, departure, names):
    """行きの経路で、最後に降りる駅（R5 の詳しい経路の、最後の乗車の降りる停留所）"""
    if dests.empty:
        return {}
    it = r5py.DetailedItineraries(net, origins=origin, destinations=dests, departure=departure,
                                  transport_modes=MODES, snap_to_network=True)
    best = {}
    for (to_id, option), g in it.groupby(["to_id", "option"]):
        total = sum((t.total_seconds() if hasattr(t, "total_seconds") else 0) for t in g["travel_time"])
        total += sum((w.total_seconds() if hasattr(w, "total_seconds") else 0) for w in g["wait_time"])
        transit = g[g["end_stop_id"].notna()]
        stop = str(transit["end_stop_id"].iloc[-1]).split(":")[-1] if len(transit) else None
        walk = g["distance"].iloc[-1] if len(g) else None
        if to_id not in best or total < best[to_id][0]:
            best[to_id] = (total, names.get(stop, stop) if stop else "（歩きだけ）", walk)
    return {k: (v[1], v[2]) for k, v in best.items()}


def main() -> None:
    if len(ARGS) != 2 or not ARGS[0].endswith(".pbf"):
        sys.exit(__doc__)
    osm, gtfs = ARGS
    emb = Embedder()
    title, src, dst, dep, avail, stay = SCENARIO
    out = ["# 寄り道の提案：R5 と pgRouting（suggest_walk）の比較（Issue #26、実験4）", "",
           f"- モデル：{emb.model_name}。条件：{title}、{dep} 発",
           f"- R5：出発地 → 場所（行き）、帰着駅 → 場所（帰りの近似）を一対多で1回ずつ。出発時刻の幅 {WINDOW_MIN}分の中央値",
           f"- 候補：pgRouting は suggest_walk。R5 は t1 ＋ t2 ＋ 滞在 ≦ 使える時間 ＋ {WALK_IN_STAY}（往復{WALK_IN_STAY}分までの歩きは滞在に含める。suggest_walk と同じ扱い）",
           "- 並べ方：近さ − 遠回り1時間につき0.05（#6）。遠回り ＝ t1 ＋ t2 − 出発地から帰着駅への直行の時間",
           "- 比べる場所：#23 の歩きのグラフに結び付いた場所（reach.spot_walk）", ""]

    t0 = time.perf_counter()
    net = r5py.TransportNetwork(osm, [gtfs])
    print(f"交通網：{time.perf_counter() - t0:.0f}秒")

    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT group_code, min(station_name), ST_X(ST_Centroid(ST_Collect(geom))), ST_Y(ST_Centroid(ST_Collect(geom)))
                       FROM reach.station GROUP BY 1""")
        groups = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
        names = {g: v[0] for g, v in groups.items()}
        cur.execute("""SELECT s.spot_id, s.name, s.kinds, ST_X(s.geom), ST_Y(s.geom)
                       FROM reach.spot s JOIN reach.spot_walk w USING (spot_id)""")
        spots = {r[0]: r[1:] for r in cur.fetchall()}

        def pt(g):
            return gpd.GeoDataFrame({"id": [g]}, geometry=[Point(groups[g][1], groups[g][2])], crs="EPSG:4326")

        dests = gpd.GeoDataFrame({"id": list(spots)}, geometry=[Point(v[2], v[3]) for v in spots.values()], crs="EPSG:4326")
        move_max = avail - stay + WALK_IN_STAY
        t0 = time.perf_counter()
        t1 = ttm(net, pt(src), dests, at(dep), move_max)
        t2 = ttm(net, pt(dst), dests, at(dep, avail // 2), move_max)
        direct = ttm(net, pt(src), pt(dst), at(dep), avail).get(dst)
        sec = time.perf_counter() - t0
        cand = {s: (t1[s], t2[s]) for s in spots if s in t1 and s in t2 and t1[s] + t2[s] + stay <= avail + WALK_IN_STAY}
        out += [f"- R5：場所 {len(spots):,} 件への行き・帰り＋直行の時間を {sec:.1f}秒で出した。直行（新横浜 → 東京）{direct:.0f}分", ""]

        for interest in INTERESTS:
            qvec = emb.query([interest])[0]
            cur.execute("SELECT * FROM reach.suggest_walk(%s, %s, %s, %s, %s)", (src, dst, avail, stay, qvec))
            pg = {r[0]: dict(zip(PG_COLS, r)) for r in cur.fetchall()}
            cur.execute("SELECT spot_id, 1 - (embedding <=> %s) FROM reach.spot WHERE spot_id = ANY(%s)", (qvec, list(cand)))
            sim = {r[0]: float(r[1]) for r in cur.fetchall()}
            r5 = {s: {"t1": a, "t2": b, "detour": a + b - direct, "sim": sim[s],
                      "score": sim[s] - C_ALPHA * (a + b - direct) / 60} for s, (a, b) in cand.items() if s in sim}
            for p in pg.values():
                p["score"] = float(p["sim"]) - C_ALPHA * float(p["detour_min"]) / 60
            ip, ir = set(pg), set(r5)
            top_pg = sorted(pg, key=lambda s: -pg[s]["score"])[:TOP_N]
            top_r5 = sorted(r5, key=lambda s: -r5[s]["score"])[:TOP_N]
            stops = last_stop(net, pt(src), dests[dests["id"].isin(top_r5)], at(dep), names)
            out += [f"## 「{interest}」", "",
                    f"候補：pgRouting {len(ip):,} 件、R5 {len(ir):,} 件。両方 {len(ip & ir):,}、pgRouting だけ {len(ip - ir):,}、R5 だけ {len(ir - ip):,}。"
                    f"上位{TOP_N}件の重なり {len(set(top_pg) & set(top_r5))}件", "",
                    f"| 順位 | pgRouting（代表の駅・歩き） | R5（降りる駅） |", "|---|---|---|"]
            for i in range(TOP_N):
                a = top_pg[i] if i < len(top_pg) else None
                b = top_r5[i] if i < len(top_r5) else None
                ca = (f"{pg[a]['name']}（{pg[a]['station_name']}駅・{float(pg[a]['walk_min']):.0f}分、遠回り {int(pg[a]['detour_min'])}分）"
                      if a else "—")
                cb = (f"{spots[b][0]}（{stops.get(b, ('?', None))[0]}、遠回り {r5[b]['detour']:.0f}分）" if b else "—")
                out.append(f"| {i + 1} | {ca} | {cb} |")
            out.append("")
            # 両方の上位にある場所の、降りる駅の一致
            same = [s for s in set(top_pg) & set(top_r5)]
            if same:
                agree = sum(1 for s in same if stops.get(s, ("",))[0] == pg[s]["station_name"])
                out += [f"- 両方の上位にある {len(same)}件のうち、pgRouting の代表の駅と R5 の降りる駅が同じもの {agree}件", ""]
            print(f"{interest}：pg {len(ip)}、R5 {len(ir)}、上位の重なり {len(set(top_pg) & set(top_r5))}")

    path = HERE.parent / "results" / "r5_suggest_compare.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"書いた：{path}")


if __name__ == "__main__":
    main()
