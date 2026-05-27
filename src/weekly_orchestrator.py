from __future__ import annotations
"""Weekly Orchestrator — 週報 pipeline 主控。

每週一 09:00 TW 由 watchdog 觸發，聚合上週資料並生成週報。
"""

import json
import logging
import sys
from datetime import datetime, timedelta

import pytz

from src.config import (
    MISSING_DATA, PROJECT_ROOT, RUN_CONTEXT,
    WEEKLY_SNAPSHOTS_DIR,
)

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/dib_weekly.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

_TW_TZ = pytz.timezone("Asia/Taipei")


def _default_week_end_date() -> str:
    """預設：昨天（台灣時間）。週一跑的話就是週日。"""
    return (datetime.now(_TW_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")


def _week_label_from_date(date_str: str) -> str:
    """'2026-04-04' → '2026-W14'"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def run_weekly_pipeline(week_end_date: str | None = None, force: bool = False) -> dict:
    """完整週報 pipeline。

    Args:
        week_end_date: 週末日期（預設：昨天 TW）
        force: 若 True，即使 snapshot 已存在也重新生成

    Returns:
        results dict
    """
    RUN_CONTEXT.init()

    if week_end_date is None:
        week_end_date = _default_week_end_date()

    week_label = _week_label_from_date(week_end_date)

    # 檢查是否已跑過
    WEEKLY_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = WEEKLY_SNAPSHOTS_DIR / f"{week_label}.json"
    if not force and snapshot_path.exists():
        logger.info(f"Weekly pipeline: snapshot already exists for {week_label}, skipping")
        return {"status": "skipped", "week_label": week_label}

    logger.info(f"{'='*60}")
    logger.info(f"DIB v10.2 Weekly Pipeline — {week_label} (ending {week_end_date})")
    logger.info(f"{'='*60}")

    results = {
        "week_label": week_label,
        "week_end_date": week_end_date,
        "run_id": RUN_CONTEXT.run_id,
        "status": "running",
        "steps": {},
    }

    # ── Step 1: Aggregate ──────────────────────────────────────────────────
    weekly_context = None
    try:
        from src.weekly_aggregator import aggregate_week
        weekly_context = aggregate_week(week_end_date)
        results["steps"]["aggregator"] = "ok"
        logger.info(
            f"Step 1 Aggregator: {weekly_context.get('days_available', 0)} days, "
            f"{weekly_context.get('inference_analysis', {}).get('total', 0)} inferences"
        )
    except Exception as e:
        logger.error(f"Step 1 Aggregator failed: {e}")
        results["steps"]["aggregator"] = f"error: {e}"
        results["status"] = "failed"
        return results

    if weekly_context.get("days_available", 0) == 0:
        logger.warning("Weekly pipeline: no snapshots available, aborting")
        results["status"] = "no_data"
        return results

    # ── Step 2: Weekly Narrator ────────────────────────────────────────────
    report = None
    try:
        from src.agents.weekly_narrator import run_weekly_narrator
        report = run_weekly_narrator(weekly_context)
        results["steps"]["narrator"] = "ok"
        sections = report.get("sections", {})
        arc_len = len(sections.get("narrative_arc", ""))
        logger.info(f"Step 2 Narrator: narrative_arc={arc_len} chars")
    except Exception as e:
        logger.error(f"Step 2 Narrator failed: {e}")
        results["steps"]["narrator"] = f"error: {e}"
        report = {
            "sections": {
                "narrative_arc": f"週報生成失敗：{str(e)[:200]}",
                "calibration_review": "",
                "thesis_evolution": "",
                "structural_view": "",
                "watch_list": "",
                "system_reflection": "",
            },
            "metadata": {"week_label": week_label, "error": str(e)[:200]},
        }

    # ── Step 3: Notion Publisher ───────────────────────────────────────────
    notion_url = None
    try:
        from src.notion_publisher import publish_weekly_to_notion
        notion_url = publish_weekly_to_notion(
            report=report,
            weekly_context=weekly_context,
            week_label=week_label,
        )
        results["steps"]["notion"] = "ok"
        logger.info(f"Step 3 Notion: {notion_url}")
    except Exception as e:
        logger.error(f"Step 3 Notion failed: {e}")
        results["steps"]["notion"] = f"error: {e}"

    # ── Step 4: Save Snapshot + Git ────────────────────────────────────────
    try:
        weekly_snapshot = {
            "week_label": week_label,
            "date_range": weekly_context.get("date_range", {}),
            "days_available": weekly_context.get("days_available", 0),
            "metadata": report.get("metadata", {}),
            "weekly_context": weekly_context,
            "report": report,
            "notion_url": notion_url,
            "run_id": RUN_CONTEXT.run_id,
            "pipeline_version": RUN_CONTEXT.pipeline_version,
        }
        snapshot_path.write_text(
            json.dumps(weekly_snapshot, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        results["steps"]["snapshot"] = "ok"
        logger.info(f"Step 5 Snapshot: saved → {snapshot_path}")

        # Git commit
        from src.memory_manager import git_commit
        git_commit(f"weekly-{week_label}")
        results["steps"]["git"] = "ok"

    except Exception as e:
        logger.error(f"Step 5 Snapshot/Git failed: {e}")
        results["steps"]["snapshot"] = f"error: {e}"

    results["status"] = "completed"
    logger.info(f"{'='*60}")
    logger.info(f"Weekly Pipeline DONE — {week_label}")
    logger.info(f"{'='*60}")

    return results


if __name__ == "__main__":
    import sys as _sys
    week_end = _sys.argv[1] if len(_sys.argv) > 1 else None
    result = run_weekly_pipeline(week_end, force=True)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
