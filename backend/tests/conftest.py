from datetime import datetime
from pathlib import Path
from typing import Callable, Generator
import pytest
from fastapi.testclient import TestClient

from database import init_db
from main import app
from rate_limiter import default_rate_limiter


@pytest.fixture(autouse=True)
def reset_rate_limiter_fixture():
    """Ensures each test has clean rate limiting quota."""
    default_rate_limiter.reset()
    yield
    default_rate_limiter.reset()


@pytest.fixture
def test_db(tmp_path: Path) -> Generator[Path, None, None]:
    """
    Creates an isolated temporary SQLite database for each test.
    Overrides app.state.db_path so all API requests use this temporary database.
    Cleans up automatically when the test finishes.
    """
    db_file = tmp_path / "test_pastebin.db"
    init_db(db_file)
    
    # Inject temporary database path into FastAPI app state
    app.state.db_path = db_file
    app.state.enable_cleanup_worker = False  # Avoid background thread during standard unit tests
    yield db_file
    # Reset app state after test execution
    app.state.db_path = None
    app.state.enable_cleanup_worker = True


@pytest.fixture
def client(test_db: Path) -> Generator[TestClient, None, None]:
    """
    Provides a FastAPI TestClient configured to use the isolated test database.
    """
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def set_clock(monkeypatch: pytest.MonkeyPatch) -> Callable[[datetime], None]:
    """
    Helper fixture to control/mock the server's UTC clock deterministically
    during expiry tests without waiting for real time to elapse.
    """
    def _set_time(dt: datetime) -> None:
        monkeypatch.setattr("main.get_current_utc_time", lambda: dt)
    return _set_time
