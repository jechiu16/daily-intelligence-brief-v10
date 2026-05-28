from __future__ import annotations
"""Deterministic editorial contract for institution-grade daily briefs."""

from typing import Any

from src.config import MISSING_DATA

WEAK_QUALITIES = {"cached", "stale", "estimated", "MISSING_DATA", "missing", None}
DATA_LABELS = {
    "tips_10y": "TIPS 10年實質利率",
    "nfci": "NFCI 金融條件指數",
    "breakeven_5y5y": "5年5年通膨預期",
    "cot_gold": "COT 黃金持倉",
    "bdi": "波羅的海乾散貨指數",
    "brent": "Brent 原油",
    "wti": "WTI 原油",
    "spx": "標普500",
    "twse": "台股加權指數",
    "tw_foreign_net": "台股外資買賣超",
    "tgri": "台海地緣風險指數",
    "gold": "黃金",
    "dxy": "美元指數",
    "vix": "VIX",
    "copper": "銅價",
    "copper_gold_ratio": "銅金比",
    "us10y": "美國10年期公債殖利率",
    "fed_funds": "聯邦基金利率",
}
QUALITY_LABELS = {
    "confirmed": "即時確認資料",
    "cached": "快取資料",
    "stale": "過期資料",
    "estimated": "估算資料",
    "MISSING_DATA": "缺失資料",
    "missing_data": "缺失資料",
    "missing": "缺失資料",
    None: "缺失資料",
}


def build_editorial_contract(
    *,
    analysis: dict,
    verdict: dict,
    data_package: dict | None = None,
    watchboard_backtest: dict | None = None,
) -> dict:
    """Build the non-negotiable writing plan before the narrator writes."""
    data_package = data_package or {}
    watchboard_backtest = watchboard_backtest or {}
    chain = [
        item for item in analysis.get("inference_chain", [])
        if isinstance(item, dict)
    ]
    primary = _primary_inference(chain)
    true_change = analysis.get("core_tension") or primary.get("claim") or "今日真正改變尚未被分析師明確定義。"
    mechanism = primary.get("mechanism") or primary.get("claim") or "主導機制尚未明確。"

    mechanism_sentences = _mechanism_sentences(chain)
    weak_haircuts = _weak_evidence_haircuts(chain, verdict)
    challenges = [
        _clean(item.get("narrative") or item.get("reason") or "")
        for item in verdict.get("attack_verdicts", [])
        if isinstance(item, dict) and item.get("verdict") == "SUSTAINED"
    ]
    falsifiers = _falsifiers(chain, analysis)
    allocation_actions = _allocation_actions(analysis.get("compass", []))

    return {
        "schema_version": "editorial-contract-v1",
        "true_change": _clean(true_change),
        "dominant_mechanism": _clean(mechanism),
        "watchboard_first": watchboard_backtest.get("status") == "evaluated",
        "watchboard_summary": _clean(watchboard_backtest.get("summary") or "尚無前一份觀察清單可回測。"),
        "mechanism_sentences": mechanism_sentences,
        "weak_evidence_haircuts": weak_haircuts,
        "successful_challenges": challenges,
        "falsifiers": falsifiers,
        "allocation_actions": allocation_actions,
        "opening_mandate": _opening_mandate(true_change, watchboard_backtest),
        "required_compass_actions": ["加碼", "持有", "減碼", "避險", "等待"],
        "data_quality_summary": _data_quality_summary(data_package),
    }


