import json
from datetime import date, timedelta

import numpy as np
import pytest
from PIL import Image

from engine.aoi import aoi_from_point
from engine.errors import BadInput, NoClearScene
from engine.report import compare_headline, compare_notes
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


def _stats(**kw):
    base = {"vegetation": {"lost_ha": 0.0, "gained_ha": 0.0},
            "burn": {"low_ha": 0.0, "moderate_ha": 0.0, "high_ha": 0.0},
            "water": {"before_ha": 0.0, "after_ha": 0.0, "lost_ha": 0.0, "gained_ha": 0.0}}
    base.update(kw)
    return base


D1, D2 = date(2024, 12, 17), date(2025, 2, 14)
SPAN = "between 17 Dec 2024 and 14 Feb 2025"


@pytest.mark.parametrize("stats,focus,expected", [
    (_stats(burn={"low_ha": 900.0, "moderate_ha": 2250.0, "high_ha": 1870.0}), "burn",
     f"≈ 4,120 ha burned (1,870 ha high severity) {SPAN}"),
    (_stats(burn={"low_ha": 9.0, "moderate_ha": 5.0, "high_ha": 0.0}), "burn", f"≈ 5.0 ha burned {SPAN}"),
    (_stats(vegetation={"lost_ha": 310.4, "gained_ha": 12.0}), "vegetation",
     f"≈ 310 ha of vegetation lost and 12 ha gained {SPAN}"),
    (_stats(vegetation={"lost_ha": 3.04, "gained_ha": 0.0}), "vegetation",
     f"≈ 3.0 ha of vegetation lost and 0.0 ha gained {SPAN}"),
    (_stats(water={"before_ha": 5210.0, "after_ha": 3980.0, "lost_ha": 1300.0, "gained_ha": 70.0}), "water",
     f"Water area went from 5,210 ha to 3,980 ha (−1,230 ha) {SPAN}"),
    (_stats(water={"before_ha": 4.0, "after_ha": 15.0, "lost_ha": 0.0, "gained_ha": 11.0}), "water",
     f"Water area went from 4.0 ha to 15 ha (+11 ha) {SPAN}"),
    (_stats(water={"before_ha": 5.0, "after_ha": 5.04, "lost_ha": 0.5, "gained_ha": 0.54}), "water",
     f"Water area went from 5.0 ha to 5.0 ha (no net change) {SPAN}"),
    (_stats(), "burn", f"No significant burn change detected {SPAN}"),
    (_stats(burn={"low_ha": 50.0, "moderate_ha": 0.0, "high_ha": 0.0}), "burn",
     f"No significant burn change detected {SPAN}"),
    (_stats(), "vegetation", f"No significant vegetation change detected {SPAN}"),
    (_stats(), "water", f"No significant water change detected {SPAN}"),
])
def test_compare_headlines(stats, focus, expected):
    assert compare_headline(stats, focus, D1, D2) == expected


def test_compare_notes_large_area_and_low_confidence():
    ok = {"confidence": "high", "valid_fraction": 0.95}
    assert compare_notes(10, ok, []) == ["Resolution 10 m: objects smaller than ~20 m are not visible."]
    notes = compare_notes(40, {"confidence": "low", "valid_fraction": 0.62}, ["Before image is a median of 3 scenes (cloudy period)."])
    assert notes[0].startswith("Resolution 40 m: objects smaller than ~80 m")
    assert "Large area: analysed at 40 m; small changes may be missed." in notes
    assert any("62%" in n for n in notes)
    assert notes[-1].startswith("Before image is a median")


class Counting:
    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def __call__(self, scenes, grid, bands, cache_dir):
        self.calls.append((scenes[0].date, tuple(bands)))
        return self.inner(scenes, grid, bands, cache_dir)

    def full(self):
        return [c for c in self.calls if len(c[1]) > 1]

    def scl(self):
        return [c for c in self.calls if c[1] == ("scl",)]


def test_compare_after_side_fails_before_any_full_read(tmp_cache, tmp_path):
    scenes = [scene("2024-12-17"), scene("2025-02-14")]
    rd = Counting(make_reader({"2025-02-14": 0.1}))
    with pytest.raises(NoClearScene) as e:
        run_compare(AOI, date(2024, 12, 15), date(2025, 2, 15), "auto", 30, tmp_cache,
                    RunStore(tmp_path / "runs"), search=make_search(scenes), reader=rd)
    assert e.value.extra["side"] == "after" and "(after)" in e.value.message
    assert rd.full() == []


def test_compare_empty_search_suggests_nothing(tmp_cache, tmp_path):
    with pytest.raises(NoClearScene) as e:
        run_compare(AOI, date(2024, 12, 15), date(2025, 2, 15), "auto", 30, tmp_cache,
                    RunStore(tmp_path / "runs"), search=make_search([]), reader=make_reader({}))
    assert e.value.extra["suggestions"] == [] and "No usable images" in e.value.message


