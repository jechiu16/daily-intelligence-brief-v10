from __future__ import annotations
"""Devil's Advocate — 只看 data_package，獨立產生 3-6 個攻擊。"""

import json
import logging
import re

from google import genai
from google.genai import types

from src.config import GEMINI_API_KEY, GEMINI_PRO_MODEL
from src.prompts.devils_advocate_system import DEVILS_ADVOCATE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def run_devils_advocate(data_package: dict) -> dict:
    """只接收 data_package，不接收 Sonnet 結論。關鍵隔離。"""
    user_msg = (
        "以下是今日的原始市場數據包，請提出 3-6 個攻擊性論點：\n\n"
        + json.dumps(data_package, indent=2, ensure_ascii=False, default=str)
        + "\n\n請輸出攻擊清單 JSON。"
    )

    logger.info(f"Devil's Advocate: calling {GEMINI_PRO_MODEL}")

    try:
        response = _get_client().models.generate_content(
            model=GEMINI_PRO_MODEL,
            contents=user_msg,
            config=types.GenerateContentConfig(
                system_instruction=DEVILS_ADVOCATE_SYSTEM_PROMPT,
                max_output_tokens=6000,
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )

        try:
            raw_text = response.text or ""
        except Exception:
            parts = response.candidates[0].content.parts if response.candidates else []
            raw_text = "".join(getattr(p, "text", "") or "" for p in parts)

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
        return {"attacks": [], "_error": str(e)}
    except Exception as e:
        logger.error(f"Devil's Advocate API error: {e}")
        return {"attacks": [], "_error": str(e)}
