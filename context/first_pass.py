"""ex-day PoC（Issue #8）：AI を使わない1次解析で、会話の投稿を話題に振り分ける

会話の投稿を、届いた順に1件ずつ振り分ける（AI は使わない。AI に回すべき投稿を数える）。

ルール（#2 の「文脈抽出の最小の形」を実装したもの）：
  返信先がある投稿
    - 返信先が「保留」なら、保留の投稿とこの投稿をまとめて判断する（新しい話題にする。AI に回す1回として数える）
    - 相づち（名詞がなく短い）なら、返信先の話題に入れる
    - それ以外は、返信先の話題に入れる。ただし、ベクトルで別の話題のほうが明らかに近い（差が δ 以上）なら「迷い」として AI に回す
  返信先がない投稿
    - 最初の投稿なら、新しい話題にする
    - 問いかけ（？・疑問詞・「〜っけ」など）なら「保留」にする（答えが来るまで何の話か分からないため）
    - 既存の話題と名詞が重ならないなら「保留」にする（新しい話題かもしれないため）
    - 名詞が重なり、ベクトルの1位と2位の差が δ 以上なら、1位の話題に入れる
    - それ以外は「迷い」として AI に回す
  最後まで返信が来なかった「保留」は、AI に回す（数える）

AI に回した投稿は、仮に次のように振り分けて精度を出す（下限）。あわせて「AI が正しく振り分ける」と仮定した精度（上限）も出す。
  迷い：ベクトルの1位の話題／保留のまま終わったもの：名詞が重なる話題があればベクトルの1位、なければ話題外

返信先の付き方を変えて測る（実際の利用者は返信先を付けないことが多いため）：
  返信先を消す割合 0（シナリオのまま）／0.5（ランダムに半分。--seeds 回の平均）／1（すべて消す）

文脈（話題）のベクトルの作り方を比べる：
  concat：話題の投稿の文章をつなげて1回ベクトルにする
  mean  ：投稿ごとのベクトルを平均する（DB の中で計算でき、モデルの呼び出しは投稿1つにつき1回で済む）

使い方：
  python first_pass.py                                     # 既定：Ruri v3 30m、conversations/ の会話すべて
  EXDAY_MODEL=ngram-baseline python first_pass.py          # 比較用の基準（文字の重なりだけ）
  python first_pass.py --delta 0.01 0.02 0.03 --seeds 20
"""
import argparse
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent

MODEL = os.environ.get("EXDAY_MODEL", "cl-nagoya/ruri-v3-30m")
QUESTION = re.compile(r"[？?]|っけ|どこ|いつ|なぜ|なんで|どう|どんな|何|ありますか|ですか")


# ---------------------------------------------------------------- 文章の扱い

def _ngram_vector(text, dim=384):
    import hashlib
    v = np.zeros(dim, dtype=np.float32)
    t = text.replace(" ", "")
    for g in [t[i : i + 2] for i in range(len(t) - 1)] or [t]:
        v[int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16) % dim] += 1.0
    n = np.linalg.norm(v)
    return v / n if n else v


class Embedder:
    """話題の判定用に、Ruri v3 は「トピック: 」を付けて埋め込む（PR #3 と同じ）"""

    def __init__(self, name):
        self.name = name
        if name == "ngram-baseline":  # 比較用の基準（vector/common.py と同じ。DB のライブラリを読み込まないよう、ここに持つ）
            self.f = lambda ts: np.array([_ngram_vector(t) for t in ts])
            self.device = "cpu"
        else:
            from sentence_transformers import SentenceTransformer
            m = SentenceTransformer(name)
            prefix = "トピック: " if "ruri-v3" in name else ("query: " if "e5" in name else "")
            self.f = lambda ts: m.encode([prefix + t for t in ts], normalize_embeddings=True)
            self.device = str(m.device)
        self.cache = {}

    def __call__(self, text):
        if text not in self.cache:
            self.cache[text] = np.asarray(self.f([text])[0], dtype=np.float32)
        return self.cache[text]


