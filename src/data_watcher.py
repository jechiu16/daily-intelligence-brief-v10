from __future__ import annotations
"""DataWatcher — 拉取市場指標，執行降級鏈，標記品質。

v10.1 變更：
- 統一 run_timestamp（UTC），所有資產共用
- SOURCE_TIER 品質分級：Tier C 來源一律標記 estimated
- 移除 sg_nodx（SingStat 不穩 + proxy 太間接）
- 移除 Stooq BDI fallback（CSV scraping 不可靠）
- tension_note 邏輯移至 tension_engine.py
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ssl
import certifi
ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from fredapi import Fred

from src.config import (
    ALPHA_VANTAGE_API_KEY, CACHE_DIR, EIA_API_KEY, FINNHUB_API_KEY,
    FRED_API_KEY, FRED_SERIES, MISSING_DATA, SYSTEM_DIR, YFINANCE_TICKERS,
    SOURCE_TIER, quality_for_source,
)

logger = logging.getLogger(__name__)

CACHE_DIR.mkdir(parents=True, exist_ok=True)
SYSTEM_DIR.mkdir(parents=True, exist_ok=True)


# ── Retry helper ────────────────────────────────────────────────────────────

def _with_retry(func, *, retries: int = 3, base_wait: float = 1.5, label: str = ""):
    """執行 func()，失敗時指數退避重試。回傳 func() 的結果或 None。

    - retries: 最大重試次數（不含首次）
    - base_wait: 初始等待秒數（每次 ×2）
    - label: 用於 log 的識別字串
    """
    for attempt in range(retries + 1):
        try:
            result = func()
            if result is not None:
                return result
            # None 也算失敗（fetcher 回傳 None = 資料不可用）
            if attempt < retries:
                wait = base_wait * (2 ** attempt)
                logger.debug(f"{label}: returned None (attempt {attempt+1}/{retries+1}), retry in {wait:.1f}s")
                time.sleep(wait)
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as e:
            if attempt < retries:
                wait = base_wait * (2 ** attempt)
                logger.warning(f"{label}: network error (attempt {attempt+1}/{retries+1}): {e} — retry in {wait:.1f}s")
                time.sleep(wait)
            else:
                logger.warning(f"{label}: all {retries+1} attempts failed: {e}")
        except Exception as e:
            # 非網路錯誤（如 API 格式錯誤）不重試
            logger.warning(f"{label}: non-retryable error: {e}")
            return None
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Cache helpers
# ═══════════════════════════════════════════════════════════════════════════

def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def _write_cache(key: str, value, timestamp: str):
    _cache_path(key).write_text(
        json.dumps({"value": value, "timestamp": timestamp}, ensure_ascii=False),
        encoding="utf-8",
    )


def _read_cache(key: str, max_age_hours: int = 48) -> dict | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ts_str = data["timestamp"]
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        quality = "cached" if age <= 24 else "stale"
        return {"value": data["value"], "quality": quality, "timestamp": ts_str}
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Data Source Fetchers
# ═══════════════════════════════════════════════════════════════════════════

def _fetch_yfinance(ticker: str, key: str, source_label: str = "yfinance") -> dict | None:
    """yfinance 抓取單一指標。"""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="2d")
        if hist.empty:
            return None
        latest = hist.iloc[-1]
        price = float(latest["Close"])
        prev = float(hist.iloc[-2]["Close"]) if len(hist) >= 2 else price
        change_pct = round((price - prev) / prev * 100, 2) if prev != 0 else 0
        ingestion_ts = datetime.now(timezone.utc).isoformat()
        # data_timestamp：yfinance bar 的日期
        bar_date = hist.index[-1]
        data_ts = bar_date.isoformat() if hasattr(bar_date, "isoformat") else str(bar_date)
        _write_cache(key, price, ingestion_ts)
        return {
            "price": round(price, 4),
            "change_pct": change_pct,
            "source": source_label,
            "quality": quality_for_source(source_label),
            "timestamp": ingestion_ts,
            "data_timestamp": data_ts,
            "ingestion_timestamp": ingestion_ts,
            "asia_prev_day": False,
        }
    except Exception as e:
        logger.warning(f"yfinance {ticker} failed: {e}")
        return None


def _fetch_yfinance_history(ticker: str, days: int = 120) -> pd.Series | None:
    """拉取歷史數據用於冷啟動。"""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=f"{days}d")
        if hist.empty:
            return None
        return hist["Close"]
    except Exception as e:
        logger.warning(f"yfinance history {ticker} failed: {e}")
        return None


def _fetch_fred(series_id: str, key: str, lookback_days: int = 30,
                source_label: str = "FRED") -> dict | None:
    """FRED API 抓取。"""
    if not FRED_API_KEY:
        return None
    try:
        fred = Fred(api_key=FRED_API_KEY)
        data = fred.get_series(series_id, observation_start=(datetime.now(timezone.utc) - timedelta(days=lookback_days)))
        if data.empty:
            return None
        clean = data.dropna()
        latest_val = float(clean.iloc[-1])
        latest_date = str(clean.index[-1].date())
        ingestion_ts = datetime.now(timezone.utc).isoformat()
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _write_cache(key, latest_val, ingestion_ts)
        return {
            "value": round(latest_val, 4),
            "source": source_label,
            "series_id": series_id,
            "quality": quality_for_source(
                source_label,
                observation_date=latest_date,
                today_str=today_str,
            ),
            "timestamp": ingestion_ts,
            "observation_date": latest_date,
            "data_timestamp": latest_date,
            "ingestion_timestamp": ingestion_ts,
            "asia_prev_day": False,
        }
    except Exception as e:
        logger.warning(f"FRED {series_id} failed: {e}")
        return None


def _fetch_us_cpi_yfinance(key: str = "us_cpi") -> dict | None:
    """yfinance 備援：用 RINF ETF 的 NAV 做 CPI 代理，或直接抓 BLS 公開端點。"""
    try:
        # 嘗試 BLS 公開 JSON API（不需 API key）
        url = "https://api.bls.gov/publicAPI/v2/timeseries/data/CUUR0000SA0"
        params = {
            "startyear": str(datetime.now(timezone.utc).year - 1),
            "endyear": str(datetime.now(timezone.utc).year),
        }
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        series_data = payload.get("Results", {}).get("series", [])
        if series_data:
            observations = series_data[0].get("data", [])
            # observations 已按最新→最舊排列
            for obs in observations:
                try:
                    val = float(obs["value"])
                    year = obs.get("year", "")
                    period = obs.get("period", "")  # e.g. "M02"
                    obs_date = f"{year}-{period[1:]}-01" if period.startswith("M") else year
                    now = datetime.now(timezone.utc).isoformat()
                    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    _write_cache(key, val, now)
                    return {
                        "value": round(val, 4),
                        "source": "BLS",
                        "quality": quality_for_source(
                            "BLS",
                            observation_date=obs_date,
                            today_str=today_str,
                        ),
                        "timestamp": now,
                        "observation_date": obs_date,
                    }
                except (ValueError, KeyError):
                    continue
    except Exception as e:
        logger.warning(f"BLS CPI fetch failed: {e}")
    return None


def _fetch_finnhub_quote(symbol: str, key: str) -> dict | None:
    """Finnhub 報價備援。"""
    if not FINNHUB_API_KEY:
        return None
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("c", 0) == 0:
            return None
        now = datetime.now(timezone.utc).isoformat()
        _write_cache(key, data["c"], now)
        change_pct = round(data.get("dp", 0), 2)
        return {
            "price": round(data["c"], 4),
            "change_pct": change_pct,
            "source": "finnhub",
            "quality": quality_for_source("finnhub"),
            "timestamp": now,
        }
    except Exception as e:
        logger.warning(f"Finnhub {symbol} failed: {e}")
        return None


def _fetch_alpha_vantage(symbol: str, key: str) -> dict | None:
    """Alpha Vantage 備援。"""
    if not ALPHA_VANTAGE_API_KEY:
        return None
    try:
        url = "https://www.alphavantage.co/query"
        params = {
            "function": "GLOBAL_QUOTE",
            "symbol": symbol,
            "apikey": ALPHA_VANTAGE_API_KEY,
        }
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("Global Quote", {})
        if not data:
            return None
        price = float(data.get("05. price", 0))
        change_pct = float(data.get("10. change percent", "0").replace("%", ""))
        now = datetime.now(timezone.utc).isoformat()
        _write_cache(key, price, now)
        return {
            "price": round(price, 4),
            "change_pct": round(change_pct, 2),
            "source": "alpha_vantage",
            "quality": quality_for_source("alpha_vantage"),
            "timestamp": now,
        }
    except Exception as e:
        logger.warning(f"Alpha Vantage {symbol} failed: {e}")
        return None


def _fetch_gold(key: str = "gold") -> dict | None:
    """黃金定價：yFinance GC=F（COMEX 黃金期貨結算價）。

    FRED LBMA 系列（GOLDAMGBD228NLBM）已下架，直接使用 COMEX GC=F。
    source 標記為 COMEX_GC，quality = confirmed。
    """
    result = _fetch_yfinance("GC=F", key, source_label="COMEX_GC")
    return result


# ─── 衝突偵測 ────────────────────────────────────────────────────────────────

_CONFLICT_THRESHOLDS: dict[str, float] = {
    "oil":    0.01,   # WTI / Brent：1%
    "gold":   0.005,  # 黃金：0.5%
    "equity": 0.003,  # 股指：0.3%
}


def _check_source_conflict(
    symbol: str,
    value_a: float,
    source_a: str,
    value_b: float,
    source_b: str,
    asset_class: str = "equity",
) -> str:
    """比較兩個來源數值，超過閾值時 log warning，回傳應採用的來源名稱。

    優先選擇標記 'official_settlement' 的來源，否則選 source_a（第一個）。
    """
    if value_a == 0 or value_b == 0:
        return source_a
    diff = abs(value_a - value_b) / max(abs(value_a), abs(value_b))
    threshold = _CONFLICT_THRESHOLDS.get(asset_class, 0.003)
    if diff > threshold:
        logger.warning(
            f"SOURCE CONFLICT [{symbol}]: {source_a}={value_a:.4f} vs "
            f"{source_b}={value_b:.4f} → diff={diff:.2%} > threshold={threshold:.1%}"
        )
    # 優先 official_settlement
    if "official_settlement" in source_b.lower():
        return source_b
    return source_a


def _fetch_eia_crude() -> dict | None:
    """EIA 原油庫存（透過 _with_retry 重試，timeout 30s）。"""
    if not EIA_API_KEY:
        return None
    url = "https://api.eia.gov/v2/petroleum/stoc/wstk/data/"
    params = {
        "api_key": EIA_API_KEY,
        "frequency": "weekly",
        "data[0]": "value",
        "facets[product][]": "EPC0",
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "length": 1,
    }

    def _do_fetch():
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        records = data.get("response", {}).get("data", [])
        if not records:
            return None
        latest = records[0]
        now = datetime.now(timezone.utc).isoformat()
        _write_cache("eia_crude_inventory", latest["value"], now)
        return {
            "value": float(latest["value"]),
            "period": latest.get("period", ""),
            "source": "EIA",
            "quality": "confirmed",
            "timestamp": now,
        }

    return _with_retry(_do_fetch, retries=2, base_wait=2.0, label="EIA_crude")


def _fetch_akshare_safe(func_name: str, key: str, **kwargs) -> dict | None:
    """安全呼叫 akshare，介面常變動所以包在 try/except。Tier C → estimated。"""
    try:
        import akshare as ak
        func = getattr(ak, func_name, None)
        if func is None:
            logger.warning(f"akshare function {func_name} not found")
            return None
        df = func(**kwargs)
        if df is None or (hasattr(df, "empty") and df.empty):
            return None
        now = datetime.now(timezone.utc).isoformat()
        return {"data": df, "source": "akshare", "quality": "estimated", "timestamp": now}
    except Exception as e:
        logger.warning(f"akshare {func_name} failed: {e}")
        return None


# ─── 實際 Fetcher：台灣外資買賣超 ────────────────────────────────────────────

def _fetch_tw_foreign_net(key: str = "tw_foreign_net") -> dict | None:
    """TWSE BFI82U：外資及陸資每日買賣超（億台幣）。"""
    try:
        url = "https://www.twse.com.tw/fund/BFI82U?response=json&type=day"
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.json()
        if data.get("stat") != "OK":
            return None
        for row in data.get("data", []):
            # 精確找「外資及陸資(不含外資自營商)」那行
            if "不含外資自營商" in row[0]:
                net_str = row[3].replace(",", "").replace(" ", "")
                net_val = round(float(net_str) / 1e8, 2)  # 元 → 億元
                now = datetime.now(timezone.utc).isoformat()
                _write_cache(key, net_val, now)
                return {
                    "value": net_val,
                    "unit": "億台幣",
                    "source": "TWSE",
                    "quality": quality_for_source("TWSE"),
                    "timestamp": now,
                }
    except Exception as e:
        logger.warning(f"TWSE foreign net failed: {e}")
    return None


# ─── 實際 Fetcher：Caixin 製造業 PMI ─────────────────────────────────────────

def _fetch_caixin_pmi(key: str = "caixin_pmi") -> dict | None:
    """akshare index_pmi_man_cx：財新中國製造業 PMI。"""
    try:
        import akshare as ak
        df = ak.index_pmi_man_cx()
        if df is None or df.empty:
            return None
        latest = df.dropna(subset=["制造业PMI"]).iloc[-1]
        val = float(latest["制造业PMI"])
        now = datetime.now(timezone.utc).isoformat()
        _write_cache(key, val, now)
        return {
            "value": round(val, 1),
            "observation_date": str(latest["日期"]),
            "source": "Caixin/akshare",
            "quality": quality_for_source("Caixin/akshare"),  # Tier C → estimated
            "timestamp": now,
        }
    except Exception as e:
        logger.warning(f"Caixin PMI failed: {e}")
    return None


# ─── 實際 Fetcher：CFTC COT 黃金淨多倉 ──────────────────────────────────────

def _fetch_cot_gold(key: str = "cot_gold") -> dict | None:
    """CFTC COT 黃金非商業淨多倉（口數）。直接從 CFTC 官網下載。"""
    try:
        return _fetch_cot_gold_cftc(key)
    except Exception as e:
        logger.warning(f"COT gold CFTC direct failed: {e}")
    # fallback: akshare
    try:
        import akshare as ak
        df = ak.macro_usa_cftc_c_holding()
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            gold_net = float(latest.get("黄金-净仓位", 0))
            now = datetime.now(timezone.utc).isoformat()
            _write_cache(key, gold_net, now)
            return {
                "value": round(gold_net, 0),
                "unit": "contracts",
                "source": "CFTC/akshare",
                "quality": quality_for_source("CFTC/akshare"),
                "timestamp": now,
            }
    except Exception as e:
        logger.warning(f"COT gold akshare fallback failed: {e}")
    return None


def _fetch_cot_gold_cftc(key: str = "cot_gold") -> dict | None:
    """直接從 CFTC 官網下載最新 COT 報告（Combined Short Format）。
    Gold futures = COMEX, CFTC contract code 088691.
    每週五公佈上週二的數據。URL 格式穩定。Tier B。
    Short format columns: col[8]=NonComm Long, col[9]=NonComm Short."""
    import urllib.request

    url = "https://www.cftc.gov/dea/newcot/deacom.txt"
    logger.info(f"COT gold: downloading {url}")

    req = urllib.request.Request(url, headers={"User-Agent": "DIB/10.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")

    for line in text.strip().split("\n"):
        if "088691" not in line:
            continue
        parts = [p.strip().strip('"') for p in line.split(",")]
        report_date = parts[2]  # YYYY-MM-DD
        nc_long = int(parts[8])
        nc_short = int(parts[9])
        net_position = nc_long - nc_short

        ts = datetime.now(timezone.utc).isoformat()
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _write_cache(key, net_position, ts)

        return {
            "value": net_position,
            "unit": "contracts",
            "observation_date": report_date,
            "source": "CFTC",
            "quality": quality_for_source(
                "CFTC",
                observation_date=report_date,
                today_str=today_str,
            ),
            "timestamp": ts,
        }

    logger.warning("COT gold: code 088691 not found in CFTC data")
    return None


# ─── 實際 Fetcher：BDI 波羅的海乾散貨指數 ───────────────────────────────────

def _fetch_bdi(key: str = "bdi") -> dict | None:
    """akshare macro_shipping_bdi：波羅的海乾散貨指數。"""
    try:
        import akshare as ak
        df = ak.macro_shipping_bdi()
        if df is None or df.empty:
            return None
        # 找收盤欄位：akshare macro_shipping_bdi 使用「最新值」
        close_col = None
        for col in df.columns:
            if col in ("最新值", "close", "Close", "收盤"):
                close_col = col
                break
        if close_col is None:
            numeric_cols = df.select_dtypes(include=["number"]).columns
            if len(numeric_cols) > 0:
                close_col = numeric_cols[0]
        if close_col is None:
            return None
        latest_val = float(df[close_col].dropna().iloc[-1])
        now = datetime.now(timezone.utc).isoformat()
        _write_cache(key, latest_val, now)
        return {
            "value": round(latest_val, 0),
            "source": "Baltic/akshare",
            "quality": quality_for_source("Baltic/akshare"),  # Tier C → estimated
            "timestamp": now,
        }
    except Exception as e:
        logger.warning(f"BDI akshare failed: {e}")
    return None


# Stooq BDI fallback 已移除（v10.1）：CSV scraping 不可靠，只保留 akshare


# ─── 實際 Fetcher：韓國出口 YoY% ────────────────────────────────────────────

def _fetch_korea_export(key: str = "korea_export") -> dict | None:
    """FRED XTEXVA01KRM664S：韓國商品出口（韓元）YoY 計算。"""
    if not FRED_API_KEY:
        return None
    try:
        fred = Fred(api_key=FRED_API_KEY)
        data = fred.get_series(
            "XTEXVA01KRM664S",
            observation_start=(datetime.now(timezone.utc) - timedelta(days=450)).strftime("%Y-%m-%d"),
        )
        if data is None or data.empty:
            return None
        data = data.dropna().sort_index()
        if len(data) < 13:
            return None
        latest_val = float(data.iloc[-1])
        yoy_val = float(data.iloc[-13])
        yoy_pct = round((latest_val / yoy_val - 1) * 100, 2) if yoy_val != 0 else 0
        latest_date = str(data.index[-1].date())
        now = datetime.now(timezone.utc).isoformat()
        _write_cache(key, yoy_pct, now)
        return {
            "value": yoy_pct,
            "unit": "YoY%",
            "observation_date": latest_date,
            "source": "FRED",
            "quality": "confirmed",
            "timestamp": now,
        }
    except Exception as e:
        logger.warning(f"Korea export FRED failed: {e}")
    return None


# ─── 實際 Fetcher：台灣出口 YoY% ────────────────────────────────────────────

def _fetch_tw_export(key: str = "tw_export") -> dict | None:
    """台灣出口 YoY%。

    Primary: FRED VALEXPTWM052N（IMF Taiwan 商品出口，月頻）。
    Secondary: MOF 財政部統計月報 API（端點不穩，保留嘗試）。
    """
    # Primary: FRED（穩定）
    try:
        if not FRED_API_KEY:
            return None
        fred = Fred(api_key=FRED_API_KEY)
        data = fred.get_series(
            "VALEXPTWM052N",  # IMF: Taiwan Goods Value of Exports (USD millions, monthly)
            observation_start=(datetime.now(timezone.utc) - timedelta(days=600)).strftime("%Y-%m-%d"),
        )
        if data is not None and not data.empty and len(data.dropna()) >= 13:
            data = data.dropna()
            latest = float(data.iloc[-1])
            prev_yr = float(data.iloc[-13])
            yoy = round((latest / prev_yr - 1) * 100, 2) if prev_yr != 0 else 0
            now = datetime.now(timezone.utc).isoformat()
            _write_cache(key, yoy, now)
            return {
                "value": yoy,
                "unit": "YoY%",
                "observation_date": str(data.index[-1].date()),
                "source": "FRED_IMF",
                "quality": "estimated",
                "timestamp": now,
            }
    except Exception as e:
        logger.warning(f"Taiwan export FRED primary failed: {e}")

    # Secondary: MOF 財政部統計月報（端點不穩，僅嘗試）
    try:
        url = "https://www.mof.gov.tw/Download/TradeStatistics"
        params = {"type": "json", "lang": "en"}
        resp = requests.get(url, params=params, timeout=15,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        records = resp.json()
        if records:
            latest = sorted(records, key=lambda x: x.get("YearMonth", ""), reverse=True)[0]
            yoy = float(latest.get("ExportYOY", 0))
            now = datetime.now(timezone.utc).isoformat()
            _write_cache(key, yoy, now)
            return {
                "value": round(yoy, 2),
                "unit": "YoY%",
                "observation_date": latest.get("YearMonth", ""),
                "source": "MOF_Taiwan",
                "quality": quality_for_source("MOF_Taiwan"),  # Tier C → estimated
                "timestamp": now,
            }
    except Exception as e:
        logger.warning(f"Taiwan export MOF secondary failed: {e}")
    return None


# ─── 實際 Fetcher：台灣領先指標 ─────────────────────────────────────────────

def _fetch_tw_leading(key: str = "tw_leading") -> dict | None:
    """國發會景氣指標：台灣綜合領先指標（含趨勢指數）。"""
    try:
        # 嘗試 NDC 景氣指標 API
        url = "https://index.ndc.gov.tw/n/json/leading-index"
        resp = requests.get(url, timeout=15,
                            headers={"User-Agent": "Mozilla/5.0",
                                     "Referer": "https://index.ndc.gov.tw/"})
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            latest = sorted(data, key=lambda x: x.get("yearMonth", ""), reverse=True)[0]
            val = float(latest.get("cyclicalScore", latest.get("value", 0)))
            now = datetime.now(timezone.utc).isoformat()
            _write_cache(key, val, now)
            return {
                "value": round(val, 2),
                "observation_date": latest.get("yearMonth", ""),
                "source": "NDC_Taiwan",
                "quality": quality_for_source("NDC_Taiwan"),  # Tier C → estimated
                "timestamp": now,
            }
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        if status == 403:
            logger.info("Taiwan leading NDC denied access (403); using TWII proxy fallback")
        else:
            logger.warning(f"Taiwan leading NDC failed: {e}")
    except Exception as e:
        logger.warning(f"Taiwan leading NDC failed: {e}")

    # 備援：^TWII（加權指數）YTD% 作為領先指標 proxy
    try:
        t = yf.Ticker("^TWII")
        hist = t.history(period="1y")
        if hist is not None and not hist.empty and len(hist) >= 20:
            latest_price = float(hist["Close"].iloc[-1])
            year_ago_price = float(hist["Close"].iloc[0])
            ytd_pct = round((latest_price / year_ago_price - 1) * 100, 2) if year_ago_price != 0 else 0
            now = datetime.now(timezone.utc).isoformat()
            _write_cache(key, ytd_pct, now)
            return {
                "value": ytd_pct,
                "unit": "YoY%",
                "observation_date": str(hist.index[-1].date()),
                "source": "yfinance_TWII_proxy",
                "quality": "estimated",
                "timestamp": now,
            }
    except Exception as e:
        logger.warning(f"Taiwan leading TWII proxy failed: {e}")
    return None


# ─── 實際 Fetcher：台股加權指數（TWSE）────────────────────────────────────────

def _fetch_twse_yfinance(key: str = "twse") -> dict | None:
    """yfinance ^TWII：台股加權指數，取前一個已完結交易日收盤（亞洲時區感知）。

    修正：動態判斷 yfinance 最後一筆是否已是完整收盤日，而非固定取 iloc[-2]。
    - 若最後一筆日期 < 台灣今日 → 已完結，直接取 iloc[-1]
    - 若最後一筆日期 >= 台灣今日 → 台股可能尚未收盤，取 iloc[-2]
    這解決了 pipeline 在下午執行時抓到滯後一天數據的問題。
    """
    try:
        import pytz
        t = yf.Ticker("^TWII")
        hist = t.history(period="5d")
        if hist.empty or len(hist) < 2:
            return None

        # 判斷最後一筆是否已是完整的前一交易日
        tw_tz = pytz.timezone("Asia/Taipei")
        today_tw = datetime.now(tw_tz).date()
        last_bar_date = hist.index[-1].astimezone(tw_tz).date()

        if last_bar_date < today_tw:
            # 最後一筆是昨天或更早 → 完整收盤，直接用
            price_row = hist.iloc[-1]
            prev_row_data = hist.iloc[-2]
            bar_idx = -1
        else:
            # 最後一筆是今天 → 台股可能還在交易，用前一筆
            if len(hist) < 3:
                return None
            price_row = hist.iloc[-2]
            prev_row_data = hist.iloc[-3]
            bar_idx = -2

        price = float(price_row["Close"])
        prev_price = float(prev_row_data["Close"])
        change_pct = round((price - prev_price) / prev_price * 100, 2) if prev_price != 0 else 0.0
        ingestion_ts = datetime.now(timezone.utc).isoformat()
        bar_date = hist.index[bar_idx]
        data_ts = bar_date.isoformat() if hasattr(bar_date, "isoformat") else str(bar_date)
        _write_cache(key, price, ingestion_ts)
        return {
            "price": round(price, 2),
            "change_pct": change_pct,
            "source": "yfinance_TWII",
            "quality": "confirmed",
            "timestamp": ingestion_ts,
            "data_timestamp": data_ts,
            "ingestion_timestamp": ingestion_ts,
            "asia_prev_day": True,
        }
    except Exception as e:
        logger.warning(f"TWSE yfinance (^TWII) failed: {e}")
    return None



# sg_nodx 已移除（v10.1）：SingStat 端點不穩 + FRED proxy 太間接，weight 僅 0.3


# ═══════════════════════════════════════════════════════════════════════════
# Fallback chain
# ═══════════════════════════════════════════════════════════════════════════

def get_with_fallback(key: str, fetchers: list[callable]) -> dict:
    """依序嘗試各 fetcher（含指數退避重試），全部失敗則用快取，最後 MISSING_DATA。"""
    for fetcher in fetchers:
        label = f"{key}:{getattr(fetcher, '__name__', 'lambda')}"
        result = _with_retry(fetcher, retries=2, base_wait=1.5, label=label)
        if result is not None:
            return result

    # 嘗試快取
    cached = _read_cache(key)
    if cached is not None:
        logger.info(f"{key}: using {cached['quality']} cache")
        return {
            "value": cached["value"],
            "source": "cache",
            "quality": cached["quality"],
            "timestamp": cached["timestamp"],
        }

    # 嘗試手動輸入
    manual_path = Path(__file__).parent.parent / "memory" / "manual_inputs.json"
    if manual_path.exists():
        try:
            manual = json.loads(manual_path.read_text(encoding="utf-8"))
            if key in manual.get("inputs", {}):
                return {
                    "value": manual["inputs"][key]["value"],
                    "source": "manual",
                    "quality": "manual",
                    "timestamp": manual["inputs"][key].get("timestamp", ""),
                }
        except Exception:
            pass

    # 全部失敗
    logger.warning(f"{key}: ALL SOURCES FAILED → MISSING_DATA")
    return {"value": MISSING_DATA, "quality": MISSING_DATA, "source": "none", "timestamp": ""}


# ═══════════════════════════════════════════════════════════════════════════
# Main fetchers per asset
# ═══════════════════════════════════════════════════════════════════════════

def _build_market_fetchers() -> dict[str, list]:
    """建立每個指標的降級鏈。"""
    return {
        "gold": [
            lambda: _fetch_gold("gold"),
        ],
        "spx": [
            lambda: _fetch_yfinance("^GSPC", "spx"),
            lambda: _fetch_alpha_vantage("SPY", "spx"),
        ],
        "twse": [
            lambda: _fetch_twse_yfinance("twse"),
        ],
        "vix": [
            lambda: _fetch_yfinance("^VIX", "vix"),
            lambda: _fetch_finnhub_quote("CBOE:VIX", "vix"),
        ],
        "dxy": [
            lambda: _fetch_yfinance("DX-Y.NYB", "dxy"),
            lambda: _fetch_alpha_vantage("UUP", "dxy"),
        ],
        "brent": [
            lambda: _fetch_yfinance("BZ=F", "brent"),
        ],
        "wti": [
            lambda: _fetch_yfinance("CL=F", "wti"),
        ],
        "usdjpy": [
            lambda: _fetch_yfinance("USDJPY=X", "usdjpy"),
        ],
        "usdtwd": [
            lambda: _fetch_yfinance("TWD=X", "usdtwd"),
        ],
        "nikkei": [
            lambda: _fetch_yfinance("^N225", "nikkei"),
        ],
        "us10y": [
            lambda: _fetch_fred("DGS10", "us10y"),
            lambda: _fetch_yfinance("^TNX", "us10y"),
        ],
        "tips_10y": [
            lambda: _fetch_fred("DFII10", "tips_10y"),
        ],
        "yield_curve_10y2y": [
            lambda: _fetch_fred("T10Y2Y", "yield_curve_10y2y", lookback_days=10),
        ],
        "fed_funds": [
            lambda: _fetch_fred("EFFR", "fed_funds"),
        ],
        "us_cpi": [
            lambda: _fetch_fred("CPIAUCSL", "us_cpi", lookback_days=90),
            lambda: _fetch_us_cpi_yfinance("us_cpi"),
        ],
        "breakeven_5y5y": [
            lambda: _fetch_fred("T5YIFR", "breakeven_5y5y"),
        ],
        "nfci": [
            lambda: _fetch_fred("NFCI", "nfci"),
        ],
        "copper": [
            lambda: _fetch_yfinance("HG=F", "copper"),
        ],
    }


def _normalize_result(result: dict, key: str) -> dict:
    """統一輸出格式：確保有 price/value, change_pct, source, quality, timestamp。"""
    out = {
        "source": result.get("source", "unknown"),
        "quality": result.get("quality", MISSING_DATA),
        "timestamp": result.get("timestamp", ""),
    }
    if "price" in result:
        out["price"] = result["price"]
        out["change_pct"] = result.get("change_pct", 0)
    elif "value" in result:
        out["value"] = result["value"]
    out["asia_prev_day"] = result.get("asia_prev_day", False)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Cold start: pull historical data
# ═══════════════════════════════════════════════════════════════════════════

def bootstrap_history(days: int = 1460) -> pd.DataFrame | None:
    """冷啟動：拉取歷史數據建立 parquet 基線（預設 1460 天 ≈ 4 年）。"""
    from src.config import TIMESERIES_DIR
    market_parquet = TIMESERIES_DIR / "market.parquet"

    if market_parquet.exists():
        existing = pd.read_parquet(market_parquet)
        if len(existing) >= 252:
            logger.info(f"Market parquet exists with {len(existing)} rows, skipping bootstrap")
            return existing
        logger.info(f"Market parquet only has {len(existing)} rows (< 252), re-bootstrapping...")

    logger.info(f"Bootstrapping {days} days of historical data...")
    frames = {}
    for key, ticker in YFINANCE_TICKERS.items():
        series = _fetch_yfinance_history(ticker, days)
        if series is not None:
            frames[key] = series
        time.sleep(0.5)  # Rate limiting

    # FRED series
    if FRED_API_KEY:
        try:
            fred = Fred(api_key=FRED_API_KEY)
            start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            for key, series_id in FRED_SERIES.items():
                try:
                    data = fred.get_series(series_id, observation_start=start)
                    if data is not None and not data.empty:
                        frames[key] = data
                except Exception as e:
                    logger.warning(f"FRED bootstrap {series_id}: {e}")
        except Exception as e:
            logger.warning(f"FRED bootstrap init failed: {e}")

    if not frames:
        logger.error("Bootstrap failed: no data retrieved")
        return None

    # 統一移除時區資訊（tz-naive），避免 pandas join 錯誤
    normalized = {}
    for key, series in frames.items():
        s = pd.to_datetime(series.index).tz_localize(None) if series.index.tzinfo else pd.to_datetime(series.index)
        series.index = s
        normalized[key] = series

    df = pd.DataFrame(normalized)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    # v10.1: 不再使用 ffill（會掩蓋數據缺口），改用有限時間插值
    df = df.interpolate(method="time", limit=3)  # 最多補 3 天，超過留 NaN

    TIMESERIES_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(market_parquet)
    logger.info(f"Bootstrap complete: {len(df)} rows, {len(df.columns)} columns")
    return df


# ═══════════════════════════════════════════════════════════════════════════
# Main entry

def run_data_watcher(run_timestamp: str | None = None) -> dict:
    """主入口：拉取所有數據，回傳 data_package。

    Args:
        run_timestamp: 統一快照時間戳（UTC ISO8601）。由 orchestrator 傳入，
                       所有資產共用同一個 run_timestamp。
    """
    # 確保有歷史數據
    bootstrap_history()

    fetchers = _build_market_fetchers()
    data_package = {}
    health_report = {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "sources": {}}
    quality_scores = {}

    for key, chain in fetchers.items():
        result = get_with_fallback(key, chain)
        data_package[key] = _normalize_result(result, key)
        quality_scores[key] = result.get("quality", MISSING_DATA)
        health_report["sources"][key] = {
            "status": "ok" if result.get("quality") == "confirmed" else result.get("quality", "failed"),
            "source": result.get("source", "none"),
        }

    # 衍生指標
    copper_data = data_package.get("copper", {})
    gold_data = data_package.get("gold", {})
    if copper_data.get("price") and gold_data.get("price") and gold_data["price"] != 0:
        data_package["copper_gold_ratio"] = {
            "value": round(copper_data["price"] / gold_data["price"], 6),
            "source": "computed",
            "quality": quality_for_source("computed"),
            "timestamp": run_timestamp or datetime.now(timezone.utc).isoformat(),
        }
    else:
        data_package["copper_gold_ratio"] = {"value": MISSING_DATA, "quality": MISSING_DATA}

    brent_data = data_package.get("brent", {})
    wti_data = data_package.get("wti", {})
    if brent_data.get("price") and wti_data.get("price"):
        data_package["brent_wti_spread"] = {
            "value": round(brent_data["price"] - wti_data["price"], 2),
            "source": "computed",
            "quality": quality_for_source("computed"),
            "timestamp": run_timestamp or datetime.now(timezone.utc).isoformat(),
        }
    else:
        data_package["brent_wti_spread"] = {"value": MISSING_DATA, "quality": MISSING_DATA}

    # EIA
    eia = _fetch_eia_crude()
    data_package["eia_crude_inventory"] = _normalize_result(eia, "eia_crude_inventory") if eia else {
        "value": MISSING_DATA, "quality": MISSING_DATA
    }

    # ── 亞太 / 另類指標（各自 fallback chain）──────────────────────────────
    # sg_nodx 已移除（v10.1），BDI Stooq fallback 已移除
    asian_fetchers = {
        "tw_foreign_net": [
            lambda: _fetch_tw_foreign_net(),
        ],
        "caixin_pmi": [
            lambda: _fetch_caixin_pmi(),
        ],
        "cot_gold": [
            lambda: _fetch_cot_gold(),
        ],
        "korea_export": [
            lambda: _fetch_korea_export(),
        ],
        "bdi": [
            lambda: _fetch_bdi(),
        ],
        "tw_export": [
            lambda: _fetch_tw_export(),
        ],
        "tw_leading": [
            lambda: _fetch_tw_leading(),
        ],
    }

    for ak_key, chain in asian_fetchers.items():
        result = get_with_fallback(ak_key, chain)
        data_package[ak_key] = _normalize_result(result, ak_key)
        quality_scores[ak_key] = result.get("quality", MISSING_DATA)
        health_report["sources"][ak_key] = {
            "status": "ok" if result.get("quality") == "confirmed" else result.get("quality", "failed"),
            "source": result.get("source", "none"),
        }

    # 標記亞洲前一日指標
    for _asia_key in ("usdtwd", "tw_foreign_net", "nikkei", "twse"):
        if _asia_key in data_package:
            data_package[_asia_key]["asia_prev_day"] = True

    # Sanity limits：超出合理範圍標記 anomaly_flagged
    from src.config import SANITY_LIMITS
    for _key, _limits in SANITY_LIMITS.items():
        _item = data_package.get(_key, {})
        _val = _item.get("price") or _item.get("value")
        if _val is not None and _val != MISSING_DATA:
            try:
                _v = float(_val)
                _lo, _hi = _limits
                if not (_lo <= _v <= _hi):
                    logger.warning(f"SANITY CHECK: {_key}={_v} outside [{_lo}, {_hi}]")
                    quality_scores[_key] = "anomaly_flagged"
                    if _key in data_package and isinstance(data_package[_key], dict):
                        data_package[_key]["quality"] = "anomaly_flagged"
            except (TypeError, ValueError):
                pass

    data_package["quality_scores"] = quality_scores

    # 標記 data_session（tension_note 移至 tension_engine.py，在 QuantEngine 後執行）
    from src.config import ASSET_TIMING
    for _key, _item in data_package.items():
        if not isinstance(_item, dict):
            continue
        _item["data_session"] = ASSET_TIMING.get(_key, "unknown")
        # 注入統一 run_timestamp
        if run_timestamp:
            _item["run_timestamp"] = run_timestamp

    # 覆蓋率（v10.1: confirmed + estimated 都計入，只有 MISSING_DATA/stale 不計）
    total = len(quality_scores)
    usable = sum(1 for v in quality_scores.values() if v in ("confirmed", "estimated", "cached"))
    coverage = round(usable / total, 2) if total > 0 else 0
    health_report["coverage_score"] = coverage

    # 寫入 data_health.json
    health_path = SYSTEM_DIR / "data_health.json"
    health_path.write_text(json.dumps(health_report, indent=2, ensure_ascii=False), encoding="utf-8")

    confirmed_count = sum(1 for v in quality_scores.values() if v == "confirmed")
    logger.info(f"DataWatcher: coverage={coverage}, {confirmed_count}/{total} confirmed")
    return data_package


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_data_watcher()
    # 移除不可序列化的部分
    for k, v in result.items():
        if isinstance(v, dict) and "data" in v and hasattr(v["data"], "to_dict"):
            v["data"] = "DataFrame omitted"
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
