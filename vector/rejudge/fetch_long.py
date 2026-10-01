"""ex-day PoC（Issue #12）：判定に使う記事の文章を長くする（冒頭300字 → 概要の全文、最大1,000字）

data/pairs.jsonl の候補の記事について、Wikipedia の API で記事の概要（最初の見出しより前）の全文を取り、
candidate_text を最大1,000字に差し替えた data/pairs_long.jsonl を作る。記事の本文は CC BY-SA のため data/ に置く。

python fetch_long.py
"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://ja.wikipedia.org/w/api.php"
UA = "ex-day-poc/0.1 (https://github.com/ex-day/poc; technical verification)"
CHARS = 1000


def call(params):
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": 2})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    time.sleep(0.5)
    return data


def main():
    pairs = [json.loads(l) for l in (HERE / "data" / "pairs.jsonl").read_text(encoding="utf-8").splitlines()]
    ids = sorted({p["candidate_id"].split(":")[1] for p in pairs})
    text = {}
    for i in range(0, len(ids), 20):
        d = call({"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1, "exlimit": 20, "pageids": "|".join(ids[i:i + 20])})
        for p in d["query"]["pages"]:
            text[str(p["pageid"])] = (p.get("extract") or "").replace("\n", " ").strip()
        print(f"{min(i + 20, len(ids))}/{len(ids)}", flush=True)
    out = HERE / "data" / "pairs_long.jsonl"
    longer = 0
    with out.open("w", encoding="utf-8") as fh:
        for p in pairs:
            t = text.get(p["candidate_id"].split(":")[1]) or p["candidate_text"]
            p["candidate_text"] = t[:CHARS]
            longer += len(p["candidate_text"]) > 300
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"{len(pairs)} 組 → {out}（300字を超えたもの {longer} 組）")


if __name__ == "__main__":
    main()
