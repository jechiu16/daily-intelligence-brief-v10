from __future__ import annotations
"""Narrator — DeepSeek，把裁決 JSON 轉成繁體中文報告。"""

import json
import re
import logging
from datetime import datetime, timezone

from google import genai
from google.genai import types

from src.config import GEMINI_API_KEY, GEMINI_NARRATOR_MODEL, MISSING_DATA
from src.deepseek_client import extract_json
from src.prompts.narrator_system import NARRATOR_SYSTEM_PROMPT
from src.telemetry import LLMTimer, record_llm_call

_OUTPUT_TOOL = {
    "name": "emit_report",
    "description": "輸出今日市場報告的結構化 JSON",
    "input_schema": {
        "type": "object",
        "properties": {
            "sections": {
                "type": "object",
                "description": "各報告段落（headline, situation, inference_chain 等）",
            },
            "metadata": {
                "type": "object",
                "description": "報告元數據",
            },
        },
        "required": ["sections"],
    },
}

logger = logging.getLogger(__name__)
_gemini_client = genai.Client(api_key=GEMINI_API_KEY)

def _build_user_message(
    analysis: dict,
    verdict: dict,
    calibrated_chain: list[dict],
    geopolitical_package: dict,
    calendar_package: dict,
    data_package: dict,
    today_str: str,
    material_density: dict | None = None,
    temporal_context: dict | None = None,
    thesis_attention: list[dict] | None = None,
) -> str:
    """組裝 Narrator 輸入——結構化摘要，非 raw JSON dump。"""
    regime = analysis.get("regime", {})
    attack_verdicts = verdict.get("attack_verdicts", [])

    lines = [
        f"## 今日報告日期：{today_str}",
        "",
    ]

    # ── 時序狀態（交易日感知，最優先注入）──────────────────────────────────
    if temporal_context:
        lines.append("## ⚠️ 時序狀態（必讀，影響全報告用語）")
        lines.append(temporal_context.get("temporal_note", ""))
        us_status = temporal_context.get("us_status", "open")
        asia_status = temporal_context.get("asia_status", "open")
        lines.append(f"美股狀態：{us_status} | 亞股狀態：{asia_status}")
        lines.append(f"美股最後交易日數據：{temporal_context.get('us_last_trading_day', today_str)}")
        lines.append(f"亞股最後交易日數據：{temporal_context.get('asia_last_trading_day', today_str)}")
        if temporal_context.get("is_non_trading_day"):
            lines.append("【強制規則】今日為非交易日。全文禁止使用「今日市場」「今日收盤」「亞洲市場今日」等語。"
                         "必須明確說明數據對應日期，例如「週五收盤」「4月4日數據」。")
        lines.append("")

    lines += [
        "## 裁決摘要",
        f"Regime：{regime.get('current', MISSING_DATA)}（第{regime.get('day_count', 0)}天）",
        f"核心張力：{analysis.get('core_tension', MISSING_DATA)}",
        f"風險官裁決：{'原始結論成立' if verdict.get('final_conclusions_stand') else '結論需要修正'}",
        "",
    ]

    # ── 攻擊摘要（散文式，非 raw JSON）──
    lines.append("## 攻擊與修正摘要（請融入主線故事，禁止出現 DA_xxx / SUSTAINED 等代碼）")
    sustained = [v for v in attack_verdicts if v.get("verdict") == "SUSTAINED"]
    noted = [v for v in attack_verdicts if v.get("verdict") == "NOTED"]
    overruled = [v for v in attack_verdicts if v.get("verdict") == "OVERRULED"]
    if sustained:
        lines.append(f"成功挑戰的論點（{len(sustained)} 個）：")
        for v in sustained:
            narrative = v.get("narrative", v.get("reason", ""))
            lines.append(f"  • {narrative[:200]}")
    if noted:
        lines.append(f"值得記錄的質疑（{len(noted)} 個）：")
        for v in noted:
            lines.append(f"  • {v.get('narrative', v.get('reason', ''))[:150]}")
    if overruled:
        lines.append(f"被駁回的攻擊（{len(overruled)} 個，簡要帶過即可）")
    lines.append("")

    # ── Thesis 今日動態（需要注意的事）──
    if thesis_attention:
        triggered = [a for a in thesis_attention if a.get("invalidator_triggered")]
        notable   = [a for a in thesis_attention if a.get("attention") and not a.get("invalidator_triggered")]
        if triggered or notable:
            lines.append("## Thesis 今日動態（請在適當章節融入，不另起 Thesis 章節）")
            for a in triggered:
                lines.append(f"  ⚠️ [{a['thesis_id']}] {a['title'][:40]}：{a['attention']}")
            for a in notable[:3]:  # 最多顯示 3 個，避免過長
                delta = a.get("confidence_delta", 0)
                arrow = "↑" if delta > 0.02 else ("↓" if delta < -0.02 else "→")
                lines.append(f"  {arrow} [{a['thesis_id']}] {a['title'][:40]}：{a['attention']}")
            lines.append("")

    # ── 信心修正摘要 ──
    adjustments = verdict.get("confidence_adjustments", [])
    if adjustments:
        lines.append("## 關鍵信心修正")
        for adj in adjustments[:5]:
            lines.append(f"  • {adj.get('inf_id')}: {adj.get('direction')} {adj.get('magnitude', 0):.2f} — {adj.get('reason', '')[:100]}")
        lines.append("")

    # ── 風險官核心觀點（請在主線轉折段引用意象和邏輯）──
    risk_notes = verdict.get("risk_officer_notes", "")
    narrative_verdict = verdict.get("narrative_verdict", "")
    lines.append("## 風險官核心觀點（融入主線，不另起章節）")
    if narrative_verdict:
        lines.append(f"風險官隱喻/因果語言（可直接引用或改寫）：")
        lines.append(narrative_verdict[:500])
    if risk_notes:
        lines.append(f"風險官備注：{risk_notes[:300]}")
    lines.append("")

    # ── 校準後推論鏈（保留完整 JSON，narrator 需要精確數據）──
    lines.append("## 校準後推論鏈")
    for inf in calibrated_chain[:8]:
        inf_id = inf.get("id", "?")
        claim = inf.get("claim", "")
        conf = inf.get("adjusted_confidence") or inf.get("raw_confidence", 0)
        mechanism = inf.get("mechanism", "")
        lines.append(f"  {inf_id}（信心 {conf:.0%}）：{claim[:120]}")
        if mechanism:
            lines.append(f"    機制：{mechanism[:100]}")
    lines.append("")

    # ── 配置羅盤、Thesis ──
    lines.extend([
        "## 配置羅盤（原始）",
        json.dumps(analysis.get("compass", []), indent=2, ensure_ascii=False, default=str),
        "",
        "## Thesis 更新",
        json.dumps(analysis.get("thesis_updates", []), indent=2, ensure_ascii=False, default=str),
        "",
    ])

    # ── 地緣政治（卡片式） ──
    lines.append("## 地緣政治（卡片式）")
    lines.append("")
    lines.append("### TGRI 卡")

    # TGRI 結構化輸入
    tgri = geopolitical_package.get("tgri", {})
    lines.append(f"張力等級：{tgri.get('tension_display', 'N/A')}")
    lines.append(f"分數：{tgri.get('score', 'N/A')}")
    lines.append(f"趨勢：{tgri.get('trend', 'N/A')}")
    lines.append(f"主導信號：{tgri.get('dominant_signal', 'N/A')}")
    # 列最高的 2-3 個組件
    components = tgri.get("components", {})
    if components:
        from src.tgri import TGRI_WEIGHTS
        weighted = sorted(
            [(k, v * TGRI_WEIGHTS.get(k, 0)) for k, v in components.items()],
            key=lambda x: x[1], reverse=True,
        )
        top_3 = weighted[:3]
        lines.append("主要組件：" + "、".join(f"{k}={components[k]:.1f}" for k, _ in top_3))
    lines.append("")

    # 邊陲卡
    periphery = geopolitical_package.get("periphery", {})
    lines.append(f"### 今日邊陲：{periphery.get('label', 'N/A')}")
    narrator_prompt = periphery.get("narrator_prompt", "")
    if narrator_prompt:
        lines.append(narrator_prompt)
    search_context = periphery.get("search_context", "")
    if search_context:
        lines.append(f"\n搜尋結果摘要：{search_context}")

    lines.extend([
        "",
        "## 市場數據（含品質標記）",
        _format_market_data(data_package),
        "",
        "## 行事曆",
        json.dumps(calendar_package.get("today_events", [])[:5], indent=2, ensure_ascii=False, default=str),
        "",
        "## 思考題提示（來自分析師）",
        analysis.get("question_for_devil", ""),
        "",
    ])

    # ── 材料密度信號（動態長度引導）──
    if material_density:
        level = material_density.get("level", "normal")
        hint = material_density.get("narrator_hint", "")
        total_tok = material_density.get("total_tokens", 0)
        lines.append(f"## 材料密度：{level}（輸入約 {total_tok:,} tokens）")
        lines.append(hint)
        lines.append("")

    lines.append("請生成今日 DIB 報告 JSON。主線故事遵循 Krugman Motion（開場悖論→展開因果→轉折質疑→誠實結論）。")
    return "\n".join(lines)


