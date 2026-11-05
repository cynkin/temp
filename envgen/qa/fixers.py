"""Deterministic, bounded fixes: metric issue codes -> spec patch operations.

Rules are deliberately conservative: each fix is damped (half-strength of what
would close the gap in one step) to avoid oscillation, and every parameter has
hard bounds. Fixes that only touch ``post.*`` can be applied without re-rendering.
Issues with no safe automatic fix (composition, low detail, palette mismatch)
are returned as ``unresolved`` so a critic (vision model or the Claude Code QA
agent) can make the artistic call.
"""
from __future__ import annotations

from ..spec.schema import get_path

BOUNDS: dict[str, tuple[float, float]] = {
    "post.exposure": (-1.5, 1.5),
    "post.contrast": (0.8, 1.35),
    "post.saturation": (0.5, 1.3),
    "post.lift": (-0.03, 0.05),
    "post.gain": (0.8, 1.2),
    "post.vignette": (0.0, 0.6),
    "post.grain": (0.0, 0.06),
    "post.bloom.intensity": (0.0, 0.4),
    "post.bloom.threshold": (0.5, 1.2),
    "lighting.exposure": (-2.0, 2.5),
    "lighting.world.strength": (0.2, 8.0),
    "lighting.sun.strength": (0.5, 20.0),
    "lighting.fixtures.energy": (5.0, 200.0),
    "atmosphere.density": (0.0, 0.12),
    "atmosphere.anisotropy": (0.0, 0.85),
}

# post-only paths can be re-graded from the existing raw render
POST_ONLY_PREFIX = "post."


def _sev(issues: list[dict], code: str) -> float:
    return max((float(i.get("severity", 0)) for i in issues if i.get("code") == code), default=0.0)


OPPOSITES = [("too_dark", "too_bright"), ("low_contrast", "high_contrast"), ("too_warm", "too_cool"),
             ("oversaturated", "undersaturated"), ("too_hazy", "too_clear")]


def _resolve(issues: list[dict], min_severity: float) -> list[dict]:
    """Drop minor issues and resolve contradictory pairs (different sub-metrics can disagree) by net severity."""
    issues = [i for i in issues if float(i.get("severity", 0)) >= min_severity]
    for a, b in OPPOSITES:
        sa, sb = _sev(issues, a), _sev(issues, b)
        if sa and sb:
            loser = b if sa >= sb else a
            net = abs(sa - sb)
            issues = [dict(i, severity=net) if i["code"] in (a, b) else i for i in issues if i["code"] != loser]
            issues = [i for i in issues if not (i["code"] in (a, b) and net < min_severity)]
    return issues


