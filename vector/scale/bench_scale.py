"""ex-day PoC（Issue #9）：件数を増やしたときのベクトル検索の速さと再現率を測る

測るもの（件数ごと）：
  1. 読み込み時間
  2. 全件比較（インデックスなし）の応答時間。これを正解（真の上位k件と距離）とする
  3. HNSW インデックスの作成時間・サイズ
  4. HNSW の応答時間・再現率・順位（ef_search を振る）
  5. 絞り込み（WHERE）を付けたときの応答時間・再現率・順位・件数の不足
     - iterative index scan（pgvector 0.8 系）の off / relaxed_order / relaxed_order＋再ソート / strict_order
     - 部分インデックス（絞り込みの条件で作ったインデックス）

指標：
  recall   ：上位k件の集合が、全件比較の上位k件とどれだけ一致するか（Recall@k）
  top1     ：1位が全件比較の1位と一致した問いの割合
  ordered  ：返ってきた順が距離の小さい順になっていた問いの割合（relaxed_order は崩れることがある）
  shortfall：k件に届かなかった問いの割合
  uses_index：測定に使ったのと同じ SQL・パラメータの実行計画に、そのインデックスが現れたか

測り方：
  - 条件ごとに、同じ数の暖機（--warmup）をしてから、全部の問いを流す
  - これを --repeat 回くり返す。回ごとに、条件の順番と問いの順番を入れ替える
  - p50・p95 は全部の回をまとめて出す。p95 は nearest-rank（昇順で ceil(0.95n) 番目）
  - 問いごとの時間・取得した id・距離、実行計画、実行引数、DB の設定、ライブラリの版を JSON（gzip）に残す

ベクトルの作り方（--vectors）：
  synthetic：合成ベクトル。実際の埋め込みと同じく「無関係な文章どうしでも点数が高めに固まる」形にしてある
             （共通の方向＋話題のまとまり＋ゆらぎ）。モデルのダウンロードが要らず、どこでも回せる
  model    ：gen_texts.py で作った架空の Discovery の文章を、EXDAY_MODEL のモデルで埋め込む（Mac 等、モデルが使える環境で）
             --device で実行デバイス（cpu / mps / cuda）を指定できる。一括の作成時間と、1件ずつの作成時間を分けて測る

使い方：
  python bench_scale.py                                  # 既定：synthetic、256次元、1千・1万・10万件
  python bench_scale.py --sizes 1000 10000 --dim 384
  EXDAY_MODEL=cl-nagoya/ruri-v3-30m python bench_scale.py --vectors model --device cpu --sizes 1000 10000
"""
import argparse
import gzip
import json
import math
import platform
import random
import sys
import time
from datetime import datetime
from importlib import metadata
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import connect  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"
TABLE = "poc_scale.item"
ITERATIVE_MODES = ("off", "relaxed_order", "relaxed_order+resort", "strict_order")


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

    return make(n), make(n_queries), {}


def model_vectors(n, n_queries, args, seed=0):
    """一括の作成（文章をまとめて埋め込む）と、1件ずつの作成（検索の問い・1件の更新に相当）を分けて測る。
    モデルの読み込み時間も別に記録する。DB への書き込み・変更検出（content_hash）は含まない。"""
    from common import Embedder
    from gen_texts import generate_discoveries, generate_queries

    t0 = time.perf_counter()
    emb = Embedder(device=args.device)
    load_sec = time.perf_counter() - t0

    texts = generate_discoveries(n, seed=seed)
    q_texts = generate_queries(n_queries, seed=seed + 1)

    t0 = time.perf_counter()
    vecs = emb.passage(texts, batch_size=args.batch_size)
    batch_sec = time.perf_counter() - t0

    # 1件ずつ：問い（query）と、Discovery 1件の作り直し（passage）
    emb.query(q_texts[:3])  # 暖機
    single_q, single_p = [], []
    for t in q_texts[: args.single]:
        t0 = time.perf_counter()
        emb.query([t])
        single_q.append((time.perf_counter() - t0) * 1000)
    for t in texts[: args.single]:
        t0 = time.perf_counter()
        emb.passage([t])
        single_p.append((time.perf_counter() - t0) * 1000)

    q = emb.query(q_texts, batch_size=args.batch_size)
    info = {
        "model": emb.model_name,
        "device": emb.device,
        "batch_size": args.batch_size,
        "model_load_sec": round(load_sec, 2),
        "embed_batch_sec": round(batch_sec, 2),
        "embed_batch_ms_per_item": round(batch_sec / n * 1000, 3),
        "embed_single_query": stats(single_q),
        "embed_single_passage": stats(single_p),
        "text_chars_mean": round(float(np.mean([len(t) for t in texts])), 1),
        "distinct_queries": len(set(q_texts)),
    }
    return np.asarray(vecs, dtype=np.float32), np.asarray(q, dtype=np.float32), info


