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

# 結果のフォルダ名：<提供元>-<モデル>_<呼び方>_<指示>_<返信先>[_<回>]
#   例：claude-sonnet_subagent_flow_reply、openai-gpt-5_api_flow_noreply_r2
#   提供元：claude／openai など。呼び方：subagent（Claude Code のサブエージェント）／api
#   指示：object（prompts/split.md）／flow（prompts/split_flow.md）。返信先：reply／noreply
PROMPTS = {"object": "対象", "flow": "流れ"}
REPLIES = {"reply": "返信先あり", "noreply": "返信先なし"}


def conditions():
    out = []
    for d in sorted((HERE / "results").iterdir()):
        if not d.is_dir():
            continue
        parts = d.name.split("_")
        if len(parts) not in (4, 5) or parts[2] not in PROMPTS or parts[3] not in REPLIES:
            print(f"読み飛ばし（フォルダ名の形が違う）：{d.name}")
            continue
        label = f"{parts[0]}・{parts[1]}・{PROMPTS[parts[2]]}・{REPLIES[parts[3]]}" + (f"・{parts[4]}" if len(parts) == 5 else "")
        out.append((d.name, label))
    return out


HUMAN = {"posts"}  # written_by がない会話のうち、人が書いたもの


def gold_convs():
    files = sorted((HERE.parent / "conversations").glob("*.json")) + [HERE.parent / "posts.json"]
    for f in files:
        c = json.loads(f.read_text(encoding="utf-8"))
        c["_name"] = f.stem
        c["_posts"] = [p for p in c["posts"] if not p.get("new_thread")]
        c["_human"] = c.get("written_by") == "human" or f.stem in HUMAN
        yield c


def merged_f(c, r, assign):
    """細分を許すまとまりの F：AI の話題を、多数決で対応づけた正解の話題ごとに束ねてから B-cubed を出す。
    1つの正解の話題を細かく分けただけなら満点に近く、別の話題を混ぜると下がる"""
    lab = {pid: r["label"][t] for pid, t in assign.items()}
    g = {p["id"]: (set(p["topics"]) or {"OFF"}) for p in c["_posts"]}
    ids = list(g)
    bp = br = 0.0
    for i in ids:
        sp = [j for j in ids if lab[j] == lab[i]]
        sg = [j for j in ids if g[i] & g[j]]
        both = sum(1 for j in sp if g[i] & g[j])
        bp += both / len(sp)
        br += both / len(sg)
    bp, br = bp / len(ids), br / len(ids)
    return 2 * bp * br / (bp + br) if bp + br else 0.0


def strict_f(c, assign):
    """厳しいまとまりの F：正解は先頭の話題だけ（「T3 でも可」の T3 は使わない）。
    「でも可」をどちらも当たりにすると、話題をまとめる側が有利になるため、比べるときに並べて見る"""
    g = {p["id"]: (p["topics"][0] if p["topics"] else "OFF") for p in c["_posts"]}
    ids = list(g)
    bp = br = 0.0
    for i in ids:
        sp = [j for j in ids if assign[j] == assign[i]]
        sg = [j for j in ids if g[j] == g[i]]
        both = sum(1 for j in sp if g[j] == g[i])
        bp += both / len(sp)
        br += both / len(sg)
    bp, br = bp / len(ids), br / len(ids)
    return 2 * bp * br / (bp + br) if bp + br else 0.0


def agg(rows):
    n = sum(r["n"] for _, r, _ in rows)
    w = lambda k: sum(r[k] * r["n"] for _, r, _ in rows) / n  # noqa: E731
    return n, w