def propose_fixes(issues: list[dict], spec: dict, deltas: dict | None = None, damping: float = 0.5,
                  min_severity: float = 0.12) -> dict:
    """Return {"ops": [...], "reasons": [...], "unresolved": [...]} for the given metric issues."""
    ops: list[dict] = []
    reasons: list[str] = []
    issues = _resolve(issues, min_severity)
    codes = {i["code"] for i in issues}

    # ---- exposure: severity -> stops; big gaps go to the render exposure (re-render), small ones to the grade
    if "too_dark" in codes or "too_bright" in codes:
        sign = 1.0 if "too_dark" in codes else -1.0
        sev = _sev(issues, "too_dark" if sign > 0 else "too_bright")
        stops = sign * damping * (0.35 + 1.4 * sev)
        if abs(stops) > 0.45:
            ops.append({"op": "add", "path": "lighting.exposure", "value": round(stops, 3)})
            reasons.append(f"exposure {'+' if sign > 0 else ''}{stops:.2f} EV at render (large luminance gap)")
        else:
            ops.append({"op": "add", "path": "post.exposure", "value": round(stops, 3)})
            reasons.append(f"post exposure {stops:+.2f} EV")
    if "clipped_highlights" in codes and "too_dark" not in codes:
        ops.append({"op": "add", "path": "post.bloom.threshold", "value": 0.05})
        ops.append({"op": "add", "path": "post.exposure", "value": -0.1 * damping * 2})
        reasons.append("clipped highlights: lower exposure slightly, raise bloom threshold")
    if "crushed_blacks" in codes:
        s = _sev(issues, "crushed_blacks")
        ops.append({"op": "add", "path": "post.lift", "value": [0.008 * s + 0.004] * 3})
        reasons.append("crushed blacks: lift shadows")

    # ---- contrast
    # global contrast only; low *local* contrast is a texture/detail problem (not fixable by a tone curve)
    if "low_contrast" in codes:
        s = _sev(issues, "low_contrast")
        ops.append({"op": "add", "path": "post.contrast", "value": round(0.04 + 0.1 * s * damping, 3)})
        reasons.append("low contrast: increase grade contrast")
    elif "high_contrast" in codes:
        s = _sev(issues, "high_contrast")
        ops.append({"op": "add", "path": "post.contrast", "value": -round(0.04 + 0.1 * s * damping, 3)})
        reasons.append("high contrast: reduce grade contrast")

    # ---- colour
    if "too_warm" in codes or "too_cool" in codes:
        sign = 1.0 if "too_cool" in codes else -1.0
        s = _sev(issues, "too_cool" if sign > 0 else "too_warm")
        k = 0.03 * (0.5 + s) * damping * 2
        ops.append({"op": "add", "path": "post.gain", "value": [round(sign * k, 4), 0.0, round(-sign * k, 4)]})
        reasons.append(f"white balance shift {'warmer' if sign > 0 else 'cooler'}")
    if "oversaturated" in codes:
        ops.append({"op": "add", "path": "post.saturation", "value": -round(0.06 + 0.1 * _sev(issues, "oversaturated")
                                                                             * damping, 3)})
        reasons.append("oversaturated: reduce saturation")
    elif "undersaturated" in codes:
        ops.append({"op": "add", "path": "post.saturation", "value": round(0.06 + 0.1 * _sev(issues, "undersaturated")
                                                                            * damping, 3)})
        reasons.append("undersaturated: increase saturation")

    # ---- atmosphere (needs re-render)
    if "too_hazy" in codes:
        ops.append({"op": "scale", "path": "atmosphere.density", "value": round(1 - 0.35 * damping * 2 *
                                                                                   max(0.4, _sev(issues, "too_hazy")),
                                                                                   3)})
        reasons.append("too hazy: thin the fog")
    elif "too_clear" in codes and get_path(spec, "atmosphere.enabled", True):
        ops.append({"op": "scale", "path": "atmosphere.density", "value": round(1 + 0.4 * damping * 2 *
                                                                                   max(0.4, _sev(issues, "too_clear")),
                                                                                   3)})
        reasons.append("too clear: thicken the fog")

    # ---- lighting structure (needs re-render): let the key light dominate the ambient fill
    if "flat_lighting" in codes:
        ops.append({"op": "scale", "path": "lighting.world.strength", "value": 0.8})
        if get_path(spec, "lighting.sun"):
            ops.append({"op": "scale", "path": "lighting.sun.strength", "value": 1.15})
        ops.append({"op": "add", "path": "post.vignette", "value": 0.05})
        reasons.append("flat lighting: reduce ambient fill, strengthen key, deepen vignette")

    if "excessive_detail" in codes:
        ops.append({"op": "scale", "path": "post.grain", "value": 0.6})
        reasons.append("excessive high-frequency detail: reduce grain")

    unresolved = [i for i in issues if i["code"] in {"vp_offcenter", "low_detail", "palette_mismatch",
                                                     "low_local_contrast"}]
    return {"ops": _merge(ops), "reasons": reasons, "unresolved": unresolved}


def _merge(ops: list[dict]) -> list[dict]:
    """Collapse repeated add/scale ops on the same path."""
    out: dict[tuple[str, str], dict] = {}
    for op in ops:
        key = (op["op"], op["path"])
        if key in out and op["op"] in ("add", "scale"):
            a, b = out[key]["value"], op["value"]
            if isinstance(a, list):
                out[key]["value"] = [x + y if op["op"] == "add" else x * y for x, y in zip(a, b)]
            else:
                out[key]["value"] = a + b if op["op"] == "add" else a * b
        else:
            out[key] = dict(op)
    return list(out.values())


def needs_rerender(ops: list[dict]) -> bool:
    return any(not op["path"].startswith(POST_ONLY_PREFIX) for op in ops)