class Nouns:
    """名詞を取り出す。連続する名詞・接頭辞・接尾辞はつなげて1語にする（例：汽車＋道 → 汽車道）"""
    # 話題の手がかりにならない語（形式的な名詞、時を表す語など）
    STOP = {"こと", "もの", "ところ", "方", "順", "横", "ほう", "の", "今度", "ちなみ", "気", "はず", "とき", "ころ", "頃", "時",
            "今日", "昨日", "明日", "前", "後", "名前", "経由", "うろ覚え", "感じ", "ため", "よう", "そう"}

    def __init__(self):
        try:
            import fugashi
            self.tagger = fugashi.Tagger()
        except Exception as e:  # noqa: BLE001
            sys.exit(f"名詞の取り出しに fugashi が必要です（pip install fugashi unidic-lite）：{e}")

    def __call__(self, text):
        out, buf = set(), ""
        for w in self.tagger(text):
            p1 = w.feature.pos1
            if p1 in ("名詞", "接頭辞") or (p1 == "接尾辞" and buf):
                if w.feature.pos2 in ("数詞",) and not buf:
                    continue
                buf += w.surface
            else:
                if buf:
                    out.add(buf)
                buf = ""
        if buf:
            out.add(buf)
        return {n for n in out if n not in self.STOP and len(n) >= 2}


# ---------------------------------------------------------------- 1次解析

def run(conv, emb, nouns, delta, ctx_mode, drop, seed, branch_k, ai_mode):
    """ai_mode：
      fallback：AI に回した投稿を、ルールの仮の振り分けで扱う（AI を使わない場合の下限）
      oracle  ：AI に回した投稿を、正解どおりに振り分ける（AI が正しく判断する場合の上限。以降の投稿にも効く）"""
    rng = random.Random(seed)
    posts = [p for p in conv["posts"] if not p.get("new_thread")]
    gold = {p["id"]: (p["topics"][0] if p["topics"] else "OFF") for p in posts}
    reply = {}
    for p in posts:
        r = p["reply_to"] if isinstance(p["reply_to"], list) else ([p["reply_to"]] if p["reply_to"] else [])
        reply[p["id"]] = [] if (r and rng.random() < drop) else r
    text = {p["id"]: p["text"] for p in posts}
    topics = {}          # 予測の話題 → 投稿の一覧
    tnouns = defaultdict(set)
    tgold = defaultdict(Counter)  # 予測の話題に入った投稿の正解（oracle で使う）
    assign, reason, ai = {}, {}, set()
    pending = []

    def ctx_vec(tid):
        ids = topics[tid]
        if ctx_mode == "concat":
            return emb("。".join(text[i] for i in ids))
        v = np.mean([emb(text[i]) for i in ids], axis=0)
        return v / (np.linalg.norm(v) or 1)

    def sims(pid):
        v = emb(text[pid])
        return sorted(((float(v @ ctx_vec(t)), t) for t in topics if topics[t]), reverse=True)

    def new_topic():
        t = f"t{len(topics) + 1}"
        topics[t] = []
        return t

    def put(pid, tid, why):
        assign[pid] = tid
        reason[pid] = why
        if tid not in ("OFF", "PENDING"):
            topics[tid].append(pid)
            tnouns[tid] |= nouns(text[pid])
            tgold[tid][gold[pid]] += 1

    def ask_ai(pid, why, fallback):
        """AI に回す。oracle なら正解の話題（なければ新しい話題）、fallback なら仮の振り分け"""
        ai.add(pid)
        if ai_mode == "oracle":
            g = gold[pid]
            if g == "OFF":
                return put(pid, "OFF", why)
            for t in topics:
                if topics[t] and tgold[t].most_common(1)[0][0] == g:
                    return put(pid, t, why)
            return put(pid, new_topic(), why)
        put(pid, fallback() if callable(fallback) else fallback, why)

    for p in posts:
        pid = p["id"]
        ns = nouns(text[pid])
        is_ack = not ns and len(text[pid]) <= 20
        is_q = bool(QUESTION.search(text[pid]))
        rs = [r for r in reply[pid] if r in assign]
        if rs:
            pend = [r for r in rs if assign[r] == "PENDING"]
            if pend:  # 保留の投稿への返信：まとめて判断する（AI に回す）
                for r in pend:
                    pending.remove(r)
                    if ai_mode == "oracle":
                        ask_ai(r, "保留→返信が来て判断（AI）", None)
                    else:
                        ai.add(r)
                t = assign[pend[0]] if ai_mode == "oracle" and assign[pend[0]] != "PENDING" else None
                if t is None:
                    t = new_topic()
                    for r in pend:
                        put(r, t, "保留→返信が来て判断（AI）")
                if ai_mode == "oracle":
                    ask_ai(pid, "保留への返信（AI）", None)
                else:
                    put(pid, t, "保留への返信")
                continue
            cand = [assign[r] for r in rs if assign[r] not in ("OFF", "PENDING")]
            if not cand:
                put(pid, "OFF", "返信先が話題外")
                continue
            if is_ack:
                put(pid, cand[0], "相づち→返信先")
                continue
            # 分岐の候補：返信先の話題にも、ほかの話題にもない名詞が branch_k 個以上出てきた
            known = set().union(*tnouns.values()) if tnouns else set()
            fresh = ns - known
            if branch_k and len(fresh) >= branch_k:
                ask_ai(pid, f"分岐の候補（新しい名詞：{'・'.join(sorted(fresh))}）", cand[0])
                continue
            ss = sims(pid)
            best_c = max((x for x in ss if x[1] in cand), default=None)
            if not ss or ss[0][1] in cand or best_c is None or ss[0][0] - best_c[0] < delta:
                put(pid, best_c[1] if best_c else cand[0], "返信先")
            else:
                ask_ai(pid, "迷い（返信先より別の話題が近い）", ss[0][1])
            continue
        if not any(topics.values()):
            put(pid, new_topic(), "最初の投稿")
            continue
        overlap = {t for t in topics if ns & tnouns[t]}
        if is_q or not overlap:
            put(pid, "PENDING", "保留（問いかけ）" if is_q else "保留（既存の話題と名詞が重ならない）")
            pending.append(pid)
            continue
        ss = sims(pid)
        gap = ss[0][0] - (ss[1][0] if len(ss) > 1 else -1)
        if gap >= delta and ss[0][1] in overlap:
            put(pid, ss[0][1], "ベクトル＋名詞")
        else:
            ask_ai(pid, "迷い（ベクトルの差が小さい）", ss[0][1])
    for pid in list(pending):  # 返信が来なかった保留
        pending.remove(pid)
        ns = nouns(text[pid])
        overlap = {t for t in topics if ns & tnouns[t]}
        if overlap:
            ask_ai(pid, "保留のまま終了（AI）", lambda: sims(pid)[0][1])
        else:
            ask_ai(pid, "保留のまま終了（AI）", "OFF")
    return posts, assign, reason, ai, reply