# ---------------------------------------------------------------- DB

def setup_table(conn, dim):
    conn.execute("CREATE SCHEMA IF NOT EXISTS poc_scale")
    conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
    # grp：0〜9 の一様な値。WHERE grp < k で、選ばれる割合（k割）を作る
    #      例：成立済みの Discovery が全体の2割、のような状況の代わり（意味や場所とは相関しない）
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


def build_sql(k, where="", resort=False):
    """測定と実行計画の確認で、同じ SQL を使う"""
    w = f"WHERE {where}" if where else ""
    inner = f"SELECT id, embedding <=> %(q)s AS d FROM {TABLE} {w} ORDER BY embedding <=> %(q)s LIMIT {k}"
    if resort:
        # relaxed_order は距離順が前後することがあるため、取り出した候補を距離で並べ直す（pgvector の README の方法）。
        # PostgreSQL 17 以降は、MATERIALIZED の CTE の並び順を外側に引き継ぐため、ORDER BY d だと
        # 「もう並んでいる」と見なされて並べ直しが省かれる。d + 0 にして必ず並べ直させる
        return f"WITH c AS MATERIALIZED ({inner}) SELECT id, d FROM c ORDER BY d + 0"
    return inner


def condition(name, k, where="", settings=(), resort=False, index=None):
    return {"name": name, "sql": build_sql(k, where, resort), "settings": list(settings), "index": index}


def explain(conn, cond, q):
    with conn.transaction():
        for s in cond["settings"]:
            conn.execute(s)
        plan = [r[0] for r in conn.execute("EXPLAIN " + cond["sql"], {"q": q}).fetchall()]
    used = bool(cond["index"]) and any(cond["index"] in line for line in plan)
    return plan, used


def run_condition(conn, cond, queries, order, warmup):
    """暖機してから、order の順に問いを流す。問いごとの時間・id・距離を返す（id・距離は問いの番号順に並べ直す）"""
    with conn.transaction():
        for s in cond["settings"]:
            conn.execute(s)
        for i in order[:warmup]:
            conn.execute(cond["sql"], {"q": queries[i]}).fetchall()
        times = [None] * len(queries)
        ids = [None] * len(queries)
        dists = [None] * len(queries)
        for i in order:
            t0 = time.perf_counter()
            rows = conn.execute(cond["sql"], {"q": queries[i]}).fetchall()
            times[i] = (time.perf_counter() - t0) * 1000
            ids[i] = [r[0] for r in rows]
            dists[i] = [round(float(r[1]), 6) for r in rows]
    return times, ids, dists


def stats(times):
    """p50 は中央値、p95 は nearest-rank（昇順で ceil(0.95n) 番目）"""
    s = sorted(times)
    n = len(s)
    p50 = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    p95 = s[max(0, math.ceil(0.95 * n) - 1)]
    return {"n": n, "p50_ms": round(p50, 2), "p95_ms": round(p95, 2)}


def quality(truth_ids, ids, dists, k):
    recall = [len(set(t[:k]) & set(g[:k])) / max(1, min(k, len(t))) for t, g in zip(truth_ids, ids)]
    top1 = [1.0 if (g and t and g[0] == t[0]) else 0.0 for t, g in zip(truth_ids, ids)]
    ordered = [1.0 if all(d[i] <= d[i + 1] + 1e-9 for i in range(len(d) - 1)) else 0.0 for d in dists]
    short = [1.0 if len(g) < k else 0.0 for g in ids]
    r = lambda v: round(float(np.mean(v)), 4)  # noqa: E731
    return {"recall": r(recall), "top1": r(top1), "ordered": r(ordered), "shortfall": r(short)}


