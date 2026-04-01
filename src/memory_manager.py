"""Memory Manager — 更新本地記憶層，Git commit。"""

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.config import (
    MEMORY_DIR, MISSING_DATA, PROJECT_ROOT,
    SNAPSHOTS_DIR, SYNC_PATHS, TIMESERIES_DIR,
)
from src.historian import update_vector_index

logger = logging.getLogger(__name__)


def build_daily_snapshot(
    today_str: str,
    data_package: dict,
    quant_package: dict,
    analysis: dict,
    verdict: dict,
    report: dict,
    tgri: dict,
    coverage_score: float,
    citation_result: dict,
    notion_url: str | None = None,
) -> dict:
    """建立今日完整快照（不可變）。"""
    metadata = report.get("metadata", {})
    regime = analysis.get("regime", {})

    snapshot = {
        "date": today_str,
        "metadata": {
            "regime": regime.get("current", MISSING_DATA),
            "regime_day": regime.get("day_count", 0),
            "coverage_score": coverage_score,
            "pipeline_version": "v10.0",
            "run_timestamp": datetime.now().isoformat(),
            "citation_integrity_score": citation_result.get("integrity_score", 0),
            "notion_url": notion_url,
        },
        "market_data": data_package,
        "quant": quant_package,
        "tgri": tgri,
        "core_tension": analysis.get("core_tension", MISSING_DATA),
        "inference_chain": analysis.get("inference_chain", []),
        "thesis_states": analysis.get("thesis_updates", []),
        "opus_verdicts": verdict,
        "scorecard": _extract_scorecard(analysis),
        "data_gaps": citation_result.get("flags", []),
    }
    return snapshot


def _extract_scorecard(analysis: dict) -> dict:
    """從 compass 提取 scorecard 格式。"""
    compass = analysis.get("compass", [])
    scorecard = {}
    for item in compass:
        asset = item.get("asset", "").lower().replace(" ", "_")
        direction = item.get("direction", "neutral")
        conf = item.get("adjusted_confidence") or item.get("raw_confidence", 0.5)
        conf_label = "H" if conf > 0.7 else ("M" if conf > 0.5 else "L")
        scorecard[asset] = {
            "direction": direction,
            "confidence": conf_label,
            "actual_return": None,  # 隔日填入
            "result": None,
        }
    return scorecard


def save_daily_snapshot(snapshot: dict, today_str: str):
    """寫入 daily_snapshots/{date}.json。"""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOTS_DIR / f"{today_str}.json"
    path.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info(f"MemoryManager: snapshot saved → {path}")


def update_market_timeseries(data_package: dict, today_str: str):
    """更新 market.parquet。"""
    TIMESERIES_DIR.mkdir(parents=True, exist_ok=True)
    parquet_path = TIMESERIES_DIR / "market.parquet"

    row = {"date": today_str}
    for key, item in data_package.items():
        if key == "quality_scores":
            continue
        if isinstance(item, dict):
            val = item.get("price") or item.get("value")
            if val and val != MISSING_DATA:
                try:
                    row[key] = float(val)
                except (TypeError, ValueError):
                    pass

    new_row = pd.DataFrame([row]).set_index("date")
    new_row.index = pd.to_datetime(new_row.index)

    if parquet_path.exists():
        try:
            existing = pd.read_parquet(parquet_path)
            combined = pd.concat([existing, new_row])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined.to_parquet(parquet_path)
        except Exception as e:
            logger.error(f"MemoryManager: parquet update failed: {e}")
            new_row.to_parquet(parquet_path)
    else:
        new_row.to_parquet(parquet_path)


def update_regime_history(regime: str, regime_day: int, today_str: str):
    """更新 regime_history.json。"""
    path = TIMESERIES_DIR / "regime_history.json"
    history = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            history = data.get("history", [])
        except Exception:
            pass
    history.append({"date": today_str, "regime": regime, "day": regime_day})
    path.write_text(
        json.dumps({"description": "Regime 歷史", "history": history},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def update_l2(snapshot: dict, today_str: str):
    """更新 L2（近7日市場結構摘要）。"""
    l2_path = MEMORY_DIR / "l2.json"
    try:
        l2 = json.loads(l2_path.read_text(encoding="utf-8"))
    except Exception:
        l2 = {"market_structure": [], "regime_history_7d": []}

    new_entry = {
        "date": today_str,
        "regime": snapshot["metadata"].get("regime"),
        "coverage": snapshot["metadata"].get("coverage_score"),
        "core_tension": snapshot.get("core_tension"),
    }

    # 移除同一天的舊 entry（防止重複寫入），再加入新 entry
    market_structure = [e for e in l2.get("market_structure", []) if e.get("date") != today_str]
    market_structure.append(new_entry)

    # 只保留最近 7 天（按日期排序後取末尾）
    market_structure.sort(key=lambda e: e.get("date", ""))
    l2["market_structure"] = market_structure[-7:]
    l2["last_updated"] = today_str

    l2_path.write_text(json.dumps(l2, indent=2, ensure_ascii=False), encoding="utf-8")


def git_commit(today_str: str):
    """Git commit 同步 SYNC_PATHS。"""
    try:
        # 先確認是 git repo
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
        )
        if result.returncode != 0:
            logger.info("MemoryManager: not a git repo, initializing...")
            subprocess.run(["git", "init"], cwd=PROJECT_ROOT, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "dib@local"],
                cwd=PROJECT_ROOT, capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "DIB-v10"],
                cwd=PROJECT_ROOT, capture_output=True,
            )

        # Add SYNC_PATHS
        for path in SYNC_PATHS:
            full_path = PROJECT_ROOT / path
            if full_path.exists():
                subprocess.run(
                    ["git", "add", str(full_path)],
                    cwd=PROJECT_ROOT, capture_output=True,
                )

        # Commit
        commit_result = subprocess.run(
            ["git", "commit", "-m", f"DIB v10 daily snapshot {today_str}"],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
        )
        if "nothing to commit" in commit_result.stdout:
            logger.info("MemoryManager: nothing to commit")
        else:
            logger.info(f"MemoryManager: git commit done for {today_str}")

    except Exception as e:
        logger.error(f"MemoryManager: git commit failed: {e}")


def run_memory_manager(
    today_str: str,
    data_package: dict,
    quant_package: dict,
    analysis: dict,
    verdict: dict,
    report: dict,
    tgri: dict,
    coverage_score: float,
    citation_result: dict,
    notion_url: str | None = None,
):
    """主入口：儲存所有記憶，更新時序，git commit。"""
    snapshot = build_daily_snapshot(
        today_str, data_package, quant_package, analysis,
        verdict, report, tgri, coverage_score, citation_result, notion_url,
    )

    save_daily_snapshot(snapshot, today_str)
    update_market_timeseries(data_package, today_str)

    regime = analysis.get("regime", {})
    update_regime_history(
        regime.get("current", MISSING_DATA),
        regime.get("day_count", 0),
        today_str,
    )
    update_l2(snapshot, today_str)
    update_vector_index(today_str, snapshot)
    git_commit(today_str)

    logger.info(f"MemoryManager: all memory layers updated for {today_str}")
    return snapshot
