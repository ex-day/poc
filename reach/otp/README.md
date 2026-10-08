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
| 駅（stops） | `reach.station` の駅のまとまり（group_code）ごとに1つ。座標はまとまりの中の駅（ホームの中ほど）の重心。同じまとまりの中の乗換で歩く分は無し（#5 と同じ考え方） |
| 系統（trips） | 路線ごとに、端（行き止まり）どうしを結ぶ最短の経路を1系統（1路線15系統まで）。環状線は1周を1系統。上り・下りの両方 |
| 駅間の時間 | `reach.edge.cost`（距離 ÷ 表定速度。区間の上書きを含む）を足していく |
| 本数 | frequencies.txt。新幹線・在来線・路面 10分、地下鉄 5分、モノレール・新交通 8分、ケーブル 20分おき（05:00〜24:00）。`exact_times=1`（決まった時刻に出る列車） |
| 範囲 | 経度・緯度の四角（既定 `138.4,34.8,140.9,37.2`、環境変数 `EXDAY_GTFS_BBOX`）の中だけ。外に出る系統は中の部分だけ |
| 運行日 | 2026〜2027年の毎日 |

pgRouting との違いとして出そうなこと：

- 待ち：pgRouting は乗り始め5分・乗換10分の固定。OTP は時刻表どおりに待つ。出発時刻で待ちが変わるので、compare_otp.py は2分ずつずらした5回の平均を取る
- 乗換：同じまとまりの中は OTP の transferSlack（既定2分）だけ。まとまりの間（例：東京と大手町）は、OTP は OSM の道を歩いて乗り換える。pgRouting はまとまりの間の乗換をしない
- 種別：急行・快速は無い（#5 と同じ。各停の表定速度だけ）
- 新幹線：表定速度（150km/h）が短い区間では速すぎる。東京→新横浜が約10分になる（実際は18分ほど）。#5 と同じ「早く出す」ほうの誤差

## ライセンス

- OSM：© OpenStreetMap contributors（ODbL）。Geofabrik から取得
- N02：国土数値情報（鉄道データ）
- OpenTripPlanner：LGPL-3.0（Docker イメージをそのまま使う）

## 仮 GTFS の作り方を変えた理由（最初の比較から）

最初は、駅を路線ごとの停留所（ホームの中ほど）にし、`exact_times` を付けずに出した。OTP の所要時間を歩き・待ち・乗車に分けると、次のことが分かった。

- 乗車時間は pgRouting と同じ（例：新横浜→宇都宮で、OTP の乗車 54分 ＝ t1 69分 − 待ち5分 − 乗換10分）。仮 GTFS は N02 と表定速度をそのまま写せている
- 待ち：`exact_times=0` だと、OTP は乗るたびに間隔そのもの（10分おきなら10分）を待つ。乗換1回で 20〜22分（pgRouting は 15分）。待ちが重いので、横浜線に乗らず新横浜から菊名まで 1.6km 歩く経路まで選ばれた
- 乗換の歩き：路線ごとの停留所だと、東京駅の新幹線から京葉線・総武線へ 775〜1119m（13〜17分）歩いた

そこで、駅のまとまりごとに1つの停留所にし、`exact_times=1` にした。

