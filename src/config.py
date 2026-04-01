"""DIB v10 Configuration — API keys, constants, and shared definitions."""

import os
from pathlib import Path
from dotenv import load_dotenv

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
MEMORY_DIR = PROJECT_ROOT / "memory"
SNAPSHOTS_DIR = MEMORY_DIR / "daily_snapshots"
TIMESERIES_DIR = MEMORY_DIR / "timeseries"
THESES_DIR = MEMORY_DIR / "theses"
CACHE_DIR = MEMORY_DIR / "cache"
SYSTEM_DIR = MEMORY_DIR / "system"
VECTORS_DIR = MEMORY_DIR / "vectors"

# ── Environment Variables ──────────────────────────────────────────────────
load_dotenv(PROJECT_ROOT / ".env", override=True)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
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

# ── LLM Models ─────────────────────────────────────────────────────────────
SONNET_MODEL = "claude-sonnet-4-6"
OPUS_MODEL = "claude-opus-4-6"
GEMINI_FLASH_MODEL = "gemini-3-flash-preview"
GEMINI_PRO_MODEL = "gemini-3.1-pro-preview"

# ── Assets ─────────────────────────────────────────────────────────────────
TRACKED_ASSETS = [
    "gold", "spx", "vix", "dxy", "brent", "wti",
    "usdjpy", "usdtwd", "us10y", "tips_10y",
    "fed_funds", "us_cpi",
    "tw_foreign_net", "tw_export", "tw_leading",
    "caixin_pmi", "korea_export",
    "eia_crude_inventory", "cot_gold",
    "copper_gold_ratio", "brent_wti_spread",
    "breakeven_5y5y", "nfci", "bdi",
    "sg_nodx",
]

YFINANCE_TICKERS = {
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
    "gold": "GOLDAMGBD228NLBM",   # LBMA AM Fix (London 10:30 = 05:30 ET), daily
    "us10y": "DGS10",
    "tips_10y": "DFII10",
    "fed_funds": "EFFR",
    "us_cpi": "CPIAUCSL",
    "breakeven_5y5y": "T5YIFR",
    "nfci": "NFCI",
}

# ── Token Budgets (Sonnet 第一次分析輸入) ──────────────────────────────────
TOKEN_BUDGETS = {
    "coverage_warning": 200,
    "calendar_package": 800,
    "data_package": 4000,        # +1000: 市場數據是核心，給足空間
    "quant_package": 2500,       # +500: 統計指標需要更多細節
    "historian_package": 6000,   # +2000: 歷史類比是最重要的缺口
    "sentiment_package": 2000,   # +500: 容納 10 個 signals
    "geopolitical_package": 2500, # +500: TGRI + active_risks 需要更多空間
    "l1_context": 600,           # +100
    "l2_context": 2500,          # +500: 7天市場結構
    "l3_context": 4000,          # +1000: active theses 是推理核心
    "l4_context": 1000,          # 不變
    "l5_context": 1500,          # 不變（scorecard 結構固定）
}
TOKEN_BUDGET_TOTAL = 28_100  # +6600: 品質優先，歷史類比不再被截斷

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
    "confirmed": "當日 API 直接取得",
    "cached": "本地快取，附時間戳記",
    "estimated": "Gemini Search 來源",
    "stale": "過期快取，API 失敗時使用",
    "manual": "LINE 手動輸入",
    "MISSING_DATA": "所有來源失敗",
}

DATA_QUALITY_COLOR = {
    "confirmed": "green",
    "cached": "yellow",
    "estimated": "blue",
    "stale": "gray",
    "MISSING_DATA": "red",
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

# ── Opus Allowed Tools ────────────────────────────────────────────────────
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
    "memory/theses/",
    "memory/system/calibration.json",
    "memory/l2.json",
    "memory/l3.json",
    "memory/l4.json",
    "memory/l5.json",
]

# ── Sentinel ──────────────────────────────────────────────────────────────
MISSING_DATA = "MISSING_DATA"
