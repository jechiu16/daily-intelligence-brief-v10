from __future__ import annotations
"""TradingCalendar — 判斷交易日與市場狀態。

靜態假日清單（2025–2027），無需外部套件。
支援市場：us（NYSE）、asia（TWSE + TSE）。

使用方式：
    from src.trading_calendar import is_trading_day, last_trading_day, market_status_summary
"""

from datetime import date, timedelta

# ═══════════════════════════════════════════════════════════════════════════
# 靜態假日清單（格式：YYYY-MM-DD）
# ═══════════════════════════════════════════════════════════════════════════

# NYSE 假日 2025–2027
_NYSE_HOLIDAYS: set[str] = {
    # 2025
    "2025-01-01",  # New Year's Day
    "2025-01-20",  # MLK Day
    "2025-02-17",  # Presidents Day
    "2025-04-18",  # Good Friday
    "2025-05-26",  # Memorial Day
    "2025-06-19",  # Juneteenth
    "2025-07-04",  # Independence Day
    "2025-09-01",  # Labor Day
    "2025-11-27",  # Thanksgiving
    "2025-12-25",  # Christmas
    # 2026
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # MLK Day
    "2026-02-16",  # Presidents Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observed)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
    # 2027
    "2027-01-01",  # New Year's Day
    "2027-01-18",  # MLK Day
    "2027-02-15",  # Presidents Day
    "2027-03-26",  # Good Friday
    "2027-05-31",  # Memorial Day
    "2027-06-18",  # Juneteenth (observed)
    "2027-07-05",  # Independence Day (observed)
    "2027-09-06",  # Labor Day
    "2027-11-25",  # Thanksgiving
    "2027-12-24",  # Christmas (observed)
}

# TWSE（台灣） + TSE（日本東京）共同休市日
# 台灣：國定假日 + 春節；日本：國定假日 + 黃金週
_ASIA_HOLIDAYS: set[str] = {
    # 2025 台灣
    "2025-01-01",  # 元旦
    "2025-01-27", "2025-01-28", "2025-01-29", "2025-01-30",
    "2025-01-31", "2025-02-03",  # 春節
    "2025-02-28",  # 和平紀念日
    "2025-04-04",  # 兒童節
    "2025-04-05",  # 清明節（補假）
    "2025-05-01",  # 勞動節
    "2025-06-07",  # 端午節（週六補假）
    "2025-10-10",  # 國慶日
    # 2025 日本
    "2025-01-01",  "2025-01-13",  # 元旦、成人の日
    "2025-02-11",  # 建国記念の日
    "2025-03-20",  # 春分の日
    "2025-04-29",  # 昭和の日
    "2025-05-03", "2025-05-04", "2025-05-05", "2025-05-06",  # 黃金週
    "2025-07-21",  # 海の日
    "2025-08-11",  # 山の日
    "2025-09-15", "2025-09-23",  # 敬老の日、秋分の日
    "2025-10-13",  # スポーツの日
    "2025-11-03", "2025-11-24",  # 文化の日、勤労感謝の日
    # 2026 台灣
    "2026-01-01",  # 元旦
    "2026-01-26", "2026-01-27", "2026-01-28", "2026-01-29", "2026-01-30",  # 春節
    "2026-02-28",  # 和平紀念日（週六，前一天補假）
    "2026-04-03", "2026-04-04",  # 兒童節、清明節
    "2026-05-01",  # 勞動節
    "2026-06-19",  # 端午節（週五）
    "2026-10-09",  # 國慶日（週五，補假）
    # 2026 日本
    "2026-01-01", "2026-01-12",  # 元旦、成人の日
    "2026-02-11",  # 建国記念の日
    "2026-03-20",  # 春分の日
    "2026-04-29",  # 昭和の日
    "2026-05-03", "2026-05-04", "2026-05-05",  # 黃金週
    "2026-07-20",  # 海の日
    "2026-08-11",  # 山の日
    "2026-09-21", "2026-09-23",  # 敬老の日、秋分の日
    "2026-10-12",  # スポーツの日
    "2026-11-03", "2026-11-23",  # 文化の日、勤労感謝の日
    # 2027 台灣（預估）
    "2027-01-01",
    "2027-02-08", "2027-02-09", "2027-02-10", "2027-02-11", "2027-02-12",  # 春節
    "2027-04-05",  # 清明節
    "2027-04-30",  # 兒童節（補假）
    "2027-05-01",  # 勞動節
    "2027-10-09",  # 國慶日（週六補假）
}


