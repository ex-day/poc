"""R5（r5py）で「寄り道できる駅」を全駅について出し、pgRouting（reach.reachable）と比べる（Issue #26）。

OTP 2.11 には等時間線の API が無いので、一対多の所要時間の表を出すのが本業の R5 で試す。
入力は OTP と同じ：関東の OSM と、make_gtfs.py の仮 GTFS。

使い方（R5 用の venv で。手順は README.md）：
  python compare_r5_reach.py <OSM の pbf> <仮 GTFS の zip>

比べること：
  1. 交通網を作る時間と、メモリ（この Python の使うメモリ。R5 は同じプロセスの中の Java で動く）
  2. 全駅の到達判定：条件ごとに、四角の中の駅のまとまりを「両方／pgRouting だけ／R5 だけ／どちらも行けない」に分ける。
     R5 は出発時刻を10分の幅でずらした中の、中央値（p50）と安全側（p80）
  3. 1回の寄り道検索にかかる時間（行き：出発駅 → 全駅、帰り：帰着駅 → 全駅）
  4. 帰りの近似の確かめ（1条件だけ）：本来の帰り（全駅 → 帰着駅、多対一）と、近似（帰着駅 → 全駅）の差と時間
"""
import os
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# r5py は起動時の引数（--max-memory）で Java のメモリを決める。import より前に、入力のパスを取り出してから入れ替える
ARGS = sys.argv[1:]
sys.argv = [sys.argv[0], "--max-memory", os.environ.get("EXDAY_R5_MEMORY", "10G")]

import geopandas as gpd  # noqa: E402
import psutil  # noqa: E402
import psycopg  # noqa: E402
import r5py  # noqa: E402
from shapely.geometry import Point  # noqa: E402

DSN = os.environ.get("EXDAY_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/ex_day_poc")
BBOX = tuple(float(x) for x in os.environ.get("EXDAY_GTFS_BBOX", "138.4,34.8,140.9,37.2").split(","))
HERE = Path(__file__).resolve().parent
DATE = "2026-11-10"   # 平日（仮の時刻表は毎日同じ）
WINDOW_MIN = 10       # 出発時刻をずらす幅（分）。仮ダイヤの間隔（多くは10分おき）に合わせる
MODES = [r5py.TransportMode.TRANSIT, r5py.TransportMode.WALK]

# （名前, 出発のグループ, 帰着のグループ, 出発時刻, 使える時間（分）, 滞在（分））
CONDITIONS = [
    ("新横浜 → 東京", "004501", "003766", "10:00", 180, 60),
    ("八王子 → 千葉", "003947", "004173", "09:00", 300, 90),
    ("新宿 → 鎌倉", "003700", "005055", "09:00", 240, 60),
]


def rss_gb():
    return psutil.Process().memory_info().rss / 1024 ** 3


def at(hhmm, add_min=0):
    return datetime.fromisoformat(f"{DATE}T{hhmm}:00") + timedelta(minutes=add_min)


def matrix(net, origins, destinations, departure, max_min):
    t0 = time.perf_counter()
    m = r5py.TravelTimeMatrix(
        net, origins=origins, destinations=destinations, departure=departure,
        departure_time_window=timedelta(minutes=WINDOW_MIN), percentiles=[50, 80],
        transport_modes=MODES, max_time=timedelta(minutes=max_min), snap_to_network=True)
    sec = time.perf_counter() - t0
    cols = {c: c for c in m.columns}
    p50 = cols.get("travel_time_p50", "travel_time")
    p80 = cols.get("travel_time_p80", p50)
    return m, p50, p80, sec


