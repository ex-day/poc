"""ex-day PoC（Issue #9）：保存済みの結果（.json.gz）を、保存されている全件比較の1回を正解として再集計する

改修前（正解を保存していなかった版）の結果に使う。速さは計り直さない。
正解には、保存されている全件比較の runs[0]（1回目）を使い、results[].truth に残す。

python reaggregate.py results/model_256d_cpu.json.gz [...]
"""
import gzip
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import psycopg  # noqa: F401
except ImportError:  # 再集計だけなら DB は要らない
    sys.modules["psycopg"] = types.ModuleType("psycopg")
    pv = types.ModuleType("pgvector"); pvp = types.ModuleType("pgvector.psycopg")
    pvp.register_vector = lambda *a, **k: None
    sys.modules["pgvector"] = pv; sys.modules["pgvector.psycopg"] = pvp
from bench_scale import quality, to_markdown  # noqa: E402

SOURCE = "保存されている全件比較の1回目（runs[0]）。正解を保存していなかった版の結果を再集計したもの"


def main(paths):
    for p in map(Path, paths):
        d = json.load(gzip.open(p, "rt", encoding="utf-8"))
        k = d["meta"]["info"]["args"]["k"]
        for r in d["results"]:
            if "truth" in r:
                continue
            ex = r["exact"]
            sets = {"all": ex["exact"]["runs"][0]}
            for name in ex:
                if name.startswith("exact grp"):
                    sets[name[len("exact "):]] = ex[name]["runs"][0]
            r["truth"] = {"source": SOURCE, "sets": {key: {"ids": v["ids"], "dists": v["dists"]} for key, v in sets.items()}}
            groups = [ex, r["hnsw"], r["partial"]]
            for g in groups:
                for name, c in g.items():
                    key = "all"
                    for f in sets:
                        if f != "all" and f in name:
                            key = f
                    last = c["runs"][-1]
                    c.update(quality(sets[key]["ids"], last["ids"], last["dists"], k))
        with gzip.open(p, "wt", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
        md = p.with_name(p.name.replace(".json.gz", ".md"))
        md.write_text(to_markdown(d["results"], d["meta"]))
        print(f"再集計：{p} → {md}")


if __name__ == "__main__":
    main(sys.argv[1:])
