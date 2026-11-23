"""连接器行读取与 readers 适配层：批量、截断、水位、列顺序与失败路径。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.connectors.base import ColumnMeta, ConnectorError, TableMeta
from app.connectors.files import CsvConnector, ExcelConnector
from app.connectors.mysql import MySQLConnector
from app.connectors.postgres import PostgresConnector
from app.connectors.sqlite import SQLiteFileConnector
from app.services.ingest import readers
from tests.source_support import csv_bytes, xlsx_bytes


def _make_events_db(path: Path) -> Path:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, name TEXT, amount REAL)"
        )
        conn.executemany(
            "INSERT INTO events (id, name, amount) VALUES (?, ?, ?)",
            [(index, f"event-{index}", index * 1.5) for index in range(1, 6)],
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _events_connector(tmp_path: Path) -> tuple[SQLiteFileConnector, TableMeta]:
    path = _make_events_db(tmp_path / "events.db")
    connector = SQLiteFileConnector(path)
    snapshot = connector.inspect()
    table = next(item for item in snapshot.tables if item.name == "events")
    return connector, table


def _flatten(batches: list[list[dict]]) -> list[dict]:
    return [row for batch in batches for row in batch]


def test_sqlite_iter_rows_batch_sizes_and_columns(tmp_path: Path) -> None:
    connector, table = _events_connector(tmp_path)
    batches = list(
        readers.iter_batches(connector, table, batch_size=2, max_rows=100)
    )

    assert [len(batch) for batch in batches] == [2, 2, 1]
    assert list(batches[0][0].keys()) == ["id", "name", "amount"]
    rows = _flatten(batches)
    assert [row["id"] for row in rows] == [1, 2, 3, 4, 5]
    assert rows[0]["name"] == "event-1"
    assert rows[0]["amount"] == 1.5


def test_sqlite_iter_rows_max_rows_truncates(tmp_path: Path) -> None:
    connector, table = _events_connector(tmp_path)
    rows = _flatten(
        list(readers.iter_batches(connector, table, batch_size=2, max_rows=3))
    )
    assert len(rows) == 3
    assert [row["id"] for row in rows] == [1, 2, 3]


def test_sqlite_iter_rows_watermark_uses_greater_or_equal(tmp_path: Path) -> None:
    connector, table = _events_connector(tmp_path)
    rows = _flatten(
        list(
            readers.iter_batches(
                connector,
                table,
                watermark_column="id",
                watermark_value="3",
                batch_size=2,
                max_rows=100,
            )
        )
    )
    assert [row["id"] for row in rows] == [3, 4, 5]

    all_rows = _flatten(
        list(readers.iter_batches(connector, table, batch_size=100, max_rows=100))
    )
    assert len(all_rows) == 5


def test_sqlite_iter_rows_quotes_weird_column_names(tmp_path: Path) -> None:
    path = tmp_path / "weird.db"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            'CREATE TABLE weird ("金额(元)" REAL, "order-id" TEXT, "客户 名称" TEXT, '
            '"quote""col" TEXT)'
        )
        conn.execute(
            'INSERT INTO weird VALUES (10.5, \'A-1\', \'张三\', \'q\')'
        )
        conn.commit()
    finally:
        conn.close()

    connector = SQLiteFileConnector(path)
    table = next(item for item in connector.inspect().tables if item.name == "weird")
    rows = _flatten(
        list(readers.iter_batches(connector, table, batch_size=10, max_rows=10))
    )
    assert rows == [
        {"金额(元)": 10.5, "order-id": "A-1", "客户 名称": "张三", "quote\"col": "q"}
    ]


def test_csv_iter_rows_skips_header_and_respects_batch(tmp_path: Path) -> None:
    path = tmp_path / "customers.csv"
    path.write_bytes(
        csv_bytes(
            [
                ["id", "name", "amount"],
                ["1", "Alice", "10.5"],
                ["2", "Bob", "20"],
                ["3", "Carol", "30"],
            ]
        )
    )
    connector = CsvConnector(path)
    table = connector.inspect().tables[0]
    batches = list(readers.iter_batches(connector, table, batch_size=2, max_rows=100))

    assert [len(batch) for batch in batches] == [2, 1]
    rows = _flatten(batches)
    assert [row["name"] for row in rows] == ["Alice", "Bob", "Carol"]
    assert list(rows[0].keys()) == [column.name for column in table.columns]
    assert "id" not in {row["name"] for row in rows}


def test_excel_iter_rows_reads_requested_sheet(tmp_path: Path) -> None:
    path = tmp_path / "book.xlsx"
    path.write_bytes(
        xlsx_bytes(
            {
                "first": [["id", "name"], [1, "a"], [2, "b"]],
                "second": [["id", "name"], [3, "c"]],
            }
        )
    )
    connector = ExcelConnector(path)
    snapshot = connector.inspect()
    first = next(table for table in snapshot.tables if table.name == "first")
    rows = _flatten(
        list(readers.iter_batches(connector, first, batch_size=10, max_rows=10))
    )
    assert [row["name"] for row in rows] == ["a", "b"]
    assert list(rows[0].keys()) == ["id", "name"]


def test_iter_batches_rejects_invalid_parameters(tmp_path: Path) -> None:
    connector, table = _events_connector(tmp_path)
    with pytest.raises(ConnectorError):
        list(readers.iter_batches(connector, table, batch_size=0))
    with pytest.raises(ConnectorError):
        list(readers.iter_batches(connector, table, batch_size=5001))
    with pytest.raises(ConnectorError):
        list(readers.iter_batches(connector, table, max_rows=0))
    with pytest.raises(ConnectorError):
        list(readers.iter_batches(object(), table))


@pytest.mark.parametrize("kind", ["postgresql", "mysql"])
def test_database_connectors_unreachable_iter_rows_are_sanitized(
    tmp_path: Path, kind: str
) -> None:
    """真实数据库连通性未运行；仅验证不可达路径抛 ConnectorError 且不泄漏密钥。"""
    table = TableMeta(
        schema="public" if kind == "postgresql" else "task04",
        name="fact_balance",
        columns=[ColumnMeta(name="balance_id", data_type="INTEGER")],
    )
    secret = "TASK04_SECRET_MARKER"
    if kind == "postgresql":
        connector = PostgresConnector(
            host="127.0.0.1",
            port=1,
            database="task04",
            username="task04",
            password=secret,
            schema="public",
        )
    else:
        connector = MySQLConnector(
            host="127.0.0.1",
            port=1,
            database="task04",
            username="task04",
            password=secret,
            schema="task04",
        )

    with pytest.raises(ConnectorError) as excinfo:
        list(
            readers.iter_batches(
                connector, table, batch_size=10, max_rows=10
            )
        )
    assert secret not in str(excinfo.value)
