from __future__ import annotations
"""Citation Checker — 前後各跑一次，核對 LLM 輸出的每個引用。純 Python。"""

import json
import logging
from datetime import datetime, timezone

from src.config import MISSING_DATA, SYSTEM_DIR

logger = logging.getLogger(__name__)

# 旗標類型
PHANTOM_CITATION = "PHANTOM_CITATION"
MISSING_DATA_CITED = "MISSING_DATA_CITED"
VALUE_MISMATCH = "VALUE_MISMATCH"
DEPENDENCY_FAILED = "DEPENDENCY_FAILED"

INTEGRITY_THRESHOLD = 0.8

# LLM 衍生 key 前綴——這些 key 來自輿情/thesis/地緣分析，不在 data_package 中
# citation_checker 對這類 key 不做 phantom 檢查（不計入 total_checks）
_SOFT_KEY_PREFIXES = (
    "sentiment_", "geo_", "l3_thesis_", "l2_", "l4_",
    "active_risks.", "regime_probability.", "data_package_",
)


class CitationChecker:
    def _lookup(
        self,
        key: str,
        data_package: dict,
        quant_package: dict,
        calendar_package: dict = None,
    ):
        """從各 package 查找 key，支援嵌套查詢。"""

        # 1. 直接查頂層（data_package、quant_package）
        for pkg in [data_package, quant_package]:
            if key in pkg:
                item = pkg[key]
                if isinstance(item, dict):
                    return item.get("price") or item.get("value")
                return item

        # 2. zscore keys：{asset}_zscore → quant_package["zscore_alerts"][asset]
        if key.endswith("_zscore"):
            asset = key[: -len("_zscore")]
            zscore_alerts = quant_package.get("zscore_alerts", {})
            if asset in zscore_alerts:
                return zscore_alerts[asset]

        # 3. correlation keys：{a}_{b}_correlation → quant_package["correlation_matrix_30d"]["{a}_{b}"]
        if key.endswith("_correlation"):
            pair = key[: -len("_correlation")]
            corr_matrix = quant_package.get("correlation_matrix_30d", {})
            if pair in corr_matrix:
                return corr_matrix[pair]
            # 試反向 key（例：gold_brent vs brent_gold）
            parts = pair.split("_", 1)
            if len(parts) == 2:
                rev = f"{parts[1]}_{parts[0]}"
                if rev in corr_matrix:
                    return corr_matrix[rev]

        # 4. granger keys：granger_{a}_{b} → quant_package["granger_causality"] list
        if key.startswith("granger_"):
            rest = key[len("granger_"):]
            granger_list = quant_package.get("granger_causality", [])
            parts = rest.split("_", 1)
            if len(parts) == 2:
                cause, effect = parts
                for entry in granger_list:
                    if entry.get("cause") == cause and entry.get("effect") == effect:
                        return entry.get("p_value")

        # 5. calendar keys（fomc_days_out、nfp_days_out 等）→ calendar_package
        if calendar_package and key in calendar_package:
            return calendar_package[key]

        # 6. tgri_score → quant_package 或 data_package
        if key == "tgri_score":
            val = quant_package.get("tgri_score")
            if val is None:
                val = data_package.get("tgri_score")
            return val

        return None


def _lookup(assembled_data: dict, data_key: str, calendar_package: dict = None):
    """從 assembled_data 的 packages 找值（向下相容包裝）。"""
    packages = assembled_data.get("packages", {})
    dp = packages.get("data_package", {})
    qp = packages.get("quant_package", {})
    cal = calendar_package or assembled_data.get("packages", {}).get("calendar_package")
    return CitationChecker()._lookup(data_key, dp, qp, cal)


def _value_mismatch(actual, cited, tolerance: float = 0.02) -> bool:
    """允許 2% 誤差（市場數據會有小數點差異）。"""
    try:
        a = float(actual)
        c = float(cited)
        if a == 0:
            return c != 0
        return abs(a - c) / abs(a) > tolerance
    except (TypeError, ValueError):
        return str(actual) != str(cited)


