"""Case messaging between EMTs and the receiving hospital team."""
import json
import os
import random
import socket
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-testing-only")

from main import app
from backend import database
from backend.database import query, run, init_database, insert_sample_data, utc_now_iso

REAL_DB_PATH = Path(database.__file__).parent.parent / "asclepius.db"

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module", autouse=True)
def temp_database(tmp_path_factory):
    """Fresh seeded temp DB for this module; never the real asclepius.db."""
    previous = database.DB_PATH
    database.DB_PATH = tmp_path_factory.mktemp("messages-db") / "test.db"
    assert database.DB_PATH.resolve() != REAL_DB_PATH.resolve()
    init_database()
    insert_sample_data()
    yield database.DB_PATH
    database.DB_PATH = previous


def user_id(username):
    return query('SELECT id FROM users WHERE username = ?', (username,))[0]["id"]


def auth(username):
    response = client.post("/api/auth/login", json={"username": username, "password": "password123"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def hospital_id(code="GEN"):
    return query("SELECT id FROM hospitals WHERE code = ?", (code,))[0]["id"]


def create_case(emt_username="emt.wilson", hospital="GEN"):
    """A case routed to `hospital`. Closed, so one crew can have many; its thread still works."""
    now = utc_now_iso()
    return run(
        """INSERT INTO cases (emt_id, patient_info, status, started_at, updated_at, destination_hospital_id, info_version)
           VALUES (?, ?, 'closed', ?, ?, ?, 1)""",
        (user_id(emt_username), "Inbound patient", now, now, hospital_id(hospital))
    )["id"]


def send(case_id, headers, body, **extra):
    return client.post(f"/api/cases/{case_id}/messages", headers=headers, json={"body": body, **extra})


def history(case_id, headers, **params):
    return client.get(f"/api/cases/{case_id}/messages", headers=headers, params=params)


@pytest.fixture(scope="module")
def emt():
    return auth("emt.wilson")


@pytest.fixture(scope="module")
def other_emt():
    return auth("emt.garcia")


@pytest.fixture(scope="module")
def doctor():
    return auth("dr.smith")


@pytest.fixture(scope="module")
def other_doctor():
    return auth("dr.jones")


class TestConversation:
    def test_two_way_exchange_persists_in_order(self, emt, doctor):
        case = create_case()

        empty = history(case, emt)
        assert empty.status_code == 200
        assert empty.json() == {"case_id": case, "messages": []}

        sent = [
            (emt, "ETA 8 minutes, 58M chest pain, STEMI on 12-lead"),
            (doctor, "Cath lab activated. Any aspirin given?"),
            (emt, "324mg aspirin given at 14:02"),
            (doctor, "Good. Bring straight to cath lab bay 2"),
        ]
        for headers, body in sent:
            response = send(case, headers, body)
            assert response.status_code == 201, response.text
            message = response.json()
            assert message["case_id"] == case
            assert message["body"] == body

        # "Refresh" on both sides: a fresh read returns the full conversation, oldest first.
        for headers in (emt, doctor):
            messages = history(case, headers).json()["messages"]
            assert [m["body"] for m in messages] == [body for _, body in sent]
            assert [m["sender_role"] for m in messages] == ["emt", "doctor", "emt", "doctor"]
            assert [m["id"] for m in messages] == sorted(m["id"] for m in messages)
            assert messages[0]["sender_first_name"] == "Mike"
            assert messages[0]["sender_last_name"] == "Wilson"
            assert messages[1]["sender_first_name"] == "John"
            assert messages[1]["sender_id"] == user_id("dr.smith")
            timestamps = [m["created_at"] for m in messages]
            assert all(t.endswith("Z") and "T" in t for t in timestamps)
            assert timestamps == sorted(timestamps)

    def test_messages_survive_a_new_database_connection(self, emt, doctor):
        case = create_case()
        send(case, emt, "first")
        send(case, doctor, "second")

        conn = sqlite3.connect(str(database.DB_PATH))
        rows = conn.execute(
            'SELECT body FROM messages WHERE case_id = ? ORDER BY id', (case,)
        ).fetchall()
        conn.close()
        assert [r[0] for r in rows] == ["first", "second"]

    def test_after_id_returns_only_newer_messages(self, emt, doctor):
        case = create_case()
        first = send(case, emt, "one").json()
        send(case, doctor, "two")
        send(case, emt, "three")

        newer = history(case, doctor, after_id=first["id"]).json()["messages"]
        assert [m["body"] for m in newer] == ["two", "three"]

    def test_message_body_is_trimmed(self, emt):
        case = create_case()
        assert send(case, emt, "  BP 90/60  ").json()["body"] == "BP 90/60"

    def test_everyone_at_the_receiving_hospital_shares_the_conversation(self, emt, doctor):
        colleague = {"username": "dr.gen.colleague", "email": "colleague@gen.test", "password": "password123",
                     "role": "doctor", "first_name": "Ana", "last_name": "Ruiz", "hospital_id": hospital_id("GEN")}
        assert client.post("/api/auth/register", json=colleague).status_code in (201, 400)
        case = create_case()
        send(case, auth("dr.gen.colleague"), "Trauma bay ready")
        send(case, doctor, "I'll take the airway")
        bodies = [m["body"] for m in history(case, emt).json()["messages"]]
        assert bodies == ["Trauma bay ready", "I'll take the airway"]


class TestCaseIsolation:
    def test_messages_never_appear_under_another_case(self, emt, doctor):
        case_a = create_case()
        case_b = create_case()

        send(case_a, emt, "A: patient is diabetic")
        send(case_b, emt, "B: patient on warfarin")
        send(case_a, doctor, "A: check glucose")

        a = history(case_a, doctor).json()["messages"]
        b = history(case_b, doctor).json()["messages"]
        assert [m["body"] for m in a] == ["A: patient is diabetic", "A: check glucose"]
        assert [m["body"] for m in b] == ["B: patient on warfarin"]
        assert {m["case_id"] for m in a} == {case_a}
        assert {m["case_id"] for m in b} == {case_b}

    def test_case_id_in_body_is_ignored(self, emt):
        case_a = create_case()
        case_b = create_case()

        response = client.post(
            f"/api/cases/{case_a}/messages",
            headers=emt,
            json={"body": "goes to A", "case_id": case_b},
        )
        assert response.status_code == 201
        assert response.json()["case_id"] == case_a
        assert history(case_b, emt).json()["messages"] == []

    def test_after_id_from_another_case_does_not_leak(self, emt):
        case_a = create_case()
        case_b = create_case()
        send(case_a, emt, "A only")
        later_b = send(case_b, emt, "B only").json()

        # A case-B message id used as the cursor on case A returns nothing from B.
        assert history(case_a, emt, after_id=0).json()["messages"][0]["body"] == "A only"
        assert history(case_a, emt, after_id=later_b["id"]).json()["messages"] == []

    def test_client_id_cannot_move_a_message_to_another_case(self, emt):
        case_a = create_case()
        case_b = create_case()

        assert send(case_a, emt, "hello", client_id="c-move").status_code == 201
        response = send(case_b, emt, "hello", client_id="c-move")
        assert response.status_code == 409
        assert history(case_b, emt).json()["messages"] == []
        assert len(history(case_a, emt).json()["messages"]) == 1

    def test_concurrent_senders_on_many_cases_stay_separated(self, emt, doctor):
        cases = [create_case() for _ in range(6)]
        jobs = [
            (case, headers, f"case-{case}-msg-{i}")
            for case in cases
            for i, headers in enumerate([emt, doctor] * 5)
        ]
        random.Random(7).shuffle(jobs)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda job: send(*job), jobs))
        assert all(r.status_code == 201 for r in results)

        for case in cases:
            messages = history(case, doctor).json()["messages"]
            assert len(messages) == 10
            assert all(m["case_id"] == case for m in messages)
            assert all(m["body"].startswith(f"case-{case}-") for m in messages)


