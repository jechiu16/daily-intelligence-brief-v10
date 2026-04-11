from __future__ import annotations
"""Orchestrator — 主控，串起完整 pipeline。

v10.1: RunContext, tension_engine, inference_store 整合。
"""

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytz

_TW_TZ = pytz.timezone("Asia/Taipei")

from src.config import MISSING_DATA, PROJECT_ROOT, RUN_CONTEXT

# 設定 logging
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/dib_daily.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def check_missed_runs() -> bool:
    """補跑：若今天還沒跑，回傳 True。"""
    today_str = datetime.now(_TW_TZ).strftime("%Y-%m-%d")
    snapshot_path = PROJECT_ROOT / "memory" / "daily_snapshots" / f"{today_str}.json"
    if snapshot_path.exists():
        logger.info(f"Orchestrator: today's snapshot already exists ({today_str}), skipping")
        return False
    return True


def _load_memory_layers() -> dict:
    """載入 L2/L3/L5。"""
    from src.config import MEMORY_DIR
    layers = {}
    for name in ["l2", "l3", "l5"]:
        path = MEMORY_DIR / f"{name}.json"
        if path.exists():
            try:
                layers[name] = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                layers[name] = {}
        else:
            layers[name] = {}
    return layers


def _get_active_theses(memory_layers: dict) -> list[dict]:
    l3 = memory_layers.get("l3", {})
    return [t for t in l3.get("active_theses", []) if t.get("status") == "active"]


