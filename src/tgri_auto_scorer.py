from __future__ import annotations
"""TGRI Auto-Scorer — Gemini Flash + Google Search Grounding 自動評分。

每日自動搜尋台海相關新聞，評分四個 TGRI 輸入：
- adiz_intrusions (0-10)
- pla_activity_level (0-3)
- us_tw_contact_frequency (0-10)
- trade_policy_risk (0-10)

寫入 manual_inputs.json。失敗時 silently fallback 到現有值。
"""

import json
import logging
import re
from datetime import datetime, timezone

from google import genai
from google.genai import types

from src.config import GEMINI_API_KEY, GEMINI_ENABLE_DAILY_SEARCH, GEMINI_SEARCH_MODEL, MEMORY_DIR
from src.prompts.language_policy import TRADITIONAL_CHINESE_ONLY

logger = logging.getLogger(__name__)

_gemini_client = genai.Client(api_key=GEMINI_API_KEY)

MANUAL_INPUTS_PATH = MEMORY_DIR / "manual_inputs.json"

_SCORER_PROMPT = TRADITIONAL_CHINESE_ONLY + "\n\n" + """你是台灣地緣政治風險評估員。根據最近 24 小時的新聞，分別獨立評估以下四個指標。

⚠️ 重要：每個指標必須獨立評分，只根據該指標自身的搜尋結果。不要讓一個領域的緊張影響另一個領域的分數。

⚠️ 日期驗證：只採用能確認發生在最近 7 天內的事件。忽略無法確認日期的消息。

1. adiz_intrusions (0-10)：中國軍機進入台灣 ADIZ 的頻率和規模
   - 0-2：無報導或常態巡邏
   - 3-5：有報導，頻率略高於常態
   - 6-8：密集入侵，多架次或特殊機型
   - 9-10：大規模軍事演習等級

2. pla_activity_level (0-3)：解放軍整體軍事活動（不含 ADIZ）
   - 0：無特殊活動
   - 1：常態演訓
   - 2：有報導的具體軍事行動或部署變化
   - 3：大規模軍演或導彈試射

3. us_tw_contact_frequency (0-10)：美台官方接觸頻率
   - 0-2：無公開接觸報導
   - 3-5：常態外交互動（AIT 聲明、經貿對話等）
   - 6-8：高層級接觸（國會代表團訪台、部長級通話、軍售公告）
   - 9-10：極高頻接觸（總統級通話、國務卿級會面、重大軍事合作宣布）

4. trade_policy_risk (0-10)：美中台貿易/科技制裁動態
   - 0-3：穩定，無新政策
   - 4-6：有新關稅或制裁討論
   - 7-10：重大政策變化（晶片禁令升級、新關稅生效等）

搜尋以下關鍵詞的最新新聞：
- Taiwan ADIZ intrusion PLA military aircraft
- China military exercise Taiwan Strait PLA navy
- US Taiwan relations AIT congress arms sale
- US China trade tariff semiconductor sanctions

根據搜尋結果評估。如果某個指標沒有找到相關新聞，使用基線值（adiz=2, pla=1, us_tw=3, trade=3）。

輸出純 JSON，不要任何說明文字：
{"adiz_intrusions": float, "pla_activity_level": float, "us_tw_contact_frequency": float, "trade_policy_risk": float, "reasoning": "一句話摘要，標注資料日期"}
"""


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


def _load_existing() -> dict:
    """讀取現有 manual_inputs.json。"""
    try:
        return json.loads(MANUAL_INPUTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_inputs(data: dict) -> None:
    """寫入 manual_inputs.json。"""
    MANUAL_INPUTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANUAL_INPUTS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def auto_score_tgri_inputs() -> dict:
    """用 Gemini Flash + Google Search Grounding 自動評分 TGRI 手動輸入。

    搜尋最近 24h 的台海相關新聞，輸出四個分數。
    寫入 manual_inputs.json。失敗時 silently fallback 到現有值。

    Returns: {"adiz_intrusions": float, "pla_activity_level": float,
              "us_tw_contact_frequency": float, "trade_policy_risk": float}
    """
    existing = _load_existing()
    if not GEMINI_ENABLE_DAILY_SEARCH:
        logger.info("TGRI Auto-Scorer: skipped because GEMINI_ENABLE_DAILY_SEARCH=false")
        return _fallback_scores(existing)

    try:
        response = _gemini_client.models.generate_content(
            model=GEMINI_SEARCH_MODEL,
            contents=_SCORER_PROMPT,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                response_mime_type="application/json",
            ),
        )
        raw_text = _extract_json_text(response)
        scores = json.loads(raw_text)

        # 驗證並限制範圍
        adiz = min(max(float(scores.get("adiz_intrusions", 2.0)), 0), 10.0)
        pla = min(max(float(scores.get("pla_activity_level", 1.0)), 0), 3.0)
        us_tw = min(max(float(scores.get("us_tw_contact_frequency", 3.0)), 0), 10.0)
        trade = min(max(float(scores.get("trade_policy_risk", 3.0)), 0), 10.0)
        reasoning = scores.get("reasoning", "")

        result = {
            "adiz_intrusions": round(adiz, 1),
            "pla_activity_level": round(pla, 1),
            "us_tw_contact_frequency": round(us_tw, 1),
            "trade_policy_risk": round(trade, 1),
        }

        # 寫入 manual_inputs.json
        result["_last_updated"] = datetime.now(timezone.utc).isoformat()
        result["_auto_scored"] = True
        result["_auto_reasoning"] = reasoning[:200] if reasoning else ""
        _save_inputs(result)

        logger.info(
            f"TGRI Auto-Scorer: adiz={result['adiz_intrusions']}, "
            f"pla={result['pla_activity_level']}, "
            f"us_tw={result['us_tw_contact_frequency']}, "
            f"trade={result['trade_policy_risk']} — {reasoning[:80]}"
        )
        return result

    except json.JSONDecodeError as e:
        logger.warning(f"TGRI Auto-Scorer: JSON parse fallback activated: {e}")
        return _fallback_scores(existing)
    except Exception as e:
        logger.warning(f"TGRI Auto-Scorer: failed (will use existing values): {e}")
        return _fallback_scores(existing)


def _fallback_scores(existing: dict) -> dict:
    """失敗時回傳現有值或基線值。"""
    return {
        "adiz_intrusions": existing.get("adiz_intrusions", 2.0),
        "pla_activity_level": existing.get("pla_activity_level", 1.0),
        "us_tw_contact_frequency": existing.get("us_tw_contact_frequency", 3.0),
        "trade_policy_risk": existing.get("trade_policy_risk", 3.0),
    }
