"""Optional PostgreSQL storage for collected Drone metadata."""

from __future__ import annotations

import json
import os
from typing import Optional


def database_url() -> Optional[str]:
    value = os.getenv("OVERMIND_DATABASE_URL") or os.getenv("DATABASE_URL")
    if value:
        return value
    host = os.getenv("OVERMIND_POSTGRES_HOST")
    if not host:
        return None
    user = os.getenv("OVERMIND_POSTGRES_USER", "overmind")
    password = os.getenv("OVERMIND_POSTGRES_PASSWORD", "overmind")
    database = os.getenv("OVERMIND_POSTGRES_DB", "overmind")
    port = os.getenv("OVERMIND_POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


class PostgresMetadataStore:
    """Tiny optional store; the app still works when PostgreSQL is absent."""

    def __init__(self) -> None:
        self.url = database_url()
        self._ready = False

    def _connect(self):
        if not self.url:
            return None
        try:
            import psycopg
        except Exception:
            return None
        return psycopg.connect(self.url)

    def ensure_schema(self) -> None:
        if self._ready or not self.url:
            return
        conn = self._connect()
        if conn is None:
            return
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS drone_action_results (
                        id BIGSERIAL PRIMARY KEY,
                        device_id TEXT NOT NULL,
                        action_id TEXT,
                        result_type TEXT,
                        result JSONB NOT NULL,
                        received_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
        self._ready = True

    def store_action_result(self, device_id: str, action_id: str, result: dict) -> None:
        if not self.url:
            return
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO drone_action_results (device_id, action_id, result_type, result)
                    VALUES (%s, %s, %s, %s::jsonb)
                    """,
                    (device_id, action_id, result.get("type"), json.dumps(result)),
                )


postgres_store = PostgresMetadataStore()
