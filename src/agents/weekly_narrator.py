from __future__ import annotations
"""Weekly Narrator — Sonnet，把週度聚合資料轉成繁體中文週報。

單次 LLM 呼叫。週報是反思與模式識別，不是發現——日報已經辯論過了。
"""

import json
import logging
import re

import anthropic

from src.config import ANTHROPIC_API_KEY, MISSING_DATA, SONNET_MODEL
from src.prompts.weekly_narrator_system import WEEKLY_NARRATOR_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _build_weekly_user_message(weekly_context: dict) -> str:
    """把 weekly_context dict 組裝成 narrator 的 user message。"""
    wl = weekly_context.get("week_label", "")
    dr = weekly_context.get("date_range", {})
    days = weekly_context.get("days_available", 0)
    degraded = weekly_context.get("degraded", False)

    lines = [
        f"## 週報輸入：{wl}（{dr.get('from', '')} ~ {dr.get('to', '')}，{days} 個交易日）",
    ]
    if degraded:
        lines.append(f"⚠️ 資料不完整（僅 {days} 天），報告需標註此限制。")
    lines.append("")

    # ── 每日張力回顧 ──
    lines.append("## 一、每日張力回顧")
    for dt in weekly_context.get("daily_tensions", []):
        tension = dt.get("tension", MISSING_DATA)
        lines.append(f"- **{dt.get('date', '')}**：{tension[:200] if tension != MISSING_DATA else '（缺失）'}")
    lines.append("")

    # ── 推論績效 ──
    ia = weekly_context.get("inference_analysis", {})
    lines.append("## 二、推論績效分析")
    lines.append(f"本週推論總數：{ia.get('total', 0)}")
    lines.append(f"裁決分布：{json.dumps(ia.get('by_verdict', {}), ensure_ascii=False)}")
    lines.append(f"結果分布：{json.dumps(ia.get('by_outcome', {}), ensure_ascii=False)}")
    if ia.get("accuracy_by_asset"):
        lines.append("各資產命中率：")
        for asset, info in ia["accuracy_by_asset"].items():
            lines.append(f"  - {asset}: {info.get('hit_rate', 'N/A')} (n={info.get('n', 0)})")
    if ia.get("blind_spots"):
        lines.append("盲點（高信心但被反駁）：")
        for bs in ia["blind_spots"]:
            lines.append(f"  - [{bs['date']}] {bs['claim']}（信心 {bs['confidence']:.2f}）")
    if ia.get("strongest_signals"):
        lines.append("最強信號（高信心且被驗證）：")
        for ss in ia["strongest_signals"]:
            lines.append(f"  - [{ss['date']}] {ss['claim']}（信心 {ss['confidence']:.2f}）")
    if ia.get("recurring_assets"):
        lines.append("反覆出現的資產主題：")
        for ra in ia["recurring_assets"]:
            lines.append(f"  - {ra['asset']}：{ra['days']} 天出現")
    lines.append("")

    # ── 校準計分卡 ──
    sc = weekly_context.get("scorecard", {})
    lines.append("## 三、校準計分卡")
    lines.append(f"週 Brier Score：{sc.get('weekly_brier', 'N/A')}")
    lines.append(f"30 日 Brier Score：{sc.get('rolling_30d_brier', 'N/A')}")
    lines.append(f"命中率：{sc.get('hit_rate', 'N/A')}（{sc.get('correct', 0)}/{sc.get('total_filled', 0)}）")
    lines.append(f"平均信心度：{sc.get('avg_confidence', 'N/A')}")
    lines.append(f"校準缺口：{sc.get('calibration_gap', 'N/A')}（正值=過度自信）")
    if sc.get("by_asset"):
        lines.append("分資產：")
        for asset, info in sc["by_asset"].items():
            lines.append(f"  - {asset}: 命中率 {info.get('hit_rate', 'N/A')} (n={info.get('n', 0)})")
    lines.append("")

    # ── Regime 演化 ──
    re_ = weekly_context.get("regime_evolution", {})
    lines.append("## 四、Regime 演化")
    lines.append(f"週初 Regime：{re_.get('start_regime', MISSING_DATA)}")
    lines.append(f"週末 Regime：{re_.get('end_regime', MISSING_DATA)}（第 {re_.get('regime_day_count', 0)} 天）")
    lines.append(f"主導 Regime：{re_.get('dominant_regime', MISSING_DATA)}（穩定性 {re_.get('regime_stability', 0)}）")
    if re_.get("transitions"):
        lines.append("本週轉換：")
        for t in re_["transitions"]:
            lines.append(f"  - {t['date']}：{t['from']} → {t['to']}")
    else:
        lines.append("本週無 regime 轉換。")
    lines.append("")

    # ── Thesis 追蹤 ──
    tt = weekly_context.get("thesis_tracking", {})
    lines.append("## 五、Thesis 生命週期")
    lines.append(f"Active 數量：{tt.get('active_count', 0)}")
    for ev in tt.get("evolution", []):
        delta_str = ""
        if ev.get("confidence_delta") is not None:
            d = ev["confidence_delta"]
            delta_str = f"（{'↑' if d > 0 else '↓' if d < 0 else '→'}{abs(d):.2f}）"
        lines.append(f"  - {ev.get('id', '')}: {ev.get('title', '')} {delta_str}")
        if ev.get("invalidation_condition"):
            lines.append(f"    失效條件：{ev['invalidation_condition'][:100]}")
    if tt.get("invalidated_this_week"):
        lines.append("本週失效：")
        for inv in tt["invalidated_this_week"]:
            lines.append(f"  - {inv.get('id', '')}: {inv.get('title', '')} — {inv.get('reason', '')[:100]}")
    lines.append("")

    # ── 市場結構 ──
    ms = weekly_context.get("market_structure", {})
    lines.append("## 六、市場結構摘要")
    if ms.get("biggest_movers"):
        lines.append("週度最大波動資產：")
        for m in ms["biggest_movers"]:
            lines.append(f"  - {m['asset']}: {m['return_pct']:+.2f}%")
    if ms.get("persistent_anomalies"):
        lines.append("持續性異常（z-score > 2，多日觸發）：")
        for pa in ms["persistent_anomalies"]:
            lines.append(f"  - {pa['asset']}：{pa['days_triggered']} 天")
    if ms.get("volatility"):
        vol = ms["volatility"]
        lines.append(f"VIX 週概況：平均 {vol.get('vix_avg')}, 高 {vol.get('vix_high')}, 低 {vol.get('vix_low')}")
    lines.append("")

    # ── 風控洞察 ──
    rd = weekly_context.get("risk_digest", {})
    lines.append("## 七、風險官洞察")
    lines.append(f"本週 SUSTAINED 攻擊：{rd.get('total_sustained', 0)} 個")
    for sa in rd.get("sustained_attacks", []):
        lines.append(f"  - [{sa['date']}] {sa.get('reason', '')}")
    if rd.get("risk_notes"):
        lines.append("風險官每日備忘：")
        for rn in rd["risk_notes"]:
            lines.append(f"  - [{rn['date']}] {rn['notes'][:150]}")
    lines.append("")

    # ── 元數據 ──
    meta = weekly_context.get("metadata_summary", {})
    lines.append("## 八、系統元數據")
    lines.append(f"平均覆蓋率：{meta.get('avg_coverage', 'N/A')}")
    lines.append(f"平均 Citation Integrity：{meta.get('avg_integrity', 'N/A')}")
    lines.append("")
    lines.append("請根據以上資料生成週報 JSON。")

    return "\n".join(lines)


