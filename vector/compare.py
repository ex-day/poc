"""ex-day PoC（Issue #1）：複数のモデルを同じ問いで比べる

使い方：
  python compare.py                                   # 既定の組み合わせ（下の DEFAULT_MODELS）
  python compare.py cl-nagoya/ruri-v3-70m intfloat/multilingual-e5-small   # モデルを指定

各モデルについて、埋め込みを作って（load.py と同じ処理。作り済みなら作り直さない）、正解付きの問いを実行し、表にまとめる。
初回は各モデルのダウンロード（数十MB〜1GB程度）に時間がかかる。

見る数字：
  nDCG@5   上位5件の並びが正解にどれだけ近いか（1.00が理想）
  分離     正解（関連度2以上）の中でいちばん低い点数 − 不正解（関連度0・正解に無いもの）の中でいちばん高い点数。
           プラスなら「どこかにしきい値を引けば正解だけを取り出せる」。マイナスなら点数だけでは分けられない。
           e5 のように点数が0.8前後に固まるモデルでは小さくなりやすい。
"""
import json
import math
import sys
import time
from pathlib import Path

import load
from common import Embedder, MODELS, connect
from search import METHODS, TOP, ndcg, search_discovery, search_text

HERE = Path(__file__).parent
DEFAULT_MODELS = [
    "ngram-baseline",
    "intfloat/multilingual-e5-small",
    "cl-nagoya/ruri-v3-30m",
    "cl-nagoya/ruri-v3-70m",
    "cl-nagoya/ruri-v3-130m",
]
VECTOR_METHODS = [m for m in METHODS if m != "exact"]


def separation(ranked, judgments):
    """正解（2以上）の最低点 − 不正解（0・未判定）の最高点"""
    pos = [s for d, s in ranked if judgments.get(d, 0) >= 2]
    neg = [s for d, s in ranked if judgments.get(d, 0) == 0]
    if not pos or not neg:
        return None
    return min(pos) - max(neg)


def expected_topics(q):
    """expect_topic は1つ（文字列）か、どれでも正解（リスト）"""
    e = q.get("expect_topic")
    return [e] if isinstance(e, str) else list(e or [])


def evaluate(cur, emb, data):
    rows = {}
    topic_hits = {}  # (問い, variant) -> (1位の話題ID, 期待した話題の順位 or None)
    times = []
    for q in data["queries"]:
        t0 = time.time()
        if q["type"] == "text":
            results, _ = search_text(cur, emb, q["text"], limit=100)
            if q.get("expect_topic"):
                for variant, trows in results.get("_topics", {}).items():
                    ids = [t for t, _, _ in trows]
                    ranks = [ids.index(e) + 1 for e in expected_topics(q) if e in ids]
                    rank = min(ranks) if ranks else None
                    topic_hits[(q["id"], variant)] = (ids[0] if ids else None, rank)
        else:
            results, _ = search_discovery(cur, emb.model_name, q["source"], limit=100)
        times.append(time.time() - t0)
        for m in METHODS:
            ranked = [(d, s) for d, s in results.get(m, []) if s > 1e-6]
            rows[(q["id"], m)] = (ndcg([d for d, _ in ranked], q["judgments"]), separation(ranked, q["judgments"]))
    return rows, topic_hits, sum(times) / len(times)


