from __future__ import annotations
"""Historian — DeepSeek，超長 context 歷史類比。"""

import json
import logging
import numpy as np
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.config import (
    DEEPSEEK_FAST_MODEL, MISSING_DATA,
    SNAPSHOTS_DIR, TIMESERIES_DIR, VECTORS_DIR,
)
from src.deepseek_client import chat
from src.prompts.language_policy import TRADITIONAL_CHINESE_ONLY

logger = logging.getLogger(__name__)

EMBEDDINGS_FILE = VECTORS_DIR / "embeddings.npy"
INDEX_FILE = VECTORS_DIR / "index.json"

# Archive（歷史種子，由 scripts/seed_historian.py 生成）
ARCHIVE_DIR       = Path(__file__).parent.parent / "memory" / "archive"
ARCHIVE_INDEX_FILE     = VECTORS_DIR / "archive_index.json"
ARCHIVE_EMBEDDINGS_FILE = VECTORS_DIR / "archive_embeddings.npy"

MIN_SNAPSHOTS_FOR_HISTORIAN = 30


def _load_snapshots_index() -> list[dict]:
    """載入向量索引，archive（歷史種子）+ live（每日運行）合併。"""
    archive: list[dict] = []
    if ARCHIVE_INDEX_FILE.exists():
        try:
            archive = json.loads(ARCHIVE_INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            archive = []

    live: list[dict] = []
    if INDEX_FILE.exists():
        try:
            live = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            live = []

    # 合併，去重（live 覆蓋同日的 archive entry）
    seen: dict[str, dict] = {}
    for entry in archive:
        seen[entry["date"]] = entry
    for entry in live:
        seen[entry["date"]] = entry  # live 覆蓋 archive

    return list(seen.values())


def _load_embeddings() -> np.ndarray | None:
    """載入 embedding 矩陣，archive + live 依 index 順序對齊堆疊。"""
    index = _load_snapshots_index()
    if not index:
        return None

    # 分別載入
    archive_vecs: np.ndarray | None = None
    if ARCHIVE_EMBEDDINGS_FILE.exists():
        try:
            archive_vecs = np.load(str(ARCHIVE_EMBEDDINGS_FILE))
        except Exception:
            archive_vecs = None

    live_vecs: np.ndarray | None = None
    if EMBEDDINGS_FILE.exists():
        try:
            live_vecs = np.load(str(EMBEDDINGS_FILE))
        except Exception:
            live_vecs = None

    if archive_vecs is None and live_vecs is None:
        return None

    # archive index 與 live index 分別建立 date→row 映射
    archive_index: list[dict] = []
    if ARCHIVE_INDEX_FILE.exists():
        try:
            archive_index = json.loads(ARCHIVE_INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            archive_index = []

    live_index: list[dict] = []
    if INDEX_FILE.exists():
        try:
            live_index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            live_index = []

    archive_map = {e["date"]: i for i, e in enumerate(archive_index)}
    live_map    = {e["date"]: i for i, e in enumerate(live_index)}

    # 按 merged index 順序拼出 embedding 矩陣
    vectors = []
    for entry in index:
        date = entry["date"]
        if date in live_map and live_vecs is not None:
            i = live_map[date]
            if i < len(live_vecs):
                vectors.append(live_vecs[i])
                continue
        if date in archive_map and archive_vecs is not None:
            i = archive_map[date]
            if i < len(archive_vecs):
                vectors.append(archive_vecs[i])

    if not vectors:
        return None
    return np.array(vectors, dtype=np.float32)


def _embed_snapshot(snapshot: dict) -> np.ndarray | None:
    """用 sentence-transformers 做本地 embedding。"""
    try:
        logging.getLogger("huggingface_hub.utils._http").setLevel(logging.ERROR)
        logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")

        # 把快照轉為文字描述
        text = _snapshot_to_text(snapshot)
        embedding = model.encode(text, normalize_embeddings=True)
        return embedding
    except ImportError:
        logger.warning("sentence-transformers not installed, skipping embedding")
        return None
    except Exception as e:
        logger.error(f"Embedding error: {e}")
        return None


def _snapshot_to_text(snapshot: dict) -> str:
    """把快照轉為可 embed 的文字。"""
    parts = []
    md = snapshot.get("metadata", {})
    parts.append(f"regime={md.get('regime', '')} day={md.get('regime_day', 0)}")

    mkt = snapshot.get("market_data", {})
    for key in ["gold", "spx", "vix", "dxy", "brent", "usdjpy"]:
        item = mkt.get(key, {})
        if isinstance(item, dict):
            val = item.get("price") or item.get("value")
            chg = item.get("change_pct", 0)
            if val and val != MISSING_DATA:
                parts.append(f"{key}={val} ({chg:+.1f}%)")

    tgri = snapshot.get("tgri", {})
    if tgri.get("score") is not None:
        parts.append(f"tgri={tgri['score']}")

    return " ".join(parts)


def find_similar_by_vector(today_snapshot: dict, top_k: int = 10) -> list[str]:
    """向量搜尋最相似的歷史日期。"""
    embeddings = _load_embeddings()
    index = _load_snapshots_index()

    if embeddings is None or not index or len(index) < MIN_SNAPSHOTS_FOR_HISTORIAN:
        return []

    today_vec = _embed_snapshot(today_snapshot)
    if today_vec is None:
        return []

    # 餘弦相似度
    similarities = np.dot(embeddings, today_vec)
    top_indices = np.argsort(similarities)[::-1][:top_k]
    return [index[i]["date"] for i in top_indices if i < len(index)]


def filter_by_quant_similarity(candidates: list[str], today_snapshot: dict) -> list[str]:
    """從候選日期中，用量化特徵進一步篩選前 3。"""
    if len(candidates) <= 3:
        return candidates[:3]

    market_parquet = TIMESERIES_DIR / "market.parquet"
    if not market_parquet.exists():
        return candidates[:3]

    try:
        df = pd.read_parquet(market_parquet)
    except Exception:
        return candidates[:3]

    # 簡單：取前 3（向量相似度已排序）
    return candidates[:3]


def load_historical_snapshot(date: str) -> dict | None:
    """載入歷史快照，優先 daily_snapshots，fallback 到 archive。"""
    for directory in (SNAPSHOTS_DIR, ARCHIVE_DIR):
        path = directory / f"{date}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None


def get_outcomes_after(date: str, days: int = 14) -> dict:
    """讀取某日期後 days 天的市場走勢。"""
    try:
        df = pd.read_parquet(TIMESERIES_DIR / "market.parquet")
        df.index = pd.to_datetime(df.index)
        start = pd.Timestamp(date)

        outcomes = {}
        for col in ["gold", "spx", "dxy", "brent"]:
            if col not in df.columns:
                continue
            window = df[col].loc[start:]
            if len(window) < days:
                continue
            start_price = window.iloc[0]
            end_price = window.iloc[min(days, len(window) - 1)]
            if start_price != 0:
                outcomes[col] = round((end_price - start_price) / start_price, 4)
        return outcomes
    except Exception as e:
        logger.debug(f"get_outcomes_after {date}: {e}")
        return {}


def run_historian(today_snapshot: dict) -> dict:
    """主入口：歷史類比分析。"""
    index = _load_snapshots_index()

    if len(index) < MIN_SNAPSHOTS_FOR_HISTORIAN:
        logger.info(f"Historian: only {len(index)} snapshots, need {MIN_SNAPSHOTS_FOR_HISTORIAN}")
        return _missing_historian()

    # 1. 向量搜尋候選
    candidates = find_similar_by_vector(today_snapshot, top_k=10)
    if not candidates:
        logger.info("Historian: no embedding index, skipping")
        return _missing_historian()

    # 2. 量化篩選 top 3
    top_3 = filter_by_quant_similarity(candidates, today_snapshot)

    # 3. 讀歷史快照
    historical_snapshots = []
    outcomes = []
    for d in top_3:
        snap = load_historical_snapshot(d)
        if snap:
            historical_snapshots.append(snap)
            outcomes.append(get_outcomes_after(d, days=14))

    if not historical_snapshots:
        return _missing_historian()

    # 4. 計算 base rates
    base_rates = {}
    for asset in ["gold", "spx", "dxy"]:
        vals = [o.get(asset, 0) for o in outcomes if asset in o]
        if vals:
            base_rates[f"{asset}_down_next_14d"] = round(sum(1 for v in vals if v < 0) / len(vals), 2)
            base_rates["sample_size"] = len(vals)

    # 5. DeepSeek 敘事型歷史類比
    narrative = _deepseek_narrative(historical_snapshots, outcomes, today_snapshot)

    analog_ids = [f"HIST_{d.replace('-', '')}" for d in top_3]

    result = {
        "similar_periods": [
            {
                "date": top_3[i],
                "analog_id": analog_ids[i],
                "subsequent_14d": outcomes[i] if i < len(outcomes) else {},
            }
            for i in range(len(top_3))
        ],
        "base_rates": base_rates,
        "analog_ids": analog_ids,
        "narrative_comparison": narrative,
    }

    logger.info(f"Historian: found {len(top_3)} analogs: {top_3}")
    return result


def _deepseek_narrative(
    historical_snapshots: list[dict],
    outcomes: list[dict],
    today_snapshot: dict,
) -> str:
    """用 DeepSeek 做敘事型歷史類比。"""
    prompt = TRADITIONAL_CHINESE_ONLY + "\n\n" + f"""你是歷史類比分析師。以下是今日市場快照和三個最相似的歷史場景。

## 今日快照
{json.dumps(_snapshot_to_text_dict(today_snapshot), ensure_ascii=False)}

## 歷史類比場景

"""
    for i, (snap, outcome) in enumerate(zip(historical_snapshots, outcomes)):
        prompt += f"""### 場景 {i+1}：{snap.get('date', '')}
{json.dumps(_snapshot_to_text_dict(snap), ensure_ascii=False)}
後續14天走勢：{json.dumps(outcome, ensure_ascii=False)}

"""

    prompt += """
請用繁體中文，2-3 段散文，說明今日市場結構與哪個歷史場景最相似，以及這個類比暗示了什麼。
注意：類比只是參考，不是預言，請誠實說明類比的侷限性。"""

    try:
        text, _usage = chat(
            model=DEEPSEEK_FAST_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
        )
        return text.strip()
    except Exception as e:
        logger.error(f"DeepSeek historian narrative error ({DEEPSEEK_FAST_MODEL}): {e}")
        return MISSING_DATA


def _snapshot_to_text_dict(snapshot: dict) -> dict:
    """提取快照的關鍵數值用於 DeepSeek 輸入。"""
    md = snapshot.get("metadata", {})
    mkt = snapshot.get("market_data", {})
    result = {
        "date": snapshot.get("date", ""),
        "regime": md.get("regime", MISSING_DATA),
        "coverage": md.get("coverage_score", 0),
    }
    for key in ["gold", "spx", "vix", "dxy", "brent", "us10y", "tips_10y"]:
        item = mkt.get(key, {})
        if isinstance(item, dict):
            val = item.get("price") or item.get("value")
            if val and val != MISSING_DATA:
                result[key] = val
    return result


def _missing_historian() -> dict:
    return {
        "similar_periods": [],
        "base_rates": {MISSING_DATA: True},
        "analog_ids": [],
        "narrative_comparison": MISSING_DATA,
    }


def update_vector_index(date: str, snapshot: dict):
    """每日 pipeline 後呼叫，更新向量索引。

    M6 修正：embedding 失敗時明確 warning，不再 silent skip。
    sentence-transformers 未安裝時 historian 功能完全降級，analyst 會拿到空的 historian_package。
    """
    VECTORS_DIR.mkdir(parents=True, exist_ok=True)

    embedding = _embed_snapshot(snapshot)
    if embedding is None:
        logger.warning(
            "update_vector_index: embedding skipped (sentence-transformers 未安裝或 embedding 失敗) — "
            "historian 運行在降級模式，analyst 今日不會收到歷史類比資料。"
            "安裝方式：pip install sentence-transformers"
        )
        return

    index = _load_snapshots_index()
    existing_embeddings = _load_embeddings()

    # 避免重複
    if any(e.get("date") == date for e in index):
        return

    index.append({"date": date})
    INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    if existing_embeddings is None:
        all_embeddings = np.array([embedding])
    else:
        all_embeddings = np.vstack([existing_embeddings, embedding])

    np.save(str(EMBEDDINGS_FILE), all_embeddings)
    logger.info(f"Historian: vector index updated, total={len(index)} snapshots")
