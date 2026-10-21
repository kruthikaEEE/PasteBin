from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, Optional, Union

from database import get_db_connection

# Well-defined genesis hash for the very first event in the audit chain
GENESIS_HASH = "0" * 64


def canonicalize_details(details: Optional[Union[Dict[str, Any], str]]) -> str:
    """
    Serializes event details into a deterministic, sorted-key JSON string.
    Never includes sensitive text such as paste contents or raw revocation tokens.
    """
    if details is None:
        return "{}"
    if isinstance(details, str):
        try:
            parsed = json.loads(details)
            return json.dumps(parsed, sort_keys=True, separators=(",", ":"))
        except Exception:
            return json.dumps({"raw": details}, sort_keys=True, separators=(",", ":"))
    return json.dumps(details, sort_keys=True, separators=(",", ":"))


def compute_event_hash(
    previous_hash: str,
    event_type: str,
    paste_id: Optional[str],
    created_at: str,
    details: str,
) -> str:
    """
    Computes a cryptographic SHA-256 hash chaining the previous hash with the
    canonical representation of the current event's security-relevant fields.
    """
    canonical_dict = {
        "created_at": created_at,
        "details": details,
        "event_type": event_type,
        "paste_id": paste_id,
        "previous_hash": previous_hash,
    }
    canonical_bytes = json.dumps(
        canonical_dict,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def record_audit_event(
    conn: sqlite3.Connection,
    event_type: str,
    paste_id: Optional[str] = None,
    details: Optional[Union[Dict[str, Any], str]] = None,
    created_at: Optional[Union[str, datetime]] = None,
) -> Dict[str, Any]:
    """
    Records an append-only audit event inside an existing SQLite transaction connection.
    
    Hash chaining logic:
    1. Fetches the event_hash of the immediately preceding event.
    2. If no previous events exist, sets previous_hash to GENESIS_HASH.
    3. Computes the SHA-256 event_hash using previous_hash + canonical fields.
    4. Inserts the record into 'audit_events'.
    
    Security rule:
    - Never stores raw paste content or unhashed tokens in details.
    """
    if created_at is None:
        formatted_created_at = datetime.now(timezone.utc).isoformat()
    elif isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        formatted_created_at = created_at.isoformat()
    else:
        formatted_created_at = str(created_at).strip()

    canonical_details = canonicalize_details(details)

    # 1. Fetch previous event's hash
    cursor = conn.execute(
        """
        SELECT event_hash
        FROM audit_events
        ORDER BY id DESC
        LIMIT 1;
        """
    )
    row = cursor.fetchone()
    previous_hash = row["event_hash"] if row else GENESIS_HASH

    # 2. Compute current event's hash
    event_hash = compute_event_hash(
        previous_hash=previous_hash,
        event_type=event_type,
        paste_id=paste_id,
        created_at=formatted_created_at,
        details=canonical_details,
    )

    # 3. Insert audit event record
    insert_cursor = conn.execute(
        """
        INSERT INTO audit_events (event_type, paste_id, created_at, details, previous_hash, event_hash)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (event_type, paste_id, formatted_created_at, canonical_details, previous_hash, event_hash),
    )

    return {
        "id": insert_cursor.lastrowid,
        "event_type": event_type,
        "paste_id": paste_id,
        "created_at": formatted_created_at,
        "details": canonical_details,
        "previous_hash": previous_hash,
        "event_hash": event_hash,
    }


def record_audit_event_standalone(
    event_type: str,
    paste_id: Optional[str] = None,
    details: Optional[Union[Dict[str, Any], str]] = None,
    created_at: Optional[Union[str, datetime]] = None,
    db_path: Optional[Union[Path, str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Convenience helper to record an audit event outside of an existing transaction
    (e.g., for logging access denials or rate limits).
    Fails safely: logs and returns None if recording fails so main API is not blocked.
    """
    try:
        conn = get_db_connection(db_path)
        try:
            with conn:
                return record_audit_event(
                    conn=conn,
                    event_type=event_type,
                    paste_id=paste_id,
                    details=details,
                    created_at=created_at,
                )
        finally:
            conn.close()
    except Exception:
        # Never allow an audit logging failure to leak content or break execution flow
        return None


def verify_audit_chain(db_path: Optional[Union[Path, str]] = None) -> Dict[str, Any]:
    """
    Verifies the integrity of the entire audit event hash chain in SQLite.
    
    Verification checks:
    1. First event's previous_hash must match GENESIS_HASH.
    2. Each subsequent event's previous_hash must equal the preceding event's event_hash.
    3. Each event's recomputed hash must match its stored event_hash exactly.
    4. Ordering is strictly sequential by event ID.
    
    Returns:
        {
            "valid": bool,
            "total_events": int,
            "message": str,
            "error_index": Optional[int],
            "event_id": Optional[int],
            "error_details": Optional[str]
        }
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT id, event_type, paste_id, created_at, details, previous_hash, event_hash
            FROM audit_events
            ORDER BY id ASC;
            """
        )
        rows = cursor.fetchall()

        total = len(rows)
        if total == 0:
            return {
                "valid": True,
                "total_events": 0,
                "message": "Audit chain is empty and valid (0 events recorded).",
            }

        expected_previous_hash = GENESIS_HASH

        for idx, row in enumerate(rows):
            event_id = row["id"]
            event_type = row["event_type"]
            paste_id = row["paste_id"]
            created_at = row["created_at"]
            details = row["details"]
            stored_previous_hash = row["previous_hash"]
            stored_event_hash = row["event_hash"]

            # Check 1: Previous hash link integrity
            if stored_previous_hash != expected_previous_hash:
                return {
                    "valid": False,
                    "total_events": total,
                    "error_index": idx,
                    "event_id": event_id,
                    "error_details": f"Broken chain link at event #{event_id}: stored previous_hash '{stored_previous_hash}' does not match expected '{expected_previous_hash}'.",
                    "message": f"Audit chain verification failed at event #{event_id}.",
                }

            # Check 2: Content integrity by recomputing hash
            recomputed_hash = compute_event_hash(
                previous_hash=stored_previous_hash,
                event_type=event_type,
                paste_id=paste_id,
                created_at=created_at,
                details=details,
            )

            if stored_event_hash != recomputed_hash:
                return {
                    "valid": False,
                    "total_events": total,
                    "error_index": idx,
                    "event_id": event_id,
                    "error_details": f"Tampered data detected at event #{event_id}: stored hash '{stored_event_hash}' does not match recomputed hash '{recomputed_hash}'.",
                    "message": f"Audit chain verification failed at event #{event_id} due to hash mismatch.",
                }

            expected_previous_hash = stored_event_hash

        return {
            "valid": True,
            "total_events": total,
            "message": f"Audit chain verified successfully ({total} events intact).",
        }
    finally:
        conn.close()
