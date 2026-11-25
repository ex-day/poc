"""関東などの OSM の .osm.pbf から、箱にかかる highway の道（点は全部）と、入口の点を切り出す（Issue #46）。

使い方：
  python extract_highway.py <元の .osm.pbf> <書き出す .osm.pbf> [西 南 東 北]
  例：python extract_highway.py ~/Downloads/kanto-261119.osm.pbf ~/Downloads/kanto-261119-5339-highway.osm.pbf

箱を省くと2次メッシュ 5339（東経139〜140度、北緯35°20'〜36°）。
pyosmium 4.3.2 で確かめた（pip install osmium==4.3.2）。関東（525MB）で約50秒、メモリ 4GB の Linux（arm64）。

- 道：highway のある way のうち、点の1つ以上が箱の中にあるもの。点は箱の外のものも含めて全部（BackReferenceWriter が元のファイルから足す）
- 点：entrance のある node と、railway=subway_entrance・train_station_entrance の node のうち、箱の中のもの
- 点の位置は C++ の側の索引（flex_mem）で持ち、Python では highway・entrance・railway のタグのあるものだけを見る
"""
import sys
import time

import osmium

MESH_5339 = (139.0, 35.0 + 20 / 60, 140.0, 36.0)


def main() -> None:
    if len(sys.argv) not in (3, 7):
        sys.exit(__doc__)
    src, dst = sys.argv[1], sys.argv[2]
    west, south, east, north = map(float, sys.argv[3:7]) if len(sys.argv) == 7 else MESH_5339

    def inbox(loc) -> bool:
        return loc.valid() and west <= loc.lon <= east and south <= loc.lat <= north

    t0 = time.time()
    n_ways = n_entrances = 0
    with osmium.BackReferenceWriter(dst, ref_src=src, overwrite=True) as writer:
        fp = (osmium.FileProcessor(src, osmium.osm.NODE | osmium.osm.WAY)
              .with_locations("flex_mem")
              .with_filter(osmium.filter.KeyFilter("highway", "entrance", "railway")))
        for o in fp:
            if o.is_way():
                if "highway" in o.tags and any(inbox(n.location) for n in o.nodes):
                    writer.add_way(o)
                    n_ways += 1
            elif o.is_node():
                t = o.tags
                if ("entrance" in t or t.get("railway") in ("subway_entrance", "train_station_entrance")) \
                        and inbox(o.location):
                    writer.add_node(o)
                    n_entrances += 1
    print(f"道 {n_ways} 本、入口 {n_entrances} 点（{time.time() - t0:.0f}秒）")


if __name__ == "__main__":
    main()
