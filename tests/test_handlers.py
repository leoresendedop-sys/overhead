import json
import sys
import types
from datetime import date, timedelta
from pathlib import Path

import pytest

from test_pipeline import make_reader, make_search, scene

COMPARE = {"place": "Testville", "before": "2024-12-15", "after": "2025-02-15"}
SCENES = [scene("2024-12-17"), scene("2025-02-14")]
LAPSE_SCENES = [scene("2024-09-14"), scene("2024-10-12"), scene("2024-11-09"), scene("2024-12-05")]


@pytest.fixture
def h(plugin, monkeypatch):
    """The handlers module with place lookup and imagery faked (no network)."""
    mod = plugin.handlers
    pipeline = plugin.engine.pipeline
    aoi = plugin.engine.aoi.aoi_from_point(34.19, -118.13, 1, "Testville")
    monkeypatch.setattr(mod, "resolve_aoi", lambda args, cache_dir: aoi)
    monkeypatch.setattr(mod, "run_compare", lambda *a, **k: pipeline.run_compare(
        *a, search=make_search(SCENES), reader=make_reader({}), **k))
    monkeypatch.setattr(mod, "run_timelapse", lambda *a, **k: pipeline.run_timelapse(
        *a, search=make_search(LAPSE_SCENES), reader=make_reader({}), **k))
    monkeypatch.setattr(mod, "want_images", lambda: False)
    return mod


@pytest.fixture
def tools(h, tmp_path):
    return h.make_handlers(tmp_path)


def error_of(result):
    assert isinstance(result, str)
    return json.loads(result)["error"]


FUTURE = (date.today() + timedelta(days=5)).isoformat()


@pytest.mark.parametrize("before,after,field,hint", [
    ("2019", "2025-01-01", "before", "overhead_scenes"),
    ("last summer", "2025-01-01", "before", "YYYY-MM-DD or YYYY-MM"),
    ("2024-12-15", FUTURE, "after", "future"),
])
def test_compare_rejects_bad_dates(tools, h, monkeypatch, before, after, field, hint):
    calls = []
    monkeypatch.setattr(h, "resolve_aoi", lambda args, cache_dir: calls.append(args))
    err = error_of(tools["overhead_compare"]({"place": "x", "before": before, "after": after}))
    assert err["type"] == "bad_input" and field in err["message"] and hint in err["message"]
    assert calls == []  # a bad date never reaches place lookup, which is network I/O


def test_compare_rejects_inverted_range(tools):
    err = error_of(tools["overhead_compare"]({"place": "x", "before": "2025-02-15", "after": "2024-12-15"}))
    assert err["type"] == "bad_input" and "swap" in err["message"]


def test_compare_missing_date_is_bad_input(tools):
    err = error_of(tools["overhead_compare"]({"place": "x", "after": "2025-01-01"}))
    assert err["type"] == "bad_input" and "before" in err["message"]


@pytest.mark.parametrize("name,args", [
    ("overhead_locate", {"place": "x"}),
    ("overhead_scenes", {"place": "x", "start": "2024-01-01", "end": "2024-02-01"}),
    ("overhead_compare", COMPARE),
    ("overhead_timelapse", {"place": "x", "start": "2024-01", "end": "2024-06"}),
])
def test_handler_never_raises(h, tmp_path, monkeypatch, name, args):
    def boom(*a, **k):
        raise RuntimeError("boom")
    for target in ("resolve_aoi", "run_compare", "run_scenes", "run_timelapse"):
        monkeypatch.setattr(h, target, boom)
    err = error_of(h.make_handlers(tmp_path)[name](args, task_id="t1"))
    assert err["type"] == "internal" and "boom" in err["message"]


def test_overhead_error_keeps_its_details(h, tmp_path, monkeypatch, plugin):
    cands = [{"name": "Springfield, Illinois", "country": "United States", "area_km2": 158.0}]

    def ambiguous(args, cache_dir):
        raise plugin.engine.errors.AmbiguousPlace("'Springfield' matches several places.",
                                                  candidates=cands)
    monkeypatch.setattr(h, "resolve_aoi", ambiguous)
    err = error_of(h.make_handlers(tmp_path)["overhead_locate"]({"place": "Springfield"}))
    assert err["type"] == "ambiguous_place" and err["candidates"] == cands


