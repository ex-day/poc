"""OTP の等時間線で「寄り道できる駅」を全駅について出し、pgRouting（reach.reachable）と比べる（Issue #26）。

使い方（OTP を TravelTime API つきで起動してから。手順は README.md）：
  python compare_otp_reach.py

比べること：
  1. 全駅の到達判定：条件（出発・帰着・使える時間・滞在）ごとに、四角の中の駅のまとまりを
     「両方で行ける／pgRouting だけ／OTP だけ／どちらも行けない」に分ける
  2. OTP の待ち：発車時刻を2分ずつずらして5回問い合わせ、中央値と、安全側（5回のうち遅いほうから2番目＝80パーセンタイル）で判定する
  3. 等時間線の速さ：1回の問い合わせ（帯つき）にかかる時間

OTP の TravelTime API（/otp/traveltime/isochrone）を使う。等時間線は5分刻みの帯でもらい、
駅のまとまりの重心がどの帯に入るか（入る中でいちばん短い帯の上限）を、PostGIS で調べる。
帯の上限を使うので、OTP の時間は最大5分長めに出る（「行けない」側に寄せる）。
"""
import json
import os
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path

import psycopg

DSN = os.environ.get("EXDAY_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/ex_day_poc")
OTP = os.environ.get("EXDAY_OTP_BASE", "http://127.0.0.1:8080/otp")
BBOX = tuple(float(x) for x in os.environ.get("EXDAY_GTFS_BBOX", "138.4,34.8,140.9,37.2").split(","))
HERE = Path(__file__).resolve().parent
DATE = "2026-11-10"   # 平日（仮の時刻表は毎日同じ）
STEP = 5              # 帯の刻み（分）
OFFSETS = [0, 2, 4, 6, 8]

# （名前, 出発のグループ, 帰着のグループ, 出発時刻, 使える時間（分）, 滞在（分））
CONDITIONS = [
    ("新横浜 → 東京", "004501", "003766", "10:00", 180, 60),
    ("八王子 → 千葉", "003947", "004173", "09:00", 300, 90),
    ("新宿 → 鎌倉", "003700", "005055", "09:00", 240, 60),
]


def hhmm(base, add_min):
    h, m = map(int, base.split(":"))
    t = h * 60 + m + add_min
    return f"{t // 60:02d}:{t % 60:02d}"


def isochrone(lon, lat, at, arrive_by, max_min):
    q = [("location", f"{lat},{lon}"), ("time", f"{DATE}T{at}:00+09:00"),
         ("modes", "WALK,TRANSIT"), ("arriveBy", "true" if arrive_by else "false")]
    q += [("cutoff", f"{m}M") for m in range(STEP, max_min + STEP, STEP)]
    url = f"{OTP}/traveltime/isochrone?" + urllib.parse.urlencode(q)
    t0 = time.perf_counter()
    with urllib.request.urlopen(url, timeout=300) as r:
        body = json.load(r)
    return body, time.perf_counter() - t0


def band_minutes(feature, i):
    """帯の上限（分）。OTP は properties.time（秒）に入れる。無ければ並び順から決める。刻みに丸める"""
    p = feature.get("properties") or {}
    m = p["time"] / 60.0 if "time" in p else STEP * (i + 1)
    return int(round(m / STEP) * STEP)


