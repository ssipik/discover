from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import Engine

from rag_discover.db import get_engine, upsert_manifest
from rag_discover.readers import get_reader
from rag_discover.sources import load_sources

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryReport:
    run_id: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def run_discovery(
    config_path: str | Path,
    source_ids: list[str] | None = None,
    run_id: str | None = None,
    engine: Engine | None = None,
) -> DiscoveryReport:
    """DISCOVER stage: list documents per source, upsert manifest rows.

    This is the function Airflow tasks / Nomad jobs invoke later; callers
    pass their run identifier (e.g. the Airflow run_id) so manifest rows
    tie back to the orchestrated run. Per the stage contract, only counts
    are returned — never document content.
    """
    sources = load_sources(config_path)
    if source_ids is None:
        selected = list(sources.values())
    else:
        missing = sorted(set(source_ids) - set(sources))
        if missing:
            raise ValueError(f"unknown source(s) {missing}; known: {sorted(sources)}")
        selected = [sources[sid] for sid in source_ids]

    report = DiscoveryReport(
        run_id=run_id
        or "local-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    engine = engine or get_engine()
    for cfg in selected:
        entries = list(get_reader(cfg).discover(report.run_id))
        report.counts[cfg.id] = upsert_manifest(engine, entries)
        logger.info("%s: %s manifest rows", cfg.id, report.counts[cfg.id])
    return report
