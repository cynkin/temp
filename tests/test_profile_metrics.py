"""Tests for envgen.vision.profile and envgen.qa.metrics.

Runnable with pytest or directly: ``python tests/test_profile_metrics.py``.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from envgen.qa.metrics import score_render  # noqa: E402
from envgen.vision.profile import (  # noqa: E402
    aggregate_profiles,
    compute_profile,
    detect_letterbox,
    load_image,
)

REFS = ROOT / "references"
CORRIDOR = ["00", "02", "08", "09", "12", "13", "15", "16", "17", "18", "19"]

_PROFILE_CACHE: dict[str, dict] = {}


def _ref_index() -> dict[str, Path]:
    out = {}
    for line in (REFS / "index.txt").read_text().splitlines():
        if line.strip():
            n, p = line.split(None, 1)
            out[n] = ROOT / p.strip()
    return out


def _ref_profiles() -> dict[str, dict]:
    if not _PROFILE_CACHE:
        for n, p in _ref_index().items():
            _PROFILE_CACHE[n] = compute_profile(p)
    return _PROFILE_CACHE


def _draw_line(img: np.ndarray, p0, p1, value, width: float = 1.5) -> None:
    h, w = img.shape[:2]
    n = int(max(abs(p1[0] - p0[0]), abs(p1[1] - p0[1])) * 2) + 2
    t = np.linspace(0.0, 1.0, n)
    xs = p0[0] + (p1[0] - p0[0]) * t
    ys = p0[1] + (p1[1] - p0[1]) * t
    r = int(math.ceil(width))
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy > width * width:
                continue
            xi = np.clip(np.round(xs + dx).astype(int), 0, w - 1)
            yi = np.clip(np.round(ys + dy).astype(int), 0, h - 1)
            img[yi, xi] = value


def synthetic_corridor(vp=(0.58, 0.44), w=768, h=432, seed=0) -> np.ndarray:
    """Dark corridor-like image: lines converging to ``vp`` + distractors."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    vx, vy = vp[0] * w, vp[1] * h
    # brighter towards the end of the corridor, darker at the frame edges
    d = np.hypot((xx - vx) / w, (yy - vy) / h)
    base = 0.08 + 0.25 * np.exp(-(d / 0.25) ** 2)
    img = np.repeat(base[..., None], 3, axis=-1) * np.array([1.0, 0.97, 0.9], np.float32)
    # converging edges (floor/ceiling/wall seams, tiles, pipes)
    for ang in np.linspace(0, 360, 28, endpoint=False) + 4.0:
        a = math.radians(ang)
        ca, sa = math.cos(a), math.sin(a)
        if min(abs(ca), abs(sa)) < 0.2:  # skip near-axis directions
            continue
        r0 = 25 + 10 * rng.random()
        tmax = min(
            ((w - 1 - vx) / ca) if ca > 0 else ((-vx) / ca),
            ((h - 1 - vy) / sa) if sa > 0 else ((-vy) / sa),
        )
        p0 = (vx + r0 * ca, vy + r0 * sa)
        p1 = (vx + tmax * ca, vy + tmax * sa)
        _draw_line(img, p0, p1, 0.75 if ang % 2 < 1 else 0.02)
    # distractors: door frames (vertical) and shelf edges (horizontal)
    for x in (90, 160, 610, 690):
        _draw_line(img, (x, h * 0.2), (x, h * 0.8), 0.5)
    for y in (60, 380):
        _draw_line(img, (w * 0.05, y), (w * 0.3, y), 0.5)
    img += rng.normal(0, 0.01, img.shape).astype(np.float32)
    return np.clip(img, 0, 1).astype(np.float32)


# --------------------------------------------------------------------------- #
# profile tests
# --------------------------------------------------------------------------- #

PROFILE_KEYS = {"luminance", "contrast", "color", "haze", "detail", "structure",
                "spatial", "clipping", "size"}


def test_synthetic_profile_schema_and_vp():
    true_vp = (0.58, 0.44)
    prof = compute_profile(synthetic_corridor(true_vp))
    assert PROFILE_KEYS <= set(prof), set(prof)
    json.dumps(prof)  # serialisable
    lum = prof["luminance"]
    assert set(lum) >= {"mean", "median", "p02", "p50", "p98", "std", "key"}
    assert lum["key"] == "low"
    assert lum["p02"] <= lum["p50"] <= lum["p98"]
    assert len(prof["color"]["palette"]) == 6
    ws = [e["weight"] for e in prof["color"]["palette"]]
    assert ws == sorted(ws, reverse=True) and abs(sum(ws) - 1) < 0.01
    assert prof["size"] == [768, 432]
    g = prof["spatial"]["luma_grid"]
    assert len(g) == 3 and all(len(r) == 3 for r in g)
    assert prof["spatial"]["center_vs_edge"] > 1.0
    vp = prof["structure"]["vanishing_point"]
    assert vp is not None, prof["structure"]
    dist = math.hypot(vp[0] - true_vp[0], vp[1] - true_vp[1])
    assert dist < 0.1, (vp, true_vp, dist)
    assert prof["structure"]["vp_confidence"] > 0.3
    fr = prof["structure"]["dominant_orientations"]
    assert abs(fr["horizontal"] + fr["vertical"] + fr["oblique"] - 1) < 0.02
    assert fr["oblique"] > fr["horizontal"]


