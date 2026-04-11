#!/usr/bin/env python3
"""Cold Start — 大版本升級時清除可變記憶層，保留歷史快照和向量索引。

保留（歷史資產）：
  memory/daily_snapshots/    ← 每日不可變快照
  memory/archive/            ← 歷史快照（historian 用）
  memory/vectors/            ← 嵌入向量（historian 用）
  memory/cache/              ← 數據快取（下次 pipeline 會更新）
  memory/timeseries/market.parquet  ← 市場歷史數據

清除（狀態記憶）：
  memory/theses/active|proposed|closed|archived  ← Thesis 全部重來
  memory/l3.json             ← Active theses 同步
  memory/l4.json             ← 裁決歷史
  memory/l5.json             ← 預測評分卡
  memory/timeseries/inference_history.jsonl
  memory/timeseries/regime_history.json
  memory/timeseries/scorecard_history.json
  memory/system/calibration.json

用法：
  python3 scripts/cold_start.py
  python3 scripts/cold_start.py --dry-run   # 只印出會刪什麼，不執行
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MEMORY = PROJECT_ROOT / "memory"


def cold_start(dry_run: bool = False):
    def do(action: str, path: Path):
        print(f"  {'[DRY-RUN] ' if dry_run else ''}{action}: {path.relative_to(PROJECT_ROOT)}")
        if not dry_run:
            if action == "delete_dir":
                shutil.rmtree(path, ignore_errors=True)
                path.mkdir(parents=True, exist_ok=True)
            elif action == "write_empty":
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(path._empty_content), encoding="utf-8")  # type: ignore
            elif action == "write_text":
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(path._content, encoding="utf-8")  # type: ignore

    print("=" * 60)
    print("DIB Cold Start")
    print("=" * 60)

    # Thesis 目錄（全清，重建空目錄）
    for subdir in ["active", "proposed", "closed", "archived"]:
        p = MEMORY / "theses" / subdir
        do("delete_dir", p)

    # L3（active_theses 清空）
    l3_path = MEMORY / "l3.json"
    if l3_path.exists():
        try:
            l3 = json.loads(l3_path.read_text(encoding="utf-8"))
            l3["active_theses"] = []
            if not dry_run:
                l3_path.write_text(json.dumps(l3, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  {'[DRY-RUN] ' if dry_run else ''}clear active_theses: memory/l3.json")
        except Exception as e:
            print(f"  [WARN] l3.json parse failed: {e}")

    # L5
    _write_json(MEMORY / "l5.json", {"predictions": []}, dry_run)

    # Inference history（JSONL → 清空）
    _write_text(MEMORY / "timeseries" / "inference_history.jsonl", "", dry_run)

    # Regime + scorecard history
    _write_json(MEMORY / "timeseries" / "regime_history.json", [], dry_run)
    _write_json(MEMORY / "timeseries" / "scorecard_history.json", [], dry_run)

    # Calibration
    _write_json(
        MEMORY / "system" / "calibration.json",
        {"accuracy_by_asset_regime": {}, "bias_by_asset": {}, "total_predictions": 0},
        dry_run,
    )

    print()
    print("保留：daily_snapshots/, archive/, vectors/, cache/, market.parquet")
    print("完成。" if not dry_run else "（dry-run 模式，未執行任何刪除）")


def _write_json(path: Path, content, dry_run: bool):
    print(f"  {'[DRY-RUN] ' if dry_run else ''}reset: {path.relative_to(PROJECT_ROOT)}")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_text(path: Path, content: str, dry_run: bool):
    print(f"  {'[DRY-RUN] ' if dry_run else ''}clear: {path.relative_to(PROJECT_ROOT)}")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DIB Cold Start — 大版本升級後清除狀態記憶")
    parser.add_argument("--dry-run", action="store_true", help="只印出動作，不執行")
    args = parser.parse_args()

    if not args.dry_run:
        confirm = input("⚠️  這將清除所有 thesis、calibration 和推論歷史。確定？(yes/N): ")
        if confirm.strip().lower() != "yes":
            print("已取消。")
            sys.exit(0)

    cold_start(dry_run=args.dry_run)
