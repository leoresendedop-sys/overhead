"""Human-readable text for results: headlines, notes and legends."""

from __future__ import annotations

from datetime import date

from .render import OVERLAY_COLORS

LABELS = {
    "veg_loss": "Vegetation loss",
    "veg_gain": "Vegetation gain",
    "burn_low": "Low severity",
    "burn_mod": "Moderate severity",
    "burn_high": "High severity",
    "water_gain": "Water gained",
    "water_loss": "Water lost",
}
MINUS = "−"


def fmt_ha(v: float) -> str:
    return f"{round(v):,}" if v >= 10 else f"{v:.1f}"


def fmt_date(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def legend_for(focus: str) -> list[tuple[str, str]]:
    return [(color, LABELS[key]) for key, color in OVERLAY_COLORS[focus]]


def focus_total(stats: dict, focus: str) -> float:
    if focus == "burn":
        return stats["burn"]["burned_ha"]
    if focus == "vegetation":
        return stats["vegetation"]["lost_ha"] + stats["vegetation"]["gained_ha"]
    return stats["water"]["lost_ha"] + stats["water"]["gained_ha"]


def compare_headline(stats: dict, focus: str, d1: date, d2: date) -> str:
    span = f"between {fmt_date(d1)} and {fmt_date(d2)}"
    if focus_total(stats, focus) == 0:
        return f"No significant {focus} change detected {span}"
    if focus == "burn":
        b = stats["burn"]
        high = f" ({fmt_ha(b['high_ha'])} ha high severity)" if b["high_ha"] > 0 else ""
        return f"≈ {fmt_ha(b['burned_ha'])} ha burned{high} {span}"
    if focus == "vegetation":
        v = stats["vegetation"]
        return (f"≈ {fmt_ha(v['lost_ha'])} ha of vegetation lost and "
                f"{fmt_ha(v['gained_ha'])} ha gained {span}")
    w = stats["water"]
    delta = w["net_ha"]
    change = fmt_ha(abs(delta))
    if change in ("0.0", "0"):
        diff = "no net change"
    else:
        diff = f"{'+' if delta > 0 else MINUS}{change} ha"
    return (f"Water area went from {fmt_ha(w['before_ha'])} ha to {fmt_ha(w['after_ha'])} ha "
            f"({diff}) {span}")


def compare_notes(res: int, stats: dict, side_notes: list[str]) -> list[str]:
    notes = [f"Resolution {res} m: objects smaller than ~{2 * res} m are not visible."]
    if res > 10:
        notes.append(f"Large area: analysed at {res} m; small changes may be missed.")
    if stats["confidence"] == "low":
        pct = round(stats["valid_fraction"] * 100)
        notes.append(f"Low confidence: only {pct}% of the area is clear in both images.")
    return notes + side_notes
