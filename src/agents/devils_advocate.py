from __future__ import annotations
"""Devil's Advocate — 只看 data_package，獨立產生 3-6 個攻擊。"""

import json
import logging
import re
import time

from src.config import DEEPSEEK_MODEL
from src.deepseek_client import chat
from src.prompts.devils_advocate_system import DEVILS_ADVOCATE_SYSTEM_PROMPT
from src.telemetry import record_llm_call

logger = logging.getLogger(__name__)


def run_devils_advocate(data_package: dict, today_str: str | None = None) -> dict:
    """只接收 data_package，不接收 DeepSeek 結論。關鍵隔離。"""
    date_header = f"🗓️ 今日分析日期：{today_str}（台灣時間）\n" if today_str else ""
    user_msg = (
        date_header
        + "以下是今日的原始市場數據包，請提出 3-6 個攻擊性論點：\n\n"
        + json.dumps(data_package, indent=2, ensure_ascii=False, default=str)
        + "\n\n請輸出攻擊清單 JSON。"
    )

    logger.info(f"Devil's Advocate: calling {DEEPSEEK_MODEL}")

    try:
        started = time.perf_counter()
        raw_text, usage = chat(
            messages=[{"role": "user", "content": user_msg}],
            system=DEVILS_ADVOCATE_SYSTEM_PROMPT,
            max_tokens=6000,
        )
        elapsed = time.perf_counter() - started
        record_llm_call(
            agent="devils_advocate",
            model=DEEPSEEK_MODEL,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            duration_s=elapsed,
        )

        raw_text = raw_text.strip()

        # 找 ```json ... ``` block
        match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw_text)
        if match:
            raw_text = match.group(1).strip()
        elif not raw_text.startswith("{"):
            # 找裸露 JSON 物件
            m = re.search(r"(\{[\s\S]*\})", raw_text)
            if m:
                raw_text = m.group(1).strip()

        result = json.loads(raw_text)
        attacks = result.get("attacks", [])
        logger.info(f"Devil's Advocate: {len(attacks)} attacks generated")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"Devil's Advocate JSON parse error: {e}")
        return {"attacks": [], "_error": f"json_parse_error: {e}"}
    except Exception as e:
        # google-genai SDK 沒有細化異常層級，保留 Exception 但用 logger.exception 輸出完整 traceback
        logger.exception(f"Devil's Advocate error: {e}")
        return {"attacks": [], "_error": f"error: {e}"}
