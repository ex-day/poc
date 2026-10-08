"""寄り道の提案（Issue #6）：絞り込みの順序（案1・案2）と並べ方を比べ、results/suggest_<モデル>.md に書く。

使い方：
  EXDAY_MODEL=cl-nagoya/ruri-v3-30m python compare_suggest.py

reach.spot の埋め込みと同じモデルで動かすこと（load_spots.py と同じ EXDAY_MODEL）。
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "vector"))
from common import Embedder, connect  # noqa: E402

# 寄り道の条件：名前、出発、帰着、使える時間（分）、滞在時間（分）
SCENARIOS = [
    ("Scenario 01：新大阪 → 新横浜（6時間、滞在90分）", "新大阪", "新横浜", 360, 90),
    ("新横浜 → 東京（3時間、滞在60分）", "新横浜", "003766", 180, 60),
]

# 興味。Wikidata に合う種類があるもの（城、温泉、湖、庭園）と、ないもの（魚料理）と、言い回しのもの（港町の歴史）
INTERESTS = [
    "城を見たい",
    "温泉に入りたい",
    "湖を見たい",
    "庭園を散歩したい",
    "魚料理を食べたい",
    "港町の歴史を知りたい",
]

TOP_N = 5          # 並べ方ごとに見せる件数
B_POOL = 30        # 並べ方B：近さの上位何件から、遠回りの小さい順に並べ直すか
C_ALPHA = 0.05     # 並べ方C：近さ − C_ALPHA × 遠回り（時間）。遠回り1時間につき0.05を引く
K_LIST = [50, 200, 1000]  # 案2：興味で先に取る件数

COLS = ["spot_id", "source", "name", "kinds", "group_code", "station_name", "dist_m",
        "t1_min", "t2_min", "slack_min", "detour_min", "sim"]


def fetch(cur, sql, params):
    t0 = time.perf_counter()
    cur.execute(sql, params)
    rows = [dict(zip(COLS, r)) for r in cur.fetchall()]
    return rows, time.perf_counter() - t0


def line(r):
    return (f"{r['name']}（{r['kinds'] or '-'}）｜{r['station_name']}駅から{int(r['dist_m'])}m｜"
            f"近さ {r['sim']:.3f}｜遠回り {int(r['detour_min'])}分｜余裕 {int(r['slack_min'])}分")


def main() -> None:
    emb = Embedder()
    out = [f"# 寄り道の提案：絞り込みの順序と並べ方（Issue #6）", "",
           f"- モデル：{emb.model_name}",
           f"- 並べ方A：興味との近さの順",
           f"- 並べ方B：近さの上位{B_POOL}件を、遠回りの小さい順に",
           f"- 並べ方C：近さ − {C_ALPHA} × 遠回り（時間）の順（遠回り1時間につき{C_ALPHA}を引く）",
           f"- 案2：興味で全国から上位k件を先に取り、そのうち行ける駅のまわりにあるものだけ残す", ""]
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT model, count(*) FROM reach.spot GROUP BY 1")
        models = dict(cur.fetchall())
        if set(models) != {emb.model_name}:
            sys.exit(f"reach.spot の埋め込みのモデル {models} と EXDAY_MODEL（{emb.model_name}）が違う")
        for title, src, dst, avail, stay in SCENARIOS:
            out += [f"## {title}", ""]
            for interest in INTERESTS:
                qvec = emb.query([interest])[0]
                rows, sec1 = fetch(cur, "SELECT * FROM reach.suggest_reach_first(%s, %s, %s, %s, %s)",
                                   (src, dst, avail, stay, qvec))
                out += [f"### 「{interest}」", "",
                        f"案1：候補 {len(rows)} 件（{sec1:.2f}秒）", ""]
                a = sorted(rows, key=lambda r: -r["sim"])
                b = sorted(a[:B_POOL], key=lambda r: (r["detour_min"], -r["sim"]))
                c = sorted(rows, key=lambda r: -(r["sim"] - C_ALPHA * float(r["detour_min"]) / 60))
                for label, ranked in (("A（近さ）", a), ("B（近さ上位→遠回り）", b), ("C（合計点）", c)):
                    out.append(f"並べ方{label}")
                    out += [f"{i}. {line(r)}" for i, r in enumerate(ranked[:TOP_N], 1)]
                    out.append("")
                top_a = {r["spot_id"] for r in a[:10]}
                out.append("| 案2の k | 残った件数 | 案1・並べ方Aの上位10件のうち含まれる数 | 時間 |")
                out.append("|---|---|---|---|")
                for k in K_LIST:
                    rows2, sec2 = fetch(cur, "SELECT * FROM reach.suggest_interest_first(%s, %s, %s, %s, %s, %s)",
                                        (src, dst, avail, stay, qvec, k))
                    hit = len(top_a & {r["spot_id"] for r in rows2})
                    out.append(f"| {k} | {len(rows2)} | {hit} | {sec2:.2f}秒 |")
                out.append("")
                print(f"{title}／{interest}：案1 {len(rows)} 件")
    path = HERE / "results" / f"suggest_{emb.model_name.replace('/', '_')}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"書いた：{path}")


if __name__ == "__main__":
    main()
