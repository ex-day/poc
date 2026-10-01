"""ex-day PoC（Issue #9）：件数を増やしたときのベクトル検索の速さと再現率を測る

測るもの（件数ごと）：
  1. 読み込み時間
  2. 全件比較（インデックスなし）の応答時間。これを正解（真の上位k件）とする
  3. HNSW インデックスの作成時間・サイズ
  4. HNSW の応答時間と再現率（ef_search を振る）
  5. 絞り込み（WHERE）を付けたときの応答時間・再現率・件数の不足
     - iterative index scan（pgvector 0.8 系）の off / relaxed_order / strict_order
     - 部分インデックス（絞り込みの条件で作ったインデックス）

ベクトルの作り方（--vectors）：
  synthetic：合成ベクトル。実際の埋め込みと同じく「無関係な文章どうしでも点数が高めに固まる」形にしてある
             （共通の方向＋話題のまとまり＋ゆらぎ）。モデルのダウンロードが要らず、どこでも回せる
  model    ：gen_texts.py で作った架空の Discovery の文章を、EXDAY_MODEL のモデルで埋め込む（Mac 等、モデルが使える環境で）

使い方：
  python bench_scale.py                                  # 既定：synthetic、256次元、1千・1万・10万件
  python bench_scale.py --sizes 1000 10000 --dim 384
  EXDAY_MODEL=cl-nagoya/ruri-v3-30m python bench_scale.py --vectors model --sizes 1000 10000
"""
import argparse
import json
import os
import platform
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import connect  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"
TABLE = "poc_scale.item"


# ---------------------------------------------------------------- ベクトルを作る

