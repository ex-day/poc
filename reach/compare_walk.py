"""歩きの道のりと直線を比べ、results/walk_<モデル>.md に書く（Issue #23）。

使い方：
  EXDAY_MODEL=cl-nagoya/ruri-v3-30m python compare_walk.py

前提：06〜08 の SQL と load_n13.py を流し、reach.station_spot_walk ができていること。
reach.spot の埋め込みと同じモデルで動かすこと（#6 の load_spots.py と同じ EXDAY_MODEL）。
"""
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "vector"))
from common import Embedder, connect  # noqa: E402

SCENARIO = ("新横浜 → 東京（3時間、滞在60分）", "新横浜", "003766", 180, 60)
INTERESTS = ["城を見たい", "温泉に入りたい", "湖を見たい", "庭園を散歩したい", "魚料理を食べたい", "港町の歴史を知りたい"]
TOP_N = 5
C_ALPHA = 0.05  # 遠回り1時間につき0.05を引く（#6 と同じ）

COLS = ["spot_id", "source", "name", "kinds", "group_code", "station_name", "dist_m",
        "t1_min", "t2_min", "slack_min", "detour_min", "sim"]


def score(r):
    return r["sim"] - C_ALPHA * float(r["detour_min"]) / 60


def line(r, walk=False):
    w = f"｜歩き {float(r['walk_min']):.0f}分" if walk else ""
    return (f"{r['name']}（{r['kinds'] or '-'}）｜{r['station_name']}駅から直線{int(r['dist_m'])}m{w}｜"
            f"近さ {r['sim']:.3f}｜遠回り {int(r['detour_min'])}分｜余裕 {int(r['slack_min'])}分")


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))]


