from datetime import datetime, timedelta
from pathlib import Path
from fastapi.testclient import TestClient

from paste_repository import create_paste, get_paste_by_id


def test_1_presets_10_60_1440_minutes_work(client: TestClient):
    """1. Existing presets of 10, 60, and 1,440 minutes still work."""
    for duration in [10, 60, 1440]:
        response = client.post(
            "/pastes", json={"content": f"Preset {duration} min", "expiry_minutes": duration}
        )
        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert "url" in data


def test_2_custom_duration_30_minutes_accepted(client: TestClient):
    """2. A custom duration of 30 minutes is accepted."""
    response = client.post("/pastes", json={"content": "30 minutes custom paste", "expiry_minutes": 30})
    assert response.status_code == 201
    data = response.json()
    created_dt = datetime.fromisoformat(data["created_at"])
    expires_dt = datetime.fromisoformat(data["expires_at"])
    assert expires_dt - created_dt == timedelta(minutes=30)


def test_3_custom_duration_5_hours_as_300_minutes_accepted(client: TestClient):
    """3. A custom duration of 5 hours, converted to 300 minutes, is accepted."""
    response = client.post("/pastes", json={"content": "5 hours custom paste", "expiry_minutes": 300})
    assert response.status_code == 201
    data = response.json()
    created_dt = datetime.fromisoformat(data["created_at"])
    expires_dt = datetime.fromisoformat(data["expires_at"])
    assert expires_dt - created_dt == timedelta(minutes=300)


def test_4_custom_duration_3_days_as_4320_minutes_accepted(client: TestClient):
    """4. A custom duration of 3 days, converted to 4,320 minutes, is accepted."""
    response = client.post("/pastes", json={"content": "3 days custom paste", "expiry_minutes": 4320})
    assert response.status_code == 201
    data = response.json()
    created_dt = datetime.fromisoformat(data["created_at"])
    expires_dt = datetime.fromisoformat(data["expires_at"])
    assert expires_dt - created_dt == timedelta(minutes=4320)


def test_5_duration_1_minute_accepted(client: TestClient):
    """5. A duration of 1 minute (minimum allowed) is accepted."""
    response = client.post("/pastes", json={"content": "1 min minimum paste", "expiry_minutes": 1})
    assert response.status_code == 201
    data = response.json()
    created_dt = datetime.fromisoformat(data["created_at"])
    expires_dt = datetime.fromisoformat(data["expires_at"])
    assert expires_dt - created_dt == timedelta(minutes=1)


def test_6_duration_43200_minutes_accepted(client: TestClient):
    """6. A duration of 43,200 minutes (30 days maximum allowed) is accepted."""
    response = client.post("/pastes", json={"content": "30 days maximum paste", "expiry_minutes": 43200})
    assert response.status_code == 201
    data = response.json()
    created_dt = datetime.fromisoformat(data["created_at"])
    expires_dt = datetime.fromisoformat(data["expires_at"])
    assert expires_dt - created_dt == timedelta(minutes=43200)


def test_7_zero_and_negative_durations_rejected(client: TestClient):
    """7. Zero and negative durations are rejected with HTTP 422."""
    for invalid_duration in [0, -1, -60, -1440]:
        response = client.post(
            "/pastes", json={"content": "Invalid duration", "expiry_minutes": invalid_duration}
        )
        assert response.status_code == 422
        assert "between 1 and 43200 minutes" in response.text


def test_8_decimal_durations_rejected(client: TestClient):
    """8. Decimal durations are rejected with HTTP 422."""
    for decimal_val in [10.5, 30.0, 1.2]:
        response = client.post(
            "/pastes", json={"content": "Decimal duration", "expiry_minutes": decimal_val}
        )
        assert response.status_code == 422


def test_9_durations_greater_than_43200_rejected(client: TestClient):
    """9. Durations greater than 43,200 minutes (30 days) are rejected with HTTP 422."""
    for excessive_val in [43201, 50000, 100000]:
        response = client.post(
            "/pastes", json={"content": "Excessive duration", "expiry_minutes": excessive_val}
        )
        assert response.status_code == 422
        assert "between 1 and 43200 minutes" in response.text