def format_editorial_contract(contract: dict) -> str:
    """Format the contract for the narrator prompt."""
    lines = [
        "## 機構編輯契約（必須遵守，不可自由改寫成空泛摘要）",
        f"- 第一段任務：{contract.get('opening_mandate', '')}",
        f"- 今日真正改變：{contract.get('true_change', '')}",
        f"- 主導機制：{contract.get('dominant_mechanism', '')}",
        f"- 昨日觀察回測：{contract.get('watchboard_summary', '')}",
    ]

    mechanisms = contract.get("mechanism_sentences", [])
    if mechanisms:
        lines.append("- 主線故事必須納入的機制句：")
        for item in mechanisms[:4]:
            lines.append(f"  - {item}")

    haircuts = contract.get("weak_evidence_haircuts", [])
    if haircuts:
        lines.append("- 弱資料信心折扣，主線故事必須明講：")
        for item in haircuts[:5]:
            keys = "、".join(item.get("weak_keys", []))
            lines.append(f"  - {item.get('claim', '')}：{keys}；{item.get('required_language', '')}")

    challenges = contract.get("successful_challenges", [])
    if challenges:
        lines.append("- 成功挑戰必須吸收，不得只放在風險段落：")
        for item in challenges[:3]:
            lines.append(f"  - {item}")

    allocations = contract.get("allocation_actions", [])
    if allocations:
        lines.append("- 配置羅盤必須使用動作欄（加碼/持有/減碼/避險/等待）：")
        for item in allocations[:6]:
            lines.append(
                f"  - {item.get('asset')}: {item.get('direction')} / {item.get('action')} / "
                f"{item.get('confidence_label')}；{item.get('reason')}"
            )

    falsifiers = contract.get("falsifiers", [])
    if falsifiers:
        lines.append("- 可反證條件：")
        for item in falsifiers[:5]:
            lines.append(f"  - {item}")

    return "\n".join(lines)


def build_contract_story_lead(contract: dict) -> str:
    """Build a deterministic lead paragraph when narrator misses the mandate."""
    watch = contract.get("watchboard_summary", "尚無前一份觀察清單可回測。")
    return (
        f"今日真正改變不是單一行情，而是{contract.get('true_change', '')}"
        f"昨日觀察回測先給出底線：{watch} "
        f"因此今天的主線必須先區分已被驗證的訊號與仍待確認的噪音。"
    )


def build_contract_mechanism_paragraph(contract: dict) -> str:
    mechanisms = contract.get("mechanism_sentences", [])
    if not mechanisms:
        return f"主導機制是：{contract.get('dominant_mechanism', '')}"
    return "主導機制很清楚：" + " ".join(mechanisms[:3])


def build_contract_haircut_paragraph(contract: dict) -> str:
    haircuts = contract.get("weak_evidence_haircuts", [])
    if not haircuts:
        return ""
    parts = []
    for item in haircuts[:4]:
        keys = ", ".join(item.get("weak_keys", []))
        parts.append(f"{item.get('claim', '')} 依賴 {keys}，必須信心折扣")
    return "弱資料處理：" + "；".join(parts) + "。"


