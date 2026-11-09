import json
from dataclasses import replace
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


def test_parse_date_month_in_a_range_covers_the_whole_month():
    assert parse_date("2024-06", field="start", role="start") == date(2024, 6, 1)
    assert parse_date("2024-06", field="end", role="end") == date(2024, 6, 30)
    assert parse_date("2024-02", field="end", role="end") == date(2024, 2, 29)  # leap year
    assert parse_date("2023-02", field="end", role="end") == date(2023, 2, 28)
    assert parse_date("2024-10", field="before") == date(2024, 10, 15)  # compare keeps the 15th
    assert parse_date("2024-10", field="before", role="point") == date(2024, 10, 15)
    for role in ("point", "start", "end"):  # a full date is taken as given
        assert parse_date("2024-10-03", field="x", role=role) == date(2024, 10, 3)


def test_parse_date_range_month_is_clamped_to_the_data():
    this_month = f"{TODAY:%Y-%m}"
    assert parse_date(this_month, field="end", role="end") == TODAY
    assert parse_date(this_month, field="start", role="start") == TODAY.replace(day=1)
    assert parse_date("2017-03", field="start", role="start") == date(2017, 3, 28)
    next_month = (TODAY.replace(day=28) + timedelta(days=7)).replace(day=1)
    for role in ("start", "end"):
        with pytest.raises(BadInput) as e:
            parse_date(f"{next_month:%Y-%m}", field="end", role=role)
        assert "future" in e.value.message
        with pytest.raises(BadInput) as e:
            parse_date("2017-02", field="start", role=role)
        assert "2017-03-28" in e.value.message
    with pytest.raises(BadInput):
        parse_date((TODAY + timedelta(days=3)).isoformat(), field="end", role="end")


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


@pytest.mark.parametrize("after_scenes,clear,expected", [
    # nearest date cloudier in metadata than two later ones: the nearest clear date still wins
    ([scene("2025-02-14", 20), scene("2025-03-08", 1), scene("2025-03-13", 2)], {}, "2025-02-14"),
    # nearly clear and near beats fully clear and far
    ([scene("2025-02-14", 20), scene("2025-03-08", 1), scene("2025-03-13", 2)],
     {"2025-02-14": 0.96}, "2025-02-14"),
    # metadata cloud above 30 % is tried last, even when it is the nearest date
    ([scene("2025-02-15", 70), scene("2025-02-20", 5), scene("2025-03-08", 1)], {}, "2025-02-20"),
])
def test_compare_picks_the_closest_clear_date(tmp_cache, tmp_path, after_scenes, clear, expected):
    scenes = [scene("2024-12-17")] + after_scenes
    r = run_compare(AOI, date(2024, 12, 15), date(2025, 2, 15), "auto", 30, tmp_cache,
                    RunStore(tmp_path / "runs"), search=make_search(scenes), reader=make_reader(clear))
    assert r["after"]["date"] == expected


def test_place_carries_the_full_name(tmp_cache):
    named = replace(AOI, name="Testville, California",
                    full_name="Testville, Some County, California, United States")
    r = run_scenes(named, date(2024, 12, 1), date(2024, 12, 31), tmp_cache,
                   search=make_search([scene("2024-12-10")]), reader=make_reader({}))
    assert r["place"]["name"] == "Testville, California"
    assert r["place"]["full_name"] == "Testville, Some County, California, United States"
    plain = run_scenes(AOI, date(2024, 12, 1), date(2024, 12, 31), tmp_cache,
                       search=make_search([scene("2024-12-10")]), reader=make_reader({}))
    assert plain["place"]["full_name"] == "Testville"


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
    """Stats with the totals change_stats adds (burned_ha, net_ha)."""
    base = {"vegetation": {"lost_ha": 0.0, "gained_ha": 0.0},
            "burn": {"low_ha": 0.0, "moderate_ha": 0.0, "high_ha": 0.0},
            "water": {"before_ha": 0.0, "after_ha": 0.0, "lost_ha": 0.0, "gained_ha": 0.0}}
    base.update(kw)
    v, b, w = base["vegetation"], base["burn"], base["water"]
    v["net_ha"] = round(v["gained_ha"] - v["lost_ha"], 2)
    b["burned_ha"] = round(b["moderate_ha"] + b["high_ha"], 2)
    w["net_ha"] = round(w["after_ha"] - w["before_ha"], 2)
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


