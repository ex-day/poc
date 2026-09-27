"""ex-day PoC（Issue #1）：サンプルデータを入れ、埋め込みを作って保存する

使い方：
  python load.py                      # 既定のモデル（multilingual-e5-small）
  EXDAY_MODEL=ngram-baseline python load.py   # 比較用の基準（文字の重なりだけ）
  EXDAY_MODEL=cl-nagoya/ruri-v3-70m python load.py   # 日本語向けのモデル（使えるモデルは common.py の MODELS）

何度実行してもよい（データは入れ直す。埋め込みは文章が変わったときだけ作り直す）。
"""
import json
import time
from pathlib import Path

from common import Embedder, connect, content_hash, normalize

HERE = Path(__file__).parent


def discovery_texts(d, topics=()):
    """Discoveryをベクトル化する文章を作る（何をベクトル化すると精度が上がるかを比べるため）。
    name_subjects：名前＋対象＋観点
    full         ：上に、わかってきたことを足す
    topics       ：上に、話題ごとの「何について」の1行と、話題でわかってきたことを足す（話題がなければ full と同じ）
    """
    targets = [s for s, k in d["subjects"] if k in ("target", "place", "event")]
    viewpoints = [s for s, k in d["subjects"] if k == "viewpoint"]
    base = f"{d['name']}。対象：{'、'.join(targets)}。観点：{'、'.join(viewpoints)}。"
    full = base + "".join(f"{t}。" for _, t in d["findings"])
    with_topics = full + "".join(f"{t['about']}。" + "".join(f"{x}。" for _, x in t["findings"]) for t in topics)
    return {"name_subjects": base, "full": full, "topics": with_topics}


def topic_texts(t):
    """話題をベクトル化する文章。about：何についての1行だけ、about_findings：1行＋わかってきたこと。"""
    return {"about": t["about"], "about_findings": t["about"] + "。" + "".join(f"{x}。" for _, x in t["findings"])}


