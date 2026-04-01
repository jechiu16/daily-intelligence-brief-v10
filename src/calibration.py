"""Calibration Engine — 校準信心值，計算 Brier Score。"""

import json
import logging
from datetime import datetime

from src.config import MISSING_DATA, SYSTEM_DIR

logger = logging.getLogger(__name__)

CALIBRATION_FILE = SYSTEM_DIR / "calibration.json"
MIN_CONFIDENCE = 0.10
MAX_CONFIDENCE = 0.90
MIN_HISTORY_FOR_CALIBRATION = 30


def _load_calibration() -> dict:
    SYSTEM_DIR.mkdir(parents=True, exist_ok=True)
    if not CALIBRATION_FILE.exists():
        return {"accuracy_by_asset_regime": {}, "bias_by_asset": {}, "total_predictions": 0}
    try:
        return json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"accuracy_by_asset_regime": {}, "bias_by_asset": {}, "total_predictions": 0}


def _save_calibration(data: dict):
    CALIBRATION_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def adjust_confidence(
    raw_confidence: float,
    asset: str,
    regime: str,
    data_coverage: float,
    verdict_adjustments: list[dict] | None = None,
) -> float:
    """
    校準信心值。

    前 30 天：adjusted = raw（無歷史精度資料）
    30 天後：用歷史 accuracy、coverage_penalty、bias_correction
    """
    cal = _load_calibration()

    if cal["total_predictions"] < MIN_HISTORY_FOR_CALIBRATION:
        # 冷啟動：只做 coverage penalty
        coverage_penalty = 1 - (1 - data_coverage) * 0.3
        adjusted = raw_confidence * coverage_penalty
    else:
        # 有歷史數據：三重校準
        key = f"{asset}_{regime}"
        historical_accuracy = cal["accuracy_by_asset_regime"].get(key, 0.5)
        coverage_penalty = 1 - (1 - data_coverage) * 0.3
        bias_correction = cal["bias_by_asset"].get(asset, 1.0)
        adjusted = raw_confidence * historical_accuracy * coverage_penalty * bias_correction

    # Opus 裁決的信心調整
    if verdict_adjustments:
        for adj in verdict_adjustments:
            if adj.get("direction") == "down":
                adjusted -= adj.get("magnitude", 0.05)
            elif adj.get("direction") == "up":
                adjusted += adj.get("magnitude", 0.05)

    # Clamp [0.1, 0.9]
    adjusted = round(min(MAX_CONFIDENCE, max(MIN_CONFIDENCE, adjusted)), 2)
    return adjusted


def apply_calibration_to_inference_chain(
    inference_chain: list[dict],
    regime: str,
    data_coverage: float,
    verdict_adjustments: list[dict] | None = None,
) -> list[dict]:
    """對 inference_chain 的每個 INF 套用校準。"""
    adjustments_by_id = {}
    if verdict_adjustments:
        for adj in verdict_adjustments:
            adjustments_by_id[adj.get("inf_id", "")] = [adj]

    for inf in inference_chain:
        inf_id = inf.get("id", "")
        raw = inf.get("raw_confidence", 0.5)

        # 找出此 inference 相關的資產
        asset = "general"
        for ev in inf.get("evidence", []):
            dk = ev.get("data_key", "")
            if dk in ["gold", "spx", "vix", "brent", "wti", "dxy", "usdjpy", "usdtwd",
                      "us10y", "tips_10y"]:
                asset = dk
                break

        inf_adjustments = adjustments_by_id.get(inf_id)
        adjusted = adjust_confidence(raw, asset, regime, data_coverage, inf_adjustments)
        inf["adjusted_confidence"] = adjusted

    return inference_chain


def record_prediction(
    date: str,
    asset: str,
    direction: str,
    confidence: float,
    regime: str,
):
    """記錄今日預測，供日後計算 Brier Score 使用。"""
    from src.config import MEMORY_DIR
    l5_path = MEMORY_DIR / "l5.json"
    try:
        l5 = json.loads(l5_path.read_text(encoding="utf-8"))
    except Exception:
        l5 = {"predictions": []}

    l5.setdefault("predictions", []).append({
        "date": date,
        "asset": asset,
        "direction": direction,
        "confidence": confidence,
        "regime": regime,
        "actual_return": None,  # 由 memory_manager 在隔日填入
        "result": None,
    })

    # 只保留最近 90 天的記錄
    l5["predictions"] = l5["predictions"][-90:]
    l5_path.write_text(json.dumps(l5, indent=2, ensure_ascii=False), encoding="utf-8")


def compute_brier_score(predictions: list[dict]) -> float | None:
    """
    Brier Score = mean((prob - outcome)²)
    outcome: 1 = correct direction, 0 = wrong
    """
    completed = [p for p in predictions if p.get("result") is not None]
    if len(completed) < 10:
        return None

    total = 0.0
    for p in completed:
        prob = p["confidence"] if p["direction"] != "down" else (1 - p["confidence"])
        outcome = 1.0 if p["result"] == "correct" else 0.0
        total += (prob - outcome) ** 2

    return round(total / len(completed), 4)


def update_outcome(date: str, asset: str, actual_return: float):
    """隔日填入實際漲跌，計算預測是否正確。"""
    from src.config import MEMORY_DIR
    l5_path = MEMORY_DIR / "l5.json"
    try:
        l5 = json.loads(l5_path.read_text(encoding="utf-8"))
    except Exception:
        return

    for p in l5.get("predictions", []):
        if p.get("date") == date and p.get("asset") == asset and p.get("result") is None:
            p["actual_return"] = actual_return
            correct = (
                (p["direction"] == "up" and actual_return > 0) or
                (p["direction"] == "down" and actual_return < 0) or
                (p["direction"] == "neutral" and abs(actual_return) < 0.005)
            )
            p["result"] = "correct" if correct else "wrong"

    brier = compute_brier_score(l5.get("predictions", []))
    l5["brier_score"] = brier
    l5["last_updated"] = datetime.now().isoformat()
    l5_path.write_text(json.dumps(l5, indent=2, ensure_ascii=False), encoding="utf-8")