def measure_phase(conn, conds, queries, truth, args, rng):
    """1つの段階（同じインデックスの状態）の条件群を、--repeat 回、順番を入れ替えて測る"""
    out = {c["name"]: {"times": [], "runs": []} for c in conds}
    nq = len(queries)
    for rep in range(args.repeat):
        order_c = list(conds)
        rng.shuffle(order_c)
        for c in order_c:
            order_q = list(range(nq))
            rng.shuffle(order_q)
            t, ids, dists = run_condition(conn, c, queries, order_q, args.warmup)
            out[c["name"]]["times"].extend(t)
            out[c["name"]]["runs"].append({"repeat": rep, "times_ms": [round(x, 3) for x in t], "ids": ids, "dists": dists})
    res = {}
    for c in conds:
        o = out[c["name"]]
        last = o["runs"][-1]
        plan, used = explain(conn, c, queries[0])
        tr = truth.get(c.get("truth_key", ""), None)
        q = quality(tr, last["ids"], last["dists"], args.k) if tr is not None else {}
        res[c["name"]] = {**stats(o["times"]), **q, "uses_index": used, "plan": plan, "sql": c["sql"], "settings": c["settings"], "runs": o["runs"]}
    return res


# ---------------------------------------------------------------- 1つの件数での計測

def bench_size(conn, vecs, queries, args, extra):
    n, dim = vecs.shape
    k = args.k
    rng = random.Random(args.seed)
    res = {"n": n, "dim": dim, **extra}
    print(f"\n=== {n:,} 件・{dim}次元 ===", flush=True)

    setup_table(conn, dim)
    res["load_sec"] = round(load(conn, vecs), 2)
    res["table_mb"] = round(rel_size_mb(conn, TABLE), 1)
    print(f"読み込み {res['load_sec']} 秒、テーブル {res['table_mb']} MB", flush=True)

    filters = {f"grp<{s}": f"grp < {s}" for s in args.filter_tenths}
    no_idx = ("SET LOCAL enable_indexscan = off",)

    # 段階1：インデックスなし（全件比較）。正解を作る
    conds = [dict(condition("exact", k, settings=no_idx), truth_key="all")]
    conds += [dict(condition(f"exact {f}", k, where=w, settings=no_idx), truth_key=f) for f, w in filters.items()]
    # 正解は、全件比較の1回目の結果（どの回でも同じ）
    truth = {}
    tmp = {c["name"]: run_condition(conn, c, queries, list(range(len(queries))), 0) for c in conds}
    truth["all"] = tmp["exact"][1]
    for f in filters:
        truth[f] = tmp[f"exact {f}"][1]
    # 再現率・1位の基準に使った正解を、そのまま残す（同点があると全件比較でも回ごとに並びが入れ替わるため）
    res["truth"] = {"source": "計測前に1回流した全件比較（問いの番号順）",
                    "sets": {key: {"ids": tmp[name][1], "dists": tmp[name][2]}
                             for key, name in [("all", "exact")] + [(f, f"exact {f}") for f in filters]}}
    res["exact"] = measure_phase(conn, conds, queries, truth, args, rng)
    print(f"全件比較 {summary(res['exact']['exact'])}", flush=True)

    # 段階2：HNSW（全体）
    conn.execute(f"SET maintenance_work_mem = '{args.maintenance_work_mem}'")
    t0 = time.perf_counter()
    conn.execute(f"CREATE INDEX item_hnsw ON {TABLE} USING hnsw (embedding vector_cosine_ops) WITH (m = {args.m}, ef_construction = {args.ef_construction})")
    res["hnsw_build_sec"] = round(time.perf_counter() - t0, 2)
    res["hnsw_mb"] = round(rel_size_mb(conn, "poc_scale.item_hnsw"), 1)
    print(f"HNSW 作成 {res['hnsw_build_sec']} 秒、{res['hnsw_mb']} MB", flush=True)

    conds = [dict(condition(f"hnsw ef={ef}", k, settings=(f"SET LOCAL hnsw.ef_search = {ef}",), index="item_hnsw"), truth_key="all") for ef in args.ef_search]
    for f, w in filters.items():
        for mode in ITERATIVE_MODES:
            base = mode.replace("+resort", "")
            st = (f"SET LOCAL hnsw.ef_search = {args.filter_ef}", f"SET LOCAL hnsw.iterative_scan = {base}")
            conds.append(dict(condition(f"hnsw {f} {mode}", k, where=w, settings=st, resort=mode.endswith("+resort"), index="item_hnsw"), truth_key=f))
    res["hnsw"] = measure_phase(conn, conds, queries, truth, args, rng)
    for name, r in res["hnsw"].items():
        print(f"  {name}: {summary(r)}", flush=True)

    # 段階3：部分インデックス（絞り込みの条件と同じ条件で作る）
    conn.execute("DROP INDEX poc_scale.item_hnsw")
    res["partial"] = {}
    for f, w in filters.items():
        idx = "item_hnsw_part"
        t0 = time.perf_counter()
        conn.execute(f"CREATE INDEX {idx} ON {TABLE} USING hnsw (embedding vector_cosine_ops) WITH (m = {args.m}, ef_construction = {args.ef_construction}) WHERE {w}")
        build = round(time.perf_counter() - t0, 2)
        size = round(rel_size_mb(conn, f"poc_scale.{idx}"), 1)
        c = dict(condition(f"partial {f}", k, where=w, settings=(f"SET LOCAL hnsw.ef_search = {args.filter_ef}",), index=idx), truth_key=f)
        r = measure_phase(conn, [c], queries, truth, args, rng)[c["name"]]
        res["partial"][f] = {"build_sec": build, "mb": size, **r}
        print(f"  部分インデックス {f}: {summary(r)}（作成 {build} 秒・{size} MB）", flush=True)
        conn.execute(f"DROP INDEX poc_scale.{idx}")
    return res


