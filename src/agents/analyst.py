from __future__ import annotations
"""DeepSeek 首席分析師 — 第一次分析，產生 inference_chain。"""

import json
import logging

from src.config import MISSING_DATA, SONNET_MODEL
from src.deepseek_client import DeepSeekError, chat, chat_json, extract_json
from src.prompts.analyst_system import ANALYST_SYSTEM_PROMPT
from src.telemetry import LLMTimer, record_llm_call

logger = logging.getLogger(__name__)

# ── 強制 JSON 輸出 tool（tool_choice 模式，取代 regex 提取）──────────────────
_OUTPUT_TOOL = {
    "name": "emit_analysis",
    "description": "輸出今日市場分析的結構化 JSON",
    "input_schema": {
        "type": "object",
        "properties": {
            "regime": {
                "type": "object",
                "description": "當前市場 regime 判斷",
            },
            "core_tension": {
                "type": "string",
                "description": "今日核心市場張力（一句話）",
            },
            "inference_chain": {
                "type": "array",
                "description": "推論鏈（INF_001 等）",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "claim": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "object"}},
                        "logic": {"type": "string"},
                        "raw_confidence": {"type": "number"},
                        "dependencies": {"type": "array", "items": {"type": "string"}},
                        "invalidation_condition": {"type": "string"},
                        "mechanism": {"type": "string"},
                        "counterexample": {"type": "string"},
                        "asset_predictions": {
                            "type": "array",
                            "description": "此推論的方向性預測，格式: '{asset}_{direction}'，例: ['gold_up', 'spx_down']。只填方向明確的資產，不確定則留空 []。",
                            "items": {"type": "string"},
                        },
                    },
                },
            },
            "thesis_updates": {
                "type": "array",
                "description": "Active theses 更新",
                "items": {"type": "object"},
            },
            "new_thesis_candidates": {
                "type": "array",
                "description": "新 thesis 候選（新興主題，尚未成為 active thesis）",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "簡短 thesis 標題（中英皆可）"},
                        "rationale": {"type": "string", "description": "為何現在提出，與今日數據的連結"},
                        "initial_confidence": {"type": "number", "description": "初始信心值 0.0-1.0"},
                        "suggested_invalidator": {"type": "string", "description": "具體可量化的終止條件"},
                    },
                    "required": ["title", "rationale"],
                },
            },
            "compass": {
                "type": "array",
                "description": "方向性預測羅盤",
                "items": {"type": "object"},
            },
            "question_for_devil": {
                "type": "string",
                "description": "給 Devil's Advocate 的挑戰問題",
            },
            "data_gaps_affecting_analysis": {
                "type": "array",
                "items": {"type": "string"},
            },
            "raw_confidence_adjustments": {
                "type": "object",
            },
        },
        "required": ["regime", "core_tension", "inference_chain", "compass"],
    },
}


_META_KEYS = {"_source", "_timestamp", "run_id", "fetch_time", "_reduced", "_note"}


def _strip_metadata(obj):
    """遞迴移除 metadata 欄位，保留分析實質，節省 token。"""
    if isinstance(obj, dict):
        return {k: _strip_metadata(v) for k, v in obj.items() if k not in _META_KEYS}
    if isinstance(obj, list):
        return [_strip_metadata(item) for item in obj]
    return obj


