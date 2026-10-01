"""ex-day PoC（Issue #8）：AI にまとめて振り分けさせた結果を、正解と比べる

入力：results/<条件>/<会話>.json（AI の振り分け。assign：投稿 id → 話題の記号 or "OFF"）
正解：../conversations/*.json と ../posts.json の topics
数え方は ../first_pass.py の evaluate と同じ（多数決の精度、まとまりの F（B-cubed）、話題の数）

python score.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from first_pass import evaluate  # noqa: E402

CONDS = [
    ("haiku_batch_reply", "Haiku・一括・返信先あり"),
    ("sonnet_batch_reply", "Sonnet・一括・返信先あり"),
    ("haiku_batch_noreply", "Haiku・一括・返信先なし"),
    ("sonnet_batch_noreply", "Sonnet・一括・返信先なし"),
]


def gold_convs():
    files = sorted((HERE.parent / "conversations").glob("*.json")) + [HERE.parent / "posts.json"]
    for f in files:
        c = json.loads(f.read_text(encoding="utf-8"))
        c["_name"] = f.stem
        c["_posts"] = [p for p in c["posts"] if not p.get("new_thread")]
        yield c


def main():
    convs = list(gold_convs())
    L = ["# AI にまとめて振り分けさせた結果（Issue #8）\n"]
    L.append("- 判定役：正解を見せないサブエージェント（Claude Code。Haiku・Sonnet）。指示は `prompts/split.md`")
    L.append("- 一括：会話の全投稿を一度に渡す。返信先なし：reply_to を全部消した入力")
    L.append("- 精度・まとまりの F・話題の数の意味は `../first_pass.py` の結果と同じ。比べるときは、まとまりの F を主に見る")
    L.append(f"- 正解の話題の数は全体で {sum(len(c['topics']) for c in convs)} 個（sakuragicho_21 の T5 は T2 の一部とみなしてよい）\n")
    L.append("| 条件 | 会話 | 精度 | まとまりの F | 適合（分けすぎない） | 再現（まとめ損ねない） | 話題の数 | 正解の話題の数 |")
    L.append("|---|---|---|---|---|---|---|---|")
    detail = []
    for key, name in CONDS:
        d = HERE / "results" / key
        if not d.exists():
            continue
        rows = []
        for c in convs:
            j = json.loads((d / f"{c['_name']}.json").read_text(encoding="utf-8"))
            assign = {p["id"]: j["assign"].get(p["id"], "MISSING") for p in c["_posts"]}
            missing = [i for i, t in assign.items() if t == "MISSING"]
            assert not missing, f"{key}/{c['_name']}：振り分けがない投稿 {missing}"
            r = evaluate(c, c["_posts"], assign, set())
            rows.append((c, r, j))
        n = sum(r["n"] for _, r, _ in rows)
        w = lambda k: sum(r[k] * r["n"] for _, r, _ in rows) / n  # noqa: E731
        L.append(f"| **{name}** | 全体 | {w('acc'):.2f} | {w('bcubed'):.2f} | {w('bp'):.2f} | {w('br'):.2f} | "
                 f"{sum(r['n_topics'] for _, r, _ in rows)} | {sum(len(c['topics']) for c, _, _ in rows)} |")
        for c, r, j in rows:
            L.append(f"| {name} | {c['_name']} | {r['acc']:.2f} | {r['bcubed']:.2f} | {r['bp']:.2f} | {r['br']:.2f} | {r['n_topics']} | {len(c['topics'])} |")
            wrong = [p for p in c["_posts"] if not r["ok"][p["id"]]]
            if wrong:
                detail.append(f"### {name}・{c['_name']}\n")
                detail.append("AI の話題：" + "、".join(f"{k} {v}" for k, v in j["topics"].items()) + "\n")
                detail.append("| 投稿 | 本文 | AI | 対応する正解 | 正解 |")
                detail.append("|---|---|---|---|---|")
                for p in wrong:
                    t = j["assign"][p["id"]]
                    detail.append(f"| {p['id']} | {p['text'][:24]} | {t} | {r['label'][t]} | {','.join(p['topics']) or '話題外'} |")
                detail.append("")
    L.append("\n## 外れた投稿\n")
    L += detail
    (HERE / "results" / "score.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[: L.index("\n## 外れた投稿\n")]))


if __name__ == "__main__":
    main()
