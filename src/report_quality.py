from __future__ import annotations
"""Deterministic editorial quality checks for published DIB reports."""

import re
from typing import Any

MISSING_SECTION_PENALTY = 8

REQUIRED_SECTIONS = (
    "institutional_brief",
    "tension",
    "market_data",
    "main_story",
    "causal_graph",
    "tgri_card",
    "thesis_tracking",
    "compass",
    "watchboard_backtest",
    "watchboard",
    "question",
)

BANNED_PHRASES = (
    "綜上所述",
    "總體而言",
    "值得關注",
    "值得注意",
    "不容忽視",
    "首先",
    "其次",
    "最後",
)

MACHINE_TOKENS_RE = re.compile(r"\b(?:INF|DA)_\d{3}\b|SUSTAINED|NOTED|OVERRULED")
QUALITY_TOKEN_RE = re.compile(r"\{\{(?:confirmed|cached|estimated|stale|manual|MISSING_DATA|anomaly_flagged|deviation):[^}]+\}\}")
MECHANISM_RE = re.compile(r"(?:透過|經由|藉由).{1,80}(?:影響|推動|壓制|支撐|拖累|傳導|改變)")
TRUE_CHANGE_MARKERS = ("真正改變", "改變", "轉為", "轉向", "不再", "開始", "失效", "重新", "邊際")
WATCHBOARD_FIRST_MARKERS = ("昨日觀察", "回測", "觸發", "未觸發", "部分觸發", "尚無前一份")
COMPASS_ACTION_MARKERS = ("加碼", "持有", "減碼", "避險", "等待", "add", "hold", "trim", "hedge", "wait")
WEAK_QUALITIES = {"cached", "stale", "estimated", "MISSING_DATA", "missing", None}


def repair_report_contract(
    *,
    report: dict,
    analysis: dict | None = None,
    verdict: dict | None = None,
    data_package: dict | None = None,
    calendar_package: dict | None = None,
    geopolitical_package: dict | None = None,
    watchboard_backtest: dict | None = None,
    causal_graph: dict | None = None,
) -> dict:
    """Fill deterministic sections that should not depend on narrator creativity."""
    from src.editorial_contract import (
        build_contract_compass,
        build_contract_haircut_paragraph,
        build_contract_mechanism_paragraph,
        build_contract_story_lead,
        build_editorial_contract,
    )
    from src.research_ledger import (
        build_causal_graph,
        build_institutional_brief,
        build_watchboard_seed,
        extract_watchboard_items,
        format_causal_graph,
        format_watchboard_backtest,
    )

    analysis = analysis or {}
    verdict = verdict or {}
    data_package = data_package or {}
    calendar_package = calendar_package or {}
    geopolitical_package = geopolitical_package or {}
    if not isinstance(report, dict):
        report = {}

    sections = report.get("sections")
    if not isinstance(sections, dict):
        sections = {}
        report["sections"] = sections

    def fill(key: str, value: Any) -> None:
        if _is_missing_section(sections.get(key)) and not _is_missing_section(value):
            sections[key] = value

    causal_graph_obj = causal_graph or report.get("_causal_graph") or build_causal_graph(analysis, verdict)
    backtest_obj = (
        watchboard_backtest
        if watchboard_backtest is not None
        else report.get("_watchboard_backtest", {"status": "no_prior_watchboard", "summary": "尚無前一份觀察清單可回測。", "items": []})
    )
    report["_causal_graph"] = causal_graph_obj
    report["_watchboard_backtest"] = backtest_obj
    editorial_contract = report.get("_editorial_contract")
    if not isinstance(editorial_contract, dict):
        editorial_contract = build_editorial_contract(
            analysis=analysis,
            verdict=verdict,
            data_package=data_package,
            watchboard_backtest=backtest_obj,
        )
    report["_editorial_contract"] = editorial_contract

    fill("market_data", report.get("_market_data_structured", ""))
    fill("causal_graph", format_causal_graph(causal_graph_obj))
    fill("watchboard_backtest", format_watchboard_backtest(backtest_obj))
    fill("watchboard", build_watchboard_seed(data_package, verdict, calendar_package))
    fill("institutional_brief", build_institutional_brief(
        analysis=analysis,
        verdict=verdict,
        watchboard_backtest=backtest_obj,
        watchboard_items=extract_watchboard_items(report),
    ))
    fill("tension", analysis.get("core_tension") or _first_claim(analysis) or "今日核心張力尚未生成。")
    fill("main_story", _main_story_fallback(analysis, verdict, backtest_obj))
    fill("tgri_card", _tgri_fallback(geopolitical_package))
    fill("thesis_tracking", _thesis_fallback(analysis))
    fill("compass", _compass_fallback(analysis))
    fill("question", analysis.get("question_for_devil") or "若下一次關鍵數據公布後，觀察清單仍未觸發，今日主線是否只是噪音？")
    _enforce_editorial_contract(
        sections,
        editorial_contract,
        build_contract_story_lead=build_contract_story_lead,
        build_contract_mechanism_paragraph=build_contract_mechanism_paragraph,
        build_contract_haircut_paragraph=build_contract_haircut_paragraph,
        build_contract_compass=build_contract_compass,
    )

    return report


