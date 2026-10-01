# 件数を増やしたときのベクトル検索（Issue [#9](https://github.com/ex-day/poc/issues/9)）

[#1](https://github.com/ex-day/poc/issues/1) で固まりつつある方式（Discovery ごとに1本のベクトル、Ruri v3）を、件数を増やしたときにも使えるかを確かめる一式。#1 の「ベクトルを MVP に入れるかは、件数を増やしたときの速さを見てから決める」を受けたもの。

## 中身

| ファイル | 役割 |
|---|---|
| `bench_scale.py` | 件数ごとに、読み込み・全件比較・HNSW の作成と検索・絞り込み（iterative scan、部分インデックス）を測り、`results/` に表（.md）と生データ（.json）を書く |
| `gen_texts.py` | `--vectors model` 用に、架空の Discovery の文章（名前＋対象＋観点＋わかってきたこと）と問いを量産する |
| `results/` | 計測結果 |

テーブルは `poc_scale` スキーマに作り、毎回作り直す（#1 の `poc` スキーマには触らない）。

## 測るもの

| 項目 | 内容 |
|---|---|
| 全件比較 | インデックスを使わずに全件と比べる。結果を正解（真の上位10件）として、HNSW の再現率を出す |
| HNSW | 作成時間・サイズ、`hnsw.ef_search`（40／100／200）ごとの応答時間（p50／p95）と再現率 |
| 絞り込み | `WHERE grp < k`（全体の k 割が残る。例：成立済みの Discovery だけを探す、場所で絞る、の代わり）を付けたとき。`hnsw.iterative_scan`（pgvector 0.8 系）の off／relaxed_order／strict_order と、絞り込みと同じ条件で作った**部分インデックス**を比べる |
| 不足 | 絞り込みで、10件に届かなかった問いの割合。HNSW は先に近い候補を `ef_search` 件だけ取ってから絞り込むため、残る割合が小さいと候補が足りなくなる |
| 埋め込みの作成 | `--vectors model` のときだけ。量産した文章を埋め込む時間 |

## ベクトルの作り方

- `--vectors synthetic`（既定）：合成ベクトル。「共通の方向＋話題のまとまり＋ゆらぎ」で作り、無関係どうしのコサイン類似度 ≒ 0.75、同じ話題どうし ≒ 0.90 にしてある（#1 で見た「点数が 0.8 前後に固まる」に寄せる）。モデルのダウンロードが要らない。**速さとインデックスの振る舞いを見るためのもので、検索の精度（正解との一致）は表さない**
- `--vectors model`：`gen_texts.py` の文章を `EXDAY_MODEL` のモデルで埋め込む。実際の埋め込みの分布での再現率と、埋め込みの作成時間が測れる

合成ベクトルは話題のまとまりがはっきりしているぶん、HNSW の再現率は実際の埋め込みより高めに出る可能性がある。`--vectors model` で同じ表を作って比べる。

## 使い方

DB は #1 と同じ（リポジトリのルートで `docker compose up -d`）。Python の環境は `vector/` のもの（`vector/README.md`）を使う。

```bash
cd vector/scale
python bench_scale.py                                   # synthetic・256次元・1千／1万／10万件
python bench_scale.py --dim 384                         # Ruri v3 70m 相当の次元
python bench_scale.py --sizes 1000000 --queries 50 --tag posts   # コメント（投稿）も対象にした場合の規模の目安

# 実際のモデルで（Mac 等、モデルをダウンロードできる環境で）
EXDAY_MODEL=cl-nagoya/ruri-v3-30m python bench_scale.py --vectors model --sizes 1000 10000 100000 --env "MacBook Pro（…）・Docker"
```

10万件の埋め込みは、CPU で1件あたり数十ミリ秒として1時間前後かかる見込み。まず1万件までで試す。

主なオプション：`--m`・`--ef-construction`（HNSW の作り方）、`--ef-search`、`--filter-tenths`（絞り込みで残す割合。既定 1 2 5 割）、`--filter-ef`（絞り込みのときの ef_search。既定 40）、`--maintenance-work-mem`（インデックス作成に使うメモリ。インデックスがこれに収まらないと作成が遅くなる）。

## 結果

[results/](results/) を参照。考察は Issue [#9](https://github.com/ex-day/poc/issues/9) に残す。
