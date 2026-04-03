#!/usr/bin/env python3
"""Historian Warm-Start — 從 market.parquet 批量 bootstrap 向量索引。

解決 Historian 的 30-day 冷啟動問題。
將 market.parquet 的 1530 行轉成 lite snapshots → embedding → 存入向量索引。

用法：
    python tools/historian_warmstart.py
    python tools/historian_warmstart.py --limit 200       # 只處理最近 200 天
    python tools/historian_warmstart.py --reset           # 清空並重建
    python tools/historian_warmstart.py --dry-run         # 只顯示統計，不寫入
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ── 路徑 ──────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import MISSING_DATA, TIMESERIES_DIR, VECTORS_DIR

EMBEDDINGS_FILE = VECTORS_DIR / "embeddings.npy"
INDEX_FILE = VECTORS_DIR / "index.json"
PARQUET_FILE = TIMESERIES_DIR / "market.parquet"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ── 量化特徵正規化 ────────────────────────────────────────────────────────────

_ASSET_APPROX = {
    "gold":   (1800, 5000),
    "spx":    (3000, 7000),
    "vix":    (10, 50),
    "dxy":    (90, 115),
    "brent":  (40, 130),
    "wti":    (35, 120),
    "usdjpy": (100, 160),
    "usdtwd": (28, 35),
    "us10y":  (0.5, 5.0),
    "copper": (3.0, 5.5),
}


def _parquet_row_to_text(row: pd.Series) -> str:
    """把 parquet 的一行轉成可 embed 的文字。"""
    parts = []
    for col, (lo, hi) in _ASSET_APPROX.items():
        val = row.get(col)
        if pd.notna(val) and val != 0:
            # 正規化到 -1~1 以便 embedding 語意穩定
            norm = (val - lo) / (hi - lo) * 2 - 1
            parts.append(f"{col}={norm:.3f}")

    # 計算簡單衍生指標（change vs 20天均線 proxy）
    return " ".join(parts)


def _parquet_row_to_snapshot_lite(date: str, row: pd.Series) -> dict:
    """把 parquet 一行轉成 lite snapshot（不含推論鏈，只有市場數據）。"""
    mkt = {}
    for col in _ASSET_APPROX:
        val = row.get(col)
        if pd.notna(val) and val != 0:
            mkt[col] = {"price": float(val), "quality": "cached", "source": "parquet"}

    return {
        "date": date,
        "metadata": {
            "regime": MISSING_DATA,
            "regime_day": 0,
            "coverage_score": 0.5,
            "pipeline_version": "warmstart",
        },
        "market_data": mkt,
        "tgri": {},
        "inference_chain": [],
    }


# ── Embedding ─────────────────────────────────────────────────────────────────

def _load_model():
    """載入 sentence-transformers 模型（懶加載）。"""
    try:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading all-MiniLM-L6-v2 model...")
        return SentenceTransformer("all-MiniLM-L6-v2")
    except ImportError:
        logger.error("sentence-transformers not installed. Run: pip install sentence-transformers")
        sys.exit(1)


def _batch_embed(texts: list[str], model, batch_size: int = 64) -> np.ndarray:
    """批量 embed，帶進度提示。"""
    all_embeddings = []
    total = len(texts)
    for start in range(0, total, batch_size):
        batch = texts[start:start + batch_size]
        embs = model.encode(batch, normalize_embeddings=True, show_progress_bar=False)
        all_embeddings.append(embs)
        done = min(start + batch_size, total)
        if done % 200 == 0 or done == total:
            logger.info(f"  Embedded {done}/{total} rows...")
    return np.vstack(all_embeddings)


# ── 主程式 ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Historian Warm-Start Tool")
    parser.add_argument("--limit", type=int, default=0, help="只處理最近 N 天（0=全部）")
    parser.add_argument("--reset", action="store_true", help="清空現有索引再重建")
    parser.add_argument("--dry-run", action="store_true", help="只顯示統計，不寫入")
    args = parser.parse_args()

    if not PARQUET_FILE.exists():
        logger.error(f"market.parquet 不存在: {PARQUET_FILE}")
        sys.exit(1)

    # 1. 載入 parquet
    df = pd.read_parquet(PARQUET_FILE)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    logger.info(f"Parquet: {len(df)} rows, {df.index.min().date()} → {df.index.max().date()}")

    if args.limit > 0:
        df = df.tail(args.limit)
        logger.info(f"Limiting to last {args.limit} rows → {len(df)} rows")

    # 2. 載入現有索引（若不 reset）
    existing_dates: set[str] = set()
    existing_index: list[dict] = []
    existing_embeddings: np.ndarray | None = None

    if not args.reset and INDEX_FILE.exists():
        try:
            existing_index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            existing_dates = {e["date"] for e in existing_index}
            logger.info(f"Existing index: {len(existing_dates)} dates")
        except Exception as e:
            logger.warning(f"Failed to load existing index: {e}")

    if not args.reset and EMBEDDINGS_FILE.exists():
        try:
            existing_embeddings = np.load(str(EMBEDDINGS_FILE))
            logger.info(f"Existing embeddings: {existing_embeddings.shape}")
        except Exception as e:
            logger.warning(f"Failed to load existing embeddings: {e}")

    # 3. 篩選需要新 embed 的行
    new_rows = []
    for ts, row in df.iterrows():
        date_str = ts.strftime("%Y-%m-%d")
        if date_str not in existing_dates:
            new_rows.append((date_str, row))

    logger.info(f"New rows to embed: {len(new_rows)}")

    if args.dry_run:
        print(f"\n[DRY RUN] 現有索引: {len(existing_dates)} 筆")
        print(f"[DRY RUN] 待新增: {len(new_rows)} 筆")
        print(f"[DRY RUN] 完成後總計: {len(existing_dates) + len(new_rows)} 筆")
        return

    if not new_rows:
        logger.info("All rows already in index. Nothing to do.")
        return

    # 4. 生成文字並 embed
    model = _load_model()
    texts = [_parquet_row_to_text(row) for _, row in new_rows]
    logger.info(f"Embedding {len(texts)} rows...")
    new_embeddings = _batch_embed(texts, model)

    # 5. 合併索引
    VECTORS_DIR.mkdir(parents=True, exist_ok=True)

    if args.reset or existing_embeddings is None:
        # 從頭建立：把 existing 也重建（reset）或合併空的
        all_index = existing_index + [{"date": d} for d, _ in new_rows]
        all_embeddings = new_embeddings if existing_embeddings is None else np.vstack([existing_embeddings, new_embeddings])
    else:
        all_index = existing_index + [{"date": d} for d, _ in new_rows]
        all_embeddings = np.vstack([existing_embeddings, new_embeddings])

    # 去重（按 date 排序，保留最後一筆）
    seen = {}
    for i, entry in enumerate(all_index):
        seen[entry["date"]] = i
    unique_indices = sorted(seen.values())

    all_index = [all_index[i] for i in unique_indices]
    all_embeddings = all_embeddings[unique_indices]

    # 6. 寫入
    INDEX_FILE.write_text(json.dumps(all_index, ensure_ascii=False), encoding="utf-8")
    np.save(str(EMBEDDINGS_FILE), all_embeddings)

    logger.info(f"✓ Vector index saved: {len(all_index)} snapshots → {INDEX_FILE}")
    logger.info(f"✓ Embeddings saved: {all_embeddings.shape} → {EMBEDDINGS_FILE}")
    print(f"\n完成！向量索引現有 {len(all_index)} 筆快照（{all_embeddings.shape[1]}維）")
    print(f"Historian 將可以進行歷史類比分析（需要 ≥30 筆）")


if __name__ == "__main__":
    main()
