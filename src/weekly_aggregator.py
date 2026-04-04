from __future__ import annotations
"""Weekly Aggregator — 純 Python 資料聚合，無 LLM 呼叫。

讀取 7 天 daily_snapshots + inference_history + regime_history + L5 scorecard，
產出結構化 weekly_context dict，供 weekly_narrator 使用。
"""

import json
import logging
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from src.config import (
    MEMORY_DIR, MISSING_DATA, SNAPSHOTS_DIR,
    THESES_DIR, TIMESERIES_DIR,
)

logger = logging.getLogger(__name__)

# 最少需要幾個 snapshot 才算完整週報
MIN_SNAPSHOTS_FOR_FULL = 3


# ═══════════════════════════════════════════════════════════════════════════
# Helper: ISO Week Label
# ═══════════════════════════════════════════════════════════════════════════

def _compute_iso_week(date_str: str) -> str:
    """'2026-04-03' → '2026-W14'"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


# ═══════════════════════════════════════════════════════════════════════════
# Step 1: Load Snapshots
# ═══════════════════════════════════════════════════════════════════════════

def _load_week_snapshots(week_end_date: str) -> list[dict]:
    """往回找最多 7 個日曆天的 snapshot，回傳按日期升序排列。"""
    end_dt = datetime.strptime(week_end_date, "%Y-%m-%d")
    snapshots = []
    for i in range(7):
        d = end_dt - timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        path = SNAPSHOTS_DIR / f"{date_str}.json"
        if path.exists():
            try:
                snap = json.loads(path.read_text(encoding="utf-8"))
                snap.setdefault("date", date_str)
                snapshots.append(snap)
            except Exception as e:
                logger.warning(f"Failed to load snapshot {date_str}: {e}")
    snapshots.sort(key=lambda s: s["date"])
    return snapshots


# ═══════════════════════════════════════════════════════════════════════════
# Step 2: Inference Analysis
# ═══════════════════════════════════════════════════════════════════════════

def _analyze_inferences(date_from: str, date_to: str) -> dict:
    """分析本週推論績效。"""
    from src.inference_store import query

    inferences = query(date_from=date_from, date_to=date_to)
    total = len(inferences)
    if total == 0:
        return {
            "total": 0, "by_verdict": {}, "by_outcome": {},
            "accuracy_by_asset": {}, "blind_spots": [],
            "strongest_signals": [], "recurring_assets": [],
        }

    # By verdict
    by_verdict = Counter()
    for inf in inferences:
        v = inf.get("verdict") or "pending"
        by_verdict[v] += 1

    # By outcome
    by_outcome = Counter()
    for inf in inferences:
        o = inf.get("outcome") or "pending"
        by_outcome[o] += 1

    # Accuracy by primary asset
    asset_stats: dict[str, dict] = {}
    for inf in inferences:
        keys = inf.get("evidence_keys", [])
        primary = keys[0] if keys else "general"
        if primary not in asset_stats:
            asset_stats[primary] = {"total": 0, "vindicated": 0, "refuted": 0}
        if inf.get("outcome"):
            asset_stats[primary]["total"] += 1
            if inf["outcome"] == "vindicated":
                asset_stats[primary]["vindicated"] += 1
            elif inf["outcome"] == "refuted":
                asset_stats[primary]["refuted"] += 1

    accuracy_by_asset = {}
    for asset, s in asset_stats.items():
        if s["total"] > 0:
            accuracy_by_asset[asset] = {
                "hit_rate": round(s["vindicated"] / s["total"], 3),
                "n": s["total"],
            }

    # Blind spots: high confidence + refuted
    blind_spots = [
        {
            "date": inf["date"],
            "inf_id": inf.get("inf_id", ""),
            "claim": inf.get("claim", "")[:120],
            "confidence": inf.get("adjusted_confidence", 0),
        }
        for inf in inferences
        if inf.get("outcome") == "refuted"
        and (inf.get("adjusted_confidence") or 0) > 0.6
    ]

    # Strongest signals: high confidence + vindicated
    strongest = [
        {
            "date": inf["date"],
            "inf_id": inf.get("inf_id", ""),
            "claim": inf.get("claim", "")[:120],
            "confidence": inf.get("adjusted_confidence", 0),
        }
        for inf in inferences
        if inf.get("outcome") == "vindicated"
        and (inf.get("adjusted_confidence") or 0) > 0.6
    ]

    # Recurring assets: count how many unique dates each primary asset appears
    asset_dates: dict[str, set] = {}
    for inf in inferences:
        keys = inf.get("evidence_keys", [])
        primary = keys[0] if keys else "general"
        asset_dates.setdefault(primary, set()).add(inf.get("date", ""))
    recurring = [
        {"asset": a, "days": len(dates)}
        for a, dates in asset_dates.items()
        if len(dates) >= 2  # appears in 2+ days (relaxed threshold for early data)
    ]
    recurring.sort(key=lambda x: x["days"], reverse=True)

    return {
        "total": total,
        "by_verdict": dict(by_verdict),
        "by_outcome": dict(by_outcome),
        "accuracy_by_asset": accuracy_by_asset,
        "blind_spots": blind_spots[:5],
        "strongest_signals": strongest[:5],
        "recurring_assets": recurring[:10],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Step 3: Calibration Scorecard
# ═══════════════════════════════════════════════════════════════════════════

def _compute_weekly_scorecard(date_from: str, date_to: str) -> dict:
    """計算本週 Brier Score 和命中率。"""
    l5_path = MEMORY_DIR / "l5.json"
    if not l5_path.exists():
        return {
            "weekly_brier": None, "hit_rate": None,
            "correct": 0, "wrong": 0, "total_filled": 0,
            "by_asset": {}, "calibration_gap": None,
        }

    try:
        l5 = json.loads(l5_path.read_text(encoding="utf-8"))
    except Exception:
        l5 = {}

    predictions = l5.get("predictions", [])

    # Filter to this week's date range
    week_preds = [
        p for p in predictions
        if date_from <= p.get("date", "") <= date_to
    ]

    filled = [p for p in week_preds if p.get("result") is not None]
    correct = sum(1 for p in filled if p.get("result") == "correct")
    wrong = len(filled) - correct

    # Brier Score: mean of (confidence - actual)^2
    brier_scores = []
    confidences = []
    for p in filled:
        conf = p.get("confidence", 0.5)
        actual = 1.0 if p.get("result") == "correct" else 0.0
        brier_scores.append((conf - actual) ** 2)
        confidences.append(conf)

    weekly_brier = round(sum(brier_scores) / len(brier_scores), 4) if brier_scores else None
    hit_rate = round(correct / len(filled), 3) if filled else None
    avg_conf = round(sum(confidences) / len(confidences), 3) if confidences else None
    calibration_gap = round(avg_conf - hit_rate, 3) if avg_conf is not None and hit_rate is not None else None

    # By asset
    asset_results: dict[str, dict] = {}
    for p in filled:
        asset = p.get("asset", "unknown")
        if asset not in asset_results:
            asset_results[asset] = {"correct": 0, "wrong": 0}
        if p.get("result") == "correct":
            asset_results[asset]["correct"] += 1
        else:
            asset_results[asset]["wrong"] += 1

    by_asset = {}
    for asset, r in asset_results.items():
        total = r["correct"] + r["wrong"]
        by_asset[asset] = {
            "hit_rate": round(r["correct"] / total, 3) if total > 0 else None,
            "n": total,
        }

    # Rolling 30d Brier for trend
    all_filled = [p for p in predictions if p.get("result") is not None]
    end_dt = datetime.strptime(date_to, "%Y-%m-%d")
    start_30d = (end_dt - timedelta(days=30)).strftime("%Y-%m-%d")
    recent_30d = [p for p in all_filled if p.get("date", "") >= start_30d]
    brier_30d_scores = []
    for p in recent_30d:
        conf = p.get("confidence", 0.5)
        actual = 1.0 if p.get("result") == "correct" else 0.0
        brier_30d_scores.append((conf - actual) ** 2)
    rolling_30d_brier = round(sum(brier_30d_scores) / len(brier_30d_scores), 4) if brier_30d_scores else None

    return {
        "weekly_brier": weekly_brier,
        "rolling_30d_brier": rolling_30d_brier,
        "hit_rate": hit_rate,
        "correct": correct,
        "wrong": wrong,
        "total_filled": len(filled),
        "total_predictions": len(week_preds),
        "by_asset": by_asset,
        "calibration_gap": calibration_gap,
        "avg_confidence": avg_conf,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Step 4: Regime Evolution
# ═══════════════════════════════════════════════════════════════════════════

def _analyze_regime_evolution(date_from: str, date_to: str) -> dict:
    """追蹤本週 regime 變化。"""
    path = TIMESERIES_DIR / "regime_history.json"
    if not path.exists():
        return {
            "start_regime": MISSING_DATA, "end_regime": MISSING_DATA,
            "transitions": [], "regime_stability": 0,
            "dominant_regime": MISSING_DATA, "regime_day_count": 0,
        }

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        history = data.get("history", [])
    except Exception:
        history = []

    week_entries = [
        h for h in history
        if date_from <= h.get("date", "") <= date_to
    ]

    if not week_entries:
        return {
            "start_regime": MISSING_DATA, "end_regime": MISSING_DATA,
            "transitions": [], "regime_stability": 0,
            "dominant_regime": MISSING_DATA, "regime_day_count": 0,
        }

    week_entries.sort(key=lambda h: h["date"])

    start_regime = week_entries[0].get("regime", MISSING_DATA)
    end_regime = week_entries[-1].get("regime", MISSING_DATA)
    end_day_count = week_entries[-1].get("day", 0)

    # Detect transitions
    transitions = []
    for i in range(1, len(week_entries)):
        prev = week_entries[i - 1].get("regime", "")
        curr = week_entries[i].get("regime", "")
        if prev != curr and prev != MISSING_DATA and curr != MISSING_DATA:
            transitions.append({
                "date": week_entries[i]["date"],
                "from": prev,
                "to": curr,
            })

    # Dominant regime
    regime_counts = Counter(
        h.get("regime", MISSING_DATA) for h in week_entries
        if h.get("regime") != MISSING_DATA
    )
    dominant = regime_counts.most_common(1)[0] if regime_counts else (MISSING_DATA, 0)
    stability = round(dominant[1] / len(week_entries), 2) if week_entries else 0

    return {
        "start_regime": start_regime,
        "end_regime": end_regime,
        "transitions": transitions,
        "regime_stability": stability,
        "dominant_regime": dominant[0],
        "regime_day_count": end_day_count,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Step 5: Thesis Evolution
# ═══════════════════════════════════════════════════════════════════════════

def _track_thesis_evolution(snapshots: list[dict]) -> dict:
    """追蹤 thesis 在本週的信心變化和生命週期。"""
    # Collect thesis_states from each snapshot
    daily_states: dict[str, list[dict]] = {}  # thesis_id -> [{date, ...}, ...]
    for snap in snapshots:
        date = snap.get("date", "")
        for ts in snap.get("thesis_states", []):
            if not isinstance(ts, dict):
                continue
            tid = ts.get("id", ts.get("thesis_id", ""))
            if not tid:
                continue
            daily_states.setdefault(tid, []).append({
                "date": date,
                **ts,
            })

    # Read current active theses for context
    active_theses = []
    active_dir = THESES_DIR / "active"
    if active_dir.exists():
        for f in sorted(active_dir.glob("*.json")):
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(t, dict):
                    active_theses.append(t)
            except Exception:
                pass

    # Read closed theses for invalidations this week
    closed_dir = THESES_DIR / "closed"
    closed_this_week = []
    if closed_dir.exists():
        date_from = snapshots[0]["date"] if snapshots else ""
        for f in sorted(closed_dir.glob("*.json")):
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(t, dict):
                    closed_date = t.get("closed_date", t.get("invalidated_date", ""))
                    if closed_date >= date_from:
                        closed_this_week.append(t)
            except Exception:
                pass

    # Build evolution records
    evolution = []
    for thesis in active_theses:
        tid = thesis.get("id", "")
        states = daily_states.get(tid, [])
        if states:
            states.sort(key=lambda s: s["date"])
            conf_start = states[0].get("confidence", thesis.get("confidence"))
            conf_end = states[-1].get("confidence", thesis.get("confidence"))
        else:
            conf_start = thesis.get("confidence")
            conf_end = thesis.get("confidence")

        evolution.append({
            "id": tid,
            "title": thesis.get("title", "")[:100],
            "status": thesis.get("status", "active"),
            "confidence_start": conf_start,
            "confidence_end": conf_end,
            "confidence_delta": (
                round(conf_end - conf_start, 3)
                if conf_start is not None and conf_end is not None
                else None
            ),
            "invalidation_condition": thesis.get("invalidation_condition", ""),
            "daily_updates_count": len(states),
        })

    return {
        "active_count": len([t for t in active_theses if t.get("status") == "active"]),
        "evolution": evolution,
        "invalidated_this_week": [
            {
                "id": t.get("id", ""),
                "title": t.get("title", "")[:100],
                "reason": t.get("invalidation_reason", t.get("close_reason", "")),
            }
            for t in closed_this_week
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Step 6: Market Structure
# ═══════════════════════════════════════════════════════════════════════════

def _summarize_market_structure(snapshots: list[dict]) -> dict:
    """週度市場結構：漲跌幅、異常值、相關性變化。"""
    if not snapshots:
        return {"weekly_returns": {}, "biggest_movers": [], "persistent_anomalies": [], "volatility": {}}

    first = snapshots[0].get("market_data", {})
    last = snapshots[-1].get("market_data", {})

    # Weekly returns: last price vs first price
    weekly_returns = {}
    for asset in last:
        if asset == "quality_scores":
            continue
        last_item = last.get(asset, {})
        first_item = first.get(asset, {})
        if not isinstance(last_item, dict) or not isinstance(first_item, dict):
            continue
        last_price = last_item.get("price") or last_item.get("value")
        first_price = first_item.get("price") or first_item.get("value")
        if last_price and first_price and last_price != MISSING_DATA and first_price != MISSING_DATA:
            try:
                lp = float(last_price)
                fp = float(first_price)
                if fp != 0:
                    weekly_returns[asset] = {
                        "first_price": round(fp, 2),
                        "last_price": round(lp, 2),
                        "return_pct": round((lp - fp) / fp * 100, 2),
                    }
            except (TypeError, ValueError):
                pass

    # Biggest movers
    movers = sorted(
        weekly_returns.items(),
        key=lambda x: abs(x[1].get("return_pct", 0)),
        reverse=True,
    )[:7]
    biggest_movers = [
        {"asset": a, "return_pct": r["return_pct"]}
        for a, r in movers
    ]

    # Persistent anomalies: count z-score alerts per asset across days
    anomaly_counts: dict[str, int] = {}
    for snap in snapshots:
        alerts = snap.get("quant", {}).get("zscore_alerts", {})
        if isinstance(alerts, dict):
            for asset, info in alerts.items():
                if isinstance(info, dict) and abs(info.get("zscore", 0)) > 2.0:
                    anomaly_counts[asset] = anomaly_counts.get(asset, 0) + 1
        elif isinstance(alerts, list):
            for alert in alerts:
                if isinstance(alert, dict):
                    asset = alert.get("asset", "")
                    if asset:
                        anomaly_counts[asset] = anomaly_counts.get(asset, 0) + 1

    persistent = [
        {"asset": a, "days_triggered": d}
        for a, d in anomaly_counts.items()
        if d >= 2
    ]
    persistent.sort(key=lambda x: x["days_triggered"], reverse=True)

    # VIX regime summary
    vix_values = []
    for snap in snapshots:
        vix_data = snap.get("market_data", {}).get("vix", {})
        if isinstance(vix_data, dict):
            p = vix_data.get("price")
            if p and p != MISSING_DATA:
                try:
                    vix_values.append(float(p))
                except (TypeError, ValueError):
                    pass

    volatility = {}
    if vix_values:
        volatility = {
            "vix_avg": round(sum(vix_values) / len(vix_values), 2),
            "vix_high": round(max(vix_values), 2),
            "vix_low": round(min(vix_values), 2),
        }

    return {
        "weekly_returns": weekly_returns,
        "biggest_movers": biggest_movers,
        "persistent_anomalies": persistent,
        "volatility": volatility,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Step 7: Risk Insights Digest
# ═══════════════════════════════════════════════════════════════════════════

def _digest_risk_insights(snapshots: list[dict]) -> dict:
    """提取本週風險官洞察。"""
    sustained_attacks = []
    risk_notes = []

    for snap in snapshots:
        date = snap.get("date", "")
        verdicts = snap.get("opus_verdicts", {})
        if not isinstance(verdicts, dict):
            continue

        # Sustained attacks
        for av in verdicts.get("attack_verdicts", []):
            if isinstance(av, dict) and av.get("verdict") == "SUSTAINED":
                sustained_attacks.append({
                    "date": date,
                    "attack_id": av.get("attack_id", ""),
                    "reason": (av.get("reason", "") or "")[:150],
                })

        # Risk officer notes
        notes = verdicts.get("risk_officer_notes", "")
        if notes:
            risk_notes.append({
                "date": date,
                "notes": notes[:200],
            })

    return {
        "sustained_attacks": sustained_attacks,
        "risk_notes": risk_notes,
        "total_sustained": len(sustained_attacks),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ═══════════════════════════════════════════════════════════════════════════

def aggregate_week(week_end_date: str) -> dict:
    """主進入點：聚合一週的資料。

    Args:
        week_end_date: 週末日期（通常是週日），格式 "YYYY-MM-DD"。
                       往回找最多 7 天。

    Returns:
        weekly_context dict，供 weekly_narrator 使用。
    """
    logger.info(f"WeeklyAggregator: aggregating week ending {week_end_date}")

    snapshots = _load_week_snapshots(week_end_date)
    if not snapshots:
        logger.warning("WeeklyAggregator: no snapshots found for this week")
        return {
            "week_label": _compute_iso_week(week_end_date),
            "date_range": {"from": week_end_date, "to": week_end_date},
            "days_available": 0,
            "degraded": True,
            "degraded_reason": "No daily snapshots available",
        }

    date_from = snapshots[0]["date"]
    date_to = snapshots[-1]["date"]
    degraded = len(snapshots) < MIN_SNAPSHOTS_FOR_FULL

    logger.info(
        f"WeeklyAggregator: found {len(snapshots)} snapshots "
        f"({date_from} ~ {date_to})"
        f"{' [DEGRADED]' if degraded else ''}"
    )

    weekly_context = {
        "week_label": _compute_iso_week(date_from),
        "date_range": {"from": date_from, "to": date_to},
        "days_available": len(snapshots),
        "degraded": degraded,
        "inference_analysis": _analyze_inferences(date_from, date_to),
        "scorecard": _compute_weekly_scorecard(date_from, date_to),
        "regime_evolution": _analyze_regime_evolution(date_from, date_to),
        "thesis_tracking": _track_thesis_evolution(snapshots),
        "market_structure": _summarize_market_structure(snapshots),
        "risk_digest": _digest_risk_insights(snapshots),
        "daily_tensions": [
            {"date": s["date"], "tension": s.get("core_tension", "")}
            for s in snapshots
        ],
        "metadata_summary": {
            "avg_coverage": round(
                sum(
                    s.get("metadata", {}).get("coverage_score", 0)
                    for s in snapshots
                ) / len(snapshots), 3
            ),
            "avg_integrity": round(
                sum(
                    s.get("metadata", {}).get("citation_integrity_score", 0)
                    for s in snapshots
                ) / len(snapshots), 3
            ),
        },
    }

    logger.info(
        f"WeeklyAggregator: done — "
        f"{weekly_context['inference_analysis']['total']} inferences, "
        f"scorecard hit_rate={weekly_context['scorecard'].get('hit_rate')}"
    )

    return weekly_context