def is_trading_day(d: date | str, market: str = "us") -> bool:
    """判斷 d 是否為交易日。market: 'us' 或 'asia'。"""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    # 週六週日通用
    if d.weekday() >= 5:
        return False
    holidays = _NYSE_HOLIDAYS if market == "us" else _ASIA_HOLIDAYS
    return d.isoformat() not in holidays


def last_trading_day(d: date | str, market: str = "us") -> date:
    """回傳 d 或之前最近的交易日（d 本身若是交易日則回傳 d）。"""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    while not is_trading_day(d, market):
        d -= timedelta(days=1)
    return d


def market_status_summary(d: date | str) -> dict:
    """回傳指定日期的市場狀態摘要，供 Orchestrator / Narrator 使用。

    回傳格式：
    {
        "report_date": "2026-04-06",
        "us_trading": False,
        "asia_trading": False,
        "us_status": "closed_weekend",          # closed_weekend / closed_holiday / open
        "asia_status": "closed_weekend",
        "us_last_trading_day": "2026-04-04",    # 上一個實際有數據的交易日
        "asia_last_trading_day": "2026-04-04",
        "is_non_trading_day": True,             # 全市場休市
        "temporal_note": "今天是週日（2026-04-06），所有市場休市。..."
    }
    """
    if isinstance(d, str):
        d = date.fromisoformat(d)

    us_open = is_trading_day(d, "us")
    asia_open = is_trading_day(d, "asia")

    def _status(open_flag: bool, d: date, market: str) -> str:
        if open_flag:
            return "open"
        if d.weekday() >= 5:
            return "closed_weekend"
        return "closed_holiday"

    us_status = _status(us_open, d, "us")
    asia_status = _status(asia_open, d, "asia")

    us_last = last_trading_day(d if us_open else d - timedelta(days=1), "us")
    asia_last = last_trading_day(d if asia_open else d - timedelta(days=1), "asia")

    is_non_trading = not us_open and not asia_open

    # 給 Narrator 的明確指示文字
    weekday_zh = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][d.weekday()]
    if is_non_trading:
        us_last_weekday_zh = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][us_last.weekday()]
        note = (
            f"今天是{weekday_zh}（{d.isoformat()}），美股與亞洲市場均休市。"
            f"以下所有市場數據均為上一個交易日（美股：{us_last.isoformat()}，"
            f"亞股：{asia_last.isoformat()}）的收盤數據。"
            f"撰寫報告時必須使用「{us_last_weekday_zh}收盤」或「{us_last.isoformat()} 收盤」等表述，"
            f"嚴禁使用「今日」描述市場動態。"
        )
    elif not asia_open and us_open:
        note = (
            f"今天是{weekday_zh}（{d.isoformat()}），亞洲市場休市（數據為 {asia_last.isoformat()} 收盤），"
            f"美股正常交易。描述亞洲數據時須標示為前一交易日數據。"
        )
    elif asia_open and not us_open:
        note = (
            f"今天是{weekday_zh}（{d.isoformat()}），美股休市（數據為 {us_last.isoformat()} 收盤），"
            f"亞洲市場正常交易。描述美股數據時須標示為前一交易日數據。"
        )
    else:
        note = f"今天是{weekday_zh}（{d.isoformat()}），美股與亞洲市場均為交易日。"

    return {
        "report_date": d.isoformat(),
        "us_trading": us_open,
        "asia_trading": asia_open,
        "us_status": us_status,
        "asia_status": asia_status,
        "us_last_trading_day": us_last.isoformat(),
        "asia_last_trading_day": asia_last.isoformat(),
        "is_non_trading_day": is_non_trading,
        "temporal_note": note,
    }
