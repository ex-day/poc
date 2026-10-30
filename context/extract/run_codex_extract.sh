#!/bin/bash
# ex-day PoC（Issue #34）：1段目（主張）と2段目（v3 のまとめ）を、Codex（codex exec）で回す
#
# 呼び出しごとに新しい codex exec を起動し、指示のファイル1つだけを空の作業フォルダに置いて渡す。
# 正解やほかの結果は見えない。照らし合わせは、結果を push したあと Claude（Sonnet）で行う。
#
# 使い方（context/extract で）：
#   ./run_codex_extract.sh                         # 既定のモデル（gpt-5.6-luna）
#   CODEX_MODEL=<モデル名> ./run_codex_extract.sh  # モデルを替える（結果のフォルダ名は替わらないので注意）
#   CODEX_BIN=<codex の場所> ./run_codex_extract.sh
#
# 途中で止まっても、同じコマンドでやり直せる（結果のファイルがある呼び出しは飛ばす）。
# codex exec の表示（使われたモデル名、トークン数）は、work/ の <名前>.log に残す。
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
MODEL="${CODEX_MODEL:-gpt-5.6-luna}"
COND=openai-gpt-5.6-luna_codex_whole_reply
export EXDAY_CONVS=tsurumi_38,kohoku_31,chidorihama_15,keyakidai_17

run_one() {  # $1=指示のファイル  $2=結果のファイル
  if [ -s "$2" ]; then return; fi
  local tmp; tmp="$(mktemp -d)"
  # 指示の末尾の「結果の JSON は <絶対パス> に書いて」を、作業フォルダの out.json に置き換える
  sed -E 's#^結果の JSON は `.*` に書いてください。#結果の JSON は `out.json` に書いてください。#' "$1" > "$tmp/prompt.md"
  echo ">> $(basename "$1")"
  "${CODEX_BIN:-codex}" exec -m "$MODEL" --skip-git-repo-check --sandbox workspace-write -C "$tmp" \
    "このフォルダの prompt.md を読み、書かれた指示のとおりに判断して、結果の JSON だけを out.json に書いてください。ほかのファイルやフォルダは読まないでください。" </dev/null >"${2%.out.json}.log" 2>&1
  [ -s "$tmp/out.json" ] || { echo "結果が書かれなかった：$1（作業フォルダ $tmp）" >&2; exit 1; }
  cp "$tmp/out.json" "$2"
  rm -rf "$tmp"
}

# 1段目：主張（prepare は作業フォルダを作り直すので、結果がそろっていれば作り直さない）
if ! ls "work/$COND"/*.out.json >/dev/null 2>&1; then
  $PY run.py prepare "$COND" > /dev/null
fi
for c in ${EXDAY_CONVS//,/ }; do run_one "work/$COND/$c.prompt.md" "work/$COND/$c.out.json"; done
$PY run.py merge "$COND"

# 2段目：v3 のまとめ
$PY synth.py prepare v3luna > /dev/null
for c in ${EXDAY_CONVS//,/ }; do run_one "work/synth_v3luna/$c.prompt.md" "work/synth_v3luna/$c.out.json"; done
$PY synth.py merge v3luna
echo "終わり。results/$COND/ と results/synth_v3_luna/ を commit・push してください（照らし合わせは Claude が行う）。"