def water_reader(glint_dates=()):
    """Clear pixels are SCL water; swir16 is dark on clean dates and bright on glinted ones."""
    base = make_reader({})

    def reader(scenes, grid, bands, cache_dir):
        d = scenes[0].date
        out = base(scenes, grid, bands, cache_dir)
        if "scl" in out:
            out["scl"] = np.where(out["scl"] == 4, 6, out["scl"]).astype(np.uint8)
        if "swir16" in out:
            out["swir16"] = np.full_like(out["swir16"], 2000 if d in glint_dates else 100)
        return out
    return reader


def test_timelapse_water_prefers_non_glinted_date(tmp_cache, tmp_path):
    scenes = [scene("2024-09-05", 3), scene("2024-09-20", 6)]
    clear = {"2024-09-20": 0.8}
    r = run_timelapse(AOI, date(2024, 9, 1), date(2024, 9, 30), "month", "water", tmp_cache,
                      RunStore(tmp_path / "runs"), search=make_search(scenes),
                      reader=Clearing(water_reader({"2024-09-05"}), clear))
    pt = r["series"][0]
    assert pt["date"] == "2024-09-20" and "flag" not in pt
    assert abs(pt["clear"] - 0.8) <= 0.02
    assert not any("glint" in n for n in r["notes"])


def test_timelapse_water_flags_glint_when_only_option(tmp_cache, tmp_path):
    scenes = [scene("2024-09-05", 3), scene("2024-10-12", 3)]
    r = run_timelapse(AOI, date(2024, 9, 1), date(2024, 10, 31), "month", "water", tmp_cache,
                      RunStore(tmp_path / "runs"), search=make_search(scenes),
                      reader=water_reader({"2024-09-05"}))
    assert r["series"][0]["flag"] == "glint" and "flag" not in r["series"][1]
    assert r["series"][1]["clear"] == 1.0
    note = next(n for n in r["notes"] if "glint" in n)
    assert "2024-09-05" in note and "2024-10-12" not in note and "underestimated" in note


def test_timelapse_ndvi_points_have_clear_and_no_flag(tmp_cache, tmp_path):
    r = run_timelapse(AOI, date(2024, 9, 1), date(2024, 10, 31), "month", "ndvi", tmp_cache,
                      RunStore(tmp_path / "runs"), search=make_search([scene("2024-09-14")]),
                      reader=water_reader({"2024-09-14"}))
    assert r["series"][0]["clear"] == 1.0 and "flag" not in r["series"][0]
    assert r["series"][1]["date"] is None


def test_compare_notes_glint_on_a_side(tmp_cache, tmp_path):
    scenes = [scene("2024-12-17"), scene("2025-02-14")]
    r = run_compare(AOI, date(2024, 12, 15), date(2025, 2, 15), "water", 30, tmp_cache,
                    RunStore(tmp_path / "runs"), search=make_search(scenes),
                    reader=water_reader({"2024-12-17"}))
    notes = [n for n in r["notes"] if "glint" in n]
    assert len(notes) == 1 and "2024-12-17" in notes[0] and "water figures" in notes[0]


class Clearing:
    """Reader wrapper that forces a clear fraction per date by clouding the right-hand columns."""
    def __init__(self, inner, clear):
        self.inner, self.clear = inner, clear

    def __call__(self, scenes, grid, bands, cache_dir):
        out = self.inner(scenes, grid, bands, cache_dir)
        f = self.clear.get(scenes[0].date)
        if f is not None and "scl" in out:
            out["scl"] = out["scl"].copy()
            out["scl"][:, int(round(grid.width * f)):] = 9
        return out


