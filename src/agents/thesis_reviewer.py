from __future__ import annotations
"""Thesis Reviewer — 用 Gemini + Google Search 每日審核 thesis。

Pipeline Step 15.6（位於 ThesisPromoter 之後）。

職責：
1. Proposed theses → 搜尋當前市場訊號 → accept / reject / pending
   - accept: 有佐證，移至 active/
   - reject: 有直接反例，刪除
   - pending: 訊號不足，超過 PENDING_TTL_DAYS 天後自動 activate
2. Active theses → 搜尋最新發展 → confidence_delta + 注意事項
   - 若搜尋發現 invalidator 條件已達成 → 標記 triggered=True

輸出（寫入 memory + 回傳）：
- attention_items: 今日需要注意的 thesis 動態（精簡，給人看）
- activated_ids: 本次升格的 proposed thesis ID
- updated_ids: 本次更新 confidence 的 active thesis ID
"""

import json
import logging
import re
import shutil
from datetime import date as _date, datetime, timedelta, timezone
from pathlib import Path

from google import genai
from google.genai import types

from src.config import GEMINI_API_KEY, GEMINI_FLASH_MODEL, THESES_DIR

logger = logging.getLogger(__name__)

PENDING_TTL_DAYS = 2   # pending 超過此天數 → 自動 activate
MIN_CONFIDENCE_TO_REVIEW = 0.35   # 低於此值的 proposed 直接 pending

THESES_ACTIVE   = THESES_DIR / "active"
THESES_PROPOSED = THESES_DIR / "proposed"
THESES_CLOSED   = THESES_DIR / "closed"

_gemini_client = genai.Client(api_key=GEMINI_API_KEY)


# ── Gemini 搜尋呼叫 ──────────────────────────────────────────────────────────

def _search_thesis(thesis: dict, today_str: str, role: str) -> dict:
    """對單一 thesis 呼叫 Gemini + Google Search，回傳結構化評估。

    role: "review"（proposed）或 "update"（active）
    """
    title     = thesis.get("title", "")
    rationale = thesis.get("rationale", "")
    invalidators = [inv.get("condition", "") for inv in thesis.get("invalidators", []) if not inv.get("triggered")]

    if role == "review":
        task = f"""你是市場 thesis 審核員。今日日期：{today_str}。

請搜尋以下 thesis 的當前市場訊號，判斷它是否應該被採納追蹤。

Thesis 標題：{title}
Thesis 邏輯：{rationale}

請搜尋：
1. 支持此 thesis 的最新數據或事件（過去 14 天內）
2. 反對此 thesis 的最新數據或事件
3. 與此 thesis 直接相關的重要新聞

根據搜尋結果，輸出以下 JSON（純 JSON，不加任何 markdown）：
{{
  "decision": "accept" | "reject" | "pending",
  "confidence_initial": 0.0-1.0,
  "evidence_for": ["一句話描述佐證1", "佐證2"],
  "evidence_against": ["一句話描述反例1"],
  "attention_note": "給投資人看的一句話摘要（若 decision=accept），或空字串",
  "reasoning": "決策理由（2-3句）"
}}

決策規則：
- accept: 有明確佐證，且無直接反例
- reject: 有直接反例（需要具體數據或事件）
- pending: 訊號不足或混合訊號"""

    else:  # update
        invalidator_str = "\n".join(f"- {c}" for c in invalidators) if invalidators else "（無）"
        task = f"""你是市場 thesis 追蹤員。今日日期：{today_str}。

請搜尋以下 active thesis 的最新發展，評估其信度變化。

Thesis 標題：{title}
Thesis 邏輯：{rationale}
待觸發的 invalidator 條件：
{invalidator_str}

請搜尋：
1. 過去 7 天內直接相關的新聞或數據
2. 是否有任何 invalidator 條件已被觸發

輸出以下 JSON（純 JSON，不加任何 markdown）：
{{
  "confidence_delta": -0.15 到 +0.15 之間的浮點數（0 代表無變化）,
  "invalidator_triggered": true | false,
  "attention_note": "若有重要發展，用一句話描述（給投資人看）；若無變化填空字串",
  "evidence_summary": "最多兩句話，描述本週與此 thesis 最相關的發展"
}}

注意：只在確有直接數據/事件支持時才填 invalidator_triggered=true。"""

    try:
        response = _gemini_client.models.generate_content(
            model=GEMINI_FLASH_MODEL,
            contents=task,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.2,
            ),
        )
        raw = response.text or ""
        # 清除 markdown fence
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip().strip("`").strip()
        # 擷取第一個 JSON 物件
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            raise ValueError("No JSON found in response")
        return json.loads(m.group(0))
    except Exception as e:
        logger.warning(f"thesis_reviewer _search_thesis({title[:30]}): {e}")
        return {}


