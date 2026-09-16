from __future__ import annotations

from typing import Iterable

from sqlalchemy import (
    BigInteger,
    func,
    select,
    Column,
    DateTime,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import Engine

from rag_discover.models import ManifestEntry
from rag_discover.settings import get_settings

metadata = MetaData()

# Mirrors ../postgres/sql/001_discover.sql (authoritative for the schema).
ingest_manifest = Table(
    "ingest_manifest",
    metadata,
    # GENERATED ALWAYS AS IDENTITY in the DDL: never supplied on insert.
    Column("manifest_id", BigInteger, primary_key=True),
    Column("run_id", Text, nullable=False),
    Column("source_system", Text, nullable=False),
    Column("doc_url", Text, nullable=False),
    Column("source_id", Text, nullable=False),
    Column("title", Text),
    Column("published_at", DateTime(timezone=True)),
    Column("doc_version", Text),
    Column("size_bytes", BigInteger),
    Column("extra", JSONB, nullable=False),
    Column("execution_ts", DateTime(timezone=True), nullable=False),
    # the natural key; discover upserts on it so a re-run of one run updates
    # its sighting instead of minting a second manifest_id
    UniqueConstraint("run_id", "source_system", "doc_url"),
)

# source_id is deliberately not part of the key: the same document listed by
# two sources of one source_system (e.g. a release in both ECB feeds) is one
# document and must not be parsed/embedded twice (dedup). Last writer
# wins on source_id/title/extra, so per-source counts can sum above row count.
_KEY = ("run_id", "source_system", "doc_url")


def get_engine(dsn: str | None = None) -> Engine:
    return create_engine(dsn or get_settings().pg_dsn)


def deduplicate_entries(entries: Iterable[ManifestEntry]) -> list[ManifestEntry]:
    """Keep the last entry per manifest key — a feed occasionally lists the
    same URL twice, and ON CONFLICT cannot update the same row twice within
    one INSERT statement."""
    by_key = {(e.run_id, e.source_system, e.doc_url): e for e in entries}
    return list(by_key.values())


def upsert_manifest(engine: Engine, entries: Iterable[ManifestEntry]) -> int:
    rows = [e.model_dump() for e in deduplicate_entries(entries)]
    if not rows:
        return 0
    stmt = insert(ingest_manifest)
    stmt = stmt.on_conflict_do_update(
        index_elements=list(_KEY),
        # only columns actually supplied: manifest_id is GENERATED ALWAYS, so
        # it is absent from the rows and must keep its value on re-discovery
        set_={
            c.name: stmt.excluded[c.name]
            for c in ingest_manifest.columns
            if c.name not in _KEY and c.name in rows[0]
        },
    )
    with engine.begin() as conn:
        conn.execute(stmt, rows)
    return len(rows)


def count_manifest_rows(engine: Engine, run_id: str) -> int:
    """Rows actually stored in ingest_manifest for this run."""
    with engine.connect() as conn:
        return conn.execute(
            select(func.count()).select_from(ingest_manifest).where(ingest_manifest.c.run_id == run_id)
        ).scalar_one()