def _section_text(sections: dict[str, Any], key: str) -> str:
    value = sections.get(key, "")
    return value if isinstance(value, str) else str(value)


def assess_report_quality(
    *,
    report: dict,
    analysis: dict | None = None,
    verdict: dict | None = None,
    coverage: float | None = None,
    integrity_score: float | None = None,
) -> dict:
    """Return a compact quality assessment that can be logged and published."""
    analysis = analysis or {}
    verdict = verdict or {}
    sections = report.get("sections", {}) if isinstance(report, dict) else {}
    flags: list[dict[str, Any]] = []
    score = 100

    def flag(code: str, severity: str, note: str, penalty: int) -> None:
        nonlocal score
        score -= penalty
        flags.append({
            "code": code,
            "severity": severity,
            "note": note,
            "penalty": penalty,
        })

    for key in REQUIRED_SECTIONS:
        text = _section_text(sections, key).strip()
        if not text or text == "MISSING_DATA":
            flag("missing_section", "high", f"缺少必要章節：{key}", MISSING_SECTION_PENALTY)

    story = _section_text(sections, "main_story")
    if len(story) < 700:
        flag("main_story_too_short", "medium", "主線故事少於 700 字，可能沒有充分展開機制。", 6)
    elif len(story) > 4200:
        flag("main_story_too_long", "medium", "主線故事超過 4200 字，可能降低日報可讀性。", 4)

    story_paragraphs = _paragraphs(story)
    opening = story_paragraphs[0] if story_paragraphs else ""
    if story and not any(marker in opening for marker in TRUE_CHANGE_MARKERS):
        flag("opening_not_true_change", "medium", "主線故事第一段沒有直接回答今日真正改變。", 6)

    mechanism_count = sum(1 for para in story_paragraphs if MECHANISM_RE.search(para))
    required_mechanisms = min(2, len(story_paragraphs)) if story_paragraphs else 0
    if required_mechanisms and mechanism_count < required_mechanisms:
        flag("thin_mechanism_density", "medium", "主線故事缺少足夠的 X 透過 Y 影響 Z 機制句。", 6)

    backtest_obj = report.get("_watchboard_backtest", {}) if isinstance(report, dict) else {}
    if isinstance(backtest_obj, dict) and backtest_obj.get("status") == "evaluated":
        if not any(marker in opening for marker in WATCHBOARD_FIRST_MARKERS):
            flag("watchboard_not_first", "medium", "已有昨日觀察回測，但主線故事開頭沒有先裁決昨日 watchboard。", 5)

    all_text = "\n".join(_section_text(sections, key) for key in sections)
    banned_hits = [p for p in BANNED_PHRASES if p in all_text]
    if banned_hits:
        flag("banned_phrases", "medium", f"出現空泛或序列式語句：{', '.join(banned_hits[:5])}", min(12, 3 * len(banned_hits)))

    machine_hits = MACHINE_TOKENS_RE.findall(all_text)
    if machine_hits:
        flag("machine_tokens", "high", f"報告仍暴露機器代碼：{', '.join(sorted(set(machine_hits))[:5])}", 10)

    quality_token_count = len(QUALITY_TOKEN_RE.findall(all_text))
    if quality_token_count < 8:
        flag("weak_numeric_anchoring", "medium", "品質標記數字少於 8 個，數據錨定可能不足。", 6)

    question = _section_text(sections, "question")
    if not any(marker in question for marker in ("若", "如果", "一旦")) or not any(marker in question for marker in ("觀察", "追蹤", "驗證", "公布")):
        flag("weak_falsifier", "high", "思考題缺少可驗證條件或觀測信號。", 8)

    watchboard = _section_text(sections, "watchboard")
    if watchboard and watchboard != "MISSING_DATA":
        rows = [line for line in watchboard.splitlines() if "|" in line]
        if len(rows) < 4:
            flag("thin_watchboard", "medium", "觀察清單不像表格或少於 3 個觀測項。", 5)

    institutional_brief = _section_text(sections, "institutional_brief")
    if institutional_brief and institutional_brief != "MISSING_DATA":
        rows = [line for line in institutional_brief.splitlines() if "|" in line]
        if len(rows) < 5 or "今日真正改變" not in institutional_brief or "最大反證" not in institutional_brief:
            flag("thin_institutional_brief", "medium", "機構快照缺少真正改變、主導機制或最大反證。", 5)

    sustained = [
        item for item in verdict.get("attack_verdicts", [])
        if isinstance(item, dict) and item.get("verdict") == "SUSTAINED"
    ]
    if sustained and "成功挑戰" not in story and "修正" not in story:
        flag("sustained_not_integrated", "high", "存在成功挑戰，但主線故事未明確吸收修正。", 10)

    weak_claims = _weak_evidence_without_haircut(analysis, verdict)
    if weak_claims:
        flag(
            "weak_data_no_haircut",
            "high",
            f"核心推論使用 cached/stale/estimated/MISSING_DATA 證據但未顯性降信心：{', '.join(weak_claims[:3])}",
            min(12, 4 * len(weak_claims)),
        )

    compass = _section_text(sections, "compass")
    if compass and compass != "MISSING_DATA" and not any(marker in compass.lower() for marker in COMPASS_ACTION_MARKERS):
        flag("compass_no_action_language", "medium", "配置羅盤缺少加碼、持有、減碼、避險或等待等 position-sizing 動作語言。", 6)

    if coverage is not None and coverage < 0.85:
        flag("low_coverage", "medium", f"資料覆蓋率偏低：{coverage:.0%}", 5)
    if integrity_score is not None and integrity_score < 0.9:
        flag("low_integrity", "high", f"引用完整性偏低：{integrity_score:.0%}", 10)

    score = max(0, min(100, score))
    if score >= 90:
        grade = "institutional"
    elif score >= 80:
        grade = "publishable"
    elif score >= 70:
        grade = "needs_editor"
    else:
        grade = "hold"

    return {
        "score": score,
        "grade": grade,
        "flags": flags,
        "summary": _summary(score, grade, flags),
    }


