import os
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from dotenv import load_dotenv
import pathlib
import json

# Load environment variables
load_dotenv()

# Import custom exception
from middleware.auth import APIError

# Import routers
from routes import auth, recordings, doctors, notifications

app = FastAPI(title="Asclepius EMT System")

# Custom exception handler for Express-style error responses
@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error}
    )

# Handle FastAPI validation errors like Express (map to 500 or appropriate status)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Express typically returns 500 "Server error" for malformed requests
    # or lets them through as undefined/null values
    # We'll map validation errors to Express-style responses
    print(f"Validation error: {exc.errors()}")
    return JSONResponse(
        status_code=500,
        content={"error": "Something went wrong!"}
    )

# Handle JSON decode errors (malformed JSON body)
@app.exception_handler(json.JSONDecodeError)
async def json_decode_error_handler(request: Request, exc: json.JSONDecodeError):
    return JSONResponse(
        status_code=500,
        content={"error": "Something went wrong!"}
    )

# CORS configuration - match Express cors()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers (helmet equivalent) - match Express helmet defaults
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # Note: helmet also adds other headers, but these are the key ones Express sets
    return response

# Create uploads directory if it doesn't exist
uploads_dir = pathlib.Path("uploads")
uploads_dir.mkdir(exist_ok=True)

# Static files for uploaded audio
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Routes
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(recordings.router, prefix="/api/recordings", tags=["recordings"])
app.include_router(doctors.router, prefix="/api/doctors", tags=["doctors"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["notifications"])

# Serve React app in production
if os.getenv("NODE_ENV") == "production":
    client_build = pathlib.Path("client/build")
    if client_build.exists():
        app.mount("/static", StaticFiles(directory="client/build/static"), name="static")
        
        @app.get("/{full_path:path}")
        async def serve_react_app(full_path: str):
            file_path = client_build / full_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(client_build / "index.html")

# Error handling - Express style
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    print(f"Error: {exc}")
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"error": "Something went wrong!"}
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 5000))
    print(f"🏥 Asclepius EMT System running on port {port}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
