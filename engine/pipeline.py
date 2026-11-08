"""The three flows behind the tools: scene listing, before/after comparison, timelapse."""

from __future__ import annotations

import calendar
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from PIL import Image

from .analysis import (
    COMPOSITE_BELOW,
    change_stats,
    clear_fraction,
    DNBR_MOD,
    is_glinted,
    reflectance64,
    water_mask,
    composite_median,
    nbr,
    ndvi,
    pick_focus,
    valid_mask,
)
from .aoi import AOI, resolution_for
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
from .stac import COLLECTION, STAC_URL, Scene, group_by_date, search_scenes, truncation

FIRST_DATE = date(2017, 3, 28)  # Sentinel-2 L2A global coverage starts around here
FOCUSES = ("auto", "vegetation", "burn", "water")
MIN_CLEAR = 0.30
SUGGEST_CLEAR = 0.60
STOP_CLEAR = 0.95
MIN_CLEAR_TIMELAPSE = 0.60
MAX_FRAMES = 24
MAX_SIDE_ITEMS = 500  # STAC items per compare side window (Earth Search returns the newest first)
MAX_PERIOD_ITEMS = 200  # STAC items per timelapse period
WORKERS = 6  # orchestration threads; HTTP reads are bounded by the shared pool in raster
ALL_BANDS = ["blue", "green", "red", "nir", "swir16", "swir22", "scl"]
METRICS = {
    "ndvi": (["nir"], "Mean NDVI"),
    "water": (["swir16"], "Water area (ha)"),
    "burn": (["nir", "swir22"], "Burned area (ha)"),
}
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")


ROLES = ("point", "start", "end")


