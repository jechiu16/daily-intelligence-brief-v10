from __future__ import annotations
"""Structured research ledger utilities for GitHub snapshots and Notion notes."""

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from src.config import MISSING_DATA, SNAPSHOTS_DIR

QUALITY_TOKEN_RE = re.compile(r"\{\{(\w+):([^}]+)\}\}")
NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")

INDICATOR_KEYWORDS = (
    ("tips", "tips_10y"),
    ("實質利率", "tips_10y"),
    ("vix", "vix"),
    ("spx", "spx"),
    ("brent", "brent"),
    ("wti", "wti"),
    ("外資", "tw_foreign_net"),
    ("usdtwd", "usdtwd"),
    ("美元/台幣", "usdtwd"),
    ("nfci", "nfci"),
    ("breakeven", "breakeven_5y5y"),
    ("10y", "us10y"),
    ("美債", "us10y"),
    ("tgri", "tgri_score"),
)


def load_previous_snapshot(today_str: str, snapshots_dir: Path | None = None) -> dict:
    """Load the most recent daily snapshot before today, if available."""
    snapshots_dir = snapshots_dir or SNAPSHOTS_DIR
    try:
        today = date.fromisoformat(today_str)
    except ValueError:
        return {}

    candidates: list[tuple[date, Path]] = []
    for path in snapshots_dir.glob("*.json"):
        try:
            dt = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if dt < today:
            candidates.append((dt, path))

    if not candidates:
        return {}

    _, path = max(candidates, key=lambda item: item[0])
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def extract_watchboard_items(report_or_snapshot: dict) -> list[dict]:
    """Extract structured watchboard rows from a report or saved snapshot."""
    if not isinstance(report_or_snapshot, dict):
        return []

    existing = (
        report_or_snapshot.get("watchboard", {}).get("items")
        if isinstance(report_or_snapshot.get("watchboard"), dict)
        else None
    )
    if isinstance(existing, list):
        return existing

    ledger_items = (
        report_or_snapshot.get("research_ledger", {}).get("watchboard_items")
        if isinstance(report_or_snapshot.get("research_ledger"), dict)
        else None
    )
    if isinstance(ledger_items, list):
        return ledger_items

    sections = report_or_snapshot.get("sections") or report_or_snapshot.get("report_sections") or {}
    watchboard_text = sections.get("watchboard", "") if isinstance(sections, dict) else ""
    rows = _parse_markdown_table(watchboard_text)
    if len(rows) < 2:
        return []

    items = []
    for idx, row in enumerate(rows[1:], 1):
        if len(row) < 4:
            continue
        indicator, reading, trigger, implication = row[:4]
        quality, value = _extract_quality_value(reading)
        data_key = _infer_data_key(indicator)
        items.append({
            "id": f"WB_{idx:03d}",
            "indicator": indicator,
            "data_key": data_key,
            "current_reading": reading,
            "current_quality": quality,
            "current_value": value,
            "trigger_condition": trigger,
            "implication": implication,
        })
    return items


def build_watchboard_seed(
    data_package: dict,
    verdict: dict | None = None,
    calendar_package: dict | None = None,
) -> str:
    """Build a deterministic 24-72h watchboard table from available inputs."""
    verdict = verdict or {}
    calendar_package = calendar_package or {}
    rows = [
        "| 儀表板 | 目前讀數 | 觸發條件 | 若觸發代表什麼 |",
        "| --- | --- | --- | --- |",
    ]

    for key, label, trigger, implication in [
        ("tips_10y", "10Y TIPS 實質利率", "連續回升且突破近期高點", "估值壓制重新成為主導約束"),
        ("vix", "VIX", "升破 20 且 SPX 同步轉弱", "低波動自滿開始失效"),
        ("brent", "Brent 原油", "重新站上 100 或跌破 88", "滯脹尾部風險重新定價或能源壓力撤銷"),
        ("tw_foreign_net", "台股外資", "連續兩日轉為大額賣超", "台股 risk-on 資金流可能反轉"),
        ("nfci", "NFCI", "由負值快速靠近 0", "流動性環境不再支撐高風險資產"),
    ]:
        quality, value = _lookup_current_value(key, data_package)
        rows.append(f"| {label} | {_format_quality(quality, value)} | {trigger} | {implication} |")

    sustained = [
        item for item in verdict.get("attack_verdicts", [])
        if isinstance(item, dict) and item.get("verdict") == "SUSTAINED"
    ]
    if sustained:
        reason = sustained[0].get("narrative") or sustained[0].get("reason", "")
        rows.append(f"| 成功挑戰後續驗證 | {_clean_table_cell(reason, 60)} | 下一次數據更新未修復該弱點 | 原始推論需繼續下修 |")

    events = calendar_package.get("today_events", []) if isinstance(calendar_package, dict) else []
    if events:
        event = (
            events[0].get("event") or events[0].get("name") or events[0].get("title")
            if isinstance(events[0], dict)
            else str(events[0])
        )
        rows.append(f"| 事件日曆 | {_clean_table_cell(event, 60)} | 公布結果偏離市場敘事 | regime 需要重新校準 |")

    return "\n".join(rows)


