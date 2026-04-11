from __future__ import annotations
"""Pre-mortem Protocol — 假設 thesis 失敗，分析原因。"""

import json
import re
import logging

import anthropic

from src.config import ANTHROPIC_API_KEY, SONNET_MODEL
from src.prompts.premortem_system import PREMORTEM_SYSTEM_PROMPT
from src.telemetry import LLMTimer, record_llm_call

logger = logging.getLogger(__name__)

_client = None

_OUTPUT_TOOL = {
    "name": "emit_premortem",
    "description": "輸出 pre-mortem 失敗場景分析",
    "input_schema": {
        "type": "object",
        "properties": {
            "scenarios": {
                "type": "array",
                "description": "各 thesis 的失敗場景列表",
                "items": {"type": "object"},
            },
        },
        "required": ["scenarios"],
    },
}


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def run_premortem(active_theses: list[dict], data_package: dict, today_str: str | None = None) -> dict:
    """接收 active theses 和今日數據，產生失敗場景。"""
    if not active_theses:
        logger.info("Pre-mortem: no active theses, skipping")
        return {"scenarios": []}

    date_header = f"🗓️ 今日分析日期：{today_str}（台灣時間）\n\n" if today_str else ""
    user_msg = (
        date_header
        + "## Active Theses\n"
        + json.dumps(active_theses, indent=2, ensure_ascii=False, default=str)
        + "\n\n## 今日市場數據\n"
        + json.dumps(data_package, indent=2, ensure_ascii=False, default=str)
        + "\n\n假設以上 theses 已經失敗，請分析每個 thesis 的失敗場景。"
    )

    logger.info(f"Pre-mortem: calling {SONNET_MODEL} for {len(active_theses)} theses")

    try:
        with LLMTimer("premortem", SONNET_MODEL) as _t:
            response = _get_client().messages.create(
                model=SONNET_MODEL,
                max_tokens=4000,
                system=PREMORTEM_SYSTEM_PROMPT,
                tools=[_OUTPUT_TOOL],
                tool_choice={"type": "tool", "name": "emit_premortem"},
                messages=[{"role": "user", "content": user_msg}],
            )
        record_llm_call(
            agent="premortem", model=SONNET_MODEL,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            duration_s=_t.elapsed,
        )

        # 從 tool_use block 直接取 dict（無需 regex）
        result = None
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "emit_premortem":
                result = block.input
                break
        if result is None:
            # fallback: 文字解析
            raw_text = response.content[0].text.strip() if response.content else ""
            m = re.search(r"(\{[\s\S]*\})", raw_text)
            raw_text = m.group(1) if m else raw_text
            result = json.loads(raw_text)

        logger.info(f"Pre-mortem: {len(result.get('scenarios', []))} scenarios")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"Pre-mortem JSON parse error: {e}")
        return {"scenarios": [], "_error": f"json_parse_error: {e}"}
    except anthropic.APIStatusError as e:
        logger.error(f"Pre-mortem API status error {e.status_code}: {e.message}")
        return {"scenarios": [], "_error": f"api_status_{e.status_code}"}
    except anthropic.APIError as e:
        logger.error(f"Pre-mortem API error: {e}")
        return {"scenarios": [], "_error": f"api_error: {e}"}
    except Exception as e:
        logger.exception(f"Pre-mortem unexpected error: {e}")
        return {"scenarios": [], "_error": f"unexpected: {e}"}
