"""寄り道の提案（Issue #6）で使う「場所」を reach.spot に入れ、埋め込みを作る。

使い方：
  python load_spots.py <Wikidata の結果の JSON>
  例：EXDAY_MODEL=cl-nagoya/ruri-v3-30m python load_spots.py data/wikidata_spots.json

- Wikidata の JSON は、README の問い合わせを query.wikidata.org で実行し、「JSON file」で保存したもの
- vector/sample_data.json の Discovery も、手で付けた座標（下の SAMPLE_COORDS）で入れる
- 埋め込みは vector/common.py の Embedder を使う（モデルは環境変数 EXDAY_MODEL）
先に sql/04_spot.sql を流しておくこと。
"""
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "vector"))
from common import Embedder, connect  # noqa: E402

# sample_data.json の Discovery の概略の座標（経度, 緯度）。地図で見当を付けた値で、正確ではない。
# 「別地域の貝料理」は特定の場所がないので入れない。
SAMPLE_COORDS = {
    "namamugi": (139.672, 35.494),           # 生麦
    "kozukue": (139.594, 35.511),            # 小机城址
    "walk_route": (139.640, 35.450),         # 桜木町〜山下公園の途中（汽車道のあたり）
    "yamashita_area": (139.650, 35.446),     # 山下公園
    "motomachi": (139.648, 35.441),          # 元町商店街
    "sakuragicho_area": (139.631, 35.451),   # 桜木町駅
    "rinkosen": (139.643, 35.451),           # 山下臨港線プロムナード
    "hikawamaru": (139.651, 35.447),         # 氷川丸
    "akaikutsu": (139.650, 35.446),          # 赤い靴はいてた女の子像
    "kishamichi": (139.636, 35.453),         # 汽車道
    "abt_road": (138.710, 36.350),           # アプトの道（横川〜熊ノ平）
    "sankeien": (139.663, 35.417),           # 三溪園
    "nogeyama_zoo": (139.623, 35.446),       # 野毛山動物園
    "osanbashi": (139.646, 35.452),          # 大さん橋
    "zounohana": (139.642, 35.450),          # 象の鼻パーク
    "chinatown": (139.646, 35.443),          # 横浜中華街
    "minatonomieruoka": (139.652, 35.438),   # 港の見える丘公園
    "fukagawameshi": (139.796, 35.672),      # 深川（門前仲町のあたり）
    "odawara_castle": (139.153, 35.251),     # 小田原城
    "kamakura_daibutsu": (139.536, 35.317),  # 鎌倉大仏
    "enoden": (139.481, 35.305),             # 江ノ電（江ノ島駅のあたり）
    "hodogaya": (139.596, 35.447),           # 旧東海道 保土ケ谷宿
}

POINT_RE = re.compile(r"Point\(([-0-9.eE]+) ([-0-9.eE]+)\)")


def wikidata_rows(path: Path):
    """同じ場所が種類違いで複数行あるので、Q番号でまとめる。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else data["results"]["bindings"]

    def val(r, k):
        v = r.get(k)
        return v.get("value") if isinstance(v, dict) else v

    items = OrderedDict()
    for r in rows:
        qid = val(r, "item").rsplit("/", 1)[-1]
        m = POINT_RE.match(val(r, "coord") or "")
        if not m:
            continue
        name = val(r, "itemLabel") or qid
        if name == qid:  # 日本語・英語の名前がないもの
            continue
        it = items.setdefault(qid, {
            "name": name, "description": val(r, "itemDescription"),
            "kinds": [], "lon": float(m.group(1)), "lat": float(m.group(2)),
        })
        kind = val(r, "typeLabel")
        if kind and kind not in it["kinds"]:
            it["kinds"].append(kind)
    for qid, it in items.items():
        parts = [it["name"], it["description"], "、".join(it["kinds"])]
        doc = "。".join(p for p in parts if p)
        yield ("wikidata", qid, it["name"], it["description"], "、".join(it["kinds"]), doc, it["lon"], it["lat"])


def sample_rows():
    data = json.loads((HERE.parent / "vector" / "sample_data.json").read_text(encoding="utf-8"))
    for d in data["discoveries"]:
        if d["id"] not in SAMPLE_COORDS:
            continue
        lon, lat = SAMPLE_COORDS[d["id"]]
        subjects = "、".join(s[0] for s in d.get("subjects", []))
        findings = "。".join(f[1] for f in d.get("findings", []))
        doc = "。".join(p for p in [d["name"], subjects, findings] if p)
        yield ("sample", d["id"], d["name"], None, "サンプルの Discovery", doc, lon, lat)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    rows = list(wikidata_rows(Path(sys.argv[1]).expanduser())) + list(sample_rows())
    emb = Embedder()
    print(f"{len(rows)} 件の埋め込みを作る（{emb.model_name}、{emb.device}）")
    vecs = emb.passage([r[5] for r in rows], batch_size=64)

    with connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE reach.spot RESTART IDENTITY")
        with cur.copy(
            "COPY reach.spot (source, ext_id, name, description, kinds, doc, model, embedding, geom) FROM STDIN"
        ) as copy:
            for r, v in zip(rows, vecs):
                source, ext_id, name, desc, kinds, doc, lon, lat = r
                copy.write_row((
                    source, ext_id, name, desc, kinds, doc, emb.model_name,
                    "[" + ",".join(f"{x:.6f}" for x in v) + "]",
                    f"SRID=4326;POINT({lon} {lat})",
                ))
        cur.execute("ANALYZE reach.spot")
        cur.execute("SELECT source, count(*) FROM reach.spot GROUP BY 1 ORDER BY 1")
        print("入れた件数：", dict(cur.fetchall()))


if __name__ == "__main__":
    main()
