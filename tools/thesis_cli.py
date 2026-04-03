#!/usr/bin/env python3
"""Thesis Lifecycle CLI — 管理 DIB v10 的 active theses。

用法：
    python tools/thesis_cli.py list
    python tools/thesis_cli.py create --title "..." --rationale "..." --deadline 2026-09-30
    python tools/thesis_cli.py close T001 --reason "invalidated by ..."
    python tools/thesis_cli.py update-confidence T001 0.75
    python tools/thesis_cli.py show T001
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# ── 路徑 ──────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
THESES_ACTIVE = PROJECT_ROOT / "memory" / "theses" / "active"
THESES_CLOSED = PROJECT_ROOT / "memory" / "theses" / "closed"
L3_PATH = PROJECT_ROOT / "memory" / "l3.json"


# ── 工具函數 ──────────────────────────────────────────────────────────────────

def _load_all_theses(directory: Path) -> dict[str, dict]:
    """載入目錄中所有 thesis JSON，以 id 為 key。"""
    theses = {}
    if not directory.exists():
        return theses
    for f in sorted(directory.glob("*.json")):
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(t, dict) and "id" in t:
                theses[t["id"]] = t
        except Exception as e:
            print(f"[WARN] 無法讀取 {f.name}: {e}", file=sys.stderr)
    return theses


def _find_thesis_file(thesis_id: str, directory: Path) -> Path | None:
    """在目錄中尋找指定 id 的 thesis 檔案（id 不分大小寫）。"""
    if not directory.exists():
        return None
    tid = thesis_id.upper()
    for f in directory.glob("*.json"):
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(t, dict) and t.get("id", "").upper() == tid:
                return f
        except Exception:
            continue
    return None


def _safe_filename(title: str) -> str:
    """將 title 轉為安全的檔名（ASCII，移除特殊字元）。"""
    slug = re.sub(r"[^\w\s-]", "", title.lower(), flags=re.UNICODE)
    slug = re.sub(r"[\s_]+", "_", slug).strip("_")
    return slug[:40] if slug else "thesis"


def _next_thesis_id() -> str:
    """產生下一個可用的 Thesis ID（T001, T002, ...）。"""
    active = _load_all_theses(THESES_ACTIVE)
    closed = _load_all_theses(THESES_CLOSED)
    all_ids = set(active) | set(closed)
    i = 1
    while f"T{i:03d}" in all_ids:
        i += 1
    return f"T{i:03d}"


def _sync_l3(today_str: str | None = None):
    """在 CLI 操作完成後同步 l3.json（避免 import 整個 orchestrator）。"""
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from src.memory_manager import sync_theses_dir_to_l3
        sync_theses_dir_to_l3(today_str)
    except Exception as e:
        print(f"[WARN] L3 sync 失敗（非致命）: {e}", file=sys.stderr)


# ── 指令：list ──────────────────────────────────────────────────────────────

def cmd_list(args):
    """列出所有 active theses。"""
    theses = _load_all_theses(THESES_ACTIVE)
    if not theses:
        print("（目前沒有 active theses）")
        return

    print(f"{'ID':<6} {'Title':<40} {'Confidence':<10} {'Deadline':<12} {'Status'}")
    print("-" * 80)
    for tid, t in sorted(theses.items()):
        conf = t.get("current_confidence") or t.get("initial_confidence") or "—"
        if isinstance(conf, float):
            conf = f"{conf:.2f}"
        deadline = t.get("deadline", "—")
        status = t.get("status", "active")
        title = t.get("title", "")[:38]
        print(f"{tid:<6} {title:<40} {str(conf):<10} {deadline:<12} {status}")

    closed = _load_all_theses(THESES_CLOSED)
    if closed:
        print(f"\n（另有 {len(closed)} 個已關閉的 theses，用 --show-closed 查看）")


def cmd_list_closed(args):
    """列出所有已關閉的 theses。"""
    theses = _load_all_theses(THESES_CLOSED)
    if not theses:
        print("（沒有已關閉的 theses）")
        return
    print(f"{'ID':<6} {'Title':<40} {'Closed Date':<14} {'Reason'}")
    print("-" * 80)
    for tid, t in sorted(theses.items()):
        closed_date = t.get("closed_date", "—")
        reason = (t.get("close_reason", "") or "")[:40]
        title = t.get("title", "")[:38]
        print(f"{tid:<6} {title:<40} {closed_date:<14} {reason}")


# ── 指令：show ──────────────────────────────────────────────────────────────

def cmd_show(args):
    """顯示指定 thesis 的完整內容。"""
    tid = args.thesis_id.upper()
    f = _find_thesis_file(tid, THESES_ACTIVE) or _find_thesis_file(tid, THESES_CLOSED)
    if f is None:
        print(f"[ERROR] 找不到 thesis: {tid}", file=sys.stderr)
        sys.exit(1)
    t = json.loads(f.read_text(encoding="utf-8"))
    print(json.dumps(t, indent=2, ensure_ascii=False))


# ── 指令：create ──────────────────────────────────────────────────────────────

def cmd_create(args):
    """建立新的 thesis。"""
    THESES_ACTIVE.mkdir(parents=True, exist_ok=True)

    tid = _next_thesis_id()
    today_str = datetime.now().strftime("%Y-%m-%d")

    thesis = {
        "id": tid,
        "status": "active",
        "title": args.title,
        "rationale": args.rationale or "",
        "created_date": today_str,
        "deadline": args.deadline or "",
        "initial_confidence": args.confidence,
        "current_confidence": args.confidence,
        "confidence_history": [
            {"date": today_str, "value": args.confidence, "note": "initial"}
        ],
        "invalidators": [],
        "updates": [],
        "tags": (args.tags or "").split(",") if args.tags else [],
    }

    slug = _safe_filename(args.title)
    filename = f"{tid}_{slug}.json"
    path = THESES_ACTIVE / filename
    path.write_text(json.dumps(thesis, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"✓ Thesis 已建立: {tid} → {path}")
    _sync_l3()


# ── 指令：close ───────────────────────────────────────────────────────────────

def cmd_close(args):
    """將 thesis 標記為 closed，移至 closed 目錄。"""
    tid = args.thesis_id.upper()
    f = _find_thesis_file(tid, THESES_ACTIVE)
    if f is None:
        print(f"[ERROR] Active thesis 中找不到: {tid}", file=sys.stderr)
        sys.exit(1)

    THESES_CLOSED.mkdir(parents=True, exist_ok=True)

    t = json.loads(f.read_text(encoding="utf-8"))
    today_str = datetime.now().strftime("%Y-%m-%d")
    t["status"] = "closed"
    t["closed_date"] = today_str
    t["close_reason"] = args.reason or ""

    # 寫到 closed 目錄，刪除 active 中的檔案
    closed_path = THESES_CLOSED / f.name
    closed_path.write_text(json.dumps(t, indent=2, ensure_ascii=False), encoding="utf-8")
    f.unlink()

    print(f"✓ Thesis {tid} 已關閉 → {closed_path}")
    _sync_l3()


# ── 指令：update-confidence ───────────────────────────────────────────────────

def cmd_update_confidence(args):
    """更新 thesis 的當前信度。"""
    tid = args.thesis_id.upper()
    f = _find_thesis_file(tid, THESES_ACTIVE)
    if f is None:
        print(f"[ERROR] Active thesis 中找不到: {tid}", file=sys.stderr)
        sys.exit(1)

    new_conf = float(args.confidence)
    if not (0.0 <= new_conf <= 1.0):
        print("[ERROR] confidence 必須在 0.0 ~ 1.0 之間", file=sys.stderr)
        sys.exit(1)

    t = json.loads(f.read_text(encoding="utf-8"))
    old_conf = t.get("current_confidence", "—")
    today_str = datetime.now().strftime("%Y-%m-%d")

    t["current_confidence"] = new_conf
    if "confidence_history" not in t:
        t["confidence_history"] = []
    t["confidence_history"].append({
        "date": today_str,
        "value": new_conf,
        "note": args.note or "",
    })

    f.write_text(json.dumps(t, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✓ {tid} confidence: {old_conf} → {new_conf}")
    _sync_l3()


# ── 指令：add-invalidator ─────────────────────────────────────────────────────

def cmd_add_invalidator(args):
    """為 thesis 新增一個 invalidator 條件。"""
    tid = args.thesis_id.upper()
    f = _find_thesis_file(tid, THESES_ACTIVE)
    if f is None:
        print(f"[ERROR] Active thesis 中找不到: {tid}", file=sys.stderr)
        sys.exit(1)

    t = json.loads(f.read_text(encoding="utf-8"))
    if "invalidators" not in t:
        t["invalidators"] = []

    invalidator = {
        "condition": args.condition,
        "data_key": args.data_key or "",
        "operator": args.operator or "",
        "threshold": float(args.threshold) if args.threshold else None,
        "triggered": False,
        "triggered_date": None,
    }
    t["invalidators"].append(invalidator)
    f.write_text(json.dumps(t, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✓ Invalidator 已新增至 {tid}: {args.condition}")


# ── 主程式 ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="DIB v10 Thesis Lifecycle CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser("list", help="列出 active theses")
    p_list.add_argument("--show-closed", action="store_true", help="顯示已關閉的 theses")

    # show
    p_show = sub.add_parser("show", help="顯示 thesis 詳情")
    p_show.add_argument("thesis_id", help="Thesis ID（如 T001）")

    # create
    p_create = sub.add_parser("create", help="建立新 thesis")
    p_create.add_argument("--title", required=True, help="Thesis 標題")
    p_create.add_argument("--rationale", default="", help="理論基礎")
    p_create.add_argument("--deadline", default="", help="到期日（YYYY-MM-DD）")
    p_create.add_argument("--confidence", type=float, default=0.6, help="初始信度（0-1，預設 0.6）")
    p_create.add_argument("--tags", default="", help="標籤（逗號分隔）")

    # close
    p_close = sub.add_parser("close", help="關閉 thesis")
    p_close.add_argument("thesis_id", help="要關閉的 Thesis ID")
    p_close.add_argument("--reason", default="", help="關閉原因")

    # update-confidence
    p_conf = sub.add_parser("update-confidence", help="更新 thesis 信度")
    p_conf.add_argument("thesis_id", help="Thesis ID")
    p_conf.add_argument("confidence", help="新的信度（0.0 - 1.0）")
    p_conf.add_argument("--note", default="", help="備注說明")

    # add-invalidator
    p_inv = sub.add_parser("add-invalidator", help="新增 invalidator 條件")
    p_inv.add_argument("thesis_id", help="Thesis ID")
    p_inv.add_argument("--condition", required=True, help="條件描述（完整句子）")
    p_inv.add_argument("--data-key", default="", help="資料鍵（如 spx, gold）")
    p_inv.add_argument("--operator", default="", help="比較運算子（>, <, ==）")
    p_inv.add_argument("--threshold", default=None, help="觸發閾值（數字）")

    args = parser.parse_args()

    # 確保 project root 在 sys.path（讓 _sync_l3 可以 import src）
    sys.path.insert(0, str(PROJECT_ROOT))

    if args.command == "list":
        if getattr(args, "show_closed", False):
            cmd_list_closed(args)
        else:
            cmd_list(args)
    elif args.command == "show":
        cmd_show(args)
    elif args.command == "create":
        cmd_create(args)
    elif args.command == "close":
        cmd_close(args)
    elif args.command == "update-confidence":
        cmd_update_confidence(args)
    elif args.command == "add-invalidator":
        cmd_add_invalidator(args)


if __name__ == "__main__":
    main()
