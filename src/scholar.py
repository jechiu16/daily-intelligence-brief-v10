from __future__ import annotations
"""Scholar — Gemini Flash + Google Search，地緣政治分析 + PDF 讀取。"""

import json
import logging
import re
from datetime import datetime, timezone

import pytz
import requests
from google import genai
from google.genai import types

_TW_TZ = pytz.timezone("Asia/Taipei")

from src.config import (
    GEMINI_API_KEY, GEMINI_ENABLE_DAILY_SEARCH, GEMINI_ENABLE_PERIPHERY_SEARCH,
    GEMINI_SEARCH_MODEL, MISSING_DATA,
)
from src.periphery import get_periphery_search_query
from src.prompts.language_policy import TRADITIONAL_CHINESE_ONLY
from src.tgri import calculate_tgri

logger = logging.getLogger(__name__)
_gemini_client = genai.Client(api_key=GEMINI_API_KEY)


def _extract_json_text(response) -> str:
    """從 Gemini 回應中提取純 JSON 字串。"""
    try:
        raw = response.text or ""
    except Exception:
        parts = response.candidates[0].content.parts if response.candidates else []
        raw = "".join(getattr(p, "text", "") or "" for p in parts)

    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if match:
        return match.group(1).strip()

    match = re.search(r"(\{[\s\S]*\})", raw)
    if match:
        return match.group(1).strip()

    return raw.strip()


# 重要 PDF 報告來源
IMPORTANT_REPORTS = [
    # IMF World Economic Outlook（每年 4 月 / 10 月）
    # BIS Quarterly Review
    # Fed Beige Book（每年 8 次）
    # 這裡留 placeholder，實際 URL 由外部設定或動態抓取
]


def read_pdf_report(url: str) -> str | None:
    """直接讀取 PDF 報告。"""
    try:
        import pymupdf
        resp = requests.get(url, timeout=30, headers={"User-Agent": "DIB-v10"})
        resp.raise_for_status()
        doc = pymupdf.open(stream=resp.content, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        # 限制長度（避免 token 爆炸）
        return text[:50000] if len(text) > 50000 else text
    except ImportError:
        logger.warning("pymupdf not installed, skipping PDF read")
        return None
    except Exception as e:
        logger.error(f"PDF read failed for {url}: {e}")
        return None


def _build_scholar_prompt(
    data_package: dict,
    tgri: dict,
    sentiment_signals: list[dict],
    pdf_content: str | None = None,
) -> str:
    today = datetime.now(_TW_TZ).strftime("%Y-%m-%d")

    parts = [
        TRADITIONAL_CHINESE_ONLY,
        "",
        f"# 今日地緣政治分析請求 — {today}",
        "",
        "## 台灣地緣風險指數（TGRI）",
        json.dumps(tgri, indent=2, ensure_ascii=False, default=str),
        "",
        "## 相關輿情信號",
        json.dumps(sentiment_signals[:5], indent=2, ensure_ascii=False, default=str),
    ]

    if pdf_content:
        parts += [
            "",
            "## 重要報告摘錄",
            pdf_content[:10000],  # 只取前 10K 字元
        ]

    parts += [
        "",
        "## 任務",
        "請用繁體中文分析今日地緣政治局勢，輸出純 JSON：",
        "",
        """
{
  "active_risks": [
    {
      "region": "台海",
      "risk_level": "medium",
      "summary": "2-3句摘要",
      "trend": "rising|stable|falling",
      "connection_to_markets": "與市場的連結"
    }
  ],
  "scholar_analysis": "3-4段散文，地緣政治對今日宏觀的影響",
  "structural_observations": "長週期的結構性觀察（1-2句）",
  "thesis_connections": "與當前 active thesis 的連結"
}
""",
        "",
        "禁止輸出 markdown 包裝，只輸出純 JSON。",
        "",
        "重要：只引用你從 Google Search 結果確認的事件。不要用訓練資料補充「最近事件」，那些可能已過時。",
    ]

    return "\n".join(parts)


def _search_periphery_news(keywords: str, label: str) -> str:
    """用 Gemini Flash + Search Grounding 搜尋邊陲區域最新新聞。"""
    try:
        prompt = (
            TRADITIONAL_CHINESE_ONLY
            + "\n\n"
            f'搜尋 "{keywords}" 最近一週的重要新聞。\n'
            f"用 2-3 句繁體中文簡述「{label}」這個地區最近發生了什麼。\n"
            f"如果沒有找到重大新聞，說「近期無重大事件」。\n"
            f"只輸出摘要文字，不要 JSON。"
        )
        response = _gemini_client.models.generate_content(
            model=GEMINI_SEARCH_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            ),
        )
        try:
            text = response.text or ""
        except Exception:
            parts = response.candidates[0].content.parts if response.candidates else []
            text = "".join(getattr(p, "text", "") or "" for p in parts)
        result = text.strip()[:500]
        logger.info(f"Scholar periphery search: {label} → {len(result)} chars")
        return result
    except Exception as e:
        logger.warning(f"Scholar periphery search failed for {label}: {e}")
        return "搜尋失敗，請參考通用知識。"


