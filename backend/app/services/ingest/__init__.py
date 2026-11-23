"""数据同步与元数据目录服务包。"""

from app.services.ingest.store import (
    STAGING_SCHEMA_VERSION,
    ensure_staging_available,
    init_staging,
    staging_connection,
    staging_now,
)
from app.services.ingest.writers import (
    create_table,
    drop_orphan_temp_tables,
    drop_table_if_exists,
    insert_rows,
    row_key,
    sqlite_affinity,
    staging_name_for,
    swap_table,
    table_max_value,
    table_row_count,
)

__all__ = [
    "STAGING_SCHEMA_VERSION",
    "create_table",
    "drop_orphan_temp_tables",
    "drop_table_if_exists",
    "ensure_staging_available",
    "init_staging",
    "insert_rows",
    "row_key",
    "sqlite_affinity",
    "staging_connection",
    "staging_name_for",
    "staging_now",
    "swap_table",
    "table_max_value",
    "table_row_count",
]
