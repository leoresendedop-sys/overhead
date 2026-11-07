import numpy as np
import rasterio
from rasterio.transform import from_origin

from engine import raster
from engine.aoi import aoi_from_geojson, aoi_from_point
from engine.raster import (
    Grid,
    aoi_mask,
    choose_overview,
    grid_for_aoi,
    mosaic_first_valid,
    read_band,
    read_date_stack,
    utm_epsg,
)
from engine.stac import Scene


def test_utm_epsg():
    assert utm_epsg(-46.6, -23.5) == 32723
    assert utm_epsg(-118.1, 34.2) == 32611


def test_grid_for_10km_square():
    # Off the central meridian the lon/lat box rotates in UTM and its bounding
    # grid grows (about 1014 px at -118.1), so test on the meridian. UTM scale
    # (0.9996) and the AOI's spherical km/degree still shift it a few pixels.
    g = grid_for_aoi(aoi_from_point(34.2, -117.0, 5), 10)
    assert g.epsg == 32611
    assert 995 <= g.width <= 1005
    assert 995 <= g.height <= 1005
    assert g.transform[0] == 10 and g.transform[4] == -10
    assert g.transform[2] % 10 == 0 and g.transform[5] % 10 == 0


def test_aoi_mask_counts():
    full = aoi_from_point(34.2, -118.1, 5)
    g = grid_for_aoi(full, 10)
    minx, miny, maxx, maxy = full.bbox
    half = aoi_from_geojson(
        {
            "type": "Polygon",
            "coordinates": [
                [[minx, miny], [(minx + maxx) / 2, miny], [(minx + maxx) / 2, maxy], [minx, maxy], [minx, miny]]
            ],
        }
    )
    m = aoi_mask(half, g)
    assert m.dtype == bool and m.shape == (g.height, g.width)
    assert abs(m.mean() - 0.5) < 0.02


def _write_tif(path, data, epsg=32723, x0=300000.0, y0=7400000.0, res=10.0):
    with rasterio.open(
        path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1], count=1,
        dtype="uint16", crs=f"EPSG:{epsg}", transform=from_origin(x0, y0, res, res), nodata=0,
    ) as dst:
        dst.write(data.astype("uint16"), 1)


def test_read_band_reprojects_local_tif(tmp_path):
    data = (np.arange(100)[None, :] * 10 + np.arange(100)[:, None] * 3 + 100).astype("uint16")
    p = tmp_path / "a.tif"
    _write_tif(p, data)
    g = Grid(32723, (10.0, 0.0, 300000.0, 0.0, -10.0, 7400000.0), 100, 100, 10)
    out = read_band(str(p), g)
    assert out.dtype == np.uint16 and out.shape == (100, 100)
    assert np.array_equal(out, data)

    g20 = Grid(32723, (20.0, 0.0, 300000.0, 0.0, -20.0, 7400000.0), 50, 50, 20)
    out20 = read_band(str(p), g20)
    assert out20.shape == (50, 50)
    assert abs(out20.mean() / data.mean() - 1) < 0.01


def test_choose_overview():
    factors = [2, 4, 8, 16]
    assert choose_overview(10, factors, 10) is None
    assert choose_overview(10, factors, 20) == 0
    assert choose_overview(10, factors, 40) == 1
    assert choose_overview(10, factors, 30) == 0
    assert choose_overview(10, factors, 80) == 2
    assert choose_overview(20, factors, 20) is None
    assert choose_overview(20, factors, 80) == 1
    assert choose_overview(10, [], 80) is None


def test_mosaic_first_valid_fills_from_second_tile():
    a = np.zeros((4, 6), dtype=np.uint16)
    a[:, :3] = 5
    b = np.full((4, 6), 9, dtype=np.uint16)
    out = mosaic_first_valid([a, b])
    assert (out[:, :3] == 5).all() and (out[:, 3:] == 9).all()
    assert (out != 0).all()


def _scene(sid):
    return Scene(sid, "2025-02-01T18:00:00Z", "2025-02-01", 1.0, 32611, (), {"red": f"https://x/{sid}_red.tif"}, "")


def test_read_date_stack_uses_cache(tmp_cache, monkeypatch):
    g = Grid(32611, (10.0, 0.0, 0.0, 0.0, -10.0, 40.0), 4, 4, 10)
    calls = []

    def fake(href, grid, *, categorical=False):
        calls.append(href)
        return np.full((grid.height, grid.width), 7, dtype=np.uint16)

    monkeypatch.setattr(raster, "read_band", fake)
    scenes = [_scene("A"), _scene("B")]
    first = read_date_stack(scenes, g, ["red"], tmp_cache)
    assert len(calls) == 2
    assert first["red"].dtype == np.uint16 and (first["red"] == 7).all()
    second = read_date_stack(scenes, g, ["red"], tmp_cache)
    assert len(calls) == 2
    assert np.array_equal(first["red"], second["red"])
    # a corrupt cache file is a miss
    for f in (tmp_cache / "arrays").glob("*.npy"):
        f.write_bytes(b"junk")
    third = read_date_stack(scenes, g, ["red"], tmp_cache)
    assert len(calls) == 4 and (third["red"] == 7).all()
