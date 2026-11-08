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


def _fetch_features(request: dict, max_items: int, post, sleep) -> list[dict]:
    url = STAC_URL + "/search"
    headers = {"User-Agent": USER_AGENT}
    features: list[dict] = []
    body = request
    while True:
        page = retry_transient(lambda: post(url, body, headers, timeout=SEARCH_TIMEOUT), sleep=sleep)
        features.extend(page.get("features", []))
        if len(features) >= max_items:
            break
        nxt = next((l for l in page.get("links", []) if l.get("rel") == "next"), None)
        if not nxt or not nxt.get("body"):
            break
        body = {**request, **nxt["body"]} if nxt.get("merge") else nxt["body"]
    return features[:max_items]


def search_scenes(
    aoi: AOI,
    start: date,
    end: date,
    cache_dir: Path,
    *,
    max_cloud: float = 80,
    max_items: int = 200,
    post=post_json,
    sleep=time.sleep,
    today: date | None = None,
) -> list[Scene]:
    request = {
        "collections": [COLLECTION],
        "intersects": aoi.geometry,
        "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "limit": PAGE_LIMIT,
    }
    canonical = json.dumps([request, max_items], sort_keys=True, separators=(",", ":"))
    path = Path(cache_dir) / "stac" / f"{hashlib.sha1(canonical.encode()).hexdigest()}.json"
    cacheable = end < (today or date.today())

    features = None
    if cacheable and path.exists():
        try:
            features = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            features = None
    if features is None:
        features = _fetch_features(request, max_items, post, sleep)
        if cacheable:
            write_atomic(path, json.dumps(features).encode("utf-8"))

    scenes = [s for s in map(_parse, features) if s is not None]
    scenes.sort(key=lambda s: s.datetime)
    return scenes


def group_by_date(scenes: list[Scene]) -> dict[str, list[Scene]]:
    groups: dict[str, list[Scene]] = {}
    for s in sorted(scenes, key=lambda s: (s.date, s.datetime)):
        groups.setdefault(s.date, []).append(s)
    return groups
