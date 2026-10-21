from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator

# Expiry limits: minimum 1 minute, maximum 30 days (30 * 24 * 60 = 43,200 minutes)
MIN_EXPIRY_MINUTES = 1
MAX_EXPIRY_MINUTES = 30 * 24 * 60  # 43,200 minutes

# Maximum content limit: 100 KB = 100 * 1024 bytes = 102,400 bytes
MAX_CONTENT_BYTES = 100 * 1024


class PasteCreateRequest(BaseModel):
    """
    Request model for creating a new paste.
    Validates content and expiry_minutes strictly according to requirements:
      - content: non-empty, non-whitespace, max 100 KB UTF-8 bytes
      - expiry_minutes: integer from 1 to 43,200 minutes (up to 30 days)
    """
    content: str
    expiry_minutes: int

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        # Reject missing, empty, or whitespace-only content
        if not v or not v.strip():
            raise ValueError("Paste content cannot be empty or contain only whitespace.")
        
        # Enforce maximum size on UTF-8 encoded bytes
        byte_length = len(v.encode("utf-8"))
        if byte_length > MAX_CONTENT_BYTES:
            raise ValueError(
                f"Paste content exceeds maximum limit of 100 KB "
                f"({byte_length:,} bytes provided, limit is {MAX_CONTENT_BYTES:,} bytes)."
            )
        return v

    @field_validator("expiry_minutes", mode="before")
    @classmethod
    def validate_expiry_minutes(cls, v: Any) -> int:
        # Strictly reject booleans (bool is a subclass of int in Python)
        if type(v) is bool:
            raise ValueError("expiry_minutes cannot be a boolean; it must be an integer.")
        
        # Reject strings, floats, lists, dicts, decimals, etc.
        if not isinstance(v, int):
            raise ValueError("expiry_minutes must be an integer (between 1 and 43200 minutes).")
        
        # Reject values less than 1 or greater than 43,200 minutes (30 days)
        if v < MIN_EXPIRY_MINUTES or v > MAX_EXPIRY_MINUTES:
            raise ValueError(
                f"expiry_minutes must be between {MIN_EXPIRY_MINUTES} and {MAX_EXPIRY_MINUTES} minutes (up to 30 days)."
            )
        return v


class PasteCreateResponse(BaseModel):
    """
    Response model returned when a paste is successfully created.
    Includes the owner's secret revocation token (returned only once).
    """
    id: str
    url: str
    created_at: str
    expires_at: str
    revoke_token: Optional[str] = None


class PasteViewResponse(BaseModel):
    """
    Response model returned when viewing an active, unexpired paste.
    Never exposes revocation tokens or internal hashes.
    """
    id: str
    content: str
    created_at: str
    expires_at: Optional[str] = None


class PasteRevokeResponse(BaseModel):
    """
    Response model returned when a paste is revoked.
    """
    status: str
    message: str


class AuditVerifyResponse(BaseModel):
    """
    Response model returned when an administrator verifies the audit hash chain.
    """
    valid: bool
    total_events: int
    message: str
    error_index: Optional[int] = None
    event_id: Optional[int] = None
    error_details: Optional[str] = None

