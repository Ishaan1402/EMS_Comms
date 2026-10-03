import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
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
from database import init_database, insert_sample_data

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Missing keys only fail the routes that need them; warn instead of exiting.
    expected_env_vars = ["JWT_SECRET", "OPENAI_API_KEY", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"]
    missing_vars = [var for var in expected_env_vars if not os.getenv(var)]
    if missing_vars:
        print(f"⚠️  Missing environment variables: {', '.join(missing_vars)}. Routes that need them will fail.")

    init_database()
    if os.getenv("SEED_DEMO_USERS", "").lower() in ("1", "true", "yes"):
        insert_sample_data()
    yield

app = FastAPI(title="Asclepius EMT System", lifespan=lifespan)

# Custom exception handler for Express-style error responses
@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error}
    )

# Handle FastAPI validation errors like Express (map to 500)
# BUT: missing required File(...) should map to route-specific error
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Check if it's a missing file field on the upload endpoint
    # Express multer handles missing files in the route, returning "Server error during upload"
    if request.url.path == "/api/recordings/upload":
        # Missing audio file -> route-level error, not global handler
        return JSONResponse(
            status_code=500,
            content={"error": "Server error during upload"}
        )
    
    # All other validation errors -> global handler
    print(f"Validation error: {exc.errors()}")
    return JSONResponse(
        status_code=500,
        content={"error": "Something went wrong!"}
    )

# Handle JSON decode errors (malformed JSON body) - map to 500
@app.exception_handler(json.JSONDecodeError)
async def json_decode_error_handler(request: Request, exc: json.JSONDecodeError):
    return JSONResponse(
        status_code=500,
        content={"error": "Something went wrong!"}
    )

# CORS configuration - match Express cors() (NO credentials)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # Express cors() default is false
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers - match Express helmet() defaults exactly
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    
    # Helmet defaults (as of helmet 4.x used by Express)
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["x-frame-options"] = "SAMEORIGIN"  # Not DENY
    response.headers["x-xss-protection"] = "0"  # Helmet 4 disables this
    response.headers["x-dns-prefetch-control"] = "off"
    response.headers["x-download-options"] = "noopen"
    response.headers["x-permitted-cross-domain-policies"] = "none"
    response.headers["referrer-policy"] = "no-referrer"
    response.headers["cross-origin-opener-policy"] = "same-origin"
    response.headers["cross-origin-resource-policy"] = "same-origin"
    response.headers["origin-agent-cluster"] = "?1"
    
    # CSP
    response.headers["content-security-policy"] = (
        "default-src 'self';"
        "base-uri 'self';"
        "font-src 'self' https: data:;"
        "form-action 'self';"
        "frame-ancestors 'self';"
        "img-src 'self' data:;"
        "object-src 'none';"
        "script-src 'self';"
        "script-src-attr 'none';"
        "style-src 'self' https: 'unsafe-inline';"
        "upgrade-insecure-requests"
    )
    
    # HSTS
    response.headers["strict-transport-security"] = "max-age=15552000; includeSubDomains"
    
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

# 404 handler - return HTML like Express default (not JSON)
# Express returns 404 HTML for both unknown routes AND wrong methods (not 405)
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    # Match Express default 404 response (HTML with trailing newline)
    return HTMLResponse(
        content="""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Error</title>
</head>
<body>
<pre>Cannot {method} {path}</pre>
</body>
</html>
""".format(method=request.method, path=request.url.path),
        status_code=404
    )

# Wrong method (405) - Express also returns 404 HTML (not 405)
@app.exception_handler(405)
async def method_not_allowed_handler(request: Request, exc):
    # Express returns 404 for wrong methods too, not 405 (with trailing newline)
    return HTMLResponse(
        content="""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Error</title>
</head>
<body>
<pre>Cannot {method} {path}</pre>
</body>
</html>
""".format(method=request.method, path=request.url.path),
        status_code=404  # 404, not 405!
    )

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
