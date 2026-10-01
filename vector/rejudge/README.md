# AI の再判定と人の判定の一致（Issue [#12](https://github.com/ex-day/poc/issues/12)）

[#9](https://github.com/ex-day/poc/issues/9) で決めた構成は「ベクトルで候補を拾い、AI で再判定し、提案として出す」。本フォルダでは、このうち AI の再判定が人の判定とどこまで一致するかを測る。

## 中身

| ファイル | 役割 |
|---|---|
| `build_pairs.py` | #9 で人が関連度を付けた100件から、AI に渡す組（問い × 記事の題名・冒頭）を作り、`data/pairs.jsonl` に書く。**人の判定・仮判定は入れない** |
| `prompts/base.md` | 判定の指示（関連度 0〜3 の意味、理由の種類、出力の形） |
| `prompts/criteria.md` | 「基準あり」のときに足す、人の判断基準（`../accuracy/README.md` の「関連度の付け方」を、#9 の100件の具体例を除いて書き直したもの） |
| `results/judge_*.jsonl` | AI の判定（関連度・理由の種類・理由） |
| `score.py` | 人の判定との一致を数え、`results/score.md` を書く |
| `data/` | 組（記事の冒頭を含むため、リポジトリには入れない） |

## やり方

- 判定役は、人の判定を見せないサブエージェント（Claude Code のサブエージェント。API の契約前のため、#2 と同じく Claude が判定役をする）。入力ファイル以外を読まないように指示した。
- 条件：Haiku・Sonnet × 基準なし・基準あり の4通り。各100件を1回ずつ。
- 比べる相手：人の判定（`../accuracy/results/review_ruri-v3-30m.csv` の「判定（0〜3）」）。

```bash
cd vector/rejudge
python build_pairs.py      # ../accuracy/data/wikipedia_kanto.jsonl が必要（../accuracy/fetch_wikipedia.py）
# AI の判定（results/judge_*.jsonl）を作る（サブエージェント、または API で）
python score.py
```

## 結果

[results/score.md](results/score.md) を参照。考察は Issue #12 に残す。

### 読むときの注意

- **人の判定は、Claude の仮判定（題名だけから推測）を見ながら付けたもの**（仮判定を参考に、違うところを直す手順だった）。そのため、仮判定との一致は高めに出ている。仮判定は比較の参考にとどめる。
- 記事は冒頭の最大300字だけを渡している。本文に書いてあることでも、冒頭になければ AI は根拠にできない（例：倉庫が赤れんが造りか、庭園に古い建物があるか）。
- 人の判定は1人分。各条件1回だけの実行で、揺れは測っていない。
- 「基準あり」の基準は、#9 の同じ100件を判定した人の考え方から作った。具体例は除いたが、同じデータで評価しているので、効果は高めに出ている可能性がある。
