from __future__ import annotations
"""LINE Publisher — LINE Messaging API Push Message。"""

import json
import logging
import re
from datetime import datetime, timezone

import requests

from src.config import LINE_CHANNEL_ACCESS_TOKEN, LINE_TARGET_ID, MISSING_DATA

logger = logging.getLogger(__name__)

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def _push(text: str) -> bool:
    """透過 Messaging API 推播純文字訊息。"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        logger.info("LINE_CHANNEL_ACCESS_TOKEN not set, skipping LINE notification")
        return False
    if not LINE_TARGET_ID:
        logger.info("LINE_TARGET_ID not set, skipping LINE notification")
        return False

    # LINE 單則訊息上限 5000 字元
    text = text[:4999]

    try:
        resp = requests.post(
            LINE_PUSH_URL,
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            data=json.dumps({
                "to": LINE_TARGET_ID,
                "messages": [{"type": "text", "text": text}],
            }),
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("LINE Messaging API: sent successfully")
        return True
    except Exception as e:
        logger.error(f"LINE Messaging API failed: {e}")
        return False


def _strip_quality_markers(text: str) -> str:
    """移除 {{quality:value}} 標記，只保留數值。"""
    return re.sub(r'\{\{\w+:([^}]+)\}\}', r'\1', text)


def _format_arrow(change_pct) -> str:
    """格式化漲跌箭頭。"""
    if change_pct is None:
        return "→"
    try:
        c = float(change_pct)
        if c > 1.5:
            return f"↑{c:+.1f}% ⚠️" if c > 3 else f"↑{c:+.1f}%"
        elif c < -1.5:
            return f"↓{c:.1f}% ⚠️" if c < -3 else f"↓{c:.1f}%"
        return "→"
    except (TypeError, ValueError):
        return "→"


def send_daily_summary(
    report: dict,
    data_package: dict,
    today_str: str,
    notion_url: str | None = None,
) -> bool:
    """發送日報摘要。"""
    metadata = report.get("metadata", {})
    sections = report.get("sections", {})

    regime = metadata.get("regime", MISSING_DATA)
    regime_day = metadata.get("regime_day", 0)
    tension = _strip_quality_markers(sections.get("tension", ""))

    def _price(key: str) -> str:
        item = data_package.get(key, {})
        if not isinstance(item, dict):
            return "N/A"
        p = item.get("price") or item.get("value")
        return str(round(float(p), 1)) if p and p != MISSING_DATA else "N/A"

    def _change(key: str):
        item = data_package.get(key, {})
        return item.get("change_pct") if isinstance(item, dict) else None

    notion_line = f"\n完整報告 → {notion_url}" if notion_url else ""

    msg = f"""📊 DIB 日報 {today_str}
Regime：{regime}（第{regime_day}天）

─────────────────
⚡ 今日核心
{tension[:120] if tension and tension != MISSING_DATA else '分析進行中'}

─────────────────
📈 關鍵數據
黃金    ${_price('gold')}  {_format_arrow(_change('gold'))}
布蘭特  ${_price('brent')}  {_format_arrow(_change('brent'))}
標普500  {_price('spx')}  {_format_arrow(_change('spx'))}
波動率  {_price('vix')}  {_format_arrow(_change('vix'))}
美元指數  {_price('dxy')}  {_format_arrow(_change('dxy'))}
台幣  {_price('usdtwd')}  {_format_arrow(_change('usdtwd'))}
外資  {_price('tw_foreign_net')}億{notion_line}"""

    return _push(msg.strip())


def send_invalidator_alerts(triggered: list[dict]) -> bool:
    """發送 Invalidator 觸發警報。"""
    if not triggered:
        return False

    lines = ["🚨 Invalidator 觸發警報"]
    for t in triggered:
        lines.append(f"\nThesis {t.get('thesis_id')}：{t.get('invalidator', '')}")
        lines.append(f"觸發條件：{t.get('data_key')} = {t.get('value')}")
        lines.append(f"時間：{datetime.now(timezone.utc).strftime('%H:%M')} UTC")

    return _push("\n".join(lines))


def send_pipeline_error(step: str, error: str) -> bool:
    """Pipeline CRITICAL 錯誤通知。"""
    msg = f"⚠️ DIB 系統警告\n步驟 {step} 失敗\n{error[:200]}"
    return _push(msg)


def send_weekly_summary(
    report: dict,
    weekly_context: dict,
    week_label: str,
    notion_url: str | None = None,
) -> bool:
    """發送週報摘要。"""
    metadata = report.get("metadata", {})
    sc = weekly_context.get("scorecard", {})
    re_ = weekly_context.get("regime_evolution", {})

    regime = re_.get("dominant_regime", "N/A")
    regime_day = re_.get("regime_day_count", 0)
    brier = sc.get("weekly_brier")
    hit_rate = sc.get("hit_rate")
    correct = sc.get("correct", 0)
    total = sc.get("total_filled", 0)
    cal_gap = sc.get("calibration_gap")
    days = weekly_context.get("days_available", 0)

    brier_str = f"{brier:.4f}" if brier is not None else "N/A"
    hit_str = f"{hit_rate:.0%} ({correct}/{total})" if hit_rate is not None else "N/A"
    gap_str = ""
    if cal_gap is not None:
        label = "過度自信" if cal_gap > 0 else "偏保守"
        gap_str = f"\n校準缺口：{cal_gap:+.3f}（{label}）"

    by_asset = sc.get("by_asset", {})
    best = worst = None
    for asset, info in by_asset.items():
        if info.get("n", 0) < 1:
            continue
        hr = info.get("hit_rate", 0)
        if best is None or hr > best[1]:
            best = (asset, hr)
        if worst is None or hr < worst[1]:
            worst = (asset, hr)

    asset_lines = []
    if best:
        asset_lines.append(f"最準：{best[0]} ({best[1]:.0%})")
    if worst and worst[0] != (best[0] if best else ""):
        asset_lines.append(f"最偏：{worst[0]} ({worst[1]:.0%})")
    asset_str = "　".join(asset_lines) if asset_lines else ""

    arc = report.get("sections", {}).get("narrative_arc", "")
    arc_excerpt = _strip_quality_markers(arc[:120]) + "..." if len(arc) > 120 else _strip_quality_markers(arc)

    watch = report.get("sections", {}).get("watch_list", "")
    watch_lines = []
    for line in watch.split("\n"):
        line = line.strip()
        if line.startswith("- ") or line.startswith("* "):
            watch_lines.append(f"  {_strip_quality_markers(line[:60])}")
            if len(watch_lines) >= 3:
                break

    notion_line = f"\n完整報告 → {notion_url}" if notion_url else ""

    msg = f"""📊 DIB 週報 {week_label}（{days} 個交易日）
Regime：{regime}（第{regime_day}天）

─────────────────
📖 本週故事線
{arc_excerpt}

─────────────────
🎯 校準表現
Brier Score：{brier_str}
命中率：{hit_str}{gap_str}
{asset_str}

🔭 下週關鍵觀測
{chr(10).join(watch_lines) if watch_lines else '（見完整報告）'}{notion_line}"""

    return _push(msg.strip())


def send_citation_warning(integrity_score: float) -> bool:
    """Citation integrity 不足警告。"""
    msg = (
        f"⚠️ Citation Integrity 警告\n"
        f"完整性分數：{integrity_score:.2f}（門檻 0.8）\n"
        f"今日報告部分引用可能有誤，請謹慎參考。"
    )
    return _push(msg)
