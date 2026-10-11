"""到達点（入口・範囲の端）で、駅からの歩きと寄り道の提案がどう変わるかを、pgRouting（N13）で比べる（Issue #31 の 2）。

使い方（リポジトリ直下の .venv で）：
  EXDAY_MODEL=cl-nagoya/ruri-v3-30m python reach/entrance/compare_access.py

前提：
  - #23 の歩き（06〜08）と reach.station_spot_walk ができていること
  - 09_access_point.sql を流し、spot_access_point.csv を入れ、10_build_access_walk.sql を流してあること（README の手順）

比べること：
  1. 歩き：到達点を持つ場所で、いちばん近い駅グループからの歩き（点 → 到達点）
  2. 25分までに歩いて行ける（駅グループ, 場所）の組の出入り
  3. 寄り道の提案（#6・#23 と同じ条件・興味）の上位10件：reach.station_spot_walk を到達点の版に差し替えて（トランザクションの中だけ）並べ直す

結果は reach/results/access_walk_compare.md。
"""
import sys
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "vector"))
from common import Embedder, connect  # noqa: E402

SCENARIO = ("新横浜 → 東京（3時間、滞在60分）", "004501", "003766", 180, 60)
INTERESTS = ["城を見たい", "温泉に入りたい", "湖を見たい", "庭園を散歩したい", "魚料理を食べたい", "港町の歴史を知りたい"]
TOP_N = 10
C_ALPHA = 0.05
COLS = ["spot_id", "source", "name", "kinds", "group_code", "station_name", "dist_m",
        "t1_min", "t2_min", "slack_min", "detour_min", "sim", "walk_min"]
RESULT = HERE.parent / "results" / "access_walk_compare.md"


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else float("nan")


def suggest(cur, qvec):
    cur.execute("SELECT * FROM reach.suggest_walk(%s, %s, %s, %s, %s)",
                (SCENARIO[1], SCENARIO[2], SCENARIO[3], SCENARIO[4], qvec))
    rows = [dict(zip(COLS, r)) for r in cur.fetchall()]
    for r in rows:
        r["score"] = r["sim"] - C_ALPHA * float(r["detour_min"]) / 60
    return sorted(rows, key=lambda r: -r["score"])


