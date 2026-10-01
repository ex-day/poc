"""ex-day PoC（Issue #8）：1次解析の改良版（ベクトルで分岐と新しい話題を見分ける）

first_pass.py（v1）で分かったこと：
  - 返信の中で話題が分かれても、返信先の話題に入ってしまう（新しい名詞の数で拾うと AI に回る投稿が多すぎる）
  - 返信がない投稿は「保留」になり、返信が来ないと全部 AI に回る

v2 のルール：
  1. 返信がある：返信先の話題の文脈とのベクトルの近さが θ_branch 以上なら、返信先の話題に入れる。
     下回ったら「分岐の候補」として AI に回す（AI なしの場合は、返信がない投稿と同じ扱い）
  2. 返信がない：いちばん近い話題との近さが θ_new 以上で、2位との差が δ 以上ならその話題に入れる。
     θ_new 未満なら新しい話題を始める（仮）。差が δ 未満なら AI
     問いかけも同じ扱い（保留にしない）。名詞がなく短い投稿は話題外（雑談）
  3. 仮の話題に返信が来たり、近い投稿が続いたりしたら、そのまま話題になる。
     最後まで1件だけの仮の話題は「単発」として AI に回す

しきい値（θ_new・θ_branch）は、正解の振り分けで作った文脈との近さの分布から決める。
  - 「全会話」：全会話の分布から決めたもの（評価と同じデータで決めるので、高めに出る）
  - 「1本抜き」：その会話以外の会話の分布から決めたもの（評価する会話を見ずに決めた値。こちらを本命として見る）

python first_pass_v2.py --env "MacBook Pro（M4 Pro）"
EXDAY_MODEL=ngram-baseline python first_pass_v2.py
"""
import argparse
import json
import random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

from first_pass import HERE, MODEL, Embedder, Nouns, evaluate


def load(files):
    convs = []
    for f in files:
        c = json.loads(Path(f).read_text(encoding="utf-8"))
        c["_name"] = Path(f).stem
        c["_posts"] = [p for p in c["posts"] if not p.get("new_thread")]
        convs.append(c)
    return convs


def ctx(emb, texts):
    return emb("。".join(texts))


# ---------------------------------------------------------------- 分布を見る（正解の振り分けで文脈を作る）

def diagnose(c, emb):
    """投稿を届いた順に見て、それまでの投稿を正解どおりに話題へ入れた状態で、近さを測る。
    start：話題を始めた投稿の、既存の話題とのいちばん高い近さ
    cont ：続きの投稿の、自分の話題との近さ（own）と、ほかの話題とのいちばん高い近さ（other）
    reply：返信の投稿の、返信先の話題との近さと、分岐か（返信先と正解の話題が違う）"""
    G = defaultdict(list)
    start, cont, reply = [], [], []
    tp = {p["id"]: p["topics"] for p in c["_posts"]}
    for p in c["_posts"]:
        pid, ts = p["id"], p["topics"]
        v = emb(p["text"])
        sim = {t: float(v @ ctx(emb, G[t])) for t in G if G[t]}
        if ts:
            g = ts[0]
            if g not in G:
                if sim:
                    start.append((max(sim.values()), pid))
            else:
                other = max((s for t, s in sim.items() if t not in ts), default=None)
                cont.append((sim[g], other, pid))
        for r in p["reply_to"]:
            pts = tp.get(r) or []
            if pts and pts[0] in sim:
                reply.append((sim[pts[0]], not (set(ts) & set(pts)), pid))
        for t in ts:
            G[t].append(p["text"])
    return start, cont, reply


def best_threshold(pos, neg):
    """pos（しきい値以上であってほしい）と neg（未満であってほしい）を、いちばんよく分けるしきい値（両側の正解率の平均が最大）"""
    if not pos or not neg:
        return None, None
    cands = sorted(set(pos) | set(neg))
    best = (-1, None)
    for i in range(len(cands)):
        th = cands[i] if i == 0 else (cands[i - 1] + cands[i]) / 2
        ba = (np.mean([x >= th for x in pos]) + np.mean([x < th for x in neg])) / 2
        if ba > best[0]:
            best = (ba, th)
    return best[1], best[0]


def auc(pos, neg):
    if not pos or not neg:
        return None
    return float(np.mean([(p > n) + 0.5 * (p == n) for p in pos for n in neg]))