def _summary(score: int, grade: str, flags: list[dict[str, Any]]) -> str:
    if not flags:
        return f"品質分數 {score}/100（{grade}）：通過全部硬性檢查。"
    top = "；".join(flag["note"] for flag in flags[:3])
    return f"品質分數 {score}/100（{grade}）：{top}"


def _enforce_editorial_contract(
    sections: dict[str, Any],
    contract: dict,
    *,
    build_contract_story_lead,
    build_contract_mechanism_paragraph,
    build_contract_haircut_paragraph,
    build_contract_compass,
) -> None:
    """Upgrade weak narrator output with non-negotiable editorial contract content."""
    story = _section_text(sections, "main_story")
    paragraphs = _paragraphs(story)
    opening = paragraphs[0] if paragraphs else ""
    lead_additions: list[str] = []
    trailing_additions: list[str] = []

    needs_lead = not any(marker in opening for marker in TRUE_CHANGE_MARKERS)
    if contract.get("watchboard_first") and not any(marker in opening for marker in WATCHBOARD_FIRST_MARKERS):
        needs_lead = True
    if needs_lead:
        lead_additions.append(build_contract_story_lead(contract))

    mechanism_count = sum(1 for para in paragraphs if MECHANISM_RE.search(para))
    if mechanism_count < min(2, max(1, len(paragraphs))):
        mechanism_para = build_contract_mechanism_paragraph(contract)
        if mechanism_para:
            trailing_additions.append(mechanism_para)

    if contract.get("weak_evidence_haircuts") and not any(marker in story for marker in ("信心折扣", "弱資料", "資料品質")):
        haircut_para = build_contract_haircut_paragraph(contract)
        if haircut_para:
            trailing_additions.append(haircut_para)

    if lead_additions or trailing_additions:
        story_parts = [story] if story else []
        sections["main_story"] = "\n\n".join(lead_additions + story_parts + trailing_additions)

    compass = _section_text(sections, "compass")
    if compass and compass != "MISSING_DATA" and not any(marker in compass.lower() for marker in COMPASS_ACTION_MARKERS):
        sections["compass"] = build_contract_compass(contract)


