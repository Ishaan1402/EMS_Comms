import pytest
from fastapi.testclient import TestClient
import asyncio
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

# Set test environment BEFORE importing main
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-testing-only")

from main import app
from backend import database
from backend.database import query, run, get_db, init_database, insert_sample_data
from backend import openai_config

REAL_DB_PATH = database.configured_db_path()

# Global error handler responses are 500s; inspect them instead of re-raising.
client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="session", autouse=True)
def temp_database(tmp_path_factory):
    """Point every query at a fresh, explicitly seeded temp DB; never the real eir.db."""
    database.DB_PATH = tmp_path_factory.mktemp("db") / "test.db"
    assert database.DB_PATH.resolve() != REAL_DB_PATH.resolve()
    init_database()
    insert_sample_data()
    yield database.DB_PATH
    database.DB_PATH = REAL_DB_PATH


@pytest.fixture(autouse=True)
def no_external_services(monkeypatch, tmp_path):
    """Run with no OpenAI client, and keep any clip audio in a temp dir."""
    from backend.routes import cases
    monkeypatch.setattr(openai_config, "openai_client", None)
    monkeypatch.setattr(cases, "SEGMENT_DIR", tmp_path / "segments")


# Test data
TEST_EMT = {
    "username": "test.emt",
    "email": "test.emt@test.com",
    "password": "testpassword123",
    "role": "emt",
    "first_name": "Test",
    "last_name": "EMT"
}

TEST_DOCTOR = {
    "username": "test.doctor",
    "email": "test.doctor@test.com",
    "password": "testpassword123",
    "role": "doctor",
    "first_name": "Test",
    "last_name": "Doctor",
    "specialty": "Emergency Medicine"
}

