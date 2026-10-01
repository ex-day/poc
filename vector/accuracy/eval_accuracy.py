"""ex-day PoC（Issue #9 の項目3）：件数が増えたときの検索精度を見る

#1 の正解付きのデータ（Discovery 23件・問い41問）に、紛れ込み用の文章（Wikipedia の地点記事の冒頭）を
0・1千・1万件と混ぜ、次を比べる。速さではなく「何が出てくるか」を見るため、検索は numpy の全件比較で行う（DB は使わない）。

1. nDCG@5 と、上位5件への紛れ込みの数
2. 候補の決め方ごとの、適合率・再現率・出す件数・紛れ込みの数
   - 上位〇件（k = 1, 3, 5, 10）
   - 固定のしきい値（t。0件のときにいちばん良かった t を、そのまま件数を増やしたときにも使う）
   - 1位との差（1位の点数 − δ 以上。最大10件）
   - 上位5件かつしきい値以上
3. 点数の重なり：正解（関連度2以上）の最高点と、紛れ込みの最高点
4. 人が確かめる一覧：上位5件に入った紛れ込み（実は関係がある記事かもしれない）

関連度の扱い：2以上を「正解」、0 と紛れ込みを「不正解」、1 はどちらにも数えない。
紛れ込みは正解を付けていないため、実は関係がある記事も「不正解」に数えている（4 の一覧で人が確かめる）。

準備：python fetch_wikipedia.py（data/wikipedia_kanto.jsonl を作る）
使い方：
  EXDAY_MODEL=cl-nagoya/ruri-v3-30m python eval_accuracy.py --device cpu
  EXDAY_MODEL=cl-nagoya/ruri-v3-70m python eval_accuracy.py --device cpu --sizes 0 1000 10000
"""
import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import random
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import Embedder  # noqa: E402
from load import discovery_texts  # noqa: E402

DATA = HERE / "data"
RESULTS = HERE / "results"
TOP = 5


# ---------------------------------------------------------------- データ

def load_judged():
    data = json.loads((HERE.parent / "sample_data.json").read_text(encoding="utf-8"))
    topics = data.get("topics", [])
    docs = []
    for d in data["discoveries"]:
        own = [t for t in topics if t["discovery_id"] == d["id"]]
        docs.append({"id": d["id"], "title": d["name"], "text": discovery_texts(d, own)["topics"], "judged": True})
    return data, docs


def exclusion_words(data):
    """#1 の Discovery と同じものを指す記事は、正解が付いていないのに関係があるため、紛れ込みから外す"""
    words = set()
    for d in data["discoveries"]:
        words.add(d["name"])
        for s, kind in d["subjects"]:
            if kind in ("target", "place", "event"):
                words.add(s)
    return {w for w in words if len(w) >= 2}


def load_distractors(data, seed):
    path = DATA / "wikipedia_kanto.jsonl"
    if not path.exists():
        sys.exit(f"{path} がありません。先に python fetch_wikipedia.py を実行してください")
    words = exclusion_words(data)
    keep, excluded = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        a = json.loads(line)
        if any(w in a["title"] or a["title"] in w for w in words):
            excluded.append(a["title"])
            continue
        keep.append({"id": f"wiki:{a['pageid']}", "title": a["title"], "text": f"{a['title']}。{a['extract']}", "judged": False})
    random.Random(seed).shuffle(keep)  # 件数を増やすときは先頭から取る（1千件は1万件に含まれる）
    return keep, sorted(excluded)


# ---------------------------------------------------------------- 埋め込み（作ったものは data/ に取っておく）

def embed_passages(emb, texts, batch_size):
    slug = emb.model_name.replace("/", "_")
    cache = DATA / f"emb_{slug}.npz"
    store = {}
    if cache.exists():
        z = np.load(cache, allow_pickle=False)
        store = dict(zip(z["keys"].tolist(), z["vecs"]))
    keys = [hashlib.sha256(t.encode("utf-8")).hexdigest()[:20] for t in texts]
    todo = [(k, t) for k, t in zip(keys, texts) if k not in store]
    if todo:
        print(f"埋め込みを作る：{len(todo)} 件", flush=True)
        vecs = emb.passage([t for _, t in todo], batch_size=batch_size)
        for (k, _), v in zip(todo, vecs):
            store[k] = np.asarray(v, dtype=np.float32)
        DATA.mkdir(exist_ok=True)
        np.savez(cache, keys=np.array(list(store.keys())), vecs=np.stack(list(store.values())))
    return np.stack([store[k] for k in keys])


# ---------------------------------------------------------------- 評価

