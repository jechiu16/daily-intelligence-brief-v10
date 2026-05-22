from __future__ import annotations
"""DIB v10.1 Configuration — API keys, constants, and shared definitions."""

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


# ── Run Context（每次 pipeline 執行的唯一身份） ──────────────────────────────
@dataclass
class RunContext:
    """每次 pipeline 執行時生成，所有模組共用同一個 instance。"""
    run_timestamp: str = ""      # UTC ISO8601，全局統一
    run_id: str = ""             # {date}_{HHmmss}Z
    pipeline_version: str = "v10.1"

    def init(self):
        """在 pipeline 啟動時呼叫一次。"""
        now = datetime.now(timezone.utc)
        self.run_timestamp = now.isoformat()
        self.run_id = now.strftime("%Y%m%d_%H%M%SZ")
        return self


RUN_CONTEXT = RunContext()

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
MEMORY_DIR = PROJECT_ROOT / "memory"
SNAPSHOTS_DIR = MEMORY_DIR / "daily_snapshots"
TIMESERIES_DIR = MEMORY_DIR / "timeseries"
THESES_DIR = MEMORY_DIR / "theses"
CACHE_DIR = MEMORY_DIR / "cache"
SYSTEM_DIR = MEMORY_DIR / "system"
VECTORS_DIR = MEMORY_DIR / "vectors"
ARCHIVE_DIR = MEMORY_DIR / "archive"
WEEKLY_SNAPSHOTS_DIR = MEMORY_DIR / "weekly_snapshots"

# ── Environment Variables ──────────────────────────────────────────────────
load_dotenv(PROJECT_ROOT / ".env", override=True)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
DEEPSEEK_FAST_MODEL = os.getenv("DEEPSEEK_FAST_MODEL", "deepseek-v4-flash")
DEEPSEEK_THINKING = os.getenv("DEEPSEEK_THINKING", "enabled")
DEEPSEEK_REASONING_EFFORT = os.getenv("DEEPSEEK_REASONING_EFFORT", "max")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
EIA_API_KEY = os.getenv("EIA_API_KEY", "")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "")
LINE_NOTIFY_TOKEN = os.getenv("LINE_NOTIFY_TOKEN", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_TARGET_ID = os.getenv("LINE_TARGET_ID", "")
LINE_ALLOWED_USERS: set[str] = set(os.getenv("LINE_ALLOWED_USERS", "").split(",")) - {""}
LINE_DAILY_LIMIT: int = int(os.getenv("LINE_DAILY_LIMIT", "30"))
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "jechiu16/daily-intelligence-brief-v10")

# ── LLM Models ─────────────────────────────────────────────────────────────
SONNET_MODEL = DEEPSEEK_MODEL
OPUS_MODEL = DEEPSEEK_MODEL
GEMINI_FLASH_MODEL = os.getenv("GEMINI_NARRATOR_MODEL", "gemini-3.5-flash")
GEMINI_NARRATOR_MODEL = GEMINI_FLASH_MODEL
GEMINI_SEARCH_MODEL = os.getenv("GEMINI_SEARCH_MODEL", "gemini-3.1-flash-lite")
GEMINI_ENABLE_DAILY_SEARCH = os.getenv("GEMINI_ENABLE_DAILY_SEARCH", "true").lower() in {"1", "true", "yes", "on"}
GEMINI_ENABLE_PERIPHERY_SEARCH = os.getenv("GEMINI_ENABLE_PERIPHERY_SEARCH", "true").lower() in {"1", "true", "yes", "on"}
GEMINI_THESIS_REVIEW_LIMIT = int(os.getenv("GEMINI_THESIS_REVIEW_LIMIT", "1"))
GEMINI_ACTIVE_THESIS_UPDATE_LIMIT = int(os.getenv("GEMINI_ACTIVE_THESIS_UPDATE_LIMIT", "1"))

# ── Source Tier（數據來源分級） ────────────────────────────────────────────
# Tier A: 官方 API，有 SLA 或穩定公開端點
# Tier B: 商業免費，無 SLA 但長期穩定
# Tier C: 社群/不穩定，品質一律標記 estimated
SOURCE_TIER: dict[str, str] = {
    # Tier A — official
    "FRED": "A", "EIA": "A", "BLS": "A", "TWSE": "A",
    # Tier B — commercial free
    "yfinance": "B", "COMEX_GC": "B", "finnhub": "B", "alpha_vantage": "B",
    "yfinance_TWII": "B", "CFTC": "B",
    # Tier C — community / unstable
    "akshare": "C", "Caixin/akshare": "C", "CFTC/akshare": "C",
    "Baltic/akshare": "C", "GDELT": "C",
    "SingStat": "C", "MOF_Taiwan": "C", "NDC_Taiwan": "C",
    "FRED_IMF": "B", "FRED_EXP5590_proxy": "C",  # proxy 降級
    "yfinance_TWII_proxy": "C",                    # proxy 降級
    # 衍生 / 計算
    "computed": "B", "cache": "C", "manual": "C",
}


