from __future__ import annotations
"""Thesis Consistency Checker — 一致性檢查。純 Python。"""

import json
import logging
from datetime import datetime, timezone

from src.config import MISSING_DATA

logger = logging.getLogger(__name__)

# 嚴重程度
CRITICAL = "CRITICAL"
HIGH = "HIGH"
MEDIUM = "MEDIUM"

# 旗標類型
CONFIDENCE_JUMP = "CONFIDENCE_JUMP"
DIRECTION_REVERSAL = "DIRECTION_REVERSAL"
INVALIDATOR_IGNORED = "INVALIDATOR_IGNORED"
DEADLINE_PASSED = "DEADLINE_PASSED"


def check_consistency(
    active_theses: list[dict],
    analysis: dict,
    today_str: str | None = None,
) -> dict:
    """
    比對當日分析和 thesis 歷史，檢查一致性問題。

    active_theses: L3 中的 active thesis 清單
    analysis: DeepSeek 分析的 JSON
    today_str: YYYY-MM-DD，預設今日
    """
    if today_str is None:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    flags = []

    # 從分析中取得今日信心值
    today_confidence = {}
    for upd in analysis.get("thesis_updates", []):
        tid = upd.get("thesis_id")
        change = upd.get("confidence_change", 0)
        today_confidence[tid] = change

    for thesis in active_theses:
        tid = thesis.get("id", "")
        history = thesis.get("confidence_history", [])
        status = thesis.get("status", "active")
        deadline = thesis.get("deadline", "")
        invalidators = thesis.get("invalidators", [])

        # 1. 信心值單日跳躍 > 0.2
        if len(history) >= 2:
            prev_adj = history[-2].get("adjusted", history[-2].get("raw", 0))
            curr_adj = history[-1].get("adjusted", history[-1].get("raw", 0))
            if abs(curr_adj - prev_adj) > 0.2:
                flags.append({
                    "type": CONFIDENCE_JUMP,
                    "severity": HIGH,
                    "thesis_id": tid,
                    "detail": f"Confidence jumped {prev_adj:.2f} → {curr_adj:.2f} (delta={abs(curr_adj - prev_adj):.2f})",
                })

        # 2. 方向反轉
        if len(history) >= 2:
            compass = analysis.get("compass", [])
            thesis_title = thesis.get("title", "")
            for comp in compass:
                if tid.lower() in comp.get("logic_id", "").lower():
                    prev_direction = thesis.get("_last_direction")
                    curr_direction = comp.get("direction")
                    if prev_direction and curr_direction and prev_direction != curr_direction:
                        flags.append({
                            "type": DIRECTION_REVERSAL,
                            "severity": HIGH,
                            "thesis_id": tid,
                            "detail": f"Direction reversed: {prev_direction} → {curr_direction}",
                        })

        # 3. Invalidator 已觸發但 thesis 仍 active（最嚴重）
        for inv in invalidators:
            if inv.get("triggered") and status == "active":
                flags.append({
                    "type": INVALIDATOR_IGNORED,
                    "severity": CRITICAL,
                    "thesis_id": tid,
                    "invalidator": inv.get("condition", ""),
                    "triggered_date": inv.get("triggered_date", ""),
                    "detail": f"Invalidator '{inv.get('condition')}' triggered on {inv.get('triggered_date')} but thesis still active",
                })

        # 4. Deadline 已過但未結案
        if deadline and deadline < today_str and status == "active":
            flags.append({
                "type": DEADLINE_PASSED,
                "severity": HIGH,
                "thesis_id": tid,
                "deadline": deadline,
                "detail": f"Deadline {deadline} passed but thesis still active",
            })

    has_critical = any(f["severity"] == CRITICAL for f in flags)

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "flags": flags,
        "has_critical": has_critical,
        "critical_count": sum(1 for f in flags if f["severity"] == CRITICAL),
        "high_count": sum(1 for f in flags if f["severity"] == HIGH),
    }

    if has_critical:
        logger.error(f"ConsistencyChecker CRITICAL: {result['critical_count']} critical flags → pipeline should pause")
    elif flags:
        logger.warning(f"ConsistencyChecker: {len(flags)} flags ({result['high_count']} HIGH)")
    else:
        logger.info("ConsistencyChecker: OK")

    return result
