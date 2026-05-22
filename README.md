# Daily Intelligence Brief v10.1

Daily Intelligence Brief（DIB）是一個自動化的跨資產情報引擎。它每天蒐集市場、總經、地緣政治、新聞輿情與既有 thesis，經過多代理人交叉辯論後，產出一份可追溯、可反駁、可累積記憶的投資情報簡報，並發布到 Notion。

它不是「把行情整理成摘要」的工具，而是把市場資料轉成一條可檢查的因果推論鏈。

## 目前能力

| 面向 | 說明 |
| --- | --- |
| 每日自動化 | GitHub Actions 每天 07:00（Asia/Taipei）自動執行，也可手動觸發 |
| 多代理人推論 | Analyst、Devil's Advocate、Pre-mortem、Risk Officer、Narrator 分工協作 |
| 即時搜尋 | 搜尋類任務由 Gemini 3.5 Flash Lite 負責，DeepSeek 不負責搜尋 |
| 高推理分析 | 核心分析與風險裁決使用 DeepSeek v4 Pro，thinking enabled，reasoning effort max |
| 長期記憶 | 保存每日快照、推論紀錄、歷史類比、active thesis 與校準結果 |
| Notion 發布 | 自動建立結構化 Notion 頁面，包含摘要、主線故事、配置羅盤、風險提醒 |
| 繁體中文輸出 | 所有 LLM prompt 加入最高優先層級的繁體中文政策，禁止簡體中文輸出 |
| 失敗保護 | JSON 修復、非空 fallback、資料品質標記、執行逾時上限與 log 降噪 |

## 系統哲學

DIB 的核心資產不是文字報告，而是推論歷史。每一天的觀點、被挑戰的假設、事後驗證結果和 thesis 狀態，都會被保存下來，讓系統逐步學會自己在哪些市場環境容易判斷錯。

系統以三個思想框架作為敘事骨架：

| 思想家 | 在系統中的角色 | 方法 |
| --- | --- | --- |
| Paul Krugman | 主線故事、配置羅盤 | 先立再破：共識、忽略的機制、一般均衡反轉 |
| Amartya Sen | Thesis 邊界與地緣政治戰術層 | 先定義問題，再判斷選項 |
| Daron Acemoglu | 地緣政治與制度約束 | 路徑依賴：今日選擇限制明日選項 |

## 架構概覽

```mermaid
flowchart TD
    A["GitHub Actions / Manual Run"] --> B["Scheduler"]
    B --> C["DataWatcher"]
    C --> D["QuantEngine"]
    C --> E["SentimentWatcher<br/>Gemini Search"]
    C --> F["Scholar / TGRI<br/>Gemini Search"]
    D --> G["Historian<br/>DeepSeek Flash + Local Vectors"]
    E --> H["Assembler"]
    F --> H
    G --> H
    H --> I["Analyst<br/>DeepSeek v4 Pro"]
    H --> J["Devil's Advocate<br/>DeepSeek v4 Flash"]
    H --> K["Pre-mortem<br/>DeepSeek v4 Flash"]
    I --> L["Risk Officer<br/>DeepSeek v4 Pro"]
    J --> L
    K --> L
    L --> M["Narrator<br/>Gemini 3.5 Flash"]
    M --> N["Notion Publisher"]
    N --> O["Daily Snapshot + Memory Layers"]
```

### Model Routing

| 任務 | 模型 | 搜尋 | 用途 |
| --- | --- | --- | --- |
| Analyst | `deepseek-v4-pro` | 否 | 核心市場 regime、推論鏈、配置羅盤 |
| Risk Officer | `deepseek-v4-pro` | 否 | 仲裁 Analyst、DA、Pre-mortem 的衝突 |
| Historian | `deepseek-v4-flash` | 否 | 歷史類比敘事與相似場景解讀 |
| Devil's Advocate | `deepseek-v4-flash` | 否 | 故意攻擊主論點，找確認偏誤 |
| Pre-mortem | `deepseek-v4-flash` | 否 | 替 active thesis 生成失敗情境 |
| Narrator | `gemini-3.5-flash` | 否 | 把裁決後的分析寫成 Notion 報告 |
| Sentiment Watcher | `gemini-3.5-flash-lite` | 是 | 每日新聞、輿情、事件脈絡 |
| Scholar / TGRI | `gemini-3.5-flash-lite` | 是 | 台海與周邊地緣政治風險 |
| Thesis Reviewer | `gemini-3.5-flash-lite` | 是 | 新 thesis 審核與 active thesis 更新 |
| LINE Webhook | `gemini-3.5-flash-lite` | 是 | 即時問答與行動端查詢 |

DeepSeek 的請求會帶入：

```json
{
  "thinking": {
    "type": "enabled"
  },
  "reasoning_effort": "max"
}
```

## 每日 Pipeline

