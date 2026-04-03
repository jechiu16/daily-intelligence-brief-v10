from __future__ import annotations
"""Quant Engine — 數量信號計算。純 Python，不做文字判斷。

v10.1: NaN-aware — bootstrap_history 不再 ffill，
所有滾動計算必須容許 NaN 並在 gap > 5 天時 emit warning。
"""

import json
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy import stats

from src.config import MISSING_DATA, REGIME_TYPES, TIMESERIES_DIR

logger = logging.getLogger(__name__)


def _load_market_history() -> pd.DataFrame | None:
    """載入歷史市場數據，並檢查 NaN gap。"""
    parquet_path = TIMESERIES_DIR / "market.parquet"
    if not parquet_path.exists():
        logger.warning("market.parquet not found, quant calculations limited")
        return None
    df = pd.read_parquet(parquet_path)
    # NaN gap 偵測：連續 NaN > 5 天的欄位發出警告
    for col in df.columns:
        na_mask = df[col].isna()
        if not na_mask.any():
            continue
        # 計算最大連續 NaN 長度
        groups = (na_mask != na_mask.shift()).cumsum()
        max_gap = na_mask.groupby(groups).sum().max()
        if max_gap > 5:
            logger.warning(f"NaN gap > 5 days in '{col}': max consecutive gap = {int(max_gap)}")
    return df