def ndcg(ranked_ids, judgments):
    dcg = sum(judgments.get(d, 0) / math.log2(i + 2) for i, d in enumerate(ranked_ids[:TOP]))
    ideal = sorted(judgments.values(), reverse=True)[:TOP]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def select(rule, ids, scores):
    kind, val = rule
    if kind == "top":
        return ids[:val]
    if kind == "thr":
        return [d for d, s in zip(ids, scores) if s >= val]
    if kind == "gap":
        return [d for d, s in zip(ids[:10], scores[:10]) if s >= scores[0] - val]
    if kind == "top5thr":
        return [d for d, s in zip(ids[:5], scores[:5]) if s >= val]
    raise ValueError(kind)


def judge_set(sel, judgments, docs_by_id):
    pos = sum(1 for d in sel if judgments.get(d, 0) >= 2)
    neg = sum(1 for d in sel if judgments.get(d, 0) == 0)
    wiki = sum(1 for d in sel if not docs_by_id[d]["judged"] and judgments.get(d, 0) == 0)
    total = sum(1 for v in judgments.values() if v >= 2)
    return pos, neg, wiki, total


def rule_stats(rule, per_query, docs_by_id):
    P, R, N, W = [], [], [], []
    for q in per_query:
        sel = select(rule, q["ids"], q["scores"])
        pos, neg, wiki, total = judge_set(sel, q["judgments"], docs_by_id)
        if pos + neg:
            P.append(pos / (pos + neg))
        if total:
            R.append(pos / total)
        N.append(len(sel))
        W.append(wiki)
    m = lambda v: round(float(np.mean(v)), 3) if v else None  # noqa: E731
    return {"precision": m(P), "recall": m(R), "selected": m(N), "wiki": m(W)}


def f1(s):
    p, r = s["precision"] or 0, s["recall"] or 0
    return 2 * p * r / (p + r) if p + r else 0


def load_review(path, column):
    """人が確かめた一覧（review_*.csv）から、紛れ込みに付けた関連度を読む。空欄は不正解（0）のまま"""
    extra = {}
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            v = (row.get(column) or "").strip()
            if v:
                extra.setdefault(row["問いID"], {})[row["記事ID"]] = int(v)
    return extra