def run_scholar(
    data_package: dict,
    sentiment_package: dict | None = None,
    active_theses: list[dict] | None = None,
) -> dict:
    """主入口：地緣政治分析。"""
    # 1. 計算 TGRI
    tgri = calculate_tgri(data_package)

    # 2. 從 sentiment 取相關信號
    signals = []
    if sentiment_package:
        all_signals = sentiment_package.get("signals", [])
        # 篩選地緣相關
        geo_keywords = ["taiwan", "china", "military", "pla", "geopolit", "defense", "taiwan"]
        signals = [
            s for s in all_signals
            if any(kw in s.get("headline", "").lower() or kw in s.get("keyword", "").lower()
                   for kw in geo_keywords)
        ][:5]

    # 3. 建立 prompt
    prompt = _build_scholar_prompt(data_package, tgri, signals)

    logger.info(f"Scholar: calling {GEMINI_SEARCH_MODEL}")

    if not GEMINI_ENABLE_DAILY_SEARCH:
        logger.info("Scholar: skipped Google Search because GEMINI_ENABLE_DAILY_SEARCH=false")
        geopolitical_package = _fallback_geopolitical(tgri)
    else:
        try:
            response = _gemini_client.models.generate_content(
                model=GEMINI_SEARCH_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    response_mime_type="application/json",
                ),
            )
            raw_text = _extract_json_text(response)
            scholar_result = json.loads(raw_text)

            geopolitical_package = {
                "tgri": tgri,
                "active_risks": scholar_result.get("active_risks", []),
                "scholar_analysis": scholar_result.get("scholar_analysis", MISSING_DATA),
                "structural_observations": scholar_result.get("structural_observations", ""),
                "thesis_connections": scholar_result.get("thesis_connections", ""),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

            logger.info(
                f"Scholar: TGRI={tgri['score']}, "
                f"{len(geopolitical_package['active_risks'])} active risks"
            )

        except json.JSONDecodeError as e:
            logger.warning(f"Scholar JSON parse fallback activated: {e}; raw={raw_text[:300] if 'raw_text' in locals() else ''}")
            geopolitical_package = _fallback_geopolitical(tgri)
        except Exception as e:
            logger.warning(f"Scholar fallback activated: {e}")
            geopolitical_package = _fallback_geopolitical(tgri)

    # 4. 邊陲系統（獨立於 Scholar LLM 結果）
    today_str = datetime.now(_TW_TZ).strftime("%Y-%m-%d")
    try:
        label, keywords, narrator_prompt = get_periphery_search_query(today_str)
        if GEMINI_ENABLE_PERIPHERY_SEARCH:
            periphery_context = _search_periphery_news(keywords, label)
        else:
            periphery_context = "邊陲搜尋已因 Gemini 配額控管暫停；僅保留今日輪值區域。"
        geopolitical_package["periphery"] = {
            "label": label,
            "keywords": keywords,
            "narrator_prompt": narrator_prompt,
            "search_context": periphery_context,
        }
        logger.info(f"Scholar: periphery region = {label} (search={GEMINI_ENABLE_PERIPHERY_SEARCH})")
    except Exception as e:
        logger.warning(f"Scholar periphery failed (non-fatal): {e}")
        geopolitical_package["periphery"] = {
            "label": "N/A",
            "keywords": "",
            "narrator_prompt": "",
            "search_context": "",
        }

    return geopolitical_package


def _fallback_geopolitical(tgri: dict) -> dict:
    return {
        "tgri": tgri,
        "active_risks": [],
        "scholar_analysis": MISSING_DATA,
        "structural_observations": MISSING_DATA,
        "thesis_connections": MISSING_DATA,
        "_error": "Scholar analysis failed",
    }
