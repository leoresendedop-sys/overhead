import json
from datetime import date, timedelta

import numpy as np
import pytest
from PIL import Image

from engine.aoi import aoi_from_point
from engine.errors import BadInput, NoClearScene
from engine.pipeline import parse_date, run_compare, run_scenes, run_timelapse
from engine.runs import RunStore
from engine.stac import Scene

TODAY = date.today()
AOI = aoi_from_point(34.19, -118.13, 1, "Testville")  # ~4 km2, 10 m grid


def scene(d: str, cloud: float = 5.0, tile: str = "A") -> Scene:
    return Scene(id=f"S2_{d}_{tile}", datetime=f"{d}T18:00:00Z", date=d, cloud_cover=cloud, epsg=32611,
                 bbox=(0, 0, 1, 1), assets={}, self_url=f"https://stac.test/items/S2_{d}_{tile}")


def make_search(scenes):
    def search(aoi, start, end, cache_dir, **kw):
        return [s for s in scenes if start.isoformat() <= s.date <= end.isoformat()]
    return search


def make_reader(clear: dict, burn_from: str = "2025-01-01"):
    """clear: date -> clear fraction. Dates >= burn_from carry a burn patch (20 % of the area)."""
    def reader(scenes, grid, bands, cache_dir):
        d = scenes[0].date
        h, w = grid.height, grid.width
        f = clear.get(d, 1.0)
        scl = np.full((h, w), 4, np.uint8)
        cloud_w = int(round(w * (1 - f)))
        start = (int(d[-2:]) % 3) * int(w * 0.3)
        cols = [(start + i) % w for i in range(cloud_w)]
        scl[:, cols] = 9
        arr = {"blue": 500, "green": 2000, "red": 500, "nir": 4000, "swir16": 3000, "swir22": 1500}
        out = {b: np.full((h, w), arr[b], np.uint16) for b in arr}
        if d >= burn_from:
            r = out["nir"][: int(h * 0.4), int(w * 0.5):]
            out["nir"][: int(h * 0.4), int(w * 0.5):] = 3500
            out["swir22"][: int(h * 0.4), int(w * 0.5):] = 4500
        out["scl"] = scl
        return {b: out[b] for b in bands}
    return reader


def test_parse_date_variants():
    assert parse_date("2024-10", field="before") == date(2024, 10, 15)
    assert parse_date("2024-10-03", field="before") == date(2024, 10, 3)
    future = (TODAY + timedelta(days=3)).isoformat()
    for bad in ("2019", "tomorrow", future, "2016-01-01", "2024-13"):
        with pytest.raises(BadInput) as e:
            parse_date(bad, field="after")
        assert "after" in e.value.message


def test_compare_writes_layers_and_result(tmp_cache, tmp_path):
    scenes = [scene("2024-12-17"), scene("2025-02-14")]
    store = RunStore(tmp_path / "runs")
    r = run_compare(AOI, date(2024, 12, 15), date(2025, 2, 15), "auto", 30, tmp_cache, store,
                    search=make_search(scenes), reader=make_reader({}))
    assert r["focus"] == "burn"
    assert r["before"]["date"] == "2024-12-17" and r["before"]["composite"] is False
    assert r["resolution_m"] == 10 and r["confidence"] == "high"
    assert r["headline"].startswith("≈ ") and "between 17 Dec 2024 and 14 Feb 2025" in r["headline"]
    assert "masks" not in r["stats"] and "masks" not in r
    for k in ("panel", "before", "after", "overlay"):
        assert r["files"][k].startswith("/") and Path_exists(r["files"][k])
    ov = Image.open(r["files"]["overlay"])
    assert ov.mode == "RGBA" and ov.getpixel((0, 0))[3] == 0
    on_disk = json.loads((store.dir(r["run_id"]) / "result.json").read_text())
    assert on_disk == r
    assert r["provenance"]["items"] == [s.self_url for s in scenes]
    assert store.list()[0]["run_id"] == r["run_id"]


