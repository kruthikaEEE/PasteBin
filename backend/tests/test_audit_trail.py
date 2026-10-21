from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from audit import verify_audit_chain, GENESIS_HASH
from database import get_db_connection


def test_1_paste_creation_records_audit_event(client: TestClient, test_db: Path):
    """
    Verify that creating a paste records a PASTE_CREATED audit event
    with metadata, and does NOT record raw content or raw tokens.
    """
    secret_text = "Sensitive data that should never be in audit logs"
    resp = client.post("/pastes", json={"content": secret_text, "expiry_minutes": 10})
    assert resp.status_code == 201
    paste_id = resp.json()["id"]
    revoke_token = resp.json()["revoke_token"]

    conn = get_db_connection(test_db)
    row = conn.execute(
        "SELECT event_type, paste_id, details, previous_hash, event_hash FROM audit_events WHERE paste_id = ?",
        (paste_id,),
    ).fetchone()
    assert row is not None
    assert row["event_type"] == "PASTE_CREATED"
    assert row["previous_hash"] == GENESIS_HASH
    assert len(row["event_hash"]) == 64

    # Security check: paste content and raw token MUST NOT appear anywhere in the audit record
    row_text = f"{row['details']} {row['event_type']} {row['previous_hash']} {row['event_hash']}"
    assert secret_text not in row_text
    assert revoke_token not in row_text
    conn.close()


def test_2_revocation_records_audit_event(client: TestClient, test_db: Path):
    """
    Verify that revoking a paste records a PASTE_REVOKED audit event in the same chain.
    """
    resp = client.post("/pastes", json={"content": "Content to revoke", "expiry_minutes": 10})
    paste_id = resp.json()["id"]
    revoke_token = resp.json()["revoke_token"]

    # Revoke
    rev_resp = client.post(f"/pastes/{paste_id}/revoke", headers={"X-Revoke-Token": revoke_token})
    assert rev_resp.status_code == 200

    conn = get_db_connection(test_db)
    events = conn.execute(
        "SELECT event_type, previous_hash, event_hash FROM audit_events WHERE paste_id = ? ORDER BY id ASC",
        (paste_id,),
    ).fetchall()
    assert len(events) == 2
    assert events[0]["event_type"] == "PASTE_CREATED"
    assert events[1]["event_type"] == "PASTE_REVOKED"
    # Hash link check
    assert events[1]["previous_hash"] == events[0]["event_hash"]
    conn.close()


def test_3_access_denials_record_audit_events(client: TestClient, test_db: Path, set_clock):
    """
    Verify that access denial scenarios record the appropriate audit event:
    - Expired access: EXPIRED_ACCESS_DENIED
    - Revoked access: REVOKED_ACCESS_DENIED
    - Non-existent access: UNKNOWN_PASTE_ACCESS_DENIED
    """
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=timezone.utc)
    set_clock(t0)

    # 1. Non-existent ID lookup
    r404 = client.get("/pastes/missing_id_999")
    assert r404.status_code == 404

    # 2. Expired lookup
    r_exp = client.post("/pastes", json={"content": "Expire test", "expiry_minutes": 10})
    id_exp = r_exp.json()["id"]
    set_clock(t0 + timedelta(minutes=20))
    r410_exp = client.get(f"/pastes/{id_exp}")
    assert r410_exp.status_code == 410

    # 3. Revoked lookup
    r_rev = client.post("/pastes", json={"content": "Revoke test", "expiry_minutes": 60})
    id_rev = r_rev.json()["id"]
    client.post(f"/pastes/{id_rev}/revoke", headers={"X-Revoke-Token": r_rev.json()["revoke_token"]})
    r410_rev = client.get(f"/pastes/{id_rev}")
    assert r410_rev.status_code == 410

    conn = get_db_connection(test_db)
    event_types = [
        row["event_type"]
        for row in conn.execute("SELECT event_type FROM audit_events ORDER BY id ASC").fetchall()
    ]
    assert "UNKNOWN_PASTE_ACCESS_DENIED" in event_types
    assert "EXPIRED_ACCESS_DENIED" in event_types
    assert "REVOKED_ACCESS_DENIED" in event_types
    conn.close()


