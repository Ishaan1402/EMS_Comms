from fastapi import Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from datetime import datetime, timedelta
import os
import time
from typing import Optional

JWT_SECRET = os.getenv("JWT_SECRET")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

security = HTTPBearer(auto_error=False)

# Rendered by main.py as {"error": ...} with the given status code.
class APIError(Exception):
    def __init__(self, status_code: int, error: str):
        self.status_code = status_code
        self.error = error
        super().__init__(error)

def create_access_token(data: dict) -> str:
    """Create a JWT token with 24h expiry and an iat claim."""
    to_encode = data.copy()
    now = datetime.utcnow()
    expire = now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({
        "iat": int(time.time()),
        "exp": expire
    })
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> dict:
    """
    Verify JWT token and return decoded payload.
    - Missing token -> 401: {"error": "Access denied. No token provided."}
    - Invalid token (including wrong prefix) -> 400: {"error": "Invalid token."}
    """
    if not credentials:
        raise APIError(401, "Access denied. No token provided.")
    
    try:
        # HTTPBearer already strips "Bearer " prefix (case-sensitive)
        # If someone sends lowercase "bearer", HTTPBearer won't recognize it and credentials will be None (caught above)
        # If someone sends "Basic" or other prefix, token decode will fail -> 400
        token = credentials.credentials
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise APIError(400, "Invalid token.")

def get_current_user_raw_header(request: Request) -> dict:
    """
    Accepts a raw JWT, or one prefixed with exactly "Bearer " (case-sensitive).
    Any other prefix ("bearer ", "Basic ") fails decoding -> 400.
    """
    auth_header = request.headers.get("Authorization")
    
    if not auth_header:
        raise APIError(401, "Access denied. No token provided.")
    
    token = auth_header
    if token.startswith("Bearer "):
        token = token[7:]
    
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise APIError(400, "Invalid token.")

def get_current_user(request: Request) -> dict:
    """Get current authenticated user from token."""
    return get_current_user_raw_header(request)

def require_role(allowed_roles: list):
    """
    Dependency to require specific roles.
    Returns 403 if user doesn't have required role.
    """
    def role_checker(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in allowed_roles:
            raise APIError(403, "Access denied. Insufficient permissions.")
        return user
    return role_checker
