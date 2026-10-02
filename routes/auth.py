from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import bcrypt
from database import query, run
from middleware.auth import create_access_token, get_current_user, APIError

router = APIRouter()

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    role: str
    first_name: str
    last_name: str
    phone: Optional[str] = None
    specialty: Optional[str] = None

class LoginRequest(BaseModel):
    username: str
    password: str

@router.post("/register", status_code=201)
async def register(req: RegisterRequest):
    """Register new user."""
    try:
        # Check if user already exists
        existing_users = query(
            'SELECT * FROM users WHERE username = ? OR email = ?',
            (req.username, req.email)
        )
        
        if len(existing_users) > 0:
            return JSONResponse(
                status_code=400,
                content={"error": "Username or email already exists"}
            )
        
        # Hash password
        password_hash = bcrypt.hashpw(req.password.encode('utf-8'), bcrypt.gensalt(rounds=10)).decode('utf-8')
        
        # Insert new user
        result = run(
            'INSERT INTO users (username, email, password_hash, role, first_name, last_name, phone, specialty) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (req.username, req.email, password_hash, req.role, req.first_name, req.last_name, req.phone, req.specialty)
        )
        
        # Get the new user
        new_users = query(
            'SELECT id, username, email, role, first_name, last_name FROM users WHERE id = ?',
            (result["id"],)
        )
        
        new_user = new_users[0]
        
        # Generate JWT token
        token = create_access_token({
            "id": new_user["id"],
            "username": new_user["username"],
            "role": new_user["role"]
        })
        
        return {
            "message": "User registered successfully",
            "user": new_user,
            "token": token
        }
    
    except Exception as error:
        print(f"Registration error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error during registration"}
        )

@router.post("/login")
async def login(req: LoginRequest):
    """Login user."""
    try:
        # Find user
        users = query(
            'SELECT * FROM users WHERE username = ?',
            (req.username,)
        )
        
        if len(users) == 0:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid credentials"}
            )
        
        user = users[0]
        
        # Check password
        is_valid_password = bcrypt.checkpw(req.password.encode('utf-8'), user["password_hash"].encode('utf-8'))
        
        if not is_valid_password:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid credentials"}
            )
        
        # Generate JWT token
        token = create_access_token({
            "id": user["id"],
            "username": user["username"],
            "role": user["role"]
        })
        
        return {
            "message": "Login successful",
            "user": {
                "id": user["id"],
                "username": user["username"],
                "role": user["role"],
                "first_name": user["first_name"],
                "last_name": user["last_name"],
                "specialty": user["specialty"]
            },
            "token": token
        }
    
    except Exception as error:
        print(f"Login error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error during login"}
        )

@router.get("/profile")
async def get_profile(current_user: dict = Depends(get_current_user)):
    """Get current user profile."""
    try:
        print(f"Profile request for user ID: {current_user['id']}")
        
        users = query(
            'SELECT id, username, email, role, first_name, last_name, phone, specialty, is_available FROM users WHERE id = ?',
            (current_user["id"],)
        )
        
        if len(users) == 0:
            return JSONResponse(
                status_code=404,
                content={"error": "User not found"}
            )
        
        user = users[0]
        print(f"Found user: {user}")
        
        return user
    
    except Exception as error:
        print(f"Profile error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error getting profile"}
        )
