from __future__ import annotations
"""Assembler — 整合所有 package，硬性驗證完整性，管理 token 預算。純 Python。"""

import json
import logging
from datetime import datetime, timezone

from src.config import MEMORY_DIR, MISSING_DATA, REQUIRED_FIELDS, TOKEN_BUDGETS

logger = logging.getLogger(__name__)


def _estimate_tokens(obj) -> int:
    """粗估 token 數（中文約 1.5 字/token，英文約 4 字元/token）。"""
    text = json.dumps(obj, ensure_ascii=False, default=str)
    # 混合中英文的粗估
    return max(1, int(len(text) / 2.5))


def _truncate_to_budget(obj, budget: int, label: str):
    """截斷到 token 預算，留下截斷標記。"""
    estimated = _estimate_tokens(obj)
    if estimated <= budget:
        return obj

    text = json.dumps(obj, ensure_ascii=False, default=str)
    # 按比例截斷
    ratio = budget / estimated
    cut_len = int(len(text) * ratio * 0.9)  # 留 10% 空間給截斷標記
    truncated_text = text[:cut_len]
    logger.warning(f"{label}: truncated from {estimated} to ~{budget} tokens")

    return {
        "_truncated": True,
        "_original_tokens": estimated,
        "_budget": budget,
        "_warning": f"[⚠️ 已截斷，原始 {estimated} token]",
        "data": truncated_text,
    }


def _validate_package(package: dict | None, package_name: str) -> tuple[dict, list[str]]:
    """驗證 package 的必要欄位，缺失填 MISSING_DATA。"""
    if package is None:
        package = {}

    required = REQUIRED_FIELDS.get(package_name, [])
    gaps = []

    for field in required:
        if field not in package:
            package[field] = MISSING_DATA
            gaps.append(f"{package_name}.{field}")
        elif package[field] is None:
            package[field] = MISSING_DATA
            gaps.append(f"{package_name}.{field}")

    return package, gaps


def _load_memory_layer(filename: str) -> dict:
    """載入記憶層 JSON。"""
    path = MEMORY_DIR / filename
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Failed to load {filename}: {e}")
        return {}


def compute_coverage_score(data_package: dict) -> float:
    """計算加權數據覆蓋率（核心指標權重高，邊緣指標權重低）。"""
    from src.config import COVERAGE_WEIGHTS
    quality_scores = data_package.get("quality_scores", {})
    if not quality_scores or not isinstance(quality_scores, dict):
        return 0.0
    total_weight = sum(COVERAGE_WEIGHTS.get(k, 0.5) for k in quality_scores)
    confirmed_weight = sum(
        COVERAGE_WEIGHTS.get(k, 0.5)
        for k, v in quality_scores.items()
        if v == "confirmed"
    )
    return round(confirmed_weight / total_weight, 2) if total_weight > 0 else 0.0


def run_assembler(
    calendar_package: dict | None = None,
    data_package: dict | None = None,
    quant_package: dict | None = None,
    historian_package: dict | None = None,
    sentiment_package: dict | None = None,
    geopolitical_package: dict | None = None,
) -> dict:
    """主入口：組裝所有 package 為 assembled_context。"""

    all_gaps = []

    # 1. 驗證各 package
    calendar_package, gaps = _validate_package(calendar_package, "calendar_package")
    all_gaps.extend(gaps)

    data_package, gaps = _validate_package(data_package, "data_package")
    all_gaps.extend(gaps)

    quant_package, gaps = _validate_package(quant_package, "quant_package")
    all_gaps.extend(gaps)

    historian_package, gaps = _validate_package(historian_package, "historian_package")
    all_gaps.extend(gaps)

    sentiment_package, gaps = _validate_package(sentiment_package, "sentiment_package")
    all_gaps.extend(gaps)

    geopolitical_package, gaps = _validate_package(geopolitical_package, "geopolitical_package")
    all_gaps.extend(gaps)

    # 2. 計算覆蓋率
    coverage = compute_coverage_score(data_package)
    coverage_warning = ""
    if coverage < 0.7:
        coverage_warning = f"⚠️ 嚴重數據缺口：覆蓋率僅 {coverage:.0%}，以下分析的可信度受限"
    elif coverage < 0.9:
        coverage_warning = f"⚠️ 部分數據缺口：覆蓋率 {coverage:.0%}，部分判斷可能受影響"

    # 3. 載入記憶層
    l2 = _load_memory_layer("l2.json")
    l3 = _load_memory_layer("l3.json")
    l4 = _load_memory_layer("l4.json")
    l5 = _load_memory_layer("l5.json")

    # L1 = 昨日張力（從最近的 daily_snapshot 取）
    l1 = _load_yesterday_tension()

    # 4. Token 預算截斷
    packages = {
        "coverage_warning": coverage_warning,
        "calendar_package": _truncate_to_budget(calendar_package, TOKEN_BUDGETS["calendar_package"], "calendar"),
        "data_package": _truncate_to_budget(data_package, TOKEN_BUDGETS["data_package"], "data"),
        "quant_package": _truncate_to_budget(quant_package, TOKEN_BUDGETS["quant_package"], "quant"),
        "historian_package": _truncate_to_budget(historian_package, TOKEN_BUDGETS["historian_package"], "historian"),
        "sentiment_package": _truncate_to_budget(sentiment_package, TOKEN_BUDGETS["sentiment_package"], "sentiment"),
        "geopolitical_package": _truncate_to_budget(geopolitical_package, TOKEN_BUDGETS["geopolitical_package"], "geopolitical"),
        "l1_context": _truncate_to_budget(l1, TOKEN_BUDGETS["l1_context"], "l1"),
        "l2_context": _truncate_to_budget(l2, TOKEN_BUDGETS["l2_context"], "l2"),
        "l3_context": _truncate_to_budget(l3, TOKEN_BUDGETS["l3_context"], "l3"),
        "l4_context": _truncate_to_budget(l4, TOKEN_BUDGETS["l4_context"], "l4"),
        "l5_context": _truncate_to_budget(l5, TOKEN_BUDGETS["l5_context"], "l5"),
    }

    # 5. 計算總 token
    total_tokens = sum(_estimate_tokens(v) for v in packages.values())

    assembled = {
        "assembled_at": datetime.now(timezone.utc).isoformat(),
        "coverage_score": coverage,
        "total_estimated_tokens": total_tokens,
        "data_gaps": all_gaps,
        "packages": packages,
    }

    logger.info(
        f"Assembler: coverage={coverage}, gaps={len(all_gaps)}, "
        f"tokens≈{total_tokens}"
    )
    return assembled


def _load_yesterday_tension() -> dict:
    """載入昨日張力（L1）。"""
    from src.config import SNAPSHOTS_DIR
    if not SNAPSHOTS_DIR.exists():
        return {"tension": MISSING_DATA}

    snapshots = sorted(SNAPSHOTS_DIR.glob("*.json"), reverse=True)
    if not snapshots:
        return {"tension": MISSING_DATA}

    try:
        latest = json.loads(snapshots[0].read_text(encoding="utf-8"))
        return {
            "date": latest.get("date", ""),
            "regime": latest.get("metadata", {}).get("regime", MISSING_DATA),
            "core_tension": latest.get("core_tension", MISSING_DATA),
        }
    except Exception:
        return {"tension": MISSING_DATA}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_assembler()
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