def Path_exists(p):
    from pathlib import Path
    return Path(p).exists()


def test_compare_uses_composite_when_cloudy(tmp_cache, tmp_path):
    scenes = [scene(f"2024-12-{d}") for d in (10, 14, 18)] + [scene("2025-02-14")]
    clear = {"2024-12-10": 0.6, "2024-12-14": 0.6, "2024-12-18": 0.6}
    r = run_compare(AOI, date(2024, 12, 15), date(2025, 2, 15), "burn", 30, tmp_cache,
                    RunStore(tmp_path / "runs"), search=make_search(scenes), reader=make_reader(clear))
    assert r["before"]["composite"] is True and len(r["before"]["scenes"]) == 3
    assert r["after"]["composite"] is False
    assert any("median of 3 scenes" in n for n in r["notes"])


def test_compare_no_clear_scene_suggests_dates(tmp_cache, tmp_path):
    scenes = [scene("2024-12-14"), scene("2025-02-14")]
    clear = {"2024-12-14": 0.2}
    with pytest.raises(NoClearScene) as e:
        run_compare(AOI, date(2024, 12, 15), date(2025, 2, 15), "auto", 30, tmp_cache,
                    RunStore(tmp_path / "runs"), search=make_search(scenes), reader=make_reader(clear))
    assert e.value.extra["suggestions"]
    assert "before" in e.value.message


def test_compare_rejects_inverted_range(tmp_cache, tmp_path):
    with pytest.raises(BadInput):
        run_compare(AOI, date(2025, 2, 15), date(2024, 12, 15), "auto", 30, tmp_cache,
                    RunStore(tmp_path), search=make_search([]), reader=make_reader({}))


def test_scenes_ranked_by_clear_fraction(tmp_cache):
    scenes = [scene("2024-12-10", 1), scene("2024-12-20", 2, "A"), scene("2024-12-20", 2, "B")]
    r = run_scenes(AOI, date(2024, 12, 1), date(2024, 12, 31), tmp_cache,
                   search=make_search(scenes), reader=make_reader({"2024-12-10": 0.4, "2024-12-20": 0.9}))
    assert [d["date"] for d in r["dates"]] == ["2024-12-20", "2024-12-10"]
    top = r["dates"][0]
    assert top["tiles"] == 2 and abs(top["aoi_clear_fraction"] - 0.9) <= 0.02


def test_timelapse_gaps_and_gif(tmp_cache, tmp_path):
    scenes = [scene("2024-09-14"), scene("2024-10-12"), scene("2024-11-09"), scene("2024-12-05")]
    r = run_timelapse(AOI, date(2024, 9, 1), date(2024, 12, 31), "month", "ndvi", tmp_cache,
                      RunStore(tmp_path / "runs"), search=make_search(scenes),
                      reader=make_reader({"2024-11-09": 0.2}))
    assert len(r["series"]) == 4
    assert sum(s["value"] is None for s in r["series"]) == 1
    assert [s["period"] for s in r["series"]][0] == "2024-09"
    assert len(r["files"]["frames"]) == 3
    gif = Image.open(r["files"]["gif"])
    assert gif.n_frames == 3
    assert "panel" not in r["files"] and Path_exists(r["files"]["chart"])
    assert r["headline"].startswith("Mean NDVI")


def test_timelapse_too_many_frames(tmp_cache, tmp_path):
    with pytest.raises(BadInput):
        run_timelapse(AOI, date(2020, 1, 1), date(2024, 12, 31), "month", "ndvi", tmp_cache,
                      RunStore(tmp_path), search=make_search([]), reader=make_reader({}))


def test_run_store_rejects_traversal(tmp_path):
    store = RunStore(tmp_path)
    for bad in ("../../etc", "compare-2026-abc", "other-20261108-a1b2c3"):
        with pytest.raises(BadInput):
            store.dir(bad)
    rid, path = store.new("compare")
    assert store.dir(rid) == path and store.list() == []  # no result.json yet