def build_contract_compass(contract: dict) -> str:
    rows = [
        "| 資產 | 方向 | 動作 | 信心 | 一句理由 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in contract.get("allocation_actions", [])[:8]:
        rows.append(
            f"| {item.get('asset', '')} | {item.get('direction', '')} | {item.get('action', '等待')} | "
            f"{item.get('confidence_label', '未量化')} | {item.get('reason', '')} |"
        )
    if len(rows) == 2:
        rows.append("| 全資產 | 中性 | 等待 | 未量化 | 缺少足夠推論鏈，暫不做方向性配置。 |")
    return "\n".join(rows)


def _primary_inference(chain: list[dict]) -> dict:
    if not chain:
        return {}
    return max(
        chain,
        key=lambda item: item.get("adjusted_confidence")
        if isinstance(item.get("adjusted_confidence"), (int, float))
        else item.get("raw_confidence", 0),
    )


def _mechanism_sentences(chain: list[dict]) -> list[str]:
    lines = []
    for item in chain[:6]:
        mechanism = item.get("mechanism") or item.get("claim")
        if not mechanism:
            continue
        evidence_keys = [
            humanize_data_key(str(ev.get("data_key"))) for ev in item.get("evidence", [])
            if isinstance(ev, dict) and ev.get("data_key")
        ]
        left = "、".join(evidence_keys[:3]) or "核心數據"
        predictions = "、".join(humanize_prediction(pred) for pred in item.get("asset_predictions", [])[:2]) or "資產定價"
        if "透過" in mechanism or "經由" in mechanism or "藉由" in mechanism:
            lines.append(f"{left} 顯示：{mechanism}，影響 {predictions}。")
        else:
            lines.append(f"{left} 透過 {mechanism} 影響 {predictions}。")
    return lines


def _weak_evidence_haircuts(chain: list[dict], verdict: dict) -> list[dict]:
    down_adjusted = {
        item.get("inf_id") for item in verdict.get("confidence_adjustments", [])
        if isinstance(item, dict) and item.get("direction") == "down" and item.get("inf_id")
    }
    haircuts = []
    for item in chain[:8]:
        weak = [
            ev for ev in item.get("evidence", [])
            if isinstance(ev, dict) and ev.get("quality") in WEAK_QUALITIES
        ]
        if not weak:
            continue
        raw = item.get("raw_confidence")
        adjusted = item.get("adjusted_confidence")
        already_cut = (
            isinstance(raw, (int, float))
            and isinstance(adjusted, (int, float))
            and adjusted <= raw - 0.02
        ) or item.get("id") in down_adjusted
        weak_keys = [
            humanize_quality_key(ev.get("data_key"), ev.get("quality"))
            for ev in weak
            if ev.get("data_key")
        ]
        haircuts.append({
            "claim": _clean(item.get("claim") or item.get("id") or "未命名推論")[:100],
            "weak_keys": weak_keys,
            "already_cut": already_cut,
            "required_language": "主線故事需明說資料品質較弱，並說明是否已下修信心。",
        })
    return haircuts


def _allocation_actions(compass: Any) -> list[dict]:
    if not isinstance(compass, list):
        return []
    rows = []
    for item in compass[:8]:
        if not isinstance(item, dict):
            continue
        asset = item.get("asset") or "未標示資產"
        direction = item.get("direction") or "中性"
        confidence = item.get("adjusted_confidence") or item.get("raw_confidence")
        action = _position_action(direction, confidence)
        confidence_label = f"{confidence:.0%}" if isinstance(confidence, (int, float)) else "未量化"
        reason = _clean(item.get("reason") or item.get("claim") or "映射自今日主線判斷。")
        rows.append({
            "asset": asset,
            "direction": direction,
            "action": action,
            "confidence_label": confidence_label,
            "reason": reason,
        })
    return rows


def _position_action(direction: Any, confidence: Any) -> str:
    direction_text = str(direction or "").lower()
    conf = confidence if isinstance(confidence, (int, float)) else None
    if conf is None or conf < 0.45:
        return "等待"
    if "hedge" in direction_text or "避險" in direction_text:
        return "避險"
    if any(word in direction_text for word in ("down", "short", "減", "空")):
        return "減碼" if conf >= 0.55 else "等待"
    if any(word in direction_text for word in ("up", "long", "加", "多")):
        return "加碼" if conf >= 0.65 else "持有"
    return "持有" if conf >= 0.55 else "等待"


def _falsifiers(chain: list[dict], analysis: dict) -> list[str]:
    items = []
    for item in chain[:6]:
        condition = item.get("invalidation_condition")
        if condition:
            items.append(_clean(condition))
    question = analysis.get("question_for_devil")
    if question:
        items.append(_clean(question))
    return items


def _opening_mandate(true_change: Any, watchboard_backtest: dict) -> str:
    if watchboard_backtest.get("status") == "evaluated":
        return f"先裁決昨日觀察回測，再說今日真正改變：{_clean(true_change)}"
    return f"第一段直接回答今日真正改變：{_clean(true_change)}"


def _data_quality_summary(data_package: dict) -> dict:
    counts: dict[str, int] = {}
    for item in data_package.values():
        if not isinstance(item, dict):
            continue
        quality = item.get("quality", MISSING_DATA)
        counts[quality] = counts.get(quality, 0) + 1
    return counts


def _clean(value: Any) -> str:
    return str(value or "").replace("|", "｜").replace("\n", " ").strip()


def humanize_data_key(key: Any) -> str:
    return DATA_LABELS.get(str(key), str(key))


def humanize_quality(quality: Any) -> str:
    if quality is None:
        return QUALITY_LABELS[None]
    key = str(quality)
    return QUALITY_LABELS.get(key, QUALITY_LABELS.get(key.lower(), key or "缺失資料"))


def humanize_quality_key(key: Any, quality: Any) -> str:
    return f"{humanize_data_key(key)}（{humanize_quality(quality)}）"


def humanize_prediction(prediction: Any) -> str:
    text = str(prediction or "")
    direction_map = {
        "_up": "偏上",
        "_down": "偏下",
    }
    for suffix, label in direction_map.items():
        if text.endswith(suffix):
            return f"{humanize_data_key(text[:-len(suffix)])}{label}"
    return humanize_data_key(text)
