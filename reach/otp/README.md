# OpenTripPlanner で到達圏を出してみる（Issue #7）

時刻表を使わずに、pgRouting（#5・#23）と同じことが OpenTripPlanner（OTP）だけでできるかを確かめる。

- 電車：N02 と表定速度（`reach.edge.cost`）から**仮の GTFS** を作り、OTP に読ませる。実在のダイヤではない
- 歩き：OTP は OSM の道で歩く。#23 の N13 の結果と比べる
- 比べる相手：`reach.reachable`（t1・t2）と `reach.station_spot_walk`（walk_min）

時刻表（ODPT など）を使わない理由：公開されている時刻表の多くはチャレンジ向けの利用条件で、ex-day ではそこまでの正確さもいらない（「だいたい○○分」でよい）。

## 前提

- `reach` の表が入っていること（#5 の手順）。歩きの比較（C）は #23 の表（`reach.station_spot_walk`）も使う
- Docker に 16GB くらいのメモリを割り当てる。`docker info --format '{{.MemTotal}}'` で確かめられる
  - colima：既定は 2GB。`colima stop` のあと `colima start --memory 16 --cpu 4`（DB も止まるので、あとで `docker-compose up -d db`）
  - Docker Desktop：Settings → Resources → Memory
- OTP は `-Xmx12g`（環境変数 `OTP_JAVA_OPTS` で変えられる）
  - 割り当てが足りないと、グラフ作成の途中（OSM の Ways を読むところ）で `Killed` とだけ出て止まる（colima の既定 2GB のときがそうだった）
  - グラフ作成のあいだは DB は要らないので、止めておくと余裕ができる
- 関東の OSM：Geofabrik の `kanto-latest.osm.pbf`（ODbL）

## 手順

poc の直下で行う。

```sh
mkdir -p docker/otp/data
cp ~/Downloads/kanto-*.osm.pbf docker/otp/data/

# 1. 仮 GTFS を作る（ファイル名に gtfs を含めること）
python reach/otp/make_gtfs.py docker/otp/data/exday-pseudo-gtfs.zip

# 2. グラフを作る（数分〜十数分。docker/otp/data/graph.obj ができる）
docker-compose run --rm otp --build --save

# 3. 起動する（http://127.0.0.1:8080）。"Grizzly server running" が出るまで待つ
docker-compose --profile otp up -d otp
docker-compose --profile otp logs -f otp

# 4. 比べる（reach/results/otp_compare.md に書く）
python reach/otp/compare_otp.py

# 止める
docker-compose --profile otp stop otp
```

`docker/otp/data` は `.gitignore` に入れてある（OSM・GTFS・グラフは大きいので）。

## 仮 GTFS の作り方（make_gtfs.py）

| 項目 | 決め方 |
|---|---|
| 駅（stops） | `reach.station`。座標は駅の線（ホーム）の中ほど |
| 系統（trips） | 路線ごとに、端（行き止まり）どうしを結ぶ最短の経路を1系統（1路線15系統まで）。環状線は1周を1系統。上り・下りの両方 |
| 駅間の時間 | `reach.edge.cost`（距離 ÷ 表定速度。区間の上書きを含む）を足していく |
| 本数 | frequencies.txt。新幹線・在来線・路面 10分、地下鉄 5分、モノレール・新交通 8分、ケーブル 20分おき（05:00〜24:00） |
| 範囲 | 経度・緯度の四角（既定 `138.4,34.8,140.9,37.2`、環境変数 `EXDAY_GTFS_BBOX`）の中だけ。外に出る系統は中の部分だけ |
| 運行日 | 2026〜2027年の毎日 |

pgRouting との違いとして出そうなこと：

- 待ち：pgRouting は乗り始め5分・乗換10分の固定。OTP は「何分おき」から待ちを見積もり、乗換は駅の座標どうしを OSM で歩く
- 種別：急行・快速は無い（#5 と同じ。各停の表定速度だけ）
- 新幹線：表定速度（150km/h）が短い区間では速すぎる。東京→新横浜が約10分になる（実際は18分ほど）。#5 と同じ「早く出す」ほうの誤差

## ライセンス

- OSM：© OpenStreetMap contributors（ODbL）。Geofabrik から取得
- N02：国土数値情報（鉄道データ）
- OpenTripPlanner：LGPL-3.0（Docker イメージをそのまま使う）
