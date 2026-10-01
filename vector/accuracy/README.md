# 件数が増えたときの検索精度（Issue [#9](https://github.com/ex-day/poc/issues/9) の項目3）

[#1](https://github.com/ex-day/poc/issues/1) の正解付きのデータ（Discovery 23件・問い41問）に、紛れ込み用の文章を 0・1千・1万件と混ぜる。件数が増えたときに、次のことがどう変わるかを見る。

- 並びの良さ（nDCG@5）
- 候補の決め方ごとの、何が紛れ込むか

速さは [`../scale/`](../scale/) で測った。ここでは速さではなく「何が出てくるか」を見るため、検索は numpy の全件比較で行う（DB は使わない）。

## 中身

| ファイル | 役割 |
|---|---|
| `fetch_wikipedia.py` | 紛れ込み用に、Wikipedia（日本語版）の神奈川・東京あたりの地点記事の冒頭を集め、`data/wikipedia_kanto.jsonl` に書く |
| `eval_accuracy.py` | 正解付きのデータと紛れ込みを混ぜて評価し、`results/` に表（.md）、人が確かめる一覧（.csv）、生データ（.json.gz）を書く |
| `data/` | 集めた記事と、作った埋め込み。**リポジトリには入れない**（.gitignore） |

## 紛れ込みの文章について

- 実在の場所・題材の文章なので、紛らわしさが実際に近い。#1 の Discovery と同じ地域（横浜など）の記事も多く含まれる。
- 記事の本文は CC BY-SA のため、リポジトリには入れない。入れるのは取得のスクリプトと、結果に出る記事の題名・id・点数だけ。
- #1 の Discovery と同じものを指す記事は、紛れ込みから外す。題名に、Discovery の名前か対象（例：氷川丸、山下公園）を含む記事が該当する。正解を付けていないのに、実際には関係があるため。
- それでも、関係がある記事は残る（例：横浜港の歴史を述べた記事）。そこで、上位5件に入った紛れ込みを `results/review_*.csv` に出し、人が関連度（0〜3）を付けて確かめる。

## 比べる候補の決め方

| 決め方 | 内容 |
|---|---|
| 上位〇件 | 点数の高い順に k 件（1・3・5・10） |
| 固定のしきい値 | 点数が t 以上。紛れ込み0件のときに F1 がいちばん良い t を選び、件数を増やしたときもそのまま使う（固定のしきい値が件数の増加に耐えるかを見る） |
| 1位との差 | 1位の点数 − δ 以上（最大10件） |
| 上位5件かつしきい値以上 | 2つの組み合わせ |

数え方：関連度2以上を正解、0 と紛れ込みを不正解とし、1 はどちらにも数えない。

## 使い方（Mac 等、Wikipedia とモデルに接続できる環境で）

Python の環境は `vector/` のもの（`vector/README.md`）を使う。追加のライブラリは要らない。

```bash
cd vector/accuracy
python fetch_wikipedia.py                       # 既定で最大 12,000 記事。API の間隔をあけるため数十分かかる。止めても続きから取れる
EXDAY_MODEL=cl-nagoya/ruri-v3-30m python eval_accuracy.py --device cpu --env "MacBook Pro（M4 Pro）"
EXDAY_MODEL=cl-nagoya/ruri-v3-70m python eval_accuracy.py --device cpu --env "MacBook Pro（M4 Pro）"   # モデルを比べるとき
```

埋め込みは `data/emb_<モデル>.npz` に取っておくので、2回目以降は速い。

## 結果

[results/](results/) を参照。考察は Issue [#9](https://github.com/ex-day/poc/issues/9) に残す。
