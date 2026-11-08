import warnings

import numpy as np

from engine.analysis import (
    clear_fraction,
    composite_median,
    change_stats,
    mndwi,
    ndvi,
    pick_focus,
    reflectance,
    valid_mask,
)

BANDS = ("blue", "green", "red", "nir", "swir16", "swir22")
PIXEL_HA = 0.01


def stack(red=1000, nir=9000, green=2000, swir16=2000, swir22=2000, scl=4):
    out = {"blue": np.full((10, 10), 500, np.uint16)}
    for name, v in (("green", green), ("red", red), ("nir", nir),
                    ("swir16", swir16), ("swir22", swir22)):
        out[name] = np.full((10, 10), v, np.uint16)
    out["scl"] = np.full((10, 10), scl, np.uint8)
    return out


def full_aoi():
    return np.ones((10, 10), bool)


def test_reflectance_no_offset():
    r = reflectance(np.array([300, 0], np.uint16))
    assert r.dtype == np.float32
    assert abs(r[0] - 0.03) < 1e-6
    assert np.isnan(r[1])
    v = ndvi(reflectance(np.array([300], np.uint16)), reflectance(np.array([3000], np.uint16)))
    assert abs(float(v[0]) - 0.818) < 0.001


def test_valid_mask_classes():
    scl = np.arange(12, dtype=np.uint8)
    got = {int(i) for i in np.flatnonzero(valid_mask(scl))}
    assert got == {2, 4, 5, 6, 7, 11}


def test_clear_fraction():
    scl = np.full((10, 10), 4, np.uint8)
    scl[:5] = 9
    assert clear_fraction(scl, full_aoi()) == 0.5
    assert clear_fraction(scl, np.zeros((10, 10), bool)) == 0.0


def test_vegetation_loss_counts():
    before, after = stack(), stack()
    # NDVI 0.8 -> 0.3 on 5 pixels
    for k in ("red", "nir"):
        after[k][0, :5] = {"red": 3500, "nir": 6500}[k]
    s = change_stats(before, after, full_aoi(), PIXEL_HA)
    assert s["vegetation"] == {"lost_ha": 0.05, "gained_ha": 0.0}
    assert s["masks"]["veg_loss"].sum() == 5
    assert s["masks"]["veg_gain"].sum() == 0


def test_burn_classes():
    before, after = stack(swir22=1000), stack(swir22=1000)
    # NBR before = (9000-1000)/(10000) = 0.8; after chosen so dNBR = 0.2, 0.4, 0.8
    for col, nbr_after in enumerate((0.6, 0.4, 0.0)):
        after["nir"][0, col] = int(round(5000 * (1 + nbr_after)))
        after["swir22"][0, col] = int(round(5000 * (1 - nbr_after)))
    s = change_stats(before, after, full_aoi(), PIXEL_HA)
    assert s["burn"] == {"low_ha": 0.01, "moderate_ha": 0.01, "high_ha": 0.01}


def test_water_gain():
    before = stack(green=4000, swir16=6000)  # MNDWI -0.2
    after = stack(green=4000, swir16=6000)
    after["green"][1, :3] = 6500
    after["swir16"][1, :3] = 3500  # MNDWI 0.3
    s = change_stats(before, after, full_aoi(), PIXEL_HA)
    assert s["water"]["gained_ha"] == 0.03
    assert s["water"]["lost_ha"] == 0.0
    assert s["water"]["after_ha"] == 0.03
    assert s["water"]["before_ha"] == 0.0
    assert abs(float(mndwi(np.array([0.4]), np.array([0.6]))[0]) + 0.2) < 1e-6


def test_low_confidence():
    before, after = stack(), stack()
    after["scl"][:4] = 9
    s = change_stats(before, after, full_aoi(), PIXEL_HA)
    assert s["valid_fraction"] == 0.6
    assert s["confidence"] == "low"
    assert change_stats(stack(), stack(), full_aoi(), PIXEL_HA)["confidence"] == "high"


def test_change_stats_all_invalid():
    before, after = stack(scl=0), stack(scl=0)
    for k in BANDS:
        before[k][:] = 0
        after[k][:] = 0
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        s = change_stats(before, after, full_aoi(), PIXEL_HA)
        empty = change_stats(stack(), stack(), np.zeros((10, 10), bool), PIXEL_HA)
    assert s["valid_fraction"] == 0.0
    assert s["confidence"] == "low"
    assert s["vegetation"] == {"lost_ha": 0.0, "gained_ha": 0.0}
    assert s["burn"] == {"low_ha": 0.0, "moderate_ha": 0.0, "high_ha": 0.0}
    assert s["water"] == {"before_ha": 0.0, "after_ha": 0.0, "lost_ha": 0.0, "gained_ha": 0.0}
    assert empty["valid_fraction"] == 0.0
    assert all(not m.any() for m in s["masks"].values())


def test_threshold_boundaries():
    # dNBR exactly 0.10 is low; NDVI below 0.5 before is never a loss
    s = change_stats(stack(nir=5500, swir22=4500), stack(nir=5000, swir22=5000), full_aoi(), PIXEL_HA)
    assert s["burn"]["low_ha"] == 1.0  # all 100 pixels sit exactly at dNBR 0.10
    s2 = change_stats(stack(red=3333, nir=6667), stack(red=5000, nir=5000), full_aoi(), PIXEL_HA)
    assert s2["vegetation"]["lost_ha"] == 0.0  # NDVI 0.33 < 0.5, never counted


def test_composite_median_skips_clouds():
    a, b, c = stack(red=1000), stack(red=3000), stack(red=9999)
    c["scl"][0, 0] = 9
    d = composite_median([a, b, c])
    assert d["red"].dtype == np.uint16
    assert d["red"][0, 0] == 2000
    assert d["red"][1, 1] == 3000
    assert (d["scl"] == 4).all()
    b["scl"][0, 0] = 3
    a["scl"][0, 0] = 9
    d = composite_median([a, b, c])
    assert d["red"][0, 0] == 0 and d["scl"][0, 0] == 0


def test_pick_focus():
    stats = {
        "vegetation": {"lost_ha": 20.0, "gained_ha": 10.0},
        "burn": {"low_ha": 100.0, "moderate_ha": 35.0, "high_ha": 5.0},
        "water": {"lost_ha": 1.0, "gained_ha": 0.0},
    }
    assert pick_focus(stats) == "burn"
    noisy = {**stats, "burn": {"low_ha": 900.0, "moderate_ha": 2.0, "high_ha": 1.0}}
    assert pick_focus(noisy) == "vegetation"  # low-severity dNBR is noise, not counted
    tie = {
        "vegetation": {"lost_ha": 5.0, "gained_ha": 5.0},
        "burn": {"low_ha": 0.0, "moderate_ha": 10.0, "high_ha": 0.0},
        "water": {"lost_ha": 10.0, "gained_ha": 0.0},
    }
    assert pick_focus(tie) == "vegetation"
