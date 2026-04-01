"""Opus 首席風險官 — 三源裁決（預載模式）。

架構說明：
  舊版：多輪 tool_use 循環（5 輪 × 4K token），最後一輪常無空間輸出裁決。
  新版：預載所有 Opus 可能需要的資料（computed_data/memory/historian）直接
       放入 user_message，Opus 一次性拿到全部資訊做裁決。
       唯一保留 tool：flag_data_gap（寫入操作，不可預載）。
"""

import json
import re
import logging

import anthropic

from src.config import ANTHROPIC_API_KEY, MISSING_DATA, OPUS_MODEL
from src.opus_tool_executor import OpusToolExecutor
from src.prompts.risk_officer_system import RISK_OFFICER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_client = None

# 只保留寫入工具，查詢工具已預載進 context
FLAG_TOOL = [
    {
        "name": "flag_data_gap",
        "description": "標記數據缺口，觸發通知",
        "input_schema": {
            "type": "object",
            "properties": {
                "data_key": {"type": "string", "description": "缺失的數據鍵名"},
                "impact": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"], "description": "影響程度"},
                "note": {"type": "string", "description": "說明"},
            },
            "required": ["data_key", "impact"],
        },
    },
]


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _preload_context(
    assembled_data: dict,
    memory_layers: dict,
    historian_package: dict,
) -> str:
    """預載 Opus 所有可查詢資料，直接注入 user message。

    取代舊版 query_computed_data / query_memory_layer / query_historian 工具呼叫。
    """
    sections = []

    # ── Computed Data（完整 data_package，非僅 assembled） ──
    data_pkg = assembled_data.get("packages", {}).get("data_package", {})
    quant_pkg = assembled_data.get("packages", {}).get("quant_package", {})
    sections.append("### 預載：計算數據（data_package）")
    sections.append(json.dumps(data_pkg, indent=2, ensure_ascii=False, default=str))
    sections.append("\n### 預載：量化統計（quant_package）")
    sections.append(json.dumps(quant_pkg, indent=2, ensure_ascii=False, default=str))

    # ── Memory Layers（只開放 l2/l3/l5）──
    for layer_key in ["l2_context", "l3_context", "l5_context"]:
        layer_data = assembled_data.get("packages", {}).get(layer_key, {})
        if layer_data:
            sections.append(f"\n### 預載：記憶層 {layer_key}")
            sections.append(json.dumps(layer_data, indent=2, ensure_ascii=False, default=str))

    # ── Historian Package ──
    if historian_package and historian_package.get("analog_ids") != MISSING_DATA:
        sections.append("\n### 預載：歷史類比（historian_package）")
        sections.append(json.dumps(historian_package, indent=2, ensure_ascii=False, default=str))
    else:
        sections.append("\n### 預載：歷史類比 — MISSING_DATA（需要 30 天快照才啟用）")

    return "\n".join(sections)


def _build_user_message(
    assembled_data: dict,
    analysis: dict,
    da_result: dict,
    premortem_result: dict,
    preloaded_context: str,
) -> str:
    """組裝完整三源輸入 + 預載 context。"""
    lines = [
        "# 首席風險官裁決請求",
        "",
        "## ─── 預載查詢資料（等同 query_computed_data / query_memory_layer / query_historian 結果）",
        preloaded_context,
        "",
        "## ─── 來源 A：完整數據包（assembled_data，基準真相）",
        json.dumps(assembled_data.get("packages", {}), indent=2, ensure_ascii=False, default=str),
        "",
        "## ─── 來源 B：首席分析師報告（Sonnet 推理鏈）",
        json.dumps(analysis, indent=2, ensure_ascii=False, default=str),
        "",
        "## ─── 來源 C：邏輯攻擊",
        "### Devil's Advocate 攻擊清單",
        json.dumps(da_result, indent=2, ensure_ascii=False, default=str),
        "",
        "### Pre-mortem 失敗場景",
        json.dumps(premortem_result, indent=2, ensure_ascii=False, default=str),
        "",
        "## ─── 裁決指令",
        "請審查以上三個來源，執行完整裁決。",
        "如發現數據缺口需標記，請呼叫 flag_data_gap 工具。",
        "完成標記後，立即輸出裁決 JSON（不要等待更多工具呼叫）。",
    ]
    return "\n".join(lines)


def run_risk_officer(
    assembled_data: dict,
    analysis: dict,
    da_result: dict,
    premortem_result: dict,
    memory_layers: dict | None = None,
    historian_package: dict | None = None,
) -> dict:
    """Opus 三源裁決 — 預載模式（單輪推理 + 選擇性 flag_data_gap）。"""
    client = _get_client()
    executor = OpusToolExecutor(
        assembled_data=assembled_data,
        memory_layers=memory_layers or {},
        historian_package=historian_package or {},
    )

    # 預載所有查詢資料
    preloaded_context = _preload_context(
        assembled_data,
        memory_layers or {},
        historian_package or {},
    )

    user_msg = _build_user_message(
        assembled_data, analysis, da_result, premortem_result, preloaded_context
    )
    messages = [{"role": "user", "content": user_msg}]

    logger.info(f"Risk Officer: calling {OPUS_MODEL} (preload mode, max 3 turns for flag_data_gap)")

    # 最多 3 輪：Opus 可能分批呼叫 flag_data_gap（1-2輪），最後 1 輪輸出裁決
    max_turns = 3
    for turn in range(max_turns):
        try:
            response = client.messages.create(
                model=OPUS_MODEL,
                max_tokens=16000,   # 大幅提升：Opus 全力裁決不受限
                system=RISK_OFFICER_SYSTEM_PROMPT,
                tools=FLAG_TOOL,    # 只保留寫入工具
                messages=messages,
            )
        except Exception as e:
            logger.error(f"Risk Officer API error: {e}")
            return _fallback_verdict(str(e))

        # 處理 flag_data_gap 工具呼叫
        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use" and block.name == "flag_data_gap":
                    result = executor.execute(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
            continue

        # 最終裁決輸出
        final_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                final_text = block.text.strip()
                break

        match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", final_text)
        if match:
            final_text = match.group(1).strip()
        else:
            start = final_text.find('{')
            end = final_text.rfind('}')
            if start != -1 and end != -1:
                final_text = final_text[start:end+1]

        try:
            verdict = json.loads(final_text)
            n_verdicts = len(verdict.get("attack_verdicts", []))
            stands = verdict.get("final_conclusions_stand", True)
            logger.info(f"Risk Officer: {n_verdicts} attack verdicts, conclusions_stand={stands}")
            return verdict
        except json.JSONDecodeError as e:
            logger.error(f"Risk Officer JSON parse error: {e}")
            return _fallback_verdict(str(e))

    logger.warning("Risk Officer: max turns reached")
    return _fallback_verdict("max turns reached")


def _fallback_verdict(error: str) -> dict:
    return {
        "factual_errors": [],
        "data_integrity_violations": [],
        "attack_verdicts": [],
        "confidence_adjustments": [],
        "final_conclusions_stand": False,
        "mandatory_corrections": [],
        "risk_officer_notes": f"裁決失敗：{error}",
        "fallback_reason": "risk_officer_exception",
        "warning": "風控系統異常，此結論未經驗證",
        "_error": error,
    }
