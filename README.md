# PasteBin

A secure, modern, full-stack PasteBin web application featuring custom expiration durations, instant owner revocation (Kill Switch), and a tamper-evident, append-only cryptographic audit trail.

---

## Architecture Overview

- **Frontend**: React 19 SPA built with Vite, Vanilla CSS, and modern responsive UI.
- **Backend**: FastAPI (Python 3.13) REST API with asynchronous request handling.
- **Storage**: SQLite with thread-safe connection pooling, automated schema migrations, and periodic background worker cleanup for expired pastes.
- **Security & Integrity**:
  - Cryptographically secure random identifiers (`secrets.token_urlsafe`).
  - Owner revocation tokens hashed with SHA-256 (raw tokens are never persisted).
  - Append-only, tamper-evident audit logging chained with cryptographic SHA-256 hashes (`GENESIS` to current event).
  - Built-in sliding-window in-memory rate limiting.
  - Safe fail-closed access controls.

---

## Project Structure

```
PasteBin/
├── .gitignore               # Comprehensive Git exclusion rules
├── README.md                # Project documentation & setup instructions
├── backend/
│   ├── audit.py             # Tamper-evident audit chain implementation
│   ├── database.py          # SQLite connection and migration logic
│   ├── main.py              # FastAPI application endpoints & lifespan worker
│   ├── paste_repository.py  # CRUD operations & revocation logic
│   ├── pytest.ini           # Test suite configuration
│   ├── rate_limiter.py      # In-memory IP rate limiter
│   ├── requirements.txt     # Python backend dependencies
│   ├── schemas.py           # Pydantic v2 validation models
│   └── tests/               # Comprehensive automated test suite
└── frontend/
    ├── .env.example         # Environment template for frontend
    ├── index.html           # HTML5 entrypoint
    ├── package.json         # Node dependencies and scripts
    ├── vite.config.js       # Vite build configuration
    ├── public/              # Static assets and favicons
    └── src/
        ├── App.jsx          # Route configuration
        ├── main.jsx         # React DOM root
        ├── index.css        # Global CSS design system
        ├── components/
        │   └── Navbar.jsx   # Top navigation component
        └── pages/
            ├── CreatePaste.jsx # Paste creation & revocation management
            └── ViewPaste.jsx   # Secure paste retrieval & display
```

---

## Getting Started

### 1. Prerequisites

- Python 3.10+
- Node.js 18+ and npm

### 2. Backend Setup

```bash
cd backend

# Create and activate virtual environment
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start FastAPI dev server
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

The backend API will be available at: `http://127.0.0.1:8000`  
Interactive Swagger docs: `http://127.0.0.1:8000/docs`

### 3. Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# (Optional) Configure environment
cp .env.example .env

# Start Vite dev server
npm run dev
```

The frontend application will be available at: `http://localhost:5173`

### 4. Running Tests

```bash
cd backend
pytest
```

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/pastes` | Create a new paste with content and expiration time |
| `GET` | `/pastes/{paste_id}` | Retrieve and display paste content (returns 410 if expired or revoked) |
| `POST` | `/pastes/{paste_id}/revoke` | Instantly revoke a paste using the owner's `X-Revoke-Token` |
| `GET` | `/admin/audit/verify` | Verify cryptographic SHA-256 audit chain integrity |
| `GET` | `/health` | Health check endpoint |

---

## License

MIT
