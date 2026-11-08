"""Sentinel-2 L2A scene search on the Earth Search STAC API."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .aoi import AOI, USER_AGENT
from .fsutil import write_atomic
from .http import post_json, retry_transient

STAC_URL = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
BAND_KEYS = ("blue", "green", "red", "nir", "swir16", "swir22", "scl")
PAGE_LIMIT = 100
SEARCH_TIMEOUT = 20  # seconds per attempt; transient failures are retried twice
SORTS = {"cloud": [{"field": "properties.eo:cloud_cover", "direction": "asc"}]}  # least cloudy first


@dataclass(frozen=True)
class Scene:
    id: str
    datetime: str  # ISO 8601, UTC
    date: str  # YYYY-MM-DD
    cloud_cover: float
    epsg: int
    bbox: tuple
    assets: dict[str, str]  # band key -> https href
    self_url: str


class Scenes(list):
    """A search result: the scenes, plus how many items the API matched (None when it did not
    say) and how many it returned. Earth Search returns the newest first unless sorted."""
    matched: int | None = None
    returned: int = 0


def truncation(scenes) -> tuple[int, int] | None:
    """(returned, matched) when the search matched more items than it returned, else None."""
    matched = getattr(scenes, "matched", None)
    returned = getattr(scenes, "returned", len(scenes))
    return (returned, matched) if matched is not None and matched > returned else None


def _matched(page: dict) -> int | None:
    ctx = page.get("context")
    m = ctx.get("matched") if isinstance(ctx, dict) else None
    if m is None:
        m = page.get("numberMatched")
    return int(m) if isinstance(m, (int, float)) and not isinstance(m, bool) else None


def _epsg(props: dict) -> int | None:
    if props.get("proj:epsg") is not None:
        return int(props["proj:epsg"])
    code = props.get("proj:code")
    if isinstance(code, str) and code.upper().startswith("EPSG:"):
        return int(code.split(":", 1)[1])
    return None


def _parse(feature: dict) -> Scene | None:
    props = feature.get("properties", {})
    assets = feature.get("assets", {})
    if any(k not in assets or "href" not in assets[k] for k in BAND_KEYS):
        return None
    epsg = _epsg(props)
    dt = props.get("datetime")
    if epsg is None or not dt:
        return None
    self_url = next(
        (l["href"] for l in feature.get("links", []) if l.get("rel") == "self"), ""
    )
    return Scene(
        id=feature["id"],
        datetime=dt,
        date=dt[:10],
        cloud_cover=float(props.get("eo:cloud_cover", 0.0)),
        epsg=epsg,
        bbox=tuple(feature.get("bbox", ())),
        assets={k: assets[k]["href"] for k in BAND_KEYS},
        self_url=self_url,
    )


def _fetch_features(request: dict, max_items: int, post, sleep) -> tuple[list[dict], int | None]:
    url = STAC_URL + "/search"
    headers = {"User-Agent": USER_AGENT}
    features: list[dict] = []
    matched = None
    body = request
    while True:
        page = retry_transient(lambda: post(url, body, headers, timeout=SEARCH_TIMEOUT), sleep=sleep)
        if matched is None:
            matched = _matched(page)
        features.extend(page.get("features", []))
        if len(features) >= max_items:
            break
        nxt = next((l for l in page.get("links", []) if l.get("rel") == "next"), None)
        if not nxt or not nxt.get("body"):
            break
        body = {**request, **nxt["body"]} if nxt.get("merge") else nxt["body"]
    return features[:max_items], matched


def _read_cache(path: Path) -> tuple[list[dict], int | None] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        return None
    matched = data.get("matched")
    return data["features"], matched if isinstance(matched, int) else None


def search_scenes(
    aoi: AOI,
    start: date,
    end: date,
    cache_dir: Path,
    *,
    max_cloud: float = 80,
    max_items: int = 200,
    sortby: str | None = None,
    post=post_json,
    sleep=time.sleep,
    today: date | None = None,
) -> list[Scene]:
    request = {
        "collections": [COLLECTION],
        "bbox": list(aoi.bbox),  # not the polygon: a user's boundary never leaves the machine
        "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "limit": PAGE_LIMIT,
    }
    if sortby is not None:
        request["sortby"] = SORTS[sortby]
    canonical = json.dumps([request, max_items], sort_keys=True, separators=(",", ":"))
    path = Path(cache_dir) / "stac" / f"{hashlib.sha1(canonical.encode()).hexdigest()}.json"
    cacheable = end < (today or date.today())

    hit = _read_cache(path) if cacheable and path.exists() else None
    if hit is None:
        hit = _fetch_features(request, max_items, post, sleep)
        if cacheable:
            write_atomic(path, json.dumps({"features": hit[0], "matched": hit[1]}).encode("utf-8"))
    features, matched = hit

    scenes = Scenes(sorted((s for s in map(_parse, features) if s is not None),
                           key=lambda s: s.datetime))
    scenes.matched, scenes.returned = matched, len(features)
    return scenes


def group_by_date(scenes: list[Scene]) -> dict[str, list[Scene]]:
    groups: dict[str, list[Scene]] = {}
    for s in sorted(scenes, key=lambda s: (s.date, s.datetime)):
        groups.setdefault(s.date, []).append(s)
    return groups
