"""Live smoke tests: the three scenes of the demo, on real Sentinel-2 data.

Skipped unless OVERHEAD_LIVE=1. They call the tool handlers exactly as Hermes does. Set
OVERHEAD_DATA_ROOT to the plugin data directory to warm the cache the demo will use; without it
each run starts cold in a temporary directory. Run with -s to see the timings.
"""

import importlib
import json
import os
import time
from pathlib import Path

import pytest

from conftest import load_plugin

pytestmark = pytest.mark.skipif(os.environ.get("OVERHEAD_LIVE") != "1",
                                reason="live network test: set OVERHEAD_LIVE=1")

# The exact tool arguments of each demo scene (see demo/places.md for why these were chosen).
ALTADENA = {"place": "Altadena, California", "lat": 34.2, "lon": -118.11, "radius_km": 6,
            "before": "2024-12-15", "after": "2025-02-15", "focus": "auto"}
NOVO_PROGRESSO = {"place": "Novo Progresso, Brazil", "lat": -7.05, "lon": -55.40, "radius_km": 10,
                  "before": "2019-08-01", "after": "2025-08-01", "focus": "vegetation"}
MANAUS = {"place": "Manaus, Brazil", "lat": -3.1, "lon": -60.2, "radius_km": 15,
          "start": "2024-06", "end": "2024-12", "step": "month", "metric": "water"}


@pytest.fixture(scope="module")
def handlers(tmp_path_factory):
    root = os.environ.get("OVERHEAD_DATA_ROOT")
    data_root = Path(root) if root else tmp_path_factory.mktemp("overhead-live")
    load_plugin()
    module = importlib.import_module("overhead_plugin.handlers")
    return module.make_handlers(data_root)


def call(handlers, tool, args):
    """Run one tool; return (parsed result, seconds). Fails with the tool's own error."""
    started = time.perf_counter()
    raw = handlers[tool](dict(args))
    seconds = time.perf_counter() - started
    text = raw["text_summary"] if isinstance(raw, dict) else raw
    result = json.loads(text)
    assert "error" not in result, result["error"]
    print(f"\n[live] {tool} {args.get('place')}: {seconds:.1f} s - {result['headline']}")
    return result, seconds


def test_live_altadena_burn(handlers):
    result, _ = call(handlers, "overhead_compare", ALTADENA)
    assert result["focus"] == "burn"
    burn = result["stats"]["burn"]
    assert burn["moderate_ha"] + burn["high_ha"] > 500
    assert Path(result["files"]["panel"]).is_file()


def test_live_novo_progresso_vegetation(handlers):
    result, _ = call(handlers, "overhead_compare", NOVO_PROGRESSO)
    assert result["focus"] == "vegetation"
    assert result["stats"]["vegetation"]["lost_ha"] > 100
    assert Path(result["files"]["panel"]).is_file()


def test_live_rio_negro_water(handlers):
    result, _ = call(handlers, "overhead_timelapse", MANAUS)
    values = [(p["value"], p["period"]) for p in result["series"] if p["value"] is not None]
    assert len(values) >= 4
    assert min(values)[1] in ("2024-09", "2024-10", "2024-11")
    assert Path(result["files"]["gif"]).is_file()
    assert Path(result["files"]["chart"]).is_file()
