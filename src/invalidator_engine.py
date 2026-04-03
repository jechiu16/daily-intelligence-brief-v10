from __future__ import annotations
"""InvalidatorEngine — 結構化條件檢查，invalidator 觸發。純 Python。"""

import json
import logging
from datetime import datetime, timezone

from src.config import MISSING_DATA, THESES_DIR

logger = logging.getLogger(__name__)

OPERATORS = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _evaluate(invalidator: dict, data: dict) -> bool:
    """評估單一 invalidator 條件。"""
    data_key = invalidator.get("data_key", "")
    operator = invalidator.get("operator", "")
    threshold = invalidator.get("threshold")

    if not data_key or operator not in OPERATORS:
        return False

    # 從 data_package 找值
    val = _lookup_value(data, data_key)
    if val is None or val == MISSING_DATA:
        return False

    try:
        return OPERATORS[operator](float(val), float(threshold))
    except (TypeError, ValueError) as e:
        logger.debug(f"InvalidatorEngine evaluate error for {data_key}: {e}")
        return False


def _lookup_value(data: dict, key: str):
    """從 data_package 找數值。"""
    if key in data:
        item = data[key]
        if isinstance(item, dict):
            return item.get("price") or item.get("value")
        return item
    return None


def check_all(data_package: dict, theses: list[dict], today_str: str | None = None) -> list[dict]:
    """
    檢查所有 thesis 的 invalidator 條件。
    回傳觸發的清單。
    """
    if today_str is None:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    triggered = []
    updated_theses = []

    for thesis in theses:
        tid = thesis.get("id", "")
        thesis_updated = dict(thesis)

        for i, inv in enumerate(thesis.get("invalidators", [])):
            if inv.get("triggered"):
                continue  # 已觸發，跳過

            if _evaluate(inv, data_package):
                val = _lookup_value(data_package, inv.get("data_key", ""))
                trigger_record = {
                    "thesis_id": tid,
                    "invalidator": inv.get("condition", ""),
                    "data_key": inv.get("data_key", ""),
                    "triggered_at": today_str,
                    "value": val,
                }
                triggered.append(trigger_record)

                # 更新 thesis 的 invalidator 狀態
                thesis_updated["invalidators"][i]["triggered"] = True
                thesis_updated["invalidators"][i]["triggered_date"] = today_str

                logger.warning(
                    f"InvalidatorEngine: TRIGGERED thesis={tid}, "
                    f"condition='{inv.get('condition')}', value={val}"
                )

        updated_theses.append(thesis_updated)

    # 寫回 l3.json
    if triggered:
        _update_theses_file(updated_theses)

    return triggered


def _update_theses_file(updated_theses: list[dict]):
    """更新 l3.json 中的 thesis invalidator 狀態。"""
    from src.config import MEMORY_DIR
    l3_path = MEMORY_DIR / "l3.json"
    try:
        l3 = json.loads(l3_path.read_text(encoding="utf-8"))
        l3["active_theses"] = updated_theses
        l3["last_updated"] = datetime.now(timezone.utc).isoformat()
        l3_path.write_text(json.dumps(l3, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.error(f"InvalidatorEngine: failed to update l3.json: {e}")


def load_active_theses() -> list[dict]:
    """從 l3.json 載入 active theses。"""
    from src.config import MEMORY_DIR
    l3_path = MEMORY_DIR / "l3.json"
    if not l3_path.exists():
        return []
    try:
        l3 = json.loads(l3_path.read_text(encoding="utf-8"))
        return [t for t in l3.get("active_theses", []) if t.get("status") == "active"]
    except Exception as e:
        logger.error(f"InvalidatorEngine: failed to load l3.json: {e}")
        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # 測試：模擬 data_package 和 thesis
    test_data = {"gold": {"price": 4185.0, "quality": "confirmed"}}
    test_theses = [{
        "id": "T_TEST",
        "status": "active",
        "title": "測試 thesis",
        "deadline": "2099-12-31",
        "invalidators": [{
            "condition": "gold_price > 4180",
            "data_key": "gold",
            "operator": ">",
            "threshold": 4180,
            "triggered": False,
        }],
        "confidence_history": [],
    }]
    result = check_all(test_data, test_theses)
    print(json.dumps(result, indent=2, ensure_ascii=False))
