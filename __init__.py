"""Overhead: what changed at this place between these dates? Free Sentinel-2 imagery for Hermes."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from . import hooks, schemas

logger = logging.getLogger(__name__)

TOOLSET = "overhead"
SKILL_MD = Path(__file__).resolve().parent / "skills" / "earth-watch" / "SKILL.md"


def _skill_description(path: Path) -> str:
    """The description line of the SKILL.md frontmatter (shown in the skills index)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if line.startswith("description:"):
                return line.split(":", 1)[1].strip().strip("\"'")
    return ""


def _lazy_handlers(data_root: Path) -> dict:
    """Tool handlers that import the engine on the first call, not at plugin load.

    The engine pulls in rasterio and GDAL. The first load from a freshly installed environment
    can take longer than Hermes's plugin-load deadline (10 s by default), which would drop the
    plugin for that session. A tool call has minutes.
    """
    lock = threading.Lock()
    built: dict = {}

    def table() -> dict:
        with lock:
            if not built:
                from .handlers import make_handlers
                built.update(make_handlers(data_root))
        return built

    def lazy(name: str):
        def handler(args, **kwargs):
            try:
                real = table()[name]
            except Exception as e:  # e.g. rasterio is not installed
                logger.exception("overhead: engine failed to load")
                return json.dumps({"error": {
                    "type": "internal",
                    "message": f"The Overhead engine could not load ({type(e).__name__}: {e}). "
                               "Its Python dependency rasterio may be missing from the Hermes "
                               "environment."}})
            return real(args, **kwargs)
        handler.__name__ = name
        return handler

    return {schema["name"]: lazy(schema["name"]) for schema in schemas.ALL}


def register(ctx):
    from plugins.plugin_storage import plugin_data_dir

    handlers = _lazy_handlers(plugin_data_dir("overhead"))
    for schema in schemas.ALL:
        name = schema["name"]
        try:
            ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handlers[name],
                              emoji="🛰️")
        except Exception:
            logger.warning("overhead: tool %s not registered", name, exc_info=True)
    for event, callback in (("post_tool_call", hooks.record_media),
                            ("transform_llm_output", hooks.append_missing_media)):
        try:
            ctx.register_hook(event, callback)
        except Exception:
            logger.warning("overhead: hook %s not registered; the deliver line in each result "
                           "still asks the model to attach the image", event, exc_info=True)
    try:
        ctx.register_skill("earth-watch", SKILL_MD, description=_skill_description(SKILL_MD))
    except Exception:
        logger.warning("overhead: skill earth-watch not registered", exc_info=True)