def _series(*vals):
    return [{"period": f"2024-{i + 7:02d}", "date": f"2024-{i + 7:02d}-10", "value": v,
             **({"flag": "glint"} if g else {})} for i, (v, g) in enumerate(vals)]


def test_timelapse_headline_ignores_glint_flagged_points():
    from engine.pipeline import timelapse_headline
    s = _series((20000.0, False), (19000.0, False), (7000.0, True), (12000.0, False))
    h = timelapse_headline(s, "water", "month", 4)
    assert "12,000 ha (Oct 2024)" in h and "7,000" not in h and "glint" not in h


def test_timelapse_headline_uses_all_points_when_too_few_clean():
    from engine.pipeline import timelapse_headline
    s = _series((20000.0, False), (7000.0, True), (9000.0, True))
    h = timelapse_headline(s, "water", "month", 3)
    assert "7,000 ha" in h and h.endswith("(some dates affected by sun glint or haze)")


# ---------------------------------------------------------------- before/after windows (F2)

class Recording:
    """Search wrapper that records each (start, end, keyword arguments) call."""
    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def __call__(self, aoi, start, end, cache_dir, **kw):
        self.calls.append((start, end, kw))
        return self.inner(aoi, start, end, cache_dir, **kw)


def test_compare_close_dates_never_take_an_after_scene_for_before(tmp_cache, tmp_path):
    # Fire on 7 Jan; the only clear scenes are after it. Each side used to search its own
    # +-30 days, so before picked 12 Jan (post-fire) and the burn vanished, at high confidence.
    scenes = [scene("2024-12-27"), scene("2025-01-01"), scene("2025-01-12"), scene("2025-01-17")]
    clear = {"2024-12-27": 0.2, "2025-01-01": 0.25, "2025-01-12": 1.0, "2025-01-17": 1.0}
    search = Recording(make_search(scenes))
    r = run_compare(AOI, date(2025, 1, 1), date(2025, 1, 20), "auto", 30, tmp_cache,
                    RunStore(tmp_path / "runs"), search=search,
                    reader=make_reader(clear, burn_from="2025-01-07"))
    assert r["before"]["date"] < "2025-01-07" < r["after"]["date"]
    assert r["focus"] == "burn" and r["stats"]["burn"]["burned_ha"] > 0
    windows = {(s, e) for s, e, _ in search.calls}
    assert (date(2024, 12, 2), date(2025, 1, 10)) in windows  # before: up to the midpoint
    assert (date(2025, 1, 11), date(2025, 2, 19)) in windows  # after: from the day after it


def test_compare_never_uses_one_scene_for_both_sides(tmp_cache, tmp_path):
    # Wet season, one usable scene (14 Jan): it used to be both before and after, "between
    # 14 Jan 2025 and 14 Jan 2025", confidence high.
    scenes = [scene("2024-12-20"), scene("2025-01-02"), scene("2025-01-14"), scene("2025-01-27")]
    clear = {"2024-12-20": 0.4, "2025-01-02": 0.5, "2025-01-14": 0.9, "2025-01-27": 0.45}
    r = run_compare(AOI, date(2025, 1, 1), date(2025, 1, 25), "auto", 30, tmp_cache,
                    RunStore(tmp_path / "runs"), search=make_search(scenes),
                    reader=make_reader(clear, burn_from="2025-01-10"))
    assert r["before"]["date"] < r["after"]["date"]
    assert not set(r["before"]["scenes"]) & set(r["after"]["scenes"])
    assert "between 14 Jan 2025 and 14 Jan 2025" not in r["headline"]