# 市場數據分組順序（三組，對應 narrator system prompt 的呼吸節奏）
_MARKET_DATA_GROUPS = [
    ("【風險資產】", [
        ("spx",   "標普500（SPX）"),
        ("vix",   "VIX"),
        ("nfci",  "NFCI（金融壓力）"),
        ("brent", "Brent原油"),
        ("wti",   "WTI原油"),
    ]),
    ("【利率與匯率】", [
        ("us10y",          "美國10年期公債"),
        ("tips_10y",       "TIPS 10年（實質利率）"),
        ("breakeven_5y5y", "5年5年通膨預期"),
        ("dxy",            "美元指數（DXY）"),
        ("usdjpy",         "美日（USDJPY）"),
        ("usdtwd",         "美台（USDTWD）"),
    ]),
    ("【商品與避險】", [
        ("gold",              "黃金（XAU）"),
        ("copper_gold_ratio", "銅金比"),
        ("brent_wti_spread",  "Brent-WTI 價差"),
        ("bdi",               "波羅的海乾散貨指數（BDI）"),
        ("tw_foreign_net",    "台股外資淨額"),
        ("cot_gold",          "COT黃金淨多頭"),
    ]),
]


def _format_market_data(data_package: dict) -> str:
    """格式化市場數據，依類別分組，帶品質標記與方向符號。

    單位規則：
    - copper_gold_ratio：原始比率 × 100 顯示為 %
    - yield_curve_10y2y：T10Y2Y 已是百分點，直接加 % 後綴
    - nfci：金融壓力指數（正常範圍 -1~+2），非百分比，加「（指數）」
    - us10y / tips_10y / fed_funds / breakeven_5y5y：殖利率已是 %，加後綴
    - 其餘數值型（price / value）：原樣顯示，若有 change_pct 加 ↑/↓
    """
    lines = []
    for group_name, assets in _MARKET_DATA_GROUPS:
        lines.append(f"### {group_name}")
        for key, label in assets:
            item = data_package.get(key, {})
            if not isinstance(item, dict):
                lines.append(f"- {label}: N/A")
                continue
            quality = item.get("quality", MISSING_DATA)
            price = item.get("price") or item.get("value")
            change = item.get("change_pct", "")

            if price and price != MISSING_DATA:
                # 方向符號（僅股票/商品等有 change_pct 的指標）
                if isinstance(change, (int, float)):
                    arrow = "↑" if change >= 0 else "↓"
                    change_str = f" {arrow} ({change:+.1f}%)"
                else:
                    arrow = ""
                    change_str = ""

                tension = item.get("tension_note", "")
                tension_suffix = f" — {tension}" if tension and tension != "數據缺失，無法判讀" else ""

                # 標注前一交易日亞洲數據（禁止 Narrator 用「今日」描述）
                prev_day_suffix = "【前一交易日收盤】" if item.get("asia_prev_day") else ""

                if key == "copper_gold_ratio":
                    display_val = f"{float(price) * 100:.2f}%"
                    lines.append(f"- {label}{prev_day_suffix}: {{{{{quality}:{display_val}}}}}{tension_suffix}")

                elif key == "yield_curve_10y2y":
                    display_val = f"{float(price):.2f}%"
                    lines.append(f"- {label}{prev_day_suffix}: {{{{{quality}:{display_val}}}}}{tension_suffix}")

                elif key == "nfci":
                    display_val = f"{float(price):.4f}（指數）"
                    lines.append(f"- {label}{prev_day_suffix}: {{{{{quality}:{display_val}}}}}{tension_suffix}")

                elif key in ("us10y", "tips_10y", "fed_funds", "breakeven_5y5y"):
                    display_val = f"{float(price):.2f}%"
                    lines.append(f"- {label}{prev_day_suffix}: {{{{{quality}:{display_val}}}}}{change_str}{tension_suffix}")

                else:
                    lines.append(f"- {label}{prev_day_suffix}: {{{{{quality}:{price}}}}}{change_str}{tension_suffix}")
            else:
                lines.append(f"- {label}: {{{{missing:N/A}}}}")
    return "\n".join(lines)


