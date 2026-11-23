#!/usr/bin/env python
"""确定性 mock 财务数据播种脚本。

用法：
    cd backend && uv run python scripts/seed.py
    uv run --directory backend python scripts/seed.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 保证以 `python scripts/seed.py` 方式运行时也能 import app 包
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.db.connection import seed_database  # noqa: E402


def main() -> int:
    print(f"[seed] database: {settings.db_path}")
    result = seed_database(reset=True)
    rows = result.get("rows", {})
    print(f"[seed] tables: {len(result.get('tables', []))}")
    for table in (
        "dim_company",
        "dim_account",
        "dim_customer",
        "dim_vendor",
        "dim_period",
        "fact_journal",
        "fact_invoice",
        "fact_payment",
        "fact_balance",
        "fact_budget",
    ):
        print(f"[seed] {table}: {rows.get(table, 0)}")
    reconciliation = result.get("reconciliation") or {}
    print(f"[seed] reconciliation passed: {reconciliation.get('passed')}")
    for check in reconciliation.get("checks", []):
        print(f"[seed]   - {check['name']}: {check['passed']}")
    print(f"[seed] duration: {result.get('duration_ms')} ms")
    if not reconciliation.get("passed"):
        print("[seed] ERROR: reconciliation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
