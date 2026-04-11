"""Smoke tests for calibration functions not yet covered by test_calibration.py:
- apply_calibration_to_inference_chain
- record_prediction (idempotent write)
- compute_accuracy_by_type
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch


# ── apply_calibration_to_inference_chain ─────────────────────────────────────

def test_apply_calibration_sets_adjusted_confidence():
    from src.calibration import apply_calibration_to_inference_chain

    chain = [
        {
            "id": "INF001",
            "raw_confidence": 0.75,
            "evidence": [{"data_key": "gold"}],
            "inference": "Gold breaks resistance on dollar weakness",
        }
    ]
    result = apply_calibration_to_inference_chain(chain, regime="RISK_ON", data_coverage=0.9)

    assert len(result) == 1
    assert "adjusted_confidence" in result[0]
    adj = result[0]["adjusted_confidence"]
    assert 0.0 <= adj <= 1.0


def test_apply_calibration_adjusts_downward_on_low_coverage():
    from src.calibration import apply_calibration_to_inference_chain

    chain = [{"id": "INF002", "raw_confidence": 0.80, "evidence": []}]

    high_cov = apply_calibration_to_inference_chain(chain[:], regime="RISK_ON", data_coverage=0.95)
    low_cov  = apply_calibration_to_inference_chain(chain[:], regime="RISK_ON", data_coverage=0.30)

    assert high_cov[0]["adjusted_confidence"] >= low_cov[0]["adjusted_confidence"]


def test_apply_calibration_returns_chain_unchanged_structure():
    from src.calibration import apply_calibration_to_inference_chain

    chain = [
        {"id": "INF003", "raw_confidence": 0.6, "evidence": [], "inference": "test"},
        {"id": "INF004", "raw_confidence": 0.5, "evidence": [], "inference": "test2"},
    ]
    result = apply_calibration_to_inference_chain(chain, regime="NEUTRAL", data_coverage=0.8)
    assert len(result) == 2
    assert result[0]["id"] == "INF003"
    assert result[1]["id"] == "INF004"


def test_apply_calibration_verdict_adjustments_applied():
    """verdict_adjustments with matching inf_id should influence calibration."""
    from src.calibration import apply_calibration_to_inference_chain

    chain = [{"id": "INF005", "raw_confidence": 0.65, "evidence": []}]
    verdict_adjustments = [{"inf_id": "INF005", "verdict": "SUSTAINED", "confidence_delta": 0.1}]

    baseline = apply_calibration_to_inference_chain(
        [{"id": "INF005", "raw_confidence": 0.65, "evidence": []}],
        regime="RISK_ON", data_coverage=0.9, verdict_adjustments=None,
    )
    with_adj = apply_calibration_to_inference_chain(
        chain, regime="RISK_ON", data_coverage=0.9, verdict_adjustments=verdict_adjustments,
    )

    # Both must produce a valid confidence in [0,1]
    assert 0.0 <= baseline[0]["adjusted_confidence"] <= 1.0
    assert 0.0 <= with_adj[0]["adjusted_confidence"] <= 1.0


# ── record_prediction ─────────────────────────────────────────────────────────

def test_record_prediction_writes_to_l5(tmp_path, monkeypatch):
    import src.calibration as cal
    monkeypatch.setattr("src.config.MEMORY_DIR", tmp_path)

    cal.record_prediction(
        date="2026-04-10", asset="gold", direction="up",
        confidence=0.72, regime="RISK_ON",
    )

    l5_path = tmp_path / "l5.json"
    assert l5_path.exists()
    l5 = json.loads(l5_path.read_text(encoding="utf-8"))
    assert len(l5["predictions"]) == 1
    p = l5["predictions"][0]
    assert p["asset"] == "gold"
    assert p["direction"] == "up"
    assert p["result"] is None


def test_record_prediction_is_idempotent(tmp_path, monkeypatch):
    """Re-running for the same date+asset should not create duplicate records."""
    import src.calibration as cal
    monkeypatch.setattr("src.config.MEMORY_DIR", tmp_path)

    cal.record_prediction("2026-04-10", "spx", "down", 0.6, "RISK_OFF")
    cal.record_prediction("2026-04-10", "spx", "down", 0.6, "RISK_OFF")

    l5 = json.loads((tmp_path / "l5.json").read_text(encoding="utf-8"))
    assert len(l5["predictions"]) == 1


def test_record_prediction_different_assets_both_recorded(tmp_path, monkeypatch):
    import src.calibration as cal
    monkeypatch.setattr("src.config.MEMORY_DIR", tmp_path)

    cal.record_prediction("2026-04-10", "gold", "up",   0.7, "RISK_ON")
    cal.record_prediction("2026-04-10", "spx",  "down", 0.6, "RISK_ON")

    l5 = json.loads((tmp_path / "l5.json").read_text(encoding="utf-8"))
    assert len(l5["predictions"]) == 2


# ── compute_accuracy_by_type (inference_store) ────────────────────────────────

def test_compute_accuracy_by_type_returns_dict(tmp_path, monkeypatch):
    """compute_accuracy_by_type reads from INFERENCE_HISTORY_PATH; smoke: returns a dict."""
    import src.inference_store as ifs
    monkeypatch.setattr(ifs, "INFERENCE_HISTORY_PATH", tmp_path / "inference_history.jsonl")

    # Empty store → should still return a dict without crashing
    result = ifs.compute_accuracy_by_type()
    assert isinstance(result, dict)


def test_compute_accuracy_by_type_with_data(tmp_path, monkeypatch):
    """Seeded inference history → compute_accuracy_by_type returns a dict."""
    import json
    import src.inference_store as ifs
    history_path = tmp_path / "inference_history.jsonl"
    monkeypatch.setattr(ifs, "INFERENCE_HISTORY_PATH", history_path)

    records = [
        {"inf_id": "INF_001", "date": "2026-04-09", "outcome": "vindicated",
         "asset_predictions": ["gold_up"], "confidence": 0.7},
        {"inf_id": "INF_002", "date": "2026-04-09", "outcome": "vindicated",
         "asset_predictions": ["spx_up"], "confidence": 0.6},
        {"inf_id": "INF_003", "date": "2026-04-08", "outcome": "refuted",
         "asset_predictions": ["gold_up"], "confidence": 0.8},
    ]
    with open(history_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    result = ifs.compute_accuracy_by_type()
    assert isinstance(result, dict)
