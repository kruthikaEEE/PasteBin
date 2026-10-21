from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from database import init_db
from paste_repository import create_paste, get_paste_by_id


@pytest.fixture
def test_db(tmp_path: Path) -> Path:
    """
    Pytest fixture that creates an isolated, temporary SQLite database
    for each test so that the real production/development database is never affected.
    """
    db_file = tmp_path / "test_pastebin.db"
    init_db(db_file)
    return db_file


def test_create_and_store_paste(test_db: Path):
    """1. Test that a paste can be created and stored."""
    content = "print('Hello, Pastebin!')"
    created = create_paste(content=content, db_path=test_db)

    assert created is not None
    assert isinstance(created["id"], str)
    assert len(created["id"]) > 0
    assert created["content"] == content
    assert "created_at" in created
    assert created["created_at"] is not None


def test_retrieve_paste_by_id(test_db: Path):
    """2. Test that stored content can be retrieved using its ID."""
    original_content = "Special snippet content to verify retrieval"
    created = create_paste(content=original_content, db_path=test_db)
    paste_id = created["id"]

    retrieved = get_paste_by_id(paste_id=paste_id, db_path=test_db)
    assert retrieved is not None
    assert retrieved["id"] == paste_id
    assert retrieved["content"] == original_content
    assert retrieved["created_at"] == created["created_at"]


def test_unknown_id_returns_none(test_db: Path):
    """3. Test that querying an unknown/non-existent ID returns None."""
    result = get_paste_by_id(paste_id="definitely-nonexistent-id", db_path=test_db)
    assert result is None


def test_two_pastes_receive_different_ids(test_db: Path):
    """4. Test that two distinct pastes receive unique, different IDs."""
    paste1 = create_paste(content="First paste content", db_path=test_db)
    paste2 = create_paste(content="Second paste content", db_path=test_db)

    assert paste1["id"] != paste2["id"]


def test_paste_without_expiry(test_db: Path):
    """5. Test that a paste can be stored without an expiry (expires_at is None)."""
    paste = create_paste(content="Permanent paste with no expiry", expires_at=None, db_path=test_db)

    assert paste["expires_at"] is None

    retrieved = get_paste_by_id(paste_id=paste["id"], db_path=test_db)
    assert retrieved is not None
    assert retrieved["expires_at"] is None


def test_paste_with_expiry_stores_correct_timestamp(test_db: Path):
    """6. Test that a paste with an expiry stores the correct ISO 8601 timestamp."""
    future_time = datetime.now(timezone.utc) + timedelta(hours=24)
    expected_iso = future_time.isoformat()

    # Test passing a datetime object
    paste = create_paste(content="Expiring paste snippet", expires_at=future_time, db_path=test_db)
    assert paste["expires_at"] == expected_iso

    retrieved = get_paste_by_id(paste_id=paste["id"], db_path=test_db)
    assert retrieved is not None
    assert retrieved["expires_at"] == expected_iso


def test_empty_or_whitespace_content_rejected(test_db: Path):
    """7. Test that empty or whitespace-only content is rejected with ValueError."""
    # Test completely empty string
    with pytest.raises(ValueError, match="cannot be empty"):
        create_paste(content="", db_path=test_db)

    # Test string with spaces only
    with pytest.raises(ValueError, match="cannot be empty"):
        create_paste(content="   ", db_path=test_db)

    # Test string with newlines and tabs only
    with pytest.raises(ValueError, match="cannot be empty"):
        create_paste(content="\n\t  \r\n", db_path=test_db)

    # Test None
    with pytest.raises(ValueError, match="cannot be empty"):
        create_paste(content=None, db_path=test_db)


def test_database_persistence_across_connections(test_db: Path):
    """8. Test that records persist in SQLite across independent connection lifecycles."""
    content = "Persistent content verification"
    created = create_paste(content=content, db_path=test_db)

    # get_paste_by_id opens a brand new connection to the file and closes it
    fetched = get_paste_by_id(paste_id=created["id"], db_path=test_db)
    assert fetched is not None
    assert fetched["content"] == content
