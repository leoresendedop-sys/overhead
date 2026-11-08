import copy
import json
from datetime import date
from pathlib import Path

import pytest

from engine.aoi import USER_AGENT, aoi_from_geojson, aoi_from_point
from engine.errors import NetworkError
from engine.stac import STAC_URL, group_by_date, search_scenes, truncation

FIX = Path(__file__).parent / "fixtures"
BANDS = {"blue", "green", "red", "nir", "swir16", "swir22", "scl"}


def _pages():
    return [json.loads((FIX / f).read_text()) for f in ("stac_page1.json", "stac_page2.json")]


class FakePost:
    def __init__(self, pages=None):
        self.pages = pages or _pages()
        self.calls = []

    def __call__(self, url, body, headers, timeout=60):
        self.calls.append((url, copy.deepcopy(body), headers))
        return copy.deepcopy(self.pages[len(self.calls) - 1 if "next" in body else 0])


AOI = aoi_from_point(-23.1, -47.0, 5)
PAST = (date(2025, 2, 1), date(2025, 2, 20))


def _search(post, cache, rng=PAST, **kw):
    return search_scenes(AOI, rng[0], rng[1], cache, post=post, today=date(2026, 11, 7), **kw)


def test_parses_assets_and_epsg(tmp_cache):
    scenes = _search(FakePost(), tmp_cache)
    s = scenes[0]
    assert set(s.assets) == BANDS
    assert s.epsg == 32723
    assert s.assets["red"].startswith("https://")
    assert s.self_url.endswith("/items/" + s.id)
    assert s.date == s.datetime[:10]


def test_request_shape(tmp_cache):
    post = FakePost()
    _search(post, tmp_cache)
    url, body, headers = post.calls[0]
    assert url == STAC_URL + "/search"
    assert body["collections"] == ["sentinel-2-l2a"]
    assert body["bbox"] == list(AOI.bbox)
    assert "intersects" not in body
    assert body["query"] == {"eo:cloud_cover": {"lt": 80}}
    assert body["limit"] == 100
    assert body["datetime"] == "2025-02-01T00:00:00Z/2025-02-20T23:59:59Z"
    assert headers["User-Agent"] == USER_AGENT


def test_request_sends_only_the_bbox_of_a_polygon(tmp_cache):
    """A user's own boundary (a farm, say) never leaves the machine: only its bounding box does."""
    farm = aoi_from_geojson({"type": "Polygon", "coordinates": [[
        [-47.01, -23.10], [-46.98, -23.11], [-46.97, -23.08], [-47.00, -23.07], [-47.01, -23.10]]]})
    post = FakePost()
    search_scenes(farm, *PAST, tmp_cache, post=post, today=date(2026, 11, 7))
    body = post.calls[0][1]
    assert body["bbox"] == list(farm.bbox) == [-47.01, -23.11, -46.97, -23.07]
    assert "intersects" not in body and "-46.98" not in json.dumps(body)


def test_epsg_from_proj_code(tmp_cache):
    pages = _pages()
    for f in pages[0]["features"]:
        del f["properties"]["proj:epsg"]
        f["properties"]["proj:code"] = "EPSG:32723"
    assert _search(FakePost(pages), tmp_cache)[0].epsg == 32723


def test_skips_items_missing_a_band(tmp_cache):
    pages = _pages()
    del pages[0]["features"][0]["assets"]["scl"]
    scenes = _search(FakePost(pages), tmp_cache)
    assert len(scenes) == 3


def test_follows_next_link(tmp_cache):
    post = FakePost()
    scenes = _search(post, tmp_cache)
    assert len(scenes) == 4
    assert len(post.calls) == 2
    assert post.calls[1][1]["next"] == "token-page-2"
    assert [s.datetime for s in scenes] == sorted(s.datetime for s in scenes)


def test_max_items_stops_paging(tmp_cache):
    post = FakePost()
    scenes = _search(post, tmp_cache, max_items=3)
    assert len(scenes) == 3
    assert len(post.calls) == 1


def test_group_by_date_merges_tiles(tmp_cache):
    groups = group_by_date(_search(FakePost(), tmp_cache))
    assert list(groups) == sorted(groups)
    assert len(groups["2025-02-05"]) == 2
    assert len(groups) == 3


def _single_page():
    pages = _pages()
    pages[0]["links"] = []
    return FakePost(pages)


def test_cache_only_for_past_ranges(tmp_cache):
    post = _single_page()
    _search(post, tmp_cache)
    _search(post, tmp_cache)
    assert len(post.calls) == 1

    post2 = _single_page()
    today_range = (date(2026, 10, 1), date(2026, 11, 7))
    _search(post2, tmp_cache, today_range)
    _search(post2, tmp_cache, today_range)
    assert len(post2.calls) == 2


def test_cached_multipage_search_is_one_miss(tmp_cache):
    post = FakePost()
    first = _search(post, tmp_cache)
    again = _search(post, tmp_cache)
    assert len(post.calls) == 2
    assert again == first


