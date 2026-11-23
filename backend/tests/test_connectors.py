"""五类连接器与通用元数据工具的单元测试（PT-02 风险点）。"""

from __future__ import annotations

import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from app.connectors.base import (
    ColumnMeta,
    ConnectorError,
    MetadataSnapshot,
    TableMeta,
    infer_text_type,
    json_safe,
    quote_ident,
    truncate_metadata,
)
from app.connectors.files import CsvConnector, ExcelConnector, _clean_header
from app.connectors.mysql import MySQLConnector
from app.connectors.postgres import PostgresConnector
from app.connectors.sqlite import SQLiteFileConnector
from tests.source_support import csv_bytes, make_sqlite_db, xlsx_bytes


# ---------------------------------------------------------------- SQLite


def test_sqlite_connector_metadata_and_samples(tmp_path: Path) -> None:
    path = make_sqlite_db(tmp_path / "warehouse.db")
    connector = SQLiteFileConnector(path)

    result = connector.test()
    assert result.success is True
    assert result.table_count == 2
    assert result.server_version
    assert result.latest_period is None

    snapshot = connector.inspect()
    assert snapshot.kind == "sqlite"
    tables = {table.name: table for table in snapshot.tables}
    assert set(tables) == {"parent", "child"}

    parent = tables["parent"]
    assert parent.schema == "main"
    assert parent.table_type == "table"
    assert parent.row_count == 2
    assert [column.name for column in parent.columns] == ["id", "name"]
    assert parent.columns[0].is_primary_key is True

    child = tables["child"]
    assert [column.name for column in child.columns] == [
        "id",
        "parent_id",
        "amount",
        "note",
    ]
    assert child.columns[1].nullable is False
    assert child.foreign_keys[0].from_column == "parent_id"
    assert child.foreign_keys[0].to_table == "parent"
    assert any(
        index.name == "idx_child_parent" and index.unique
        for index in child.indexes
    )
    assert child.indexes[0].columns == ["parent_id"]

    samples = connector.sample_rows(child, 1)
    assert len(samples) == 1
    assert set(samples[0]) == {"id", "parent_id", "amount", "note"}
    assert samples[0]["id"] == 1
    assert samples[0]["amount"] == 10.5


def test_sqlite_connector_missing_and_corrupt_files(tmp_path: Path) -> None:
    missing = SQLiteFileConnector(tmp_path / "missing.db")
    with pytest.raises(ConnectorError, match="不存在"):
        missing.test()
    with pytest.raises(ConnectorError, match="不存在"):
        missing.inspect()

    corrupt_path = tmp_path / "corrupt.db"
    corrupt_path.write_bytes(b"this-is-not-a-sqlite-database")
    corrupt = SQLiteFileConnector(corrupt_path)
    with pytest.raises(ConnectorError):
        corrupt.test()
    with pytest.raises(ConnectorError):
        corrupt.inspect()


# ---------------------------------------------------------------- base utils


def test_quote_ident_escaping_and_nul_rejection() -> None:
    assert quote_ident('a"b', "sqlite") == '"a""b"'
    assert quote_ident("a`b", "mysql") == "`a``b`"
    assert quote_ident("plain", "postgresql") == '"plain"'
    with pytest.raises(ConnectorError):
        quote_ident("bad\x00name", "sqlite")
    with pytest.raises(ConnectorError):
        quote_ident("", "mysql")


