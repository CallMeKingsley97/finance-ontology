"""核心配置。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> parents[3] = finance-ontology-mvp
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "finance.db"


class Settings(BaseSettings):
    """应用配置，全部可通过环境变量覆盖。"""

    database_path: str = ""
    backend_url: str = "http://127.0.0.1:8000"
    openai_base_url: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None
    llm_timeout_seconds: float = 15.0

    # ===== 多数据源接入 =====
    sources_database_path: str = ""
    upload_dir: str = ""
    source_secret_key: str = ""
    source_allowed_hosts: str = ""
    source_metadata_ttl_seconds: int = 300
    source_max_tables: int = 200
    source_max_columns: int = 500
    source_max_metadata_bytes: int = 2_000_000
    source_max_csv_bytes: int = 52_428_800
    source_max_excel_bytes: int = 20_971_520
    source_max_sqlite_bytes: int = 104_857_600
    source_sample_limit: int = 20

    # ===== 数据同步与 staging =====
    staging_database_path: str = ""
    staging_batch_size: int = 1000
    staging_max_rows_per_table: int = 200_000
    staging_max_bytes_per_table: int = 67_108_864
    staging_max_retries: int = 3
    staging_retry_backoff_seconds: float = 1.0
    staging_run_stale_seconds: int = 300
    staging_run_retention_days: int = 180
    staging_max_batch_sources: int = 50
    sync_service_token: str = ""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def db_path(self) -> Path:
        """数据库绝对路径；DATABASE_PATH 可覆盖，且与启动目录无关。"""
        raw = (self.database_path or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return DEFAULT_DB_PATH

    @property
    def llm_enabled(self) -> bool:
        """三个 OPENAI_* 变量同时存在才启用 LLM。"""
        return bool(self.openai_base_url and self.openai_api_key and self.openai_model)

    @property
    def sources_db_path(self) -> Path:
        """数据源注册库路径；未显式配置时与业务库同目录，便于测试隔离。"""
        raw = (self.sources_database_path or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return self.db_path.parent / "sources.db"

    @property
    def uploads_dir(self) -> Path:
        """上传文件根目录；未显式配置时位于注册库同目录。"""
        raw = (self.upload_dir or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return self.sources_db_path.parent / "uploads"

    @property
    def source_key_path(self) -> Path:
        """Fernet 密钥文件路径。"""
        return self.sources_db_path.parent / ".source_key"

    @property
    def staging_db_path(self) -> Path:
        """staging 库路径；未显式配置时与业务库同目录，便于测试隔离。"""
        raw = (self.staging_database_path or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return self.db_path.parent / "staging.db"

    @property
    def allowed_source_hosts(self) -> list[str]:
        """可选的外部数据库主机白名单；为空表示不限制。"""
        return [
            item.strip().lower()
            for item in (self.source_allowed_hosts or "").split(",")
            if item.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
