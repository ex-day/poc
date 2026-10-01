"""ex-day PoC（Issue #8・#14）：AI にまとめて振り分けさせた結果を、正解と比べる

入力：results/<条件>/<会話>.json（AI の振り分け。assign：投稿 id → 話題の記号 or "OFF"）
正解：../conversations/*.json と ../posts.json の topics
数え方は ../first_pass.py の evaluate と同じ（多数決の精度、まとまりの F（B-cubed）、話題の数）

結果がない会話は飛ばす（条件ごとに、どの会話を回したかが違ってよい）。
集計は「人が書いた会話」と「Claude が書いた会話」に分けて出す（written_by。posts.json は人が書いたもの）。

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
HUMAN = {"posts"}  # written_by がない会話のうち、人が書いたもの


def gold_convs():
    files = sorted((HERE.parent / "conversations").glob("*.json")) + [HERE.parent / "posts.json"]
    for f in files:
        c = json.loads(f.read_text(encoding="utf-8"))
        c["_name"] = f.stem
        c["_posts"] = [p for p in c["posts"] if not p.get("new_thread")]
        c["_human"] = c.get("written_by") == "human" or f.stem in HUMAN
        yield c


def agg(rows):
    n = sum(r["n"] for _, r, _ in rows)
    w = lambda k: sum(r[k] * r["n"] for _, r, _ in rows) / n  # noqa: E731
    return n, w


def main():
    convs = list(gold_convs())
    L = ["# AI にまとめて振り分けさせた結果（Issue #8・#14）\n"]
    L.append("- 判定役：正解を見せないサブエージェント（Claude Code。Haiku・Sonnet）。指示は `prompts/split.md`")
    L.append("- 一括：会話の全投稿を一度に渡す。返信先なし：reply_to を全部消した入力")
    L.append("- 精度・まとまりの F・話題の数の意味は `../first_pass.py` の結果と同じ。比べるときは、まとまりの F を主に見る")
    L.append("- 正解が複数ある投稿（「T3 でも可」など）は、どれに振り分けても当たり")
    L.append("- 人が書いた会話：" + "、".join(c["_name"] for c in convs if c["_human"]) + "。それ以外は Claude が書いた会話\n")
    L.append("## 1. まとめ\n")
    L.append("| 条件 | 会話 | 件数 | 精度 | まとまりの F | 適合（分けすぎない） | 再現（まとめ損ねない） | 話題の数 | 正解の話題の数 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    per, detail, alt = [], [], []
    for key, name in CONDS:
        d = HERE / "results" / key
        if not d.exists():
            continue
        rows = []
        for c in convs:
            f = d / f"{c['_name']}.json"
            if not f.exists():
                continue
            j = json.loads(f.read_text(encoding="utf-8"))
            assign = {p["id"]: j["assign"].get(p["id"], "MISSING") for p in c["_posts"]}
            missing = [i for i, t in assign.items() if t == "MISSING"]
            assert not missing, f"{key}/{c['_name']}：振り分けがない投稿 {missing}"
            rows.append((c, evaluate(c, c["_posts"], assign, set()), j))
        for label, sel in (("人が書いた会話", lambda c: c["_human"]), ("Claude が書いた会話", lambda c: not c["_human"])):
            rs = [x for x in rows if sel(x[0])]
            if not rs:
                continue
            n, w = agg(rs)
            L.append(f"| **{name}** | {label}（{len(rs)}本） | {n} | {w('acc'):.2f} | {w('bcubed'):.2f} | {w('bp'):.2f} | {w('br'):.2f} | "
                     f"{sum(r['n_topics'] for _, r, _ in rs)} | {sum(len(c['topics']) for c, _, _ in rs)} |")
        for c, r, j in rows:
            per.append(f"| {name} | {c['_name']}{'（人）' if c['_human'] else ''} | {r['n']} | {r['acc']:.2f} | {r['bcubed']:.2f} | {r['bp']:.2f} | {r['br']:.2f} | {r['n_topics']} | {len(c['topics'])} |")
            topics_txt = "AI の話題：" + "、".join(f"{k} {v}" for k, v in j["topics"].items()) + "\n"
            wrong = [p for p in c["_posts"] if not r["ok"][p["id"]]]
            if wrong:
                detail.append(f"### {name}・{c['_name']}\n")
                detail.append(topics_txt)
                detail.append("| 投稿 | 本文 | AI | 対応する正解 | 正解 |")
                detail.append("|---|---|---|---|---|")
                for p in wrong:
                    t = j["assign"][p["id"]]
                    detail.append(f"| {p['id']} | {p['text'][:24]} | {t} | {r['label'][t]} | {','.join(p['topics']) or '話題外'} |")
                detail.append("")
            multi = [p for p in c["_posts"] if len(p["topics"]) > 1]
            if multi:
                alt.append(f"### {name}・{c['_name']}\n")
                alt.append(topics_txt)
                alt.append("| 投稿 | 本文 | 正解 | AI | AI の話題が対応する正解 |")
                alt.append("|---|---|---|---|---|")
                for p in multi:
                    t = j["assign"][p["id"]]
                    alt.append(f"| {p['id']} | {p['text'][:24]} | {','.join(p['topics'])} | {t} {j['topics'].get(t, '')} | {r['label'][t]} |")
                alt.append("")
    L.append("\n## 2. 会話ごと\n")
    L.append("| 条件 | 会話 | 件数 | 精度 | まとまりの F | 適合（分けすぎない） | 再現（まとめ損ねない） | 話題の数 | 正解の話題の数 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    L += per
    L.append("\n## 3. 正解が複数ある投稿を、AI はどちらに入れたか\n")
    L += alt
    L.append("## 4. 外れた投稿\n")
    L += detail
    (HERE / "results" / "score.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[: L.index("\n## 3. 正解が複数ある投稿を、AI はどちらに入れたか\n")]))


if __name__ == "__main__":
    main()
