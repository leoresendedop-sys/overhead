"""Minimal JSON-over-HTTP helpers on the standard library."""

import json
import urllib.error
import urllib.parse
import urllib.request

from .errors import NetworkError


def _send(req: urllib.request.Request, timeout: float):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise NetworkError(f"HTTP {e.code} from {req.full_url.split('?')[0]}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise NetworkError(f"Request to {req.full_url.split('?')[0]} failed: {e}") from e
    except ValueError as e:
        raise NetworkError(f"Invalid JSON from {req.full_url.split('?')[0]}") from e


def get_json(url: str, params: dict, headers: dict, timeout=30) -> dict | list:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    return _send(urllib.request.Request(url, headers=dict(headers or {})), timeout)


def post_json(url: str, body: dict, headers: dict, timeout=60) -> dict:
    h = {"Content-Type": "application/json", **(headers or {})}
    data = json.dumps(body).encode("utf-8")
    return _send(urllib.request.Request(url, data=data, headers=h, method="POST"), timeout)