def test_4_audit_chain_verification_succeeds_on_intact_history(client: TestClient, test_db: Path):
    """
    Verify that an untampered sequence of audit events verifies successfully.
    """
    # Generate several events: create, view, revoke, 404
    p1 = client.post("/pastes", json={"content": "Paste 1", "expiry_minutes": 10}).json()
    p2 = client.post("/pastes", json={"content": "Paste 2", "expiry_minutes": 20}).json()
    client.get(f"/pastes/{p1['id']}")
    client.post(f"/pastes/{p2['id']}/revoke", headers={"X-Revoke-Token": p2["revoke_token"]})
    client.get(f"/pastes/{p2['id']}")

    result = verify_audit_chain(db_path=test_db)
    assert result["valid"] is True
    assert result["total_events"] >= 4
    assert "Audit chain verified successfully" in result["message"]


def test_5_tampering_content_causes_verification_failure(client: TestClient, test_db: Path):
    """
    Verify that modifying an event's details in SQLite is caught by hash recomputation.
    """
    p1 = client.post("/pastes", json={"content": "Tamper test", "expiry_minutes": 10}).json()
    paste_id = p1["id"]

    # Chain should be valid initially
    assert verify_audit_chain(db_path=test_db)["valid"] is True

    # Tamper with the details column directly in SQLite
    conn = get_db_connection(test_db)
    conn.execute(
        "UPDATE audit_events SET details = ? WHERE paste_id = ?",
        (json.dumps({"forged_key": "malicious_change"}), paste_id),
    )
    conn.commit()
    conn.close()

    # Verification must fail
    result = verify_audit_chain(db_path=test_db)
    assert result["valid"] is False
    assert "Tampered data detected" in result["error_details"]
    assert "hash mismatch" in result["message"]


def test_6_tampering_chain_link_or_deletion_causes_verification_failure(client: TestClient, test_db: Path):
    """
    Verify that deleting an intermediate event breaks the previous_hash link
    and causes verification to fail.
    """
    # Create 3 events
    p1 = client.post("/pastes", json={"content": "First", "expiry_minutes": 10}).json()
    p2 = client.post("/pastes", json={"content": "Second", "expiry_minutes": 10}).json()
    p3 = client.post("/pastes", json={"content": "Third", "expiry_minutes": 10}).json()

    assert verify_audit_chain(db_path=test_db)["valid"] is True

    # Delete the middle audit event directly from SQLite
    conn = get_db_connection(test_db)
    conn.execute("DELETE FROM audit_events WHERE paste_id = ?", (p2["id"],))
    conn.commit()
    conn.close()

    # Verification must detect the broken link
    result = verify_audit_chain(db_path=test_db)
    assert result["valid"] is False
    assert "Broken chain link" in result["error_details"]


def test_7_admin_audit_verification_endpoint_protection(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """
    Verify security of GET /admin/audit/verify:
    - Fails closed with 401 if AUDIT_ADMIN_TOKEN is unset
    - Returns 401 if token is incorrect or missing
    - Returns 200 with verification details if valid token is provided
    """
    # 1. Unset token -> fail closed
    monkeypatch.delenv("AUDIT_ADMIN_TOKEN", raising=False)
    r_unconfigured = client.get("/admin/audit/verify", headers={"X-Admin-Token": "any_token"})
    assert r_unconfigured.status_code == 401
    assert "not configured on the server" in r_unconfigured.json()["detail"]

    # 2. Configure secret token
    secret_admin_key = "secure_production_audit_key_9999"
    monkeypatch.setenv("AUDIT_ADMIN_TOKEN", secret_admin_key)

    # 3. Request without token
    r_missing = client.get("/admin/audit/verify")
    assert r_missing.status_code == 401

    # 4. Request with invalid token
    r_bad = client.get("/admin/audit/verify", headers={"X-Admin-Token": "wrong_key"})
    assert r_bad.status_code == 401
    assert "Invalid or missing admin token" in r_bad.json()["detail"]

    # 5. Request with correct token via X-Admin-Token
    r_good = client.get("/admin/audit/verify", headers={"X-Admin-Token": secret_admin_key})
    assert r_good.status_code == 200
    assert r_good.json()["valid"] is True

    # 6. Request with correct token via Authorization: Bearer
    r_bearer = client.get("/admin/audit/verify", headers={"Authorization": f"Bearer {secret_admin_key}"})
    assert r_bearer.status_code == 200
    assert r_bearer.json()["valid"] is True
