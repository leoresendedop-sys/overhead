"""Area-of-interest resolution: place name, point + radius, or GeoJSON."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from rasterio.warp import transform_geom

from .errors import AmbiguousPlace, AreaTooLarge, BadInput, PlaceNotFound
from .fsutil import write_atomic
from .http import get_json

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "overhead-hermes-plugin/0.1.0 (+https://github.com/leoresendedop-sys/overhead)"
KM_PER_DEG = 111.195
MAX_AREA_KM2 = 40000
MAX_SIDE_PX = 2600  # 2,500 px a side plus a little for the UTM rotation of a lat/lon square
RESOLUTIONS = (10, 20, 40, 80)  # metres

_last_request = 0.0  # wall-clock time of the last real Nominatim request
_nominatim_lock = threading.Lock()  # held across the wait and the request: 1 request/s across threads
LANGUAGE = "en"  # place names in English ("Brazil", not "Brasil")


@dataclass(frozen=True)
class AOI:
    name: str
    geometry: dict
    bbox: tuple[float, float, float, float]
    area_km2: float
    source: str
    full_name: str | None = None  # the geocoder's long name, when there is one


def _rings(geometry: dict):
    t = geometry["type"]
    if t == "Polygon":
        return [geometry["coordinates"]]
    if t == "MultiPolygon":
        return list(geometry["coordinates"])
    raise BadInput(f"Geometry must be a Polygon or MultiPolygon, got {t}.")


def _bbox(geometry: dict) -> tuple[float, float, float, float]:
    pts = [p for poly in _rings(geometry) for ring in poly for p in ring]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _shoelace(ring) -> float:
    s = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the UTM zone containing the point (326xx north, 327xx south)."""
    zone = int((lon + 180) // 6) % 60 + 1
    return (32600 if lat >= 0 else 32700) + zone


def utm_bounds(geometry: dict) -> tuple[int, float, float, float, float]:
    """(EPSG, minx, miny, maxx, maxy): the geometry's bounds in metres, in the UTM zone of its
    bounding-box centre (the zone the analysis grid uses)."""
    minx, miny, maxx, maxy = _bbox(geometry)
    epsg = utm_epsg((minx + maxx) / 2, (miny + maxy) / 2)
    g = transform_geom("EPSG:4326", f"EPSG:{epsg}", geometry)
    pts = [p for poly in _rings(g) for ring in poly for p in ring]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return epsg, min(xs), min(ys), max(xs), max(ys)


def grid_shape(bounds: tuple, res: int) -> tuple[int, int]:
    """(width, height) in pixels of the grid snapped to `res` that covers `bounds`."""
    _, minx, miny, maxx, maxy = bounds
    return (math.ceil(maxx / res) - math.floor(minx / res),
            math.ceil(maxy / res) - math.floor(miny / res))


def area_km2(geometry: dict) -> float:
    minx, miny, maxx, maxy = _bbox(geometry)
    crs = f"EPSG:{utm_epsg((minx + maxx) / 2, (miny + maxy) / 2)}"
    g = transform_geom("EPSG:4326", crs, geometry)
    total = 0.0
    for poly in _rings(g):
        outer, *holes = ([[(p[0], p[1]) for p in ring[:-1]] for ring in poly])
        total += _shoelace(outer) - sum(_shoelace(h) for h in holes)
    return total / 1e6


def _bbox_polygon(minx, miny, maxx, maxy) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]],
    }


def _make(name: str, geometry: dict, source: str, full_name: str | None = None) -> AOI:
    return AOI(name, geometry, _bbox(geometry), round(area_km2(geometry), 1), source, full_name)


