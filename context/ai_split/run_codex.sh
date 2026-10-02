#!/bin/bash
# ex-day PoC（Issue #14）：seq_run.py の条件を、Codex（codex exec）で最後まで回す
#
# 区切り1つ・会話1本ごとに、新しい codex exec を起動する（前の実行の記憶を引き継がない）。
# 指示のファイル1つだけを空の作業フォルダに置いて渡すので、正解やほかの結果は見えない。
#
# 使い方（context/ai_split で）：
#   python build_inputs.py
#   ./run_codex.sh openai-gpt-5.6-sol_codex-seq10_flow_reply
#   ./run_codex.sh openai-gpt-5.6-sol_codex-seq10_flow_noreply
#   ./run_codex.sh openai-gpt-5.6-sol_codex-seq10r20_flow_reply      # 20件で一括の振り分け直しを入れる
#   EXDAY_CONVS=tsurumi_38,kohoku_31,posts ./run_codex.sh <条件>       # 会話を絞る
#   CODEX_MODEL=<モデル名> ./run_codex.sh <条件>                        # モデルを指定する（省略時は Codex の既定）
#
# 途中で止まっても、同じコマンドでやり直せる（結果のファイルがある区切りは飛ばす）。
set -euo pipefail
cd "$(dirname "$0")"
COND="$1"
PY="${PYTHON:-python3}"
MODEL_ARGS=()
if [ -n "${CODEX_MODEL:-}" ]; then MODEL_ARGS=(-m "$CODEX_MODEL"); fi

run_one() {  # $1=指示のファイル  $2=結果のファイル
  if [ -s "$2" ]; then return; fi
  local tmp; tmp="$(mktemp -d)"
  cp "$1" "$tmp/prompt.md"
  echo ">> $(basename "$1")"
  codex exec "${MODEL_ARGS[@]}" --skip-git-repo-check --sandbox workspace-write -C "$tmp" \
    "このフォルダの prompt.md を読み、書かれた指示のとおりに判定して、結果の JSON だけを out.json に書いてください。ほかのファイルやフォルダは読まないでください。" >/dev/null
  cp "$tmp/out.json" "$2"
  rm -rf "$tmp"
}

for k in 1 2 3 4 5 6 7 8; do
  list="$($PY seq_run.py prepare "$COND" "$k")"
  [ -z "$list" ] && break
  while IFS=$'\t' read -r conv prompt out; do run_one "$prompt" "$out"; done <<< "$list"
  $PY seq_run.py merge "$COND" "$k"
  if [[ "$COND" == *seq10r20* && "$k" == 2 ]]; then
    list="$($PY seq_run.py reseg "$COND" 2)"
    while IFS=$'\t' read -r conv prompt out; do [ -n "$conv" ] && run_one "$prompt" "$out"; done <<< "$list"
    $PY seq_run.py reseg-merge "$COND" 2
  fi
done
echo "終わり。python score.py で集計する。"
