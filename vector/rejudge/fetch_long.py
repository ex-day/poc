"""ex-day PoC（Issue #12）：判定に使う記事の文章を長くする（冒頭300字 → 本文の先頭から最大1,000字）

data/pairs.jsonl の候補の記事について、Wikipedia の API で記事の本文（概要だけでなく、見出しの後も含む）を取り、
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
    # 概要（exintro）だけだと短い記事が多いため、本文全体を取る。本文全体は1回に1記事しか取れない
    for i, pid in enumerate(ids, 1):
        d = call({"action": "query", "prop": "extracts", "explaintext": 1, "exsectionformat": "plain", "pageids": pid})
        for p in d["query"]["pages"]:
            text[str(p["pageid"])] = " ".join((p.get("extract") or "").split())
        if i % 10 == 0 or i == len(ids):
            print(f"{i}/{len(ids)}", flush=True)
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
