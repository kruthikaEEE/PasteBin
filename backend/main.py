import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import logging
import os
import secrets
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware

from audit import record_audit_event_standalone, verify_audit_chain
from database import init_db
from paste_repository import create_paste, delete_expired_pastes, get_paste_by_id, revoke_paste
from rate_limiter import rate_limit_paste_creation
from schemas import (
    AuditVerifyResponse,
    PasteCreateRequest,
    PasteCreateResponse,
    PasteRevokeResponse,
    PasteViewResponse,
)

# Configure structured, secure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pastebin")

# Periodic cleanup interval in seconds (default: 300 seconds = 5 minutes)
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "300"))


def get_current_utc_time() -> datetime:
    """
    Returns the current UTC time.
    Separated into a helper function so automated tests can deterministically
    mock or control the clock without arbitrary sleeps.
    """
    return datetime.now(timezone.utc)


async def periodic_cleanup_task(app: FastAPI, interval_seconds: int):
    """
    Background worker that periodically deletes expired pastes from SQLite.
    Runs asynchronously and delegates synchronous SQLite queries to a worker thread
    via asyncio.to_thread to avoid blocking FastAPI's event loop.
    Does NOT delete audit events.
    """
    logger.info("Starting background expired-paste cleanup worker (interval=%ds)...", interval_seconds)
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            db_path = getattr(app.state, "db_path", None)
            now = get_current_utc_time()

            deleted_count = await asyncio.to_thread(delete_expired_pastes, now=now, db_path=db_path)
            if deleted_count > 0:
                logger.info("Automatic cleanup completed: deleted %d expired record(s).", deleted_count)
        except asyncio.CancelledError:
            logger.info("Background cleanup worker stopping cleanly due to application shutdown.")
            raise
        except Exception as e:
            logger.error("Error during periodic expired-paste cleanup: %s", type(e).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan context manager:
    - On Startup: Initializes the SQLite database and starts the periodic cleanup background task.
    - On Shutdown: Cancels and cleanly awaits background task termination.
    """
    db_path = getattr(app.state, "db_path", None)
    init_db(db_path)

    cleanup_task: Optional[asyncio.Task] = None
    interval = getattr(app.state, "cleanup_interval_seconds", CLEANUP_INTERVAL_SECONDS)
    enable_worker = getattr(app.state, "enable_cleanup_worker", True)

    if enable_worker and interval > 0:
        cleanup_task = asyncio.create_task(periodic_cleanup_task(app, interval))

    yield

    if cleanup_task and not cleanup_task.done():
        cleanup_task.cancel()
        try:
            await asyncio.gather(cleanup_task, return_exceptions=True)
        except Exception:
            pass
        logger.info("Background cleanup worker terminated.")


# Initialize FastAPI application with lifespan management
app = FastAPI(
    title="Pastebin Backend API",
    description="Backend API for Pastebin project - Kill Switch & Tamper-Evident Audit Trail",
    version="2.0.0",
    lifespan=lifespan,
)

ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:5175",
    "http://127.0.0.1:5175",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

ALLOWED_ORIGIN_REGEX = r"^http://(localhost|127\.0\.0\.1):(517\d|518\d|3000)$"

ALLOWED_HEADERS = [
    "Content-Type",
    "X-Revoke-Token",
    "X-Admin-Token",
    "Authorization",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=ALLOWED_HEADERS,
)


@app.get("/", summary="Root status endpoint")
def read_root():
    return {
        "status": "online",
        "message": "Pastebin Backend is running successfully!",
        "stage": "Kill Switch & Tamper-Evident Audit Trail Complete",
    }


@app.get("/health", summary="Health check endpoint")
def health_check():
    return {
        "status": "healthy"
    }


@app.post(
    "/pastes",
    status_code=status.HTTP_201_CREATED,
    response_model=PasteCreateResponse,
    dependencies=[Depends(rate_limit_paste_creation)],
    summary="Create a new paste",
)
def create_new_paste(payload: PasteCreateRequest, request: Request):
    """
    Creates a new paste record with the specified content and expiry duration.
    
    Security & Audit:
    - Generates a secret owner revocation token returned only to the creator.
    - Persists only the SHA-256 hash of the revocation token in SQLite.
    - Atomically records a PASTE_CREATED audit event in the same transaction.
    - Rate limited and server-validated.
    - Never logs paste content or raw tokens.
    """
    now = get_current_utc_time()
    expires_at = now + timedelta(minutes=payload.expiry_minutes)
    db_path = getattr(app.state, "db_path", None)

    try:
        paste = create_paste(
            content=payload.content,
            created_at=now,
            expires_at=expires_at,
            db_path=db_path,
        )
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(ve),
        )
    except Exception as e:
        logger.error("Failed to save paste to database: %s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save paste to the database. Please try again.",
        )

    # Secure logging: metadata only, never content, never raw tokens
    byte_count = len(payload.content.encode("utf-8"))
    id_prefix = paste["id"][:4] if len(paste["id"]) >= 4 else "..."
    logger.info("Paste created (bytes=%d, expiry_min=%d, id_prefix=%s...)", byte_count, payload.expiry_minutes, id_prefix)

    origin = request.headers.get("origin")
    if origin and ("localhost" in origin or "127.0.0.1" in origin):
        frontend_base_url = origin.rstrip("/")
    else:
        frontend_base_url = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")
    shareable_url = f"{frontend_base_url}/p/{paste['id']}"

    return PasteCreateResponse(
        id=paste["id"],
        url=shareable_url,
        created_at=paste["created_at"],
        expires_at=paste["expires_at"],
        revoke_token=paste["revoke_token"],
    )


@app.post(
    "/pastes/{paste_id}/revoke",
    status_code=status.HTTP_200_OK,
    response_model=PasteRevokeResponse,
    summary="Instantly revoke a paste link (Kill Switch)",
)
def revoke_paste_endpoint(
    paste_id: str,
    x_revoke_token: Optional[str] = Header(None, alias="X-Revoke-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Instant Kill Switch:
    - Requires the owner's revocation token via X-Revoke-Token header (or Bearer auth).
    - Verifies token hash in constant time.
    - Atomically updates status to 'REVOKED' and records PASTE_REVOKED audit event.
    - Idempotent: repeated revocations do not duplicate audit events.
    - Returns HTTP 404 if paste not found, HTTP 401 if token missing or invalid.
    """
    db_path = getattr(app.state, "db_path", None)

    # Extract token from X-Revoke-Token header or Authorization Bearer header
    raw_token = x_revoke_token
    if not raw_token and authorization and authorization.startswith("Bearer "):
        raw_token = authorization.split("Bearer ", 1)[1].strip()

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing revocation token. Please provide the token in the X-Revoke-Token header.",
        )

    result = revoke_paste(paste_id=paste_id, raw_token=raw_token, db_path=db_path)

    if not result["success"]:
        if result.get("error_type") == "NOT_FOUND":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Paste not found",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing revocation token.",
        )

    id_prefix = paste_id[:4] if len(paste_id) >= 4 else "..."
    logger.info("Paste revoked successfully (id_prefix=%s...)", id_prefix)

    return PasteRevokeResponse(
        status="revoked",
        message=result["message"],
    )