def main() -> None:
    emb = Embedder()
    out = ["# 歩きの道のりと直線の比較（Issue #23）", "", f"- モデル：{emb.model_name}",
           "- 歩く速さ：分速80m。歩く時間＝駅の代表点→最寄りの道路の点（直線）＋道路の道のり＋道路の点→場所（直線）", ""]
    with connect() as conn, conn.cursor() as cur:
        # 1. グラフの大きさとつながり
        cur.execute("SELECT (SELECT count(*) FROM reach.n13_road), (SELECT count(*) FROM reach.walk_node), "
                    "(SELECT count(*) FROM reach.walk_edge), (SELECT count(*) FROM reach.station_walk), "
                    "(SELECT count(*) FROM reach.spot_walk)")
        roads, nodes, edges, st, sp = cur.fetchone()
        t0 = time.perf_counter()
        cur.execute("""
            SELECT component, count(*) AS n FROM pgr_connectedComponents(
              'SELECT id, source, target, cost, reverse_cost FROM reach.walk_edge')
            GROUP BY component ORDER BY n DESC""")
        comps = [r[1] for r in cur.fetchall()]
        sec_cc = time.perf_counter() - t0
        out += ["## 1. 歩きのグラフ", "",
                f"- 道路 {roads:,} 本 → 点 {nodes:,}、辺 {edges:,}",
                f"- 道路の点に結び付いた駅グループ {st:,}、場所 {sp:,}（300m 以内に道路の点があるもの）",
                f"- つながった塊：{len(comps):,} 個。いちばん大きい塊に点の {comps[0] / nodes:.1%}"
                f"（2番目 {comps[1] if len(comps) > 1 else 0:,} 点、10点以下の塊 {sum(1 for c in comps if c <= 10):,} 個）。{sec_cc:.0f}秒", ""]

        # 2. 道のりは直線の何倍か（駅の代表点から場所まで）
        cur.execute("""
            SELECT w.walk_min * 80.0 AS walk_m,
                   ST_Distance(s.geom::geography, g.p::geography) AS line_m,
                   s.name, st.station_name, w.walk_min
            FROM reach.station_spot_walk w
            JOIN reach.spot s USING (spot_id)
            JOIN (SELECT group_code, min(station_name) AS station_name, ST_Centroid(ST_Collect(geom)) AS p
                  FROM reach.station GROUP BY group_code) g USING (group_code)
            JOIN (SELECT group_code, min(station_name) AS station_name FROM reach.station GROUP BY group_code) st USING (group_code)""")
        rows = cur.fetchall()
        ratios = [r[0] / r[1] for r in rows if r[1] >= 300]
        near_but_far = [r for r in rows if r[1] <= 800 and r[4] > 15]
        out += ["## 2. 道のりは直線の何倍か", "",
                f"駅と場所の組 {len(rows):,}（片道25分以内）のうち、直線300m以上の {len(ratios):,} 組。", "",
                "| 中央値 | 上位25% | 上位10% | 上位1% |", "|---|---|---|---|",
                f"| {statistics.median(ratios):.2f}倍 | {pct(ratios, 0.75):.2f}倍 | {pct(ratios, 0.9):.2f}倍 | {pct(ratios, 0.99):.2f}倍 |", "",
                f"直線では800m以内なのに、歩くと15分を超える組：{len(near_but_far):,}", ""]
        far = sorted([r for r in rows if r[1] >= 300], key=lambda r: -r[0] / r[1])[:10]
        out += ["直線との差が大きい組（例）：", ""]
        out += [f"- {r[3]}駅 → {r[2]}：直線 {r[1]:.0f}m、歩き {r[4]:.0f}分（{r[0]:.0f}m、{r[0] / r[1]:.1f}倍）" for r in far]
        out.append("")

        # 3. 寄り道の提案：直線と道のり
        title, src, dst, avail, stay = SCENARIO
        cur.execute("SELECT group_code FROM reach.station_walk")
        in_area = {r[0] for r in cur.fetchall()}
        out += [f"## 3. 寄り道の提案：{title}", "",
                "直線は #6 の reach.suggest_reach_first（800m＋余裕、上限2km）、道のりは reach.suggest_walk（片道10分＋余裕、上限25分）。"
                "比べやすいように、直線の側も道路の範囲の中の駅だけにした。並べ方は #6 の C（近さ − 遠回り1時間につき0.05）。", ""]
        for interest in INTERESTS:
            qvec = emb.query([interest])[0]
            t0 = time.perf_counter()
            cur.execute("SELECT * FROM reach.suggest_reach_first(%s, %s, %s, %s, %s)", (src, dst, avail, stay, qvec))
            a = [dict(zip(COLS, r)) for r in cur.fetchall()]
            a = [r for r in a if r["group_code"] in in_area]
            sec_a = time.perf_counter() - t0
            t0 = time.perf_counter()
            cur.execute("SELECT * FROM reach.suggest_walk(%s, %s, %s, %s, %s)", (src, dst, avail, stay, qvec))
            b = [dict(zip(COLS + ["walk_min"], r)) for r in cur.fetchall()]
            sec_b = time.perf_counter() - t0
            ia, ib = {r["spot_id"] for r in a}, {r["spot_id"] for r in b}
            out += [f"### 「{interest}」", "",
                    f"候補：直線 {len(a):,} 件（{sec_a:.2f}秒）、道のり {len(b):,} 件（{sec_b:.2f}秒）。"
                    f"両方 {len(ia & ib):,}、直線だけ {len(ia - ib):,}、道のりだけ {len(ib - ia):,}", ""]
            for label, ranked, walk in (("直線", sorted(a, key=lambda r: -score(r)), False),
                                         ("道のり", sorted(b, key=lambda r: -score(r)), True)):
                out.append(f"{label}")
                out += [f"{i}. {line(r, walk)}" for i, r in enumerate(ranked[:TOP_N], 1)]
                out.append("")
            print(f"{interest}：直線 {len(a)} 件、道のり {len(b)} 件")
    path = HERE / "results" / f"walk_{emb.model_name.replace('/', '_')}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"書いた：{path}")


if __name__ == "__main__":
    main()
