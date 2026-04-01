"""LINE Publisher — LINE Notify 單向推播。"""

import logging
import re
from datetime import datetime

import requests

from src.config import LINE_NOTIFY_TOKEN, MISSING_DATA

logger = logging.getLogger(__name__)

LINE_NOTIFY_URL = "https://notify-api.line.me/api/notify"


def _send(message: str) -> bool:
    """發送 LINE Notify 通知。"""
    if not LINE_NOTIFY_TOKEN:
        logger.info("LINE_NOTIFY_TOKEN not set, skipping LINE notification")
        return False
    try:
        resp = requests.post(
            LINE_NOTIFY_URL,
            headers={"Authorization": f"Bearer {LINE_NOTIFY_TOKEN}"},
            data={"message": message},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("LINE Notify: sent successfully")
        return True
    except Exception as e:
        logger.error(f"LINE Notify failed: {e}")
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
    """發送日報摘要（200-500 字）。"""
    metadata = report.get("metadata", {})
    sections = report.get("sections", {})

    regime = metadata.get("regime", MISSING_DATA)
    regime_day = metadata.get("regime_day", 0)
    tension = _strip_quality_markers(sections.get("tension", ""))

    # 關鍵數據
    def _price(key: str) -> str:
        item = data_package.get(key, {})
        if not isinstance(item, dict):
            return "N/A"
        p = item.get("price") or item.get("value")
        return str(round(float(p), 1)) if p and p != MISSING_DATA else "N/A"

    def _change(key: str):
        item = data_package.get(key, {})
        return item.get("change_pct") if isinstance(item, dict) else None

    gold_p = _price("gold")
    brent_p = _price("brent")
    spx_p = _price("spx")
    vix_p = _price("vix")
    dxy_p = _price("dxy")
    twd_p = _price("usdtwd")
    tw_net = _price("tw_foreign_net")

    notion_line = f"\n完整報告 → {notion_url}" if notion_url else ""

    msg = f"""
📊 DIB 日報 {today_str}
Regime：{regime}（第{regime_day}天）

─────────────────
⚡ 今日核心
{tension[:100] if tension and tension != MISSING_DATA else '分析進行中'}

─────────────────
📈 關鍵數據
黃金    ${gold_p}  {_format_arrow(_change('gold'))}
布蘭特  ${brent_p}  {_format_arrow(_change('brent'))}
標普500  {spx_p}  {_format_arrow(_change('spx'))}
波動率  {vix_p}  {_format_arrow(_change('vix'))}
美元指數  {dxy_p}  {_format_arrow(_change('dxy'))}
台幣  {twd_p}  {_format_arrow(_change('usdtwd'))}
外資  {tw_net}億{notion_line}"""

    return _send(msg.strip())


def send_invalidator_alerts(triggered: list[dict]) -> bool:
    """發送 Invalidator 觸發警報。"""
    if not triggered:
        return False

    lines = ["🚨 Invalidator 觸發警報"]
    for t in triggered:
        lines.append(f"\nThesis {t.get('thesis_id')}：{t.get('invalidator', '')}")
        lines.append(f"觸發條件：{t.get('data_key')} = {t.get('value')}")
        lines.append(f"時間：{datetime.now().strftime('%H:%M')} 台北時間")

    return _send("\n".join(lines))


def send_pipeline_error(step: str, error: str) -> bool:
    """Pipeline CRITICAL 錯誤通知。"""
    msg = f"\n⚠️ DIB 系統警告\n步驟 {step} 失敗\n{error[:200]}"
    return _send(msg)


def send_citation_warning(integrity_score: float) -> bool:
    """Citation integrity 不足警告。"""
    msg = (
        f"\n⚠️ Citation Integrity 警告\n"
        f"完整性分數：{integrity_score:.2f}（門檻 0.8）\n"
        f"今日報告部分引用可能有誤，請謹慎參考。"
    )
    return _send(msg)