def run_daily_pipeline(force: bool = False) -> dict:
    """完整每日 pipeline。"""
    # 初始化 RunContext — 全 pipeline 統一時間戳
    RUN_CONTEXT.init()
    today_str = datetime.now(_TW_TZ).strftime("%Y-%m-%d")

    if not force and not check_missed_runs():
        return {"status": "skipped", "date": today_str}

    # ── 時序狀態（交易日感知）───────────────────────────────────────────────
    try:
        from src.trading_calendar import market_status_summary
        temporal_context = market_status_summary(today_str)
    except Exception as e:
        logger.warning(f"trading_calendar failed (non-fatal): {e}")
        temporal_context = {"temporal_note": "", "is_non_trading_day": False}

    if temporal_context.get("is_non_trading_day"):
        logger.warning(f"⚠️  Non-trading day: {today_str} — {temporal_context.get('temporal_note', '')}")

    logger.info(f"{'='*60}")
    logger.info(f"DIB v10.1 Daily Pipeline — {today_str} (run_id={RUN_CONTEXT.run_id})")
    logger.info(f"{'='*60}")

    results = {"date": today_str, "run_id": RUN_CONTEXT.run_id, "status": "running", "steps": {}}

    # ── 全域 fallback 初始化（防止 try-block 失敗後 NameError）──────────────
    memory_layers   = _load_memory_layers()
    active_theses   = _get_active_theses(memory_layers)
    calendar_package = {}
    data_package    = {}
    quant_package   = {}
    sentiment_package = {}
    geopolitical_package = {}
    historian_package = {}
    assembled_context = {"packages": {}, "coverage_score": 0, "coverage_warning": "",
                         "all_gaps": [], "material_density": {}, "token_allocation": {}}
    coverage_score  = 0
    analysis        = {"regime": {}, "core_tension": "", "inference_chain": [],
                       "thesis_updates": [], "new_thesis_candidates": [], "compass": []}
    da_result       = {"attacks": []}
    premortem_result = {"scenarios": []}
    verdict         = {"attack_verdicts": [], "final_conclusions_stand": True,
                       "risk_officer_notes": "", "confidence_adjustments": []}
    calibrated_chain = []
    report          = {"sections": {}, "metadata": {}}
    notion_url      = None
    thesis_review_result = {"activated_ids": [], "rejected_ids": [], "attention_items": [], "summary": ""}

    # ── Step 1: Scheduler ───────────────────────────────────────────────
    try:
        from src.scheduler import run_scheduler
        calendar_package = run_scheduler()
        results["steps"]["scheduler"] = "ok"
        logger.info("✓ Scheduler done")
    except Exception as e:
        logger.error(f"✗ Scheduler failed: {e}")
        calendar_package = {}
        results["steps"]["scheduler"] = f"error: {e}"

    # ── Step 2: DataWatcher ─────────────────────────────────────────────
    try:
        from src.data_watcher import run_data_watcher
        data_package = run_data_watcher(run_timestamp=RUN_CONTEXT.run_timestamp)
        # M7 修正：統一標記 run_id，確保各 package 可追溯到同一次執行
        data_package["_run_id"] = RUN_CONTEXT.run_id
        results["steps"]["data_watcher"] = "ok"
        logger.info("✓ DataWatcher done")
    except Exception as e:
        logger.error(f"✗ DataWatcher failed: {e}")
        data_package = {}
        results["steps"]["data_watcher"] = f"error: {e}"

    # ── Step 2.5a: 填入昨日預測結果（用今日 change_pct）────────────────────
    try:
        from src.calibration import fill_yesterday_outcomes
        fill_yesterday_outcomes(data_package, today_str)
    except Exception as e:
        logger.warning(f"fill_yesterday_outcomes failed (non-fatal): {e}")

    # ── Step 2.5b: Sync theses/active → l3.json ──────────────────────────
    try:
        from src.memory_manager import sync_theses_dir_to_l3
        sync_theses_dir_to_l3(today_str)
    except Exception as e:
        logger.warning(f"sync_theses_dir_to_l3 failed (non-fatal): {e}")

    # ── Step 2.5c: Fill inference outcomes（用今日漲跌回填昨日推論結果）─────
    try:
        from src.calibration import fill_inference_outcomes
        fill_inference_outcomes(data_package, today_str)
    except Exception as e:
        logger.warning(f"fill_inference_outcomes failed (non-fatal): {e}")

    # ── Step 3: SentimentWatcher ────────────────────────────────────────
    try:
        from src.sentiment_watcher import run_sentiment_watcher, should_trigger_event
        # memory_layers / active_theses 已在頂部初始化，直接使用
        # 用昨日 L2 中記錄的 TGRI 做代理（Step 6 才算今日 TGRI）
        tgri_score = memory_layers.get("l2", {}).get("tgri_score", 0.0)

        sentiment_package = run_sentiment_watcher(
            active_theses=active_theses,
            tgri_score=tgri_score,
            trigger="daily_pipeline",
            today_str=today_str,
        )
        results["steps"]["sentiment_watcher"] = "ok"
        logger.info("✓ SentimentWatcher done")
    except Exception as e:
        logger.error(f"✗ SentimentWatcher failed: {e}")
        sentiment_package = {}
        results["steps"]["sentiment_watcher"] = f"error: {e}"

    # ── Step 4: QuantEngine ─────────────────────────────────────────────
    try:
        from src.quant_engine import run_quant_engine
        quant_package = run_quant_engine(data_package)
        quant_package["_run_id"] = RUN_CONTEXT.run_id
        results["steps"]["quant_engine"] = "ok"
        logger.info("✓ QuantEngine done")
    except Exception as e:
        logger.error(f"✗ QuantEngine failed: {e}")
        quant_package = {}
        results["steps"]["quant_engine"] = f"error: {e}"

    # ── Step 4.5: TensionEngine（跨資產張力描述）─────────────────────────
    try:
        from src.tension_engine import apply_tension_notes
        data_package = apply_tension_notes(data_package)
        results["steps"]["tension_engine"] = "ok"
        logger.info("✓ TensionEngine done")
    except Exception as e:
        logger.error(f"✗ TensionEngine failed: {e}")
        results["steps"]["tension_engine"] = f"error: {e}"

    # ── Step 5: Historian ───────────────────────────────────────────────
    try:
        from src.historian import run_historian
        today_snapshot_preview = {
            "date": today_str,
            "metadata": {"regime": MISSING_DATA},
            "market_data": data_package,
        }
        historian_package = run_historian(today_snapshot_preview)
        results["steps"]["historian"] = "ok"
        logger.info("✓ Historian done")
    except Exception as e:
        logger.error(f"✗ Historian failed: {e}")
        historian_package = {}
        results["steps"]["historian"] = f"error: {e}"

    # ── Step 5.5: TGRI Auto-Scorer ────────────────────────────────────────
    try:
        from src.tgri_auto_scorer import auto_score_tgri_inputs
        auto_scores = auto_score_tgri_inputs()
        results["steps"]["tgri_auto_scorer"] = "ok"
        logger.info(f"✓ TGRI Auto-Scorer: {auto_scores}")
    except Exception as e:
        logger.warning(f"TGRI Auto-Scorer failed (non-fatal): {e}")
        results["steps"]["tgri_auto_scorer"] = f"fallback: {e}"

    # ── Step 6: Scholar + TGRI ──────────────────────────────────────────
    try:
        from src.scholar import run_scholar
        geopolitical_package = run_scholar(
            data_package=data_package,
            sentiment_package=sentiment_package,
            active_theses=active_theses,
        )
        tgri = geopolitical_package.get("tgri", {"score": 0})
        results["steps"]["scholar_tgri"] = "ok"
        logger.info(f"✓ Scholar+TGRI done (TGRI={tgri.get('score', 0)})")
    except Exception as e:
        logger.error(f"✗ Scholar+TGRI failed: {e}")
        geopolitical_package = {}
        tgri = {"score": 0}
        results["steps"]["scholar_tgri"] = f"error: {e}"

    # ── Step 7: Assembler ───────────────────────────────────────────────
    try:
        from src.assembler import run_assembler
        assembled_context = run_assembler(
            calendar_package=calendar_package,
            data_package=data_package,
            quant_package=quant_package,
            historian_package=historian_package,
            sentiment_package=sentiment_package,
            geopolitical_package=geopolitical_package,
        )
        coverage_score = assembled_context.get("coverage_score", 0)
        results["steps"]["assembler"] = "ok"
        alloc = assembled_context.get("token_allocation", {})
        logger.info(
            f"✓ Assembler done (coverage={coverage_score:.0%}, "
            f"tokens: data={alloc.get('data_total', 0)}, "
            f"memory={alloc.get('memory_total', 0)}, "
            f"ratio={alloc.get('data_to_memory_ratio', 0)})"
        )
    except Exception as e:
        logger.error(f"✗ Assembler failed: {e}")
        assembled_context = {"packages": {}, "coverage_score": 0}
        coverage_score = 0
        results["steps"]["assembler"] = f"error: {e}"

    # ── Step 8: Citation Checker（預驗證）──────────────────────────────
    # 預驗證：檢查 assembled_context 必要 package 是否存在且非空
    _required_pkgs = ["data_package", "quant_package", "calendar_package"]
    _packages = assembled_context.get("packages", {})
    _missing_pkgs = [p for p in _required_pkgs if not _packages.get(p)]
    pre_citation = {
        "integrity_score": 1.0 if not _missing_pkgs else 0.5,
        "pass": len(_missing_pkgs) == 0,
        "flags": [{"type": "MISSING_PACKAGE", "package": p} for p in _missing_pkgs],
    }
    if not pre_citation["pass"]:
        logger.warning(f"⚠️ Citation pre-check: missing packages {_missing_pkgs}")
    else:
        logger.info("✓ Citation pre-check done")
    results["steps"]["citation_pre"] = "ok"

    # ── Step 9: Analyst（Sonnet 第一次分析）────────────────────────────
    try:
        from src.agents.analyst import run_analyst
        analysis = run_analyst(assembled_context, today_str=today_str)
        results["steps"]["analyst"] = "ok"
        logger.info(f"✓ Analyst done (regime={analysis.get('regime', {}).get('current', '?')})")
    except Exception as e:
        logger.error(f"✗ Analyst failed: {e}")
        analysis = {"regime": {"current": MISSING_DATA, "day_count": 0},
                    "inference_chain": [], "compass": [], "thesis_updates": []}
        results["steps"]["analyst"] = f"error: {e}"

    # ── Step 10: Devil's Advocate ───────────────────────────────────────
    try:
        from src.agents.devils_advocate import run_devils_advocate
        da_result = run_devils_advocate(data_package, today_str=today_str)
        results["steps"]["devils_advocate"] = "ok"
        logger.info(f"✓ Devil's Advocate done ({len(da_result.get('attacks', []))} attacks)")
    except Exception as e:
        logger.error(f"✗ Devil's Advocate failed: {e}")
        da_result = {"attacks": []}
        results["steps"]["devils_advocate"] = f"error: {e}"

    # ── Step 11: Pre-mortem ─────────────────────────────────────────────
    try:
        from src.agents.premortem import run_premortem
        premortem_result = run_premortem(active_theses, data_package, today_str=today_str)
        results["steps"]["premortem"] = "ok"
        logger.info(f"✓ Pre-mortem done ({len(premortem_result.get('scenarios', []))} scenarios)")
    except Exception as e:
        logger.error(f"✗ Pre-mortem failed: {e}")
        premortem_result = {"scenarios": []}
        results["steps"]["premortem"] = f"error: {e}"

    # ── Step 12: Citation Checker（後驗證）────────────────────────────
    try:
        from src.citation_checker import verify_chain
        inference_chain = analysis.get("inference_chain", [])
        post_citation = verify_chain(inference_chain, assembled_context)
        results["steps"]["citation_post"] = "ok"
        logger.info(f"✓ Citation post-check done (score={post_citation.get('integrity_score', 0):.2f})")

        if not post_citation.get("pass"):
            logger.error("⚠️ Citation integrity < 0.8 — pipeline will continue but flag is raised")
            results["citation_warning"] = True
    except Exception as e:
        logger.error(f"✗ Citation post-check failed: {e}")
        post_citation = {"integrity_score": 0.0, "pass": False, "flags": []}
        results["steps"]["citation_post"] = f"error: {e}"

    # ── Step 13: Thesis Consistency Checker ─────────────────────────────
    try:
        from src.consistency_checker import check_consistency
        consistency = check_consistency(active_theses, analysis, today_str)
        results["steps"]["consistency"] = "ok"

        if consistency.get("has_critical"):
            logger.error("🚨 ConsistencyChecker CRITICAL — check theses immediately")
    except Exception as e:
        logger.error(f"✗ ConsistencyChecker failed: {e}")
        consistency = {"has_critical": False, "flags": []}
        results["steps"]["consistency"] = f"error: {e}"

    # ── Step 14: Opus（三源裁決）───────────────────────────────────────
    try:
        from src.agents.risk_officer import run_risk_officer
        verdict = run_risk_officer(
            assembled_data=assembled_context,
            analysis=analysis,
            da_result=da_result,
            premortem_result=premortem_result,
            memory_layers=memory_layers,
            historian_package=historian_package,
            today_str=today_str,
        )
        results["steps"]["risk_officer"] = "ok"
        logger.info(f"✓ Risk Officer done (conclusions_stand={verdict.get('final_conclusions_stand')})")
    except Exception as e:
        logger.error(f"✗ Risk Officer failed: {e}")
        verdict = {"attack_verdicts": [], "final_conclusions_stand": False,
                   "confidence_adjustments": [], "risk_officer_notes": "",
                   "fallback_reason": "risk_officer_exception",
                   "warning": "風控系統異常，此結論未經驗證"}
        results["steps"]["risk_officer"] = f"error: {e}"

    # ── Step 15: Calibration Engine ─────────────────────────────────────
    try:
        from src.calibration import apply_calibration_to_inference_chain, record_prediction
        regime_name = analysis.get("regime", {}).get("current", "unknown")
        calibrated_chain = apply_calibration_to_inference_chain(
            inference_chain=analysis.get("inference_chain", []),
            regime=regime_name,
            data_coverage=coverage_score,
            verdict_adjustments=verdict.get("confidence_adjustments", []),
        )
        # 記錄 compass 預測（含 supporting_inferences）
        for comp in analysis.get("compass", []):
            # analyst 輸出 logic_id: "INF_001"，映射為 supporting_inferences
            logic_id = comp.get("logic_id")
            supporting = [logic_id] if logic_id else comp.get("supporting_inferences", [])
            record_prediction(
                today_str,
                comp.get("asset", "").lower(),
                comp.get("direction", "neutral"),
                comp.get("adjusted_confidence") or comp.get("raw_confidence", 0.5),
                regime_name,
                supporting_inferences=supporting,
            )
        results["steps"]["calibration"] = "ok"
        logger.info("✓ Calibration done")
    except Exception as e:
        logger.error(f"✗ Calibration failed: {e}")
        calibrated_chain = analysis.get("inference_chain", [])
        results["steps"]["calibration"] = f"error: {e}"

    # ── Step 15.3: M3 Regime 一致性驗證（analyst vs quant_engine）──────
    analyst_regime = analysis.get("regime", {}).get("current", "")
    quant_regime_prob = quant_package.get("regime_probability", {})
    if analyst_regime and isinstance(quant_regime_prob, dict) and quant_regime_prob:
        top_quant_regime = max(quant_regime_prob, key=quant_regime_prob.get)
        top_prob = quant_regime_prob[top_quant_regime]
        if top_quant_regime != analyst_regime and top_prob > 0.4:
            logger.warning(
                f"⚠️ Regime 不一致：analyst='{analyst_regime}' vs quant_engine='{top_quant_regime}'({top_prob:.0%}) "
                f"— 可能需要人工確認"
            )

    # ── Step 15.5: Thesis Promoter（自動升格 new_thesis_candidates）──────
    try:
        from src.thesis_promoter import promote_candidates
        candidates = analysis.get("new_thesis_candidates", [])
        promoted_ids = promote_candidates(candidates, today_str)
        if promoted_ids:
            results["promoted_thesis_ids"] = promoted_ids
            logger.info(f"✓ ThesisPromoter: promoted {len(promoted_ids)} candidates → {promoted_ids}")
        else:
            logger.info("✓ ThesisPromoter: no new candidates to promote")
        results["steps"]["thesis_promoter"] = "ok"
    except Exception as e:
        logger.warning(f"ThesisPromoter failed (non-fatal): {e}")
        results["steps"]["thesis_promoter"] = f"error: {e}"

    # ── Step 15.6: Thesis Reviewer（Gemini 搜尋審核 proposed + 追蹤 active）──
    thesis_review_result = {"activated_ids": [], "rejected_ids": [], "attention_items": [], "summary": ""}
    try:
        from src.agents.thesis_reviewer import run_thesis_reviewer
        thesis_review_result = run_thesis_reviewer(today_str)
        if thesis_review_result.get("activated_ids"):
            # 重新載入 active_theses（剛升格）
            active_theses = _get_active_theses(_load_memory_layers())
        results["steps"]["thesis_reviewer"] = "ok"
        logger.info(f"✓ ThesisReviewer: {thesis_review_result.get('summary', '')}")
    except Exception as e:
        logger.warning(f"ThesisReviewer failed (non-fatal): {e}")
        results["steps"]["thesis_reviewer"] = f"error: {e}"

    # ── Step 15.7: Inference Store（核心資產寫入）──────────────────────
    try:
        from src import inference_store
        # 合併 INF + GEO 推論
        all_inferences = list(calibrated_chain)
        geo_infs = geopolitical_package.get("tgri", {}).get("geo_inferences", [])
        all_inferences.extend(geo_infs)
        inference_store.append_inferences(all_inferences, RUN_CONTEXT.run_id, today_str)
        results["steps"]["inference_store"] = "ok"
        logger.info(f"✓ InferenceStore done ({len(all_inferences)} inferences)")
    except Exception as e:
        logger.error(f"✗ InferenceStore failed: {e}")
        results["steps"]["inference_store"] = f"error: {e}"

    # ── Step 16: InvalidatorEngine ──────────────────────────────────────
    try:
        from src.invalidator_engine import check_all
        triggered = check_all(data_package, active_theses, today_str)
        results["steps"]["invalidator"] = "ok"
        if triggered:
            logger.warning(f"⚡ Invalidator triggered: {len(triggered)} theses")
            results["triggered_invalidators"] = triggered
        else:
            logger.info("✓ InvalidatorEngine done (no triggers)")
    except Exception as e:
        logger.error(f"✗ InvalidatorEngine failed: {e}")
        triggered = []
        results["steps"]["invalidator"] = f"error: {e}"

    # ── Step 17: Narrator ───────────────────────────────────────────────
    try:
        from src.agents.narrator import run_narrator
        report = run_narrator(
            analysis=analysis,
            verdict=verdict,
            calibrated_chain=calibrated_chain,
            geopolitical_package=geopolitical_package,
            calendar_package=calendar_package,
            data_package=data_package,
            today_str=today_str,
            material_density=assembled_context.get("material_density"),
            temporal_context=temporal_context,
            thesis_attention=thesis_review_result.get("attention_items", []),
        )
        results["steps"]["narrator"] = "ok"
        logger.info("✓ Narrator done")
    except Exception as e:
        logger.error(f"✗ Narrator failed: {e}")
        from src.agents.narrator import _fallback_report
        report = _fallback_report(today_str, str(e))
        results["steps"]["narrator"] = f"error: {e}"

    # ── Step 18: Notion Publisher ───────────────────────────────────────
    notion_url = None
    try:
        # 把 narrative_verdict 和 Brier Score 嵌入 report 供 publisher 使用
        def _replace_ids_with_descriptions(text: str, inference_chain: list, attack_verdicts: list) -> str:
            """將 INF_XXX 替換為推論 claim，DA_XXX 替換為攻擊裁決 narrative。"""
            _strip_prefix = re.compile(r'^(?:INF|DA)_\d{3}[：:，。\s]*')
            inf_map = {
                item.get("id", ""): _strip_prefix.sub('', item.get("claim", "")).strip()
                for item in inference_chain if item.get("id")
            }
            da_map = {
                v.get("attack_id", ""): _strip_prefix.sub('', v.get("narrative", "")).strip()
                for v in attack_verdicts if v.get("attack_id")
            }
            def _sub(m):
                key = m.group(0)
                prefix = m.group(1)
                lookup = inf_map if prefix == "INF" else da_map
                return lookup.get(key, key)
            # 兩次 pass，解決 replacement value 裡的巢狀 ID
            result = re.sub(r'\b(INF|DA)_\d{3}\b', _sub, text)
            result = re.sub(r'\b(INF|DA)_\d{3}\b', _sub, result)
            return result

        # H2 修正：將 INF_XXX / DA_XXX 替換為人類可讀文字後再發布
        _sections = report.get("sections", {})
        _inf_chain = analysis.get("inference_chain", [])
        _attack_verdicts = verdict.get("attack_verdicts", [])
        for _k, _v in _sections.items():
            if isinstance(_v, str):
                _sections[_k] = _replace_ids_with_descriptions(_v, _inf_chain, _attack_verdicts)

        report["_risk_officer_notes"] = verdict.get("risk_officer_notes", "")
        from src.config import MEMORY_DIR
        import json as _json
        try:
            _l5 = _json.loads((MEMORY_DIR / "l5.json").read_text(encoding="utf-8"))
            report["_brier_score"] = _l5.get("brier_score")
        except Exception:
            report["_brier_score"] = None

        from src.notion_publisher import publish_to_notion
        notion_url = publish_to_notion(
            report=report,
            today_str=today_str,
            coverage=coverage_score,
            integrity_score=post_citation.get("integrity_score", 1.0),
        )
        results["steps"]["notion"] = "ok"
        logger.info(f"✓ Notion published → {notion_url}")
    except Exception as e:
        logger.error(f"✗ Notion publisher failed: {e}")
        results["steps"]["notion"] = f"error: {e}"

    # ── Step 19: LINE Publisher ─────────────────────────────────────────
    try:
        from src.line_publisher import send_daily_summary, send_invalidator_alerts
        send_daily_summary(report, data_package, today_str, notion_url)
        if triggered:
            send_invalidator_alerts(triggered)
        results["steps"]["line"] = "ok"
        logger.info("✓ LINE notification sent")
    except Exception as e:
        logger.warning(f"LINE publisher skipped: {e}")
        results["steps"]["line"] = f"skipped: {e}"

    # ── Step 20: Memory Manager ─────────────────────────────────────────
    try:
        from src.memory_manager import run_memory_manager
        snapshot = run_memory_manager(
            today_str=today_str,
            data_package=data_package,
            quant_package=quant_package,
            analysis=analysis,
            verdict=verdict,
            report=report,
            tgri=tgri,
            coverage_score=coverage_score,
            citation_result=post_citation,
            notion_url=notion_url,
            premortem_result=premortem_result,
        )
        results["steps"]["memory_manager"] = "ok"
        logger.info("✓ MemoryManager done")
    except Exception as e:
        logger.error(f"✗ MemoryManager failed: {e}")
        results["steps"]["memory_manager"] = f"error: {e}"

    # ── Step 21: Git Commit + Push（SYNC_PATHS 全量，唯一 commit 出口）──
    # H6 修正：memory_manager.git_commit 已移除，此處是唯一 commit + push 路徑
    try:
        import subprocess
        from src.config import SYNC_PATHS
        for _sync_path in SYNC_PATHS:
            _full = PROJECT_ROOT / _sync_path
            if _full.exists():
                # -f: 強制加入，防止 .gitignore 誤擋 SYNC_PATHS 中的重要資料
                subprocess.run(["git", "add", "-f", str(_full)],
                               cwd=PROJECT_ROOT, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"DIB v10 daily snapshot {today_str}"],
            cwd=PROJECT_ROOT, check=True, capture_output=True,
        )
        subprocess.run(["git", "push"], cwd=PROJECT_ROOT, check=True, capture_output=True)
        results["steps"]["git_push"] = "ok"
        logger.info(f"✓ Git pushed snapshot {today_str}")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", errors="replace").strip() if e.stderr else ""
        # "nothing to commit" 不算 error
        if "nothing to commit" in stderr or "nothing added" in stderr:
            results["steps"]["git_push"] = "skipped: nothing to commit"
            logger.info("Git push skipped: nothing to commit")
        else:
            logger.warning(f"Git push failed: {stderr}")
            results["steps"]["git_push"] = f"warning: {stderr[:100]}"
    except Exception as e:
        logger.warning(f"Git push step failed: {e}")
        results["steps"]["git_push"] = f"warning: {e}"

    # ── Summary ─────────────────────────────────────────────────────────
    failed = [k for k, v in results["steps"].items() if str(v).startswith("error")]
    results["status"] = "completed" if not failed else "completed_with_errors"
    results["failed_steps"] = failed

    logger.info(f"{'='*60}")
    logger.info(f"Pipeline completed: {results['status']}")
    if failed:
        logger.warning(f"Failed steps: {failed}")
    logger.info(f"{'='*60}")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="DIB v10 Daily Pipeline")
    parser.add_argument("--force", action="store_true", help="Force run even if already ran today")
    args = parser.parse_args()

    results = run_daily_pipeline(force=args.force)
    print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
