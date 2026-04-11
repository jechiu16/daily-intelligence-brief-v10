"""Unit tests for src/telemetry.py — record_llm_call, aggregate_run_telemetry, LLMTimer."""
from __future__ import annotations

import json
import time
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def patch_telemetry_dir(tmp_path, monkeypatch):
    """Redirect telemetry writes to a temp directory."""
    import src.telemetry as tel
    tel_dir = tmp_path / "telemetry"
    tel_dir.mkdir()
    monkeypatch.setattr(tel, "TELEMETRY_DIR", tel_dir)
    monkeypatch.setattr(tel, "LLM_USAGE_PATH", tel_dir / "llm_usage.jsonl")
    return tel_dir


# ── record_llm_call ──────────────────────────────────────────────────────────

def test_record_llm_call_returns_dict():
    from src.telemetry import record_llm_call
    rec = record_llm_call(
        agent="analyst", model="claude-sonnet-4-6",
        input_tokens=1000, output_tokens=500,
        duration_s=1.23, run_id="run-abc", date="2026-04-10",
    )
    assert rec["agent"] == "analyst"
    assert rec["model"] == "claude-sonnet-4-6"
    assert rec["input_tokens"] == 1000
    assert rec["output_tokens"] == 500
    assert rec["total_tokens"] == 1500
    assert rec["duration_s"] == 1.23
    assert rec["cost_usd"] > 0  # Sonnet has a price


def test_record_llm_call_writes_jsonl(tmp_path, monkeypatch):
    import src.telemetry as tel
    from src.telemetry import record_llm_call

    record_llm_call(
        agent="narrator", model="claude-sonnet-4-6",
        input_tokens=2000, output_tokens=800,
        duration_s=2.5, run_id="run-xyz", date="2026-04-10",
    )

    lines = tel.LLM_USAGE_PATH.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["agent"] == "narrator"
    assert obj["run_id"] == "run-xyz"


def test_record_llm_call_cost_zero_for_unknown_model():
    from src.telemetry import record_llm_call
    rec = record_llm_call(
        agent="test", model="unknown-model-xyz",
        input_tokens=1000, output_tokens=1000,
        duration_s=1.0, run_id="r1", date="2026-04-10",
    )
    assert rec["cost_usd"] == 0.0


def test_record_llm_call_multiple_appends(tmp_path, monkeypatch):
    import src.telemetry as tel
    from src.telemetry import record_llm_call

    for i in range(3):
        record_llm_call(
            agent=f"agent_{i}", model="claude-sonnet-4-6",
            input_tokens=100, output_tokens=50,
            duration_s=0.5, run_id="run-multi", date="2026-04-10",
        )

    lines = tel.LLM_USAGE_PATH.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3


# ── aggregate_run_telemetry ───────────────────────────────────────────────────

def test_aggregate_run_telemetry_sums_correctly():
    import src.telemetry as tel
    from src.telemetry import record_llm_call, aggregate_run_telemetry

    run_id = "run-agg-test"
    record_llm_call("analyst", "claude-sonnet-4-6", 1000, 500, 1.0, run_id=run_id, date="2026-04-10")
    record_llm_call("narrator", "claude-sonnet-4-6", 2000, 800, 2.0, run_id=run_id, date="2026-04-10")

    result = aggregate_run_telemetry(run_id)
    assert result["total_calls"] == 2
    assert result["total_input_tokens"] == 3000
    assert result["total_output_tokens"] == 1300
    assert result["total_tokens"] == 4300
    assert result["total_cost_usd"] > 0
    assert "analyst" in result["by_agent"]
    assert "narrator" in result["by_agent"]


def test_aggregate_run_telemetry_excludes_other_runs():
    from src.telemetry import record_llm_call, aggregate_run_telemetry

    record_llm_call("a1", "claude-sonnet-4-6", 100, 50, 0.5, run_id="run-A", date="2026-04-10")
    record_llm_call("a2", "claude-sonnet-4-6", 200, 100, 1.0, run_id="run-B", date="2026-04-10")

    result = aggregate_run_telemetry("run-A")
    assert result["total_calls"] == 1
    assert result["by_agent"]["a1"]["calls"] == 1


def test_aggregate_run_telemetry_empty_returns_empty_dict():
    from src.telemetry import aggregate_run_telemetry

    result = aggregate_run_telemetry("nonexistent-run-id")
    assert result == {}


# ── LLMTimer ─────────────────────────────────────────────────────────────────

def test_llm_timer_measures_elapsed():
    from src.telemetry import LLMTimer

    with LLMTimer("test_agent", "claude-sonnet-4-6") as t:
        time.sleep(0.05)

    assert t.elapsed >= 0.04
    assert t.elapsed < 2.0  # sanity cap


def test_llm_timer_zero_before_exit():
    from src.telemetry import LLMTimer

    timer = LLMTimer("test_agent", "claude-sonnet-4-6")
    assert timer.elapsed == 0.0
    with timer:
        pass
    assert timer.elapsed >= 0.0
