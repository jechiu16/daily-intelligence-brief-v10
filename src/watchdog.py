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


def run_invalidator_check():
    """每30分鐘執行一次 InvalidatorEngine。"""
    logger.info("Watchdog: running invalidator check")
    try:
        from src.config import MEMORY_DIR
        from src.data_watcher import run_data_watcher
        from src.invalidator_engine import check_all, load_active_theses
        from src.line_publisher import send_invalidator_alerts

        data_package = run_data_watcher()
        active_theses = load_active_theses()
        triggered = check_all(data_package, active_theses)

        if triggered:
            logger.warning(f"Watchdog: {len(triggered)} invalidators triggered!")
            send_invalidator_alerts(triggered)
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
    """每週一觸發週報（預留）。"""
    logger.info("Watchdog: weekly pipeline triggered (not yet implemented)")


def _pipeline_ran_today(today_tw_str: str) -> bool:
    """檢查今日（台灣日期）的 snapshot 是否已存在。"""
    try:
        from src.config import PROJECT_ROOT
        snapshot_path = PROJECT_ROOT / "memory" / "daily_snapshots" / f"{today_tw_str}.json"
        return snapshot_path.exists()
    except Exception:
        return False


def main():
    global _last_invalidator_run, _last_sentiment_run, _last_pipeline_date

    logger.info("DIB v10 Watchdog starting (sleep-aware mode)...")
    logger.info(f"  - InvalidatorEngine: every 30 minutes (elapsed-based)")
    logger.info(f"  - SentimentWatcher: every 4 hours (elapsed-based)")
    logger.info(f"  - Daily pipeline: {DAILY_HOUR:02d}:{DAILY_MINUTE:02d} TW time (sleep-aware catch-up)")
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

        # ── 每日 Pipeline：台灣時間 07:30 後，補跑機制 ──────────────────────
        # 只要：(1) 現在台灣時間 >= 07:30，且 (2) 今天還沒跑過 → 立即補跑
        past_trigger = (
            now_tw.hour > DAILY_HOUR or
            (now_tw.hour == DAILY_HOUR and now_tw.minute >= DAILY_MINUTE)
        )
        if past_trigger and _last_pipeline_date != today_tw_str:
            if _pipeline_ran_today(today_tw_str):
                # Snapshot 已存在（例如手動跑過），標記為完成
                logger.info(f"Watchdog: snapshot already exists for {today_tw_str}, skip pipeline")
                _last_pipeline_date = today_tw_str
            else:
                logger.info(
                    f"Watchdog: daily pipeline trigger — "
                    f"TW {now_tw.strftime('%H:%M')}, date={today_tw_str}"
                )
                _last_pipeline_date = today_tw_str
                run_daily_pipeline_check()

        # ── 週報：週一 08:00 TW 後 ────────────────────────────────────────
        if now_tw.weekday() == 0 and (
            now_tw.hour > 8 or (now_tw.hour == 8 and now_tw.minute >= 0)
        ):
            # 用 f-string 避免重複跑（只跑一次/週）
            weekly_key = f"weekly_{today_tw_str}"
            if _last_pipeline_date != weekly_key:
                run_weekly_pipeline()
                # 不覆蓋 _last_pipeline_date，避免干擾每日判斷

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
