#!/usr/bin/env python3
"""CLI tool for updating TGRI manual inputs.

Usage:
    python tools/update_manual.py --adiz 3 --pla 1 --trade-risk 5
    python tools/update_manual.py --show           # show current values
    python tools/update_manual.py --adiz 4         # update single field
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

MANUAL_PATH = Path(__file__).resolve().parent.parent / "memory" / "manual_inputs.json"

FIELDS = {
    "adiz_intrusions": {"range": (0, 10), "help": "ADIZ intrusions (0-10, from Japan MOD press releases)"},
    "pla_activity_level": {"range": (0, 3), "help": "PLA activity level (0=baseline, 1=elevated, 2=high, 3=crisis)"},
    "us_tw_contact_frequency": {"range": (0, 10), "help": "US-Taiwan official contacts (0-10, weekly)"},
    "trade_policy_risk": {"range": (0, 10), "help": "Trade policy risk (0=stable, 5=moderate tension, 10=trade war)"},
    "caixin_pmi": {"range": (35, 60), "help": "Caixin Manufacturing PMI (monthly, manual entry)"},
}


def load() -> dict:
    if MANUAL_PATH.exists():
        try:
            return json.loads(MANUAL_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save(data: dict):
    data["_last_updated"] = datetime.now(timezone.utc).isoformat()
    MANUAL_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def show(data: dict):
    last = data.get("_last_updated", "never")
    print(f"Last updated: {last}")
    print()
    for key, meta in FIELDS.items():
        val = data.get(key, "—")
        lo, hi = meta["range"]
        print(f"  {key:30s} = {val:>8}  (range {lo}–{hi})")
    print()
    # Staleness check
    if last != "never":
        try:
            ts = datetime.fromisoformat(last)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - ts).days
            if age_days > 7:
                print(f"  ⚠ Manual inputs are {age_days} days old — consider updating!")
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Update TGRI manual inputs")
    parser.add_argument("--show", action="store_true", help="Show current values")
    for key, meta in FIELDS.items():
        flag = "--" + key.replace("_", "-")
        lo, hi = meta["range"]
        parser.add_argument(flag, type=float, default=None, help=f"{meta['help']}")

    args = parser.parse_args()
    data = load()

    if args.show:
        show(data)
        return

    updated = False
    for key, meta in FIELDS.items():
        val = getattr(args, key, None)
        if val is not None:
            lo, hi = meta["range"]
            if val < lo or val > hi:
                print(f"Error: {key}={val} out of range [{lo}, {hi}]", file=sys.stderr)
                sys.exit(1)
            data[key] = val
            updated = True
            print(f"  {key} → {val}")

    if updated:
        save(data)
        print(f"\nSaved to {MANUAL_PATH}")
    else:
        print("No fields specified. Use --show to see current values, or --help for options.")


if __name__ == "__main__":
    main()
