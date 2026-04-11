"""Unit tests for src/quant_engine.py — pure logic, no file I/O."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _make_df(assets: dict[str, list[float]], dates: list[str] | None = None) -> pd.DataFrame:
    """Helper: build a DataFrame with date index."""
    if dates is None:
        dates = pd.date_range("2026-01-01", periods=len(next(iter(assets.values()))))
    return pd.DataFrame(assets, index=pd.DatetimeIndex(dates))


# ── compute_zscore_alerts ─────────────────────────────────────────────────

def test_zscore_price_uses_pct_change():
    """Non-rate assets: extreme pct_change → high z-score."""
    from src.quant_engine import compute_zscore_alerts
    n = 35
    # 30 days of ~0% change, then a 10% spike on the last day
    gold = [100.0] * (n - 1) + [110.0]
    df = _make_df({"gold": gold})
    alerts = compute_zscore_alerts(df, window=30)
    assert "gold" in alerts
    assert alerts["gold"] > 2.0, "10% spike should produce large z-score"


def test_zscore_rate_uses_diff():
    """H4 修正驗證：利率資產用絕對差，不用 pct_change。

    測試原理：同樣的 bps 波動幅度，在不同利率水準下，
    用 diff() 得到相同 z-score；用 pct_change() 則差異極大。

    Series A (高利率水準 ~4.0%)：daily diff 標準差 ≈ 0.05
    Series B (低利率水準 ~0.2%)：同樣 0.05 的 daily diff 標準差
    末日加入相同的 0.5 絕對變動 → 應得到相近的 z-score
    """
    import numpy as np
    from src.quant_engine import compute_zscore_alerts

    rng = np.random.default_rng(42)
    n = 35
    noise = rng.normal(0, 0.05, n)  # ±5bps 日常波動

    # Series A：高利率 ~4.0%，末日 +0.5（10 個標準差）
    high_base = 4.0 + np.cumsum(noise)
    high_base[-1] = high_base[-2] + 0.5  # 固定最後一天的變動

    # Series B：低利率 ~0.2%，同樣的 noise pattern，末日同樣 +0.5
    low_base = 0.2 + np.cumsum(noise)
    low_base[-1] = low_base[-2] + 0.5

    df_high = _make_df({"us10y": list(high_base)})
    df_low = _make_df({"us10y": list(low_base)})

    alerts_high = compute_zscore_alerts(df_high, window=30)
    alerts_low = compute_zscore_alerts(df_low, window=30)

    # 兩者都應偵測到異常
    assert "us10y" in alerts_high
    assert "us10y" in alerts_low

    # diff() 模式：同樣 0.5 的絕對移動 → z-score 應相近（允許 5 個單位差距）
    z_high = abs(alerts_high["us10y"])
    z_low = abs(alerts_low["us10y"])
    assert abs(z_high - z_low) < 5, (
        f"diff-based zscore should be similar across rate levels: "
        f"high={z_high:.2f}, low={z_low:.2f}"
    )

    # 若用 pct_change，低利率 0.5/0.2=250% vs 高利率 0.5/4.0=12.5%，差距會超過 5


def test_zscore_returns_empty_on_short_series():
    """足夠資料才計算。"""
    from src.quant_engine import compute_zscore_alerts
    df = _make_df({"gold": [100.0] * 10})
    assert compute_zscore_alerts(df, window=30) == {}


# ── compute_rolling_correlation ───────────────────────────────────────────

def test_correlation_perfect_positive():
    from src.quant_engine import compute_rolling_correlation
    n = 35
    vals = list(range(n))
    df = _make_df({"gold": vals, "spx": vals})
    corr = compute_rolling_correlation(df, window=30)
    assert "gold_spx" in corr
    assert abs(corr["gold_spx"] - 1.0) < 0.01


def test_correlation_empty_on_single_asset():
    from src.quant_engine import compute_rolling_correlation
    df = _make_df({"gold": list(range(35))})
    assert compute_rolling_correlation(df, window=30) == {}


# ── classify_vix_regime ───────────────────────────────────────────────────

def test_vix_regime_low():
    from src.quant_engine import classify_vix_regime
    r = classify_vix_regime(12.0)
    assert r["regime"] == "low"


def test_vix_regime_stress():
    from src.quant_engine import classify_vix_regime
    r = classify_vix_regime(35.0)
    assert r["regime"] == "stress"
    assert r["level"] == 35.0


def test_vix_regime_elevated():
    from src.quant_engine import classify_vix_regime
    r = classify_vix_regime(25.0)
    assert r["regime"] == "elevated"


# ── compute_rolling_volatility ────────────────────────────────────────────

def test_volatility_constant_series():
    """Constant price series → zero volatility."""
    from src.quant_engine import compute_rolling_volatility
    df = _make_df({"gold": [100.0] * 35})
    vol = compute_rolling_volatility(df, window=30)
    # pct_change of constant series = 0 → std = 0
    assert "gold" in vol
    assert vol["gold"] == 0.0 or vol["gold"] is None
