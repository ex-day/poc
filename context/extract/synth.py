"""ex-day PoC（Issue #34）：主張をまとめて、わかってきたことと、まだわかっていないこと（問い）を書かせ、人の正解と比べる

入力：会話の投稿と、一括の条件で取り出した主張（results/claude-sonnet_subagent_whole_reply/<会話>.json）
正解：expected/findings.json（人が書いた。会話から読めることと土地勘を分けてある）
会話：正解のある2本（tsurumi_38・kohoku_31）

指示の版：
  v1 … prompts/synthesize.md（途中経過2）。結果は results/synth/
  v2 … prompts/synthesize_v2.md（途中経過2の人の確認で決めた、まとめ方の決まりを足した）。結果は results/synth_v2/

正解の版：
  正解は、途中経過2のあと、人の確認で足した（問いを一覧にし、29件にした）。
  results/synth/judge_<会話>.json と results/synth/score.md は、足す前の正解（commit ee1e2fa）で照らし合わせたもの。
  足したあとの正解での照らし合わせは judge-g2_<会話>.json（照らし合わせの指示は prompts/judge_synth_v2.md）。

手順（<版> は v1 か v2）：
  python synth.py prepare <版>        # work/synth_<版>/<会話>.prompt.md（まとめる指示）
  # 指示ファイルごとに、新しい実行で AI に渡す（正解は見せない）
  python synth.py merge <版>          # results/<版の場所>/<会話>.json
  python synth.py judge-prepare <版>  # work/synth_<版>/judge-g2_<会話>.prompt.md（照らし合わせの指示。正解を渡す）
  # 指示ファイルごとに、新しい実行で AI に渡す
  python synth.py judge-merge <版>    # results/<版の場所>/judge-g2_<会話>.json
  python synth.py score               # results/synth_v2/score.md（足したあとの正解で、版を並べる）
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run import posts_for_ai, prompt_file  # noqa: E402

CLAIMS = HERE / "results" / "claude-sonnet_subagent_whole_reply"
CONVS = ["tsurumi_38", "kohoku_31"]
TYPES = ("範囲", "確度", "理由", "条件", "現状")
VERSIONS = {"v1": ("synthesize.md", "synth"), "v2": ("synthesize_v2.md", "synth_v2"), "v3": ("synthesize_v3.md", "synth_v3"),
            "v3h": ("synthesize_v3.md", "synth_v3_haiku"),  # v3h：v3 の指示で、1段目・2段目とも Haiku（照らし合わせは Sonnet）
            "v3luna": ("synthesize_v3.md", "synth_v3_luna"),
            "v3diff": ("synthesize_diff.md", "synth_diff_haiku")}  # v3diff：日ごとに差分で回す（diff_run.py。#36）。照らし合わせだけ synth.py で行う  # v3luna：1段目・2段目とも GPT-5.6 Luna（Codex。run_codex_extract.sh）
CLAIMS_BY = {"v3h": HERE / "results" / "claude-haiku_subagent_whole_reply",
             "v3luna": HERE / "results" / "openai-gpt-5.6-luna_codex_whole_reply"}
SAFETY = ["chidorihama_15", "keyakidai_17"]  # 除外を確かめる会話（safety/。v3 から）
# 会話が属する Discovery の場所（v3 から渡す）。鶴見・港北は Maker が置いた（人に確かめ、反対がなかった）
PLACES = {"tsurumi_38": "鶴見", "kohoku_31": "港北IC付近の緑産業道路"}


def convs(v):
    return CONVS + (SAFETY if v.startswith("v3") else [])


def place(conv):
    if conv in PLACES:
        return PLACES[conv]
    return json.loads((HERE / "safety" / f"{conv}.json").read_text(encoding="utf-8"))["place"]
PTS = {"一致": 1.0, "一部": 0.5, "なし": 0.0}


def work(v):
    d = HERE / "work" / f"synth_{v}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def out(v):
    d = HERE / "results" / VERSIONS[v][1]
    d.mkdir(parents=True, exist_ok=True)
    return d


def expected(conv):
    d = json.loads((HERE / "expected" / "findings.json").read_text(encoding="utf-8"))
    return [f for f in d["findings"] if f["conversation"] == conv]


def prepare(v):
    rules = (HERE / "prompts" / VERSIONS[v][0]).read_text(encoding="utf-8")
    for conv in convs(v):
        claims = json.loads((CLAIMS_BY.get(v, CLAIMS) / f"{conv}.json").read_text(encoding="utf-8"))["facts"]
        payload = {"conversation": conv, **({"place": place(conv)} if v.startswith("v3") else {}), "posts": posts_for_ai(conv),
                   "claims": [{"id": c["id"], "content": c["content"], "kind": c["kind"], "src": c["src"]} for c in claims]}
        o = work(v) / f"{conv}.out.json"
        (work(v) / f"{conv}.prompt.md").write_text(prompt_file(rules, conv, "会話の投稿と主張", payload, o), encoding="utf-8")
        print(f"{work(v) / (conv + '.prompt.md')}\t{o}")


def merge(v):
    for conv in convs(v):
        r = json.loads((work(v) / f"{conv}.out.json").read_text(encoding="utf-8"))
        posts = {p["id"] for p in posts_for_ai(conv)}
        claims = {c["id"] for c in json.loads((CLAIMS_BY.get(v, CLAIMS) / f"{conv}.json").read_text(encoding="utf-8"))["facts"]}
        wids = {w["id"] for w in r["findings"]}
        for w in r["findings"]:
            if w["kind"] not in ("事実", "説", "価値") or not set(w["claims"]) <= claims or not set(w["posts"]) <= posts:
                raise SystemExit(f"{conv} {w['id']}：kind／claims／posts が違う")
        for q in r["questions"]:
            if q["type"] not in TYPES or q["about"] not in wids or not set(q.get("question_src", [])) <= posts:
                raise SystemExit(f"{conv} {q['id']}：type／about／question_src が違う")
        (out(v) / f"{conv}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        print(conv, len(r["findings"]), len(r["questions"]))


def judge_prepare(v):
    rules = (HERE / "prompts" / "judge_synth_v2.md").read_text(encoding="utf-8")
    for conv in CONVS:
        r = json.loads((out(v) / f"{conv}.json").read_text(encoding="utf-8"))
        exp = [{"id": f["id"], "from_conversation": f["from_conversation"], "local_knowledge": f.get("local_knowledge"),
                "priority": f["priority"], "kind": f.get("kind"),
                "questions": [{"question": q["question"], "optional": bool(q.get("optional"))} for q in f["questions"]]}
               for f in expected(conv)]
        payload = {"conversation": conv, "posts": posts_for_ai(conv), "expected": exp,
                   "ai": {"findings": [{k: w.get(k) for k in ("id", "content", "kind", "status")} for w in r["findings"]],
                          "questions": [{k: q[k] for k in ("id", "about", "question")} for q in r["questions"]]}}
        o = work(v) / f"judge-g2_{conv}.out.json"
        (work(v) / f"judge-g2_{conv}.prompt.md").write_text(prompt_file(rules, conv, "会話・正解・AI の出力", payload, o), encoding="utf-8")
        print(f"{work(v) / ('judge-g2_' + conv + '.prompt.md')}\t{o}")
    srules = (HERE / "prompts" / "judge_safety.md").read_text(encoding="utf-8")
    for conv in [c for c in convs(v) if c in SAFETY]:
        r = json.loads((out(v) / f"{conv}.json").read_text(encoding="utf-8"))
        o = work(v) / f"judge-safety_{conv}.out.json"
        (work(v) / f"judge-safety_{conv}.prompt.md").write_text(prompt_file(srules, conv, "会話・正解・AI の出力", safety_payload(conv, r), o), encoding="utf-8")
        print(f"{work(v) / ('judge-safety_' + conv + '.prompt.md')}\t{o}")


def safety_payload(conv, r):
    d = json.loads((HERE / "safety" / f"{conv}.json").read_text(encoding="utf-8"))
    return {"conversation": conv, "posts": posts_for_ai(conv), "expected": d["expected"],
            "ai": {"findings": [{k: w.get(k) for k in ("id", "content", "kind")} for w in r["findings"]],
                   "questions": [{k: q[k] for k in ("id", "about", "question")} for q in r["questions"]]}}


def judge_merge(v):
    for conv in CONVS:
        j = json.loads((work(v) / f"judge-g2_{conv}.out.json").read_text(encoding="utf-8"))
        exp = {f["id"]: f for f in expected(conv)}
        got = {e["id"]: e for e in j["expected"]}
        if set(exp) != set(got):
            raise SystemExit(f"{conv}：正解の項目が足りない／多い {set(exp) ^ set(got)}")
        for k, e in got.items():
            if sorted(q["q"] for q in e.get("questions", [])) != list(range(len(exp[k]["questions"]))):
                raise SystemExit(f"{conv} {k}：問いの照らし合わせが、正解の問いの数と合わない")
        (out(v) / f"judge-g2_{conv}.json").write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
        print(conv, "ok")
    for conv in [c for c in convs(v) if c in SAFETY]:
        j = json.loads((work(v) / f"judge-safety_{conv}.out.json").read_text(encoding="utf-8"))
        e = json.loads((HERE / "safety" / f"{conv}.json").read_text(encoding="utf-8"))["expected"]
        for k in ("must_not", "allowed"):
            if {x["id"] for x in e[k]} != {x["id"] for x in j[k]}:
                raise SystemExit(f"{conv}：{k} の項目が足りない／多い")
        (out(v) / f"judge-safety_{conv}.json").write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
        print(conv, "ok")


def stats(v, conv):
    exp = {f["id"]: f for f in expected(conv)}
    j = json.loads((out(v) / f"judge-g2_{conv}.json").read_text(encoding="utf-8"))
    r = json.loads((out(v) / f"{conv}.json").read_text(encoding="utf-8"))
    s = dict(want=0.0, nwant=0, excl=0, nexcl=0, q=0.0, nq=0, ef_ok=0, ef_ng=0, eq_ok=0, eq_ng=0,
             nw=len(r["findings"]), nqa=len(r["questions"]))
    for e in j["expected"]:
        f = exp[e["id"]]
        if f["priority"] == "want":
            s["want"] += PTS[e["match"]]
            s["nwant"] += 1
            for qm in e.get("questions", []):
                if not f["questions"][qm["q"]].get("optional"):
                    s["q"] += PTS[qm["match"]]
                    s["nq"] += 1
        if f["priority"] == "exclude":
            s["nexcl"] += 1
            s["excl"] += e["match"] != "なし"
    for x in j.get("extra_findings", []):
        s["ef_ok" if x["label"] == "妥当" else "ef_ng"] += 1
    for x in j.get("extra_questions", []):
        s["eq_ok" if x["label"] == "妥当" else "eq_ng"] += 1
    return s, j, r, exp


def score():
    vs = [v for v in VERSIONS if all((out(v) / f"judge-g2_{c}.json").exists() for c in CONVS)]
    lines = ["# わかってきたこと・問いの照らし合わせ：指示の版を並べる（Issue #34）", "",
             "正解は、途中経過2の人の確認で足したもの（29件）。まとめ・照らし合わせとも Claude Sonnet（サブエージェント）、各1回。人の確認の前。", "",
             "| 版 | 会話 | 拾いたい知見 | 知見にしないものが出た | 問い（出ても出なくてもよい問いは除く） | 正解にない知見（妥当／余計） | 正解にない問い（妥当／余計） | AI の知見・問いの数 |",
             "|---|---|---|---|---|---|---|---|"]
    detail = []
    for v in vs:
        for conv in CONVS:
            s, j, r, exp = stats(v, conv)
            lines.append(f"| {v} | {conv} | {s['want']:g} ／ {s['nwant']} | {s['excl']} ／ {s['nexcl']} | {s['q']:g} ／ {s['nq']} | "
                         f"{s['ef_ok']}／{s['ef_ng']} | {s['eq_ok']}／{s['eq_ng']} | {s['nw']}・{s['nqa']} |")
            aw = {w["id"]: w for w in r["findings"]}
            aq = {q["id"]: q for q in r["questions"]}
            detail += ["", f"## {v}・{conv}", "", "| 正解 | 優先 | 正解の知見（会話から） | 判定 | AI | 問い（正解 → 判定 → AI） | メモ |", "|---|---|---|---|---|---|---|"]
            for e in j["expected"]:
                f = exp[e["id"]]
                ai = " ／ ".join(f"{i}：{aw[i]['content']}" for i in e["by"] if i in aw)
                qs = "<br>".join(f"{f['questions'][qm['q']]['question']} → {qm['match']} → " +
                                 " ／ ".join(f"{i}：{aq[i]['question']}" for i in qm.get("by", []) if i in aq)
                                 for qm in e.get("questions", []))
                detail.append(f"| {e['id']} | {f['priority']} | {f['from_conversation']} | {e['match']} | {ai} | {qs} | {e.get('note', '')} |")
            ex = [(x, aw.get(x["id"], {}).get("content", "")) for x in j.get("extra_findings", [])] + \
                 [(x, aq.get(x["id"], {}).get("question", "")) for x in j.get("extra_questions", [])]
            if ex:
                detail += ["", "正解にない AI の出力：", ""] + [f"- {x['id']}（{x['label']}）：{t} {x.get('note', '')}" for x, t in ex]
    for v in [v for v in VERSIONS if all((out(v) / f"judge-safety_{c}.json").exists() for c in SAFETY)]:
        lines += ["", f"## 除外を確かめる会話（{v}）", "",
                  "| 会話 | 出してはいけないものが出た | 出てよいもの（一致＋一部×0.5 ／ 件数。optional は除く） | AI の知見・問いの数 |", "|---|---|---|---|"]
        for conv in SAFETY:
            j = json.loads((out(v) / f"judge-safety_{conv}.json").read_text(encoding="utf-8"))
            e = json.loads((HERE / "safety" / f"{conv}.json").read_text(encoding="utf-8"))["expected"]
            r = json.loads((out(v) / f"{conv}.json").read_text(encoding="utf-8"))
            cat = {x["id"]: x["category"] for x in e["must_not"]}
            opt = {x["id"] for x in e["allowed"] if x.get("optional")}
            leaked = [x for x in j["must_not"] if x["leaked"]]
            ok = [x for x in j["allowed"] if x["id"] not in opt]
            lines.append(f"| {conv} | {len(leaked)} ／ {len(j['must_not'])}" + "".join(f"<br>{x['id']}（{cat[x['id']]}）：{x.get('note', '')}" for x in leaked) +
                         f" | {sum(PTS[x['match']] for x in ok):g} ／ {len(ok)} | {len(r['findings'])}・{len(r['questions'])} |")
            aw = {w["id"]: w for w in r["findings"]}
            aq = {q["id"]: q for q in r["questions"]}
            detail += ["", f"## {v}・{conv}", ""] + [f"- {w['id']}（{w['kind']}）：{w['content']}" for w in r["findings"]] + \
                      [f"  - {q['id']}（{q['about']}・{q['type']}）：{q['question']}" for q in r["questions"]]
    md = "\n".join(lines + detail) + "\n"
    (out("v3") / "score.md").write_text(md, encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "score":
        score()
    else:
        {"prepare": prepare, "merge": merge, "judge-prepare": judge_prepare, "judge-merge": judge_merge}[cmd](sys.argv[2])
