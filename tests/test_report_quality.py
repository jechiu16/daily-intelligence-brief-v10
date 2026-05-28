from __future__ import annotations


def test_repair_report_contract_fills_institutional_layers():
    from src.report_quality import assess_report_quality, repair_report_contract

    report = {
        "sections": {"headline": "legacy shape"},
        "_market_data_structured": "- VIX: {{confirmed:19.2}}",
    }
    analysis = {
        "core_tension": "VIX 低位但實質利率上行",
        "question_for_devil": "若明日 VIX 升破 20，是否代表低波動敘事失效？",
        "inference_chain": [
            {
                "id": "INF_001",
                "claim": "實質利率透過折現率壓制長久期資產",
                "mechanism": "TIPS 透過折現率影響股票估值",
                "evidence": [{"data_key": "tips_10y"}],
                "asset_predictions": ["spx_down"],
            }
        ],
        "compass": [{"asset": "SPX", "direction": "down", "adjusted_confidence": 0.6}],
    }
    verdict = {"attack_verdicts": [], "confidence_adjustments": []}
    data_package = {
        "vix": {"price": 19.2, "quality": "confirmed"},
        "tips_10y": {"price": 2.1, "quality": "confirmed"},
    }

    repaired = repair_report_contract(
        report=report,
        analysis=analysis,
        verdict=verdict,
        data_package=data_package,
        watchboard_backtest={"status": "no_prior_watchboard", "summary": "尚無前一份觀察清單可回測。", "items": []},
    )

    sections = repaired["sections"]
    assert "今日真正改變" in sections["institutional_brief"]
    assert "tips_10y" in sections["causal_graph"]
    assert "尚無前一份觀察清單可回測" in sections["watchboard_backtest"]
    assert "| 儀表板 | 目前讀數 | 觸發條件 | 若觸發代表什麼 |" in sections["watchboard"]
    assert any(action in sections["compass"] for action in ("加碼", "持有", "減碼", "避險", "等待"))
    assert repaired["_causal_graph"]["nodes"]

    quality = assess_report_quality(report=repaired, analysis=analysis, verdict=verdict, coverage=1.0, integrity_score=1.0)
    assert quality["score"] > 0
    assert not any(flag["code"] == "missing_section" for flag in quality["flags"])


def test_quality_gate_flags_red_team_density_failures():
    from src.report_quality import assess_report_quality

    report = {
        "_watchboard_backtest": {"status": "evaluated", "summary": "昨日觀察清單：1 項觸發。", "items": []},
        "sections": {
            "institutional_brief": (
                "| 機構快照 | 內容 |\n"
                "| --- | --- |\n"
                "| 今日真正改變 | 油價回落 |\n"
                "| 主導機制 | 成本壓力下降 |\n"
                "| 最大反證 | VIX 升破 20 |\n"
                "| 昨日驗證 | 1 項觸發 |\n"
                "| 接下來 24-72 小時 | 看 VIX |"
            ),
            "tension": "油價回落但利率壓力仍高。",
            "market_data": " ".join(f"{{{{confirmed:{idx}}}}}" for idx in range(8)),
            "main_story": "市場今天看起來偏強。\n\n利率仍高，股市仍漲，這兩件事放在一起很矛盾。",
            "causal_graph": "- oil → costs → spx",
            "tgri_card": "TGRI：{{confirmed:30}}",
            "thesis_tracking": "今日無重大更新。",
            "compass": "| 資產 | 方向 | 信心 | 一句理由 |\n| --- | --- | --- | --- |\n| SPX | up | 60% | 風險偏好 |",
            "watchboard_backtest": "| 昨日觀察項 | 今日讀數 | 狀態 | 含義 |\n| --- | --- | --- | --- |\n| VIX | {{confirmed:21}} | 觸發 | 低波動失效 |",
            "watchboard": (
                "| 儀表板 | 目前讀數 | 觸發條件 | 若觸發代表什麼 |\n"
                "| --- | --- | --- | --- |\n"
                "| VIX | {{confirmed:21}} | 升破 20 | 低波動失效 |\n"
                "| Brent | {{confirmed:90}} | 跌破 88 | 能源壓力撤銷 |"
            ),
            "question": "若明日 VIX 仍高於 20，觀察清單是否驗證低波動失效？",
        },
    }
    analysis = {
        "inference_chain": [
            {
                "id": "INF_001",
                "claim": "利率壓制估值",
                "raw_confidence": 0.7,
                "adjusted_confidence": 0.7,
                "evidence": [{"data_key": "tips_10y", "quality": "cached"}],
            }
        ]
    }

    quality = assess_report_quality(report=report, analysis=analysis, verdict={}, coverage=1.0, integrity_score=1.0)
    codes = {flag["code"] for flag in quality["flags"]}

    assert "opening_not_true_change" in codes
    assert "thin_mechanism_density" in codes
    assert "watchboard_not_first" in codes
    assert "weak_data_no_haircut" in codes
    assert "compass_no_action_language" in codes


