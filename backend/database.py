import sqlite3
from pathlib import Path
from typing import Optional, Union

# Path to the persistent SQLite database file inside the backend directory
DB_PATH = Path(__file__).resolve().parent / "pastebin.db"


def get_db_connection(db_path: Optional[Union[Path, str]] = None) -> sqlite3.Connection:
    """
    Establishes and returns a connection to the SQLite database.
    Configures row_factory to sqlite3.Row so columns can be accessed by name.
    """
    target_path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[Union[Path, str]] = None) -> None:
    """
    Initializes the SQLite database with safe, idempotent migrations.
    
    Creates and updates:
      1. 'pastes' table:
         - id: TEXT PRIMARY KEY (cryptographically random identifier)
         - content: TEXT NOT NULL (paste content text)
         - created_at: TEXT NOT NULL (ISO 8601 UTC timestamp)
         - expires_at: TEXT (optional ISO 8601 UTC timestamp, or NULL)
         - status: TEXT NOT NULL DEFAULT 'ACTIVE' ('ACTIVE' or 'REVOKED')
         - revoke_token_hash: TEXT (SHA-256 hash of owner's revocation token)
         
      2. 'audit_events' table:
         - id: INTEGER PRIMARY KEY AUTOINCREMENT
         - event_type: TEXT NOT NULL (e.g. PASTE_CREATED, PASTE_REVOKED)
         - paste_id: TEXT NULL (related paste ID if applicable)
         - created_at: TEXT NOT NULL (ISO 8601 UTC timestamp)
         - details: TEXT NOT NULL (JSON string with sanitized metadata)
         - previous_hash: TEXT NOT NULL (SHA-256 hash of previous event, or GENESIS)
         - event_hash: TEXT NOT NULL (SHA-256 hash of current event payload + previous_hash)
    """
    conn = get_db_connection(db_path)
    try:
        with conn:
            # 1. Create pastes table if it does not exist
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pastes (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    revoke_token_hash TEXT
                );
                """
            )

            # 2. Idempotent schema migration for existing databases
            cursor = conn.execute("PRAGMA table_info(pastes);")
            existing_columns = {row["name"] for row in cursor.fetchall()}

            if "status" not in existing_columns:
                conn.execute("ALTER TABLE pastes ADD COLUMN status TEXT NOT NULL DEFAULT 'ACTIVE';")

            if "revoke_token_hash" not in existing_columns:
                conn.execute("ALTER TABLE pastes ADD COLUMN revoke_token_hash TEXT;")

            # Backfill any null statuses to 'ACTIVE'
            conn.execute("UPDATE pastes SET status = 'ACTIVE' WHERE status IS NULL;")

            # 3. Create audit_events table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    paste_id TEXT,
                    created_at TEXT NOT NULL,
                    details TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                );
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audit_created_at
                ON audit_events (created_at);
                """
            )
    finally:
        conn.close()


# Initialize the database on module load to ensure the tables exist
if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
