"""MEDIA guardrail: the reply always carries the files the Overhead tools produced.

post_tool_call records the deliver path of each compare or timelapse result; transform_llm_output
appends a MEDIA line for any of them the reply does not already deliver. Records are keyed by
(session_id, turn_id): Hermes passes both hooks the same turn id (agent._current_turn_id), so a
one-shot run with no session id still matches, and a turn that ends without text cannot push its
files into a later reply.
"""

from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict

MEDIA_TOOLS = frozenset({"overhead_compare", "overhead_timelapse"})
MAX_TURNS = 50
_FENCED = re.compile(r"(?s)```.*?(?:```|\Z)")

_lock = threading.Lock()
_pending: OrderedDict[tuple[str, str], list[str]] = OrderedDict()


def _key(session_id, turn_id) -> tuple[str, str] | None:
    key = (str(session_id or ""), str(turn_id or ""))
    return key if any(key) else None


def _tool_text(result) -> str:
    """The JSON text of a tool result: the string itself, or an envelope's first text part."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for part in result.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "text":
                return str(part.get("text", ""))
    return ""


def _deliver_path(result) -> str | None:
    try:
        deliver = json.loads(_tool_text(result)).get("deliver")
    except (ValueError, AttributeError):
        return None
    if isinstance(deliver, str) and deliver.startswith("MEDIA:") and len(deliver) > 6:
        return deliver[6:]
    return None


def _delivered(path: str, text: str) -> bool:
    """True when MEDIA:<path> stands on a line of its own outside fenced code: the only form
    Hermes delivers for sure (it masks tags in JSON strings, code and quotes)."""
    visible = _FENCED.sub("", text)
    return re.search(rf"(?m)^[ \t]*MEDIA:{re.escape(path)}[ \t\r]*$", visible) is not None


def record_media(tool_name, args, result, task_id=None, **kw) -> None:
    if tool_name not in MEDIA_TOOLS:
        return None
    key = _key(kw.get("session_id"), kw.get("turn_id"))
    path = _deliver_path(result)
    if key is None or not path:
        return None
    with _lock:
        paths = _pending.pop(key, [])
        if path not in paths:
            paths.append(path)
        _pending[key] = paths  # most recent last
        while len(_pending) > MAX_TURNS:
            _pending.popitem(last=False)
    return None


def append_missing_media(response_text, session_id=None, **kw) -> str | None:
    key = _key(session_id, kw.get("turn_id"))
    if key is None:
        return None
    with _lock:
        paths = _pending.pop(key, None)
    if not paths or not isinstance(response_text, str):
        return None
    missing = [p for p in paths if not _delivered(p, response_text)]
    if not missing:
        return None
    return response_text + "".join(f"\n\nMEDIA:{p}" for p in missing)