def quality_for_source(
    source: str,
    data_is_today: bool = True,
    observation_date: str | None = None,
    today_str: str | None = None,
) -> str:
    """根據 SOURCE_TIER 和數據時效決定品質標記。

    若提供 observation_date 和 today_str，以實際落差天數覆蓋 data_is_today：
      lag == 0 → confirmed（Tier A/B）
      lag 1-7  → cached
      lag > 7  → stale
    """
    tier = SOURCE_TIER.get(source, "C")
    if tier == "C":
        return "estimated"

    # 若有明確觀測日期，用落差天數決定品質
    # M8 修正：不同來源日期格式不一（YYYY-MM-DD / YYYY-MM / YYYYMMDD），統一正規化後再比較
    if observation_date and today_str:
        try:
            from datetime import date as _date
            import re as _re
            def _parse_date(s: str) -> _date:
                s = s.strip()
                # YYYY-MM-DD（標準格式，最常見）
                if _re.match(r'^\d{4}-\d{2}-\d{2}$', s):
                    return _date.fromisoformat(s)
                # YYYYMMDD（無分隔符）
                if _re.match(r'^\d{8}$', s):
                    return _date(int(s[:4]), int(s[4:6]), int(s[6:8]))
                # YYYY-MM（月精度，取月首日）
                if _re.match(r'^\d{4}-\d{2}$', s):
                    return _date(int(s[:4]), int(s[5:7]), 1)
                # MM/DD/YYYY（美式格式，BLS 偶爾使用）
                m = _re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', s)
                if m:
                    return _date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
                raise ValueError(f"unrecognized date format: {s!r}")
            lag = (_parse_date(today_str) - _parse_date(observation_date)).days
            if lag > 7:
                return "stale"
            if lag > 0:
                return "cached"
            return "confirmed"
        except (ValueError, TypeError):
            pass  # 日期解析失敗，退回原邏輯

    if tier in ("A", "B") and data_is_today:
        return "confirmed"
    return "confirmed" if data_is_today else "cached"


# ── Assets ─────────────────────────────────────────────────────────────────
# sg_nodx 已移除：SingStat 端點不穩 + FRED proxy 太間接（weight 僅 0.3）
TRACKED_ASSETS = [
    "gold", "spx", "vix", "dxy", "brent", "wti",
    "usdjpy", "usdtwd", "us10y", "tips_10y",
    "fed_funds", "us_cpi",
    "tw_foreign_net", "tw_export", "tw_leading",
    "caixin_pmi", "korea_export",
    "eia_crude_inventory", "cot_gold",
    "copper_gold_ratio", "brent_wti_spread",
    "breakeven_5y5y", "nfci", "bdi",
]

YFINANCE_TICKERS = {
    "gold": "GC=F",        # COMEX 黃金期貨（FRED LBMA 系列已下架）
    "spx": "^GSPC",
    "vix": "^VIX",
    "dxy": "DX-Y.NYB",
    "brent": "BZ=F",
    "wti": "CL=F",
    "usdjpy": "USDJPY=X",
    "usdtwd": "TWD=X",
    "us10y_alt": "^TNX",
    "copper": "HG=F",
}

FRED_SERIES = {
    # gold 已移除：FRED LBMA 系列下架，改用 yFinance GC=F
    "us10y": "DGS10",
    "tips_10y": "DFII10",
    "fed_funds": "EFFR",
    "us_cpi": "CPIAUCSL",
    "breakeven_5y5y": "T5YIFR",
    "nfci": "NFCI",
}

# ── Assembler 動態 Token 分配 ─────────────────────────────────────────────
# 三級優先：Tier 1 不截斷 / Tier 2 超限時適度縮減（30%）/ Tier 3 先犧牲（50%）
PACKAGE_TIERS: dict[str, int] = {
    # Tier 1 — 核心輸入，永不截斷
    "data_package": 1,
    "historian_package": 1,
    "l3_context": 1,          # active theses = 推理核心
    "l4_context": 1,          # 知識歷史 = 系統最大資產
    # Tier 2 — 重要補充，超限時適度縮減
    "quant_package": 2,
    "geopolitical_package": 2,
    "sentiment_package": 2,
    "l2_context": 2,
    "l5_context": 2,
    # Tier 3 — 可壓縮，超限時先犧牲
    "calendar_package": 3,
    "coverage_warning": 3,
    "l1_context": 3,
}

ASSEMBLER_SOFT_TARGET = 40_000   # 低於此值：零截斷（正常日）
ASSEMBLER_HARD_CEILING = 60_000  # 高於此值：tier-based 截斷啟動