def compute_rolling_correlation(df: pd.DataFrame, window: int = 30) -> dict:
    """30 天滾動相關係數矩陣（NaN-aware）。"""
    assets = ["gold", "spx", "dxy", "brent", "usdjpy", "usdtwd"]
    available = [a for a in assets if a in df.columns]

    if len(available) < 2 or len(df) < window:
        return {}

    subset = df[available].tail(window)
    # min_periods=15: 至少需要一半窗口有效數據才計算相關
    corr = subset.corr(min_periods=max(15, window // 2))

    result = {}
    for i, a1 in enumerate(available):
        for a2 in available[i + 1:]:
            key = f"{a1}_{a2}"
            val = corr.loc[a1, a2]
            result[key] = round(val, 3) if not np.isnan(val) else None
    return result


def compute_zscore_alerts(df: pd.DataFrame, window: int = 30) -> dict:
    """Z-score 異常偵測：當日變動 vs 過去 30 天的標準差。"""
    if len(df) < window + 1:
        return {}

    alerts = {}
    for col in df.columns:
        series = df[col].dropna()
        if len(series) < window + 1:
            continue
        returns = series.pct_change().dropna()
        if len(returns) < window:
            continue
        recent = returns.iloc[-window:]
        today_return = returns.iloc[-1]
        mean = recent.mean()
        std = recent.std()
        if std == 0 or np.isnan(std):
            continue
        zscore = (today_return - mean) / std
        alerts[col] = round(float(zscore), 2)
    return alerts


def compute_rolling_volatility(df: pd.DataFrame, window: int = 30) -> dict:
    """30 天滾動波動率。"""
    if len(df) < window + 1:
        return {}

    vols = {}
    for col in df.columns:
        series = df[col].dropna()
        if len(series) < window + 1:
            continue
        returns = series.pct_change().dropna().tail(window)
        vol = returns.std()
        vols[col] = round(float(vol), 5) if not np.isnan(vol) else None
    return vols


def compute_regime_probability(df: pd.DataFrame) -> dict:
    """基於歷史數據的 Regime 機率推斷。簡化版。"""
    if df is None or len(df) < 30:
        return {r: 0.25 for r in REGIME_TYPES}

    probs = {}
    # 使用近期指標特徵判斷 regime
    recent = df.tail(30)

    # 1. VIX 水位（取最後一個非 NaN 值）
    vix = recent.get("vix")
    vix_level = 20  # default
    if vix is not None:
        vix_clean = vix.dropna()
        if not vix_clean.empty:
            vix_level = float(vix_clean.iloc[-1])

    # 2. 實質利率趨勢（tips_10y）
    tips = recent.get("tips_10y")
    tips_trend = 0
    if tips is not None and len(tips.dropna()) >= 5:
        tips_clean = tips.dropna()
        tips_trend = float(tips_clean.iloc[-1] - tips_clean.iloc[-5])

    # 3. DXY 趨勢
    dxy = recent.get("dxy")
    dxy_trend = 0
    if dxy is not None and len(dxy.dropna()) >= 5:
        dxy_clean = dxy.dropna()
        dxy_trend = float(dxy_clean.iloc[-1] - dxy_clean.iloc[-5])

    # 簡化的 regime 推斷邏輯
    if vix_level > 30:
        # 高波動 → 可能是滯脹或通縮風險
        if tips_trend > 0:
            probs = {"政策過渡": 0.20, "風險偏好增長": 0.05, "滯脹": 0.55, "通縮風險": 0.20}
        else:
            probs = {"政策過渡": 0.15, "風險偏好增長": 0.05, "滯脹": 0.20, "通縮風險": 0.60}
    elif vix_level > 20:
        # 中等波動 → 政策過渡
        probs = {"政策過渡": 0.55, "風險偏好增長": 0.15, "滯脹": 0.20, "通縮風險": 0.10}
    else:
        # 低波動 → 風險偏好
        probs = {"政策過渡": 0.20, "風險偏好增長": 0.55, "滯脹": 0.15, "通縮風險": 0.10}

    # 正規化
    total = sum(probs.values())
    probs = {k: round(v / total, 2) for k, v in probs.items()}
    return probs


def compute_granger_causality(df: pd.DataFrame, maxlag: int = 10) -> list[dict]:
    """Granger Causality 檢定（每週更新）。"""
    try:
        from statsmodels.tsa.stattools import grangercausalitytests
    except ImportError:
        logger.warning("statsmodels not available for Granger causality")
        return []

    if df is None or len(df) < maxlag + 30:
        return []

    assets = ["gold", "spx", "dxy", "brent"]
    available = [a for a in assets if a in df.columns]
    results = []

    for i, cause in enumerate(available):
        for effect in available[i + 1:]:
            try:
                pair = df[[effect, cause]].dropna()
                if len(pair) < maxlag + 30:
                    continue
                returns = pair.pct_change().dropna()
                test_result = grangercausalitytests(returns, maxlag=maxlag, verbose=False)
                # 找最顯著的 lag
                best_lag = min(test_result.keys(),
                               key=lambda k: test_result[k][0]["ssr_ftest"][1])
                p_value = test_result[best_lag][0]["ssr_ftest"][1]
                if p_value < 0.05:
                    results.append({
                        "cause": cause,
                        "effect": effect,
                        "lag": best_lag,
                        "p_value": round(p_value, 4),
                    })
            except Exception as e:
                logger.debug(f"Granger {cause}→{effect}: {e}")
                continue
    return results


def classify_vix_regime(vix_price: float) -> dict:
    """VIX 水準分類：區分 level（絕對水準）vs change（相對變化）。"""
    if vix_price < 15:
        regime = "low"
    elif vix_price < 22:
        regime = "neutral"
    elif vix_price < 30:
        regime = "elevated"
    else:
        regime = "stress"
    return {"level": round(vix_price, 2), "regime": regime}


def run_quant_engine(data_package: dict | None = None) -> dict:
    """主入口：產生 quant_package。"""
    df = _load_market_history()

    correlation = compute_rolling_correlation(df, 30) if df is not None else {}
    zscore = compute_zscore_alerts(df, 30) if df is not None else {}
    vol = compute_rolling_volatility(df, 30) if df is not None else {}
    regime = compute_regime_probability(df)

    # 銅金比和 Brent-WTI 價差（從 data_package 取，若有）
    copper_gold = None
    brent_wti = None
    if data_package:
        cgr = data_package.get("copper_gold_ratio", {})
        copper_gold = cgr.get("value") if cgr.get("quality") != MISSING_DATA else None
        bws = data_package.get("brent_wti_spread", {})
        brent_wti = bws.get("value") if bws.get("quality") != MISSING_DATA else None

    # 異常檢測
    anomalies = []
    for asset, z in zscore.items():
        if z is not None and abs(z) >= 2.0:
            anomalies.append({
                "asset": asset,
                "type": "zscore_extreme",
                "value": z,
                "threshold": 2.0,
            })

    # Granger（計算量較大，可選）
    granger = compute_granger_causality(df) if df is not None and len(df) > 40 else []

    # VIX 水準分類
    vix_regime = None
    if data_package:
        vix_item = data_package.get("vix", {})
        vix_val = vix_item.get("price") if isinstance(vix_item, dict) else None
        if vix_val is not None:
            try:
                vix_regime = classify_vix_regime(float(vix_val))
            except (TypeError, ValueError):
                pass

    quant_package = {
        "correlation_matrix_30d": correlation,
        "zscore_alerts": zscore,
        "rolling_vol_30d": vol,
        "regime_probability": regime,
        "vix_regime": vix_regime,
        "copper_gold_ratio": copper_gold,
        "brent_wti_spread": brent_wti,
        "anomalies": anomalies,
        "granger_causality": granger,
    }

    logger.info(
        f"QuantEngine: {len(anomalies)} anomalies, "
        f"regime={max(regime, key=regime.get) if regime else 'unknown'}"
    )
    return quant_package


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_quant_engine()
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
