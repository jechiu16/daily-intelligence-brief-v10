from __future__ import annotations
"""Notion Publisher — 把報告發布到 Notion Database。

v10.1: 地緣三層 callout、compass 表格、pipeline_version 標籤。
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

import requests

from src.config import (
    DATA_QUALITY_COLOR, MISSING_DATA, NOTION_API_KEY, NOTION_DATABASE_ID,
)

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"

# Emoji 清除（用於內文，不含 section 標題）
_EMOJI_RE = re.compile(
    r'[\U00002600-\U000027BF'
    r'\U0001F300-\U0001F9FF'
    r'\U0001FA00-\U0001FAFF'
    r'\U0001F1E0-\U0001F1FF'
    r'\U00002702-\U000027B0'
    r'\U0000FE00-\U0000FE0F'
    r'\U0001F000-\U0001FFFF]',
    flags=re.UNICODE,
)


def strip_emoji(text: str) -> str:
    """移除 emoji 字符（用於內文；section 標題不套用）。"""
    return _EMOJI_RE.sub('', text).strip()
NOTION_VERSION = "2022-06-28"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _notion_post(endpoint: str, payload: dict, max_retries: int = 3) -> dict | None:
    """L7 修正：加入指數退避重試，處理 Notion 429 rate limit。"""
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                f"{NOTION_API_BASE}/{endpoint}",
                headers=_headers(),
                json=payload,
                timeout=30,
            )
            if resp.status_code == 429:
                # Notion 回傳 Retry-After header（秒），預設指數退避
                retry_after = int(resp.headers.get("Retry-After", 2 ** attempt + 1))
                logger.warning(
                    f"Notion rate limited (attempt {attempt+1}/{max_retries}), "
                    f"retrying in {retry_after}s"
                )
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            logger.error(f"Notion API error {resp.status_code}: {resp.text[:300]}")
            return None
        except requests.Timeout:
            logger.error(f"Notion request timed out (attempt {attempt+1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return None
        except requests.RequestException as e:
            logger.error(f"Notion request failed: {e}")
            return None
    logger.error("Notion API: max retries exceeded")
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Rich text with color
# ═══════════════════════════════════════════════════════════════════════════

def _plain_text(text: str) -> dict:
    return {"type": "text", "text": {"content": text}}


def _format_number(value_str: str) -> str:
    """統一格式：保留原始語義，不做自動百分比轉換。

    設計原則：Narrator 寫 {{confirmed:0.22}}% 時，0.22 就是 0.22，不乘 100。
    已含單位符號或中文後綴者原樣保留。
    純數字依大小選擇精度：≥1000→整數，≥1→二位小數，<1→四位有效。
    """
    value_str = value_str.strip()
    # 已有單位符號，原樣保留
    if any(c in value_str for c in ('%', 'σ', '—')):
        return value_str
    # 含中文或括號後綴（如 "-0.4337（指數）"），原樣保留
    if any('\u4e00' <= c <= '\u9fff' or c in '（）()' for c in value_str):
        return value_str
    try:
        v = float(value_str)
        if abs(v) >= 1000:
            result = f"{v:.0f}"
        elif abs(v) >= 1:
            result = f"{v:.2f}"
        elif v == 0:
            result = "0"
        else:
            # <1：保留到有效位數（最多4位小數）
            result = f"{v:.4f}".rstrip('0').rstrip('.')
        if value_str.lstrip('-').startswith('+') or (value_str.startswith('+') and v > 0):
            if not result.startswith('+'):
                result = "+" + result
        return result
    except ValueError:
        return value_str


def parse_markdown_to_rich_text(text: str) -> list[dict]:
    """把含 markdown 和品質標記的文字解析成 Notion rich_text segments。

    支援（按優先順序）：
    - ``{{quality:value}}`` → 帶顏色（confirmed=藍/bold，cached=藍，estimated=紫，stale=灰，anomaly_flagged=黃，deviation=粉紅）
    - ``**text**`` → bold
    - ``*text*`` → italic
    其餘文字保持普通格式。純小數 (-1,1) 自動轉百分比顯示。
    """
    # 合併 pattern：品質標記 | 粗體 | 斜體
    pattern = re.compile(
        r'\{\{(\w+):([^}]+)\}\}'   # group 1=quality, 2=value
        r'|\*\*([^*]+)\*\*'        # group 3=bold text
        r'|\*([^*]+)\*'            # group 4=italic text
    )
    result = []
    last_end = 0

    for match in pattern.finditer(text):
        # 匹配前的普通文字
        if match.start() > last_end:
            plain = text[last_end:match.start()]
            if plain:
                result.append(_plain_text(plain))

        quality, value, bold_text, italic_text = (
            match.group(1), match.group(2),
            match.group(3), match.group(4),
        )

        if quality is not None:
            # {{quality:value}} → 顏色標記
            color = DATA_QUALITY_COLOR.get(quality, "default")
            result.append({
                "type": "text",
                "text": {"content": _format_number(value)},
                "annotations": {
                    "color": color,
                    "bold": quality == "confirmed",
                },
            })
        elif bold_text is not None:
            # **text** → bold
            result.append({
                "type": "text",
                "text": {"content": bold_text},
                "annotations": {"bold": True},
            })
        elif italic_text is not None:
            # *text* → italic
            result.append({
                "type": "text",
                "text": {"content": italic_text},
                "annotations": {"italic": True},
            })

        last_end = match.end()

    # 尾部剩餘文字
    if last_end < len(text):
        remaining = text[last_end:]
        if remaining:
            result.append(_plain_text(remaining))

    # 最後一道 strip：清除每個 segment 中殘留的孤立 **
    for seg in result:
        if seg.get("type") == "text" and "text" in seg:
            seg["text"]["content"] = seg["text"]["content"].replace("**", "")

    return result if result else [_plain_text(text)]


# 保留舊名稱供向後相容
parse_colored_numbers = parse_markdown_to_rich_text


def _rich_text(text: str) -> list[dict]:
    """帶 markdown + 顏色標記解析的 rich_text。"""
    return parse_markdown_to_rich_text(text)


def _heading(text: str, level: int = 2) -> dict:
    heading_type = f"heading_{level}"
    return {
        "object": "block",
        "type": heading_type,
        heading_type: {
            "rich_text": [_plain_text(text)],
        },
    }


def _paragraph(text: str, max_len: int = 1800) -> list[dict]:
    """Return one or more paragraph blocks, splitting at sentence boundaries if > max_len chars."""
    chunks = _split_long_text(text, max_len=max_len)
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _rich_text(chunk)},
        }
        for chunk in chunks
    ]


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _bullet(text: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": _rich_text(text)},
    }


def _callout(text: str, emoji: str = "⚠️") -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": _rich_text(text),
            "icon": {"type": "emoji", "emoji": emoji},
        },
    }


def _code_block(text: str, language: str = "plain text") -> dict:
    return {
        "object": "block",
        "type": "code",
        "code": {
            "rich_text": [_plain_text(text)],
            "language": language,
        },
    }


def _strip_quality_markers(text: str) -> str:
    """移除 {{quality:value}} → 只保留數值。"""
    return re.sub(r'\{\{\w+:([^}]+)\}\}', r'\1', text)


def _split_long_text(text: str, max_len: int = 800) -> list[str]:
    """把長文字切割成不超過 max_len 字元的段落，優先在句號/逗號斷句。
    不切割在 {{quality:value}} 模板內部，避免渲染時 regex 無法匹配。
    """
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        cut = text.rfind("。", 0, max_len)
        if cut == -1:
            cut = text.rfind("，", 0, max_len)
        if cut == -1:
            cut = max_len
        else:
            cut += 1
        # 確保切點不在 {{...}} 模板內部
        open_idx = text.rfind("{{", 0, cut)
        if open_idx != -1:
            close_idx = text.find("}}", open_idx)
            if close_idx == -1 or close_idx >= cut:
                # 切點落在模板內，退到模板起點之前
                cut = open_idx
                if cut == 0:
                    # 模板從頭開始，強制切到模板結束後
                    cut = text.find("}}", 0)
                    cut = cut + 2 if cut != -1 else max_len
        chunks.append(text[:cut])
        text = text[cut:].strip()
    return chunks


def _add_paragraphs(blocks: list, text: str) -> None:
    """把文字按段落拆分後加入 blocks，長段落自動截斷（>1800 字元）。"""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        blocks.extend(_paragraph(para))


# 資產 emoji 對照表
_ASSET_EMOJI = {
    "gold": "🥇", "黃金": "🥇",
    "spx": "📈", "s&p": "📈", "標普": "📈",
    "brent": "🛢️", "wti": "🛢️", "原油": "🛢️",
    "dxy": "💱", "美元": "💱",
    "usdjpy": "🇯🇵", "日圓": "🇯🇵",
    "usdtwd": "🇹🇼", "台幣": "🇹🇼",
    "us10y": "💵", "美債": "💵", "tips": "💵",
    "vix": "😱",
    "copper": "🔶", "銅": "🔶",
    "bdi": "🚢",
    "cpi": "📊",
}

def _asset_emoji(line: str) -> str:
    """從行內容推斷資產 emoji，找不到就回傳空字串。"""
    lower = line.lower()
    for key, emoji in _ASSET_EMOJI.items():
        if key in lower:
            return emoji
    return ""


# ═══════════════════════════════════════════════════════════════════════════
# Block builders per section
# ═══════════════════════════════════════════════════════════════════════════

# Section heading emoji 對照
_SECTION_EMOJI = {
    "今日張力": "⚡",
    "市場數據": "📊",
    "主線故事": "📰",
    "地緣政治": "🌍",
    "Thesis": "🎯",
    "配置羅盤": "🧭",
    "思考題": "❓",
}

def _section_heading(title: str, level: int = 2) -> dict:
    """帶 emoji prefix 的 section 標題。"""
    emoji = ""
    for key, e in _SECTION_EMOJI.items():
        if key in title:
            emoji = e + " "
            break
    return _heading(f"{emoji}{title}", level)


def _market_data_section(market_text: str) -> list[dict]:
    """把市場數據文字（含 ### 分組標記）轉成 Notion blocks。

    - ``### GROUP`` → heading_3
    - ``- label: {{quality:value}} ↑ (+0.5%)`` → paragraph（保留品質標記讓
      parse_markdown_to_rich_text 上色；移除 emoji）
    """
    blocks = []
    for line in market_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("### "):
            title = line[4:].strip()
            blocks.append(_heading(title, 3))
        elif line.startswith("- ") or line.startswith("* "):
            # 保留品質標記（{{confirmed:xxx}}）供 parse_markdown_to_rich_text 上色
            content = line[2:].replace("**", "").strip()
            content = strip_emoji(content)
            if content:
                blocks.extend(_paragraph(content))
        else:
            content = line.replace("**", "").strip()
            content = strip_emoji(content)
            if content:
                blocks.extend(_paragraph(content))
    return blocks


def _compass_section(compass_text: str) -> list[dict]:
    """把配置羅盤 markdown 表格轉成 Notion native table block。"""
    rows = _parse_markdown_table(compass_text)
    if rows and len(rows) >= 2:
        return [_notion_table(rows)]
    # fallback: 非表格格式，用 bullet
    blocks = []
    for line in compass_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("- ") or line.startswith("* "):
            clean = strip_emoji(line[2:].lstrip("*").strip())
            if clean:
                blocks.append(_bullet(clean))
        elif line.startswith("**"):
            clean = strip_emoji(line.strip("*").strip())
            if clean:
                blocks.append(_bullet(clean))
        else:
            blocks.extend(_paragraph(strip_emoji(line)))
    return blocks


def _parse_markdown_table(text: str) -> list[list[str]]:
    """解析 markdown 表格，回傳 [[cell, ...], ...]。跳過分隔線。"""
    rows = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or not line.startswith("|"):
            continue
        # 跳過分隔線 |---|---|
        if all(c in "-| :" for c in line):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if cells:
            rows.append(cells)
    return rows


def _notion_table(rows: list[list[str]]) -> dict:
    """建構 Notion API table block。解析 {{quality:value}} 為帶色標記。"""
    if not rows:
        return _bullet("（表格為空）")
    width = max(len(r) for r in rows)
    children = []
    for row in rows:
        # 補齊欄位數
        padded = row + [""] * (width - len(row))
        cells = [
            parse_markdown_to_rich_text(strip_emoji(cell)) or [_plain_text("")]
            for cell in padded
        ]
        children.append({
            "type": "table_row",
            "table_row": {"cells": cells},
        })
    return {
        "type": "table",
        "table": {
            "table_width": width,
            "has_column_header": True,
            "has_row_header": False,
            "children": children,
        },
    }


def _build_blocks(report: dict, coverage: float) -> list[dict]:
    sections = report.get("sections", {})
    blocks = []

    # 覆蓋率警告 + Brier Score
    header_parts = []
    if coverage < 0.85:
        header_parts.append(f"數據覆蓋率：{coverage:.0%}，部分判斷可能受影響")
    brier = report.get("_brier_score")
    if brier is not None:
        header_parts.append(f"校準 Brier Score：{brier:.4f}（越低越好）")
    if header_parts:
        blocks.append(_callout("　".join(header_parts), "⚠️"))

    # 一、今日張力
    blocks.append(_section_heading("一、今日張力", 2))
    tension = sections.get("tension", MISSING_DATA)
    _add_paragraphs(blocks, tension)
    blocks.append(_divider())

    # 二、市場數據全覽（分組 paragraph blocks）
    blocks.append(_section_heading("二、市場數據全覽", 2))
    market = report.get("_market_data_structured") or sections.get("market_data", "")
    blocks.extend(_market_data_section(market))
    blocks.append(_divider())

    # 三、主線故事
    blocks.append(_section_heading("三、主線故事", 2))
    story = sections.get("main_story", MISSING_DATA)
    _add_paragraphs(blocks, story)
    blocks.append(_divider())

    # 四、地緣政治（卡片式：TGRI + 邊陲）
    blocks.append(_section_heading("四、地緣政治", 2))

    # Card 1: TGRI
    tgri_card = sections.get("tgri_card", "")
    if tgri_card and tgri_card != MISSING_DATA:
        # 第一行是張力標籤，其餘是驅動因素說明
        tgri_parts = tgri_card.split("\n\n", 1)
        blocks.append(_callout(tgri_parts[0], "🌡️"))
        if len(tgri_parts) > 1:
            _add_paragraphs(blocks, tgri_parts[1])

    # Card 2: 邊陲
    periphery_card = sections.get("periphery_card", "")
    if periphery_card and periphery_card != MISSING_DATA:
        # 找標題
        title_match = re.search(r"##?\s*今日邊陲[：:]\s*(.+)", periphery_card)
        if title_match:
            blocks.append(_heading(f"🌍 今日邊陲：{title_match.group(1).strip()}", 3))
            body = periphery_card[title_match.end():].strip()
        else:
            blocks.append(_heading("🌍 今日邊陲", 3))
            body = periphery_card
        _add_paragraphs(blocks, body)

    # 向後兼容：舊版三層結構
    geo_tactical = sections.get("geopolitics_tactical", "")
    if geo_tactical and geo_tactical != MISSING_DATA and not tgri_card:
        blocks.append(_callout(geo_tactical, "🎯"))
    geo_operational = sections.get("geopolitics_operational", "")
    if geo_operational and geo_operational != MISSING_DATA and not tgri_card:
        blocks.append(_callout(geo_operational, "📊"))
    geo_structural = sections.get("geopolitics_structural", "")
    if geo_structural and geo_structural != MISSING_DATA and not tgri_card:
        blocks.append(_callout(geo_structural, "🏛️"))

    blocks.append(_divider())

    # 五、Thesis 追蹤（每個 thesis 用 callout，狀態 emoji prefix）
    blocks.append(_section_heading("五、Thesis 追蹤", 2))
    thesis = sections.get("thesis_tracking", MISSING_DATA)
    current_callout_lines = []
    current_emoji = "🎯"
    for line in thesis.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("### "):
            # 先 flush 前一個 thesis
            if current_callout_lines:
                blocks.append(_callout("\n".join(current_callout_lines), current_emoji))
                current_callout_lines = []
            title = line[4:]
            lower = title.lower()
            if any(k in lower for k in ["支持", "成立", "確認", "上調", "看漲", "維持"]):
                current_emoji = "✅"
            elif any(k in lower for k in ["挑戰", "失效", "駁回", "下調", "看跌"]):
                current_emoji = "⚠️"
            else:
                current_emoji = "➖"
            current_callout_lines = [title]
        elif line.startswith("- "):
            current_callout_lines.append(line)
        else:
            current_callout_lines.append(line)
    # flush 最後一個
    if current_callout_lines:
        blocks.append(_callout("\n".join(current_callout_lines), current_emoji))
    blocks.append(_divider())

    # 六、配置羅盤
    blocks.append(_section_heading("六、配置羅盤", 2))
    compass = sections.get("compass", "")
    # 嘗試解析為表格，若失敗則退回 bullet
    compass_blocks = _compass_section(compass)
    blocks.extend(compass_blocks)
    blocks.append(_divider())

    # 七、思考題
    blocks.append(_section_heading("七、思考題", 2))
    question = sections.get("question", MISSING_DATA)
    _add_paragraphs(blocks, question)

    return _strip_residual_templates(blocks)


_RAW_TEMPLATE_RE = re.compile(r'\{\{\w+:[^}]+\}\}')


def _strip_residual_templates(blocks: list) -> list:
    """最後防線：掃描所有 blocks 的 rich_text，strip 任何未被解析的 {{quality:value}}。
    正常情況不應觸發；觸發代表上游切割有問題，記錄 warning。
    """
    def _fix_content(content: str) -> str:
        if _RAW_TEMPLATE_RE.search(content):
            logger.warning(f"Residual template detected, stripping: {content[:80]}")
            return _RAW_TEMPLATE_RE.sub(lambda m: m.group(0).split(':', 1)[-1].rstrip('}'), content)
        return content

    def _fix_rich_text(rt_list: list) -> list:
        for seg in rt_list:
            if isinstance(seg, dict) and seg.get("type") == "text":
                text_obj = seg.get("text", {})
                if isinstance(text_obj, dict) and "content" in text_obj:
                    text_obj["content"] = _fix_content(text_obj["content"])
        return rt_list

    def _fix_block(block: dict) -> dict:
        btype = block.get("type", "")
        inner = block.get(btype, {})
        if isinstance(inner, dict):
            if "rich_text" in inner:
                inner["rich_text"] = _fix_rich_text(inner["rich_text"])
            # table 的 children
            if "children" in inner:
                for row in inner["children"]:
                    row_inner = row.get(row.get("type", ""), {})
                    if isinstance(row_inner, dict) and "cells" in row_inner:
                        row_inner["cells"] = [_fix_rich_text(cell) for cell in row_inner["cells"]]
        return block

    return [_fix_block(b) for b in blocks]


# ═══════════════════════════════════════════════════════════════════════════
# Main publisher
# ═══════════════════════════════════════════════════════════════════════════

def publish_to_notion(
    report: dict,
    today_str: str | None = None,
    coverage: float = 1.0,
    integrity_score: float = 1.0,
) -> str | None:
    """發布報告到 Notion，回傳 page URL。"""
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        logger.warning("Notion credentials not set, skipping publish")
        return None

    if today_str is None:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    metadata = report.get("metadata", {})
    regime = metadata.get("regime", MISSING_DATA)

    blocks = _build_blocks(report, coverage)

    # Notion 每次最多 100 blocks
    first_batch = blocks[:100]

    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Title": {
                "title": [_plain_text(f"Daily Intelligence Brief | {today_str}")]
            },
            "Date": {"date": {"start": today_str}},
            "Regime": {"select": {"name": regime}},
            "Regime Day": {"number": metadata.get("regime_day", 0)},
            "Coverage": {"number": round(coverage, 2)},
            "Integrity Score": {"number": round(integrity_score, 2)},
            "Type": {"select": {"name": "Daily_v10.1"}},
            "Status": {"select": {"name": "Published"}},
        },
        "children": first_batch,
    }

    result = _notion_post("pages", payload)
    if not result:
        return None

    page_id = result.get("id", "")
    page_url = result.get("url", "")

    # 若 blocks 超過 100，追加剩餘
    if len(blocks) > 100:
        _append_blocks(page_id, blocks[100:])

    logger.info(f"Notion: published → {page_url}")
    return page_url


def _append_blocks(page_id: str, blocks: list[dict], max_retries: int = 3):
    """追加 blocks 到已存在的 page，含 rate limit 重試。"""
    for i in range(0, len(blocks), 100):
        batch = blocks[i:i + 100]
        for attempt in range(max_retries):
            try:
                resp = requests.patch(
                    f"{NOTION_API_BASE}/blocks/{page_id}/children",
                    headers=_headers(),
                    json={"children": batch},
                    timeout=30,
                )
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 2 ** attempt + 1))
                    logger.warning(f"Notion append rate limited, retrying in {retry_after}s")
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                break  # 成功，離開 retry 迴圈
            except requests.HTTPError as e:
                logger.error(f"Notion append blocks HTTP error: {e}")
                break
            except requests.RequestException as e:
                logger.error(f"Notion append blocks failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    return  # 放棄


# ═══════════════════════════════════════════════════════════════════════════
# Weekly Report Publisher
# ═══════════════════════════════════════════════════════════════════════════

_WEEKLY_SECTION_EMOJI = {
    "敘事弧線": "📖",
    "校準": "🎯",
    "Thesis": "📋",
    "結構": "🏛️",
    "觀測": "👀",
    "自省": "🪞",
}


def _weekly_section_heading(title: str, level: int = 2) -> dict:
    emoji = ""
    for key, e in _WEEKLY_SECTION_EMOJI.items():
        if key in title:
            emoji = e + " "
            break
    return _heading(f"{emoji}{title}", level)


def _build_weekly_blocks(report: dict, weekly_context: dict) -> list[dict]:
    """週報 Notion blocks。"""
    sections = report.get("sections", {})
    metadata = report.get("metadata", {})
    blocks = []

    # Header callout
    sc = weekly_context.get("scorecard", {})
    brier = sc.get("weekly_brier")
    hit_rate = sc.get("hit_rate")
    days = weekly_context.get("days_available", 0)
    header_parts = [f"交易日：{days} 天"]
    if brier is not None:
        header_parts.append(f"Brier Score：{brier:.4f}")
    if hit_rate is not None:
        header_parts.append(f"命中率：{hit_rate:.0%}")
    cal_gap = sc.get("calibration_gap")
    if cal_gap is not None:
        label = "過度自信" if cal_gap > 0 else "偏保守"
        header_parts.append(f"校準缺口：{cal_gap:+.3f}（{label}）")
    if weekly_context.get("degraded"):
        header_parts.append("⚠️ 資料不完整")
    blocks.append(_callout("　".join(header_parts), "📊"))

    # 一、本週敘事弧線
    blocks.append(_weekly_section_heading("一、本週敘事弧線"))
    _add_paragraphs(blocks, sections.get("narrative_arc", ""))
    blocks.append(_divider())

    # 二、推論績效與校準回顧
    blocks.append(_weekly_section_heading("二、推論績效與校準回顧"))
    _add_paragraphs(blocks, sections.get("calibration_review", ""))

    # 校準表格（from aggregated data）
    by_asset = sc.get("by_asset", {})
    if by_asset:
        rows = [["資產", "命中率", "樣本數"]]
        for asset, info in sorted(by_asset.items()):
            hr = f"{info['hit_rate']:.0%}" if info.get("hit_rate") is not None else "N/A"
            rows.append([asset, hr, str(info.get("n", 0))])
        blocks.append(_notion_table(rows))

    blocks.append(_divider())

    # 三、Thesis 演化
    blocks.append(_weekly_section_heading("三、Thesis 演化圖譜"))
    thesis_text = sections.get("thesis_evolution", "")
    if thesis_text:
        # Parse ### headers as callouts
        current_lines = []
        current_emoji = "📋"
        for line in thesis_text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("### "):
                if current_lines:
                    blocks.append(_callout("\n".join(current_lines), current_emoji))
                    current_lines = []
                title = line[4:]
                lower = title.lower()
                if any(k in lower for k in ["強化", "支持", "上調"]):
                    current_emoji = "✅"
                elif any(k in lower for k in ["弱化", "失效", "下調", "挑戰"]):
                    current_emoji = "⚠️"
                else:
                    current_emoji = "➖"
                current_lines = [title]
            else:
                current_lines.append(line)
        if current_lines:
            blocks.append(_callout("\n".join(current_lines), current_emoji))
    blocks.append(_divider())

    # 四、結構性觀察
    blocks.append(_weekly_section_heading("四、結構性觀察"))
    _add_paragraphs(blocks, sections.get("structural_view", ""))
    blocks.append(_divider())

    # 五、下週觀測清單
    blocks.append(_weekly_section_heading("五、下週觀測清單"))
    watch = sections.get("watch_list", "")
    for line in watch.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("- ") or line.startswith("* "):
            blocks.append(_bullet(line[2:]))
        else:
            blocks.extend(_paragraph(line))
    blocks.append(_divider())

    # 六、系統自省
    blocks.append(_weekly_section_heading("六、系統自省"))
    _add_paragraphs(blocks, sections.get("system_reflection", ""))

    return blocks


def publish_weekly_to_notion(
    report: dict,
    weekly_context: dict,
    week_label: str,
) -> str | None:
    """發布週報到 Notion，回傳 page URL。"""
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        logger.warning("Notion credentials not set, skipping weekly publish")
        return None

    metadata = report.get("metadata", {})
    regime = metadata.get("dominant_regime", MISSING_DATA)
    dr = weekly_context.get("date_range", {})
    date_from = dr.get("from", "")
    date_to = dr.get("to", "")
    sc = weekly_context.get("scorecard", {})

    blocks = _build_weekly_blocks(report, weekly_context)
    first_batch = blocks[:100]

    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Title": {
                "title": [_plain_text(
                    f"Weekly Intelligence Brief | {week_label} ({date_from}~{date_to})"
                )]
            },
            "Date": {"date": {"start": date_from, "end": date_to}},
            "Regime": {"select": {"name": regime}},
            "Coverage": {"number": round(
                weekly_context.get("metadata_summary", {}).get("avg_coverage", 0), 2
            )},
            "Integrity Score": {"number": round(
                weekly_context.get("metadata_summary", {}).get("avg_integrity", 0), 2
            )},
            "Type": {"select": {"name": "Weekly_v10.2"}},
            "Status": {"select": {"name": "Published"}},
        },
        "children": first_batch,
    }

    result = _notion_post("pages", payload)
    if not result:
        return None

    page_id = result.get("id", "")
    page_url = result.get("url", "")

    if len(blocks) > 100:
        _append_blocks(page_id, blocks[100:])

    logger.info(f"Notion Weekly: published → {page_url}")
    return page_url


def dry_run_blocks(report: dict, coverage: float = 1.0) -> list[dict]:
    """Dry-run：只產生 blocks，不發布到 Notion。印出前 500 字元預覽。"""
    blocks = _build_blocks(report, coverage)
    preview_chars = 0
    print(f"\n{'═'*60}")
    print(f"DRY RUN: {len(blocks)} blocks 總計")
    print('═'*60)
    for i, block in enumerate(blocks):
        btype = block.get("type", "unknown")
        # 取出文字內容
        content_obj = block.get(btype, {})
        rich = content_obj.get("rich_text", [])
        text = "".join(seg.get("text", {}).get("content", "") for seg in rich)
        if not text and btype == "code":
            text = "[code block]"
        elif not text and btype == "divider":
            text = "─────"
        line = f"[{i:02d}] {btype:<22} {text[:80]}"
        print(line)
        preview_chars += len(line)
        if preview_chars >= 500:
            remaining = len(blocks) - i - 1
            if remaining > 0:
                print(f"... (還有 {remaining} 個 blocks)")
            break
    print('═'*60 + "\n")
    return blocks