# ── Required Fields (Assembler 驗證) ──────────────────────────────────────
REQUIRED_FIELDS = {
    "data_package": [
        "gold", "spx", "vix", "dxy", "brent", "wti",
        "usdjpy", "usdtwd", "us10y", "tips_10y",
        "tw_foreign_net", "quality_scores",
    ],
    "quant_package": [
        "correlation_matrix_30d", "zscore_alerts",
        "rolling_vol_30d", "regime_probability",
    ],
    "historian_package": [
        "similar_periods", "base_rates", "analog_ids",
    ],
    "sentiment_package": [
        "signals", "aggregate", "scan_time",
    ],
    "calendar_package": [
        "today_events", "next_7_days", "days_to_next_major",
    ],
    "geopolitical_package": [
        "tgri", "active_risks", "scholar_analysis",
    ],
}

# ── Data Quality ──────────────────────────────────────────────────────────
DATA_QUALITY = {
    "confirmed": "Tier A/B 來源，當日 API 直接取得",
    "cached": "本地快取（< 24h），附時間戳記",
    "estimated": "Tier C 來源、proxy 計算、或 Gemini Search",
    "stale": "過期快取（> 24h），API 失敗時使用",
    "manual": "手動輸入（LINE / manual_inputs.json）",
    "MISSING_DATA": "所有來源失敗",
    "anomaly_flagged": "數值超出 SANITY_LIMITS，已標記",
    "deviation": "乖離值：vs 均線或歷史常態的偏差",
}

# 五色系統：藍（可靠）/ 紫（推算）/ 灰（不可靠）/ 黃（異常值）/ 粉紅（乖離值）
# 刻意避開紅/綠，防止與漲跌顏色混淆
DATA_QUALITY_COLOR = {
    "confirmed": "blue",         # 可靠：API 直接取得
    "cached": "blue",            # 可靠：< 24h 本地快取
    "estimated": "purple",       # 推算：Gemini Search / proxy
    "stale": "gray",             # 不可靠：過期快取
    "manual": "purple",          # 推算：手動輸入
    "MISSING_DATA": "gray",      # 不可靠：完全缺失
    "anomaly_flagged": "yellow", # 異常值：超出合理範圍的實測值
    "deviation": "pink",         # 乖離值：均線偏差 / z-score / 跨資產乖離
}

# ── Data Sanity Limits（異常值邊界） ─────────────────────────────────────
SANITY_LIMITS = {
    "gold": (1500, 8000),
    "spx": (2000, 10000),
    "vix": (5, 100),
    "dxy": (70, 130),
    "brent": (20, 200),
    "wti": (15, 200),
    "us10y": (0.0, 10.0),
    "tips_10y": (-2.0, 8.0),
    "usdtwd": (25, 45),
    "usdjpy": (80, 200),
    "fed_funds": (0.0, 10.0),
    "us_cpi": (200.0, 500.0),   # CPI index level (CPIAUCSL)，非 YoY%
    "bdi": (100, 10000),
    "copper": (2.0, 12.0),      # HG futures $/lb
    "nikkei": (15000, 80000),
    "korea_export": (-30, 60),   # YoY%
    "tw_export": (-30, 80),      # YoY%
    "caixin_pmi": (35, 60),
    "cot_gold": (-200000, 500000),  # CFTC net contracts
    "nfci": (-2.0, 5.0),        # 金融壓力指數
    "breakeven_5y5y": (0.0, 6.0),
}

# ── Quality Multiplier（品質乘數，搭配 COVERAGE_WEIGHTS 計算覆蓋率）────────
QUALITY_MULTIPLIER: dict[str, float] = {
    "confirmed": 1.0,       # Tier A/B 當日 API 直取
    "cached": 0.85,         # 有效快取 <24h，可靠
    "estimated": 0.50,      # Tier C proxy，可用但不確定
    "stale": 0.20,          # 過期 >24h，邊緣可用
    "manual": 0.60,         # 手動輸入，合理但非即時
    "MISSING_DATA": 0.0,    # 完全缺失
    "anomaly_flagged": 0.70,  # 超出範圍但有實測值
    "deviation": 0.90,      # 乖離值，數據本身可靠
}

# ── Coverage Weights（加權覆蓋率） ─────────────────────────────────────────
COVERAGE_WEIGHTS = {
    "gold": 1.0, "spx": 1.0, "vix": 1.0, "dxy": 0.9,
    "brent": 0.8, "wti": 0.7, "usdjpy": 0.8, "usdtwd": 0.7,
    "us10y": 1.0, "tips_10y": 0.9, "fed_funds": 0.8, "us_cpi": 0.8,
    "tw_foreign_net": 0.6, "tw_export": 0.5, "tw_leading": 0.5,
    "caixin_pmi": 0.5, "korea_export": 0.4,
    "eia_crude_inventory": 0.6, "cot_gold": 0.4,
    "copper_gold_ratio": 0.7, "brent_wti_spread": 0.5,
    "breakeven_5y5y": 0.8, "nfci": 0.7, "bdi": 0.4,
}

