"""PostgreSQL 连接器：只读会话、pg_catalog 元数据与样本。"""

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
)
from app.core.crypto import sanitize_error

_TABLES_SQL = """
SELECT n.nspname AS schema,
       c.relname AS name,
       CASE c.relkind WHEN 'v' THEN 'view' WHEN 'm' THEN 'view' ELSE 'table' END AS table_type,
       c.reltuples::bigint AS row_count,
       obj_description(c.oid, 'pg_class') AS comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'v', 'm')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY n.nspname, c.relname
"""

_COLUMNS_SQL = """
SELECT c.table_schema AS schema,
       c.table_name AS table_name,
       c.column_name AS column_name,
       c.data_type AS data_type,
       c.is_nullable AS is_nullable,
       c.column_default AS column_default,
       c.ordinal_position AS ordinal_position
FROM information_schema.columns c
WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""

_COLUMN_COMMENTS_SQL = """
SELECT c.table_schema AS schema,
       c.table_name AS table_name,
       c.column_name AS column_name,
       col_description(
           format('%I.%I', c.table_schema, c.table_name)::regclass::oid,
           c.ordinal_position
       ) AS comment
FROM information_schema.columns c
WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
"""

_PRIMARY_KEYS_SQL = """
SELECT n.nspname AS schema,
       c.relname AS table_name,
       a.attname AS column_name
FROM pg_index i
JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY (i.indkey)
WHERE i.indisprimary
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY n.nspname, c.relname, a.attnum
"""

_FOREIGN_KEYS_SQL = """
SELECT n.nspname AS schema,
       c.relname AS table_name,
       a.attname AS from_column,
       fn.nspname AS to_schema,
       fc.relname AS to_table,
       fa.attname AS to_column,
       con.conname AS constraint_name
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_class fc ON fc.oid = con.confrelid
JOIN pg_namespace fn ON fn.oid = fc.relnamespace
JOIN LATERAL unnest(con.conkey, con.confkey) WITH ORDINALITY AS k(from_attnum, to_attnum, ord) ON TRUE
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.from_attnum
JOIN pg_attribute fa ON fa.attrelid = fc.oid AND fa.attnum = k.to_attnum
WHERE con.contype = 'f'
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY n.nspname, c.relname, con.conname, k.ord
"""

_INDEXES_SQL = """
SELECT n.nspname AS schema,
       c.relname AS table_name,
       ic.relname AS index_name,
       i.indisunique AS is_unique,
       a.attname AS column_name,
       k.ord AS ord
