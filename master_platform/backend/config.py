from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Master Platform."""

    model_config = SettingsConfigDict(
        env_prefix="NEURON_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_url: str = "sqlite+aiosqlite:///./data/neuron.db"
    libraries_dir: Path = Field(default=Path("../libraries"))
    shared_schemas_dir: Path = Field(default=Path("../shared_schemas"))
    build_artifacts_dir: Path = Field(default=Path("./build_artifacts"))
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080
    log_level: str = "info"
    # Audit retention — events older than this many days are pruned hourly.
    audit_keep_days: int = 90


settings = Settings()
