"""The three flows behind the tools: scene listing, before/after comparison, timelapse."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from PIL import Image

from .analysis import (
    COMPOSITE_BELOW,
    change_stats,
    clear_fraction,
    _refl64,
    composite_median,
    mndwi,
    nbr,
    ndvi,
    pick_focus,
    valid_mask,
)
from .aoi import AOI, resolution_for_area
from .errors import BadInput, NoClearScene
from .raster import aoi_mask, grid_for_aoi, read_date_stack
from .render import (
    annotate_frame,
    overlay,
    panel,
    save_gif,
    series_chart,
    true_color,
)
from .report import (
    compare_headline,
    compare_notes,
    fmt_date,
    fmt_ha,
    legend_for,
)
from .runs import RunStore
from .stac import COLLECTION, STAC_URL, Scene, group_by_date, search_scenes

FIRST_DATE = date(2017, 3, 28)  # Sentinel-2 L2A global coverage starts around here
FOCUSES = ("auto", "vegetation", "burn", "water")
MIN_CLEAR = 0.30
MIN_CLEAR_TIMELAPSE = 0.60
MAX_FRAMES = 24
ALL_BANDS = ["blue", "green", "red", "nir", "swir16", "swir22", "scl"]
METRICS = {
    "ndvi": (["nir"], "Mean NDVI"),
    "water": (["swir16"], "Water area (ha)"),
    "burn": (["nir", "swir22"], "Mean NBR"),
}
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")


def parse_date(s: str, *, field: str) -> date:
    hint = f"{field} '{s}' is not usable: use YYYY-MM-DD or YYYY-MM, or call overhead_scenes."
    m = _DATE_RE.match(str(s).strip())
    if not m:
        raise BadInput(hint)
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3) or 15)
    try:
        out = date(y, mo, d)
    except ValueError:
        raise BadInput(hint)
    if out > date.today():
        raise BadInput(f"{field} '{s}' is in the future; Sentinel-2 imagery only exists up to today.")
    if out < FIRST_DATE:
        raise BadInput(f"{field} '{s}' is too early; Sentinel-2 L2A coverage starts on 2017-03-28.")
    return out


# ---------------------------------------------------------------- helpers

class _Area:
    """Grids and masks for one AOI: analysis grid plus a coarse grid for ranking."""

    def __init__(self, aoi: AOI):
        self.aoi = aoi
        self.res = resolution_for_area(aoi.area_km2)
        self.grid = grid_for_aoi(aoi, self.res)
        self.mask = aoi_mask(aoi, self.grid)
        self.pixel_ha = self.res * self.res / 10000
        coarse = max(self.res, 60)
        self.cgrid = grid_for_aoi(aoi, coarse)
        self.cmask = aoi_mask(aoi, self.cgrid)


def _place(aoi: AOI) -> dict:
    return {"name": aoi.name, "bbox": list(aoi.bbox), "area_km2": aoi.area_km2, "source": aoi.source}


def _mean_cloud(scenes: list[Scene]) -> float:
    return sum(s.cloud_cover for s in scenes) / len(scenes)


def _lowest_cloud(groups: dict[str, list[Scene]], n: int) -> list[str]:
    return sorted(groups, key=lambda d: (_mean_cloud(groups[d]), d))[:n]


def _clear_by_date(area: _Area, groups, dates, reader, cache_dir) -> dict[str, float]:
    out = {}
    for d in dates:
        scl = reader(groups[d], area.cgrid, ["scl"], cache_dir)["scl"]
        out[d] = clear_fraction(scl, area.cmask)
    return out


def _provenance(scenes: list[Scene]) -> dict:
    seen: list[str] = []
    for s in scenes:
        if s.self_url and s.self_url not in seen:
            seen.append(s.self_url)
    return {"collection": COLLECTION, "stac": STAC_URL, "items": seen}


def _save_jpeg(img: Image.Image, path: Path) -> None:
    img.convert("RGB").save(path, "JPEG", quality=88)


def _rgb_image(stack: dict) -> Image.Image:
    return true_color(stack["red"], stack["green"], stack["blue"])


def _write_result(path: Path, result: dict) -> None:
    tmp = path.with_name("result.json.tmp")
    tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # result.json marks the run complete, so it goes last


# ---------------------------------------------------------------- scenes

def run_scenes(aoi: AOI, start: date, end: date, cache_dir: Path, *, max_results: int = 12,
               search=search_scenes, reader=read_date_stack) -> dict:
    if end <= start:
        raise BadInput("end must be after start.")
    area = _Area(aoi)
    groups = group_by_date(search(aoi, start, end, cache_dir))
    dates = _lowest_cloud(groups, 3 * max_results)
    clear = _clear_by_date(area, groups, dates, reader, cache_dir)
    ranked = sorted(dates, key=lambda d: (-clear[d], d))[:max_results]
    return {
        "place": _place(aoi),
        "dates": [{"date": d, "aoi_clear_fraction": round(clear[d], 2), "tiles": len(groups[d])}
                  for d in ranked],
    }


# ---------------------------------------------------------------- compare

def _suggest(area: _Area, target: date, cache_dir, search, reader) -> list[dict]:
    end = min(target + timedelta(days=90), date.today())
    groups = group_by_date(search(area.aoi, target - timedelta(days=90), end, cache_dir))
    dates = _lowest_cloud(groups, 8)
    clear = _clear_by_date(area, groups, dates, reader, cache_dir)
    best = sorted((d for d in dates if clear[d] > 0), key=lambda d: (-clear[d], d))[:3]
    return [{"date": d, "clear_fraction": round(clear[d], 2)} for d in best]


def _fail(area: _Area, target: date, side: str, cache_dir, search, reader):
    sugg = _suggest(area, target, cache_dir, search, reader)
    msg = f"No clear image near {target.isoformat()} ({side})."
    if sugg:
        msg += " Clearest nearby dates: " + ", ".join(
            f"{s['date']} ({round(s['clear_fraction'] * 100)}% clear)" for s in sugg
        ) + "."
    else:
        msg += " No usable images were found within 90 days."
    raise NoClearScene(msg, suggestions=sugg, side=side, target=target.isoformat())


def _pick_side(area: _Area, target: date, side: str, window: int, cache_dir, search, reader) -> dict:
    today = date.today()
    scenes = search(area.aoi, target - timedelta(days=window), min(target + timedelta(days=window), today),
                    cache_dir)
    groups = group_by_date(scenes)
    if not groups:
        _fail(area, target, side, cache_dir, search, reader)
    cand = _lowest_cloud(groups, 6)
    clear = _clear_by_date(area, groups, cand, reader, cache_dir)
    gap = {d: abs((date.fromisoformat(d) - target).days) for d in cand}
    ranked = sorted(cand, key=lambda d: (-clear[d], gap[d]))
    best = ranked[0]
    note = None
    if clear[best] < COMPOSITE_BELOW and len(ranked) >= 2:
        use = ranked[:3]
        comp = composite_median([reader(groups[d], area.grid, ALL_BANDS, cache_dir) for d in use])
        frac = clear_fraction(comp["scl"], area.mask)
        stack = comp
        used = [s for d in use for s in groups[d]]
        composite = True
        note = f"{'Before' if side == 'before' else 'After'} image is a median of {len(use)} scenes (cloudy period)."
    else:
        frac = clear[best]
        stack = reader(groups[best], area.grid, ALL_BANDS, cache_dir)
        used = list(groups[best])
        composite = False
    if frac < MIN_CLEAR:
        _fail(area, target, side, cache_dir, search, reader)
    return {
        "requested": target.isoformat(), "date": best, "scenes": [s.id for s in used],
        "composite": composite, "clear_fraction": round(frac, 2),
        "_stack": stack, "_used": used, "_note": note, "_date": date.fromisoformat(best),
    }


def run_compare(aoi: AOI, before: date, after: date, focus: str, window_days: int, cache_dir: Path,
                store: RunStore, *, search=search_scenes, reader=read_date_stack) -> dict:
    if after <= before:
        raise BadInput("after must be later than before; swap the two dates.")
    if focus not in FOCUSES:
        raise BadInput(f"focus must be one of {', '.join(FOCUSES)}.")
    window_days = max(1, min(int(window_days), 90))
    area = _Area(aoi)
    sides = {
        "before": _pick_side(area, before, "before", window_days, cache_dir, search, reader),
        "after": _pick_side(area, after, "after", window_days, cache_dir, search, reader),
    }
    stats = change_stats(sides["before"]["_stack"], sides["after"]["_stack"], area.mask, area.pixel_ha)
    masks = stats.pop("masks")
    if focus == "auto":
        focus = pick_focus(stats)
    d1, d2 = sides["before"]["_date"], sides["after"]["_date"]
    headline = compare_headline(stats, focus, d1, d2)
    notes = compare_notes(area.res, stats, [s["_note"] for s in sides.values() if s["_note"]])

    run_id, rdir = store.new("compare")
    img_b, img_a = _rgb_image(sides["before"]["_stack"]), _rgb_image(sides["after"]["_stack"])
    grey = img_a.convert("L").point(lambda v: int(v * 0.55)).convert("RGB")
    change = overlay(grey, masks, focus)
    files = {k: str((rdir / n).resolve()) for k, n in
             (("panel", "panel.png"), ("before", "before.jpg"), ("after", "after.jpg"),
              ("overlay", "overlay.png"))}
    _save_jpeg(img_b, Path(files["before"]))
    _save_jpeg(img_a, Path(files["after"]))
    overlay(Image.new("RGBA", img_a.size, (0, 0, 0, 0)), masks, focus).save(files["overlay"], "PNG")
    side_notes = [s["_note"] for s in sides.values() if s["_note"]]
    pct = round(stats["valid_fraction"] * 100)
    # the bundled panel font has no "≈" or minus glyph
    lines = [headline.replace("≈ ", "~").replace("\u2212", "-"),
             f"Sentinel-2 · {area.res} m · {pct}% clear pixels · confidence {stats['confidence']}"]
    if side_notes:
        lines.append(" ".join(side_notes))
    panel(img_b, img_a, change, aoi.name, lines, legend_for(focus),
          captions=(f"Before · {fmt_date(d1)}", f"After · {fmt_date(d2)}", "Change")
          ).save(files["panel"], "PNG")

    used = sides["before"]["_used"] + sides["after"]["_used"]
    result = {
        "run_id": run_id, "kind": "compare", "place": _place(aoi),
        "before": {k: v for k, v in sides["before"].items() if not k.startswith("_")},
        "after": {k: v for k, v in sides["after"].items() if not k.startswith("_")},
        "resolution_m": area.res, "focus": focus,
        "stats": {k: stats[k] for k in ("vegetation", "burn", "water")},
        "valid_fraction": stats["valid_fraction"], "confidence": stats["confidence"],
        "thresholds": stats["thresholds"], "headline": headline, "files": files,
        "provenance": _provenance(used), "notes": notes,
    }
    _write_result(rdir / "result.json", result)
    return result


# ---------------------------------------------------------------- timelapse

def _periods(start: date, end: date, step: str) -> list[str]:
    out: list[str] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(_period_of(f"{y:04d}-{m:02d}-01", step))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return list(dict.fromkeys(out))


def _period_of(iso: str, step: str) -> str:
    y, m = iso[:4], int(iso[5:7])
    if step == "month":
        return f"{y}-{m:02d}"
    if step == "quarter":
        return f"{y}-Q{(m - 1) // 3 + 1}"
    return y


def _metric_value(metric: str, stack: dict, area: _Area) -> float | None:
    ok = area.mask & valid_mask(stack["scl"])
    if metric == "ndvi":
        v = ndvi(_refl64(stack["red"]), _refl64(stack["nir"]))
    elif metric == "burn":
        v = nbr(_refl64(stack["nir"]), _refl64(stack["swir22"]))
    else:
        v = mndwi(_refl64(stack["green"]), _refl64(stack["swir16"]))
    ok &= np.isfinite(v)
    if not ok.any():
        return None
    if metric == "water":
        return round(float((v[ok] > 0).sum()) * area.pixel_ha, 2)
    return round(float(v[ok].mean()), 3)


def _label(period: str, step: str) -> str:
    if step == "month":
        return f"{date(int(period[:4]), int(period[5:7]), 1):%b %Y}"
    return period


def run_timelapse(aoi: AOI, start: date, end: date, step: str, metric: str, cache_dir: Path,
                  store: RunStore, *, search=search_scenes, reader=read_date_stack) -> dict:
    if end <= start:
        raise BadInput("end must be after start.")
    if step not in ("month", "quarter", "year"):
        raise BadInput("step must be month, quarter or year.")
    if metric not in METRICS:
        raise BadInput(f"metric must be one of {', '.join(METRICS)}.")
    periods = _periods(start, end, step)
    if len(periods) > MAX_FRAMES:
        raise BadInput(f"That range gives {len(periods)} frames; the limit is {MAX_FRAMES}. "
                       "Use a larger step or a shorter range.")
    area = _Area(aoi)
    scenes = search(aoi, start, min(end, date.today()), cache_dir, max_items=1000)
    groups = group_by_date(scenes)
    by_period: dict[str, dict[str, list[Scene]]] = {p: {} for p in periods}
    for d, g in groups.items():
        by_period[_period_of(d, step)][d] = g
    extra, metric_label = METRICS[metric]
    bands = ["red", "green", "blue", "scl", *extra]

    series, frames, used = [], [], []
    for p in periods:
        pg = by_period[p]
        cand = _lowest_cloud(pg, 3)
        clear = _clear_by_date(area, pg, cand, reader, cache_dir)
        best = sorted(cand, key=lambda d: (-clear[d], d))[:1]
        if not best or clear[best[0]] < MIN_CLEAR_TIMELAPSE:
            series.append({"period": p, "date": None, "value": None})
            continue
        d = best[0]
        stack = reader(pg[d], area.grid, bands, cache_dir)
        series.append({"period": p, "date": d, "value": _metric_value(metric, stack, area)})
        frames.append(annotate_frame(_rgb_image(stack), fmt_date(date.fromisoformat(d))))
        used.extend(pg[d])
    if not frames:
        raise NoClearScene(
            f"No clear image found between {start.isoformat()} and {end.isoformat()}.",
            suggestions=[], side="range", target=start.isoformat())

    run_id, rdir = store.new("timelapse")
    (rdir / "frames").mkdir()
    frame_paths = []
    for i, f in enumerate(frames):
        fp = rdir / "frames" / f"{i:03d}.jpg"
        _save_jpeg(f, fp)
        frame_paths.append(str(fp.resolve()))
    gif, chart = rdir / "timelapse.gif", rdir / "chart.png"
    save_gif(frames, gif, 700)
    series_chart([(s["period"], s["value"]) for s in series], metric_label, chart)

    vals = [s for s in series if s["value"] is not None]
    gaps = sum(1 for s in series if s["date"] is None)
    unit = f"{step}{'' if len(periods) == 1 else 's'}"
    if len(vals) < 2:
        headline = f"Only one clear image found over {len(periods)} {unit}."
    else:
        lo = min(vals, key=lambda s: s["value"])
        hi = max(vals, key=lambda s: s["value"])
        fmt = (lambda v: f"{fmt_ha(v)} ha") if metric == "water" else (lambda v: f"{v:.3f}")
        name = {"ndvi": "Mean NDVI", "water": "Water area", "burn": "Mean NBR"}[metric]
        headline = (f"{name} ranged from {fmt(lo['value'])} ({_label(lo['period'], step)}) to "
                    f"{fmt(hi['value'])} ({_label(hi['period'], step)}) over {len(periods)} {unit}.")
    notes = [f"Resolution {area.res} m: objects smaller than ~{2 * area.res} m are not visible."]
    if area.res > 10:
        notes.append(f"Large area: analysed at {area.res} m; small changes may be missed.")
    if gaps:
        notes.append(f"{gaps} of {len(periods)} periods had no clear image and are left out of the animation.")
    result = {
        "run_id": run_id, "kind": "timelapse", "place": _place(aoi), "resolution_m": area.res,
        "metric": metric, "series": series, "headline": headline,
        "files": {"gif": str(gif.resolve()), "chart": str(chart.resolve()), "frames": frame_paths},
        "provenance": _provenance(used), "notes": notes,
    }
    _write_result(rdir / "result.json", result)
    return result