def run_size(n, judged, distractors, qvecs, qsrc, queries, jvecs, dvecs, extra=None):
    docs = judged + distractors[:n]
    vecs = np.vstack([jvecs, dvecs[:n]]) if n else jvecs
    by_id = {d["id"]: d for d in docs}
    per_query = []
    for q, qv, src in zip(queries, qvecs, qsrc):
        s = vecs @ qv
        order = np.argsort(-s)
        ids, scores = [], []
        for i in order:
            if docs[i]["id"] == src:
                continue  # Discovery 起点の問いでは、自分自身を除く
            ids.append(docs[i]["id"])
            scores.append(float(s[i]))
            if len(ids) >= 50:
                break
        j = {k: v for k, v in q["judgments"].items() if k != src}
        # 紛れ込みのうち、人（または仮判定）が関連度を付けたもの。その件数のときに混ぜていない記事は数えない
        # （入れると、件数の少ないときに「見つかるはずのない正解」が再現率・nDCG の分母に入ってしまう）
        j.update({k: v for k, v in (extra or {}).get(q["id"], {}).items() if k in by_id})
        best_pos = max([sc for d, sc in zip(ids, scores) if j.get(d, 0) >= 2], default=None)
        best_wiki = max([sc for d, sc in zip(ids, scores) if not by_id[d]["judged"] and j.get(d, 0) == 0], default=None)
        per_query.append({"qid": q["id"], "text": q.get("text") or f"（{q['source']} 起点）", "category": q.get("category"),
                          "ids": ids, "scores": scores, "judgments": j, "ndcg": ndcg(ids, j),
                          "wiki_top5": sum(1 for d in ids[:TOP] if not by_id[d]["judged"] and j.get(d, 0) == 0),
                          "top1_wiki": not by_id[ids[0]]["judged"] and j.get(ids[0], 0) == 0, "best_pos": best_pos, "best_wiki": best_wiki})
    return per_query, by_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[0, 1000, 10000])
    ap.add_argument("--device", default=None)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--env", default="", help="結果に書く環境の説明")
    ap.add_argument("--review", default=None, help="紛れ込みに関連度を付けた review_*.csv。付けたものは正解・不正解として数える")
    ap.add_argument("--review-column", default="判定（0〜3）", help="--review で使う列（例：仮判定（Claude））")
    args = ap.parse_args()

    data, judged = load_judged()
    distractors, excluded = load_distractors(data, args.seed)
    need = max(args.sizes)
    if need > len(distractors):
        sys.exit(f"紛れ込みが {len(distractors)} 件しかありません（{need} 件必要）。fetch_wikipedia.py の --max を増やしてください")
    distractors = distractors[:need]

    emb = Embedder(device=args.device)
    jvecs = embed_passages(emb, [d["text"] for d in judged], args.batch_size)
    dvecs = embed_passages(emb, [d["text"] for d in distractors], args.batch_size) if need else np.zeros((0, jvecs.shape[1]), dtype=np.float32)
    jid = {d["id"]: i for i, d in enumerate(judged)}
    queries = data["queries"]
    qvecs, qsrc = [], []
    for q in queries:
        if q["type"] == "discovery":
            qvecs.append(jvecs[jid[q["source"]]])
            qsrc.append(q["source"])
        else:
            qvecs.append(np.asarray(emb.query([q["text"]])[0], dtype=np.float32))
            qsrc.append(None)

    gap_grid = [0.005, 0.01, 0.02, 0.03, 0.05]
    out = {"meta": {"when": datetime.now().strftime("%Y-%m-%d %H:%M"), "model": emb.model_name, "device": emb.device,
                    "env": args.env, "sizes": args.sizes, "seed": args.seed, "judged": len(judged), "queries": len(queries),
                    "excluded_titles": excluded}, "sizes": {}}
    extra = load_review(args.review, args.review_column) if args.review else {}
    out["meta"]["review"] = {"file": args.review, "column": args.review_column, "judged": sum(len(v) for v in extra.values())} if args.review else None
    runs = {}
    for n in args.sizes:
        runs[n] = run_size(n, judged, distractors, qvecs, qsrc, queries, jvecs, dvecs, extra)
    # しきい値の候補は、モデルで点数の幅が違うため、最初の件数での上位10件の点数の幅から 0.01 刻みで作る
    first = [sc for q in runs[args.sizes[0]][0] for sc in q["scores"][:10]]
    lo, hi = math.floor(min(first) * 100) / 100, math.ceil(max(first) * 100) / 100
    thr_grid = [round(x, 2) for x in np.arange(lo, hi + 0.001, 0.01)]
    for n in args.sizes:
        pq, by_id = runs[n]
        rules = {}
        for k in (1, 3, 5, 10):
            rules[f"上位{k}件"] = rule_stats(("top", k), pq, by_id)
        for t in thr_grid:
            rules[f"しきい値 {t:.2f}"] = rule_stats(("thr", t), pq, by_id)
        for g in gap_grid:
            rules[f"1位との差 {g}"] = rule_stats(("gap", g), pq, by_id)
        out["sizes"][n] = {"per_query": pq, "rules": rules}
        print(f"{n:>6} 件：nDCG@5 {np.mean([q['ndcg'] for q in pq]):.3f}、上位5件の紛れ込み {np.mean([q['wiki_top5'] for q in pq]):.2f}", flush=True)

    # 0件のときに F1 がいちばん良いしきい値を選び、件数を増やしたときにもそのまま使う
    base = out["sizes"][args.sizes[0]]["rules"]
    best_t = max(thr_grid, key=lambda t: f1(base[f"しきい値 {t:.2f}"]))
    out["meta"]["best_threshold"] = best_t
    for n in args.sizes:
        pq = out["sizes"][n]["per_query"]
        by_id = runs[n][1]
        out["sizes"][n]["rules"][f"上位5件かつ {best_t:.2f} 以上"] = rule_stats(("top5thr", best_t), pq, by_id)

    write(out, judged, distractors)


