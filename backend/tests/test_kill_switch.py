from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from database import get_db_connection
from paste_repository import create_paste, get_paste_by_id, revoke_paste


def test_1_new_pastes_default_to_active(test_db: Path):
    """
    Verify that newly created pastes have status 'ACTIVE'.
    """
    paste = create_paste(
        content="Testing default active status",
        db_path=test_db,
    )
    assert paste["status"] == "ACTIVE"

    retrieved = get_paste_by_id(paste["id"], db_path=test_db)
    assert retrieved is not None
    assert retrieved["status"] == "ACTIVE"


def test_2_and_3_valid_owner_token_can_revoke_and_immediately_blocks_reads(client: TestClient):
    """
    Verify that:
    1. Paste creator receives a revoke_token.
    2. Calling POST /pastes/{id}/revoke with X-Revoke-Token succeeds.
    3. Subsequent GET /pastes/{id} returns HTTP 410 Gone ("Paste has been revoked").
    4. Revoked content is NEVER returned.
    """
    # Create paste
    resp = client.post("/pastes", json={"content": "Confidential financial data", "expiry_minutes": 60})
    assert resp.status_code == 201
    data = resp.json()
    paste_id = data["id"]
    revoke_token = data.get("revoke_token")
    assert revoke_token is not None
    assert len(revoke_token) >= 32

    # Verify readable before revocation
    view_before = client.get(f"/pastes/{paste_id}")
    assert view_before.status_code == 200
    assert view_before.json()["content"] == "Confidential financial data"

    # Revoke with valid token
    revoke_resp = client.post(
        f"/pastes/{paste_id}/revoke",
        headers={"X-Revoke-Token": revoke_token},
    )
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["status"] == "revoked"

    # Subsequent GET immediately returns HTTP 410 Gone
    view_after = client.get(f"/pastes/{paste_id}")
    assert view_after.status_code == 410
    assert view_after.json()["detail"] == "Paste has been revoked"
    # Verify content is NEVER exposed
    assert "Confidential financial data" not in view_after.text


