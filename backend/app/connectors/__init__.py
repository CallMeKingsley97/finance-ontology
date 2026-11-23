"""数据源连接器集合。"""

from app.connectors.base import (
    ColumnMeta,
    Connector,
    ConnectorError,
    ForeignKeyMeta,
    IndexMeta,
    MetadataSnapshot,
    TableMeta,
    TestResult,
    infer_text_type,
    json_safe,
    quote_ident,
    truncate_metadata,
)
from app.connectors.files import CsvConnector, ExcelConnector
from app.connectors.mysql import MySQLConnector
from app.connectors.postgres import PostgresConnector
from app.connectors.sqlite import SQLiteFileConnector

__all__ = [
    "ColumnMeta",
    "Connector",
    "ConnectorError",
    "CsvConnector",
    "ExcelConnector",
    "ForeignKeyMeta",
    "IndexMeta",
    "MetadataSnapshot",
    "MySQLConnector",
    "PostgresConnector",
    "SQLiteFileConnector",
    "TableMeta",
    "TestResult",
    "infer_text_type",
    "json_safe",
    "quote_ident",
    "truncate_metadata",
]
