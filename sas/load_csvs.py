"""Load the app's tables into CAS as yourself, from Jupyter in SAS Viya.

Use this until the app's own login (OAuth client app.inthebud) may write to CAS. It creates the
same tables, with the same column types and formats, that sas_sync.py pushes, so a dashboard built
on them keeps working when the live push takes over.

1. On your laptop, in the repo:   python -m sas_sync csv sas_export/
2. Upload the sas_export/ folder and this file to your Jupyter home (drag them into the file browser).
3. In a notebook:

       import swat
       conn = ...                      # connect exactly as in your cas_test.py
       from load_csvs import load_tables
       load_tables(conn, "sas_export") # or caslib="ASCLEPIUS" if the team gets its own caslib

Each table is replaced: uploaded to a staging table, swapped in under the shared name, and saved to
disk so it survives a CAS restart. Only the tables listed in sas_export/schema.json are touched.
"""
import json
import os


def _check(step: str, result) -> None:
    """swat returns action results with a severity; 2 means the action failed."""
    if getattr(result, "severity", 0) >= 2:
        raise RuntimeError(f"{step} failed: {getattr(result, 'status', '') or 'see the CAS log above'}")


def load_tables(conn, folder: str, caslib: str = "Public") -> None:
    try:
        import swat
        swat.set_option("cas.exception_on_severity", 2)  # stop at the first failed action
    except ImportError:
        pass

    with open(os.path.join(folder, "schema.json")) as handle:
        schema = json.load(handle)

    for table, spec in schema["tables"].items():
        if not spec["rows"]:
            print(f"{caslib}.{table}: no rows yet, skipped")
            continue
        stage = f"{table}_STAGE"
        conn.upload_file(
            os.path.join(folder, f"{table}.csv"),
            casout={"name": stage, "caslib": caslib, "replace": True},
            importoptions={"fileType": "CSV", "vars": spec["vars"]},
        )
        _check(f"format {table}", conn.table.alterTable(name=stage, caslib=caslib, columns=spec["formats"]))
        _check(f"drop old {table}", conn.table.dropTable(name=table, caslib=caslib, quiet=True))
        _check(f"publish {table}", conn.table.promote(name=stage, caslib=caslib, target=table,
                                                       targetLib=caslib, drop=True))
        _check(f"save {table}", conn.table.save(table={"name": table, "caslib": caslib}, caslib=caslib,
                                                name=f"{table}.sashdat", replace=True))
        print(f"{caslib}.{table}: {spec['rows']} rows")
