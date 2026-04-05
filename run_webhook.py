from __future__ import annotations
"""啟動 LINE webhook Flask server。

使用方式：
    python run_webhook.py

環境變數：
    WEBHOOK_PORT  監聽埠號（預設 5000）
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

from src.line_webhook import app  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

if __name__ == "__main__":
    port = int(os.getenv("WEBHOOK_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
