"""国土数値情報 N02（鉄道）の GeoJSON を reach.n02_station・reach.n02_section に入れる（Issue #5）。

使い方：
  python load_n02.py <N02 の UTF-8 フォルダ>
  例：python load_n02.py ~/Downloads/N02-25_GML/UTF-8

接続先は環境変数 EXDAY_DSN（既定：postgresql://postgres:postgres@127.0.0.1:5432/ex_day_poc）。
先に sql/01_schema.sql を流しておくこと。
"""
import json
import os
import sys
from pathlib import Path

import psycopg

DSN = os.environ.get("EXDAY_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/ex_day_poc")


def features(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)["features"]


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    base = Path(sys.argv[1]).expanduser()
    stations = features(next(base.glob("N02-*_Station.geojson")))
    sections = features(next(base.glob("N02-*_RailroadSection.geojson")))

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE reach.n02_station, reach.n02_section RESTART IDENTITY CASCADE")
        with cur.copy(
            "COPY reach.n02_station (rail_class, operator_type, line_name, operator,"
            " station_name, station_code, group_code, geom) FROM STDIN"
        ) as copy:
            for f in stations:
                p = f["properties"]
                copy.write_row((
                    p["N02_001"], p["N02_002"], p["N02_003"], p["N02_004"],
                    p["N02_005"], p["N02_005c"], p["N02_005g"],
                    geojson_to_ewkt(f["geometry"]),
                ))
        with cur.copy(
            "COPY reach.n02_section (rail_class, operator_type, line_name, operator, geom) FROM STDIN"
        ) as copy:
            for f in sections:
                p = f["properties"]
                copy.write_row((
                    p["N02_001"], p["N02_002"], p["N02_003"], p["N02_004"],
                    geojson_to_ewkt(f["geometry"]),
                ))
        cur.execute("SELECT (SELECT count(*) FROM reach.n02_station), (SELECT count(*) FROM reach.n02_section)")
        n_st, n_se = cur.fetchone()
    print(f"駅 {n_st} 件、区間 {n_se} 件を入れた")


def geojson_to_ewkt(geom: dict) -> str:
    if geom["type"] != "LineString":
        raise ValueError(f"想定していない形：{geom['type']}")
    coords = ", ".join(f"{x} {y}" for x, y in geom["coordinates"])
    return f"SRID=6668;LINESTRING({coords})"


if __name__ == "__main__":
    main()