def test_compare_refuses_scenes_out_of_order(tmp_cache, tmp_path):
    """Final guard: whatever the search returns, before must be earlier than after."""
    everything = [scene("2025-01-14")]
    with pytest.raises(NoClearScene) as e:
        run_compare(AOI, date(2025, 1, 1), date(2025, 1, 25), "auto", 30, tmp_cache,
                    RunStore(tmp_path / "runs"), search=lambda *a, **k: list(everything),
                    reader=make_reader({}))
    assert "not earlier" in e.value.message and "overhead_scenes" in e.value.message


def test_compare_windows_far_apart_are_the_full_window(tmp_cache, tmp_path):
    """The demo dates are two months apart: each side keeps its whole +-window_days."""
    search = Recording(make_search([scene("2024-12-17"), scene("2025-02-14")]))
    run_compare(AOI, date(2024, 12, 15), date(2025, 2, 15), "auto", 30, tmp_cache,
                RunStore(tmp_path / "runs"), search=search, reader=make_reader({}))
    assert [(s, e) for s, e, _ in search.calls] == [
        (date(2024, 11, 15), date(2025, 1, 14)), (date(2025, 1, 16), date(2025, 3, 17))]


# ---------------------------------------------------------------- search truncation (F3)

from engine.stac import Scenes  # noqa: E402


class EarthSearch:
    """Fake STAC search that answers like Earth Search: the items in the range, newest first (or
    least cloudy first with sortby="cloud"), at most max_items, plus how many matched."""
    def __init__(self, scenes):
        self.scenes, self.calls = scenes, []

    def __call__(self, aoi, start, end, cache_dir, *, max_items=200, sortby=None, **kw):
        self.calls.append((start, end, max_items, sortby))
        hits = [s for s in self.scenes if start.isoformat() <= s.date <= end.isoformat()]
        if sortby == "cloud":
            hits.sort(key=lambda s: s.cloud_cover)
        else:
            hits.sort(key=lambda s: s.datetime, reverse=True)
        out = Scenes(sorted(hits[:max_items], key=lambda s: s.datetime))
        out.matched, out.returned = len(hits), min(len(hits), max_items)
        return out


def _every_other_day(first: date, last: date, tiles=("A", "B")):
    d, out = first, []
    while d <= last:
        out += [scene(d.isoformat(), tile=t) for t in tiles]
        d += timedelta(days=2)
    return out


def test_timelapse_early_years_survive_newest_first_truncation(tmp_cache, tmp_path):
    # 8 years of scenes on two tiles is ~2,900 items. One search capped at 1,000 newest-first
    # kept only 2023-2025, and the earlier years came out as "no clear image".
    search = EarthSearch(_every_other_day(date(2018, 1, 1), date(2025, 12, 31)))
    r = run_timelapse(AOI, date(2018, 1, 1), date(2025, 12, 31), "year", "ndvi", tmp_cache,
                      RunStore(tmp_path / "runs"), search=search, reader=make_reader({}))
    assert [s["period"] for s in r["series"]] == [str(y) for y in range(2018, 2026)]
    assert all(s["value"] is not None for s in r["series"])
    assert len(search.calls) == 24 and all(c[2] == 200 for c in search.calls)
    assert not any("incomplete" in n for n in r["notes"])


def test_timelapse_searches_each_period_within_the_range(tmp_cache, tmp_path):
    search = EarthSearch([scene("2024-06-14"), scene("2024-07-12"), scene("2024-08-09")])
    r = run_timelapse(AOI, date(2024, 6, 10), date(2024, 8, 20), "month", "ndvi", tmp_cache,
                      RunStore(tmp_path / "runs"), search=search, reader=make_reader({}))
    assert sorted((s, e) for s, e, _, _ in search.calls) == [
        (date(2024, 6, 10), date(2024, 6, 30)), (date(2024, 7, 1), date(2024, 7, 31)),
        (date(2024, 8, 1), date(2024, 8, 20))]
    assert not any("matched more" in n for n in r["notes"])


