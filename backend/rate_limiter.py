import logging
import threading
import time
from collections import defaultdict
from typing import Dict, List, Tuple
from fastapi import HTTPException, Request, status

logger = logging.getLogger("pastebin.rate_limiter")

# Default: 30 paste creations per 60 seconds per IP
DEFAULT_MAX_REQUESTS = 30
DEFAULT_WINDOW_SECONDS = 60


class InMemoryRateLimiter:
    """
    Lightweight in-memory sliding window rate limiter.
    
    Tracks timestamps of recent creation requests per client IP.
    Requires no Redis or external infrastructure services.
    
    Limitations:
    - In-Memory Only: State is stored in process memory. It does not synchronize
      across multiple worker processes or distributed server instances.
    - Non-Persistent: Limit counters reset whenever the server process restarts.
    - Suited for single-process deployments, small-to-medium assignments, and development.
    """
    def __init__(self, max_requests: int = DEFAULT_MAX_REQUESTS, window_seconds: int = DEFAULT_WINDOW_SECONDS):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check_rate_limit(self, client_ip: str) -> Tuple[bool, int]:
        """
        Checks whether client_ip has exceeded max_requests within window_seconds.
        
        Returns:
            (is_allowed: bool, retry_after_seconds: int)
        """
        now = time.monotonic()
        with self._lock:
            cutoff = now - self.window_seconds
            # Evict timestamps older than the sliding window
            timestamps = [t for t in self._requests[client_ip] if t > cutoff]
            self._requests[client_ip] = timestamps

            if len(timestamps) >= self.max_requests:
                oldest = timestamps[0]
                retry_after = max(1, int(oldest + self.window_seconds - now))
                return False, retry_after

            self._requests[client_ip].append(now)
            return True, 0

    def reset(self):
        """Clears all tracked requests (primarily for test isolation)."""
        with self._lock:
            self._requests.clear()


# Default singleton instance for the application
default_rate_limiter = InMemoryRateLimiter()


def get_client_ip(request: Request) -> str:
    """
    Safely extracts the client IP address.
    Does NOT blindly trust client-supplied headers like X-Forwarded-For
    to prevent header spoofing attacks when no trusted reverse proxy is configured.
    """
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def rate_limit_paste_creation(request: Request):
    """
    FastAPI dependency applied to POST /pastes.
    Enforces the sliding window rate limit.
    Raises HTTP 429 Too Many Requests with Retry-After header when exceeded.
    Records RATE_LIMIT_TRIGGERED in the audit trail.
    """
    limiter: InMemoryRateLimiter = getattr(request.app.state, "rate_limiter", default_rate_limiter)
    client_ip = get_client_ip(request)
    is_allowed, retry_after = limiter.check_rate_limit(client_ip)

    if not is_allowed:
        masked_ip = client_ip[:7] + "..." if len(client_ip) > 7 else client_ip
        logger.warning("Rate limit exceeded for client (%s). Retry after %ds", masked_ip, retry_after)

        # Record audit event safely
        try:
            from audit import record_audit_event_standalone
            db_path = getattr(request.app.state, "db_path", None)
            record_audit_event_standalone(
                event_type="RATE_LIMIT_TRIGGERED",
                paste_id=None,
                details={"ip_prefix": masked_ip, "retry_after": retry_after},
                db_path=db_path,
            )
        except Exception:
            pass

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Maximum {limiter.max_requests} paste creation requests per {limiter.window_seconds} seconds. Please wait before creating another paste.",
            headers={"Retry-After": str(retry_after)},
        )