def main(model_name=None, quiet=False):
    data = json.loads((HERE / "sample_data.json").read_text(encoding="utf-8"))
    conn = connect()
    cur = conn.cursor()

    # データの入れ直し（埋め込みは model ごとに残す）
    cur.execute("DELETE FROM poc.discovery_subject; DELETE FROM poc.discovery_finding; DELETE FROM poc.topic_finding;")
    for d in data["discoveries"]:
        cur.execute(
            """INSERT INTO poc.discovery (id, name, spatial_type, note) VALUES (%s,%s,%s,%s)
               ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, spatial_type=EXCLUDED.spatial_type, note=EXCLUDED.note""",
            (d["id"], d["name"], d["spatial_type"], d.get("note")),
        )
        for name, kind in d["subjects"]:
            cur.execute(
                """INSERT INTO poc.subject (name, normalized_name, kind) VALUES (%s,%s,%s)
                   ON CONFLICT (normalized_name) DO UPDATE SET name=EXCLUDED.name RETURNING id""",
                (name, normalize(name), kind),
            )
            sid = cur.fetchone()[0]
            role = "観点" if kind == "viewpoint" else "対象"
            cur.execute(
                "INSERT INTO poc.discovery_subject VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (d["id"], sid, role),
            )
        for kind, text in d["findings"]:
            cur.execute(
                "INSERT INTO poc.discovery_finding (discovery_id, kind, text) VALUES (%s,%s,%s)",
                (d["id"], kind, text),
            )

    topics = data.get("topics", [])
    for t in topics:
        cur.execute(
            """INSERT INTO poc.topic (id, discovery_id, place, target, viewpoint, about) VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (id) DO UPDATE SET discovery_id=EXCLUDED.discovery_id, place=EXCLUDED.place,
               target=EXCLUDED.target, viewpoint=EXCLUDED.viewpoint, about=EXCLUDED.about""",
            (t["id"], t["discovery_id"], t["place"], t["target"], t["viewpoint"], t["about"]),
        )
        for kind, text in t["findings"]:
            cur.execute("INSERT INTO poc.topic_finding (topic_id, kind, text) VALUES (%s,%s,%s)", (t["id"], kind, text))

    t0 = time.time()
    emb = Embedder(model_name)
    load_sec = time.time() - t0
    log = (lambda *a: None) if quiet else print
    log(f"モデル：{emb.model_name}（{emb.dim}次元。読み込み {load_sec:.1f} 秒）")

    # Subjectの埋め込み（文章が変わったものだけ作り直す）
    cur.execute("SELECT id, name FROM poc.subject ORDER BY id")
    subjects = cur.fetchall()
    cur.execute("SELECT subject_id, content_hash FROM poc.subject_embedding WHERE model=%s", (emb.model_name,))
    have = dict(cur.fetchall())
    todo = [(sid, name) for sid, name in subjects if have.get(sid) != content_hash(name)]
    t0 = time.time()
    if todo:
        vecs = emb.query([name for _, name in todo])
        for (sid, name), v in zip(todo, vecs):
            cur.execute(
                """INSERT INTO poc.subject_embedding VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (subject_id, model) DO UPDATE SET source_text=EXCLUDED.source_text,
                   content_hash=EXCLUDED.content_hash, embedding=EXCLUDED.embedding""",
                (sid, emb.model_name, name, content_hash(name), v),
            )
    log(f"Subject：{len(subjects)} 件（作り直し {len(todo)} 件、{time.time() - t0:.2f} 秒）")

    # Discoveryの埋め込み（3通りの文章）
    rows = []
    for d in data["discoveries"]:
        own = [t for t in topics if t["discovery_id"] == d["id"]]
        for variant, text in discovery_texts(d, own).items():
            rows.append((d["id"], variant, text))
    cur.execute("SELECT discovery_id, variant, content_hash FROM poc.discovery_embedding WHERE model=%s", (emb.model_name,))
    have = {(a, b): c for a, b, c in cur.fetchall()}
    todo = [r for r in rows if have.get((r[0], r[1])) != content_hash(r[2])]
    t0 = time.time()
    if todo:
        vecs = emb.passage([t for _, _, t in todo])
        for (did, variant, text), v in zip(todo, vecs):
            cur.execute(
                """INSERT INTO poc.discovery_embedding VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (discovery_id, model, variant) DO UPDATE SET source_text=EXCLUDED.source_text,
                   content_hash=EXCLUDED.content_hash, embedding=EXCLUDED.embedding""",
                (did, emb.model_name, variant, text, content_hash(text), v),
            )
    log(f"Discovery：{len(rows)} 件（作り直し {len(todo)} 件、{time.time() - t0:.2f} 秒）")
    # 話題の埋め込み（2通りの文章）
    rows = [(t["id"], variant, text) for t in topics for variant, text in topic_texts(t).items()]
    cur.execute("SELECT topic_id, variant, content_hash FROM poc.topic_embedding WHERE model=%s", (emb.model_name,))
    have = {(a, b): c for a, b, c in cur.fetchall()}
    todo = [r for r in rows if have.get((r[0], r[1])) != content_hash(r[2])]
    if todo:
        vecs = emb.passage([t for _, _, t in todo])
        for (tid, variant, text), v in zip(todo, vecs):
            cur.execute(
                """INSERT INTO poc.topic_embedding VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (topic_id, model, variant) DO UPDATE SET source_text=EXCLUDED.source_text,
                   content_hash=EXCLUDED.content_hash, embedding=EXCLUDED.embedding""",
                (tid, emb.model_name, variant, text, content_hash(text), v),
            )
    log(f"話題：{len(topics)} 件（作り直し {len(todo)} 件）")
    log("完了。次は python search.py を実行してください。")
    return emb, load_sec


if __name__ == "__main__":
    main()