# ── Proposed thesis 審核 ─────────────────────────────────────────────────────

def _review_proposed(today_str: str) -> tuple[list[str], list[str]]:
    """審核所有 proposed theses，回傳 (activated_ids, rejected_ids)。"""
    if not THESES_PROPOSED.exists():
        return [], []

    THESES_ACTIVE.mkdir(parents=True, exist_ok=True)
    THESES_CLOSED.mkdir(parents=True, exist_ok=True)

    activated, rejected = [], []

    for f in sorted(THESES_PROPOSED.glob("*.json")):
        try:
            thesis = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        tid = thesis.get("id", f.stem)
        created = thesis.get("created_date", today_str)
        days_pending = (_date.fromisoformat(today_str) - _date.fromisoformat(created)).days

        # 低信心 or 超過 TTL → 直接 activate，不搜尋
        if thesis.get("initial_confidence", 0) < MIN_CONFIDENCE_TO_REVIEW or days_pending >= PENDING_TTL_DAYS:
            _activate(f, thesis, today_str, note="auto-activate (TTL/low-confidence)")
            activated.append(tid)
            logger.info(f"ThesisReviewer: auto-activate {tid} (days={days_pending})")
            continue

        # Gemini 搜尋審核
        result = _search_thesis(thesis, today_str, role="review")
        decision = result.get("decision", "pending")

        if decision == "accept":
            conf = result.get("confidence_initial")
            if conf:
                thesis["initial_confidence"] = round(float(conf), 3)
                thesis["current_confidence"] = thesis["initial_confidence"]
                thesis["confidence_history"].append({
                    "date": today_str, "value": thesis["current_confidence"],
                    "note": result.get("reasoning", "reviewer-accepted")
                })
            thesis["reviewer_evidence"] = {
                "for": result.get("evidence_for", []),
                "against": result.get("evidence_against", []),
            }
            _activate(f, thesis, today_str, note="reviewer-accepted")
            activated.append(tid)
            logger.info(f"ThesisReviewer: accept {tid}")

        elif decision == "reject":
            reason = result.get("reasoning", "reviewer-rejected")
            closed = dict(thesis)
            closed["status"] = "closed"
            closed["close_reason"] = reason
            closed["closed_date"] = today_str
            dst = THESES_CLOSED / f.name
            dst.write_text(json.dumps(closed, indent=2, ensure_ascii=False), encoding="utf-8")
            f.unlink()
            rejected.append(tid)
            logger.info(f"ThesisReviewer: reject {tid} — {reason[:60]}")

        else:  # pending — 等下一天
            thesis["last_review"] = today_str
            f.write_text(json.dumps(thesis, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info(f"ThesisReviewer: pending {tid}")

    return activated, rejected


def _activate(proposed_path: Path, thesis: dict, today_str: str, note: str):
    """將 thesis 從 proposed/ 移至 active/。"""
    thesis["status"] = "active"
    thesis["activated_date"] = today_str
    thesis.setdefault("updates", []).append({"date": today_str, "note": note})
    dst = THESES_ACTIVE / proposed_path.name
    dst.write_text(json.dumps(thesis, indent=2, ensure_ascii=False), encoding="utf-8")
    proposed_path.unlink()


# ── Active thesis 每日更新 ────────────────────────────────────────────────────

def _update_active(today_str: str) -> list[dict]:
    """搜尋所有 active theses 的最新發展，回傳 attention_items（需要注意的事）。"""
    if not THESES_ACTIVE.exists():
        return []

    attention_items = []

    for f in sorted(THESES_ACTIVE.glob("*.json")):
        try:
            thesis = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        tid   = thesis.get("id", f.stem)
        title = thesis.get("title", "")

        result = _search_thesis(thesis, today_str, role="update")
        if not result:
            continue

        delta     = float(result.get("confidence_delta", 0.0))
        triggered = bool(result.get("invalidator_triggered", False))
        note      = result.get("attention_note", "")
        summary   = result.get("evidence_summary", "")

        changed = False

        # 更新信度
        if abs(delta) >= 0.02:
            old_conf = thesis.get("current_confidence", thesis.get("initial_confidence", 0.5))
            new_conf = round(max(0.05, min(0.95, old_conf + delta)), 3)
            thesis["current_confidence"] = new_conf
            thesis.setdefault("confidence_history", []).append({
                "date": today_str, "value": new_conf,
                "note": summary or "reviewer-daily-update"
            })
            changed = True

        # 觸發 invalidator
        if triggered:
            for inv in thesis.get("invalidators", []):
                if not inv.get("triggered"):
                    inv["triggered"] = True
                    inv["triggered_date"] = today_str
                    changed = True
                    logger.info(f"ThesisReviewer: invalidator triggered for {tid}")

        # 記錄更新
        if summary or note:
            thesis.setdefault("updates", []).append({
                "date": today_str,
                "note": summary,
                "confidence_delta": delta,
                "invalidator_triggered": triggered,
            })
            changed = True

        if changed:
            f.write_text(json.dumps(thesis, indent=2, ensure_ascii=False), encoding="utf-8")

        # 收集需要注意的事
        if note:
            attention_items.append({
                "thesis_id": tid,
                "title": title,
                "attention": note,
                "confidence_delta": delta,
                "invalidator_triggered": triggered,
            })
        elif triggered:
            attention_items.append({
                "thesis_id": tid,
                "title": title,
                "attention": f"⚠️ Invalidator 條件觸發，thesis 可能已不成立",
                "confidence_delta": delta,
                "invalidator_triggered": True,
            })

    return attention_items


# ── 主入口 ───────────────────────────────────────────────────────────────────

def run_thesis_reviewer(today_str: str) -> dict:
    """Pipeline Step 15.6 入口。

    回傳：
    {
      "activated_ids": [...],
      "rejected_ids": [...],
      "attention_items": [
        {"thesis_id": "T001", "title": "...", "attention": "一句話摘要", ...}
      ],
      "summary": "今日 thesis 動態摘要（給人看）"
    }
    """
    logger.info(f"ThesisReviewer: starting for {today_str}")

    activated, rejected = _review_proposed(today_str)
    attention_items      = _update_active(today_str)

    # 同步 L3
    try:
        from src.memory_manager import sync_theses_dir_to_l3
        sync_theses_dir_to_l3()
    except Exception as e:
        logger.warning(f"ThesisReviewer: L3 sync failed (non-fatal): {e}")

    # 生成人類可讀摘要
    parts = []
    if activated:
        parts.append(f"新升格 thesis：{', '.join(activated)}")
    if rejected:
        parts.append(f"已拒絕：{', '.join(rejected)}")
    if attention_items:
        highlights = [f"[{a['thesis_id']}] {a['attention']}" for a in attention_items if a.get("attention")]
        if highlights:
            parts.append("需注意：" + "；".join(highlights))
    summary = "　".join(parts) if parts else "今日 thesis 無重大變動"

    logger.info(
        f"ThesisReviewer: activated={activated}, rejected={rejected}, "
        f"attention={len(attention_items)} items"
    )

    return {
        "activated_ids": activated,
        "rejected_ids": rejected,
        "attention_items": attention_items,
        "summary": summary,
    }
