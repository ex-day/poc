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

worktree で行う場合も含め、poc のフォルダ（compose.yaml のあるところ）で行う。zsh ではコマンドの後ろに `# …` を書くとコメントにならず引数として渡るので、コマンドだけを打つ。

1. OSM を置く

```sh
mkdir -p docker/otp/data
cp ~/Downloads/kanto-*.osm.pbf docker/otp/data/
```

2. 仮 GTFS を作る（ファイル名に gtfs を含めること）

```sh
python reach/otp/make_gtfs.py docker/otp/data/exday-pseudo-gtfs.zip
```

3. グラフを作る（10分前後。最後に graph.obj を保存したと出れば成功）

```sh
docker-compose run --rm otp --build --save
```

4. 起動する（http://127.0.0.1:8080）。`Grizzly server running` が出たら Ctrl+C で抜ける

```sh
docker-compose --profile otp up -d otp
docker-compose --profile otp logs -f otp
```

5. 比べる（`reach/results/otp_compare.md` に書く）。DB（#5 の reach の表）が起動していること

```sh
python reach/otp/compare_otp.py
```

6. 止める

```sh
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

## 結果（関東の OSM、仮 GTFS、2026-10）

`reach/results/otp_compare.md`（最初の作り方の結果は `otp_compare_v1-per-line-stops.md`）。

### 電車：選んだ30駅では、OTP と pgRouting の所要時間はほぼ同じ

新横浜から30駅（行き）と、30駅から東京（帰り）を、2分ずつずらした5回の平均で比べた。30駅は `reach.reachable` が行けると判定した駅から選んだので、「pgRouting では行けないが OTP では行ける駅」「判定が逆転する駅」はこの比較では見えない（PR #25 のレビュー）。

| | 差の中央値（OTP − pg） | 範囲 |
|---|---|---|
| 行き | −1.2分 | −20.3〜+3.4分 |
| 帰り | −2.3分 | −17.8〜+3.6分 |

- 乗車時間は同じ。仮 GTFS は pgRouting と同じ `reach.edge.cost` から作っているので、一致するのは作りの上で当然。**現実の所要時間に近いかは、この比較では確かめていない**
- 最初の待ちは、平均すると 5分前後で、pgRouting の固定値と合う
- OTP のほうが短く出るのは、次の2つ。どちらも仮 GTFS の作りから出た値で、**OTP のほうが現実に近いとは言えない**
  - 乗換の待ち：pgRouting は乗換1回10分の固定。OTP は仮ダイヤどおりで、中央値 4分（乗換の多い経路ほど差が開く。田園調布・自由が丘で −5分）。ただし仮ダイヤは、全系統が 05:00 から一定の間隔で出る（発車の位相がそろっている）、間隔は種別ごとに一律、駅の中の移動は無し、という作りで、4分はその結果。現実と比べるなら、路線ごとに発車の位相をずらす、時間帯ごとの間隔にする、平均ではなく安全側（75〜90パーセンタイル）を見る、などが要る
  - 駅のまとまりの間を歩いて乗り換える／降りる：栄町（−18分、王子から歩く）、荒川区役所前（−20分、三ノ輪から歩く）、京成八幡（−13分、本八幡から歩く）、日本橋（東京から歩く）。pgRouting はまとまりの間の乗換をしない（#5 で決めた）
- 1回の問い合わせは 0.07〜0.12秒。ただし「行ける駅の一覧」を出すには駅の数だけ問い合わせが要る。pgRouting は1回（約0.6秒）で全駅が出る

### 歩き：OSM と N13 は、ふつうの組ではほぼ同じ。N13 で遠回りの組は OSM が短い（OSM も正解ではない）

- ランダムな20組：OTP（OSM）のほうが +1〜+6分。ほぼ同じ
- N13 で道のりと直線の差が大きい10組：OSM のほうが 5〜15分短い。N13 に細い道・参道が無いため。#23 の「遠回り」の一部は N13 の粗さだった
- ただし OSM は入れない場所も通る（例：桜田門 → 宮中三殿 9分。皇居の中）。「OSM のほうが実際に近い」と一般化する前に、通れない道（access・私道・門・営業時間）の扱い、駅の改札・出口、Discovery の入口を確かめる必要がある
- 30組は #23 の `station_spot_walk` から選んだ。OTP の歩きで Discovery の候補・代表の駅がどう変わるかは、まだ確かめていない

### 仮 GTFS の網羅性（レビューのあとに検査）

`reach/results/otp_gtfs_coverage.md`（`make_gtfs.py` が書く）。

- 四角の中の駅のまとまり 2,229 のうち **28 が GTFS に入らなかった**。（まとまり, 路線）の組では 2,644 のうち 60 にどの系統も止まらない
  - 大江戸線のほぼ全駅、ユーカリが丘線の全駅：環状に枝が付いた形（6の字）を系統にできていない
  - JR 東北線・東海道線などの一部の駅（東戸塚、平塚、御徒町など）：端の組が多く、上限の15系統で切り捨てた
- **区間の 30.6% に2系統以上が重なる**（最大は東北線の12系統）。どの系統も同じ間隔で走らせているので、重なった分だけ本数が多く、待ちが短く出る
- 上の電車の比較は、この抜けと水増しのある仮 GTFS での結果

### 分かったこと

- 時刻表（ODPT など）が無くても、N02 と表定速度から作った仮 GTFS で OTP は動く。選んだ30駅では pgRouting とほぼ同じ所要時間になった
- 仮 GTFS は、駅のまとまりごとに1つの停留所、`exact_times=1` にすること。そうしないと待ちと乗換の歩きが大きく出る（上の「作り方を変えた理由」）
- ただし仮 GTFS には駅の抜けと本数の水増しがあり、OTP の待ちや歩きが現実に近いかも確かめていない

### 今の時点の判断（PR #25 のレビュー）

- この PR は PoC の記録。OTP を採用するかは、まだ決められない（#7 は開いたまま）
- 初期リリースの候補の絞り込みは、#5・#6 の pgRouting を使う
- 歩きは、まず直線で広めに候補を出し、N13・OSM は境界の判定や表示へ段階的に入れる
- OTP は、1つの Discovery への経路の説明や、歩きの経路の補助として、続けて確かめる

### 採用を判断する前の追加実験（#26）

- 仮 GTFS の作り直し：全区間を覆う系統、重なりに合わせた本数、路線ごとの発車の位相
- 複数の出発地・帰着地・時間で、全駅を「pgRouting だけ／OTP だけ／両方／どちらも不可」に分けて比べる
- OTP の等時間線（帯つき、到着時刻からの逆向き）で、全候補を出す時間とメモリを測る
- #6（PR #22）の提案を OTP の時間でやり直し、候補・上位10件・代表の駅の差を出す

---

# 仮 GTFS を直し、全駅の到達判定と等時間線を比べる（Issue [#26](https://github.com/ex-day/poc/issues/26)）

PR #25 のレビューで挙がった、OTP を採用するかを決める前の追加実験。

## 仮 GTFS の作り直し

- 系統：端どうしの最短経路（最大15本）をやめ、路線の辺を重ならないたどりに分ける（`cover_trails`）。どの区間もちょうど1つの系統が通るので、区間ごとの本数は設定した間隔のとおりになる。環状に枝が付いた路線（大江戸線・ユーカリが丘線）も覆う
- 分かれ目：系統の始まり・終わりに、隣の系統のいちばん近い駅を足し、乗り継げるようにする
- 発車の位相：路線・向きごとにずらす（全系統が 05:00 ちょうどに出ないように）
- 網羅性（`reach/results/otp_gtfs_coverage.md`）：抜けたまとまり 28 → **0**、どの系統も止まらない（まとまり, 路線）60 → **3**（N02 でほかの辺とつながっていないホーム：伊勢崎線の押上など。どれもほかの路線では止まる）、2系統以上が重なる区間 30.6% → **0.7%**（最大2）

## 全駅の到達判定と等時間線（compare_otp_reach.py）

OTP の TravelTime API（`/otp/traveltime/isochrone`、試験的な機能）で、行き（出発時刻から）と帰り（期限に着く逆向き）の等時間線を5分刻みの帯でもらい、PostGIS で駅のまとまりの重心がどの帯に入るかを調べる。`reach.reachable` の t1・t2 と比べ、全駅を「両方／pgRouting だけ／OTP だけ／どちらも行けない」に分ける。

- 条件：新横浜 → 東京（10:00、180分、滞在60分）、八王子 → 千葉（09:00、300分、滞在90分）、新宿 → 鎌倉（09:00、240分、滞在60分）
- 発車時刻を0〜8分ずらして5回。OTP の判定は中央値と安全側（80パーセンタイル）
- 帯の上限を使うので、OTP の時間は最大5分長めに出る（「行けない」側に寄せる）

### 手順

worktree（compose.yaml のあるところ）で行う。DB は poc の本体で起動しておく。

1. OSM・設定・仮 GTFS を置く

```sh
mkdir -p docker/otp/data
cp ~/Downloads/kanto-261006.osm.pbf docker/otp/data/
cp reach/otp/otp-config.json docker/otp/data/
python reach/otp/make_gtfs.py docker/otp/data/exday-pseudo-gtfs.zip
```

2. グラフを作る（colima のメモリが 16GB あること：`docker info --format '{{.MemTotal}}'`）

```sh
docker-compose run --rm otp --build --save
```

3. 起動して、`Grizzly server running` が出たら Ctrl+C

```sh
docker-compose --profile otp up -d otp
docker-compose --profile otp logs -f otp
```

4. 比べる。前後でメモリを見る

```sh
docker stats --no-stream
python reach/otp/compare_otp_reach.py
docker stats --no-stream
```

結果は `reach/results/otp_reach_compare.md`。

