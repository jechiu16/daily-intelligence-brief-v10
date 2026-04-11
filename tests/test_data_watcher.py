"""Unit tests for src/data_watcher.py — retry helper and fallback chain (no network calls)."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


# ── _with_retry ──────────────────────────────────────────────────────────────

def test_with_retry_success_first_attempt():
    from src.data_watcher import _with_retry
    call_count = 0

    def func():
        nonlocal call_count
        call_count += 1
        return {"value": 42}

    result = _with_retry(func, retries=2, base_wait=0, label="test")
    assert result == {"value": 42}
    assert call_count == 1


def test_with_retry_retries_on_none():
    from src.data_watcher import _with_retry
    results = [None, None, {"value": 99}]
    idx = 0

    def func():
        nonlocal idx
        r = results[idx]
        idx += 1
        return r

    result = _with_retry(func, retries=3, base_wait=0, label="test")
    assert result == {"value": 99}
    assert idx == 3


def test_with_retry_exhausted_returns_none():
    from src.data_watcher import _with_retry

    result = _with_retry(lambda: None, retries=2, base_wait=0, label="test")
    assert result is None


def test_with_retry_non_retryable_exception_returns_none_immediately():
    import requests
    from src.data_watcher import _with_retry
    call_count = 0

    def func():
        nonlocal call_count
        call_count += 1
        raise ValueError("bad input")

    result = _with_retry(func, retries=3, base_wait=0, label="test")
    assert result is None
    assert call_count == 1  # Non-retryable: should not retry


def test_with_retry_retries_on_network_timeout():
    import requests
    from src.data_watcher import _with_retry
    attempts = []

    def func():
        attempts.append(1)
        if len(attempts) < 3:
            raise requests.exceptions.Timeout("timed out")
        return {"value": 1}

    result = _with_retry(func, retries=3, base_wait=0, label="test")
    assert result == {"value": 1}
    assert len(attempts) == 3


def test_with_retry_retries_on_connection_error():
    import requests
    from src.data_watcher import _with_retry
    attempts = []

    def func():
        attempts.append(1)
        if len(attempts) < 2:
            raise requests.exceptions.ConnectionError("refused")
        return {"ok": True}

    result = _with_retry(func, retries=2, base_wait=0, label="test")
    assert result == {"ok": True}
    assert len(attempts) == 2


# ── get_with_fallback ─────────────────────────────────────────────────────────

def test_get_with_fallback_uses_first_successful_fetcher():
    from src.data_watcher import get_with_fallback

    fetcher_a = lambda: {"value": 1, "source": "A"}
    fetcher_b = lambda: {"value": 2, "source": "B"}

    result = get_with_fallback("test_key", [fetcher_a, fetcher_b])
    assert result["source"] == "A"


def test_get_with_fallback_skips_failed_fetcher():
    from src.data_watcher import get_with_fallback

    fetcher_a = lambda: None
    fetcher_b = lambda: {"value": 2, "source": "B"}

    result = get_with_fallback("test_key", [fetcher_a, fetcher_b])
    assert result["source"] == "B"


def test_get_with_fallback_uses_cache_when_all_fetchers_fail(tmp_path, monkeypatch):
    from src import data_watcher

    monkeypatch.setattr(data_watcher, "CACHE_DIR", tmp_path)

    # Pre-populate cache
    import json
    from datetime import datetime, timezone
    cache_file = tmp_path / "my_key.json"
    ts = datetime.now(timezone.utc).isoformat()
    cache_file.write_text(json.dumps({"value": 123.4, "timestamp": ts}), encoding="utf-8")

    result = data_watcher.get_with_fallback("my_key", [lambda: None])
    assert result["source"] == "cache"
    assert result["value"] == 123.4


def test_get_with_fallback_returns_missing_data_when_everything_fails():
    from src.data_watcher import get_with_fallback
    from src.config import MISSING_DATA

    result = get_with_fallback("nonexistent_key_xyz123", [lambda: None])
    assert result["value"] == MISSING_DATA
    assert result["source"] == "none"
