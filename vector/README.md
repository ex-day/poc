# pgvector を触ってみる（Issue #1）

完全一致（Subjectの名前）とベクトル検索（意味の近さ）を並べて比べるための一式。**費用はかからない**（pgvectorはPostgreSQLの拡張機能、埋め込みは手元で動くモデル）。

- 検証用の物理テーブルであり、本番のドメイン設計には持ち込まない（#1 の注意点）
- 場所でつながる関連（地理の判定）は #82 で扱う。ここでは意味のつながりを見る

## 中身

| ファイル | 役割 |
|---|---|
| `../../docker/postgres/init/02_schema.sql` | 検証用のテーブル（`poc` スキーマ） |
| `sample_data.json` | Discovery 12件（#1 の A・B・C、#82 の A〜D、#61 のシナリオの既存のDiscovery、場所の離れた廃線跡）と、正解付きの問い6件 |
| `common.py` | DB接続と、文章をベクトルにする処理 |
| `load.py` | データを入れ、埋め込みを作って保存する |
| `search.py` | 4つの方式で検索し、並べて比べる |
| `compare.py` | 複数のモデルを同じ問いで比べ、表にまとめる |
| `../../docker/postgres/init/03_multi_model.sql` | 次元の違うモデルを同じテーブルに入れられるようにする |

## 準備

### 1. テーブルを作る

`docker/postgres/init/` のSQLは、DBが空の初回起動のときしか自動で実行されない。すでに起動済みなので、手で1回流す。

```bash
docker compose exec -T db psql -U postgres -d ex_day_poc < docker/postgres/init/02_schema.sql
```

（リポジトリのルートで実行。何度流しても壊れない）

### 2. Pythonの環境を作る

