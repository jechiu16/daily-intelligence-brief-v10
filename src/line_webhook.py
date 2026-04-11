from __future__ import annotations
"""LINE Webhook Server — 多輪對話、Gemini 聯網、白名單、用量控制。"""

import base64
import hashlib
import hmac
import json
import logging
import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests as _requests
from flask import Flask, abort, request
from google import genai
from google.genai import types as _gtypes

from src.config import (
    GEMINI_API_KEY, GEMINI_FLASH_MODEL,
    GITHUB_REPO, GITHUB_TOKEN,
    LINE_ALLOWED_USERS, LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET,
    LINE_DAILY_LIMIT, LINE_TARGET_ID,
    MEMORY_DIR, SNAPSHOTS_DIR,
)
from src.line_publisher import _push

logger = logging.getLogger(__name__)
app = Flask(__name__)

# ── Gemini client ───────────────────────────────────────────────────────────
_gemini_client: genai.Client | None = None

def _get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


# ── Deduplication（LINE 重試去重） ─────────────────────────────────────────
_SEEN_EVENT_IDS: OrderedDict[str, None] = OrderedDict()
_DEDUP_MAX = 200
_DEDUP_LOCK = threading.Lock()


def _is_duplicate(event_id: str) -> bool:
    with _DEDUP_LOCK:
        if event_id in _SEEN_EVENT_IDS:
            return True
        _SEEN_EVENT_IDS[event_id] = None
        while len(_SEEN_EVENT_IDS) > _DEDUP_MAX:
            _SEEN_EVENT_IDS.popitem(last=False)
        return False


# ── Session store（per-user 對話記憶） ────────────────────────────────────
# Gemini messages 格式：{"role": "user"/"model", "parts": [{"text": "..."}]}
_SESSIONS: dict[str, dict] = {}
_SESSION_LOCK = threading.Lock()
_SESSION_TTL_MINUTES = 30
_HISTORY_FIXED = 2    # 快照 inject 的前 2 條永久保留
_HISTORY_RECENT = 18  # 最近保留條數（9 輪對話）


def _expire_old_sessions() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_SESSION_TTL_MINUTES)
    expired = [uid for uid, s in _SESSIONS.items() if s["last_active"] < cutoff]
    for uid in expired:
        del _SESSIONS[uid]


def _get_or_create_session(user_id: str) -> dict:
    with _SESSION_LOCK:
        _expire_old_sessions()
        if user_id not in _SESSIONS:
            _SESSIONS[user_id] = {
                "messages": [],
                "last_active": datetime.now(timezone.utc),
                "context_injected": False,
                "daily_count": 0,
                "count_date": "",
            }
        return _SESSIONS[user_id]


def _touch_session(user_id: str) -> None:
    with _SESSION_LOCK:
        if user_id in _SESSIONS:
            _SESSIONS[user_id]["last_active"] = datetime.now(timezone.utc)
            _SESSIONS[user_id]["context_injected"] = True


def _trim_history(session: dict) -> None:
    msgs = session["messages"]
    if len(msgs) > _HISTORY_FIXED + _HISTORY_RECENT:
        session["messages"] = msgs[:_HISTORY_FIXED] + msgs[_HISTORY_FIXED:][-_HISTORY_RECENT:]


# ── 日期工具 ────────────────────────────────────────────────────────────────
def _today_tw() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


