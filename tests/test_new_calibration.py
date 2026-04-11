"""Unit tests for new calibration functions added in v10.1:
- fill_inference_outcomes
- _compute_da_premortem_stats
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ── fill_inference_outcomes ──────────────────────────────────────────────────

def test_fill_inference_outcomes_vindicated(tmp_path, monkeypatch):
    """Single prediction matching market direction → vindicated."""
    from src.calibration import fill_inference_outcomes

    mock_records = [
        {
            "inf_id": "INF_20260409_001",
            "date": "2026-04-09",
            "outcome": None,
            "asset_predictions": ["gold_up"],
        }
    ]

    captured_outcomes = {}

    def mock_load_all():
        return mock_records

    def mock_fill_outcomes(date_str, outcomes):
        captured_outcomes.update(outcomes)

    with patch("src.inference_store.load_all", mock_load_all), \
         patch("src.inference_store.fill_outcomes", mock_fill_outcomes):

        data_package = {
            "gold": {"change_pct": 1.5, "source": "yfinance"},
        }
        fill_inference_outcomes(data_package, "2026-04-10")

    assert captured_outcomes.get("INF_20260409_001") == "vindicated"


def test_fill_inference_outcomes_refuted(tmp_path, monkeypatch):
    """Prediction opposite to market move → refuted."""
    from src.calibration import fill_inference_outcomes

    mock_records = [
        {
            "inf_id": "INF_20260409_002",
            "date": "2026-04-09",
            "outcome": None,
            "asset_predictions": ["spx_down"],
        }
    ]
    captured = {}

    with patch("src.inference_store.load_all", return_value=mock_records), \
         patch("src.inference_store.fill_outcomes", lambda d, o: captured.update(o)):

        data_package = {
            "spx": {"change_pct": 0.8},  # up, but prediction was down
        }
        fill_inference_outcomes(data_package, "2026-04-10")

    assert captured.get("INF_20260409_002") == "refuted"


def test_fill_inference_outcomes_inconclusive_no_data():
    """No matching asset in data_package → inconclusive."""
    from src.calibration import fill_inference_outcomes

    mock_records = [
        {
            "inf_id": "INF_20260409_003",
            "date": "2026-04-09",
            "outcome": None,
            "asset_predictions": ["bdi_up"],
        }
    ]
    captured = {}

    with patch("src.inference_store.load_all", return_value=mock_records), \
         patch("src.inference_store.fill_outcomes", lambda d, o: captured.update(o)):

        data_package = {}  # bdi not present
        fill_inference_outcomes(data_package, "2026-04-10")

    assert captured.get("INF_20260409_003") == "inconclusive"


def test_fill_inference_outcomes_skips_already_filled():
    """Records with outcome already set should not be re-evaluated."""
    from src.calibration import fill_inference_outcomes

    mock_records = [
        {
            "inf_id": "INF_20260409_004",
            "date": "2026-04-09",
            "outcome": "vindicated",  # already filled
            "asset_predictions": ["gold_down"],
        }
    ]
    captured = {}

    with patch("src.inference_store.load_all", return_value=mock_records), \
         patch("src.inference_store.fill_outcomes", lambda d, o: captured.update(o)):

        data_package = {"gold": {"change_pct": -0.5}}
        fill_inference_outcomes(data_package, "2026-04-10")

    assert "INF_20260409_004" not in captured  # should not overwrite


def test_fill_inference_outcomes_no_pending():
    """All inferences already have outcomes → fill_outcomes never called."""
    from src.calibration import fill_inference_outcomes

    mock_records = [
        {"inf_id": "INF_001", "date": "2026-04-09", "outcome": "refuted", "asset_predictions": ["spx_up"]},
    ]
    fill_called = []

    with patch("src.inference_store.load_all", return_value=mock_records), \
         patch("src.inference_store.fill_outcomes", lambda d, o: fill_called.append(o)):

        fill_inference_outcomes({"spx": {"change_pct": 1.0}}, "2026-04-10")

    assert fill_called == []


# ── _compute_da_premortem_stats ──────────────────────────────────────────────

def test_compute_da_premortem_stats_basic(tmp_path, monkeypatch):
    """Scan snapshots and compute sustained rate correctly."""
    import src.calibration as cal
    monkeypatch.setattr("src.config.SNAPSHOTS_DIR", tmp_path)

    snap = {
        "opus_verdicts": {
            "attack_verdicts": [
                {"verdict": "SUSTAINED", "attack": "A1"},
                {"verdict": "OVERRULED", "attack": "A2"},
                {"verdict": "SUSTAINED", "attack": "A3"},
            ]
        },
        "premortem_scenarios": [{"id": "S1"}, {"id": "S2"}],
    }
    (tmp_path / "2026-04-09.json").write_text(json.dumps(snap), encoding="utf-8")

    result = cal._compute_da_premortem_stats(window_days=30)
    assert result["da_total_attacks"] == 3
    assert result["da_sustained_attacks"] == 2
    assert abs(result["da_sustained_rate"] - 2 / 3) < 0.001
    assert result["premortem_scenario_count"] == 2


def test_compute_da_premortem_stats_no_snapshots(tmp_path, monkeypatch):
    """Empty directory → zero-attack stats dict."""
    import src.calibration as cal
    monkeypatch.setattr("src.config.SNAPSHOTS_DIR", tmp_path)

    result = cal._compute_da_premortem_stats(window_days=30)
    # Directory exists but is empty → returns stats dict with all zeros
    assert result["da_total_attacks"] == 0
    assert result["da_sustained_rate"] is None
    assert result["premortem_scenario_count"] == 0


def test_compute_da_premortem_stats_zero_attacks(tmp_path, monkeypatch):
    """Snapshot with no attack_verdicts → sustained_rate is None."""
    import src.calibration as cal
    monkeypatch.setattr("src.config.SNAPSHOTS_DIR", tmp_path)

    snap = {"opus_verdicts": {"attack_verdicts": []}, "premortem_scenarios": []}
    (tmp_path / "2026-04-09.json").write_text(json.dumps(snap), encoding="utf-8")

    result = cal._compute_da_premortem_stats(window_days=30)
    assert result["da_total_attacks"] == 0
    assert result["da_sustained_rate"] is None


def test_compute_da_premortem_stats_skips_old_snapshots(tmp_path, monkeypatch):
    """Snapshots older than window_days are excluded."""
    import src.calibration as cal
    monkeypatch.setattr("src.config.SNAPSHOTS_DIR", tmp_path)

    old_snap = {
        "opus_verdicts": {
            "attack_verdicts": [{"verdict": "SUSTAINED"}, {"verdict": "SUSTAINED"}]
        },
        "premortem_scenarios": [],
    }
    recent_snap = {
        "opus_verdicts": {"attack_verdicts": [{"verdict": "OVERRULED"}]},
        "premortem_scenarios": [],
    }
    # Old: 60 days ago
    (tmp_path / "2026-02-09.json").write_text(json.dumps(old_snap), encoding="utf-8")
    # Recent: 1 day ago
    (tmp_path / "2026-04-09.json").write_text(json.dumps(recent_snap), encoding="utf-8")

    result = cal._compute_da_premortem_stats(window_days=30)
    assert result["da_total_attacks"] == 1  # only recent snapshot
    assert result["da_sustained_attacks"] == 0
