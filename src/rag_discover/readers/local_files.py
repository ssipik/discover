from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from rag_discover.models import ManifestEntry
from rag_discover.readers.base import BaseReader

logger = logging.getLogger(__name__)


class LocalFilesReader(BaseReader):
    """Discovery over a local directory (e.g. a mounted share of PDFs/DOCX).

    Lists files under ``config.path`` matching ``config.glob`` and yields one
    ManifestEntry per file. Listings only, as the discover stage's contract
    requires: it calls ``stat()`` for the pre-parsing change signal (mtime +
    size) but never reads file content — the parse_chunk stage fetches the
    raw bytes.

    A directory scan is a *complete* snapshot (unlike a rolling-window feed),
    so a file's absence from a run is a real deletion signal: such sources
    carry ``detect_deletions: true`` in sources.yaml.

    ``doc_url`` is a ``file:///`` URI built from the path *relative* to
    ``config.path``, so it stays stable across checkouts and host/container
    mounts (the same file keeps one manifest identity regardless of where the
    directory lives). The parse_chunk stage resolves it back against ``config.path``.
    """

    def discover(self, run_id: str) -> Iterator[ManifestEntry]:
        if not self.config.path:
            raise ValueError(
                f"local_files source {self.config.id!r} has no 'path' configured"
            )
        base = Path(self.config.path)
        if not base.is_dir():
            raise ValueError(
                f"local_files source {self.config.id!r}: path {base!s} "
                f"does not exist or is not a directory"
            )

        found = False
        for path in sorted(base.glob(self.config.glob)):
            if not path.is_file():
                continue
            found = True
            stat = path.stat()
            rel = path.relative_to(base).as_posix()
            last_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            yield ManifestEntry(
                run_id=run_id,
                source_system=self.config.source_system,
                source_id=self.config.id,
                doc_url=f"file:///{rel}",
                title=path.name,
                size_bytes=stat.st_size,
                # the pre-parsing check: mtime|size is the sole change token.
                doc_version=f"{last_modified.isoformat()}|{stat.st_size}",
                extra={
                    "filename": path.name,
                    "rel_path": rel,
                    "doc_type": self.config.doc_type,
                    "default_language": self.config.default_language,
                },
            )
        if not found:
            logger.warning(
                "local_files source %s matched no files (%s / %r)",
                self.config.id,
                base,
                self.config.glob,
            )