def build_watchboard_backtest(previous_snapshot: dict, current_data: dict) -> dict:
    """Evaluate yesterday's watchboard against today's market data."""
    items = extract_watchboard_items(previous_snapshot)
    if not items:
        return {
            "status": "no_prior_watchboard",
            "summary": "沒有可回測的前一份觀察清單。",
            "items": [],
        }

    results = []
    for item in items:
        data_key = item.get("data_key") or _infer_data_key(item.get("indicator", ""))
        current_quality, current_value = _lookup_current_value(data_key, current_data)
        previous_value = _coerce_float(item.get("current_value"))
        trigger = item.get("trigger_condition", "")
        status, note = _evaluate_trigger(
            trigger,
            previous_value,
            current_value,
            current_data=current_data,
            primary_key=data_key,
        )
        results.append({
            "id": item.get("id"),
            "indicator": item.get("indicator"),
            "data_key": data_key,
            "previous_reading": item.get("current_reading"),
            "current_reading": _format_quality(current_quality, current_value),
            "trigger_condition": trigger,
            "status": status,
            "implication": item.get("implication", ""),
            "note": note,
        })

    triggered = sum(1 for item in results if item["status"] == "triggered")
    partial = sum(1 for item in results if item["status"] == "partial")
    unresolved = sum(1 for item in results if item["status"] == "unresolved")
    return {
        "status": "evaluated",
        "summary": f"昨日觀察清單：{triggered} 項觸發、{partial} 項部分觸發、{unresolved} 項無法裁決。",
        "items": results,
    }


def format_watchboard_backtest(backtest: dict) -> str:
    if not backtest or backtest.get("status") == "no_prior_watchboard":
        return backtest.get("summary", "沒有可回測的前一份觀察清單。") if isinstance(backtest, dict) else ""

    rows = [
        "| 昨日觀察項 | 今日讀數 | 狀態 | 含義 |",
        "| --- | --- | --- | --- |",
    ]
    for item in backtest.get("items", [])[:6]:
        rows.append(
            f"| {item.get('indicator', '')} | {item.get('current_reading', '')} | "
            f"{_status_label(item.get('status', ''))} | {item.get('implication') or item.get('note', '')} |"
        )
    return "\n".join(rows)


def build_institutional_brief(
    *,
    analysis: dict,
    verdict: dict,
    watchboard_backtest: dict | None = None,
    watchboard_items: list[dict] | None = None,
) -> str:
    """Build the first-screen institutional summary for Notion."""
    inference_chain = analysis.get("inference_chain", []) if isinstance(analysis, dict) else []
    primary = next((inf for inf in inference_chain if isinstance(inf, dict)), {})
    sustained = [
        item for item in verdict.get("attack_verdicts", [])
        if isinstance(item, dict) and item.get("verdict") == "SUSTAINED"
    ]
    adjustments = [
        item for item in verdict.get("confidence_adjustments", [])
        if isinstance(item, dict)
    ]

    changed = analysis.get("core_tension") or primary.get("claim") or "今日沒有足夠資料形成新的核心變化。"
    mechanism = primary.get("mechanism") or primary.get("claim") or "主導機制尚未明確。"
    falsifier = (
        sustained[0].get("narrative") if sustained else
        primary.get("invalidation_condition") or
        analysis.get("question_for_devil") or
        "尚未定義明確反證。"
    )
    backtest_summary = (watchboard_backtest or {}).get("summary") or "沒有可回測的前一份觀察清單。"
    next_watch = "尚未形成新的觀察清單。"
    if watchboard_items:
        first = watchboard_items[0]
        next_watch = f"{first.get('indicator', '')}：{first.get('trigger_condition', '')}"
    if adjustments:
        adj = adjustments[0]
        mechanism += f"（信心修正：{adj.get('direction')} {adj.get('magnitude')}）"

    return "\n".join([
        "| 機構快照 | 內容 |",
        "| --- | --- |",
        f"| 今日真正改變 | {_clean_table_cell(changed, 180)} |",
        f"| 主導機制 | {_clean_table_cell(mechanism, 180)} |",
        f"| 最大反證 | {_clean_table_cell(falsifier, 180)} |",
        f"| 昨日驗證 | {_clean_table_cell(backtest_summary, 180)} |",
        f"| 接下來 24-72 小時 | {_clean_table_cell(next_watch, 180)} |",
    ])


