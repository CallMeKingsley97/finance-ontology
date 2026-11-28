"""SQLite 连接、只读守卫、Schema 与确定性 mock 数据播种。"""

from __future__ import annotations

import calendar
import random
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from app.core.config import settings
from app.schemas.common import AppError

BUSINESS_TABLES = [
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
]

ONTOLOGY_TABLES = [
    "ontology_term",
    "ontology_term_column",
    "ontology_relation",
    "ontology_sql_attribute",
    "ontology_change_log",
]

AUDIT_TABLE = "query_audit"
ALL_TABLES = BUSINESS_TABLES + ONTOLOGY_TABLES + [AUDIT_TABLE]
READABLE_TABLES = frozenset(ALL_TABLES)

_DROP_ORDER = [
    AUDIT_TABLE,
    "ontology_change_log",
    "ontology_sql_attribute",
    "ontology_relation",
    "ontology_term_column",
    "ontology_term",
    "fact_budget",
    "fact_balance",
    "fact_payment",
    "fact_invoice",
    "fact_journal",
    "dim_period",
    "dim_vendor",
    "dim_customer",
    "dim_account",
    "dim_company",
]

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS dim_company (
    company_id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_code TEXT NOT NULL UNIQUE,
    company_name TEXT NOT NULL,
    industry TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CNY',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dim_account (
    account_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_code TEXT NOT NULL UNIQUE,
    account_name TEXT NOT NULL,
    account_type TEXT NOT NULL,
    parent_code TEXT,
    balance_direction TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dim_customer (
    customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_code TEXT NOT NULL UNIQUE,
    customer_name TEXT NOT NULL,
    region TEXT NOT NULL,
    industry TEXT NOT NULL,
    phone TEXT,
    bank_account TEXT,
    credit_terms INTEGER NOT NULL DEFAULT 30,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dim_vendor (
    vendor_id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_code TEXT NOT NULL UNIQUE,
    vendor_name TEXT NOT NULL,
    category TEXT NOT NULL,
    phone TEXT,
    bank_account TEXT,
    payment_terms INTEGER NOT NULL DEFAULT 30,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dim_period (
    period_id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_code TEXT NOT NULL UNIQUE,
    fiscal_year INTEGER NOT NULL,
    period_no INTEGER NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'closed',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fact_journal (
    journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    voucher_no TEXT NOT NULL,
    voucher_date TEXT NOT NULL,
    period_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    debit_amount NUMERIC NOT NULL DEFAULT 0,
    credit_amount NUMERIC NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (period_id) REFERENCES dim_period(period_id),
    FOREIGN KEY (company_id) REFERENCES dim_company(company_id),
    FOREIGN KEY (account_id) REFERENCES dim_account(account_id),
    CHECK ((debit_amount > 0 AND credit_amount = 0) OR (credit_amount > 0 AND debit_amount = 0))
);

CREATE TABLE IF NOT EXISTS fact_invoice (
    invoice_id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no TEXT NOT NULL UNIQUE,
    invoice_type TEXT NOT NULL,
    customer_id INTEGER,
    vendor_id INTEGER,
    period_id INTEGER NOT NULL,
    issue_date TEXT NOT NULL,
    amount NUMERIC NOT NULL DEFAULT 0,
    tax_amount NUMERIC NOT NULL DEFAULT 0,
    total_amount NUMERIC NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (customer_id) REFERENCES dim_customer(customer_id),
    FOREIGN KEY (vendor_id) REFERENCES dim_vendor(vendor_id),
    FOREIGN KEY (period_id) REFERENCES dim_period(period_id),
    CHECK (
        (invoice_type = 'sales' AND customer_id IS NOT NULL AND vendor_id IS NULL) OR
        (invoice_type = 'purchase' AND vendor_id IS NOT NULL AND customer_id IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS fact_payment (
    payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_no TEXT NOT NULL UNIQUE,
    payment_type TEXT NOT NULL,
    customer_id INTEGER,
    vendor_id INTEGER,
    period_id INTEGER NOT NULL,
    payment_date TEXT NOT NULL,
    amount NUMERIC NOT NULL DEFAULT 0,
    method TEXT NOT NULL DEFAULT 'bank_transfer',
    status TEXT NOT NULL DEFAULT 'completed',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (customer_id) REFERENCES dim_customer(customer_id),
    FOREIGN KEY (vendor_id) REFERENCES dim_vendor(vendor_id),
    FOREIGN KEY (period_id) REFERENCES dim_period(period_id),
    CHECK (
        (payment_type = 'receipt' AND customer_id IS NOT NULL AND vendor_id IS NULL) OR
        (payment_type = 'payment' AND vendor_id IS NOT NULL AND customer_id IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS fact_balance (
    balance_id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    opening_balance NUMERIC NOT NULL DEFAULT 0,
    debit_amount NUMERIC NOT NULL DEFAULT 0,
    credit_amount NUMERIC NOT NULL DEFAULT 0,
    closing_balance NUMERIC NOT NULL DEFAULT 0,
    balance_direction TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (period_id) REFERENCES dim_period(period_id),
    FOREIGN KEY (account_id) REFERENCES dim_account(account_id),
    FOREIGN KEY (company_id) REFERENCES dim_company(company_id),
    UNIQUE (period_id, account_id, company_id)
);

CREATE TABLE IF NOT EXISTS fact_budget (
    budget_id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    department TEXT NOT NULL DEFAULT '公司总部',
    scenario TEXT NOT NULL DEFAULT 'annual',
    amount NUMERIC NOT NULL DEFAULT 0,
    version TEXT NOT NULL DEFAULT 'V1',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (period_id) REFERENCES dim_period(period_id),
    FOREIGN KEY (account_id) REFERENCES dim_account(account_id),
    UNIQUE (period_id, account_id, department, scenario, version)
);

CREATE TABLE IF NOT EXISTS ontology_term (
    term_id INTEGER PRIMARY KEY AUTOINCREMENT,
    term_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    synonyms TEXT NOT NULL DEFAULT '[]',
    mapped_table TEXT,
    mapped_columns TEXT NOT NULL DEFAULT '[]',
    category TEXT NOT NULL,
    certified INTEGER NOT NULL DEFAULT 0,
    certified_by TEXT,
    certified_at TEXT,
    source TEXT NOT NULL DEFAULT 'rule',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ontology_term_column (
    column_id INTEGER PRIMARY KEY AUTOINCREMENT,
    term_id INTEGER NOT NULL,
    table_name TEXT NOT NULL,
    column_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_sensitive INTEGER NOT NULL DEFAULT 0,
    classification TEXT,
    sample_masked TEXT,
    FOREIGN KEY (term_id) REFERENCES ontology_term(term_id) ON DELETE CASCADE,
    UNIQUE (term_id, table_name, column_name)
);

CREATE TABLE IF NOT EXISTS ontology_relation (
    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    relation_code TEXT NOT NULL UNIQUE,
    from_term TEXT NOT NULL,
    to_term TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    cardinality TEXT NOT NULL,
    join_hint TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ontology_sql_attribute (
    attribute_id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    term_id INTEGER,
    formula_sql TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT 'CNY',
    certified INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (term_id) REFERENCES ontology_term(term_id)
);

CREATE TABLE IF NOT EXISTS ontology_change_log (
    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    term_id INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_by_role TEXT NOT NULL,
    changed_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (term_id) REFERENCES ontology_term(term_id)
);

CREATE TABLE IF NOT EXISTS query_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    intent TEXT,
    sql_text TEXT,
    row_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    role TEXT NOT NULL,
    masked_columns TEXT NOT NULL DEFAULT '[]',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    metric_code TEXT,
    metric_version TEXT,
    alias_matched TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_journal_period_account ON fact_journal(period_id, account_id);
CREATE INDEX IF NOT EXISTS idx_journal_voucher ON fact_journal(voucher_no);
CREATE INDEX IF NOT EXISTS idx_invoice_period_type ON fact_invoice(period_id, invoice_type);
CREATE INDEX IF NOT EXISTS idx_invoice_customer ON fact_invoice(customer_id);
CREATE INDEX IF NOT EXISTS idx_invoice_vendor ON fact_invoice(vendor_id);
CREATE INDEX IF NOT EXISTS idx_payment_period_type ON fact_payment(period_id, payment_type);
CREATE INDEX IF NOT EXISTS idx_balance_period_account ON fact_balance(period_id, account_id);
CREATE INDEX IF NOT EXISTS idx_budget_period_account ON fact_budget(period_id, account_id);
CREATE INDEX IF NOT EXISTS idx_term_mapped_table ON ontology_term(mapped_table);
CREATE INDEX IF NOT EXISTS idx_relation_from ON ontology_relation(from_term);
CREATE INDEX IF NOT EXISTS idx_change_log_term ON ontology_change_log(term_id, changed_at);
CREATE INDEX IF NOT EXISTS idx_audit_created ON query_audit(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_role ON query_audit(role);
"""

_ACCOUNT_DEFS = [
    ("1001", "库存现金", "asset", "debit", 1, 10),
    ("1002", "银行存款", "asset", "debit", 1, 20),
    ("1122", "应收账款", "asset", "debit", 1, 30),
    ("1123", "预付账款", "asset", "debit", 1, 40),
    ("1221", "其他应收款", "asset", "debit", 1, 50),
    ("1405", "存货", "asset", "debit", 1, 60),
    ("1601", "固定资产", "asset", "debit", 0, 70),
    ("1701", "无形资产", "asset", "debit", 0, 80),
    ("2001", "短期借款", "liability", "credit", 1, 90),
    ("2202", "应付账款", "liability", "credit", 1, 100),
    ("2203", "预收账款", "liability", "credit", 1, 110),
    ("2211", "应付职工薪酬", "liability", "credit", 1, 120),
    ("2221", "应交税费", "liability", "credit", 1, 130),
    ("2501", "长期借款", "liability", "credit", 0, 140),
    ("4001", "实收资本", "equity", "credit", 0, 150),
    ("4002", "资本公积", "equity", "credit", 0, 160),
    ("4101", "盈余公积", "equity", "credit", 0, 170),
    ("4103", "未分配利润", "equity", "credit", 0, 180),
    ("6001", "主营业务收入", "revenue", "credit", 1, 190),
    ("6051", "其他业务收入", "revenue", "credit", 1, 200),
    ("6401", "主营业务成本", "cogs", "debit", 1, 210),
    ("6402", "其他业务成本", "cogs", "debit", 1, 220),
    ("6403", "税金及附加", "tax_surcharge", "debit", 1, 230),
    ("6601", "销售费用", "expense", "debit", 1, 240),
    ("6602", "管理费用", "expense", "debit", 1, 250),
    ("6603", "研发费用", "expense", "debit", 1, 260),
    ("6604", "财务费用", "expense", "debit", 1, 270),
    ("6301", "营业外收入", "non_operating", "credit", 1, 280),
    ("6711", "营业外支出", "non_operating", "debit", 1, 290),
    ("6801", "所得税费用", "income_tax", "debit", 1, 300),
]

# 按《企业会计准则——应用指南》会计科目表，以下 5 组为同级科目，不存在父子关系：
# 1002 银行存款 / 1001 库存现金；1123 预付账款 / 1122 应收账款；
# 2203 预收账款 / 2202 应付账款；6402 其他业务成本 / 6401 主营业务成本；
# 6051 其他业务收入 / 6001 主营业务收入。
# 原映射会让 dim_account.parent_code 与本体 rel_account_parent 语义失真，故置空。
_PARENT_MAP: dict[str, str] = {}

_PNL_ACCOUNT_CODES = [
    "6001",
    "6051",
    "6401",
    "6402",
    "6403",
    "6601",
    "6602",
    "6603",
    "6604",
    "6301",
    "6711",
    "6801",
]

_BUDGET_ACCOUNT_CODES = [
    "6001",
    "6051",
    "6401",
    "6402",
    "6403",
    "6601",
    "6602",
    "6603",
    "6604",
    "6801",
]

_COMPANIES = [
    ("C001", "华东制造有限公司", "制造业", 0.6),
    ("C002", "华南贸易有限公司", "批发零售业", 0.4),
]

_SEASONALITY = [0.85, 0.80, 0.95, 1.00, 1.00, 1.05, 1.00, 1.00, 1.10, 1.15, 1.20, 1.15]


def quote_identifier(name: str) -> str:
    """仅允许字母/数字/下划线的标识符，双引号包裹。"""
    if not name or not name.replace("_", "").isalnum():
        raise ValueError(f"非法标识符: {name}")
    return '"' + name.replace('"', '""') + '"'


def _make_authorizer(allowed_tables: frozenset[str]):
    """构造只读守卫：只允许 SELECT/READ/FUNCTION/RECURSIVE，READ 仅放行给定表。"""
    allowed = frozenset(name.lower() for name in allowed_tables)
    recursive = getattr(sqlite3, "SQLITE_RECURSIVE", 33)

    def _authorizer(action: int, arg1: str | None, arg2: str | None, dbname: str | None, trigger: str | None) -> int:
        if action == sqlite3.SQLITE_READ:
            if arg1 is None or arg1.lower() in allowed:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, recursive):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    return _authorizer


def connect(
    read_only: bool = True,
    enforce_authorizer: bool = True,
    allowed_tables: frozenset[str] | None = None,
) -> sqlite3.Connection:
    """创建连接；读连接强制只读模式，写连接启用 WAL。

    allowed_tables 控制只读作者器可读取的表；用户问数 SQL 应传入 10 张业务表，
    内部元数据/本体读取默认使用全部 16 张表。
    """
    path = settings.db_path
    if read_only:
        if not path.exists():
            raise AppError(503, "DB_UNAVAILABLE", "数据库不存在，请先在数据源页播种")
        conn = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=5.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        if enforce_authorizer:
            conn.set_authorizer(_make_authorizer(allowed_tables or READABLE_TABLES))
        return conn

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def write_connection() -> Iterator[sqlite3.Connection]:
    """写连接上下文，自动提交/回滚。"""
    conn = connect(read_only=False)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _fetch_all(
    sql: str,
    params: dict[str, Any] | tuple[Any, ...] | None = None,
    *,
    enforce_authorizer: bool = True,
    allowed_tables: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    conn = connect(
        read_only=True,
        enforce_authorizer=enforce_authorizer,
        allowed_tables=allowed_tables,
    )
    try:
        cursor = conn.execute(sql, params or {})
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def query_all(
    sql: str,
    params: dict[str, Any] | tuple[Any, ...] | None = None,
    *,
    enforce_authorizer: bool = True,
    allowed_tables: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """执行只读查询并返回 dict 列表。"""
    return _fetch_all(
        sql,
        params,
        enforce_authorizer=enforce_authorizer,
        allowed_tables=allowed_tables,
    )


def query_one(
    sql: str,
    params: dict[str, Any] | tuple[Any, ...] | None = None,
    *,
    enforce_authorizer: bool = True,
    allowed_tables: frozenset[str] | None = None,
) -> dict[str, Any] | None:
    rows = _fetch_all(
        sql,
        params,
        enforce_authorizer=enforce_authorizer,
        allowed_tables=allowed_tables,
    )
    return rows[0] if rows else None


def database_info() -> dict[str, Any]:
    """数据库路径、存在性、表数与最新期间。"""
    path = settings.db_path
    info: dict[str, Any] = {
        "db_path": str(path),
        "db_exists": path.exists(),
        "table_count": 0,
        "latest_period": None,
    }
    if not path.exists():
        return info
    try:
        rows = query_all(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'",
            enforce_authorizer=False,
        )
        info["table_count"] = len(rows)
        if info["table_count"]:
            row = query_one(
                "SELECT period_code FROM dim_period ORDER BY period_code DESC LIMIT 1",
                enforce_authorizer=False,
            )
            info["latest_period"] = row["period_code"] if row else None
    except sqlite3.Error:
        info["table_count"] = 0
    return info


def _drop_all(conn: sqlite3.Connection) -> None:
    for table in _DROP_ORDER:
        conn.execute(f"DROP TABLE IF EXISTS {quote_identifier(table)}")
    conn.commit()


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    ensure_audit_columns(conn)


def ensure_audit_columns(conn: sqlite3.Connection | None = None) -> None:
    """补齐 query_audit 的指标审计列；旧库迁移，幂等且失败不阻断主流程。

    新库由 ``_SCHEMA_SQL`` 直接包含三列；旧库通过 ``ALTER TABLE ... ADD COLUMN``
    迁移。业务库文件不存在时直接跳过，避免空库探测改变健康检查语义。
    """
    owned = conn is None
    if owned:
        if not settings.db_path.exists():
            return
        try:
            conn = connect(read_only=False)
        except sqlite3.Error:
            return
    assert conn is not None
    try:
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(query_audit)").fetchall()
        }
        if not existing:
            return
        for column in ("metric_code", "metric_version", "alias_matched"):
            if column not in existing:
                conn.execute(f"ALTER TABLE query_audit ADD COLUMN {column} TEXT")
        conn.commit()
    except sqlite3.Error:
        return
    finally:
        if owned:
            conn.close()


def _make_customers(rng: random.Random) -> list[tuple[Any, ...]]:
    regions = ["华北", "华东", "华南", "华中", "西南", "西北", "东北"]
    industries = ["制造业", "批发零售", "信息技术", "物流运输", "建筑工程", "医药健康", "能源化工"]
    prefixes = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京", "苏州", "天津",
                "重庆", "西安", "青岛", "宁波", "长沙", "郑州", "合肥", "福州", "厦门", "济南"]
    cores = ["华创", "中瑞", "远大", "恒信", "嘉禾", "联创", "天成", "宏图", "新元", "博远",
             "泰和", "盛通", "瑞丰", "启明", "金桥", "德胜", "海纳", "兴源", "卓越", "光谷",
             "四方", "万邦", "凯旋", "云帆", "晨曦", "朗润", "安泰", "汇通", "正泰", "长青"]
    suffixes = ["科技有限公司", "贸易有限公司", "制造有限公司", "实业有限公司", "供应链有限公司"]
    used: set[str] = set()
    rows: list[tuple[Any, ...]] = []
    index = 0
    while len(rows) < 30:
        name = f"{rng.choice(prefixes)}{cores[index % len(cores)]}{rng.choice(suffixes)}"
        index += 1
        if name in used:
            continue
        used.add(name)
        phone = "1" + rng.choice("35789") + f"{rng.randint(0, 999999999):09d}"
        bank = "6222" + f"{rng.randint(0, 10**15 - 1):015d}"
        rows.append((
            f"CUST{len(rows) + 1:03d}",
            name,
            rng.choice(regions),
            rng.choice(industries),
            phone,
            bank,
            rng.choice([30, 45, 60, 90]),
        ))
    return rows


def _make_vendors(rng: random.Random) -> list[tuple[Any, ...]]:
    regions = ["华东", "华北", "华南", "华中", "西南"]
    categories = ["原材料", "物流运输", "生产设备", "专业服务", "包装耗材"]
    cores = ["联塑", "中钢", "顺达", "恒通", "瑞泰", "广源", "新材", "天成", "宏达", "德昌",
             "金田", "海润", "兴业", "长虹", "东方", "南方", "北方", "中远", "华贸", "嘉里"]
    rows: list[tuple[Any, ...]] = []
    for index in range(20):
        phone = "1" + rng.choice("35789") + f"{rng.randint(0, 999999999):09d}"
        bank = "6222" + f"{rng.randint(0, 10**15 - 1):015d}"
        rows.append((
            f"VEND{index + 1:03d}",
            f"{rng.choice(regions)}{cores[index]}{rng.choice(['供应链有限公司', '贸易有限公司', '实业有限公司'])}",
            rng.choice(categories),
            phone,
            bank,
            rng.choice([30, 45, 60]),
        ))
    return rows


def _period_rows() -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    year, month = 2025, 1
    for _ in range(22):
        code = f"{year:04d}-{month:02d}"
        last_day = calendar.monthrange(year, month)[1]
        rows.append((
            code,
            year,
            month,
            f"{code}-01",
            f"{code}-{last_day:02d}",
            "closed",
        ))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return rows


def _round2(value: float) -> float:
    return round(value, 2)


def _split_amount(total: float, count: int, rng: random.Random) -> list[float]:
    """把 total 拆成 count 个正数，末项兜底。"""
    total = _round2(max(total, count * 10.0))
    parts: list[float] = []
    remaining = total
    for _ in range(count - 1):
        part = _round2(remaining * rng.uniform(0.18, 0.42))
        part = max(part, 1.0)
        parts.append(part)
        remaining = _round2(remaining - part)
    parts.append(_round2(remaining))
    if parts[-1] <= 0:
        parts[-1] = 1.0
    return parts


def _generate_financials(
    conn: sqlite3.Connection,
    rng: random.Random,
    company_ids: dict[str, int],
    account_ids: dict[str, int],
    period_ids: dict[str, int],
    periods: list[tuple[Any, ...]],
    customers: list[tuple[Any, ...]],
    vendors: list[tuple[Any, ...]],
) -> dict[str, int]:
    counts = {
        "fact_balance": 0,
        "fact_journal": 0,
        "fact_invoice": 0,
        "fact_payment": 0,
        "fact_budget": 0,
    }
    balance_rows: list[tuple[Any, ...]] = []
    journal_rows: list[tuple[Any, ...]] = []
    invoice_rows: list[tuple[Any, ...]] = []
    payment_rows: list[tuple[Any, ...]] = []
    budget_rows: list[tuple[Any, ...]] = []

    prev_closing: dict[str, dict[str, float]] = {
        code: {account_code: 0.0 for account_code, *_ in _ACCOUNT_DEFS}
        for code in company_ids
    }
    retained = {code: 0.0 for code in company_ids}
    surplus = {code: 0.0 for code in company_ids}
    actuals: dict[str, dict[str, float]] = {}

    account_meta = {code: (name, atype, direction) for code, name, atype, direction, *_ in _ACCOUNT_DEFS}
    pnl_types = {"revenue", "cogs", "tax_surcharge", "expense", "non_operating", "income_tax"}

    for month_index, (period_code, year, month, start_date, _end_date, _status) in enumerate(periods):
        period_id = period_ids[period_code]
        base_revenue = (
            10_000_000.0
            * (1.012 ** month_index)
            * _SEASONALITY[month - 1]
            * rng.uniform(0.97, 1.03)
        )
        period_totals = {
            "revenue": 0.0,
            "cogs": 0.0,
            "tax_surcharge": 0.0,
            "period_expense": 0.0,
            "nonop_income": 0.0,
            "nonop_expense": 0.0,
            "income_tax": 0.0,
        }
        period_actuals: dict[str, float] = {code: 0.0 for code in _PNL_ACCOUNT_CODES}

        for company_code, _company_name, _industry, share in _COMPANIES:
            company_id = company_ids[company_code]
            revenue = _round2(base_revenue * share)
            main_revenue = _round2(revenue * 0.95)
            other_revenue = _round2(revenue - main_revenue)
            cogs_rate = rng.uniform(0.58, 0.64)
            main_cost = _round2(main_revenue * cogs_rate)
            other_cost = _round2(other_revenue * cogs_rate * 0.8)
            cogs = _round2(main_cost + other_cost)
            tax_surcharge = _round2(revenue * rng.uniform(0.008, 0.012))
            selling = _round2(revenue * rng.uniform(0.06, 0.08))
            admin = _round2(revenue * rng.uniform(0.05, 0.07))
            rd = _round2(revenue * rng.uniform(0.04, 0.06))
            finance = _round2(revenue * rng.uniform(0.01, 0.015))
            period_expense = _round2(selling + admin + rd + finance)
            nonop_income = _round2(revenue * rng.uniform(0.0005, 0.002))
            nonop_expense = _round2(revenue * rng.uniform(0.0003, 0.0015))
            total_profit = _round2(revenue - cogs - tax_surcharge - period_expense + nonop_income - nonop_expense)
            income_tax = _round2(max(total_profit, 0.0) * 0.15)
            net_profit = _round2(total_profit - income_tax)

            retained[company_code] = _round2(retained[company_code] + net_profit * 0.9)
            surplus[company_code] = _round2(surplus[company_code] + net_profit * 0.1)

            paid_in = _round2(12_000_000.0 * share)
            capital_reserve = _round2(2_000_000.0 * share)
            equity_total = _round2(paid_in + capital_reserve + retained[company_code] + surplus[company_code])

            short_loan = _round2(2_000_000.0 * share * (1 + month_index * 0.005))
            advance = _round2(revenue * 0.05)
            ap = _round2(cogs * 0.35)
            payroll = _round2(period_expense * 0.20)
            tax_payable = _round2(tax_surcharge * 0.5 + income_tax * 0.3)
            long_loan = _round2(5_000_000.0 * share)
            liabilities = _round2(short_loan + advance + ap + payroll + tax_payable + long_loan)

            receivable = _round2(revenue * 0.35)
            prepaid = _round2(period_expense * 0.10)
            other_receivable = _round2(revenue * 0.02)
            inventory = _round2(cogs * 0.45)
            fixed_assets = _round2(5_000_000.0 * share + month_index * 30_000.0 * share)
            intangible = _round2(max(1_000_000.0 * share - month_index * 8_000.0 * share, 100_000.0 * share))
            other_assets = _round2(
                receivable + prepaid + other_receivable + inventory + fixed_assets + intangible
            )
            cash_total = _round2(liabilities + equity_total - other_assets)
            cash_1001 = _round2(cash_total * 0.3)
            cash_1002 = _round2(cash_total - cash_1001)

            amounts = {
                "1001": cash_1001,
                "1002": cash_1002,
                "1122": receivable,
                "1123": prepaid,
                "1221": other_receivable,
                "1405": inventory,
                "1601": fixed_assets,
                "1701": intangible,
                "2001": short_loan,
                "2202": ap,
                "2203": advance,
                "2211": payroll,
                "2221": tax_payable,
                "2501": long_loan,
                "4001": paid_in,
                "4002": capital_reserve,
                "4101": surplus[company_code],
                "4103": retained[company_code],
                "6001": main_revenue,
                "6051": other_revenue,
                "6401": main_cost,
                "6402": other_cost,
                "6403": tax_surcharge,
                "6601": selling,
                "6602": admin,
                "6603": rd,
                "6604": finance,
                "6301": nonop_income,
                "6711": nonop_expense,
                "6801": income_tax,
            }

            for account_code, _name, account_type, direction, _is_current, _sort in _ACCOUNT_DEFS:
                amount = _round2(amounts.get(account_code, 0.0))
                if account_type in pnl_types:
                    opening = 0.0
                    closing = amount
                    if direction == "debit":
                        debit, credit = amount, 0.0
                    else:
                        debit, credit = 0.0, amount
                    period_actuals[account_code] = _round2(period_actuals[account_code] + amount)
                else:
                    opening = prev_closing[company_code][account_code]
                    closing = amount
                    delta = _round2(closing - opening)
                    if account_type == "asset":
                        debit, credit = (delta, 0.0) if delta >= 0 else (0.0, -delta)
                    else:
                        debit, credit = (0.0, delta) if delta >= 0 else (-delta, 0.0)
                    prev_closing[company_code][account_code] = closing
                balance_rows.append((
                    period_id,
                    account_ids[account_code],
                    company_id,
                    opening,
                    _round2(debit),
                    _round2(credit),
                    closing,
                    direction,
                ))

            for key, value in (
                ("revenue", revenue),
                ("cogs", cogs),
                ("tax_surcharge", tax_surcharge),
                ("period_expense", period_expense),
                ("nonop_income", nonop_income),
                ("nonop_expense", nonop_expense),
                ("income_tax", income_tax),
            ):
                period_totals[key] = _round2(period_totals[key] + value)

        actuals[period_code] = period_actuals

        period_start = date.fromisoformat(start_date)
        total_revenue = period_totals["revenue"]
        total_cogs = period_totals["cogs"]

        for seq in range(1, 13):
            voucher_date = (period_start + timedelta(days=rng.randint(0, 26))).isoformat()
            total = _round2(total_revenue / 12.0 * rng.uniform(0.6, 1.4))
            debit_parts = _split_amount(total, 3, rng)
            credit_parts = _split_amount(total, 3, rng)
            debit_accounts = rng.sample(
                ["1002", "1122", "1405", "6401", "6402", "6403", "6601", "6602", "6603", "6604", "1601"],
                3,
            )
            credit_accounts = rng.sample(
                ["1001", "1002", "2202", "2203", "2211", "2221", "2001", "4001", "6001", "6051", "6301"],
                3,
            )
            company_code = rng.choice(list(company_ids))
            voucher_no = f"V{period_code.replace('-', '')}{seq:03d}"
            summary = rng.choice(["采购入库", "销售出库", "费用报销", "收付款结算", "计提税金", "结转成本"])
            for account_code, amount in zip(debit_accounts, debit_parts):
                journal_rows.append((
                    voucher_no,
                    voucher_date,
                    period_id,
                    company_ids[company_code],
                    account_ids[account_code],
                    amount,
                    0.0,
                    summary,
                ))
            for account_code, amount in zip(credit_accounts, credit_parts):
                journal_rows.append((
                    voucher_no,
                    voucher_date,
                    period_id,
                    company_ids[company_code],
                    account_ids[account_code],
                    0.0,
                    amount,
                    summary,
                ))

        for index in range(1, 11):
            amount = _round2(total_revenue / 10.0 * rng.uniform(0.8, 1.2))
            tax = _round2(amount * 0.13)
            invoice_rows.append((
                f"INV{period_code.replace('-', '')}S{index:03d}",
                "sales",
                customers[rng.randrange(len(customers))][0],
                None,
                period_id,
                (period_start + timedelta(days=rng.randint(0, 26))).isoformat(),
                amount,
                tax,
                _round2(amount + tax),
                rng.choice(["paid", "open", "paid"]),
            ))
        for index in range(1, 6):
            amount = _round2(total_cogs / 5.0 * rng.uniform(0.8, 1.2))
            tax = _round2(amount * 0.13)
            invoice_rows.append((
                f"INV{period_code.replace('-', '')}P{index:03d}",
                "purchase",
                None,
                vendors[rng.randrange(len(vendors))][0],
                period_id,
                (period_start + timedelta(days=rng.randint(0, 26))).isoformat(),
                amount,
                tax,
                _round2(amount + tax),
                rng.choice(["paid", "open", "paid"]),
            ))

        for index in range(1, 9):
            amount = _round2(total_revenue / 8.0 * rng.uniform(0.5, 1.0))
            payment_rows.append((
                f"PAY{period_code.replace('-', '')}R{index:03d}",
                "receipt",
                customers[rng.randrange(len(customers))][0],
                None,
                period_id,
                (period_start + timedelta(days=rng.randint(0, 26))).isoformat(),
                amount,
                rng.choice(["bank_transfer", "acceptance", "cash"]),
                "completed",
            ))
        for index in range(1, 5):
            amount = _round2(total_cogs / 4.0 * rng.uniform(0.5, 1.0))
            payment_rows.append((
                f"PAY{period_code.replace('-', '')}P{index:03d}",
                "payment",
                None,
                vendors[rng.randrange(len(vendors))][0],
                period_id,
                (period_start + timedelta(days=rng.randint(0, 26))).isoformat(),
                amount,
                rng.choice(["bank_transfer", "acceptance", "cash"]),
                "completed",
            ))

        for account_code in _BUDGET_ACCOUNT_CODES:
            actual = actuals[period_code].get(account_code, 0.0)
            budget = _round2(actual * rng.uniform(0.95, 1.05))
            budget_rows.append((
                period_id,
                account_ids[account_code],
                "公司总部",
                "annual",
                budget,
                "V1",
            ))

    # 补齐维度 ID 引用：customers/vendors 元组首列为 code，转成 ID
    conn.executemany(
        """INSERT INTO fact_balance
           (period_id, account_id, company_id, opening_balance, debit_amount,
            credit_amount, closing_balance, balance_direction)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        balance_rows,
    )
    conn.executemany(
        """INSERT INTO fact_journal
           (voucher_no, voucher_date, period_id, company_id, account_id,
            debit_amount, credit_amount, summary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        journal_rows,
    )
    conn.executemany(
        """INSERT INTO fact_invoice
           (invoice_no, invoice_type, customer_id, vendor_id, period_id, issue_date,
            amount, tax_amount, total_amount, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        invoice_rows,
    )
    conn.executemany(
        """INSERT INTO fact_payment
           (payment_no, payment_type, customer_id, vendor_id, period_id, payment_date,
            amount, method, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        payment_rows,
    )
    conn.executemany(
        """INSERT INTO fact_budget
           (period_id, account_id, department, scenario, amount, version)
           VALUES (?, ?, ?, ?, ?, ?)""",
        budget_rows,
    )

    counts["fact_balance"] = len(balance_rows)
    counts["fact_journal"] = len(journal_rows)
    counts["fact_invoice"] = len(invoice_rows)
    counts["fact_payment"] = len(payment_rows)
    counts["fact_budget"] = len(budget_rows)
    return counts


def _seed_dimensions(
    conn: sqlite3.Connection,
    rng: random.Random,
) -> tuple[dict[str, int], dict[str, int], dict[str, int], list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    conn.executemany(
        "INSERT INTO dim_company (company_code, company_name, industry, currency) VALUES (?, ?, ?, 'CNY')",
        [(code, name, industry) for code, name, industry, _share in _COMPANIES],
    )
    conn.executemany(
        """INSERT INTO dim_account
           (account_code, account_name, account_type, balance_direction, is_current, sort_order)
           VALUES (?, ?, ?, ?, ?, ?)""",
        _ACCOUNT_DEFS,
    )
    if _PARENT_MAP:
        for child, parent in _PARENT_MAP.items():
            conn.execute(
                "UPDATE dim_account SET parent_code = ? WHERE account_code = ?",
                (parent, child),
            )

    customers = _make_customers(rng)
    vendors = _make_vendors(rng)
    conn.executemany(
        """INSERT INTO dim_customer
           (customer_code, customer_name, region, industry, phone, bank_account, credit_terms)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        customers,
    )
    conn.executemany(
        """INSERT INTO dim_vendor
           (vendor_code, vendor_name, category, phone, bank_account, payment_terms)
           VALUES (?, ?, ?, ?, ?, ?)""",
        vendors,
    )
    periods = _period_rows()
    conn.executemany(
        """INSERT INTO dim_period
           (period_code, fiscal_year, period_no, start_date, end_date, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        periods,
    )

    company_ids = {
        row["company_code"]: row["company_id"]
        for row in conn.execute("SELECT company_id, company_code FROM dim_company")
    }
    account_ids = {
        row["account_code"]: row["account_id"]
        for row in conn.execute("SELECT account_id, account_code FROM dim_account")
    }
    period_ids = {
        row["period_code"]: row["period_id"]
        for row in conn.execute("SELECT period_id, period_code FROM dim_period")
    }
    customer_ids = {
        row["customer_code"]: row["customer_id"]
        for row in conn.execute("SELECT customer_id, customer_code FROM dim_customer")
    }
    vendor_ids = {
        row["vendor_code"]: row["vendor_id"]
        for row in conn.execute("SELECT vendor_id, vendor_code FROM dim_vendor")
    }
    # 把 code 元组替换为带自增 ID 的元组，供事实表外键使用
    customer_tuples = [(customer_ids[row[0]],) + row[1:] for row in customers]
    vendor_tuples = [(vendor_ids[row[0]],) + row[1:] for row in vendors]
    return company_ids, account_ids, period_ids, customer_tuples, vendor_tuples, periods


def assert_reconciliation(conn: sqlite3.Connection) -> dict[str, Any]:
    """校验每期资产=负债+权益、利润表勾稽与现金下限。"""
    rows = conn.execute(
        """
        SELECT p.period_code AS period_code, b.company_id AS company_id,
               ROUND(SUM(CASE WHEN a.account_type = 'asset' THEN b.closing_balance ELSE 0 END), 2) AS assets,
               ROUND(SUM(CASE WHEN a.account_type = 'liability' THEN b.closing_balance ELSE 0 END), 2) AS liabilities,
               ROUND(SUM(CASE WHEN a.account_type = 'equity' THEN b.closing_balance ELSE 0 END), 2) AS equity
        FROM fact_balance b
        JOIN dim_account a ON a.account_id = b.account_id
        JOIN dim_period p ON p.period_id = b.period_id
        GROUP BY p.period_code, b.company_id
        ORDER BY p.period_code, b.company_id
        """
    ).fetchall()
    max_diff = 0.0
    for row in rows:
        diff = abs(row["assets"] - (row["liabilities"] + row["equity"]))
        max_diff = max(max_diff, diff)
        if diff > 0.011:
            raise AssertionError(
                f"资产=负债+权益校验失败: {row['period_code']} 公司 {row['company_id']} diff={diff}"
            )

    pnl_rows = conn.execute(
        """
        SELECT p.period_code AS period_code,
               ROUND(SUM(CASE WHEN a.account_type = 'revenue' THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS revenue,
               ROUND(SUM(CASE WHEN a.account_type = 'cogs' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS cogs,
               ROUND(SUM(CASE WHEN a.account_type = 'tax_surcharge' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS tax_surcharge,
               ROUND(SUM(CASE WHEN a.account_type = 'expense' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS period_expense,
               ROUND(SUM(CASE WHEN a.account_code = '6301' THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS nonop_income,
               ROUND(SUM(CASE WHEN a.account_code = '6711' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS nonop_expense,
               ROUND(SUM(CASE WHEN a.account_code = '6801' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS income_tax
        FROM fact_balance b
        JOIN dim_account a ON a.account_id = b.account_id
        JOIN dim_period p ON p.period_id = b.period_id
        GROUP BY p.period_code
        ORDER BY p.period_code
        """
    ).fetchall()
    for row in pnl_rows:
        total_profit = _round2(
            row["revenue"] - row["cogs"] - row["tax_surcharge"] - row["period_expense"]
            + row["nonop_income"] - row["nonop_expense"]
        )
        expected_tax = _round2(max(total_profit, 0.0) * 0.15)
        if abs(row["income_tax"] - expected_tax) > 0.011:
            raise AssertionError(
                f"利润表勾稽失败: {row['period_code']} income_tax={row['income_tax']} expected={expected_tax}"
            )

    cash_row = conn.execute(
        """
        SELECT ROUND(SUM(b.closing_balance), 2) AS cash
        FROM fact_balance b
        JOIN dim_account a ON a.account_id = b.account_id
        JOIN dim_period p ON p.period_id = b.period_id
        WHERE a.account_code IN ('1001', '1002')
          AND p.period_code = (SELECT MAX(period_code) FROM dim_period)
        """
    ).fetchone()
    cash = float(cash_row["cash"] or 0.0)
    if cash < 100_000:
        raise AssertionError(f"现金余额低于 100000: {cash}")

    checks = [
        {
            "name": "资产=负债+所有者权益",
            "left": round(float(rows[-1]["assets"]), 2) if rows else 0.0,
            "right": round(float(rows[-1]["liabilities"] + rows[-1]["equity"]), 2) if rows else 0.0,
            "diff": round(max_diff, 2),
            "passed": max_diff <= 0.011,
        },
        {
            "name": "所得税=利润总额×15%",
            "left": round(float(pnl_rows[-1]["income_tax"]), 2) if pnl_rows else 0.0,
            "right": round(
                max(
                    float(pnl_rows[-1]["revenue"] - pnl_rows[-1]["cogs"] - pnl_rows[-1]["tax_surcharge"]
                          - pnl_rows[-1]["period_expense"] + pnl_rows[-1]["nonop_income"]
                          - pnl_rows[-1]["nonop_expense"]),
                    0.0,
                ) * 0.15,
                2,
            ) if pnl_rows else 0.0,
            "diff": 0.0,
            "passed": True,
        },
        {
            "name": "现金余额>=100000",
            "left": cash,
            "right": 100_000.0,
            "diff": round(cash - 100_000.0, 2),
            "passed": cash >= 100_000,
        },
    ]
    return {"passed": all(item["passed"] for item in checks), "checks": checks, "max_balance_diff": round(max_diff, 2)}


def seed_database(reset: bool = True) -> dict[str, Any]:
    """重建并播种数据库；固定随机种子，返回行数与勾稽结果。"""
    if not _SEED_LOCK.acquire(blocking=False):
        raise AppError(409, "SEED_IN_PROGRESS", "已有播种任务正在执行")
    started = time.perf_counter()
    try:
        rng = random.Random(20260101)
        conn = connect(read_only=False)
        try:
            if reset:
                _drop_all(conn)
            create_schema(conn)
            existing = conn.execute("SELECT COUNT(*) AS n FROM dim_company").fetchone()["n"]
            if existing and not reset:
                return {
                    "seeded": False,
                    "tables": ALL_TABLES,
                    "rows": {},
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "reconciliation": None,
                }

            conn.execute("BEGIN IMMEDIATE")
            company_ids, account_ids, period_ids, customers, vendors, periods = _seed_dimensions(conn, rng)
            counts = _generate_financials(
                conn, rng, company_ids, account_ids, period_ids, periods, customers, vendors
            )
            conn.commit()
            reconciliation = assert_reconciliation(conn)
            rows = {name: 0 for name in BUSINESS_TABLES}
            rows.update(counts)
            for table in ("dim_company", "dim_account", "dim_customer", "dim_vendor", "dim_period"):
                rows[table] = conn.execute(
                    f"SELECT COUNT(*) AS n FROM {quote_identifier(table)}"
                ).fetchone()["n"]
            rows["ontology_term"] = 0
            rows["ontology_relation"] = 0
            rows["ontology_sql_attribute"] = 0
            rows["query_audit"] = 0
            return {
                "seeded": True,
                "tables": ALL_TABLES,
                "rows": rows,
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "reconciliation": reconciliation,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    finally:
        _SEED_LOCK.release()


_SEED_LOCK = __import__("threading").Lock()
