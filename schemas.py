"""Tool schemas: what the model reads to decide when and how to call each Overhead tool."""

from __future__ import annotations

DATES = "Dates are YYYY-MM-DD or YYYY-MM (a month means its 15th), from 2017-03-28 to today."
NUMBERS = "The numbers in the result are the only numbers to report; never estimate your own."
STATS = ("Report `headline` and the stats exactly as given. Never add, subtract or recompute "
         "figures. `burn.low_ha` is low-severity change (often seasonal or urban noise) and is NOT "
         "counted as burned.")
DELIVER = ("The result's instruction gives a line MEDIA:<path>: end your reply with exactly that "
           "line, on a line of its own (not in code or quotes), so the image reaches the user.")

PLACE = {
    "place": {
        "type": "string",
        "description": "Place name to look up, e.g. 'Altadena, California'. With lat/lon it is only "
                       "the title.",
    },
    "lat": {"type": "number", "minimum": -90, "maximum": 90,
            "description": "Latitude of the centre of a square area (use with lon)."},
    "lon": {"type": "number", "minimum": -180, "maximum": 180,
            "description": "Longitude of the centre of a square area (use with lat)."},
    "radius_km": {
        "type": "number",
        "default": 5,
        "exclusiveMinimum": 0,
        "maximum": 100,
        "description": "Half the side of the square around lat/lon, in km (greater than 0, at most 100).",
    },
    "geojson": {
        "description": "A GeoJSON Polygon, MultiPolygon, Feature or FeatureCollection (object or JSON "
                       "text). Takes precedence over place and lat/lon.",
        "anyOf": [{"type": "object", "additionalProperties": True}, {"type": "string"}],
    },
}
WHERE = "Say where with place, or lat and lon, or geojson."


def _schema(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {**PLACE, **props},
            "required": required,
        },
    }


def _date(what: str) -> dict:
    return {"type": "string", "description": f"{what} (YYYY-MM-DD or YYYY-MM)."}


LOCATE = _schema(
    "overhead_locate",
    "Resolve a place to the exact area Overhead will analyse: name, bbox, area in km2 and the "
    "analysis resolution. Optional: the other overhead tools accept the same place fields. Use it "
    "to check an ambiguous or very large place before a slow analysis. " + WHERE + " " + NUMBERS,
    {},
    [],
)

SCENES = _schema(
    "overhead_scenes",
    "List the Sentinel-2 dates in a range, ranked by how much of the requested area is cloud-free "
    "(aoi_clear_fraction, 0 to 1). Use it when the user's dates are vague ('last summer', 'after "
    "the storm') or when a compare fails with no_clear_scene, then pass the best dates to "
    "overhead_compare. " + WHERE + " " + DATES + " " + NUMBERS,
    {
        "start": _date("Start of the range"),
        "end": _date("End of the range"),
        "max_results": {"type": "integer", "default": 12, "minimum": 1, "maximum": 30,
                        "description": "How many dates to return."},
    },
    ["start", "end"],
)

COMPARE = _schema(
    "overhead_compare",
    "The main tool: what changed at a place between two dates. Picks the clearest Sentinel-2 image "
    "near each date and returns hectares of vegetation lost/gained, burned area by severity and "
    "water gained/lost, plus a before | after | change panel image. Use it for 'what changed', "
    "'how much burned', 'was forest cleared'. For a trend over many dates use overhead_timelapse. "
    + WHERE + " " + DATES + " " + NUMBERS + " " + STATS
    + " Always state the confidence and the resolution. " + DELIVER,
    {
        "before": _date("Date before the change"),
        "after": _date("Date after the change"),
        "focus": {"type": "string", "enum": ["auto", "vegetation", "burn", "water"], "default": "auto",
                  "description": "Which change to headline and map. auto picks the largest."},
        "window_days": {"type": "integer", "default": 30, "minimum": 1, "maximum": 120,
                        "description": "Search this many days either side of each date for a clear "
                                       "image."},
    },
    ["before", "after"],
)

TIMELAPSE = _schema(
    "overhead_timelapse",
    "How a place evolved over time: one natural-colour frame per month, quarter or year (animated "
    "GIF), and a series of mean NDVI, water area (ha) or burned area (ha) per frame. Periods with "
    "no clear image are left out. At most 24 frames, so use quarter or year for long ranges. Use "
    "it for 'how did it change over the years', seasons, lake levels, regrowth. For one before/"
    "after figure use overhead_compare. " + WHERE + " " + DATES + " " + NUMBERS + " " + DELIVER,
    {
        "start": _date("First period"),
        "end": _date("Last period"),
        "step": {"type": "string", "enum": ["month", "quarter", "year"], "default": "month",
                 "description": "One frame per month, quarter or year."},
        "metric": {"type": "string", "enum": ["ndvi", "water", "burn"], "default": "ndvi",
                   "description": "ndvi: mean vegetation index; water: water area in ha; burn: area "
                                  "burned since the first frame, in ha."},
    },
    ["start", "end"],
)

ALL = (LOCATE, SCENES, COMPARE, TIMELAPSE)
