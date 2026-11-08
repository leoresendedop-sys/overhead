# Demo scenes

Three scenes for the 90-second video, proven on live data by `tests/test_live.py`
(`OVERHEAD_LIVE=1`). The cache is keyed by scene, grid and band, so the demo is fast only if the
model calls the tools with exactly the arguments below. The prompts give the coordinates, radius and
title explicitly so a model reproduces them.

Warm the cache before recording, with the data directory Hermes uses:

    OVERHEAD_DATA_ROOT=<hermes plugin data dir>/overhead OVERHEAD_LIVE=1 python -m pytest tests/test_live.py -s

Timings below are from one Apple-silicon Mac on home broadband. The live tests print the same
numbers with `-s`.

## 1. Eaton fire, Altadena, California (burn)

**Prompt**

> Use the Overhead tool to compare the area around lat 34.20, lon -118.11 (radius 6 km, call it
> "Altadena, California") between 2024-12-15 and 2025-02-15. What burned in the January fire?

**Expected call:** `overhead_compare`
`{"place": "Altadena, California", "lat": 34.2, "lon": -118.11, "radius_km": 6, "before": "2024-12-15", "after": "2025-02-15", "focus": "auto"}`

**Dates picked:** 18 Dec 2024 and 21 Feb 2025, both 100% clear, confidence high, 10 m.
**Headline:** about 3,449 ha burned (910 ha high severity). The panel title is the place label.
**Files:** `panel.png`, `before.jpg`, `after.jpg`, `overlay.png`.
**Timing:** cold 42 s (an earlier cold run took 99 s), warm 0.3 s.

Why a point and not the place name: `place: "Altadena, California"` alone resolves to the census
polygon of the town (21 km2), which gives about 499 ha burned and misses the burned chaparral
above it, and the brief's threshold (moderate + high above 500 ha) is borderline on it. A 5 km
square centred on 34.19, -118.13 gave 2,372 ha but cut off the east and north of the burn scar.
The 6 km square centred on 34.20, -118.11 holds almost the whole Eaton fire scar, with the town
at the bottom of the frame, which tells the story best.

## 2. Deforestation near Novo Progresso, Pará, Brazil (vegetation)

**Prompt**

> Use the Overhead tool on the area around lat -7.05, lon -55.40 (radius 10 km, call it "Novo
> Progresso, Brazil"). Compare 2019-08-01 with 2025-08-01, focusing on vegetation. How much forest
> was lost?

**Expected call:** `overhead_compare`
`{"place": "Novo Progresso, Brazil", "lat": -7.05, "lon": -55.4, "radius_km": 10, "before": "2019-08-01", "after": "2025-08-01", "focus": "vegetation"}`

**Dates picked:** 31 Jul 2019 (98% clear) and 29 Jul 2025 (100% clear), confidence high, 10 m.
**Headline:** about 3,718 ha of vegetation lost and 243 ha gained.
**Files:** `panel.png`, `before.jpg`, `after.jpg`, `overlay.png`.
**Timing:** cold 67 s (another run 56 s), warm 0.6 s.

Why a point and a label without the accent: the place name resolves to the whole municipality
(about 3.8 million ha, 175,000 ha "burned" at 80 m), which is useless as a picture. The point
gives the town and the cleared pasture around it. The label is written "Brazil", not "Pará",
because the panel font has no glyph for "á" (it draws an empty box in the title).

## 3. Rio Negro drought at Manaus, Brazil (water timelapse)

**Prompt**

> Use the Overhead tool to make a monthly water timelapse of the area around lat -3.10, lon -60.20
> (radius 15 km, call it "Manaus, Brazil") from 2024-06 to 2024-12. How low did the Rio Negro get?

**Expected call:** `overhead_timelapse`
`{"place": "Manaus, Brazil", "lat": -3.1, "lon": -60.2, "radius_km": 15, "start": "2024-06", "end": "2024-12", "step": "month", "metric": "water"}`

**Frames used:** 8 Jul, 7 Aug, 1 Sep, 11 Oct and 30 Nov 2024 (June and December have no clear image
and are left out). Resolution 20 m.
**Headline:** water area ranged from 7,493 ha (Oct 2024) to 22,165 ha (Jul 2024) over 7 months.
**Files:** `timelapse.gif`, `chart.png`, `frames/000.jpg` ... `frames/004.jpg`.
**Timing:** cold 55 s, warm 1.3 s.

The first try (lat -3.06, lon -60.1) also passed, with a minimum in Oct 2024 (10,720 ha) against
21,036 ha in Aug, but its frames were partly cloudy (June 63% clear) and its east edge hit the
black nodata wedge of an orbit swath. Centring on -3.10, -60.20 gives clear frames (90 to 100%
clear from July to October) and the whole river reach.
