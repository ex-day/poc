"""ex-day PoC（Issue #1）：完全一致とベクトル検索を並べて比べる

使い方：
  python search.py                    # sample_data.json の問いをすべて実行し、方式ごとの成績を出す
  python search.py "自由な文章"        # その文章で検索して、方式ごとの上位5件を出す
  EXDAY_MODEL=ngram-baseline python search.py   # 比較用の基準で同じことをする（先に load.py も同じモデルで）

方式：
  exact            Subjectの完全一致（表記をそろえて、問いの文章にSubjectの名前が含まれるか／共通のSubjectの数）
  subject_vec      問いとSubjectのベクトルの近さ（Discoveryごとに、いちばん近いSubjectの点数）
  disc_name_subj   問いとDiscoveryのベクトルの近さ（名前＋Subjectをベクトル化したもの）
  disc_full        同上（名前＋Subject＋わかってきたことをベクトル化したもの）
  disc_topics      同上（disc_full に、話題ごとの「何について」の1行と話題でわかってきたことを足したもの）
  disc+topic       Discovery全体（disc_full）と、そのDiscoveryの話題（1行＋わかってきたこと）の、近いほうの点数
"""
import json
import math
import sys
import time
from pathlib import Path

from common import Embedder, connect, normalize

HERE = Path(__file__).parent
TOP = 5
METHODS = ["exact", "subject_vec", "disc_name_subj", "disc_full", "disc_topics", "disc+topic"]


def search_text(cur, emb, text, limit=TOP):
    results = {}
    # exact
    cur.execute(
        """SELECT ds.discovery_id, count(*)::float FROM poc.discovery_subject ds
           JOIN poc.subject s ON s.id = ds.subject_id
           WHERE position(s.normalized_name in %s) > 0
           GROUP BY ds.discovery_id ORDER BY 2 DESC, 1 LIMIT %s""",
        (normalize(text), limit),
    )
    results["exact"] = cur.fetchall()

    q = emb.query([text])[0]
    t0 = time.time()
    cur.execute(
        """SELECT ds.discovery_id, max(1 - (se.embedding <=> %s)) AS score
           FROM poc.subject_embedding se JOIN poc.discovery_subject ds ON ds.subject_id = se.subject_id
           WHERE se.model = %s GROUP BY ds.discovery_id ORDER BY score DESC LIMIT %s""",
        (q, emb.model_name, limit),
    )
    results["subject_vec"] = cur.fetchall()
    for method, variant in (("disc_name_subj", "name_subjects"), ("disc_full", "full"), ("disc_topics", "topics")):
        cur.execute(
            """SELECT discovery_id, 1 - (embedding <=> %s) AS score FROM poc.discovery_embedding
               WHERE model = %s AND variant = %s ORDER BY embedding <=> %s LIMIT %s""",
            (q, emb.model_name, variant, q, limit),
        )
        results[method] = cur.fetchall()
    # Discovery全体と話題の、近いほうの点数
    cur.execute(
        """SELECT discovery_id, max(s) AS score FROM (
             SELECT discovery_id, 1 - (embedding <=> %(q)s) AS s FROM poc.discovery_embedding
               WHERE model = %(m)s AND variant = 'full'
             UNION ALL
             SELECT tp.discovery_id, 1 - (te.embedding <=> %(q)s) AS s FROM poc.topic_embedding te
               JOIN poc.topic tp ON tp.id = te.topic_id WHERE te.model = %(m)s AND te.variant = 'about_findings'
           ) x GROUP BY discovery_id ORDER BY score DESC LIMIT %(n)s""",
        {"q": q, "m": emb.model_name, "n": limit},
    )
    results["disc+topic"] = cur.fetchall()
    # 話題の順位（表示と、期待した話題が1位かの確認用）
    results["_topics"] = {}
    for variant in ("about", "about_findings"):
        cur.execute(
            """SELECT te.topic_id, tp.about, 1 - (te.embedding <=> %s) AS score FROM poc.topic_embedding te
               JOIN poc.topic tp ON tp.id = te.topic_id WHERE te.model = %s AND te.variant = %s
               ORDER BY te.embedding <=> %s LIMIT 3""",
            (q, emb.model_name, variant, q),
        )
        results["_topics"][variant] = cur.fetchall()
    return results, time.time() - t0


