from __future__ import annotations


def test_editorial_contract_extracts_haircuts_and_actions():
    from src.editorial_contract import build_editorial_contract, format_editorial_contract

    contract = build_editorial_contract(
        analysis={
            "core_tension": "油價回落但實質利率仍壓制估值",
            "question_for_devil": "若 PCE 高於預期，risk-on 是否失效？",
            "inference_chain": [
                {
                    "claim": "實質利率壓制成長股",
                    "mechanism": "高實質利率提高折現率",
                    "evidence": [{"data_key": "tips_10y", "quality": "cached"}],
                    "asset_predictions": ["spx_down"],
                    "raw_confidence": 0.7,
                    "adjusted_confidence": 0.7,
                }
            ],
            "compass": [{"asset": "SPX", "direction": "down", "adjusted_confidence": 0.62}],
        },
        verdict={"attack_verdicts": [], "confidence_adjustments": []},
        data_package={"tips_10y": {"price": 2.1, "quality": "cached"}},
        watchboard_backtest={"status": "evaluated", "summary": "昨日觀察清單：1 項觸發。"},
    )

    assert contract["watchboard_first"] is True
    assert "油價回落" in contract["true_change"]
    assert contract["weak_evidence_haircuts"][0]["weak_keys"] == ["TIPS 10年實質利率（快取資料）"]
    assert contract["allocation_actions"][0]["action"] == "減碼"
    assert "機構編輯契約" in format_editorial_contract(contract)


def test_contract_story_builders_are_reader_facing():
    from src.editorial_contract import (
        build_contract_compass,
        build_contract_haircut_paragraph,
        build_contract_mechanism_paragraph,
        build_contract_story_lead,
    )

    contract = {
        "true_change": "油價回落但利率壓制沒有消失。",
        "watchboard_summary": "昨日觀察清單：0 項觸發。",
        "mechanism_sentences": ["brent 透過 成本壓力下降 影響 spx_up。"],
        "weak_evidence_haircuts": [{"claim": "利率壓制估值", "weak_keys": ["TIPS 10年實質利率（快取資料）"]}],
        "allocation_actions": [{"asset": "SPX", "direction": "up", "action": "持有", "confidence_label": "60%", "reason": "risk-on 但利率壓制"}],
    }

    assert "今日真正改變" in build_contract_story_lead(contract)
    assert "透過" in build_contract_mechanism_paragraph(contract)
    assert "信心折扣" in build_contract_haircut_paragraph(contract)
    assert "| SPX | up | 持有 | 60% |" in build_contract_compass(contract)
