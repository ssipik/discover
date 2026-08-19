from pathlib import Path

import pytest

from rag_discover.readers import get_reader
from rag_discover.readers.local_files import LocalFilesReader
from rag_config import SourceConfig


def make_config(path: str, **overrides) -> SourceConfig:
    kwargs = dict(
        id="local_pdfs",
        type="local_files",
        source_system="local_files",
        path=path,
        glob="**/*.pdf",
        default_language="de",
        detect_deletions=True,
    )
    kwargs.update(overrides)
    return SourceConfig(**kwargs)


def _make_pdf(dir_path: Path, name: str, body: bytes = b"%PDF-1.7 body") -> Path:
    p = dir_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    return p


def test_registry_resolves_local_files(tmp_path):
    reader = get_reader(make_config(str(tmp_path)))
    assert isinstance(reader, LocalFilesReader)


def test_discover_yields_manifest_entries(tmp_path):
    _make_pdf(tmp_path, "MR_DE_de_X_2026-06-30.pdf", b"%PDF-1.7 aaaa")
    reader = LocalFilesReader(make_config(str(tmp_path)))

    entries = list(reader.discover(run_id="test-run"))

    assert len(entries) == 1
    entry = entries[0]
    assert entry.run_id == "test-run"
    assert entry.source_system == "local_files"
    assert entry.source_id == "local_pdfs"
    # doc_url is relative to the source root, so it is stable across mounts.
    assert entry.doc_url == "file:///MR_DE_de_X_2026-06-30.pdf"
    assert entry.title == "MR_DE_de_X_2026-06-30.pdf"
    assert entry.size_bytes == len(b"%PDF-1.7 aaaa")
    assert entry.doc_version and str(entry.size_bytes) in entry.doc_version
    assert entry.extra["default_language"] == "de"
    assert entry.extra["rel_path"] == "MR_DE_de_X_2026-06-30.pdf"


def test_glob_filters_and_recurses(tmp_path):
    _make_pdf(tmp_path, "top.pdf")
    _make_pdf(tmp_path, "sub/nested.pdf")
    (tmp_path / "notes.txt").write_bytes(b"ignore me")
    reader = LocalFilesReader(make_config(str(tmp_path)))

    urls = sorted(e.doc_url for e in reader.discover(run_id="r"))

    assert urls == ["file:///sub/nested.pdf", "file:///top.pdf"]


def test_doc_version_changes_with_content_size(tmp_path):
    _make_pdf(tmp_path, "a.pdf", b"%PDF short")
    v1 = next(iter(LocalFilesReader(make_config(str(tmp_path))).discover("r"))).doc_version
    _make_pdf(tmp_path, "a.pdf", b"%PDF much longer body than before")
    v2 = next(iter(LocalFilesReader(make_config(str(tmp_path))).discover("r"))).doc_version
    assert v1 != v2


def test_missing_directory_raises(tmp_path):
    reader = LocalFilesReader(make_config(str(tmp_path / "nope")))
    with pytest.raises(ValueError, match="does not exist or is not a directory"):
        list(reader.discover(run_id="r"))


def test_empty_directory_yields_nothing(tmp_path, caplog):
    reader = LocalFilesReader(make_config(str(tmp_path)))
    assert list(reader.discover(run_id="r")) == []
