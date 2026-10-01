"""ex-day PoC（Issue #9）：件数を増やす用の、架空の Discovery の文章と問いを作る

disc_full と同じ形（名前＋対象＋観点＋わかってきたこと）の文章を、地名・対象・観点・わかってきたことの組み合わせで量産する。
速さと埋め込みの作成時間を測るためのもの。中身は架空で、精度の評価（正解との比較）には使わない
（精度の評価には、実在の場所・題材に近い文章を別に用意する。Issue #9「データ」）。

python gen_texts.py 5    # 見本を5件表示
"""
import random
import sys

PLACES = [
    "桜木町", "山下公園", "元町", "中華街", "関内", "野毛", "伊勢佐木町", "大さん橋", "赤レンガ倉庫", "みなとみらい",
    "生麦", "鶴見", "神奈川宿", "保土ケ谷", "戸塚", "小机", "新横浜", "日吉", "金沢八景", "本牧",
    "鎌倉", "江の島", "小田原", "箱根", "川崎", "浅草", "日本橋", "深川", "品川", "浜松町",
    "上野", "谷中", "神田", "築地", "月島", "横須賀", "三浦", "大磯", "藤沢", "厚木",
]
OBJECTS = [
    ("貨物線の跡", "鉄道"), ("古い石垣", "城"), ("商店街", "まち"), ("灯台", "港"), ("郷土料理", "食"), ("干物", "食"),
    ("寺の山門", "寺社"), ("神社の祭り", "行事"), ("運河", "水辺"), ("坂道", "地形"), ("洋館", "建物"), ("赤れんがの倉庫", "建物"),
    ("路面電車の跡", "鉄道"), ("市場", "食"), ("宿場の名残", "街道"), ("渡し船", "水辺"), ("桜並木", "景色"), ("夜景", "景色"),
    ("古い地名", "地名"), ("職人の店", "まち"), ("貝の料理", "食"), ("城跡", "城"), ("トンネル", "鉄道"), ("高架の遊歩道", "景色"),
]
VIEWS = ["歴史", "景色", "食文化", "散歩", "由来", "地域史", "暮らし", "思い出", "季節", "建築"]
FINDINGS = [
    "明治のころに作られたとされる", "地元では昔から親しまれている", "夕方に眺めがよくなる", "春の行事のときににぎわう",
    "戦後に一度なくなったが、復元された", "名前の由来には説がいくつかある", "漁師町だったころの名残がある", "歩いて回るのにちょうどよい",
    "季節によって味が変わる", "古い写真と比べると街並みが大きく変わった", "近くの駅から少し坂を上る", "昔は貨物を運んでいた",
    "地元の人が保存の活動をしている", "雨の日は足元に気をつける", "観光客より地元の人が多い", "夜は灯りがともって雰囲気が変わる",
]


def generate_discoveries(n, seed=0):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        place = rng.choice(PLACES)
        obj, cat = rng.choice(OBJECTS)
        views = rng.sample(VIEWS, k=rng.randint(1, 3))
        findings = rng.sample(FINDINGS, k=rng.randint(1, 4))
        name = f"{place}の{obj}"
        out.append(f"{name}。対象：{obj}、{cat}。観点：{'、'.join(views)}。" + "。".join(findings) + "。")
    return out


QUERY_TEMPLATES = [
    "{place}で{view}を感じられる場所",
    "{obj}を見に行きたい",
    "{place}の近くで{obj}ってある？",
    "{view}について知りたい、{place}あたりで",
    "昔の{obj}が残っているところ",
]


def generate_queries(n, seed=1):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        obj, _ = rng.choice(OBJECTS)
        out.append(rng.choice(QUERY_TEMPLATES).format(place=rng.choice(PLACES), obj=obj, view=rng.choice(VIEWS)))
    return out


if __name__ == "__main__":
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    for t in generate_discoveries(k):
        print(t)
    print("---")
    for q in generate_queries(k):
        print(q)
