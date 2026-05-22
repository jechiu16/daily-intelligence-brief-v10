from __future__ import annotations
"""Pre-mortem Protocol: analyze how active theses could fail."""

import json
import logging

from src.config import DEEPSEEK_FAST_MODEL
from src.deepseek_client import DeepSeekError, chat_json
from src.prompts.premortem_system import PREMORTEM_SYSTEM_PROMPT
from src.telemetry import LLMTimer, record_llm_call

logger = logging.getLogger(__name__)


def run_premortem(active_theses: list[dict], data_package: dict, today_str: str | None = None) -> dict:
    """Generate failure scenarios for active theses."""
    if not active_theses:
        logger.info("Pre-mortem: no active theses, skipping")
        return {"scenarios": []}

    date_header = f"今日分析日期：{today_str}（台灣時間）\n\n" if today_str else ""
    user_msg = (
        date_header
        + "## Active Theses\n"
        + json.dumps(active_theses, indent=2, ensure_ascii=False, default=str)
        + "\n\n## 今日市場數據\n"
        + json.dumps(data_package, indent=2, ensure_ascii=False, default=str)
        + "\n\n假設以上 theses 已經失敗，請分析每個 thesis 的失敗場景。"
        + '\n\n請只輸出 JSON，格式為：{"scenarios": [...]}'
    )

    logger.info(f"Pre-mortem: calling {DEEPSEEK_FAST_MODEL} for {len(active_theses)} theses")

    try:
        with LLMTimer("premortem", DEEPSEEK_FAST_MODEL) as timer:
            result, usage = chat_json(
                model=DEEPSEEK_FAST_MODEL,
                max_tokens=4000,
                system=PREMORTEM_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
        record_llm_call(
            agent="premortem",
            model=DEEPSEEK_FAST_MODEL,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            duration_s=timer.elapsed,
        )

        logger.info(f"Pre-mortem: {len(result.get('scenarios', []))} scenarios")
        return result

    except json.JSONDecodeError as exc:
        logger.info(f"Pre-mortem JSON parse fallback activated: {exc}")
        return _fallback_scenarios(active_theses, f"json_parse_error: {exc}")
    except DeepSeekError as exc:
        logger.error(f"Pre-mortem API error: {exc}")
        return _fallback_scenarios(active_theses, f"api_error: {exc}")
    except Exception as exc:
        logger.exception(f"Pre-mortem unexpected error: {exc}")
        return _fallback_scenarios(active_theses, f"unexpected: {exc}")


def _fallback_scenarios(active_theses: list[dict], error: str) -> dict:
    """Generate deterministic failure scenarios when the LLM JSON is invalid."""
    scenarios = []
    for thesis in active_theses[:5]:
        tid = thesis.get("id", "")
        title = thesis.get("title", "未命名 thesis")
        invalidators = thesis.get("invalidators", [])
        invalidator_text = ""
        if invalidators and isinstance(invalidators[0], dict):
            invalidator_text = invalidators[0].get("condition", "")
        scenarios.append({
            "thesis_id": tid,
            "title": title,
            "failure_mode": invalidator_text or "核心前提未被後續數據驗證，市場改用其他因子定價。",
            "early_warning": "觀察相關資產是否連續兩個交易日與 thesis 預期方向背離。",
            "probability": "medium",
            "fallback_generated": True,
        })
    return {"scenarios": scenarios, "_error": error, "_fallback": True}
