"""Is the app's CAS 403 fixed? A read-only check as app.inthebud, the login in .env.

    python3 sas/check_app_cas.py

Runs two read actions (server status, list the Public tables). It works inside the app's existing stuck
session if that still exists, so no session is added. Otherwise it opens a session and tries to close it.
It writes no tables.
"""
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from sas_sync import CasClient, SasError, Settings  # noqa: E402  (needs the path and .env above)

STUCK_SESSION = "4117e684-eb22-c746-8f2a-0d88694291de"
READS = [("builtins.serverStatus", {}), ("table.tableInfo", {"caslib": "Public"})]


async def main() -> int:
    settings = Settings.from_env()
    if not settings.configured:
        print("Set SAS_VIYA_URL, SAS_CLIENT_ID and SAS_CLIENT_SECRET in .env")
        return 1
    client = CasClient(settings)
    opened = None
    try:
        try:
            await client._call("GET", f"{client.cas_url}/cas/sessions/{STUCK_SESSION}", "stuck session")
            session = STUCK_SESSION
            print(f"stuck session {STUCK_SESSION}: still exists, testing inside it")
        except SasError as error:
            print(f"stuck session: {error} (gone or not readable), opening a new session")
            opened = session = await client.open_session()
            print(f"opened session {opened}")

        passed = 0
        for action, params in READS:
            try:
                await client.action(session, action, params)
                print(f"{action}: PASS")
                passed += 1
            except SasError as error:
                print(f"{action}: FAIL ({error})")

        if opened:
            try:
                await client.close_session(opened)
                print(f"closed session {opened}")
            except SasError as error:
                print(f"could not close session {opened}: {error}. Add it to the admin list.")

        if passed == len(READS):
            print("\nRESULT: the app can run CAS actions now. Next: python3 -m sas_sync push (replaces the ASC_ tables).")
        else:
            print("\nRESULT: still blocked. The app needs CAS action rights and table.upload, alterTable,"
                  " dropTable, promote and save on the Public caslib.")
        return 0
    except SasError as error:
        # sign-in and REST calls happen first, so an HTTP status here means SAS answered and refused
        print(f"RESULT: still blocked: {error}")
        return 1
    finally:
        await client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