def summary(r):
    keys = ("p50_ms", "p95_ms", "recall", "top1", "ordered", "shortfall", "uses_index")
    return {k: r[k] for k in keys if k in r}


# ---------------------------------------------------------------- 記録

def env_info(conn, args):
    def ver(p):
        try:
            return metadata.version(p)
        except metadata.PackageNotFoundError:
            return None

    settings = {}
    for s in ("server_version", "shared_buffers", "work_mem", "max_parallel_workers_per_gather", "max_parallel_maintenance_workers", "jit", "effective_cache_size"):
        settings[s] = conn.execute(f"SHOW {s}").fetchone()[0]
    settings["pgvector"] = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0]
    return {
        "args": vars(args),
        "db": settings,
        "python": platform.python_version(),
        "machine": f"{platform.system()} {platform.release()} {platform.machine()} {platform.processor()}".strip(),
        "libs": {p: ver(p) for p in ("numpy", "psycopg", "pgvector", "torch", "sentence-transformers", "transformers")},
    }


def cell(r):
    s = f"{r['p50_ms']}／{r['p95_ms']}・{r.get('recall', 0):.3f}"
    if r.get("top1", 1) < 1 or r.get("ordered", 1) < 1:
        s += f"（1位 {r['top1']:.2f}・順 {r['ordered']:.2f}）"
    if r.get("shortfall"):
        s += f"・不足 {r['shortfall']:.0%}"
    return s


def mark(r, expect_index):
    s = cell(r)
    if expect_index and not r["uses_index"]:
        s += "（全件走査）"
    return s