def test_10_missing_or_invalid_duration_types_rejected(client: TestClient):
    """10. Missing or invalid duration types (booleans, strings) are rejected with HTTP 422."""
    # Missing expiry_minutes
    assert client.post("/pastes", json={"content": "No expiry"}).status_code == 422

    # Booleans (must strictly reject True/False)
    assert client.post("/pastes", json={"content": "Bool true", "expiry_minutes": True}).status_code == 422
    assert client.post("/pastes", json={"content": "Bool false", "expiry_minutes": False}).status_code == 422

    # Strings
    assert client.post("/pastes", json={"content": "String num", "expiry_minutes": "30"}).status_code == 422
    assert client.post("/pastes", json={"content": "String text", "expiry_minutes": "one hour"}).status_code == 422


def test_response_fields_format(client: TestClient):
    """Verify response format contains generated ID, URL, and timestamps."""
    response = client.post("/pastes", json={"content": "Format test", "expiry_minutes": 10})
    assert response.status_code == 201
    data = response.json()
    assert "id" in data and isinstance(data["id"], str) and len(data["id"]) > 0
    assert "url" in data and data["url"] == f"http://localhost:5173/p/{data['id']}"
    assert "created_at" in data
    assert "expires_at" in data


def test_created_record_is_stored_in_sqlite(client: TestClient, test_db: Path):
    """Verify that the created record is actually stored in SQLite."""
    text_content = "def test_db_storage():\n    return 'Stored successfully'"
    response = client.post("/pastes", json={"content": text_content, "expiry_minutes": 60})
    assert response.status_code == 201
    paste_id = response.json()["id"]

    record = get_paste_by_id(paste_id, db_path=test_db)
    assert record is not None
    assert record["id"] == paste_id
    assert record["content"] == text_content


def test_empty_content_returns_422(client: TestClient):
    """Empty content returns HTTP 422 Unprocessable Entity."""
    response = client.post("/pastes", json={"content": "", "expiry_minutes": 10})
    assert response.status_code == 422
    assert "cannot be empty" in response.text


def test_whitespace_only_content_returns_422(client: TestClient):
    """Whitespace-only content returns HTTP 422 Unprocessable Entity."""
    response = client.post("/pastes", json={"content": "   \n\t  \r\n  ", "expiry_minutes": 10})
    assert response.status_code == 422
    assert "cannot be empty or contain only whitespace" in response.text


def test_missing_content_returns_422(client: TestClient):
    """Missing content returns HTTP 422 Unprocessable Entity."""
    response = client.post("/pastes", json={"expiry_minutes": 10})
    assert response.status_code == 422


def test_content_exceeding_size_limit_returns_422(client: TestClient):
    """Content exceeding 100 KB limit returns HTTP 422 Unprocessable Entity."""
    exact_limit = "x" * (100 * 1024)
    res_valid = client.post("/pastes", json={"content": exact_limit, "expiry_minutes": 10})
    assert res_valid.status_code == 201

    exceeded = "x" * (100 * 1024 + 1)
    res_invalid = client.post("/pastes", json={"content": exceeded, "expiry_minutes": 10})
    assert res_invalid.status_code == 422
    assert "exceeds maximum limit" in res_invalid.text


def test_unique_ids_across_requests(client: TestClient):
    """Two separate requests generate different, unique IDs."""
    res1 = client.post("/pastes", json={"content": "Paste A", "expiry_minutes": 10})
    res2 = client.post("/pastes", json={"content": "Paste B", "expiry_minutes": 10})
    assert res1.status_code == 201
    assert res2.status_code == 201
    assert res1.json()["id"] != res2.json()["id"]


def test_id_collision_retry_without_overwriting(test_db: Path):
    """If an ID collision occurs, the original record is not overwritten and a new ID is generated."""
    original_id = "initial_fixed_id"
    create_paste(content="Original intact content", db_path=test_db, id_generator=lambda: original_id)

    id_sequence = iter([original_id, "resolved_unique_id"])
    second_paste = create_paste(
        content="New paste content after collision",
        db_path=test_db,
        id_generator=lambda: next(id_sequence),
    )

    assert second_paste["id"] == "resolved_unique_id"
    original_record = get_paste_by_id(original_id, db_path=test_db)
    assert original_record is not None
    assert original_record["content"] == "Original intact content"


def test_root_and_health_endpoints(client: TestClient):
    """Verify that GET / and GET /health continue to respond properly."""
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert res_root.json()["status"] == "online"

    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "healthy"
