"""Narrator — Sonnet，把裁決 JSON 轉成繁體中文報告。"""

import json
import re
import logging
from datetime import datetime

import anthropic

from src.config import ANTHROPIC_API_KEY, MISSING_DATA, SONNET_MODEL
from src.prompts.narrator_system import NARRATOR_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _build_user_message(
    analysis: dict,
    verdict: dict,
    calibrated_chain: list[dict],
    geopolitical_package: dict,
    calendar_package: dict,
    data_package: dict,
    today_str: str,
) -> str:
    """組裝 Narrator 輸入。"""
    lines = [
        f"## 今日報告日期：{today_str}",
        "",
        "## 裁決摘要",
        f"Regime：{analysis.get('regime', {}).get('current', MISSING_DATA)}（第{analysis.get('regime', {}).get('day_count', 0)}天）",
        f"核心張力：{analysis.get('core_tension', MISSING_DATA)}",
        f"首席風險官裁決：{'結論成立' if verdict.get('final_conclusions_stand') else '需要修正'}",
        "",
        "## 攻擊裁決清單",
        json.dumps(verdict.get("attack_verdicts", []), indent=2, ensure_ascii=False, default=str),
        "",
        "## 校準後推論鏈",
        json.dumps(calibrated_chain, indent=2, ensure_ascii=False, default=str),
        "",
        "## 配置羅盤（原始）",
        json.dumps(analysis.get("compass", []), indent=2, ensure_ascii=False, default=str),
        "",
        "## Thesis 更新",
        json.dumps(analysis.get("thesis_updates", []), indent=2, ensure_ascii=False, default=str),
        "",
        "## 地緣政治",
        json.dumps(geopolitical_package, indent=2, ensure_ascii=False, default=str),
        "",
        "## 市場數據（含品質標記）",
        _format_market_data(data_package),
        "",
        "## 行事曆",
        json.dumps(calendar_package.get("today_events", [])[:5], indent=2, ensure_ascii=False, default=str),
        "",
        "## 首席風險官備注",
        verdict.get("risk_officer_notes", ""),
        "",
        "## 思考題提示（來自分析師）",
        analysis.get("question_for_devil", ""),
        "",
        "請生成今日 DIB 報告 JSON。",
    ]
    return "\n".join(lines)


# 市場數據分組順序（每組：(組名, [(data_key, 顯示標籤), ...])）
_MARKET_DATA_GROUPS = [
    ("【核心風險指標】", [
        ("vix",   "VIX"),
        ("nfci",  "NFCI（金融壓力）"),
    ]),
    ("【股市】", [
        ("spx",  "標普500（SPX）"),
        ("twse", "台股（TWSE）"),
    ]),
    ("【貴金屬/商品】", [
        ("gold",              "黃金（XAU）"),
        ("brent",             "Brent原油"),
        ("wti",               "WTI原油"),
        ("copper_gold_ratio", "銅金比"),
    ]),
    ("【利率】", [
        ("us10y",             "美國10年期公債"),
        ("tips_10y",          "TIPS 10年（實質利率）"),
        ("yield_curve_10y2y", "殖利率曲線（10Y-2Y spread）"),
    ]),
    ("【匯率】", [
        ("dxy",    "美元指數（DXY）"),
        ("usdjpy", "美日（USDJPY）"),
        ("usdtwd", "美台（USDTWD）"),
    ]),
    ("【資金流向】", [
        ("tw_foreign_net", "台股外資淨額"),
        ("cot_gold",       "COT淨多頭"),
    ]),
]


def _format_market_data(data_package: dict) -> str:
    """格式化市場數據，依類別分組，帶品質標記。銅金比以百分比顯示。"""
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
                if key == "copper_gold_ratio":
                    # 銅金比乘以 100 顯示為百分比
                    display_val = f"{float(price) * 100:.2f}%"
                    lines.append(f"- {label}: {{{{{quality}:{display_val}}}}}")
                else:
                    change_str = f" ({change:+.1f}%)" if isinstance(change, (int, float)) else ""
                    lines.append(f"- {label}: {{{{{quality}:{price}}}}}{change_str}")
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
) -> dict:
    """呼叫 Sonnet Narrator，產生最終報告。"""
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")

    client = _get_client()
    user_msg = _build_user_message(
        analysis, verdict, calibrated_chain,
        geopolitical_package, calendar_package, data_package, today_str,
    )

    logger.info(f"Narrator: calling {SONNET_MODEL}")

    try:
        response = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=10000,  # 提升：7 段報告每段都要有深度，不能壓縮
            system=NARRATOR_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw_text = response.content[0].text.strip()

        match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", raw_text)
        if match:
            raw_text = match.group(1).strip()
        else:
            start = raw_text.find('{')
            end = raw_text.rfind('}')
            if start != -1 and end != -1:
                raw_text = raw_text[start:end+1]

        report = json.loads(raw_text)
        sections = report.get("sections", {})
        logger.info(f"Narrator: report generated, {len(sections)} sections")
        # 嵌入結構化市場數據，供 notion_publisher 直接使用（分組 + 銅金比百分比）
        report["_market_data_structured"] = _format_market_data(data_package)
        return report

    except json.JSONDecodeError as e:
        logger.error(f"Narrator JSON parse error: {e}")
        return _fallback_report(today_str, str(e))
    except Exception as e:
        logger.error(f"Narrator API error: {e}")
        return _fallback_report(today_str, str(e))


def _fallback_report(today_str: str, error: str) -> dict:
    return {
        "sections": {
            "tension": MISSING_DATA,
            "market_data": MISSING_DATA,
            "main_story": f"報告生成失敗：{error}",
            "geopolitics": MISSING_DATA,
            "thesis_tracking": MISSING_DATA,
            "compass": MISSING_DATA,
            "question": MISSING_DATA,
        },
        "metadata": {
            "regime": MISSING_DATA,
            "regime_day": 0,
            "coverage_score": 0,
            "integrity_score": 0,
            "date": today_str,
        },
        "_error": error,
    }