1. `Scheduler`：抓取總經事件與財經日曆，標記今天是否有重要資料公布。
2. `DataWatcher`：抓取跨資產資料，包括股、債、匯、商品、台灣數據、能源與景氣指標。
3. `SentimentWatcher`：使用 Gemini Search 搜尋最新新聞與輿情事件。
4. `QuantEngine`：計算 regime、分數卡、張力指標與技術狀態。
5. `Historian`：從向量記憶中找相似歷史日，補上歷史類比。
6. `TGRI Auto-Scorer`：更新台灣地緣政治風險指標。
7. `Assembler`：整理所有資料包、缺口、品質標記與 token allocation。
8. `Analyst`：產生第一版因果推論鏈與配置羅盤。
9. `Devil's Advocate`：只看資料包，不看 Analyst 推論，獨立攻擊假設。
10. `Pre-mortem`：推演 active thesis 最可能失敗的方式。
11. `Risk Officer`：裁決衝突、調整信心、保留或推翻結論。
12. `Narrator`：把最終結論寫成可讀的繁體中文報告。
13. `Notion Publisher`：發布到 Notion，並保存每日快照與記憶更新。

## 資料與記憶

| 位置 | 內容 |
| --- | --- |
| `memory/daily_snapshots/` | 每日完整輸出與 Notion URL |
| `memory/weekly_snapshots/` | 週度回顧輸出 |
| `memory/timeseries/` | regime、TGRI、scorecard、inference history |
| `memory/theses/active/` | 目前仍在追蹤的市場 thesis |
| `memory/theses/proposed/` | 待審核的新 thesis |
| `memory/theses/closed/` | 已結案或失效的 thesis |
| `memory/vectors/` | 歷史快照向量索引 |
| `memory/system/` | 資料健康狀態與缺口紀錄 |
| `memory/cache/` | 外部資料快取 |

資料品質會以來源分級與日期落差標記：

| 標記 | 意義 |
| --- | --- |
| `confirmed` | 官方或穩定來源，資料為最新 |
| `cached` | 來源可信，但沿用近期快取 |
| `stale` | 資料過舊，只能輔助判斷 |
| `estimated` | 社群、不穩定來源或 proxy 資料 |
| `MISSING_DATA` | 來源失敗且無合理替代 |

## 快速開始

### 1. 安裝

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 2. 設定環境變數

本機可建立 `.env`：

```bash
DEEPSEEK_API_KEY=...
GEMINI_API_KEY=...
NOTION_API_KEY=...
NOTION_DATABASE_ID=...
FRED_API_KEY=...
FINNHUB_API_KEY=...
EIA_API_KEY=...
```

GitHub Actions 則放在 repository secrets。必要 secrets：

| Secret | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek 分析、風險裁決與 fast agents |
| `GEMINI_API_KEY` | Gemini Narrator 與搜尋任務 |
| `NOTION_API_KEY` | Notion 發布 |
| `NOTION_DATABASE_ID` | Notion 目標資料庫 |
| `FRED_API_KEY` | 美國總經資料 |
| `FINNHUB_API_KEY` | 財經日曆與部分市場資料 |
| `EIA_API_KEY` | 原油與能源資料 |

選用 secrets：

| Secret | 用途 |
| --- | --- |
| `ALPHA_VANTAGE_API_KEY` | 補充市場資料來源 |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE 推播 |
| `LINE_CHANNEL_SECRET` | LINE webhook 驗簽 |
| `LINE_TARGET_ID` | LINE 推播目標 |
| `LINE_ALLOWED_USERS` | LINE 白名單 |
| `HF_TOKEN` | Hugging Face 模型下載額度與提示降噪 |

查看 GitHub repo 目前有哪些 secrets：

```bash
gh secret list --repo jechiu16/daily-intelligence-brief-v10
```

新增或更新 secret：

```bash
gh secret set DEEPSEEK_API_KEY --repo jechiu16/daily-intelligence-brief-v10
gh secret set GEMINI_API_KEY --repo jechiu16/daily-intelligence-brief-v10
```

### 3. 執行

```bash
python -m src.orchestrator
```

忽略今日已存在快照並強制重跑：

```bash
python -m src.orchestrator --force
```

週度回顧：

```bash
python -m src.weekly_orchestrator
```

## GitHub Actions

Workflow：`.github/workflows/daily-brief.yml`

| 設定 | 值 |
| --- | --- |
| 排程 | 每天 07:00 Asia/Taipei |
| UTC cron | `0 23 * * *` |
| 手動執行 | GitHub Actions 頁面中的 `workflow_dispatch` |
| Job timeout | 60 分鐘 |
| Pipeline timeout | `timeout 55m python -m src.orchestrator --force` |
| 自動提交 | `memory/` 有變更時由 `github-actions[bot]` commit 回 repo |

目前 workflow 預設的模型與搜尋控制：