def aoi_from_point(lat: float, lon: float, radius_km: float, name: str | None = None) -> AOI:
    try:
        lat, lon, radius_km = float(lat), float(lon), float(radius_km)
    except (TypeError, ValueError):
        raise BadInput("lat, lon and radius_km must be numbers.")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise BadInput("lat must be in [-90, 90] and lon in [-180, 180].")
    if not 0 < radius_km <= 100:
        raise BadInput("radius_km must be greater than 0 and at most 100.")
    dlat = radius_km / KM_PER_DEG
    dlon = dlat / max(math.cos(math.radians(lat)), 0.01)
    geom = _bbox_polygon(
        max(lon - dlon, -180), max(lat - dlat, -90), min(lon + dlon, 180), min(lat + dlat, 90)
    )
    return _make(name or f"{lat:.4f}, {lon:.4f}", geom, "point")


def _geometry_from_geojson(obj: dict) -> dict:
    t = obj.get("type") if isinstance(obj, dict) else None
    if t == "FeatureCollection":
        feats = [f for f in obj.get("features", []) if f.get("geometry")]
        if not feats:
            raise BadInput("The GeoJSON FeatureCollection has no geometry.")
        geoms = [_geometry_from_geojson(f["geometry"]) for f in feats]
        polys = [p for g in geoms for p in _rings(g)]
        return geoms[0] if len(polys) == 1 else {"type": "MultiPolygon", "coordinates": polys}
    if t == "Feature":
        if not obj.get("geometry"):
            raise BadInput("The GeoJSON Feature has no geometry.")
        return _geometry_from_geojson(obj["geometry"])
    if t in ("Polygon", "MultiPolygon"):
        return obj
    raise BadInput(f"GeoJSON must be a Polygon or MultiPolygon, got {t}.")


def aoi_from_geojson(obj: dict | str, name: str | None = None) -> AOI:
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except ValueError:
            raise BadInput("geojson is not valid JSON.")
    geom = _geometry_from_geojson(obj)
    return _make(name or "Custom area", geom, "geojson")