def test_json_safe_conversions() -> None:
    assert json_safe(None) is None
    assert json_safe("text") == "text"
    assert json_safe(True) is True
    assert json_safe(Decimal("1.25")) == 1.25
    assert json_safe(date(2026, 1, 2)) == "2026-01-02"
    assert json_safe(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05"
    assert json_safe(b"\x01\x02") == "0x0102"
    assert json_safe(float("nan")) == "nan"


def test_infer_text_type_rules() -> None:
    assert infer_text_type(["1", "-2"]) == "INTEGER"
    assert infer_text_type(["1.5", "2"]) == "REAL"
    assert infer_text_type(["true", "No", "是"]) == "BOOLEAN"
    assert infer_text_type(["2026-01-02"]) == "DATE"
    assert infer_text_type(["2026-01-02T03:04:05"]) == "DATETIME"
    assert infer_text_type(["abc", "1"]) == "TEXT"
    assert infer_text_type(["", None]) == "TEXT"


def test_truncate_metadata_limits_tables_columns_and_bytes() -> None:
    snapshot = MetadataSnapshot(
        kind="csv",
        server_version=None,
        tables=[
            TableMeta(
                schema="file",
                name=f"t{index}",
                columns=[
                    ColumnMeta(name=f"c{column}", data_type="TEXT")
                    for column in range(3)
                ],
            )
            for index in range(3)
        ],
    )
    limited = truncate_metadata(snapshot, max_tables=2, max_columns=2, max_bytes=10_000_000)
    assert [table.name for table in limited.tables] == ["t0", "t1"]
    assert all(len(table.columns) == 2 for table in limited.tables)
    assert limited.truncated is True
    assert any("仅展示前 2 张表" in warning for warning in limited.warnings)
    assert any("仅展示前 2 列" in warning for warning in limited.warnings)

    squeezed = truncate_metadata(snapshot, max_tables=10, max_columns=10, max_bytes=1)
    assert squeezed.tables == []
    assert squeezed.truncated is True
    assert any("字节" in warning for warning in squeezed.warnings)


# ---------------------------------------------------------------- CSV


def test_csv_utf8_header_types_and_samples(tmp_path: Path) -> None:
    path = tmp_path / "sales.csv"
    path.write_bytes(
        csv_bytes(
            [
                ["id", "name", "amount", "active"],
                ["1", "甲", "10.5", "true"],
                ["2", "乙", "20", "false"],
            ]
        )
    )
    connector = CsvConnector(path)
    snapshot = connector.inspect()
    table = snapshot.tables[0]

    assert table.schema == "file"
    assert table.name == "sales"
    assert table.table_type == "file"
    assert table.row_count == 2
    assert [column.name for column in table.columns] == [
        "id",
        "name",
        "amount",
        "active",
    ]
    assert [column.data_type for column in table.columns] == [
        "INTEGER",
        "TEXT",
        "REAL",
        "BOOLEAN",
    ]
    assert connector.encoding == "utf-8-sig"
    assert connector.delimiter == ","
    assert connector.has_header is True

    samples = connector.sample_rows(table, 5)
    assert len(samples) == 2
    assert samples[0]["id"] == "1"
    assert samples[0]["name"] == "甲"


def test_csv_gb18030_with_semicolon(tmp_path: Path) -> None:
    path = tmp_path / "gb_data.csv"
    path.write_bytes("编号;姓名;金额\n1;张三;100.5\n2;李四;200\n".encode("gb18030"))
    connector = CsvConnector(path)
    table = connector.inspect().tables[0]

    assert connector.delimiter == ";"
    assert connector.encoding.lower().replace("-", "") in {"gb18030", "gbk", "gb2312"}
    assert [column.name for column in table.columns] == ["编号", "姓名", "金额"]
    assert [column.data_type for column in table.columns] == ["INTEGER", "TEXT", "REAL"]
    assert table.row_count == 2


def test_csv_no_header_and_duplicate_header_fallback(tmp_path: Path) -> None:
    no_header = tmp_path / "no_header.csv"
    no_header.write_bytes(b"1,2\n3,4\n")
    connector = CsvConnector(no_header)
    table = connector.inspect().tables[0]
    assert connector.has_header is False
    assert [column.name for column in table.columns] == ["column_1", "column_2"]
    assert [column.data_type for column in table.columns] == ["INTEGER", "INTEGER"]
    assert table.row_count == 2

    # 重复表头无法通过 _detect_header（要求单元格互不重复），按无表头数据处理。
    duplicated = tmp_path / "duplicated.csv"
    duplicated.write_bytes(b"name,name,extra,id\nAlice,Bob,x,1\n")
    duplicate_connector = CsvConnector(duplicated)
    duplicate_table = duplicate_connector.inspect().tables[0]
    assert duplicate_connector.has_header is False
    assert [column.name for column in duplicate_table.columns] == [
        "column_1",
        "column_2",
        "column_3",
        "column_4",
    ]
    assert duplicate_table.row_count == 2


def test_clean_header_deduplicates_and_fills_empty_cells() -> None:
    assert _clean_header(["name", "name", "", "id"], 4) == [
        "name",
        "name_2",
        "column_3",
        "id",
    ]
    assert _clean_header([" Name ", "NAME"], 2) == ["Name", "NAME_2"]


def test_csv_empty_and_binary_inputs(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_bytes(b"")
    snapshot = CsvConnector(empty).inspect()
    assert snapshot.tables[0].row_count == 0
    assert snapshot.tables[0].columns == []
    assert any("为空" in warning for warning in snapshot.warnings)

    binary = tmp_path / "binary.csv"
    binary.write_bytes(b"a,b\n\x00\x01")
    with pytest.raises(ConnectorError, match="二进制"):
        CsvConnector(binary).inspect()


# ---------------------------------------------------------------- Excel


def test_excel_multi_sheet_and_samples(tmp_path: Path) -> None:
    path = tmp_path / "book.xlsx"
    path.write_bytes(
        xlsx_bytes(
            {
                "销售数据": [
                    ["id", "name", "amount"],
                    [1, "张三", 10.5],
                    [2, "李四", 20.25],
                ],
                "空表": [],
            }
        )
    )
    connector = ExcelConnector(path)
    snapshot = connector.inspect()

    assert len(snapshot.tables) == 1
    table = snapshot.tables[0]
    assert table.schema == "excel"
    assert table.name == "销售数据"
    assert table.table_type == "file"
    assert table.comment == "工作表 销售数据"
    assert table.row_count == 2
    assert table.row_count_estimated is True
    assert [column.name for column in table.columns] == ["id", "name", "amount"]
    assert [column.data_type for column in table.columns] == [
        "INTEGER",
        "TEXT",
        "REAL",
    ]
    assert any("Excel 行数" in warning for warning in snapshot.warnings)

    samples = connector.sample_rows(table, 1)
    assert len(samples) == 1
    assert samples[0]["name"] == "张三"
    assert samples[0]["amount"] == 10.5


def test_excel_rejects_legacy_and_suspicious_archive(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.xls"
    legacy.write_bytes(b"legacy-binary")
    with pytest.raises(ConnectorError, match="EXCEL_LEGACY_UNSUPPORTED"):
        ExcelConnector(legacy).inspect()

    bomb = tmp_path / "bomb.xlsx"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("payload.bin", b"\x00" * (2 * 1024 * 1024))
    with pytest.raises(ConnectorError, match="UPLOAD_SUSPICIOUS_ARCHIVE"):
        ExcelConnector(bomb).inspect()


def test_connectors_do_not_use_select_star() -> None:
    connector_dir = Path(__file__).resolve().parents[1] / "app" / "connectors"
    for name in ("sqlite.py", "postgres.py", "mysql.py"):
        source = (connector_dir / name).read_text(encoding="utf-8").lower()
        assert "select *" not in source, f"{name} 含 SELECT *"


# ---------------------------------------------------------------- PostgreSQL mock


PG_TABLE_ROWS = [
    {
        "schema": "public",
        "name": "fact_balance",
        "table_type": "table",
        "row_count": 1320,
        "comment": "月度科目余额表",
    },
    {
        "schema": "public",
        "name": "dim_period",
        "table_type": "view",
        "row_count": -1,
        "comment": None,
    },
]
PG_COLUMN_ROWS = [
    {
        "schema": "public",
        "table_name": "fact_balance",
        "column_name": "balance_id",
        "data_type": "integer",
        "is_nullable": "NO",
        "column_default": "nextval('balance_seq')",
        "ordinal_position": 1,
    },
    {
        "schema": "public",
        "table_name": "fact_balance",
        "column_name": "period_id",
        "data_type": "integer",
        "is_nullable": "NO",
        "column_default": None,
        "ordinal_position": 2,
    },
    {
        "schema": "public",
        "table_name": "fact_balance",
        "column_name": "amount",
        "data_type": "numeric",
        "is_nullable": "YES",
        "column_default": "0",
        "ordinal_position": 3,
    },
    {
        "schema": "public",
        "table_name": "dim_period",
        "column_name": "period_id",
        "data_type": "integer",
        "is_nullable": "NO",
        "column_default": None,
        "ordinal_position": 1,
    },
]
PG_COMMENT_ROWS = [
    {
        "schema": "public",
        "table_name": "fact_balance",
        "column_name": "amount",
        "comment": "余额金额",
    }
]
PG_PK_ROWS = [
    {"schema": "public", "table_name": "fact_balance", "column_name": "balance_id"}
]
PG_FK_ROWS = [
    {
        "schema": "public",
        "table_name": "fact_balance",
        "from_column": "period_id",
        "to_schema": "public",
        "to_table": "dim_period",
        "to_column": "period_id",
        "constraint_name": "fk_balance_period",
    }
]
PG_INDEX_ROWS = [
    {
        "schema": "public",
        "table_name": "fact_balance",
        "index_name": "idx_balance_period",
        "is_unique": False,
        "column_name": "period_id",
        "ord": 1,
    },
    {
        "schema": "public",
        "table_name": "fact_balance",
        "index_name": "idx_balance_period",
        "is_unique": False,
        "column_name": "amount",
        "ord": 2,
    },
    {
        "schema": "public",
        "table_name": "fact_balance",
        "index_name": "ux_balance_id",
        "is_unique": True,
        "column_name": "balance_id",
        "ord": 1,
    },
]
PG_SAMPLE_ROWS = [
    {"balance_id": 1, "period_id": 10, "amount": Decimal("12.5")},
    {"balance_id": 2, "period_id": 10, "amount": None},
]


def _pg_responder(sql: str, params) -> list[dict]:
    if "SELECT version() AS server_version" in sql:
        return [{"server_version": "PostgreSQL 16.3"}]
    if "to_regclass" in sql:
        return [{"reg": None}]
    if "COUNT(*) AS n" in sql:
        return [{"n": 2}]
    if "obj_description" in sql:
        return PG_TABLE_ROWS
    if "col_description" in sql:
        return PG_COMMENT_ROWS
    if "information_schema.columns" in sql:
        return PG_COLUMN_ROWS
    if "indisprimary" in sql:
        return PG_PK_ROWS
    if "contype = 'f'" in sql:
        return PG_FK_ROWS
    if "indisunique" in sql:
        return PG_INDEX_ROWS
    if "LIMIT %s" in sql:
        return PG_SAMPLE_ROWS
    return []


class _FakePGResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict]:
        return list(self._rows)


class _FakePGConnection:
    def __init__(self, responder) -> None:
        self.responder = responder
        self.calls: list[tuple[object, object]] = []
        self.closed = False
        self.kwargs: dict = {}

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        rendered = str(sql)
        return _FakePGResult(self.responder(rendered, params))

    def close(self) -> None:
        self.closed = True


def _pg_factory(responder):
    connections: list[_FakePGConnection] = []

    def connect(**kwargs):
        connection = _FakePGConnection(responder)
        connection.kwargs = kwargs
        connections.append(connection)
        return connection

    return connect, connections


def test_postgres_connector_maps_catalog_readonly_and_samples() -> None:
    connector = PostgresConnector(
        "pg.internal", 5432, "finance", "readonly", "{{SECRET_xqdz6p4i}}", "public"
    )
    connect, connections = _pg_factory(_pg_responder)
    with patch("psycopg.connect", side_effect=connect):
        snapshot = connector.inspect()
        result = connector.test()
        samples = connector.sample_rows(snapshot.tables[0], 2)

    assert snapshot.kind == "postgresql"
    assert snapshot.server_version == "PostgreSQL 16.3"
    tables = {table.name: table for table in snapshot.tables}
    balance = tables["fact_balance"]
    assert balance.row_count == 1320
    assert balance.row_count_estimated is True
    assert balance.comment == "月度科目余额表"
    assert [column.name for column in balance.columns] == [
        "balance_id",
        "period_id",
        "amount",
    ]
    assert balance.columns[0].is_primary_key is True
    assert balance.columns[1].is_primary_key is False
    assert balance.columns[2].nullable is True
    assert balance.columns[2].default == "0"
    assert balance.columns[2].comment == "余额金额"
    assert balance.foreign_keys[0].to_table == "dim_period"
    index_map = {index.name: index for index in balance.indexes}
    assert index_map["idx_balance_period"].columns == ["period_id", "amount"]
    assert index_map["idx_balance_period"].unique is False
    assert index_map["ux_balance_id"].unique is True

    period_view = tables["dim_period"]
    assert period_view.table_type == "view"
    assert period_view.row_count is None
    assert period_view.row_count_estimated is False

    assert result.success is True
    assert result.server_version == "PostgreSQL 16.3"
    assert result.table_count == 2
    assert result.latest_period is None

    assert len(samples) == 2
    assert samples[0]["amount"] == 12.5
    assert samples[1]["amount"] is None

    assert len(connections) == 3
    assert all(connection.closed for connection in connections)
    for connection in connections:
        assert connection.kwargs["connect_timeout"] == 5
        assert connection.kwargs["autocommit"] is True
        assert "default_transaction_read_only=on" in connection.kwargs["options"]
        assert "statement_timeout=5000" in connection.kwargs["options"]
        assert "{{SECRET_xqdz6p4i}}" in connection.kwargs.values()

    sample_call = next(
        call for call in connections[2].calls if call[1] == (2,)
    )
    rendered = sample_call[0].as_string(None)
    assert rendered.startswith(
        'SELECT "balance_id", "period_id", "amount" FROM "public"."fact_balance" LIMIT %s'
    )
    assert "*" not in rendered


def test_postgres_connection_errors_are_sanitized() -> None:
    secret = "{{SECRET_hcbn3m7u}}"
    captured: dict = {}

    def boom(**kwargs):
        captured.update(kwargs)
        raise RuntimeError(f"auth failed passwd={secret} host=pg.internal")

    connector = PostgresConnector(
        "pg.internal", 5432, "finance", "readonly", secret, "public"
    )
    with patch("psycopg.connect", side_effect=boom):
        with pytest.raises(ConnectorError) as excinfo:
            connector.test()

    message = str(excinfo.value)
    assert secret not in message
    assert "passwd=***" in message
    assert captured["connect_timeout"] == 5
    assert "default_transaction_read_only=on" in captured["options"]


# ---------------------------------------------------------------- MySQL mock


MYSQL_TABLE_ROWS = [
    {
        "table_schema": "finance",
        "table_name": "fact_balance",
        "table_type": "BASE TABLE",
        "row_count": 100,
        "comment": "余额表",
    },
    {
        "table_schema": "finance",
        "table_name": "v_balance",
        "table_type": "VIEW",
        "row_count": 0,
        "comment": "",
    },
]
MYSQL_COLUMN_ROWS = [
    {
        "table_name": "fact_balance",
        "column_name": "balance_id",
        "column_type": "bigint(20)",
        "data_type": "bigint",
        "is_nullable": "NO",
        "column_default": None,
        "column_key": "PRI",
        "comment": "主键",
        "ordinal_position": 1,
    },
    {
        "table_name": "fact_balance",
        "column_name": "amount",
        "column_type": "decimal(18,2)",
        "data_type": "decimal",
        "is_nullable": "YES",
        "column_default": "0.00",
        "column_key": "",
        "comment": "金额",
        "ordinal_position": 2,
    },
    {
        "table_name": "v_balance",
        "column_name": "balance_id",
        "column_type": "bigint(20)",
        "data_type": "bigint",
        "is_nullable": "YES",
        "column_default": None,
        "column_key": "",
        "comment": "",
        "ordinal_position": 1,
    },
]
MYSQL_FK_ROWS = [
    {
        "table_name": "fact_balance",
        "constraint_name": "fk_balance_period",
        "from_column": "period_id",
        "to_schema": "finance",
        "to_table": "dim_period",
        "to_column": "period_id",
        "ordinal_position": 1,
    }
]
MYSQL_INDEX_ROWS = [
    {
        "table_name": "fact_balance",
        "index_name": "idx_balance_period",
        "non_unique": 0,
        "column_name": "period_id",
        "seq_in_index": 1,
    },
    {
        "table_name": "fact_balance",
        "index_name": "idx_balance_account",
        "non_unique": 1,
        "column_name": "account_id",
        "seq_in_index": 1,
    },
    {
        "table_name": "fact_balance",
        "index_name": "idx_balance_account",
        "non_unique": 1,
        "column_name": "balance_id",
        "seq_in_index": 2,
    },
]
MYSQL_SAMPLE_ROWS = [
    {"balance_id": 1, "amount": Decimal("8.5")},
    {"balance_id": 2, "amount": None},
]


def _mysql_responder(sql: str, params) -> list[dict]:
    if "SET SESSION TRANSACTION READ ONLY" in sql:
        return []
    if "SET SESSION MAX_EXECUTION_TIME" in sql:
        return []
    if "SELECT VERSION() AS version" in sql:
        return [{"version": "8.0.36"}]
    if "FROM information_schema.TABLES" in sql:
        return MYSQL_TABLE_ROWS
    if "FROM information_schema.COLUMNS" in sql:
        return MYSQL_COLUMN_ROWS
    if "FROM information_schema.KEY_COLUMN_USAGE" in sql:
        return MYSQL_FK_ROWS
    if "FROM information_schema.STATISTICS" in sql:
        return MYSQL_INDEX_ROWS
    if "LIMIT %s" in sql:
        return MYSQL_SAMPLE_ROWS
    return []


class _FakeMySQLCursor:
    def __init__(self, connection: "_FakeMySQLConnection") -> None:
        self.connection = connection
        self._rows: list[dict] = []

    def __enter__(self) -> "_FakeMySQLCursor":
        return self

    def __exit__(self, *args) -> bool:
        return False

    def execute(self, sql, params=None) -> None:
        self.connection.calls.append((sql, params))
        self._rows = self.connection.responder(sql, params)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict]:
        return list(self._rows)


class _FakeMySQLConnection:
    def __init__(self, responder) -> None:
        self.responder = responder
        self.calls: list[tuple[str, object]] = []
        self.closed = False
        self.kwargs: dict = {}

    def cursor(self) -> _FakeMySQLCursor:
        return _FakeMySQLCursor(self)

    def close(self) -> None:
        self.closed = True


def _mysql_factory(responder):
    connections: list[_FakeMySQLConnection] = []

    def connect(**kwargs):
        connection = _FakeMySQLConnection(responder)
        connection.kwargs = kwargs
        connections.append(connection)
        return connection

    return connect, connections


def test_mysql_connector_maps_catalog_readonly_and_samples() -> None:
    connector = MySQLConnector(
        "mysql.internal", 3306, "finance", "readonly", "{{SECRET_wqvo5r8e}}", "finance"
    )
    connect, connections = _mysql_factory(_mysql_responder)
    with patch("pymysql.connect", side_effect=connect):
        snapshot = connector.inspect()
        samples = connector.sample_rows(snapshot.tables[0], 2)

    assert snapshot.kind == "mysql"
    assert snapshot.server_version == "8.0.36"
    tables = {table.name: table for table in snapshot.tables}
    balance = tables["fact_balance"]
    assert balance.table_type == "table"
    assert balance.row_count == 100
    assert balance.row_count_estimated is True
    assert balance.comment == "余额表"
    assert [column.name for column in balance.columns] == ["balance_id", "amount"]
    assert balance.columns[0].is_primary_key is True
    assert balance.columns[0].nullable is False
    assert balance.columns[1].data_type == "decimal(18,2)"
    assert balance.columns[1].nullable is True
    assert balance.columns[1].default == "0.00"
    assert balance.columns[1].comment == "金额"
    assert balance.foreign_keys[0].to_table == "dim_period"
    index_map = {index.name: index for index in balance.indexes}
    assert index_map["idx_balance_period"].unique is True
    assert index_map["idx_balance_account"].unique is False
    assert index_map["idx_balance_account"].columns == ["account_id", "balance_id"]
    assert tables["v_balance"].table_type == "view"

    assert len(samples) == 2
    assert samples[0]["amount"] == 8.5
    assert samples[1]["amount"] is None

    assert connections[0].closed is True
    first_sql = connections[0].calls[0][0]
    assert first_sql == "SET SESSION TRANSACTION READ ONLY"
    assert any("MAX_EXECUTION_TIME" in sql for sql, _ in connections[0].calls)
    for connection in connections:
        assert connection.kwargs["connect_timeout"] == 5
        assert connection.kwargs["read_timeout"] == 5
        assert connection.kwargs["write_timeout"] == 5
        assert connection.kwargs["charset"] == "utf8mb4"
        assert connection.kwargs["autocommit"] is True
        assert "{{SECRET_wqvo5r8e}}" in connection.kwargs.values()

    sample_call = next(
        call for call in connections[1].calls if call[1] == (2,)
    )
    assert sample_call[0] == (
        "SELECT `balance_id`, `amount` FROM `finance`.`fact_balance` LIMIT %s"
    )


def test_mysql_readonly_failure_rejects_connection() -> None:
    def failing_responder(sql: str, params):
        if "SET SESSION TRANSACTION READ ONLY" in sql:
            raise RuntimeError("readonly session rejected")
        return []

    connector = MySQLConnector(
        "mysql.internal", 3306, "finance", "readonly", "mysql-secret", "finance"
    )
    connect, connections = _mysql_factory(failing_responder)
    with patch("pymysql.connect", side_effect=connect):
        with pytest.raises(ConnectorError, match="readonly session rejected"):
            connector.inspect()
    assert connections[0].closed is True


def test_mysql_connection_errors_are_sanitized() -> None:
    secret = "{{SECRET_yln2qk9s}}"

    def boom(**kwargs):
        raise RuntimeError(f"access denied pwd={secret} host=mysql.internal")

    connector = MySQLConnector(
        "mysql.internal", 3306, "finance", "readonly", secret, "finance"
    )
    with patch("pymysql.connect", side_effect=boom):
        with pytest.raises(ConnectorError) as excinfo:
            connector.test()
    message = str(excinfo.value)
    assert secret not in message
    assert "pwd=***" in message