class TestAccessControl:
    def test_other_emt_cannot_read_write_or_stream(self, emt, other_emt):
        case = create_case()
        send(case, emt, "private")

        assert history(case, other_emt).status_code == 404
        assert send(case, other_emt, "intrusion").status_code == 404
        assert client.get(f"/api/cases/{case}/messages/stream", headers=other_emt).status_code == 404
        assert [m["body"] for m in history(case, emt).json()["messages"]] == ["private"]

    def test_doctor_at_another_hospital_is_denied(self, other_doctor):
        case = create_case(hospital="GEN")
        assert history(case, other_doctor).status_code == 404
        assert send(case, other_doctor, "hi").status_code == 404

    def test_requires_authentication(self):
        case = create_case()
        response = client.get(f"/api/cases/{case}/messages")
        assert response.status_code == 401
        assert response.json() == {"error": "Access denied. No token provided."}
        assert client.post(f"/api/cases/{case}/messages", json={"body": "x"}).status_code == 401

    def test_missing_or_non_numeric_case_is_404(self, emt):
        assert history(999999, emt).status_code == 404
        assert history("abc", emt).json() == {"error": "Case not found"}


class TestValidation:
    @pytest.mark.parametrize("payload", [{}, {"body": ""}, {"body": "   "}, {"body": 42}, {"body": None}])
    def test_empty_or_non_string_body_rejected(self, emt, payload):
        case = create_case()
        response = client.post(f"/api/cases/{case}/messages", headers=emt, json=payload)
        assert response.status_code == 400
        assert history(case, emt).json()["messages"] == []

    def test_too_long_rejected(self, emt):
        case = create_case()
        assert send(case, emt, "x" * 2001).status_code == 400
        assert send(case, emt, "x" * 2000).status_code == 201

    def test_malformed_json_rejected(self, emt):
        case = create_case()
        response = client.post(
            f"/api/cases/{case}/messages",
            headers={**emt, "Content-Type": "application/json"},
            content=b"{not json",
        )
        assert response.status_code == 400
        assert response.json() == {"error": "Invalid JSON"}

    @pytest.mark.parametrize("after_id", ["-1", "abc", "1.5", "²"])
    def test_bad_after_id_rejected(self, emt, after_id):
        case = create_case()
        assert history(case, emt, after_id=after_id).status_code == 400

    def test_concurrent_retries_with_same_client_id_store_one_message(self, emt):
        case = create_case()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: send(case, emt, "retry storm", client_id="storm-1"), range(8)))
        assert sorted(r.status_code for r in results) == [200] * 7 + [201]
        assert len({r.json()["id"] for r in results}) == 1
        assert len(history(case, emt).json()["messages"]) == 1

    def test_retry_with_same_client_id_is_not_duplicated(self, emt):
        case = create_case()
        first = send(case, emt, "BP dropping", client_id="retry-1")
        second = send(case, emt, "BP dropping", client_id="retry-1")
        assert first.status_code == 201
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        assert len(history(case, emt).json()["messages"]) == 1