# ── Asset Timing（資料基準時間標記） ──────────────────────────────────────────
# us_close:      美股收盤 (ET 16:00)
# fx_ny_close:   外匯紐約收盤 (ET 17:00)
# settlement:    期貨結算價 (CME/ICE)
# asia_prev_close: 亞洲前日收盤（台/日市場）
# release_date:  月度/季度統計發布日
# periodic:      週/日週期更新
# cot_weekly:    CFTC COT 週報（週二持倉/週五發布）
ASSET_TIMING = {
    "spx": "us_close", "vix": "us_close",
    "us10y": "us_close", "tips_10y": "us_close",
    "fed_funds": "us_close", "breakeven_5y5y": "us_close",
    "nfci": "periodic",
    "dxy": "fx_ny_close", "usdjpy": "fx_ny_close", "usdtwd": "fx_ny_close",
    "gold": "settlement", "brent": "settlement", "wti": "settlement",
    "copper_gold_ratio": "settlement", "brent_wti_spread": "settlement",
    "twse": "asia_prev_close", "nikkei": "asia_prev_close",
    "tw_foreign_net": "asia_prev_close",
    "us_cpi": "release_date", "caixin_pmi": "release_date",
    "tw_export": "release_date", "tw_leading": "release_date",
    "korea_export": "release_date",
    "eia_crude_inventory": "release_date",
    "cot_gold": "cot_weekly", "bdi": "periodic",
}

# ── Trusted Sources (SentimentWatcher) ────────────────────────────────────
TRUSTED_SOURCES = {
    "tier_1": [
        "federalreserve.gov", "ecb.europa.eu", "boj.or.jp",
        "imf.org", "bis.org", "mof.gov.tw", "cbc.gov.tw",
        "twse.com.tw", "ndc.gov.tw",
    ],
    "tier_2": [
        "reuters.com", "bloomberg.com", "ft.com", "wsj.com",
        "nikkei.com", "scmp.com", "cna.com.tw", "taipeitimes.com",
    ],
    "tier_3": [
        "brookings.edu", "piie.com", "voxeu.org",
    ],
    "excluded": [
        "zerohedge.com", "reddit.com", "twitter.com", "seekingalpha.com",
    ],
}

# ── Risk Officer Allowed Tools ─────────────────────────────────────────────
OPUS_ALLOWED_TOOLS = [
    "query_computed_data",
    "query_memory_layer",
    "query_historian",
    "flag_data_gap",
]
OPUS_FORBIDDEN_TOOLS = [
    "web_search", "fetch_url", "call_api",
    "write_memory", "modify_data",
]

# ── Regime Types ──────────────────────────────────────────────────────────
REGIME_TYPES = [
    "政策過渡",      # POLICY_TRANSITION
    "風險偏好增長",  # RISK_ON_GROWTH
    "滯脹",          # STAGFLATION
    "通縮風險",      # DEFLATION_RISK
]

# ── Terminology Map (Narrator 強制) ───────────────────────────────────────
TERMINOLOGY_MAP = {
    "POLICY_TRANSITION": "政策過渡",
    "RISK_ON_GROWTH": "風險偏好增長",
    "STAGFLATION": "滯脹",
    "DEFLATION_RISK": "通縮風險",
    "OVERRULED": "駁回",
    "SUSTAINED": "成立",
    "NOTED": "存記",
    "confirmed": "已確認",
    "MISSING_DATA": "數據缺口",
    "Devil's Advocate": "反方論證",
}

# ── Git Sync Paths ────────────────────────────────────────────────────────
SYNC_PATHS = [
    "memory/daily_snapshots/",
    "memory/timeseries/regime_history.json",
    "memory/timeseries/scorecard_history.json",
    "memory/timeseries/inference_history.jsonl",
    "memory/theses/",
    "memory/system/calibration.json",
    "memory/l2.json",
    "memory/l3.json",
    "memory/l4.json",
    "memory/l5.json",
    "memory/weekly_snapshots/",
]

# ── TGRI 常數（M4 修正：移出 tgri.py，集中管理，方便定期更新）──────────
# TWII 總市值近似值（美元）。每季更新一次：台灣證交所統計 → 折算當時匯率。
# 2026-Q1 估算：約 ~60 兆台幣 ≈ 1.85 兆美元（匯率 32.5）
TWII_TOTAL_MARKET_CAP_USD: float = 1.85e12

# ── Sentinel ──────────────────────────────────────────────────────────────
MISSING_DATA = "MISSING_DATA"