def pct(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def main() -> None:
    out = ["# OTP の等時間線と pgRouting の、全駅の到達判定の比較（Issue #26）", "",
           f"- OTP：{OTP}/traveltime/isochrone、日付 {DATE}、帯は {STEP}分刻み（帯の上限を使うので、OTP は最大{STEP}分長めに出る）",
           f"- 発車時刻を {OFFSETS} 分ずらして {len(OFFSETS)} 回。OTP の判定は「中央値」と「安全側（80パーセンタイル）」の2つ",
           "- pgRouting：reach.reachable（乗り始めの待ち5分・乗換10分を含む）",
           f"- 対象：四角 {BBOX} の中の駅のまとまり（出発・帰着の駅を除く）", ""]
    req_secs = []
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE iso (cond int, dir text, sample int, minutes int, geom geometry)")
        for ci, (title, src, dst, dep, avail, stay) in enumerate(CONDITIONS):
            move_max = avail - stay
            cur.execute("""SELECT group_code, ST_X(ST_Centroid(ST_Collect(geom))), ST_Y(ST_Centroid(ST_Collect(geom)))
                           FROM reach.station WHERE group_code IN (%s, %s) GROUP BY 1""", (src, dst))
            pts = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
            # pgRouting：使える時間を大きくして、全駅の t1・t2 をもらう
            cur.execute("SELECT group_code, station_name, t1_min, t2_min, ST_X(geom), ST_Y(geom) "
                        "FROM reach.reachable(%s, %s, 1000, 0)", (src, dst))
            pg = {r[0]: (r[1], float(r[2]), float(r[3])) for r in cur.fetchall()
                  if BBOX[0] <= r[4] <= BBOX[2] and BBOX[1] <= r[5] <= BBOX[3]}
            # OTP：行き（出発時刻から）と帰り（期限に着く、逆向き）を、時刻をずらして
            for si, off in enumerate(OFFSETS):
                for d, (lon, lat), at, ab in (("out", pts[src], hhmm(dep, off), False),
                                              ("back", pts[dst], hhmm(dep, avail - off), True)):
                    body, sec = isochrone(lon, lat, at, ab, move_max)
                    req_secs.append(sec)
                    feats = body.get("features", [])
                    for i, f in enumerate(feats):
                        cur.execute("INSERT INTO iso VALUES (%s, %s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))",
                                    (ci, d, si, band_minutes(f, i), json.dumps(f["geometry"])))
                print(f"{title}：{si + 1}/{len(OFFSETS)}")
            # 駅のまとまりの重心が入る、いちばん短い帯
            cur.execute("""
                WITH g AS (SELECT group_code, ST_Centroid(ST_Collect(geom)) AS p FROM reach.station GROUP BY 1)
                SELECT g.group_code, i.dir, i.sample, min(i.minutes)
                FROM g JOIN iso i ON i.cond = %s AND ST_Covers(i.geom, g.p)
                GROUP BY 1, 2, 3""", (ci,))
            otp = {}
            for g, d, si, m in cur.fetchall():
                otp.setdefault(g, {}).setdefault(d, {})[si] = m

            def otp_t(g, d, q):
                v = otp.get(g, {}).get(d, {})
                if len(v) < len(OFFSETS):   # どれかの時刻で帯に入らない＝その時刻では行けない
                    return None
                return pct(list(v.values()), q)

            rows = []
            for g, (name, t1, t2) in pg.items():
                pg_ok = t1 + stay + t2 <= avail
                res = {}
                for label, q in (("中央値", 0.5), ("安全側", 0.8)):
                    o1, o2 = otp_t(g, "out", q), otp_t(g, "back", q)
                    res[label] = (o1, o2, o1 is not None and o2 is not None and o1 + stay + o2 <= avail)
                rows.append((g, name, t1, t2, pg_ok, res))

            out += [f"## {title}（{dep} 発、使える時間 {avail}分、滞在 {stay}分）", ""]
            out += ["| OTP の判定 | 両方で行ける | pgRouting だけ | OTP だけ | どちらも行けない |", "|---|---|---|---|---|"]
            for label in ("中央値", "安全側"):
                both = sum(1 for r in rows if r[4] and r[5][label][2])
                pg_only = sum(1 for r in rows if r[4] and not r[5][label][2])
                otp_only = sum(1 for r in rows if not r[4] and r[5][label][2])
                none = len(rows) - both - pg_only - otp_only
                out.append(f"| {label} | {both:,} | {pg_only:,} | {otp_only:,} | {none:,} |")
            out.append("")
            # 判定が逆転した駅の例（安全側）
            for label, cond in (("pgRouting だけ", lambda r: r[4] and not r[5]["安全側"][2]),
                                ("OTP だけ", lambda r: not r[4] and r[5]["安全側"][2])):
                ex = [r for r in rows if cond(r)]
                ex.sort(key=lambda r: abs((r[2] + r[3]) - ((r[5]["安全側"][0] or 999) + (r[5]["安全側"][1] or 999))), reverse=True)
                if ex:
                    out += [f"{label}（安全側。差の大きい順に10駅）：", "",
                            "| 駅 | pg t1 | pg t2 | OTP t1 | OTP t2 |", "|---|---|---|---|---|"]
                    f = lambda x: "—" if x is None else f"{x:.0f}"  # noqa: E731
                    out += [f"| {r[1]} | {r[2]:.0f} | {r[3]:.0f} | {f(r[5]['安全側'][0])} | {f(r[5]['安全側'][1])} |" for r in ex[:10]]
                    out.append("")
            # 両方で行ける駅の、時間の差
            d1 = [r[5]["中央値"][0] - r[2] for r in rows if r[5]["中央値"][0] is not None and r[4]]
            d2 = [r[5]["中央値"][1] - r[3] for r in rows if r[5]["中央値"][1] is not None and r[4]]
            if d1 and d2:
                out += [f"- pgRouting で行ける駅の、OTP（中央値・帯の上限）− pg：行き 中央値 {statistics.median(d1):+.0f}分、"
                        f"帰り 中央値 {statistics.median(d2):+.0f}分", ""]
    if req_secs:
        out += ["## 等時間線の速さ", "",
                f"- {len(req_secs)} 回。1回（帯 {STEP}分刻み）の中央値 {statistics.median(req_secs):.2f}秒、最大 {max(req_secs):.2f}秒",
                "- メモリは `docker stats --no-stream` で、問い合わせの前後に見る（README）", ""]
    path = HERE.parent / "results" / "otp_reach_compare.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"書いた：{path}")


if __name__ == "__main__":
    main()
