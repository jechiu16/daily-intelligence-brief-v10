"""Watchdog — 常駐監控，每30分鐘檢查 invalidator，每4小時掃描輿情。

v10.2: 改用 sleep-aware 輪詢機制，取代 schedule 排程庫。
- 電腦從睡眠喚醒後，會自動補跑錯過的每日 pipeline。
- 每日 pipeline 改為台灣時間 07:30 觸發。
- Invalidator / Sentiment 用「距上次執行秒數」判斷，不依賴牆鐘時間點。
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import pytz
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/dib_watchdog.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

TW_TZ = pytz.timezone("Asia/Taipei")

# 每日 pipeline 觸發時間（台灣時間）
DAILY_HOUR = 7
DAILY_MINUTE = 30

# 輪詢間隔（秒）
POLL_INTERVAL = 300  # 5 分鐘

# 追蹤上次執行時間
_last_invalidator_run: datetime | None = None
_last_sentiment_run: datetime | None = None
_last_pipeline_date: str | None = None  # "YYYY-MM-DD"（台灣日期）
_last_weekly_key: str | None = None    # "weekly_YYYY-MM-DD"


def run_invalidator_check():
    """每30分鐘執行一次 InvalidatorEngine。"""
    logger.info("Watchdog: running invalidator check")
    try:
        from src.data_watcher import run_data_watcher
        from src.invalidator_engine import check_all, load_active_theses

        data_package = run_data_watcher()
        active_theses = load_active_theses()
        triggered = check_all(data_package, active_theses)

        if triggered:
            logger.warning(f"Watchdog: {len(triggered)} invalidators triggered!")
        else:
            logger.info("Watchdog: no invalidators triggered")

    except Exception as e:
        logger.error(f"Watchdog invalidator check failed: {e}")


def run_sentiment_scan():
    """每4小時執行一次 SentimentWatcher（排程補充版）。"""
    logger.info("Watchdog: running sentiment scan")
    try:
        from src.config import MEMORY_DIR
        from src.sentiment_watcher import run_sentiment_watcher
        import json

        l3_path = MEMORY_DIR / "l3.json"
        active_theses = []
        if l3_path.exists():
            try:
                l3 = json.loads(l3_path.read_text(encoding="utf-8"))
                active_theses = [t for t in l3.get("active_theses", []) if t.get("status") == "active"]
            except Exception:
                pass

        result = run_sentiment_watcher(
            active_theses=active_theses,
            trigger="watchdog_scheduled",
        )
        signals = result.get("signals", [])
        logger.info(f"Watchdog: sentiment scan done, {len(signals)} signals")

    except Exception as e:
        logger.error(f"Watchdog sentiment scan failed: {e}")


def run_daily_pipeline_check():
    """觸發主 pipeline。"""
    logger.info("Watchdog: triggering daily pipeline")
    try:
        from src.orchestrator import run_daily_pipeline
        run_daily_pipeline()
    except Exception as e:
        logger.error(f"Watchdog daily pipeline failed: {e}")


def run_weekly_pipeline():
    """每週一觸發週報。"""
    logger.info("Watchdog: triggering weekly pipeline")
    try:
        from src.weekly_orchestrator import run_weekly_pipeline as _run
        _run()
    except Exception as e:
        logger.error(f"Watchdog weekly pipeline failed: {e}")


def _pipeline_ran_today(today_tw_str: str) -> bool:
    """檢查今日（台灣日期）的 snapshot 是否已存在。"""
    try:
        from src.config import PROJECT_ROOT
        snapshot_path = PROJECT_ROOT / "memory" / "daily_snapshots" / f"{today_tw_str}.json"
        return snapshot_path.exists()
    except Exception:
        return False


def _weekly_ran_this_week(today_tw_str: str) -> bool:
    """檢查本週的 weekly snapshot 是否已存在。"""
    try:
        from src.config import WEEKLY_SNAPSHOTS_DIR
        dt = datetime.strptime(today_tw_str, "%Y-%m-%d")
        iso_year, iso_week, _ = dt.isocalendar()
        week_label = f"{iso_year}-W{iso_week:02d}"
        return (WEEKLY_SNAPSHOTS_DIR / f"{week_label}.json").exists()
    except Exception:
        return False


def main():
    global _last_invalidator_run, _last_sentiment_run, _last_pipeline_date, _last_weekly_key

    logger.info("DIB v10 Watchdog starting (sleep-aware mode)...")
    logger.info(f"  - InvalidatorEngine: every 30 minutes (elapsed-based)")
    logger.info(f"  - SentimentWatcher: every 4 hours (elapsed-based)")
    logger.info(f"  - Daily pipeline: Mon-Sat {DAILY_HOUR:02d}:{DAILY_MINUTE:02d} TW (Mon=週末回顧)")
    logger.info(f"  - Weekly pipeline: Sunday 09:00 TW time")
    logger.info(f"  - Poll interval: {POLL_INTERVAL}s")

    # 立即執行一次 invalidator check
    run_invalidator_check()
    _last_invalidator_run = datetime.now()

    while True:
        now = datetime.now()
        now_tw = datetime.now(TW_TZ)
        today_tw_str = now_tw.strftime("%Y-%m-%d")

        # ── Invalidator：每30分鐘 ──────────────────────────────────────────
        elapsed_invalidator = (
            (now - _last_invalidator_run).total_seconds()
            if _last_invalidator_run else float("inf")
        )
        if elapsed_invalidator >= 1800:
            run_invalidator_check()
            _last_invalidator_run = datetime.now()

        # ── Sentiment：每4小時 ─────────────────────────────────────────────
        elapsed_sentiment = (
            (now - _last_sentiment_run).total_seconds()
            if _last_sentiment_run else float("inf")
        )
        if elapsed_sentiment >= 14400:
            run_sentiment_scan()
            _last_sentiment_run = datetime.now()

        # ── 每日 Pipeline：美股交易日 07:30 TW ───────────────────────────────
        # 改用 trading_calendar 判斷，取代硬編碼 weekday 邏輯
        try:
            from src.trading_calendar import is_trading_day
            _is_us_trading_day = is_trading_day(today_tw_str, market="us")
        except Exception:
            # fallback：排除週末即可
            _is_us_trading_day = now_tw.weekday() < 5

        is_sunday = now_tw.weekday() == 6  # 週日保留，供週報觸發判斷
        past_trigger = (
            now_tw.hour > DAILY_HOUR or
            (now_tw.hour == DAILY_HOUR and now_tw.minute >= DAILY_MINUTE)
        )
        if past_trigger and _is_us_trading_day and _last_pipeline_date != today_tw_str:
            if _pipeline_ran_today(today_tw_str):
                logger.info(f"Watchdog: snapshot already exists for {today_tw_str}, skip pipeline")
                _last_pipeline_date = today_tw_str
            else:
                logger.info(
                    f"Watchdog: daily pipeline trigger — "
                    f"TW {now_tw.strftime('%H:%M')}, date={today_tw_str}, "
                    f"weekday={now_tw.weekday()} (0=Mon), us_trading={_is_us_trading_day}"
                )
                _last_pipeline_date = today_tw_str
                run_daily_pipeline_check()

        # ── 週報：週日 09:00 TW ───────────────────────────────────────────
        if is_sunday and now_tw.hour >= 9:
            weekly_key = f"weekly_{today_tw_str}"
            if _last_weekly_key != weekly_key:
                if _weekly_ran_this_week(today_tw_str):
                    logger.info(f"Watchdog: weekly snapshot already exists, skip")
                    _last_weekly_key = weekly_key
                else:
                    logger.info(f"Watchdog: weekly pipeline trigger — TW {now_tw.strftime('%H:%M')}")
                    _last_weekly_key = weekly_key
                    run_weekly_pipeline()

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
