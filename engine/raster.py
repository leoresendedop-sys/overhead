"""Common UTM grid, windowed COG reads over HTTPS, and per-date mosaics."""

from __future__ import annotations

import hashlib
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_geom

from .aoi import AOI, _rings, utm_epsg
from .stac import Scene

__all__ = [
    "Grid",
    "utm_epsg",
    "grid_for_aoi",
    "aoi_mask",
    "choose_overview",
    "read_band",
    "mosaic_first_valid",
    "read_date_stack",
]

GDAL_OPTIONS = {
    "AWS_NO_SIGN_REQUEST": "YES",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "VSI_CACHE": "TRUE",
    "GDAL_INGESTED_BYTES_AT_OPEN": "32768",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "1",
}
MAX_WORKERS = 12
_OVERVIEW_PROBE: dict[tuple[str, int], int | None] = {}  # (href, target res) -> overview index
_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()


def _shared_pool() -> ThreadPoolExecutor:
    """One process-wide pool for HTTP reads, so concurrent callers cannot multiply threads."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="overhead-read")
        return _pool
CATEGORICAL_BANDS = {"scl"}


@dataclass(frozen=True)
class Grid:
    epsg: int
    transform: tuple[float, ...]  # affine a, b, c, d, e, f
    width: int
    height: int
    res: int


def grid_for_aoi(aoi: AOI, res: int) -> Grid:
    minx, miny, maxx, maxy = aoi.bbox
    epsg = utm_epsg((minx + maxx) / 2, (miny + maxy) / 2)
    g = transform_geom("EPSG:4326", f"EPSG:{epsg}", aoi.geometry)
    pts = [p for poly in _rings(g) for ring in poly for p in ring]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0 = math.floor(min(xs) / res) * res
    x1 = math.ceil(max(xs) / res) * res
    y0 = math.floor(min(ys) / res) * res
    y1 = math.ceil(max(ys) / res) * res
    return Grid(epsg, (float(res), 0.0, float(x0), 0.0, -float(res), float(y1)),
                int(round((x1 - x0) / res)), int(round((y1 - y0) / res)), res)


def aoi_mask(aoi: AOI, grid: Grid) -> np.ndarray:
    """Boolean (height, width) array, True inside the AOI."""
    g = transform_geom("EPSG:4326", f"EPSG:{grid.epsg}", aoi.geometry)
    return geometry_mask(
        [g], out_shape=(grid.height, grid.width), transform=Affine(*grid.transform), invert=True
    )


def choose_overview(native_res: float, factors: list[int], target_res: float) -> int | None:
    """Index of the coarsest overview whose pixel size is <= target_res, else None."""
    best = None
    for k, f in enumerate(factors):
        if native_res * f <= target_res + 1e-6 and (best is None or f > factors[best]):
            best = k
    return best


def read_band(href: str, grid: Grid, *, categorical: bool = False) -> np.ndarray:
    """Read one band onto `grid` as uint16 (0 = nodata)."""
    if categorical:
        resampling = Resampling.nearest
    elif grid.res > 10:
        resampling = Resampling.average
    else:
        resampling = Resampling.bilinear
    with rasterio.Env(**GDAL_OPTIONS):
        level = None
        if grid.res > 10:
            probe = (href, grid.res)
            if probe in _OVERVIEW_PROBE:  # the header is read once per href and target resolution
                level = _OVERVIEW_PROBE[probe]
            else:
                with rasterio.open(href) as src:
                    level = choose_overview(src.res[0], list(src.overviews(1)), grid.res)
                _OVERVIEW_PROBE[probe] = level
        kwargs = {} if level is None else {"overview_level": level}
        with rasterio.open(href, **kwargs) as src:
            with WarpedVRT(
                src,
                crs=f"EPSG:{grid.epsg}",
                transform=Affine(*grid.transform),
                width=grid.width,
                height=grid.height,
                resampling=resampling,
                src_nodata=0,
                nodata=0,
            ) as vrt:
                return vrt.read(1).astype(np.uint16)


def mosaic_first_valid(arrays: list[np.ndarray]) -> np.ndarray:
    out = arrays[0].copy()
    for a in arrays[1:]:
        hole = out == 0
        if not hole.any():
            break
        out[hole] = a[hole]
    return out


def _cache_path(cache_dir: Path, scenes: list[Scene], grid: Grid, band: str) -> Path:
    key = "|".join([*(s.id for s in scenes), repr(grid), band])
    return Path(cache_dir) / "arrays" / f"{hashlib.sha1(key.encode()).hexdigest()}.npy"


def _load(path: Path, grid: Grid) -> np.ndarray | None:
    try:
        arr = np.load(path, allow_pickle=False)
    except (OSError, ValueError, EOFError):
        return None
    if arr.shape != (grid.height, grid.width) or arr.dtype != np.uint16:
        return None
    return arr


def _save(path: Path, arr: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        np.save(f, arr)
    os.replace(tmp, path)


def read_date_stack(
    scenes: list[Scene], grid: Grid, bands: list[str], cache_dir: Path
) -> dict[str, np.ndarray]:
    """Read `bands` for the scenes of one date and mosaic the tiles (first valid wins)."""
    paths = {b: _cache_path(cache_dir, scenes, grid, b) for b in bands}
    out: dict[str, np.ndarray] = {}
    for b in bands:
        arr = _load(paths[b], grid)
        if arr is not None:
            out[b] = arr
    todo = [b for b in bands if b not in out]
    if todo:
        jobs = [(b, s) for b in todo for s in scenes]
        pool = _shared_pool()
        futures = [
            pool.submit(read_band, s.assets[b], grid, categorical=b in CATEGORICAL_BANDS)
            for b, s in jobs
        ]
        results = [f.result() for f in futures]
        for b in todo:
            tiles = [r for (jb, _), r in zip(jobs, results) if jb == b]
            out[b] = mosaic_first_valid(tiles)
            _save(paths[b], out[b])
    return {b: out[b] for b in bands}
