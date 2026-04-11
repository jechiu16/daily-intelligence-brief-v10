#!/bin/bash
# DIB Webhook 啟動腳本
# 用法：bash start.sh

DIR="$(cd "$(dirname "$0")" && pwd)"

# 開新視窗跑 ngrok
osascript <<EOF
tell application "Terminal"
    do script "ngrok http --domain=mikki-autophytic-ivy.ngrok-free.dev 8080"
end tell
EOF

# 目前視窗跑 webhook server
cd "$DIR"
python3 run_webhook.py
