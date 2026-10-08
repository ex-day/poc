"""国土数値情報 N13（道路）の GeoJSON を reach.n13_road に入れる（Issue #23）。

使い方：
  python load_n13.py <N13 の GeoJSON>
  例：python load_n13.py ~/Downloads/N13-24_5339_GEOJSON/N13-24_5339.geojson

ファイルが大きい（5339 で 675MB・約190万本）ので、1行ずつ読んで流し込む。
配布の GeoJSON は「1行に1つの道路」の形になっている前提。
先に sql/06_walk.sql を流しておくこと。
"""
import json
import os
import sys
from pathlib import Path

import psycopg

DSN = os.environ.get("EXDAY_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/ex_day_poc")


def features(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            s = line.strip().rstrip(",")
            if s.startswith('{ "type": "Feature"') or s.startswith('{"type":"Feature"'):
                yield json.loads(s)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = Path(sys.argv[1]).expanduser()
    n = 0
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE reach.n13_road RESTART IDENTITY CASCADE")
        with cur.copy(
            "COPY reach.n13_road (kind, road_class, state, layer, width, toll, geom) FROM STDIN"
        ) as copy:
            for f in features(path):
                p = f["properties"]
                g = f["geometry"]
                if g["type"] != "LineString":
                    continue
                coords = ", ".join(f"{x} {y}" for x, y in g["coordinates"])
                copy.write_row((
                    p.get("N13_002"), p.get("N13_003"), p.get("N13_004"),
                    p.get("N13_005"), p.get("N13_006"), p.get("N13_007"),
                    f"SRID=6668;LINESTRING({coords})",
                ))
                n += 1
                if n % 200000 == 0:
                    print(f"{n} 本…", flush=True)
    print(f"道路 {n} 本を入れた")


if __name__ == "__main__":
    main()
