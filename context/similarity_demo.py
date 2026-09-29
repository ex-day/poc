"""会話の投稿どうしが、どのくらい近いかを見る（ex-day/poc#2）

使い方：
  python similarity_demo.py                 # 既定：Ruri v3（30m）
  python similarity_demo.py cl-nagoya/ruri-v3-70m intfloat/multilingual-e5-small

見るもの：
  1. 投稿どうしの近さの表（1.00 に近いほど近い）
  2. 投稿ごとに、いちばん近いほかの投稿（同じ話題なら ○、違えば ×）
  3. 積み重なった文脈と比べる：新しい投稿を、それより前の各投稿までの会話の積み重ね
     （返信先をたどったもの。例：P3 を「P1+P2」と、P6 を「P1+P5」と）と比べ、どの文脈がいちばん近いか
  4. 単語どうしの近さ（「汽車道」と「ロープウェイ」は近いか、など）

"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent

# 埋め込みのモデルと、先頭に付ける決まり（用途ごと）
# Ruri v3：""（意味）、"トピック: "（分類・クラスタリング・話題）、"検索クエリ: " / "検索文書: "（検索）
# e5：似ているかを比べる用途では、両方に "query: " を付ける
PREFIXES = {
    "cl-nagoya/ruri-v3": [("意味", ""), ("話題", "トピック: ")],
    "intfloat/multilingual-e5": [("意味", "query: ")],
}
DEFAULT_MODELS = ["cl-nagoya/ruri-v3-30m"]


def prefixes_for(model):
    for key, value in PREFIXES.items():
        if model.startswith(key):
            return value
    return [("そのまま", "")]


def cos_matrix(vectors):
    v = np.asarray(vectors, dtype=float)
    norm = np.linalg.norm(v, axis=1, keepdims=True)
    norm[norm == 0] = 1
    v = v / norm
    return v @ v.T


def show_matrix(title, ids, sim):
    print(f"\n■ {title}")
    print("      " + "".join(f"{i:>6}" for i in ids))
    for i, row in zip(ids, sim):
        print(f"{i:>6}" + "".join(f"{x:>6.2f}" for x in row))


def show_nearest(ids, sim, posts):
    topic = {p["id"]: set(p["topics"]) for p in posts}
    text = {p["id"]: p["text"] for p in posts}
    ok = 0
    for k, i in enumerate(ids):
        row = sim[k].copy()
        row[k] = -1
        j = int(np.argmax(row))
        if row[j] <= 1e-6:
            # どの投稿とも近さが0（共通の単語がない等）。判断の材料がない
            print(f"  {i}「{text[i][:16]}」→ どの投稿とも近さが 0（判断の材料なし） －")
            continue
        same = bool(topic[i] & topic[ids[j]])
        ok += same
        print(f"  {i}「{text[i][:16]}」→ いちばん近いのは {ids[j]}「{text[ids[j]][:16]}」（{row[j]:.2f}） {'○' if same else '×'}")
    print(f"  同じ話題の投稿がいちばん近かった数：{ok}/{len(ids)}（－ は数えない）")


def chain_of(pid, by_id):
    """返信先をたどって、最初の投稿からその投稿までの並び（例：P3 → [P1, P2, P3]）"""
    chain = []
    while pid:
        chain.append(pid)
        pid = by_id[pid]["reply_to"]
    return list(reversed(chain))


def show_context(title, posts, encode):
    """新しい投稿を、それより前の各投稿までの「積み重なった文脈」と比べる。
    例：P3 を「P1+P2」「P1」と、P6 を「P1+P5」「P1」「P1+P2+P3」…と比べる。
    文脈の話題は、その文脈の最後の投稿の話題とする。"""
    by_id = {p["id"]: p for p in posts}
    print(f"\n■ 積み重なった文脈と比べる：{title}")
    ok = 0
    n = 0
    for k, p in enumerate(posts):
        if k == 0:
            continue
        cands = []
        for q in posts[:k]:
            ids = chain_of(q["id"], by_id)
            cands.append(("+".join(ids), " ".join(by_id[x]["text"] for x in ids), set(q["topics"])))
        vecs = encode([p["text"]] + [c[1] for c in cands])
        sim = cos_matrix(vecs)[0, 1:]
        order = np.argsort(-sim)
        best = order[0]
        reply_ctx = "+".join(chain_of(p["reply_to"], by_id)) if p["reply_to"] else "－"
        if sim[best] <= 1e-6:
            print(f"  {p['id']}「{p['text'][:14]}」→ どの文脈とも近さが 0（判断の材料なし） －")
            continue
        n += 1
        same = bool(set(p["topics"]) & cands[best][2])
        ok += same
        ranking = "、".join(f"{cands[i][0]}（{sim[i]:.2f}）" for i in order[:3])
        print(f"  {p['id']}「{p['text'][:14]}」→ 近い順：{ranking} {'○' if same else '×'}（返信先の文脈：{reply_ctx}）")
    print(f"  いちばん近い文脈が同じ話題だった数：{ok}/{len(posts) - 1}（－ は数えない）")


def main():
    data = json.loads((HERE / "posts.json").read_text(encoding="utf-8"))
    posts = data["posts"]
    ids = [p["id"] for p in posts]
    texts = [p["text"] for p in posts]
    for p in posts:
        print(f"{p['id']} {p['author']}（返信先：{p['reply_to'] or 'なし'}／正解の話題：{','.join(p['topics'])}）：{p['text']}")

    # 積み重なった文脈と比べるときに使う「文章 → ベクトル」の関数
    encoders = []
    runs = []

    words = data.get("words", [])
    models = sys.argv[1:] or DEFAULT_MODELS
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("sentence-transformers が入っていません。pip install -r requirements.txt で入れてください")
        return
    word_runs = []
    for name in models:
        try:
            model = SentenceTransformer(name)
        except Exception as e:  # ダウンロードできない環境など
            print(f"\n（{name} を読み込めなかったので省略：{type(e).__name__}）")
            continue
        for label, prefix in prefixes_for(name):
            vec = model.encode([prefix + t for t in texts], normalize_embeddings=True)
            runs.append((f"{name}（{label}：先頭に「{prefix.strip() or 'なし'}」）", cos_matrix(vec), None))
            encoders.append((f"{name}（{label}）",
                             lambda xs, m=model, pf=prefix: m.encode([pf + x for x in xs], normalize_embeddings=True)))
            if words:
                wv = model.encode([prefix + x for x in words], normalize_embeddings=True)
                word_runs.append((f"{name}（{label}）", cos_matrix(wv)))

    for title, sim, _ in runs:
        show_matrix(title, ids, sim)
        show_nearest(ids, sim, posts)

    for title, enc in encoders:
        show_context(title, posts, enc)

    for title, sim in word_runs:
        print(f"\n■ 単語どうしの近さ：{title}")
        print("            " + "".join(f"{x[:5]:>8}" for x in words))
        for x, row in zip(words, sim):
            print(f"{x[:6]:>10}" + "".join(f"{v:>8.2f}" for v in row))


if __name__ == "__main__":
    main()
