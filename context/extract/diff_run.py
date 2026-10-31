"""ex-day PoC（Issue #36）：日ごとに届く投稿を、差分で解析する（platform#127 の手順2 の標準の経路）

1段目：その日に届いた Post ごとに、文脈（返信の鎖・直前の数件・器の Discovery の場所）を付けて主張を取り出す
2段目：Discovery ごとに、保存した主張（除外済みは除く）と今のわかってきたこと・問いを渡し、変わった分だけ返させる

今の会話は投稿日がどれも1日目なので、DAY 件ずつを「1日」として届ける。
状態（主張・わかってきたこと・問い）は work/diff/<会話>.state.json に持つ（リポジトリには入れない）。

手順（日 k = 1, 2, … ごとに）：
  python diff_run.py prepare1 <k>   # 1段目の指示を work/diff/ に作る
  # 指示ごとに、新しい実行で AI に渡す
  python diff_run.py merge1 <k>     # 主張を状態に足す
  python diff_run.py prepare2 <k>   # 2段目の指示を作る
  # 指示ごとに、新しい実行で AI に渡す
  python diff_run.py merge2 <k>     # 変わった分を状態に当てる
最後に：
  python diff_run.py final          # results/synth_diff_haiku/<会話>.json（synth.py の照らし合わせと同じ形）
  python synth.py judge-prepare v3diff など（照らし合わせは synth.py）
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run import posts_for_ai, prompt_file  # noqa: E402
from synth import place  # noqa: E402

CONVS = ["tsurumi_38", "kohoku_31", "chidorihama_15", "keyakidai_17"]
DAY = 10        # 1日に届く件数
CHAIN = 8       # 返信の鎖をたどる深さの上限
PREV = 5        # 直前の何件を文脈に入れるか
WORK = HERE / "work" / "diff"
OUT = HERE / "results" / "synth_diff_haiku"
KINDS = ("事実", "説", "価値")  # 2段目のわかってきたことの種類
CLAIM_KINDS = ("事実", "体験", "評価", "疑問")  # 1段目の主張の種類（prompts/extract.md）
TYPES = ("範囲", "確度", "理由", "条件", "現状")


def days(conv):
    ps = posts_for_ai(conv)
    return [ps[i:i + DAY] for i in range(0, len(ps), DAY)]


def state_path(conv):
    return WORK / f"{conv}.state.json"


def load_state(conv):
    p = state_path(conv)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"claims": [], "findings": [], "questions": [], "nf": 0, "nw": 0, "nq": 0}


def save_state(conv, st):
    state_path(conv).write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def context_of(post, earlier):
    """返信の鎖（深さ CHAIN まで）＋直前の PREV 件（鎖と重ならないもの）。届いた順に並べる"""
    by = {p["id"]: p for p in earlier}
    chain, cur, seen = [], post, set()
    for _ in range(CHAIN):
        nxt = [r for r in cur.get("reply_to", []) if r in by and r not in seen]
        if not nxt:
            break
        cur = by[nxt[0]]
        seen.add(cur["id"])
        chain.append(cur["id"])
    prev = [p["id"] for p in earlier[-PREV:] if p["id"] not in seen]
    keep = set(chain) | set(prev)
    return [p for p in earlier if p["id"] in keep]


def prepare1(k):
    rules = (HERE / "prompts" / "extract_diff.md").read_text(encoding="utf-8")
    WORK.mkdir(parents=True, exist_ok=True)
    for conv in CONVS:
        ds = days(conv)
        if k > len(ds):
            continue
        earlier_all = [p for d in ds[:k - 1] for p in d]
        targets = []
        for i, p in enumerate(ds[k - 1]):
            earlier = earlier_all + ds[k - 1][:i]
            targets.append({"post": p, "context": context_of(p, earlier)})
        name = f"{conv}_d{k}_1"
        o = WORK / f"{name}.out.json"
        (WORK / f"{name}.prompt.md").write_text(
            prompt_file(rules, name, "対象の投稿と文脈", {"conversation": conv, "place": place(conv), "targets": targets}, o), encoding="utf-8")
        print(f"{WORK / (name + '.prompt.md')}\t{o}")


def merge1(k):
    for conv in CONVS:
        ds = days(conv)
        if k > len(ds):
            continue
        st = load_state(conv)
        r = json.loads((WORK / f"{conv}_d{k}_1.out.json").read_text(encoding="utf-8"))
        ok_ids = {p["id"] for d in ds[:k] for p in d}
        for f in r["facts"]:
            bad = [s for s in f["src"] if s not in ok_ids]
            if bad or f["kind"] not in CLAIM_KINDS:
                raise SystemExit(f"{conv} d{k}：{f.get('id')} の src／kind が違う {bad} {f['kind']}")
            st["nf"] += 1
            st["claims"].append({"id": f"F{st['nf']}", "content": f["content"], "kind": f["kind"], "src": f["src"], "day": k,
                                 "excluded": None})
        save_state(conv, st)
        print(conv, f"d{k}", len(r["facts"]), "claims")


def prepare2(k):
    rules = (HERE / "prompts" / "synthesize_diff.md").read_text(encoding="utf-8")
    for conv in CONVS:
        ds = days(conv)
        if k > len(ds):
            continue
        st = load_state(conv)
        claims = [{"id": c["id"], "content": c["content"], "kind": c["kind"], "src": c["src"], "new": c["day"] == k}
                  for c in st["claims"] if not c["excluded"]]
        findings = [{k2: w[k2] for k2 in ("id", "content", "kind", "status", "related_places", "aliases", "claims")}
                    for w in st["findings"] if not w.get("expired")]
        questions = [{k2: q[k2] for k2 in ("id", "about", "question", "type", "in_conversation", "question_src")}
                     for q in st["questions"] if not q.get("removed")]
        new_posts = [{k2: p[k2] for k2 in ("id", "author", "reply_to", "text")} for p in ds[k - 1]]
        name = f"{conv}_d{k}_2"
        o = WORK / f"{name}.out.json"
        payload = {"conversation": conv, "place": place(conv), "claims": claims, "findings": findings, "questions": questions, "new_posts": new_posts}
        (WORK / f"{name}.prompt.md").write_text(prompt_file(rules, name, "主張と、今のわかってきたこと・問い", payload, o), encoding="utf-8")
        print(f"{WORK / (name + '.prompt.md')}\t{o}")


def merge2(k):
    for conv in CONVS:
        ds = days(conv)
        if k > len(ds):
            continue
        st = load_state(conv)
        r = json.loads((WORK / f"{conv}_d{k}_2.out.json").read_text(encoding="utf-8"))
        cl = {c["id"]: c for c in st["claims"]}
        for x in r.get("exclude_claims", []):
            if x["id"] not in cl:
                raise SystemExit(f"{conv} d{k}：除外する主張 {x['id']} がない")
            cl[x["id"]]["excluded"] = {"day": k, "reason": x["reason"]}
        live = {c for c, v in cl.items() if not v["excluded"]}
        wf = {w["id"]: w for w in st["findings"]}
        fr = r.get("findings", {})
        nmap = {}

        def check(w):
            if w["kind"] not in KINDS:
                raise SystemExit(f"{conv} d{k}：kind が違う {w}")
            bad = set(w["claims"]) - live
            if bad:  # 除外済み・存在しない主張は入れない（DEC-0015 決定6）
                print(f"注意：{conv} d{k} {w['id']} に除外済み・存在しない主張 {sorted(bad)} → 外す")
                w["claims"] = [c for c in w["claims"] if c in live]

        for w in fr.get("add", []):
            st["nw"] += 1
            nid = f"W{st['nw']}"
            nmap[w["id"]] = nid
            w = dict(w, id=nid, day=k)
            check(w)
            st["findings"].append(w)
        for w in fr.get("update", []):
            if w["id"] not in wf:
                raise SystemExit(f"{conv} d{k}：更新する {w['id']} がない")
            check(w)
            wf[w["id"]].update({k2: w[k2] for k2 in ("content", "kind", "status", "related_places", "aliases", "claims") if k2 in w})
            wf[w["id"]]["updated"] = k
        for x in fr.get("expire", []):
            wf[x["id"]]["expired"] = {"day": k, "reason": x["reason"]}
        qr = r.get("questions", {})
        qf = {q["id"]: q for q in st["questions"]}
        for q in qr.get("add", []):
            st["nq"] += 1
            about = nmap.get(q["about"], q["about"])
            if q["type"] not in TYPES or about not in {w["id"] for w in st["findings"]}:
                raise SystemExit(f"{conv} d{k}：問いの type／about が違う {q}")
            st["questions"].append(dict(q, id=f"Q{st['nq']}", about=about, day=k))
        for q in qr.get("update", []):
            qf[q["id"]].update({k2: q[k2] for k2 in ("question", "type") if k2 in q})
        for x in qr.get("remove", []):
            qf[x["id"]]["removed"] = {"day": k, "reason": x["reason"]}
        save_state(conv, st)
        print(conv, f"d{k}", "add", len(fr.get("add", [])), "update", len(fr.get("update", [])), "expire", len(fr.get("expire", [])),
              "q+", len(qr.get("add", [])), "excl", len(r.get("exclude_claims", [])))


def final():
    OUT.mkdir(parents=True, exist_ok=True)
    for conv in CONVS:
        st = load_state(conv)
        cl = {c["id"]: c for c in st["claims"]}
        fs = []
        for w in st["findings"]:
            if w.get("expired"):
                continue
            posts = sorted({s for c in w["claims"] for s in cl[c]["src"]}, key=lambda x: int(x[1:]))
            fs.append({k2: w.get(k2) for k2 in ("id", "content", "kind", "status", "related_places", "aliases", "claims")} | {"posts": posts})
        ids = {w["id"] for w in fs}
        qs = [{k2: q.get(k2) for k2 in ("id", "about", "question", "type", "in_conversation", "question_src")}
              for q in st["questions"] if not q.get("removed") and q["about"] in ids]
        (OUT / f"{conv}.json").write_text(json.dumps({"conversation": conv, "findings": fs, "questions": qs}, ensure_ascii=False, indent=1), encoding="utf-8")
        (OUT / f"{conv}.state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        ex = [c for c in st["claims"] if c["excluded"]]
        print(conv, "claims", len(st["claims"]), "excluded", len(ex), "findings", len(fs), "questions", len(qs))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "final":
        final()
    else:
        {"prepare1": prepare1, "merge1": merge1, "prepare2": prepare2, "merge2": merge2}[cmd](int(sys.argv[2]))
