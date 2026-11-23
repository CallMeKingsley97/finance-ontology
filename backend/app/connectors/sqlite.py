"""SQLite 文件连接器：只读打开、读取元数据与样本。"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Iterator

from app.connectors.base import (
    ColumnMeta,
    ConnectorError,
    ForeignKeyMeta,
    IndexMeta,
    MetadataSnapshot,
    TableMeta,
    TestResult,
    json_safe,
    quote_ident,
)

_ROW_COUNT_CAP = 100_000


class SQLiteFileConnector:
    """以只读模式访问本地 SQLite 文件。"""

    kind = "sqlite"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise ConnectorError("SQLite 文件不存在")
        try:
            conn = sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=ro",
                uri=True,
                timeout=5.0,
            )
        except sqlite3.Error as exc:
            raise ConnectorError(f"SQLite 连接失败: {exc}") from exc
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def test(self) -> TestResult:
        started = time.perf_counter()
        try:
            conn = self._connect()
            try:
                version = conn.execute("SELECT sqlite_version()").fetchone()[0]
                table_count = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                ).fetchone()[0]
                latest_period = None
                try:
                    row = conn.execute("SELECT MAX(period_code) FROM dim_period").fetchone()
                    latest_period = row[0] if row else None
                except sqlite3.Error:
                    latest_period = None
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise ConnectorError(f"SQLite 连接测试失败: {exc}") from exc
        return TestResult(
            success=True,
            latency_ms=int((time.perf_counter() - started) * 1000),
            server_version=str(version),
            table_count=int(table_count),
            latest_period=latest_period,
            message="SQLite 连接成功",
        )

    def inspect(self) -> MetadataSnapshot:
        warnings: list[str] = []
        tables: list[TableMeta] = []
        try:
            conn = self._connect()
            try:
                version = conn.execute("SELECT sqlite_version()").fetchone()[0]
                rows = conn.execute(
                    "SELECT name, type FROM sqlite_master "
                    "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                ).fetchall()
                for name, object_type in rows:
                    try:
                        table = self._inspect_table(conn, name, object_type, warnings)
                    except sqlite3.Error as exc:
                        warnings.append(f"表 {name} 读取失败: {exc}")
                        continue
                    tables.append(table)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise ConnectorError(f"SQLite 元数据读取失败: {exc}") from exc
        return MetadataSnapshot(
            kind=self.kind,
            server_version=str(version),
            tables=tables,
            truncated=bool(warnings),
            warnings=warnings,
        )

    def _inspect_table(
        self,
        conn: sqlite3.Connection,
        name: str,
        object_type: str,
        warnings: list[str],
    ) -> TableMeta:
        table_type = "view" if object_type == "view" else "table"
        columns = [
            ColumnMeta(
                name=row[1],
                data_type=(row[2] or "TEXT"),
                nullable=not bool(row[3]),
                default=row[4],
                is_primary_key=bool(row[5]),
            )
            for row in conn.execute(f"PRAGMA table_info({quote_ident(name, 'sqlite')})")
        ]
        foreign_keys = [
            ForeignKeyMeta(
                from_table=name,
                from_column=row[3],
                to_table=row[2],
                to_column=row[4] or "",
                to_schema=None,
                constraint_name=None,
            )
            for row in conn.execute(f"PRAGMA foreign_key_list({quote_ident(name, 'sqlite')})")
        ]
        indexes: list[IndexMeta] = []
        for index in conn.execute(f"PRAGMA index_list({quote_ident(name, 'sqlite')})"):
            index_columns = [
                item[2]
                for item in conn.execute(
                    f"PRAGMA index_info({quote_ident(index[1], 'sqlite')})"
                )
                if item[2]
            ]
            indexes.append(
                IndexMeta(name=index[1], columns=index_columns, unique=bool(index[2]))
            )

        row_count: int | None = None
        estimated = False
        try:
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM (SELECT 1 FROM {quote_ident(name, 'sqlite')} "
                f"LIMIT {_ROW_COUNT_CAP + 1})"
            ).fetchone()
            row_count = int(count_row[0])
            if row_count > _ROW_COUNT_CAP:
                row_count = _ROW_COUNT_CAP
                estimated = True
                warnings.append(f"表 {name} 行数已截断为 {_ROW_COUNT_CAP}")
        except sqlite3.Error:
            row_count = None

        return TableMeta(
            schema="main",
            name=name,
            table_type=table_type,
            row_count=row_count,
            row_count_estimated=estimated,
            columns=columns,
            foreign_keys=foreign_keys,
            indexes=indexes,
        )

    def sample_rows(self, table: TableMeta, limit: int = 5) -> list[dict[str, Any]]:
        column_names = [column.name for column in table.columns]
        if not column_names:
            return []
        safe_limit = max(1, min(int(limit or 5), 1000))
        selected = ", ".join(quote_ident(name, "sqlite") for name in column_names)
        sql = (
            f"SELECT {selected} FROM "
            f"{quote_ident(table.schema or 'main', 'sqlite')}."
            f"{quote_ident(table.name, 'sqlite')} LIMIT ?"
        )
        try:
            conn = self._connect()
            try:
                rows = conn.execute(sql, (safe_limit,)).fetchall()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise ConnectorError(f"SQLite 样本查询失败: {exc}") from exc
        return [
            {name: json_safe(value) for name, value in zip(column_names, row)}
            for row in rows
        ]

    def iter_rows(
        self,
        table: TableMeta,
        *,
        watermark_column: str | None = None,
        watermark_value: str | None = None,
        batch_size: int = 1000,
        max_rows: int = 200_000,
    ) -> Iterator[list[dict[str, Any]]]:
        """按批只读读取表数据；列清单来自元数据，水位值参数化。"""
        column_names = [column.name for column in table.columns]
        if not column_names:
            return
        selected = ", ".join(quote_ident(name, "sqlite") for name in column_names)
        where = ""
        params: list[Any] = []
        if watermark_column:
            where = f" WHERE {quote_ident(watermark_column, 'sqlite')} >= ?"
            params.append(watermark_value)
        sql = (
            f"SELECT {selected} FROM "
            f"{quote_ident(table.schema or 'main', 'sqlite')}."
            f"{quote_ident(table.name, 'sqlite')}{where}"
        )
        safe_batch = max(1, int(batch_size or 1000))
        safe_max = max(1, int(max_rows or 1))
        conn = self._connect()
        try:
            cursor = conn.execute(sql, tuple(params))
            yielded = 0
            while yielded < safe_max:
                rows = cursor.fetchmany(min(safe_batch, safe_max - yielded))
                if not rows:
                    break
                yield [
                    {name: json_safe(value) for name, value in zip(column_names, row)}
                    for row in rows
                ]
                yielded += len(rows)
        except sqlite3.Error as exc:
            raise ConnectorError(f"SQLite 行读取失败: {exc}") from exc
        finally:
            conn.close()

    def close(self) -> None:
        """SQLite 连接按需创建并立即关闭，无需额外清理。"""
