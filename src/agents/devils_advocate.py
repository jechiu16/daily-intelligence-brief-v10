from __future__ import annotations
"""Devil's Advocate — 只看 data_package，獨立產生 3-6 個攻擊。"""

import json
import logging
import time

from src.config import DEEPSEEK_FAST_MODEL
from src.deepseek_client import chat, extract_json
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

    logger.info(f"Devil's Advocate: calling {DEEPSEEK_FAST_MODEL}")

    try:
        started = time.perf_counter()
        raw_text, usage = chat(
            model=DEEPSEEK_FAST_MODEL,
            messages=[{"role": "user", "content": user_msg}],
            system=DEVILS_ADVOCATE_SYSTEM_PROMPT,
            max_tokens=6000,
        )
        elapsed = time.perf_counter() - started
        record_llm_call(
            agent="devils_advocate",
            model=DEEPSEEK_FAST_MODEL,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            duration_s=elapsed,
        )

        result = extract_json(raw_text)
        attacks = result.get("attacks", [])
        logger.info(f"Devil's Advocate: {len(attacks)} attacks generated")
        return result

    except json.JSONDecodeError as e:
        logger.warning(f"Devil's Advocate JSON parse fallback activated: {e}")
        return _fallback_attacks(data_package, f"json_parse_error: {e}")
    except Exception as e:
        # google-genai SDK 沒有細化異常層級，保留 Exception 但用 logger.exception 輸出完整 traceback
        logger.exception(f"Devil's Advocate error: {e}")
        return _fallback_attacks(data_package, f"error: {e}")


def _fallback_attacks(data_package: dict, error: str) -> dict:
    """Generate deterministic attacks if the LLM returns malformed JSON."""
    attacks = []
    for key, label in [
        ("spx", "風險資產"),
        ("vix", "波動率"),
        ("gold", "避險需求"),
        ("us10y", "利率"),
        ("dxy", "美元"),
    ]:
        item = data_package.get(key, {}) if isinstance(data_package, dict) else {}
        value = item.get("price") or item.get("value")
        if value and value != "MISSING_DATA":
            attacks.append({
                "attack_id": f"DA_FALLBACK_{len(attacks)+1:03d}",
                "target": label,
                "narrative": f"{label}資料仍可解讀為噪音或落後反應，單一指標不足以支撐強因果結論。",
                "evidence_key": key,
                "severity": "medium",
                "fallback_generated": True,
            })
    return {"attacks": attacks[:5], "_error": error, "_fallback": True}