def test_timelapse_quarter_periods_are_searched_whole(tmp_cache, tmp_path):
    search = EarthSearch([scene("2024-02-14"), scene("2024-05-12")])
    run_timelapse(AOI, date(2024, 1, 1), date(2024, 6, 30), "quarter", "ndvi", tmp_cache,
                  RunStore(tmp_path / "runs"), search=search, reader=make_reader({}))
    assert sorted((s, e) for s, e, _, _ in search.calls) == [
        (date(2024, 1, 1), date(2024, 3, 31)), (date(2024, 4, 1), date(2024, 6, 30))]


def test_compare_side_searches_take_up_to_500_items_and_say_when_cut(tmp_cache, tmp_path):
    many = _every_other_day(date(2024, 11, 15), date(2025, 1, 14), tiles="ABCDEFGHIJKLMNOPQ") \
        + [scene("2025-02-14")]
    search = EarthSearch(many)
    r = run_compare(AOI, date(2024, 12, 15), date(2025, 2, 15), "auto", 30, tmp_cache,
                    RunStore(tmp_path / "runs"), search=search, reader=make_reader({}))
    side_calls = [c for c in search.calls if c[3] is None]
    assert len(side_calls) == 2 and all(c[2] == 500 for c in side_calls)
    note = next(n for n in r["notes"] if "matched" in n)
    assert note.startswith("Before:") and "527 scenes" in note and "newest 500" in note


def test_scenes_ranks_the_least_cloudy_and_says_when_cut(tmp_cache):
    search = EarthSearch(_every_other_day(date(2024, 1, 1), date(2024, 12, 31)))
    r = run_scenes(AOI, date(2024, 1, 1), date(2024, 12, 31), tmp_cache, search=search,
                   reader=make_reader({}))
    assert search.calls[0][3] == "cloud"
    assert any("366 scenes" in n and "least cloudy 200" in n for n in r["notes"])
    small = run_scenes(AOI, date(2024, 12, 1), date(2024, 12, 31), tmp_cache,
                       search=make_search([scene("2024-12-10")]), reader=make_reader({}))
    assert small["notes"] == []


def test_suggestions_search_the_least_cloudy_first(tmp_cache, tmp_path):
    search = EarthSearch([scene("2024-12-14"), scene("2025-02-14")])
    with pytest.raises(NoClearScene):
        run_compare(AOI, date(2024, 12, 15), date(2025, 2, 15), "auto", 30, tmp_cache,
                    RunStore(tmp_path / "runs"), search=search, reader=make_reader({"2024-12-14": 0.2}))
    assert [c[3] for c in search.calls if c[3]] == ["cloud"]  # the +-90-day suggestion search


# ---------------------------------------------------------------- size policy (F4)

def test_analysis_grid_of_a_long_thin_area_stays_small():
    from engine.aoi import aoi_from_geojson
    from engine.pipeline import _Area
    from test_aoi import RIVER_STRIP
    area = _Area(aoi_from_geojson(RIVER_STRIP))
    assert area.res == 40 and max(area.grid.width, area.grid.height) <= 2600
    assert area.pixel_ha == 0.16


# ---------------------------------------------------------------- dates (A7)

def test_parse_date_this_month_as_a_point_is_at_most_today():
    assert parse_date(f"{TODAY:%Y-%m}", field="after") == min(TODAY.replace(day=15), TODAY)
    with pytest.raises(BadInput) as e:  # a full date in the future is still refused
        parse_date((TODAY + timedelta(days=1)).isoformat(), field="after")
    assert "future" in e.value.message


