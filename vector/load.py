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


def discovery_texts(d):
    """Discoveryをベクトル化する文章を2通り作る（何をベクトル化すると精度が上がるかを比べるため）。"""
    targets = [s for s, k in d["subjects"] if k in ("target", "place", "event")]
    viewpoints = [s for s, k in d["subjects"] if k == "viewpoint"]
    base = f"{d['name']}。対象：{'、'.join(targets)}。観点：{'、'.join(viewpoints)}。"
    full = base + "".join(f"{t}。" for _, t in d["findings"])
    return {"name_subjects": base, "full": full}


def main(model_name=None, quiet=False):
    data = json.loads((HERE / "sample_data.json").read_text(encoding="utf-8"))
    conn = connect()
    cur = conn.cursor()

    # データの入れ直し（埋め込みは model ごとに残す）
    cur.execute("DELETE FROM poc.discovery_subject; DELETE FROM poc.discovery_finding;")
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

    # Discoveryの埋め込み（2通りの文章）
    rows = []
    for d in data["discoveries"]:
        for variant, text in discovery_texts(d).items():
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
    log("完了。次は python search.py を実行してください。")
    return emb, load_sec


if __name__ == "__main__":
    main()
