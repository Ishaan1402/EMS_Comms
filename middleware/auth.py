from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from datetime import datetime, timedelta
import os
from typing import Optional

# JWT configuration
JWT_SECRET = os.getenv("JWT_SECRET")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

security = HTTPBearer(auto_error=False)

def create_access_token(data: dict) -> str:
    """Create a JWT token with 24h expiry."""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> dict:
    """
    Verify JWT token and return decoded payload.
    - Missing token -> 401: "Access denied. No token provided."
    - Invalid token -> 400: "Invalid token."
    """
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail={"error": "Access denied. No token provided."}
        )
    
    try:
        token = credentials.credentials
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid token."}
        )

def get_current_user(user_data: dict = Depends(verify_token)) -> dict:
    """Get current authenticated user from token."""
    return user_data

def require_role(allowed_roles: list):
    """
    Dependency to require specific roles.
    Returns 403 if user doesn't have required role.
    """
    def role_checker(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail={"error": "Access denied. Insufficient permissions."}
            )
        return user
    return role_checker