def thresholds(diags):
    start = [s for d in diags for s, _ in d[0]]
    own = [o for d in diags for o, _, _ in d[1]]
    rep_same = [s for d in diags for s, b, _ in d[2] if not b]
    rep_branch = [s for d in diags for s, b, _ in d[2] if b]
    th_new, _ = best_threshold(own, start)
    th_branch, _ = best_threshold(rep_same, rep_branch)
    return th_new, th_branch


# ---------------------------------------------------------------- v2 の1次解析

def run_v2(c, emb, nouns, th_new, th_branch, delta, drop, seed, ai_mode):
    rng = random.Random(seed)
    posts = c["_posts"]
    gold = {p["id"]: (p["topics"][0] if p["topics"] else "OFF") for p in posts}
    reply = {p["id"]: ([] if (p["reply_to"] and rng.random() < drop) else p["reply_to"]) for p in posts}
    text = {p["id"]: p["text"] for p in posts}
    topics, tgold = {}, defaultdict(Counter)
    tentative = set()
    assign, reason, ai = {}, {}, set()

    def ctxv(t):
        return ctx(emb, [text[i] for i in topics[t]])

    def sims(pid):
        v = emb(text[pid])
        return sorted(((float(v @ ctxv(t)), t) for t in topics if topics[t]), reverse=True)

    def new_topic():
        t = f"t{len(topics) + 1}"
        topics[t] = []
        return t

    def put(pid, tid, why):
        assign[pid], reason[pid] = tid, why
        if tid != "OFF":
            topics[tid].append(pid)
            tgold[tid][gold[pid]] += 1

    def unput(pid):
        t = assign.pop(pid)
        if t != "OFF":
            topics[t].remove(pid)
            tgold[t][gold[pid]] -= 1

    def oracle_topic(pid):
        g = gold[pid]
        if g == "OFF":
            return "OFF"
        for t in topics:
            if topics[t] and tgold[t].most_common(1)[0][0] == g and tgold[t][g] > 0:
                return t
        return new_topic()

    def ask_ai(pid, why, fallback):
        ai.add(pid)
        if ai_mode == "oracle":
            return put(pid, oracle_topic(pid), why)
        put(pid, fallback() if callable(fallback) else fallback, why)

    def by_vector(pid, why_prefix=""):
        """返信がない投稿（または分岐した投稿）を、ベクトルで振り分ける。（振り分け先, 理由, AI に回すか）"""
        ss = sims(pid)
        if not ss or ss[0][0] < th_new:
            t = new_topic()
            tentative.add(t)
            return t, why_prefix + "新しい話題（仮）", False
        gap = ss[0][0] - (ss[1][0] if len(ss) > 1 else -1)
        if gap >= delta:
            return ss[0][1], why_prefix + "ベクトル（{:.2f}）".format(ss[0][0]), False
        return ss[0][1], why_prefix + "迷い（ベクトルの差が小さい）", True

    for p in posts:
        pid = p["id"]
        ns = nouns(text[pid])
        is_ack = not ns and len(text[pid]) <= 20
        rs = [r for r in reply[pid] if r in assign]
        if rs:
            cand = [assign[r] for r in rs if assign[r] != "OFF"]
            if not cand:
                put(pid, "OFF", "返信先が話題外")
                continue
            tentative.discard(cand[0])
            if is_ack:
                put(pid, cand[0], "相づち→返信先")
                continue
            s = float(emb(text[pid]) @ ctxv(cand[0]))
            if s >= th_branch:
                put(pid, cand[0], "返信先（{:.2f}）".format(s))
                continue
            ask_ai(pid, "分岐の候補（返信先の話題と {:.2f}）".format(s), lambda: by_vector(pid)[0])
            continue
        if is_ack:
            put(pid, "OFF", "雑談（名詞がなく短い）")
            continue
        t, why, need_ai = by_vector(pid)
        if need_ai:
            ask_ai(pid, why, t)
        else:
            if t not in tentative or topics[t]:
                tentative.discard(t)  # 既存の仮の話題に近い投稿が続いた
            put(pid, t, why)
    for t in list(tentative):  # 最後まで1件だけの仮の話題
        if len(topics[t]) == 1:
            pid = topics[t][0]
            if pid in ai:
                continue
            why = reason[pid] + "→単発のまま終了"
            if ai_mode == "oracle":
                unput(pid)
                ask_ai(pid, why, None)
            else:
                ai.add(pid)
                reason[pid] = why
    return posts, assign, reason, ai, reply


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conversations", nargs="+", default=None)
    ap.add_argument("--delta", type=float, default=0.02)
    ap.add_argument("--drops", type=float, nargs="+", default=[0.0, 0.5, 1.0])
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--env", default="")
    args = ap.parse_args()
    files = args.conversations or sorted((HERE / "conversations").glob("*.json")) + [HERE / "posts.json"]
    convs = load(files)
    emb, nouns = Embedder(MODEL), Nouns()
    diags = {c["_name"]: diagnose(c, emb) for c in convs}

    L = [f"# 1次解析 v2：ベクトルで分岐と新しい話題を見分ける（{MODEL}）\n"]
    L.append(f"- 実行日時：{datetime.now().strftime('%Y-%m-%d %H:%M')}　環境：{args.env}（デバイス {emb.device}）")
    L.append("- 会話：" + ", ".join(f"{c['_name']}（{len(c['_posts'])}件）" for c in convs))
    L.append("- ルールは `first_pass_v2.py` の冒頭を参照。δ = {}。返信先を消す割合 0.5 は {} 回の平均\n".format(args.delta, args.seeds))

    # 1. 分布
    L.append("## 1. 近さの分布（それまでの投稿を正解どおりに振り分けた文脈と比べる）\n")
    L.append("| 会話 | 話題の始まり：既存の話題との近さ（中央値） | 続き：自分の話題との近さ（中央値） | 始まりと続きを分ける AUC | 続きが、ほかの話題より自分の話題に近い割合 | 返信：返信先の話題と同じ（中央値・件数） | 返信：分岐（中央値・件数） | 分岐を見分ける AUC |")
    L.append("|---|---|---|---|---|---|---|---|")

    def row(name, st, co, rp):
        s = [x for x, _ in st]
        o = [x for x, _, _ in co]
        win = [a > b for a, b, _ in co if b is not None]
        same = [x for x, b, _ in rp if not b]
        br = [x for x, b, _ in rp if b]
        f = lambda xs: f"{np.median(xs):.2f}" if xs else "－"  # noqa: E731
        a1, a2 = auc(o, s), auc(same, br)
        L.append(f"| {name} | {f(s)}（{len(s)}） | {f(o)}（{len(o)}） | {a1:.2f} | {np.mean(win):.2f} | {f(same)}（{len(same)}） | {f(br)}（{len(br)}） | "
                 f"{'－' if a2 is None else f'{a2:.2f}'} |" if a1 is not None else f"| {name} | － | － | － | － | － | － | － |")

    for c in convs:
        row(c["_name"], *diags[c["_name"]])
    row("全体", *[sum((d[i] for d in diags.values()), []) for i in range(3)])

    th_all = thresholds(list(diags.values()))
    th_loo = {c["_name"]: thresholds([d for n, d in diags.items() if n != c["_name"]]) for c in convs}
    L.append(f"\nしきい値（全会話から）：θ_new = {th_all[0]:.3f}、θ_branch = {th_all[1]:.3f}")
    L.append("しきい値（1本抜き）：" + "、".join(f"{n} θ_new {a:.3f}・θ_branch {b:.3f}" for n, (a, b) in th_loo.items()) + "\n")

    # 分岐の投稿の一覧（全体のしきい値で見落とすもの）
    L.append("### 返信の中の分岐（正解）と、返信先の話題との近さ\n")
    L.append("| 会話 | 投稿 | 本文 | 返信先の話題との近さ | θ_branch（全会話）で拾えるか |")
    L.append("|---|---|---|---|---|")
    text = {(c["_name"], p["id"]): p["text"] for c in convs for p in c["_posts"]}
    for n, d in diags.items():
        for s, b, pid in d[2]:
            if b:
                L.append(f"| {n} | {pid} | {text[(n, pid)][:24]} | {s:.2f} | {'○' if s < th_all[1] else '×'} |")

    # 2. v2 の結果
    L.append("\n## 2. v2 の振り分け\n")
    L.append("精度・まとまりの F の意味は first_pass.py の結果と同じ。v1 と比べるときは、まとまりの F を主に見る（v2 は話題を細かく分けやすく、多数決の精度は高めに出る）\n")
    L.append("| 会話 | しきい値 | 返信先を消す割合 | 精度（AI なし） | 精度（自動で振り分けた分） | 精度（AI が正しい場合） | AI に回す割合 | まとまりの F（AI なし） | まとまりの F（AI が正しい場合） | 話題の数（AI なし） | 正解の話題の数 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    stats, traces = {}, []
    for c in convs:
        for thname, (tn, tb) in (("1本抜き", th_loo[c["_name"]]), ("全会話", th_all)):
            for drop in args.drops:
                seeds = range(args.seeds) if 0 < drop < 1 else [0]
                res = {}
                for ai_mode in ("fallback", "oracle"):
                    rs = []
                    for s in seeds:
                        out = run_v2(c, emb, nouns, tn, tb, args.delta, drop, s, ai_mode)
                        rs.append(evaluate(c, out[0], out[1], out[3]))
                        if s == 0 and thname == "1本抜き" and drop in (0.0, 1.0):
                            traces.append((c, drop, ai_mode, *out, rs[-1]))
                    res[ai_mode] = rs
                m = lambda rs, k: float(np.mean([r[k] for r in rs if r[k] is not None])) if any(r[k] is not None for r in rs) else float("nan")  # noqa: E731
                stats[(c["_name"], thname, drop)] = (m(res["fallback"], "acc"), m(res["fallback"], "acc_auto"), m(res["oracle"], "acc"),
                                                     m(res["fallback"], "ai_rate"), len(c["_posts"]),
                                                     m(res["fallback"], "bcubed"), m(res["oracle"], "bcubed"), m(res["fallback"], "n_topics"), len(c["topics"]))
    for thname in ("1本抜き", "全会話"):
        for drop in args.drops:
            vs = [stats[(c["_name"], thname, drop)] for c in convs]
            n = sum(v[4] for v in vs)
            na = sum(v[4] * (1 - v[3]) for v in vs)
            auto = sum(v[1] * v[4] * (1 - v[3]) for v in vs if not np.isnan(v[1])) / na if na else float("nan")
            L.append(f"| **全体** | {thname} | {drop} | {sum(v[0] * v[4] for v in vs) / n:.2f} | {auto:.2f} | "
                     f"{sum(v[2] * v[4] for v in vs) / n:.2f} | {sum(v[3] * v[4] for v in vs) / n:.2f} | "
                     f"{sum(v[5] * v[4] for v in vs) / n:.2f} | {sum(v[6] * v[4] for v in vs) / n:.2f} | {sum(v[7] for v in vs):.1f} | {sum(v[8] for v in vs)} |")
    for c in convs:
        for drop in args.drops:
            v = stats[(c["_name"], "1本抜き", drop)]
            L.append(f"| {c['_name']} | 1本抜き | {drop} | {v[0]:.2f} | {v[1]:.2f} | {v[2]:.2f} | {v[3]:.2f} | {v[5]:.2f} | {v[6]:.2f} | {v[7]:.1f} | {v[8]} |")

    L.append("\n## 3. 投稿ごとの振り分け（1本抜きのしきい値、返信先を消す割合 0 と 1）\n")
    for c, drop, ai_mode, posts, assign, reason, ai, reply, r in traces:
        L.append(f"### {c['_name']}・返信先を消す割合 {drop}・{'AI なし' if ai_mode == 'fallback' else 'AI が正しい場合'}\n")
        L.append("| 投稿 | 返信先 | 本文 | 振り分け | 対応する正解 | 正解 | 当たり | 理由 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for p in posts:
            i = p["id"]
            L.append(f"| {i} | {','.join(reply[i]) or '－'} | {p['text'][:22]} | {assign[i]} | {r['label'][assign[i]]} | {','.join(p['topics']) or '話題外'} | "
                     f"{'○' if r['ok'][i] else '×'} | {reason[i]}{'（AI）' if i in ai else ''} |")
        L.append("")

    out = HERE / "results"
    out.mkdir(exist_ok=True)
    path = out / f"first_pass_v2_{MODEL.split('/')[-1]}.md"
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[: L.index("\n## 3. 投稿ごとの振り分け（1本抜きのしきい値、返信先を消す割合 0 と 1）\n")]))
    print(f"\n結果：{path}")


if __name__ == "__main__":
    main()
