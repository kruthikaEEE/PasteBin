from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from fastapi.testclient import TestClient

from paste_repository import create_paste, get_paste_by_id


def test_1_and_2_existing_unexpired_paste_returns_200_and_matches_content(
    client: TestClient, test_db: Path
):
    """1 & 2. An existing, unexpired paste returns HTTP 200 OK and matches stored content."""
    stored_text = "console.log('Unexpired snippet');"
    future_time = datetime.now(timezone.utc) + timedelta(hours=1)
    paste = create_paste(content=stored_text, expires_at=future_time, db_path=test_db)
    paste_id = paste["id"]

    response = client.get(f"/pastes/{paste_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == paste_id
    assert data["content"] == stored_text
    assert "created_at" in data
    assert data["expires_at"] == future_time.isoformat()


def test_3_and_4_expired_paste_returns_410_and_never_exposes_content(
    client: TestClient, test_db: Path, set_clock: Callable[[datetime], None]
):
    """3 & 4. An expired paste returns HTTP 410 Gone and never exposes stored content."""
    secret_content = "TOP_SECRET_CODE_NEVER_RETURN_ON_EXPIRY"
    base_time = datetime(2026, 10, 20, 10, 0, 0, tzinfo=timezone.utc)
    expired_time = base_time - timedelta(minutes=10)

    paste = create_paste(content=secret_content, expires_at=expired_time, db_path=test_db)
    paste_id = paste["id"]

    # Set server clock to base_time (which is 10 minutes past expired_time)
    set_clock(base_time)

    response = client.get(f"/pastes/{paste_id}")
    assert response.status_code == 410
    data = response.json()
    assert data["detail"] == "Time limit exceeded"

    # Crucial security verification: content must never be exposed
    assert "content" not in data
    assert secret_content not in response.text


def test_5_exact_expiry_boundary_returns_410(
    client: TestClient, test_db: Path, set_clock: Callable[[datetime], None]
):
    """
    5. A paste whose expiry timestamp is exactly equal to current time returns HTTP 410 Gone.
    Verifies that current_time >= expires_at triggers expiry when current_time == expires_at.
    Example: Current time: 10:10 AM; expiry: 10:10 AM -> 410 Gone.
    """
    exact_time = datetime(2026, 10, 20, 10, 10, 0, tzinfo=timezone.utc)

    paste = create_paste(content="Exact boundary paste", expires_at=exact_time, db_path=test_db)
    paste_id = paste["id"]

    # Simulate clock at exactly 10:10 AM
    set_clock(exact_time)

    response = client.get(f"/pastes/{paste_id}")
    assert response.status_code == 410
    assert response.json()["detail"] == "Time limit exceeded"


def test_6_time_progression_future_and_past(
    client: TestClient, test_db: Path, set_clock: Callable[[datetime], None]
):
    """
    6. Verifies clock progression around the 10:10 AM expiry:
       - Current time: 10:00 AM; expiry: 10:10 AM -> 200 OK.
       - Current time: 10:11 AM; expiry: 10:10 AM -> 410 Gone.
    """
    expiry_time = datetime(2026, 10, 20, 10, 10, 0, tzinfo=timezone.utc)
    paste = create_paste(content="Progressing time snippet", expires_at=expiry_time, db_path=test_db)
    paste_id = paste["id"]

    # At 10:00 AM (in the future relative to creation, before expiry) -> 200 OK
    set_clock(datetime(2026, 10, 20, 10, 0, 0, tzinfo=timezone.utc))
    res_before = client.get(f"/pastes/{paste_id}")
    assert res_before.status_code == 200
    assert res_before.json()["content"] == "Progressing time snippet"

    # At 10:11 AM (after expiry) -> 410 Gone
    set_clock(datetime(2026, 10, 20, 10, 11, 0, tzinfo=timezone.utc))
    res_after = client.get(f"/pastes/{paste_id}")
    assert res_after.status_code == 410
    assert res_after.json()["detail"] == "Time limit exceeded"


def test_7_paste_with_null_expiry_returns_200(client: TestClient, test_db: Path):
    """7. A paste with expires_at = NULL never expires and returns HTTP 200 OK."""
    paste = create_paste(content="Permanent documentation paste", expires_at=None, db_path=test_db)
    paste_id = paste["id"]

    response = client.get(f"/pastes/{paste_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["content"] == "Permanent documentation paste"
    assert data["expires_at"] is None


def test_8_unknown_paste_id_returns_404(client: TestClient):
    """8. An unknown paste ID returns HTTP 404 Not Found."""
    response = client.get("/pastes/nonexistent_paste_xyz_999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Paste not found"


def test_9_expired_records_remain_in_sqlite(
    client: TestClient, test_db: Path, set_clock: Callable[[datetime], None]
):
    """9. Expired records remain in SQLite; retrieval must not automatically delete them."""
    base_time = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    expired_time = base_time - timedelta(minutes=20)

    paste = create_paste(content="Content still preserved in SQLite", expires_at=expired_time, db_path=test_db)
    paste_id = paste["id"]

    set_clock(base_time)

    # API returns 410
    response = client.get(f"/pastes/{paste_id}")
    assert response.status_code == 410

    # Query SQLite directly: verify the row still exists and was not deleted
    record_in_db = get_paste_by_id(paste_id, db_path=test_db)
    assert record_in_db is not None
    assert record_in_db["id"] == paste_id
    assert record_in_db["content"] == "Content still preserved in SQLite"
    assert record_in_db["expires_at"] == expired_time.isoformat()
