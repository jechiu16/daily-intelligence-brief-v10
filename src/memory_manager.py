from __future__ import annotations
"""Memory Manager — 更新本地記憶層，Git commit。"""

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.config import (
    MEMORY_DIR, MISSING_DATA, PROJECT_ROOT,
    SNAPSHOTS_DIR, SYNC_PATHS, THESES_DIR, TIMESERIES_DIR,
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
            "run_timestamp": datetime.now(timezone.utc).isoformat(),
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
    # 去重：移除同日舊記錄（keep-last 語意，允許重跑覆蓋）
    history = [h for h in history if h.get("date") != today_str]
    history.append({"date": today_str, "regime": regime, "day": regime_day})
    history = sorted(history, key=lambda x: x.get("date", ""))
    path.write_text(
        json.dumps({"description": "Regime 歷史", "history": history},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def sync_theses_dir_to_l3(today_str: str = None):
    """橋接 theses/active/*.json → l3.json。

    Pipeline 啟動時呼叫，確保 L3 active_theses 與磁碟上的 thesis 檔案一致。
    Upsert 語意：disk 優先覆蓋，保留 l3 中 disk 沒有的記錄（維護 invalidator 狀態）。
    """
    if today_str is None:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    theses_dir = THESES_DIR / "active"
    l3_path = MEMORY_DIR / "l3.json"

    # 讀取磁碟上所有 thesis 檔案
    disk_theses: dict[str, dict] = {}
    if theses_dir.exists():
        for f in sorted(theses_dir.glob("*.json")):
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(t, dict) and "id" in t:
                    disk_theses[t["id"]] = t
            except Exception as e:
                logger.warning(f"sync_theses: failed to read {f.name}: {e}")

    # 載入現有 l3.json
    try:
        l3 = json.loads(l3_path.read_text(encoding="utf-8"))
    except Exception:
        l3 = {"description": "L3 Active Theses", "active_theses": [], "thesis_count": 0}

    # Upsert：disk 的 thesis 優先覆蓋 l3 中同 id 的記錄
    existing = {t["id"]: t for t in l3.get("active_theses", []) if isinstance(t, dict) and "id" in t}
    existing.update(disk_theses)

    l3["active_theses"] = list(existing.values())
    l3["thesis_count"] = len([t for t in l3["active_theses"] if t.get("status") == "active"])
    l3["last_updated"] = today_str

    l3_path.write_text(json.dumps(l3, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"sync_theses_dir_to_l3: {len(disk_theses)} from disk, {l3['thesis_count']} active in l3")


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


def _extract_and_update_l4(snapshot: dict, today_str: str):
    """從今日快照提取高信度推論和首席風險官洞察，寫入 L4 知識庫。

    規則：
    - inference_chain 中 adjusted_confidence >= 0.65 → type="inference"
    - attack_verdicts 中 verdict="SUSTAINED" → type="sustained_attack"（已確認的反駁點）
    - risk_officer_notes + narrative_verdict → type="risk_insight"（整體風控備忘）
    - 保留最近 90 筆（超過則刪除最舊的）
    """
    L4_MAX_ENTRIES = 90
    l4_path = MEMORY_DIR / "l4.json"

    try:
        l4 = json.loads(l4_path.read_text(encoding="utf-8"))
    except Exception:
        l4 = {"description": "L4 Knowledge Base", "last_updated": None, "entries": []}

    regime = snapshot.get("metadata", {}).get("regime", MISSING_DATA)
    entries = l4.get("entries", [])

    # 移除今日已有的 entry（允許重跑覆蓋）
    entries = [e for e in entries if e.get("date") != today_str]

    new_entries = []

    # 1. 高信度推論（adjusted_confidence >= 0.65）
    for inf in snapshot.get("inference_chain", []):
        if not isinstance(inf, dict):
            continue
        conf = inf.get("adjusted_confidence", 0)
        if conf is None or conf < 0.65:
            continue
        new_entries.append({
            "date": today_str,
            "type": "inference",
            "source_id": inf.get("id", ""),
            "regime": regime,
            "claim": inf.get("claim", ""),
            "confidence": conf,
            "invalidation_condition": inf.get("invalidation_condition", ""),
            "logic_summary": (inf.get("logic", "") or "")[:300],  # 截斷防爆
        })

    # 2. Sustained attacks（被確認成立的攻擊 = 分析師盲點）
    verdicts = snapshot.get("opus_verdicts", {})
    for av in verdicts.get("attack_verdicts", []):
        if not isinstance(av, dict):
            continue
        if av.get("verdict") == "SUSTAINED":
            new_entries.append({
                "date": today_str,
                "type": "sustained_attack",
                "source_id": av.get("attack_id", ""),
                "regime": regime,
                "claim": av.get("reason", ""),
                "narrative": av.get("narrative", ""),
                "data_reference": av.get("data_reference", ""),
            })

    # 3. 首席風險官風控洞察（每日一筆，不論信度）
    notes = verdicts.get("risk_officer_notes", "")
    narrative = verdicts.get("narrative_verdict", "")
    if notes or narrative:
        new_entries.append({
            "date": today_str,
            "type": "risk_insight",
            "source_id": "risk_officer",
            "regime": regime,
            "claim": (notes or "")[:400],
            "narrative": (narrative or "")[:600],
        })

    entries.extend(new_entries)

    # 保留最新 90 筆（按 date 排序）
    entries.sort(key=lambda e: (e.get("date", ""), e.get("source_id", "")))
    if len(entries) > L4_MAX_ENTRIES:
        entries = entries[-L4_MAX_ENTRIES:]

    l4["entries"] = entries
    l4["last_updated"] = today_str
    l4["entry_count"] = len(entries)

    l4_path.write_text(json.dumps(l4, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info(f"MemoryManager: L4 updated — {len(new_entries)} new entries (total {len(entries)})")


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
    _extract_and_update_l4(snapshot, today_str)
    update_vector_index(today_str, snapshot)
    git_commit(today_str)

    logger.info(f"MemoryManager: all memory layers updated for {today_str}")
    return snapshot