def parse_date(s: str, *, field: str, role: str = "point") -> date:
    """YYYY-MM-DD as given, or YYYY-MM. A month is its 15th for a point in time (compare's before
    and after; today at most, for the current month) and the whole month in a range: the 1st as
    its start, the last day as its end, clamped to the dates Sentinel-2 L2A covers (2017-03-28 to
    today)."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    hint = f"{field} '{s}' is not usable: use YYYY-MM-DD or YYYY-MM, or call overhead_scenes."
    m = _DATE_RE.match(str(s).strip())
    if not m:
        raise BadInput(hint)
    y, mo, day = int(m.group(1)), int(m.group(2)), m.group(3)
    try:
        if day or role == "point":
            first = last = date(y, mo, int(day or 15))
            if not day and first > date.today() >= date(y, mo, 1):  # the current month
                first = last = date.today()
        else:
            first, last = date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1])
    except ValueError:
        raise BadInput(hint)
    if first > date.today():
        raise BadInput(f"{field} '{s}' is in the future; Sentinel-2 imagery only exists up to today.")
    if last < FIRST_DATE:
        raise BadInput(f"{field} '{s}' is too early; Sentinel-2 L2A coverage starts on 2017-03-28.")
    return max(first, FIRST_DATE) if role == "start" else min(last, date.today())


# ---------------------------------------------------------------- helpers

class _Area:
    """Grids and masks for one AOI: analysis grid plus a coarse grid for ranking."""

    def __init__(self, aoi: AOI):
        self.aoi = aoi
        self.res = resolution_for(aoi)
        self.grid = grid_for_aoi(aoi, self.res)
        self.mask = aoi_mask(aoi, self.grid)
        self.pixel_ha = self.res * self.res / 10000
        coarse = max(self.res, 60)
        self.cgrid = grid_for_aoi(aoi, coarse)
        self.cmask = aoi_mask(aoi, self.cgrid)


def _place(aoi: AOI) -> dict:
    return {"name": aoi.name, "full_name": aoi.full_name or aoi.name, "bbox": list(aoi.bbox),
            "area_km2": aoi.area_km2, "source": aoi.source}


def _mean_cloud(scenes: list[Scene]) -> float:
    return sum(s.cloud_cover for s in scenes) / len(scenes)


def _lowest_cloud(groups: dict[str, list[Scene]], n: int) -> list[str]:
    return sorted(groups, key=lambda d: (_mean_cloud(groups[d]), d))[:n]


def _nearest_first(groups: dict[str, list[Scene]], target: date, n: int) -> list[str]:
    """Dates nearest the target first; dates over 30 % cloud (scene metadata) go last."""
    def key(d):
        cloud = _mean_cloud(groups[d])
        return (cloud > 30, abs((date.fromisoformat(d) - target).days), cloud, d)
    return sorted(groups, key=key)[:n]


def _clear_by_date(area: _Area, groups, dates, reader, cache_dir, pool=None,
                   glint=False) -> dict[str, tuple[float, np.ndarray, bool]]:
    """(clear fraction, coarse SCL, glinted) per date; concurrent when a pool is given.

    With glint=True swir16 is read too, to flag sun glint or haze over water."""
    bands = ["scl", "swir16"] if glint else ["scl"]

    def one(d):
        st = reader(groups[d], area.cgrid, bands, cache_dir)
        return clear_fraction(st["scl"], area.cmask), st["scl"], glint and is_glinted(st, area.cmask)
    results = list(pool.map(one, dates)) if pool else [one(d) for d in dates]
    return dict(zip(dates, results))


def _rank_clearest(area: _Area, groups, limit, reader, cache_dir, pool=None, batch=2, glint=False,
                   target: date | None = None):
    """Evaluate candidate dates in small batches; stop once one is nearly cloud-free.

    Candidates come nearest the target first when there is one, lowest cloud first otherwise."""
    order = _nearest_first(groups, target, limit) if target else _lowest_cloud(groups, limit)
    out: dict = {}
    for i in range(0, len(order), batch):
        out.update(_clear_by_date(area, groups, order[i:i + batch], reader, cache_dir, pool, glint))
        if any(v[0] >= STOP_CLEAR and not v[2] for v in out.values()):
            break
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
    if end < start:
        raise BadInput("end must be on or after start.")
    area = _Area(aoi)
    found = search(aoi, start, end, cache_dir, sortby="cloud")
    groups = group_by_date(found)
    dates = _lowest_cloud(groups, 3 * max_results)
    with ThreadPoolExecutor(WORKERS) as pool:
        clear = _clear_by_date(area, groups, dates, reader, cache_dir, pool)
    ranked = sorted(dates, key=lambda d: (-clear[d][0], d))[:max_results]
    return {
        "place": _place(aoi),
        "dates": [{"date": d, "aoi_clear_fraction": round(clear[d][0], 2), "tiles": len(groups[d])}
                  for d in ranked],
        "notes": [f"The search matched {cut[1]} scenes; only the least cloudy {cut[0]} (by scene "
                  "metadata) were ranked." for cut in [truncation(found)] if cut],
    }


# ---------------------------------------------------------------- compare

def _suggest(area: _Area, target: date, cache_dir, search, reader, pool) -> list[dict]:
    end = min(target + timedelta(days=90), date.today())
    groups = group_by_date(search(area.aoi, target - timedelta(days=90), end, cache_dir,
                                  sortby="cloud"))
    dates = _lowest_cloud(groups, 8)
    clear = {d: v[0] for d, v in _clear_by_date(area, groups, dates, reader, cache_dir, pool).items()}
    ranked = sorted((d for d in dates if clear[d] > 0), key=lambda d: (-clear[d], d))
    good = [d for d in ranked if clear[d] >= SUGGEST_CLEAR]
    return [{"date": d, "clear_fraction": round(clear[d], 2)} for d in (good or ranked)[:3]]


def _fail(area: _Area, target: date, side: str, cache_dir, search, reader, pool):
    sugg = _suggest(area, target, cache_dir, search, reader, pool)
    msg = f"No clear image near {target.isoformat()} ({side})."
    if sugg:
        msg += " Clearest nearby dates: " + ", ".join(
            f"{s['date']} ({round(s['clear_fraction'] * 100)}% clear)" for s in sugg
        ) + "."
    else:
        msg += " No usable images were found within 90 days."
    raise NoClearScene(msg, suggestions=sugg, side=side, target=target.isoformat())


def _side_windows(before: date, after: date, window_days: int) -> dict[str, tuple[date, date]]:
    """Each side's search window: +-window_days around its date, split at the midpoint so the two
    never overlap (a scene can then never serve as both before and after, or in reverse order)."""
    w = timedelta(days=window_days)
    mid = before + timedelta(days=(after - before).days // 2)
    return {"before": (before - w, min(before + w, mid)),
            "after": (max(after - w, mid + timedelta(days=1)), min(after + w, date.today()))}


def _rank_side(area: _Area, target: date, side: str, window: tuple[date, date], cache_dir, search,
               reader, pool) -> dict:
    """Search one side's window and rank it with coarse SCL only; decides single scene vs composite."""
    scenes = search(area.aoi, window[0], window[1], cache_dir, max_items=MAX_SIDE_ITEMS)
    cut = truncation(scenes)
    note = (f"{side.capitalize()}: the search from {window[0].isoformat()} to {window[1].isoformat()} "
            f"matched {cut[1]} scenes; only the newest {cut[0]} were considered." if cut else None)
    groups = group_by_date(scenes)
    if not groups:
        _fail(area, target, side, cache_dir, search, reader, pool)
    clear = _rank_clearest(area, groups, 6, reader, cache_dir, pool, target=target)
    gap = {d: abs((date.fromisoformat(d) - target).days) for d in clear}

    def rank(d):  # the nearest nearly cloud-free date; failing that, the clearest
        c = clear[d][0]
        return (0, gap[d], -c) if c >= STOP_CLEAR else (1, -c, gap[d])
    ranked = sorted(clear, key=rank)
    best = ranked[0]
    use = [best]
    est = clear[best][0]
    if est < COMPOSITE_BELOW and len(ranked) >= 2:
        use = ranked[:3]
        union = np.zeros_like(clear[best][1], dtype=bool)
        for d in use:
            union |= valid_mask(clear[d][1])
        est = float((union & area.cmask).sum()) / max(1, int(area.cmask.sum()))
    if est < MIN_CLEAR:  # fail before any full-resolution read
        _fail(area, target, side, cache_dir, search, reader, pool)
    return {"side": side, "target": target, "best": best, "use": use, "groups": groups,
            "coarse": clear[best][0], "search_note": note}


