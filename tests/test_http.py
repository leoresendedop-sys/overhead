import io
import urllib.error
import urllib.request

import pytest

from engine.errors import NetworkError
from engine.http import post_json, retry_transient


def _raise(exc):
    def urlopen(req, timeout):
        raise exc
    return urlopen


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize("exc,transient", [
    (urllib.error.HTTPError("https://x/search", 503, "busy", {}, None), True),
    (urllib.error.HTTPError("https://x/search", 400, "bad", {}, None), False),
    (urllib.error.URLError("connection refused"), True),
    (TimeoutError("timed out"), True),
])
def test_network_errors_say_whether_a_retry_can_help(monkeypatch, exc, transient):
    monkeypatch.setattr(urllib.request, "urlopen", _raise(exc))
    with pytest.raises(NetworkError) as e:
        post_json("https://x/search", {}, {})
    assert e.value.transient is transient and "transient" not in e.value.extra


def test_invalid_json_is_not_transient(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: _Resp(b"<html>"))
    with pytest.raises(NetworkError) as e:
        post_json("https://x/search", {}, {})
    assert e.value.transient is False


def test_retry_transient_returns_the_first_success():
    calls, sleeps = [], []

    def call():
        calls.append(1)
        if len(calls) < 2:
            raise NetworkError("timed out", transient=True)
        return "ok"
    assert retry_transient(call, sleep=sleeps.append) == "ok" and sleeps == [1]
