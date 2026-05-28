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


def test_publish_to_notion_archives_same_day_pages_after_new_page(monkeypatch):
    import src.notion_publisher as notion

    monkeypatch.setattr(notion, "NOTION_API_KEY", "test-key")
    monkeypatch.setattr(notion, "NOTION_DATABASE_ID", "db123")
    post_calls = []
    patch_calls = []

    def fake_post(endpoint, payload, max_retries=3):
        post_calls.append((endpoint, payload))
        if endpoint == "pages":
            return {"id": "new-page", "url": "https://notion.so/new-page"}
        if endpoint == "databases/db123/query":
            return {
                "results": [
                    {"id": "old-page", "archived": False},
                    {"id": "new-page", "archived": False},
                ],
                "has_more": False,
            }
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    def fake_patch(endpoint, payload, max_retries=3):
        patch_calls.append((endpoint, payload))
        return {"id": endpoint.rsplit("/", 1)[-1], "archived": True}

    monkeypatch.setattr(notion, "_notion_post", fake_post)
    monkeypatch.setattr(notion, "_notion_patch", fake_patch)

    url = notion.publish_to_notion(
        {"metadata": {"regime": "test"}, "sections": {"tension": "今日真正改變是重跑測試。"}},
        today_str="2026-05-28",
    )

    assert url == "https://notion.so/new-page"
    assert patch_calls == [("pages/old-page", {"archived": True})]
    query_payload = [payload for endpoint, payload in post_calls if endpoint.endswith("/query")][0]
    assert query_payload["filter"]["and"] == [
        {"property": "Date", "date": {"equals": "2026-05-28"}},
        {"property": "Type", "select": {"equals": "Daily_v10.1"}},
    ]


def test_publish_to_notion_can_keep_same_day_pages_when_requested(monkeypatch):
    import src.notion_publisher as notion

    monkeypatch.setattr(notion, "NOTION_API_KEY", "test-key")
    monkeypatch.setattr(notion, "NOTION_DATABASE_ID", "db123")
    patch_calls = []

    def fake_post(endpoint, payload, max_retries=3):
        assert endpoint == "pages"
        return {"id": "new-page", "url": "https://notion.so/new-page"}

    monkeypatch.setattr(notion, "_notion_post", fake_post)
    monkeypatch.setattr(notion, "_notion_patch", lambda *args, **kwargs: patch_calls.append(args))

    notion.publish_to_notion(
        {"metadata": {"regime": "test"}, "sections": {"tension": "測試。"}},
        today_str="2026-05-28",
        replace_same_day=False,
    )

    assert patch_calls == []