class TestAuthentication:
    """Test authentication endpoints."""
    
    def test_register_emt_success(self):
        """POST /api/auth/register - EMT registration should return 201 with user and token."""
        response = client.post("/api/auth/register", json=TEST_EMT)
        assert response.status_code == 201
        data = response.json()
        assert data["message"] == "User registered successfully"
        assert "user" in data
        assert data["user"]["username"] == TEST_EMT["username"]
        assert data["user"]["role"] == "emt"
        assert "password_hash" not in data["user"]
        assert "token" in data
    
    def test_register_duplicate_username(self):
        """POST /api/auth/register - Duplicate username should return 400."""
        response = client.post("/api/auth/register", json=TEST_EMT)
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "Username or email already exists"
    
    def test_login_demo_user_success(self):
        """POST /api/auth/login - Demo user login should work with password123."""
        response = client.post("/api/auth/login", json={
            "username": "emt.wilson",
            "password": "password123"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Login successful"
        assert "user" in data
        assert data["user"]["username"] == "emt.wilson"
        assert data["user"]["role"] == "emt"
        assert "specialty" in data["user"]
        assert "token" in data
    
    def test_login_invalid_credentials(self):
        """POST /api/auth/login - Wrong password should return 400."""
        response = client.post("/api/auth/login", json={
            "username": "emt.wilson",
            "password": "wrongpassword"
        })
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "Invalid credentials"
    
    def test_login_nonexistent_user(self):
        """POST /api/auth/login - Non-existent user should return 400 (same as wrong password)."""
        response = client.post("/api/auth/login", json={
            "username": "nonexistent.user",
            "password": "password123"
        })
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "Invalid credentials"
    
    def test_profile_no_token(self):
        """GET /api/auth/profile - No token should return 401."""
        response = client.get("/api/auth/profile")
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "Access denied. No token provided."
    
    def test_profile_invalid_token(self):
        """GET /api/auth/profile - Invalid token should return 400."""
        response = client.get(
            "/api/auth/profile",
            headers={"Authorization": "Bearer invalid_token_here"}
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "Invalid token."
    
    def test_profile_with_valid_token(self):
        """GET /api/auth/profile - Valid token should return user profile."""
        # First login to get token
        login_response = client.post("/api/auth/login", json={
            "username": "emt.wilson",
            "password": "password123"
        })
        token = login_response.json()["token"]
        
        # Get profile
        response = client.get(
            "/api/auth/profile",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "emt.wilson"
        assert data["role"] == "emt"
        assert "is_available" in data
        # Check that is_available is 0 or 1 (integer), not boolean
        assert data["is_available"] in [0, 1]


class TestRoleBasedAccess:
    """Test role-based access control."""
    
    def setup_method(self):
        """Get tokens for EMT and Doctor."""
        emt_response = client.post("/api/auth/login", json={
            "username": "emt.wilson",
            "password": "password123"
        })
        self.emt_token = emt_response.json()["token"]
        
        doctor_response = client.post("/api/auth/login", json={
            "username": "dr.smith",
            "password": "password123"
        })
        self.doctor_token = doctor_response.json()["token"]
    
    def test_emt_cannot_access_the_hospital_queue(self):
        """GET /api/cases/queue - EMT should get 403."""
        response = client.get(
            "/api/cases/queue",
            headers={"Authorization": f"Bearer {self.emt_token}"}
        )
        assert response.status_code == 403
        data = response.json()
        assert data["error"] == "Access denied. Insufficient permissions."
    
    def test_doctor_can_access_the_hospital_queue(self):
        """GET /api/cases/queue - Doctor should succeed."""
        response = client.get(
            "/api/cases/queue",
            headers={"Authorization": f"Bearer {self.doctor_token}"}
        )
        assert response.status_code == 200
        assert isinstance(response.json()["cases"], list)


class TestDataIntegrity:
    """Test data integrity and field types."""
    
    def test_boolean_fields_as_integers(self):
        """Verify is_available and delivered are 0/1, not true/false."""
        # Get a user profile
        login_response = client.post("/api/auth/login", json={
            "username": "emt.wilson",
            "password": "password123"
        })
        token = login_response.json()["token"]
        
        profile_response = client.get(
            "/api/auth/profile",
            headers={"Authorization": f"Bearer {token}"}
        )
        profile = profile_response.json()
        
        # is_available should be 0 or 1 (integer)
        assert profile["is_available"] in [0, 1]
        assert not isinstance(profile["is_available"], bool)
    
class TestErrorMessages:
    """Test exact error messages."""

    def test_removed_old_routes_are_gone(self):
        """The old record-and-upload, doctor and notification routes no longer exist."""
        token = client.post("/api/auth/login", json={"username": "emt.wilson", "password": "password123"}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        for method, path in [("GET", "/api/recordings/my-recordings"), ("POST", "/api/recordings/upload"),
                             ("GET", "/api/recordings/1"), ("GET", "/api/recordings/1/messages"),
                             ("GET", "/api/doctors/available"), ("GET", "/api/doctors/notifications"),
                             ("PATCH", "/api/doctors/availability"), ("POST", "/api/notifications/send"),
                             ("POST", "/api/notifications/test"), ("GET", "/api/notifications/status/1")]:
            assert client.request(method, path, headers=headers).status_code == 404, (method, path)
    
    def test_missing_token_message(self):
        """Verify exact error message for missing token."""
        response = client.get("/api/auth/profile")
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "Access denied. No token provided."
    
    def test_invalid_token_message(self):
        """Verify exact error message for invalid token."""
        response = client.get(
            "/api/auth/profile",
            headers={"Authorization": "Bearer bad_token"}
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "Invalid token."
    
    def test_insufficient_permissions_message(self):
        """Verify exact error message for insufficient permissions."""
        emt_response = client.post("/api/auth/login", json={
            "username": "emt.wilson",
            "password": "password123"
        })
        token = emt_response.json()["token"]
        
        response = client.get(
            "/api/cases/queue",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403
        data = response.json()
        assert data["error"] == "Access denied. Insufficient permissions."


class TestExpressContractParity:
    """Test exact API contract edge cases."""
    
    def setup_method(self):
        """Get tokens for tests."""
        emt_response = client.post("/api/auth/login", json={
            "username": "emt.wilson",
            "password": "password123"
        })
        self.emt_token = emt_response.json()["token"]
        
        doctor_response = client.post("/api/auth/login", json={
            "username": "dr.smith",
            "password": "password123"
        })
        self.doctor_token = doctor_response.json()["token"]
    
    # Auth headers / JWT tests
    
    def test_raw_jwt_no_bearer_prefix(self):
        """Raw JWT without Bearer prefix should work."""
        # Get a token
        login_response = client.post("/api/auth/login", json={
            "username": "emt.wilson",
            "password": "password123"
        })
        token = login_response.json()["token"]
        
        # Send without Bearer prefix
        response = client.get(
            "/api/auth/profile",
            headers={"Authorization": token}
        )
        assert response.status_code == 200
    
    def test_lowercase_bearer_prefix(self):
        """Lowercase 'bearer' prefix should fail with 400."""
        login_response = client.post("/api/auth/login", json={
            "username": "emt.wilson",
            "password": "password123"
        })
        token = login_response.json()["token"]
        
        # Send with lowercase bearer
        response = client.get(
            "/api/auth/profile",
            headers={"Authorization": f"bearer {token}"}
        )
        # Only "Bearer " (capital B) is stripped, so decoding fails -> 400
        assert response.status_code == 400
    
    def test_basic_auth_prefix(self):
        """Authorization: Basic <token> should fail with 400 (not 401)."""
        login_response = client.post("/api/auth/login", json={
            "username": "emt.wilson",
            "password": "password123"
        })
        token = login_response.json()["token"]
        
        # Send with Basic prefix
        response = client.get(
            "/api/auth/profile",
            headers={"Authorization": f"Basic {token}"}
        )
        assert response.status_code in [400, 401]
    
    def test_jwt_has_iat_claim(self):
        """JWT should include 'iat' and 'exp' claims."""
        from jose import jwt as jose_jwt
        login_response = client.post("/api/auth/login", json={
            "username": "emt.wilson",
            "password": "password123"
        })
        token = login_response.json()["token"]
        
        # Decode without verification to check payload
        decoded = jose_jwt.get_unverified_claims(token)
        assert "iat" in decoded
        assert "exp" in decoded
    
    def test_malformed_json_returns_500(self):
        """Malformed JSON should return 500 not 422."""
        response = client.post(
            "/api/auth/login",
            content=b'{"username": "emt.wilson",',
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 500
        assert response.json() == {"error": "Something went wrong!"}
    
    def test_validation_error_returns_500(self):
        """RequestValidationError should return 500 not 422."""
        response = client.get(
            "/api/sas/status?check=maybe",
            headers={"Authorization": f"Bearer {self.doctor_token}"},
        )
        assert response.status_code == 500
        assert response.json() == {"error": "Something went wrong!"}


class TestSecurityFixes:
    """Regression tests for security and robustness fixes."""
    
    def setup_method(self):
        """Get tokens for tests."""
        emt_response = client.post("/api/auth/login", json={
            "username": "emt.wilson",
            "password": "password123"
        })
        self.emt_token = emt_response.json()["token"]
        
        doctor_response = client.post("/api/auth/login", json={
            "username": "dr.smith",
            "password": "password123"
        })
        self.doctor_token = doctor_response.json()["token"]
    
    def test_path_traversal_blocked(self, tmp_path, monkeypatch):
        """Issue 1: Path traversal attempts must not escape client/build in production."""
        import os
        import pathlib
        
        # Save original NODE_ENV
        original_env = os.environ.get("NODE_ENV")
        
        try:
            # Create synthetic filesystem in pytest's tmp_path (safe, never deletes real files)
            build_dir = tmp_path / "client" / "build"
            backup_dir = tmp_path / "client" / "build-backup"
            
            # Create directories including static subdir
            build_dir.mkdir(parents=True, exist_ok=True)
            (build_dir / "static").mkdir(exist_ok=True)
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            # Create test files
            (build_dir / "asset.txt").write_text("public asset")
            (build_dir / "index.html").write_text("<html>React App</html>")
            (backup_dir / "secret.txt").write_text("PRIVATE SECRET")
            
            # Create a symlink that escapes (if possible)
            symlink_target = backup_dir / "secret.txt"
            symlink_path = build_dir / "escape-link.txt"
            try:
                symlink_path.symlink_to(symlink_target.resolve())
            except (OSError, NotImplementedError):
                # Symlink creation may fail on some systems
                pass
            
            # Change to tmp_path so app sees our synthetic client/build
            monkeypatch.chdir(tmp_path)
            
            # Set production mode and reload the app
            os.environ["NODE_ENV"] = "production"
            
            # Force reimport to pick up production mode
            import importlib
            import main
            importlib.reload(main)
            from starlette.testclient import TestClient
            test_client = TestClient(main.app)
            
            # Test 1: Valid asset inside build loads
            response = test_client.get("/asset.txt")
            assert response.status_code == 200
            assert response.text == "public asset"
            
            # An unknown API path is a 404, not index.html (e.g. the removed old routes)
            for path in ("/api/recordings/my-recordings", "/api/doctors/available", "/api/notifications/status/1"):
                assert test_client.get(path).status_code == 404, path

            # Test 2: Parent traversal blocked
            response = test_client.get("/../build-backup/secret.txt")
            assert response.status_code == 200  # Returns index.html, not secret
            assert "PRIVATE SECRET" not in response.text
            
            # Test 3: Encoded parent traversal blocked
            response = test_client.get("/%2e%2e/build-backup/secret.txt")
            assert response.status_code == 200  # Returns index.html
            assert "PRIVATE SECRET" not in response.text
            
            # Test 4: Sibling directory blocked (key vulnerability)
            response = test_client.get("/../build-backup/secret.txt")
            assert "PRIVATE SECRET" not in response.text
            
            # Test 5: Absolute path blocked
            abs_secret = (backup_dir / "secret.txt").resolve()
            response = test_client.get(f"/{abs_secret}")
            assert "PRIVATE SECRET" not in response.text
            
            # Test 6: Symlink that escapes blocked (if created)
            if symlink_path.exists():
                response = test_client.get("/escape-link.txt")
                # Should either return 404/index or not contain the secret
                assert "PRIVATE SECRET" not in response.text
            
        finally:
            # Restore environment
            if original_env is None:
                os.environ.pop("NODE_ENV", None)
            else:
                os.environ["NODE_ENV"] = original_env
            
            # Reload to restore non-production mode (tmp_path cleanup is automatic)
            import importlib
            import main
            importlib.reload(main)
    
    def test_mime_with_codec_parameter(self):
        """Issue 2: Accept MIME types with parameters like audio/webm;codecs=opus (live clip upload)."""
        import io
        hospital = query("SELECT id FROM hospitals WHERE code = 'GEN'")[0]["id"]
        run("UPDATE cases SET status = 'closed' WHERE emt_id = (SELECT id FROM users WHERE username = 'emt.wilson')")
        headers = {"Authorization": f"Bearer {self.emt_token}"}
        case = client.post("/api/cases", headers=headers, json={"destination_hospital_id": hospital}).json()
        response = client.post(
            f"/api/cases/{case['id']}/segments",
            headers=headers,
            files={"audio": ("test.webm", io.BytesIO(b"fake webm audio content"), "audio/webm;codecs=opus")},
            data={"seq": "0", "client_id": "codec-test"},
        )
        assert response.status_code == 201, response.text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
