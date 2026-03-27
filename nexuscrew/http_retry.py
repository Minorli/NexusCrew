"""Small retry helper for outbound HTTPS calls."""
import socket
import ssl
import time
import urllib.error


NETWORK_ERRORS = (
    urllib.error.URLError,
    TimeoutError,
    socket.timeout,
    ssl.SSLError,
    OSError,
)


def with_retries(func, retries: int = 3, backoff_seconds: float = 1.0):
    """Execute func() with lightweight retry for transient network errors."""
    last_err = None
    for attempt in range(retries):
        try:
            return func()
        except NETWORK_ERRORS as err:
            last_err = err
            if attempt + 1 >= retries:
                raise
            time.sleep(backoff_seconds * (attempt + 1))
    raise last_err  # pragma: no cover
