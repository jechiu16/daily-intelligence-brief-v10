from __future__ import annotations
"""TGRI — 台灣地緣風險指數計算。

v10.1: 移除 GDELT 依賴（不穩定 + 間接），改為可控來源：
- adiz_intrusions: manual_inputs.json（日本 MOD press release 為參考）
- pla_activity_level: manual_inputs.json（獨立於 ADIZ）
- us_tw_contact_frequency: manual_inputs.json（每週更新）
- ndf_premium: yfinance spot-based approximation（品質 = estimated）
- twse_foreign_net_30d: 從 data_package 取得
- trade_policy_risk: 新增，基於手動輸入的關稅/貿易政策動態
- semiconductor_concentration: 新增，TSM 市值佔 TWII 比重
- capital_flow_pressure: 從外資淨流出計算

生成 GEO_xxx 推論條目，納入 inference_history。
"""

import json
import logging
from datetime import datetime, timezone

from src.config import (
    MEMORY_DIR,
    MISSING_DATA,
    TIMESERIES_DIR,
)

logger = logging.getLogger(__name__)

# ── 張力分級（四級） ──────────────────────────────────────────────────────
TENSION_LEVELS = [
    (20,  "低張力", "⚪"),
    (40,  "平穩",   "🟢"),
    (60,  "升溫",   "🟡"),
    (100, "高張力", "🔴"),
]


def _tension_label(score: float) -> dict:
    """回傳 {"level": "平穩", "emoji": "🟢"}"""
    for threshold, label, emoji in TENSION_LEVELS:
        if score <= threshold:
            return {"level": label, "emoji": emoji}
    return {"level": "高張力", "emoji": "🔴"}


def _direction_arrow(trend: str) -> str:
    """trend → 方向箭頭"""
    return {"rising": "↑", "falling": "↓"}.get(trend, "→")


# TGRI 組件加權（v10.1 重新分配）
TGRI_WEIGHTS = {
    "adiz_intrusions": 0.20,
    "pla_activity_level": 0.15,
    "ndf_premium": 0.15,
    "twse_foreign_net_30d": 0.15,
    "trade_policy_risk": 0.15,
    "semiconductor_concentration": 0.10,
    "capital_flow_pressure": 0.10,
}


def _load_manual_inputs() -> dict:
    """讀取 manual_inputs.json。"""
    path = MEMORY_DIR / "manual_inputs.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _get_adiz_intrusions(manual: dict) -> float:
    """從 manual_inputs.json 取得 ADIZ 侵入活動分數 (0-10)。

    使用者應參考日本統合幕僚監部 press release 或國防部公告更新。
    未提供時使用基線值 2.0。
    """
    val = manual.get("adiz_intrusions")
    if val is not None:
        try:
            return min(float(val), 10.0)
        except (TypeError, ValueError):
            pass
    return 2.0  # 基線值


def _get_pla_activity_level(manual: dict) -> float:
    """從 manual_inputs.json 取得 PLA 活動等級 (0-3)。

    獨立於 ADIZ（可能是海軍演習、導彈試射等）。
    """
    val = manual.get("pla_activity_level")
    if val is not None:
        try:
            return min(float(val), 3.0)
        except (TypeError, ValueError):
            pass
    return 1.0  # 基線值


def _get_us_tw_contacts(manual: dict) -> float:
    """從 manual_inputs.json 取得美台接觸頻率分數 (0-10)。

    每週更新，基於公開可查的官方接觸事件。
    """
    val = manual.get("us_tw_contact_frequency")
    if val is not None:
        try:
            return min(float(val), 10.0)
        except (TypeError, ValueError):
            pass
    return 3.0  # 基線值


def _calculate_ndf_premium(usdtwd_spot: float | None) -> float:
    """計算 USDTWD NDF 溢價（近似值）。

    品質 = estimated：這是 spot-based approximation，不是真正的 NDF 市場數據。
    """
    if usdtwd_spot is None:
        return 0.0
    try:
        import yfinance as yf
        ticker = yf.Ticker("TWD=X")
        hist_90 = ticker.history(period="90d")
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
    """取 TWSE 外資淨額（從 data_package）。"""
    fw = data_package.get("tw_foreign_net", {})
    val = fw.get("value") if isinstance(fw, dict) else None
    if val and val != MISSING_DATA:
        try:
            return float(val)
        except (TypeError, ValueError):
            pass
    return 0.0


