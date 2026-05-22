from __future__ import annotations
"""DeepSeek 首席風險官 — 三源裁決（預載模式）。

架構說明：
  舊版：多輪 tool_use 循環（5 輪 × 4K token），最後一輪常無空間輸出裁決。
  新版：預載所有 DeepSeek 可能需要的資料（computed_data/memory/historian）直接
       放入 user_message，DeepSeek 一次性拿到全部資訊做裁決。
       唯一保留 tool：flag_data_gap（寫入操作，不可預載）。
"""

import json
import logging


from src.config import MISSING_DATA, OPUS_MODEL
from src.deepseek_client import DeepSeekError, chat_json
from src.prompts.risk_officer_system import RISK_OFFICER_SYSTEM_PROMPT
from src.telemetry import LLMTimer, record_llm_call

logger = logging.getLogger(__name__)

# ── 最終裁決 tool（最後一輪，強制 JSON 輸出）─────────────────────────────────
_VERDICT_TOOL = {
    "name": "emit_verdict",
    "description": "輸出最終風控裁決 JSON",
    "input_schema": {
        "type": "object",
        "properties": {
            "factual_errors": {"type": "array", "items": {"type": "object"}},
            "data_integrity_violations": {"type": "array", "items": {"type": "object"}},
            "attack_verdicts": {"type": "array", "items": {"type": "object"}},
            "confidence_adjustments": {"type": "array", "items": {"type": "object"}},
            "final_conclusions_stand": {"type": "boolean"},
            "mandatory_corrections": {"type": "array", "items": {"type": "object"}},
            "risk_officer_notes": {"type": "string"},
            "narrative_verdict": {"type": "string"},
        },
        "required": ["attack_verdicts", "final_conclusions_stand", "risk_officer_notes"],
    },
}

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


