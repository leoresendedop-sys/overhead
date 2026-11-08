"""MEDIA guardrail: the reply always carries the files the Overhead tools produced.

post_tool_call records the deliver path of each compare or timelapse result per session;
transform_llm_output appends any MEDIA line the model left out. Both hooks receive the same
session_id (the agent's), so that is the key.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict

MEDIA_TOOLS = frozenset({"overhead_compare", "overhead_timelapse"})
MAX_SESSIONS = 50

_lock = threading.Lock()
_pending: OrderedDict[str, list[str]] = OrderedDict()


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


def record_media(tool_name, args, result, task_id=None, **kw) -> None:
    if tool_name not in MEDIA_TOOLS:
        return None
    key = kw.get("session_id") or task_id
    path = _deliver_path(result)
    if not key or not path:
        return None
    with _lock:
        paths = _pending.pop(key, [])
        if path not in paths:
            paths.append(path)
        _pending[key] = paths  # most recent last
        while len(_pending) > MAX_SESSIONS:
            _pending.popitem(last=False)
    return None


def append_missing_media(response_text, session_id=None, **kw) -> str | None:
    if not session_id:
        return None
    with _lock:
        paths = _pending.pop(session_id, None)
    if not paths or not isinstance(response_text, str):
        return None
    missing = [p for p in paths if f"MEDIA:{p}" not in response_text]
    if not missing:
        return None
    return response_text + "".join(f"\n\nMEDIA:{p}" for p in missing)
