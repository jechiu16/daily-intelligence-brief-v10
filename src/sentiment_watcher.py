from __future__ import annotations
"""SentimentWatcher — Gemini Flash + Search Grounding，輿情監控。"""

import json
import logging
import re
from datetime import datetime, timezone

from google import genai
from google.genai import types

from src.config import (
    GEMINI_API_KEY, GEMINI_FLASH_MODEL, MISSING_DATA, TRUSTED_SOURCES,
)
from src.prompts.language_policy import TRADITIONAL_CHINESE_ONLY

logger = logging.getLogger(__name__)

_gemini_client = genai.Client(api_key=GEMINI_API_KEY)


def _extract_json_text(response) -> str:
    """從 Gemini 回應中提取純 JSON 字串（處理前言文字、markdown code block）。"""
    # 取得所有文字 parts 合併
    try:
        raw = response.text or ""
    except Exception:
        parts = response.candidates[0].content.parts if response.candidates else []
        raw = "".join(getattr(p, "text", "") or "" for p in parts)

    # 找 ```json ... ``` 或 ``` ... ```
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if match:
        return match.group(1).strip()

    # 找裸露的 { ... }（取第一個完整 JSON 物件）
    match = re.search(r"(\{[\s\S]*\})", raw)
    if match:
        return match.group(1).strip()

    return raw.strip()


def build_search_scope(active_theses: list[dict], tgri_score: float) -> dict:
    """動態生成搜尋範圍。"""
    # 從 active thesis 提取關鍵詞（最多 5 個）
    thesis_keywords = []
    for thesis in active_theses[:5]:
        title = thesis.get("title", "")
        # 提取英文關鍵詞
        words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', title)
        thesis_keywords.extend(words[:2])
    thesis_keywords = list(dict.fromkeys(thesis_keywords))[:5]  # 去重取前 5

    # 固定結構性關鍵詞
    structural = [
        "Federal Reserve monetary policy",
        "Bank of Japan YCC yield curve",
        "OPEC oil production",
        "semiconductor supply chain Taiwan",
    ]

    # 條件觸發：TGRI > 60 才加入台灣軍事關鍵詞
    conditional = []
    if tgri_score > 60:
        conditional = [
            "Taiwan military PLA ADIZ",
            "US Taiwan relations",
            "Taiwan Strait",
        ]

    return {
        "primary": thesis_keywords,
        "structural": structural,
        "conditional": conditional,
        "excluded": TRUSTED_SOURCES["excluded"],
    }


def _build_prompt(search_scope: dict, trigger: str, today_str: str | None = None) -> str:
    keywords = (
        search_scope.get("primary", []) +
        search_scope.get("structural", []) +
        search_scope.get("conditional", [])
    )

    excluded = search_scope.get("excluded", [])

    date_note = f"🗓️ 今日分析日期：{today_str}（台灣時間）\n" if today_str else ""
    return TRADITIONAL_CHINESE_ONLY + "\n\n" + date_note + f"""你是 DIB 輿情監控員。請搜尋以下關鍵詞的最新新聞和事件，輸出結構化 JSON。

## 搜尋關鍵詞
{chr(10).join(f'- {k}' for k in keywords)}

## 排除來源
{', '.join(excluded)}

## 可信來源優先級
- Tier 1（官方）：{', '.join(TRUSTED_SOURCES['tier_1'][:4])}
- Tier 2（主要媒體）：{', '.join(TRUSTED_SOURCES['tier_2'][:4])}
- Tier 3（研究機構）：{', '.join(TRUSTED_SOURCES['tier_3'])}

## 輸出格式（純 JSON，不加 markdown）

{{
  "scan_time": "{datetime.now(timezone.utc).isoformat()}",
  "trigger": "{trigger}",
  "signals": [
    {{
      "keyword": "觸發的關鍵詞",
      "sentiment": "bullish|bearish|neutral",
      "confidence": 0.7,
      "source": "來源域名",
      "tier": 1,
      "headline": "新聞標題或摘要",
      "reasoning": "一句話：這個信號為什麼重要、它改變了什麼預期",
      "relevance": ["T1", "T2"],
      "new_since_last_scan": true
    }}
  ],
  "aggregate": {{
    "overall_sentiment": "risk_on|risk_off|mildly_risk_on|mildly_risk_off|neutral",
    "dominant_theme": "主題",
    "noise_level": "low|medium|high"
  }},
  "items_discarded": 0,
  "discard_reasons": {{
    "excluded_source": 0,
    "duplicate": 0,
    "irrelevant": 0
  }}
}}

注意：
- signals 最多 10 個，選最重要的
- sentiment 必須有數據支撐，不得空洞評論
- 只列出最近 24 小時的新訊號
"""


def run_sentiment_watcher(
    active_theses: list[dict] | None = None,
    tgri_score: float = 0.0,
    trigger: str = "scheduled",
    today_str: str | None = None,
) -> dict:
    """執行輿情掃描。"""
    if active_theses is None:
        active_theses = []

    search_scope = build_search_scope(active_theses, tgri_score)
    prompt = _build_prompt(search_scope, trigger, today_str=today_str)

    logger.info(f"SentimentWatcher: scanning ({trigger}), {len(active_theses)} active theses")

    try:
        response = _gemini_client.models.generate_content(
            model=GEMINI_FLASH_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            ),
        )
        raw_text = _extract_json_text(response)

        result = json.loads(raw_text)
        signals = result.get("signals", [])
        logger.info(
            f"SentimentWatcher: {len(signals)} signals, "
            f"sentiment={result.get('aggregate', {}).get('overall_sentiment', 'unknown')}"
        )
        return result

    except json.JSONDecodeError as e:
        logger.error(f"SentimentWatcher JSON parse error: {e}")
        return _fallback_sentiment(trigger)
    except Exception as e:
        logger.error(f"SentimentWatcher error: {e}")
        return _fallback_sentiment(trigger)


def _fallback_sentiment(trigger: str) -> dict:
    return {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "trigger": trigger,
        "signals": [],
        "aggregate": {
            "overall_sentiment": MISSING_DATA,
            "dominant_theme": MISSING_DATA,
            "noise_level": MISSING_DATA,
        },
        "items_discarded": 0,
        "discard_reasons": {},
        "_error": "SentimentWatcher failed",
    }


def should_trigger_event(data_package: dict) -> bool:
    """判斷是否需要事件觸發掃描（資產單日波動 > 2% 或 VIX 單小時變化 > 10%）。"""
    for key in ["gold", "spx", "brent", "wti", "dxy"]:
        item = data_package.get(key, {})
        change = abs(item.get("change_pct", 0) or 0)
        if change > 2.0:
            logger.info(f"SentimentWatcher event trigger: {key} change={change:.1f}%")
            return True

    vix = data_package.get("vix", {})
    vix_change = abs(vix.get("change_pct", 0) or 0)
    if vix_change > 10.0:
        logger.info(f"SentimentWatcher event trigger: VIX change={vix_change:.1f}%")
        return True

    return False