def _build_user_message(assembled_context: dict, today_str: str | None = None) -> str:
    """將 assembled_context 轉為 LLM 輸入文字。"""
    packages = assembled_context.get("packages", {})
    coverage = assembled_context.get("coverage_score", 0)
    gaps = assembled_context.get("data_gaps", [])

    lines = []

    # 今日日期永遠放第一行，讓 agent 有明確時間錨點
    if today_str:
        lines.append(f"🗓️ 今日分析日期：{today_str}（台灣時間）")
        lines.append("※ 資料的 observation_date / data_timestamp 若早於今日，代表該數據存在觀測落差，請在引用時注意。")

    # 覆蓋率警告
    warning = packages.get("coverage_warning", "")
    if warning:
        lines.append(warning)
    lines.append(f"📊 總覆蓋率：{coverage:.0%}")
    lines.append("═" * 50)

    def _dump(pkg):
        return json.dumps(_strip_metadata(pkg), ensure_ascii=False, default=str)

    # 行事曆
    lines.append("## 【行事曆上下文】")
    lines.append(_dump(packages.get("calendar_package", {})))

    # 市場數據
    lines.append("\n## 【市場數據全覽 + 品質標記】")
    lines.append(_dump(packages.get("data_package", {})))

    # 量化信號
    lines.append("\n## 【量化信號】")
    lines.append(_dump(packages.get("quant_package", {})))

    # 歷史類比
    lines.append("\n## 【歷史類比】")
    lines.append(_dump(packages.get("historian_package", {})))

    # 輿情
    lines.append("\n## 【輿情信號】")
    lines.append(_dump(packages.get("sentiment_package", {})))

    # 地緣政治
    lines.append("\n## 【地緣政治 + TGRI】")
    lines.append(_dump(packages.get("geopolitical_package", {})))

    lines.append("\n" + "═" * 50)

    # 記憶層
    lines.append("\n## 【L1 昨日張力】")
    lines.append(_dump(packages.get("l1_context", {})))

    lines.append("\n## 【L2 近7日市場結構】")
    lines.append(_dump(packages.get("l2_context", {})))

    lines.append("\n## 【L3 Active Theses】")
    lines.append(_dump(packages.get("l3_context", {})))

    lines.append("\n## 【L4 知識歷史】")
    lines.append(_dump(packages.get("l4_context", {})))

    lines.append("\n## 【L5 Scorecard 近30天】")
    lines.append(_dump(packages.get("l5_context", {})))

    if gaps:
        lines.append(f"\n## 【已知數據缺口】")
        lines.append(", ".join(gaps))

    lines.append("\n\n請輸出今日分析的 JSON。")
    return "\n".join(lines)


def _parse_json_from_text(raw_text: str):
    """從 LLM 回傳文字中提取並解析 JSON。"""
    return extract_json(raw_text)


def run_analyst(assembled_context: dict, today_str: str | None = None) -> dict:
    """呼叫 DeepSeek 第一次分析，回傳 analysis JSON。

    primary: tool_choice 強制結構化輸出（無 regex）
    fallback: 文字解析（tool_use 失敗或 stop_reason != tool_use）
    """
    user_msg = _build_user_message(assembled_context, today_str=today_str)

    logger.info(f"Analyst: calling {SONNET_MODEL}, input ~{len(user_msg)//4} tokens")

    def _call_api_tool(messages: list) -> dict:
        """主路徑：tool_choice 強制輸出，直接回傳 dict。"""
        with LLMTimer("analyst", SONNET_MODEL) as t:
            analysis, usage = chat_json(
                model=SONNET_MODEL,
                max_tokens=12000,
                system=ANALYST_SYSTEM_PROMPT,
                messages=messages,
            )
        record_llm_call(
            agent="analyst", model=SONNET_MODEL,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            duration_s=t.elapsed,
        )
        return analysis

    def _call_api_text(messages: list) -> str:
        """備用路徑：純文字輸出（用於 retry fallback）。"""
        with LLMTimer("analyst_retry", SONNET_MODEL) as t:
            raw_text, usage = chat(
                model=SONNET_MODEL,
                max_tokens=12000,
                system=ANALYST_SYSTEM_PROMPT,
                messages=messages,
            )
        record_llm_call(
            agent="analyst_retry", model=SONNET_MODEL,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            duration_s=t.elapsed,
        )
        return raw_text

    try:
        try:
            analysis = _call_api_tool([{"role": "user", "content": user_msg}])
        except Exception as tool_err:
            logger.warning(f"Analyst tool_choice failed: {tool_err} — falling back to text parse")
            raw_text = _call_api_text([{"role": "user", "content": user_msg}])
            try:
                analysis = _parse_json_from_text(raw_text)
            except json.JSONDecodeError as e:
                # 最後一次重試：要求只輸出 JSON
                retry_suffix = (
                    "\n\n[系統提示：上次輸出的 JSON 無法解析。"
                    "請重新輸出，只輸出純 JSON 物件，"
                    "不要任何說明文字、前言或 markdown 標記。]"
                )
                raw_text2 = _call_api_text([
                    {"role": "user", "content": user_msg},
                    {"role": "assistant", "content": raw_text},
                    {"role": "user", "content": retry_suffix},
                ])
                analysis = _parse_json_from_text(raw_text2)

        logger.info(
            f"Analyst: got {len(analysis.get('inference_chain', []))} inferences, "
            f"regime={analysis.get('regime', {}).get('current', 'unknown')}"
        )
        return analysis

    except json.JSONDecodeError as e:
        logger.warning(f"Analyst JSON parse fallback activated after retry: {e}")
        return _fallback_analysis(f"json_parse_error: {e}", assembled_context)
    except DeepSeekError as e:
        logger.error(f"Analyst API error: {e}")
        return _fallback_analysis(f"api_error: {e}", assembled_context)
    except Exception as e:
        # 非預期錯誤，完整 traceback 以便除錯
        logger.exception(f"Analyst unexpected error: {e}")
        return _fallback_analysis(f"unexpected: {e}", assembled_context)


