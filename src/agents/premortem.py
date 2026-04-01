"""Pre-mortem Protocol — 假設 thesis 失敗，分析原因。"""

import json
import re
import logging

import anthropic

from src.config import ANTHROPIC_API_KEY, SONNET_MODEL
from src.prompts.premortem_system import PREMORTEM_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def run_premortem(active_theses: list[dict], data_package: dict) -> dict:
    """接收 active theses 和今日數據，產生失敗場景。"""
    if not active_theses:
        logger.info("Pre-mortem: no active theses, skipping")
        return {"scenarios": []}

    user_msg = (
        "## Active Theses\n"
        + json.dumps(active_theses, indent=2, ensure_ascii=False, default=str)
        + "\n\n## 今日市場數據\n"
        + json.dumps(data_package, indent=2, ensure_ascii=False, default=str)
        + "\n\n假設以上 theses 已經失敗，請分析每個 thesis 的失敗場景。"
    )

    logger.info(f"Pre-mortem: calling {SONNET_MODEL} for {len(active_theses)} theses")

    try:
        response = _get_client().messages.create(
            model=SONNET_MODEL,
            max_tokens=4000,   # 提升：多個失敗場景各需早期預警指標
            system=PREMORTEM_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw_text = response.content[0].text.strip()
        match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw_text)
        if match:
            raw_text = match.group(1).strip()
        elif not raw_text.startswith("{"):
            m = re.search(r"(\{[\s\S]*\})", raw_text)
            if m:
                raw_text = m.group(1).strip()

        result = json.loads(raw_text)
        logger.info(f"Pre-mortem: {len(result.get('scenarios', []))} scenarios")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"Pre-mortem JSON parse error: {e}")
        return {"scenarios": [], "_error": str(e)}
    except Exception as e:
        logger.error(f"Pre-mortem API error: {e}")
        return {"scenarios": [], "_error": str(e)}