def to_markdown(results, meta):
    L = []
    L.append(f"# 件数を増やしたときのベクトル検索（{meta['vectors']}・{meta['dim']}次元）\n")
    L.append(f"- 実行日時：{meta['when']}")
    L.append(f"- 環境：{meta['env']}（{meta['info']['machine']}）")
    db = meta["info"]["db"]
    L.append(f"- PostgreSQL {db['server_version']}／pgvector {db['pgvector']}。shared_buffers={db['shared_buffers']}、max_parallel_workers_per_gather={db['max_parallel_workers_per_gather']}")
    a = meta["info"]["args"]
    L.append(f"- HNSW：m={a['m']}、ef_construction={a['ef_construction']}。問い {a['queries']} 件、上位 {a['k']} 件。条件ごとに暖機 {a['warmup']} 件、{a['repeat']} 回くり返し（回ごとに条件と問いの順番を入れ替え）")
    L.append("- 時間は「p50／p95（ms）」。p95 は nearest-rank。再現率・1位・順は最後の回の結果")
    src = (results[0].get("truth") or {}).get("source", "計測前に1回流した全件比較")
    L.append(f"- 再現率・1位の正解：{src}（JSON の results[].truth）")
    L.append("- 1位：1位が全件比較の1位と一致した割合。順：距離の小さい順に並んでいた割合。どちらも 1.00 のときは省略")
    L.append("- （全件走査）：測定と同じ SQL の実行計画に、HNSW のインデックスが現れなかった（件数が少ないとプランナーが全件走査を選ぶ）")
    if results and "model" in results[0]:
        r0 = results[0]
        L.append(f"- モデル：{r0['model']}（デバイス {r0['device']}、バッチ {r0['batch_size']}）")
    L.append("")

    L.append("## 速さとサイズ\n")
    efs = a["ef_search"]
    L.append("| 件数 | 読込(秒) | 表(MB) | 全件比較 | HNSW作成(秒) | HNSW(MB) | " + " | ".join(f"ef={e}" for e in efs) + " |")
    L.append("|" + "---|" * (6 + len(efs)))
    for r in results:
        ex = r["exact"]["exact"]
        cells = [f"{r['n']:,}", str(r["load_sec"]), str(r["table_mb"]), f"{ex['p50_ms']}／{ex['p95_ms']}", str(r["hnsw_build_sec"]), str(r["hnsw_mb"])]
        cells += [mark(r["hnsw"][f"hnsw ef={e}"], True) for e in efs]
        L.append("| " + " | ".join(cells) + " |")
    L.append("")

    L.append(f"## 絞り込み（WHERE grp < k。k割が残る）と iterative scan（ef_search={a['filter_ef']}）\n")
    L.append("再現率・1位・順は、同じ絞り込みでの全件比較に対するもの。grp は意味や場所と相関しない一様な値で、候補が足りなくなる仕組みを見るためのもの。\n")
    L.append("| 件数 | 絞り込み | 全件比較 | off | relaxed_order | relaxed_order＋再ソート | strict_order | 部分インデックス（作成秒・MB） |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in results:
        for f in [x for x in r["partial"]]:
            ex = r["exact"][f"exact {f}"]
            row = [f"{r['n']:,}", f, f"{ex['p50_ms']}／{ex['p95_ms']}"]
            row += [mark(r["hnsw"][f"hnsw {f} {m}"], True) for m in ITERATIVE_MODES]
            p = r["partial"][f]
            row.append(f"{mark(p, True)}（{p['build_sec']}・{p['mb']}）")
            L.append("| " + " | ".join(row) + " |")

    if results and "embed_batch_sec" in results[0]:
        L.append("\n## 埋め込みの作成\n")
        L.append("一括：文章をまとめて埋め込む時間（DB への書き込み・変更検出は含まない）。1件ずつ：問い1件・Discovery 1件をそれぞれ単独で埋め込む時間（検索の問い、1件の作り直しに相当）。\n")
        L.append("| 件数 | 文章の平均字数 | モデル読込(秒) | 一括(秒) | 一括 1件あたり(ms) | 1件ずつ 問い p50／p95(ms) | 1件ずつ Discovery p50／p95(ms) |")
        L.append("|---|---|---|---|---|---|---|")
        for r in results:
            sq, sp = r["embed_single_query"], r["embed_single_passage"]
            L.append(f"| {r['n']:,} | {r['text_chars_mean']} | {r['model_load_sec']} | {r['embed_batch_sec']} | {r['embed_batch_ms_per_item']} | {sq['p50_ms']}／{sq['p95_ms']} | {sp['p50_ms']}／{sp['p95_ms']} |")
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
    ap.add_argument("--warmup", type=int, default=10, help="条件ごとの暖機の問い数")
    ap.add_argument("--repeat", type=int, default=3, help="条件ごとのくり返し回数（回ごとに順番を入れ替える）")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--maintenance-work-mem", default="1GB")
    ap.add_argument("--device", default=None, help="model のとき：cpu / mps / cuda（省略時は sentence-transformers が自動で選ぶ）")
    ap.add_argument("--batch-size", type=int, default=32, help="model のとき：一括の埋め込みのバッチサイズ")
    ap.add_argument("--single", type=int, default=50, help="model のとき：1件ずつ測る件数")
    ap.add_argument("--env", default=platform.platform(), help="結果に書く環境の説明（機種など）")
    ap.add_argument("--tag", default="", help="結果のファイル名に付ける名前")
    args = ap.parse_args()

    conn = connect()
    info = env_info(conn, args)

    results = []
    for n in args.sizes:
        if args.vectors == "synthetic":
            vecs, queries, extra = synthetic_vectors(n, args.dim, args.queries, seed=args.seed)
        else:
            vecs, queries, extra = model_vectors(n, args.queries, args, seed=args.seed)
        results.append(bench_size(conn, vecs, queries, args, extra))

    meta = {"vectors": args.vectors, "dim": results[0]["dim"], "when": datetime.now().strftime("%Y-%m-%d %H:%M"), "env": args.env, "info": info}
    RESULTS_DIR.mkdir(exist_ok=True)
    name = f"{args.vectors}_{meta['dim']}d{('_' + args.tag) if args.tag else ''}"
    # 問いごとの結果を含むため大きい（10万件までで数MB）。gzip で保存する
    with gzip.open(RESULTS_DIR / f"{name}.json.gz", "wt", encoding="utf-8") as fh:
        json.dump({"meta": meta, "results": results}, fh, ensure_ascii=False)
    (RESULTS_DIR / f"{name}.md").write_text(to_markdown(results, meta))
    print(f"\n結果：{RESULTS_DIR / (name + '.md')}")


if __name__ == "__main__":
    main()