def _load_side(area: _Area, plan: dict, stacks: dict) -> dict:
    use, groups, best, side = plan["use"], plan["groups"], plan["best"], plan["side"]
    composite = len(use) > 1
    if composite:
        stack = composite_median([stacks[d] for d in use])
        frac = clear_fraction(stack["scl"], area.mask)
        label = "Before" if side == "before" else "After"
        note = f"{label} image is a median of {len(use)} scenes (cloudy period)."
    else:
        stack, frac, note = stacks[best], plan["coarse"], None
    used = [s for d in use for s in groups[d]]
    return {"requested": plan["target"].isoformat(), "date": best, "scenes": [s.id for s in used],
            "composite": composite, "clear_fraction": round(frac, 2),
            "_stack": stack, "_used": used, "_note": note, "_date": date.fromisoformat(best)}


def run_compare(aoi: AOI, before: date, after: date, focus: str, window_days: int, cache_dir: Path,
                store: RunStore, *, search=search_scenes, reader=read_date_stack) -> dict:
    if after <= before:
        raise BadInput("after must be later than before; swap the two dates.")
    if focus not in FOCUSES:
        raise BadInput(f"focus must be one of {', '.join(FOCUSES)}.")
    if isinstance(window_days, bool) or not isinstance(window_days, int) or not 1 <= window_days <= 120:
        raise BadInput("window_days must be a whole number of days between 1 and 120.")
    area = _Area(aoi)
    windows = _side_windows(before, after, window_days)
    with ThreadPoolExecutor(2) as side_pool, ThreadPoolExecutor(WORKERS) as pool:
        futs = [side_pool.submit(_rank_side, area, t, side, windows[side], cache_dir, search, reader,
                                 pool)
                for t, side in ((before, "before"), (after, "after"))]
        plans = []
        for f in futs:  # before's failure is reported first
            plans.append(f.result())
        if max(plans[0]["use"]) >= min(plans[1]["use"]):  # final guard, before any full read
            raise NoClearScene(
                f"The clear images found for before ({', '.join(plans[0]['use'])}) are not earlier "
                f"than those for after ({', '.join(plans[1]['use'])}), so no change can be "
                "measured. Choose dates further apart, or call overhead_scenes to pick them.",
                suggestions=[], side="both", target=before.isoformat())
        # full-resolution reads of both sides at once (the readers share one HTTP pool)
        jobs = [(p["side"], d) for p in plans for d in p["use"]]
        reads = [pool.submit(reader, plans[0 if side == "before" else 1]["groups"][d], area.grid,
                             ALL_BANDS, cache_dir) for side, d in jobs]
        got = {job: r.result() for job, r in zip(jobs, reads)}
    sides = {p["side"]: _load_side(area, p, {d: got[(p["side"], d)] for d in p["use"]}) for p in plans}
    for side, s in sides.items():
        if s["clear_fraction"] < MIN_CLEAR:
            with ThreadPoolExecutor(WORKERS) as pool:
                _fail(area, plans[0 if side == "before" else 1]["target"], side, cache_dir, search,
                      reader, pool)
    stats = change_stats(sides["before"]["_stack"], sides["after"]["_stack"], area.mask, area.pixel_ha)
    masks = stats.pop("masks")
    if focus == "auto":
        focus = pick_focus(stats)
    d1, d2 = sides["before"]["_date"], sides["after"]["_date"]
    headline = compare_headline(stats, focus, d1, d2)
    side_notes = [s["_note"] for s in sides.values() if s["_note"]]
    notes = compare_notes(area.res, stats, side_notes)
    notes += [p["search_note"] for p in plans if p["search_note"]]
    for s in sides.values():
        if is_glinted(s["_stack"], area.mask):
            notes.append(f"Sun glint or smoke haze over water on {s['date']}: "
                         "water figures for that date are likely underestimated.")

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
    pct = round(stats["valid_fraction"] * 100)
    lines = [headline,
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


def _period_bounds(period: str, step: str) -> tuple[date, date]:
    """First and last day of a period ("2024-06", "2024-Q2" or "2024")."""
    y = int(period[:4])
    if step == "month":
        first = last = int(period[5:7])
    elif step == "quarter":
        first = 3 * (int(period[-1]) - 1) + 1
        last = first + 2
    else:
        first, last = 1, 12
    return date(y, first, 1), date(y, last, calendar.monthrange(y, last)[1])


def _period_of(iso: str, step: str) -> str:
    y, m = iso[:4], int(iso[5:7])
    if step == "month":
        return f"{y}-{m:02d}"
    if step == "quarter":
        return f"{y}-Q{(m - 1) // 3 + 1}"
    return y


def _metric_value(metric: str, stack: dict, area: _Area, first: dict | None = None) -> float | None:
    """ndvi: mean over valid AOI pixels; water: hectares; burn: hectares with dNBR >= 0.27 vs `first`."""
    ok = area.mask & valid_mask(stack["scl"])
    if metric == "burn":
        if first is None:
            return 0.0
        ok &= valid_mask(first["scl"])
        d = nbr(reflectance64(first["nir"]), reflectance64(first["swir22"])) - \
            nbr(reflectance64(stack["nir"]), reflectance64(stack["swir22"]))
        ok &= np.isfinite(d)
        if not ok.any():
            return None
        with np.errstate(invalid="ignore"):
            return round(float((np.round(d[ok], 9) >= DNBR_MOD).sum()) * area.pixel_ha, 2)
    if metric == "water":
        if not ok.any():
            return None
        return round(float(water_mask(stack, ok).sum()) * area.pixel_ha, 2)
    v = ndvi(reflectance64(stack["red"]), reflectance64(stack["nir"]))
    ok &= np.isfinite(v)
    if not ok.any():
        return None
    return round(float(v[ok].mean()), 3)


def _label(period: str, step: str) -> str:
    if step == "month":
        return f"{date(int(period[:4]), int(period[5:7]), 1):%b %Y}"
    return period


def timelapse_headline(series: list[dict], metric: str, step: str, n_periods: int) -> str:
    """One-sentence summary. Glint-flagged points are left out of the extremes when at least two
    clean points remain; otherwise all points are used and the caveat is appended."""
    vals = [s for s in series if s["value"] is not None]
    unit = f"{step}{'' if n_periods == 1 else 's'}"
    if len(vals) < 2:
        return f"Only one clear image found over {n_periods} {unit}."
    clean = [s for s in vals if not s.get("flag")]
    caveat = ""
    if len(clean) >= 2:
        vals = clean
    elif len(clean) < len(vals):
        caveat = " (some dates affected by sun glint or haze)"
    if metric == "burn":
        hi = max(vals, key=lambda s: s["value"])
        if hi["value"] > 0:
            return (f"Burned area reached {fmt_ha(hi['value'])} ha ({_label(hi['period'], step)}) "
                    f"over {n_periods} {unit}.{caveat}")
        return f"No burned area detected over {n_periods} {unit}.{caveat}"
    lo = min(vals, key=lambda s: s["value"])
    hi = max(vals, key=lambda s: s["value"])
    fmt = (lambda v: f"{fmt_ha(v)} ha") if metric == "water" else (lambda v: f"{v:.3f}")
    name = "Water area" if metric == "water" else "Mean NDVI"
    return (f"{name} ranged from {fmt(lo['value'])} ({_label(lo['period'], step)}) to "
            f"{fmt(hi['value'])} ({_label(hi['period'], step)}) over {n_periods} {unit}.{caveat}")


def run_timelapse(aoi: AOI, start: date, end: date, step: str, metric: str, cache_dir: Path,
                  store: RunStore, *, search=search_scenes, reader=read_date_stack) -> dict:
    if end < start:
        raise BadInput("end must be on or after start.")
    if step not in ("month", "quarter", "year"):
        raise BadInput("step must be month, quarter or year.")
    if metric not in METRICS:
        raise BadInput(f"metric must be one of {', '.join(METRICS)}.")
    periods = _periods(start, end, step)
    if len(periods) > MAX_FRAMES:
        raise BadInput(f"That range gives {len(periods)} frames; the limit is {MAX_FRAMES}. "
                       "Use a larger step or a shorter range.")
    area = _Area(aoi)
    last = min(end, date.today())

    def search_period(p):  # one search per period: a single capped search loses the oldest ones
        first_day, last_day = _period_bounds(p, step)
        lo, hi = max(first_day, start), min(last_day, last)
        return search(aoi, lo, hi, cache_dir, max_items=MAX_PERIOD_ITEMS) if lo <= hi else []
    with ThreadPoolExecutor(WORKERS) as pool:
        found = dict(zip(periods, pool.map(search_period, periods)))
    by_period: dict[str, dict[str, list[Scene]]] = {
        p: {d: g for d, g in group_by_date(found[p]).items() if _period_of(d, step) == p}
        for p in periods}
    cut = [p for p in periods if truncation(found[p])]
    extra, metric_label = METRICS[metric]
    bands = ["red", "green", "blue", "scl", *extra]

    def best_of(p):
        pg = by_period[p]
        if not pg:
            return None
        clear = _rank_clearest(area, pg, 3, reader, cache_dir, batch=1, glint=metric == "water")
        ok = [k for k in clear if clear[k][0] >= MIN_CLEAR_TIMELAPSE]
        if not ok:
            return None
        d = max(ok, key=lambda k: (not clear[k][2], clear[k][0], k))  # clean dates first
        return d, round(clear[d][0], 2), clear[d][2]

    with ThreadPoolExecutor(WORKERS) as pool:
        chosen = list(pool.map(best_of, periods))
        reads = {p: pool.submit(reader, by_period[p][c[0]], area.grid, bands, cache_dir)
                 for p, c in zip(periods, chosen) if c}
        stacks = {p: f.result() for p, f in reads.items()}

    series, frames, used, first, glinted = [], [], [], None, []
    for p, c in zip(periods, chosen):
        if not c:
            series.append({"period": p, "date": None, "value": None, "clear": None})
            continue
        d, frac, glint = c
        stack = stacks[p]
        first = first or stack
        point = {"period": p, "date": d, "value": _metric_value(metric, stack, area, first), "clear": frac}
        if glint:
            point["flag"] = "glint"
            glinted.append(d)
        series.append(point)
        frames.append(annotate_frame(_rgb_image(stack), fmt_date(date.fromisoformat(d))))
        used.extend(by_period[p][d])
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
    series_chart([(s["period"], s["value"]) for s in series], metric_label, chart,
                 flags=[bool(s.get("flag")) for s in series])

    gaps = sum(1 for s in series if s["date"] is None)
    headline = timelapse_headline(series, metric, step, len(periods))
    notes = [f"Resolution {area.res} m: objects smaller than ~{2 * area.res} m are not visible."]
    if area.res > 10:
        notes.append(f"Large area: analysed at {area.res} m; small changes may be missed.")
    if gaps:
        notes.append(f"{gaps} of {len(periods)} periods had no clear image and are left out of the animation.")
    if glinted:
        notes.append(f"Sun glint or smoke haze over water on {', '.join(glinted)}: "
                     "water area there is likely underestimated.")
    if cut:
        notes.append(f"Searches for {len(cut)} of {len(periods)} periods matched more than "
                     f"{MAX_PERIOD_ITEMS} scenes; only the newest {MAX_PERIOD_ITEMS} of each were "
                     f"considered ({', '.join(cut)}).")
    result = {
        "run_id": run_id, "kind": "timelapse", "place": _place(aoi), "resolution_m": area.res,
        "metric": metric, "series": series, "headline": headline,
        "files": {"gif": str(gif.resolve()), "chart": str(chart.resolve()), "frames": frame_paths},
        "provenance": _provenance(used), "notes": notes,
    }
    _write_result(rdir / "result.json", result)
    return result
