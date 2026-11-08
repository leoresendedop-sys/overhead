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


@pytest.mark.parametrize("name,args,hint", [
    ("overhead_compare", {"before": "2025-02-15", "after": "2024-12-15"}, "swap"),
    ("overhead_compare", {"before": "2025-02", "after": "2025-02-15"}, "swap"),  # same day
    ("overhead_timelapse", {"start": "2025-06", "end": "2025-01"}, "after start"),
    ("overhead_scenes", {"start": "2025-06-01", "end": "2025-01-01"}, "after start"),
])
def test_inverted_range_rejected_before_place_lookup(tools, h, monkeypatch, name, args, hint):
    calls = []
    monkeypatch.setattr(h, "resolve_aoi", lambda a, cache_dir: calls.append(a))
    err = error_of(tools[name]({"place": "x", **args}))
    assert err["type"] == "bad_input" and hint in err["message"] and calls == []


def test_compare_missing_date_is_bad_input(tools):
    err = error_of(tools["overhead_compare"]({"place": "x", "after": "2025-01-01"}))
    assert err["type"] == "bad_input" and "before" in err["message"]


CALLS = {
    "overhead_locate": ({"place": "x"}, "resolution_for_area"),
    "overhead_scenes": ({"place": "x", "start": "2024-01-01", "end": "2024-02-01"}, "run_scenes"),
    "overhead_compare": (COMPARE, "run_compare"),
    "overhead_timelapse": ({"place": "x", "start": "2024-01", "end": "2024-06"}, "run_timelapse"),
}


@pytest.mark.parametrize("name", CALLS)
def test_handler_never_raises(h, tmp_path, monkeypatch, name):
    args, engine_call = CALLS[name]

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(h, engine_call, boom)  # raises inside the engine step, after the AOI
    err = error_of(h.make_handlers(tmp_path)[name](dict(args), task_id="t1"))
    assert err["type"] == "internal" and "boom" in err["message"]


@pytest.mark.parametrize("name", ["overhead_scenes", "overhead_compare", "overhead_timelapse"])
def test_malformed_engine_result_is_internal(h, tmp_path, monkeypatch, name):
    args, engine_call = CALLS[name]
    monkeypatch.setattr(h, engine_call, lambda *a, **k: {"place": {}})  # breaks payload building
    err = error_of(h.make_handlers(tmp_path)[name](dict(args)))
    assert err["type"] == "internal"


def test_error_details_that_are_not_json_still_serialize(h, tmp_path, monkeypatch, plugin):
    def no_clear(*a, **k):
        raise plugin.engine.errors.NoClearScene("No clear image.", target=date(2025, 1, 15),
                                                where=Path("/x"))
    monkeypatch.setattr(h, "run_compare", no_clear)
    err = error_of(h.make_handlers(tmp_path)["overhead_compare"](dict(COMPARE)))
    assert err["type"] == "no_clear_scene" and err["target"] == "2025-01-15" and err["where"] == "/x"


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
    assert data["instruction"] == "End your reply with this exact line: " + data["deliver"]
    assert data["headline"].startswith("≈ ") and data["confidence"] == "high"
    assert data["resolution_m"] == 10 and data["stats"]["burn"]["high_ha"] >= 0
    assert data["before"]["date"] == "2024-12-17" and data["after"]["clear_fraction"] == 1.0
    assert data["run_id"].startswith("compare-") and data["notes"]
    assert set(data["place"]) == {"name", "full_name", "area_km2"} and data["place"]["full_name"] == "Testville"
    assert data["stats"]["burn"]["burned_ha"] == round(
        data["stats"]["burn"]["moderate_ha"] + data["stats"]["burn"]["high_ha"], 2)
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
    # the summary is what text-only providers and resumed sessions see: keep every number
    assert out["text_summary"] == out["content"][0]["text"]


def test_point_with_place_uses_place_as_title(plugin, tmp_path):
    loc = plugin.handlers.make_handlers(tmp_path)["overhead_locate"](
        {"place": "Altadena, California", "lat": 34.19, "lon": -118.13, "radius_km": 2})
    data = json.loads(loc)
    assert data["name"] == "Altadena, California" and data["source"] == "point"
    assert data["full_name"] == "Altadena, California"
    assert data["resolution_m"] == 10 and 15 < data["area_km2"] < 17 and len(data["bbox"]) == 4


@pytest.mark.parametrize("empty", [None, ""])
def test_empty_place_fields_mean_not_given(plugin, tmp_path, empty):
    loc = plugin.handlers.make_handlers(tmp_path)["overhead_locate"](
        {"place": "Altadena", "lat": 34.19, "lon": -118.13, "radius_km": empty, "geojson": empty})
    data = json.loads(loc)
    assert data["name"] == "Altadena" and 99 < data["area_km2"] < 101  # default radius 5 km


