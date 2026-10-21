import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

from audit import record_audit_event
from database import get_db_connection

# Maximum retry attempts if an ID collision occurs
MAX_ID_COLLISION_RETRIES = 5


def generate_paste_id(nbytes: int = 8) -> str:
    """
    Generates a cryptographically secure, hard-to-guess, URL-safe random identifier.
    Example output: 'uQ1Lz5-b6sA'
    """
    return secrets.token_urlsafe(nbytes)


def generate_revoke_token(nbytes: int = 32) -> str:
    """
    Generates a high-entropy secret token for paste revocation.
    Returned only once to the creator; only its SHA-256 hash is persisted in SQLite.
    """
    return secrets.token_urlsafe(nbytes)


def hash_token(raw_token: str) -> str:
    """
    Computes the SHA-256 hexadecimal hash of a token for secure storage & comparison.
    """
    return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()


def create_paste(
    content: str,
    expires_at: Optional[Union[str, datetime]] = None,
    created_at: Optional[Union[str, datetime]] = None,
    db_path: Optional[Union[Path, str]] = None,
    max_retries: int = MAX_ID_COLLISION_RETRIES,
    id_generator: Optional[Callable[[], str]] = None,
    revoke_token_generator: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    """
    Creates a new paste and stores it in the SQLite database within a single transaction
    along with its corresponding PASTE_CREATED audit event.
    
    Security:
    - Generates a secret revocation token for the owner.
    - Stores only the SHA-256 hash of the revocation token in the database.
    - Returns the raw revocation token ONLY in the return dictionary for the creator.
    - Retries with a fresh ID if an ID collision occurs.
    """
    if content is None or not isinstance(content, str) or not content.strip():
        raise ValueError("Paste content cannot be empty or contain only whitespace.")

    # Format creation timestamp in ISO 8601 UTC format
    if created_at is None:
        formatted_created_at = datetime.now(timezone.utc).isoformat()
    elif isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        formatted_created_at = created_at.isoformat()
    else:
        formatted_created_at = str(created_at).strip()

    # Format expiry timestamp in ISO 8601 UTC format if provided
    formatted_expires_at: Optional[str] = None
    if expires_at is not None:
        if isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            formatted_expires_at = expires_at.isoformat()
        elif isinstance(expires_at, str):
            formatted_expires_at = expires_at.strip() or None

    # Generate owner revocation token and compute hash
    raw_revoke_token = revoke_token_generator() if revoke_token_generator else generate_revoke_token()
    stored_token_hash = hash_token(raw_revoke_token)

    content_bytes = len(content.encode("utf-8"))

    conn = get_db_connection(db_path)
    try:
        for attempt in range(max_retries):
            paste_id = id_generator() if id_generator else generate_paste_id()

            try:
                # Single transaction: Insert paste record AND record audit event
                with conn:
                    conn.execute(
                        """
                        INSERT INTO pastes (id, content, created_at, expires_at, status, revoke_token_hash)
                        VALUES (?, ?, ?, ?, 'ACTIVE', ?);
                        """,
                        (paste_id, content, formatted_created_at, formatted_expires_at, stored_token_hash),
                    )

                    # Append-only audit record (never stores raw content or raw tokens)
                    record_audit_event(
                        conn=conn,
                        event_type="PASTE_CREATED",
                        paste_id=paste_id,
                        details={
                            "bytes": content_bytes,
                            "has_expiry": formatted_expires_at is not None,
                        },
                        created_at=formatted_created_at,
                    )

                return {
                    "id": paste_id,
                    "content": content,
                    "created_at": formatted_created_at,
                    "expires_at": formatted_expires_at,
                    "status": "ACTIVE",
                    "revoke_token": raw_revoke_token,
                }
            except sqlite3.IntegrityError:
                if attempt == max_retries - 1:
                    raise RuntimeError("Failed to generate a unique paste ID after multiple attempts.")
                continue
    finally:
        conn.close()

    raise RuntimeError("Unexpected failure during paste creation.")


def get_paste_by_id(
    paste_id: str,
    db_path: Optional[Union[Path, str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Retrieves a paste record by its unique ID.
    Includes current status ('ACTIVE' or 'REVOKED').
    Never exposes raw revocation tokens.
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT id, content, created_at, expires_at, status
            FROM pastes
            WHERE id = ?;
            """,
            (paste_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        # Handle backward compatibility if status column is missing
        row_keys = row.keys()
        status_val = row["status"] if "status" in row_keys and row["status"] else "ACTIVE"

        return {
            "id": row["id"],
            "content": row["content"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "status": status_val,
        }
    finally:
        conn.close()


def revoke_paste(
    paste_id: str,
    raw_token: str,
    db_path: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """
    Revokes an active paste using the owner's secret revocation token.

    Transaction consistency:
    - Compares token hash using constant-time hmac.compare_digest.
    - If status is 'ACTIVE', updates status to 'REVOKED' and records 'PASTE_REVOKED'
      audit event in the same atomic database transaction.
    - If already 'REVOKED', returns idempotent success without duplicate audit events.
    """
    if not raw_token or not raw_token.strip():
        return {
            "success": False,
            "error_type": "UNAUTHORIZED",
            "message": "Missing or empty revocation token.",
        }

    conn = get_db_connection(db_path)
    try:
        # Read paste details with row lock
        cursor = conn.execute(
            """
            SELECT id, status, revoke_token_hash
            FROM pastes
            WHERE id = ?;
            """,
            (paste_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return {
                "success": False,
                "error_type": "NOT_FOUND",
                "message": "Paste not found.",
            }

        stored_hash = row["revoke_token_hash"]
        current_status = row["status"] or "ACTIVE"

        # If paste was created before revoke tokens existed, fail safe
        if not stored_hash:
            return {
                "success": False,
                "error_type": "UNAUTHORIZED",
                "message": "This paste does not have a configured revocation token.",
            }

        # Constant-time comparison
        provided_hash = hash_token(raw_token)
        if not hmac.compare_digest(stored_hash, provided_hash):
            return {
                "success": False,
                "error_type": "UNAUTHORIZED",
                "message": "Invalid revocation token.",
            }

        # Idempotent handling: If already revoked, return success without duplicate audit log
        if current_status == "REVOKED":
            return {
                "success": True,
                "already_revoked": True,
                "message": "Paste is already revoked.",
            }

        # Atomic transaction: Update status to 'REVOKED' and record PASTE_REVOKED audit event
        now_utc = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                """
                UPDATE pastes
                SET status = 'REVOKED'
                WHERE id = ?;
                """,
                (paste_id,),
            )

            record_audit_event(
                conn=conn,
                event_type="PASTE_REVOKED",
                paste_id=paste_id,
                details={"reason": "owner_revocation"},
                created_at=now_utc,
            )

        return {
            "success": True,
            "already_revoked": False,
            "message": "Paste has been revoked successfully.",
        }
    finally:
        conn.close()


def delete_expired_pastes(
    now: Optional[Union[str, datetime]] = None,
    db_path: Optional[Union[Path, str]] = None,
) -> int:
    """
    Deletes expired paste records from the SQLite database.
    Does NOT delete audit events.
    """
    if now is None:
        current_utc_str = datetime.now(timezone.utc).isoformat()
    elif isinstance(now, datetime):
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        current_utc_str = now.isoformat()
    else:
        current_utc_str = str(now).strip()

    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(
                """
                DELETE FROM pastes
                WHERE expires_at IS NOT NULL
                  AND expires_at <= ?;
                """,
                (current_utc_str,),
            )
            return cursor.rowcount
    finally:
        conn.close()
