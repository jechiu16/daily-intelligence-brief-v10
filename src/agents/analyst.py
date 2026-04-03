from __future__ import annotations
"""Sonnet 首席分析師 — 第一次分析，產生 inference_chain。"""

import json
import re
import logging

import anthropic

from src.config import ANTHROPIC_API_KEY, MISSING_DATA, SONNET_MODEL
from src.prompts.analyst_system import ANALYST_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _build_user_message(assembled_context: dict) -> str:
    """將 assembled_context 轉為 LLM 輸入文字。"""
    packages = assembled_context.get("packages", {})
    coverage = assembled_context.get("coverage_score", 0)
    gaps = assembled_context.get("data_gaps", [])

    lines = []

    # 覆蓋率警告永遠放第一行
    warning = packages.get("coverage_warning", "")
    if warning:
        lines.append(warning)
    lines.append(f"📊 總覆蓋率：{coverage:.0%}")
    lines.append("═" * 50)

    # 行事曆
    lines.append("## 【行事曆上下文】")
    cal = packages.get("calendar_package", {})
    lines.append(json.dumps(cal, ensure_ascii=False, default=str))

    # 市場數據
    lines.append("\n## 【市場數據全覽 + 品質標記】")
    data = packages.get("data_package", {})
    lines.append(json.dumps(data, ensure_ascii=False, default=str))

    # 量化信號
    lines.append("\n## 【量化信號】")
    quant = packages.get("quant_package", {})
    lines.append(json.dumps(quant, ensure_ascii=False, default=str))

    # 歷史類比
    lines.append("\n## 【歷史類比】")
    hist = packages.get("historian_package", {})
    lines.append(json.dumps(hist, ensure_ascii=False, default=str))

    # 輿情
    lines.append("\n## 【輿情信號】")
    sentiment = packages.get("sentiment_package", {})
    lines.append(json.dumps(sentiment, ensure_ascii=False, default=str))

    # 地緣政治
    lines.append("\n## 【地緣政治 + TGRI】")
    geo = packages.get("geopolitical_package", {})
    lines.append(json.dumps(geo, ensure_ascii=False, default=str))

    lines.append("\n" + "═" * 50)

    # 記憶層
    lines.append("\n## 【L1 昨日張力】")
    lines.append(json.dumps(packages.get("l1_context", {}), ensure_ascii=False, default=str))

    lines.append("\n## 【L2 近7日市場結構】")
    lines.append(json.dumps(packages.get("l2_context", {}), ensure_ascii=False, default=str))

    lines.append("\n## 【L3 Active Theses】")
    lines.append(json.dumps(packages.get("l3_context", {}), ensure_ascii=False, default=str))

    lines.append("\n## 【L4 知識歷史】")
    lines.append(json.dumps(packages.get("l4_context", {}), ensure_ascii=False, default=str))

    lines.append("\n## 【L5 Scorecard 近30天】")
    lines.append(json.dumps(packages.get("l5_context", {}), ensure_ascii=False, default=str))

    if gaps:
        lines.append(f"\n## 【已知數據缺口】")
        lines.append(", ".join(gaps))

    lines.append("\n\n請輸出今日分析的 JSON。")
    return "\n".join(lines)


def _parse_json_from_text(raw_text: str):
    """從 LLM 回傳文字中提取並解析 JSON。"""
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", raw_text)
    if match:
        return json.loads(match.group(1).strip())
    start = raw_text.find('{')
    end = raw_text.rfind('}')
    if start != -1 and end != -1:
        return json.loads(raw_text[start:end+1])
    raise json.JSONDecodeError("No JSON object found", raw_text, 0)


def run_analyst(assembled_context: dict) -> dict:
    """呼叫 Sonnet 第一次分析，回傳 analysis JSON。

    修正：max_tokens 提升至 12000，並在 JSON 解析失敗時加入一次重試，
    避免因截斷或非法字符導致 inference_chain 全空的問題。
    """
    client = _get_client()
    user_msg = _build_user_message(assembled_context)

    logger.info(f"Analyst: calling {SONNET_MODEL}, input ~{len(user_msg)//4} tokens")

    def _call_api(messages: list) -> str:
        response = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=12000,
            system=ANALYST_SYSTEM_PROMPT,
            messages=messages,
        )
        return response.content[0].text.strip()

    try:
        raw_text = _call_api([{"role": "user", "content": user_msg}])
        try:
            analysis = _parse_json_from_text(raw_text)
        except json.JSONDecodeError as e:
            logger.warning(f"Analyst JSON parse error (attempt 1): {e} — retrying")
            # 重試：明確要求只輸出合法 JSON
            retry_suffix = (
                "\n\n[系統提示：上次輸出的 JSON 無法解析。"
                "請重新輸出，只輸出純 JSON 物件，"
                "不要任何說明文字、前言或 markdown 標記。]"
            )
            raw_text = _call_api([
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": raw_text},
                {"role": "user", "content": retry_suffix},
            ])
            analysis = _parse_json_from_text(raw_text)

        logger.info(
            f"Analyst: got {len(analysis.get('inference_chain', []))} inferences, "
            f"regime={analysis.get('regime', {}).get('current', 'unknown')}"
        )
        return analysis

    except json.JSONDecodeError as e:
        logger.error(f"Analyst JSON parse error (attempt 2, giving up): {e}")
        return _fallback_analysis(str(e))
    except Exception as e:
        logger.error(f"Analyst API error: {e}")
        return _fallback_analysis(str(e))


def _fallback_analysis(error: str) -> dict:
    """API 失敗時的最小結構，讓 pipeline 能繼續。"""
    return {
        "regime": {
            "current": MISSING_DATA,
            "confidence": 0.0,
            "day_count": 0,
            "supporting_data": [],
            "contrary_signals": [],
        },
        "core_tension": MISSING_DATA,
        "inference_chain": [],
        "thesis_updates": [],
        "new_thesis_candidates": [],
        "compass": [],
        "question_for_devil": MISSING_DATA,
        "data_gaps_affecting_analysis": [],
        "raw_confidence_adjustments": {},
        "_error": error,
    }
