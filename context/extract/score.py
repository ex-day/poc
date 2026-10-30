"""ex-day PoC（Issue #34）：取り出した知見と採点を集計し、人が確かめるシートを作る

入力：results/<条件>/<会話>.json（知見）と results/<条件>/judge_<会話>.json（AI の採点）
出力：
  results/score.md   … 集計
  review/review.csv  … 人が確かめるシート（Excel で開ける UTF-8 BOM 付き）。人の欄は空。
                       人が欄を埋めたものを review/review_done.csv に置くと、AI の採点との一致も集計する

数えるもの（知見ごと。「支える投稿」は、AI の採点の support＋missing）：
  根拠の適合 … src のうち、支える投稿の割合（低いと、根拠の過剰）
  根拠の再現 … 支える投稿のうち、src に挙がった割合（低いと、根拠の漏れ）
  取りこぼし … 場所の情報があるのに、どの知見にも入らなかった投稿（AI の採点の uncovered）
  話題をまたぐ … 1つの知見を支える投稿が、正解の話題の1つに収まらない（「でも可」の話題も使ってよい）

python score.py
"""
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run import CONVS, gold, posts_for_ai  # noqa: E402

RESULTS = HERE / "results"
REVIEW = HERE / "review"
MODES = {"whole": "一括（話題に分けない）", "bytopic": "話題ごと（正解の話題で分けて渡す）"}


def conditions():
    ds = [d.name for d in RESULTS.iterdir() if d.is_dir() and d.name.split("_")[2] in MODES]
    return sorted(ds, key=lambda c: (list(MODES).index(c.split("_")[2]), c))  # 一括を先に


def load(cond, conv):
    r = json.loads((RESULTS / cond / f"{conv}.json").read_text(encoding="utf-8"))
    j = json.loads((RESULTS / cond / f"judge_{conv}.json").read_text(encoding="utf-8"))
    return r, j


def one_topic(ids, g):
    """ids の投稿が、正解の話題の1つに収まるか"""
    if not ids:
        return True
    common = set(g[ids[0]])
    for i in ids[1:]:
        common &= set(g[i])
    return bool(common)


def stats(cond, convs):
    s = dict(n=0, ok=0, part=0, ng=0, src=0, sup_in=0, sup_all=0, exact=0, uncovered=0, posts=0,
             uncited=0, cross_src=0, cross_true=0)
    for conv in convs:
        r, j = load(cond, conv)
        g = gold(conv)
        judged = {f["id"]: f for f in j["facts"]}
        cited = set()
        for f in r["facts"]:
            jf = judged[f["id"]]
            s["n"] += 1
            s[{"正しい": "ok", "一部": "part", "誤り": "ng"}[jf["verdict"]]] += 1
            s["src"] += len(f["src"])
            s["sup_in"] += len(jf["support"])
            s["sup_all"] += len(jf["support"]) + len(jf["missing"])
            s["exact"] += not jf["extra"] and not jf["missing"]
            s["cross_src"] += not one_topic(f["src"], g)
            s["cross_true"] += not one_topic(jf["support"] + jf["missing"], g)
            cited |= set(f["src"])
        s["uncovered"] += len(j["uncovered"])
        s["posts"] += len(g)
        s["uncited"] += len(set(g) - cited)
    return s


def pct(a, b):
    return f"{a / b:.2f}" if b else "－"


def table(rows):
    head = "| 条件 | 会話 | 知見 | 正しい／一部／誤り | 根拠の適合 | 根拠の再現 | 根拠がぴったり | 取りこぼし（投稿） | どの根拠にもならない投稿 | 話題をまたぐ（src ／ 支える投稿） |"
    out = [head, "|" + "---|" * 10]
    for label, conv, s in rows:
        out.append(f"| {label} | {conv} | {s['n']} | {s['ok']}／{s['part']}／{s['ng']} | {pct(s['sup_in'], s['src'])} | "
                   f"{pct(s['sup_in'], s['sup_all'])} | {s['exact']}/{s['n']} | {s['uncovered']} | {s['uncited']}/{s['posts']} | "
                   f"{s['cross_src']} ／ {s['cross_true']} |")
    return "\n".join(out)