def synthetic_vectors(n, dim, n_queries, seed=0):
    """共通の方向 c0 ＋ 話題の中心 ck ＋ ゆらぎ。
    重み（2乗）を 0.75 / 0.15 / 0.10 にすると、コサイン類似度は
    無関係どうし ≒ 0.75、同じ話題どうし ≒ 0.90 になる（実際の Ruri・e5 で見た「0.8 前後に固まる」に近づける）。"""
    rng = np.random.default_rng(seed)
    n_topics = max(20, n // 100)

    def unit(m):
        m = rng.standard_normal((m, dim)).astype(np.float32)
        return m / np.linalg.norm(m, axis=1, keepdims=True)

    c0 = unit(1)[0]
    centers = unit(n_topics)
    a, b, c = np.sqrt(0.75), np.sqrt(0.15), np.sqrt(0.10)

    def make(m):
        topic = rng.integers(0, n_topics, m)
        v = a * c0 + b * centers[topic] + c * unit(m)
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    return make(n), make(n_queries)


def model_vectors(n, n_queries, seed=0):
    from common import Embedder
    from gen_texts import generate_discoveries, generate_queries

    emb = Embedder()
    texts = generate_discoveries(n, seed=seed)
    t0 = time.perf_counter()
    vecs = emb.passage(texts)
    embed_sec = time.perf_counter() - t0
    q = emb.query(generate_queries(n_queries, seed=seed + 1))
    return np.asarray(vecs, dtype=np.float32), np.asarray(q, dtype=np.float32), embed_sec, emb.model_name


# ---------------------------------------------------------------- DB

def setup_table(conn, dim):
    conn.execute("CREATE SCHEMA IF NOT EXISTS poc_scale")
    conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
    # grp：0〜9 の一様な値。WHERE grp < k で、選ばれる割合（k割）を作る
    #      例：成立済みの Discovery が全体の2割、のような状況の代わり
    conn.execute(f"CREATE TABLE {TABLE} (id bigint PRIMARY KEY, grp smallint NOT NULL, embedding vector({dim}) NOT NULL)")


def load(conn, vecs, seed=0):
    rng = np.random.default_rng(seed + 99)
    grp = rng.integers(0, 10, len(vecs))
    t0 = time.perf_counter()
    with conn.cursor() as cur:
        with cur.copy(f"COPY {TABLE} (id, grp, embedding) FROM STDIN WITH (FORMAT BINARY)") as copy:
            copy.set_types(["int8", "int2", "vector"])
            for i, v in enumerate(vecs):
                copy.write_row((i, int(grp[i]), v))
    conn.execute(f"ANALYZE {TABLE}")
    return time.perf_counter() - t0


def rel_size_mb(conn, name):
    return conn.execute("SELECT pg_relation_size(%s::regclass)", (name,)).fetchone()[0] / 1024 / 1024


def run_queries(conn, queries, k, where="", settings=()):
    """問いを順に流し、応答時間（ms）と結果の id を返す"""
    with conn.transaction():
        for s in settings:
            conn.execute(s)
        sql = f"SELECT id FROM {TABLE} {('WHERE ' + where) if where else ''} ORDER BY embedding <=> %s LIMIT {k}"
        times, ids = [], []
        # 1回目はキャッシュの暖機として捨てる
        conn.execute(sql, (queries[0],)).fetchall()
        for q in queries:
            t0 = time.perf_counter()
            rows = conn.execute(sql, (q,)).fetchall()
            times.append((time.perf_counter() - t0) * 1000)
            ids.append([r[0] for r in rows])
    return times, ids


def stats(times):
    s = sorted(times)
    return {"p50_ms": round(statistics.median(s), 2), "p95_ms": round(s[int(len(s) * 0.95) - 1], 2)}


def recall(truth, got, k):
    vals = [len(set(t[:k]) & set(g[:k])) / max(1, min(k, len(t))) for t, g in zip(truth, got)]
    return round(float(np.mean(vals)), 4)


def shortfall(got, k):
    """k件に届かなかった問いの割合（絞り込みでインデックスが候補を取りこぼすと起きる）"""
    return round(sum(1 for g in got if len(g) < k) / len(got), 4)


def explain_uses_index(conn, q, where, settings, index_name):
    with conn.transaction():
        for s in settings:
            conn.execute(s)
        sql = f"EXPLAIN SELECT id FROM {TABLE} {('WHERE ' + where) if where else ''} ORDER BY embedding <=> %s LIMIT 10"
        plan = "\n".join(r[0] for r in conn.execute(sql, (q,)).fetchall())
    return index_name in plan


# ---------------------------------------------------------------- 1つの件数での計測

def bench_size(conn, vecs, queries, args, extra):
    n, dim = vecs.shape
    k = args.k
    res = {"n": n, "dim": dim, **extra}
    print(f"\n=== {n:,} 件・{dim}次元 ===", flush=True)

    setup_table(conn, dim)
    res["load_sec"] = round(load(conn, vecs), 2)
    res["table_mb"] = round(rel_size_mb(conn, TABLE), 1)
    print(f"読み込み {res['load_sec']} 秒、テーブル {res['table_mb']} MB", flush=True)

    # 全件比較（正解）
    no_idx = ("SET LOCAL enable_indexscan = off",)
    t, truth = run_queries(conn, queries, k, settings=no_idx)
    res["exact"] = stats(t)
    print(f"全件比較 {res['exact']}", flush=True)

    filters = {f"grp<{s}": f"grp < {s}" for s in args.filter_tenths}
    truth_f = {}
    res["exact_filtered"] = {}
    for name, w in filters.items():
        t, truth_f[name] = run_queries(conn, queries, k, where=w, settings=no_idx)
        res["exact_filtered"][name] = stats(t)

    # HNSW
    conn.execute(f"SET maintenance_work_mem = '{args.maintenance_work_mem}'")
    t0 = time.perf_counter()
    conn.execute(f"CREATE INDEX item_hnsw ON {TABLE} USING hnsw (embedding vector_cosine_ops) WITH (m = {args.m}, ef_construction = {args.ef_construction})")
    res["hnsw_build_sec"] = round(time.perf_counter() - t0, 2)
    res["hnsw_mb"] = round(rel_size_mb(conn, "poc_scale.item_hnsw"), 1)
    print(f"HNSW 作成 {res['hnsw_build_sec']} 秒、{res['hnsw_mb']} MB", flush=True)

    res["hnsw"] = {}
    for ef in args.ef_search:
        t, got = run_queries(conn, queries, k, settings=(f"SET LOCAL hnsw.ef_search = {ef}",))
        res["hnsw"][ef] = {**stats(t), "recall": recall(truth, got, k)}
        print(f"  ef_search={ef}: {res['hnsw'][ef]}", flush=True)

    # 絞り込み × iterative scan
    res["hnsw_filtered"] = {}
    for name, w in filters.items():
        res["hnsw_filtered"][name] = {}
        for mode in ("off", "relaxed_order", "strict_order"):
            st = (f"SET LOCAL hnsw.ef_search = {args.filter_ef}", f"SET LOCAL hnsw.iterative_scan = {mode}")
            t, got = run_queries(conn, queries, k, where=w, settings=st)
            used = explain_uses_index(conn, queries[0], w, st, "item_hnsw")
            r = {**stats(t), "recall": recall(truth_f[name], got, k), "shortfall": shortfall(got, k), "uses_hnsw": used}
            res["hnsw_filtered"][name][mode] = r
            print(f"  {name} iterative_scan={mode}: {r}", flush=True)

    # 部分インデックス（絞り込みの条件と同じ条件で作る）
    res["partial"] = {}
    conn.execute("DROP INDEX poc_scale.item_hnsw")
    for name, w in filters.items():
        idx = "item_hnsw_part"
        t0 = time.perf_counter()
        conn.execute(f"CREATE INDEX {idx} ON {TABLE} USING hnsw (embedding vector_cosine_ops) WITH (m = {args.m}, ef_construction = {args.ef_construction}) WHERE {w}")
        build = round(time.perf_counter() - t0, 2)
        size = round(rel_size_mb(conn, f"poc_scale.{idx}"), 1)
        st = (f"SET LOCAL hnsw.ef_search = {args.filter_ef}",)
        t, got = run_queries(conn, queries, k, where=w, settings=st)
        used = explain_uses_index(conn, queries[0], w, st, idx)
        res["partial"][name] = {"build_sec": build, "mb": size, **stats(t), "recall": recall(truth_f[name], got, k), "shortfall": shortfall(got, k), "uses_hnsw": used}
        print(f"  部分インデックス {name}: {res['partial'][name]}", flush=True)
        conn.execute(f"DROP INDEX poc_scale.{idx}")

    return res


# ---------------------------------------------------------------- 表にする

def to_markdown(results, meta):
    L = []
    L.append(f"# 件数を増やしたときのベクトル検索（{meta['vectors']}・{meta['dim']}次元）\n")
    L.append(f"- 実行日時：{meta['when']}")
    L.append(f"- 環境：{meta['env']}")
    L.append(f"- PostgreSQL：{meta['pg']}／pgvector {meta['pgvector']}")
    L.append(f"- HNSW：m={meta['m']}、ef_construction={meta['ef_construction']}、問い {meta['n_queries']} 件、上位 {meta['k']} 件")
    if meta.get("model"):
        L.append(f"- モデル：{meta['model']}")
    L.append("")
    L.append("## 速さとサイズ\n")
    efs = list(results[0]["hnsw"].keys())
    head = "| 件数 | 読込(秒) | 表(MB) | 全件比較 p50/p95(ms) | HNSW作成(秒) | HNSW(MB) | " + " | ".join(f"ef={e} p50/p95(ms)・再現率" for e in efs) + " |"
    L.append(head)
    L.append("|" + "---|" * (6 + len(efs)))
    for r in results:
        cells = [f"{r['n']:,}", str(r["load_sec"]), str(r["table_mb"]), f"{r['exact']['p50_ms']} / {r['exact']['p95_ms']}", str(r["hnsw_build_sec"]), str(r["hnsw_mb"])]
        cells += [f"{r['hnsw'][e]['p50_ms']} / {r['hnsw'][e]['p95_ms']}・{r['hnsw'][e]['recall']:.3f}" for e in efs]
        L.append("| " + " | ".join(cells) + " |")
    L.append("")
    L.append(f"## 絞り込み（WHERE grp < k。k割が残る）と iterative scan（ef_search={meta['filter_ef']}）\n")
    L.append("再現率は、同じ絞り込みでの全件比較の上位 k 件に対するもの。不足は、k 件に届かなかった問いの割合。\n")
    L.append("| 件数 | 絞り込み | 全件比較 p50(ms) | off p50・再現率・不足 | relaxed_order p50・再現率・不足 | strict_order p50・再現率・不足 | 部分インデックス p50・再現率・不足（作成秒・MB） |")
    L.append("|---|---|---|---|---|---|---|")
    for r in results:
        for name in r["hnsw_filtered"]:
            f = r["hnsw_filtered"][name]
            p = r["partial"][name]

            def c(x):
                s = f"{x['p50_ms']}・{x['recall']:.3f}・{x['shortfall']:.0%}"
                return s if x["uses_hnsw"] else s + "（索引不使用）"

            L.append(f"| {r['n']:,} | {name} | {r['exact_filtered'][name]['p50_ms']} | {c(f['off'])} | {c(f['relaxed_order'])} | {c(f['strict_order'])} | {c(p)}（{p['build_sec']}・{p['mb']}） |")
    if any("embed_sec" in r for r in results):
        L.append("\n## 埋め込みの作成\n")
        L.append("| 件数 | 作成(秒) | 1件あたり(ms) |")
        L.append("|---|---|---|")
        for r in results:
            if "embed_sec" in r:
                L.append(f"| {r['n']:,} | {r['embed_sec']:.1f} | {r['embed_sec'] / r['n'] * 1000:.1f} |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vectors", choices=["synthetic", "model"], default="synthetic")
    ap.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000, 100000])
    ap.add_argument("--dim", type=int, default=256, help="synthetic のときの次元（Ruri v3 30m=256、70m=384）")
    ap.add_argument("--queries", type=int, default=200)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--m", type=int, default=16)
    ap.add_argument("--ef-construction", type=int, default=64)
    ap.add_argument("--ef-search", type=int, nargs="+", default=[40, 100, 200])
    ap.add_argument("--filter-tenths", type=int, nargs="+", default=[1, 2, 5], help="WHERE grp < k の k（k割が残る）")
    ap.add_argument("--filter-ef", type=int, default=40)
    ap.add_argument("--maintenance-work-mem", default="1GB")
    ap.add_argument("--env", default=platform.platform(), help="結果に書く環境の説明")
    ap.add_argument("--tag", default="", help="結果のファイル名に付ける名前")
    args = ap.parse_args()

    conn = connect()
    pg = conn.execute("SHOW server_version").fetchone()[0]
    pgv = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0]

    results, model_name = [], None
    for n in args.sizes:
        extra = {}
        if args.vectors == "synthetic":
            vecs, queries = synthetic_vectors(n, args.dim, args.queries)
        else:
            vecs, queries, embed_sec, model_name = model_vectors(n, args.queries)
            extra["embed_sec"] = embed_sec
        results.append(bench_size(conn, vecs, queries, args, extra))

    meta = {
        "vectors": args.vectors, "dim": results[0]["dim"], "when": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "env": args.env, "pg": pg, "pgvector": pgv, "m": args.m, "ef_construction": args.ef_construction,
        "n_queries": args.queries, "k": args.k, "filter_ef": args.filter_ef, "model": model_name,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    name = f"{args.vectors}_{meta['dim']}d{('_' + args.tag) if args.tag else ''}"
    (RESULTS_DIR / f"{name}.json").write_text(json.dumps({"meta": meta, "results": results}, ensure_ascii=False, indent=1))
    (RESULTS_DIR / f"{name}.md").write_text(to_markdown(results, meta))
    print(f"\n結果：{RESULTS_DIR / (name + '.md')}")


if __name__ == "__main__":
    main()