def _build_structured_input(
    assembled_data: dict,
    analysis: dict,
    da_result: dict,
    premortem_result: dict,
    historian_package: dict,
    today_str: str | None = None,
) -> str:
    """建立以衝突為核心的結構化輸入，取代舊版 _preload_context + _build_user_message。

    核心改變：
    1. 只提取被 inference/attack 引用的 critical_evidence（不 dump 全量數據包）
    2. 消除舊版 _build_user_message 中對 packages 的重複 dump（舊版送了兩次相同資料）
    3. 以假說空間 / 對立力量 / 失敗場景 / 關鍵證據 / 裁決焦點 五段結構組織輸入
    """
    packages = assembled_data.get("packages", {})
    data_pkg = packages.get("data_package", {})
    quant_pkg = packages.get("quant_package", {})
    inference_chain = analysis.get("inference_chain", [])
    attacks = da_result.get("attacks", [])
    scenarios = premortem_result.get("scenarios", [])

    # ── 1. 收集被 inference / attack 引用的 data_key ─────────────────────
    cited_keys: set[str] = set()
    for inf in inference_chain:
        for ev in inf.get("evidence", []):
            if isinstance(ev, dict):
                cited_keys.add(ev.get("data_key", ""))
    for atk in attacks:
        for ev in atk.get("evidence", []):
            if isinstance(ev, dict):
                cited_keys.add(ev.get("data_key", ""))
    cited_keys.discard("")
    cited_keys.discard(MISSING_DATA)

    # ── 2. critical_evidence：只含被引用的數據點 + zscore ────────────────
    zscore_map: dict[str, float] = {
        a.get("asset"): a.get("zscore")
        for a in quant_pkg.get("zscore_alerts", [])
        if isinstance(a, dict) and a.get("asset")
    }
    critical_evidence = []
    for key in sorted(cited_keys):
        asset = data_pkg.get(key)
        if not isinstance(asset, dict):
            continue
        ev: dict = {
            "key": key,
            "quality": asset.get("quality"),
            "timestamp": asset.get("timestamp"),
        }
        for field in ("value", "price", "rate", "change_pct", "tension_note"):
            if asset.get(field) is not None:
                ev[field] = asset[field]
        if key in zscore_map:
            ev["zscore"] = zscore_map[key]
        critical_evidence.append(ev)

    # ── 3. hypothesis_space（推論鏈壓縮）─────────────────────────────────
    hypothesis_space = []
    for inf in inference_chain:
        h = {
            "id": inf.get("id"),
            "claim": inf.get("claim"),
            "confidence": inf.get("raw_confidence"),
            "mechanism": inf.get("mechanism"),
            "evidence_keys": [
                ev.get("data_key") for ev in inf.get("evidence", [])
                if isinstance(ev, dict) and ev.get("data_key")
            ],
            "invalidation": inf.get("invalidation_condition"),
        }
        hypothesis_space.append({k: v for k, v in h.items() if v is not None})

    # ── 4. counter_forces（DA 攻擊）─────────────────────────────────────
    counter_forces = []
    for atk in attacks:
        c = {
            "id": atk.get("id"),
            "target": atk.get("target", "general"),
            "claim": atk.get("claim"),
            "severity": atk.get("severity"),
            "evidence_keys": [
                ev.get("data_key") for ev in atk.get("evidence", [])
                if isinstance(ev, dict) and ev.get("data_key")
            ],
        }
        counter_forces.append({k: v for k, v in c.items() if v is not None})

    # ── 5. failure_modes（pre-mortem 壓縮）──────────────────────────────
    failure_modes = []
    for sc in scenarios:
        f = {
            "thesis_id": sc.get("thesis_id"),
            "failure_mode": sc.get("failure_mode"),
            "early_signals": sc.get("leading_indicators", [])[:3],
            "probability": sc.get("probability"),
        }
        failure_modes.append({k: v for k, v in f.items() if v is not None})

    # ── 6. historical_analogs ────────────────────────────────────────────
    historical_analogs: list[dict] = []
    if historian_package and historian_package.get("analog_ids") != MISSING_DATA:
        for period in historian_package.get("similar_periods", [])[:3]:
            if isinstance(period, dict):
                historical_analogs.append({
                    "date": period.get("date"),
                    "regime": period.get("regime"),
                    "outcome_14d": period.get("outcome_14d", {}),
                    "similarity": period.get("similarity_score"),
                })
        narrative = historian_package.get("narrative_comparison", "")
        if narrative:
            historical_analogs.append({"_narrative": narrative[:500]})
    else:
        historical_analogs = [{"_note": "歷史類比未啟用（需 30 天快照）"}]

    # ── 5b. 發現附錄：分析師未引用但可能重要的數據點 ────────────────────
    _skip = {"quality_scores", "_source", "_timestamp", "run_id",
             "_reduced", "_note", "fetch_time"}
    all_data_keys = {k for k in data_pkg
                     if k not in _skip and isinstance(data_pkg.get(k), dict)}
    uncited_keys = sorted(all_data_keys - cited_keys)

    uncited_summary: list[str] = []
    for key in uncited_keys:
        asset = data_pkg[key]
        val = asset.get("value") or asset.get("price")
        chg = asset.get("change_pct")
        q = asset.get("quality", "?")
        line = f"{key}: {q}, val={val}"
        if isinstance(chg, (int, float)):
            line += f", chg={chg:+.2f}%"
        uncited_summary.append(line)

    uncited_quant: list[str] = []
    for alert in quant_pkg.get("zscore_alerts", []):
        if isinstance(alert, dict) and alert.get("asset") not in cited_keys:
            uncited_quant.append(
                f"{alert.get('asset')}: z={alert.get('zscore', '?')}")
    regime_prob = quant_pkg.get("regime_probability")
    if regime_prob and regime_prob != MISSING_DATA:
        uncited_quant.append(f"regime_probability: {regime_prob}")
    corr = quant_pkg.get("correlation_matrix_30d")
    if corr and corr != MISSING_DATA and isinstance(corr, dict):
        uncited_quant.append(
            f"correlation_matrix_30d: {len(corr)} pairs available")

    # ── 7. decision_focus ────────────────────────────────────────────────
    decision_focus: list[str] = []
    for atk in attacks:
        if atk.get("severity") in ("critical", "high"):
            decision_focus.append(
                f"[{atk.get('severity', '?').upper()}] {atk.get('id')}: "
                f"{str(atk.get('claim', ''))[:120]}"
            )
    analyst_q = analysis.get("question_for_devil", "")
    if analyst_q:
        decision_focus.append(f"[分析師自問] {str(analyst_q)[:150]}")
    if not decision_focus:
        decision_focus = ["裁決所有 counter_forces 並評估整體結論是否成立"]

    # ── 8. 系統狀態（精簡記憶摘要）─────────────────────────────────────
    l3_theses = packages.get("l3_context", {}).get("active_theses", [])
    l5_ctx = packages.get("l5_context", {})
    system_state = {
        "regime": analysis.get("regime", {}),
        "coverage_score": assembled_data.get("coverage_score"),
        "active_theses": [t.get("id") for t in l3_theses if isinstance(t, dict)],
        "brier_score": l5_ctx.get("brier_score"),
        "data_gaps": assembled_data.get("data_gaps", [])[:5],
    }

    # ── 組裝 ─────────────────────────────────────────────────────────────
    today_note = f"🗓️ 今日裁決日期：{today_str}（台灣時間）" if today_str else ""
    sections = [
        "# 首席風險官裁決請求",
        today_note,
        "",
        "## 1. 假說空間（分析師推論鏈）",
        json.dumps(hypothesis_space, indent=2, ensure_ascii=False, default=str),
        "",
        "## 2. 對立力量（Devil's Advocate 攻擊）",
        json.dumps(counter_forces, indent=2, ensure_ascii=False, default=str),
        "",
        "## 3. 失敗場景（Pre-mortem）",
        json.dumps(failure_modes, indent=2, ensure_ascii=False, default=str),
        "",
        "## 4. 關鍵證據（僅被推論 / 攻擊引用的數據點）",
        json.dumps(critical_evidence, indent=2, ensure_ascii=False, default=str),
        "",
        "## 5. 歷史類比",
        json.dumps(historical_analogs, indent=2, ensure_ascii=False, default=str),
        "",
        "## 5b. 發現附錄（分析師未引用，但可能重要的數據點）",
        "以下數據未被推論鏈或攻擊引用。請檢查是否存在分析師的盲區。",
        *([f"- {line}" for line in uncited_summary] if uncited_summary
          else ["- （所有數據點皆已被引用）"]),
        *(["### 未引用 quant 信號",
           *[f"- {q}" for q in uncited_quant]] if uncited_quant else []),
        "",
        "## 6. 裁決焦點",
        *[f"- {q}" for q in decision_focus],
        "",
        "## 7. 系統狀態",
        json.dumps(system_state, indent=2, ensure_ascii=False, default=str),
        "",
        "## 裁決指令",
        "針對每個 counter_force 裁決（OVERRULED / SUSTAINED / NOTED）。",
        "如有數據缺口需標記，請呼叫 flag_data_gap 工具。",
        "完成後輸出裁決 JSON。",
    ]
    return "\n".join(sections)


