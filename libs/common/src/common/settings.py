"""Centralized settings loaded from environment.

All services read configuration from the same set of environment
variables so behavior is consistent in dev and production. Defaults
match the docker-compose stack so the gateway works out of the box
on a fresh `make up`.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Gateway settings. Sourced from environment, no .env required."""

    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    # --- Postgres -----------------------------------------------------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "gateway"
    postgres_user: str = "gateway"
    postgres_password: str = "gateway"

    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # --- MinIO --------------------------------------------------------
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "gateway"
    minio_secret_key: str = "gateway-dev-password"
    minio_secure: bool = False
    minio_bucket_raw: str = "raw-captures"

    # --- NATS ---------------------------------------------------------
    nats_url: str = "nats://localhost:4222"

    # --- Listeners ----------------------------------------------------
    # Where each protocol listener binds. Empty = disabled.
    cot_xml_tcp_port: int = 8087     # TAK plain TCP port for CoT XML
    cot_xml_udp_port: int = 0        # UDP multicast/unicast for CoT XML (0=off)
    cot_xml_udp_group: str = ""      # multicast group; empty = unicast bind
    cot_pb_tcp_port: int = 8089      # TAK protobuf stream port

    # --- Logging ------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True

    # --- Routes config ------------------------------------------------
    routes_config_path: str = "/etc/gateway/routes.yaml"
    
    # --- KMZ ingest (Phase 2a) ----------------------------------------
    # Folder watcher inbox path. Empty/None disables the watcher;
    # POST /ingest/kmz still works.
    kmz_inbox_path: str = ""
    kmz_inbox_poll_interval_s: float = 2.0
    kmz_max_upload_bytes: int = 50 * 1024 * 1024  # 50 MiB

    # --- Opstore (Phase 2a) -------------------------------------------
    # NATS subject the opstore subscribes to
    opstore_subject: str = "cto.normalized.>"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