def _get_trade_policy_risk(manual: dict) -> float:
    """從 manual_inputs.json 取得貿易政策風險分數 (0-10)。

    基於最新關稅動態、US-CN 貿易政策。
    """
    val = manual.get("trade_policy_risk")
    if val is not None:
        try:
            return min(float(val), 10.0)
        except (TypeError, ValueError):
            pass
    return 3.0  # 基線值


def _calculate_semiconductor_concentration() -> float:
    """計算台積電市值集中度（佔台股比重）。

    值越高 → 台灣對半導體的依賴度越高 → 地緣風險越集中。
    正規化到 0-10 分。
    """
    try:
        import yfinance as yf
        tsm = yf.Ticker("TSM")
        tsm_info = tsm.info
        tsm_cap = tsm_info.get("marketCap", 0)
        if tsm_cap == 0:
            return 5.0  # 基線值

        # 用 TSM ADR 市值佔 TWII 總市值的近似比重
        # TWII 總市值約 ~55-65 兆台幣 ≈ ~1.7-2.0 兆美元
        # TSM 市值若 ~800B USD，佔比約 40-50%
        twii_total_approx = 1.8e12  # 美元，近似值
        ratio = tsm_cap / twii_total_approx
        # 40% = 基線（5分），每多10% 加2分
        score = 5.0 + (ratio - 0.4) * 20
        return round(min(max(score, 0), 10.0), 1)
    except Exception as e:
        logger.debug(f"Semiconductor concentration calc failed: {e}")
        return 5.0  # 基線值


def _weighted_composite(components: dict) -> float:
    """加權合成 TGRI 分數（0-100）。"""
    score = 0.0
    for key, weight in TGRI_WEIGHTS.items():
        val = components.get(key, 0)
        if val is None:
            val = 0
        # 所有組件已正規化到 0-10，乘以 10 轉為 0-100 貢獻
        normalized = min(float(val) * 10, 100.0)
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


def _compute_5d_slope(tgri_parquet_path) -> float | None:
    """計算 TGRI 5 日斜率，供操作層分析使用。"""
    try:
        import pandas as pd
        if not tgri_parquet_path.exists():
            return None
        df = pd.read_parquet(tgri_parquet_path)
        if len(df) < 5:
            return None
        recent = df["score"].tail(5).dropna()
        if len(recent) < 3:
            return None
        return round(float(recent.iloc[-1] - recent.iloc[0]), 1)
    except Exception:
        return None


def _generate_geo_inferences(
    score: float,
    trend: str,
    components: dict,
    dominant: str,
    date: str,
) -> list[dict]:
    """生成 GEO_xxx 推論條目，與 INF_xxx 同格式。"""
    inferences = []

    # GEO_001: TGRI 整體水準判讀
    if score > 60:
        claim = f"TGRI 處於高風險區（{score:.1f}），台海緊張度顯著高於基線"
        confidence = 0.7
    elif score > 40:
        claim = f"TGRI 處於中度風險（{score:.1f}），地緣壓力存在但可控"
        confidence = 0.5
    else:
        claim = f"TGRI 處於低風險區（{score:.1f}），地緣環境相對平穩"
        confidence = 0.6

    inferences.append({
        "id": "GEO_001",
        "claim": claim,
        "evidence": [{"data_key": "tgri", "value": score}],
        "raw_confidence": confidence,
        "adjusted_confidence": confidence,
        "asset_predictions": [],
    })

    # GEO_002: 趨勢判讀
    if trend == "rising":
        inferences.append({
            "id": "GEO_002",
            "claim": "TGRI 趨勢上升，地緣壓力在累積",
            "evidence": [{"data_key": "tgri_trend", "value": trend}],
            "raw_confidence": 0.6,
            "adjusted_confidence": 0.6,
            "asset_predictions": ["usdtwd_up"],
        })
    elif trend == "falling":
        inferences.append({
            "id": "GEO_002",
            "claim": "TGRI 趨勢下降，地緣壓力在緩解",
            "evidence": [{"data_key": "tgri_trend", "value": trend}],
            "raw_confidence": 0.55,
            "adjusted_confidence": 0.55,
            "asset_predictions": [],
        })

    # GEO_003: 主導信號變化
    inferences.append({
        "id": "GEO_003",
        "claim": f"當前 TGRI 主導信號為 {dominant}",
        "evidence": [{"data_key": dominant, "value": components.get(dominant, 0)}],
        "raw_confidence": 0.5,
        "adjusted_confidence": 0.5,
        "asset_predictions": [],
    })

    return inferences