def _parse_json_from_text(raw_text: str) -> dict:
    """從 LLM 輸出提取 JSON。"""
    # Try markdown code block first
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", raw_text)
    if match:
        return json.loads(match.group(1).strip())
    # Try raw JSON
    start = raw_text.find('{')
    end = raw_text.rfind('}')
    if start != -1 and end != -1:
        return json.loads(raw_text[start:end + 1])
    raise json.JSONDecodeError("No JSON object found", raw_text, 0)


def _fallback_report(week_label: str, error: str) -> dict:
    """LLM 失敗時的 fallback。"""
    return {
        "sections": {
            "narrative_arc": f"週報生成失敗：{error[:200]}。請參閱本週各日報。",
            "calibration_review": "校準數據請查閱 L5 scorecard。",
            "thesis_evolution": "Thesis 狀態請查閱 L3。",
            "structural_view": "結構分析暫缺。",
            "watch_list": "觀測清單暫缺。",
            "system_reflection": f"系統錯誤：{error[:100]}",
        },
        "metadata": {
            "week_label": week_label,
            "error": error[:200],
        },
    }


def run_weekly_narrator(weekly_context: dict) -> dict:
    """單次 Sonnet LLM 呼叫生成週報。

    Returns:
        {
            "sections": { narrative_arc, calibration_review, thesis_evolution,
                          structural_view, watch_list, system_reflection },
            "metadata": { week_label, dominant_regime, weekly_brier, ... }
        }
    """
    week_label = weekly_context.get("week_label", "unknown")
    logger.info(f"WeeklyNarrator: generating report for {week_label}")

    user_msg = _build_weekly_user_message(weekly_context)
    logger.info(f"WeeklyNarrator: user message length = {len(user_msg)} chars")

    try:
        client = _get_client()
        response = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=16000,
            system=WEEKLY_NARRATOR_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw_text = response.content[0].text
        logger.info(f"WeeklyNarrator: received {len(raw_text)} chars from LLM")

        report = _parse_json_from_text(raw_text)

        # Validate required sections
        sections = report.get("sections", {})
        required = [
            "narrative_arc", "calibration_review", "thesis_evolution",
            "structural_view", "watch_list", "system_reflection",
        ]
        missing = [k for k in required if k not in sections]
        if missing:
            logger.warning(f"WeeklyNarrator: missing sections: {missing}")

        # Enrich metadata from aggregator
        meta = report.setdefault("metadata", {})
        meta.setdefault("week_label", week_label)
        meta.setdefault("dominant_regime", weekly_context.get("regime_evolution", {}).get("dominant_regime"))
        meta.setdefault("weekly_brier", weekly_context.get("scorecard", {}).get("weekly_brier"))
        meta.setdefault("hit_rate", weekly_context.get("scorecard", {}).get("hit_rate"))
        meta.setdefault("total_inferences", weekly_context.get("inference_analysis", {}).get("total"))
        meta.setdefault("days_available", weekly_context.get("days_available"))

        logger.info(f"WeeklyNarrator: report generated successfully for {week_label}")
        return report

    except json.JSONDecodeError as e:
        logger.error(f"WeeklyNarrator: JSON parse error: {e}")
        # Retry once
        try:
            logger.info("WeeklyNarrator: retrying with explicit JSON instruction")
            retry_suffix = (
                "\n\n[系統提示：上次輸出的 JSON 無法解析。"
                "請重新輸出，只輸出純 JSON 物件，不要任何說明文字、前言或 markdown 標記。]"
            )
            response = client.messages.create(
                model=SONNET_MODEL,
                max_tokens=16000,
                system=WEEKLY_NARRATOR_SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": user_msg},
                    {"role": "assistant", "content": raw_text},
                    {"role": "user", "content": retry_suffix},
                ],
            )
            raw_text_2 = response.content[0].text
            report = _parse_json_from_text(raw_text_2)
            logger.info(f"WeeklyNarrator: retry succeeded for {week_label}")
            return report
        except Exception as retry_err:
            logger.error(f"WeeklyNarrator: retry also failed: {retry_err}")
            return _fallback_report(week_label, str(e))

    except Exception as e:
        logger.error(f"WeeklyNarrator: failed: {e}")
        return _fallback_report(week_label, str(e))
