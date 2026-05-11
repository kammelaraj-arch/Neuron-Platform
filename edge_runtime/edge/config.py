from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EDGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_url: str = "sqlite+aiosqlite:///./data/edge.db"
    policy_path: Path = Field(default=Path("./policies/edge_default.json"))
    bind_host: str = "0.0.0.0"
    bind_port: int = 8090

    edge_id: str = "edge-default"
    site_id: str = "site-default"
    parent_node_url: str = "https://neuron.shital.org.uk"

    # Push twin updates to the Master the moment they change so the Master's
    # browser SSE delivery has zero polling lag. Set both env vars to enable.
    master_push_url: str = ""           # e.g. https://neuron.shital.org.uk
    master_push_api_key: str = ""       # an X-API-Key with devices:write scope

    tls_enabled: bool = False
    tls_cert_path: Path = Field(default=Path("./certs/edge.crt"))
    tls_key_path: Path = Field(default=Path("./certs/edge.key"))
    tls_ca_path: Path = Field(default=Path("./certs/parent_ca.crt"))

    # Local Mosquitto broker — Pico devices publish/subscribe directly,
    # the Edge bridges its twin_cache to MQTT. Empty MQTT_HOST disables
    # the bridge and the Edge keeps using REST + SSE only.
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_client_id: str = "neuron-edge"

    log_level: str = "info"


settings = Settings()
