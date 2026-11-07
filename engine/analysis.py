"""Cloud masking, spectral indices and change statistics (pure numpy, no I/O)."""

from __future__ import annotations

import numpy as np

INVALID_SCL = frozenset({0, 1, 3, 8, 9, 10})
VEG_MIN = 0.5
VEG_DELTA = 0.25
DNBR_LOW = 0.10
DNBR_MOD = 0.27
DNBR_HIGH = 0.66
WATER_MNDWI = 0.0
LOW_CONF_VALID = 0.70
COMPOSITE_BELOW = 0.80

_BANDS = ("blue", "green", "red", "nir", "swir16", "swir22")


def valid_mask(scl: np.ndarray) -> np.ndarray:
    """True where the scene classification is usable (not nodata, cloud, shadow, defective)."""
    return ~np.isin(scl, sorted(INVALID_SCL))


def clear_fraction(scl: np.ndarray, aoi: np.ndarray) -> float:
    n = int(aoi.sum())
    if n == 0:
        return 0.0
    return float((valid_mask(scl) & aoi).sum()) / n


def reflectance(dn: np.ndarray) -> np.ndarray:
    """DN / 10000 as float32, with DN 0 (nodata) as NaN. No BOA offset is applied."""
    out = dn.astype(np.float32) / np.float32(10000.0)
    out[dn == 0] = np.nan
    return out


def _norm_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (a - b) / (a + b)
    out[~np.isfinite(out)] = np.nan
    return out


def ndvi(red, nir):
    return _norm_diff(nir, red)


def nbr(nir, swir22):
    return _norm_diff(nir, swir22)


def mndwi(green, swir16):
    return _norm_diff(green, swir16)


def _refl64(dn: np.ndarray) -> np.ndarray:
    out = dn.astype(np.float64) / 10000.0
    out[dn == 0] = np.nan
    return out


def composite_median(stacks: list[dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
    """Per-pixel median of DN over the scenes whose SCL is valid at that pixel."""
    ok = np.stack([valid_mask(s["scl"]) for s in stacks])
    out: dict[str, np.ndarray] = {}
    for band in _BANDS:
        data = np.stack([s[band] for s in stacks]).astype(np.float64)
        good = ok & (data != 0)
        data[~good] = np.nan
        res = np.zeros(data.shape[1:], np.uint16)
        any_good = good.any(axis=0)
        if any_good.any():
            med = np.nanmedian(data[:, any_good], axis=0)
            res[any_good] = np.rint(med).astype(np.uint16)
        out[band] = res
    out["scl"] = np.where(ok.any(axis=0), 4, 0).astype(np.uint8)
    return out


def _ha(mask: np.ndarray, pixel_ha: float) -> float:
    return round(float(mask.sum()) * pixel_ha, 2)


def change_stats(before: dict, after: dict, aoi: np.ndarray, pixel_ha: float) -> dict:
    """Vegetation, burn and water change between two stacks, in hectares.

    Only pixels inside the AOI and with a valid SCL class in both dates count.
    An index is only evaluated where its own bands are non-zero in both dates.
    """
    base = aoi & valid_mask(before["scl"]) & valid_mask(after["scl"])
    n_aoi = int(aoi.sum())
    valid_fraction = float(base.sum()) / n_aoi if n_aoi else 0.0

    def idx(fn, a, b, stack):
        return fn(_refl64(stack[a]), _refl64(stack[b]))

    ndvi_b, ndvi_a = idx(ndvi, "red", "nir", before), idx(ndvi, "red", "nir", after)
    nbr_b, nbr_a = idx(nbr, "nir", "swir22", before), idx(nbr, "nir", "swir22", after)
    wat_b, wat_a = idx(mndwi, "green", "swir16", before), idx(mndwi, "green", "swir16", after)

    with np.errstate(invalid="ignore"):
        ok_v = base & np.isfinite(ndvi_b) & np.isfinite(ndvi_a)
        d_ndvi = np.round(ndvi_a - ndvi_b, 9)
        veg_loss = ok_v & (ndvi_b >= VEG_MIN) & (d_ndvi <= -VEG_DELTA)
        veg_gain = ok_v & (ndvi_a >= VEG_MIN) & (d_ndvi >= VEG_DELTA)

        ok_b = base & np.isfinite(nbr_b) & np.isfinite(nbr_a)
        dnbr = np.round(nbr_b - nbr_a, 9)
        burn_low = ok_b & (dnbr >= DNBR_LOW) & (dnbr < DNBR_MOD)
        burn_mod = ok_b & (dnbr >= DNBR_MOD) & (dnbr < DNBR_HIGH)
        burn_high = ok_b & (dnbr >= DNBR_HIGH)

        ok_w = base & np.isfinite(wat_b) & np.isfinite(wat_a)
        w_b = ok_w & (wat_b > WATER_MNDWI)
        w_a = ok_w & (wat_a > WATER_MNDWI)
    water_loss = w_b & ~w_a
    water_gain = ~w_b & w_a & ok_w

    return {
        "valid_fraction": round(valid_fraction, 4),
        "confidence": "low" if valid_fraction < LOW_CONF_VALID else "high",
        "vegetation": {"lost_ha": _ha(veg_loss, pixel_ha), "gained_ha": _ha(veg_gain, pixel_ha)},
        "burn": {
            "low_ha": _ha(burn_low, pixel_ha),
            "moderate_ha": _ha(burn_mod, pixel_ha),
            "high_ha": _ha(burn_high, pixel_ha),
        },
        "water": {
            "before_ha": _ha(w_b, pixel_ha),
            "after_ha": _ha(w_a, pixel_ha),
            "lost_ha": _ha(water_loss, pixel_ha),
            "gained_ha": _ha(water_gain, pixel_ha),
        },
        "thresholds": {
            "veg_min": VEG_MIN,
            "veg_delta": VEG_DELTA,
            "dnbr_low": DNBR_LOW,
            "dnbr_moderate": DNBR_MOD,
            "dnbr_high": DNBR_HIGH,
            "water_mndwi": WATER_MNDWI,
        },
        "masks": {
            "veg_loss": veg_loss,
            "veg_gain": veg_gain,
            "burn_low": burn_low,
            "burn_mod": burn_mod,
            "burn_high": burn_high,
            "water_loss": water_loss,
            "water_gain": water_gain,
        },
    }


def pick_focus(stats: dict) -> str:
    """Focus with the most changed hectares; ties go vegetation, burn, water."""
    totals = {
        "vegetation": stats["vegetation"]["lost_ha"] + stats["vegetation"]["gained_ha"],
        "burn": stats["burn"]["low_ha"] + stats["burn"]["moderate_ha"] + stats["burn"]["high_ha"],
        "water": stats["water"]["lost_ha"] + stats["water"]["gained_ha"],
    }
    return max(totals, key=lambda k: totals[k])  # max keeps the first of equal keys
