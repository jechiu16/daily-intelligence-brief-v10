#!/bin/bash
# review.sh <file> [--lang en|zh] — 用 DeepSeek 對單一檔案做攻擊性 code review
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

MODEL="${DEEPSEEK_REVIEW_MODEL:-${DEEPSEEK_FAST_MODEL:-deepseek-v4-flash}}"
BASE_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "缺少 DEEPSEEK_API_KEY，無法呼叫 DeepSeek review。" >&2
  exit 1
fi

echo "▶ DeepSeek adversarial review: $FILE (${SIZE} bytes, model=${MODEL})"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python - "$FILE" "$MODEL" "$BASE_URL" "$PROMPT" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

file_path, model, base_url, prompt = sys.argv[1:5]
with open(file_path, "r", encoding="utf-8", errors="replace") as f:
    code = f.read()

payload = {
    "model": model,
    "messages": [
        {"role": "system", "content": prompt},
        {"role": "user", "content": code},
    ],
    "temperature": 0.2,
    "max_tokens": 3000,
}
request = urllib.request.Request(
    base_url.rstrip("/") + "/chat/completions",
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {os.environ['DEEPSEEK_API_KEY']}",
        "Content-Type": "application/json",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=120) as response:
        data = json.loads(response.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", errors="replace")
    raise SystemExit(f"DeepSeek HTTP {exc.code}: {body[:1000]}")

message = data.get("choices", [{}])[0].get("message", {}).get("content", "")
print(message.strip() or json.dumps(data, ensure_ascii=False, indent=2))
PY
