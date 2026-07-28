from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class ManifestEntry(BaseModel):
    """One discovered document — the discover stage's output contract
    (ARCHITECTURE.md → Ingestion orchestration).

    Discovery is listings-only: an entry carries identifiers and cheap
    change signals, never document content. Raw bytes are fetched in
    the parse_chunk stage, after the delta check has discarded unchanged docs.
    """

    run_id: str
    source_system: str
    source_id: str
    doc_url: str
    title: str | None = None
    published_at: datetime | None = None
    # Sole pre-parsing change token: a source-specific opaque version —
    # RSS guid|pubDate, git commit SHA, Confluence version, HTTP ETag, file
    # mtime|size. Delta compares only this; None ⇒ not comparable ⇒ changed.
    doc_version: str | None = None
    # Cost proxy for the plan stage's batch balancing; not a change signal.
    size_bytes: int | None = None
    extra: dict = Field(default_factory=dict)
    # When this run saw the item (named after Airflow's `ts` context variable;
    # its ::date is the run's execution date for testing/validation).
    execution_ts: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
