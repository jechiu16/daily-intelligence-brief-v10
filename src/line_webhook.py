from __future__ import annotations
"""LINE Webhook Server — 接收訊息，用 Sonnet + 今日 DIB 快照回答。"""

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
from flask import Flask, abort, request

from src.config import (
    ANTHROPIC_API_KEY, LINE_CHANNEL_SECRET,
    MEMORY_DIR, SNAPSHOTS_DIR, SONNET_MODEL,
)
from src.line_publisher import _push

logger = logging.getLogger(__name__)
app = Flask(__name__)
_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _today_tw() -> str:
    """今日台北日期（UTC+8 固定偏移，不依賴 zoneinfo）。"""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


def _validate_signature(body: bytes, sig_header: str) -> bool:
    """驗證 LINE X-Line-Signature（HMAC-SHA256 + base64）。"""
    if not LINE_CHANNEL_SECRET:
        return True  # dev mode：secret 未設則略過驗證
    computed = base64.b64encode(
        hmac.new(LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(computed, sig_header)


def _load_snapshot(date_str: str) -> dict:
    path = Path(SNAPSHOTS_DIR) / f"{date_str}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Failed to load snapshot {date_str}: {e}")
        return {}


def _load_l3() -> dict:
    path = Path(MEMORY_DIR) / "l3.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"active_theses": []}


def _build_context(snapshot: dict, l3: dict) -> str:
    """組裝 ~6K token 上下文供 Sonnet 參考。"""
    lines = [f"=== DIB 今日快照 ({_today_tw()}) ==="]

    # Regime
    meta = snapshot.get("metadata", {})
    lines.append(
        f"市場機制：{meta.get('regime', '未知')}（第{meta.get('regime_day', '?')}天）"
    )
    notion_url = meta.get("notion_url", "")
    if notion_url:
        lines.append(f"完整報告：{notion_url}")

    # Core tension
    ct = snapshot.get("core_tension", "")
    if ct and ct != "MISSING_DATA":
        lines.append(f"核心張力：{ct}")

    # Inference chain（前5）
    chain = snapshot.get("inference_chain", [])
    if chain:
        lines.append("\n--- 推理鏈（前5）---")
        for i, item in enumerate(chain[:5]):
            if not isinstance(item, dict):
                continue
            conf = item.get("adjusted_confidence") or item.get("raw_confidence", "")
            conf_str = f"（信心 {conf:.0%}）" if isinstance(conf, float) else ""
            claim = item.get("claim", "")[:120]
            lines.append(f"{i+1}. {claim}{conf_str}")
            mech = item.get("mechanism", "")
            if mech:
                lines.append(f"   機制：{mech[:80]}")

    # 市場數據摘要
    md = snapshot.get("market_data", {})
    if md:
        lines.append("\n--- 市場數據 ---")
        for key, label in [
            ("gold", "黃金"), ("spx", "標普500"), ("vix", "VIX"),
            ("dxy", "美元指數"), ("usdtwd", "美元/台幣"),
        ]:
            item = md.get(key, {})
            if not isinstance(item, dict):
                continue
            price = item.get("price") or item.get("value", "N/A")
            chg = item.get("change_pct")
            chg_str = f" ({chg:+.1f}%)" if isinstance(chg, (int, float)) else ""
            lines.append(f"{label}: {price}{chg_str}")

    # TGRI
    tgri = snapshot.get("tgri", {})
    if isinstance(tgri, dict):
        td = tgri.get("tension_display") or f"TGRI {tgri.get('score', '?')}"
        lines.append(f"台海張力：{td}")

    # Active theses
    theses = l3.get("active_theses", [])
    if theses:
        lines.append("\n--- 活躍論題 ---")
        for t in theses:
            if isinstance(t, dict):
                lines.append(f"• [{t.get('id', '')}] {t.get('title', '')}")

    return "\n".join(lines)


_SYSTEM_PROMPT = """你是 DIB（每日情報簡報）分析師助理。

職責：
- 基於今日 DIB 快照（市場機制、推理鏈、市場數據、台海張力）回答用戶問題
- 繁體中文回答，300-600 字為宜（LINE 5000 字元上限）
- 對快照以外的事不過度延伸，不確定就明說
- 不捏造數據，若數據缺失請告知

語氣：專業簡潔，適合台灣投資人。"""


def _ask_sonnet(context: str, question: str) -> str:
    resp = _get_client().messages.create(
        model=SONNET_MODEL,
        max_tokens=1500,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"{context}\n\n===\n問：{question}"}],
    )
    return resp.content[0].text.strip()


def _handle(user_text: str) -> None:
    """載入快照、呼叫 Sonnet、推播回答。全程非致命。"""
    snapshot = _load_snapshot(_today_tw())
    if not snapshot:
        _push("今日快照尚未生成，請在 07:30 後再試。")
        return
    try:
        ctx = _build_context(snapshot, _load_l3())
        _push(_ask_sonnet(ctx, user_text))
    except anthropic.APIError as e:
        logger.error(f"Sonnet API error: {e}")
        _push("AI 分析師暫時無法回應，請稍後再試。")
    except Exception as e:
        logger.error(f"Webhook handler error: {e}")
        _push("系統錯誤，請稍後再試。")


@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_data()
    sig = request.headers.get("X-Line-Signature", "")

    if not _validate_signature(body, sig):
        logger.warning("Invalid LINE signature")
        abort(400)

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        abort(400)

    for event in payload.get("events", []):
        if event.get("type") != "message":
            continue
        msg = event.get("message", {})
        if msg.get("type") != "text":
            continue
        text = msg.get("text", "").strip()
        if text:
            _handle(text)

    return "OK", 200
