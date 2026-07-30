from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import quote

import httpx

from rag_discover.models import ManifestEntry
from rag_discover.readers.base import BaseReader
from rag_discover.settings import get_settings

logger = logging.getLogger(__name__)

# Confluence Cloud caps v2 page size at 250; the v1 search endpoint at 100.
SEARCH_LIMIT = 100
ATTACHMENT_LIMIT = 250
# 429 is Confluence's backpressure signal (ARCHITECTURE.md names Confluence as
# the rate-limited source); past this many retries it is a real problem.
MAX_RETRIES = 5
DEFAULT_RETRY_AFTER = 5.0

# Attachments are deferred to a later iteration (decided 2026-07-30, see
# ../ARCHITECTURE.md block 1): the first corpus is text documents only. The
# listing code below stays, but discover() does not call it, so no attachment
# reaches ingest_manifest and nothing downstream sees one.
INCLUDE_ATTACHMENTS_DEFERRED = True

# Attachment media types stage 4 has a parser for (its PARSER_REGISTRY) — the
# first gate for when attachments come back. Pages carry pasted screenshots,
# and without this every one of them becomes a document that stage 4 downloads
# and then fails on as an unsupported content type, on every batch re-run.
PARSEABLE_MEDIA_TYPES = frozenset(
    {"application/pdf", "text/html", "application/xhtml+xml"}
)


class ConfluenceReader(BaseReader):
    """Confluence Cloud discovery: one source = one space + one label.

    Labelling a page is how someone opts it into the corpus
    (`scripts/label_confluence_pages.py` labels a whole page tree). The listing
    is therefore complete on every run, so such sources carry
    detect_deletions: true — and removing a label takes the page out of the
    corpus exactly like deleting it.

    Pages only for now: attachments are deferred (INCLUDE_ATTACHMENTS_DEFERRED).

    Listings only, as the stage's contract requires: this reads titles and
    version numbers, never a page body — the parse_chunk stage fetches those.

    doc_url is built from the page id, not the title, so a rename keeps the
    document's identity (ARCHITECTURE.md defers a separate stable doc_id).

    The attachment code below is kept for that later iteration: it costs one
    extra call per page (no Cloud endpoint lists the attachments of a CQL
    result set in one go) and lists only the media types stage 4 has a parser
    for (PARSEABLE_MEDIA_TYPES).
    """

    def discover(self, run_id: str) -> Iterator[ManifestEntry]:
        cfg = self.config
        for field in ("base_url", "space_key", "label"):
            if not getattr(cfg, field):
                raise ValueError(
                    f"confluence source {cfg.id!r} has no {field!r} configured"
                )
        settings = get_settings()
        if not settings.confluence_email or not settings.confluence_api_token:
            raise ValueError(
                f"confluence source {cfg.id!r} needs RAG_CONFLUENCE_EMAIL and "
                f"RAG_CONFLUENCE_API_TOKEN"
            )

        with httpx.Client(
            auth=(settings.confluence_email, settings.confluence_api_token),
            timeout=settings.http_timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": settings.user_agent,
            },
            follow_redirects=True,
        ) as client:
            pages = self._search_pages(client)
            if not pages:
                logger.warning(
                    "confluence source %s matched no pages (space %s, label %s)",
                    cfg.id,
                    cfg.space_key,
                    cfg.label,
                )
            if cfg.include_attachments and INCLUDE_ATTACHMENTS_DEFERRED:
                logger.warning(
                    "confluence source %s sets include_attachments, but "
                    "attachments are deferred — listing pages only",
                    cfg.id,
                )
            for page in pages:
                yield self.page_entry(page, run_id)
                if cfg.include_attachments and not INCLUDE_ATTACHMENTS_DEFERRED:
                    page_id = str(page["id"])
                    for attachment in self._list_attachments(client, page_id):
                        if not is_parseable(attachment):
                            continue
                        yield self.attachment_entry(attachment, page_id, run_id)

    # ---- pure mapping: raw API objects → ManifestEntry -------------------
    # Split out from the I/O above so the tests can run them on recorded
    # responses without a network (the RssReader.parse_feed pattern).

    def page_entry(self, page: dict, run_id: str) -> ManifestEntry:
        cfg = self.config
        page_id = str(page["id"])
        version = page.get("version") or {}
        return ManifestEntry(
            run_id=run_id,
            source_system=cfg.source_system,
            source_id=cfg.id,
            doc_url=self.page_url(page_id),
            title=page.get("title"),
            published_at=_to_datetime(version.get("when")),
            # the sole change token: Confluence bumps this on every edit
            doc_version=_version_token(version),
            extra={
                "confluence_kind": "page",
                "page_id": page_id,
                "space_key": cfg.space_key,
                "label": cfg.label,
                "doc_type": cfg.doc_type,
                "default_language": cfg.default_language,
            },
        )

    def attachment_entry(
        self, attachment: dict, page_id: str, run_id: str
    ) -> ManifestEntry:
        cfg = self.config
        # v2 attachments name the file in `title`
        filename = attachment.get("title") or str(attachment["id"])
        version = attachment.get("version") or {}
        return ManifestEntry(
            run_id=run_id,
            source_system=cfg.source_system,
            source_id=cfg.id,
            doc_url=self.attachment_url(page_id, filename),
            title=filename,
            published_at=_to_datetime(version.get("createdAt") or version.get("when")),
            doc_version=_version_token(version),
            size_bytes=attachment.get("fileSize"),
            extra={
                "confluence_kind": "attachment",
                "attachment_id": str(attachment["id"]),
                "page_id": page_id,
                "filename": filename,
                "media_type": attachment.get("mediaType"),
                "space_key": cfg.space_key,
                "label": cfg.label,
                "doc_type": cfg.doc_type,
                "default_language": cfg.default_language,
            },
        )

    def page_url(self, page_id: str) -> str:
        """The page's permalink. Built from the id, not the title, so renames
        keep one identity; Confluence redirects it to the titled URL."""
        base = self.config.base_url.rstrip("/")
        return f"{base}/wiki/spaces/{self.config.space_key}/pages/{page_id}"

    def attachment_url(self, page_id: str, filename: str) -> str:
        """The attachment's download URL — what stage 4 fetches, and what a
        citation links to. Carries no version query, so it keeps pointing at the
        current version. A renamed attachment gets a new identity and is
        re-ingested; that is rare and the outcome is still correct."""
        base = self.config.base_url.rstrip("/")
        return f"{base}/wiki/download/attachments/{page_id}/{quote(filename)}"

    # ---- I/O -------------------------------------------------------------

    def _search_pages(self, client: httpx.Client) -> list[dict]:
        """Every page in the space carrying the label, via CQL — the only Cloud
        API that filters on space and label in one call."""
        cql = (
            f'space="{_cql_value(self.config.space_key)}" '
            f'and label="{_cql_value(self.config.label)}" '
            f"and type=page"
        )
        return self._paginate(
            client,
            "/wiki/rest/api/content/search",
            {"cql": cql, "expand": "version", "limit": SEARCH_LIMIT},
        )

    def _list_attachments(self, client: httpx.Client, page_id: str) -> list[dict]:
        return self._paginate(
            client,
            f"/wiki/api/v2/pages/{page_id}/attachments",
            {"limit": ATTACHMENT_LIMIT},
        )

    def _paginate(
        self, client: httpx.Client, path: str, params: dict
    ) -> list[dict]:
        """Every `results` item across one cursor-paginated endpoint. The next
        link already carries the cursor, so it is followed without params."""
        results: list[dict] = []
        response = self._get(client, self._url(path), params)
        while True:
            body = response.json()
            results.extend(body.get("results", []))
            next_link = (body.get("_links") or {}).get("next")
            if not next_link:
                return results
            response = self._get(client, self._url(next_link), None)

    def _url(self, path: str) -> str:
        """Absolute URL for an API path or a `_links.next`. The v1 endpoints
        return next links without the /wiki context path, the v2 ones with it."""
        if path.startswith("http"):
            return path
        base = self.config.base_url.rstrip("/")
        if path.startswith("/wiki"):
            return f"{base}{path}"
        return f"{base}/wiki{path}"

    def _get(
        self, client: httpx.Client, url: str, params: dict | None
    ) -> httpx.Response:
        for _ in range(MAX_RETRIES):
            response = client.get(url, params=params)
            if response.status_code != 429:
                break
            wait = _retry_after(response)
            logger.warning(
                "confluence source %s rate limited, waiting %.0fs",
                self.config.id,
                wait,
            )
            time.sleep(wait)
        response.raise_for_status()
        return response


