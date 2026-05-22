"""Shared language policy for all user-facing generated text."""

TRADITIONAL_CHINESE_ONLY = """## 語言政策（最高優先）

- 所有面向使用者、Notion、LINE、memory snapshot 的自然語言輸出，必須使用繁體中文（台灣用語）。
- 嚴格禁止簡體中文。不得輸出「市场、风险、数据、输出、根据、发现、证据、逻辑、趋势、预测、上涨、下跌、美元指数」等簡體字形。
- 若來源資料含簡體中文，必須先改寫為繁體中文後再輸出。
- 英文縮寫、股票代號、API 欄位名稱、JSON key 可以保留英文。
"""

