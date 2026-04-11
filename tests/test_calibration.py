"""Unit tests for src/calibration.py — pure logic, no API calls."""
from __future__ import annotations

import pytest


# ── compute_brier_score ───────────────────────────────────────────────────

def test_brier_perfect_high_confidence():
    """High confidence + always correct → near-zero Brier."""
    from src.calibration import compute_brier_score
    preds = [
        {"confidence": 0.9, "direction": "up", "result": "correct"}
        for _ in range(10)
    ]
    score = compute_brier_score(preds)
    assert score is not None
    assert score < 0.02  # (0.9-1)^2 = 0.01


def test_brier_worst_case():
    """High confidence + always wrong → near-1 Brier."""
    from src.calibration import compute_brier_score
    preds = [
        {"confidence": 0.9, "direction": "up", "result": "wrong"}
        for _ in range(10)
    ]
    score = compute_brier_score(preds)
    assert score is not None
    assert score > 0.8  # (0.9-0)^2 = 0.81


def test_brier_down_direction_not_flipped():
    """C2 修正驗證：down 方向的信心值不應被翻轉。

    若模型對 "down" 方向有 0.8 信心且預測正確，
    Brier = (0.8-1)^2 = 0.04（好），而非舊版 (0.2-1)^2 = 0.64（差）。
    """
    from src.calibration import compute_brier_score
    preds = [
        {"confidence": 0.8, "direction": "down", "result": "correct"}
        for _ in range(10)
    ]
    score = compute_brier_score(preds)
    assert score is not None
    assert score < 0.05, f"Expected low Brier for correct high-conf down prediction, got {score}"


def test_brier_returns_none_below_10():
    """少於 10 筆完成記錄時回傳 None。"""
    from src.calibration import compute_brier_score
    preds = [{"confidence": 0.7, "direction": "up", "result": "correct"} for _ in range(9)]
    assert compute_brier_score(preds) is None


def test_brier_mixed():
    """Brier Score 在 0-1 之間，混合正確/錯誤。"""
    from src.calibration import compute_brier_score
    preds = (
        [{"confidence": 0.7, "direction": "up", "result": "correct"}] * 5 +
        [{"confidence": 0.7, "direction": "up", "result": "wrong"}] * 5
    )
    score = compute_brier_score(preds)
    assert score is not None
    assert 0 <= score <= 1


# ── _normalize_asset ──────────────────────────────────────────────────────

def test_normalize_chinese_gold():
    from src.calibration import _normalize_asset
    assert _normalize_asset("黃金") == "gold"
    assert _normalize_asset("黄金") == "gold"


def test_normalize_english_alias():
    from src.calibration import _normalize_asset
    assert _normalize_asset("xau") == "gold"
    assert _normalize_asset("crude") == "brent"


def test_normalize_complex_name():
    from src.calibration import _normalize_asset
    # 前綴匹配
    assert _normalize_asset("brent原油") == "brent"


def test_normalize_passthrough():
    from src.calibration import _normalize_asset
    assert _normalize_asset("unknown_asset") == "unknown_asset"


def test_normalize_empty():
    from src.calibration import _normalize_asset
    assert _normalize_asset("") == ""


# ── _find_most_recent_pending_date ────────────────────────────────────────

def test_find_pending_date_basic():
    from src.calibration import _find_most_recent_pending_date
    preds = [
        {"date": "2026-04-08", "result": None},
        {"date": "2026-04-09", "result": "correct"},
    ]
    result = _find_most_recent_pending_date(preds, before="2026-04-10")
    assert result == "2026-04-08"


def test_find_pending_date_none_when_all_filled():
    from src.calibration import _find_most_recent_pending_date
    preds = [{"date": "2026-04-09", "result": "correct"}]
    assert _find_most_recent_pending_date(preds, before="2026-04-10") is None


def test_find_pending_date_beyond_7_day_cutoff():
    from src.calibration import _find_most_recent_pending_date
    preds = [{"date": "2026-03-01", "result": None}]  # 40 days ago
    assert _find_most_recent_pending_date(preds, before="2026-04-10") is None


# ── adjust_confidence ─────────────────────────────────────────────────────

def test_adjust_confidence_cold_start(tmp_path, monkeypatch):
    """冷啟動（< 30 筆）：只做 coverage penalty，不做三重校準。"""
    import src.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "CALIBRATION_FILE", tmp_path / "calibration.json")
    monkeypatch.setattr(cal_mod, "SYSTEM_DIR", tmp_path)

    adjusted = cal_mod.adjust_confidence(0.8, "gold", "risk_on", data_coverage=1.0)
    assert abs(adjusted - 0.8) < 0.01  # coverage=1.0 → penalty=1.0, no change


def test_adjust_confidence_coverage_penalty(tmp_path, monkeypatch):
    """Coverage 不足 → 信心下調。"""
    import src.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "CALIBRATION_FILE", tmp_path / "calibration.json")
    monkeypatch.setattr(cal_mod, "SYSTEM_DIR", tmp_path)

    adjusted = cal_mod.adjust_confidence(0.8, "gold", "risk_on", data_coverage=0.5)
    expected = 0.8 * (1 - 0.5 * 0.3)  # = 0.8 * 0.85 = 0.68
    assert abs(adjusted - expected) < 0.01


def test_adjust_confidence_clamped(tmp_path, monkeypatch):
    """結果不超出 [MIN_CONFIDENCE, MAX_CONFIDENCE]。"""
    import src.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "CALIBRATION_FILE", tmp_path / "calibration.json")
    monkeypatch.setattr(cal_mod, "SYSTEM_DIR", tmp_path)

    assert cal_mod.adjust_confidence(0.01, "gold", "risk_on", 1.0) >= cal_mod.MIN_CONFIDENCE
    assert cal_mod.adjust_confidence(0.99, "gold", "risk_on", 1.0) <= cal_mod.MAX_CONFIDENCE