def main():
    convs = list(gold_convs())
    L = ["# AI にまとめて振り分けさせた結果（Issue #8・#14）\n"]
    L.append("- 判定役：正解を見せないエージェント（Claude Code のサブエージェント、Codex）。条件の名前に、提供元・モデル・呼び方が入る")
    L.append("- 呼び方に -seq10 が付かないものは、会話の全投稿を一度に渡す（一括）。-seq10 は、10件ずつ順に渡し、前までの振り分けを変えずに引き継ぐ（`seq_run.py`）。返信先なしは、reply_to を全部消した入力")
    L.append("- 精度・まとまりの F・話題の数の意味は `../first_pass.py` の結果と同じ。比べるときは、まとまりの F を主に見る")
    L.append("- 条件の名前：提供元-モデル・呼び方（subagent／codex／api。順に渡す条件は -seq10 を付ける）・指示・返信先。指示は、対象＝`prompts/split.md`（同じ対象を話題にする）、流れ＝`prompts/split_flow.md`（会話の流れのひとまとまりを話題にする）")
    L.append("- 正解が複数ある投稿（「T3 でも可」など）は、どれに振り分けても当たり")
    L.append("- 適合が低い＝別々の話題を混ぜた（重い外れ。あとから分け直せない）。再現が低い＝1つの話題を細かく分けた（軽い外れ。束ねれば戻せる）")
    L.append("- 厳しい F：正解を先頭の話題だけにして数えたまとまりの F（「でも可」の話題は当たりにしない）。「でも可」をどちらも当たりにすると、話題をまとめる側が有利になるため、モデルを比べるときに並べて見る")
    L.append("- 細分を許す F：AI の話題を、多数決で対応づけた正解の話題ごとに束ねてから数えたもの。細かく分けただけなら下がらず、混ぜると下がる")
    L.append("- 人が書いた会話：" + "、".join(c["_name"] for c in convs if c["_human"]) + "。それ以外は Claude が書いた会話\n")
    L.append("## 1. まとめ\n")
    L.append("| 条件 | 会話 | 件数 | 精度 | まとまりの F | 適合（別の話題を混ぜない） | 再現（同じ話題を分けすぎない） | 細分を許す F | 厳しい F | 話題の数 | 正解の話題の数 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    per, detail, alt = [], [], []
    for key, name in conditions():
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
            r = evaluate(c, c["_posts"], assign, set())
            r["merged"] = merged_f(c, r, assign)
            r["strict"] = strict_f(c, assign)
            rows.append((c, r, j))
        for label, sel in (("人が書いた会話", lambda c: c["_human"]), ("Claude が書いた会話", lambda c: not c["_human"])):
            rs = [x for x in rows if sel(x[0])]
            if not rs:
                continue
            n, w = agg(rs)
            L.append(f"| **{name}** | {label}（{len(rs)}本） | {n} | {w('acc'):.2f} | {w('bcubed'):.2f} | {w('bp'):.2f} | {w('br'):.2f} | {w('merged'):.2f} | {w('strict'):.2f} | "
                     f"{sum(r['n_topics'] for _, r, _ in rs)} | {sum(len(c['topics']) for c, _, _ in rs)} |")
        for c, r, j in rows:
            per.append(f"| {name} | {c['_name']}{'（人）' if c['_human'] else ''} | {r['n']} | {r['acc']:.2f} | {r['bcubed']:.2f} | {r['bp']:.2f} | {r['br']:.2f} | {r['merged']:.2f} | {r['strict']:.2f} | {r['n_topics']} | {len(c['topics'])} |")
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
    L.append("| 条件 | 会話 | 件数 | 精度 | まとまりの F | 適合（別の話題を混ぜない） | 再現（同じ話題を分けすぎない） | 細分を許す F | 厳しい F | 話題の数 | 正解の話題の数 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    L += per
    L.append("\n## 3. 正解が複数ある投稿を、AI はどちらに入れたか\n")
    L += alt
    L.append("## 4. 外れた投稿\n")
    L += detail
    (HERE / "results" / "score.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[: L.index("\n## 3. 正解が複数ある投稿を、AI はどちらに入れたか\n")]))


if __name__ == "__main__":
    main()