def run_narrator(
    analysis: dict,
    verdict: dict,
    calibrated_chain: list[dict],
    geopolitical_package: dict,
    calendar_package: dict,
    data_package: dict,
    today_str: str | None = None,
    material_density: dict | None = None,
    temporal_context: dict | None = None,
    thesis_attention: list[dict] | None = None,
) -> dict:
    """呼叫 Gemini Narrator，產生最終報告。"""
    if today_str is None:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    user_msg = _build_user_message(
        analysis, verdict, calibrated_chain,
        geopolitical_package, calendar_package, data_package, today_str,
        material_density=material_density,
        temporal_context=temporal_context,
        thesis_attention=thesis_attention,
    )

    logger.info(f"Narrator: calling {GEMINI_NARRATOR_MODEL}")

    try:
        with LLMTimer("narrator", GEMINI_NARRATOR_MODEL) as _t:
            response = _gemini_client.models.generate_content(
                model=GEMINI_NARRATOR_MODEL,
                contents=user_msg,
                config=types.GenerateContentConfig(
                    system_instruction=NARRATOR_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    max_output_tokens=12000,
                ),
            )
        try:
            raw_text = response.text or ""
        except Exception:
            parts = response.candidates[0].content.parts if response.candidates else []
            raw_text = "".join(getattr(p, "text", "") or "" for p in parts)

        report = extract_json(raw_text)
        usage = getattr(response, "usage_metadata", None)
        record_llm_call(
            agent="narrator", model=GEMINI_NARRATOR_MODEL,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            duration_s=_t.elapsed,
        )
        sections = report.get("sections", {})
        logger.info(f"Narrator: report generated, {len(sections)} sections")
        report["_market_data_structured"] = _format_market_data(data_package)
        return report

    except json.JSONDecodeError as e:
        logger.warning(f"Narrator JSON parse fallback activated: {e}")
        return _fallback_report(
            today_str,
            str(e),
            analysis=analysis,
            verdict=verdict,
            calibrated_chain=calibrated_chain,
            geopolitical_package=geopolitical_package,
            calendar_package=calendar_package,
            data_package=data_package,
            thesis_attention=thesis_attention,
        )
    except Exception as e:
        logger.error(f"Narrator API error: {e}")
        return _fallback_report(
            today_str,
            str(e),
            analysis=analysis,
            verdict=verdict,
            calibrated_chain=calibrated_chain,
            geopolitical_package=geopolitical_package,
            calendar_package=calendar_package,
            data_package=data_package,
            thesis_attention=thesis_attention,
        )


