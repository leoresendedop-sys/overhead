"""Overhead backend for the Desktop card: serves one run's layers as data URLs.

Mounted by Hermes at ``/api/plugins/overhead/``. Hermes loads this file standalone (not as part
of the plugin package), so the engine is loaded by path under a private name, and only the light
modules (``runs``, ``render``; no rasterio) are needed.

Routes:
    GET /runs?limit=20          recent runs (RunStore.list)
    GET /runs/{run_id}/layers   before/after (JPEG), overlay (PNG, keeps alpha), frames (JPEG),
                                chart (PNG) as data URLs, plus result.json. Missing optional
                                files are null / [].
"""

from __future__ import annotations

import base64
import importlib
import importlib.util
import io
import json
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

MAX_SIDE = 1600        # before / after / overlay / chart: long side cap, px
FRAME_MAX_SIDE = 960   # timelapse frames: smaller, there can be many of them

_ENGINE = "overhead_engine_api"
_NOT_FOUND = HTTPException(status_code=404, detail="Run not found.")


def _engine():
    """Import the sibling ``engine`` package under a private name (no rasterio needed)."""
    if _ENGINE not in sys.modules:
        root = Path(__file__).resolve().parent.parent / "engine"
        spec = importlib.util.spec_from_file_location(
            _ENGINE, root / "__init__.py", submodule_search_locations=[str(root)])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[_ENGINE] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(_ENGINE, None)
            raise
    return sys.modules[_ENGINE]


def _runs_root() -> Path:
    from plugins.plugin_storage import plugin_data_dir  # Hermes only; imported lazily

    return Path(plugin_data_dir("overhead")) / "runs"


def _store():
    _engine()
    runs = importlib.import_module(f"{_ENGINE}.runs")
    return runs.RunStore(_runs_root())


def _jpeg_url(path: Path, max_side: int) -> str | None:
    if not path.is_file():
        return None
    from PIL import Image

    render = importlib.import_module(f"{_ENGINE}.render")
    with Image.open(path) as im:
        return render.preview_data_url(im, max_side=max_side)


def _png_url(path: Path, max_side: int) -> str | None:
    if not path.is_file():
        return None
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
        scale = max_side / max(im.size)
        if scale < 1:
            im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                           Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


@router.get("/runs")
def list_runs(limit: int = Query(20, ge=1, le=100)):
    return _store().list(limit)


@router.get("/runs/{run_id}/layers")
def run_layers(run_id: str):
    store = _store()
    bad_input = importlib.import_module(f"{_ENGINE}.errors").BadInput
    try:
        d = store.dir(run_id)
    except bad_input:
        raise _NOT_FOUND
    try:
        result = json.loads((d / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise _NOT_FOUND
    frames_dir = d / "frames"
    frames = sorted(frames_dir.glob("*.jpg")) if frames_dir.is_dir() else []
    return {
        "kind": result.get("kind", run_id.split("-", 1)[0]),
        "before": _jpeg_url(d / "before.jpg", MAX_SIDE),
        "after": _jpeg_url(d / "after.jpg", MAX_SIDE),
        "overlay": _png_url(d / "overlay.png", MAX_SIDE),
        "frames": [u for u in (_jpeg_url(f, FRAME_MAX_SIDE) for f in frames) if u],
        "chart": _png_url(d / "chart.png", MAX_SIDE),
        "result": result,
    }