def test_corrupt_cache_is_a_miss(tmp_cache):
    post = FakePost()
    _search(post, tmp_cache)
    for f in (tmp_cache / "stac").iterdir():
        f.write_text("{not json")
    post2 = FakePost()
    assert len(_search(post2, tmp_cache)) == 4


class FlakyPost(FakePost):
    """Raises the given errors on the first calls, then answers like FakePost."""

    def __init__(self, errors):
        super().__init__([_pages()[0] | {"links": []}])
        self.errors = list(errors)
        self.timeouts = []

    def __call__(self, url, body, headers, timeout=60):
        self.timeouts.append(timeout)
        if self.errors:
            raise self.errors.pop(0)
        return super().__call__(url, body, headers, timeout)


def _transient(msg="timed out"):
    return NetworkError(msg, transient=True)


def test_search_retries_transient_errors_with_backoff(tmp_cache):
    post, sleeps = FlakyPost([_transient(), _transient("HTTP 503")]), []
    scenes = _search(post, tmp_cache, sleep=sleeps.append)
    assert len(scenes) == 3 and sleeps == [1, 2]  # page 1 of the fixture
    assert post.timeouts == [20, 20, 20]


def test_search_gives_up_after_two_retries(tmp_cache):
    post, sleeps = FlakyPost([_transient(), _transient(), _transient("third")]), []
    with pytest.raises(NetworkError) as e:
        _search(post, tmp_cache, sleep=sleeps.append)
    assert e.value.message == "third" and sleeps == [1, 2] and len(post.timeouts) == 3


def test_search_does_not_retry_client_errors(tmp_cache):
    post, sleeps = FlakyPost([NetworkError("HTTP 400")]), []
    with pytest.raises(NetworkError):
        _search(post, tmp_cache, sleep=sleeps.append)
    assert sleeps == [] and len(post.timeouts) == 1


# ---------------------------------------------------------------- sort order and truncation (F3)

def _with_matched(matched, key="context"):
    pages = _pages()
    for p in pages:
        if key == "context":
            p["context"] = {"limit": 100, "matched": matched, "returned": len(p["features"])}
        else:
            p["numberMatched"] = matched
    return FakePost(pages)


def test_sortby_cloud_asks_for_the_least_cloudy_first(tmp_cache):
    post = FakePost()
    _search(post, tmp_cache, sortby="cloud")
    assert post.calls[0][1]["sortby"] == [{"field": "properties.eo:cloud_cover", "direction": "asc"}]
    plain = FakePost()
    _search(plain, tmp_cache, (date(2025, 3, 1), date(2025, 3, 9)))
    assert "sortby" not in plain.calls[0][1]  # Earth Search's own order: newest first


def test_sortby_is_part_of_the_cache_key(tmp_cache):
    post = _single_page()
    _search(post, tmp_cache)
    _search(post, tmp_cache, sortby="cloud")
    assert len(post.calls) == 2


@pytest.mark.parametrize("key", ["context", "numberMatched"])
def test_search_reports_more_matches_than_it_returned(tmp_cache, key):
    scenes = _search(_with_matched(3118, key), tmp_cache, max_items=3)
    assert len(scenes) == 3 and scenes.matched == 3118 and truncation(scenes) == (3, 3118)


def test_search_not_truncated_when_everything_came_back(tmp_cache):
    scenes = _search(_with_matched(4), tmp_cache)
    assert scenes.matched == 4 and truncation(scenes) is None
    assert truncation(_search(FakePost(), tmp_cache, (date(2025, 3, 1), date(2025, 3, 9)))) is None
    assert truncation([]) is None  # a plain list (a test fake) is never truncated


def test_matched_count_survives_the_cache(tmp_cache):
    first = _search(_with_matched(3118), tmp_cache, max_items=3)
    again = _search(FakePost(), tmp_cache, max_items=3)  # served from the cache
    assert again == first and again.matched == 3118


# ---------------------------------------------------------------- hardening (A9)

class EndlessPost(FakePost):
    """A server that keeps sending a next link with empty pages."""
    def __call__(self, url, body, headers, timeout=60):
        self.calls.append(body)
        if len(self.calls) > 5:
            raise AssertionError("still paging through empty pages")
        return {"type": "FeatureCollection", "features": [],
                "links": [{"rel": "next", "body": {"next": f"t{len(self.calls)}"}}]}


def test_empty_page_stops_paging(tmp_cache):
    post = EndlessPost()
    assert _search(post, tmp_cache) == [] and len(post.calls) == 1


@pytest.mark.parametrize("junk", ["[1, 2]", '{"features": "x"}', '{"features": [1, "a"]}', '"text"'])
def test_cache_of_the_wrong_shape_is_a_miss(tmp_cache, junk):
    _search(FakePost(), tmp_cache)
    for f in (tmp_cache / "stac").iterdir():
        f.write_text(junk)
    post = FakePost()
    assert len(_search(post, tmp_cache)) == 4 and len(post.calls) == 2