def test_geojson_with_place_uses_place_as_title(plugin, tmp_path):
    square = {"type": "Polygon", "coordinates": [[[-118.15, 34.18], [-118.11, 34.18],
                                                  [-118.11, 34.21], [-118.15, 34.21],
                                                  [-118.15, 34.18]]]}
    data = json.loads(plugin.handlers.make_handlers(tmp_path)["overhead_locate"](
        {"place": "Altadena", "geojson": json.dumps(square)}))
    assert data["name"] == "Altadena" and data["source"] == "geojson"


def test_schemas_bound_the_place_numbers_and_ask_for_result_numbers(plugin):
    for schema in plugin.schemas.ALL:
        props = schema["parameters"]["properties"]
        assert props["radius_km"]["exclusiveMinimum"] == 0 and props["radius_km"]["maximum"] == 100
        assert (props["lat"]["minimum"], props["lat"]["maximum"]) == (-90, 90)
        assert (props["lon"]["minimum"], props["lon"]["maximum"]) == (-180, 180)
        assert "only numbers to report" in schema["description"]
    compare = plugin.schemas.COMPARE["description"]
    assert "Report `headline` and the stats exactly as given." in compare
    assert "Never add, subtract or recompute figures." in compare
    assert "`burn.low_ha` is low-severity change (often seasonal or urban noise) and is NOT " \
           "counted as burned." in compare


def test_timelapse_delivers_gif_with_first_and_last_frame(tools, h, monkeypatch):
    args = {"place": "x", "start": "2024-09-01", "end": "2024-12-31", "step": "month"}
    text = json.loads(tools["overhead_timelapse"](dict(args)))
    assert text["deliver"].endswith("timelapse.gif") and text["metric"] == "ndvi"
    assert len(text["series"]) == 4 and "frames" not in text["files"]
    monkeypatch.setattr(h, "want_images", lambda: True)
    env = tools["overhead_timelapse"](dict(args))
    assert sum(p["type"] == "image_url" for p in env["content"]) == 2
    assert env["text_summary"] == env["content"][0]["text"] and "timelapse.gif" in env["text_summary"]


def test_want_images_is_false_without_hermes(plugin, monkeypatch):
    monkeypatch.setitem(sys.modules, "tools", None)  # makes "import tools.vision_tools" fail
    assert plugin.handlers.want_images() is False


class FakeCtx:
    def __init__(self, failing_hook=None):
        self.handlers, self.hooks, self.skills, self.failing_hook = {}, [], [], failing_hook

    def register_tool(self, name, toolset, schema, handler, **kw):
        assert toolset == "overhead" and schema["name"] == name and callable(handler)
        self.handlers[name] = handler

    def register_hook(self, name, fn, **kw):
        if name == self.failing_hook:
            raise ValueError("unknown hook")  # an older Hermes
        self.hooks.append(name)

    def register_skill(self, name, path, **kw):
        assert Path(path).is_file() and kw.get("description")
        self.skills.append(name)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    mod = types.ModuleType("plugins.plugin_storage")
    mod.plugin_data_dir = lambda name: tmp_path / name
    monkeypatch.setitem(sys.modules, "plugins", types.ModuleType("plugins"))
    monkeypatch.setitem(sys.modules, "plugins.plugin_storage", mod)


def test_register_survives_a_failing_registration(plugin, storage):
    ctx = FakeCtx(failing_hook="transform_llm_output")
    plugin.register(ctx)
    assert list(ctx.handlers) == ["overhead_locate", "overhead_scenes", "overhead_compare",
                                  "overhead_timelapse"]
    assert ctx.hooks == ["post_tool_call"] and ctx.skills == ["earth-watch"]


def test_register_does_not_import_the_engine(storage):
    from conftest import load_plugin
    name = "overhead_lazy_probe"
    try:
        ctx = FakeCtx()
        load_plugin(name).register(ctx)  # Hermes gives import + register() 10 s
        assert f"{name}.handlers" not in sys.modules and f"{name}.engine" not in sys.modules
        out = ctx.handlers["overhead_compare"]({"place": "x", "before": "2019", "after": "2025-01"})
        assert json.loads(out)["error"]["type"] == "bad_input"
        assert f"{name}.handlers" in sys.modules
    finally:
        for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
            del sys.modules[key]


def test_tools_report_a_missing_engine(plugin, storage, monkeypatch):
    monkeypatch.setitem(sys.modules, "overhead_plugin.handlers", None)  # import fails
    ctx = FakeCtx()
    plugin.register(ctx)
    err = error_of(ctx.handlers["overhead_locate"]({"place": "x"}))
    assert err["type"] == "internal" and "rasterio" in err["message"]
    assert "hermes gateway restart" in err["message"]  # a process started before the install
