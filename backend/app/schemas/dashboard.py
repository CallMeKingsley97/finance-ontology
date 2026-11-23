"""仪表盘响应模型。"""

from __future__ import annotations

from pydantic import BaseModel


class KpiItem(BaseModel):
    key: str
    label: str
    value: float | None = None
    unit: str = "CNY"
    formatted: str = "—"
    change_pct: float | None = None
    trend: str | None = None


class TrendPoint(BaseModel):
    period: str
    revenue: float
    net_profit: float


class ReconciliationCheck(BaseModel):
    name: str
    left: float
    right: float
    diff: float
    passed: bool


class Reconciliation(BaseModel):
    passed: bool
    checks: list[ReconciliationCheck]


class Counts(BaseModel):
    tables: int
    terms: int
    relations: int
    sql_attributes: int
    queries: int


class DashboardData(BaseModel):
    latest_period: str | None
    kpis: list[KpiItem]
    trend: list[TrendPoint]
    reconciliation: Reconciliation
    counts: Counts
