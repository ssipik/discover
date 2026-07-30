"""Offline tests for the Confluence reader.

The fixtures are hand-built to the documented Cloud response shape rather than
captured from a live instance — replace them with real (credential-scrubbed)
responses the first time the reader runs against the wiki.
"""

import json
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import rag_discover.readers.confluence as confluence_module
from rag_discover.readers import get_reader
from rag_discover.readers.confluence import (
    ConfluenceReader,
    _cql_value,
    is_parseable,
)
from rag_discover.sources import SourceConfig

FIXTURES = Path(__file__).parent / "fixtures"
SEARCH = json.loads((FIXTURES / "confluence_search.json").read_text())
ATTACHMENTS = json.loads((FIXTURES / "confluence_attachments.json").read_text())


def make_config(**overrides) -> SourceConfig:
    return SourceConfig(
        **{
            "id": "confluence_risk",
            "type": "confluence",
            "source_system": "confluence",
            "base_url": "https://acme.atlassian.net",
            "space_key": "RISK",
            "label": "RAG",
            "include_attachments": True,
            "doc_type": "wiki_page",
            "default_language": "de",
            **overrides,
        }
    )


def page_entries(reader, run_id="test-run"):
    return [reader.page_entry(p, run_id) for p in SEARCH["results"]]


class TestPageEntries:
    def test_maps_a_page_to_a_manifest_entry(self):
        entry = page_entries(ConfluenceReader(make_config()))[0]

        assert entry.run_id == "test-run"
        assert entry.source_system == "confluence"
        assert entry.source_id == "confluence_risk"
        assert entry.title == "Risikoinventur 2026"
        assert entry.extra["confluence_kind"] == "page"
        assert entry.extra["page_id"] == "123456"
        assert entry.extra["space_key"] == "RISK"
        assert entry.extra["label"] == "RAG"
        # the parse_chunk stage reads these two off the manifest extra
        assert entry.extra["doc_type"] == "wiki_page"
        assert entry.extra["default_language"] == "de"

    def test_doc_url_is_built_from_the_page_id_not_the_title(self):
        # a rename must not change the document's identity
        entry = page_entries(ConfluenceReader(make_config()))[0]
        assert (
            entry.doc_url
            == "https://acme.atlassian.net/wiki/spaces/RISK/pages/123456"
        )

    def test_doc_version_is_the_confluence_version_number(self):
        entries = page_entries(ConfluenceReader(make_config()))
        assert entries[0].doc_version == "7"
        assert entries[1].doc_version == "1"

    def test_published_at_is_the_last_edit_in_utc(self):
        entry = page_entries(ConfluenceReader(make_config()))[0]
        assert entry.published_at.tzinfo is not None
        assert entry.published_at.astimezone(timezone.utc).isoformat() == (
            "2026-07-20T09:12:33.123000+00:00"
        )

    def test_listings_only(self):
        entry = page_entries(ConfluenceReader(make_config()))[0]
        assert "content" not in entry.model_dump()

    def test_trailing_slash_on_base_url_does_not_double(self):
        cfg = make_config(base_url="https://acme.atlassian.net/")
        entry = page_entries(ConfluenceReader(cfg))[0]
        assert "//wiki" not in entry.doc_url


class TestAttachmentEntries:
    def entries(self, cfg=None):
        reader = ConfluenceReader(cfg or make_config())
        return [
            reader.attachment_entry(a, "123456", "test-run")
            for a in ATTACHMENTS["results"]
        ]

    def test_maps_an_attachment_to_a_manifest_entry(self):
        entry = self.entries()[0]
        assert entry.title == "Risikobericht Q2.pdf"
        assert entry.doc_version == "2"
        assert entry.size_bytes == 482913
        assert entry.extra["confluence_kind"] == "attachment"
        assert entry.extra["attachment_id"] == "att4001"
        assert entry.extra["page_id"] == "123456"
        assert entry.extra["media_type"] == "application/pdf"

    def test_doc_url_is_the_download_url_with_the_filename_quoted(self):
        # stage 4 GETs this URL directly, so it must be a real download link
        entry = self.entries()[0]
        assert entry.doc_url == (
            "https://acme.atlassian.net/wiki/download/attachments/123456/"
            "Risikobericht%20Q2.pdf"
        )

    def test_doc_url_carries_no_version_query(self):
        # so it keeps pointing at the current version of the attachment
        assert "?" not in self.entries()[0].doc_url

    def test_size_bytes_feeds_the_plan_stage(self):
        assert [e.size_bytes for e in self.entries()] == [482913, 20481]


