"""ex-day PoC（Issue #12）：AI の再判定と、人の判定の一致を数える

入力：
  - ../accuracy/results/review_ruri-v3-30m.csv（人の判定「判定（0〜3）」と、Claude の仮判定）
  - results/judge_*.jsonl（AI の判定。1行1組：pair_id, rel, reason_type, reason）
  - data/pairs.jsonl（組と、問い・候補の対応。build_pairs.py で作る）

出力：results/score.md

python score.py
"""
import csv
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REVIEW = HERE.parent / "accuracy" / "results" / "review_ruri-v3-30m.csv"
CONDITIONS = [  # (ファイル名, 表示名)
    ("judge_haiku_base.jsonl", "Haiku・基準なし"),
    ("judge_haiku_criteria.jsonl", "Haiku・基準あり"),
    ("judge_sonnet_base.jsonl", "Sonnet・基準なし"),
    ("judge_sonnet_criteria.jsonl", "Sonnet・基準あり"),
]


def three(v):
    return 0 if v == 0 else (2 if v >= 2 else 1)


def kappa(a, b):
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(a) | set(b)) / n ** 2
    return po, (po - pe) / (1 - pe) if pe < 1 else 0.0


def weighted_kappa(a, b, k=4):
    """重み付き κ（2乗の重み）。隣の段階のずれを軽く、離れたずれを重く数える"""
    n = len(a)
    w = [[(i - j) ** 2 / (k - 1) ** 2 for j in range(k)] for i in range(k)]
    obs = sum(w[x][y] for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    exp = sum(w[i][j] * ca[i] * cb[j] for i in range(k) for j in range(k)) / n ** 2
    return 1 - obs / exp if exp else 0.0


def opposite(a, b):
    return [i for i, (x, y) in enumerate(zip(a, b)) if {three(x), three(y)} == {0, 2}]


def main():
    rows = list(csv.DictReader(open(REVIEW, encoding="utf-8")))
    human = [int(r["判定（0〜3）"]) for r in rows]
    prov = [int(r["仮判定（Claude）"]) for r in rows]
    pairs = [json.loads(l) for l in (HERE / "data" / "pairs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [p["candidate_id"] for p in pairs] == [r["記事ID"] for r in rows], "組の並びが review と一致しない"

    L = ["# AI の再判定と人の判定の一致（Issue #12）\n"]
    L.append("- 対象：#9 で、紛れ込み1万件のときに上位5件に入った Wikipedia の記事 100件（問い × 記事）")
    L.append("- 人の判定：`../accuracy/results/review_ruri-v3-30m.csv` の「判定（0〜3）」（1人）")
    L.append("- AI の判定：人の判定を見せないサブエージェントに、問いと記事の題名・冒頭（最大300字）を渡して付けさせた。指示は `prompts/`（基準ありは `base.md` に `criteria.md` を足したもの）")
    L.append("- 比較用に、#9 の Claude の仮判定（題名だけから推測、記事の本文は見ていない）も並べる")
    L.append("- 3区分：0／1／2以上。正反対：一方が2以上、もう一方が0\n")

    conds = [("Claude 仮判定（題名のみ）", prov, None)]
    for fn, name in CONDITIONS:
        p = HERE / "results" / fn
        if not p.exists():
            continue
        js = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert [j["pair_id"] for j in js] == [x["pair_id"] for x in pairs], f"{fn} の並びが合わない"
        conds.append((name, [int(j["rel"]) for j in js], js))

    L.append("## 1. 人の判定との一致\n")
    L.append("| 判定 | 4段階 一致率 | κ | 重み付きκ | 3区分 一致率 | κ | 正反対 | AI が高め／低め（3区分） | 関連度の分布（0/1/2/3） |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    hc = Counter(human)
    L.append(f"| 人 | － | － | － | － | － | － | － | {hc[0]}/{hc[1]}/{hc[2]}/{hc[3]} |")
    for name, a, _ in conds:
        p4, k4 = kappa(a, human)
        p3, k3 = kappa([three(x) for x in a], [three(x) for x in human])
        wk = weighted_kappa(a, human)
        opp = opposite(a, human)
        hi = sum(1 for x, y in zip(a, human) if three(x) > three(y))
        lo = sum(1 for x, y in zip(a, human) if three(x) < three(y))
        c = Counter(a)
        L.append(f"| {name} | {p4:.2f} | {k4:.2f} | {wk:.2f} | {p3:.2f} | {k3:.2f} | {len(opp)} | {hi}／{lo} | {c[0]}/{c[1]}/{c[2]}/{c[3]} |")

    L.append("\n## 2. AI の判定の両端は、人の判定とどれだけ合うか\n")
    L.append("運用で「AI が2以上なら提案（または確定）、0 なら落とす、1 は人が確認」とした場合の目安。\n")
    L.append("| 判定 | AI が2以上 | うち人も2以上 | うち人が0 | AI が0 | うち人も0 | うち人が2以上 | AI が1（人の確認に回る） |")
    L.append("|---|---|---|---|---|---|---|---|")
    for name, a, _ in conds:
        pos = [y for x, y in zip(a, human) if x >= 2]
        neg = [y for x, y in zip(a, human) if x == 0]
        mid = sum(1 for x in a if x == 1)
        L.append(f"| {name} | {len(pos)} | {sum(1 for y in pos if y >= 2)} | {sum(1 for y in pos if y == 0)} | {len(neg)} | "
                 f"{sum(1 for y in neg if y == 0)} | {sum(1 for y in neg if y >= 2)} | {mid} |")

    L.append("\n## 3. 正反対になった組\n")
    L.append("| 判定 | 問い | 記事 | AI | 人 | AI の理由 |")
    L.append("|---|---|---|---|---|---|")
    for name, a, js in conds:
        for i in opposite(a, human):
            reason = js[i]["reason"] if js else "（題名のみ）"
            L.append(f"| {name} | {rows[i]['問い'][:24]} | {rows[i]['記事']} | {a[i]} | {human[i]} | {reason} |")

    L.append("\n## 4. ずれ（3区分）の内訳\n")
    L.append("人が 0 なのに AI が 1 以上（AI が甘い）、人が 2 以上なのに AI が 1 以下（AI が厳しい）の組。\n")
    for name, a, js in conds[1:]:
        loose = [i for i in range(len(a)) if human[i] == 0 and a[i] >= 1]
        strict = [i for i in range(len(a)) if human[i] >= 2 and a[i] <= 1]
        L.append(f"### {name}：甘い {len(loose)} 件・厳しい {len(strict)} 件\n")
        for label, idx in (("甘い", loose), ("厳しい", strict)):
            for i in idx:
                L.append(f"- {label}：{rows[i]['問い'][:24]} → {rows[i]['記事']}（AI {a[i]}・人 {human[i]}）{js[i]['reason']}")
        L.append("")

    L.append("## 5. AI が付けた理由の種類（関連度1以上のもの）\n")
    L.append("| 判定 | 同じ対象 | 同じ場所 | 同じ観点 | 似た話題 | なし |")
    L.append("|---|---|---|---|---|---|")
    for name, a, js in conds[1:]:
        c = Counter(j["reason_type"] for j in js if int(j["rel"]) >= 1)
        L.append(f"| {name} | {c['same_target']} | {c['same_place']} | {c['same_viewpoint']} | {c['similar_topic']} | {c['none']} |")

    (HERE / "results" / "score.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print((HERE / "results" / "score.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
