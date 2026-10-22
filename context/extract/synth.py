"""ex-day PoC（Issue #34）：主張をまとめて、わかってきたことと、まだわかっていないこと（問い）を書かせ、人の正解と比べる

入力：会話の投稿と、一括の条件で取り出した主張（results/claude-sonnet_subagent_whole_reply/<会話>.json）
正解：expected/findings.json（人が書いた。会話から読めることと土地勘を分けてある）
会話：正解のある2本（tsurumi_38・kohoku_31）

手順：
  python synth.py prepare        # work/synth/<会話>.prompt.md（まとめる指示）
  # 指示ファイルごとに、新しい実行で AI に渡す（正解は見せない）
  python synth.py merge          # results/synth/<会話>.json
  python synth.py judge-prepare  # work/synth/judge_<会話>.prompt.md（照らし合わせの指示。正解を渡す）
  # 指示ファイルごとに、新しい実行で AI に渡す
  python synth.py judge-merge    # results/synth/judge_<会話>.json
  python synth.py score          # results/synth/score.md
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run import posts_for_ai, prompt_file  # noqa: E402

CLAIMS = HERE / "results" / "claude-sonnet_subagent_whole_reply"
WORK = HERE / "work" / "synth"
OUT = HERE / "results" / "synth"
CONVS = ["tsurumi_38", "kohoku_31"]
TYPES = ("範囲", "確度", "理由", "条件", "現状")


def expected(conv):
    d = json.loads((HERE / "expected" / "findings.json").read_text(encoding="utf-8"))
    return [f for f in d["findings"] if f["conversation"] == conv]


def prepare():
    rules = (HERE / "prompts" / "synthesize.md").read_text(encoding="utf-8")
    WORK.mkdir(parents=True, exist_ok=True)
    for conv in CONVS:
        claims = json.loads((CLAIMS / f"{conv}.json").read_text(encoding="utf-8"))["facts"]
        payload = {"conversation": conv, "posts": posts_for_ai(conv),
                   "claims": [{"id": c["id"], "content": c["content"], "kind": c["kind"], "src": c["src"]} for c in claims]}
        out = WORK / f"{conv}.out.json"
        (WORK / f"{conv}.prompt.md").write_text(prompt_file(rules, conv, "会話の投稿と主張", payload, out), encoding="utf-8")
        print(f"{WORK / (conv + '.prompt.md')}\t{out}")


def merge():
    OUT.mkdir(parents=True, exist_ok=True)
    for conv in CONVS:
        r = json.loads((WORK / f"{conv}.out.json").read_text(encoding="utf-8"))
        posts = {p["id"] for p in posts_for_ai(conv)}
        claims = {c["id"] for c in json.loads((CLAIMS / f"{conv}.json").read_text(encoding="utf-8"))["facts"]}
        wids = {w["id"] for w in r["findings"]}
        for w in r["findings"]:
            if w["kind"] not in ("事実", "説") or not set(w["claims"]) <= claims or not set(w["posts"]) <= posts:
                raise SystemExit(f"{conv} {w['id']}：kind／claims／posts が違う")
        for q in r["questions"]:
            if q["type"] not in TYPES or q["about"] not in wids or not set(q.get("question_src", [])) <= posts:
                raise SystemExit(f"{conv} {q['id']}：type／about／question_src が違う")
        (OUT / f"{conv}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        print(conv, len(r["findings"]), len(r["questions"]))


def judge_prepare():
    rules = (HERE / "prompts" / "judge_synth.md").read_text(encoding="utf-8")
    for conv in CONVS:
        r = json.loads((OUT / f"{conv}.json").read_text(encoding="utf-8"))
        exp = [{k: f.get(k) for k in ("id", "from_conversation", "local_knowledge", "priority", "kind", "question")} for f in expected(conv)]
        payload = {"conversation": conv, "posts": posts_for_ai(conv), "expected": exp,
                   "ai": {"findings": [{k: w[k] for k in ("id", "content", "kind")} for w in r["findings"]],
                          "questions": [{k: q[k] for k in ("id", "about", "question")} for q in r["questions"]]}}
        out = WORK / f"judge_{conv}.out.json"
        (WORK / f"judge_{conv}.prompt.md").write_text(prompt_file(rules, conv, "会話・正解・AI の出力", payload, out), encoding="utf-8")
        print(f"{WORK / ('judge_' + conv + '.prompt.md')}\t{out}")


def judge_merge():
    for conv in CONVS:
        j = json.loads((WORK / f"judge_{conv}.out.json").read_text(encoding="utf-8"))
        want = {f["id"] for f in expected(conv)}
        got = {e["id"] for e in j["expected"]}
        if want != got:
            raise SystemExit(f"{conv}：正解の項目が足りない／多い {want ^ got}")
        (OUT / f"judge_{conv}.json").write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
        print(conv, "ok")


def score():
    pts = {"一致": 1.0, "一部": 0.5, "なし": 0.0}
    lines = ["# わかってきたこと・問いの照らし合わせ（Issue #34）", "",
             "主張（一括の条件）と会話を渡し、Claude Sonnet（サブエージェント）にまとめさせた。照らし合わせも Claude Sonnet（正解を渡す）。各1回。人の確認の前。", "",
             "| 会話 | 拾いたい知見（一致＋一部×0.5 ／ 件数） | 知見にしないものが出た | 未決のもの | 問い（一致＋一部×0.5 ／ 件数。出ても出なくてもよい問いは除く） | 正解にない知見（妥当／余計） | 正解にない問い（妥当／余計） | AI の知見・問いの数 |",
             "|---|---|---|---|---|---|---|---|"]
    detail = []
    for conv in CONVS:
        exp = {f["id"]: f for f in expected(conv)}
        j = json.loads((OUT / f"judge_{conv}.json").read_text(encoding="utf-8"))
        r = json.loads((OUT / f"{conv}.json").read_text(encoding="utf-8"))
        aw = {w["id"]: w for w in r["findings"]}
        aq = {q["id"]: q for q in r["questions"]}
        want = [e for e in j["expected"] if exp[e["id"]]["priority"] == "want"]
        wscore = sum(pts[e["match"]] for e in want)
        excl = [e for e in j["expected"] if exp[e["id"]]["priority"] == "exclude" and e["match"] != "なし"]
        und = [e for e in j["expected"] if exp[e["id"]]["priority"] == "undecided"]
        qs = [e for e in j["expected"] if exp[e["id"]].get("question") and exp[e["id"]]["priority"] == "want" and not exp[e["id"]].get("question_optional")]
        qscore = sum(pts[e["q_match"] or "なし"] for e in qs)
        ef = j.get("extra_findings", [])
        eq = j.get("extra_questions", [])
        lines.append(f"| {conv} | {wscore:g} ／ {len(want)} | {len(excl)} | {sum(e['match'] != 'なし' for e in und)}/{len(und)} 件が出た | "
                     f"{qscore:g} ／ {len(qs)} | {sum(x['label'] == '妥当' for x in ef)}／{sum(x['label'] == '余計' for x in ef)} | "
                     f"{sum(x['label'] == '妥当' for x in eq)}／{sum(x['label'] == '余計' for x in eq)} | {len(aw)}・{len(aq)} |")
        detail += ["", f"## {conv}", "", "| 正解 | 優先 | 正解の知見（会話から） | 判定 | AI | 正解の問い | 判定 | AI の問い | メモ |", "|---|---|---|---|---|---|---|---|---|"]
        for e in j["expected"]:
            f = exp[e["id"]]
            ai = " ／ ".join(f"{i}：{aw[i]['content']}" for i in e["by"] if i in aw)
            aiq = " ／ ".join(f"{i}：{aq[i]['question']}" for i in (e.get("q_by") or []) if i in aq)
            detail.append(f"| {e['id']} | {f['priority']} | {f['from_conversation']} | {e['match']} | {ai} | {f.get('question') or ''} | "
                          f"{e.get('q_match') or ''} | {aiq} | {e.get('note', '')} |")
        if ef or eq:
            detail += ["", "正解にない AI の出力：", ""]
            detail += [f"- {x['id']}（{x['label']}）：{aw[x['id']]['content'] if x['id'] in aw else ''} {x.get('note', '')}" for x in ef]
            detail += [f"- {x['id']}（{x['label']}）：{aq[x['id']]['question'] if x['id'] in aq else ''} {x.get('note', '')}" for x in eq]
    md = "\n".join(lines + detail) + "\n"
    (OUT / "score.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    {"prepare": prepare, "merge": merge, "judge-prepare": judge_prepare, "judge-merge": judge_merge, "score": score}[sys.argv[1]]()
