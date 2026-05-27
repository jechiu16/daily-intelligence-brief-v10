from __future__ import annotations
"""LLM Telemetry — 記錄每次 LLM 呼叫的 token 用量與耗時。

每次呼叫後透過 record_llm_call() 寫入 memory/telemetry/llm_usage.jsonl。
MemoryManager 在 pipeline 結束時呼叫 aggregate_run_telemetry() 彙整每次執行的總計。
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from src.config import MEMORY_DIR, RUN_CONTEXT

logger = logging.getLogger(__name__)

TELEMETRY_DIR = MEMORY_DIR / "telemetry"
LLM_USAGE_PATH = TELEMETRY_DIR / "llm_usage.jsonl"

# LLM cost estimates（$/1M tokens）.
_PRICE_PER_M: dict[str, dict[str, float]] = {
    "deepseek-v4-pro": {"input": 0.0, "output": 0.0},
    "deepseek-v4-flash": {"input": 0.0, "output": 0.0},
    "claude-sonnet-4-6":  {"input": 3.0,   "output": 15.0},
    "claude-opus-4-6":    {"input": 15.0,  "output": 75.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """估算本次呼叫費用（USD）。若無定價表則回傳 0。"""
    prices = _PRICE_PER_M.get(model, {})
    if not prices:
        return 0.0
    return round(
        prices.get("input", 0) * input_tokens / 1_000_000
        + prices.get("output", 0) * output_tokens / 1_000_000,
        6,
    )


def record_llm_call(
    agent: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    duration_s: float,
    run_id: str | None = None,
    date: str | None = None,
) -> dict:
    """記錄一次 LLM 呼叫，寫入 JSONL，回傳記錄 dict。

    Parameters
    ----------
    agent:         呼叫者名稱（如 "analyst", "risk_officer"）
    model:         模型 ID
    input_tokens:  輸入 token 數
    output_tokens: 輸出 token 數
    duration_s:    API 耗時（秒）
    run_id:        pipeline run_id（省略則從 RUN_CONTEXT 取）
    date:          日期字串（省略則取今日 UTC）
    """
    TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)

    rid = run_id or RUN_CONTEXT.run_id or "unknown"
    today = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cost = _estimate_cost(model, input_tokens, output_tokens)

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "date": today,
        "run_id": rid,
        "agent": agent,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "duration_s": round(duration_s, 2),
        "cost_usd": cost,
    }

    try:
        with open(LLM_USAGE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"telemetry.record_llm_call failed (non-fatal): {e}")

    return record


def aggregate_run_telemetry(run_id: str | None = None) -> dict:
    """彙整本次 run_id 的所有 LLM 呼叫，回傳統計 dict。"""
    rid = run_id or RUN_CONTEXT.run_id
    if not rid or not LLM_USAGE_PATH.exists():
        return {}

    records = []
    try:
        with open(LLM_USAGE_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("run_id") == rid:
                        records.append(obj)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning(f"telemetry.aggregate_run_telemetry read failed: {e}")
        return {}

    if not records:
        return {}

    total_input = sum(r.get("input_tokens", 0) for r in records)
    total_output = sum(r.get("output_tokens", 0) for r in records)
    total_cost = sum(r.get("cost_usd", 0) for r in records)
    total_duration = sum(r.get("duration_s", 0) for r in records)
    by_agent = {}
    for r in records:
        a = r["agent"]
        by_agent.setdefault(a, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
        by_agent[a]["calls"] += 1
        by_agent[a]["input_tokens"] += r.get("input_tokens", 0)
        by_agent[a]["output_tokens"] += r.get("output_tokens", 0)
        by_agent[a]["cost_usd"] = round(by_agent[a]["cost_usd"] + r.get("cost_usd", 0), 6)

    return {
        "run_id": rid,
        "total_calls": len(records),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "total_cost_usd": round(total_cost, 6),
        "total_duration_s": round(total_duration, 2),
        "by_agent": by_agent,
    }


class LLMTimer:
    """Context manager — 計時並自動記錄 LLM 呼叫。

    用法：
        with LLMTimer("analyst", SONNET_MODEL) as t:
            response = chat_json(...)
        record_llm_call(..., duration_s=t.elapsed)
    """
    def __init__(self, agent: str, model: str):
        self.agent = agent
        self.model = model
        self.elapsed = 0.0
        self._start = 0.0

    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, *args):
        self.elapsed = round(time.monotonic() - self._start, 2)
