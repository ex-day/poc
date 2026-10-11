# 範囲のある場所の入口・到達点と、駅の出入口（Issue [#31](https://github.com/ex-day/poc/issues/31)）

公園・湖・広い境内のような「範囲のある場所」を、今は1つの点（Wikidata の座標）で持っている。点と実際の入口がずれると、駅からの歩きが長く・短く出る。また、駅の代表点（ホームの重心）が、改札・出入口からずれている（#24・#27 の後続）。OSM から入口・駅の出入口がどのくらい取れ、どれだけ効くかを確かめる。

計算の仕組み（pgRouting か R5 か。platform の DEC-0014）には依らない形にする。到達点は「場所ごとの点の集まり」（`reach.spot_access_point`）で持ち、どちらの仕組みでも、いちばん近い到達点までの歩きを使う。

## 中身

| ファイル | 役割 |
|---|---|
| `extract_osm.py` | OSM の pbf から、範囲（閉じた way・multipolygon）、入口になりうる点（`entrance=*`・門・駅の出入口）、歩ける道と範囲の境界の交わりを抜き出す |
| `analyze.py` | 抜き出したものと、DB の場所・駅を突き合わせて集計し、到達点（`spot_access_point.csv`）と駅の出入口（`station_exit_point.csv`）を書く |
| `../sql/09_access_point.sql` | 到達点の表と、到達点で歩きを出す関数 `reach.walk_from_station_ap` |
| `../sql/10_build_access_walk.sql` | 到達点を道路（N13）の点に結び、駅グループからの歩き（`reach.station_spot_walk_ap`）を前もって計算する |
| `compare_access.py` | pgRouting（N13）で、点と到達点の歩き・寄り道の提案を比べる |
| `compare_access_r5.py` | R5（OSM の道）で、点と到達点、ホームの重心と出入口の歩きを比べる |

## 到達点の決め方

場所ごとに、上から順に使う。

1. **OSM の入口の印**（`osm_entrance`）：範囲の境界の way に乗っている `entrance=*`・`barrier=gate` など（`access=private/no` は除く）
2. **歩ける道との交わり**（`osm_crossing`）：歩ける道（`highway`。motorway 系・`access=private/no`・`foot=no` を除く）が範囲の境界と交わる点
3. **範囲の端**（`area_edge`）：範囲の外周を 100m ごと（1つの範囲で 200 点まで）に区切った点。道路への結び付けで、いちばん近い道路の点に寄る

範囲の決め方：

- 場所（Wikidata の Q 番号）と同じ `wikidata=*` タグの OSM の範囲。同じ Q 番号が複数あれば、いちばん広いもの
- 無ければ、場所の点を含む名前つきの範囲のうち、種類の合うもの（寺なら `amenity=place_of_worship` など）でいちばん狭いもの。種類を見ないと、公園の中の神社が公園に、遺跡の上の寺が遺跡に結ばれた
- 行政界・地名・建物、30km² を超える範囲（国立公園・湾）は使わない

## 手順

### 1. OSM から抜き出し、集計する（DB は要らない。Python と `osmium`・`shapely`）

```sh
pip install osmium shapely
python reach/entrance/extract_osm.py ~/Downloads/kanto-261006.osm.pbf docker/otp/data/issue31/out pass1
python reach/entrance/extract_osm.py ~/Downloads/kanto-261006.osm.pbf docker/otp/data/issue31/out pass2 0/1
```

関東で、1回目が約2分（メモリ 1GB ほど、位置の索引のファイル `loc.idx` が 1GB）、2回目が約1分。2回目は1回目の `loc.idx` を使う。

場所と駅を DB から書き出す（poc の本体のフォルダで）。

```sh
docker-compose exec -T db psql -U postgres -d ex_day_poc -c "COPY (SELECT spot_id, source, ext_id, name, kinds, ST_X(geom) AS lon, ST_Y(geom) AS lat FROM reach.spot) TO STDOUT CSV HEADER" > docker/otp/data/issue31_spot.csv
docker-compose exec -T db psql -U postgres -d ex_day_poc -c "COPY (SELECT station_code, station_name, group_code, line_id, ST_X(geom) AS lon, ST_Y(geom) AS lat FROM reach.station) TO STDOUT CSV HEADER" > docker/otp/data/issue31_station.csv
```

