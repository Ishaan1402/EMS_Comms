import pytest
from fastapi.testclient import TestClient
import os

# Set test environment BEFORE importing main
os.environ["JWT_SECRET"] = "test-secret-key-for-testing-only"
os.environ["OPENAI_API_KEY"] = "test"

from main import app
from database import query, run

client = TestClient(app)

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
    """Test authentication endpoints for parity."""
    
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
    
    def test_emt_cannot_access_doctor_notifications(self):
        """GET /api/doctors/notifications - EMT should get 403."""
        response = client.get(
            "/api/doctors/notifications",
            headers={"Authorization": f"Bearer {self.emt_token}"}
        )
        assert response.status_code == 403
        data = response.json()
        assert data["error"] == "Access denied. Insufficient permissions."
    
    def test_doctor_can_access_notifications(self):
        """GET /api/doctors/notifications - Doctor should succeed."""
        response = client.get(
            "/api/doctors/notifications",
            headers={"Authorization": f"Bearer {self.doctor_token}"}
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestDoctorsEndpoints:
    """Test doctors endpoints."""
    
    def test_get_available_doctors_no_auth(self):
        """GET /api/doctors/available - Public endpoint, no auth required."""
        response = client.get("/api/doctors/available")
        assert response.status_code == 200
        doctors = response.json()
        assert isinstance(doctors, list)
        # Should have dr.smith and dr.jones
        assert len(doctors) >= 2
        # Check structure
        if len(doctors) > 0:
            doctor = doctors[0]
            assert "id" in doctor
            assert "first_name" in doctor
            assert "last_name" in doctor
            assert "specialty" in doctor
            assert "phone" in doctor
            assert "email" in doctor
    
    def test_notifications_sorting(self):
        """GET /api/doctors/notifications - Test sorting options."""
        doctor_response = client.post("/api/auth/login", json={
            "username": "dr.smith",
            "password": "password123"
        })
        token = doctor_response.json()["token"]
        
        # Test different sort options
        for sort_by in ["newest", "oldest", "priority"]:
            response = client.get(
                f"/api/doctors/notifications?sortBy={sort_by}",
                headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == 200
            assert isinstance(response.json(), list)


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
    
    def test_risk_score_range(self):
        """Verify risk_score is 0-10, not 0-100."""
        # This will be tested after we create a recording with processing
        # For now, just verify schema allows 0-10
        pass
    
    def test_priority_level_range(self):
        """Verify priority_level is 1-5."""
        # This will be tested after we create a recording with processing
        pass


class TestErrorMessages:
    """Test exact error messages for parity."""
    
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
            "/api/doctors/notifications",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403
        data = response.json()
        assert data["error"] == "Access denied. Insufficient permissions."


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
