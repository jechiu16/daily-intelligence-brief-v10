# Daily Intelligence Brief v10.1 (DIB)

> 多代理人市場情報系統 — 每日自動生成跨資產因果推理報告，發布至 Notion

---

## 目錄

1. [系統哲學](#系統哲學)
2. [快速啟動](#快速啟動)
3. [架構概覽](#架構概覽)
4. [完整 Pipeline 流程](#完整-pipeline-流程)
5. [角色互動圖](#角色互動圖)
6. [資料傳導機制](#資料傳導機制)
7. [資料結構規格](#資料結構規格)
8. [記憶層 L1–L5](#記憶層-l1l5)
9. [代理人規格](#代理人規格)
10. [Notion 輸出格式](#notion-輸出格式)
11. [TGRI 計算機制](#tgri-計算機制)
12. [數據品質系統](#數據品質系統)
13. [開發紀錄](#開發紀錄)
14. [API 依賴總覽](#api-依賴總覽)
15. [目錄結構](#目錄結構)

---

## 系統哲學

DIB 的核心資產**不是文字報告，而是推論歷史**。每一天的推論、每一個被挑戰的假設、每一次預測的結果，都被儲存為可追溯的記錄。系統設計的五個目標：

1. **呈現尚未被市場完全解決的張力** — 不是已知共識，而是矛盾的共存
2. **將數據嵌入因果鏈** — 數字不是清單，是論證的證據
3. **將政治經濟約束納入市場解讀** — Acemoglu 框架：路徑依賴決定選項空間
4. **讓讀者獲得新的問題** — 每日思考題帶時間錨點和觀測信號
5. **訓練跨資產因果推理能力** — 閱讀者不只接收觀點，而是學習推論結構

### 三位思想家框架（系統靈魂，不可替換）

| 思想家 | 負責章節 | 核心方法 |
|--------|----------|----------|
| **Paul Krugman** | 主線故事、配置羅盤 | 先立再破：共識 → 忽略的機制 → 一般均衡反轉 |
| **Amartya Sen** | 地緣政治戰術層、Thesis 追蹤 | 定義先行：邊界清晰後再判斷 |
| **Daron Acemoglu** | 地緣政治結構層 | 路徑依賴：今日選擇限制明日選項 |

---

## 快速啟動

```bash
# 安裝依賴
pip install anthropic google-genai pandas numpy scipy requests fredapi yfinance

# 設置環境變數（.env 或 shell）
export ANTHROPIC_API_KEY="sk-..."
export GEMINI_API_KEY="AI..."
export NOTION_API_KEY="secret_..."
export NOTION_DATABASE_ID="..."
export FRED_API_KEY="..."
export EIA_API_KEY="..."
export FINNHUB_API_KEY="..."

# 執行每日 Pipeline
python3 -m src.orchestrator

# 強制重跑（忽略當日已存在的快照）
python3 -m src.orchestrator --force

# 更新 TGRI 手動輸入
python3 tools/update_manual.py adiz_intrusions 3
python3 tools/update_manual.py pla_activity_level 2

# 管理 Thesis
python3 tools/thesis_cli.py list
python3 tools/thesis_cli.py create --title "Fed 年內降息兩次" --deadline 2026-12-31
python3 tools/thesis_cli.py close T001 --reason "數據否定"
```

---

## 架構概覽

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          ORCHESTRATOR (主控)                             │
│                      src/orchestrator.py                                │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │ 22 步驟順序執行（有容錯）
          ┌────────────────────┼─────────────────────┐
          │                    │                     │
    ┌─────▼──────┐    ┌────────▼────────┐   ┌───────▼──────┐
    │  數據層     │    │   分析層         │   │   記憶層      │
    │            │    │                 │   │              │
    │ DataWatcher│    │ Analyst(DeepSeek) │   │ L2 市場結構  │
    │ QuantEngine│    │ DevilsAdvocate  │   │ L3 Active    │
    │ Sentiment  │    │   (Gemini)      │   │    Theses    │
    │ Watcher    │    │ RiskOfficer     │   │ L4 歷史知識  │
    │ Scheduler  │    │   (DeepSeek)        │   │ L5 預測評分  │
    │ TensionEng │    │ Narrator        │   │              │
    │ Historian  │    │   (DeepSeek)      │   │ market.parq  │
    │ Scholar    │    │ PreMortem       │   │ inference_   │
    │ TGRI       │    │   (DeepSeek)      │   │  history.jl  │
    └────────────┘    └─────────────────┘   └──────────────┘
                               │
                    ┌──────────▼──────────┐
                    │     輸出層           │
                    │                     │
                    │  NotionPublisher    │
                    │  LINEPublisher      │
                    │  MemoryManager      │
                    └─────────────────────┘
```

---

## 完整 Pipeline 流程

### Step 1 — Scheduler（日曆）
```
INPUT:  今日日期
PROCESS: Finnhub API + FRED release calendar
OUTPUT: calendar_package
  {
    "today_events": [{"event": "FOMC Minutes", "time": "19:00 UTC"}],
    "next_7_days": [...],
    "next_major_event": "NFP",
    "days_until_major": 3
  }
```

---

### Step 2 — DataWatcher（市場數據）
```
INPUT:  run_timestamp
PROCESS: 25 資產多來源抓取，含 fallback chain + 快取
OUTPUT: data_package
  {
    "gold": {
      "price": 4678.0,
      "change_pct": -2.2,
      "source": "COMEX_GC",
      "quality": "confirmed",    ← 品質標記
      "timestamp": "2026-04-02T17:36:03Z",
      "tension_note": "..."      ← TensionEngine 注入
    },
    ...25 個資產...,
    "quality_scores": {"gold": "confirmed", "caixin_pmi": "MISSING_DATA", ...}
  }
```

**各資產 Fallback Chain：**

| 資產 | Primary | Secondary | Cache |
|------|---------|-----------|-------|
| Gold | FRED LBMA | yfinance GC=F | memory/cache/gold.json |
| SPX | yfinance ^GSPC | Alpha Vantage SPY | cache |
| US10Y | FRED DGS10 | yfinance ^TNX | cache |
| TIPS 10Y | FRED DFII10 | — | cache |
| Breakeven 5y5y | FRED T5YIFR | — | cache |
| NFCI | FRED NFCI | — | cache |
| COT Gold | CFTC deacom.txt | akshare | cache |
| EIA Inventory | EIA API | — | cache |
| caixin_pmi | akshare | — | MISSING_DATA |
| BDI | akshare | — | MISSING_DATA |

---

### Step 2.5a — Calibration（補昨日結果）
```
INPUT:  yesterday's predictions (L5), today's data_package
PROCESS: fill_yesterday_outcomes()
  → 找所有 date < today 且 actual_return=null 的預測
  → 比對 data_package[asset].change_pct 填入 actual_return
  → 判斷 CORRECT / WRONG / AMBIGUOUS
OUTPUT: L5 更新（下一次 Brier Score 計算用）
```

---

### Step 2.5b — Thesis Sync（同步 L3）
```
INPUT:  memory/theses/active/*.json（磁碟主版本）
PROCESS: sync_theses_dir_to_l3()
  → 讀磁碟 thesis 檔案
  → Upsert 到 l3.json（以 id 為 key）
  → 磁碟優先：磁碟有就覆蓋，磁碟無就保留 l3.json 原有
OUTPUT: l3.json 更新
```

---

### Step 3 — SentimentWatcher（輿情）
```
INPUT:  active_theses, tgri_score
MODEL:  Gemini Flash + Google Search Grounding
PROCESS:
  → 動態搜尋範圍 = thesis keywords + 固定結構性關鍵字
  → 若 TGRI > 60，加入台海地緣特殊關鍵字
  → 分類信號 bullish/bearish/neutral，評分來源 tier
OUTPUT: sentiment_package
  {
    "signals": [
      {"title": "...", "source": "reuters.com", "source_tier": "tier_1",
       "sentiment": "bearish", "relevance": 0.91}
    ],
    "aggregate": "mildly_risk_off",
    "summary": "...",
    "scan_time": "..."
  }
```

---

### Step 4 — QuantEngine（量化）
```
INPUT:  data_package + memory/timeseries/market.parquet（歷史）
PROCESS: 純 Python 計算（無 LLM）
OUTPUT: quant_package
  {
    "correlation_matrix_30d": {
      "gold_spx": 0.87,   ← 30日 Pearson，窗口 15+ 資料點
      "spx_brent": -0.91,
      ...
    },
    "zscore_alerts": {
      "wti": 1.60,        ← (今日報酬 - 30d均值) / 30d標準差
      "gold": -0.79
    },
    "rolling_vol_30d": {
      "wti": 0.05331,     ← 年化 = std(returns) × √252
    },
    "regime_probability": {
      "政策過渡": 0.55,
      "滯脹": 0.20,
      "風險偏好增長": 0.15,
      "通縮風險": 0.10
    },
    "vix_regime": {"level": 25.4, "regime": "elevated"},
    "granger_causality": [
      {"cause": "spx", "effect": "dxy", "lag": "1", "p_value": 0.00}
    ],
    "copper_gold_ratio": 0.001193,
    "brent_wti_spread": -3.40,
    "anomalies": []
  }
```

---

### Step 4.5 — TensionEngine（張力注記）
```
INPUT:  data_package, quant_package
PROCESS: 規則引擎（非 LLM）
  → 每個資產根據 z-score + 跨資產相關性生成一句脈絡說明
  → 注入 data_package[asset]["tension_note"]
OUTPUT: data_package（已附加 tension_note）
```

---

### Step 5 — Historian（歷史類比）
```
INPUT:  today's data summary
MODEL:  sentence-transformers（本地，all-MiniLM-L6-v2）
PROCESS:
  → 將今日快照 embed 為 384 維向量
  → 與 memory/vectors/embeddings.npy cosine similarity
  → 返回 top-10 最相似歷史日期
OUTPUT: historian_package
  {
    "similar_periods": [
      {"date": "2025-11-15", "similarity": 0.87, "regime": "滯脹"}
    ],
    "base_rates": [0.72],
    "sample_size": 24
  }
NOTE: 需 sentence-transformers 套件；未安裝則跳過 embedding
```

---

### Step 6 — Scholar + TGRI（地緣政治）
```
INPUT:  data_package, sentiment_package, active_theses
MODEL:  Gemini 3.5 Flash + Google Search（配額控管）
PROCESS:
  → 計算 TGRI（8 組件，0-100 分）
  → 生成地緣風險推論 GEO_xxx
  → 三層地緣分析（戰術/操作/結構）
OUTPUT: geopolitical_package
  {
    "tgri": {
      "score": 28.1,
      "trend": "stable",
      "percentile": "50%ile",
      "components": {
        "adiz_intrusions": 2.0,
        "pla_activity_level": 1.0,
        "ndf_premium": 1.15,
        "semiconductor_concentration": 10.0,
        ...
      }
    },
    "active_risks": [{"region": "台海", "risk": "...", "probability": 0.35}],
    "geo_inferences": [{"id": "GEO_001", "claim": "...", "raw_confidence": 0.42}]
  }
```

---

### Step 7 — Assembler（組裝）
```
INPUT:  全部 packages + L1-L5 記憶層
PROCESS:
  → 驗證必填欄位
  → 計算 coverage_score（加權平均）
  → Token-aware 截斷（總預算 28,100 tokens）
  → 拼裝 assembled_context
OUTPUT: assembled_context（Analyst 的完整輸入）
  Token 分配:
    data_package:      4,000 tokens
    quant_package:     2,500 tokens
    historian_package: 6,000 tokens
    sentiment_package: 2,000 tokens
    geo_package:       2,500 tokens
    L2-L5 記憶:        11,100 tokens（合計）
```

---

### Step 8 — Citation Checker Pre（前置引用檢查）
```
INPUT:  assembled_context
PROCESS: 結構完整性檢查
OUTPUT: 通過 or 警告（不阻斷 pipeline）
```

---

### Step 9 — Analyst（分析師，DeepSeek v4 Pro）
```
INPUT:  assembled_context
MODEL:  deepseek-v4-pro（~4,000 input tokens）
SYSTEM: src/prompts/analyst_system.py
TASK:
  → 判斷 regime（4 選 1）
  → 建立推論鏈 INF_001 ~ INF_00N（每個含 evidence[]、mechanism、counterexample）
  → 配置羅盤（每個資產的方向 + logic_id 指向推論）
  → Thesis 更新建議
  → 一個給惡魔代言人的可偽證假設
OUTPUT: analysis
  {
    "regime": {
      "current": "滯脹",
      "confidence": 0.72,
      "day_count": 1,
      "supporting_data": ["wti", "tips_10y"],
      "contrary_signals": ["gold_down"]
    },
    "core_tension": "WTI 單日暴漲 11.7% 但黃金下跌——能源衝擊未觸發傳統避險模式",
    "inference_chain": [
      {
        "id": "INF_001",
        "claim": "WTI 暴漲構成輸入型通膨衝擊",
        "evidence": [{"data_key": "wti", "value": 111.85, "quality": "confirmed"}],
        "logic": "{{confirmed:111.85}} 的 z-score {{confirmed:1.60}} 超過 1.5σ 閾值",
        "raw_confidence": 0.70,
        "mechanism": "能源成本透過 CPI 能源分項傳導至 PCE",
        "counterexample": "2014 年頁岩油革命期間同樣高 WTI 但通膨未起"
      }
    ],
    "compass": [
      {"asset": "WTI 原油", "direction": "up", "raw_confidence": 0.65, "logic_id": "INF_001"}
    ],
    "thesis_updates": [{"thesis_id": "T1", "update_type": "挑戰", "confidence_change": -0.03}],
    "question_for_devil": "若 OPEC+ 減產執行率低於 80%，WTI 是否在 2 週內回落至 95？"
  }
```

---

### Step 10 — Devil's Advocate（惡魔代言人，DeepSeek）
```
INPUT:  data_package ONLY（與 DeepSeek 分析隔離）
MODEL:  deepseek-v4-pro
ISOLATION: 故意不看 DeepSeek 的推論，避免確認偏誤
TASK:  從數據中找出 DeepSeek 最可能犯錯的地方（5 個攻擊）
OUTPUT: da_result
  {
    "attacks": [
      {
        "id": "DA_001",
        "title": "黃金下跌否定滯脹避險需求",
        "logic": "若滯脹真的開始，黃金應飆升；-2.2% 說明市場不信",
        "evidence": ["gold", "tips_10y"],
        "strength": "STRONG"
      }
    ]
  }
```

---

### Step 11 — Pre-Mortem（前死亡分析，DeepSeek v4 Pro）
```
INPUT:  active_theses (L3), data_package
MODEL:  deepseek-v4-pro
TASK:  「假設 6 個月後這個 Thesis 失敗了，最可能的原因是什麼？」
OUTPUT: premortem_result
  {
    "scenarios": [
      {
        "thesis_id": "T1",
        "failure_scenario": "Fed 因通膨回升暫停降息",
        "early_warning_signals": ["breakeven_5y5y > 2.5%", "fed_funds 維持不變 3 個月"],
        "timeline": "2-8 週"
      }
    ]
  }
```

---

### Step 12 — Citation Checker Post（引用完整性驗證）
```
INPUT:  inference_chain, assembled_data
PROCESS: 逐一驗證每個 evidence.data_key
  PHANTOM_CITATION:   data_key 不存在於 assembled_data → FAIL
  MISSING_DATA_CITED: value = "MISSING_DATA" 卻被引用 → FAIL
  VALUE_MISMATCH:     數值偏差 >2% → FAIL
SOFT PREFIXES（免驗證）: sentiment_*, geo_*, l3_thesis_*, l2_*, l4_*
THRESHOLD: integrity_score ≥ 0.80 才 pass
OUTPUT: {"integrity_score": 1.00, "pass": true, "flags": []}
```

---

### Step 13 — Consistency Checker（一致性）
```
INPUT:  active_theses, analysis, today_str
CHECKS:
  CONFIDENCE_JUMP:      單日 Δconfidence > 0.2 → HIGH
  DIRECTION_REVERSAL:   資產方向從 up 翻 down → HIGH
  INVALIDATOR_IGNORED:  條件已滿足但 thesis 仍 active → CRITICAL
  DEADLINE_PASSED:      過期未關閉 → MEDIUM
OUTPUT: {"has_critical": false, "flags": [...]}
```

---

### Step 14 — Risk Officer（風險官，DeepSeek v4 Pro）
```
INPUT:  assembled_data + analysis + da_result + premortem_result
        + L2/L3/L5 記憶 + historian_package（全部預載入 user_message）
MODEL:  deepseek-v4-pro（preload 模式，最多 3 輪 tool 呼叫）
PRESERVED TOOL: flag_data_gap（唯一寫入操作）
TASK:
  → 逐一裁決 5 個 DA 攻擊（SUSTAINED/OVERRULED/NOTED）
  → 調整 INF_xxx 的信心值（方向 + 幅度）
  → 判斷最終結論是否成立
  → 提供因果語言供 Narrator 直接引用
OUTPUT: verdict
  {
    "final_conclusions_stand": true,
    "attack_verdicts": [
      {
        "attack_id": "DA_001",
        "verdict": "SUSTAINED",           ← 攻擊成立
        "narrative": "黃金下跌確實否定了系統性滯脹避險模式...",
        "confidence_impact": -0.08
      }
    ],
    "confidence_adjustments": [
      {"inf_id": "INF_001", "direction": "down", "magnitude": 0.05, "reason": "催化劑不確定"}
    ],
    "risk_officer_notes": "結構性問題：傳導鏈第二節點（通膨預期）卡住",
    "narrative_verdict": "灑水系統仍在運作——油價燃燒的是浸滿流動性的房間，不是 1973 年的茅草屋"
  }
```

---

### Step 15 — Calibration Engine（校準）
```
INPUT:  inference_chain (raw_confidence), verdict (adjustments), L5 (歷史準確率)
PROCESS:
  → 對每個 INF_xxx 套用 Risk Officer 信心調整
  → 若有 30 天以上歷史：adjusted = raw × (1 + bias) × coverage_factor
  → Bounds: [0.10, 0.90]
  → 記錄 compass 預測到 calibration.json（供明日回填）
OUTPUT: calibrated_chain（adjusted_confidence 已設定）
```

---

### Step 15.5 — Inference Store（推論儲存）
```
INPUT:  calibrated_chain + geo_inferences
PROCESS: 逐筆 append 到 inference_history.jsonl
OUTPUT: memory/timeseries/inference_history.jsonl
  每行一筆:
  {"date":"2026-04-02","run_id":"...","inf_id":"INF_001",
   "claim":"...","evidence_keys":["wti","nfci"],
   "raw_confidence":0.70,"adjusted_confidence":0.65,
   "verdict":"SUSTAINED","outcome":null}
```

---

### Step 16 — Invalidator Engine（觸發器）
```
INPUT:  data_package, active_theses
PROCESS: 評估每個 thesis 的 invalidator 條件
  operators: >, <, >=, <=, ==, !=
  例：data_package["spx"]["price"] > 5800 → triggered=true
OUTPUT: triggered_list → MemoryManager 關閉對應 thesis
```

---

### Step 17 — Narrator（敘事者，DeepSeek v4 Pro）
```
INPUT:  analysis, verdict, calibrated_chain,
        geopolitical_package, calendar_package, data_package
MODEL:  deepseek-v4-pro（max_tokens=10,000）
SYSTEM: src/prompts/narrator_system.py
        Krugman Motion: 開場悖論 → 展開因果 → 轉折質疑 → 誠實結論
INPUT FORMAT（結構化摘要，非 raw JSON）:
  → 攻擊摘要（散文式，禁止 DA_001 代碼）
  → 信心修正列表
  → 風險官因果語言（可直接引用）
  → 校準後推論鏈摘要（含 mechanism）
  → 配置羅盤 JSON
  → 地緣政治 JSON
  → 市場數據（_format_market_data 格式化）
OUTPUT: report（7 個 sections）
  {
    "sections": {
      "tension": "一句話悖論，問號結尾",
      "market_data": "分三組的市場數據 markdown",
      "main_story": "1800~2500 字散文（Krugman Motion）",
      "geopolitics_tactical": "Sen 路線：定義邊界",
      "geopolitics_operational": "Krugman 路線：二階效果",
      "geopolitics_structural": "Acemoglu 路線：路徑依賴",
      "thesis_tracking": "### 標題 \\n 狀態 | 信心 | 到期",
      "compass": "| 資產 | 方向 | 信心 | 一句理由 |",
      "question": "帶時間錨點的開放問題"
    },
    "metadata": {"regime": "滯脹", "regime_day": 1, ...}
  }
```

---

### Step 18 — Notion Publisher（Notion 發布）
```
INPUT:  report, today_str, coverage, integrity_score
PROCESS:
  → 解析 {{quality:value}} 模板為帶色 rich_text
  → 建立 11 種 block 類型
  → 原生 table block（compass 羅盤）
OUTPUT: notion_url（字串）
```

---

### Step 19 — LINE Publisher（LINE 推播）
```
INPUT:  report, data_package, today_str, notion_url
PROCESS: 200-500 字摘要 → LINE Notify API
OUTPUT: bool（成功/失敗，非致命）
```

---

### Step 20 — Memory Manager（記憶管理）
```
INPUT:  全部 pipeline 輸出
PROCESS:
  → build_daily_snapshot() → 儲存至 daily_snapshots/{date}.json
  → update_market_timeseries() → append 到 market.parquet
  → update_regime_history() → append 到 regime_history.json
  → sync_theses_dir_to_l3() → 更新 l3.json
  → 更新 L2/L4/L5
  → update_vector_index() → 更新 embeddings.npy + index.json
  → git commit（memory/ 目錄下變更）
OUTPUT: 各記憶層更新完成
```

---

## 角色互動圖

```
外部世界
  ├── FRED / yfinance / EIA / CFTC / akshare
  │        └──► DataWatcher ──► data_package
  │
  ├── Finnhub / FRED Calendar
  │        └──► Scheduler ──► calendar_package
  │
  └── Google Search（即時新聞）
           └──► SentimentWatcher ──► sentiment_package

本地計算
  ├── market.parquet ──► QuantEngine ──► quant_package
  ├── data_package ──────► TensionEngine ──► tension_notes（注入 data_package）
  ├── embeddings.npy ──► Historian ──► historian_package
  └── manual_inputs.json ──► Scholar/TGRI ──► geopolitical_package

Assembler（彙整所有輸入）
  └── assembled_context（28,100 tokens）
         │
         ├──► Analyst (DeepSeek)
         │       └── analysis（推論鏈 INF_xxx）
         │
         ├──► Devil's Advocate (Gemini)  ← 只看 data_package（隔離）
         │       └── da_result（DA_001 ~ DA_005）
         │
         ├──► Pre-Mortem (DeepSeek)  ← 只看 active_theses + data_package
         │       └── premortem_result
         │
         └──► Risk Officer (DeepSeek)  ← 看全部（裁判）
                 └── verdict
                       ├── SUSTAINED / OVERRULED / NOTED（對每個 DA 攻擊）
                       ├── confidence_adjustments（對每個 INF_xxx）
                       └── narrative_verdict（因果語言，供 Narrator 引用）

Narrator (DeepSeek)
  └── 輸入：analysis + verdict + calibrated_chain + geo + calendar + data
  └── 輸出：7 個 sections（純文字散文）
         │
         ├──► Notion Publisher ──► notion_url
         └──► LINE Publisher ──► 推播通知

Memory Manager
  └── 儲存全部輸出至 L2-L5 + daily_snapshot + timeseries
```

---

## 資料傳導機制

### 數字品質標記傳導

每一個數字從進入系統到輸出 Notion，都帶著品質標記：

```
DataWatcher 取得數據
  │
  ▼
data_package["gold"]["quality"] = "confirmed"   ← 品質來源
  │
  ▼
Analyst 寫推論
  "黃金在 {{confirmed:4678}} 收盤"              ← 嵌入模板
  │
  ▼
Narrator 沿用或改寫
  "{{confirmed:4678}} 的回落..."
  │
  ▼
NotionPublisher 解析
  parse_markdown_to_rich_text()
    → DATA_QUALITY_COLOR["confirmed"] = "blue"
    → bold = True
    → content = _format_number("4678") = "4678"
  │
  ▼
Notion 顯示：[藍色粗體] 4678
```

### 數字格式化規則（_format_number）

| 輸入值 | 規則 | 輸出 |
|--------|------|------|
| `"4678.0"` | ≥ 1000 → 整數 | `"4678"` |
| `"111.85"` | ≥ 1 → 2 位小數 | `"111.85"` |
| `"0.22"` | < 1 → 最多 4 位有效小數 | `"0.22"` |
| `"-0.4337（指數）"` | 含中文後綴 → 原樣 | `"-0.4337（指數）"` |
| `"2.07%"` | 含 % → 原樣 | `"2.07%"` |

> ⚠️ **重要**：系統**不做自動 ×100 百分比轉換**。Narrator 寫 `{{confirmed:0.22}}%` 時，0.22 就是 0.22，不乘 100。若要顯示百分比，在模板值內加 `%` 後綴（如 `{{confirmed:2.07%}}`）。

### Thesis 生命週期傳導

```
tools/thesis_cli.py create
  └──► memory/theses/active/T001_xxx.json（磁碟主版本）

每日 Pipeline Step 2.5b
  └──► sync_theses_dir_to_l3()
  └──► memory/l3.json（Assembler 讀取）

Analyst（Step 9）
  └──► thesis_updates: [{"thesis_id": "T001", "update_type": "挑戰", "confidence_change": -0.03}]

Risk Officer（Step 14）
  └──► 裁決 DA 攻擊是否使 thesis 受損

Calibration（Step 15）
  └──► 套用信心調整

InvalidatorEngine（Step 16）
  └──► 若觸發條件 → triggered=true → thesis 關閉

Memory Manager（Step 20）
  └──► 更新 l3.json confidence_history
  └──► 若關閉 → 移至 memory/theses/closed/
```

### 預測回填傳導（Feedback Loop）

```
Day N:
  Analyst compass → {"asset":"WTI","direction":"up","confidence":0.65}
  Calibration → record_prediction() → l5.json（actual_return=null）

Day N+1:
  Step 2.5a: fill_yesterday_outcomes()
    → 找 l5.json 中 date < today、actual_return=null 的預測
    → 比對 data_package["wti"]["change_pct"] = +11.7%
    → direction="up", change_pct > 0 → result="CORRECT"
    → 寫入 actual_return=0.117, result="CORRECT"

30天後:
  Calibration → Brier Score 計算 → 調整未來信心乘數
```

---

## 資料結構規格

### 品質枚舉

```python
QUALITY = Literal[
    "confirmed",        # Tier A/B 來源，今日取得
    "cached",           # 24h 內快取
    "estimated",        # Tier C 或 proxy 計算
    "stale",            # >24h 快取
    "manual",           # tools/update_manual.py 手動輸入
    "MISSING_DATA",     # 所有來源失敗
    "anomaly_flagged",  # 超出 SANITY_LIMITS 範圍
    "deviation",        # z-score 異常標記
]
```

### Regime 枚舉

```python
REGIME = Literal["政策過渡", "風險偏好增長", "滯脹", "通縮風險"]
```

### 顏色映射

```python
DATA_QUALITY_COLOR = {
    "confirmed":      "blue",    # 藍色粗體
    "cached":         "blue",
    "estimated":      "purple",  # 紫色
    "stale":          "gray",    # 灰色
    "manual":         "purple",
    "MISSING_DATA":   "gray",
    "anomaly_flagged":"yellow",  # 黃色
    "deviation":      "pink",    # 粉紅色
}
```

### Source Tier

```python
SOURCE_TIER = {
    # Tier A: 官方來源，有 SLA
    "FRED": "A", "EIA": "A", "BLS": "A", "TWSE": "A",
    # Tier B: 穩定免費來源
    "yfinance": "B", "finnhub": "B", "alpha_vantage": "B", "CFTC": "B",
    # Tier C: 不穩定 / 社群來源
    "akshare": "C", "GDELT": "C",
}
```

### 推論鏈完整結構

```json
{
  "id": "INF_001",
  "claim": "具體可驗證的判斷",
  "evidence": [
    {
      "data_key": "wti",
      "value": 111.85,
      "quality": "confirmed",
      "observation_date": "2026-04-02"
    }
  ],
  "logic": "帶 {{quality:value}} 的因果推理文字",
  "raw_confidence": 0.70,
  "dependencies": ["INF_002"],
  "mechanism": "X 透過 Y 影響 Z（一句話因果核心）",
  "counterexample": "什麼歷史情境下此機制失效過",
  "logic_id": "INF_001",
  "supporting_inferences": ["INF_001"]
}
```

### 日快照完整結構

```json
{
  "date": "2026-04-02",
  "metadata": {
    "regime": "滯脹",
    "regime_day": 1,
    "coverage_score": 0.84,
    "pipeline_version": "v10.1",
    "run_timestamp": "2026-04-02T17:54:02Z",
    "citation_integrity_score": 1.00,
    "notion_url": "https://www.notion.so/..."
  },
  "market_data": {
    "wti": {"price": 111.85, "change_pct": 11.7, "quality": "confirmed", ...},
    "gold": {"price": 4678.0, "change_pct": -2.2, "quality": "confirmed", ...},
    "quality_scores": {"wti": "confirmed", "caixin_pmi": "MISSING_DATA", ...}
  },
  "quant": {
    "correlation_matrix_30d": {"gold_spx": 0.87, ...},
    "zscore_alerts": {"wti": 1.60, ...},
    "regime_probability": {"滯脹": 0.20, "政策過渡": 0.55, ...}
  },
  "tgri": {"score": 28.1, "trend": "stable", "percentile": "50%ile"},
  "core_tension": "...",
  "inference_chain": [...],
  "thesis_states": [...],
  "opus_verdicts": {...},
  "data_gaps": ["caixin_pmi", "bdi", "breakeven_5y5y"]
}
```

---

## 記憶層 L1–L5

| 層 | 檔案 | 用途 | 保留期 | 更新方式 |
|----|------|------|--------|----------|
| L1 | 暫態（TensionEngine 輸出） | 今日跨資產脈絡注記 | 1 天 | 每日覆寫 |
| L2 | `memory/l2.json` | 7 日 regime 結構 | 7 天 | Append，保留最近 7 筆 |
| L3 | `memory/l3.json` + `theses/active/*.json` | 活躍 Thesis | 不定期 | Upsert（磁碟優先） |
| L4 | `memory/l4.json` | 裁決歷史、已關閉 Thesis | 90 天 | Append |
| L5 | `memory/l5.json` | 預測評分卡（Brier Score） | 30 天 | Rolling append |

### L2 結構

```json
{
  "description": "L2 Market Structure Memory",
  "last_updated": "2026-04-02",
  "market_structure": [
    {
      "date": "2026-04-02",
      "regime": "滯脹",
      "coverage": 0.84,
      "core_tension": "WTI +11.7% 但黃金 -2.2%——能源衝擊未觸發傳統避險模式"
    }
  ],
  "regime_history_7d": [],
  "key_moves": []
}
```

### L3 結構（Thesis 節點）

```json
{
  "id": "T001",
  "title": "Fed 年內降息兩次",
  "rationale": "...",
  "status": "active",
  "created_date": "2026-03-31",
  "deadline": "2026-12-31",
  "confidence_history": [
    {"date": "2026-03-31", "raw": 0.65, "adjusted": 0.68}
  ],
  "invalidators": [
    {
      "condition": "若 CPI YoY > 4.5%，降息論點破裂",
      "data_key": "us_cpi",
      "operator": ">",
      "threshold": 450,
      "triggered": false,
      "triggered_date": null
    }
  ]
}
```

### L4 結構（知識歷史）

```json
{
  "description": "L4 Memory",
  "last_updated": "2026-04-02",
  "entries": [
    {
      "date": "2026-04-02",
      "type": "sustained_attack",
      "source_id": "DA_001",
      "regime": "滯脹",
      "claim": "攻擊成立原因的詳細論述",
      "narrative": "生動的隱喻語言",
      "data_reference": "WTI +11.7% (111.85), Gold -2.2%"
    }
  ],
  "entry_count": 5
}
```

### L5 結構（預測評分）

```json
{
  "predictions": [
    {
      "date": "2026-04-02",
      "asset": "WTI 原油",
      "direction": "up",
      "confidence": 0.65,
      "regime": "滯脹",
      "supporting_inferences": ["INF_001"],
      "actual_return": 0.117,
      "result": "CORRECT"
    }
  ],
  "brier_score": 0.31
}
```

---

## 代理人規格

| 代理人 | 模型 | Input Tokens（約） | 主要職責 | 輸出格式 |
|--------|------|--------------------|----------|----------|
| Analyst | deepseek-v4-pro | ~4,300 | 推論鏈 + Regime + 羅盤 | JSON |
| Devil's Advocate | gemini-3.5-flash | ~3,000 | 攻擊 DeepSeek 假設 | JSON |
| Pre-Mortem | deepseek-v4-pro | ~2,000 | Thesis 失敗情境 | JSON |
| Risk Officer | deepseek-v4-pro | ~8,000 | 裁決 DA + 調整信心 | JSON |
| Narrator | deepseek-v4-pro | ~6,000 | 散文化（Krugman Motion） | JSON（含 sections） |
| Sentiment | gemini-3.5-flash | ~1,500 | 輿情分類 | JSON |
| Scholar | gemini-3.5-flash | ~4,000 | 地緣分析 + TGRI | JSON |
| Historian | 本地 sentence-transformers | — | 向量相似搜尋 | JSON |

---

## Notion 輸出格式

### 七個章節 Block 結構

```
⚠️ Callout     → 數據覆蓋率警告 + Brier Score
── Divider ──

## ⚡ 一、今日張力
  Paragraph    → 一句話悖論（問號結尾）

## 📊 二、市場數據全覽
  Paragraph ×3 → 【風險資產】【利率與匯率】【商品與避險】
                  帶品質色彩的 {{quality:value}} 解析

## 📰 三、主線故事
  Paragraph ×N → 1800~2500 字散文（Krugman Motion）
                  段落依語意自動分割（max 1800 chars/block）

## 🌍 四、地緣政治
  🎯 Callout   → Sen（戰術層：TGRI + 定義邊界）
  📊 Callout   → Krugman（操作層：二階效果）
  🏛️ Callout   → Acemoglu（結構層：路徑依賴）

## 🎯 五、Thesis 追蹤
  ✅/⚠️/➖ Callout × N → 每個 thesis 一個 callout
              （✅支持 / ⚠️挑戰 / ➖中性）

## 🧭 六、配置羅盤
  Table Block  → Notion 原生表格（has_column_header=true）
                 | 資產 | 方向 | 信心 | 一句理由 |
                 表格 cell 同樣解析 {{quality:value}}

## ❓ 七、思考題
  Paragraph    → 帶時間錨點 + 可觀測信號的開放問題
```

---

## TGRI 計算機制

**Taiwan Geopolitical Risk Index (0–100)**

| 組件 | 權重 | 來源 | 標準化方式 |
|------|------|------|-----------|
| ADIZ 侵擾次數 | 0.20 | manual_inputs.json | 0-10 分 |
| PLA 活動強度 | 0.15 | manual_inputs.json | 0-3 → 0-10 |
| 美台接觸頻率（反向） | 0.15 | manual_inputs.json | 10-頻率 |
| NDF 溢價（90d 偏離） | 0.15 | yfinance USDTWD | 百分比偏離 → 0-10 |
| 台股外資淨流（30d） | 0.15 | data_package tw_foreign_net | 正規化 0-10 |
| 貿易政策風險 | 0.15 | manual_inputs.json | 0-10 分 |
| 半導體集中度 | 0.10 | TSM 市值 / TWII | 比率 → 0-10 |
| 資本流出壓力 | 0.10 | 外部估算 | 0-10 |

**TGRI = Σ(組件分數 × 權重) × 10**

**趨勢判斷：** 與 5 日前比較（rising / stable / falling）

**百分位：** 與歷史 tgri.parquet 比較

**更新手動輸入：**
```bash
python3 tools/update_manual.py adiz_intrusions 3     # 0-10
python3 tools/update_manual.py pla_activity_level 2  # 0-3
python3 tools/update_manual.py us_tw_contact_frequency 7
python3 tools/update_manual.py trade_policy_risk 6
python3 tools/update_manual.py caixin_pmi 51.2       # 月度 PMI
```

---

## 數據品質系統

### 覆蓋率計算

```python
COVERAGE_WEIGHTS = {
    "gold": 1.0, "spx": 1.0, "vix": 1.0, "dxy": 1.0,
    "brent": 0.9, "wti": 0.9, "us10y": 0.9,
    "tips_10y": 0.8, "nfci": 0.8, "usdjpy": 0.8,
    "breakeven_5y5y": 0.7, "usdtwd": 0.7, "twse": 0.7,
    "tw_foreign_net": 0.6, "cot_gold": 0.5,
    "caixin_pmi": 0.4, "bdi": 0.3, ...
}
coverage = Σ(confirmed × weight) / Σ(weight)
```

### Sanity Limits（異常標記）

```python
SANITY_LIMITS = {
    "gold":      (1500, 8000),
    "spx":       (2000, 10000),
    "vix":       (5, 100),
    "dxy":       (70, 130),
    "brent":     (20, 200),
    "wti":       (20, 200),
    "us10y":     (0, 20),
    "tips_10y":  (-5, 10),
    "copper":    (1.5, 20),
    "nikkei":    (15000, 65000),
    "cot_gold":  (-300000, 400000),
    "nfci":      (-3, 5),
    "breakeven_5y5y": (0, 6),
}
```

超出範圍 → quality 改為 `"anomaly_flagged"`，繼續使用但標記黃色

---

## 開發紀錄

### v10.1 重大變更

| 類別 | 變更 | 修復 Bug |
|------|------|----------|
| **數據** | CFTC COT 直接下載（`deacom.txt`），取代不穩定的 akshare | COT gold 從 MISSING → confirmed |
| **數據** | 擴充 SANITY_LIMITS（copper, nikkei, cot_gold, nfci, breakeven_5y5y 等） | anomaly_flagged 正確觸發 |
| **寫作** | Narrator 系統提示全面改寫（Krugman Motion 結構） | 報告缺乏因果推理 |
| **寫作** | Analyst 新增 `mechanism` + `counterexample` 欄位 | 推論鏈缺乏可驗證機制 |
| **寫作** | Narrator `_build_user_message()` 改為結構化摘要（非 raw JSON） | LLM 看不清哪些是重點 |
| **Notion** | Compass 改用原生 Table Block | Markdown 表格顯示失敗 |
| **Notion** | 移除「首席風險官裁決紀要」獨立章節，融入主線故事 | 報告結構割裂 |
| **Notion** | `_notion_table()` cell 改用 `parse_markdown_to_rich_text()` | `{{confirmed:...}}` 模板洩漏到表格 |
| **Notion** | `_format_number()` 移除自動 ×100 百分比轉換 | SPX -0.2% 被錯誤顯示為 +20% |
| **推論** | `supporting_inferences` 從 analyst `logic_id` 正確映射 | 所有預測都缺少 inference 連結 |
| **引用** | `CitationChecker` 加入 soft key 前綴豁免 | sentiment_/geo_ 鍵導致 integrity=0 |
| **工具** | 新增 `tools/update_manual.py`（CLI 更新 TGRI 手動輸入） | 手動輸入無工具只能直接編輯 JSON |
| **架構** | Risk Officer 改為 preload 模式（不再多輪 tool 呼叫） | 多輪呼叫增加延遲和 token 消耗 |

### 已知限制

| 問題 | 狀態 | 解決方案 |
|------|------|----------|
| `caixin_pmi` MISSING | 低優先 | `tools/update_manual.py caixin_pmi 51.2` 手動輸入，或 FRED UPMICN |
| `bdi` MISSING | 低優先 | akshare 不穩定；對核心判斷權重低 |
| `tw_leading` 用 proxy | 進行中 | NDC API 403 禁止；目前用 TWII proxy |
| Historian embedding 空 | 需累積 | 需 30+ 快照才有效；sentence-transformers 需手動安裝 |
| Feedback loop 需隔日 | 設計如此 | Day N 的預測在 Day N+1 才能回填結果 |

---

## API 依賴總覽

| 服務 | 用途 | Token/費用 | 必要性 |
|------|------|-----------|--------|
| DeepSeek v4 Pro | Analyst, Narrator, Pre-Mortem | 每次約 $0.05-0.15 | **必要** |
| DeepSeek v4 Pro | Risk Officer | 每次約 $0.30-0.80 | **必要** |
| Google Gemini 3.5 Flash | Devil's Advocate, Scholar | 免費層 / 付費 | **必要** |
| Google Gemini 3.5 Flash | Sentiment Watcher, Historian | 免費層 | **必要** |
| Notion | 報告發布 | 免費 | **必要** |
| FRED | 利率、CPI、NFCI | 免費（需 API key） | **必要** |
| yfinance | 股指、商品、匯率 | 免費 | **必要** |
| CFTC | COT 倉位數據 | 完全免費 | 重要 |
| EIA | 原油庫存 | 免費（需 API key） | 重要 |
| BLS | CPI 發布 | 完全免費 | 補充 |
| Finnhub | 經濟日曆 | 免費層（有頻率限制） | 補充 |
| Alpha Vantage | 備援股價/匯率 | 免費層 | 備援 |
| akshare | 台灣/中國/韓國數據 | 免費（不穩定） | 補充（常失敗） |
| LINE Notify | 推播通知 | 免費 | 可選 |

---

## 目錄結構

```
daily-intelligence-brief-v10/
│
├── src/                          # 核心源碼
│   ├── orchestrator.py           # 主控：22 步驟 pipeline
│   ├── config.py                 # 全局配置（模型、路徑、常數）
│   ├── data_watcher.py           # 市場數據抓取（25 資產，含 fallback）
│   ├── quant_engine.py           # 量化計算（相關性、z-score、波動率）
│   ├── tension_engine.py         # 跨資產脈絡注記（規則引擎）
│   ├── sentiment_watcher.py      # 輿情分析（Gemini Flash + Google Search）
│   ├── historian.py              # 歷史類比（sentence-transformers + 向量搜尋）
│   ├── scholar.py                # 地緣政治分析（Gemini Flash + Google Search）
│   ├── tgri.py                   # 台灣地緣風險指數計算
│   ├── scheduler.py              # 經濟日曆
│   ├── assembler.py              # 彙整所有 package（token-aware）
│   ├── memory_manager.py         # L2-L5 寫入 + git commit
│   ├── citation_checker.py       # 引用完整性驗證
│   ├── consistency_checker.py    # Thesis 一致性檢查
│   ├── calibration.py            # 信心校準 + 預測記錄 + Brier Score
│   ├── inference_store.py        # 推論歷史 JSONL 儲存
│   ├── invalidator_engine.py     # Thesis 觸發條件評估
│   ├── notion_publisher.py       # Notion 發布（11 種 block）
│   ├── line_publisher.py         # LINE Notify 推播
│   ├── assembler.py              # 組裝 assembled_context
│   │
│   ├── agents/                   # LLM 代理人
│   │   ├── analyst.py            # DeepSeek：推論鏈 + Regime
│   │   ├── devils_advocate.py    # DeepSeek：攻擊假設
│   │   ├── premortem.py          # DeepSeek：失敗情境
│   │   ├── risk_officer.py       # DeepSeek：裁決 + 仲裁
│   │   └── narrator.py           # DeepSeek：散文化報告
│   │
│   └── prompts/                  # 系統提示
│       ├── analyst_system.py     # Analyst 規則（含 evidence 格式）
│       └── narrator_system.py    # Narrator 規則（Krugman Motion）
│
├── memory/                       # 持久化記憶
│   ├── l2.json                   # 7 日市場結構
│   ├── l3.json                   # 活躍 Thesis（同步自 theses/active/）
│   ├── l4.json                   # 裁決歷史
│   ├── l5.json                   # 預測評分卡
│   ├── manual_inputs.json        # TGRI 手動輸入
│   ├── weekly_comments.json      # 每週評論
│   │
│   ├── cache/                    # 數據快取（每個資產一個 JSON）
│   │   ├── gold.json
│   │   ├── spx.json
│   │   └── ...（24 個）
│   │
│   ├── daily_snapshots/          # 每日不可變快照
│   │   └── 2026-04-02.json
│   │
│   ├── timeseries/               # 時間序列資料
│   │   ├── market.parquet        # 歷史市場數據（QuantEngine 用）
│   │   ├── tgri.parquet          # TGRI 歷史（百分位計算用）
│   │   ├── inference_history.jsonl # 所有推論紀錄（系統核心資產）
│   │   ├── regime_history.json   # Regime 變遷記錄
│   │   └── scorecard_history.json
│   │
│   ├── theses/                   # Thesis 管理
│   │   ├── active/               # 活躍中（工具 CLI 管理）
│   │   │   └── T001_xxx.json
│   │   ├── closed/               # 已關閉
│   │   └── outcomes/
│   │       └── outcome_log.json
│   │
│   ├── vectors/                  # 向量索引（Historian 用）
│   │   ├── embeddings.npy        # 384 維快照嵌入
│   │   └── index.json            # 嵌入對應的日期 + 元數據
│   │
│   ├── system/                   # 系統狀態
│   │   ├── calibration.json      # 預測校準參數
│   │   ├── data_gaps.json        # 數據缺口追蹤
│   │   └── data_health.json      # 各數據源健康狀態
│   │
│   └── archive/
│       └── v9/                   # v9 歷史快照
│
├── tools/                        # CLI 工具
│   ├── thesis_cli.py             # Thesis 管理（create/list/close/update）
│   ├── update_manual.py          # TGRI 手動輸入更新
│   └── historian_warmstart.py    # 向量索引初始化
│
├── tests/                        # 測試（若有）
│
├── .env                          # 環境變數（不入 git）
└── README.md                     # 本文件
```

---

*DIB v10.1 — 推論即資產，每日一次，持續校準。*
