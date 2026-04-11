#!/bin/bash
# review.sh <file> [--lang en|zh] — 用 Gemini CLI 對單一檔案做攻擊性 code review
#
# 用法：
#   bash scripts/review.sh src/agents/thesis_reviewer.py
#   bash scripts/review.sh src/calibration.py --lang en
set -euo pipefail

FILE=""
LANG="zh"
MAX_BYTES=80000   # 超過 ~80KB 直接拒絕（約 20k tokens）

while [[ $# -gt 0 ]]; do
  case "$1" in
    --lang) LANG="$2"; shift 2 ;;
    *)      FILE="$1"; shift ;;
  esac
done

if [ -z "$FILE" ]; then
  echo "用法: bash scripts/review.sh <file> [--lang en|zh]" >&2
  exit 1
fi

if [ ! -f -- "$FILE" ] || [ ! -r -- "$FILE" ]; then
  echo "找不到或無法讀取: $FILE" >&2
  exit 1
fi

SIZE=$(wc -c < "$FILE")
if [ "$SIZE" -gt "$MAX_BYTES" ]; then
  echo "檔案太大 (${SIZE} bytes > ${MAX_BYTES})，拒絕送出以避免 token 暴雷。" >&2
  exit 1
fi

if [ "$LANG" = "en" ]; then
  PROMPT="File: $FILE

You are a brutally critical senior engineer. Find flaws in this code. Be aggressive. Find: 1) bugs that will explode in production 2) fundamental design errors 3) logical holes 4) things the author clearly didn't think through. No praise first. Just attack."
else
  PROMPT="檔案：$FILE

你是一個極度挑剔的資深工程師，專門找 code 的致命缺陷。請攻擊性地 review 這段程式碼。不要客氣，要找：1) 會在 production 爆炸的 bug 2) 設計上的根本錯誤 3) 邏輯漏洞 4) 你認為這個工程師沒想清楚的地方。用繁體中文，直接講缺點，不要先誇。"
fi

echo "▶ Gemini adversarial review: $FILE (${SIZE} bytes)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
npx --yes @google/gemini-cli --prompt "$PROMPT" < "$FILE"