def _fallback_analysis(error: str, assembled_context: dict | None = None) -> dict:
    """API 失敗時用結構化資料產生非空分析，讓報告不退化成空白。"""
    packages = (assembled_context or {}).get("packages", {})
    data_package = packages.get("data_package", {}) if isinstance(packages, dict) else {}
    quant_package = packages.get("quant_package", {}) if isinstance(packages, dict) else {}
    geopolitical_package = packages.get("geopolitical_package", {}) if isinstance(packages, dict) else {}

    regime_prob = quant_package.get("regime_probability", {}) if isinstance(quant_package, dict) else {}
    if regime_prob:
        regime_name = max(regime_prob, key=regime_prob.get)
        regime_confidence = float(regime_prob.get(regime_name, 0.0) or 0.0)
    else:
        regime_name = MISSING_DATA
        regime_confidence = 0.0

    usable_assets = []
    for key, label in [
        ("spx", "標普500"),
        ("twse", "台股加權"),
        ("vix", "VIX"),
        ("gold", "黃金"),
        ("us10y", "美國10年期公債殖利率"),
        ("dxy", "美元指數"),
        ("brent", "Brent 原油"),
    ]:
        item = data_package.get(key, {}) if isinstance(data_package, dict) else {}
        value = item.get("price") or item.get("value")
        change = item.get("change_pct")
        if value and value != MISSING_DATA:
            change_txt = f"，日變動 {change:+.2f}%" if isinstance(change, (int, float)) else ""
            usable_assets.append((key, label, value, change_txt))

    core_tension = "模型輸出格式失敗，系統改用量化與市場資料生成備援判讀。"
    if usable_assets:
        first = "；".join(f"{label}={value}{change_txt}" for _, label, value, change_txt in usable_assets[:4])
        core_tension = f"{regime_name} 的量化判讀與主要市場數據交叉驗證：{first}。"

    tgri = geopolitical_package.get("tgri", {}) if isinstance(geopolitical_package, dict) else {}
    inference_chain = []
    if regime_name != MISSING_DATA:
        inference_chain.append({
            "id": "INF_FALLBACK_001",
            "claim": f"量化 regime 機率目前指向「{regime_name}」。",
            "mechanism": "DeepSeek Analyst 未輸出可解析 JSON，改採 quant_engine 的 regime_probability 作為備援。",
            "evidence": [],
            "raw_confidence": max(0.35, min(0.65, regime_confidence or 0.45)),
            "adjusted_confidence": max(0.30, min(0.60, regime_confidence or 0.40)),
            "dependencies": [],
        })
    if tgri:
        inference_chain.append({
            "id": "INF_FALLBACK_002",
            "claim": f"TGRI 位於 {tgri.get('score', 'N/A')}，地緣風險未成為唯一主導因子。",
            "mechanism": "使用 TGRI 結構化輸出補足地緣政治段落。",
            "evidence": [],
            "raw_confidence": 0.45,
            "adjusted_confidence": 0.40,
            "dependencies": [],
        })

    compass = []
    if any(k == "spx" for k, *_ in usable_assets):
        compass.append({"asset": "spx", "direction": "neutral", "raw_confidence": 0.45, "logic_id": "INF_FALLBACK_001"})
    if any(k == "gold" for k, *_ in usable_assets):
        compass.append({"asset": "gold", "direction": "neutral", "raw_confidence": 0.40, "logic_id": "INF_FALLBACK_001"})

    return {
        "regime": {
            "current": regime_name,
            "confidence": regime_confidence,
            "day_count": 0,
            "supporting_data": [],
            "contrary_signals": [],
        },
        "core_tension": core_tension,
        "inference_chain": inference_chain,
        "thesis_updates": [],
        "new_thesis_candidates": [],
        "compass": compass,
        "question_for_devil": "若核心 LLM 產生格式失敗，哪些量化與市場數據仍足以支持今日 regime 判讀？",
        "data_gaps_affecting_analysis": [],
        "raw_confidence_adjustments": {},
        "_error": error,
        "_fallback": True,
    }