```bash
cd poc/vector
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`sentence-transformers` は PyTorch も一緒に入るので、数百MBのダウンロードになる。

## 使い方

```bash
python load.py      # 初回はモデル（multilingual-e5-small、数百MB）を自動でダウンロードする
python search.py    # 正解付きの問い6件を実行し、方式ごとの成績を出す
python search.py "赤レンガから歩ける場所"   # 自由な文章で試す
```

### 比べる方式

| 方式 | 内容 |
|---|---|
| `exact` | Subjectの完全一致（表記をそろえて、問いの文章にSubjectの名前が含まれるか。Discovery起点なら共通のSubjectの数） |
| `subject_vec` | 問いとSubjectのベクトルの近さ（Discoveryごとに、いちばん近いSubjectの点数） |
| `disc_name_subj` | 問いとDiscoveryのベクトルの近さ（名前＋対象＋観点をベクトル化） |
| `disc_full` | 同上（名前＋対象＋観点＋わかってきたことをベクトル化） |

結果の `[ ]` は正解の関連度（0〜3）、右の数字は近さの点数（1に近いほど近い）。`nDCG@5` は上位5件の並びがどれだけ正解に近いか（1.00が理想）。

### モデルを比べる（compare.py）

日本語向けの Ruri v3 等と比べる。次元の違うモデルを同じテーブルに入れるため、先に `03_multi_model.sql` を1回流す（リポジトリのルートで）。

```bash
docker compose exec -T db psql -U postgres -d ex_day_poc < docker/postgres/init/03_multi_model.sql
pip install -r requirements.txt   # transformers 等を追加したので入れ直す
python compare.py                 # 既定：基準・e5-small・Ruri v3（30m・70m・130m）
python compare.py cl-nagoya/ruri-v3-310m intfloat/multilingual-e5-base   # モデルを指定
```

初回は各モデルのダウンロードに時間がかかる（小さいもので数十MB、310m で1GB程度）。作り済みの埋め込みは作り直さない。

| モデル | 次元 | 特徴 |
|---|---|---|
| `ngram-baseline` | 384 | 文字の重なりだけ（意味は分からない。比較の基準） |
| `intfloat/multilingual-e5-small` / `-base` / `-large` | 384 / 768 / 1024 | 多言語 |
| `cl-nagoya/ruri-v3-30m` / `-70m` / `-130m` / `-310m` | 256 / 384 / 512 / 768 | 日本語向け（名古屋大学） |

出る数字：

- **nDCG@5**：上位5件の並びが正解にどれだけ近いか（1.00が理想）
- **分離**：正解（関連度2以上）の最低点 − 不正解の最高点。プラスなら点数でしきい値を引ける。e5 のように点数が0.8前後に固まるモデルでは小さくなりやすい
- **読込（秒）・1問（ms）**：モデルの読み込み時間と、1問あたりの検索時間

モデルごとの先頭に付ける決まり（e5：`query:` / `passage:`、Ruri v3：`検索クエリ:` / `検索文書:`）は `common.py` の `MODELS` にまとめてある。一覧にないモデルを試すときは、そこに足す。

Python は 3.10 以降を推奨（macOS 標準の 3.9 でも動く見込みだが、transformers の新しい版が入らない場合がある）。

### 1つのモデルに切り替えて使う

```bash
EXDAY_MODEL=ngram-baseline python load.py
EXDAY_MODEL=ngram-baseline python search.py
```

`ngram-baseline` は、文字の重なりだけで比べる比較用の基準（意味は分からない）。e5 と並べると、「意味が分かる」ことでどれだけ良くなるかが見える。

`EXDAY_MODEL` にモデル名を入れれば、load.py・search.py をそのモデルで動かせる（例：`EXDAY_MODEL=cl-nagoya/ruri-v3-70m python search.py "青柳"`）。

## 問いとDiscoveryの数

Discovery 23件、正解付きの問い30問（種類：名前、名前の一部、別名・言い換え、自由文、場所を越えた意味、Discovery起点）。紛れ込み用に、横浜の他の場所（三溪園、大さん橋、象の鼻パーク、中華街、港の見える丘公園等）と、場所の離れた似たもの（深川めし、小田原城、江ノ電、旧東海道 保土ケ谷宿等）を入れている。

**正解（関連度）はClaudeが作った仮のもの**。気になる問いがあれば `sample_data.json` の `judgments` を直して、もう一度 `python compare.py` を実行する（埋め込みは作り直さないので速い）。

## 見どころ（最初の6問の意図）

| 問い | 見たいこと |
|---|---|
| q1「青柳」 | 完全一致ではバカガイ（青柳の別名）が出ない。ベクトルで出るか |
| q2 生麦の食文化に関連するもの | 「地域史」等の広いSubjectだけ近い小机城址が上位に来ないか |
| q3 P21「桜木町から山下公園まで歩くなら…」 | 似たDiscoveryの案内（F03）。地名が入っていれば完全一致でも拾える |
| q4「昔の貨物線の跡を歩ける場所ってある？」 | 地名のない自由文。場所の離れたアプトの道（廃線跡）も出るか |
| q5「夕方に船がきれいに見える場所」 | 言い換え（船→氷川丸、夕方→ライトアップ） |
| q6「山下公園って、赤い靴に関連する…」 | #61 シナリオの P12 |

## 参考：比較用の基準（ngram-baseline）での結果

クラウドの作業環境で、処理が一通り動くことを `ngram-baseline` で確認した（e5 はそちらの環境ではダウンロードできなかったため未確認）。平均 nDCG@5：exact 0.46、subject_vec 0.59、disc_name_subj 0.59、disc_full 0.84。文字の重なりだけでも、わかってきたことを含めた文章（disc_full）が強い。「赤レンガ」と「赤レンガ倉庫」のように、問いの言葉がSubjectの名前より短いと完全一致では拾えない。e5 でどこまで良くなるかが見どころ。

## 後で試すこと

- 件数を増やしたときの速さ（架空のDiscoveryを1千・1万・10万件入れて、HNSWインデックスの有無で比べる）
- 完全一致＋ベクトル＋場所（#82）を組み合わせた並べ方
