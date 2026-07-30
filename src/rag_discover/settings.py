from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_PG_DSN = "postgresql+psycopg://rag:rag@localhost:5433/rag_ingestion"
DEFAULT_USER_AGENT = "rag-pipeline-ingest/0.1"


@dataclass(frozen=True)
class Settings:
    pg_dsn: str
    http_timeout: float
    user_agent: str
    # Confluence Cloud basic auth (email + API token). Kept out of sources.yaml,
    # which is committed and baked into the images; Vault supplies these on the
    # cluster (ARCHITECTURE.md → Deployment). None until a confluence source runs.
    confluence_email: str | None
    confluence_api_token: str | None


def get_settings() -> Settings:
    return Settings(
        pg_dsn=os.environ.get("RAG_PG_DSN", DEFAULT_PG_DSN),
        http_timeout=float(os.environ.get("RAG_HTTP_TIMEOUT", "30")),
        user_agent=os.environ.get("RAG_HTTP_USER_AGENT", DEFAULT_USER_AGENT),
        confluence_email=os.environ.get("RAG_CONFLUENCE_EMAIL"),
        confluence_api_token=os.environ.get("RAG_CONFLUENCE_API_TOKEN"),
    )
