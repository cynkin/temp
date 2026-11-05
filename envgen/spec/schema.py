"""Scene specification: loading, defaults, validation and patching.

A spec is a plain nested dict (YAML on disk). It is the single source of truth
for a scene: the Blender builder reads only the spec, so the same spec + seed
always reproduces the same scene. The QA loop modifies scenes exclusively by
applying *patches* to the spec, which keeps every iteration auditable.

Patch format (list of ops, a small subset of RFC 6902 plus numeric helpers):
    - {op: set,   path: lighting.sun.strength, value: 4.0}
    - {op: scale, path: atmosphere.density, value: 0.8}       # multiply number
    - {op: add,   path: post.saturation, value: -0.05}        # add to number
    - {op: append, path: story.beats, value: {...}}
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "name": "untitled",
    "seed": 1,
    "description": "",
    "references": [],
    "reference_target": None,
    "layout": {
        "type": "corridor",
        "length": 22.0,
        "width": 2.6,
        "height": 2.85,
        "plenum": 0.65,
        "bay": 3.0,
    },
    "architecture": {},
    "materials": {},
    "lighting": {
        "exposure": 0.0,
        "world": {"color": [0.02, 0.025, 0.03], "strength": 1.0},
        "sun": None,
        "fixtures": [],
        "practicals": [],
    },
    "atmosphere": {
        "enabled": True,
        "density": 0.02,
        "anisotropy": 0.45,
        "color": [0.9, 0.92, 0.95],
        "absorption": 0.0,
        "dust_motes": 400,
    },
    "camera": {
        "shots": [
            {"name": "hero", "position": [0.0, 0.6, 1.6], "target": [0.0, 10.0, 1.4],
             "focal_mm": 20.0, "fstop": 4.0, "focus_distance": 6.0}
        ],
        "sensor_mm": 36.0,
    },
    "post": {
        "exposure": 0.0,
        "contrast": 1.0,
        "saturation": 1.0,
        "lift": [0.0, 0.0, 0.0],
        "gamma": [1.0, 1.0, 1.0],
        "gain": [1.0, 1.0, 1.0],
        "shadow_tint": [0.0, 0.0, 0.0],
        "highlight_tint": [0.0, 0.0, 0.0],
        "bloom": {"threshold": 0.85, "intensity": 0.12, "radius": 0.02},
        "vignette": 0.25,
        "grain": 0.025,
        "chromatic_aberration": 0.0015,
    },
    "story": {"beats": []},
    "render": {
        "engine": "CYCLES",
        "preview": {"resolution": [960, 540], "samples": 48},
        "final": {"resolution": [1920, 1080], "samples": 192},
        "max_bounces": 6,
        "volume_step_rate": 2.0,
        "adaptive_threshold": 0.03,
    },
    "qa": {
        "max_iterations": 4,
        "target_score": 0.82,
        "min_improvement": 0.01,
        "patience": 2,
    },
}


class SpecError(ValueError):
    pass


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_spec(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise SpecError(f"spec not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise SpecError(f"spec root must be a mapping: {path}")
    spec = deep_merge(DEFAULTS, raw)
    spec["_source"] = str(path)
    validate_spec(spec)
    return spec


def _minimal(value: Any, default: Any) -> Any:
    """Recursively drop entries equal to their default (keeps saved specs short and reviewable)."""
    if isinstance(value, dict) and isinstance(default, dict):
        out = {}
        for k, v in value.items():
            if k in default:
                m = _minimal(v, default[k])
                if m is not _SAME:
                    out[k] = m
            else:
                out[k] = v
        return out if out else _SAME
    return _SAME if value == default else value


_SAME = object()


def save_spec(spec: dict, path: str | Path, minimal: bool = True) -> None:
    """Write a spec as YAML. ``minimal`` stores only values that differ from DEFAULTS."""
    clean = {k: v for k, v in spec.items() if not k.startswith("_")}
    if minimal:
        clean = {k: v for k, v in clean.items() if k in ("name", "seed")
                 or _minimal(v, DEFAULTS.get(k, object())) is not _SAME}
        clean = {k: (_minimal(v, DEFAULTS[k]) if isinstance(v, dict) and isinstance(DEFAULTS.get(k), dict) else v)
                 for k, v in clean.items()}
    Path(path).write_text(yaml.safe_dump(clean, sort_keys=False, width=110, default_flow_style=None),
                          encoding="utf-8")


def _norm(x: Any) -> Any:
    """Canonical form for hashing: ints and floats that compare equal hash equal (36 == 36.0)."""
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, dict):
        return {str(k): _norm(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_norm(v) for v in x]
    return x


def spec_hash(spec: dict) -> str:
    clean = _norm({k: v for k, v in spec.items() if not k.startswith("_")})
    return hashlib.sha1(json.dumps(clean, sort_keys=True, default=str).encode()).hexdigest()[:10]


def validate_spec(spec: dict) -> None:
    errors: list[str] = []
    lay = spec.get("layout", {})
    if lay.get("type") not in {"corridor", "room", "tunnel"}:
        errors.append(f"layout.type must be corridor|room|tunnel, got {lay.get('type')!r}")
    for key, lo, hi in (("length", 2.0, 120.0), ("width", 1.2, 40.0), ("height", 2.1, 20.0)):
        v = lay.get(key)
        if not isinstance(v, (int, float)) or not lo <= v <= hi:
            errors.append(f"layout.{key}={v!r} outside plausible range [{lo}, {hi}] m")
    for shot in spec.get("camera", {}).get("shots", []):
        if not 8.0 <= float(shot.get("focal_mm", 0)) <= 200.0:
            errors.append(f"camera shot {shot.get('name')}: focal_mm out of range")
        if not 0.2 <= float(shot.get("position", [0, 0, 0])[2]) <= lay.get("height", 3):
            errors.append(f"camera shot {shot.get('name')}: height outside room")
    atm = spec.get("atmosphere", {})
    if not 0.0 <= float(atm.get("density", 0)) <= 0.5:
        errors.append("atmosphere.density must be within [0, 0.5]")
    qa = spec.get("qa", {})
    if int(qa.get("max_iterations", 1)) > 12:
        errors.append("qa.max_iterations capped at 12 to prevent runaway refinement loops")
    if errors:
        raise SpecError("invalid spec:\n  - " + "\n  - ".join(errors))


# ----------------------------------------------------------------------------- patching

def _walk(spec: dict, path: str, create: bool = False):
    parts = path.split(".")
    node: Any = spec
    for part in parts[:-1]:
        if isinstance(node, list):
            node = node[int(part)]
            continue
        if part not in node or node[part] is None:
            if not create:
                raise SpecError(f"patch path not found: {path}")
            node[part] = {}
        node = node[part]
    return node, parts[-1]


def get_path(spec: dict, path: str, default: Any = None) -> Any:
    try:
        node, leaf = _walk(spec, path)
        return node[int(leaf)] if isinstance(node, list) else node.get(leaf, default)
    except (SpecError, KeyError, IndexError, ValueError, TypeError):
        return default


def apply_patch(spec: dict, ops: list[dict], bounds: dict[str, tuple[float, float]] | None = None) -> dict:
    """Return a new spec with ``ops`` applied. Numeric results are clamped to ``bounds``."""
    out = copy.deepcopy(spec)
    bounds = bounds or {}
    for op in ops:
        kind, path, value = op.get("op", "set"), op["path"], op.get("value")
        node, leaf = _walk(out, path, create=(kind in {"set", "append"}))
        key: Any = int(leaf) if isinstance(node, list) else leaf
        current = node[key] if (isinstance(node, list) or key in node) else None
        if kind == "set":
            new = copy.deepcopy(value)
        elif kind in {"scale", "add"}:
            if isinstance(current, list):
                vals = value if isinstance(value, list) else [value] * len(current)
                new = [c * v if kind == "scale" else c + v for c, v in zip(current, vals)]
            elif isinstance(current, (int, float)):
                new = current * value if kind == "scale" else current + value
            else:
                raise SpecError(f"{kind} needs a numeric target at {path}, found {current!r}")
        elif kind == "append":
            new = list(current or []) + [copy.deepcopy(value)]
        else:
            raise SpecError(f"unknown patch op {kind!r}")
        if path in bounds:
            lo, hi = bounds[path]
            if isinstance(new, list):
                new = [min(hi, max(lo, v)) for v in new]
            elif isinstance(new, (int, float)):
                new = min(hi, max(lo, new))
        node[key] = new
    validate_spec(out)
    return out