def write(out, judged, distractors):
    RESULTS.mkdir(exist_ok=True)
    meta = out["meta"]
    slug = meta["model"].split("/")[-1] + ("_reviewed" if meta.get("review") else "")
    sizes = meta["sizes"]
    title = {d["id"]: d["title"] for d in judged + distractors}
    L = [f"# 件数が増えたときの検索精度（{meta['model']}）\n"]
    L.append(f"- 実行日時：{meta['when']}　環境：{meta['env']}（デバイス {meta['device']}）")
    L.append(f"- 正解付き：#1 の Discovery {meta['judged']} 件（文章は disc_topics）・問い {meta['queries']} 問。紛れ込み：Wikipedia（日本語版）の神奈川・東京あたりの地点記事の冒頭（{', '.join(f'{n:,}' for n in sizes)} 件）")
    L.append(f"- #1 の Discovery と同じものを指す記事（題名に Discovery の名前・対象を含むもの）は紛れ込みから外した：{len(meta['excluded_titles'])} 件")
    L.append("- 関連度2以上を正解、0 と紛れ込みを不正解、1 はどちらにも数えない。紛れ込みには正解を付けていないので、実は関係がある記事も不正解に数えている（下の「人が確かめる一覧」）")
    if meta.get("review"):
        rv = meta["review"]
        L.append(f"- 紛れ込みのうち {rv['judged']} 件に、{rv['file']} の「{rv['column']}」列で関連度を付けた。2以上は正解に、0 は不正解に数え、「紛れ込み」の数からは外す（関連度を付けたものは紛れ込みではなく、見つかってよいもの・よくないものとして扱う）")
    L.append("- 検索は全件比較（近似なし）\n")

    L.append("## 1. 並びの良さと紛れ込み\n")
    L.append("| 紛れ込みの件数 | nDCG@5 | 上位5件の紛れ込み（平均） | 1位が紛れ込みの問い | 正解の最高点（平均） | 紛れ込みの最高点（平均） |")
    L.append("|---|---|---|---|---|---|")
    for n in sizes:
        pq = out["sizes"][n]["per_query"]
        bp = [q["best_pos"] for q in pq if q["best_pos"] is not None]
        bw = [q["best_wiki"] for q in pq if q["best_wiki"] is not None]
        L.append(f"| {n:,} | {np.mean([q['ndcg'] for q in pq]):.3f} | {np.mean([q['wiki_top5'] for q in pq]):.2f} | "
                 f"{sum(q['top1_wiki'] for q in pq)}／{len(pq)} | {np.mean(bp):.3f} | {(f'{np.mean(bw):.3f}' if bw else '－')} |")

    L.append("\n## 2. 候補の決め方ごとの結果\n")
    L.append(f"各欄は「適合率／再現率・出す件数（平均）・紛れ込み（平均）」。しきい値は、紛れ込み0件のときに F1 がいちばん良かった **{meta['best_threshold']:.2f}** を中心に並べた。\n")
    names = [f"上位{k}件" for k in (1, 3, 5, 10)]
    bt = meta["best_threshold"]
    names += [f"しきい値 {t:.2f}" for t in (round(bt - 0.02, 2), round(bt - 0.01, 2), bt, round(bt + 0.01, 2), round(bt + 0.02, 2))]
    names += [f"1位との差 {g}" for g in (0.005, 0.01, 0.02, 0.03, 0.05)]
    names += [f"上位5件かつ {bt:.2f} 以上"]
    L.append("| 決め方 | " + " | ".join(f"{n:,} 件" for n in sizes) + " |")
    L.append("|---|" + "---|" * len(sizes))
    for name in names:
        cells = []
        for n in sizes:
            s = out["sizes"][n]["rules"].get(name)
            if not s:
                cells.append("－")
                continue
            p = "－" if s["precision"] is None else f"{s['precision']:.2f}"
            r = "－" if s["recall"] is None else f"{s['recall']:.2f}"
            cells.append(f"{p}／{r}・{s['selected']}・{s['wiki']}")
        L.append(f"| {name} | " + " | ".join(cells) + " |")

    big = sizes[-1]
    L.append(f"\n## 3. 人が確かめる一覧（紛れ込み {big:,} 件のとき、上位5件に入った紛れ込み）\n")
    L.append("実は問いに関係がある記事なら、紛れ込みではなく「見つかってよいもの」。`review_*.csv` の「判定」列に 0〜3 を付ける。\n")
    L.append("| 問い | 順位 | 記事 | 点数 |")
    L.append("|---|---|---|---|")
    rows = []
    for q in out["sizes"][big]["per_query"]:
        for rank, (d, s) in enumerate(zip(q["ids"][:TOP], q["scores"][:TOP]), 1):
            if d.startswith("wiki:"):
                rows.append([q["qid"], q["text"], rank, title[d], d, f"{s:.4f}", ""])
                L.append(f"| {q['text'][:30]} | {rank} | {title[d]} | {s:.3f} |")
    (RESULTS / f"accuracy_{slug}.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    with open(RESULTS / f"review_{slug}.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["問いID", "問い", "順位", "記事", "記事ID", "点数", "判定（0〜3）"])
        w.writerows(rows)
    for n in sizes:  # 記事の本文は入れない（題名と id、点数だけ）
        for q in out["sizes"][n]["per_query"]:
            q["titles"] = [title[d] for d in q["ids"]]
    with gzip.open(RESULTS / f"accuracy_{slug}.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    print(f"結果：{RESULTS / f'accuracy_{slug}.md'}")


if __name__ == "__main__":
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    main()