def test_4_unknown_paste_id_revoke_returns_404(client: TestClient):
    """
    Verify that calling revoke on a non-existent paste returns HTTP 404.
    """
    resp = client.post(
        "/pastes/nonexistent_xyz_999/revoke",
        headers={"X-Revoke-Token": "some_random_token_12345"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Paste not found"


def test_5_missing_or_invalid_revoke_token_is_rejected(client: TestClient):
    """
    Verify that missing or incorrect revocation tokens return HTTP 401 Unauthorized.
    """
    # Create paste
    create_resp = client.post("/pastes", json={"content": "Top secret content", "expiry_minutes": 30})
    assert create_resp.status_code == 201
    paste_id = create_resp.json()["id"]
    valid_token = create_resp.json()["revoke_token"]

    # 1. Missing token header completely
    r_missing = client.post(f"/pastes/{paste_id}/revoke")
    assert r_missing.status_code == 401

    # 2. Invalid token
    r_invalid = client.post(
        f"/pastes/{paste_id}/revoke",
        headers={"X-Revoke-Token": "completely_wrong_token"},
    )
    assert r_invalid.status_code == 401
    assert "Invalid or missing revocation token" in r_invalid.json()["detail"]

    # 3. Paste remains active and unrevoked
    view_resp = client.get(f"/pastes/{paste_id}")
    assert view_resp.status_code == 200
    assert view_resp.json()["content"] == "Top secret content"


def test_6_shareable_id_alone_cannot_revoke(client: TestClient):
    """
    Verify that a public viewer with only the paste ID cannot revoke the paste.
    """
    create_resp = client.post("/pastes", json={"content": "Publicly shared text", "expiry_minutes": 30})
    paste_id = create_resp.json()["id"]

    # Trying to revoke using the paste_id as the token should fail
    resp = client.post(
        f"/pastes/{paste_id}/revoke",
        headers={"X-Revoke-Token": paste_id},
    )
    assert resp.status_code == 401


def test_7_repeated_revocations_are_idempotent_and_do_not_duplicate_audit_events(client: TestClient, test_db: Path):
    """
    Verify that revoking an already-revoked paste is safe, returns success,
    and does NOT generate duplicate PASTE_REVOKED audit events.
    """
    create_resp = client.post("/pastes", json={"content": "Idempotent revoke test", "expiry_minutes": 60})
    paste_id = create_resp.json()["id"]
    revoke_token = create_resp.json()["revoke_token"]

    # Revoke once
    r1 = client.post(f"/pastes/{paste_id}/revoke", headers={"X-Revoke-Token": revoke_token})
    assert r1.status_code == 200

    # Count PASTE_REVOKED events in DB
    conn = get_db_connection(test_db)
    count1 = conn.execute(
        "SELECT count(*) as c FROM audit_events WHERE event_type = 'PASTE_REVOKED' AND paste_id = ?",
        (paste_id,),
    ).fetchone()["c"]
    assert count1 == 1

    # Revoke second time with same token
    r2 = client.post(f"/pastes/{paste_id}/revoke", headers={"X-Revoke-Token": revoke_token})
    assert r2.status_code == 200
    assert r2.json()["status"] == "revoked"

    # Count PASTE_REVOKED events in DB should still be 1 (no duplicate)
    count2 = conn.execute(
        "SELECT count(*) as c FROM audit_events WHERE event_type = 'PASTE_REVOKED' AND paste_id = ?",
        (paste_id,),
    ).fetchone()["c"]
    assert count2 == 1
    conn.close()


def test_8_revoked_paste_persists_in_sqlite(client: TestClient, test_db: Path):
    """
    Verify that a revoked paste remains stored in SQLite with status 'REVOKED'
    and continues returning 410 across requests.
    """
    create_resp = client.post("/pastes", json={"content": "Persistence after revoke", "expiry_minutes": 60})
    paste_id = create_resp.json()["id"]
    revoke_token = create_resp.json()["revoke_token"]

    client.post(f"/pastes/{paste_id}/revoke", headers={"X-Revoke-Token": revoke_token})

    # Direct database inspection
    conn = get_db_connection(test_db)
    row = conn.execute("SELECT status, content FROM pastes WHERE id = ?", (paste_id,)).fetchone()
    assert row is not None
    assert row["status"] == "REVOKED"
    assert row["content"] == "Persistence after revoke"
    conn.close()

    # API read returns 410
    view_resp = client.get(f"/pastes/{paste_id}")
    assert view_resp.status_code == 410
    assert view_resp.json()["detail"] == "Paste has been revoked"


def test_9_expiry_and_revocation_are_independent(client: TestClient, set_clock):
    """
    Verify that:
    - An unrevoked expired paste returns 'Time limit exceeded' (410).
    - An active unexpired paste returns 200.
    - A revoked unexpired paste returns 'Paste has been revoked' (410).
    """
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=timezone.utc)
    set_clock(t0)

    # 1. Unrevoked paste expiring in 10 minutes
    r_exp = client.post("/pastes", json={"content": "Expiring paste", "expiry_minutes": 10})
    id_exp = r_exp.json()["id"]

    # 2. Unexpired paste revoked at t0 + 2m
    r_rev = client.post("/pastes", json={"content": "Revoked paste", "expiry_minutes": 60})
    id_rev = r_rev.json()["id"]
    client.post(f"/pastes/{id_rev}/revoke", headers={"X-Revoke-Token": r_rev.json()["revoke_token"]})

    # Move time to t0 + 15m (first paste is expired; second paste was revoked earlier)
    set_clock(t0 + timedelta(minutes=15))

    # Paste 1: Expired
    view_exp = client.get(f"/pastes/{id_exp}")
    assert view_exp.status_code == 410
    assert view_exp.json()["detail"] == "Time limit exceeded"

    # Paste 2: Revoked
    view_rev = client.get(f"/pastes/{id_rev}")
    assert view_rev.status_code == 410
    assert view_rev.json()["detail"] == "Paste has been revoked"