def verify_chain(
    inference_chain: list[dict],
    assembled_data: dict,
    calendar_package: dict = None,
) -> dict:
    """核對 inference_chain 的每個引用。"""
    flags = []
    total_checks = 0
    failed_checks = 0

    verified_ids = set()

    for inf in inference_chain:
        inf_id = inf.get("id", "UNKNOWN")

        # 驗證 evidence
        for ev in inf.get("evidence", []):
            data_key = ev.get("data_key", "")
            cited_value = ev.get("value")

            # Soft keys（輿情/thesis/地緣衍生）不計入 integrity 檢查
            if any(data_key.startswith(prefix) for prefix in _SOFT_KEY_PREFIXES):
                continue

            total_checks += 1
            actual = _lookup(assembled_data, data_key, calendar_package)

            if actual is None:
                flags.append({
                    "type": PHANTOM_CITATION,
                    "inference_id": inf_id,
                    "data_key": data_key,
                    "detail": f"data_key '{data_key}' not found in assembled_data",
                })
                failed_checks += 1

            elif actual == MISSING_DATA:
                flags.append({
                    "type": MISSING_DATA_CITED,
                    "inference_id": inf_id,
                    "data_key": data_key,
                    "detail": f"'{data_key}' is MISSING_DATA but was cited as {cited_value}",
                })
                failed_checks += 1

            elif cited_value is not None and _value_mismatch(actual, cited_value):
                flags.append({
                    "type": VALUE_MISMATCH,
                    "inference_id": inf_id,
                    "data_key": data_key,
                    "actual": actual,
                    "cited": cited_value,
                    "detail": f"Value mismatch: actual={actual}, cited={cited_value}",
                })
                failed_checks += 1

        verified_ids.add(inf_id)

        # 驗證依賴鏈
        for dep_id in inf.get("dependencies", []):
            if dep_id not in verified_ids:
                total_checks += 1
                flags.append({
                    "type": DEPENDENCY_FAILED,
                    "inference_id": inf_id,
                    "dependency_id": dep_id,
                    "detail": f"Dependency {dep_id} not verified before {inf_id}",
                })
                failed_checks += 1

    integrity_score = round(1 - (failed_checks / total_checks), 3) if total_checks > 0 else 1.0

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_checks": total_checks,
        "failed_checks": failed_checks,
        "integrity_score": integrity_score,
        "flags": flags,
        "pass": integrity_score >= INTEGRITY_THRESHOLD,
    }

    if not result["pass"]:
        logger.error(
            f"Citation integrity FAILED: {integrity_score:.2f} < {INTEGRITY_THRESHOLD} "
            f"({failed_checks}/{total_checks} failed)"
        )
    else:
        logger.info(
            f"Citation integrity OK: {integrity_score:.2f} "
            f"({failed_checks}/{total_checks} failed)"
        )

    # 寫入 data_gaps.json
    if flags:
        _save_gaps(flags)

    return result


def _save_gaps(flags: list[dict]):
    """記錄缺口到 system/data_gaps.json。"""
    SYSTEM_DIR.mkdir(parents=True, exist_ok=True)
    gaps_path = SYSTEM_DIR / "data_gaps.json"
    existing = []
    if gaps_path.exists():
        try:
            raw = json.loads(gaps_path.read_text(encoding="utf-8"))
            # 相容舊格式 {"gaps": [...]} 和新格式 [...]
            existing = raw.get("gaps", raw) if isinstance(raw, dict) else raw
            if not isinstance(existing, list):
                existing = []
        except Exception:
            pass
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for f in flags:
        existing.append({"date": today, **f})
    gaps_path.write_text(json.dumps(existing[-500:], indent=2, ensure_ascii=False), encoding="utf-8")
