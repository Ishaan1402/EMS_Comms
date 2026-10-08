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

load_dotenv()

from middleware.auth import APIError
from routes import auth, recordings, doctors, notifications, messages, cases
from database import init_database, insert_sample_data
import case_assessment

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Missing keys only fail the routes that need them; warn instead of exiting.
    expected_env_vars = ["JWT_SECRET", "OPENAI_API_KEY", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"]
    missing_vars = [var for var in expected_env_vars if not os.getenv(var)]
    if missing_vars:
        print(f"⚠️  Missing environment variables: {', '.join(missing_vars)}. Routes that need them will fail.")

    init_database()
    cases.fail_interrupted_segments()
    case_assessment.fail_interrupted_assessments()
    for case_id in case_assessment.unassessed_open_cases():
        case_assessment.runner.start(case_id, force=True)
    if os.getenv("SEED_DEMO_USERS", "").lower() in ("1", "true", "yes"):
        insert_sample_data()
    yield

app = FastAPI(title="Asclepius EMT System", lifespan=lifespan)

@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error}
    )

# Validation errors are reported as 500s, never 422.
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # A missing audio file on upload gets the upload route's own error message.
    if request.url.path == "/api/recordings/upload":
        return JSONResponse(
            status_code=500,
            content={"error": "Server error during upload"}
        )
    
    print(f"Validation error: {exc.errors()}")
    return JSONResponse(
        status_code=500,
        content={"error": "Something went wrong!"}
    )

# Malformed JSON bodies are 500s.
@app.exception_handler(json.JSONDecodeError)
async def json_decode_error_handler(request: Request, exc: json.JSONDecodeError):
    return JSONResponse(
        status_code=500,
        content={"error": "Something went wrong!"}
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers (helmet 4.x defaults)
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["x-frame-options"] = "SAMEORIGIN"
    response.headers["x-xss-protection"] = "0"
    response.headers["x-dns-prefetch-control"] = "off"
    response.headers["x-download-options"] = "noopen"
    response.headers["x-permitted-cross-domain-policies"] = "none"
    response.headers["referrer-policy"] = "no-referrer"
    response.headers["cross-origin-opener-policy"] = "same-origin"
    response.headers["cross-origin-resource-policy"] = "same-origin"
    response.headers["origin-agent-cluster"] = "?1"
    
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
    
    response.headers["strict-transport-security"] = "max-age=15552000; includeSubDomains"
    
    return response

uploads_dir = pathlib.Path("uploads")
uploads_dir.mkdir(exist_ok=True)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(recordings.router, prefix="/api/recordings", tags=["recordings"])
app.include_router(messages.router, prefix="/api/recordings", tags=["messages"])
app.include_router(doctors.router, prefix="/api/doctors", tags=["doctors"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["notifications"])
app.include_router(cases.router, prefix="/api/cases", tags=["cases"])
app.include_router(messages.case_router, prefix="/api/cases", tags=["messages"])
app.include_router(cases.hospitals_router, prefix="/api/hospitals", tags=["hospitals"])

# Serve React app in production
if os.getenv("NODE_ENV") == "production":
    client_build = pathlib.Path("client/build")
    if client_build.exists():
        app.mount("/static", StaticFiles(directory="client/build/static"), name="static")
        
        @app.get("/{full_path:path}")
        async def serve_react_app(full_path: str):
            # Resolve the path and ensure it stays within client_build
            file_path = client_build / full_path
            try:
                # Resolve to absolute path and check it's within client_build
                resolved_file = file_path.resolve()
                resolved_build = client_build.resolve()
                
                # Ensure the resolved path is the build dir itself or a real descendant
                # Use filesystem ancestry check: resolved_build must be in resolved_file's parents
                # or be the same path. String startswith is insufficient (allows siblings like build-backup)
                try:
                    # is_relative_to is Python 3.9+
                    if not resolved_file.is_relative_to(resolved_build):
                        return FileResponse(client_build / "index.html")
                except AttributeError:
                    # Fallback for Python < 3.9: check if build is in file's parents
                    if resolved_file != resolved_build and resolved_build not in resolved_file.parents:
                        return FileResponse(client_build / "index.html")
                
                # Check if it's a file that exists
                if resolved_file.exists() and resolved_file.is_file():
                    return FileResponse(resolved_file)
            except (ValueError, OSError):
                # Invalid path - return index.html
                pass
            
            return FileResponse(client_build / "index.html")

# Unknown routes get a plain HTML 404 page, not JSON.
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
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

# Wrong methods get the same HTML 404 instead of a 405.
@app.exception_handler(405)
async def method_not_allowed_handler(request: Request, exc):
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
