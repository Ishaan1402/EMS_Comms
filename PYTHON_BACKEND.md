# Python/FastAPI Backend Port

This document describes the Python/FastAPI backend port from the original Node/Express implementation.

## Overview

The Python backend is a behavior-preserving port that maintains exact API compatibility with the original Node/Express backend. All endpoints, request/response formats, status codes, authentication, and business logic have been preserved.

## Running the Python Backend

### Prerequisites

- Python 3.8 or higher
- Same environment variables as Node backend

### Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Start the server
python3 main.py

# Or use the start script
chmod +x start-python.sh
./start-python.sh
```

The server will run on port 5000 (or PORT environment variable).

### Environment Variables

Same as the Node backend:

```env
JWT_SECRET=your-super-secret-jwt-key-here
OPENAI_API_KEY=your-openai-api-key-here
TWILIO_ACCOUNT_SID=your-twilio-account-sid
TWILIO_AUTH_TOKEN=your-twilio-auth-token
TWILIO_PHONE_NUMBER=+1234567890
SENDGRID_API_KEY=your-sendgrid-api-key
SENDGRID_FROM_EMAIL=noreply@yourapp.com
PORT=5000
NODE_ENV=production  # For serving React build
FRONTEND_URL=http://localhost:3000
```

## Project Structure

```
/workspace/
├── main.py                 # FastAPI application entry point
├── database.py            # SQLite database module
├── requirements.txt       # Python dependencies
├── middleware/
│   └── auth.py           # JWT authentication middleware
├── routes/
│   ├── auth.py          # Authentication endpoints
│   ├── recordings.py    # Recording upload and processing
│   ├── doctors.py       # Doctor dashboard endpoints
│   └── notifications.py # Notification sending
├── uploads/              # Audio file storage
└── asclepius.db         # SQLite database file
```

## Dependencies

- **FastAPI**: Web framework (equivalent to Express)
- **Uvicorn**: ASGI server
- **python-jose**: JWT token handling
- **bcrypt**: Password hashing (compatible with bcryptjs)
- **OpenAI**: Whisper transcription and GPT-4 analysis
- **Twilio**: SMS notifications
- **SendGrid**: Email notifications
- **python-multipart**: File upload handling

## API Parity

See `PARITY_CHECKLIST.md` for complete API documentation and parity validation.

### Key Compatibility Notes

1. **SQLite Database**: Uses the same `asclepius.db` file as Node backend
2. **Password Hashes**: bcrypt hashes are compatible with Node's bcryptjs
3. **JWT Tokens**: Same payload structure `{id, username, role}` with 24h expiry
4. **Boolean Fields**: Stored as 0/1 integers in SQLite (not true/false)
5. **Risk Scoring**: 0-10 scale (not 0-100)
6. **Priority Levels**: 1-5 (lower = higher priority)
7. **Background Processing**: Uses FastAPI BackgroundTasks (no Celery/Redis)

### Known Behavioral Differences

These differences are acceptable and documented:

1. **Multipart Error Messages**: FastAPI may return slightly different error messages for invalid file uploads
2. **OpenAI SDK Errors**: Error strings may vary between SDK versions
3. **Background Concurrency**: Different async runtime may interleave operations differently under high load
4. **Timestamp Precision**: Minor differences in SQLite CURRENT_TIMESTAMP formatting

### Preserved Quirks

These quirks from the Node backend were intentionally preserved:

1. **urgency_level NOT updated**: LLM analysis returns urgency_level but the UPDATE query doesn't include it
2. **SMS template field references**: Template references EMT names that may not be in the recording object
3. **Notification split**: `notifyDoctors()` only creates notification rows; `/api/notifications/send` performs actual delivery

## Testing

Run the parity test suite:

```bash
pip install pytest httpx
pytest test_parity.py -v
```

All 18 parity tests should pass.

## Frontend Integration

The React frontend works identically with the Python backend:

1. Start Python backend: `python3 main.py` (port 5000)
2. Start React frontend: `cd client && npm start` (port 3000)
3. No frontend code changes required

## Production Deployment

For production deployment, the Python backend can:

1. Serve static React build files (when `NODE_ENV=production`)
2. Handle same PORT environment variable
3. Use same CORS and security headers

## Development vs Node Backend

### Advantages of Python Backend

- Type hints for better IDE support
- Native async/await syntax
- Modern dependency management with pip
- OpenAI Python SDK is more actively maintained

### Migration Path

1. Test Python backend in development
2. Run Python backend alongside Node backend
3. Switch traffic to Python backend
4. Retire Node backend after validation

## Troubleshooting

### Database Issues

If the database file becomes corrupted:

```bash
rm asclepius.db
python3 main.py  # Will recreate with seed data
```

### API Key Issues

If OpenAI/Twilio/SendGrid fail, the server will:

- Start successfully (allowing auth/profile endpoints to work)
- Return errors for endpoints requiring those services
- Log clear error messages

### Port Conflicts

If port 5000 is in use:

```bash
PORT=8000 python3 main.py
```

## Support

For issues specific to the Python port, check:

1. `PARITY_CHECKLIST.md` for API documentation
2. `test_parity.py` for endpoint examples
3. Original Node backend in `server.js`, `routes/`, etc.