def main():
    out = ["# 到達点（入口・範囲の端）で歩きと寄り道の提案を比べる（Issue #31 の 2、pgRouting・N13）", ""]
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT source, count(*), count(DISTINCT spot_id) FROM reach.spot_access_point GROUP BY source ORDER BY source""")
        out += ["## 到達点", "", "| 種類 | 点 | 場所 |", "|---|---|---|"]
        for s, n, k in cur.fetchall():
            out.append(f"| {s} | {n:,} | {k:,} |")
        cur.execute("""
            SELECT count(DISTINCT spot_id) FILTER (WHERE ap_id IS NOT NULL),
                   count(*) FILTER (WHERE ap_id IS NOT NULL),
                   (SELECT count(*) FROM reach.spot_access_point)
            FROM reach.spot_walk_ap""")
        k, n, total = cur.fetchone()
        out += ["", f"- 道路（N13）の点に 300m 以内で結び付いた到達点：{n:,} / {total:,}（場所 {k:,}）", ""]

        # 1. いちばん近い駅グループからの歩き
        cur.execute("""
            WITH ap_spots AS (SELECT DISTINCT spot_id FROM reach.spot_walk_ap WHERE ap_id IS NOT NULL),
            a AS (SELECT spot_id, min(walk_min) AS w FROM reach.station_spot_walk JOIN ap_spots USING (spot_id) GROUP BY spot_id),
            b AS (SELECT spot_id, min(walk_min) AS w FROM reach.station_spot_walk_ap JOIN ap_spots USING (spot_id) GROUP BY spot_id)
            SELECT ap_spots.spot_id, s.name, s.kinds, a.w, b.w,
                   (SELECT string_agg(DISTINCT source, ',') FROM reach.spot_access_point x WHERE x.spot_id = ap_spots.spot_id)
            FROM ap_spots JOIN reach.spot s USING (spot_id)
            LEFT JOIN a USING (spot_id) LEFT JOIN b USING (spot_id)""")
        rows = cur.fetchall()
        both = [(r, r[4] - r[3]) for r in rows if r[3] is not None and r[4] is not None]
        only_point = [r for r in rows if r[3] is not None and r[4] is None]
        only_ap = [r for r in rows if r[3] is None and r[4] is not None]
        d = [x for _, x in both]
        out += ["## 1. いちばん近い駅グループからの歩き（点 → 到達点）", "",
                f"到達点を持つ場所 {len(rows):,} 件のうち、どちらでも25分以内に駅がある {len(both):,} 件。",
                "",
                "| | 中央値 | 20パーセンタイル | 80パーセンタイル | 最小 | 最大 |",
                "|---|---|---|---|---|---|",
                f"| 到達点 − 点（分） | {q(d, .5):+.1f} | {q(d, .2):+.1f} | {q(d, .8):+.1f} | {min(d, default=0):+.1f} | {max(d, default=0):+.1f} |",
                "",
                f"- 3分以上短くなった：{sum(x <= -3 for x in d):,} 件、3分以上長くなった：{sum(x >= 3 for x in d):,} 件",
                f"- 到達点にしたら25分以内の駅が無くなった：{len(only_point):,} 件、新たに25分以内になった：{len(only_ap):,} 件",
                ""]
        out += ["短くなった例（上位15件）：", "", "| 場所 | 種類 | 点 | 到達点 | 差 | 到達点の種類 |", "|---|---|---|---|---|---|"]
        for r, x in sorted(both, key=lambda t: t[1])[:15]:
            out.append(f"| {r[1]} | {r[2] or '-'} | {r[3]:.0f}分 | {r[4]:.0f}分 | {x:+.0f} | {r[5]} |")
        out += ["", "長くなった例（上位10件）：", "", "| 場所 | 種類 | 点 | 到達点 | 差 | 到達点の種類 |", "|---|---|---|---|---|---|"]
        for r, x in sorted(both, key=lambda t: -t[1])[:10]:
            out.append(f"| {r[1]} | {r[2] or '-'} | {r[3]:.0f}分 | {r[4]:.0f}分 | {x:+.0f} | {r[5]} |")
        out.append("")

        # 2. 組の出入り
        cur.execute("""
            SELECT count(*) FILTER (WHERE a.spot_id IS NOT NULL AND b.spot_id IS NOT NULL),
                   count(*) FILTER (WHERE b.spot_id IS NULL),
                   count(*) FILTER (WHERE a.spot_id IS NULL)
            FROM reach.station_spot_walk a
            FULL JOIN reach.station_spot_walk_ap b USING (group_code, spot_id)""")
        same, lost, gained = cur.fetchone()
        out += ["## 2. 25分までに歩いて行ける（駅グループ, 場所）の組", "",
                f"- 両方：{same:,}、点だけ：{lost:,}、到達点だけ：{gained:,}", ""]

        # 東京ステーションギャラリー：ホームの重心から直線 60m ほどなのに、#27 では歩き16分と出た（#24 の後続）
        cur.execute("""
            SELECT st.group_code, st.access_m, sp.access_m, w.walk_min,
                   round(ST_Distance(g.p::geography, s.geom::geography)::numeric)
            FROM reach.spot s
            JOIN reach.spot_walk sp USING (spot_id)
            CROSS JOIN (SELECT group_code, ST_Centroid(ST_Collect(geom)) AS p FROM reach.station
                        WHERE group_code IN ('003766', '003785') GROUP BY group_code) g
            JOIN reach.station_walk st USING (group_code)
            LEFT JOIN reach.station_spot_walk w ON w.group_code = st.group_code AND w.spot_id = s.spot_id
            WHERE s.name = '東京ステーションギャラリー'""")
        out += ["## 東京ステーションギャラリーの歩き（N13）", "",
                "| 駅グループ | 重心から場所（直線） | 重心 → 道路の点 | 場所 → 道路の点 | 歩き |", "|---|---|---|---|---|"]
        for g, sa, pa, w, d in cur.fetchall():
            out.append(f"| {g} | {d}m | {sa:.0f}m | {pa:.0f}m | {'—' if w is None else f'{w:.0f}分'} |")
        out.append("")

        # 3. 寄り道の提案
        emb = Embedder()
        out += [f"## 3. 寄り道の提案の上位{TOP_N}件（{SCENARIO[0]}、モデル {emb.model_name}）", "",
                "| 興味 | 重なり | 点だけ | 到達点だけ |", "|---|---|---|---|"]
        detail = []
        for interest in INTERESTS:
            qvec = emb.query([interest])[0]
            base = suggest(cur, qvec)[:TOP_N]
            # 到達点の版に差し替えて並べ直し、元に戻す（セーブポイントの中だけ）
            try:
                with conn.transaction():
                    cur.execute("TRUNCATE reach.station_spot_walk")
                    cur.execute("INSERT INTO reach.station_spot_walk SELECT * FROM reach.station_spot_walk_ap")
                    ap = suggest(cur, qvec)[:TOP_N]
                    raise psycopg.Rollback()
            except psycopg.Rollback:
                pass
            a_ids = {r["spot_id"] for r in base}
            b_ids = {r["spot_id"] for r in ap}
            out.append(f"| {interest} | {len(a_ids & b_ids)} | "
                       f"{'、'.join(r['name'] for r in base if r['spot_id'] not in b_ids) or '—'} | "
                       f"{'、'.join(r['name'] for r in ap if r['spot_id'] not in a_ids) or '—'} |")
            changed = []
            bmap = {r["spot_id"]: r for r in ap}
            for r in base:
                o = bmap.get(r["spot_id"])
                if o and (o["station_name"] != r["station_name"] or abs(float(o["walk_min"]) - float(r["walk_min"])) >= 3):
                    changed.append(f"{r['name']}：{r['station_name']}駅から{float(r['walk_min']):.0f}分 → "
                                   f"{o['station_name']}駅から{float(o['walk_min']):.0f}分")
            if changed:
                detail.append(f"- {interest}：" + "／".join(changed))
        out += ["", "両方の上位にあって、降りる駅か歩き（3分以上）が変わったもの：", ""] + (detail or ["- なし"]) + [""]

    RESULT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
