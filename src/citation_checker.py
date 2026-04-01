"""Citation Checker — 前後各跑一次，核對 LLM 輸出的每個引用。純 Python。"""

import json
import logging
from datetime import datetime

from src.config import MISSING_DATA, SYSTEM_DIR

logger = logging.getLogger(__name__)

# 旗標類型
PHANTOM_CITATION = "PHANTOM_CITATION"
MISSING_DATA_CITED = "MISSING_DATA_CITED"
VALUE_MISMATCH = "VALUE_MISMATCH"
DEPENDENCY_FAILED = "DEPENDENCY_FAILED"

INTEGRITY_THRESHOLD = 0.8


def _lookup(assembled_data: dict, data_key: str):
    """從 assembled_data 的 packages.data_package 找值。"""
    packages = assembled_data.get("packages", {})
    dp = packages.get("data_package", {})
    qp = packages.get("quant_package", {})

    # 依序查找
    for pkg in [dp, qp]:
        if data_key in pkg:
            item = pkg[data_key]
            if isinstance(item, dict):
                return item.get("price") or item.get("value")
            return item
    return None


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


def verify_chain(inference_chain: list[dict], assembled_data: dict) -> dict:
    """核對 inference_chain 的每個引用。"""
    flags = []
    total_checks = 0
    failed_checks = 0

    verified_ids = set()

    for inf in inference_chain:
        inf_id = inf.get("id", "UNKNOWN")

        # 驗證 evidence
        for ev in inf.get("evidence", []):
            total_checks += 1
            data_key = ev.get("data_key", "")
            cited_value = ev.get("value")

            actual = _lookup(assembled_data, data_key)

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
        "timestamp": datetime.now().isoformat(),
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
            existing = json.loads(gaps_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    today = datetime.now().strftime("%Y-%m-%d")
    for f in flags:
        existing.append({"date": today, **f})
    gaps_path.write_text(json.dumps(existing[-500:], indent=2, ensure_ascii=False), encoding="utf-8")
