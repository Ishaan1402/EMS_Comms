from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import asyncio
import bcrypt
import json
from database import query, run
from middleware.auth import create_access_token, get_current_user, APIError
from routes.recordings import fast_executor

router = APIRouter()

class RegisterRequest(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    specialty: Optional[str] = None

class LoginRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None

@router.post("/register", status_code=201)
async def register(request: Request):
    """
    Register new user.
    Missing or invalid fields -> 500, not 422.
    """
    try:
        # Get body manually to avoid validation errors
        try:
            body = await request.json()
        except:
            body = {}
        
        username = body.get("username")
        email = body.get("email")
        password = body.get("password")
        role = body.get("role")
        first_name = body.get("first_name")
        last_name = body.get("last_name")
        phone = body.get("phone")
        specialty = body.get("specialty")
        
        existing_users = query(
            'SELECT * FROM users WHERE username = ? OR email = ?',
            (username, email)
        )
        
        if len(existing_users) > 0:
            return JSONResponse(
                status_code=400,
                content={"error": "Username or email already exists"}
            )
        
        # Hash password - if missing, will fail with generic 500
        password_hash = (await asyncio.get_running_loop().run_in_executor(
            fast_executor, bcrypt.hashpw, password.encode('utf-8'), bcrypt.gensalt(rounds=10)
        )).decode('utf-8')
        
        # Insert new user - if required fields missing, SQL will fail -> 500
        result = run(
            'INSERT INTO users (username, email, password_hash, role, first_name, last_name, phone, specialty) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (username, email, password_hash, role, first_name, last_name, phone, specialty)
        )
        
        new_users = query(
            'SELECT id, username, email, role, first_name, last_name FROM users WHERE id = ?',
            (result["id"],)
        )
        
        new_user = new_users[0]
        
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
async def login(request: Request):
    """
    Login user.
    Malformed JSON -> 500 "Something went wrong!"
    Missing fields -> 400 "Invalid credentials"
    """
    try:
        # Get body - let JSONDecodeError propagate to global handler
        body = await request.json()
        
        username = body.get("username")
        password = body.get("password")
        
        users = query(
            'SELECT * FROM users WHERE username = ?',
            (username,)
        )
        
        if len(users) == 0:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid credentials"}
            )
        
        user = users[0]
        
        # Check password - if password is None, will fail -> 500
        is_valid_password = await asyncio.get_running_loop().run_in_executor(
            fast_executor, bcrypt.checkpw, password.encode('utf-8'), user["password_hash"].encode('utf-8')
        )
        
        if not is_valid_password:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid credentials"}
            )
        
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
    
    except json.JSONDecodeError:
        # Re-raise to be caught by global JSON decode error handler
        raise
    except Exception as error:
        print(f"Login error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error during login"}
        )

@router.get("/profile")
async def get_profile(request: Request):
    """Get current user profile."""
    try:
        current_user = get_current_user(request)
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
    
    except APIError as error:
        # Re-raise APIError to be handled by the exception handler in main.py
        raise
    except Exception as error:
        print(f"Profile error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error getting profile"}
        )
