"""全国の OSM と仮 GTFS で R5 の交通網を作り、作る・常に使う・探すときのメモリと時間を測る（Issue #30）。

R5 は r5py から、Python と同じプロセスの中の Java で動く。メモリは、このプロセス全体の RSS（Java を含む）を
0.2秒ごとに測った最大と、Java のヒープの使用量（Runtime.totalMemory − freeMemory）を記録する。

r5py は、作った交通網を ~/.cache/r5py/<入力の sha256>.transport_network に保存し、次からはそれを読む。
  - 1回目（キャッシュが無い）：OSM の読み込み（MapDB に書く）→ 道路 → 仮 GTFS → 保存。「作る」ときのメモリと時間
  - 2回目以降：保存した交通網を読むだけ。「常に動くサーバーの起動」に近い
どちらだったかは、実行の前後で .transport_network が増えたかで判定して記録する。

使い方（リポジトリ直下の .venv で。JAVA_HOME を設定しておく。OTP・DB 以外の重いものは止めておく）：
  EXDAY_R5_MEMORY=20G python reach/otp/r5_scale.py <OSM の pbf> <仮 GTFS の zip> <ラベル>

  ラベルは結果のファイル名（reach/results/r5_scale_<ラベル>.md）。例：japan-build、japan-load-12G、kanto-load
  EXDAY_R5_MEMORY は Java のヒープの上限（既定 20G）。常に動かすときに要る量は、load をこの値を下げながら流して見る

探すとき：寄り道の検索1回＝出発駅 → 全国の駅（行き）と、帰着駅 → 全国の駅（帰りの近似。#26 と同じ）の一対多を1回ずつ。
5つの条件（東京・大阪・福岡・札幌・仙台）を2周する（1周目は温まっていない）。駅は DB の reach.station（全国）。
"""
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

ARGS = sys.argv[1:]
MAX_MEMORY = os.environ.get("EXDAY_R5_MEMORY", "20G")
sys.argv = [sys.argv[0], "--max-memory", MAX_MEMORY]

import geopandas as gpd  # noqa: E402
import jpype  # noqa: E402
import psutil  # noqa: E402
import psycopg  # noqa: E402
import r5py  # noqa: E402
from r5py.util.config import Config  # noqa: E402
from shapely.geometry import Point  # noqa: E402

DSN = os.environ.get("EXDAY_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/ex_day_poc")
HERE = Path(__file__).resolve().parent
DATE = "2026-11-10"
WINDOW_MIN = 10
MAX_MIN = 300
MODES = [r5py.TransportMode.TRANSIT, r5py.TransportMode.WALK]
# （名前, 出発駅の名前, 帰着駅の名前, 出発時刻）
CONDITIONS = [
    ("東京 → 新横浜", "東京", "新横浜", "10:00"),
    ("新大阪 → 京都", "新大阪", "京都", "10:00"),
    ("博多 → 小倉", "博多", "小倉", "10:00"),
    ("札幌 → 小樽", "札幌", "小樽", "10:00"),
    ("仙台 → 福島", "仙台", "福島", "10:00"),
]
GB = 1024 ** 3


class Peak:
    """プロセス全体の RSS を 0.2秒ごとに測り、区間ごとの最大を取る。"""

    def __init__(self):
        self.proc = psutil.Process()
        self.peak = 0
        self.stop = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self.stop:
            self.peak = max(self.peak, self.proc.memory_info().rss)
            time.sleep(0.2)

    def reset(self):
        self.peak = self.proc.memory_info().rss
        return self

    def now(self):
        return self.proc.memory_info().rss


def heap_gb():
    rt = jpype.JClass("java.lang.Runtime").getRuntime()
    return (rt.totalMemory() - rt.freeMemory()) / GB, rt.totalMemory() / GB, rt.maxMemory() / GB


def settle():
    """ガベージコレクションをかけて、落ち着いたときの値を見る"""
    jpype.JClass("java.lang.System").gc()
    time.sleep(3)
    jpype.JClass("java.lang.System").gc()
    time.sleep(2)


def group_of(cur, name):
    cur.execute("""SELECT group_code FROM reach.station WHERE station_name = %s
                   GROUP BY group_code ORDER BY count(*) DESC, group_code LIMIT 1""", (name,))
    r = cur.fetchone()
    if not r:
        sys.exit(f"駅が見つからない：{name}")
    return r[0]