def test_compare_text_when_no_vision(tools):
    out = tools["overhead_compare"](dict(COMPARE))
    assert isinstance(out, str) and '"deliver": "MEDIA:' in out
    data = json.loads(out)
    assert data["deliver"] == "MEDIA:" + data["files"]["panel"]
    assert data["deliver"].endswith("panel.png") and Path(data["files"]["panel"]).exists()
    assert "verbatim" in data["instruction"]
    assert data["headline"].startswith("≈ ") and data["confidence"] == "high"
    assert data["resolution_m"] == 10 and data["stats"]["burn"]["high_ha"] >= 0
    assert data["before"]["date"] == "2024-12-17" and data["after"]["clear_fraction"] == 1.0
    assert data["run_id"].startswith("compare-") and data["notes"]
    assert "provenance" not in data and "thresholds" not in data  # kept in result.json


def test_compare_envelope_when_vision(tools, h, monkeypatch):
    monkeypatch.setattr(h, "want_images", lambda: True)
    out = tools["overhead_compare"](dict(COMPARE))
    assert isinstance(out, dict) and out["_multimodal"] is True
    images = [p for p in out["content"] if p["type"] == "image_url"]
    assert len(images) == 2
    assert all(p["image_url"]["url"].startswith("data:image/jpeg;base64,") for p in images)
    text = json.loads(out["content"][0]["text"])
    assert text["deliver"].startswith("MEDIA:")
    assert "MEDIA:" in out["text_summary"] and text["headline"] in out["text_summary"]


def test_point_with_place_uses_place_as_title(plugin, tmp_path):
    loc = plugin.handlers.make_handlers(tmp_path)["overhead_locate"](
        {"place": "Altadena, California", "lat": 34.19, "lon": -118.13, "radius_km": 2})
    data = json.loads(loc)
    assert data["name"] == "Altadena, California" and data["source"] == "point"
    assert data["resolution_m"] == 10 and 15 < data["area_km2"] < 17 and len(data["bbox"]) == 4


def test_timelapse_delivers_gif_with_first_and_last_frame(tools, h, monkeypatch):
    args = {"place": "x", "start": "2024-09-01", "end": "2024-12-31", "step": "month"}
    text = json.loads(tools["overhead_timelapse"](dict(args)))
    assert text["deliver"].endswith("timelapse.gif") and text["metric"] == "ndvi"
    assert len(text["series"]) == 4 and "frames" not in text["files"]
    monkeypatch.setattr(h, "want_images", lambda: True)
    env = tools["overhead_timelapse"](dict(args))
    assert sum(p["type"] == "image_url" for p in env["content"]) == 2
    assert "timelapse.gif" in env["text_summary"]


def test_want_images_is_false_without_hermes(plugin, monkeypatch):
    monkeypatch.setitem(sys.modules, "tools", None)  # makes "import tools.vision_tools" fail
    assert plugin.handlers.want_images() is False


def test_register_survives_a_failing_registration(plugin, tmp_path, monkeypatch):
    storage = types.ModuleType("plugins.plugin_storage")
    storage.plugin_data_dir = lambda name: tmp_path / name
    monkeypatch.setitem(sys.modules, "plugins", types.ModuleType("plugins"))
    monkeypatch.setitem(sys.modules, "plugins.plugin_storage", storage)

    class Ctx:
        def __init__(self):
            self.tools, self.hooks, self.skills = [], [], []

        def register_tool(self, name, toolset, schema, handler, **kw):
            assert toolset == "overhead" and schema["name"] == name and callable(handler)
            self.tools.append(name)

        def register_hook(self, name, fn, **kw):
            if name == "transform_llm_output":
                raise ValueError("unknown hook")  # an older Hermes
            self.hooks.append(name)

        def register_skill(self, name, path, **kw):
            assert Path(path).is_file() and kw.get("description")
            self.skills.append(name)

    ctx = Ctx()
    plugin.register(ctx)
    assert ctx.tools == ["overhead_locate", "overhead_scenes", "overhead_compare", "overhead_timelapse"]
    assert ctx.hooks == ["post_tool_call"] and ctx.skills == ["earth-watch"]
