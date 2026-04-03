from __future__ import annotations
"""Inference Store — 推論歷史的核心資產。

v10.1: inference history 是系統的護城河。
每次 pipeline 結束後 append 當日所有 inferences 到 JSONL。
隔日由 fill_yesterday_outcomes 回填 outcome。

格式（每行一個 JSON object）：
{
  "date": "2026-04-03",
  "run_id": "20260403_090000Z",
  "inf_id": "INF_001",
  "claim": "黃金受實質利率下行支撐，短期偏多",
  "evidence_keys": ["gold", "tips_10y"],
  "raw_confidence": 0.65,
  "adjusted_confidence": 0.62,
  "verdict": null,
  "asset_predictions": ["gold_up"],
  "outcome": null,
  "outcome_date": null
}
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.config import TIMESERIES_DIR

logger = logging.getLogger(__name__)

INFERENCE_HISTORY_PATH = TIMESERIES_DIR / "inference_history.jsonl"


def _ensure_dir():
    TIMESERIES_DIR.mkdir(parents=True, exist_ok=True)


def append_inferences(inferences: list[dict], run_id: str, date: str):
    """Pipeline 結束後呼叫，將當日所有 inferences append 到 JSONL。

    每個 inference dict 至少需要：
    - id (str): INF_xxx 或 GEO_xxx
    - claim (str): 推論主張
    - evidence (list[dict]): 證據鏈，每個元素至少有 data_key
    - raw_confidence (float)
    - adjusted_confidence (float)

    可選：
    - verdict (str): OVERRULED / SUSTAINED / NOTED
    - asset_predictions (list[str]): e.g. ["gold_up", "spx_down"]
    """
    _ensure_dir()

    records = []
    for inf in inferences:
        evidence_keys = [
            ev.get("data_key", "") for ev in inf.get("evidence", [])
            if ev.get("data_key")
        ]
        record = {
            "date": date,
            "run_id": run_id,
            "inf_id": inf.get("id", ""),
            "claim": inf.get("claim", ""),
            "evidence_keys": evidence_keys,
            "raw_confidence": inf.get("raw_confidence"),
            "adjusted_confidence": inf.get("adjusted_confidence"),
            "verdict": inf.get("verdict"),
            "asset_predictions": inf.get("asset_predictions", []),
            "outcome": None,
            "outcome_date": None,
        }
        records.append(record)

    if not records:
        logger.debug("append_inferences: no inferences to store")
        return

    with open(INFERENCE_HISTORY_PATH, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    logger.info(f"Stored {len(records)} inferences for {date} (run_id={run_id})")


def load_all() -> list[dict]:
    """載入全部推論歷史。"""
    if not INFERENCE_HISTORY_PATH.exists():
        return []
    records = []
    with open(INFERENCE_HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning(f"Skipping malformed JSONL line: {line[:80]}...")
    return records


def query(
    *,
    asset: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    verdict: str | None = None,
    outcome: str | None = None,
) -> list[dict]:
    """查詢推論歷史。所有條件為 AND。"""
    records = load_all()
    result = []
    for rec in records:
        if asset and asset not in rec.get("evidence_keys", []):
            continue
        if date_from and rec.get("date", "") < date_from:
            continue
        if date_to and rec.get("date", "") > date_to:
            continue
        if verdict and rec.get("verdict") != verdict:
            continue
        if outcome is not None and rec.get("outcome") != outcome:
            continue
        result.append(rec)
    return result


def fill_outcomes(date: str, outcomes: dict[str, str]):
    """回填指定日期的推論結果。

    outcomes: {inf_id: "vindicated" | "refuted" | "inconclusive"}
    """
    if not INFERENCE_HISTORY_PATH.exists():
        return

    records = load_all()
    updated = 0
    for rec in records:
        if rec.get("date") == date and rec.get("inf_id") in outcomes:
            rec["outcome"] = outcomes[rec["inf_id"]]
            rec["outcome_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            updated += 1

    if updated > 0:
        # 重寫整個檔案（JSONL 不支持原地更新）
        with open(INFERENCE_HISTORY_PATH, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        logger.info(f"Updated {updated} inference outcomes for {date}")


def compute_accuracy_by_type() -> dict:
    """計算各類推論的準確率。"""
    records = load_all()
    completed = [r for r in records if r.get("outcome") is not None]
    if not completed:
        return {}

    # 按 evidence_keys 的主要資產分組
    by_asset: dict[str, dict] = {}
    for rec in completed:
        keys = rec.get("evidence_keys", [])
        primary = keys[0] if keys else "general"
        if primary not in by_asset:
            by_asset[primary] = {"total": 0, "vindicated": 0}
        by_asset[primary]["total"] += 1
        if rec["outcome"] == "vindicated":
            by_asset[primary]["vindicated"] += 1

    result = {}
    for asset, counts in by_asset.items():
        if counts["total"] > 0:
            result[asset] = {
                "accuracy": round(counts["vindicated"] / counts["total"], 3),
                "n": counts["total"],
            }
    return result