def build_causal_graph(analysis: dict, verdict: dict | None = None) -> dict:
    """Create a compact causal graph from inference_chain and verdict adjustments."""
    verdict = verdict or {}
    adjustments = {
        adj.get("inf_id"): adj for adj in verdict.get("confidence_adjustments", [])
        if isinstance(adj, dict) and adj.get("inf_id")
    }

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    lines: list[str] = []

    for inf in analysis.get("inference_chain", [])[:8]:
        if not isinstance(inf, dict):
            continue
        inf_id = inf.get("id", "INF_UNKNOWN")
        claim = inf.get("claim", "")
        nodes.append({"id": inf_id, "type": "inference", "label": claim[:80]})

        evidence_keys = [
            ev.get("data_key") for ev in inf.get("evidence", [])
            if isinstance(ev, dict) and ev.get("data_key")
        ]
        for key in evidence_keys:
            ev_id = f"DATA_{key}"
            if not any(node["id"] == ev_id for node in nodes):
                nodes.append({"id": ev_id, "type": "data", "label": key})
            edges.append({"from": ev_id, "to": inf_id, "label": "supports"})

        predictions = inf.get("asset_predictions", []) or []
        for pred in predictions:
            asset_id = f"ASSET_{pred}"
            if not any(node["id"] == asset_id for node in nodes):
                nodes.append({"id": asset_id, "type": "asset_prediction", "label": pred})
            edges.append({"from": inf_id, "to": asset_id, "label": "implies"})

        adj = adjustments.get(inf_id)
        adj_text = ""
        if adj:
            adj_text = f"；裁決修正：{adj.get('direction')} {adj.get('magnitude')}"

        left = ", ".join(evidence_keys) if evidence_keys else "未列 evidence"
        right = ", ".join(predictions) if predictions else "無直接資產映射"
        mechanism = inf.get("mechanism") or claim
        lines.append(f"{left} → {mechanism} → {right}{adj_text}")

    return {"nodes": nodes, "edges": edges, "text": "\n".join(f"- {line}" for line in lines)}


def format_causal_graph(graph: dict) -> str:
    text = graph.get("text", "") if isinstance(graph, dict) else ""
    return text or "今日推論鏈不足以生成因果圖。"


def build_research_ledger(
    *,
    analysis: dict,
    verdict: dict,
    report: dict,
    calibrated_chain: list[dict] | None = None,
) -> dict:
    """Build the immutable research ledger object saved in GitHub snapshots."""
    chain = calibrated_chain or analysis.get("inference_chain", [])
    claims = []
    for inf in chain:
        if not isinstance(inf, dict):
            continue
        claims.append({
            "id": inf.get("id"),
            "claim": inf.get("claim"),
            "mechanism": inf.get("mechanism"),
            "evidence_keys": [
                ev.get("data_key") for ev in inf.get("evidence", [])
                if isinstance(ev, dict) and ev.get("data_key")
            ],
            "raw_confidence": inf.get("raw_confidence"),
            "adjusted_confidence": inf.get("adjusted_confidence"),
            "invalidation_condition": inf.get("invalidation_condition"),
            "asset_predictions": inf.get("asset_predictions", []),
        })

    return {
        "schema_version": "research-ledger-v1",
        "institutional_brief": report.get("sections", {}).get("institutional_brief", ""),
        "claims": claims,
        "attack_verdicts": verdict.get("attack_verdicts", []),
        "confidence_adjustments": verdict.get("confidence_adjustments", []),
        "watchboard_items": extract_watchboard_items(report),
        "watchboard_backtest": report.get("_watchboard_backtest", {}),
        "causal_graph": build_causal_graph(analysis, verdict),
        "editorial_contract": report.get("_editorial_contract", {}),
        "quality_assessment": report.get("_quality_assessment", {}),
    }


