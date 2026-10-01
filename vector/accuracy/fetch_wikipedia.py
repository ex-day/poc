"""ex-day PoC（Issue #9）：紛れ込み用の文章として、Wikipedia（日本語版）の地点記事の冒頭を集める

神奈川・東京のあたりを格子に区切り、各点の周り（半径10km）の記事を座標で探して（list=geosearch）、
記事の冒頭の文章（prop=extracts）を取る。結果は data/wikipedia_kanto.jsonl に1行1記事で書く。

- 記事の本文は CC BY-SA のため、リポジトリには入れない（data/ は .gitignore）。取得のスクリプトだけを置く
- 途中で止めても、もう一度実行すれば取得済みの記事は飛ばして続きから取る
- Wikimedia の API の利用ルールに合わせ、User-Agent を付け、1回ずつ間をあけて呼ぶ

python fetch_wikipedia.py                 # 既定：神奈川・東京のあたり、最大 12,000 記事
python fetch_wikipedia.py --max 3000      # 少なめに試す
"""
import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://ja.wikipedia.org/w/api.php"
UA = "ex-day-poc/0.1 (https://github.com/ex-day/poc; technical verification)"
OUT = Path(__file__).resolve().parent / "data" / "wikipedia_kanto.jsonl"


def call(params, wait):
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": 2})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            time.sleep(wait)
            return data
        except Exception as e:  # 一時的な失敗は少し待ってやり直す
            print(f"  再試行 {attempt + 1}：{e}")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("API の呼び出しに失敗しました")


def grid(lat0, lat1, lon0, lon1, step):
    lat = lat0
    while lat <= lat1:
        lon = lon0
        while lon <= lon1:
            yield round(lat, 4), round(lon, 4)
            lon += step
        lat += step


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", type=float, nargs=4, default=[35.15, 35.90, 138.95, 139.95], metavar=("LAT0", "LAT1", "LON0", "LON1"))
    ap.add_argument("--step", type=float, default=0.08, help="格子の間隔（度）。0.08度 ≒ 7〜9km")
    ap.add_argument("--max", type=int, default=12000, help="集める記事の上限")
    ap.add_argument("--chars", type=int, default=300, help="冒頭の文章を何文字までにするか")
    ap.add_argument("--wait", type=float, default=0.5, help="API の呼び出しの間隔（秒）")
    args = ap.parse_args()

    OUT.parent.mkdir(exist_ok=True)
    have = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            have.add(json.loads(line)["pageid"])
    print(f"取得済み：{len(have)} 記事")

    # 1. 座標で記事を探す
    found = {}
    points = list(grid(*args.bbox, args.step))
    for i, (lat, lon) in enumerate(points, 1):
        d = call({"action": "query", "list": "geosearch", "gscoord": f"{lat}|{lon}", "gsradius": 10000, "gslimit": 500, "gsnamespace": 0}, args.wait)
        for g in d.get("query", {}).get("geosearch", []):
            found.setdefault(g["pageid"], {"pageid": g["pageid"], "title": g["title"], "lat": g["lat"], "lon": g["lon"]})
        print(f"格子 {i}/{len(points)}（{lat}, {lon}）：見つかった記事 {len(found)}", flush=True)
        if len(found) >= args.max * 1.2:
            break

    todo = [p for p in found.values() if p["pageid"] not in have][: max(0, args.max - len(have))]
    print(f"冒頭を取る記事：{len(todo)}")

    # 2. 冒頭の文章を取る（20記事ずつ）
    with OUT.open("a", encoding="utf-8") as fh:
        for i in range(0, len(todo), 20):
            batch = todo[i : i + 20]
            d = call({"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1, "exlimit": 20,
                      "pageids": "|".join(str(p["pageid"]) for p in batch)}, args.wait)
            pages = {p["pageid"]: p for p in d.get("query", {}).get("pages", [])}
            for p in batch:
                text = (pages.get(p["pageid"], {}).get("extract") or "").replace("\n", " ").strip()
                if not text:
                    continue
                fh.write(json.dumps({**p, "extract": text[: args.chars]}, ensure_ascii=False) + "\n")
            print(f"  冒頭 {min(i + 20, len(todo))}/{len(todo)}", flush=True)
    print(f"完了：{OUT}")


if __name__ == "__main__":
    main()
