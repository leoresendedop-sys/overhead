"""Tool handlers: check the arguments, run the engine, shape the result for the model.

Handlers never raise. They return a JSON string, or the multimodal envelope when the main model
can see images in tool results.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import Callable

from PIL import Image

from .engine.aoi import resolution_for, resolve_aoi
from .engine.errors import BadInput, OverheadError, to_error_dict
from .engine.pipeline import parse_date, run_compare, run_scenes, run_timelapse
from .engine.render import preview_data_url
from .engine.runs import RunStore

logger = logging.getLogger(__name__)

INSTRUCTION = "End your reply with this exact line: "


class _Internal(OverheadError):
    type = "internal"


def want_images() -> bool:
    """True when the main model takes images in tool results (Hermes's own gate for this)."""
    try:
        from tools.vision_tools import _should_use_native_vision_fast_path
        return bool(_should_use_native_vision_fast_path())
    except Exception:
        return False


def _json(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)  # never raises on odd details


def _or(args: dict, key: str, default):
    """The argument, or its default when it is missing, null or empty."""
    value = args.get(key)
    return default if value is None or value == "" else value


def _date(args: dict, field: str, role: str = "point"):
    value = _or(args, field, None)
    if value is None:
        raise BadInput(f"{field} is required: use YYYY-MM-DD or YYYY-MM.")
    return parse_date(value, field=field, role=role)


def _range(args: dict, first: str, last: str, message: str, *, months: str = "whole"):
    """Both dates of a range, checked before any place lookup (which is network I/O).

    months="whole": a YYYY-MM start is its 1st and a YYYY-MM end its last day (scenes, timelapse).
    months="mid": each YYYY-MM is its 15th (compare's before and after are points in time)."""
    roles = ("start", "end") if months == "whole" else ("point", "point")
    a, b = _date(args, first, roles[0]), _date(args, last, roles[1])
    if b <= a:
        raise BadInput(message)
    return a, b


def _aoi(args: dict, cache_dir: Path):
    args = {k: v for k, v in args.items() if v is not None and v != ""}  # empty means not given
    aoi = resolve_aoi(args, cache_dir)
    if args.get("place") and aoi.source in ("point", "geojson"):  # then the place is only a title
        aoi = dataclasses.replace(aoi, name=str(args["place"]).strip())
    return aoi


def _place(result: dict) -> dict:
    place = result["place"]
    return {"name": place["name"], "full_name": place["full_name"], "area_km2": place["area_km2"]}


def _side(side: dict) -> dict:
    return {k: side[k] for k in ("requested", "date", "clear_fraction", "composite")}


def _deliverable(payload: dict, media: str, previews: list[str]) -> str | dict:
    """Add the deliver line; wrap in the multimodal envelope when the model can see images."""
    payload["deliver"] = f"MEDIA:{media}"
    payload["instruction"] = INSTRUCTION + payload["deliver"]
    text = _json(payload)
    if not want_images():
        return text
    try:
        images = []
        for path in previews:
            with Image.open(path) as img:
                images.append({"type": "image_url", "image_url": {"url": preview_data_url(img)}})
    except Exception:
        logger.warning("overhead: could not attach preview images", exc_info=True)
        return text
    return {
        "_multimodal": True,
        "content": [{"type": "text", "text": text}, *images],
        "text_summary": text,  # what text-only providers and resumed sessions get: keep it all
    }


def make_handlers(data_root: Path) -> dict[str, Callable[[dict], str | dict]]:
    cache_dir = Path(data_root) / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    store = RunStore(Path(data_root) / "runs")

    def locate(args: dict):
        aoi = _aoi(args, cache_dir)
        return _json({"name": aoi.name, "full_name": aoi.full_name or aoi.name,
                      "bbox": list(aoi.bbox), "area_km2": aoi.area_km2, "source": aoi.source,
                      "resolution_m": resolution_for(aoi)})

    def scenes(args: dict):
        start, end = _range(args, "start", "end", "end must be after start.")
        n = _or(args, "max_results", 12)
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 30:
            raise BadInput("max_results must be a whole number between 1 and 30.")
        r = run_scenes(_aoi(args, cache_dir), start, end, cache_dir, max_results=n)
        return _json({"place": _place(r), "dates": r["dates"], "notes": r["notes"]})

    def compare(args: dict):
        before, after = _range(args, "before", "after",
                               "after must be later than before; swap the two dates.", months="mid")
        aoi = _aoi(args, cache_dir)
        r = run_compare(aoi, before, after, _or(args, "focus", "auto"),
                        _or(args, "window_days", 30), cache_dir, store)
        payload = {
            "run_id": r["run_id"], "place": _place(r), "headline": r["headline"], "focus": r["focus"],
            "stats": r["stats"], "confidence": r["confidence"], "valid_fraction": r["valid_fraction"],
            "resolution_m": r["resolution_m"], "before": _side(r["before"]),
            "after": _side(r["after"]), "notes": r["notes"], "files": r["files"],
        }
        return _deliverable(payload, r["files"]["panel"], [r["files"]["before"], r["files"]["after"]])

    def timelapse(args: dict):
        start, end = _range(args, "start", "end", "end must be after start.")
        aoi = _aoi(args, cache_dir)
        r = run_timelapse(aoi, start, end, _or(args, "step", "month"), _or(args, "metric", "ndvi"),
                          cache_dir, store)
        frames = r["files"]["frames"]
        payload = {
            "run_id": r["run_id"], "place": _place(r), "headline": r["headline"],
            "metric": r["metric"], "resolution_m": r["resolution_m"], "series": r["series"],
            "notes": r["notes"], "files": {"gif": r["files"]["gif"], "chart": r["files"]["chart"]},
        }
        return _deliverable(payload, r["files"]["gif"], list(dict.fromkeys([frames[0], frames[-1]])))

    return {name: _safe(fn) for name, fn in (
        ("overhead_locate", locate), ("overhead_scenes", scenes),
        ("overhead_compare", compare), ("overhead_timelapse", timelapse))}


def _safe(fn: Callable[[dict], str | dict]) -> Callable[..., str | dict]:
    def handler(args: dict, **kwargs) -> str | dict:
        try:
            return fn(args if isinstance(args, dict) else {})
        except OverheadError as e:
            return _json(to_error_dict(e))
        except Exception as e:
            logger.exception("overhead: %s failed", fn.__name__)
            return _json(to_error_dict(_Internal(
                f"Unexpected error ({type(e).__name__}: {e}). Try again; if it persists, the inputs "
                "may be unusual: try a smaller area or other dates.")))
    handler.__name__ = fn.__name__
    return handler
