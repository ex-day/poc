# 件数を増やしたときのベクトル検索（Issue [#9](https://github.com/ex-day/poc/issues/9)）

[#1](https://github.com/ex-day/poc/issues/1) で固まりつつある方式（Discovery ごとに1本のベクトル、Ruri v3）を、件数を増やしたときにも使えるかを確かめる一式。#1 の「ベクトルを MVP に入れるかは、件数を増やしたときの速さを見てから決める」を受けたもの。

## 中身

| ファイル | 役割 |
|---|---|
| `bench_scale.py` | 件数ごとに、読み込み・全件比較・HNSW の作成と検索・絞り込み（iterative scan、部分インデックス）を測り、`results/` に表（.md）と生データ（.json.gz）を書く |
| `gen_texts.py` | `--vectors model` 用に、架空の Discovery の文章（名前＋対象＋観点＋わかってきたこと）と問いを量産する |
| `results/` | 計測結果 |

テーブルは `poc_scale` スキーマに作り、毎回作り直す（#1 の `poc` スキーマには触らない）。

## 測るもの

| 項目 | 内容 |
|---|---|
| 全件比較 | インデックスを使わずに全件と比べる。結果を正解（真の上位10件）として、HNSW の再現率を出す |
| HNSW | 作成時間・サイズ、`hnsw.ef_search`（40／100／200）ごとの応答時間（p50／p95）・再現率・1位・順 |
| 再現率・1位・順 | 再現率：上位10件の集合が全件比較とどれだけ一致するか（Recall@10）。1位：1位が全件比較の1位と一致した割合。順：返ってきた順が距離の小さい順になっていた割合。集合が合っていても順が崩れることがあるため、分けて見る |
| 実行計画 | 測定と同じ SQL・パラメータで `EXPLAIN` し、HNSW が使われたかを記録する。使われなかったもの（件数が少ないとプランナーが全件走査を選ぶ）は表に「（全件走査）」と書く |
| 絞り込み | `WHERE grp < k`（全体の k 割が残る。例：成立済みの Discovery だけを探す、場所で絞る、の代わり）を付けたとき。`hnsw.iterative_scan`（pgvector 0.8 系）の off／relaxed_order／relaxed_order＋再ソート（materialized CTE で取り出してから距離で並べ直す。pgvector の README の方法）／strict_order と、絞り込みと同じ条件で作った**部分インデックス**を比べる |
| 不足 | 絞り込みで、10件に届かなかった問いの割合。HNSW は先に近い候補を `ef_search` 件だけ取ってから絞り込むため、残る割合が小さいと候補が足りなくなる |
| 埋め込みの作成 | `--vectors model` のときだけ。モデルの読み込み、一括（まとめて埋め込む）、1件ずつ（問い1件・Discovery 1件。検索の問いと1件の作り直しに相当）を分けて測る。実行デバイス（`--device`）とバッチサイズを記録する。DB への書き込みと変更検出（content_hash）は含まない |

測り方：条件ごとに同じ数の暖機（`--warmup`、既定10件）をしてから全部の問いを流し、これを `--repeat` 回（既定3回）くり返す。回ごとに条件の順番と問いの順番を入れ替える。p50・p95 は全部の回をまとめて出す（p95 は nearest-rank：昇順で ceil(0.95n) 番目）。JSON（gzip。`results/*.json.gz`）には、問いごとの時間・取得した id・距離、実行計画、実行引数、DB の主な設定、ライブラリの版を残す。

## ベクトルの作り方

- `--vectors synthetic`（既定）：合成ベクトル。「共通の方向＋話題のまとまり＋ゆらぎ」で作り、無関係どうしのコサイン類似度 ≒ 0.75、同じ話題どうし ≒ 0.90 にしてある（#1 で見た「点数が 0.8 前後に固まる」に寄せる）。モデルのダウンロードが要らない。**速さとインデックスの振る舞いを見るためのもので、検索の精度（正解との一致）は表さない**
- `--vectors model`：`gen_texts.py` の文章を `EXDAY_MODEL` のモデルで埋め込む。実際の埋め込みの分布での再現率と、埋め込みの作成時間が測れる

合成ベクトルは話題のまとまりがはっきりしているぶん、HNSW の再現率は実際の埋め込みより高めに出る可能性がある。`--vectors model` で同じ表を作って比べる。

## 使い方

DB は #1 と同じ（リポジトリのルートで `docker compose up -d`）。HNSW の並列作成は共有メモリを `maintenance_work_mem`（既定 1GB）の分だけ使うため、`compose.yaml` で `shm_size: 1gb` にしてある。これより前に作ったコンテナは `docker compose up -d` で作り直す（データは消えない）。足りないと `could not resize shared memory segment ... No space left on device` になる。Python の環境は `vector/` のもの（`vector/README.md`）を使う。

```bash
cd vector/scale
python bench_scale.py                                   # synthetic・256次元・1千／1万／10万件
python bench_scale.py --dim 384                         # Ruri v3 70m 相当の次元
python bench_scale.py --sizes 1000000 --queries 50 --tag posts   # コメント（投稿）も対象にした場合の規模の目安

# 実際のモデルで（Mac 等、モデルをダウンロードできる環境で）。--device で CPU／GPU を明示する
EXDAY_MODEL=cl-nagoya/ruri-v3-30m python bench_scale.py --vectors model --device cpu --sizes 1000 10000 100000 --tag cpu --env "MacBook Pro（機種）・Docker"
EXDAY_MODEL=cl-nagoya/ruri-v3-30m python bench_scale.py --vectors model --device mps --sizes 100000 --tag mps --env "MacBook Pro（機種）・Docker"
```

gen_texts.py の文章は短い（60〜120字程度）ので、実際の長い文章では埋め込みの作成にもっと時間がかかる。また、問いはテンプレートの組み合わせで作るため重複がある（200件中149種類）。速さの計測への影響は小さいが、問いの多様さは実際より低い。

主なオプション：`--m`・`--ef-construction`（HNSW の作り方）、`--ef-search`、`--filter-tenths`（絞り込みで残す割合。既定 1 2 5 割）、`--filter-ef`（絞り込みのときの ef_search。既定 40）、`--maintenance-work-mem`（インデックス作成に使うメモリ。インデックスがこれに収まらないと作成が遅くなる）。

## この計測で言えること・言えないこと

- 言えること：埋め込み済みのベクトルを渡したときの、DB の検索1回の時間（1つの接続から順に流したもの）。HNSW の再現率と、絞り込みで候補が足りなくなる仕組み。
- 言えないこと：
  - API 全体の応答時間（問いの埋め込み、通信、他の条件や JOIN を含まない）
  - 場所で絞ったときの性能。`grp` は意味や場所と相関しない一様な値で、PostGIS の計算や、地理と意味の相関を含まない
  - 同時に検索が来たときの振る舞い（#9 のメモは参考の実験で、CPU の数は変えていない）
  - 検索の精度（問いに合う Discovery が出るか）。#9 の項目3で、#1 の41問等を使って別に見る
- 部分インデックスと「全体のインデックス＋relaxed_order」のどちらがよいかは、件数と残る割合で変わる。件数の不足がないことと、再現率が高いことは別に見る。

## 結果

[results/](results/) を参照。考察は Issue [#9](https://github.com/ex-day/poc/issues/9) に残す。

`results/model_256d*.md` の最初の版（commit 1d578b8・6953c07）は、改修前のスクリプトの結果（実行デバイス未記録、問いごとの結果なし）。改修後のスクリプトで取り直したら置き換える。