def _read_cached(path: Path) -> list[dict] | None:
    """The cached answer, or None when it is missing, unreadable or not a list of results."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        return None
    return data


def _fetch_results(query, cache_dir, fetch, sleep):
    global _last_request
    cdir = Path(cache_dir) / "geocode"
    cdir.mkdir(parents=True, exist_ok=True)
    key = f"{LANGUAGE}|{query.strip().lower()}"
    path = cdir / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")
    cached = _read_cached(path)
    if cached is not None:
        return cached
    with _nominatim_lock:
        cached = _read_cached(path)  # another thread may have fetched it while this one waited
        if cached is not None:
            return cached
        wait = 1.0 - (time.time() - _last_request)
        if wait > 0:
            sleep(wait)
        params = {
            "q": query,
            "format": "jsonv2",
            "polygon_geojson": 1,
            "addressdetails": 1,
            "limit": 5,
            "accept-language": LANGUAGE,
        }
        try:
            results = fetch(NOMINATIM_URL, params, {"User-Agent": USER_AGENT}, timeout=30)
        finally:
            _last_request = time.time()
        if not isinstance(results, list) or not all(isinstance(r, dict) for r in results):
            raise PlaceNotFound(f"No place found for '{query}'. Try a more specific name or pass "
                                "lat/lon.")
        write_atomic(path, json.dumps(results).encode("utf-8"))  # inside the lock: see the re-check
    return results


def _geometry_of(result: dict) -> dict:
    g = result.get("geojson")
    if isinstance(g, dict) and g.get("type") in ("Polygon", "MultiPolygon"):
        return g
    s, n, w, e = (float(v) for v in result["boundingbox"])
    return _bbox_polygon(w, s, e, n)


def _country(result: dict) -> str:
    c = (result.get("address") or {}).get("country")
    return c or result.get("display_name", "").rsplit(",", 1)[-1].strip()


def _short_name(result: dict, fallback: str) -> str:
    """Short title: the name plus its state, region or country, e.g. Altadena, California."""
    display = str(result.get("display_name") or "")
    name = str(result.get("name") or display.split(",")[0]).strip() or fallback
    address = result.get("address") or {}
    region = next((str(address[k]).strip() for k in ("state", "region", "country")
                   if address.get(k) and str(address[k]).strip() != name), "")
    return f"{name}, {region}" if region else name


def _is_postcode(result: dict) -> bool:
    return "postcode" in (result.get("addresstype"), result.get("type"))


def _inside_bbox(result: dict, other: dict) -> bool:
    try:
        s, n, w, e = (float(v) for v in other["boundingbox"])
        lat, lon = float(result["lat"]), float(result["lon"])
    except (KeyError, TypeError, ValueError):
        return False
    return s <= lat <= n and (w <= lon <= e if w <= e else lon >= w or lon <= e)


def _distinct_places(results: list[dict]) -> list[dict]:
    """Drop postcode rows when anything else matched, then rows that are the top place again
    (Nominatim often returns a town both as its boundary and as a point inside it)."""
    if any(not _is_postcode(r) for r in results):
        results = [r for r in results if not _is_postcode(r)]
    if not results:
        return results
    top = results[0]
    return [top] + [r for r in results[1:] if not _inside_bbox(r, top)]


def geocode(query: str, cache_dir: Path, *, fetch=get_json, sleep=time.sleep) -> AOI:
    results = _distinct_places(_fetch_results(query, cache_dir, fetch, sleep))
    if not results:
        raise PlaceNotFound(f"No place found for '{query}'. Try a more specific name or pass lat/lon.")
    top = results[0]
    if len(results) > 1 and float(top.get("importance", 0)) < float(results[1].get("importance", 0)) + 0.1:
        cands = []
        for r in results[:5]:
            g = _geometry_of(r)
            cands.append(
                {
                    "name": _short_name(r, query),
                    "country": _country(r),
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"]),
                    "area_km2": round(area_km2(g), 1),
                }
            )
        raise AmbiguousPlace(
            f"'{query}' matches several places. Pick one or pass lat/lon.", candidates=cands
        )
    return _make(_short_name(top, query), _geometry_of(top), "nominatim",
                 top.get("display_name") or None)


def resolve_aoi(args: dict, cache_dir: Path) -> AOI:
    name = args.get("name")
    if args.get("geojson") is not None:
        return aoi_from_geojson(args["geojson"], name)
    if args.get("lat") is not None and args.get("lon") is not None:
        return aoi_from_point(args["lat"], args["lon"], args.get("radius_km", 5), name)
    if args.get("place"):
        return geocode(str(args["place"]), cache_dir)
    raise BadInput("Give a place name, lat and lon, or a GeoJSON polygon.")


def resolution_for(aoi: AOI) -> int:
    """The analysis resolution: the first of 10, 20, 40 and 80 m at which the area's UTM grid is at
    most MAX_SIDE_PX a side. The grid, not the area, sets the memory a run needs: a long thin strip
    or scattered polygons have a small area and a huge grid. Refuses areas over MAX_AREA_KM2, and
    areas whose grid is too big even at 80 m."""
    if aoi.area_km2 > MAX_AREA_KM2:
        raise AreaTooLarge(
            f"The area is {aoi.area_km2:.0f} km2; the limit is {MAX_AREA_KM2} km2. Choose a smaller area."
        )
    bounds = utm_bounds(aoi.geometry)
    for res in RESOLUTIONS:
        side = max(grid_shape(bounds, res))
        if side <= MAX_SIDE_PX:
            return res
    _, minx, miny, maxx, maxy = bounds
    raise AreaTooLarge(
        f"The area spans {(maxx - minx) / 1000:.0f} km by {(maxy - miny) / 1000:.0f} km: even at "
        f"{RESOLUTIONS[-1]} m its image would be {side:,} px a side, over the {MAX_SIDE_PX:,} px "
        "limit. Choose a smaller or more compact area, or analyse its parts separately."
    )
