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
| `../../docker/postgres/init/04_topic.sql` | 話題（会話の集合）単位のテーブル |

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
| `disc_topics` | 同上（disc_full に、話題ごとの「何について」の1行と、話題でわかってきたことを足してベクトル化） |
| `disc+topic` | Discovery全体（disc_full）と、そのDiscoveryの話題ごと（1行＋わかってきたこと）のベクトルの、近いほうの点数 |

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

Discovery 23件、正解付きの問い41問（種類：名前、名前の一部、別名・言い換え、自由文、場所を越えた意味、Discovery起点、話題）。紛れ込み用に、横浜の他の場所（三溪園、大さん橋、象の鼻パーク、中華街、港の見える丘公園等）と、場所の離れた似たもの（深川めし、小田原城、江ノ電、旧東海道 保土ケ谷宿等）を入れている。

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

## 話題単位の検証（04_topic.sql）

4つのDiscoveryに、合わせて14の話題を付けてある。話題ごとに「何について話しているか」の1行（型：［場所］の［対象］を［観点］で話している）と、話題でわかってきたことを持つ。

| Discovery | 話題 |
|---|---|
| 散歩ルート（walk_route） | T1 歩き心地、T2 貨物線の跡の歴史、T3 横浜博覧会の臨時列車の思い出、T4 氷川丸の景色、T5 赤い靴の像の由来 |
| 氷川丸（hikawamaru） | T1 歴史、T2 ライトアップの景色、T3 船内の見学 |
| 山下臨港線プロムナード（rinkosen） | T1 貨物線の跡の歴史、T2 横浜博覧会の臨時列車の経路、T3 高架の遊歩道の夜の散歩 |
| 横浜中華街（chinatown） | T1 食べ歩き、T2 関帝廟の由来、T3 春節の行事 |

わざと紛れやすくしてある：同じ対象を別のDiscoveryの話題が別の観点で話している（博覧会の列車：思い出と経路、貨物線の跡：散歩ルートと臨港線、氷川丸の景色：散歩ルートと氷川丸）。

```bash
docker compose exec -T db psql -U postgres -d ex_day_poc < docker/postgres/init/04_topic.sql
cd poc/vector
python compare.py        # load.py が話題も入れて埋め込みを作る（変わったものだけ作り直す）
```

見るところ：

- **方式 disc_full → disc+topic の比較**：話題の単位を足すと、Discoveryの中の一部の話を聞く問いが上に来るか。逆に、関係ない問いで順位が崩れないか
- **話題の当たり**：種類「話題」の問い11問（q31〜q41）で、期待した話題が1位か。`about`（1行だけ）と `about_findings`（1行＋わかってきたこと）を比べる。q36・q37 は同じ博覧会の列車を観点（思い出／経路）で選び分けられるかを見る

正解の見直し：話題を足したことで、散歩ルートの中に関係する話が入った問いの正解を直した（q6 赤い靴：0→2、q4 貨物線の跡・q28 線路跡の遊歩道：1→2、q5 夕方の船：0→1）。q31〜q33 は、話題を足したことでどちらでも正解になる話題を、期待にリストで並べた。

クラウドの作業環境での ngram-baseline の結果：平均 nDCG@5 は disc_full 0.69、disc_topics 0.75、disc+topic 0.73。話題の当たりは about 8/11、about_findings 9/11（文字の重なりだけだと、観点の違いを選び分けにくい）。

## 後で試すこと

- 件数を増やしたときの速さ（架空のDiscoveryを1千・1万・10万件入れて、HNSWインデックスの有無で比べる）
- 完全一致＋ベクトル＋場所（#82）を組み合わせた並べ方