def evaluate(conv, posts, assign, ai):
    """予測の話題を、正解の話題に対応づけて（多数決）、投稿ごとに当たりかを見る"""
    alias = conv.get("alias", {})
    gold = {p["id"]: set(p["topics"]) | {alias[t] for t in p["topics"] if t in alias} for p in posts}
    votes = defaultdict(Counter)
    for p in posts:
        t = assign[p["id"]]
        for g in (p["topics"] or ["OFF"]):
            votes[t][g] += 1
    label = {t: (c.most_common(1)[0][0] if t != "OFF" else "OFF") for t, c in votes.items()}
    ok = {}
    for p in posts:
        lab = label[assign[p["id"]]]
        ok[p["id"]] = (lab in gold[p["id"]]) or (lab == "OFF" and not gold[p["id"]])
    n = len(posts)
    auto = [i for i in ok if i not in ai]
    return {
        "n": n,
        "acc": sum(ok.values()) / n,
        "acc_auto": (sum(ok[i] for i in auto) / len(auto)) if auto else None,
        "ai_rate": len(ai) / n,
        "acc_upper": sum(1 for i in ok if ok[i] or i in ai) / n,
        "label": label,
        "ok": ok,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conversations", nargs="+", default=None, help="会話の JSON（既定：conversations/*.json と posts.json）")
    ap.add_argument("--delta", type=float, nargs="+", default=[0.01, 0.02, 0.03])
    ap.add_argument("--drops", type=float, nargs="+", default=[0.0, 0.5, 1.0])
    ap.add_argument("--seeds", type=int, default=20, help="返信先を一部消すときの、ランダムのくり返し回数")
    ap.add_argument("--branch", type=int, nargs="+", default=[0, 1, 2], help="返信の投稿で、既存の話題にない名詞がいくつ出たら分岐の候補として AI に回すか（0 はしない）")
    ap.add_argument("--env", default="")
    args = ap.parse_args()

    files = [Path(f) for f in args.conversations] if args.conversations else sorted((HERE / "conversations").glob("*.json")) + [HERE / "posts.json"]
    convs = []
    for f in files:
        c = json.loads(f.read_text(encoding="utf-8"))
        c["_name"] = f.stem
        convs.append(c)
    emb, nouns = Embedder(MODEL), Nouns()

    L = [f"# 1次解析で会話の投稿を話題に振り分ける（{MODEL}）\n"]
    L.append(f"- 実行日時：{datetime.now().strftime('%Y-%m-%d %H:%M')}　環境：{args.env}（デバイス {emb.device}）")
    names = ", ".join("{}（{}件）".format(c["_name"], len([p for p in c["posts"] if not p.get("new_thread")])) for c in convs)
    L.append(f"- 会話：{names}")
    L.append("- 精度：予測の話題を正解の話題に多数決で対応づけ、投稿が正解の話題（複数ありうる）に入ったか。話題外（雑談）は「話題外」に入れば当たり")
    L.append("- 精度（AI なし）：AI に回した投稿も、ルールの仮の振り分けで扱ったもの。精度（自動で振り分けた分）：AI に回さなかった投稿だけの精度。精度（AI が正しい場合）：AI に回した投稿を正解どおりに振り分けたもの（以降の投稿にも効く）")
    L.append(f"- 返信先を消す割合 0.5 は、{args.seeds} 回の平均\n")
    head = ("| {} | 文脈のベクトル | δ | 分岐の候補（新しい名詞の数） | 返信先を消す割合 | 精度（AI なし） | 精度（自動で振り分けた分） | 精度（AI が正しい場合） | AI に回す割合 |",
            "|---|---|---|---|---|---|---|---|---|")
    traces, stats = [], {}
    for c in convs:
        for mode in ("concat", "mean"):
            for d in args.delta:
                for bk in args.branch:
                    for drop in args.drops:
                        seeds = range(args.seeds) if 0 < drop < 1 else [0]
                        row = []
                        for ai_mode in ("fallback", "oracle"):
                            rs = []
                            for s in seeds:
                                posts, assign, reason, ai, reply = run(c, emb, nouns, d, mode, drop, s, bk, ai_mode)
                                rs.append(evaluate(c, posts, assign, ai))
                                if s == 0 and mode == "concat" and d == args.delta[len(args.delta) // 2] and bk == args.branch[-1] and drop in (0.0, 1.0):
                                    traces.append((c, drop, ai_mode, posts, assign, reason, ai, reply, rs[-1]))
                            row.append(rs)
                        m = lambda rs, k: np.mean([r[k] for r in rs if r[k] is not None])  # noqa: E731
                        fb, orc = row
                        n = len([p for p in c["posts"] if not p.get("new_thread")])
                        stats[(c["_name"], mode, d, bk, drop)] = (m(fb, "acc"), m(fb, "acc_auto"), m(orc, "acc"), m(fb, "ai_rate"), n)

    def fmt(key_name, mode, d, bk, drop, v):
        acc, auto, orc, air, _ = v
        return f"| {key_name} | {mode} | {d} | {bk or 'なし'} | {drop} | {acc:.2f} | {auto:.2f} | {orc:.2f} | {air:.2f} |"

    # 全会話の合計（投稿数で重み付け。自動で振り分けた分は、自動で振り分けた投稿数で重み付け）
    L.append(f"## 全会話（{len(convs)}本・{sum(v[4] for k, v in stats.items() if k[1:] == next(iter(stats))[1:])}件）\n")
    L.append(head[0].format("会話"))
    L.append(head[1])
    for mode in ("concat", "mean"):
        for d in args.delta:
            for bk in args.branch:
                for drop in args.drops:
                    vs = [stats[(c["_name"], mode, d, bk, drop)] for c in convs]
                    n = sum(v[4] for v in vs)
                    na = sum(v[4] * (1 - v[3]) for v in vs)
                    auto = sum(v[1] * v[4] * (1 - v[3]) for v in vs if not np.isnan(v[1])) / na if na else float("nan")
                    agg = (sum(v[0] * v[4] for v in vs) / n, auto, sum(v[2] * v[4] for v in vs) / n, sum(v[3] * v[4] for v in vs) / n, n)
                    L.append(fmt("全体", mode, d, bk, drop, agg))
    dm = args.delta[len(args.delta) // 2]
    L.append(f"\n## 会話ごと（concat、δ {dm}）\n")
    L.append(head[0].format("会話"))
    L.append(head[1])
    for c in convs:
        for bk in args.branch:
            for drop in args.drops:
                L.append(fmt(c["_name"], "concat", dm, bk, drop, stats[(c["_name"], "concat", dm, bk, drop)]))

    L.append("\n## 投稿ごとの振り分け（concat、δ は中央の値、分岐の候補は最後の値、返信先 0 と 1）\n")
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
    name = MODEL.split("/")[-1]
    (out / f"first_pass_{name}.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[: L.index(next(x for x in L if x.startswith("\n## 投稿ごと")))]))
    print(f"\n結果：{out / f'first_pass_{name}.md'}")


if __name__ == "__main__":
    main()
