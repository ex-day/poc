"""ex-day PoC（Issue #14）：投稿が順に届く条件で、AI に話題へ振り分けさせる

会話を CHUNK 件ずつに区切り、1回の呼び出しで1区切りだけを振り分ける。
呼び出しは毎回新しい実行（前の呼び出しの記憶はない）。前までの結果は「状態」として渡す。
  状態：話題の一覧（記号と名前）と、これまでの投稿（本文と、振り分けた話題）
  渡す文脈：
    full   … これまでの投稿を全部渡す
    narrow … 話題ごとに直近 NARROW 件だけ渡す（話題の名前と件数は全部渡す）
一度決めた振り分けは変えない（本番でも、決めた振り分けを毎回やり直すことはしない想定）。

振り分け直し（呼び方に r20 が付く条件）：
  投稿が 20 件たまった時点で、そこまでの投稿を一括で振り分け直し、状態を置き換える（前の振り分けは渡さない）。
  順に渡すだけだと話題が細かく増え続けるため、その手当てが効くかを見る。
    python seq_run.py reseg <条件> <k>        # k 番目の区切りまでの投稿を、一括で振り分け直す指示を作る
    python seq_run.py reseg-merge <条件> <k>  # 結果で状態を置き換える

手順（区切りごとに繰り返す）：
  python seq_run.py prepare <条件> <k>   # k 番目の区切りの指示ファイルを work/<条件>/ に作る
  # 指示ファイルごとに、新しい実行で AI に渡し、結果を <会話>_<k>.out.json に書かせる
  python seq_run.py merge <条件> <k>     # 結果を確かめて状態に足す。最後の区切りなら results/ に書く

条件：<提供元>-<モデル>_<呼び方>_<指示>_<返信先>（score.py と同じ形。呼び方に -seq10 / -seq10n を付ける）
  例：claude-sonnet_subagent-seq10_flow_reply（full）、claude-sonnet_subagent-seq10n_flow_noreply（narrow）、
      claude-sonnet_subagent-seq10r20_flow_reply（full ＋ 20件で振り分け直し）
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"
CHUNK = 10
NARROW = 3


def split_cond(cond):
    prov, runner, prompt, reply = cond.split("_")[:4]
    mode = "narrow" if runner.endswith("n") and "seq" in runner else "full"
    return prompt, reply, mode


def conversations(reply):
    only = set(filter(None, os.environ.get("EXDAY_CONVS", "").split(",")))  # 例：EXDAY_CONVS=tsurumi_38,kohoku_31,posts
    for f in sorted((HERE / "data" / reply).glob("*.json")):
        if only and f.stem not in only:
            continue
        yield f.stem, json.loads(f.read_text(encoding="utf-8"))["posts"]


def rules(prompt):
    name = {"flow": "split_flow.md", "object": "split.md"}[prompt]
    s = (HERE / "prompts" / name).read_text(encoding="utf-8")
    return s[s.index("## 話題とは"):s.index("## 入力")].strip()


def state_path(cond, conv):
    return WORK / cond / f"{conv}.state.json"


def load_state(cond, conv):
    p = state_path(cond, conv)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"topics": {}, "assign": {}, "calls": 0}


def prepare(cond, k):
    prompt, reply, mode = split_cond(cond)
    (WORK / cond).mkdir(parents=True, exist_ok=True)
    made = []
    for conv, posts in conversations(reply):
        new = posts[(k - 1) * CHUNK:k * CHUNK]
        if not new:
            continue
        st = load_state(cond, conv)
        if all(p["id"] in st["assign"] for p in new):
            continue  # もう振り分けてある（やり直しのとき）
        done = posts[:(k - 1) * CHUNK]
        assert set(st["assign"]) == {p["id"] for p in done}, f"{conv}：状態が区切り {k - 1} まで進んでいない"
        L = ["# 会話の投稿を話題に振り分ける（投稿が順に届く場合）\n"]
        L.append("ex-day は、場所について人が投稿し、返信し合うサービスです。1つの会話の中で、話はいくつもの「話題」に分かれていきます。")
        L.append("会話の投稿は少しずつ届きます。今回届いた投稿を、話題に振り分けてください。\n")
        L.append(rules(prompt) + "\n")
        L.append("## これまでの状態\n")
        if not done:
            L.append("まだ投稿はありません。今回が最初の投稿です。\n")
        else:
            L.append("これまでに決めた話題と、投稿の振り分けです。**これまでの投稿の振り分けは変えません。**\n")
            cnt = {t: sum(1 for v in st["assign"].values() if v == t) for t in st["topics"]}
            L.append("話題の一覧：\n")
            for t, n in st["topics"].items():
                L.append(f"- {t}：{n}（{cnt[t]}件）")
            L.append("")
            if mode == "full":
                shown = done
                L.append("これまでの投稿（`topic` は振り分けた話題。`OFF` は雑談）：\n")
            else:
                keep = set()
                for t in list(st["topics"]) + ["OFF"]:
                    ids = [p["id"] for p in done if st["assign"][p["id"]] == t]
                    keep |= set(ids[-NARROW:])
                shown = [p for p in done if p["id"] in keep]
                L.append(f"これまでの投稿のうち、話題ごとに直近{NARROW}件だけを示します（`topic` は振り分けた話題。`OFF` は雑談）：\n")
            L.append("```json")
            L.append(json.dumps([dict(p, topic=st["assign"][p["id"]]) for p in shown], ensure_ascii=False, indent=1))
            L.append("```\n")
        L.append("## 今回届いた投稿\n")
        L.append("```json")
        L.append(json.dumps(new, ensure_ascii=False, indent=1))
        L.append("```\n")
        L.append("## 出力\n")
        L.append("次の形の JSON だけを、指定されたファイルに書いてください。説明の文章は要りません。\n")
        L.append('```json\n{"topics": {"A": "話題の短い名前", "B": "..."}, "assign": {"P11": "A", "P12": "OFF"}}\n```\n')
        L.append("- `assign` には、今回届いた投稿だけを、全部入れます（雑談は `\"OFF\"`）。")
        L.append("- `topics` には、話題の一覧を全部入れます。これまでの話題の記号は変えません。名前は、分かりやすく直してかまいません。")
        L.append("- 今回の投稿が、これまでのどの話題にも入らないときは、新しい話題を足します。記号は、続きのアルファベットを使います。")
        p = WORK / cond / f"{conv}_{k}.prompt.md"
        p.write_text("\n".join(L) + "\n", encoding="utf-8")
        made.append((conv, str(p), str(WORK / cond / f"{conv}_{k}.out.json")))
    for m in made:
        print("\t".join(m))


def merge(cond, k):
    prompt, reply, mode = split_cond(cond)
    for conv, posts in conversations(reply):
        new = posts[(k - 1) * CHUNK:k * CHUNK]
        if not new:
            continue
        st = load_state(cond, conv)
        if all(p["id"] in st["assign"] for p in new):
            continue  # もう足してある
        out = json.loads((WORK / cond / f"{conv}_{k}.out.json").read_text(encoding="utf-8"))
        ids = [p["id"] for p in new]
        assert set(out["assign"]) == set(ids), f"{conv} {k}：assign が今回の投稿と一致しない {sorted(set(out['assign']) ^ set(ids))}"
        for t in st["topics"]:
            assert t in out["topics"], f"{conv} {k}：これまでの話題 {t} が消えた"
        for i in ids:
            assert out["assign"][i] == "OFF" or out["assign"][i] in out["topics"], f"{conv} {k}：{i} の話題 {out['assign'][i]} が一覧にない"
        st["topics"] = out["topics"]
        st["assign"].update({i: out["assign"][i] for i in ids})
        st["calls"] += 1
        state_path(cond, conv).write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        if len(st["assign"]) == len(posts):
            write_result(cond, conv, posts, st)


def reseg(cond, k):
    prompt, reply, mode = split_cond(cond)
    name = {"flow": "split_flow.md", "object": "split.md"}[prompt]
    base = (HERE / "prompts" / name).read_text(encoding="utf-8")
    for conv, posts in conversations(reply):
        if len(posts) < k * CHUNK:
            continue  # まだ k*CHUNK 件たまっていない会話は、振り分け直さない
        st = load_state(cond, conv)
        if st.get("reseg") == k:
            continue  # もう振り分け直してある（やり直しのとき）
        assert len(st["assign"]) == k * CHUNK, f"{conv}：状態が区切り {k} まで進んでいない"
        L = [base.strip(), "", "## 今回の入力", "", "```json",
             json.dumps({"conversation": conv, "posts": posts[:k * CHUNK]}, ensure_ascii=False, indent=1), "```"]
        p = WORK / cond / f"{conv}_reseg{k}.prompt.md"
        p.write_text("\n".join(L) + "\n", encoding="utf-8")
        print(conv, p, WORK / cond / f"{conv}_reseg{k}.out.json", sep="\t")


def reseg_merge(cond, k):
    prompt, reply, mode = split_cond(cond)
    for conv, posts in conversations(reply):
        if len(posts) < k * CHUNK:
            continue
        st = load_state(cond, conv)
        if st.get("reseg") == k:
            continue
        out = json.loads((WORK / cond / f"{conv}_reseg{k}.out.json").read_text(encoding="utf-8"))
        ids = [p["id"] for p in posts[:k * CHUNK]]
        assert set(out["assign"]) == set(ids), f"{conv}：assign が {k * CHUNK} 件目までの投稿と一致しない"
        for i in ids:
            assert out["assign"][i] == "OFF" or out["assign"][i] in out["topics"], f"{conv}：{i} の話題が一覧にない"
        st.update(topics=out["topics"], assign={i: out["assign"][i] for i in ids}, calls=st["calls"] + 1, reseg=k)
        state_path(cond, conv).write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        if len(st["assign"]) == len(posts):
            write_result(cond, conv, posts, st)


def write_result(cond, conv, posts, st):
    d = HERE / "results" / cond
    d.mkdir(parents=True, exist_ok=True)
    used = {t: n for t, n in st["topics"].items() if t in st["assign"].values()}
    (d / f"{conv}.json").write_text(json.dumps(
        {"conversation": conv, "calls": st["calls"], "topics": used, "assign": {p["id"]: st["assign"][p["id"]] for p in posts}},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"完了：{conv}（{st['calls']}回の呼び出し、話題 {len(used)}）")


if __name__ == "__main__":
    {"prepare": prepare, "merge": merge, "reseg": reseg, "reseg-merge": reseg_merge}[sys.argv[1]](sys.argv[2], int(sys.argv[3]))
