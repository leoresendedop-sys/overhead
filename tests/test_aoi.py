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


def _result(name, importance, lat=10.0, lon=20.0, country="Testland", **extra):
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
        **extra,
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
    fetch = lambda url, params, headers, timeout=30: [
        _result("Springfield", 0.61, 39.8, -89.6, "Illinois"),
        _result("Springfield", 0.60, 37.2, -93.3, "Missouri"),
    ]
    with pytest.raises(AmbiguousPlace) as ei:
        geocode("Springfield", tmp_cache, fetch=fetch, sleep=lambda s: None)
    cands = ei.value.extra["candidates"]
    assert len(cands) == 2
    for c in cands:
        assert set(c) == {"name", "country", "lat", "lon", "area_km2"}
    assert to_error_dict(ei.value)["error"]["type"] == "ambiguous_place"


def _geocode(results, tmp_cache):
    return geocode("q", tmp_cache, fetch=lambda *a, **k: results, sleep=lambda s: None)


def test_geocode_drops_postcode_results(tmp_cache):
    a = _geocode([_result("91001", 0.61, 40.0, -90.0, addresstype="postcode", type="postcode"),
                  _result("Altadena", 0.60, addresstype="town")], tmp_cache)
    assert a.name.startswith("Altadena")


def test_geocode_keeps_a_postcode_when_nothing_else_matched(tmp_cache):
    a = _geocode([_result("91001", 0.5, type="postcode")], tmp_cache)
    assert a.name.startswith("91001")


def test_geocode_collapses_the_same_place(tmp_cache):
    # Nominatim's real answer for "Altadena, California": the CDP polygon, the same town as a
    # point (91001), and a San Diego neighbourhood. Equal importance for the first two.
    us = {"state": "California", "country": "United States"}
    full = "Altadena, Los Angeles County, California, United States"
    a = _geocode([_result("Altadena", 0.5186, 34.1928, -118.1375, addresstype="census",
                          display_name=full, address=us),
                  _result("Altadena", 0.5186, 34.1899, -118.1317, addresstype="town",
                          display_name=full.replace("United", "91001, United"), address=us),
                  _result("Altadena", 0.1334, 32.7393, -117.1226, addresstype="quarter",
                          address=us)], tmp_cache)
    assert a.name == "Altadena, California" and a.full_name == full


@pytest.mark.parametrize("extra,expected", [
    ({"address": {"state": "Amazonas", "region": "North", "country": "Brazil"}}, "Manaus, Amazonas"),
    ({"address": {"region": "Île-de-France", "country": "France"}}, "Manaus, Île-de-France"),
    ({"address": {"country": "Brazil"}}, "Manaus, Brazil"),
    ({"address": {"state": "Manaus", "country": "Brazil"}}, "Manaus, Brazil"),  # region == name
    ({"name": "", "display_name": "Manaus, Região Norte, Brasil", "address": {"country": "Brasil"}},
     "Manaus, Brasil"),
])
def test_geocode_short_name(tmp_cache, extra, expected):
    a = _geocode([{**_result("Manaus", 0.7), **extra}], tmp_cache)
    assert a.name == expected and a.source == "nominatim"
    assert a.full_name == extra.get("display_name", "Manaus, Testland")


def test_ambiguous_candidates_use_short_names(tmp_cache):
    with pytest.raises(AmbiguousPlace) as ei:
        _geocode([_result("Springfield", 0.61, 39.8, -89.6,
                          address={"state": "Illinois", "country": "United States"}),
                  _result("Springfield", 0.60, 37.2, -93.3,
                          address={"state": "Missouri", "country": "United States"})], tmp_cache)
    names = [c["name"] for c in ei.value.extra["candidates"]]
    assert names == ["Springfield, Illinois", "Springfield, Missouri"]


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
