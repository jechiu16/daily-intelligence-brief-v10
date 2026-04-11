#!/usr/bin/env python3
"""
seed_historian.py — 一次性歷史資料種子腳本

功能：
1. 把 market.parquet 擴展至 15 年（2011–今）
2. 從 parquet 生成雙週合成快照，存至 memory/archive/
3. 為所有 archive 快照建立向量索引
4. 冷啟動：清除所有即時狀態（保留 parquet 與 archive）

用法：
    cd /path/to/daily-intelligence-brief-v10
    python3 scripts/seed_historian.py [--dry-run] [--skip-parquet] [--skip-embed] [--cold-start]

選項：
    --dry-run       只印出計劃，不執行任何寫入
    --skip-parquet  跳過 parquet 擴展（若已有 15 年資料）
    --skip-embed    跳過 embedding 建立（用於快速重跑）
    --cold-start    執行冷啟動（清除即時狀態），不加則只做 seed
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# ── 路徑設定 ─────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

MEMORY_DIR       = PROJECT_ROOT / "memory"
SNAPSHOTS_DIR    = MEMORY_DIR / "daily_snapshots"
ARCHIVE_DIR      = MEMORY_DIR / "archive"
TIMESERIES_DIR   = MEMORY_DIR / "timeseries"
VECTORS_DIR      = MEMORY_DIR / "vectors"
SYSTEM_DIR       = MEMORY_DIR / "system"

MARKET_PARQUET       = TIMESERIES_DIR / "market.parquet"
ARCHIVE_INDEX_FILE   = VECTORS_DIR / "archive_index.json"
ARCHIVE_EMBEDDINGS   = VECTORS_DIR / "archive_embeddings.npy"

# 15 年目標起始日
TARGET_START = "2011-01-01"
TARGET_DAYS  = 15 * 365  # 5475

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_historian")


# ═══════════════════════════════════════════════════════════════════════════
# 1. 擴展 market.parquet
# ═══════════════════════════════════════════════════════════════════════════

YFINANCE_TICKERS = {
    "gold":     "GC=F",
    "spx":      "^GSPC",
    "vix":      "^VIX",
    "dxy":      "DX-Y.NYB",
    "brent":    "BZ=F",
    "wti":      "CL=F",
    "usdjpy":   "USDJPY=X",
    "usdtwd":   "TWD=X",
    "us10y_alt":"^TNX",
    "copper":   "HG=F",
}

FRED_SERIES = {
    "us10y":          "DGS10",
    "tips_10y":       "DFII10",
    "fed_funds":      "EFFR",
    "us_cpi":         "CPIAUCSL",
    "breakeven_5y5y": "T5YIFR",
    "nfci":           "NFCI",
}


def extend_parquet(dry_run: bool = False) -> pd.DataFrame:
    """把 market.parquet 擴展至 TARGET_START。"""
    existing = pd.DataFrame()
    if MARKET_PARQUET.exists():
        existing = pd.read_parquet(MARKET_PARQUET)
        existing.index = pd.to_datetime(existing.index).tz_localize(None)
        oldest = existing.index.min()
        target = pd.Timestamp(TARGET_START)
        if oldest <= target:
            logger.info(f"parquet 已有 {len(existing)} 行（最早 {oldest.date()}），無需擴展")
            return existing
        logger.info(f"parquet 最早 {oldest.date()}，目標擴展至 {TARGET_START}")
    else:
        logger.info("parquet 不存在，從頭建立")

    if dry_run:
        logger.info("[DRY RUN] 跳過 parquet 擴展")
        return existing

    import yfinance as yf
    frames: dict[str, pd.Series] = {}

    # yfinance 歷史
    for key, ticker in YFINANCE_TICKERS.items():
        logger.info(f"  yfinance: {key} ({ticker})")
        try:
            data = yf.download(ticker, start=TARGET_START, end="2026-12-31",
                               progress=False, auto_adjust=True)
            if data is not None and not data.empty:
                close = data["Close"]
                if hasattr(close, "squeeze"):
                    close = close.squeeze()
                close.index = pd.to_datetime(close.index).tz_localize(None)
                frames[key] = close
        except Exception as e:
            logger.warning(f"  yfinance {key} error: {e}")
        time.sleep(0.5)

    # FRED 歷史
    try:
        from fredapi import Fred
        from src.config import FRED_API_KEY
        if FRED_API_KEY:
            fred = Fred(api_key=FRED_API_KEY)
            for key, series_id in FRED_SERIES.items():
                logger.info(f"  FRED: {key} ({series_id})")
                try:
                    data = fred.get_series(series_id, observation_start=TARGET_START)
                    if data is not None and not data.empty:
                        data.index = pd.to_datetime(data.index).tz_localize(None)
                        frames[key] = data
                except Exception as e:
                    logger.warning(f"  FRED {key} error: {e}")
        else:
            logger.warning("FRED_API_KEY 未設定，跳過 FRED 歷史")
    except ImportError:
        logger.warning("fredapi 未安裝，跳過 FRED 擴展")

    if not frames:
        logger.error("無法取得歷史資料")
        return existing

    new_df = pd.DataFrame(frames)
    new_df.index = pd.to_datetime(new_df.index)
    new_df = new_df.sort_index()
    new_df = new_df.interpolate(method="time", limit=3)

    # 合併：保留較新資料（existing 優先）
    if not existing.empty:
        combined = new_df.combine_first(existing)
        combined = combined.sort_index()
    else:
        combined = new_df

    TIMESERIES_DIR.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(MARKET_PARQUET)
    logger.info(f"parquet 擴展完成：{len(combined)} 行，"
                f"{combined.index.min().date()} ~ {combined.index.max().date()}")
    return combined


# ═══════════════════════════════════════════════════════════════════════════
# 2. Regime 分類（規則式，從 parquet 推導）
# ═══════════════════════════════════════════════════════════════════════════

REGIMES = ["政策過渡", "風險偏好增長", "滯脹", "通縮風險"]


def _pct_return(series: pd.Series, idx: int, window: int) -> float | None:
    """計算 window 天前到 idx 的漲跌幅。"""
    if idx < window:
        return None
    past = series.iloc[idx - window]
    now  = series.iloc[idx]
    if pd.isna(past) or pd.isna(now) or past == 0:
        return None
    return (now - past) / past * 100


def classify_regime_series(df: pd.DataFrame) -> pd.Series:
    """為 parquet 的每個交易日分類 regime。"""
    regimes = []
    vix   = df["vix"]   if "vix"   in df.columns else pd.Series(dtype=float)
    spx   = df["spx"]   if "spx"   in df.columns else pd.Series(dtype=float)
    gold  = df["gold"]  if "gold"  in df.columns else pd.Series(dtype=float)
    bkevn = df["breakeven_5y5y"] if "breakeven_5y5y" in df.columns else pd.Series(dtype=float)

    n = len(df)
    for i in range(n):
        v   = vix.iloc[i]   if i < len(vix)   else float("nan")
        bk  = bkevn.iloc[i] if i < len(bkevn) else float("nan")
        spx_90 = _pct_return(spx,  i, 63)  # ~90 calendar days
        gold_90 = _pct_return(gold, i, 63)
        spx_30  = _pct_return(spx,  i, 21)  # ~30 calendar days

        # 規則優先順序
        if not pd.isna(v) and v > 30:
            regime = "通縮風險"
        elif (not pd.isna(bk) and bk > 2.7
              and not pd.isna(v) and v > 18):
            regime = "滯脹"
        elif (gold_90 is not None and gold_90 > 5
              and spx_30 is not None and spx_30 < -3):
            regime = "通縮風險"
        elif (spx_90 is not None and spx_90 > 12
              and not pd.isna(v) and v < 20):
            regime = "風險偏好增長"
        elif (gold_90 is not None and gold_90 > 5
              and spx_90 is not None and spx_90 > 5):
            regime = "滯脹"
        else:
            regime = "政策過渡"

        regimes.append(regime)

    return pd.Series(regimes, index=df.index)


def compute_regime_day(regimes: pd.Series) -> pd.Series:
    """計算每個 regime 持續了幾天。"""
    days = []
    count = 0
    prev = None
    for r in regimes:
        if r != prev:
            count = 1
            prev = r
        else:
            count += 1
        days.append(count)
    return pd.Series(days, index=regimes.index)


# ═══════════════════════════════════════════════════════════════════════════
# 3. 生成合成快照
# ═══════════════════════════════════════════════════════════════════════════

def _asset_entry(row: pd.Series, key: str, prev_row: pd.Series | None) -> dict | None:
    """從 parquet row 建立資產 dict。"""
    val = row.get(key)
    if pd.isna(val):
        return None
    change_pct = 0.0
    if prev_row is not None:
        prev = prev_row.get(key)
        if not pd.isna(prev) and prev != 0:
            change_pct = round((val - prev) / prev * 100, 4)
    return {
        "value": round(float(val), 4),
        "change_pct": change_pct,
        "quality": "historical",
    }


def build_synthetic_snapshot(
    date: str,
    row: pd.Series,
    prev_row: pd.Series | None,
    regime: str,
    regime_day: int,
) -> dict:
    """從 parquet 一行建立合成快照（最小格式，historian 可用）。"""
    market_data = {}
    for key in ["gold", "spx", "vix", "dxy", "brent", "wti",
                "usdjpy", "usdtwd", "us10y", "tips_10y", "us10y_alt",
                "copper", "breakeven_5y5y", "nfci", "fed_funds"]:
        entry = _asset_entry(row, key, prev_row)
        if entry:
            # historian._snapshot_to_text 用 "price" 或 "value"
            entry["price"] = entry["value"]
            market_data[key] = entry

    return {
        "date": date,
        "_synthetic": True,
        "_source": "historical_seed",
        "metadata": {
            "regime": regime,
            "regime_day": int(regime_day),
            "coverage_score": 0.7,
            "pipeline_version": "seed_v1",
        },
        "market_data": market_data,
        "tgri": {"score": None},  # 無 TGRI 歷史資料
    }


def generate_archive_snapshots(
    df: pd.DataFrame,
    dry_run: bool = False,
    interval_weeks: int = 2,
) -> list[str]:
    """從 parquet 生成雙週合成快照，存至 memory/archive/。

    Returns:
        生成的日期列表
    """
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    # 只選交易日（parquet 本身已是交易日索引）
    regimes    = classify_regime_series(df)
    regime_days = compute_regime_day(regimes)

    # 每 interval_weeks 週取一個代表日
    interval_days = interval_weeks * 5  # ~trading days
    selected_indices = list(range(0, len(df), interval_days))
    logger.info(f"準備生成 {len(selected_indices)} 個合成快照（每 {interval_weeks} 週）")

    dates_generated = []
    for i in selected_indices:
        row   = df.iloc[i]
        prev  = df.iloc[i - 1] if i > 0 else None
        date  = df.index[i].strftime("%Y-%m-%d")
        regime    = regimes.iloc[i]
        reg_day   = regime_days.iloc[i]

        path = ARCHIVE_DIR / f"{date}.json"
        if path.exists():
            dates_generated.append(date)
            continue

        snap = build_synthetic_snapshot(date, row, prev, regime, reg_day)

        if not dry_run:
            path.write_text(json.dumps(snap, ensure_ascii=False, indent=2,
                                       default=str), encoding="utf-8")
        dates_generated.append(date)

    logger.info(f"合成快照生成完成：{len(dates_generated)} 個，存至 {ARCHIVE_DIR}")
    return dates_generated


# ═══════════════════════════════════════════════════════════════════════════
# 4. 建立 Archive 向量索引
# ═══════════════════════════════════════════════════════════════════════════

def build_archive_embeddings(dates: list[str], dry_run: bool = False):
    """為所有 archive 快照建立 sentence-transformer 向量索引。"""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.error("sentence-transformers 未安裝，無法建立 embeddings")
        logger.error("  pip install sentence-transformers")
        return

    VECTORS_DIR.mkdir(parents=True, exist_ok=True)

    # 載入已有 archive index（允許增量更新）
    existing_index: list[dict] = []
    if ARCHIVE_INDEX_FILE.exists():
        try:
            existing_index = json.loads(ARCHIVE_INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing_index = []
    existing_dates = {e["date"] for e in existing_index}

    existing_embeddings: np.ndarray | None = None
    if ARCHIVE_EMBEDDINGS.exists():
        try:
            existing_embeddings = np.load(str(ARCHIVE_EMBEDDINGS))
        except Exception:
            existing_embeddings = None

    logger.info(f"載入 sentence-transformer 模型 all-MiniLM-L6-v2…")
    model = SentenceTransformer("all-MiniLM-L6-v2")

    new_index: list[dict] = []
    new_vecs: list[np.ndarray] = []
    skipped = 0

    for date in sorted(dates):
        if date in existing_dates:
            skipped += 1
            continue
        path = ARCHIVE_DIR / f"{date}.json"
        if not path.exists():
            continue
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
            text = _snapshot_to_text(snap)
            vec  = model.encode(text, normalize_embeddings=True)
            new_index.append({"date": date, "_source": "archive"})
            new_vecs.append(vec)
        except Exception as e:
            logger.warning(f"  embedding {date} failed: {e}")

    if skipped:
        logger.info(f"  跳過已有 embedding：{skipped} 個")

    if not new_vecs and existing_embeddings is not None:
        logger.info("無新 embedding 需要建立")
        return

    if not dry_run:
        # 合併舊 + 新
        full_index = existing_index + new_index
        if existing_embeddings is not None and new_vecs:
            all_vecs = np.vstack([existing_embeddings, np.array(new_vecs)])
        elif new_vecs:
            all_vecs = np.array(new_vecs)
        else:
            return

        ARCHIVE_INDEX_FILE.write_text(
            json.dumps(full_index, ensure_ascii=False), encoding="utf-8")
        np.save(str(ARCHIVE_EMBEDDINGS), all_vecs)
        logger.info(f"Archive embedding 建立完成：共 {len(full_index)} 個向量 "
                    f"({len(new_vecs)} 新增)")
    else:
        logger.info(f"[DRY RUN] 將建立 {len(new_vecs)} 個新 embedding")


def _snapshot_to_text(snapshot: dict) -> str:
    """與 historian.py 相同的文字轉換，確保向量空間一致。"""
    parts = []
    md = snapshot.get("metadata", {})
    parts.append(f"regime={md.get('regime', '')} day={md.get('regime_day', 0)}")

    mkt = snapshot.get("market_data", {})
    for key in ["gold", "spx", "vix", "dxy", "brent", "usdjpy"]:
        item = mkt.get(key, {})
        if isinstance(item, dict):
            val = item.get("price") or item.get("value")
            chg = item.get("change_pct", 0)
            if val is not None:
                try:
                    parts.append(f"{key}={val} ({float(chg):+.1f}%)")
                except (TypeError, ValueError):
                    parts.append(f"{key}={val}")

    tgri = snapshot.get("tgri", {})
    if tgri and tgri.get("score") is not None:
        parts.append(f"tgri={tgri['score']}")

    return " ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# 5. 冷啟動：清除即時狀態
# ═══════════════════════════════════════════════════════════════════════════

COLD_START_TARGETS = [
    # 日常快照（清除後 historian 仍可用 archive）
    ("dir",  SNAPSHOTS_DIR,      "*.json"),
    # 記憶層
    ("file", MEMORY_DIR / "l2.json",  None),
    ("file", MEMORY_DIR / "l3.json",  None),
    ("file", MEMORY_DIR / "l4.json",  None),
    ("file", MEMORY_DIR / "l5.json",  None),
    # 系統狀態
    ("file", SYSTEM_DIR / "data_gaps.json",   None),
    ("file", SYSTEM_DIR / "data_health.json", None),
    # 時序推論記錄
    ("file", TIMESERIES_DIR / "inference_history.jsonl",  None),
    ("file", TIMESERIES_DIR / "scorecard_history.json",   None),
    ("file", TIMESERIES_DIR / "regime_history.json",      None),
    # 即時向量（每日自動重建，archive 仍保留）
    ("file", VECTORS_DIR / "embeddings.npy",  None),
    ("file", VECTORS_DIR / "index.json",      None),
]

COLD_START_KEEP = [
    MARKET_PARQUET,
    ARCHIVE_DIR,
    ARCHIVE_INDEX_FILE,
    ARCHIVE_EMBEDDINGS,
    TIMESERIES_DIR / "market.parquet",
]


def cold_start(dry_run: bool = False):
    """清除所有即時狀態，保留 parquet 與 archive。"""
    logger.info("=" * 60)
    logger.info("冷啟動：清除即時狀態")
    logger.info("保留：market.parquet, archive/, archive_embeddings.npy")
    logger.info("=" * 60)

    deleted = []
    for entry_type, path, glob_pattern in COLD_START_TARGETS:
        if entry_type == "file":
            if path.exists():
                if not dry_run:
                    path.unlink()
                deleted.append(str(path.relative_to(PROJECT_ROOT)))
                logger.info(f"  刪除 {path.relative_to(PROJECT_ROOT)}")
            else:
                logger.debug(f"  不存在（跳過）：{path.relative_to(PROJECT_ROOT)}")

        elif entry_type == "dir" and glob_pattern:
            files = list(path.glob(glob_pattern))
            for f in files:
                if not dry_run:
                    f.unlink()
                deleted.append(str(f.relative_to(PROJECT_ROOT)))
            if files:
                logger.info(f"  刪除 {len(files)} 個檔案 from {path.relative_to(PROJECT_ROOT)}/")

    if dry_run:
        logger.info(f"[DRY RUN] 將刪除 {len(deleted)} 個檔案/目錄")
    else:
        logger.info(f"冷啟動完成，刪除 {len(deleted)} 個項目")

    return deleted


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="DIB Historian Seed Script")
    parser.add_argument("--dry-run",      action="store_true", help="只印計劃，不寫入")
    parser.add_argument("--skip-parquet", action="store_true", help="跳過 parquet 擴展")
    parser.add_argument("--skip-embed",   action="store_true", help="跳過 embedding 建立")
    parser.add_argument("--cold-start",   action="store_true", help="執行冷啟動")
    parser.add_argument("--interval",     type=int, default=2, help="快照間隔週數（預設 2）")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("DIB Historian Seed — 15 年歷史資料種子")
    logger.info("=" * 60)

    # Step 1: 擴展 parquet
    if not args.skip_parquet:
        df = extend_parquet(dry_run=args.dry_run)
    else:
        if MARKET_PARQUET.exists():
            df = pd.read_parquet(MARKET_PARQUET)
            df.index = pd.to_datetime(df.index)
            logger.info(f"使用現有 parquet：{len(df)} 行")
        else:
            logger.error("parquet 不存在且跳過擴展，無法繼續")
            sys.exit(1)

    # Step 2: 生成 archive 快照
    dates = generate_archive_snapshots(df, dry_run=args.dry_run,
                                       interval_weeks=args.interval)

    # Step 3: 建立 archive embeddings
    if not args.skip_embed:
        build_archive_embeddings(dates, dry_run=args.dry_run)
    else:
        logger.info("跳過 embedding 建立")

    # Step 4: 冷啟動（可選）
    if args.cold_start:
        cold_start(dry_run=args.dry_run)
    else:
        logger.info("跳過冷啟動（加 --cold-start 執行）")

    logger.info("=" * 60)
    logger.info("完成")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