def review_rows(cond):
    rows = []
    for conv in CONVS:
        r, j = load(cond, conv)
        text = {p["id"]: p["text"] for p in posts_for_ai(conv)}
        judged = {f["id"]: f for f in j["facts"]}
        for f in r["facts"]:
            jf = judged[f["id"]]
            rows.append({
                "条件": cond.split("_")[2], "会話": conv, "知見": f["id"], "種類": f["kind"], "主張": f["content"],
                "src": " ".join(f["src"]), "根拠の本文": " ／ ".join(f"{i}：{text[i]}" for i in f["src"]),
                "AI：判定": jf["verdict"], "AI：過剰": " ".join(jf["extra"]), "AI：漏れ": " ".join(jf["missing"]),
                "AI：メモ": jf.get("note", ""),
                "人：判定": "", "人：過剰": "", "人：漏れ": "", "人：メモ": ""})
        for u in j["uncovered"]:
            rows.append({"条件": cond.split("_")[2], "会話": conv, "知見": "取りこぼし", "種類": "", "主張": u["info"],
                         "src": u["post"], "根拠の本文": f"{u['post']}：{text.get(u['post'], '')}",
                         "AI：判定": "取りこぼし", "AI：過剰": "", "AI：漏れ": "", "AI：メモ": "",
                         "人：判定": "", "人：過剰": "", "人：漏れ": "", "人：メモ": ""})
    return rows


def write_review(conds):
    REVIEW.mkdir(exist_ok=True)
    rows = [row for c in conds for row in review_rows(c)]
    with (REVIEW / "review.csv").open("w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def human_agreement():
    """人が埋めたシート（review/review_done.csv）があれば、AI の採点との一致を数える"""
    f = REVIEW / "review_done.csv"
    if not f.exists():
        return "人の確認：まだ（`review/review_done.csv` がない）"
    rows = [r for r in csv.DictReader(f.open(encoding="utf-8-sig")) if r["人：判定"].strip()]
    facts = [r for r in rows if r["知見"] != "取りこぼし"]
    same = sum(r["人：判定"].strip() == r["AI：判定"] for r in facts)
    same_src = sum(set(r["人：過剰"].split()) == set(r["AI：過剰"].split()) and set(r["人：漏れ"].split()) == set(r["AI：漏れ"].split())
                   for r in facts)
    miss = [r for r in rows if r["知見"] == "取りこぼし"]
    agree_miss = sum(r["人：判定"].strip() == "取りこぼし" for r in miss)
    return (f"人の確認：知見 {len(facts)} 件。判定の一致 {same}/{len(facts)}、過剰・漏れの一致 {same_src}/{len(facts)}。"
            f"AI が挙げた取りこぼしのうち、人も取りこぼしとしたもの {agree_miss}/{len(miss)}")


def main():
    conds = conditions()
    rows = []
    for c in conds:
        label = MODES[c.split("_")[2]]
        for conv in CONVS:
            rows.append((label, conv, stats(c, [conv])))
        rows.append((label, "**3本の合計**", stats(c, CONVS)))
    n = write_review(conds)
    md = ["# 知見の抽出の集計（Issue #34）", "",
          "人が書いた3本、返信先あり。取り出し・採点とも Claude Sonnet（サブエージェント）、各1回。採点は AI によるもので、人の確認の前の数字。", "",
          table(rows), "",
          "- 根拠の適合：src のうち、主張を支える投稿の割合。根拠の再現：主張を支える投稿のうち、src に挙がった割合（支える投稿＝AI の採点の support＋missing）",
          "- 根拠がぴったり：過剰も漏れもない知見の数",
          "- 取りこぼし：場所の情報があるのに、どの知見にも入らなかった投稿（AI の採点）",
          "- どの根拠にもならない投稿：どの知見の src にも挙がらなかった投稿（相づち・雑談を含む）",
          "- 話題をまたぐ：1つの知見の根拠が、正解の話題の1つに収まらない数（「でも可」の話題も使う）。src で数えたものと、AI の採点で支えるとされた投稿で数えたもの",
          "", human_agreement(), "", f"人が確かめるシート：`review/review.csv`（{n} 行）"]
    (RESULTS / "score.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