@app.get(
    "/pastes/{paste_id}",
    status_code=status.HTTP_200_OK,
    response_model=PasteViewResponse,
    summary="Retrieve and view a paste by ID",
)
def get_paste(paste_id: str):
    """
    Retrieves an existing paste by its unique identifier.

    Access Control & Audit enforcement:
    1. Check SQLite for paste_id.
    2. If not found: return HTTP 404 Not Found.
    3. If status == 'REVOKED': record REVOKED_ACCESS_DENIED, return HTTP 410 Gone ("Paste has been revoked").
    4. If expired (now >= expires_at): record EXPIRED_ACCESS_DENIED, return HTTP 410 Gone ("Time limit exceeded").
    5. If active and unexpired: return HTTP 200 OK with paste content.
    """
    db_path = getattr(app.state, "db_path", None)
    id_prefix = paste_id[:4] if len(paste_id) >= 4 else "..."

    try:
        paste = get_paste_by_id(paste_id, db_path=db_path)
    except Exception as e:
        logger.error("Failed to retrieve paste from database: %s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve paste from the database. Please try again.",
        )

    # 1. Non-existent check
    if paste is None:
        logger.info("Paste lookup 404 Not Found (id_prefix=%s...)", id_prefix)
        record_audit_event_standalone(
            event_type="UNKNOWN_PASTE_ACCESS_DENIED",
            paste_id=paste_id,
            details={"reason": "not_found"},
            db_path=db_path,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Paste not found",
        )

    # 2. Kill Switch Revocation check
    if paste.get("status") == "REVOKED":
        logger.info("Paste lookup 410 Revoked (id_prefix=%s...)", id_prefix)
        record_audit_event_standalone(
            event_type="REVOKED_ACCESS_DENIED",
            paste_id=paste_id,
            details={"reason": "paste_revoked"},
            db_path=db_path,
        )
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Paste has been revoked",
        )

    # 3. Logical Expiry check: current_time >= expires_at
    if paste["expires_at"] is not None:
        now = get_current_utc_time()
        expiry_dt = datetime.fromisoformat(paste["expires_at"])
        if expiry_dt.tzinfo is None:
            expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)

        if now >= expiry_dt:
            logger.info("Paste lookup 410 Expired (id_prefix=%s...)", id_prefix)
            record_audit_event_standalone(
                event_type="EXPIRED_ACCESS_DENIED",
                paste_id=paste_id,
                details={"reason": "time_limit_exceeded"},
                db_path=db_path,
            )
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Time limit exceeded",
            )

    logger.info("Paste lookup 200 OK (id_prefix=%s...)", id_prefix)
    return PasteViewResponse(
        id=paste["id"],
        content=paste["content"],
        created_at=paste["created_at"],
        expires_at=paste["expires_at"],
    )


@app.get(
    "/admin/audit/verify",
    status_code=status.HTTP_200_OK,
    response_model=AuditVerifyResponse,
    summary="Verify the tamper-evident audit trail hash chain",
)
def verify_audit_endpoint(
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Administrative endpoint to verify audit event chain integrity.
    
    Security:
    - Protected by AUDIT_ADMIN_TOKEN environment variable.
    - If AUDIT_ADMIN_TOKEN is unset or empty, fails closed with HTTP 401.
    - Uses constant-time comparison (secrets.compare_digest).
    - Checks previous_hash chaining and recomputes SHA-256 for all events.
    """
    expected_token = os.getenv("AUDIT_ADMIN_TOKEN", "").strip()

    # Fail closed if token is not configured on the server
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Audit verification is disabled: AUDIT_ADMIN_TOKEN is not configured on the server.",
        )

    # Extract provided token
    provided_token = x_admin_token
    if not provided_token and authorization and authorization.startswith("Bearer "):
        provided_token = authorization.split("Bearer ", 1)[1].strip()

    if not provided_token or not secrets.compare_digest(provided_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing admin token.",
        )

    db_path = getattr(app.state, "db_path", None)
    result = verify_audit_chain(db_path=db_path)

    return AuditVerifyResponse(
        valid=result["valid"],
        total_events=result["total_events"],
        message=result["message"],
        error_index=result.get("error_index"),
        event_id=result.get("event_id"),
        error_details=result.get("error_details"),
    )