def _is_missing_section(value: Any) -> bool:
    if value is None:
        return True
    text = value if isinstance(value, str) else str(value)
    return not text.strip() or text.strip() == "MISSING_DATA"


def _first_claim(analysis: dict) -> str:
    chain = analysis.get("inference_chain", [])
    if not isinstance(chain, list):
        return ""
    for item in chain:
        if isinstance(item, dict) and item.get("claim"):
            return str(item["claim"])
    return ""


def _main_story_fallback(analysis: dict, verdict: dict, watchboard_backtest: dict | None = None) -> str:
    claim = _first_claim(analysis)
    tension = analysis.get("core_tension") or claim
    if not tension:
        return "主線故事尚未生成；系統保留研究帳本、因果圖與觀察清單，等待下一輪 narrator 重新輸出。"

    watchboard_summary = (watchboard_backtest or {}).get("summary") or "尚無前一份觀察清單可回測。"
    chain = [
        item for item in analysis.get("inference_chain", [])
        if isinstance(item, dict)
    ]
    mechanism_lines = []
    for item in chain[:4]:
        mechanism = item.get("mechanism") or item.get("claim") or ""
        if not mechanism:
            continue
        evidence_keys = [
            str(ev.get("data_key")) for ev in item.get("evidence", [])
            if isinstance(ev, dict) and ev.get("data_key")
        ]
        left = "、".join(evidence_keys[:3]) or "核心數據"
        predictions = "、".join(str(pred) for pred in item.get("asset_predictions", [])[:2]) or "資產定價"
        mechanism_lines.append(f"{left} 透過 {mechanism} 影響 {predictions}。")
    if not mechanism_lines:
        mechanism_lines.append(f"核心張力透過 {claim or tension} 影響今日資產定價。")

    weak_notes = _weak_data_summary(analysis, verdict)
    sustained = [
        item for item in verdict.get("attack_verdicts", [])
        if isinstance(item, dict) and item.get("verdict") == "SUSTAINED"
    ]
    challenge = "目前沒有成功挑戰推翻主線，但這不代表結論免於回測。"
    if sustained:
        challenge = f"成功挑戰要求修正：{sustained[0].get('narrative') or sustained[0].get('reason', '')}"

    return "\n\n".join([
        f"今日真正改變不是單一價格跳動，而是{tension}。昨日觀察回測先給出底線：{watchboard_summary} 這決定了今天的新判斷必須先承認哪些條件已被驗證、哪些仍只是噪音。",
        " ".join(mechanism_lines[:2]),
        f"{challenge} {weak_notes}",
        "配置上，這份備援主線只允許小幅調整：高信心且資料確認者可以加碼或持有；依賴弱資料者必須等待、減碼或用避險保護。下一次資料公布若沒有觸發觀察清單，今日主線應被視為暫時假說而非定案。",
    ])


def _tgri_fallback(geopolitical_package: dict) -> str:
    tgri = geopolitical_package.get("tgri", {}) if isinstance(geopolitical_package, dict) else {}
    if not isinstance(tgri, dict) or not tgri:
        return "TGRI：{{MISSING_DATA:—}}。今日未取得可發布的地緣政治張力卡。"
    score = tgri.get("score", "—")
    trend = tgri.get("trend", "未知")
    dominant = tgri.get("dominant_signal") or tgri.get("dominant_driver") or "未標示"
    return f"TGRI：{{{{confirmed:{score}}}}}；趨勢：{trend}；主導信號：{dominant}。"


