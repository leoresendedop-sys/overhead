---
name: earth-watch
description: Answer "what changed at this place between these dates?" with Overhead's Sentinel-2 tools (vegetation, burn and water change in hectares, timelapses). Use for wildfires, deforestation, floods, droughts, reservoirs, construction or regrowth anywhere on Earth.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [earth-observation, satellite, maps, sentinel-2]
---

# Earth watch

Overhead measures change on the ground from free Sentinel-2 L2A imagery (10 m pixels, a new
image every few days since 2017). Every answer is backed by a tool result. Never guess.

## Pick the tool

- **overhead_compare**: the default. "What changed", "how much burned", "was forest cleared",
  "did the lake shrink". Give `before` and `after`. Leave `focus` on `auto` unless the user
  names vegetation, burn or water.
- **overhead_timelapse**: "how did it change over the years", seasons, lake levels, regrowth.
  At most 24 frames, so use `step: quarter` or `year` for long ranges. `metric`: `ndvi`
  (vegetation), `water` (ha) or `burn` (ha burned since the first frame).
- **overhead_scenes**: the dates are vague ("last summer", "after the storm"), or a compare
  failed with `no_clear_scene`. Pick dates with `aoi_clear_fraction` of 0.8 or more.
- **overhead_locate**: optional. Check an ambiguous or huge place before a slow run.

## Say where and when

- A place name works: `place: "Altadena, California"`. For a site without a good name, use
  `lat`, `lon` and `radius_km` (default 5), and keep `place` as the title.
- Dates are `YYYY-MM-DD` or `YYYY-MM`. Turn "January 2025" into `2025-01`. For an event, put
  `before` a few weeks ahead of it and `after` a few weeks past it.

## Report the result

1. Lead with the `headline`. Report `headline` and the stats exactly as given. Never add,
   subtract or recompute figures: the totals are ready-made (`burn.burned_ha`,
   `vegetation.net_ha`, `water.net_ha`). `burn.low_ha` is low-severity change (often seasonal or
   urban noise) and is NOT counted as burned.
2. Always state the `resolution_m` and pass on every item in `notes`. For a compare, also state
   the `confidence`.
3. For a compare, say which image dates were used (`before.date`, `after.date`); they can differ
   from the requested dates. Mention a composite when `composite` is true. For a timelapse,
   name the periods with no clear image (`date` is null).
4. End the reply with the line the `instruction` gives (`MEDIA:/path/...`), exactly as written,
   on a line of its own: not inside code, quotes or a sentence. It is how the panel or GIF
   reaches the user.
5. If you can see the attached images, describe what is visible in a sentence or two.

## Limits to explain when relevant

- 10 m pixels: no cars, cattle or single trees; a house is a few pixels.
- Clouds: wet seasons (the Amazon from December to May, monsoon Asia) can leave no clear image
  for weeks. Then use overhead_scenes or widen `window_days`.
- NDVI is seasonal: summer vs winter looks like vegetation loss. Across years, compare the same
  season (for example July 2019 vs July 2024).
- Burn severity uses dNBR. A harvest, land clearing or snowmelt between the two dates can look
  like a burn, so check the panel before calling it a fire.
- Sun glint or smoke can hide water; the notes say when.

## Errors

Every error has a `type` and a `message` that says what to do:
- `bad_input`: fix the argument (often a date or a reversed range).
- `place_not_found`: try a more specific name, or lat/lon.
- `ambiguous_place`: show the candidates and ask, or retry with one candidate's lat/lon.
- `area_too_large`: use a smaller area.
- `no_clear_scene`: try the suggested dates, or overhead_scenes.
- `network`: retry once, then tell the user.
- `internal`: tell the user what failed; do not invent a result.
