import pytest

from engine import aoi as aoi_mod
from engine.aoi import (
    AOI,
    aoi_from_geojson,
    aoi_from_point,
    geocode,
    resolution_for_area,
    resolve_aoi,
)
from engine.errors import (
    AmbiguousPlace,
    AreaTooLarge,
    BadInput,
    PlaceNotFound,
    to_error_dict,
)

UA = "overhead-hermes-plugin/0.1.0 (+https://github.com/leoresendedop-sys/overhead)"


def _result(name, importance, lat=10.0, lon=20.0, country="Testland"):
    d = 0.05
    return {
        "display_name": f"{name}, {country}",
        "name": name,
        "lat": str(lat),
        "lon": str(lon),
        "importance": importance,
        "boundingbox": [str(lat - d), str(lat + d), str(lon - d), str(lon + d)],
        "geojson": {"type": "Point", "coordinates": [lon, lat]},
        "address": {"country": country},
    }


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    aoi_mod._last_request = 0.0


def test_point_aoi_square():
    a = aoi_from_point(0, 0, 5)
    assert a.source == "point"
    for got, want in zip(a.bbox, (-0.0449, -0.0449, 0.0449, 0.0449)):
        assert got == pytest.approx(want, abs=1e-3)
    assert a.area_km2 == pytest.approx(100, abs=1)


@pytest.mark.parametrize("r", [0, 101])
def test_point_radius_bounds(r):
    with pytest.raises(BadInput):
        aoi_from_point(0, 0, r)


def test_geojson_feature_collection_accepted():
    poly = {"type": "Polygon", "coordinates": [[[0, 0], [0.1, 0], [0.1, 0.1], [0, 0.1], [0, 0]]]}
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": poly}]}
    a = aoi_from_geojson(fc)
    assert a.source == "geojson"
    assert a.area_km2 == pytest.approx(123.4, abs=2)
    assert a.bbox == (0, 0, 0.1, 0.1)


def test_geojson_point_rejected():
    with pytest.raises(BadInput):
        aoi_from_geojson({"type": "Point", "coordinates": [0, 0]})


@pytest.mark.parametrize("area,res", [(100, 10), (625, 10), (2500, 20), (10000, 40), (40000, 80)])
def test_resolution_policy(area, res):
    assert resolution_for_area(area) == res


def test_resolution_too_large():
    with pytest.raises(AreaTooLarge):
        resolution_for_area(40001)


def test_geocode_picks_dominant(tmp_cache):
    fetch = lambda url, params, headers, timeout=30: [_result("A", 0.8), _result("B", 0.5)]
    a = geocode("Somewhere", tmp_cache, fetch=fetch, sleep=lambda s: None)
    assert a.source == "nominatim"
    assert a.name.startswith("A")


def test_geocode_ambiguous_returns_candidates(tmp_cache):
    fetch = lambda url, params, headers, timeout=30: [_result("A", 0.61), _result("B", 0.60)]
    with pytest.raises(AmbiguousPlace) as ei:
        geocode("Springfield", tmp_cache, fetch=fetch, sleep=lambda s: None)
    cands = ei.value.extra["candidates"]
    assert len(cands) == 2
    for c in cands:
        assert set(c) == {"name", "country", "lat", "lon", "area_km2"}
    assert to_error_dict(ei.value)["error"]["type"] == "ambiguous_place"


def test_geocode_not_found(tmp_cache):
    with pytest.raises(PlaceNotFound):
        geocode("Nowhere", tmp_cache, fetch=lambda *a, **k: [], sleep=lambda s: None)


def test_geocode_cached(tmp_cache):
    calls = []

    def fetch(url, params, headers, timeout=30):
        calls.append(url)
        return [_result("A", 0.8)]

    geocode("Cached", tmp_cache, fetch=fetch, sleep=lambda s: None)
    b = geocode("Cached", tmp_cache, fetch=fetch, sleep=lambda s: None)
    assert len(calls) == 1
    assert b.source == "nominatim"


def test_geocode_sends_user_agent(tmp_cache):
    seen = {}

    def fetch(url, params, headers, timeout=30):
        seen["ua"] = headers["User-Agent"]
        seen["url"] = url
        seen["params"] = params
        return [_result("A", 0.8)]

    geocode("UA", tmp_cache, fetch=fetch, sleep=lambda s: None)
    assert seen["ua"] == UA
    assert seen["url"] == "https://nominatim.openstreetmap.org/search"
    assert seen["params"]["format"] == "jsonv2"
    assert seen["params"]["polygon_geojson"] == 1
    assert seen["params"]["limit"] == 5


def test_geocode_rate_limit_sleeps(tmp_cache):
    sleeps = []
    fetch = lambda url, params, headers, timeout=30: [_result("A", 0.8)]
    geocode("One", tmp_cache, fetch=fetch, sleep=sleeps.append)
    geocode("Two", tmp_cache, fetch=fetch, sleep=sleeps.append)
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 1.0
    geocode("Two", tmp_cache, fetch=fetch, sleep=sleeps.append)  # cache hit
    assert len(sleeps) == 1


def test_resolve_precedence_and_bad_input(tmp_cache):
    with pytest.raises(BadInput):
        resolve_aoi({}, tmp_cache)
    a = resolve_aoi({"lat": 1, "lon": 2, "radius_km": 3, "place": "x"}, tmp_cache)
    assert a.source == "point"
    poly = {"type": "Polygon", "coordinates": [[[0, 0], [0.1, 0], [0.1, 0.1], [0, 0.1], [0, 0]]]}
    assert resolve_aoi({"geojson": poly, "lat": 1, "lon": 2}, tmp_cache).source == "geojson"
