"""MySQL 连接器：只读会话、information_schema 元数据与样本。"""

from __future__ import annotations

import time
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
from app.core.crypto import sanitize_error


class MySQLConnector:
    """通过 PyMySQL 只读会话访问 MySQL。"""

    kind = "mysql"

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        schema: str | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.database = database
        self.username = username
        self._password = password
        self.schema = schema or database

    def _connect(self):
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as exc:
            raise ConnectorError("MySQL 驱动未安装") from exc
        try:
            conn = pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.username,
                password=self._password,
                database=self.database,
                connect_timeout=5,
                read_timeout=5,
                write_timeout=5,
                charset="utf8mb4",
                autocommit=True,
                cursorclass=DictCursor,
            )
        except Exception as exc:
            raise ConnectorError(sanitize_error(str(exc), (self._password,))) from exc
        try:
            with conn.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                try:
                    cursor.execute("SET SESSION MAX_EXECUTION_TIME=5000")
                except Exception:
                    # 旧版本不支持该变量，不影响只读约束。
                    pass
        except Exception as exc:
            conn.close()
            raise ConnectorError(sanitize_error(str(exc), (self._password,))) from exc
        return conn

    def test(self) -> TestResult:
        started = time.perf_counter()
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT VERSION() AS version")
                row = cursor.fetchone() or {}
                version = row.get("version")
                cursor.execute(
                    """SELECT COUNT(*) AS n
                       FROM information_schema.TABLES
                       WHERE TABLE_SCHEMA = %s""",
                    (self.schema,),
                )
                count_row = cursor.fetchone() or {}
                latest_period = None
                try:
                    cursor.execute("SELECT MAX(period_code) AS latest FROM dim_period")
                    latest = cursor.fetchone() or {}
                    latest_period = latest.get("latest")
                except Exception:
                    latest_period = None
        except Exception as exc:
            raise ConnectorError(sanitize_error(str(exc), (self._password,))) from exc
        finally:
            conn.close()
        return TestResult(
            success=True,
            latency_ms=int((time.perf_counter() - started) * 1000),
            server_version=str(version) if version else None,
            table_count=int(count_row.get("n") or 0),
            latest_period=latest_period,
            message="MySQL 连接成功",
        )

    def inspect(self) -> MetadataSnapshot:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """SELECT TABLE_SCHEMA AS table_schema,
                              TABLE_NAME AS table_name,
                              TABLE_TYPE AS table_type,
                              TABLE_ROWS AS row_count,
                              TABLE_COMMENT AS comment
                       FROM information_schema.TABLES
                       WHERE TABLE_SCHEMA = %s
                       ORDER BY TABLE_NAME""",
                    (self.schema,),
                )
                table_rows = cursor.fetchall()
                cursor.execute(
                    """SELECT TABLE_NAME AS table_name,
                              COLUMN_NAME AS column_name,
                              COLUMN_TYPE AS column_type,
                              DATA_TYPE AS data_type,
                              IS_NULLABLE AS is_nullable,
                              COLUMN_DEFAULT AS column_default,
                              COLUMN_KEY AS column_key,
                              COLUMN_COMMENT AS comment,
                              ORDINAL_POSITION AS ordinal_position
                       FROM information_schema.COLUMNS
                       WHERE TABLE_SCHEMA = %s
                       ORDER BY TABLE_NAME, ORDINAL_POSITION""",
                    (self.schema,),
                )
                column_rows = cursor.fetchall()
                cursor.execute(
                    """SELECT TABLE_NAME AS table_name,
                              CONSTRAINT_NAME AS constraint_name,
                              COLUMN_NAME AS from_column,
                              REFERENCED_TABLE_SCHEMA AS to_schema,
                              REFERENCED_TABLE_NAME AS to_table,
                              REFERENCED_COLUMN_NAME AS to_column,
                              ORDINAL_POSITION AS ordinal_position
                       FROM information_schema.KEY_COLUMN_USAGE
                       WHERE TABLE_SCHEMA = %s
                         AND REFERENCED_TABLE_NAME IS NOT NULL
                       ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION""",
                    (self.schema,),
                )
                foreign_key_rows = cursor.fetchall()
                cursor.execute(
                    """SELECT TABLE_NAME AS table_name,
                              INDEX_NAME AS index_name,
                              NON_UNIQUE AS non_unique,
                              COLUMN_NAME AS column_name,
                              SEQ_IN_INDEX AS seq_in_index
                       FROM information_schema.STATISTICS
                       WHERE TABLE_SCHEMA = %s
                       ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX""",
                    (self.schema,),
                )
                index_rows = cursor.fetchall()
                cursor.execute("SELECT VERSION() AS version")
                version_row = cursor.fetchone() or {}
        except Exception as exc:
            raise ConnectorError(sanitize_error(str(exc), (self._password,))) from exc
        finally:
            conn.close()

        columns_by_table: dict[str, list[ColumnMeta]] = {}
        for row in column_rows:
            columns_by_table.setdefault(row["table_name"], []).append(
                ColumnMeta(
                    name=row["column_name"],
                    data_type=row.get("column_type") or row.get("data_type") or "TEXT",
                    nullable=(str(row.get("is_nullable", "YES")).upper() == "YES"),
                    default=row.get("column_default"),
                    is_primary_key=(str(row.get("column_key", "")).upper() == "PRI"),
                    comment=row.get("comment") or None,
                )
            )

        foreign_keys: dict[str, list[ForeignKeyMeta]] = {}
        for row in foreign_key_rows:
            foreign_keys.setdefault(row["table_name"], []).append(
                ForeignKeyMeta(
                    from_table=row["table_name"],
                    from_column=row["from_column"],
                    to_table=row["to_table"],
                    to_column=row["to_column"],
                    to_schema=row.get("to_schema"),
                    constraint_name=row.get("constraint_name"),
                )
            )

        indexes: dict[str, dict[str, IndexMeta]] = {}
        for row in index_rows:
            index_map = indexes.setdefault(row["table_name"], {})
            name = row["index_name"]
            meta = index_map.get(name)
            if meta is None:
                meta = IndexMeta(name=name, columns=[], unique=not bool(row.get("non_unique")))
                index_map[name] = meta
            meta.columns.append(row["column_name"])

        tables: list[TableMeta] = []
        for row in table_rows:
            name = row["table_name"]
            raw_count = row.get("row_count")
            row_count = int(raw_count) if raw_count is not None else None
            raw_type = str(row.get("table_type") or "BASE TABLE").upper()
            tables.append(
                TableMeta(
                    schema=row.get("table_schema") or self.schema,
                    name=name,
                    table_type="view" if "VIEW" in raw_type else "table",
                    row_count=row_count,
                    row_count_estimated=row_count is not None,
                    comment=row.get("comment") or None,
                    columns=columns_by_table.get(name, []),
                    foreign_keys=foreign_keys.get(name, []),
                    indexes=list(indexes.get(name, {}).values()),
                )
            )

        return MetadataSnapshot(
            kind=self.kind,
            server_version=str(version_row.get("version")) if version_row.get("version") else None,
            tables=tables,
        )

    def sample_rows(self, table: TableMeta, limit: int = 5) -> list[dict[str, Any]]:
        column_names = [column.name for column in table.columns]
        if not column_names:
            return []
        safe_limit = max(1, min(int(limit or 5), 1000))
        selected = ", ".join(quote_ident(name, "mysql") for name in column_names)
        sql = (
            f"SELECT {selected} FROM "
            f"{quote_ident(table.schema, 'mysql')}.{quote_ident(table.name, 'mysql')} "
            "LIMIT %s"
        )
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, (safe_limit,))
                rows = cursor.fetchall()
        except Exception as exc:
            raise ConnectorError(sanitize_error(str(exc), (self._password,))) from exc
        finally:
            conn.close()
        return [
            {name: json_safe(value) for name, value in dict(row).items()}
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
        """只读游标分批读取；标识符反引号引用，水位值参数化。"""
        column_names = [column.name for column in table.columns]
        if not column_names:
            return
        safe_batch = max(1, int(batch_size or 1000))
        safe_max = max(1, int(max_rows or 1))
        selected = ", ".join(quote_ident(name, "mysql") for name in column_names)
        table_sql = (
            f"{quote_ident(table.schema or self.schema, 'mysql')}."
            f"{quote_ident(table.name, 'mysql')}"
        )
        where = ""
        params: tuple[Any, ...] = ()
        if watermark_column:
            where = f" WHERE {quote_ident(watermark_column, 'mysql')} >= %s"
            params = (watermark_value,)
        query = f"SELECT {selected} FROM {table_sql}{where}"
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                yielded = 0
                while yielded < safe_max:
                    rows = cursor.fetchmany(min(safe_batch, safe_max - yielded))
                    if not rows:
                        break
                    yield [
                        {name: json_safe(value) for name, value in dict(row).items()}
                        for row in rows
                    ]
                    yielded += len(rows)
        except Exception as exc:
            raise ConnectorError(sanitize_error(str(exc), (self._password,))) from exc
        finally:
            conn.close()

    def close(self) -> None:
        """连接按需创建并立即关闭，无需额外清理。"""
