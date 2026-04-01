"""Devil's Advocate — 只看 data_package，獨立產生 3-6 個攻擊。"""

import json
import logging
import re

import anthropic

from src.config import ANTHROPIC_API_KEY, SONNET_MODEL
from src.prompts.devils_advocate_system import DEVILS_ADVOCATE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def run_devils_advocate(data_package: dict) -> dict:
    """只接收 data_package，不接收 Sonnet 結論。關鍵隔離。"""
    client = _get_client()

    user_msg = (
        "以下是今日的原始市場數據包，請提出 3-6 個攻擊性論點：\n\n"
        + json.dumps(data_package, indent=2, ensure_ascii=False, default=str)
        + "\n\n請輸出攻擊清單 JSON。"
    )

    logger.info(f"Devil's Advocate: calling {SONNET_MODEL}")

    try:
        response = _get_client().messages.create(
            model=SONNET_MODEL,
            max_tokens=6000,   # 提升：6 個攻擊每個需要詳細論述
            system=DEVILS_ADVOCATE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw_text = response.content[0].text.strip()

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
        return {"attacks": [], "_error": str(e)}
    except Exception as e:
        logger.error(f"Devil's Advocate API error: {e}")
        return {"attacks": [], "_error": str(e)}