def _thesis_fallback(analysis: dict) -> str:
    updates = analysis.get("thesis_updates", [])
    if not isinstance(updates, list) or not updates:
        return "今日核心判斷沒有新的可發布更新。"
    lines = []
    for item in updates[:5]:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("thesis_id") or "未命名核心判斷"
        note = item.get("attention") or item.get("reason") or item.get("update") or "狀態待追蹤。"
        lines.append(f"### {title}\n{note}")
    return "\n\n".join(lines) or "今日核心判斷沒有新的可發布更新。"


def _compass_fallback(analysis: dict) -> str:
    compass = analysis.get("compass", [])
    rows = [
        "| 資產 | 方向 | 動作 | 信心 | 一句理由 |",
        "| --- | --- | --- | --- | --- |",
    ]
    if isinstance(compass, list):
        for item in compass[:6]:
            if not isinstance(item, dict):
                continue
            asset = item.get("asset") or "未標示資產"
            direction = item.get("direction") or "中性"
            confidence = item.get("adjusted_confidence") or item.get("raw_confidence")
            confidence_text = f"{confidence:.0%}" if isinstance(confidence, (int, float)) else "未量化"
            action = _position_action(direction, confidence)
            reason = item.get("reason") or item.get("claim") or "映射自今日主線判斷。"
            rows.append(f"| {asset} | {direction} | {action} | {confidence_text} | {reason} |")
    if len(rows) == 2:
        rows.append("| 全資產 | 中性 | 等待 | 未量化 | 缺少足夠推論鏈，暫不給方向性配置。 |")
    return "\n".join(rows)


def _paragraphs(text: str) -> list[str]:
    return [para.strip() for para in re.split(r"\n\s*\n", str(text or "")) if para.strip()]


def _weak_evidence_without_haircut(analysis: dict, verdict: dict) -> list[str]:
    down_adjusted = {
        item.get("inf_id") for item in verdict.get("confidence_adjustments", [])
        if isinstance(item, dict) and item.get("direction") == "down" and item.get("inf_id")
    }
    misses = []
    for idx, item in enumerate(analysis.get("inference_chain", [])[:6], 1):
        if not isinstance(item, dict):
            continue
        weak_evidence = [
            ev for ev in item.get("evidence", [])
            if isinstance(ev, dict) and ev.get("quality") in WEAK_QUALITIES
        ]
        if not weak_evidence:
            continue
        raw = item.get("raw_confidence")
        adjusted = item.get("adjusted_confidence")
        inf_id = item.get("id")
        explicit_haircut = (
            isinstance(raw, (int, float))
            and isinstance(adjusted, (int, float))
            and adjusted <= raw - 0.02
        )
        if not explicit_haircut and inf_id not in down_adjusted:
            misses.append(str(item.get("claim") or inf_id or f"claim_{idx}")[:40])
    return misses


def _weak_data_summary(analysis: dict, verdict: dict) -> str:
    weak_keys: list[str] = []
    for item in analysis.get("inference_chain", [])[:6]:
        if not isinstance(item, dict):
            continue
        for ev in item.get("evidence", []):
            if isinstance(ev, dict) and ev.get("quality") in WEAK_QUALITIES and ev.get("data_key"):
                weak_keys.append(str(ev["data_key"]))
    for item in verdict.get("data_integrity_violations", []):
        if isinstance(item, dict) and item.get("key"):
            weak_keys.append(str(item["key"]))
    if not weak_keys:
        return "目前沒有主要弱資料需要額外信心折扣。"
    unique = list(dict.fromkeys(weak_keys))[:5]
    return f"信心折扣必須明示，因為核心論證仍依賴弱資料：{', '.join(unique)}。"


def _position_action(direction: Any, confidence: Any) -> str:
    direction_text = str(direction or "").lower()
    conf = confidence if isinstance(confidence, (int, float)) else None
    if conf is None or conf < 0.45:
        return "等待"
    if any(word in direction_text for word in ("hedge", "避險")):
        return "避險"
    if any(word in direction_text for word in ("down", "short", "減", "空")):
        return "減碼" if conf >= 0.55 else "等待"
    if any(word in direction_text for word in ("up", "long", "加", "多")):
        return "加碼" if conf >= 0.65 else "持有"
    return "持有" if conf >= 0.55 else "等待"
