"""ex-day PoC（Issue #34）：会話から知見を、根拠の投稿 id 付きで取り出し、確かめる

条件（results/ のフォルダ名。<提供元>-<モデル>_<呼び方>_<渡し方>_<返信先>）：
  claude-sonnet_subagent_whole_reply    … 会話全体を1回で渡す（話題に分けない）
  claude-sonnet_subagent_bytopic_reply  … 正解の話題（先頭の話題）ごとに分けて、話題ごとに1回ずつ渡す
                                         （話題分割が理想どおりにできた場合）

手順：
  python run.py prepare <条件>        # work/<条件>/ に、取り出しの指示ファイルを作る（入力の会話を埋め込む）
  # 指示ファイルごとに、新しい実行（正解を見せないサブエージェント）で AI に渡し、指示に書いたファイルに結果を書かせる
  python run.py merge <条件>          # 結果を確かめ、results/<条件>/<会話>.json にまとめる（bytopic は話題ごとの結果をつなぐ）
  python run.py judge-prepare <条件>  # work/<条件>/judge_<会話>.prompt.md を作る（採点の指示。条件名は入れない）
  # 採点の指示ファイルごとに、新しい実行で AI に渡す
  python run.py judge-merge <条件>    # 採点を確かめ、results/<条件>/judge_<会話>.json に置く
  python score.py                     # 集計と、人が確かめるシート

正解（話題）は ../conversations/*.json と ../posts.json の topics。取り出し・採点のどちらの役にも見せない。
"""
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CTX = HERE.parent
WORK = HERE / "work"
RESULTS = HERE / "results"
CONVS = [c for c in os.environ.get("EXDAY_CONVS", "tsurumi_38,kohoku_31,posts").split(",") if c]  # 既定は人（利明）が書いた3本


def load(conv):
    f = CTX / ("posts.json" if conv == "posts" else f"conversations/{conv}.json")
    if not f.exists():  # 除外を確かめる会話（safety/）
        f = HERE / "safety" / f"{conv}.json"
    return json.loads(f.read_text(encoding="utf-8"))


def posts_for_ai(conv):
    """AI に渡す投稿（正解の topics とメモ note を除く）"""
    out = []
    for p in load(conv)["posts"]:
        if p.get("new_thread"):
            continue
        r = p.get("reply_to")
        r = r if isinstance(r, list) else ([r] if r else [])
        out.append({"id": p["id"], "day": p.get("day", 1), "author": p["author"], "reply_to": r, "text": p["text"]})
    return out


def gold(conv):
    """投稿 id → 正解の話題（先頭が中心の話題。2つ目以降は「でも可」）"""
    return {p["id"]: p["topics"] for p in load(conv)["posts"] if not p.get("new_thread")}


def mode_of(cond):
    return cond.split("_")[2]


def units(cond, conv):
    """1回の呼び出しで渡す単位：[(名前, 投稿の一覧)]"""
    ps = posts_for_ai(conv)
    if mode_of(cond) == "whole":
        return [(conv, ps)]
    g = gold(conv)
    order = []
    for p in ps:
        t = g[p["id"]][0]
        if t not in order:
            order.append(t)
    return [(f"{conv}_{t}", [p for p in ps if g[p["id"]][0] == t]) for t in order]


def prompt_file(rules, name, payload_title, payload, out):
    return (rules.rstrip() + "\n\n---\n\n"
            f"## {payload_title}\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=1)}\n```\n\n"
            f"結果の JSON は `{out}` に書いてください。ほかのファイルやフォルダは読まないでください。\n")


def prepare(cond):
    rules = (HERE / "prompts" / "extract.md").read_text(encoding="utf-8")
    d = WORK / cond
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    for conv in CONVS:
        for name, ps in units(cond, conv):
            out = d / f"{name}.out.json"
            (d / f"{name}.prompt.md").write_text(
                prompt_file(rules, name, "会話の投稿", {"conversation": name, "posts": ps}, out), encoding="utf-8")
            print(f"{d / (name + '.prompt.md')}\t{out}")


