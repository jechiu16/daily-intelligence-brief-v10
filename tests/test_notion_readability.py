from __future__ import annotations


def _table_rows(block: dict) -> list[list[str]]:
    children = block["table"]["children"]
    rows = []
    for row in children:
        cells = row["table_row"]["cells"]
        rows.append([
            "".join(seg["text"]["content"] for seg in cell)
            for cell in cells
        ])
    return rows


def test_market_data_markdown_table_renders_as_notion_table():
    from src.notion_publisher import _market_data_section

    blocks = _market_data_section(
        "| 分類 | 指標 | 讀數 | 變化 | 脈絡 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 利率 | 10Y TIPS 實質利率 | {{confirmed:2.16%}} | 上升 | 估值壓制仍在 |"
    )

    assert len(blocks) == 1
    assert blocks[0]["type"] == "table"
    assert blocks[0]["table"]["table_width"] == 5
    rows = _table_rows(blocks[0])
    assert rows[0] == ["分類", "指標", "讀數", "變化", "脈絡"]
    assert rows[1] == ["利率", "10Y TIPS 實質利率", "2.16%", "上升", "估值壓制仍在"]


def test_market_data_legacy_bullets_render_as_scan_table():
    from src.notion_publisher import _market_data_section

    blocks = _market_data_section(
        "### 風險資產\n"
        "- **標普500（SPX）｜{{confirmed:7519.12}} ↑ ({{confirmed:+0.61%}})** — 油價回落中股市走強"
    )

    rows = _table_rows(blocks[0])
    assert rows[0] == ["分類", "指標", "讀數", "變化", "脈絡"]
    assert rows[1][0] == "風險資產"
    assert rows[1][1] == "標普500（SPX）"
    assert rows[1][3] == "↑ +0.61%"
    assert rows[1][4] == "油價回落中股市走強"


def test_causal_graph_markdown_table_renders_as_notion_table():
    from src.notion_publisher import _causal_graph_section

    blocks = _causal_graph_section(
        "| 主傳導 | 制約或反證 | 裁決含義 |\n"
        "| --- | --- | --- |\n"
        "| 油價下跌 → 成本緩解 → 風險偏好升 | TIPS 10年實質利率仍是快取資料 | risk-on 只能持有，不追價 |"
    )

    assert blocks[0]["type"] == "table"
    assert blocks[0]["table"]["table_width"] == 3
    rows = _table_rows(blocks[0])
    assert rows[1] == [
        "油價下跌 → 成本緩解 → 風險偏好升",
        "TIPS 10年實質利率仍是快取資料",
        "risk-on 只能持有，不追價",
    ]


def test_causal_graph_legacy_chain_splits_into_readable_columns():
    from src.notion_publisher import _causal_graph_section

    blocks = _causal_graph_section(
        "【油價下跌 → 成本緩解 → SPX上漲】但【TIPS 10年實質利率（快取資料） → 估值受限】形成拉鋸 → 方向只做持有"
    )

    rows = _table_rows(blocks[0])
    assert rows[0] == ["主傳導", "制約或反證", "裁決含義"]
    assert rows[1][0] == "油價下跌 → 成本緩解 → SPX上漲"
    assert "TIPS 10年實質利率（快取資料）" in rows[1][1]
    assert rows[1][2] == "方向只做持有"
