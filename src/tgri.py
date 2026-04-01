"""TGRI — 台灣地緣風險指數計算。純 Python + 數據抓取。"""

import json
import logging
import re
import statistics
from datetime import datetime, timedelta

import requests

from src.config import MISSING_DATA, TIMESERIES_DIR

logger = logging.getLogger(__name__)

# TGRI 組件加權
TGRI_WEIGHTS = {
    "adiz_intrusions": 0.25,
    "pla_activity_level": 0.20,
    "ndf_premium": 0.20,
    "twse_foreign_net_30d": 0.15,
    "us_tw_contact_frequency": 0.10,
    "capital_flow_pressure": 0.10,
}


def _parse_mnd_tw_adiz() -> float:
    """從國防部公告解析近7天 ADIZ 侵入架次（近似值）。"""
    try:
        # 國防部公開 API 不穩定，使用 GDELT 作為替代
        url = "https://api.gdeltproject.org/api/v2/doc/doc"
        params = {
            "query": "Taiwan ADIZ PLA military",
            "mode": "artlist",
            "maxrecords": 10,
            "timespan": "7d",
            "format": "json",
        }
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            articles = data.get("articles", [])
            # 以文章數量作為 ADIZ 活動強度的代理
            return min(float(len(articles)) / 5.0, 10.0)  # 正規化到 0-10
    except Exception as e:
        logger.debug(f"ADIZ parse failed: {e}")
    return 2.0  # 基線值


def _classify_pla_activity(adiz_count: float) -> int:
    """分類 PLA 活動等級 0-3。"""
    if adiz_count >= 8:
        return 3
    elif adiz_count >= 5:
        return 2
    elif adiz_count >= 2:
        return 1
    return 0


def _count_official_contacts(days: int = 30) -> float:
    """計算近期美台官方接觸頻率（代理指標）。"""
    try:
        url = "https://api.gdeltproject.org/api/v2/doc/doc"
        params = {
            "query": "Taiwan United States official meeting",
            "mode": "artlist",
            "maxrecords": 20,
            "timespan": f"{days}d",
            "format": "json",
        }
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            articles = data.get("articles", [])
            return min(float(len(articles)), 20.0)
    except Exception as e:
        logger.debug(f"Official contacts count failed: {e}")
    return 5.0  # 基線值


def _calculate_usdtwd_ndf_premium(usdtwd_spot: float | None) -> float:
    """計算 USDTWD NDF 溢價（近似：用 yfinance 的遠匯數據）。"""
    if usdtwd_spot is None:
        return 0.0
    try:
        import yfinance as yf
        # 台幣 NDF 溢價：用期貨合約代替
        ndf = yf.Ticker("TWD=X")
        hist = ndf.history(period="5d")
        if hist.empty:
            return 0.0
        # 溢價 = (最新 - 90天均值) / 90天均值 * 100
        hist_90 = ndf.history(period="90d")
        if len(hist_90) < 30:
            return 0.0
        avg = hist_90["Close"].mean()
        current = hist_90["Close"].iloc[-1]
        premium = (current - avg) / avg * 100 if avg != 0 else 0
        return abs(round(float(premium), 2))
    except Exception as e:
        logger.debug(f"NDF premium calculation failed: {e}")
        return 0.0


def _get_twse_foreign_net_30d(data_package: dict) -> float:
    """取 TWSE 外資 30 日淨額（從 data_package）。"""
    fw = data_package.get("tw_foreign_net", {})
    val = fw.get("value") if isinstance(fw, dict) else None
    if val and val != MISSING_DATA:
        try:
            return float(val)
        except (TypeError, ValueError):
            pass
    return 0.0


def _weighted_composite(components: dict) -> float:
    """加權合成 TGRI 分數（0-100）。"""
    score = 0.0
    for key, weight in TGRI_WEIGHTS.items():
        val = components.get(key, 0)
        if val is None:
            val = 0
        # 正規化各組件到 0-100
        normalized = min(float(val) * 10, 100.0) if key in ["adiz_intrusions", "pla_activity_level"] else float(val)
        score += normalized * weight
    return round(min(max(score, 0), 100), 1)


def _compute_trend(tgri_parquet_path) -> str:
    """計算 TGRI 趨勢（rising/stable/falling）。"""
    try:
        import pandas as pd
        if not tgri_parquet_path.exists():
            return "stable"
        df = pd.read_parquet(tgri_parquet_path)
        if len(df) < 5:
            return "stable"
        recent = df["score"].tail(5).dropna()
        if len(recent) < 3:
            return "stable"
        trend = recent.iloc[-1] - recent.iloc[0]
        if trend > 5:
            return "rising"
        elif trend < -5:
            return "falling"
        return "stable"
    except Exception:
        return "stable"


def _compute_historical_percentile(score: float) -> int:
    """計算 TGRI 歷史百分位。"""
    try:
        import pandas as pd
        parquet_path = TIMESERIES_DIR / "tgri.parquet"
        if not parquet_path.exists():
            return 50
        df = pd.read_parquet(parquet_path)
        if "score" not in df.columns or len(df) < 10:
            return 50
        scores = df["score"].dropna().tolist()
        below = sum(1 for s in scores if s <= score)
        return round(below / len(scores) * 100)
    except Exception:
        return 50


def calculate_tgri(data_package: dict) -> dict:
    """計算 TGRI。"""
    # 收集各組件
    adiz = _parse_mnd_tw_adiz()
    pla_level = _classify_pla_activity(adiz)
    us_tw_contacts = _count_official_contacts(days=30)

    usdtwd = data_package.get("usdtwd", {})
    usdtwd_val = usdtwd.get("price") if isinstance(usdtwd, dict) else None
    ndf_premium = _calculate_usdtwd_ndf_premium(usdtwd_val)

    twse_net = _get_twse_foreign_net_30d(data_package)
    # 外資淨賣超越大，資本流動壓力越高
    capital_flow = max(0, -twse_net / 1e9) if twse_net < 0 else 0  # 單位: 十億

    components = {
        "adiz_intrusions": adiz,
        "pla_activity_level": float(pla_level),
        "ndf_premium": ndf_premium,
        "twse_foreign_net_30d": abs(twse_net / 1e9) if twse_net else 0,
        "us_tw_contact_frequency": us_tw_contacts,
        "capital_flow_pressure": capital_flow,
    }

    score = _weighted_composite(components)

    tgri_parquet = TIMESERIES_DIR / "tgri.parquet"
    trend = _compute_trend(tgri_parquet)
    percentile = _compute_historical_percentile(score)

    # 找主要信號
    weighted_vals = {k: components[k] * TGRI_WEIGHTS.get(k, 0) for k in components}
    dominant = max(weighted_vals, key=weighted_vals.get)

    result = {
        "score": score,
        "trend": trend,
        "dominant_signal": dominant,
        "historical_percentile": percentile,
        "components": components,
    }

    logger.info(f"TGRI: score={score}, trend={trend}, percentile={percentile}%ile")
    return result
