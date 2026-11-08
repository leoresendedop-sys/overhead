"""Overhead: what changed at this place between these dates? Free Sentinel-2 imagery for Hermes."""

from __future__ import annotations

import logging
from pathlib import Path

from . import hooks, schemas
from .handlers import make_handlers

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


def register(ctx):
    from plugins.plugin_storage import plugin_data_dir

    handlers = make_handlers(plugin_data_dir("overhead"))
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
