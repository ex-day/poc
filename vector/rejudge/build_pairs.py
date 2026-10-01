"""ex-day PoC（Issue #12）：AI に再判定させる組（問い × 候補の記事）を作る

#9 で人が関連度を付けた100件（vector/accuracy/results/review_ruri-v3-30m.csv）から、
AI に渡す組を作る。**人の判定・Claude の仮判定は入れない**（判定役に答えを見せないため）。

- 問いが文章のとき：その文章を渡す
- Discovery 起点の問いのとき：起点の Discovery の文章（名前・対象・観点・わかってきたこと・話題）を渡す
- 候補：Wikipedia の記事の題名と冒頭（最大300字）

出力：data/pairs.jsonl（記事の本文を含むため、リポジトリには入れない）

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
    n = 0
    with open(REVIEW, encoding="utf-8") as fh, OUT.open("w", encoding="utf-8") as out:
        for i, row in enumerate(csv.DictReader(fh), 1):
            q = queries[row["問いID"]]
            a = wiki[row["記事ID"]]
            pair = {"pair_id": f"P{i:03d}", "query_id": q["id"], "candidate_id": row["記事ID"],
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