def search_discovery(cur, model, source, limit=TOP):
    results = {}
    cur.execute(
        """SELECT b.discovery_id, count(*)::float FROM poc.discovery_subject a
           JOIN poc.discovery_subject b ON a.subject_id = b.subject_id AND b.discovery_id <> a.discovery_id
           WHERE a.discovery_id = %s GROUP BY b.discovery_id ORDER BY 2 DESC, 1 LIMIT %s""",
        (source, limit),
    )
    results["exact"] = cur.fetchall()
    t0 = time.time()
    # 元のDiscoveryの各Subjectについて、相手のSubjectの中でいちばん近いものの点数をとり、その平均
    cur.execute(
        """WITH src AS (
             SELECT se.embedding FROM poc.discovery_subject ds
             JOIN poc.subject_embedding se ON se.subject_id = ds.subject_id AND se.model = %(m)s
             WHERE ds.discovery_id = %(s)s),
           tgt AS (
             SELECT ds.discovery_id, se.embedding FROM poc.discovery_subject ds
             JOIN poc.subject_embedding se ON se.subject_id = ds.subject_id AND se.model = %(m)s
             WHERE ds.discovery_id <> %(s)s),
           best AS (
             SELECT tgt.discovery_id, src.embedding AS s, max(1 - (src.embedding <=> tgt.embedding)) AS sim
             FROM src CROSS JOIN tgt GROUP BY tgt.discovery_id, src.embedding)
           SELECT discovery_id, avg(sim) AS score FROM best GROUP BY discovery_id ORDER BY score DESC LIMIT %(n)s""",
        {"m": model, "s": source, "n": limit},
    )
    results["subject_vec"] = cur.fetchall()
    for method, variant in (("disc_name_subj", "name_subjects"), ("disc_full", "full"), ("disc_topics", "topics")):
        cur.execute(
            """SELECT d.discovery_id, 1 - (d.embedding <=> s.embedding) AS score
               FROM poc.discovery_embedding d JOIN poc.discovery_embedding s
                 ON s.discovery_id = %s AND s.model = d.model AND s.variant = d.variant
               WHERE d.model = %s AND d.variant = %s AND d.discovery_id <> s.discovery_id
               ORDER BY d.embedding <=> s.embedding LIMIT %s""",
            (source, model, variant, limit),
        )
        results[method] = cur.fetchall()
    results["disc+topic"] = results["disc_topics"]  # Discovery起点では話題単位の比較はしない（disc_topics と同じ）
    return results, time.time() - t0


def ndcg(ranked, judgments):
    dcg = sum(judgments.get(d, 0) / math.log2(i + 2) for i, d in enumerate(ranked[:TOP]))
    ideal = sorted(judgments.values(), reverse=True)[:TOP]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def print_results(results, names, judgments=None):
    for m in METHODS:
        rows = [(d, sc) for d, sc in results.get(m, []) if sc > 1e-6]  # 点数0（まったく似ていない）は除く
        items = []
        for did, score in rows:
            mark = f"[{judgments.get(did, 0)}]" if judgments is not None else ""
            items.append(f"{names.get(did, did)}{mark} {score:.2f}")
        line = " / ".join(items) if items else "（該当なし）"
        extra = f"  nDCG@{TOP}={ndcg([d for d, _ in rows], judgments):.2f}" if judgments else ""
        print(f"  {m:<15}{extra}\n      {line}")
    for variant, rows in results.get("_topics", {}).items():
        line = " / ".join(f"{about}（{score:.2f}）" for _, about, score in rows)
        print(f"  話題[{variant}]\n      {line}")


def main():
    conn = connect()
    cur = conn.cursor()
    emb = Embedder()
    cur.execute("SELECT id, name FROM poc.discovery")
    names = dict(cur.fetchall())
    print(f"モデル：{emb.model_name}\n")

    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
        results, sec = search_text(cur, emb, text)
        print(f"問い：{text}（ベクトル検索 {sec * 1000:.0f} ms）")
        print_results(results, names)
        return

    data = json.loads((HERE / "sample_data.json").read_text(encoding="utf-8"))
    totals = {m: [] for m in METHODS}
    for q in data["queries"]:
        if q["type"] == "text":
            results, sec = search_text(cur, emb, q["text"])
            label = q["text"]
        else:
            results, sec = search_discovery(cur, emb.model_name, q["source"])
            label = f"（{names[q['source']]}に関連するDiscovery）"
        print(f"■ {q['id']}：{label}\n  意図：{q['note']}（[ ]内は正解の関連度 0〜3）")
        print_results(results, names, q["judgments"])
        print()
        for m in METHODS:
            totals[m].append(ndcg([d for d, sc in results.get(m, []) if sc > 1e-6], q["judgments"]))

    print(f"■ 方式ごとの平均 nDCG@{TOP}（1.00が理想）")
    for m in METHODS:
        print(f"  {m:<15} {sum(totals[m]) / len(totals[m]):.2f}")


if __name__ == "__main__":
    main()
