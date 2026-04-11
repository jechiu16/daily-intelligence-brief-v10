from __future__ import annotations
"""Thesis Promoter — 自動將 analyst 的 new_thesis_candidates 升格為 proposed theses。

Pipeline Step 15.5（位於 Calibration 之後）。

升格規則：
- 不重複：與現有 active theses 標題相似度 > 0.8 者跳過
- 7 天冷卻：同一 title 在 7 天內已存在 proposed thesis 者跳過
- status 設為 "proposed"（等待人工用 thesis_cli.py review 指令確認）
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import THESES_DIR

logger = logging.getLogger(__name__)

THESES_ACTIVE = THESES_DIR / "active"
THESES_PROPOSED = THESES_DIR / "proposed"


def _slugify(text: str) -> str:
    return re.sub(r"[\s_]+", "_", re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE)).strip("_")[:40]


def _next_thesis_id() -> str:
    """產生下一個不重複的 Thesis ID（T001, T002, ...）。"""
    from src.config import THESES_DIR
    all_dirs = [THESES_ACTIVE, THESES_PROPOSED, THESES_DIR / "closed", THESES_DIR / "archived"]
    all_ids: set[str] = set()
    for d in all_dirs:
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(t, dict) and "id" in t:
                    all_ids.add(t["id"])
            except Exception:
                continue
    i = 1
    while f"T{i:03d}" in all_ids:
        i += 1
    return f"T{i:03d}"


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())


def _title_similar(a: str, b: str, threshold: float = 0.7) -> bool:
    """粗略相似度：共同 token 比例超過 threshold 視為重複。"""
    tokens_a = set(_normalize_title(a).split())
    tokens_b = set(_normalize_title(b).split())
    if not tokens_a or not tokens_b:
        return a.strip().lower() == b.strip().lower()
    overlap = len(tokens_a & tokens_b)
    return overlap / max(len(tokens_a), len(tokens_b)) >= threshold


def _load_all_titles(directory: Path) -> list[str]:
    titles = []
    if not directory.exists():
        return titles
    for f in directory.glob("*.json"):
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(t, dict) and t.get("title"):
                titles.append(t["title"])
        except Exception:
            continue
    return titles


def _within_cooldown(title: str, days: int = 7) -> bool:
    """在 proposed 目錄中找近 {days} 天同名 thesis，有則回傳 True。"""
    if not THESES_PROPOSED.exists():
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    for f in THESES_PROPOSED.glob("*.json"):
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(t, dict):
                continue
            if _title_similar(t.get("title", ""), title) and t.get("created_date", "") >= cutoff:
                return True
        except Exception:
            continue
    return False


def promote_candidates(candidates: list[dict], today_str: str) -> list[str]:
    """將 analyst 的 new_thesis_candidates 升格，回傳已建立的 thesis ID 列表。

    candidates 格式（來自 analyst 輸出）：
    [
      {
        "title": "...",
        "rationale": "...",
        "initial_confidence": 0.45,
        "suggested_invalidator": "..."
      }
    ]
    """
    if not candidates:
        return []

    THESES_PROPOSED.mkdir(parents=True, exist_ok=True)

    # 已有的 active + proposed 標題（防重複）
    existing_titles = (
        _load_all_titles(THESES_ACTIVE)
        + _load_all_titles(THESES_PROPOSED)
    )

    promoted: list[str] = []

    for cand in candidates:
        title = (cand.get("title") or "").strip()
        if not title:
            continue

        # 重複檢查
        if any(_title_similar(title, et) for et in existing_titles):
            logger.info(f"ThesisPromoter: skip '{title}' — similar thesis already exists")
            continue

        # 冷卻檢查
        if _within_cooldown(title, days=7):
            logger.info(f"ThesisPromoter: skip '{title}' — cooldown (7d)")
            continue

        tid = _next_thesis_id()
        thesis = {
            "id": tid,
            "status": "proposed",
            "title": title,
            "rationale": cand.get("rationale", ""),
            "created_date": today_str,
            "promoted_date": today_str,
            "source": "analyst_auto",
            "deadline": "",
            "initial_confidence": cand.get("initial_confidence", 0.5),
            "current_confidence": cand.get("initial_confidence", 0.5),
            "confidence_history": [
                {"date": today_str, "value": cand.get("initial_confidence", 0.5), "note": "auto-promoted"}
            ],
            "invalidators": [
                {
                    "condition": cand.get("suggested_invalidator", ""),
                    "data_key": "",
                    "operator": "",
                    "threshold": None,
                    "triggered": False,
                    "triggered_date": None,
                }
            ] if cand.get("suggested_invalidator") else [],
            "updates": [],
            "tags": ["auto-proposed"],
        }

        slug = _slugify(title) or "thesis"
        path = THESES_PROPOSED / f"{tid}_{slug}.json"
        path.write_text(json.dumps(thesis, indent=2, ensure_ascii=False), encoding="utf-8")

        existing_titles.append(title)  # 防止同批次重複
        promoted.append(tid)
        logger.info(f"ThesisPromoter: promoted '{title}' → {tid}")

    return promoted