def test_scenes_and_timelapse_take_a_single_day(tmp_cache, tmp_path):
    day = date(2024, 9, 14)
    r = run_scenes(AOI, day, day, tmp_cache, search=make_search([scene("2024-09-14")]),
                   reader=make_reader({}))
    assert [d["date"] for d in r["dates"]] == ["2024-09-14"]
    t = run_timelapse(AOI, day, day, "month", "ndvi", tmp_cache, RunStore(tmp_path / "runs"),
                      search=make_search([scene("2024-09-14")]), reader=make_reader({}))
    assert [s["period"] for s in t["series"]] == ["2024-09"] and t["series"][0]["value"] is not None
    with pytest.raises(BadInput):
        run_timelapse(AOI, day, day - timedelta(days=1), "month", "ndvi", tmp_cache,
                      RunStore(tmp_path / "runs"), search=make_search([]), reader=make_reader({}))


def test_timelapse_does_not_search_periods_after_today(tmp_cache, tmp_path):
    first = TODAY.replace(day=1)
    later = (first + timedelta(days=62)).replace(day=1)  # two months ahead
    search = EarthSearch([scene(TODAY.isoformat())])
    r = run_timelapse(AOI, first, later, "month", "ndvi", tmp_cache, RunStore(tmp_path / "runs"),
                      search=search, reader=make_reader({}))
    assert [(s, e) for s, e, _, _ in search.calls] == [(first, TODAY)]  # no inverted ranges
    assert [s["value"] is None for s in r["series"]] == [False, True, True]


def test_yearly_timelapse_can_select_january_and_keep_both_tiles(tmp_cache, tmp_path):
    scenes = _every_other_day(date(2024, 1, 1), date(2024, 12, 31))
    scenes = [replace(s, cloud_cover=0 if s.date == "2024-01-01" else 50) for s in scenes]
    search = EarthSearch(scenes)
    r = run_timelapse(AOI, date(2024, 1, 1), date(2024, 12, 31), "year", "ndvi", tmp_cache,
                      RunStore(tmp_path / "runs"), search=search, reader=make_reader({}))
    assert r["series"][0]["date"] == "2024-01-01"
    assert len(r["provenance"]["items"]) == 2
    assert not any("incomplete" in n for n in r["notes"])


@pytest.mark.parametrize("counts", [True, False])
def test_timelapse_window_recovers_every_item_without_splitting_dates(tmp_cache, counts):
    from engine.pipeline import _search_timelapse_window
    lo, hi = date(2024, 2, 15), date(2024, 3, 10)
    scenes = _every_other_day(lo, hi, tiles=[str(i) for i in range(40)])
    earth = EarthSearch(scenes)
    def search(*args, **kwargs):
        result = earth(*args, **kwargs)
        if not counts:
            result.matched = None
        return result
    result, cut = _search_timelapse_window(AOI, lo, hi, tmp_cache, search)
    assert not cut
    assert sorted(s.id for s in result) == sorted(s.id for s in scenes)
    assert len({s.id for s in result}) == len(scenes)
    assert any(s.date == "2024-02-29" for s in result)
    assert all(lo <= start <= end <= hi for start, end, _, _ in earth.calls)


def test_timelapse_window_does_not_split_exact_known_limit(tmp_cache):
    from engine.pipeline import _search_timelapse_window
    search = EarthSearch([scene("2024-01-01", tile=str(i)) for i in range(200)])
    result, cut = _search_timelapse_window(AOI, date(2024, 1, 1), date(2024, 1, 31), tmp_cache, search)
    assert len(result) == 200 and not cut
    assert len(search.calls) == 1


@pytest.mark.parametrize("counts", [True, False])
def test_timelapse_saturated_single_day_stops_and_warns(tmp_cache, tmp_path, counts):
    earth = EarthSearch([scene("2024-01-01", tile=str(i)) for i in range(201)])
    def search(*args, **kwargs):
        result = earth(*args, **kwargs)
        if not counts:
            result.matched = None
        return result
    r = run_timelapse(AOI, date(2024, 1, 1), date(2024, 1, 1), "year", "ndvi", tmp_cache,
                      RunStore(tmp_path / "runs"), search=search, reader=make_reader({}))
    assert len(earth.calls) == 1
    assert any("1 of 1 periods may be incomplete" in n and "200-scene" in n for n in r["notes"])
