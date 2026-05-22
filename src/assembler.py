from __future__ import annotations
"""Assembler — 整合所有 package，硬性驗證完整性，動態 token 分配。純 Python。"""

import json
import logging
from datetime import datetime, timezone

try:
    from src.config import (
        MEMORY_DIR, MISSING_DATA, REQUIRED_FIELDS,
        PACKAGE_TIERS, ASSEMBLER_SOFT_TARGET, ASSEMBLER_HARD_CEILING,
    )
except ImportError:
    # 防禦：長時間運行的進程（如 watchdog）可能快取了舊版 config（不含 PACKAGE_TIERS）
    # 此時重新從磁碟載入 config 模組
    import importlib, sys
    if "src.config" in sys.modules:
        importlib.reload(sys.modules["src.config"])
    from src.config import (
        MEMORY_DIR, MISSING_DATA, REQUIRED_FIELDS,
        PACKAGE_TIERS, ASSEMBLER_SOFT_TARGET, ASSEMBLER_HARD_CEILING,
    )

logger = logging.getLogger(__name__)


def _estimate_tokens(obj) -> int:
    """粗估 token 數（中文約 1.5 字/token，英文約 4 字元/token）。"""
    text = json.dumps(obj, ensure_ascii=False, default=str)
    return max(1, int(len(text) / 2.5))