def calculate_tgri(data_package: dict) -> dict:
    """計算 TGRI。

    v10.1: 不再呼叫 GDELT API。所有組件來自：
    - manual_inputs.json（人工更新）
    - data_package（pipeline 數據）
    - yfinance（NDF premium, semiconductor concentration）
    """
    manual = _load_manual_inputs()

    # 收集各組件
    adiz = _get_adiz_intrusions(manual)
    pla_level = _get_pla_activity_level(manual)

    usdtwd = data_package.get("usdtwd", {})
    usdtwd_val = usdtwd.get("price") if isinstance(usdtwd, dict) else None
    ndf_premium = _calculate_ndf_premium(usdtwd_val)

    twse_net = _get_twse_foreign_net_30d(data_package)
    # 外資淨賣超越大，分數越高（0-10）
    foreign_net_score = min(abs(twse_net) / 200, 10.0) if twse_net < 0 else 0

    trade_risk = _get_trade_policy_risk(manual)
    semi_concentration = _calculate_semiconductor_concentration()
    capital_flow = min(max(0, -twse_net / 200), 10.0) if twse_net < 0 else 0

    components = {
        "adiz_intrusions": adiz,
        "pla_activity_level": pla_level,
        "ndf_premium": min(ndf_premium, 10.0),
        "twse_foreign_net_30d": foreign_net_score,
        "trade_policy_risk": trade_risk,
        "semiconductor_concentration": semi_concentration,
        "capital_flow_pressure": capital_flow,
    }

    score = _weighted_composite(components)

    tgri_parquet = TIMESERIES_DIR / "tgri.parquet"
    trend = _compute_trend(tgri_parquet)
    percentile = _compute_historical_percentile(score)
    slope_5d = _compute_5d_slope(tgri_parquet)

    # 找主要信號
    weighted_vals = {k: components[k] * TGRI_WEIGHTS.get(k, 0) for k in components}
    dominant = max(weighted_vals, key=weighted_vals.get)

    # 寫入當日分數到 tgri.parquet
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        import pandas as pd
        new_row = pd.DataFrame([{"date": today, "score": score, **components}])
        if tgri_parquet.exists():
            existing = pd.read_parquet(tgri_parquet)
            existing = existing[existing["date"] != today]
            df_out = pd.concat([existing, new_row], ignore_index=True)
        else:
            df_out = new_row
        TIMESERIES_DIR.mkdir(parents=True, exist_ok=True)
        df_out.to_parquet(tgri_parquet, index=False)
        logger.info(f"TGRI: appended score={score:.1f} to tgri.parquet ({len(df_out)} rows)")
    except Exception as e:
        logger.warning(f"TGRI: failed to write tgri.parquet: {e}")

    # 生成地緣推論
    geo_inferences = _generate_geo_inferences(
        score, trend, components, dominant, today
    )

    # 張力分級 + 方向箭頭
    tension = _tension_label(score)
    direction = _direction_arrow(trend)
    tension_display = f"{tension['emoji']} {tension['level']} {direction}"

    result = {
        "score": score,
        "trend": trend,
        "slope_5d": slope_5d,
        "dominant_signal": dominant,
        "historical_percentile": percentile,
        "tension": tension,
        "direction": direction,
        "tension_display": tension_display,
        "components": components,
        "geo_inferences": geo_inferences,
    }

    logger.info(f"TGRI: {tension_display}（score={score:.1f}, percentile={percentile}%ile）")
    return result