```yaml
DEEPSEEK_MODEL: deepseek-v4-pro
DEEPSEEK_FAST_MODEL: deepseek-v4-flash
DEEPSEEK_THINKING: enabled
DEEPSEEK_REASONING_EFFORT: max
GEMINI_NARRATOR_MODEL: gemini-3.5-flash
GEMINI_SEARCH_MODEL: gemini-3.5-flash-lite
GEMINI_ENABLE_DAILY_SEARCH: "true"
GEMINI_ENABLE_PERIPHERY_SEARCH: "true"
GEMINI_THESIS_REVIEW_LIMIT: "1"
GEMINI_ACTIVE_THESIS_UPDATE_LIMIT: "1"
```

常用檢查指令：

```bash
gh run list --workflow daily-brief.yml --limit 5
gh run view --log
```

在 Windows PowerShell 篩選 warning / error：

```powershell
gh run view --log |
  Select-String -Pattern "ERROR|WARNING|CRITICAL|429|timeout|MISSING_DATA|JSON|model|not found" -CaseSensitive:$false
```

## Thesis 管理

列出 thesis：

```bash
python tools/thesis_cli.py list
```

建立 thesis：

```bash
python tools/thesis_cli.py create --title "Fed 年內降息兩次" --deadline 2026-12-31
```

關閉 thesis：

```bash
python tools/thesis_cli.py close T001 --reason "數據否定"
```

手動更新 TGRI 輸入：

```bash
python tools/update_manual.py adiz_intrusions 3
python tools/update_manual.py pla_activity_level 2
```

## Reliability Notes

近期的穩定性設計：

| 問題 | 現在的處理 |
| --- | --- |
| LLM JSON 格式不完整 | 會嘗試移除 trailing comma、控制字元並重新解析 |
| Narrator 失敗造成 Notion 空頁 | 會產生 deterministic fallback report，確保輸出不為空 |
| Analyst 解析失敗 | fallback 會根據資料包生成 regime、張力與配置提示 |
| Devil's Advocate / Pre-mortem JSON 失敗 | fallback 仍會產生攻擊點與失敗情境 |
| Finnhub calendar impact 型別不穩 | 統一轉成分數再比較 |
| NDC 台灣領先指標 403 | 降級成 TWII proxy，並以 info 等級記錄 |
| QuantEngine NaN warning 過多 | 聚合成單行摘要，避免 log 被洗版 |
| Node 20 deprecation warning | workflow 設定 `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true` |
| 簡體中文混入 | prompt 層加入最高優先的繁體中文政策 |

## Troubleshooting

| 現象 | 檢查方向 |
| --- | --- |
| GitHub Action 很久 | DeepSeek v4 Pro max effort 本來會慢，先看是否卡在 Analyst 或 Risk Officer |
| Gemini 429 | 降低 `GEMINI_THESIS_REVIEW_LIMIT`，或暫時關閉部分 search |
| Gemini model not found | 確認 `GEMINI_NARRATOR_MODEL` 和 `GEMINI_SEARCH_MODEL` 是否為帳號可用模型 |
| Notion 沒有輸出 | 確認 `NOTION_API_KEY`、`NOTION_DATABASE_ID`、資料庫權限 |
| LINE 沒推播 | 確認 `LINE_CHANNEL_ACCESS_TOKEN` 與 `LINE_TARGET_ID` |
| 資料大量 `MISSING_DATA` | 檢查 FRED、EIA、Finnhub、yfinance 網路與 API 額度 |
| repo push 被拒絕 | GitHub Action 可能剛提交 `memory/`，先 `git pull --rebase origin main` |

## 專案結構

```text
.
├── .github/workflows/daily-brief.yml
├── src/
│   ├── orchestrator.py
│   ├── config.py
│   ├── deepseek_client.py
│   ├── data_watcher.py
│   ├── quant_engine.py
│   ├── sentiment_watcher.py
│   ├── scholar.py
│   ├── historian.py
│   ├── assembler.py
│   ├── notion_publisher.py
│   ├── line_webhook.py
│   ├── prompts/
│   │   ├── language_policy.py
│   │   └── *_system.py
│   └── agents/
│       ├── analyst.py
│       ├── devils_advocate.py
│       ├── premortem.py
│       ├── risk_officer.py
│       ├── narrator.py
│       └── thesis_reviewer.py
├── memory/
│   ├── daily_snapshots/
│   ├── weekly_snapshots/
│   ├── theses/
│   ├── timeseries/
│   ├── vectors/
│   └── system/
├── tools/
│   ├── thesis_cli.py
│   └── update_manual.py
├── tests/
└── requirements.txt
```

## 開發檢查

```bash
python -m compileall src
pytest -q
```

README Markdown 基本檢查：

```bash
git diff --check README.md
```

## 狀態摘要

DIB v10.1 目前的定位是：DeepSeek 負責深推理，Gemini 負責搜尋與敘事，GitHub Actions 負責每日自動化，Notion 負責輸出，`memory/` 負責讓系統記住自己的判斷歷史。整個系統的目標，是把每日市場雜訊壓縮成一條可以被追蹤、被反駁、被更新的推論鏈。