def is_parseable(attachment: dict) -> bool:
    """Whether stage 4 has a parser for this attachment's media type. An
    attachment with no mediaType is kept — stage 4 sniffs the served bytes."""
    media_type = (attachment.get("mediaType") or "").split(";")[0].strip().lower()
    if not media_type:
        return True
    if media_type in PARSEABLE_MEDIA_TYPES:
        return True
    logger.debug(
        "skipping confluence attachment %s: unparseable media type %s",
        attachment.get("title") or attachment.get("id"),
        media_type,
    )
    return False


def _version_token(version: dict) -> str | None:
    """The Confluence version number as the opaque change token. Compared for
    equality only, never ordered."""
    number = version.get("number")
    return str(number) if number is not None else None


def _to_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        logger.warning("unparseable confluence timestamp %r", value)
        return None
    return (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )


def _cql_value(value: str) -> str:
    """A space key or label going into a double-quoted CQL string. Both are
    plain identifiers in Confluence, so a quote or backslash means the config is
    wrong — reject it rather than build a broken query."""
    if '"' in value or "\\" in value:
        raise ValueError(f"invalid character in confluence config value {value!r}")
    return value


def _retry_after(response: httpx.Response) -> float:
    try:
        return float(response.headers.get("Retry-After", DEFAULT_RETRY_AFTER))
    except ValueError:
        return DEFAULT_RETRY_AFTER
