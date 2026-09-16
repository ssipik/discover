
import pytest

from rag_discover.discover import run_discovery

CONFIG = None  # the copy shipped in rag-config


def test_unknown_source_id_rejected_before_any_io():
    with pytest.raises(ValueError, match="unknown source"):
        run_discovery(CONFIG, source_ids=["nope"])


class _Reader:
    def __init__(self, entries):
        self.entries = entries

    def discover(self, run_id):
        return iter(self.entries)


def test_no_sources_is_an_error(monkeypatch):
    import rag_discover.discover as discover

    monkeypatch.setattr(discover, "load_sources", lambda config_path: {})
    with pytest.raises(ValueError, match="no sources"):
        run_discovery(CONFIG, run_id="r1", engine=object())


def test_empty_manifest_for_the_run_is_an_error(monkeypatch):
    import rag_discover.discover as discover

    monkeypatch.setattr(discover, "get_reader", lambda cfg: _Reader([]))
    monkeypatch.setattr(discover, "upsert_manifest", lambda engine, entries: 0)
    monkeypatch.setattr(discover, "count_manifest_rows", lambda engine, run_id: 0)
    with pytest.raises(ValueError, match="ingest_manifest has no rows"):
        run_discovery(CONFIG, run_id="r1", engine=object())


def test_one_empty_source_is_fine_when_the_manifest_has_rows(monkeypatch):
    import rag_discover.discover as discover

    counts = iter([0, 3, 2, 1, 4, 5, 6, 7, 8, 9])
    monkeypatch.setattr(discover, "get_reader", lambda cfg: _Reader([]))
    monkeypatch.setattr(discover, "upsert_manifest", lambda engine, entries: next(counts))
    monkeypatch.setattr(discover, "count_manifest_rows", lambda engine, run_id: 3)
    report = run_discovery(CONFIG, run_id="r1", engine=object())
    assert 0 in report.counts.values()