FROM pg_index i
JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_class ic ON ic.oid = i.indexrelid
JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY n.nspname, c.relname, ic.relname, k.ord
"""


class PostgresConnector:
    """通过 psycopg 只读会话访问 PostgreSQL。"""

    kind = "postgresql"

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        schema: str = "public",
    ) -> None:
        self.host = host
        self.port = int(port)
        self.database = database
        self.username = username
        self._password = password
        self.schema = schema or "public"

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise ConnectorError("PostgreSQL 驱动未安装") from exc
        try:
            return psycopg.connect(
                host=self.host,
                port=self.port,
                dbname=self.database,
                user=self.username,
                password=self._password,
                connect_timeout=5,
                autocommit=True,
                row_factory=dict_row,
                options="-c statement_timeout=5000 -c default_transaction_read_only=on",
            )
        except Exception as exc:
            raise ConnectorError(sanitize_error(str(exc), (self._password,))) from exc

    def test(self) -> TestResult:
        started = time.perf_counter()
        conn = self._connect()
        try:
            row = conn.execute("SELECT version() AS server_version").fetchone()
            version = row["server_version"] if row else None
            count_row = conn.execute(
                """SELECT COUNT(*) AS n
                   FROM pg_class c
                   JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE c.relkind IN ('r', 'p')
                     AND n.nspname NOT IN ('pg_catalog', 'information_schema')"""
            ).fetchone()
            latest_period = None
            try:
                exists = conn.execute("SELECT to_regclass('dim_period') AS reg").fetchone()
                if exists and exists.get("reg"):
                    latest = conn.execute(
                        "SELECT MAX(period_code) AS latest FROM dim_period"
                    ).fetchone()
                    latest_period = latest.get("latest") if latest else None
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
            table_count=int(count_row["n"]) if count_row else 0,
            latest_period=latest_period,
            message="PostgreSQL 连接成功",
        )

    def inspect(self) -> MetadataSnapshot:
        conn = self._connect()
        try:
            table_rows = conn.execute(_TABLES_SQL).fetchall()
            column_rows = conn.execute(_COLUMNS_SQL).fetchall()
            comment_rows = conn.execute(_COLUMN_COMMENTS_SQL).fetchall()
            pk_rows = conn.execute(_PRIMARY_KEYS_SQL).fetchall()
            fk_rows = conn.execute(_FOREIGN_KEYS_SQL).fetchall()
            index_rows = conn.execute(_INDEXES_SQL).fetchall()
            version_row = conn.execute("SELECT version() AS server_version").fetchone()
        except Exception as exc:
            raise ConnectorError(sanitize_error(str(exc), (self._password,))) from exc
        finally:
            conn.close()

        column_comments = {
            (row["schema"], row["table_name"], row["column_name"]): row.get("comment")
            for row in comment_rows
        }
        primary_keys: dict[tuple[str, str], set[str]] = {}
        for row in pk_rows:
            primary_keys.setdefault((row["schema"], row["table_name"]), set()).add(
                row["column_name"]
            )

        columns_by_table: dict[tuple[str, str], list[ColumnMeta]] = {}
        for row in column_rows:
            key = (row["schema"], row["table_name"])
            columns_by_table.setdefault(key, []).append(
                ColumnMeta(
                    name=row["column_name"],
                    data_type=row.get("data_type") or "TEXT",
                    nullable=(str(row.get("is_nullable", "YES")).upper() == "YES"),
                    default=row.get("column_default"),
                    is_primary_key=row["column_name"] in primary_keys.get(key, set()),
                    comment=column_comments.get(key + (row["column_name"],)),
                )
            )

        foreign_keys: dict[tuple[str, str], list[ForeignKeyMeta]] = {}
        for row in fk_rows:
            key = (row["schema"], row["table_name"])
            foreign_keys.setdefault(key, []).append(
                ForeignKeyMeta(
                    from_table=row["table_name"],
                    from_column=row["from_column"],
                    to_table=row["to_table"],
                    to_column=row["to_column"],
                    to_schema=row.get("to_schema"),
                    constraint_name=row.get("constraint_name"),
                )
            )

        indexes: dict[tuple[str, str], dict[str, IndexMeta]] = {}
        for row in index_rows:
            key = (row["schema"], row["table_name"])
            index_map = indexes.setdefault(key, {})
            name = row["index_name"]
            meta = index_map.get(name)
            if meta is None:
                meta = IndexMeta(name=name, columns=[], unique=bool(row.get("is_unique")))
                index_map[name] = meta
            meta.columns.append(row["column_name"])

        tables: list[TableMeta] = []
        for row in table_rows:
            key = (row["schema"], row["name"])
            raw_count = row.get("row_count")
            row_count = int(raw_count) if raw_count is not None and int(raw_count) >= 0 else None
            tables.append(
                TableMeta(
                    schema=row["schema"],
                    name=row["name"],
                    table_type=row.get("table_type") or "table",
                    row_count=row_count,
                    row_count_estimated=row_count is not None,
                    comment=row.get("comment"),
                    columns=columns_by_table.get(key, []),
                    foreign_keys=foreign_keys.get(key, []),
                    indexes=list(indexes.get(key, {}).values()),
                )
            )

        return MetadataSnapshot(
            kind=self.kind,
            server_version=(
                str(version_row["server_version"])
                if version_row and version_row.get("server_version")
                else None
            ),
            tables=tables,
        )

    def sample_rows(self, table: TableMeta, limit: int = 5) -> list[dict[str, Any]]:
        column_names = [column.name for column in table.columns]
        if not column_names:
            return []
        safe_limit = max(1, min(int(limit or 5), 1000))
        try:
            from psycopg import sql
        except ImportError as exc:
            raise ConnectorError("PostgreSQL 驱动未安装") from exc
        query = sql.SQL("SELECT {} FROM {} LIMIT %s").format(
            sql.SQL(", ").join(sql.Identifier(name) for name in column_names),
            sql.Identifier(table.schema, table.name),
        )
        conn = self._connect()
        try:
            rows = conn.execute(query, (safe_limit,)).fetchall()
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
        """只读游标分批读取；标识符与水位值均安全处理。"""
        column_names = [column.name for column in table.columns]
        if not column_names:
            return
        try:
            from psycopg import sql
        except ImportError as exc:
            raise ConnectorError("PostgreSQL 驱动未安装") from exc
        safe_batch = max(1, int(batch_size or 1000))
        safe_max = max(1, int(max_rows or 1))
        columns_sql = sql.SQL(", ").join(sql.Identifier(name) for name in column_names)
        table_sql = sql.Identifier(table.schema or self.schema, table.name)
        if watermark_column:
            query = sql.SQL("SELECT {cols} FROM {table} WHERE {wm} >= %s").format(
                cols=columns_sql,
                table=table_sql,
                wm=sql.Identifier(watermark_column),
            )
            params: tuple[Any, ...] = (watermark_value,)
        else:
            query = sql.SQL("SELECT {cols} FROM {table}").format(
                cols=columns_sql,
                table=table_sql,
            )
            params = ()
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