def test_repair_report_contract_puts_watchboard_context_first_when_backtest_exists():
    from src.report_quality import repair_report_contract

    repaired = repair_report_contract(
        report={"sections": {}},
        analysis={
            "core_tension": "油價回落支撐風險偏好，但實質利率仍壓制估值",
            "question_for_devil": "若明日 Brent 反彈，觀察清單是否否定 risk-on？",
            "inference_chain": [
                {
                    "claim": "能源價格回落支撐股市",
                    "mechanism": "能源價格回落降低成本壓力",
                    "evidence": [{"data_key": "brent", "quality": "confirmed"}],
                    "asset_predictions": ["spx_up"],
                    "raw_confidence": 0.65,
                    "adjusted_confidence": 0.62,
                }
            ],
            "compass": [{"asset": "SPX", "direction": "up", "adjusted_confidence": 0.7}],
        },
        verdict={"attack_verdicts": [], "confidence_adjustments": []},
        data_package={"brent": {"price": 96.0, "quality": "confirmed"}},
        watchboard_backtest={"status": "evaluated", "summary": "昨日觀察清單：0 項觸發。", "items": []},
    )

    opening = repaired["sections"]["main_story"].split("\n\n", 1)[0]
    assert "昨日觀察回測" in opening
    assert "透過" in repaired["sections"]["main_story"]
    assert "加碼" in repaired["sections"]["compass"]


def test_repair_report_contract_enforces_editorial_contract_on_weak_narrator_output():
    from src.report_quality import assess_report_quality, repair_report_contract

    report = {
        "sections": {
            "main_story": "市場今天上漲，投資人情緒改善。\n\n利率仍高，但股市也漲。",
            "compass": "| 資產 | 方向 | 信心 | 一句理由 |\n| --- | --- | --- | --- |\n| SPX | down | 70% | 利率壓制 |",
        }
    }
    analysis = {
        "core_tension": "油價回落但實質利率仍壓制估值",
        "question_for_devil": "若 PCE 高於預期，觀察清單是否否定 risk-on？",
        "inference_chain": [
            {
                "claim": "高實質利率壓制股市估值",
                "mechanism": "高實質利率提高折現率",
                "evidence": [{"data_key": "tips_10y", "quality": "cached"}],
                "asset_predictions": ["spx_down"],
                "raw_confidence": 0.7,
                "adjusted_confidence": 0.7,
            }
        ],
        "compass": [{"asset": "SPX", "direction": "down", "adjusted_confidence": 0.7}],
    }

    repaired = repair_report_contract(
        report=report,
        analysis=analysis,
        verdict={"attack_verdicts": [], "confidence_adjustments": []},
        data_package={"tips_10y": {"price": 2.1, "quality": "cached"}},
        watchboard_backtest={"status": "evaluated", "summary": "昨日觀察清單：1 項觸發。", "items": []},
    )

    story = repaired["sections"]["main_story"]
    assert story.startswith("今日真正改變")
    assert "昨日觀察回測" in story
    assert "透過" in story
    assert "信心折扣" in story
    assert "| SPX | down | 減碼 |" in repaired["sections"]["compass"]
    assert repaired["_editorial_contract"]["schema_version"] == "editorial-contract-v1"

    quality = assess_report_quality(report=repaired, analysis=analysis, verdict={}, coverage=1.0, integrity_score=1.0)
    codes = {flag["code"] for flag in quality["flags"]}
    assert "opening_not_true_change" not in codes
    assert "compass_no_action_language" not in codes


def test_sanitize_report_machine_tokens_handles_chinese_adjacency():
    from src.report_quality import assess_report_quality, sanitize_report_machine_tokens

    report = {
        "sections": {
            "institutional_brief": (
                "| 機構快照 | 內容 |\n| --- | --- |\n| 今日真正改變 | 測試 |\n| 主導機制 | 測試 |\n| 最大反證 | 測試 |\n| 昨日驗證 | 測試 |\n| 接下來 24-72 小時 | 測試 |"
            ),
            "tension": "測試",
            "market_data": " ".join(f"{{{{confirmed:{idx}}}}}" for idx in range(8)),
            "main_story": "今日真正改變是風險偏好升溫。INF_001的信心被 tips_10y(cached) 與 高實質利率（cached）拖累。\n\n油價透過成本下降提升風險偏好。",
            "causal_graph": "- oil → costs → spx",
            "tgri_card": "TGRI：{{confirmed:20}}",
            "thesis_tracking": "無更新",
            "compass": "| 資產 | 方向 | 動作 | 信心 | 一句理由 |\n| --- | --- | --- | --- | --- |\n| SPX | up | 持有 | 60% | 測試 |",
            "watchboard_backtest": "尚無前一份觀察清單可回測。",
            "watchboard": (
                "| 儀表板 | 目前讀數 | 觸發條件 | 若觸發代表什麼 |\n| --- | --- | --- | --- |\n| VIX | {{confirmed:19}} | 升破 20 | 轉弱 |\n| Brent | {{confirmed:90}} | 跌破 88 | 能源變化 |"
            ),
            "question": "若明日 PCE 月增率高於 {{estimated:0.5%}}，是否會改變 risk-on 判斷？",
        }
    }
    analysis = {"inference_chain": [{"id": "INF_001", "claim": "油價回落支撐風險偏好", "evidence": []}]}

    sanitize_report_machine_tokens(report=report, analysis=analysis, verdict={})

    story = report["sections"]["main_story"]
    assert "INF_001" not in story
    assert "tips_10y(cached)" not in story
    assert "高實質利率（cached）" not in story
    assert "油價回落支撐風險偏好" in story
    assert "TIPS 10年實質利率（快取資料）" in story
    assert "高實質利率（快取資料）" in story
    quality = assess_report_quality(report=report, analysis=analysis, verdict={}, coverage=1.0, integrity_score=1.0)
    assert "machine_tokens" not in {flag["code"] for flag in quality["flags"]}
