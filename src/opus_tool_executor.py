from __future__ import annotations
"""OpusToolExecutor — Opus 的工具執行層，白名單強制。純 Python。"""

import json
import logging
from datetime import datetime, timezone

from src.config import MISSING_DATA, OPUS_ALLOWED_TOOLS, OPUS_FORBIDDEN_TOOLS, SYSTEM_DIR

logger = logging.getLogger(__name__)


class OpusToolExecutor:
    """強制執行 Opus 工具白名單。禁止 web_search、call_api 等。"""

    def __init__(self, assembled_data: dict, memory_layers: dict, historian_package: dict | None = None):
        self.assembled_data = assembled_data
        self.memory_layers = memory_layers
        self.historian_package = historian_package or {}
        self._gaps: list[dict] = []

    def execute(self, tool_name: str, tool_input: dict) -> dict:
        """執行工具請求，不在白名單的一律拒絕。"""
        if tool_name in OPUS_FORBIDDEN_TOOLS:
            logger.warning(f"OpusToolExecutor: BLOCKED forbidden tool '{tool_name}'")
            return {
                "error": f"Tool '{tool_name}' is not permitted for the risk officer. "
                         f"Allowed tools: {OPUS_ALLOWED_TOOLS}",
                "blocked": True,
            }

        if tool_name not in OPUS_ALLOWED_TOOLS:
            logger.warning(f"OpusToolExecutor: BLOCKED unknown tool '{tool_name}'")
            return {
                "error": f"Tool '{tool_name}' is not in the allowed tool list.",
                "blocked": True,
            }

        # 執行允許的工具
        if tool_name == "query_computed_data":
            return self._query_computed_data(tool_input)
        elif tool_name == "query_memory_layer":
            return self._query_memory_layer(tool_input)
        elif tool_name == "query_historian":
            return self._query_historian(tool_input)
        elif tool_name == "flag_data_gap":
            return self._flag_data_gap(tool_input)

        return {"error": f"Tool '{tool_name}' not implemented", "blocked": False}

    def _query_computed_data(self, params: dict) -> dict:
        """只讀 assembled_data。"""
        key = params.get("key", "")
        packages = self.assembled_data.get("packages", {})

        for pkg_name, pkg in packages.items():
            if isinstance(pkg, dict) and key in pkg:
                return {"key": key, "value": pkg[key], "source": pkg_name}

        return {"key": key, "value": MISSING_DATA, "error": f"Key '{key}' not found"}

    def _query_memory_layer(self, params: dict) -> dict:
        """只讀 L2/L3/L5 記憶層。"""
        layer = params.get("layer", "")
        if layer not in ["l2", "l3", "l5"]:
            return {"error": f"Memory layer '{layer}' not accessible (allowed: l2, l3, l5)"}
        return {"layer": layer, "data": self.memory_layers.get(layer, {})}

    def _query_historian(self, params: dict) -> dict:
        """只讀 historian_package。"""
        key = params.get("key", "")
        if key:
            return {"key": key, "value": self.historian_package.get(key, MISSING_DATA)}
        return {"data": self.historian_package}

    def _flag_data_gap(self, params: dict) -> dict:
        """標記數據缺口，寫入 data_gaps.json。唯一有寫入行為的工具。"""
        data_key = params.get("data_key", "")
        impact = params.get("impact", "MEDIUM")
        note = params.get("note", "")

        gap = {
            "data_key": data_key,
            "impact": impact,
            "note": note,
            "flagged_by": "opus_risk_officer",
            "flagged_at": datetime.now(timezone.utc).isoformat(),
        }
        self._gaps.append(gap)

        # 寫入 data_gaps.json
        SYSTEM_DIR.mkdir(parents=True, exist_ok=True)
        gaps_path = SYSTEM_DIR / "data_gaps.json"
        existing = []
        if gaps_path.exists():
            try:
                existing = json.loads(gaps_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing.append(gap)
        gaps_path.write_text(json.dumps(existing[-500:], indent=2, ensure_ascii=False), encoding="utf-8")

        logger.info(f"OpusToolExecutor: data gap flagged — {data_key} ({impact})")
        return {"flagged": True, "data_key": data_key}
