"""Orchestrator smoke test — mock 所有外部依賴（LLM / 資料源 / Notion），
驗證 pipeline 骨架可以走完全程而不崩潰。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── 最小假資料包 ─────────────────────────────────────────────────────────────

FAKE_DATA_PKG = {
    "gold": {"value": 3200.0, "change_pct": 0.5, "quality": "confirmed",
             "source": "yfinance", "timestamp": "2026-01-01T00:00:00Z"},
    "spx":  {"value": 5200.0, "change_pct": -0.3, "quality": "confirmed",
             "source": "yfinance", "timestamp": "2026-01-01T00:00:00Z"},
    "vix":  {"value": 22.0,   "change_pct": 1.0,  "quality": "confirmed",
             "source": "yfinance", "timestamp": "2026-01-01T00:00:00Z"},
}

FAKE_QUANT_PKG = {
    "zscore_alerts": {},
    "correlation_matrix_30d": {},
    "regime_probability": {"政策過渡": 0.7},
    "volatility_30d": {},
    "granger_causality": [],
}

FAKE_ANALYSIS = {
    "regime": {"current": "政策過渡", "confidence": 0.7, "day_count": 1,
               "supporting_data": [], "contrary_signals": []},
    "core_tension": "測試核心張力",
    "inference_chain": [],
    "thesis_updates": [],
    "new_thesis_candidates": [],
    "compass": [],
    "question_for_devil": "測試問題",
    "data_gaps_affecting_analysis": [],
    "raw_confidence_adjustments": {},
}

FAKE_VERDICT = {
    "attack_verdicts": [],
    "final_conclusions_stand": True,
    "risk_officer_notes": "smoke test",
    "factual_errors": [],
    "confidence_adjustments": [],
    "narrative_verdict": "pass",
}

FAKE_REPORT = {
    "sections": {"headline": "smoke test headline"},
    "metadata": {"coverage_score": 0.8},
}


# ── 每個 step 的 mock 回傳值 ─────────────────────────────────────────────────

def _mock_run_scheduler():
    return {"fomc_days_out": 10, "nfp_days_out": 5}


def _mock_run_data_watcher(**kwargs):
    return dict(FAKE_DATA_PKG)


def _mock_run_quant_engine(data_pkg):
    return dict(FAKE_QUANT_PKG)


def _mock_run_sentiment_watcher(**kwargs):
    return {"headline": "neutral", "triggers": []}


def _mock_run_scholar(**kwargs):
    return {
        "summary": "smoke test geo",
        "tgri_components": {},
        "tgri": {"score": 28.0, "trend": "stable", "percentile": "50%ile", "components": {}},
    }


def _mock_run_historian(**kwargs):
    return {"analogs": [], "top_analog": None}


def _mock_run_assembler(**kwargs):
    return {
        "packages": {
            "data_package": FAKE_DATA_PKG,
            "quant_package": FAKE_QUANT_PKG,
            "calendar_package": {},
        },
        "coverage_score": 0.8,
        "coverage_warning": "",
        "all_gaps": [],
        "material_density": {},
        "token_allocation": {"data_total": 100, "memory_total": 50, "data_to_memory_ratio": 2.0},
    }


def _mock_run_analyst(ctx, **kwargs):
    return dict(FAKE_ANALYSIS)


def _mock_run_devils_advocate(dp, **kwargs):
    return {"attacks": []}


def _mock_run_premortem(theses, dp, **kwargs):
    return {"scenarios": []}


def _mock_verify_chain(**kwargs):
    return {"integrity_score": 1.0, "pass": True, "flags": []}


def _mock_run_risk_officer(**kwargs):
    return dict(FAKE_VERDICT)


def _mock_run_narrator(**kwargs):
    return dict(FAKE_REPORT)


def _mock_run_memory_manager(**kwargs):
    return {}


def _mock_publish_to_notion(**kwargs):
    return "https://notion.so/fake-url"


def _mock_send_line(**kwargs):
    return True


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _chdir_to_project(tmp_path, monkeypatch):
    """確保 tmp memory dir 不污染真實 memory/。"""
    import src.config as cfg
    monkeypatch.setattr(cfg, "SNAPSHOTS_DIR", tmp_path / "daily_snapshots")
    monkeypatch.setattr(cfg, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(cfg, "TIMESERIES_DIR", tmp_path / "timeseries")
    monkeypatch.setattr(cfg, "THESES_DIR", tmp_path / "theses")
    monkeypatch.setattr(cfg, "SYSTEM_DIR", tmp_path / "system")
    (tmp_path / "memory").mkdir(parents=True)
    (tmp_path / "theses" / "active").mkdir(parents=True)
    (tmp_path / "theses" / "proposed").mkdir(parents=True)
    (tmp_path / "theses" / "closed").mkdir(parents=True)
    (tmp_path / "theses" / "archived").mkdir(parents=True)


def test_pipeline_returns_date_and_steps(monkeypatch):
    """Pipeline 在全 mock 環境下跑完，results 包含 date 和至少 10 個 step。"""
    _apply_all_mocks(monkeypatch)

    from src.orchestrator import run_daily_pipeline
    result = run_daily_pipeline(force=True)

    assert "date" in result
    assert "steps" in result
    assert len(result["steps"]) >= 10, f"Too few steps: {list(result['steps'].keys())}"


def test_pipeline_skips_when_snapshot_exists(monkeypatch):
    """不 force 且快照已存在時應回傳 skipped。"""
    import src.orchestrator as orch

    # 製造「今天已跑過」的快照
    today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    snap_dir = Path(__import__("src.config", fromlist=["SNAPSHOTS_DIR"]).SNAPSHOTS_DIR)
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / f"{today}.json").write_text(
        json.dumps({"metadata": {"coverage_score": 0.8}}), encoding="utf-8"
    )

    monkeypatch.setattr(orch, "check_missed_runs", lambda: False)
    result = orch.run_daily_pipeline(force=False)
    assert result.get("status") == "skipped"


def test_pipeline_analyst_failure_is_non_fatal(monkeypatch):
    """Analyst 失敗不應讓整個 pipeline 崩潰。"""
    _apply_all_mocks(monkeypatch)

    import src.agents.analyst as analyst_mod
    monkeypatch.setattr(analyst_mod, "run_analyst",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("analyst down")))

    from src.orchestrator import run_daily_pipeline
    result = run_daily_pipeline(force=True)
    # pipeline 不應 raise，analyst step 應標記 error
    assert "date" in result
    analyst_step = result.get("steps", {}).get("analyst", "")
    assert "error" in str(analyst_step).lower()


# ── Helper ───────────────────────────────────────────────────────────────────

def _apply_all_mocks(monkeypatch):
    """替換所有外部依賴為假實作。"""
    import src.scheduler as sched
    import src.data_watcher as dw
    import src.quant_engine as qe
    import src.sentiment_watcher as sw
    import src.scholar as scholar_mod
    import src.assembler as asm
    import src.agents.analyst as analyst_mod
    import src.agents.devils_advocate as da_mod
    import src.agents.premortem as pm_mod
    import src.agents.risk_officer as ro_mod
    import src.agents.narrator as nar_mod
    import src.memory_manager as mm_mod
    import src.orchestrator as orch

    monkeypatch.setattr(sched, "run_scheduler", _mock_run_scheduler)
    monkeypatch.setattr(dw, "run_data_watcher", _mock_run_data_watcher)
    monkeypatch.setattr(qe, "run_quant_engine", _mock_run_quant_engine)
    monkeypatch.setattr(sw, "run_sentiment_watcher",
                        lambda **kw: _mock_run_sentiment_watcher(**kw))
    monkeypatch.setattr(scholar_mod, "run_scholar",
                        lambda **kw: _mock_run_scholar(**kw))
    monkeypatch.setattr(asm, "run_assembler",
                        lambda **kw: _mock_run_assembler(**kw))
    monkeypatch.setattr(analyst_mod, "run_analyst", _mock_run_analyst)
    monkeypatch.setattr(da_mod, "run_devils_advocate", _mock_run_devils_advocate)
    monkeypatch.setattr(pm_mod, "run_premortem", _mock_run_premortem)
    monkeypatch.setattr(ro_mod, "run_risk_officer", _mock_run_risk_officer)
    monkeypatch.setattr(nar_mod, "run_narrator", _mock_run_narrator)
    monkeypatch.setattr(mm_mod, "run_memory_manager",
                        lambda **kw: _mock_run_memory_manager(**kw))
