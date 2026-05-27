from __future__ import annotations

import json


def test_extract_watchboard_items_from_report_sections():
    from src.research_ledger import extract_watchboard_items

    report = {
        "sections": {
            "watchboard": (
                "| 儀表板 | 目前讀數 | 觸發條件 | 若觸發代表什麼 |\n"
                "| --- | --- | --- | --- |\n"
                "| VIX | {{confirmed:17.0}} | 升破 20 且 SPX 同步轉弱 | 低波動自滿失效 |\n"
            )
        }
    }

    items = extract_watchboard_items(report)

    assert len(items) == 1
    assert items[0]["data_key"] == "vix"
    assert items[0]["current_quality"] == "confirmed"
    assert items[0]["current_value"] == 17.0


def test_build_watchboard_backtest_detects_trigger():
    from src.research_ledger import build_watchboard_backtest

    previous = {
        "watchboard": {
            "items": [
                {
                    "id": "WB_001",
                    "indicator": "VIX",
                    "data_key": "vix",
                    "current_reading": "{{confirmed:17.0}}",
                    "current_value": 17.0,
                    "trigger_condition": "升破 20 且 SPX 同步轉弱",
                    "implication": "低波動自滿失效",
                }
            ]
        }
    }
    current = {
        "vix": {"price": 21.4, "quality": "confirmed"},
        "spx": {"price": 5200.0, "change_pct": -0.5, "quality": "confirmed"},
    }

    backtest = build_watchboard_backtest(previous, current)

    assert backtest["status"] == "evaluated"
    assert backtest["items"][0]["status"] == "triggered"
    assert "{{confirmed:21.4}}" == backtest["items"][0]["current_reading"]


def test_build_watchboard_backtest_handles_or_and_partial_continuous():
    from src.research_ledger import build_watchboard_backtest

    previous = {
        "watchboard": {
            "items": [
                {
                    "id": "WB_001",
                    "indicator": "Brent 原油",
                    "data_key": "brent",
                    "current_reading": "{{confirmed:92.0}}",
                    "current_value": 92.0,
                    "trigger_condition": "重新站上 100 或跌破 88",
                    "implication": "能源壓力重定價",
                },
                {
                    "id": "WB_002",
                    "indicator": "台股外資",
                    "data_key": "tw_foreign_net",
                    "current_reading": "{{confirmed:100.0}}",
                    "current_value": 100.0,
                    "trigger_condition": "連續兩日轉為大額賣超",
                    "implication": "資金流反轉",
                },
            ]
        }
    }
    current = {
        "brent": {"price": 86.5, "quality": "confirmed"},
        "tw_foreign_net": {"value": -120.0, "quality": "confirmed"},
    }

    backtest = build_watchboard_backtest(previous, current)

    assert backtest["items"][0]["status"] == "triggered"
    assert backtest["items"][1]["status"] == "partial"


def test_build_causal_graph_links_evidence_to_predictions():
    from src.research_ledger import build_causal_graph

    analysis = {
        "inference_chain": [
            {
                "id": "INF_001",
                "claim": "油價回落支撐風險偏好",
                "mechanism": "油價回落透過成本壓力下降支撐風險資產",
                "evidence": [{"data_key": "brent"}, {"data_key": "spx"}],
                "asset_predictions": ["spx_up"],
            }
        ]
    }

    graph = build_causal_graph(analysis, {"confidence_adjustments": []})

    assert any(node["id"] == "DATA_brent" for node in graph["nodes"])
    assert any(edge["from"] == "INF_001" and edge["to"] == "ASSET_spx_up" for edge in graph["edges"])
    assert "brent" in graph["text"]


def test_build_institutional_brief_has_first_screen_contract():
    from src.research_ledger import build_institutional_brief

    brief = build_institutional_brief(
        analysis={
            "core_tension": "實質利率回升但風險資產仍抗跌",
            "inference_chain": [{"claim": "估值壓力正在重新累積", "mechanism": "TIPS 透過折現率壓制長久期資產"}],
        },
        verdict={"attack_verdicts": [], "confidence_adjustments": []},
        watchboard_backtest={"summary": "昨日觀察清單：1 項觸發。"},
        watchboard_items=[{"indicator": "VIX", "trigger_condition": "升破 20"}],
    )

    assert "今日真正改變" in brief
    assert "最大反證" in brief
    assert "接下來 24-72 小時" in brief


def test_load_previous_snapshot_uses_latest_before_today(tmp_path):
    from src.research_ledger import load_previous_snapshot

    (tmp_path / "2026-05-24.json").write_text(json.dumps({"date": "2026-05-24"}), encoding="utf-8")
    (tmp_path / "2026-05-26.json").write_text(json.dumps({"date": "2026-05-26"}), encoding="utf-8")
    (tmp_path / "2026-05-27.json").write_text(json.dumps({"date": "2026-05-27"}), encoding="utf-8")

    previous = load_previous_snapshot("2026-05-27", snapshots_dir=tmp_path)

    assert previous["date"] == "2026-05-26"
