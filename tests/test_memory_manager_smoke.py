"""Smoke tests for src/memory_manager.py — key functions, no network/API calls."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from datetime import datetime, timezone


# ── Shared minimal stubs ──────────────────────────────────────────────────────

def _minimal_data_package():
    return {
        "gold": {"price": 3100.0, "change_pct": 0.5, "source": "yfinance", "quality": "confirmed"},
        "spx":  {"price": 5500.0, "change_pct": -0.3, "source": "yfinance", "quality": "confirmed"},
    }

def _minimal_analysis():
    return {
        "regime": {"current": "RISK_ON", "day_count": 3},
        "core_tension": "Fed vs growth",
        "inference_chain": [],
        "thesis_updates": [],
        "compass": [
            {"asset": "gold", "direction": "up", "raw_confidence": 0.72, "adjusted_confidence": 0.68},
        ],
    }

def _minimal_verdict():
    return {"attack_verdicts": [], "final_conclusions_stand": True}

def _minimal_report():
    return {
        "metadata": {"model": "sonnet", "generated_at": "2026-04-10"},
        "sections": {
            "watchboard": (
                "| 儀表板 | 目前讀數 | 觸發條件 | 若觸發代表什麼 |\n"
                "| --- | --- | --- | --- |\n"
                "| VIX | {{confirmed:22.0}} | 升破 25 | 低波動失效 |"
            ),
            "causal_graph": "- gold → 測試機制 → gold_up",
        },
        "_quality_assessment": {"score": 88, "grade": "publishable"},
    }

def _minimal_tgri():
    return {"score": 0.45, "label": "ELEVATED"}

def _minimal_citation():
    return {"integrity_score": 0.9, "flags": []}


# ── build_daily_snapshot ──────────────────────────────────────────────────────

def test_build_daily_snapshot_returns_required_keys():
    from src.memory_manager import build_daily_snapshot

    snap = build_daily_snapshot(
        today_str="2026-04-10",
        data_package=_minimal_data_package(),
        quant_package={},
        analysis=_minimal_analysis(),
        verdict=_minimal_verdict(),
        report=_minimal_report(),
        tgri=_minimal_tgri(),
        coverage_score=0.88,
        citation_result=_minimal_citation(),
    )

    assert snap["date"] == "2026-04-10"
    assert snap["metadata"]["coverage_score"] == 0.88
    assert snap["metadata"]["regime"] == "RISK_ON"
    assert "market_data" in snap
    assert "opus_verdicts" in snap
    assert "scorecard" in snap
    assert snap["premortem_scenarios"] == []
    assert snap["metadata"]["report_quality"]["score"] == 88
    assert snap["watchboard"]["items"][0]["data_key"] == "vix"
    assert snap["research_ledger"]["schema_version"] == "research-ledger-v1"


def test_build_daily_snapshot_includes_premortem_scenarios():
    from src.memory_manager import build_daily_snapshot

    premortem = {"scenarios": [{"id": "S1", "title": "Recession trigger"}, {"id": "S2", "title": "Credit crunch"}]}

    snap = build_daily_snapshot(
        today_str="2026-04-10",
        data_package={},
        quant_package={},
        analysis=_minimal_analysis(),
        verdict=_minimal_verdict(),
        report=_minimal_report(),
        tgri=_minimal_tgri(),
        coverage_score=0.9,
        citation_result=_minimal_citation(),
        premortem_result=premortem,
    )

    assert len(snap["premortem_scenarios"]) == 2


# ── _extract_scorecard ────────────────────────────────────────────────────────

def test_extract_scorecard_maps_compass_to_hml():
    from src.memory_manager import _extract_scorecard

    analysis = {
        "compass": [
            {"asset": "gold", "direction": "up", "adjusted_confidence": 0.75},
            {"asset": "spx",  "direction": "down", "adjusted_confidence": 0.55},
            {"asset": "vix",  "direction": "up", "adjusted_confidence": 0.40},
        ]
    }
    sc = _extract_scorecard(analysis)

    assert sc["gold"]["confidence"] == "H"
    assert sc["spx"]["confidence"] == "M"
    assert sc["vix"]["confidence"] == "L"
    assert sc["gold"]["direction"] == "up"
    assert sc["gold"]["result"] is None


def test_extract_scorecard_empty_compass():
    from src.memory_manager import _extract_scorecard

    sc = _extract_scorecard({"compass": []})
    assert sc == {}


# ── save_daily_snapshot ───────────────────────────────────────────────────────

def test_save_daily_snapshot_writes_file(tmp_path, monkeypatch):
    import src.memory_manager as mm
    monkeypatch.setattr(mm, "SNAPSHOTS_DIR", tmp_path)

    snap = {"date": "2026-04-10", "metadata": {"coverage_score": 0.88}}
    mm.save_daily_snapshot(snap, "2026-04-10")

    out = tmp_path / "2026-04-10.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["metadata"]["coverage_score"] == 0.88


def test_save_daily_snapshot_does_not_overwrite_with_zero_coverage(tmp_path, monkeypatch):
    """If existing snapshot has coverage>0, a new snapshot with coverage=0 should be skipped."""
    import src.memory_manager as mm
    monkeypatch.setattr(mm, "SNAPSHOTS_DIR", tmp_path)

    good_snap = {"date": "2026-04-10", "metadata": {"coverage_score": 0.85}}
    (tmp_path / "2026-04-10.json").write_text(json.dumps(good_snap), encoding="utf-8")

    bad_snap = {"date": "2026-04-10", "metadata": {"coverage_score": 0.0}}
    mm.save_daily_snapshot(bad_snap, "2026-04-10")

    data = json.loads((tmp_path / "2026-04-10.json").read_text(encoding="utf-8"))
    assert data["metadata"]["coverage_score"] == 0.85  # unchanged


def test_save_daily_snapshot_overwrites_zero_with_good(tmp_path, monkeypatch):
    """Existing snapshot with coverage=0 can be replaced by a good one."""
    import src.memory_manager as mm
    monkeypatch.setattr(mm, "SNAPSHOTS_DIR", tmp_path)

    zero_snap = {"date": "2026-04-10", "metadata": {"coverage_score": 0.0}}
    (tmp_path / "2026-04-10.json").write_text(json.dumps(zero_snap), encoding="utf-8")

    good_snap = {"date": "2026-04-10", "metadata": {"coverage_score": 0.9}}
    mm.save_daily_snapshot(good_snap, "2026-04-10")

    data = json.loads((tmp_path / "2026-04-10.json").read_text(encoding="utf-8"))
    assert data["metadata"]["coverage_score"] == 0.9


# ── update_regime_history ─────────────────────────────────────────────────────

def test_update_regime_history_creates_file(tmp_path, monkeypatch):
    import src.memory_manager as mm
    monkeypatch.setattr(mm, "TIMESERIES_DIR", tmp_path)

    mm.update_regime_history("RISK_ON", 5, "2026-04-10")

    path = tmp_path / "regime_history.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["history"][-1] == {"date": "2026-04-10", "regime": "RISK_ON", "day": 5}


def test_update_regime_history_deduplicates_same_date(tmp_path, monkeypatch):
    import src.memory_manager as mm
    monkeypatch.setattr(mm, "TIMESERIES_DIR", tmp_path)

    mm.update_regime_history("RISK_ON", 5, "2026-04-10")
    mm.update_regime_history("RISK_OFF", 1, "2026-04-10")  # re-run same day

    data = json.loads((tmp_path / "regime_history.json").read_text(encoding="utf-8"))
    entries_for_date = [h for h in data["history"] if h["date"] == "2026-04-10"]
    assert len(entries_for_date) == 1
    assert entries_for_date[0]["regime"] == "RISK_OFF"  # keep latest


def test_update_regime_history_appends_multiple_dates(tmp_path, monkeypatch):
    import src.memory_manager as mm
    monkeypatch.setattr(mm, "TIMESERIES_DIR", tmp_path)

    mm.update_regime_history("RISK_ON", 1, "2026-04-08")
    mm.update_regime_history("RISK_ON", 2, "2026-04-09")
    mm.update_regime_history("RISK_OFF", 1, "2026-04-10")

    data = json.loads((tmp_path / "regime_history.json").read_text(encoding="utf-8"))
    assert len(data["history"]) == 3


# ── sync_theses_dir_to_l3 ─────────────────────────────────────────────────────

def test_sync_theses_dir_to_l3_creates_l3(tmp_path, monkeypatch):
    import src.memory_manager as mm
    monkeypatch.setattr(mm, "MEMORY_DIR", tmp_path)
    monkeypatch.setattr(mm, "THESES_DIR", tmp_path / "theses")

    active_dir = tmp_path / "theses" / "active"
    active_dir.mkdir(parents=True)

    thesis = {
        "id": "T001", "status": "active", "title": "Gold breaks 3000",
        "created_date": "2026-04-01",
    }
    (active_dir / "T001_gold.json").write_text(json.dumps(thesis), encoding="utf-8")

    mm.sync_theses_dir_to_l3("2026-04-10")

    l3_path = tmp_path / "l3.json"
    assert l3_path.exists()
    l3 = json.loads(l3_path.read_text(encoding="utf-8"))
    assert l3["thesis_count"] == 1
    assert l3["active_theses"][0]["id"] == "T001"


def test_sync_theses_dir_to_l3_empty_dir_clears_nothing(tmp_path, monkeypatch):
    """Empty active dir → l3 reflects 0 theses."""
    import src.memory_manager as mm
    monkeypatch.setattr(mm, "MEMORY_DIR", tmp_path)
    monkeypatch.setattr(mm, "THESES_DIR", tmp_path / "theses")
    (tmp_path / "theses" / "active").mkdir(parents=True)

    mm.sync_theses_dir_to_l3("2026-04-10")

    l3 = json.loads((tmp_path / "l3.json").read_text(encoding="utf-8"))
    assert l3["thesis_count"] == 0


# ── update_market_timeseries ──────────────────────────────────────────────────

def test_update_market_timeseries_creates_parquet(tmp_path, monkeypatch):
    import src.memory_manager as mm
    monkeypatch.setattr(mm, "TIMESERIES_DIR", tmp_path)

    mm.update_market_timeseries(_minimal_data_package(), "2026-04-10")

    parquet_path = tmp_path / "market.parquet"
    assert parquet_path.exists()


def test_update_market_timeseries_deduplicates_date(tmp_path, monkeypatch):
    import pandas as pd
    import src.memory_manager as mm
    monkeypatch.setattr(mm, "TIMESERIES_DIR", tmp_path)

    mm.update_market_timeseries({"gold": {"price": 3100.0}}, "2026-04-10")
    mm.update_market_timeseries({"gold": {"price": 3200.0}}, "2026-04-10")  # re-run same day

    df = pd.read_parquet(tmp_path / "market.parquet")
    assert len(df) == 1
    assert df["gold"].iloc[0] == 3200.0  # keep latest