def test_profile_deterministic():
    img = synthetic_corridor()
    a, b = compute_profile(img), compute_profile(img)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_letterbox_crop():
    content = synthetic_corridor(w=800, h=340)
    rng = np.random.default_rng(1)
    bar = 90
    framed = np.zeros((340 + 2 * bar, 800, 3), np.float32)
    framed += rng.uniform(0, 0.006, framed.shape).astype(np.float32)  # JPEG-ish
    framed[bar : bar + 340] = content
    # a watermark inside the bottom bar must not stop the crop
    framed[340 + bar + 40 : 340 + bar + 52, 600:700] = 0.8
    t, b, l, r = detect_letterbox(framed)
    assert bar <= t <= bar + 8 and bar <= b <= bar + 8 and l == 0 and r == 0, (t, b, l, r)
    img = load_image(framed)
    aspect = img.shape[1] / img.shape[0]
    assert abs(aspect - 800 / 340) < 0.12, aspect
    # profile measured on the crop: bars must not leak into shadow clipping
    assert compute_profile(framed)["clipping"]["shadows"] < 0.05

    # an unlit ceiling (single dark band, no matching bottom bar) is NOT cropped
    one_sided = content.copy()
    one_sided[:80] = 0.0
    assert detect_letterbox(one_sided) == (0, 0, 0, 0)


def test_letterbox_on_reference():
    # index 14 has top+bottom pure-black bars
    idx = _ref_index()
    if "14" not in idx or not idx["14"].exists():
        print("  (skipped: reference 14 missing)")
        return
    prof = compute_profile(idx["14"])
    t, b, _, _ = prof["extra"]["crop_tblr"]
    assert t > 100 and b > 100, prof["extra"]["crop_tblr"]


def test_aggregate_schema():
    profs = [compute_profile(synthetic_corridor(seed=s, vp=(0.5 + 0.02 * s, 0.45))) for s in range(3)]
    agg = aggregate_profiles(profs)
    st = agg["luminance"]["mean"]
    assert set(st) == {"mean", "std", "min", "max"}
    assert st["min"] <= st["mean"] <= st["max"]
    assert len(agg["color"]["palette"]) <= 6 and agg["color"]["palette"]
    assert agg["structure"]["vanishing_point"]["count"] == 3
    assert len(agg["spatial"]["luma_grid"]) == 3
    assert agg["luminance"]["key"]["mode"] == "low"
    json.dumps(agg)


# --------------------------------------------------------------------------- #
# metrics tests (need the reference images)
# --------------------------------------------------------------------------- #


def _have_refs() -> bool:
    idx = _ref_index() if (REFS / "index.txt").exists() else {}
    return bool(idx) and all(p.exists() for p in idx.values())


def test_reference_scores_high():
    if not _have_refs():
        print("  (skipped: reference images missing)")
        return
    profs = _ref_profiles()
    agg_all = aggregate_profiles(list(profs.values()))
    agg_cor = aggregate_profiles([profs[n] for n in CORRIDOR])
    for n, p in profs.items():
        r = score_render(p, agg_all)
        assert 0.0 <= r["overall"] <= 1.0
        assert r["overall"] > 0.6, (n, r["overall"], r["scores"])
        assert set(r["scores"]) == {"exposure", "contrast", "color_grade", "atmosphere",
                                    "detail", "lighting_structure", "composition"}
    for n in CORRIDOR:
        assert score_render(profs[n], agg_cor)["overall"] > 0.6, n


def test_flat_grey_scores_low():
    if not _have_refs():
        print("  (skipped: reference images missing)")
        return
    agg_all = aggregate_profiles(list(_ref_profiles().values()))
    grey = np.full((432, 768, 3), 0.18, np.float32)
    r = score_render(compute_profile(grey), agg_all)
    codes = {i["code"] for i in r["issues"]}
    assert r["overall"] < 0.45, r
    assert "flat_lighting" in codes and "low_detail" in codes, codes
    for i in r["issues"]:
        assert set(i) >= {"code", "criterion", "severity", "measured", "target", "message"}
        assert 0.0 <= i["severity"] <= 1.0
    json.dumps(r)


def test_bright_version_too_bright():
    if not _have_refs():
        print("  (skipped: reference images missing)")
        return
    profs = _ref_profiles()
    agg_cor = aggregate_profiles([profs[n] for n in CORRIDOR])
    img = load_image(_ref_index()["08"])
    bright = np.clip(img * 2.2 + 0.08, 0, 1)
    base = score_render(profs["08"], agg_cor)
    r = score_render(compute_profile(bright), agg_cor)
    codes = {i["code"] for i in r["issues"]}
    assert "too_bright" in codes, codes
    assert r["scores"]["exposure"] < base["scores"]["exposure"]
    assert r["overall"] < base["overall"]
    assert r["deltas"]["luminance.mean"] > 0


def test_weights_and_single_profile_target():
    prof = compute_profile(synthetic_corridor())
    r = score_render(prof, prof)  # a profile used directly as target
    assert r["overall"] > 0.95, r
    r2 = score_render(prof, prof, weights={"composition": 0.0})
    assert 0.0 <= r2["overall"] <= 1.0


def _run_all() -> int:
    tests = [(k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
