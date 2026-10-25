"""ex-day PoC（Issue #34）：知見の取り出し（1段目の主張＋2段目のまとめ v3）の費用を見積もる

AI は呼ばない。今回の会話4本の入出力の文字数から、会話の長さ N 件のときの1回あたりの量を直線で近似し、料金を掛ける。

前提（変えるときは下の定数を直す）：
- 1回の解析 = 1段目（会話全体 → 主張）＋2段目（会話全体＋主張 → わかってきたこと・問い）。#14 の方針どおり会話全体を送る
- 入出力の JSON は、空白を詰めた形で数える（今回の指示ファイルは見やすさのために字下げしている）
- 1文字あたりのトークン数は TPC（仮置き）。日本語のトークン数は測っていない（トークンを数える API には契約が要る）
- 料金は 2026-10-25 に platform.claude.com/docs/en/about-claude/pricing で確かめたもの（USD／100万トークン）
- 推論（extended thinking）は使わない前提。使うと出力が増える

python cost.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run import posts_for_ai  # noqa: E402

TPC = 1.3
PRICE = {"Sonnet 5.5": (2.0, 10.0), "Haiku 5.5": (0.10, 0.50)}  # 入力, 出力（Haiku は 10万トークン以下の料金）
CONVS = ["tsurumi_38", "kohoku_31", "chidorihama_15", "keyakidai_17"]
# 規模の想定（人の想定、#34）：(名前, 1日の投稿, 投稿のある Discovery の数, 解析するときの会話の長さ N)
SCENARIOS = [("当初", 10, 10, 30), ("回り出した", 1000, 150, 100), ("回り出した・会話が長い", 1000, 150, 200), ("伸びた", 10000, 1500, 100)]


def compact(o):
    return len(json.dumps(o, ensure_ascii=False, separators=(",", ":")))


def measure():
    r1 = len((HERE / "prompts" / "extract.md").read_text(encoding="utf-8"))
    r2 = len((HERE / "prompts" / "synthesize_v3.md").read_text(encoding="utf-8"))
    rows = []
    for c in CONVS:
        ps = posts_for_ai(c)
        claims = json.loads((HERE / "results" / "claude-sonnet_subagent_whole_reply" / f"{c}.json").read_text(encoding="utf-8"))["facts"]
        syn = json.loads((HERE / "results" / "synth_v3" / f"{c}.json").read_text(encoding="utf-8"))
        inp = r1 + compact({"posts": ps}) + r2 + compact({"posts": ps, "claims": [{k: f[k] for k in ("id", "content", "kind", "src")} for f in claims]})
        out = compact({"facts": [{k: f[k] for k in ("id", "content", "kind", "place", "when", "src")} for f in claims]}) + compact(syn)
        rows.append((len(ps), inp, out))
    return rows


def fit(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return my - b * mx, b


def main():
    rows = measure()
    ai, bi = fit([r[0] for r in rows], [r[1] for r in rows])
    ao, bo = fit([r[0] for r in rows], [r[2] for r in rows])
    out = ["# 費用の見積もり（Issue #34）", "",
           f"会話4本の実測（文字数）：" + "、".join(f"{n}件 入力{i:,}・出力{o:,}" for n, i, o in rows), "",
           f"近似：入力 ≒ {ai:,.0f} ＋ {bi:,.0f}×N 文字、出力 ≒ {ao:,.0f} ＋ {bo:,.0f}×N 文字（N は会話の投稿数）。1文字 ≒ {TPC} トークンと仮置き", "",
           "| 会話の長さ N | 入力トークン | 出力トークン | " + " | ".join(f"{m}（1回）" for m in PRICE) + " |", "|---|---|---|" + "---|" * len(PRICE)]
    for N in (30, 100, 200):
        i, o = (ai + bi * N) * TPC, (ao + bo * N) * TPC
        out.append(f"| {N} | {i:,.0f} | {o:,.0f} | " + " | ".join(f"${(i * a + o * b) / 1e6:.3f}" for a, b in PRICE.values()) + " |")
    out += ["", "| 規模 | 1日の投稿 | 解析のしかた | " + " | ".join(f"{m}（月）" for m in PRICE) + " | " + " | ".join(f"{m}・バッチ（月）" for m in PRICE) + " |",
            "|---|---|---|" + "---|" * (2 * len(PRICE))]
    for name, posts, disc, N in SCENARIOS:
        i, o = (ai + bi * N) * TPC, (ao + bo * N) * TPC
        for how, calls in (("投稿ごと", posts), ("1日1回（Discovery ごと）", disc)):
            m = [calls * (i * a + o * b) / 1e6 * 30 for a, b in PRICE.values()]
            out.append(f"| {name}（N={N}） | {posts:,} | {how} | " + " | ".join(f"${x:,.0f}" for x in m) + " | " + " | ".join(f"${x / 2:,.0f}" for x in m) + " |")
    md = "\n".join(out) + "\n"
    (HERE / "results" / "cost.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
