import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import pytest
from fastapi.testclient import TestClient

from database import init_db, get_db_connection
from main import app, periodic_cleanup_task, lifespan
from paste_repository import create_paste, get_paste_by_id, delete_expired_pastes


def test_cleanup_deletes_expired_records(test_db: Path):
    """
    Verify that records with expires_at in the past are deleted.
    """
    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    expired_time = now - timedelta(minutes=10)

    paste = create_paste(
        content="Expired paste content",
        created_at=now - timedelta(minutes=30),
        expires_at=expired_time,
        db_path=test_db,
    )

    # Verify it exists in SQLite initially
    assert get_paste_by_id(paste["id"], db_path=test_db) is not None

    # Run cleanup with reference time `now`
    deleted_count = delete_expired_pastes(now=now, db_path=test_db)
    assert deleted_count == 1

    # Verify it was physically deleted from SQLite
    assert get_paste_by_id(paste["id"], db_path=test_db) is None


def test_cleanup_deletes_records_expiring_exactly_at_current_time(test_db: Path):
    """
    Verify boundary condition: records expiring exactly at current time (expires_at == now) are deleted.
    """
    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    paste = create_paste(
        content="Expiring right now",
        created_at=now - timedelta(minutes=15),
        expires_at=now,
        db_path=test_db,
    )

    deleted_count = delete_expired_pastes(now=now, db_path=test_db)
    assert deleted_count == 1
    assert get_paste_by_id(paste["id"], db_path=test_db) is None


def test_cleanup_preserves_future_expiring_records(test_db: Path):
    """
    Verify that records with expires_at > now are preserved.
    """
    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    future_time = now + timedelta(hours=2)

    paste = create_paste(
        content="Future active paste",
        created_at=now,
        expires_at=future_time,
        db_path=test_db,
    )

    deleted_count = delete_expired_pastes(now=now, db_path=test_db)
    assert deleted_count == 0

    retrieved = get_paste_by_id(paste["id"], db_path=test_db)
    assert retrieved is not None
    assert retrieved["content"] == "Future active paste"


def test_cleanup_preserves_null_expiry_records(test_db: Path):
    """
    Verify that records where expires_at IS NULL are preserved.
    """
    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    paste = create_paste(
        content="Permanent paste with no expiry",
        created_at=now,
        expires_at=None,
        db_path=test_db,
    )

    deleted_count = delete_expired_pastes(now=now, db_path=test_db)
    assert deleted_count == 0

    retrieved = get_paste_by_id(paste["id"], db_path=test_db)
    assert retrieved is not None
    assert retrieved["content"] == "Permanent paste with no expiry"
    assert retrieved["expires_at"] is None


def test_cleanup_repeated_calls_are_safe_and_idempotent(test_db: Path):
    """
    Verify running cleanup multiple times consecutively causes no errors and returns 0 when no records match.
    """
    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    create_paste(
        content="Expired 1",
        created_at=now - timedelta(hours=1),
        expires_at=now - timedelta(minutes=30),
        db_path=test_db,
    )
    create_paste(
        content="Expired 2",
        created_at=now - timedelta(hours=1),
        expires_at=now - timedelta(minutes=10),
        db_path=test_db,
    )

    # First run deletes both
    count_1 = delete_expired_pastes(now=now, db_path=test_db)
    assert count_1 == 2

    # Second run finds none to delete -> returns 0
    count_2 = delete_expired_pastes(now=now, db_path=test_db)
    assert count_2 == 0

    # Third run still 0
    count_3 = delete_expired_pastes(now=now, db_path=test_db)
    assert count_3 == 0


def test_logical_expiry_vs_physical_deletion(client: TestClient, test_db: Path, set_clock):
    """
    Verifies the key distinction:
    - Before cleanup runs: an expired paste returns HTTP 410 Gone ("Time limit exceeded").
    - After cleanup runs: the expired paste is physically deleted and returns HTTP 404 Not Found.
    In neither case is expired content exposed!
    """
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=timezone.utc)
    set_clock(t0)

    # Create a paste expiring in 10 minutes
    resp = client.post("/pastes", json={"content": "Secret content", "expiry_minutes": 10})
    assert resp.status_code == 201
    paste_id = resp.json()["id"]

    # 1. While active (t0 + 5m) -> HTTP 200
    set_clock(t0 + timedelta(minutes=5))
    resp_active = client.get(f"/pastes/{paste_id}")
    assert resp_active.status_code == 200
    assert resp_active.json()["content"] == "Secret content"

    # 2. At t0 + 15m (expired, but cleanup hasn't run yet) -> Logical Expiry: HTTP 410 Gone
    set_clock(t0 + timedelta(minutes=15))
    resp_expired = client.get(f"/pastes/{paste_id}")
    assert resp_expired.status_code == 410
    assert resp_expired.json()["detail"] == "Time limit exceeded"
    assert "Secret content" not in resp_expired.text

    # 3. Now trigger cleanup at t0 + 15m -> Physical deletion
    deleted = delete_expired_pastes(now=t0 + timedelta(minutes=15), db_path=test_db)
    assert deleted == 1

    # 4. After cleanup -> Physical deletion: HTTP 404 Not Found
    resp_after_cleanup = client.get(f"/pastes/{paste_id}")
    assert resp_after_cleanup.status_code == 404
    assert resp_after_cleanup.json()["detail"] == "Paste not found"
    assert "Secret content" not in resp_after_cleanup.text


@pytest.mark.anyio
async def test_periodic_cleanup_task_clean_cancellation(tmp_path: Path):
    """
    Verifies that the periodic_cleanup_task runs and cancels cleanly without crashing or leaving tasks.
    """
    db_file = tmp_path / "cancel_test.db"
    init_db(db_file)
    app.state.db_path = db_file

    task = asyncio.create_task(periodic_cleanup_task(app, interval_seconds=1))
    await asyncio.sleep(0.05)

    # Cancel task
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.done()
    app.state.db_path = None
