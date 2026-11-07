import base64
import io
import warnings

import numpy as np
from PIL import Image

from engine.render import (
    annotate_frame,
    overlay,
    panel,
    preview_data_url,
    save_gif,
    series_chart,
    true_color,
)


def test_true_color_all_invalid_is_black():
    z = np.zeros((20, 30), np.uint16)
    nan = np.full((20, 30), np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        img = true_color(z, z, z)
        img2 = true_color(nan, nan, nan)
    assert img.mode == "RGB" and img.size == (30, 20)
    assert np.asarray(img).max() == 0 and np.asarray(img2).max() == 0


def test_true_color_stretches_and_blacks_nodata():
    rng = np.random.default_rng(1)
    band = rng.integers(500, 3000, (20, 20)).astype(np.uint16)
    band[0, 0] = 0
    arr = np.asarray(true_color(band, band, band))
    assert arr[0, 0].sum() == 0
    assert arr.max() > 200


def test_panel_size():
    panes = [Image.new("RGB", (400, 400), (90, 120, 90)) for _ in range(3)]
    img = panel(*panes, "Burn near Paradise", ["12.3 km2 changed", "b", "c", "dropped"],
                [("#E5484D", "Loss"), ("#30A46C", "Gain")])
    assert img.width <= 1800
    assert img.height > 400


def test_panel_scales_large_panes_and_accepts_captions():
    panes = [Image.new("RGB", (1200, 800), (30, 30, 30)) for _ in range(3)]
    img = panel(*panes, "t", [], [], captions=("Before · 2020-01-01", "After · 2021-01-01", "Change"))
    assert img.width <= 1800


def test_overlay_colors_pixel():
    base = Image.new("RGB", (4, 4), (100, 100, 100))
    m = np.zeros((4, 4), bool)
    m[1, 1] = True
    out = overlay(base, {"veg_loss": m}, "vegetation")
    r, g, b = out.getpixel((1, 1))
    assert out.mode == "RGB" and r > g and r > b
    assert out.getpixel((0, 0)) == (100, 100, 100)


def test_overlay_transparent_base_and_focus():
    base = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    m = np.zeros((4, 4), bool)
    m[2, 2] = True
    out = overlay(base, {"veg_loss": m, "water_gain": ~m}, "vegetation")
    assert out.mode == "RGBA"
    assert out.getpixel((0, 0))[3] == 0  # water mask ignored for vegetation focus
    r, g, b, a = out.getpixel((2, 2))
    assert (r, g, b) == (0xE5, 0x48, 0x4D) and abs(a - 153) <= 1


def test_annotate_frame_keeps_size_and_changes_pixels():
    img = Image.new("RGB", (200, 100), (250, 250, 250))
    out = annotate_frame(img, "2021-05")
    assert out.size == img.size
    assert np.asarray(out).min() < 250


def test_gif_frame_count(tmp_path):
    frames = [Image.new("RGB", (32, 32), (i * 50, 10, 10)) for i in range(4)]
    p = tmp_path / "t.gif"
    save_gif(frames, p)
    assert Image.open(p).n_frames == 4


def test_series_chart_with_gaps(tmp_path):
    pts = [("2020-01", 0.2), ("2020-02", None), ("2020-03", 0.6), ("2020-04", 0.5)]
    p = tmp_path / "c.png"
    series_chart(pts, "NDVI", p, size=(900, 300))
    assert Image.open(p).size == (900, 300)


def test_series_chart_degenerate(tmp_path):
    p = tmp_path / "c.png"
    series_chart([("2020-01", None)], "x", p)
    series_chart([("2020-01", 1.0), ("2020-02", 1.0)], "x", p)
    series_chart([], "x", p)
    assert Image.open(p).size == (900, 300)


def test_preview_data_url_prefix():
    img = Image.new("RGB", (2000, 1000), (10, 200, 10))
    url = preview_data_url(img)
    assert url.startswith("data:image/jpeg;base64,")
    out = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert max(out.size) <= 768