def _fallback_report(
    today_str: str,
    error: str,
    analysis: dict | None = None,
    verdict: dict | None = None,
    calibrated_chain: list[dict] | None = None,
    geopolitical_package: dict | None = None,
    calendar_package: dict | None = None,
    data_package: dict | None = None,
    thesis_attention: list[dict] | None = None,
) -> dict:
    """Generate a non-empty deterministic report when the LLM response is invalid."""
    analysis = analysis or {}
    verdict = verdict or {}
    calibrated_chain = calibrated_chain or analysis.get("inference_chain", []) or []
    geopolitical_package = geopolitical_package or {}
    calendar_package = calendar_package or {}
    data_package = data_package or {}
    thesis_attention = thesis_attention or []

    regime = analysis.get("regime", {})
    regime_name = regime.get("current", MISSING_DATA)
    regime_day = regime.get("day_count", 0)
    core_tension = analysis.get("core_tension") or "今日主要張力未能由敘事模型完整輸出，以下改用結構化資料生成備援版。"

    inference_lines = []
    for inf in calibrated_chain[:5]:
        claim = inf.get("claim", "")
        conf = inf.get("adjusted_confidence") or inf.get("raw_confidence")
        conf_text = f"（信心 {conf:.0%}）" if isinstance(conf, (int, float)) else ""
        if claim:
            inference_lines.append(f"- {claim}{conf_text}")
    if not inference_lines:
        inference_lines.append("- 今日推論鏈未能完整生成，請優先檢查 Analyst / Narrator JSON 格式。")

    tgri = geopolitical_package.get("tgri", {})
    periphery = geopolitical_package.get("periphery", {})
    thesis_lines = []
    for item in thesis_attention[:5]:
        attention = item.get("attention", "")
        tid = item.get("thesis_id", "")
        title = item.get("title", "")
        if attention:
            thesis_lines.append(f"- [{tid}] {title}：{attention}")
    if not thesis_lines:
        thesis_lines.append("- 今日 thesis 無重大更新，或 reviewer 未產生可發布摘要。")

    compass = analysis.get("compass", [])
    compass_lines = []
    for item in compass[:6]:
        asset = item.get("asset", "")
        direction = item.get("direction", "")
        conf = item.get("adjusted_confidence") or item.get("raw_confidence")
        conf_text = f"（{conf:.0%}）" if isinstance(conf, (int, float)) else ""
        if asset or direction:
            compass_lines.append(f"- {asset}: {direction}{conf_text}")
    if not compass_lines:
        compass_lines.append("- 暫無可發布配置羅盤。")

    events = calendar_package.get("today_events", []) if isinstance(calendar_package, dict) else []
    event_lines = []
    for event in events[:5]:
        if isinstance(event, dict):
            name = event.get("event") or event.get("name") or event.get("title") or "未命名事件"
            event_lines.append(f"- {name}")
    if not event_lines:
        event_lines.append("- 今日無需要特別標記的行事曆事件。")

    return {
        "sections": {
            "tension": f"{regime_name}（第 {regime_day} 天）。{core_tension}",
            "market_data": _format_market_data(data_package),
            "main_story": (
                "本段為備援版日報：Narrator 回傳格式無法解析，因此系統改用已驗證的結構化推論輸出。"
                "\n\n"
                + "\n".join(inference_lines)
            ),
            "tgri_card": (
                f"TGRI 分數：{tgri.get('score', 'N/A')}；"
                f"趨勢：{tgri.get('trend', 'N/A')}；"
                f"主導信號：{tgri.get('dominant_signal', 'N/A')}。"
            ),
            "periphery_card": (
                f"今日邊陲：{periphery.get('label', 'N/A')}。\n"
                f"{periphery.get('search_context', '') or '未取得邊陲搜尋摘要。'}"
            ),
            "thesis_tracking": "\n".join(thesis_lines),
            "compass": "\n".join(compass_lines),
            "question": analysis.get("question_for_devil") or "今日問題未生成，請檢查 Analyst 輸出。",
            "calendar": "\n".join(event_lines),
        },
        "metadata": {
            "regime": regime_name,
            "regime_day": regime_day,
            "coverage_score": 0,
            "integrity_score": 0,
            "date": today_str,
            "fallback": True,
        },
        "_market_data_structured": _format_market_data(data_package),
        "_error": error,
    }