def _parse_markdown_table(text: str) -> list[list[str]]:
    rows = []
    for line in str(text).splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if all(c in "-| :" for c in line):
            continue
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if cells:
            rows.append(cells)
    return rows


def _extract_quality_value(text: str) -> tuple[str, Any]:
    match = QUALITY_TOKEN_RE.search(str(text))
    if not match:
        return MISSING_DATA, MISSING_DATA
    quality, raw = match.group(1), match.group(2)
    return quality, _coerce_float(raw)


def _infer_data_key(indicator: str) -> str:
    lower = str(indicator).lower()
    for keyword, key in INDICATOR_KEYWORDS:
        if keyword.lower() in lower:
            return key
    return ""


def _lookup_current_value(data_key: str, current_data: dict) -> tuple[str, Any]:
    if data_key == "tgri_score":
        tgri = current_data.get("tgri", {})
        if isinstance(tgri, dict):
            return "confirmed", tgri.get("score", MISSING_DATA)
    item = current_data.get(data_key, {}) if isinstance(current_data, dict) else {}
    if not isinstance(item, dict):
        return MISSING_DATA, MISSING_DATA
    value = item.get("price") if item.get("price") is not None else item.get("value", MISSING_DATA)
    return item.get("quality", MISSING_DATA), value


def _evaluate_trigger(
    trigger: str,
    previous_value: float | None,
    current_value: Any,
    *,
    current_data: dict | None = None,
    primary_key: str = "",
) -> tuple[str, str]:
    current = _coerce_float(current_value)
    if current is None:
        return "unresolved", "缺少今日讀數。"

    trigger_text = str(trigger)
    if any(sep in trigger_text for sep in (" 或 ", "或")):
        parts = _split_or_trigger(trigger_text)
        if len(parts) > 1:
            evaluated = [
                _evaluate_single_trigger(part, previous_value, current, current_data=current_data, primary_key=primary_key)
                for part in parts
            ]
            statuses = [status for status, _ in evaluated]
            notes = [note for _, note in evaluated]
            if any(status == "triggered" for status in statuses):
                return "triggered", "；".join(notes)
            if any(status == "partial" for status in statuses):
                return "partial", "；".join(notes)
            if all(status == "not_triggered" for status in statuses):
                return "not_triggered", "；".join(notes)
            return "unresolved", "；".join(notes)

    if any(sep in trigger_text for sep in (" 且 ", "且", "同時")):
        parts = _split_compound_trigger(trigger_text)
        if len(parts) > 1:
            evaluated = [
                _evaluate_single_trigger(part, previous_value, current, current_data=current_data, primary_key=primary_key)
                for part in parts
            ]
            statuses = [status for status, _ in evaluated]
            notes = [note for _, note in evaluated]
            if all(status == "triggered" for status in statuses):
                return "triggered", "；".join(notes)
            if any(status in ("triggered", "partial") for status in statuses):
                return "partial", "；".join(notes)
            if all(status == "not_triggered" for status in statuses):
                return "not_triggered", "；".join(notes)
            return "unresolved", "；".join(notes)

    return _evaluate_single_trigger(trigger_text, previous_value, current, current_data=current_data, primary_key=primary_key)