def check_facts(facts, ids, where):
    for f in facts:
        for k in ("content", "kind", "src"):
            if k not in f:
                raise SystemExit(f"{where}：{f.get('id')} に {k} がない")
        bad = [s for s in f["src"] if s not in ids]
        if bad:
            raise SystemExit(f"{where}：{f['id']} の src に、渡していない投稿がある {bad}")
        if f["kind"] not in ("事実", "体験", "評価", "疑問"):
            raise SystemExit(f"{where}：{f['id']} の kind が違う {f['kind']}")


def merge(cond):
    d = WORK / cond
    (RESULTS / cond).mkdir(parents=True, exist_ok=True)
    for conv in CONVS:
        facts = []
        for name, ps in units(cond, conv):
            r = json.loads((d / f"{name}.out.json").read_text(encoding="utf-8"))
            check_facts(r["facts"], {p["id"] for p in ps}, name)
            for f in r["facts"]:
                f = dict(f, unit=name)
                facts.append(f)
        for i, f in enumerate(facts, 1):  # bytopic は話題ごとに F1 から振られるので、つなげて振り直す
            f["id"] = f"F{i}"
        (RESULTS / cond / f"{conv}.json").write_text(
            json.dumps({"conversation": conv, "condition": cond, "facts": facts}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(conv, len(facts))


def judge_prepare(cond):
    rules = (HERE / "prompts" / "judge.md").read_text(encoding="utf-8")
    d = WORK / cond
    d.mkdir(parents=True, exist_ok=True)
    for conv in CONVS:
        r = json.loads((RESULTS / cond / f"{conv}.json").read_text(encoding="utf-8"))
        payload = {"conversation": conv, "posts": posts_for_ai(conv),
                   "facts": [{"id": f["id"], "content": f["content"], "src": f["src"]} for f in r["facts"]]}
        out = d / f"judge_{conv}.out.json"
        (d / f"judge_{conv}.prompt.md").write_text(prompt_file(rules, conv, "会話の投稿と知見", payload, out), encoding="utf-8")
        print(f"{d / ('judge_' + conv + '.prompt.md')}\t{out}")


def judge_merge(cond):
    d = WORK / cond
    for conv in CONVS:
        r = json.loads((RESULTS / cond / f"{conv}.json").read_text(encoding="utf-8"))
        j = json.loads((d / f"judge_{conv}.out.json").read_text(encoding="utf-8"))
        want = {f["id"]: f for f in r["facts"]}
        got = {f["id"]: f for f in j["facts"]}
        if set(want) != set(got):
            raise SystemExit(f"{conv}：採点の知見が足りない／多い {set(want) ^ set(got)}")
        ids = {p["id"] for p in posts_for_ai(conv)}
        for k, f in got.items():
            if f["verdict"] not in ("正しい", "一部", "誤り"):
                raise SystemExit(f"{conv}：{k} の verdict が違う {f['verdict']}")
            src = set(want[k]["src"])
            if not set(f["support"]) | set(f["extra"]) <= src or set(f["missing"]) & src or not set(f["missing"]) <= ids:
                raise SystemExit(f"{conv}：{k} の support／extra／missing が src と合わない")
            if set(f["support"]) | set(f["extra"]) != src:
                print(f"注意：{conv} {k} の support＋extra が src と一致しない（足りない分は support とみなす）")
                f["support"] = sorted(src - set(f["extra"]), key=lambda s: int(s[1:]))
        (RESULTS / cond / f"judge_{conv}.json").write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
        print(conv, "ok")


if __name__ == "__main__":
    cmd, cond = sys.argv[1], sys.argv[2]
    {"prepare": prepare, "merge": merge, "judge-prepare": judge_prepare, "judge-merge": judge_merge}[cmd](cond)
