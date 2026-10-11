"""Check what your own SAS Viya login can do in CAS, from Jupyter in SAS Viya.

Upload this file, load_csvs.py and the sas_export/ folder to your Jupyter home, then in a notebook:

    import swat
    conn = ...                                  # connect exactly as in your cas_test.py
    import viya_checks
    viya_checks.read_only(conn, "sas_export")   # reads only; prints a PASS/FAIL table

Writes are separate and opt-in:

    viya_checks.write_test(conn)                # two-row TEST_CONNECT_<date> table in Public
    viya_checks.drop_write_test(conn)           # removes it again

Every check catches its own failure, so one denied action doesn't hide the rest.
"""
import json
import os
from datetime import date

STUCK_SESSION = "4117e684-eb22-c746-8f2a-0d88694291de"  # owned by app.inthebud; report it, never end it
TEST_TABLE = f"TEST_CONNECT_{date.today():%Y%m%d}"


def _attempt(results, check, fn):
    """Run one check; record PASS with fn's detail, or FAIL with the exact CAS message."""
    try:
        detail = fn()
        results.append((check, "PASS", detail))
    except Exception as error:  # swat raises different error types per failure; record them all
        results.append((check, "FAIL", f"{type(error).__name__}: {error}"))


def _ok(result, what):
    """swat reports a failed action through severity 2 when it doesn't raise."""
    if getattr(result, "severity", 0) >= 2:
        raise RuntimeError(f"{what}: {getattr(result, 'status', '') or 'failed, see the CAS log above'}")
    return result


def _tables(result):
    """Every DataFrame in an action result, as plain text (for actions whose output shape varies)."""
    return "; ".join(f"{key}: " + " | ".join(" ".join(line.split()) for line in value.to_string(index=False).splitlines())
                     for key, value in result.items() if hasattr(value, "to_string")) or str(dict(result))


def _print(results):
    width = max(len(check) for check, _, _ in results)
    for check, status, detail in results:
        print(f"{check:<{width}}  {status:<4}  {detail}")


def read_only(conn, export_dir="sas_export", caslib="Public"):
    """Read-only checks: server, identity, caslibs, my rights on the caslib, tables, columns, files, sessions."""
    with open(os.path.join(export_dir, "schema.json")) as handle:
        schema = json.load(handle)["tables"]
    results = []

    _attempt(results, "server status", lambda: str(_ok(conn.serverstatus(), "serverStatus").server.to_dict("records")))
    _attempt(results, "who am I (CAS)", lambda: str(dict(_ok(conn.builtins.userInfo(), "userInfo"))))
    _attempt(results, "caslibs visible",
             lambda: ", ".join(_ok(conn.table.caslibInfo(), "caslibInfo").CASLibInfo["Name"]))
    def rights():
        # "objectSelectors" was rejected on this server; try the singular name as an object, then as a list
        action, selector, errors = conn.accessControl.whatIsEffective, {"objType": "caslib", "caslib": caslib}, []
        for value in (selector, [selector]):
            try:
                return _tables(_ok(action(objectSelector=value), "whatIsEffective"))
            except Exception as error:
                errors.append(str(error))
        doc = " ".join((action.__doc__ or "").split())[:600]  # the server's own parameter list, to fix the call
        raise RuntimeError(f"{' / '.join(errors)}. Parameters per CAS: {doc}")

    _attempt(results, f"my rights on {caslib}", rights)

    loaded = {}

    def table_info():
        info = _ok(conn.table.tableInfo(caslib=caslib), "tableInfo")
        frame = info.get("TableInfo")
        if frame is not None:
            loaded.update({row.Name.upper(): row for row in frame.itertuples()})
        return ", ".join(f"{name} ({row.Rows} rows, {row.Columns} cols)" for name, row in loaded.items()) or "none"

    _attempt(results, f"tables in {caslib}", table_info)

    for table, spec in schema.items():
        def compare(table=table, spec=spec):
            if not spec["rows"]:
                # load_csvs.py and sas_sync skip or drop empty tables, so absence is expected
                return "absent, as expected (0 rows locally)" if table not in loaded else "present with 0 rows locally"
            if table not in loaded:
                raise RuntimeError(f"missing; expected {spec['rows']} rows")
            row = loaded[table]
            columns = _ok(conn.table.columnInfo(table={"caslib": caslib, "name": table}), "columnInfo").ColumnInfo
            actual = {name.upper(): kind.lower() for name, kind in zip(columns["Column"], columns["Type"])}
            expected = {var["name"].upper(): var["type"].lower() for var in spec["vars"]}
            problems = [f"missing {name}" for name in expected if name not in actual]
            problems += [f"extra {name}" for name in actual if name not in expected]
            problems += [f"{name} is {actual[name]}, expected {kind}" for name, kind in expected.items()
                         if name in actual and actual[name] != kind]
            if int(row.Rows) != spec["rows"]:
                problems.append(f"{int(row.Rows)} rows in CAS, {spec['rows']} locally")
            if problems:
                raise RuntimeError("; ".join(problems))
            return f"{int(row.Rows)} rows, {len(actual)} columns, names and types match schema.json"

        _attempt(results, f"{table} vs schema", compare)

    _attempt(results, f"saved files in {caslib}", lambda: ", ".join(
        name for name in _ok(conn.table.fileInfo(caslib=caslib), "fileInfo").FileInfo["Name"]
        if name.lower().endswith(".sashdat")))

    def sessions():
        listing = _tables(_ok(conn.session.listSessions(), "listSessions"))
        seen = "listed" if STUCK_SESSION in listing else "not visible to this login"
        return f"stuck app session {seen}. {listing}"

    _attempt(results, "CAS sessions", sessions)

    _print(results)
    return results


def write_test(conn, caslib="Public"):
    """Opt-in: can my login create and share a table in the caslib? Two rows, no saved file."""
    import pandas as pd

    results = []
    frame = pd.DataFrame({"ID": [1, 2], "NOTE": ["write test", "safe to delete"]})
    def upload():
        # upload_frame returns the new table (not an action result) and raises if the upload is denied
        conn.upload_frame(frame, casout={"name": TEST_TABLE, "caslib": caslib, "replace": True})
        return f"uploaded {TEST_TABLE}"

    _attempt(results, f"upload to {caslib} (session)", upload)
    _attempt(results, f"promote to {caslib} (shared)", lambda: str(dict(_ok(conn.table.promote(
        name=TEST_TABLE, caslib=caslib, targetLib=caslib), "promote")) or "promoted"))
    _attempt(results, "visible as shared", lambda: _tables(_ok(conn.table.tableInfo(
        caslib=caslib, name=TEST_TABLE), "tableInfo")))
    _print(results)
    print(f"\nWhen you're done: viya_checks.drop_write_test(conn)  # removes {caslib}.{TEST_TABLE}")
    return results


def drop_write_test(conn, caslib="Public"):
    """Remove the table write_test made. Touches nothing else."""
    results = []
    _attempt(results, f"drop {caslib}.{TEST_TABLE}", lambda: str(dict(_ok(conn.table.dropTable(
        name=TEST_TABLE, caslib=caslib, quiet=True), "dropTable")) or "dropped"))
    _print(results)
    return results
