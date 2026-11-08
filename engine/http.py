"""Minimal JSON-over-HTTP helpers on the standard library."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .errors import NetworkError

RETRY_DELAYS = (1, 2)  # seconds before the 2nd and 3rd attempt


def _send(req: urllib.request.Request, timeout: float):
    where = req.full_url.split("?")[0]
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise NetworkError(f"HTTP {e.code} from {where}", transient=e.code >= 500) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise NetworkError(f"Request to {where} failed: {e}", transient=True) from e
    except ValueError as e:
        raise NetworkError(f"Invalid JSON from {where}") from e


def get_json(url: str, params: dict, headers: dict, timeout=30) -> dict | list:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    return _send(urllib.request.Request(url, headers=dict(headers or {})), timeout)


def post_json(url: str, body: dict, headers: dict, timeout=60) -> dict:
    h = {"Content-Type": "application/json", **(headers or {})}
    data = json.dumps(body).encode("utf-8")
    return _send(urllib.request.Request(url, data=data, headers=h, method="POST"), timeout)


def retry_transient(call, *, delays=RETRY_DELAYS, sleep=time.sleep):
    """call(), retried after each delay while it fails with a transient NetworkError."""
    for delay in delays:
        try:
            return call()
        except NetworkError as e:
            if not e.transient:
                raise
        sleep(delay)
    return call()
