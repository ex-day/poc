# pgRouting の歩きを OSM の道で作り、N13 と比べる（Issue [#46](https://github.com/ex-day/poc/issues/46)）

platform の [#163](https://github.com/ex-day/platform/issues/163)（寄り道の到達圏の歩きのデータ・更新の運用・性能と運用）の最初の確認。MVP の到達圏は pgRouting で計算する（platform DEC-0014 決定1）。#23・#31 で比べたのは主に pgRouting＋N13 と R5＋OSM で、エンジンの差も混ざっていた。ここでは **pgRouting のまま、歩きのグラフだけを N13 から OSM に替えて**、#23・#31 と同じ条件で比べる。

## 条件

- 範囲：2次メッシュ 5339（東経139〜140度、北緯35°20'〜36°。N13-24 のある範囲）
- 駅グループの代表点（ホームの重心）・場所・到達点を、300m 以内のいちばん近い道の点に結ぶ
- 分速80m、片道25分まで、向きは見ない（`walk_precompute.sql` は `08_walk_suggest.sql` の前半と同じ。到達点は `../sql/09`・`10` をそのまま使う）
- 場所・駅・到達点・駅の出入口は #31 の CSV（`issue31_spot.csv`・`issue31_station.csv`・`res/spot_access_point.csv`・`res/station_exit_point.csv`）
- OSM：Geofabrik の関東 `kanto-261119.osm.pbf`（2026-11-19 21:22 UTC 時点）

## 中身

| ファイル | 役割 |
|---|---|
| `base.sql` | 駅（`reach.station`）と場所（`reach.spot`）。#31 の CSV を入れる。N13 用と OSM 用の両方の DB に流す |
| `schema_osm.sql` | OSM の歩きのグラフの表。名前と列は `06_walk.sql` にそろえ、07〜10 の計算をそのまま使う |
| `load_osm_walk.py` | OSM の歩ける道を交差点で区切って、点と辺を入れる |
| `build_osm.sql` | 小さな島を除き、索引を付け、駅グループ・場所を道の点に結ぶ（07 と同じ結び方） |
| `walk_precompute.sql` | 駅グループから片道25分までの歩きを前もって計算する（08 の前半） |
| `exit_walk.sql`・`exit_walk_build.sql` | 駅の代表点を OSM の出入口にしたときの歩き（出入口ごとにたどり、いちばん短いもの） |
| `compare_osm_walk.py` | 2つの DB を比べ、`../results/osm_walk_compare.md` を書く |

### 歩ける道（OSM）

`highway` のうち、`motorway`・`motorway_link`・建設中・計画・廃止などを除く。`foot=no` と、`foot=yes` 等の無い `access=private/no` も除く（#31 の歩ける道と同じ考え方）。幹線道路（`trunk`・`primary` 等）は歩道がある前提で歩けるとみなす。歩ける道の2本以上が使う点（交差点）と道の端で区切り、区切った区間1つを辺1本にする。

**小さな島を除く**：OSM には、ほかの道とつながっていない短い道の集まり（駅の中の通路・ホーム、敷地の中の道など）がある。1回目の比べでは、大宮・川口・八木崎などの駅グループがこの島に結ばれ、10分で2〜17点にしか歩けず、近くの場所に「別の駅から20分」と出た。`pgr_connectedComponents` で、つながっている点が100未満の集まり（6,340・約1.9万点）を消してから結ぶ。

## 手順

DB は PostgreSQL＋PostGIS＋pgRouting（`../sql/09` が `vector` を使わないので pgvector は無くてよい）。N13 用（ここでは `poc_n13`）と OSM 用（`poc_osm`）の2つの DB を作り、どちらにも `CREATE SCHEMA reach` と拡張（postgis・pgrouting）を入れておく。

OSM は、関東の pbf から、メッシュ 5339 の箱にかかる `highway` の道（点は全部）と入口の点を切り出して使った（pyosmium の `BackReferenceWriter`。関東の全体で約50秒、63MB）。

```sh
D=docker/otp/data
# N13
psql poc_n13 -f reach/osmwalk/base.sql
psql poc_n13 -c "\copy reach.station (station_code, station_name, group_code, line_id, lon, lat) FROM '$D/issue31_station.csv' CSV HEADER"
psql poc_n13 -c "\copy reach.spot (spot_id, source, ext_id, name, kinds, lon, lat) FROM '$D/issue31_spot.csv' CSV HEADER"
psql poc_n13 -f reach/sql/06_walk.sql
EXDAY_DSN=postgresql:///poc_n13 python reach/load_n13.py ~/Downloads/N13-24_5339_GEOJSON/N13-24_5339.geojson
psql poc_n13 -f reach/sql/07_build_walk.sql
psql poc_n13 -f reach/osmwalk/walk_precompute.sql
psql poc_n13 -f reach/sql/09_access_point.sql
psql poc_n13 -c "\copy reach.spot_access_point (spot_id, source, kind, lon, lat) FROM '$D/issue31/res/spot_access_point.csv' CSV HEADER"
psql poc_n13 -f reach/sql/10_build_access_walk.sql

# OSM（base.sql と CSV は同じ）
psql poc_osm -f reach/osmwalk/schema_osm.sql
EXDAY_DSN=postgresql:///poc_osm python reach/osmwalk/load_osm_walk.py ~/Downloads/kanto-261119-5339-highway.osm.pbf
psql poc_osm -f reach/osmwalk/build_osm.sql
psql poc_osm -f reach/osmwalk/walk_precompute.sql
psql poc_osm -f reach/sql/09_access_point.sql   # と到達点の CSV
psql poc_osm -f reach/sql/10_build_access_walk.sql
psql poc_osm -f reach/osmwalk/exit_walk.sql
psql poc_osm -c "\copy reach.station_exit (group_code, node_id, railway, name, lon, lat, dist_m) FROM '$D/issue31/res/station_exit_point.csv' CSV HEADER"
psql poc_osm -f reach/osmwalk/exit_walk_build.sql

python reach/osmwalk/compare_osm_walk.py postgresql:///poc_n13 postgresql:///poc_osm reach/results/osm_walk_compare.md
```

OSM から作ったグラフ・CSV は ODbL なので、リポジトリには入れない（結果の集計だけを入れる）。

## 作る時間（2 CPU・7GB のクラウドの作業環境、PostgreSQL 16＋PostGIS 3.4＋pgRouting 3.6）

| | 取り込み | グラフ・結び付け | 駅からの歩き（場所の点） | 駅からの歩き（到達点） | 駅の出入口から |
|---|---|---|---|---|---|
| N13 | 16秒（194万本） | 87秒 | 60秒 | 57秒 | — |
| OSM | 79秒（116万本の道 → 265万本の辺） | 12秒（島を除くのを含む） | 3分4秒 | 3分6秒 | 15分（出入口 3,321） |

OSM の駅からの歩きが N13 の約3倍かかるのは、辺が約1.4倍あり、駅のまわりの細い道（`footway` 等）が多いため。

## 結果

詳しくは [`../results/osm_walk_compare.md`](../results/osm_walk_compare.md)。

- **全体の差は小さい**：両方にある（駅グループ, 場所）の組 27,284 で、OSM − N13 の中央値 −0.1分（20%〜80% で −0.8〜+0.4分）。3分以上短くなった組 879、長くなった組 211
- **N13 の大きな不足が直る**：東京ステーションギャラリーは東京駅（003766）から 16.3分 → 1.9分。細い道・参道の寺社が大きく短くなる（羽黒神社 柏から 23分 → 6分、不動寺 西武球場前から 23分 → 6分、高尾山薬王院 23分 → 12分、川口市立美術館 13分 → 2分 など）。25分以内の駅が OSM だけである場所が 139（うち寺社 134）、N13 だけは 35
- **長くなるものもある**：禅福寺（新守谷から 12分 → 23分）など 3分以上長くなった組が 211。OSM で道が切れている・歩けない道として除いたものがある見込み（個別には見ていない）
- **到達点が道に結べるか**：#31 の「到達点 9,818点のうち N13 に結べたのは 5,660点」は、**N13 の道の不足ではなく、到達点が関東の全体にあり、N13 がメッシュ 5339 の分しか無いため**だった。メッシュの中の到達点 5,631点は N13 でもすべて結べている。OSM（メッシュ 5339 の箱で切り出したもの）は 5,685点（N13 で結べず OSM で結べた 30、その逆 5）
- **小さな島は必ず除く**：除かないと、大きな駅が島に結ばれて歩けなくなる（上の「小さな島を除く」）
- **駅の代表点を出入口にすると**：出入口のある駅グループ 1,141 で、出入口 − 重心の中央値 −0.9分（20%〜80% で −1.9〜−0.2分）、3分以上短くなった組 1,802。ただし**ホームから出入口までの駅の中の歩きを足していない**ので、そのぶん短く出る（行ける側に寄る）
- **グラフの大きさ**：メッシュ 5339 で OSM は点 191万・辺 265万（辺の表 594MB、N13 は 435MB）。駅グループの代表点から 2.5km 以内に絞っても辺 246万本（93%）で、都市部ではほとんど小さくならない
