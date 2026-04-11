#!/bin/bash
# review.sh <file> — 用 Gemini CLI 對單一檔案做攻擊性 code review
#
# 用法：
#   bash scripts/review.sh src/agents/thesis_reviewer.py
#   bash scripts/review.sh src/calibration.py --lang en

FILE="$1"
LANG="${2:-zh}"

if [ -z "$FILE" ]; then
  echo "用法: bash scripts/review.sh <file> [--lang en|zh]"
  exit 1
fi

if [ ! -f "$FILE" ]; then
  echo "找不到檔案: $FILE"
  exit 1
fi

if [ "$LANG" = "--lang" ]; then
  LANG="$3"
fi

if [ "$LANG" = "en" ]; then
  PROMPT="You are a brutally critical senior engineer. Your job is to find flaws in this code. Be aggressive. Find: 1) bugs that will explode in production 2) fundamental design errors 3) logical holes 4) things the author clearly didn't think through. No praise first. Just attack."
else
  PROMPT="你是一個極度挑剔的資深工程師，專門找 code 的致命缺陷。請攻擊性地 review 這段程式碼。不要客氣，要找：1) 會在 production 爆炸的 bug 2) 設計上的根本錯誤 3) 邏輯漏洞 4) 你認為這個工程師沒想清楚的地方。用繁體中文，直接講缺點，不要先誇。"
fi

echo "▶ Gemini adversarial review: $FILE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
cat "$FILE" | npx --yes @google/gemini-cli --prompt "$PROMPT"
