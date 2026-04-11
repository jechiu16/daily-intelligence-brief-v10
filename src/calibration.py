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
    "台股": "twse",
    "台幣": "usdtwd",
    "美元指數": "dxy",
    "美元": "dxy",
    "美債": "us10y",
    "美國10年期國債": "us10y",
    "10年期美債": "us10y",
    "日元": "usdjpy",
    "日圓": "usdjpy",
    # 帶括號的複合名稱（取前綴）
    "brent原油": "brent",
    "wti原油": "wti",
    "wti 原油": "wti",
    "美元（dxy）": "dxy",
    "日元（usd/jpy）": "usdjpy",
    "usd/jpy": "usdjpy",
    "台股（twse）": "twse",
    "台灣加權指數（twse）": "twse",
    "日經（nikkei）": "nikkei",
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

    # M5 修正：防止重跑時重複記錄同一天同資產的預測（idempotent）
    already = any(
        p.get("date") == date and p.get("asset") == asset
        for p in l5.get("predictions", [])
    )
    if already:
        logger.debug(f"record_prediction: skipping duplicate ({date}, {asset})")
        return

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
    prob = confidence in stated direction (已是對該方向的信心，不需翻轉)
    outcome: 1 = correct direction, 0 = wrong
    """
    completed = [p for p in predictions if p.get("result") is not None]
    if len(completed) < 10:
        return None

    total = 0.0
    for p in completed:
        prob = p["confidence"]  # confidence 本身即「對預測方向的信心」，不需因 down 翻轉
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
            d = p.get("direction", "neutral").lower()
            core_dir = "up" if d.startswith("up") else ("down" if d.startswith("down") else "neutral")
            correct = (
                (core_dir == "up" and actual_return > 0) or
                (core_dir == "down" and actual_return < 0) or
                (core_dir == "neutral" and abs(actual_return) < 0.005)
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
        direction = p.get("direction", "neutral").lower()
        # 正規化非標準 direction：up_yield / up_risk → up；down_risk → down；neutral_* → neutral
        if direction.startswith("up"):
            core_dir = "up"
        elif direction.startswith("down"):
            core_dir = "down"
        else:
            core_dir = "neutral"
        if core_dir == "up":
            p["result"] = "correct" if actual_return > 0 else "wrong"
        elif core_dir == "down":
            p["result"] = "correct" if actual_return < 0 else "wrong"
        else:
            p["result"] = "correct" if abs(actual_return) < 0.005 else "wrong"
        filled += 1

    if filled > 0:
        brier = compute_brier_score(l5.get("predictions", []))
        l5["brier_score"] = brier
        l5["last_updated"] = datetime.now(timezone.utc).isoformat()
        l5_path.write_text(json.dumps(l5, indent=2, ensure_ascii=False), encoding="utf-8")

        # 同步寫入 scorecard_history
        _append_scorecard(today_str, l5.get("predictions", []), brier)

        # C1 修正：更新校準統計（accuracy_by_asset_regime / bias_by_asset）
        _update_calibration_stats(l5.get("predictions", []))

        logger.info(
            f"fill_yesterday_outcomes: filled {filled}/{len(pending)} "
            f"predictions for {target_date}, Brier={brier}"
        )


def _update_calibration_stats(predictions: list[dict]):
    """用已完成的預測更新 accuracy_by_asset_regime 和 bias_by_asset，寫入 calibration.json。

    C1 修正：`_save_calibration` 先前定義但從未被呼叫，導致校準永遠用預設值 0.5。
    此函式在每次 fill_yesterday_outcomes 有新填入結果後呼叫。
    """
    from collections import defaultdict

    completed = [p for p in predictions if p.get("result") is not None]
    if not completed:
        return

    cal = _load_calibration()

    # accuracy_by_asset_regime：每個 (asset, regime) 組合的正確率
    ar_counts: dict[str, list[int]] = defaultdict(list)
    for p in completed:
        asset = p.get("asset", "general")
        regime = p.get("regime", "unknown")
        key = f"{asset}_{regime}"
        ar_counts[key].append(1 if p["result"] == "correct" else 0)

    for key, results in ar_counts.items():
        cal["accuracy_by_asset_regime"][key] = round(sum(results) / len(results), 4)

    # bias_by_asset：accuracy / avg_confidence（需至少 5 筆才計算）
    asset_data: dict[str, dict] = defaultdict(lambda: {"correct": 0, "total": 0, "sum_conf": 0.0})
    for p in completed:
        asset = p.get("asset", "general")
        asset_data[asset]["total"] += 1
        asset_data[asset]["sum_conf"] += p.get("confidence", 0.5)
        if p["result"] == "correct":
            asset_data[asset]["correct"] += 1

    for asset, d in asset_data.items():
        if d["total"] >= 5:
            accuracy = d["correct"] / d["total"]
            avg_conf = d["sum_conf"] / d["total"]
            if avg_conf > 0:
                bias = round(accuracy / avg_conf, 4)
                # Clamp [0.5, 2.0] 避免極端值破壞校準
                cal["bias_by_asset"][asset] = max(0.5, min(2.0, bias))

    cal["total_predictions"] = len(completed)

    # DA / PreMortem meta-stats（每次 fill_yesterday_outcomes 時更新，非致命）
    try:
        da_stats = _compute_da_premortem_stats(window_days=30)
        if da_stats:
            cal["da_premortem_stats"] = da_stats
    except Exception as _e:
        logger.warning(f"_compute_da_premortem_stats failed (non-fatal): {_e}")

    _save_calibration(cal)
    logger.info(
        f"_update_calibration_stats: {len(ar_counts)} asset-regime keys, "
        f"total_predictions={cal['total_predictions']}"
    )


def _compute_da_premortem_stats(window_days: int = 30) -> dict:
    """掃描最近 window_days 天的 daily_snapshots，計算：
    - da_sustained_rate: SUSTAINED 攻擊 / 總攻擊
    - da_total: 總攻擊次數
    - premortem_scenario_count: 累計 premortem 場景數（若已存入 snapshot）
    """
    from src.config import SNAPSHOTS_DIR
    from datetime import date as _date, timedelta

    cutoff = (_date.today() - timedelta(days=window_days)).isoformat()

    total_attacks = 0
    sustained_attacks = 0
    premortem_scenarios = 0

    if not SNAPSHOTS_DIR.exists():
        return {}

    for f in sorted(SNAPSHOTS_DIR.glob("*.json")):
        date_str = f.stem
        if date_str < cutoff:
            continue
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        verdicts = snap.get("opus_verdicts", {})
        for av in verdicts.get("attack_verdicts", []):
            if not isinstance(av, dict):
                continue
            total_attacks += 1
            if av.get("verdict") == "SUSTAINED":
                sustained_attacks += 1

        # premortem_scenarios 欄位（若已寫入）
        premortem_scenarios += len(snap.get("premortem_scenarios", []))

    result: dict = {
        "window_days": window_days,
        "da_total_attacks": total_attacks,
        "da_sustained_attacks": sustained_attacks,
        "da_sustained_rate": round(sustained_attacks / total_attacks, 3) if total_attacks else None,
        "premortem_scenario_count": premortem_scenarios,
    }
    return result


def fill_inference_outcomes(data_package: dict, today_str: str):
    """Step 2.5c: 用今日市場數據回填昨日 inference_store 的 outcome。

    對每條有 asset_predictions 的推論，查今日 change_pct 判斷方向是否正確：
    - 全部預測方向命中 → "vindicated"
    - 任一方向錯誤    → "refuted"
    - 無數據           → "inconclusive"
    """
    from src import inference_store

    records = inference_store.load_all()
    pending = [r for r in records if r.get("outcome") is None and r.get("date", "") < today_str]
    if not pending:
        logger.debug(f"fill_inference_outcomes: no pending inferences before {today_str}")
        return

    target_date = max(r["date"] for r in pending)
    target_records = [r for r in pending if r["date"] == target_date]

    outcomes: dict[str, str] = {}
    for rec in target_records:
        predictions_list = rec.get("asset_predictions", [])
        if not predictions_list:
            outcomes[rec["inf_id"]] = "inconclusive"
            continue

        results = []
        for pred in predictions_list:
            # 格式："{asset}_{direction}"，e.g. "gold_up", "spx_down"
            parts = pred.rsplit("_", 1)
            if len(parts) != 2:
                continue
            asset_key, direction = parts
            canonical = _normalize_asset(asset_key) if asset_key else asset_key
            item = data_package.get(canonical, {})
            change_pct = item.get("change_pct") if isinstance(item, dict) else None
            if change_pct is None or change_pct == MISSING_DATA:
                continue
            try:
                ret = float(change_pct)
            except (TypeError, ValueError):
                continue
            if direction == "up":
                results.append("vindicated" if ret > 0 else "refuted")
            elif direction == "down":
                results.append("vindicated" if ret < 0 else "refuted")

        if not results:
            outcomes[rec["inf_id"]] = "inconclusive"
        elif all(r == "vindicated" for r in results):
            outcomes[rec["inf_id"]] = "vindicated"
        elif any(r == "refuted" for r in results):
            outcomes[rec["inf_id"]] = "refuted"
        else:
            outcomes[rec["inf_id"]] = "inconclusive"

    if outcomes:
        inference_store.fill_outcomes(target_date, outcomes)
        logger.info(
            f"fill_inference_outcomes: filled {len(outcomes)} inferences for {target_date} "
            f"(vindicated={sum(1 for v in outcomes.values() if v=='vindicated')}, "
            f"refuted={sum(1 for v in outcomes.values() if v=='refuted')}, "
            f"inconclusive={sum(1 for v in outcomes.values() if v=='inconclusive')})"
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