class TestAttachmentsAreDeferred:
    """No attachment reaches ingest_manifest, so nothing downstream sees one
    (ARCHITECTURE.md block 1). The mapping code above is kept for the later
    iteration, which is why this tests discover() rather than the mappers."""

    def discover_entries(self, monkeypatch, listed):
        monkeypatch.setattr(
            confluence_module,
            "get_settings",
            lambda: SimpleNamespace(
                confluence_email="u@e.x",
                confluence_api_token="tok",
                http_timeout=10.0,
                user_agent="test-agent",
            ),
        )
        monkeypatch.setattr(confluence_module.httpx, "Client", lambda **kw: _NullClient())
        monkeypatch.setattr(
            ConfluenceReader, "_search_pages", lambda self, client: SEARCH["results"]
        )
        monkeypatch.setattr(
            ConfluenceReader,
            "_list_attachments",
            lambda self, client, page_id: listed.append(page_id) or ATTACHMENTS["results"],
        )
        reader = ConfluenceReader(make_config(include_attachments=True))
        return list(reader.discover("test-run"))

    def test_pages_only_even_when_the_source_asks_for_attachments(self, monkeypatch):
        entries = self.discover_entries(monkeypatch, [])
        assert {e.extra["confluence_kind"] for e in entries} == {"page"}
        assert len(entries) == len(SEARCH["results"])

    def test_the_extra_api_call_per_page_is_not_made(self, monkeypatch):
        listed: list[str] = []
        self.discover_entries(monkeypatch, listed)
        assert listed == []


class _NullClient:
    """discover()'s httpx client stand-in — every request is monkeypatched out."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestIsParseable:
    """The media-type gate for when attachments come back."""

    def test_pdf_is_kept(self):
        assert is_parseable({"mediaType": "application/pdf"})

    def test_pasted_screenshot_is_dropped(self):
        assert not is_parseable(
            {"title": "image-20251112-161330.png", "mediaType": "image/png"}
        )

    def test_docx_is_dropped_until_there_is_a_docx_parser(self):
        assert not is_parseable({"mediaType": ATTACHMENTS["results"][1]["mediaType"]})

    def test_charset_parameter_does_not_break_the_match(self):
        assert is_parseable({"mediaType": "text/html; charset=UTF-8"})

    def test_missing_media_type_is_kept_for_stage_4_to_sniff(self):
        assert is_parseable({"title": "report"})
        assert is_parseable({"title": "report", "mediaType": None})


class TestConfig:
    def test_registry_resolves_confluence_reader(self):
        assert isinstance(get_reader(make_config()), ConfluenceReader)

    @pytest.mark.parametrize("field", ["base_url", "space_key", "label"])
    def test_missing_required_field_raises(self, field):
        reader = ConfluenceReader(make_config(**{field: None}))
        with pytest.raises(ValueError, match=field):
            list(reader.discover("r"))

    def test_cql_value_rejects_quotes(self):
        # a space key or label is a plain identifier; anything else is a
        # misconfiguration, not something to escape into the query
        with pytest.raises(ValueError):
            _cql_value('RISK" or type=blogpost and "')


class TestNextLinkResolution:
    """v1 next links omit the /wiki context path, v2 ones include it."""

    def test_v1_next_link_gets_the_wiki_prefix(self):
        reader = ConfluenceReader(make_config())
        assert reader._url("/rest/api/content/search?cursor=x") == (
            "https://acme.atlassian.net/wiki/rest/api/content/search?cursor=x"
        )

    def test_v2_next_link_is_used_as_is(self):
        reader = ConfluenceReader(make_config())
        assert reader._url("/wiki/api/v2/pages/1/attachments?cursor=x") == (
            "https://acme.atlassian.net/wiki/api/v2/pages/1/attachments?cursor=x"
        )

    def test_absolute_next_link_is_left_alone(self):
        reader = ConfluenceReader(make_config())
        url = "https://acme.atlassian.net/wiki/api/v2/pages?cursor=x"
        assert reader._url(url) == url