def main():
    models = sys.argv[1:] or DEFAULT_MODELS
    data = json.loads((HERE / "sample_data.json").read_text(encoding="utf-8"))
    conn = connect()
    cur = conn.cursor()
    summary = []
    per_query = {}
    per_topic = {}
    for name in models:
        print(f"… {name}（{MODELS.get(name, {}).get('note', '')}）の準備中", flush=True)
        emb, load_sec = load.main(name, quiet=True)
        rows, topic_hits, avg_sec = evaluate(cur, emb, data)
        per_query[name] = rows
        per_topic[name] = topic_hits
        for m in VECTOR_METHODS:
            nd = [rows[(q["id"], m)][0] for q in data["queries"]]
            sp = [rows[(q["id"], m)][1] for q in data["queries"] if rows[(q["id"], m)][1] is not None]
            summary.append((name, emb.dim, m, sum(nd) / len(nd), (sum(sp) / len(sp)) if sp else float("nan"),
                            sum(1 for x in sp if x > 0), len(sp), load_sec, avg_sec))

    exact_nd = [per_query[models[0]][(q["id"], "exact")][0] for q in data["queries"]]
    print(f"\n問いの数：{len(data['queries'])}、Discoveryの数：{len(data['discoveries'])}")
    print(f"\n■ モデル × 方式（平均。完全一致 exact の nDCG@{TOP} は {sum(exact_nd) / len(exact_nd):.2f}）")
    print(f"{'モデル':<32}{'次元':>5}  {'方式':<15}{'nDCG@5':>8}{'分離(平均)':>11}{'分離>0':>8}{'読込(秒)':>9}{'1問(ms)':>9}")
    for name, dim, m, nd, sp, ok, n, load_sec, avg_sec in summary:
        print(f"{name:<32}{dim:>5}  {m:<15}{nd:>8.2f}{sp:>11.3f}{ok:>5}/{n:<2}{load_sec:>9.1f}{avg_sec * 1000:>9.0f}")

    cats = []
    for q in data["queries"]:
        c = q.get("category", "その他")
        if c not in cats:
            cats.append(c)
    for method in ("disc_full", "disc_topics", "disc+topic"):
        print(f"\n■ 問いの種類ごとの nDCG@{TOP}（方式：{method}。（ ）内は問いの数）")
        print(f"{'種類':<16}" + "".join(f"{n.split('/')[-1][:18]:>20}" for n in models))
        for c in cats:
            qs = [q for q in data["queries"] if q.get("category", "その他") == c]
            vals = [sum(per_query[n][(q["id"], method)][0] for q in qs) / len(qs) for n in models]
            print(f"{c + '（' + str(len(qs)) + '）':<16}" + "".join(f"{v:>20.2f}" for v in vals))

    tq = [q for q in data["queries"] if q.get("expect_topic")]
    if tq:
        print(f"\n■ 話題の当たり（期待した話題が何位か。上位3件に無ければ -。期待が複数ならどれか）")
        for variant in ("about", "about_findings"):
            print(f"  [{variant}]")
            print(f"  {'問い':<24}{'期待':<24}" + "".join(f"{n.split('/')[-1][:18]:>20}" for n in models))
            for q in tq:
                cells = []
                for n in models:
                    top, rank = per_topic[n].get((q["id"], variant), (None, None))
                    cells.append(f"{(str(rank) if rank else '-') + '位' + ('' if rank == 1 else f'（1位 {top}）'):>20}")
                print(f"  {q['id']:<24}{'/'.join(expected_topics(q)):<24}" + "".join(cells))
            hits = [sum(1 for q in tq if per_topic[n].get((q["id"], variant), (None, None))[1] == 1) for n in models]
            print(f"  {'1位の数':<48}" + "".join(f"{f'{h}/{len(tq)}':>20}" for h in hits))

    print(f"\n■ 問いごとの nDCG@{TOP}（disc_full → disc+topic）")
    print(f"{'問い':<20}" + "".join(f"{n.split('/')[-1][:18]:>20}" for n in models))
    for q in data["queries"]:
        cells = []
        for n in models:
            a, b = per_query[n][(q["id"], "disc_full")][0], per_query[n][(q["id"], "disc+topic")][0]
            cells.append(f"{f'{a:.2f}→{b:.2f}':>20}")
        print(f"{q['id']:<20}" + "".join(cells))

    print("\n問いごとの上位の並びは、EXDAY_MODEL=<モデル名> python search.py で確認できます。")


if __name__ == "__main__":
    main()