def _smart_reduce(obj, target_tokens: int, label: str):
    """JSON 結構感知縮減 — 不破壞 JSON 結構，永遠回傳合法物件。

    策略（按優先順序）：
    1. dict 中的 array 欄位：從尾部移除 items（保留較早 / 重要的前段）
    2. dict 中的長字串欄位（> 500 字元）：截斷 + 省略標記
    3. 已足夠 → 原樣回傳

    永遠不做字元比例截斷（不破壞 JSON 結構）。
    """
    current = _estimate_tokens(obj)
    if current <= target_tokens:
        return obj

    if not isinstance(obj, dict):
        # 非 dict（例如 string / list）：直接標記，不截斷內容
        return {"_reduced": True, "_note": f"[{label} 已壓縮，原始 {current} tokens]"}

    result = dict(obj)
    removed_items = 0

    # Pass 1：縮減 array 欄位（從尾部移除）
    array_keys = sorted(
        [k for k, v in result.items() if isinstance(v, list) and len(v) > 1],
        key=lambda k: _estimate_tokens(result[k]),
        reverse=True,
    )
    for key in array_keys:
        if _estimate_tokens(result) <= target_tokens:
            break
        arr = result[key]
        # 每次移除 1/3 尾部，最少保留 1 個
        keep = max(1, len(arr) * 2 // 3)
        removed_items += len(arr) - keep
        result[key] = arr[:keep]

    # Pass 2：截斷長字串欄位
    str_keys = sorted(
        [k for k, v in result.items() if isinstance(v, str) and len(v) > 500],
        key=lambda k: len(result[k]),
        reverse=True,
    )
    for key in str_keys:
        if _estimate_tokens(result) <= target_tokens:
            break
        result[key] = result[key][:500] + "…（已截斷）"

    final = _estimate_tokens(result)
    if final < current:
        logger.info(
            f"{label}: smart-reduced {current} → ~{final} tokens "
            f"（移除 {removed_items} array items）"
        )
    result["_reduced"] = True
    return result


def _compute_material_density(packages: dict) -> dict:
    """評估當日材料豐富度，輸出信號給 narrator 調整輸出長度。

    Returns:
        {
            "level": "thin"|"normal"|"rich"|"crisis",
            "total_tokens": int,
            "breakdown": {"data_package": 1200, ...},
            "narrator_hint": str,
        }
    """
    breakdown = {k: _estimate_tokens(v) for k, v in packages.items()}
    total = sum(breakdown.values())

    if total < 15_000:
        level = "thin"
        hint = "今日材料偏薄。主線應精簡有力，避免灌水，字數不需強撐。"
    elif total < 30_000:
        level = "normal"
        hint = "今日材料充足。正常展開，依材料質量決定深度。"
    elif total < 45_000:
        level = "rich"
        hint = "今日材料豐富。主線可深入展開，多層因果鏈值得鋪陳。"
    else:
        level = "crisis"
        hint = "今日材料爆量（重大事件日）。聚焦核心因果鏈，不需鋪陳邊緣細節。"

    return {
        "level": level,
        "total_tokens": total,
        "breakdown": breakdown,
        "narrator_hint": hint,
    }


def _tier_based_reduce(packages: dict, total_tokens: int) -> dict:
    """Tier-based 截斷：T3 先減（50%），T2 次之（30%），T1 永不動。"""
    overshoot = total_tokens - ASSEMBLER_HARD_CEILING
    result = dict(packages)

    for tier in [3, 2]:
        if overshoot <= 0:
            break
        tier_keys = [k for k, t in PACKAGE_TIERS.items() if t == tier]
        # 從最大的開始縮減
        tier_keys.sort(key=lambda k: _estimate_tokens(result.get(k, {})), reverse=True)

        reduction_ratio = 0.5 if tier == 3 else 0.3

        for key in tier_keys:
            if overshoot <= 0:
                break
            if key not in result:
                continue
            current = _estimate_tokens(result[key])
            target = max(50, int(current * (1 - reduction_ratio)))
            if target < current:
                result[key] = _smart_reduce(result[key], target, key)
                saved = current - _estimate_tokens(result[key])
                overshoot -= saved

    return result


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


def _extract_active_topics(data_package: dict) -> set[str]:
    """從 data_package 提取今日有效資產名稱，用於 L4 過濾。"""
    quality_scores = data_package.get("quality_scores", {})
    return {
        key for key, quality in quality_scores.items()
        if quality not in (MISSING_DATA, "MISSING_DATA", None)
    }


def _load_l4_filtered(active_topics: set[str], current_regime: str,
                      limit: int = 15) -> dict:
    """按需載入 L4：只取與今日 topics 或 regime 相關的 entry。

    過濾邏輯（OR 關係）：
    1. entry.regime 與今日 regime 完全相符
    2. entry 的文字欄位中包含 active_topics 內的資產名稱

    排序：日期降序（最新優先），取前 limit 條。
    """
    l4 = _load_memory_layer("l4.json")
    entries = l4.get("entries", [])
    if not entries:
        return l4

    original_count = len(entries)

    def is_relevant(entry: dict) -> bool:
        if entry.get("regime", "") == current_regime:
            return True
        text = " ".join([
            entry.get("claim", ""),
            entry.get("logic_summary", ""),
            entry.get("data_reference", ""),
        ]).lower()
        return any(topic.lower() in text for topic in active_topics)

    relevant = [e for e in entries if is_relevant(e)]
    relevant.sort(key=lambda e: e.get("date", ""), reverse=True)
    filtered = relevant[:limit]

    result = dict(l4)
    result["entries"] = filtered
    if original_count > len(filtered):
        logger.info(
            f"L4 filtered: {original_count} → {len(filtered)} entries "
            f"(regime='{current_regime}', active_topics={len(active_topics)})"
        )
    return result


def compute_coverage_score(data_package: dict) -> float:
    """計算加權數據覆蓋率（品質感知：confirmed=1.0, cached=0.85, estimated=0.5…）。"""
    from src.config import COVERAGE_WEIGHTS, QUALITY_MULTIPLIER
    quality_scores = data_package.get("quality_scores", {})
    if not quality_scores or not isinstance(quality_scores, dict):
        return 0.0
    total_weight = sum(COVERAGE_WEIGHTS.get(k, 0.5) for k in quality_scores)
    weighted_score = sum(
        COVERAGE_WEIGHTS.get(k, 0.5) * QUALITY_MULTIPLIER.get(v, 0.0)
        for k, v in quality_scores.items()
    )
    return round(weighted_score / total_weight, 2) if total_weight > 0 else 0.0


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
    l1 = _load_yesterday_tension()
    l2 = _load_memory_layer("l2.json")
    l3 = _load_memory_layer("l3.json")
    l5 = _load_memory_layer("l5.json")

    # L4 按需載入：regime 轉換時翻倍（30 條），正常 15 條
    current_regime = l1.get("regime", "")
    # M2 修正：yesterday_regime 與 current_regime 來自同一 l1 快照（昨日），
    # 無法在此偵測 regime shift，改用 active_topics 數量啟發式。
    active_topics = _extract_active_topics(data_package)
    # 若 active_topics 覆蓋廣（>15 個資產有效），給予更多歷史上下文
    l4_limit = 25 if len(active_topics) > 15 else 15
    l4 = _load_l4_filtered(active_topics, current_regime, limit=l4_limit)

    # 4. 組裝（不截斷）
    packages = {
        "coverage_warning": coverage_warning,
        "calendar_package": calendar_package,
        "data_package": data_package,
        "quant_package": quant_package,
        "historian_package": historian_package,
        "sentiment_package": sentiment_package,
        "geopolitical_package": geopolitical_package,
        "l1_context": l1,
        "l2_context": l2,
        "l3_context": l3,
        "l4_context": l4,
        "l5_context": l5,
    }

    # 5. 計算 total tokens
    total_tokens = sum(_estimate_tokens(v) for v in packages.values())

    # 6. 動態截斷（只在超過 HARD_CEILING 時啟動）
    if total_tokens > ASSEMBLER_HARD_CEILING:
        logger.info(
            f"Assembler: total {total_tokens} tokens exceeds hard ceiling {ASSEMBLER_HARD_CEILING}, "
            f"activating tier-based reduction"
        )
        packages = _tier_based_reduce(packages, total_tokens)
        total_tokens = sum(_estimate_tokens(v) for v in packages.values())
    elif total_tokens > ASSEMBLER_SOFT_TARGET:
        logger.info(
            f"Assembler: total {total_tokens} tokens above soft target {ASSEMBLER_SOFT_TARGET} "
            f"(below hard ceiling — no truncation)"
        )

    # 7. 計算材料密度信號
    material_density = _compute_material_density(packages)

    # 8. Token 預算追蹤（治理可視化）
    pkg_tokens = {k: _estimate_tokens(v) for k, v in packages.items()}
    data_keys = {"data_package", "quant_package", "historian_package",
                 "sentiment_package", "geopolitical_package", "calendar_package"}
    memory_keys = {"l1_context", "l2_context", "l3_context", "l4_context", "l5_context"}
    data_total = sum(pkg_tokens.get(k, 0) for k in data_keys)
    memory_total = sum(pkg_tokens.get(k, 0) for k in memory_keys)
    token_allocation = {
        "by_package": pkg_tokens,
        "data_total": data_total,
        "memory_total": memory_total,
        "overhead": pkg_tokens.get("coverage_warning", 0),
        "data_to_memory_ratio": round(data_total / memory_total, 2) if memory_total > 0 else 0,
    }

    assembled = {
        "assembled_at": datetime.now(timezone.utc).isoformat(),
        "coverage_score": coverage,
        "total_estimated_tokens": total_tokens,
        "material_density": material_density,
        "token_allocation": token_allocation,
        "data_gaps": all_gaps,
        "packages": packages,
    }

    logger.info(
        f"Assembler: coverage={coverage}, gaps={len(all_gaps)}, "
        f"tokens≈{total_tokens}, density={material_density['level']}"
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
