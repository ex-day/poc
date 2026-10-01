"""ex-day PoC（Issue #8）：AI にまとめて話題へ振り分けさせるための入力を作る

正解（topics）とメモ（note）は入れない。返信先は「あり」（そのまま）と「なし」（全部消す）の2通り。
区切って渡す条件のために、会話を CHUNK 件ずつに分けたファイルも作る。

出力：
  data/<reply|noreply>/<会話>.json               … 一括で渡す
  data/<reply|noreply>/<会話>/chunk_NN.json       … 区切って順に渡す

python build_inputs.py
"""
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
CTX = HERE.parent
CHUNK = 10


def load():
    files = sorted((CTX / "conversations").glob("*.json")) + [CTX / "posts.json"]
    for f in files:
        c = json.loads(f.read_text(encoding="utf-8"))
        posts = []
        for p in c["posts"]:
            if p.get("new_thread"):
                continue
            r = p.get("reply_to")
            r = r if isinstance(r, list) else ([r] if r else [])
            posts.append({"id": p["id"], "day": p.get("day", 1), "author": p["author"], "reply_to": r, "text": p["text"]})
        yield f.stem, posts


def main():
    out = HERE / "data"
    if out.exists():
        shutil.rmtree(out)
    for name, posts in load():
        for cond in ("reply", "noreply"):
            ps = [dict(p, reply_to=(p["reply_to"] if cond == "reply" else [])) for p in posts]
            d = out / cond
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{name}.json").write_text(json.dumps({"conversation": name, "posts": ps}, ensure_ascii=False, indent=1), encoding="utf-8")
            cd = d / name
            cd.mkdir()
            for k in range(0, len(ps), CHUNK):
                (cd / f"chunk_{k // CHUNK + 1:02d}.json").write_text(
                    json.dumps({"conversation": name, "chunk": k // CHUNK + 1, "posts": ps[k:k + CHUNK]}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(name, len(posts))


if __name__ == "__main__":
    main()
