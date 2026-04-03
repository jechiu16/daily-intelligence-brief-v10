from __future__ import annotations
"""Calibration Engine — 校準信心值，計算 Brier Score。

v10.1: 週末/假日感知、inference linkage、scorecard 增強。
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from src.config import MISSING_DATA, SYSTEM_DIR, TIMESERIES_DIR

# ── 資產名稱正規化表（中文/縮寫 → data_package key）────────────────────────
ASSET_ALIAS_MAP = {
    # 中文名稱
    "黃金": "gold",
    "黄金": "gold",
    "原油": "brent",
    "石油": "brent",
    "美股": "spx",
    "標普": "spx",
    "台股": "usdtwd",  # 台股用 USDTWD 作為 proxy（台股無直接 change_pct）
    "台幣": "usdtwd",
    "美元指數": "dxy",
    "美元": "dxy",
    "美債": "us10y",
    "日元": "usdjpy",
    "日圓": "usdjpy",
    # 帶括號的複合名稱（取前綴）
    "brent原油": "brent",
    "wti原油": "wti",
    "美元（dxy）": "dxy",
    "日元（usd/jpy）": "usdjpy",
    "台股（twse）": "usdtwd",
    "日經（nikkei）": "usdjpy",  # Nikkei proxy
    # 英文別名
    "xau": "gold",
    "crude": "brent",
    "equities": "spx",
    "bonds": "us10y",
    "yen": "usdjpy",
    "twd": "usdtwd",
}

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
    supporting_inferences: list[str] | None = None,
):
    """記錄今日預測，供日後計算 Brier Score 使用。

    supporting_inferences: 支持此預測的 INF_xxx / GEO_xxx ID 列表，
    使每個方向性判斷都可追溯到推論鏈。
    """
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
        "supporting_inferences": supporting_inferences or [],
        "actual_return": None,
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


def _normalize_asset(raw_name: str) -> str:
    """將資產名稱正規化為 data_package key。"""
    if not raw_name:
        return raw_name
    key = raw_name.strip().lower()
    # 直接匹配
    if key in ASSET_ALIAS_MAP:
        return ASSET_ALIAS_MAP[key]
    # 中文原始名稱匹配（原始大小寫）
    if raw_name in ASSET_ALIAS_MAP:
        return ASSET_ALIAS_MAP[raw_name]
    # 前綴匹配（處理帶括號的長名稱）
    for alias, canonical in ASSET_ALIAS_MAP.items():
        if key.startswith(alias.lower()):
            return canonical
    return key  # 找不到則原樣返回


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
    l5["last_updated"] = datetime.now(timezone.utc).isoformat()
    l5_path.write_text(json.dumps(l5, indent=2, ensure_ascii=False), encoding="utf-8")


def _find_most_recent_pending_date(predictions: list[dict], before: str) -> str | None:
    """向回搜尋最近有未回填預測的日期（跳過週末/假日）。

    最多回溯 7 天，避免無限搜尋。
    """
    pending_dates = sorted(set(
        p["date"] for p in predictions
        if p.get("result") is None and p.get("date", "") < before
    ), reverse=True)
    # 取最近的（最多看 7 天前）
    cutoff = (datetime.strptime(before, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    for d in pending_dates:
        if d >= cutoff:
            return d
    return None


def fill_yesterday_outcomes(today_data_package: dict, today_str: str):
    """用今日 data_package 的 change_pct 填入最近未回填的預測結果。

    v10.1: 不再假設「昨日 = today - 1」。
    改為向回搜尋最近有未回填預測的日期，處理週末/假日。
    自動正規化資產名稱（中文/縮寫 → data_package key）。
    """
    from src.config import MEMORY_DIR
    l5_path = MEMORY_DIR / "l5.json"
    try:
        l5 = json.loads(l5_path.read_text(encoding="utf-8"))
    except Exception:
        return

    target_date = _find_most_recent_pending_date(
        l5.get("predictions", []), before=today_str
    )
    if not target_date:
        logger.debug(f"fill_yesterday_outcomes: no pending predictions before {today_str}")
        return

    pending = [
        p for p in l5.get("predictions", [])
        if p.get("date") == target_date and p.get("result") is None
    ]

    filled = 0
    for p in pending:
        raw_asset = p.get("asset", "")
        canonical = _normalize_asset(raw_asset)
        item = today_data_package.get(canonical, {})
        change_pct = item.get("change_pct") if isinstance(item, dict) else None

        if change_pct is None or change_pct == MISSING_DATA:
            logger.debug(f"fill_yesterday_outcomes: no change_pct for {canonical}")
            continue

        try:
            actual_return = float(change_pct) / 100.0
        except (TypeError, ValueError):
            continue

        p["actual_return"] = round(actual_return, 4)
        direction = p.get("direction", "neutral")
        if direction == "up":
            p["result"] = "correct" if actual_return > 0 else "wrong"
        elif direction == "down":
            p["result"] = "correct" if actual_return < 0 else "wrong"
        else:  # neutral
            p["result"] = "correct" if abs(actual_return) < 0.005 else "wrong"
        filled += 1

    if filled > 0:
        brier = compute_brier_score(l5.get("predictions", []))
        l5["brier_score"] = brier
        l5["last_updated"] = datetime.now(timezone.utc).isoformat()
        l5_path.write_text(json.dumps(l5, indent=2, ensure_ascii=False), encoding="utf-8")

        # 同步寫入 scorecard_history
        _append_scorecard(today_str, l5.get("predictions", []), brier)

        logger.info(
            f"fill_yesterday_outcomes: filled {filled}/{len(pending)} "
            f"predictions for {target_date}, Brier={brier}"
        )


def _append_scorecard(date: str, predictions: list[dict], brier: float | None):
    """每日 append scorecard 記錄到 scorecard_history.json。"""
    scorecard_path = TIMESERIES_DIR / "scorecard_history.json"
    try:
        history = json.loads(scorecard_path.read_text(encoding="utf-8"))
    except Exception:
        history = []

    # 計算分資產 Brier
    completed = [p for p in predictions if p.get("result") is not None]
    by_asset: dict[str, list] = {}
    for p in completed:
        a = p.get("asset", "general")
        by_asset.setdefault(a, []).append(p)

    asset_brier = {}
    for asset, preds in by_asset.items():
        b = compute_brier_score(preds)
        if b is not None:
            asset_brier[asset] = b

    entry = {
        "date": date,
        "brier_score": brier,
        "brier_by_asset": asset_brier,
        "total_predictions": len(completed),
        "correct": sum(1 for p in completed if p["result"] == "correct"),
        "wrong": sum(1 for p in completed if p["result"] == "wrong"),
    }

    # 避免同日重複
    history = [h for h in history if h.get("date") != date]
    history.append(entry)
    history = history[-365:]  # 保留一年

    TIMESERIES_DIR.mkdir(parents=True, exist_ok=True)
    scorecard_path.write_text(
        json.dumps(history, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