def test_suggestions_are_ordered_and_prefer_clear_dates(tmp_cache, tmp_path):
    scenes = [scene("2025-02-14", 5), scene("2025-01-20", 8), scene("2025-03-30", 9), scene("2025-04-20", 10),
              scene("2025-05-05", 12), scene("2024-06-17")]
    clear = {"2025-02-14": 0.1, "2025-01-20": 0.7, "2025-03-30": 0.9, "2025-04-20": 0.65, "2025-05-05": 0.4}
    with pytest.raises(NoClearScene) as e:
        run_compare(AOI, date(2024, 6, 15), date(2025, 2, 15), "auto", 15, tmp_cache,
                    RunStore(tmp_path / "runs"), search=make_search(scenes), reader=make_reader(clear))
    sug = e.value.extra["suggestions"]
    assert [s["date"] for s in sug] == ["2025-03-30", "2025-01-20", "2025-04-20"]
    assert all(s["clear_fraction"] >= 0.6 for s in sug)
    assert sug[0]["clear_fraction"] >= sug[1]["clear_fraction"] >= sug[2]["clear_fraction"]


def test_suggestions_fall_back_when_none_reach_threshold(tmp_cache, tmp_path):
    scenes = [scene("2025-02-14", 5), scene("2025-01-20", 8)]
    with pytest.raises(NoClearScene) as e:
        run_compare(AOI, date(2024, 6, 15), date(2025, 2, 15), "auto", 15, tmp_cache,
                    RunStore(tmp_path / "runs"), search=make_search(scenes + [scene("2024-06-17")]),
                    reader=make_reader({"2025-02-14": 0.1, "2025-01-20": 0.2}))
    assert [s["date"] for s in e.value.extra["suggestions"]] == ["2025-01-20", "2025-02-14"]


def test_compare_ranking_stops_when_a_scene_is_clear_enough(tmp_cache, tmp_path):
    scenes = [scene(f"2024-12-{d:02d}", d) for d in (10, 12, 14, 16, 18, 20)] + [scene("2025-02-14")]
    rd = Counting(make_reader({}))
    run_compare(AOI, date(2024, 12, 15), date(2025, 2, 15), "burn", 30, tmp_cache,
                RunStore(tmp_path / "runs"), search=make_search(scenes), reader=rd)
    before_scl = [c for c in rd.scl() if c[0] < "2025"]
    assert len(before_scl) <= 2


@pytest.mark.parametrize("bad", [0, 121, 2.5, "30", None, True])
def test_compare_rejects_bad_window(tmp_cache, tmp_path, bad):
    with pytest.raises(BadInput) as e:
        run_compare(AOI, date(2024, 12, 15), date(2025, 2, 15), "auto", bad, tmp_cache,
                    RunStore(tmp_path), search=make_search([]), reader=make_reader({}))
    assert "window_days" in e.value.message


def test_timelapse_no_clear_period_raises(tmp_cache, tmp_path):
    with pytest.raises(NoClearScene):
        run_timelapse(AOI, date(2024, 9, 1), date(2024, 10, 31), "month", "ndvi", tmp_cache,
                      RunStore(tmp_path / "runs"), search=make_search([scene("2024-09-14")]),
                      reader=make_reader({"2024-09-14": 0.1}))


def test_timelapse_burn_is_burned_hectares_vs_first_frame(tmp_cache, tmp_path):
    scenes = [scene("2024-09-14"), scene("2024-10-12"), scene("2024-11-09"), scene("2024-12-05")]
    r = run_timelapse(AOI, date(2024, 9, 1), date(2024, 12, 31), "month", "burn", tmp_cache,
                      RunStore(tmp_path / "runs"), search=make_search(scenes),
                      reader=make_reader({}, burn_from="2024-11-01"))
    vals = [s["value"] for s in r["series"]]
    assert vals[0] == 0.0 and vals[1] == 0.0 and vals[2] > 0 and vals[3] == vals[2]
    assert r["headline"].startswith("Burned area reached")


def test_timelapse_water_series_in_hectares(tmp_cache, tmp_path):
    scenes = [scene("2024-09-14"), scene("2024-10-12")]
    r = run_timelapse(AOI, date(2024, 9, 1), date(2024, 10, 31), "month", "water", tmp_cache,
                      RunStore(tmp_path / "runs"), search=make_search(scenes), reader=make_reader({}))
    assert [s["value"] for s in r["series"]] == [0.0, 0.0]
    assert r["headline"].startswith("Water area ranged from 0.0 ha")
