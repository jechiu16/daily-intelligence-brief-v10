from __future__ import annotations
"""Devil's Advocate — 對分析師推論做結構化紅隊挑戰。"""

import json
import logging
import time

from src.config import DEEPSEEK_FAST_MODEL
from src.deepseek_client import chat, extract_json
from src.prompts.devils_advocate_system import DEVILS_ADVOCATE_SYSTEM_PROMPT
from src.telemetry import record_llm_call

logger = logging.getLogger(__name__)


def _build_hypothesis_brief(analysis: dict | None) -> dict:
    """Compress the analyst output so DA can attack claims, not prose style."""
    if not isinstance(analysis, dict):
        return {}

    chain = []
    for inf in analysis.get("inference_chain", [])[:8]:
        if not isinstance(inf, dict):
            continue
        chain.append({
            "id": inf.get("id"),
            "claim": inf.get("claim"),
            "mechanism": inf.get("mechanism"),
            "evidence_keys": [
                ev.get("data_key") for ev in inf.get("evidence", [])
                if isinstance(ev, dict) and ev.get("data_key")
            ],
            "raw_confidence": inf.get("raw_confidence"),
            "invalidation_condition": inf.get("invalidation_condition"),
        })

    return {
        "regime": analysis.get("regime"),
        "core_tension": analysis.get("core_tension"),
        "inference_chain": chain,
        "question_for_devil": analysis.get("question_for_devil"),
    }


def _normalize_attacks(result: dict) -> dict:
    """Keep old DA outputs usable while enforcing the risk-officer schema."""
    attacks = []
    for i, raw in enumerate(result.get("attacks", []) if isinstance(result, dict) else [], 1):
        if not isinstance(raw, dict):
            continue

        attack_id = raw.get("id") or raw.get("attack_id") or f"DA_{i:03d}"
        claim = raw.get("claim") or raw.get("argument") or raw.get("narrative") or ""
        evidence_keys = raw.get("evidence_keys") or []
        if isinstance(evidence_keys, str):
            evidence_keys = [evidence_keys]
        if raw.get("evidence_key"):
            evidence_keys.append(raw["evidence_key"])

        evidence = raw.get("evidence") or []
        if not evidence and evidence_keys:
            evidence = [{"data_key": key} for key in dict.fromkeys(evidence_keys) if key]

        normalized = {
            **raw,
            "id": attack_id,
            "attack_id": attack_id,
            "claim": claim,
            "argument": raw.get("argument") or claim,
            "evidence": evidence,
            "evidence_keys": list(dict.fromkeys(k for k in evidence_keys if k)),
            "severity": raw.get("severity", "medium"),
        }
        attacks.append(normalized)

    return {**(result if isinstance(result, dict) else {}), "attacks": attacks}


def run_devils_advocate(
    data_package: dict,
    analysis: dict | None = None,
    today_str: str | None = None,
) -> dict:
    """Generate attacks against concrete analyst inferences using market data."""
    date_header = f"🗓️ 今日分析日期：{today_str}（台灣時間）\n" if today_str else ""
    hypothesis_brief = _build_hypothesis_brief(analysis)
    user_msg = (
        date_header
        + "以下是分析師推論摘要。請逐條尋找最可能失效的因果鏈節點：\n\n"
        + json.dumps(hypothesis_brief, indent=2, ensure_ascii=False, default=str)
        + "\n\n以下是今日原始市場數據包。每個攻擊必須引用其中的 data_key：\n\n"
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

        result = _normalize_attacks(extract_json(raw_text))
        attacks = result.get("attacks", [])
        logger.info(f"Devil's Advocate: {len(attacks)} attacks generated")
        return result

    except json.JSONDecodeError as e:
        logger.info(f"Devil's Advocate JSON parse fallback activated: {e}")
        return _fallback_attacks(data_package, f"json_parse_error: {e}")
    except Exception as e:
        # 保留 Exception 捕捉並用 logger.exception 輸出完整 traceback。
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
                "id": f"DA_FALLBACK_{len(attacks)+1:03d}",
                "target": label,
                "claim": f"{label}資料仍可解讀為噪音或落後反應，單一指標不足以支撐強因果結論。",
                "narrative": f"{label}資料仍可解讀為噪音或落後反應，單一指標不足以支撐強因果結論。",
                "evidence_key": key,
                "evidence": [{"data_key": key}],
                "evidence_keys": [key],
                "severity": "medium",
                "fallback_generated": True,
            })
    return {"attacks": attacks[:5], "_error": error, "_fallback": True}