```sh
python reach/entrance/analyze.py docker/otp/data/issue31/out docker/otp/data/issue31_spot.csv docker/otp/data/issue31_station.csv docker/otp/data/issue31/res
```

集計は `res/entrance_osm.md`（`reach/results/entrance_osm.md` に写した）。到達点と駅の出入口の CSV は OSM から作ったもの（ODbL）なので、リポジトリには入れない。

### 2. 到達点で歩きを比べる（pgRouting）

#23 の歩き（06〜08）ができている DB で、poc の本体のフォルダから流す。

```sh
docker-compose exec -T db psql -U postgres -d ex_day_poc < reach/sql/09_access_point.sql
docker-compose exec -T db psql -U postgres -d ex_day_poc -c "COPY reach.spot_access_point (spot_id, source, kind, lon, lat) FROM STDIN CSV HEADER" < docker/otp/data/issue31/res/spot_access_point.csv
docker-compose exec -T db psql -U postgres -d ex_day_poc < reach/sql/10_build_access_walk.sql
EXDAY_MODEL=cl-nagoya/ruri-v3-30m python reach/entrance/compare_access.py
```

結果は `reach/results/access_walk_compare.md`。

### 3. 到達点・駅の出入口で歩きを比べる（R5）

```sh
export JAVA_HOME=$(/usr/libexec/java_home)
python reach/entrance/compare_access_r5.py docker/otp/data/kanto-261006.osm.pbf docker/otp/data/issue31/res/station_exit_point.csv
```

結果は `reach/results/access_walk_r5.md`。

## 結果

### 1. OSM の範囲と入口（関東、`reach/results/entrance_osm.md`）

関東の場所（Wikidata）19,565 件のうち、OSM の範囲と結べたのは 3,826 件（20%）。種類で大きく違う。

| 種類 | 件数 | 範囲あり | 入口の印あり | 印は無いが道と交わる | どちらも無い |
|---|---|---|---|---|---|
| 湖・池・沼 | 28 | 20（71%） | 0 | 11 | 9 |
| 公園・庭園 | 558 | 411（74%） | 21（5%） | 315（77%） | 75（18%） |
| 神社 | 8,197 | 1,322（16%） | 19（1%） | 508（38%） | 795（60%） |
| 寺 | 10,317 | 1,970（19%） | 66（3%） | 549（28%） | 1,355（69%） |
| 博物館・美術館 | 361 | 92（25%） | 16（17%） | 15（16%） | 61（66%） |
| 計 | 19,565 | 3,826（20%） | 123（3%） | 1,407（37%） | 2,296（60%） |

- **OSM の入口の印（`entrance=*`・門）は、ほとんど無い**（範囲のある場所の 3%）。入口を OSM の印だけで決めることはできない
- **公園は範囲が取れ（74%）、道との交わりで入口の代わりが取れる（77%）**。点と入口のずれが効くのは主に公園・湖なので、ここが取れるのは大きい
- 寺・神社は、範囲が取れるのが2割弱で、取れても小さく（境内の多くは 1万m² 未満）、道は境界に沿って走るので交わらない。点のままで困ることは少ない
- 湖は道と交わらない（湖岸の道は境界に沿う。交わるのは橋）。範囲の端の点で代わりにする
- 1万m² 以上の範囲（555件）で、場所の点 → いちばん近い道との交わりは中央値 75m、80パーセンタイル 129m。点 → 範囲の境界は中央値 40m
- Wikidata の座標が範囲の外にあるもの：24件（4%）。印旛沼は、Wikidata の点が OSM の湖（北と西に分かれている）の外にある

### 4. 駅の出入口（`reach/results/entrance_osm.md`）

- 関東の駅グループ 2,223 のうち、OSM の出入口（`railway=subway_entrance`・`railway=train_station_entrance`、3,780点）が 500m 以内にあるのは 1,422（64%）。地下鉄・大きな駅に偏る
- ホームの重心 → いちばん近い出入口は中央値 35m。多くの駅では、重心と出入口のずれは小さい
- **東京ステーションギャラリーは、ホームの重心（003766）から直線 61m**。#27 の「東京駅から歩き16分」は、代表点のずれではなく、N13 の道路への結び付け・道のりの問題らしい（駅の中・駅前広場の歩ける道が N13 に無い）。2・3 で確かめる
