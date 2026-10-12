"""R5 の寄り道の検索に、1件あたり CPU をどれだけ使うかと、同時に来たときに何件さばけるかを測る（Issue #30）。

使い方（r5_scale.py で交通網を保存したあと。リポジトリ直下の .venv で、JAVA_HOME を設定しておく）：
  EXDAY_R5_BBOX=134.8,33.4,142.1,41.6 EXDAY_R5_MEMORY=8G \\
    python reach/otp/r5_load.py <OSM の pbf> <仮 GTFS の zip> <ラベル>

寄り道の検索1回＝出発駅 → 交通網の中の全駅（行き）と、帰着駅 → 全駅（帰りの近似）の一対多を1本ずつ。
1回の検索は1つのスレッドで、行き・帰りの順に計算する（r5py の TravelTimeMatrix は、呼んだスレッドで R5 を動かす）。
出発駅・帰着駅は、交通網の中で路線の多い駅グループ（上位 30）から、出発時刻は 9〜18時から、決まった乱数で選ぶ。

測ること：
  A. 1件ずつ流したときの、1件あたりの時間と CPU 時間（プロセス全体。Java の GC なども含む）
     出発時刻の幅（10分・5分・1分）× 探す時間の上限（300分・150分）
  B. 同時に 1・2・4・8 件流したときの、応答時間（中央値・95パーセンタイル）、1秒あたりの件数、Java ヒープの最大
     A の今の設定（幅10分・300分）と、軽い設定（幅5分・150分）の2つ

結果は reach/results/r5_load_<ラベル>.md。
"""
import os
import random
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

ARGS = sys.argv[1:]
MAX_MEMORY = os.environ.get("EXDAY_R5_MEMORY", "8G")
sys.argv = [sys.argv[0], "--max-memory", MAX_MEMORY]

import geopandas as gpd  # noqa: E402
import jpype  # noqa: E402
import psutil  # noqa: E402
import psycopg  # noqa: E402
import r5py  # noqa: E402
from shapely.geometry import Point  # noqa: E402

DSN = os.environ.get("EXDAY_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/ex_day_poc")
HERE = Path(__file__).resolve().parent
BBOX = tuple(float(x) for x in os.environ["EXDAY_R5_BBOX"].split(",")) if os.environ.get("EXDAY_R5_BBOX") else None
DATE = "2026-11-10"
MODES = [r5py.TransportMode.TRANSIT, r5py.TransportMode.WALK]
GB = 1024 ** 3
N_SEQ = 10                       # A：設定ごとに流す件数
CONCURRENCY = [1, 2, 4, 8]       # B：同時に流す数
CONFIGS_A = [(10, 300), (5, 300), (1, 300), (10, 150), (5, 150), (1, 150)]   # （出発時刻の幅（分）, 探す上限（分））
CONFIGS_B = [(10, 300), (5, 150)]


def inside(lon, lat):
    return BBOX is None or (BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3])


class Sampler:
    """プロセスの RSS と Java ヒープの使用量を 0.2秒ごとに測り、最大を取る。"""

    def __init__(self):
        self.proc = psutil.Process()
        self.rt = jpype.JClass("java.lang.Runtime").getRuntime()
        self.rss = self.heap = 0
        self.stop = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self.stop:
            self.rss = max(self.rss, self.proc.memory_info().rss)
            self.heap = max(self.heap, self.rt.totalMemory() - self.rt.freeMemory())
            time.sleep(0.2)

    def reset(self):
        self.rss = self.proc.memory_info().rss
        self.heap = self.rt.totalMemory() - self.rt.freeMemory()