def main() -> None:
    if len(ARGS) != 2 or not ARGS[0].endswith(".pbf"):
        sys.exit(__doc__)
    osm, gtfs = ARGS
    out = ["# R5 と pgRouting の、全駅の到達判定の比較（Issue #26）", "",
           f"- R5：r5py {r5py.__version__}、Java のメモリ上限 {os.environ.get('EXDAY_R5_MEMORY', '10G')}",
           f"- 入力：{Path(osm).name}、{Path(gtfs).name}（make_gtfs.py の仮 GTFS）",
           f"- 出発時刻を {WINDOW_MIN}分の幅でずらし、中央値（p50）と安全側（p80）で判定",
           "- pgRouting：reach.reachable（乗り始めの待ち5分・乗換10分を含む）",
           "- 帰り：帰着駅 → 全駅で近似（仮 GTFS は上りと下りが同じ作り）。本来の多対一との差は、最初の条件で確かめる",
           f"- 対象：四角 {BBOX} の中の駅のまとまり（出発・帰着の駅を除く）", ""]

    mem0 = rss_gb()
    t0 = time.perf_counter()
    try:
        net = r5py.TransportNetwork(osm, [gtfs])
        gtfs_note = "GTFS はそのまま読めた"
    except Exception as e:  # noqa: BLE001  仮 GTFS が R5 の検査に引っかかったら、誤りを許して読み直す
        print(f"GTFS の読み込みで誤り：{e}。allow_errors=True で読み直す")
        net = r5py.TransportNetwork(osm, [gtfs], allow_errors=True)
        gtfs_note = f"GTFS に R5 の検査の誤りがあり、allow_errors=True で読んだ（{str(e)[:200]}）"
    build_sec = time.perf_counter() - t0
    mem1 = rss_gb()
    out += ["## 交通網", "",
            f"- 作る時間 {build_sec:.0f}秒（2回目以降は r5py のキャッシュを使うので短い）",
            f"- メモリ（この Python 全体）：作る前 {mem0:.1f}GB → 作った後 {mem1:.1f}GB",
            f"- {gtfs_note}", ""]
    print(f"交通網：{build_sec:.0f}秒、{mem1:.1f}GB")

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("""SELECT group_code, min(station_name), ST_X(ST_Centroid(ST_Collect(geom))), ST_Y(ST_Centroid(ST_Collect(geom)))
                       FROM reach.station GROUP BY 1""")
        groups = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
        inside = [g for g, (_, x, y) in groups.items() if BBOX[0] <= x <= BBOX[2] and BBOX[1] <= y <= BBOX[3]]
        stations = gpd.GeoDataFrame({"id": inside}, geometry=[Point(groups[g][1], groups[g][2]) for g in inside], crs="EPSG:4326")

        def one(g):
            return gpd.GeoDataFrame({"id": [g]}, geometry=[Point(groups[g][1], groups[g][2])], crs="EPSG:4326")

        search_secs = []
        for ci, (title, src, dst, dep, avail, stay) in enumerate(CONDITIONS):
            move_max = avail - stay
            cur.execute("SELECT group_code, t1_min, t2_min FROM reach.reachable(%s, %s, 1000, 0)", (src, dst))
            pg = {r[0]: (float(r[1]), float(r[2])) for r in cur.fetchall() if r[0] in set(inside)}

            m1, a50, a80, s1 = matrix(net, one(src), stations, at(dep), move_max)
            # 帰り（近似）：帰着駅から全駅へ。期限の少し前に出る
            m2, b50, b80, s2 = matrix(net, one(dst), stations, at(dep, avail - move_max // 2), move_max)
            search_secs.append(s1 + s2)
            t1 = {r.to_id: (getattr(r, a50), getattr(r, a80)) for r in m1.itertuples()}
            t2 = {r.to_id: (getattr(r, b50), getattr(r, b80)) for r in m2.itertuples()}
            print(f"{title}：行き {s1:.1f}秒、帰り {s2:.1f}秒")

            def ok(d, g, k):
                v = d.get(g)
                return None if v is None or v[k] != v[k] else float(v[k])   # NaN は届かない

            rows = []
            for g, (p1, p2) in pg.items():
                pg_ok = p1 + stay + p2 <= avail
                res = {}
                for label, k in (("中央値", 0), ("安全側", 1)):
                    o1, o2 = ok(t1, g, k), ok(t2, g, k)
                    res[label] = (o1, o2, o1 is not None and o2 is not None and o1 + stay + o2 <= avail)
                rows.append((g, groups[g][0], p1, p2, pg_ok, res))

            out += [f"## {title}（{dep} 発、使える時間 {avail}分、滞在 {stay}分）", "",
                    f"- 1回の寄り道検索（行き＋帰り）：{s1 + s2:.1f}秒（行き {s1:.1f}秒、帰り {s2:.1f}秒）", "",
                    "| R5 の判定 | 両方で行ける | pgRouting だけ | R5 だけ | どちらも行けない |", "|---|---|---|---|---|"]
            for label in ("中央値", "安全側"):
                both = sum(1 for r in rows if r[4] and r[5][label][2])
                pg_only = sum(1 for r in rows if r[4] and not r[5][label][2])
                r5_only = sum(1 for r in rows if not r[4] and r[5][label][2])
                out.append(f"| {label} | {both:,} | {pg_only:,} | {r5_only:,} | {len(rows) - both - pg_only - r5_only:,} |")
            out.append("")
            f = lambda x: "—" if x is None else f"{x:.0f}"  # noqa: E731
            for label, cond in (("pgRouting だけ", lambda r: r[4] and not r[5]["安全側"][2]),
                                ("R5 だけ", lambda r: not r[4] and r[5]["安全側"][2])):
                ex = [r for r in rows if cond(r)]
                ex.sort(key=lambda r: abs((r[2] + r[3]) - ((r[5]["安全側"][0] or 999) + (r[5]["安全側"][1] or 999))), reverse=True)
                if ex:
                    out += [f"{label}（安全側。差の大きい順に10駅）：", "",
                            "| 駅 | pg t1 | pg t2 | R5 t1 | R5 t2 |", "|---|---|---|---|---|"]
                    out += [f"| {r[1]} | {r[2]:.0f} | {r[3]:.0f} | {f(r[5]['安全側'][0])} | {f(r[5]['安全側'][1])} |" for r in ex[:10]]
                    out.append("")
            d1 = [r[5]["中央値"][0] - r[2] for r in rows if r[4] and r[5]["中央値"][0] is not None]
            d2 = [r[5]["中央値"][1] - r[3] for r in rows if r[4] and r[5]["中央値"][1] is not None]
            if d1 and d2:
                out += [f"- pgRouting で行ける駅の、R5（中央値）− pg：行き 中央値 {statistics.median(d1):+.0f}分、"
                        f"帰り 中央値 {statistics.median(d2):+.0f}分", ""]

            # 帰りの近似の確かめ（最初の条件だけ）：全駅 → 帰着駅（多対一）
            if ci == 0:
                near = [g for g in pg if t2.get(g) and t2[g][0] == t2[g][0] and t2[g][0] <= move_max]
                sample = stations[stations["id"].isin(near)]
                m3, c50, _, s3 = matrix(net, sample, one(dst), at(dep, avail - move_max // 2), move_max)
                diff = [getattr(r, c50) - t2[r.from_id][0] for r in m3.itertuples() if getattr(r, c50) == getattr(r, c50)]
                out += ["### 帰りの近似の確かめ", "",
                        f"- 帰着駅 → 全駅（近似、一対多）：{s2:.1f}秒",
                        f"- 全駅 → 帰着駅（本来、多対一）：{len(sample):,}駅で {s3:.1f}秒",
                        (f"- 本来 − 近似：中央値 {statistics.median(diff):+.1f}分、最小 {min(diff):+.0f}分、最大 {max(diff):+.0f}分"
                         if diff else "- 比べられた駅なし"), ""]
                print(f"帰りの確かめ：多対一 {s3:.1f}秒")

    out += ["## まとめ（速さとメモリ）", "",
            f"- 1回の寄り道検索（行き＋帰り、一対多×2）：中央値 {statistics.median(search_secs):.1f}秒、最大 {max(search_secs):.1f}秒",
            f"- メモリ（最後）：{rss_gb():.1f}GB", ""]
    path = HERE.parent / "results" / "r5_reach_compare.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"書いた：{path}")


if __name__ == "__main__":
    main()
