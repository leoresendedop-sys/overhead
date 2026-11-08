"""Image rendering with Pillow: true colour, change overlays, panel, GIF and chart."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

BG = "#0B0D10"
BORDER = "#1F242C"
TEXT = "#F2F4F7"
MUTED = "#9BA3AF"
LINE = "#4C9AFF"
ATTRIBUTION = "Sentinel-2 L2A · Copernicus / ESA · via Earth Search"

OVERLAY_ALPHA = 153  # 60 %
OVERLAY_COLORS: dict[str, list[tuple[str, str]]] = {
    "vegetation": [("veg_loss", "#E5484D"), ("veg_gain", "#30A46C")],
    "burn": [("burn_low", "#F5D90A"), ("burn_mod", "#F76B15"), ("burn_high", "#E5484D")],
    "water": [("water_gain", "#0090FF"), ("water_loss", "#F76B15")],
}

PAD = 28
GAP = 14
MAX_WIDTH = 1800


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def true_color(red: np.ndarray, green: np.ndarray, blue: np.ndarray) -> Image.Image:
    """RGB image from three bands (DN or reflectance); 2-98 percentile stretch, gamma 1/1.2."""
    bands = [np.asarray(b, dtype=np.float64) for b in (red, green, blue)]
    h, w = bands[0].shape
    out = np.zeros((h, w, 3), np.uint8)
    for i, band in enumerate(bands):
        valid = np.isfinite(band) & (band > 0)
        if not valid.any():
            continue
        lo, hi = np.percentile(band[valid], (2, 98))
        if hi <= lo:
            hi = lo + 1e-6
        scaled = np.clip((band - lo) / (hi - lo), 0.0, 1.0) ** (1 / 1.2)
        out[..., i] = np.where(valid, np.round(scaled * 255), 0).astype(np.uint8)
    return Image.fromarray(out, "RGB")


def overlay(base: Image.Image, masks: dict[str, np.ndarray], focus: str) -> Image.Image:
    """Paint the masks of `focus` at 60 % alpha. RGB in, RGB out; RGBA in, RGBA out."""
    layer = np.zeros((base.height, base.width, 4), np.uint8)
    for key, color in OVERLAY_COLORS.get(focus, []):
        mask = masks.get(key)
        if mask is None:
            continue
        m = np.asarray(mask, dtype=bool)
        layer[m] = (*_rgb(color), OVERLAY_ALPHA)
    layer_img = Image.fromarray(layer, "RGBA")
    composed = Image.alpha_composite(base.convert("RGBA"), layer_img)
    return composed if base.mode == "RGBA" else composed.convert("RGB")


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_w: float) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def panel(
    before: Image.Image,
    after: Image.Image,
    change: Image.Image,
    title: str,
    lines: list[str],
    legend: list[tuple[str, str]],
    captions: tuple[str, str, str] | None = None,
) -> Image.Image:
    """Before | after | change panel with header, captions, legend chips and attribution."""
    captions = captions or ("Before", "After", "Change")
    f_title, f_line, f_cap, f_small = _font(34), _font(20), _font(18), _font(14)

    avail = MAX_WIDTH - 2 * PAD - 2 * GAP
    pane_w = min(before.width, avail // 3)
    pane_h = max(1, round(before.height * pane_w / before.width))
    panes = [im.convert("RGB").resize((pane_w, pane_h), Image.LANCZOS) for im in (before, after, change)]
    width = 2 * PAD + 3 * pane_w + 2 * GAP
    inner_w = width - 2 * PAD

    # Pre-compute legend layout to know the height.
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    swatch, chip_gap, row_h = 16, 22, 28
    chips: list[tuple[int, int, str, str]] = []  # x, row, color, label
    x, row = 0, 0
    for a, b in legend:
        color, label = (a, b) if a.startswith("#") else (b, a)
        label = _fit(scratch, label, f_cap, inner_w - swatch - 8)
        w = swatch + 8 + scratch.textlength(label, font=f_cap)
        if x and x + w > inner_w:
            x, row = 0, row + 1
        chips.append((x, row, color, label))
        x += int(w) + chip_gap
    legend_rows = (row + 1) if chips else 0

    lines = lines[:3]
    header_h = 34 + 10 + len(lines) * 28 + (6 if lines else 0)
    cap_h = 30
    height = (PAD + header_h + 14 + cap_h + pane_h + 18
              + (legend_rows * row_h + 10 if legend_rows else 0) + 20 + PAD // 2 + 4)
    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)

    y = PAD
    d.text((PAD, y), _fit(d, title, f_title, inner_w), font=f_title, fill=TEXT)
    y += 34 + 10
    for line in lines:
        d.text((PAD, y), _fit(d, line, f_line, inner_w), font=f_line, fill=MUTED)
        y += 28
    y += (6 if lines else 0) + 14

    for i, (pane, cap) in enumerate(zip(panes, captions)):
        px = PAD + i * (pane_w + GAP)
        d.text((px, y + 2), _fit(d, cap, f_cap, pane_w), font=f_cap, fill=MUTED)
        img.paste(pane, (px, y + cap_h))
        d.rectangle([px - 1, y + cap_h - 1, px + pane_w, y + cap_h + pane_h], outline=BORDER, width=1)
    y += cap_h + pane_h + 18

    for cx, crow, color, label in chips:
        cy = y + crow * row_h
        d.rounded_rectangle([PAD + cx, cy + 2, PAD + cx + swatch, cy + 2 + swatch],
                            radius=4, fill=color, outline=BORDER)
        d.text((PAD + cx + swatch + 8, cy), label, font=f_cap, fill=MUTED)
    if legend_rows:
        y += legend_rows * row_h + 10

    d.text((width - PAD, height - PAD // 2 - 2), ATTRIBUTION, font=f_small, fill=MUTED, anchor="rd")
    return img


def annotate_frame(img: Image.Image, label: str) -> Image.Image:
    """Date label bottom-left on a semi-transparent dark pill."""
    base = img.convert("RGBA")
    size = max(14, round(min(base.size) / 24))
    font = _font(size)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    margin, pad_x, pad_y = max(8, size // 2), size * 0.7, size * 0.35
    tw = d.textlength(label, font=font)
    x0, y1 = margin, base.height - margin
    x1, y0 = x0 + tw + 2 * pad_x, y1 - size - 2 * pad_y
    d.rounded_rectangle([x0, y0, x1, y1], radius=(y1 - y0) / 2, fill=(11, 13, 16, 190))
    d.text((x0 + pad_x, (y0 + y1) / 2), label, font=font, fill=TEXT, anchor="lm")
    return Image.alpha_composite(base, layer).convert("RGB")


def save_gif(frames: list[Image.Image], path: Path, ms: int = 700) -> None:
    """Adaptive-palette looping GIF."""
    pal = [f.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=256) for f in frames]
    pal[0].save(path, save_all=True, append_images=pal[1:], duration=ms, loop=0, optimize=True)


def series_chart(points: list[tuple[str, float | None]], ylabel: str, path: Path,
                 size: tuple[int, int] = (900, 300)) -> None:
    """Dark line chart; None values break the line (no interpolation across gaps)."""
    k = 3  # supersample factor for antialiasing
    w, h = size[0] * k, size[1] * k
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    f_tick, f_label = _font(13 * k), _font(14 * k)
    left, right, top, bottom = 62 * k, 24 * k, 40 * k, 38 * k
    x0, x1, y0, y1 = left, w - right, top, h - bottom

    d.text((left, 12 * k), ylabel, font=f_label, fill=MUTED)
    vals = [v for _, v in points if v is not None]
    n = len(points)
    if vals:
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-9:
            lo, hi = lo - 0.5, hi + 0.5
        pad = (hi - lo) * 0.08
        ymin, ymax = lo - pad, hi + pad
    else:
        lo = hi = ymin = 0.0
        ymax = 1.0

    def px(i: int) -> float:
        return x0 + (x1 - x0) * (i / (n - 1) if n > 1 else 0.5)

    def py(v: float) -> float:
        return y1 - (y1 - y0) * (v - ymin) / (ymax - ymin)

    d.line([(x0, y1), (x1, y1)], fill=BORDER, width=k)
    d.line([(x0, y0), (x0, y1)], fill=BORDER, width=k)
    if vals:
        for v in (lo, hi):
            d.line([(x0, py(v)), (x1, py(v))], fill=BORDER, width=1)
            d.text((x0 - 8 * k, py(v)), f"{v:.2f}".rstrip("0").rstrip(".") if abs(v) < 100 else f"{v:.0f}",
                   font=f_tick, fill=MUTED, anchor="rm")
    if n:
        d.text((x0, y1 + 10 * k), points[0][0], font=f_tick, fill=MUTED, anchor="la" if n > 1 else "ma")
    if n > 1:
        d.text((x1, y1 + 10 * k), points[-1][0], font=f_tick, fill=MUTED, anchor="ra")

    run: list[tuple[float, float]] = []

    def flush() -> None:
        if len(run) > 1:
            d.line(run, fill=LINE, width=3 * k, joint="curve")
        for cx, cy in run:
            r = (2 if len(run) > 1 else 3.5) * k
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=LINE)
        run.clear()

    for i, (_, v) in enumerate(points):
        if v is None:
            flush()
        else:
            run.append((px(i), py(v)))
    flush()

    img.resize(size, Image.LANCZOS).save(path, "PNG")


def preview_data_url(img: Image.Image, max_side: int = 768) -> str:
    """JPEG (quality 80) data URL with the longest side at most `max_side`."""
    im = img.convert("RGB")
    scale = max_side / max(im.size)
    if scale < 1:
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