def cpu_sec():
    t = psutil.Process().cpu_times()
    return t.user + t.system


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def main():
    if len(ARGS) != 3:
        sys.exit(__doc__)
    osm, gtfs, label = Path(ARGS[0]).expanduser().resolve(), Path(ARGS[1]).expanduser().resolve(), ARGS[2]
    for p in (osm, gtfs):
        if not p.is_file():
            sys.exit(f"ファイルが無い：{p}")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("""SELECT group_code, ST_X(ST_Centroid(ST_Collect(geom))), ST_Y(ST_Centroid(ST_Collect(geom))),
                              min(station_name), count(*)
                       FROM reach.station GROUP BY group_code""")
        groups = [g for g in cur.fetchall() if inside(g[1], g[2])]
    stations = gpd.GeoDataFrame({"id": [g[0] for g in groups]},
                                geometry=[Point(g[1], g[2]) for g in groups], crs="EPSG:4326")
    hubs = sorted(groups, key=lambda g: (-g[4], g[0]))[:30]
    rnd = random.Random(31)
    jobs = []
    for _ in range(64):
        a, b = rnd.sample(hubs, 2)
        jobs.append((a, b, 9 * 60 + rnd.randrange(0, 9 * 60, 5)))

    t0 = time.perf_counter()
    net = r5py.TransportNetwork(str(osm), [str(gtfs)])
    t_load = time.perf_counter() - t0
    sampler = Sampler()

    def one_to_many(g, dep_min, window, max_min):
        origin = gpd.GeoDataFrame({"id": [g[0]]}, geometry=[Point(g[1], g[2])], crs="EPSG:4326")
        dep = datetime.fromisoformat(DATE) + timedelta(minutes=dep_min)
        m = r5py.TravelTimeMatrix(
            net, origins=origin, destinations=stations.copy(), departure=dep,
            departure_time_window=timedelta(minutes=window), percentiles=[80],
            transport_modes=MODES, max_time=timedelta(minutes=max_min), snap_to_network=True)
        return m

    def search(job, window, max_min):
        """寄り道の検索1回（行き・帰り）の、かかった時間（秒）"""
        a, b, dep = job
        t = time.perf_counter()
        one_to_many(a, dep, window, max_min)
        one_to_many(b, dep + 120, window, max_min)
        return time.perf_counter() - t

    # 温める（最初の数件は JIT などで遅い）
    for job in jobs[:3]:
        search(job, 10, 300)

    out = [f"# R5 の寄り道の検索の負荷：{label}（Issue #30）", "",
           f"- 日時：{datetime.now():%Y-%m-%d %H:%M}、r5py {r5py.__version__}、Java のヒープの上限 {MAX_MEMORY}",
           f"- マシン：{os.uname().sysname} {os.uname().machine}、メモリ {psutil.virtual_memory().total / GB:.0f}GB、CPU {psutil.cpu_count()}",
           f"- 交通網：{osm.name}（読むのに {t_load:.0f}秒）、行き先の駅グループ {len(groups):,}",
           f"- 検索1回＝行き＋帰りの一対多。出発・帰着は路線の多い駅グループ上位30から（{hubs[0][3]}・{hubs[1][3]}・{hubs[2][3]} など）",
           "- CPU 時間はプロセス全体（Java の GC なども含む）", "",
           "## A. 1件ずつ流したとき", "",
           "| 出発時刻の幅 | 探す上限 | 1件の時間（中央値） | 1件の CPU 時間（平均） | CPU 時間 ÷ 時間 |",
           "|---|---|---|---|---|"]
    for window, max_min in CONFIGS_A:
        c0 = cpu_sec()
        secs = [search(job, window, max_min) for job in jobs[:N_SEQ]]
        cpu = (cpu_sec() - c0) / N_SEQ
        out.append(f"| {window}分 | {max_min}分 | {statistics.median(secs):.2f}秒 | {cpu:.2f}秒 | {cpu / statistics.mean(secs):.1f} |")
        print(out[-1], flush=True)

    out += ["", "## B. 同時に流したとき", "",
            "| 設定 | 同時 | 件数 | 応答（中央値） | 応答（95%） | 1秒あたり | CPU 時間/件 | Java ヒープの最大 | RSS の最大 |",
            "|---|---|---|---|---|---|---|---|---|"]
    for window, max_min in CONFIGS_B:
        for c in CONCURRENCY:
            n = max(16, 4 * c)
            batch = (jobs * 2)[:n]
            sampler.reset()
            c0, t0 = cpu_sec(), time.perf_counter()
            with ThreadPoolExecutor(max_workers=c) as ex:
                lat = list(ex.map(lambda j: search(j, window, max_min), batch))
            wall = time.perf_counter() - t0
            cpu = (cpu_sec() - c0) / n
            out.append(f"| 幅{window}分・{max_min}分 | {c} | {n} | {q(lat, .5):.2f}秒 | {q(lat, .95):.2f}秒 | "
                       f"{n / wall:.2f}件 | {cpu:.2f}秒 | {sampler.heap / GB:.1f}GB | {sampler.rss / GB:.1f}GB |")
            print(out[-1], flush=True)
    sampler.stop = True

    out += ["", "## 見方", "",
            "- 1秒あたりにさばける件数は、おおよそ「vCPU の数 ÷ 1件の CPU 時間」。CPU が2つのマシンなら、A の CPU 時間で割って見積もる",
            "- 同時の数を増やしても1秒あたりの件数が増えなくなったところが、このマシンの上限。応答時間は同時の数に比例して延びる",
            ""]
    path = HERE.parent / "results" / f"r5_load_{label}.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
