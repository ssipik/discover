from __future__ import annotations

import logging
from calendar import timegm
from datetime import datetime, timezone
from time import struct_time
from typing import Iterator

import feedparser
import httpx

from rag_discover.models import ManifestEntry
from rag_discover.readers.base import BaseReader
from rag_discover.settings import get_settings

logger = logging.getLogger(__name__)


def _to_datetime(t: struct_time | None) -> datetime | None:
    if t is None:
        return None
    return datetime.fromtimestamp(timegm(t), tz=timezone.utc)


class RssReader(BaseReader):
    """RSS/Atom feed discovery. feedparser is format-agnostic (RSS 2.0/1.0,
    Atom), so this reader works for any feed source, not just ECB.

    Feeds like ECB press are a rolling window of the newest ~15 items:
    an item disappearing from the feed does not mean the document was
    deleted. Such sources carry detect_deletions: false in sources.yaml.
    """

    def discover(self, run_id: str) -> Iterator[ManifestEntry]:
        settings = get_settings()
        response = httpx.get(
            self.config.url,
            headers={"User-Agent": settings.user_agent},
            timeout=settings.http_timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        yield from self.parse_feed(response.content, run_id)

    def parse_feed(self, raw: bytes, run_id: str) -> Iterator[ManifestEntry]:
        feed = feedparser.parse(raw)
        if not feed.entries and (feed.bozo or not feed.version):
            raise ValueError(
                f"unparseable feed for source {self.config.id!r} "
                f"({self.config.url}): {getattr(feed, 'bozo_exception', 'not a feed')}"
            )
        if not feed.entries:
            logger.warning("feed %s is valid but empty", self.config.id)
        for entry in feed.entries:
            link = entry.get("link")
            if not link:
                logger.warning("skipping entry without link in %s", self.config.id)
                continue
            guid = entry.get("id")
            published = _to_datetime(
                entry.get("published_parsed") or entry.get("updated_parsed")
            )
            version_parts = [
                p
                for p in (guid, published.isoformat() if published else None)
                if p
            ]
            yield ManifestEntry(
                run_id=run_id,
                source_system=self.config.source_system,
                source_id=self.config.id,
                doc_url=link,
                title=entry.get("title"),
                published_at=published,
                doc_version="|".join(version_parts) or None,
                extra={
                    "guid": guid,
                    "feed_url": self.config.url,
                    "doc_type": self.config.doc_type,
                    "default_language": self.config.default_language,
                },
            )
