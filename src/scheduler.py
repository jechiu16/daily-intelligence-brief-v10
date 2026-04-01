"""Scheduler — 讀取行事曆，輸出 calendar_package。純 Python，無 LLM。"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests

from src.config import FINNHUB_API_KEY, FRED_API_KEY, PROJECT_ROOT, MISSING_DATA

logger = logging.getLogger(__name__)

# FOMC blackout: 從 FOMC 會議前兩週的週六開始
CALENDAR_STATIC_PATH = PROJECT_ROOT / "calendar_static.json"


def load_static_calendar() -> list[dict]:
    """讀取手動維護的固定行程（FOMC、NFP 等）。"""
    if not CALENDAR_STATIC_PATH.exists():
        logger.warning("calendar_static.json not found, using empty static calendar")
        return []
    with open(CALENDAR_STATIC_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_finnhub_calendar(from_date: str, to_date: str) -> list[dict]:
    """Finnhub Economic Calendar API。"""
    if not FINNHUB_API_KEY:
        logger.warning("FINNHUB_API_KEY not set, skipping Finnhub calendar")
        return []
    try:
        url = "https://finnhub.io/api/v1/calendar/economic"
        params = {"from": from_date, "to": to_date, "token": FINNHUB_API_KEY}
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        events = []
        for item in data.get("economicCalendar", []):
            importance = "HIGH" if item.get("impact", 0) >= 3 else (
                "MEDIUM" if item.get("impact", 0) == 2 else "LOW"
            )
            events.append({
                "time": item.get("time", ""),
                "event": item.get("event", ""),
                "country": item.get("country", ""),
                "importance": importance,
                "consensus": item.get("estimate"),
                "previous": item.get("prev"),
                "actual": item.get("actual"),
            })
        return events
    except Exception as e:
        logger.error(f"Finnhub calendar fetch failed: {e}")
        return []


def fetch_fred_releases(from_date: str, to_date: str) -> list[dict]:
    """FRED Release Calendar。"""
    if not FRED_API_KEY:
        logger.warning("FRED_API_KEY not set, skipping FRED releases")
        return []
    try:
        url = "https://api.stlouisfed.org/fred/releases/dates"
        params = {
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "realtime_start": from_date,
            "realtime_end": to_date,
            "include_release_dates_with_no_data": "false",
        }
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        events = []
        for item in data.get("release_dates", []):
            events.append({
                "time": item.get("date", ""),
                "event": f"FRED: {item.get('release_name', '')}",
                "importance": "MEDIUM",
                "source": "FRED",
            })
        return events
    except Exception as e:
        logger.error(f"FRED releases fetch failed: {e}")
        return []


def classify_importance(event_name: str) -> str:
    """根據事件名稱判斷重要性。"""
    high_keywords = [
        "NFP", "Non-Farm", "FOMC", "CPI", "GDP", "PCE",
        "ECB", "BOJ", "PPI", "Retail Sales", "PMI",
    ]
    for kw in high_keywords:
        if kw.lower() in event_name.lower():
            return "HIGH"
    return "MEDIUM"


def find_next_major(events: list[dict], today: datetime) -> tuple[int, str]:
    """找到下一個重大事件的天數和名稱。"""
    major_events = [e for e in events if e.get("importance") == "HIGH"]
    if not major_events:
        return -1, MISSING_DATA

    # 嘗試解析日期並找最近的未來事件
    for evt in sorted(major_events, key=lambda x: x.get("time", "")):
        try:
            evt_date = datetime.strptime(evt["time"][:10], "%Y-%m-%d")
            delta = (evt_date - today).days
            if delta >= 0:
                return delta, evt.get("event", "Unknown")
        except (ValueError, TypeError):
            continue
    return -1, MISSING_DATA


def compute_fomc_info(today: datetime, static_events: list[dict]) -> dict:
    """計算 FOMC 相關資訊。"""
    fomc_dates = [
        e["date"] for e in static_events
        if "FOMC" in e.get("event", "").upper() and "date" in e
    ]
    fomc_days_out = -1
    blackout = False
    for fd in sorted(fomc_dates):
        try:
            fomc_dt = datetime.strptime(fd, "%Y-%m-%d")
            delta = (fomc_dt - today).days
            if delta >= 0:
                fomc_days_out = delta
                # Blackout: FOMC 前兩週的週六開始（約 12 天前）
                blackout = delta <= 12
                break
        except (ValueError, TypeError):
            continue
    return {"fomc_days_out": fomc_days_out, "blackout_period": blackout}


def run_scheduler(today: datetime | None = None) -> dict:
    """主入口：產生 calendar_package。"""
    if today is None:
        today = datetime.now()

    today_str = today.strftime("%Y-%m-%d")
    end_7d = (today + timedelta(days=7)).strftime("%Y-%m-%d")

    # 1. 取得各來源事件
    static_events = load_static_calendar()
    finnhub_events = fetch_finnhub_calendar(today_str, end_7d)
    fred_events = fetch_fred_releases(today_str, end_7d)

    # 2. 合併並分類
    all_events = finnhub_events + fred_events
    for evt in static_events:
        evt_date = evt.get("date", "")
        if today_str <= evt_date <= end_7d:
            all_events.append({
                "time": evt_date,
                "event": evt.get("event", ""),
                "importance": evt.get("importance", classify_importance(evt.get("event", ""))),
                "source": "static",
            })

    # 3. 分類今日 vs 未來7天
    today_events = [e for e in all_events if e.get("time", "")[:10] == today_str]
    next_7_days = [e for e in all_events if e.get("time", "")[:10] > today_str]

    # 4. 計算輔助指標
    days_to_next_major, next_major_event = find_next_major(all_events, today)
    fomc_info = compute_fomc_info(today, static_events)

    calendar_package = {
        "date": today_str,
        "today_events": today_events,
        "next_7_days": next_7_days,
        "days_to_next_major": days_to_next_major,
        "next_major_event": next_major_event,
        "fomc_days_out": fomc_info["fomc_days_out"],
        "blackout_period": fomc_info["blackout_period"],
        "sources_used": {
            "finnhub": len(finnhub_events) > 0,
            "fred": len(fred_events) > 0,
            "static": len(static_events) > 0,
        },
    }

    logger.info(
        f"Scheduler: {len(today_events)} today events, "
        f"{len(next_7_days)} next 7 days, "
        f"next major in {days_to_next_major}d ({next_major_event})"
    )
    return calendar_package


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_scheduler()
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
