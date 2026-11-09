# Demo scenes

Three scenes for the 90-second video, proven on live data by `tests/test_live.py`
(`OVERHEAD_LIVE=1`). The cache is keyed by scene, grid and band, so the demo is fast only if the
model calls the tools with exactly the arguments below. The prompts give the coordinates, radius and
title explicitly so a model reproduces them.

Warm the cache before recording, with the data directory Hermes uses:

    OVERHEAD_DATA_ROOT=<hermes plugin data dir>/overhead OVERHEAD_LIVE=1 python -m pytest tests/test_live.py -s

Cold times are network-bound, not code-bound (spec targets: 30 s for a compare, 90 s for a timelapse; the cold compare times below miss the first). The cache MUST be warm before recording the video: run the live tests with `OVERHEAD_DATA_ROOT` first.

Timings below are from one Apple-silicon Mac on home broadband. The live tests print the same
numbers with `-s`.

Re-run on 8 Nov 2026, after the STAC search switched from the area's polygon to its bounding box
and range months started covering the whole month: the same dates and headlines for all three
scenes, warm 0.3 s, 0.6 s and 1.3 s. The cold times below are from the first measurement.

Re-run again on 8 Nov 2026 after the final fixes (compare windows split at the midpoint, one
STAC search per timelapse period, resolution from the UTM grid, timelapse GIF at most 800 px):
the same dates, resolutions and headlines for all three scenes. Cold, in a fresh data folder:
114 s, 50 s and 38 s. Warm: 0.3 s, 0.6 s and 0.9 s. Cold times swing with the network: the same
Altadena compare has taken 42 s, 99 s and 114 s.

## 1. Eaton fire, Altadena, California (burn)

**Prompt**

> Use the Overhead tool to compare the area around lat 34.20, lon -118.11 (radius 6 km, call it
> "Altadena, California") between 2024-12-15 and 2025-02-15. What burned in the January fire?

**Expected call:** `overhead_compare`
`{"place": "Altadena, California", "lat": 34.2, "lon": -118.11, "radius_km": 6, "before": "2024-12-15", "after": "2025-02-15", "focus": "auto"}`

**Dates picked:** 18 Dec 2024 and 21 Feb 2025, both 100% clear, confidence high, 10 m.
**Headline:** about 3,449 ha burned (910 ha high severity). The panel title is the place label.
**Files:** `panel.png`, `before.jpg`, `after.jpg`, `overlay.png`.
**Timing:** cold 42 s (other cold runs took 99 s and 114 s), warm 0.3 s.

Why a point and not the place name: `place: "Altadena, California"` alone resolves to the census
polygon of the town (21 km2), which gives about 499 ha burned and misses the burned chaparral
above it, and the brief's threshold (moderate + high above 500 ha) is borderline on it. A 5 km
square centred on 34.19, -118.13 gave 2,372 ha but cut off the east and north of the burn scar.
The 6 km square centred on 34.20, -118.11 holds almost the whole Eaton fire scar, with the town
at the bottom of the frame, which tells the story best.

## 2. Deforestation near Novo Progresso, Pará, Brazil (vegetation)

**Prompt**

> Use the Overhead tool on the area around lat -7.05, lon -55.40 (radius 10 km, call it "Novo
> Progresso, Pará"). Compare 2019-08-01 with 2025-08-01, focusing on vegetation. How much forest
> was lost?

**Expected call:** `overhead_compare`
`{"place": "Novo Progresso, Pará", "lat": -7.05, "lon": -55.4, "radius_km": 10, "before": "2019-08-01", "after": "2025-08-01", "focus": "vegetation"}`

**Dates picked:** 31 Jul 2019 (98% clear) and 29 Jul 2025 (100% clear), confidence high, 10 m.
**Headline:** about 3,718 ha of vegetation lost and 243 ha gained.
**Files:** `panel.png`, `before.jpg`, `after.jpg`, `overlay.png`.
**Timing:** cold 67 s (other runs 56 s and 50 s), warm 0.6 s.

Why a point: the place name resolves to the whole municipality
(about 3.8 million ha), whose projected grid exceeds the size limit even at 80 m,
so it returns `area_too_large`. The point
gives the town and the cleared pasture around it. The label keeps its accent: panels now use a Unicode font (and fold to ASCII when none is installed).

## 3. Rio Negro drought at Manaus, Brazil (water timelapse)

**Prompt**

> Use the Overhead tool to make a monthly water timelapse of the area around lat -3.10, lon -60.20
> (radius 15 km, call it "Manaus, Brazil") from 2024-06 to 2024-12. How low did the Rio Negro get?

**Expected call:** `overhead_timelapse`
`{"place": "Manaus, Brazil", "lat": -3.1, "lon": -60.2, "radius_km": 15, "start": "2024-06", "end": "2024-12", "step": "month", "metric": "water"}`

**Frames used:** 8 Jul, 7 Aug, 1 Sep, 11 Oct and 30 Nov 2024. Resolution 20 m. `start` and `end`
months cover the whole month, but the best June date is 57% clear (13 Jun) and the best December
date 55% (5 Dec), under the timelapse's 60% bar, so those two periods are left out.
**Headline:** water area ranged from 14,342 ha (Nov 2024) to 22,165 ha (Jul 2024) over 7 months. The October point (7,493 ha) is sun-glint affected, so the headline skips it; the chart draws it as a hollow marker and the notes say it is likely underestimated.
**Files:** `timelapse.gif` (at most 800 px a side), `chart.png`, `frames/000.jpg` ... `frames/004.jpg`.
**Timing:** cold 55 s (another run 38 s), warm 0.9 s (1.3 s before the GIF was capped at 800 px).

The first try (lat -3.06, lon -60.1) also passed, with a minimum in Oct 2024 (10,720 ha) against
21,036 ha in Aug, but its frames were partly cloudy (June 63% clear) and its east edge hit the
black nodata wedge of an orbit swath. Centring on -3.10, -60.20 gives clear frames (90 to 100%
clear from July to October) and the whole river reach.
