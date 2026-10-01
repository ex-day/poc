"""ex-day PoC（Issue #12）：AI に再判定させる組（問い × 候補の記事）を作る

#9 で人が関連度を付けた100件（vector/accuracy/results/review_ruri-v3-30m.csv）から、
AI に渡す組を作る。**人の判定・Claude の仮判定は入れない**（判定役に答えを見せないため）。

- 問いが文章のとき：その文章を渡す
- Discovery 起点の問いのとき：起点の Discovery の文章（名前・対象・観点・わかってきたこと・話題）を渡す
- 候補：Wikipedia の記事の題名と冒頭（最大300字）

出力：data/pairs.jsonl（記事の本文を含むため、リポジトリには入れない）
pair_id は results/pairs_index.jsonl（問いID・記事ID との対応）で固定する。ファイルがあればその対応を使い、
なければ作る。review の並びが変わっても、判定済みの結果との対応がずれないようにするため。

python build_pairs.py
"""
import csv
import json
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
VEC = HERE.parent
sys.path.insert(0, str(VEC))
try:
    import psycopg  # noqa: F401
except ImportError:  # 文章を作るだけなら DB は要らない
    sys.modules["psycopg"] = types.ModuleType("psycopg")
    pvp = types.ModuleType("pgvector.psycopg"); pvp.register_vector = lambda *a, **k: None
    sys.modules["pgvector"] = types.ModuleType("pgvector"); sys.modules["pgvector.psycopg"] = pvp
from load import discovery_texts  # noqa: E402

REVIEW = VEC / "accuracy" / "results" / "review_ruri-v3-30m.csv"
WIKI = VEC / "accuracy" / "data" / "wikipedia_kanto.jsonl"
OUT = HERE / "data" / "pairs.jsonl"


def main():
    data = json.loads((VEC / "sample_data.json").read_text(encoding="utf-8"))
    topics = data.get("topics", [])
    disc = {d["id"]: discovery_texts(d, [t for t in topics if t["discovery_id"] == d["id"]])["topics"] for d in data["discoveries"]}
    queries = {q["id"]: q for q in data["queries"]}
    wiki = {}
    for line in WIKI.read_text(encoding="utf-8").splitlines():
        a = json.loads(line)
        wiki[f"wiki:{a['pageid']}"] = a
    OUT.parent.mkdir(exist_ok=True)
    index_path = HERE / "results" / "pairs_index.jsonl"
    index = {}
    if index_path.exists():
        for l in index_path.read_text(encoding="utf-8").splitlines():
            x = json.loads(l)
            index[(x["query_id"], x["candidate_id"])] = x["pair_id"]
    rows = list(csv.DictReader(open(REVIEW, encoding="utf-8")))
    if index:
        assert {(r["問いID"], r["記事ID"]) for r in rows} == set(index), "review の組が pairs_index と一致しない"
    else:
        index = {(r["問いID"], r["記事ID"]): f"P{i:03d}" for i, r in enumerate(rows, 1)}
        index_path.parent.mkdir(exist_ok=True)
        with index_path.open("w", encoding="utf-8") as fh:
            for (qid, cid), pid in sorted(index.items(), key=lambda kv: kv[1]):
                fh.write(json.dumps({"pair_id": pid, "query_id": qid, "candidate_id": cid, "candidate_title": wiki[cid]["title"]}, ensure_ascii=False) + "\n")
    rows.sort(key=lambda r: index[(r["問いID"], r["記事ID"])])
    n = 0
    with OUT.open("w", encoding="utf-8") as out:
        for row in rows:
            q = queries[row["問いID"]]
            a = wiki[row["記事ID"]]
            pair = {"pair_id": index[(row["問いID"], row["記事ID"])], "query_id": q["id"], "candidate_id": row["記事ID"],
                    "candidate_title": a["title"], "candidate_text": a["extract"]}
            if q["type"] == "discovery":
                pair["query_kind"] = "discovery"
                pair["query"] = disc[q["source"]]
            else:
                pair["query_kind"] = "text"
                pair["query"] = q["text"]
            out.write(json.dumps(pair, ensure_ascii=False) + "\n")
            n += 1
    print(f"{n} 組 → {OUT}")


if __name__ == "__main__":
    main()
