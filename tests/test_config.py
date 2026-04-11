"""Unit tests for src/config.py — quality_for_source and date parsing (M8)."""
from __future__ import annotations

import pytest


# ── quality_for_source ────────────────────────────────────────────────────

def test_tier_c_always_estimated():
    from src.config import quality_for_source
    assert quality_for_source("unknown_source") == "estimated"


def test_confirmed_same_day():
    from src.config import quality_for_source
    # SOURCE_TIER 使用大寫 key
    q = quality_for_source("FRED", observation_date="2026-04-10", today_str="2026-04-10")
    assert q == "confirmed"


def test_cached_1_to_7_days():
    from src.config import quality_for_source
    q = quality_for_source("FRED", observation_date="2026-04-05", today_str="2026-04-10")
    assert q == "cached"


def test_stale_over_7_days():
    from src.config import quality_for_source
    q = quality_for_source("FRED", observation_date="2026-03-01", today_str="2026-04-10")
    assert q == "stale"


# ── M8: 多格式日期解析 ─────────────────────────────────────────────────────

def test_yyyymmdd_format():
    """YYYYMMDD 格式（無分隔符）。"""
    from src.config import quality_for_source
    q = quality_for_source("FRED", observation_date="20260410", today_str="20260410")
    assert q == "confirmed"


def test_yyyy_mm_format():
    """YYYY-MM（月精度，BLS CPI 使用）。"""
    from src.config import quality_for_source
    # 2026-03 被解析為 2026-03-01，對應 today=2026-04-10 → lag=40 → stale
    q = quality_for_source("BLS", observation_date="2026-03", today_str="2026-04-10")
    assert q == "stale"


def test_us_date_format():
    """MM/DD/YYYY（美式格式）。"""
    from src.config import quality_for_source
    q = quality_for_source("FRED", observation_date="04/10/2026", today_str="2026-04-10")
    assert q == "confirmed"


def test_invalid_date_falls_back():
    """無法解析時退回原邏輯（不崩潰）。"""
    from src.config import quality_for_source
    q = quality_for_source("FRED", observation_date="not-a-date", today_str="2026-04-10", data_is_today=True)
    assert q == "confirmed"  # fallback: tier A + data_is_today=True


# ── notion_publisher parse_markdown_to_rich_text ─────────────────────────

def test_parse_bold():
    from src.notion_publisher import parse_markdown_to_rich_text
    result = parse_markdown_to_rich_text("**hello**")
    assert any(seg.get("annotations", {}).get("bold") for seg in result)


def test_parse_quality_marker():
    from src.notion_publisher import parse_markdown_to_rich_text
    result = parse_markdown_to_rich_text("{{confirmed:4.50}}")
    assert len(result) == 1
    assert result[0]["text"]["content"] == "4.50"
    assert result[0]["annotations"]["color"] == "blue"


def test_parse_plain_text():
    from src.notion_publisher import parse_markdown_to_rich_text
    result = parse_markdown_to_rich_text("hello world")
    assert result[0]["text"]["content"] == "hello world"


def test_split_long_text():
    from src.notion_publisher import _split_long_text
    text = "A。" * 600  # 1200 chars
    chunks = _split_long_text(text, max_len=800)
    assert len(chunks) > 1
    assert all(len(c) <= 800 for c in chunks)


# ── inference_store rotation ──────────────────────────────────────────────

def test_rotate_keeps_recent(tmp_path, monkeypatch):
    """L6: rotation 保留近期記錄，截斷舊記錄。"""
    import json
    import src.inference_store as store
    monkeypatch.setattr(store, "INFERENCE_HISTORY_PATH", tmp_path / "inference_history.jsonl")
    monkeypatch.setattr(store, "TIMESERIES_DIR", tmp_path)

    # 寫入 old record（370 天前）和 new record（今天）
    path = tmp_path / "inference_history.jsonl"
    old = {"date": "2025-01-01", "inf_id": "INF_OLD", "claim": "old"}
    new = {"date": "2026-04-10", "inf_id": "INF_NEW", "claim": "new"}
    with open(path, "w") as f:
        f.write(json.dumps(old) + "\n")
        f.write(json.dumps(new) + "\n")

    store._rotate_inference_history(max_days=365)

    records = store.load_all()
    ids = [r["inf_id"] for r in records]
    assert "INF_NEW" in ids
    assert "INF_OLD" not in ids


def test_rotate_noop_if_all_recent(tmp_path, monkeypatch):
    """L6: 全部記錄都在保留窗口內 → 不截斷。"""
    import json
    import src.inference_store as store
    monkeypatch.setattr(store, "INFERENCE_HISTORY_PATH", tmp_path / "inference_history.jsonl")
    monkeypatch.setattr(store, "TIMESERIES_DIR", tmp_path)

    path = tmp_path / "inference_history.jsonl"
    records = [{"date": "2026-04-10", "inf_id": f"INF_{i}", "claim": ""} for i in range(5)]
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    store._rotate_inference_history(max_days=365)
    assert len(store.load_all()) == 5
