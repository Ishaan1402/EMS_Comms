"""The manual Jupyter loader must retain the last saved tables when publishing fails."""
import json
import sys
from types import SimpleNamespace

import pytest

from sas.load_csvs import load_tables


class FakeCas:
    """Stateful SWAT-shaped fake; a successful drop really removes the published table."""
    def __init__(self, failure=None, raises=False):
        self.failure = failure or set()
        self.raises = raises
        self.calls = []
        self.published = {"ASC_CASES": "previous cases", "ASC_EVENTS": "previous events"}
        self.saved = dict(self.published)
        self.staged = {}
        self.table = self

    def result(self, action, **kwargs):
        self.calls.append((action, kwargs))
        if action in self.failure:
            if self.raises:
                raise RuntimeError(f"{action} refused")
            return SimpleNamespace(severity=2, status=f"{action} refused")
        return SimpleNamespace(severity=0, status="OK")

    def upload_file(self, filename, **kwargs):
        result = self.result("upload", filename=filename, **kwargs)
        if result.severity < 2:
            self.staged[kwargs["casout"]["name"]] = "new rows"
        return result

    def alterTable(self, **kwargs):
        return self.result("format", **kwargs)

    def dropTable(self, **kwargs):
        result = self.result("drop", **kwargs)
        if result.severity < 2:
            self.published.pop(kwargs["name"], None)
        return result

    def promote(self, **kwargs):
        result = self.result("promote", **kwargs)
        if result.severity < 2:
            self.published[kwargs["target"]] = self.staged.pop(kwargs["name"])
        return result

    def save(self, **kwargs):
        result = self.result("save", **kwargs)
        if result.severity < 2:
            name = kwargs["table"]["name"]
            self.saved[name] = self.published[name]
        return result

    def loadTable(self, **kwargs):
        result = self.result("restore", **kwargs)
        if result.severity < 2:
            name = kwargs["casOut"]["name"]
            assert kwargs["path"] == f"{name}.sashdat"
            self.published[name] = self.saved[name]
        return result


@pytest.fixture
def export(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "swat", SimpleNamespace(set_option=lambda *args: None))
    spec = {"rows": 1, "vars": [{"name": "CASE_ID", "type": "double"}], "formats": []}
    (tmp_path / "schema.json").write_text(json.dumps({"tables": {"ASC_CASES": spec, "ASC_EVENTS": spec}}))
    for table in ("ASC_CASES", "ASC_EVENTS"):
        (tmp_path / f"{table}.csv").write_text("CASE_ID\n1\n")
    return tmp_path


def test_manual_load_publishes_and_saves_each_table(export):
    conn = FakeCas()
    load_tables(conn, export, caslib="Team")
    assert conn.published == conn.saved == {"ASC_CASES": "new rows", "ASC_EVENTS": "new rows"}
    assert [action for action, _ in conn.calls] == ["upload", "format", "drop", "promote", "save"] * 2
    assert all(kwargs.get("caslib", kwargs.get("casout", {}).get("caslib")) == "Team"
               for _, kwargs in conn.calls)


@pytest.mark.parametrize("raises", [False, True])
def test_failed_publish_restores_the_saved_table_and_stops(export, raises):
    conn = FakeCas({"promote"}, raises)
    with pytest.raises(RuntimeError, match="promote.*refused"):
        load_tables(conn, export)
    assert conn.published == {"ASC_CASES": "previous cases", "ASC_EVENTS": "previous events"}
    assert [action for action, _ in conn.calls] == ["upload", "format", "drop", "promote", "restore"]
    assert conn.saved["ASC_CASES"] == "previous cases"


@pytest.mark.parametrize("raises", [False, True])
def test_failed_restore_reports_both_failures(export, raises):
    conn = FakeCas({"promote", "restore"}, raises)
    with pytest.raises(RuntimeError, match="promote.*refused.*restoring ASC_CASES.*failed.*restore.*refused"):
        load_tables(conn, export)
    assert conn.saved["ASC_CASES"] == "previous cases"


@pytest.mark.parametrize("action", ["upload", "format", "drop"])
@pytest.mark.parametrize("raises", [False, True])
def test_failure_before_publish_never_replaces_the_shared_table(export, action, raises):
    conn = FakeCas({action}, raises)
    with pytest.raises(RuntimeError, match="refused"):
        load_tables(conn, export)
    assert conn.published == {"ASC_CASES": "previous cases", "ASC_EVENTS": "previous events"}
    assert "promote" not in [step for step, _ in conn.calls]


def test_save_failure_keeps_new_table_in_memory_and_last_saved_copy(export):
    conn = FakeCas({"save"})
    with pytest.raises(RuntimeError, match="save.*refused"):
        load_tables(conn, export)
    assert conn.published["ASC_CASES"] == "new rows"
    assert conn.saved["ASC_CASES"] == "previous cases"


def test_empty_export_never_touches_shared_tables(export):
    (export / "schema.json").write_text(json.dumps({"tables": {"ASC_CASES": {"rows": 0}}}))
    conn = FakeCas()
    load_tables(conn, export)
    assert conn.calls == []
