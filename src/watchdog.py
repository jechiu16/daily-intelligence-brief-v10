"""Watchdog — 常駐監控，每30分鐘檢查 invalidator，每4小時掃描輿情。"""

import json
import logging
import sys
from datetime import datetime

import schedule
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
    """每日 07:00 觸發主 pipeline。"""
    logger.info("Watchdog: triggering daily pipeline")
    try:
        from src.orchestrator import run_daily_pipeline
        run_daily_pipeline()
    except Exception as e:
        logger.error(f"Watchdog daily pipeline failed: {e}")


def run_weekly_pipeline():
    """每週一 08:00 觸發週報（預留）。"""
    logger.info("Watchdog: weekly pipeline triggered (not yet implemented)")


def main():
    logger.info("DIB v10 Watchdog starting...")

    # 排程設定
    schedule.every(30).minutes.do(run_invalidator_check)
    schedule.every(4).hours.do(run_sentiment_scan)
    schedule.every().day.at("07:00").do(run_daily_pipeline_check)
    schedule.every().monday.at("08:00").do(run_weekly_pipeline)

    logger.info("Watchdog: schedule configured")
    logger.info("  - InvalidatorEngine: every 30 minutes")
    logger.info("  - SentimentWatcher: every 4 hours")
    logger.info("  - Daily pipeline: 07:00 daily")
    logger.info("  - Weekly pipeline: Monday 08:00")

    # 立即執行一次 invalidator check
    run_invalidator_check()

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
