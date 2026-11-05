"""Objective, numeric artistic-QA scoring of a render against a target.

The *target* is normally an aggregate of reference profiles
(:func:`envgen.vision.profile.aggregate_profiles`), where every scalar leaf is
``{"mean", "std", "min", "max"}``.  A single (non-aggregated) profile is also
accepted as a target; its scalars are then treated as means with zero std (the
minimum band half-widths below keep that usable).

Every sub-metric gets a tolerance band ``[mean - hw, mean + hw]`` with
``hw = max(k * std, abs_min, rel_min * |mean|)``.  Inside the band the
sub-score is 1.0; outside it decays as ``exp(-excess / (decay * hw))`` (for
scale-like metrics in ``RATIO_LOW`` the low side is further capped at
``measured / lo``).
Criterion scores are weighted geometric means of their sub-scores (one badly
failing sub-metric drags the criterion down), and ``overall`` is the weighted
arithmetic mean of the criteria.

Pure and deterministic; the only I/O is the optional CLI.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

from envgen.vision.profile import palette_distance

__all__ = ["score_render", "DEFAULT_WEIGHTS", "SPECS", "tolerance_score"]

DEFAULT_WEIGHTS: dict[str, float] = {
    "exposure": 0.20,
    "contrast": 0.15,
    "color_grade": 0.15,
    "atmosphere": 0.10,
    "detail": 0.15,
    "lighting_structure": 0.15,
    "composition": 0.10,
}

K_STD = 1.0  # band half-width in target standard deviations
DECAY = 1.5  # outside the band, score = exp(-excess / (DECAY * half_width))

# (criterion, metric path, sub-weight, abs_min_hw, rel_min_hw, side,
#  code_if_low, code_if_high, description)
# side: "both" | "upper" (only too-high is penalised) | "lower"
SPECS: list[tuple] = [
    ("exposure", "luminance.mean", 1.0, 0.03, 0.15, "both", "too_dark", "too_bright", "mean luma"),
    ("exposure", "luminance.median", 1.0, 0.03, 0.15, "both", "too_dark", "too_bright", "median luma"),
    ("exposure", "luminance.p98", 0.7, 0.05, 0.08, "both", "too_dark", "too_bright", "highlight level (p98 luma)"),
    ("contrast", "contrast.rms", 1.0, 0.02, 0.12, "both", "low_contrast", "high_contrast", "RMS contrast"),
    ("contrast", "contrast.dynamic_range", 1.0, 0.05, 0.10, "both", "low_contrast", "high_contrast", "dynamic range (p98-p02)"),
    ("contrast", "contrast.local_contrast", 1.0, 0.004, 0.15, "both", "low_local_contrast", "high_contrast", "local contrast"),
    ("color_grade", "color.warmth", 1.0, 0.015, 0.0, "both", "too_cool", "too_warm", "warmth (R-B)"),
    ("color_grade", "color.tint", 0.7, 0.012, 0.0, "both", "palette_mismatch", "palette_mismatch", "green/magenta tint"),
    ("color_grade", "color.saturation_mean", 1.0, 0.03, 0.12, "both", "undersaturated", "oversaturated", "saturation"),
    ("color_grade", "color.palette_distance", 1.0, 4.0, 0.25, "upper", None, "palette_mismatch", "palette distance (ΔE)"),
    ("atmosphere", "haze.dark_channel_mean", 1.0, 0.012, 0.20, "both", "too_clear", "too_hazy", "dark channel (haze)"),
    ("atmosphere", "haze.veil", 0.5, 0.02, 0.20, "both", "too_clear", "too_hazy", "veil in bright regions"),
    ("detail", "detail.edge_density", 1.0, 0.006, 0.15, "both", "low_detail", "excessive_detail", "edge density"),
    ("detail", "detail.hf_energy", 1.0, 0.003, 0.15, "both", "low_detail", "excessive_detail", "high-frequency energy"),
    ("lighting_structure", "spatial.center_vs_edge", 1.0, 0.12, 0.10, "both", "flat_lighting", "high_contrast", "centre/edge luma ratio"),
    ("lighting_structure", "extra.luma_grid_cv", 1.0, 0.05, 0.15, "both", "flat_lighting", "high_contrast", "luma variation across 3x3 grid"),
    ("lighting_structure", "clipping.highlights", 0.7, 0.005, 0.50, "upper", None, "clipped_highlights", "clipped highlights fraction"),
    ("lighting_structure", "clipping.shadows", 0.7, 0.03, 0.30, "upper", None, "crushed_blacks", "crushed shadows fraction"),
]

# Scale-like, non-negative metrics: below the band the score is additionally
# capped at measured/lo, so a value collapsing towards 0 (a flat, empty or
# featureless frame) scores ~0 instead of decaying gently.
RATIO_LOW = {
    "contrast.rms",
    "contrast.dynamic_range",
    "contrast.local_contrast",
    "detail.edge_density",
    "detail.hf_energy",
    "extra.luma_grid_cv",
}

VP_NULL_SCORE = 0.25  # composition score when the render has no detectable VP
VP_MIN_HW = 0.10      # min tolerance radius for the VP (normalised units)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _get(d: Any, path: str) -> Any:
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float, np.integer, np.floating)):
        v = float(x)
        return v if math.isfinite(v) else None
    return None


def _target_stats(target: dict, path: str) -> tuple[float, float] | None:
    """(mean, std) of a target leaf; accepts aggregate stats or raw numbers."""
    node = _get(target, path)
    if isinstance(node, dict):
        m, s = _num(node.get("mean")), _num(node.get("std"))
        if m is None:
            return None
        return m, (s or 0.0)
    m = _num(node)
    return (m, 0.0) if m is not None else None


def tolerance_score(
    measured: float,
    mean: float,
    std: float,
    abs_min: float,
    rel_min: float = 0.0,
    side: str = "both",
    k: float = K_STD,
    decay: float = DECAY,
) -> tuple[float, float, float, float]:
    """Smooth band score.  Returns (score, lo, hi, signed_excess)."""
    hw = max(k * std, abs_min, rel_min * abs(mean), 1e-9)
    lo, hi = mean - hw, mean + hw
    if side == "upper":
        lo = -math.inf
    elif side == "lower":
        hi = math.inf
    if measured < lo:
        ex = measured - lo
    elif measured > hi:
        ex = measured - hi
    else:
        ex = 0.0
    score = math.exp(-abs(ex) / (decay * hw))
    return score, lo, hi, ex


def _geo_mean(scores: list[float], weights: list[float]) -> float:
    if not scores:
        return 1.0
    w = np.asarray(weights, dtype=np.float64)
    s = np.clip(np.asarray(scores, dtype=np.float64), 1e-4, 1.0)
    return float(np.exp((w * np.log(s)).sum() / w.sum()))


def _grid_cv(grid: Any) -> float | None:
    try:
        g = np.asarray(grid, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if g.size == 0 or g.dtype == object:
        return None
    return float(g.std() / (g.mean() + 1e-4))


def _measured(render: dict, target: dict, path: str) -> float | None:
    if path == "color.palette_distance":
        rp = _get(render, "color.palette")
        tp = _get(target, "color.palette")
        if not rp or not tp:
            return None
        d = palette_distance(rp, tp)
        return d if math.isfinite(d) else None
    v = _num(_get(render, path))
    if v is None and path == "extra.luma_grid_cv":
        v = _grid_cv(_get(render, "spatial.luma_grid"))
    return v


def _target_for(target: dict, path: str) -> tuple[float, float] | None:
    st = _target_stats(target, path)
    if st is not None:
        return st
    if path == "extra.luma_grid_cv":
        grid = _get(target, "spatial.luma_grid")
        if isinstance(grid, list):
            means = [[c["mean"] if isinstance(c, dict) else c for c in row] for row in grid]
            cv = _grid_cv(means)
            return (cv, 0.0) if cv is not None else None
    if path == "color.palette_distance":
        return (0.0, 0.0)  # single-profile target: ideal distance is 0
    return None


def _fmt(x: float) -> str:
    return f"{x:.4g}"


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #


def score_render(render_profile: dict, target: dict, weights: dict | None = None) -> dict:
    """Score a render profile against a target (aggregate or single profile).

    Returns ``{"scores", "overall", "issues", "deltas"}`` (see module doc).
    Issues carry an extra ``metric`` key naming the sub-metric path.
    For ``vp_offcenter`` with no detected render VP, ``measured`` is -1.0.
    """
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in DEFAULT_WEIGHTS})

    sub: dict[str, list[tuple[float, float]]] = {c: [] for c in DEFAULT_WEIGHTS}
    issues: list[dict] = []
    deltas: dict[str, float] = {}

    for crit, path, sw, abs_min, rel_min, side, code_lo, code_hi, desc in SPECS:
        m = _measured(render_profile, target, path)
        t = _target_for(target, path)
        if m is None or t is None:
            continue
        mean, std = t
        score, lo, hi, ex = tolerance_score(m, mean, std, abs_min, rel_min, side)
        if path in RATIO_LOW and ex < 0 and lo > 0:
            score = min(score, max(m, 0.0) / lo)
        sub[crit].append((score, sw))
        deltas[path] = round(m - mean, 6)
        if ex != 0.0:
            code = code_lo if ex < 0 else code_hi
            if code is None:
                continue
            direction = "below" if ex < 0 else "above"
            if path == "color.tint":
                direction += " (too magenta)" if ex < 0 else " (too green)"
            issues.append(
                {
                    "code": code,
                    "criterion": crit,
                    "severity": round(1.0 - score, 4),
                    "measured": round(m, 6),
                    "target": [round(lo, 6) if math.isfinite(lo) else None,
                               round(hi, 6) if math.isfinite(hi) else None],
                    "message": f"{desc} {_fmt(m)} is {direction} target band "
                               f"[{_fmt(lo) if math.isfinite(lo) else '-inf'}, "
                               f"{_fmt(hi) if math.isfinite(hi) else 'inf'}] (ref mean {_fmt(mean)})",
                    "metric": path,
                }
            )

    # ---- composition: vanishing point
    tvp = _get(target, "structure.vanishing_point")
    t_mean = t_std = None
    if isinstance(tvp, dict) and isinstance(tvp.get("mean"), list):
        t_mean = tvp["mean"]
        t_std = tvp.get("std") or [0.0, 0.0]
    elif isinstance(tvp, list) and len(tvp) == 2:
        t_mean, t_std = tvp, [0.0, 0.0]
    rvp = _get(render_profile, "structure.vanishing_point")
    if t_mean is not None:
        hw = max(VP_MIN_HW, K_STD * float(np.hypot(*t_std)))
        if isinstance(rvp, list) and len(rvp) == 2:
            d = float(np.hypot(rvp[0] - t_mean[0], rvp[1] - t_mean[1]))
            score, _, hi, ex = tolerance_score(d, 0.0, 0.0, hw, 0.0, "upper")
            sub["composition"].append((score, 1.0))
            deltas["structure.vanishing_point.x"] = round(rvp[0] - t_mean[0], 6)
            deltas["structure.vanishing_point.y"] = round(rvp[1] - t_mean[1], 6)
            deltas["structure.vp_distance"] = round(d, 6)
            if ex > 0:
                issues.append(
                    {
                        "code": "vp_offcenter",
                        "criterion": "composition",
                        "severity": round(1.0 - score, 4),
                        "measured": round(d, 6),
                        "target": [0.0, round(hi, 6)],
                        "message": f"vanishing point ({rvp[0]:.2f}, {rvp[1]:.2f}) is {d:.2f} "
                                   f"from target ({t_mean[0]:.2f}, {t_mean[1]:.2f}); tolerance {hw:.2f}",
                        "metric": "structure.vanishing_point",
                    }
                )
        else:
            sub["composition"].append((VP_NULL_SCORE, 1.0))
            issues.append(
                {
                    "code": "vp_offcenter",
                    "criterion": "composition",
                    "severity": round(1.0 - VP_NULL_SCORE, 4),
                    "measured": -1.0,
                    "target": [0.0, round(hw, 6)],
                    "message": "no vanishing point detected in render "
                               f"(target ({t_mean[0]:.2f}, {t_mean[1]:.2f}))",
                    "metric": "structure.vanishing_point",
                }
            )

    scores = {}
    for crit in DEFAULT_WEIGHTS:
        items = sub[crit]
        scores[crit] = round(_geo_mean([s for s, _ in items], [x for _, x in items]), 4) if items else 1.0
    tot_w = sum(max(0.0, w[c]) for c in scores) or 1.0
    overall = sum(scores[c] * max(0.0, w[c]) for c in scores) / tot_w
    issues.sort(key=lambda i: -i["severity"])
    return {"scores": scores, "overall": round(overall, 4), "issues": issues, "deltas": deltas}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    from envgen.vision.profile import compute_profile

    ap = argparse.ArgumentParser(prog="python -m envgen.qa.metrics",
                                 description="Score a render against a target profile.")
    ap.add_argument("render", help="render image (or a profile .json)")
    ap.add_argument("--target", required=True, help="target JSON (aggregate or single profile)")
    ap.add_argument("--weights", help="JSON dict of criterion weights")
    ap.add_argument("--out", help="write result JSON here (default: stdout)")
    args = ap.parse_args(argv)
    target = json.loads(Path(args.target).read_text())
    if args.render.lower().endswith(".json"):
        prof = json.loads(Path(args.render).read_text())
    else:
        prof = compute_profile(args.render)
    weights = json.loads(args.weights) if args.weights else None
    res = score_render(prof, target, weights)
    txt = json.dumps(res, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(txt + "\n")
    else:
        sys.stdout.write(txt + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