def main():
    if len(ARGS) != 3:
        sys.exit(__doc__)
    osm, gtfs, label = Path(ARGS[0]).expanduser().resolve(), Path(ARGS[1]).expanduser().resolve(), ARGS[2]
    # r5py は ~/.cache/r5py に入力へのリンクを作って読むので、無いファイルを渡すと、そのリンクが無いという分かりにくいエラーになる
    for p in (osm, gtfs):
        if not p.is_file():
            sys.exit(f"ファイルが無い：{p}")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("""SELECT group_code, ST_X(ST_Centroid(ST_Collect(geom))), ST_Y(ST_Centroid(ST_Collect(geom)))
                       FROM reach.station GROUP BY group_code""")
        groups = cur.fetchall()
        conds = [(n, group_of(cur, a), group_of(cur, b), t) for n, a, b, t in CONDITIONS]
    stations = gpd.GeoDataFrame({"id": [g[0] for g in groups]},
                                geometry=[Point(g[1], g[2]) for g in groups], crs="EPSG:4326")
    pt = {g[0]: (g[1], g[2]) for g in groups}

    peak = Peak()
    cache = Config().CACHE_DIR
    before = set(cache.glob("*.transport_network"))
    rss0 = peak.now()

    peak.reset()
    t0 = time.perf_counter()
    net = r5py.TransportNetwork(str(osm), [str(gtfs)])
    t_net = time.perf_counter() - t0
    peak_net = peak.peak
    built = bool(set(cache.glob("*.transport_network")) - before)
    settle()
    rss_idle = peak.now()
    heap_idle = heap_gb()

    rows = []
    peak.reset()
    for rnd in (1, 2):
        for name, a, b, hhmm in conds:
            dep = datetime.fromisoformat(f"{DATE}T{hhmm}:00")
            secs, counts = [], []
            for g in (a, b):
                origin = gpd.GeoDataFrame({"id": [g]}, geometry=[Point(*pt[g])], crs="EPSG:4326")
                t0 = time.perf_counter()
                m = r5py.TravelTimeMatrix(
                    net, origins=origin, destinations=stations, departure=dep,
                    departure_time_window=timedelta(minutes=WINDOW_MIN), percentiles=[50, 80],
                    transport_modes=MODES, max_time=timedelta(minutes=MAX_MIN), snap_to_network=True)
                secs.append(time.perf_counter() - t0)
                col = "travel_time_p80" if "travel_time_p80" in m.columns else "travel_time"
                counts.append(int(m[col].notna().sum()))
            rows.append((rnd, name, secs[0], secs[1], counts[0], counts[1]))
            print(f"{rnd}周目 {name}：行き {secs[0]:.2f}秒（{counts[0]}駅）、帰り {secs[1]:.2f}秒（{counts[1]}駅）", flush=True)
    peak_search = peak.peak
    heap_search = heap_gb()
    settle()
    rss_after = peak.now()

    disk = sorted(cache.glob("*.transport_network"), key=lambda p: p.stat().st_mtime)
    net_file = disk[-1] if disk else None

    out = [f"# R5 のメモリと時間：{label}（Issue #30）", "",
           f"- 日時：{datetime.now():%Y-%m-%d %H:%M}、r5py {r5py.__version__}、Java のヒープの上限 {MAX_MEMORY}",
           f"- マシン：{os.uname().sysname} {os.uname().machine}、メモリ {psutil.virtual_memory().total / GB:.0f}GB、CPU {psutil.cpu_count()}",
           f"- 入力：{osm.name}（{osm.stat().st_size / GB:.2f}GB）、{gtfs.name}（{gtfs.stat().st_size / 1024 ** 2:.1f}MB）",
           f"- 交通網：{'作った（キャッシュが無かった）' if built else '保存したものを読んだ'}"
           + (f"。保存したファイル {net_file.stat().st_size / GB:.2f}GB" if net_file else ""),
           f"- 駅グループ（行き先）：{len(groups):,}", "",
           "## メモリ（プロセス全体の RSS。Java を含む）", "",
           "| 区間 | 時間 | RSS の最大 | 区間の後の RSS | Java ヒープ（使用 / 確保 / 上限） |",
           "|---|---|---|---|---|",
           f"| 起動（r5py の読み込みまで） | — | — | {rss0 / GB:.1f}GB | — |",
           f"| 交通網を{'作る' if built else '読む'} | {t_net:.0f}秒 | {peak_net / GB:.1f}GB | {rss_idle / GB:.1f}GB（GC 後） | "
           f"{heap_idle[0]:.1f} / {heap_idle[1]:.1f} / {heap_idle[2]:.1f}GB |",
           f"| 探す（{len(conds)}条件×2周） | — | {peak_search / GB:.1f}GB | {rss_after / GB:.1f}GB（GC 後） | "
           f"{heap_search[0]:.1f} / {heap_search[1]:.1f} / {heap_search[2]:.1f}GB（GC 前） |",
           "",
           "## 寄り道の検索1回（行き＋帰り、それぞれ全国の駅への一対多）", "",
           "| 周 | 条件 | 行き | 帰り | 合計 | 行ける駅（行き・帰り、p80 で " + f"{MAX_MIN}分以内） |",
           "|---|---|---|---|---|---|"]
    for rnd, name, s1, s2, c1, c2 in rows:
        out.append(f"| {rnd} | {name} | {s1:.2f}秒 | {s2:.2f}秒 | {s1 + s2:.2f}秒 | {c1:,}・{c2:,} |")
    out.append("")
    path = HERE.parent / "results" / f"r5_scale_{label}.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    peak.stop = True


if __name__ == "__main__":
    main()
