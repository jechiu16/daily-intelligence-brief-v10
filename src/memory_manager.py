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
    premortem_result: dict | None = None,
) -> dict:
    """建立今日完整快照（不可變）。"""
    metadata = report.get("metadata", {})
    regime = analysis.get("regime", {})
    sections = report.get("sections", {}) if isinstance(report, dict) else {}
    try:
        from src.research_ledger import build_research_ledger, extract_watchboard_items
        watchboard_items = extract_watchboard_items(report)
        research_ledger = build_research_ledger(
            analysis=analysis,
            verdict=verdict,
            report=report,
        )
    except Exception as e:
        logger.warning(f"research ledger build failed (non-fatal): {e}")
        watchboard_items = []
        research_ledger = {"schema_version": "research-ledger-v1", "_error": str(e)}

    snapshot = {
        "date": today_str,
        "metadata": {
            "regime": regime.get("current", MISSING_DATA),
            "regime_day": regime.get("day_count", 0),
            "coverage_score": coverage_score,
            "pipeline_version": "v10.1",
            "run_timestamp": datetime.now(timezone.utc).isoformat(),
            "citation_integrity_score": citation_result.get("integrity_score", 0),
            "report_quality": report.get("_quality_assessment", {}),
            "notion_url": notion_url,
        },
        "report_sections": sections,
        "market_data": data_package,
        "quant": quant_package,
        "tgri": tgri,
        "core_tension": analysis.get("core_tension", MISSING_DATA),
        "inference_chain": analysis.get("inference_chain", []),
        "thesis_states": analysis.get("thesis_updates", []),
        "opus_verdicts": verdict,
        "scorecard": _extract_scorecard(analysis),
        "data_gaps": citation_result.get("flags", []),
        "premortem_scenarios": (premortem_result or {}).get("scenarios", []),
        "watchboard": {
            "raw": sections.get("watchboard", "") if isinstance(sections, dict) else "",
            "items": watchboard_items,
            "backtest": report.get("_watchboard_backtest", {}),
        },
        "causal_graph": report.get("_causal_graph", research_ledger.get("causal_graph", {})),
        "research_ledger": research_ledger,
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
    """寫入 daily_snapshots/{date}.json。
    防禦：若已存在 coverage > 0 的快照，不允許被 coverage=0 的結果覆蓋
    （防止 assembler 失敗的第二次 pipeline 跑踩掉正常的第一次結果）。
    """
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOTS_DIR / f"{today_str}.json"

    new_coverage = snapshot.get("metadata", {}).get("coverage_score", 0)
    if path.exists() and new_coverage == 0:
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            existing_coverage = existing.get("metadata", {}).get("coverage_score", 0)
            if existing_coverage > 0:
                logger.warning(
                    f"MemoryManager: skip overwrite — new coverage=0 but existing={existing_coverage:.2f}; "
                    f"keeping existing snapshot"
                )
                return
        except Exception:
            pass

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
            # H5 修正：用 is not None 避免 price=0.0 的 falsy trap
            price = item.get("price")
            val = price if price is not None else item.get("value")
            if val is not None and val != MISSING_DATA:
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

    # M1 修正：欄位層級合併 — disk 優先，但保留 l3 的動態運行時狀態
    # （避免磁碟靜態檔案覆蓋 invalidator triggered 狀態、confidence_history 等）
    existing = {t["id"]: t for t in l3.get("active_theses", []) if isinstance(t, dict) and "id" in t}
    for tid, disk_t in disk_theses.items():
        if tid in existing:
            l3_t = existing[tid]
            merged = dict(disk_t)  # disk 為主

            # 保留 l3 才有的動態欄位
            if l3_t.get("confidence_history"):
                merged.setdefault("confidence_history", l3_t["confidence_history"])
            if l3_t.get("_last_direction"):
                merged.setdefault("_last_direction", l3_t["_last_direction"])

            # invalidators：逐條保留 triggered / triggered_date（避免磁碟覆蓋已觸發狀態）
            disk_invs = {inv.get("condition"): inv for inv in merged.get("invalidators", []) if inv.get("condition")}
            l3_invs = {inv.get("condition"): inv for inv in l3_t.get("invalidators", []) if inv.get("condition")}
            for cond, l3_inv in l3_invs.items():
                if cond in disk_invs and l3_inv.get("triggered"):
                    disk_invs[cond]["triggered"] = True
                    disk_invs[cond]["triggered_date"] = l3_inv.get("triggered_date")
            if disk_invs:
                merged["invalidators"] = list(disk_invs.values())

            existing[tid] = merged
        else:
            existing[tid] = disk_t

    l3["active_theses"] = list(existing.values())
    l3["thesis_count"] = len([t for t in l3["active_theses"] if t.get("status") == "active"])
    l3["last_updated"] = today_str

    l3_path.write_text(json.dumps(l3, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"sync_theses_dir_to_l3: {len(disk_theses)} from disk, {l3['thesis_count']} active in l3")


def update_l2(snapshot: dict, today_str: str, tgri_score: float | None = None):
    """更新 L2（近7日市場結構摘要）。

    L5 修正：額外儲存 regime_day 和 tgri_score，
    供 orchestrator Step 3 讀取 tgri_score 代理值，以及 analyst 追蹤 regime 連續天數。
    """
    l2_path = MEMORY_DIR / "l2.json"
    try:
        l2 = json.loads(l2_path.read_text(encoding="utf-8"))
    except Exception:
        l2 = {"market_structure": [], "regime_history_7d": []}

    new_entry = {
        "date": today_str,
        "regime": snapshot["metadata"].get("regime"),
        "regime_day": snapshot["metadata"].get("regime_day", 0),
        "coverage": snapshot["metadata"].get("coverage_score"),
        "core_tension": snapshot.get("core_tension"),
    }
    if tgri_score is not None:
        new_entry["tgri_score"] = tgri_score

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
                # H1 修正：risk_officer 的 attack_verdict 使用 "claim" 欄位，非 "reason"
                "claim": av.get("claim", "") or av.get("narrative", ""),
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

    # 寫入推論準確率統計（每次重算，覆蓋舊值）
    try:
        from src.inference_store import compute_accuracy_by_type
        accuracy_stats = compute_accuracy_by_type()
        if accuracy_stats:
            l4["inference_accuracy_stats"] = {
                "computed_at": today_str,
                "by_asset": accuracy_stats,
            }
    except Exception as _e:
        logger.warning(f"compute_accuracy_by_type failed (non-fatal): {_e}")

    # 寫入 DA / PreMortem meta-stats（供 analyst 感知挑戰者品質）
    try:
        from src.calibration import _compute_da_premortem_stats
        da_stats = _compute_da_premortem_stats(window_days=30)
        if da_stats:
            l4["da_premortem_stats"] = da_stats
    except Exception as _e:
        logger.warning(f"_compute_da_premortem_stats in L4 failed (non-fatal): {_e}")

    l4_path.write_text(json.dumps(l4, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info(f"MemoryManager: L4 updated — {len(new_entries)} new entries (total {len(entries)})")


def _apply_thesis_updates(thesis_updates: list[dict], today_str: str, compass: list[dict] | None = None):
    """C3 修正：將 analyst 的 thesis_updates 寫回 l3.json 的 confidence_history。
    C4 修正：同時從 compass 讀取當日方向，寫入 _last_direction 供 consistency_checker 使用。

    先前 thesis_updates 只存入 snapshot（不可變存檔），但 l3.json 的活躍 thesis
    從未被更新，導致 confidence_history 永遠為空，信心追蹤失效。
    """
    if not thesis_updates:
        return

    l3_path = MEMORY_DIR / "l3.json"
    try:
        l3 = json.loads(l3_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("_apply_thesis_updates: cannot read l3.json, skipping")
        return

    theses_by_id: dict[str, dict] = {
        t["id"]: t
        for t in l3.get("active_theses", [])
        if isinstance(t, dict) and "id" in t
    }

    # C4：建立 thesis_id → compass direction 映射
    compass_dir: dict[str, str] = {}
    for comp in (compass or []):
        logic_id = comp.get("logic_id", "")
        direction = comp.get("direction")
        if logic_id and direction:
            compass_dir[logic_id] = direction

    updated = 0

    for upd in thesis_updates:
        thesis_id = upd.get("thesis_id") or upd.get("id")
        if not thesis_id or thesis_id not in theses_by_id:
            continue
        thesis = theses_by_id[thesis_id]

        # 追加 confidence_history 條目
        thesis.setdefault("confidence_history", []).append({
            "date": today_str,
            "confidence": upd.get("new_confidence") or thesis.get("confidence"),
            "reason": upd.get("reason", ""),
        })

        # 若 analyst 給出新信心值，更新 thesis 當前信心
        if upd.get("new_confidence") is not None:
            thesis["confidence"] = upd["new_confidence"]

        # C4：從 compass 寫入 _last_direction（供 consistency_checker 做方向反轉偵測）
        for logic_id, direction in compass_dir.items():
            if thesis_id.lower() in logic_id.lower():
                thesis["_last_direction"] = direction
                break

        updated += 1

    if updated > 0:
        l3["active_theses"] = list(theses_by_id.values())
        l3["last_updated"] = today_str
        l3_path.write_text(json.dumps(l3, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"_apply_thesis_updates: updated {updated} theses in l3.json")


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
    premortem_result: dict | None = None,
):
    """主入口：儲存所有記憶，更新時序，git commit。"""
    snapshot = build_daily_snapshot(
        today_str, data_package, quant_package, analysis,
        verdict, report, tgri, coverage_score, citation_result, notion_url,
        premortem_result=premortem_result,
    )

    save_daily_snapshot(snapshot, today_str)
    update_market_timeseries(data_package, today_str)

    regime = analysis.get("regime", {})
    update_regime_history(
        regime.get("current", MISSING_DATA),
        regime.get("day_count", 0),
        today_str,
    )
    update_l2(snapshot, today_str, tgri_score=tgri.get("score") if isinstance(tgri, dict) else None)
    _apply_thesis_updates(analysis.get("thesis_updates", []), today_str, compass=analysis.get("compass", []))
    _extract_and_update_l4(snapshot, today_str)
    update_vector_index(today_str, snapshot)
    # H6 修正：git commit 移至 orchestrator Step 21（唯一出口），避免雙重 commit
    # git_commit(today_str) ← 已移除

    # 彙整本次 pipeline LLM 用量，寫入 snapshot metadata
    try:
        from src.telemetry import aggregate_run_telemetry
        from src.config import RUN_CONTEXT
        tel = aggregate_run_telemetry(RUN_CONTEXT.run_id)
        if tel:
            snapshot.setdefault("metadata", {})["telemetry"] = tel
            # 回寫 snapshot（metadata 已更新）
            save_daily_snapshot(snapshot, today_str)
    except Exception as _e:
        logger.warning(f"telemetry aggregation failed (non-fatal): {_e}")

    logger.info(f"MemoryManager: all memory layers updated for {today_str}")
    return snapshot