def _evaluate_single_trigger(
    trigger_text: str,
    previous_value: float | None,
    current: float,
    *,
    current_data: dict | None = None,
    primary_key: str = "",
) -> tuple[str, str]:
    scoped_key = _infer_data_key(trigger_text) or primary_key
    scoped_current = current
    if current_data and scoped_key and scoped_key != primary_key:
        _, scoped_value = _lookup_current_value(scoped_key, current_data)
        parsed = _coerce_float(scoped_value)
        if parsed is None:
            return "unresolved", f"{scoped_key} 缺少今日讀數。"
        scoped_current = parsed

    numbers = [_coerce_float(n) for n in NUMBER_RE.findall(trigger_text)]
    numbers = [n for n in numbers if n is not None]
    threshold = numbers[0] if numbers else None

    if "連續" in trigger_text:
        base_text = trigger_text.replace("連續", "")
        base_status, base_note = _evaluate_single_trigger(
            base_text,
            previous_value,
            scoped_current,
            current_data=current_data,
            primary_key=primary_key,
        )
        if base_status == "triggered":
            return "partial", f"{base_note}；但連續條件需要多日資料確認。"
        return base_status, base_note

    if any(word in trigger_text for word in ("變化超過", "上升超過", "下降超過")) and threshold is not None:
        if previous_value is None:
            return "unresolved", "缺少前值，無法判斷相對變化。"
        pct_change = ((scoped_current - previous_value) / abs(previous_value) * 100) if previous_value else None
        if pct_change is None:
            return "unresolved", "前值為 0，無法判斷相對變化。"
        if "下降" in trigger_text:
            hit = pct_change <= -abs(threshold)
        elif "上升" in trigger_text:
            hit = pct_change >= abs(threshold)
        else:
            hit = abs(pct_change) >= abs(threshold)
        status = "triggered" if hit else "not_triggered"
        return status, f"相對變化 {pct_change:+.2f}%。"

    scoped_change = _lookup_current_change(scoped_key, current_data or {})
    if any(word in trigger_text for word in ("轉弱", "走弱", "轉跌", "下跌")) and threshold is None:
        if scoped_change is None:
            return "unresolved", f"{scoped_key or '指標'} 缺少漲跌幅，無法判斷是否轉弱。"
        return ("triggered", f"{scoped_key} 今日轉弱。") if scoped_change < 0 else ("not_triggered", f"{scoped_key} 今日未轉弱。")

    if any(word in trigger_text for word in ("轉強", "走強", "轉漲", "上漲")) and threshold is None:
        if scoped_change is None:
            return "unresolved", f"{scoped_key or '指標'} 缺少漲跌幅，無法判斷是否轉強。"
        return ("triggered", f"{scoped_key} 今日轉強。") if scoped_change > 0 else ("not_triggered", f"{scoped_key} 今日未轉強。")

    if any(word in trigger_text for word in ("升破", "突破", "站上", "高於", "大於")) and threshold is not None:
        return ("triggered", f"今日 {scoped_current} 已高於 {threshold}。") if scoped_current >= threshold else ("not_triggered", f"今日 {scoped_current} 未高於 {threshold}。")

    if any(word in trigger_text for word in ("跌破", "低於", "小於")) and threshold is not None:
        return ("triggered", f"今日 {scoped_current} 已低於 {threshold}。") if scoped_current <= threshold else ("not_triggered", f"今日 {scoped_current} 未低於 {threshold}。")

    if "靠近 0" in trigger_text or "靠近零" in trigger_text:
        if abs(scoped_current) <= 0.1:
            return "triggered", "今日讀數已接近 0。"
        if previous_value is not None and abs(scoped_current) < abs(previous_value):
            return "partial", "今日讀數正朝 0 靠近。"
        return "not_triggered", "今日讀數未朝 0 靠近。"

    if "賣超" in trigger_text or "轉負" in trigger_text:
        if scoped_current < 0:
            return "partial" if "連續" in trigger_text else "triggered", "今日讀數已轉負。"
        return "not_triggered", "今日讀數仍未轉負。"

    return "unresolved", "觸發條件需要人工或多日資料判讀。"


def _split_compound_trigger(trigger_text: str) -> list[str]:
    parts = re.split(r"\s*(?:且|同時)\s*", trigger_text)
    return [part.strip(" ，,") for part in parts if part.strip(" ，,")]


def _split_or_trigger(trigger_text: str) -> list[str]:
    parts = re.split(r"\s*或\s*", trigger_text)
    return [part.strip(" ，,") for part in parts if part.strip(" ，,")]


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    match = NUMBER_RE.search(text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _format_quality(quality: str, value: Any) -> str:
    if value == MISSING_DATA:
        return "{{MISSING_DATA:—}}"
    return f"{{{{{quality}:{value}}}}}"


def _lookup_current_change(data_key: str, current_data: dict) -> float | None:
    item = current_data.get(data_key, {}) if isinstance(current_data, dict) else {}
    if not isinstance(item, dict):
        return None
    return _coerce_float(item.get("change_pct"))


def _status_label(status: str) -> str:
    return {
        "triggered": "觸發",
        "partial": "部分觸發",
        "not_triggered": "未觸發",
        "unresolved": "未裁決",
    }.get(status, status or "未裁決")


def _clean_table_cell(text: Any, max_len: int) -> str:
    value = str(text or "").replace("|", "｜").replace("\n", " ")
    return value[:max_len] + ("..." if len(value) > max_len else "")
