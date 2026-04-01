"""Notion Publisher — 把報告發布到 Notion Database。"""

import json
import logging
import re
from datetime import datetime

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


def _notion_post(endpoint: str, payload: dict) -> dict | None:
    try:
        resp = requests.post(
            f"{NOTION_API_BASE}/{endpoint}",
            headers=_headers(),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        logger.error(f"Notion API error {resp.status_code}: {resp.text[:300]}")
        return None
    except Exception as e:
        logger.error(f"Notion request failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Rich text with color
# ═══════════════════════════════════════════════════════════════════════════

def _plain_text(text: str) -> dict:
    return {"type": "text", "text": {"content": text}}


def parse_markdown_to_rich_text(text: str) -> list[dict]:
    """把含 markdown 和品質標記的文字解析成 Notion rich_text segments。

    支援（按優先順序）：
    - ``{{quality:value}}`` → 帶顏色（confirmed=綠/bold，cached=黃，estimated=藍，stale=灰）
    - ``**text**`` → bold
    - ``*text*`` → italic
    其餘文字保持普通格式。
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
                "text": {"content": value},
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


def _paragraph(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rich_text(text)},
    }


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
    """把長文字切割成不超過 max_len 字元的段落，優先在句號/逗號斷句。"""
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
        chunks.append(text[:cut])
        text = text[cut:].strip()
    return chunks


def _add_paragraphs(blocks: list, text: str) -> None:
    """把文字按段落拆分後加入 blocks，長段落自動截斷。"""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        for chunk in _split_long_text(para):
            blocks.append(_paragraph(chunk))


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
    - ``- label: value`` → paragraph（strip emoji，strip quality markers）
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
            clean = _strip_quality_markers(line[2:]).replace("**", "").strip()
            clean = strip_emoji(clean)
            if clean:
                blocks.append(_paragraph(clean))
        else:
            clean = _strip_quality_markers(line).replace("**", "").strip()
            clean = strip_emoji(clean)
            if clean:
                blocks.append(_paragraph(clean))
    return blocks


def _compass_section(compass_text: str) -> list[dict]:
    """把配置羅盤文字轉成 Notion blocks，內文移除 emoji。"""
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
            blocks.append(_paragraph(strip_emoji(line)))
    return blocks


def _build_blocks(report: dict, coverage: float) -> list[dict]:
    sections = report.get("sections", {})
    blocks = []

    # 覆蓋率警告
    if coverage < 0.85:
        blocks.append(_callout(f"數據覆蓋率：{coverage:.0%}，部分判斷可能受影響", "⚠️"))

    # 一、今日張力
    blocks.append(_section_heading("一、今日張力", 2))
    tension = sections.get("tension", MISSING_DATA)
    for chunk in _split_long_text(tension):
        blocks.append(_paragraph(chunk))
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

    # 四、地緣政治
    blocks.append(_section_heading("四、地緣政治", 2))
    geo = sections.get("geopolitics", MISSING_DATA)
    _add_paragraphs(blocks, geo)
    blocks.append(_divider())

    # 五、Thesis 追蹤（line-by-line 解析，### → callout）
    blocks.append(_section_heading("五、Thesis 追蹤", 2))
    thesis = sections.get("thesis_tracking", MISSING_DATA)
    for line in thesis.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("### "):
            title = line[4:]
            lower = title.lower()
            if any(k in lower for k in ["成立", "確認", "上調", "看漲", "維持"]):
                emoji = "✅"
            elif any(k in lower for k in ["失效", "駁回", "下調", "看跌"]):
                emoji = "❌"
            elif any(k in lower for k in ["風險", "警告", "注意", "觀察"]):
                emoji = "⚠️"
            else:
                emoji = "🎯"
            blocks.append(_callout(title, emoji))
        elif line.startswith("- "):
            blocks.append(_bullet(line[2:]))
        else:
            for chunk in _split_long_text(line):
                blocks.append(_paragraph(chunk))
    blocks.append(_divider())

    # 六、配置羅盤
    blocks.append(_section_heading("六、配置羅盤", 2))
    compass = sections.get("compass", "")
    blocks.extend(_compass_section(compass))
    blocks.append(_divider())

    # 七、思考題
    blocks.append(_section_heading("七、思考題", 2))
    question = sections.get("question", MISSING_DATA)
    for chunk in _split_long_text(question):
        blocks.append(_paragraph(chunk))

    return blocks


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
        today_str = datetime.now().strftime("%Y-%m-%d")

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
            "Type": {"select": {"name": "Daily_v10"}},
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


def _append_blocks(page_id: str, blocks: list[dict]):
    """追加 blocks 到已存在的 page。"""
    for i in range(0, len(blocks), 100):
        batch = blocks[i:i + 100]
        try:
            resp = requests.patch(
                f"{NOTION_API_BASE}/blocks/{page_id}/children",
                headers=_headers(),
                json={"children": batch},
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Notion append blocks failed: {e}")
            break


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