class TestDatabase:
    def test_write_ahead_logging_enabled(self):
        conn = sqlite3.connect(str(database.DB_PATH))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"

    def test_message_requires_existing_case(self):
        with pytest.raises(sqlite3.IntegrityError):
            run(
                'INSERT INTO messages (case_id, sender_id, sender_role, body) VALUES (?, ?, ?, ?)',
                (987654, user_id("emt.wilson"), "emt", "orphan")
            )


# --- Live delivery over a real HTTP server ---------------------------------

@pytest.fixture(scope="module")
def live_server(temp_database):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline, "server did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def read_events(response, count, timeout=5.0):
    """Parse up to `count` SSE message events from a streaming response."""
    events, data, deadline = [], None, time.time() + timeout
    for line in response.iter_lines():
        if line.startswith("data: "):
            data = json.loads(line[len("data: "):])
        elif line == "" and data is not None:
            events.append(data)
            data = None
            if len(events) == count:
                break
        assert time.time() < deadline, f"timed out with {len(events)} events"
    return events


class TestLiveStream:
    def test_stream_delivers_only_this_cases_messages(self, live_server, emt, doctor):
        case_a = create_case()
        case_b = create_case()
        send(case_a, emt, "A history")

        with httpx.Client(base_url=live_server, timeout=10) as http:
            with http.stream("GET", f"/api/cases/{case_a}/messages/stream", headers=doctor) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")

                def post_later():
                    time.sleep(0.3)
                    http.post(f"/api/cases/{case_b}/messages", headers=emt, json={"body": "B live"})
                    http.post(f"/api/cases/{case_a}/messages", headers=emt, json={"body": "A live 1"})
                    http.post(f"/api/cases/{case_a}/messages", headers=doctor, json={"body": "A live 2"})

                poster = threading.Thread(target=post_later)
                poster.start()
                events = read_events(response, 3)
                poster.join()

        assert [e["body"] for e in events] == ["A history", "A live 1", "A live 2"]
        assert {e["case_id"] for e in events} == {case_a}

    def test_stream_resumes_after_last_event_id(self, live_server, emt):
        case = create_case()
        first = send(case, emt, "seen").json()
        send(case, emt, "missed while offline")

        with httpx.Client(base_url=live_server, timeout=10) as http:
            headers = {**emt, "Last-Event-ID": str(first["id"])}
            with http.stream("GET", f"/api/cases/{case}/messages/stream", headers=headers) as response:
                events = read_events(response, 1)

        assert [e["body"] for e in events] == ["missed while offline"]

    def test_stream_picks_up_writes_from_another_process(self, live_server, emt):
        """A write that bypasses the API (no in-process wake-up) still arrives via polling."""
        case = create_case()
        with httpx.Client(base_url=live_server, timeout=10) as http:
            with http.stream("GET", f"/api/cases/{case}/messages/stream", headers=emt) as response:
                def write_directly():
                    time.sleep(0.3)
                    conn = sqlite3.connect(str(database.DB_PATH))
                    conn.execute(
                        'INSERT INTO messages (case_id, sender_id, sender_role, body) VALUES (?, ?, ?, ?)',
                        (case, user_id("dr.smith"), "doctor", "from elsewhere")
                    )
                    conn.commit()
                    conn.close()

                writer = threading.Thread(target=write_directly)
                writer.start()
                events = read_events(response, 1)
                writer.join()

        assert events[0]["body"] == "from elsewhere"

    def test_malformed_last_event_id_is_ignored(self, live_server, emt):
        case = create_case()
        send(case, emt, "still delivered")
        with httpx.Client(base_url=live_server, timeout=10) as http:
            # "²" passes str.isdigit() but is not a valid id.
            headers = {**emt, "Last-Event-ID": "²".encode("latin-1")}
            with http.stream("GET", f"/api/cases/{case}/messages/stream", headers=headers) as response:
                assert response.status_code == 200
                events = read_events(response, 1)
        assert [e["body"] for e in events] == ["still delivered"]