# ── LINE 簽名驗證 ───────────────────────────────────────────────────────────
def _validate_signature(body: bytes, sig_header: str) -> bool:
    if not LINE_CHANNEL_SECRET:
        return True
    computed = base64.b64encode(
        hmac.new(LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(computed, sig_header)


# ── GitHub 資料載入 ─────────────────────────────────────────────────────────
def _fetch_github_json(repo_path: str):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{repo_path}"
    try:
        resp = _requests.get(
            url,
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3.raw",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return json.loads(resp.text)
        logger.warning(f"GitHub fetch {repo_path}: HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"GitHub fetch {repo_path} failed: {e}")
    return None


def _load_snapshot(date_str: str) -> dict:
    # 優先：今日快照
    gh = _fetch_github_json(f"memory/daily_snapshots/{date_str}.json")
    if gh is not None:
        return gh
    path = Path(SNAPSHOTS_DIR) / f"{date_str}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Failed to load snapshot {date_str}: {e}")

    # Fallback：本地最新快照
    candidates = sorted(Path(SNAPSHOTS_DIR).glob("*.json"), reverse=True)
    for p in candidates:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            logger.warning(f"Snapshot {date_str} not found, using fallback: {p.name}")
            # 在 metadata 標記為 fallback
            data.setdefault("metadata", {})["_fallback"] = p.stem
            return data
        except Exception:
            continue
    return {}


def _load_l3() -> dict:
    gh = _fetch_github_json("memory/l3.json")
    if gh is not None:
        return gh
    path = Path(MEMORY_DIR) / "l3.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"active_theses": []}


# ── Context 組裝 ────────────────────────────────────────────────────────────
def _build_context(snapshot: dict, l3: dict) -> str:
    lines = [f"=== DIB 今日快照 ({_today_tw()}) ==="]

    meta = snapshot.get("metadata", {})
    lines.append(
        f"市場機制：{meta.get('regime', '未知')}（第{meta.get('regime_day', '?')}天）"
    )
    notion_url = meta.get("notion_url", "")
    if notion_url:
        lines.append(f"完整報告：{notion_url}")

    ct = snapshot.get("core_tension", "")
    if ct and ct != "MISSING_DATA":
        lines.append(f"核心張力：{ct}")

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

    tgri = snapshot.get("tgri", {})
    if isinstance(tgri, dict):
        td = tgri.get("tension_display") or f"TGRI {tgri.get('score', '?')}"
        lines.append(f"台海張力：{td}")

    theses = l3.get("active_theses", [])
    if theses:
        lines.append("\n--- 活躍論題 ---")
        for t in theses:
            if isinstance(t, dict):
                lines.append(f"• [{t.get('id', '')}] {t.get('title', '')}")

    return "\n".join(lines)


# ── System Prompt ───────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """你是 DIB（每日情報簡報）分析師助理，服務台灣投資人。

== 核心職責 ==
- 基於今日 DIB 快照（市場機制、推理鏈、市場數據、台海張力）回答問題
- 記憶本次對話的所有上下文，可回答追問
- 快照以外的即時資訊，自動搜尋網路，不要憑空推斷
- 不確定就明說，不捏造數據

== LINE 格式規範（嚴格遵守）==
- 純文字輸出，禁止使用任何 Markdown 語法
- 禁止：# 標題、**粗體**、*斜體**、`程式碼`、--- 分隔線、[連結](url)
- 分段用空行（換行兩次），條列用「・」或數字加點，強調用【】或「」
- 章節標題用 emoji 開頭，例如：📌 核心觀點、📈 數據解讀
- 分隔線用：─────────────────
- 繁體中文，300-500 字為宜（LINE 5000 字元上限，但太長用戶不會讀）
- 有搜尋網路時，末尾附上：資料來源：網路搜尋（{today}）"""


# ── 核心對話引擎（Gemini） ──────────────────────────────────────────────────
def _ask_gemini_with_history(user_id: str, user_text: str, session: dict) -> str:
    """
    多輪 Gemini 呼叫，原生 google_search grounding。
    直接 mutate session["messages"]。
    """
    client = _get_gemini_client()
    today = _today_tw()
    system = _SYSTEM_PROMPT.replace("{today}", today)

    # 第一輪：注入快照（今日優先，fallback 最新日期）
    if not session["context_injected"]:
        snapshot = _load_snapshot(today)
        if not snapshot:
            return "今日快照尚未生成，請在 07:30 後再試。"
        fallback_date = snapshot.get("metadata", {}).get("_fallback")
        ctx = _build_context(snapshot, _load_l3())
        if fallback_date:
            ctx = f"⚠️ 今日快照尚未生成，使用 {fallback_date} 快照\n\n" + ctx
        session["messages"] = [
            {"role": "user",  "parts": [{"text": f"[系統背景資料，請記憶]\n{ctx}"}]},
            {"role": "model", "parts": [{"text": "已收到今日 DIB 快照，可以開始提問。"}]},
        ]

    _trim_history(session)
    session["messages"].append({"role": "user", "parts": [{"text": user_text}]})

    response = client.models.generate_content(
        model=GEMINI_FLASH_MODEL,
        contents=session["messages"],
        config=_gtypes.GenerateContentConfig(
            system_instruction=system,
            tools=[_gtypes.Tool(google_search=_gtypes.GoogleSearch())],
        ),
    )

    try:
        text = response.text or ""
    except Exception:
        parts = response.candidates[0].content.parts if response.candidates else []
        text = "".join(getattr(p, "text", "") or "" for p in parts)

    text = text.strip() or "（無回應）"
    session["messages"].append({"role": "model", "parts": [{"text": text}]})
    return text


# ── LINE Push（per-user） ───────────────────────────────────────────────────
def _push_to_user(user_id: str, text: str) -> bool:
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return False
    text = text[:4999]
    try:
        resp = _requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            data=json.dumps({
                "to": user_id,
                "messages": [{"type": "text", "text": text}],
            }),
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"LINE push to {user_id} failed: {e}")
        return False


# ── Background handler ──────────────────────────────────────────────────────
def _handle(user_id: str, user_text: str) -> None:
    if LINE_ALLOWED_USERS and user_id not in LINE_ALLOWED_USERS:
        _push_to_user(user_id, "此服務目前僅限授權用戶使用。")
        return

    session = _get_or_create_session(user_id)

    today = _today_tw()
    if session["count_date"] != today:
        session["daily_count"] = 0
        session["count_date"] = today
    if session["daily_count"] >= LINE_DAILY_LIMIT:
        _push_to_user(user_id, f"今日使用量已達上限（{LINE_DAILY_LIMIT} 則），明日重置。")
        return
    session["daily_count"] += 1

    try:
        reply = _ask_gemini_with_history(user_id, user_text, session)
        _touch_session(user_id)
        _push_to_user(user_id, reply)
    except Exception as e:
        logger.error(f"Webhook handler error for {user_id}: {e}")
        _push_to_user(user_id, "系統暫時無法回應，請稍後再試。")


# ── Webhook Endpoint ────────────────────────────────────────────────────────
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
        if not text:
            continue

        event_id = event.get("webhookEventId", "")
        user_id = event.get("source", {}).get("userId", "") or LINE_TARGET_ID

        if event_id and _is_duplicate(event_id):
            logger.info(f"Duplicate event {event_id[:12]}, skipping")
            continue

        threading.Thread(
            target=_handle,
            args=(user_id, text),
            daemon=True,
            name=f"handle-{event_id[:8] if event_id else 'noid'}",
        ).start()

    return "OK", 200


if __name__ == "__main__":
    port = int(__import__("os").getenv("PORT", __import__("os").getenv("WEBHOOK_PORT", "8080")))
    app.run(host="0.0.0.0", port=port, debug=False)