def run_risk_officer(
    assembled_data: dict,
    analysis: dict,
    da_result: dict,
    premortem_result: dict,
    memory_layers: dict | None = None,
    historian_package: dict | None = None,
    today_str: str | None = None,
) -> dict:
    """Run DeepSeek v4-pro as the risk officer and return verdict JSON."""
    user_msg = _build_structured_input(
        assembled_data, analysis, da_result, premortem_result, historian_package or {},
        today_str=today_str,
    )
    user_msg += (
        "\n\n請只輸出 JSON，格式需包含 factual_errors, data_integrity_violations, "
        "attack_verdicts, confidence_adjustments, final_conclusions_stand, "
        "mandatory_corrections, risk_officer_notes, narrative_verdict。"
    )

    logger.info(f"Risk Officer: calling {OPUS_MODEL} via DeepSeek")

    try:
        with LLMTimer("risk_officer", OPUS_MODEL) as timer:
            verdict, usage = chat_json(
                model=OPUS_MODEL,
                max_tokens=16000,
                system=RISK_OFFICER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
        record_llm_call(
            agent="risk_officer",
            model=OPUS_MODEL,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            duration_s=timer.elapsed,
        )
    except DeepSeekError as exc:
        logger.error(f"Risk Officer API error: {exc}")
        return _fallback_verdict(f"api_error: {exc}")
    except Exception as exc:
        logger.error(f"Risk Officer unexpected error: {exc}")
        return _fallback_verdict(str(exc))

    n_verdicts = len(verdict.get("attack_verdicts", []))
    stands = verdict.get("final_conclusions_stand", True)
    logger.info(f"Risk Officer: {n_verdicts} attack verdicts, conclusions_stand={stands}")
    return verdict

def _fallback_verdict(error: str) -> dict:
    return {
        "factual_errors": [],
        "data_integrity_violations": [],
        "attack_verdicts": [],
        "confidence_adjustments": [],
        "final_conclusions_stand": False,
        "mandatory_corrections": [],
        "risk_officer_notes": f"裁決失敗：{error}",
        "narrative_verdict": "",
        "fallback_reason": "risk_officer_exception",
        "warning": "風控系統異常，此結論未經驗證",
        "_error": error,
    }
