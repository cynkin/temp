"""Image "visual profile" extraction.

Used both for reference images (to build per-cluster targets) and for our own
renders (to score them against those targets, see :mod:`envgen.qa.metrics`).

All measurements are made on a normalised working copy of the image:

* RGB float32 in [0, 1], sRGB-encoded values as stored (no linearisation),
* letterbox bars (near-uniform near-black rows/cols at the borders) removed,
* resized so that the longest side equals ``max_side`` (768 by default), so
  scale-dependent metrics (edge density, local contrast, Laplacian energy,
  dark-channel patch size) are comparable between images of any resolution.

Pure numpy / scipy.ndimage / scikit-image / Pillow; no bpy.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from skimage.feature import canny
from skimage.transform import probabilistic_hough_line

__all__ = [
    "load_image",
    "compute_profile",
    "aggregate_profiles",
    "detect_letterbox",
    "luma",
    "srgb_to_lab",
    "kmeans",
    "palette_distance",
]

REF_SIDE = 768  # resolution the pixel-scale constants below are tuned for
REC709 = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

# --------------------------------------------------------------------------- #
# basic colour helpers
# --------------------------------------------------------------------------- #


def luma(img: np.ndarray) -> np.ndarray:
    """Perceptual luma: Rec.709 weights applied to sRGB-encoded values."""
    return (img[..., :3] @ REC709).astype(np.float32)


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB-encoded values in [0,1] (..., 3) -> CIE L*a*b* (D65)."""
    rgb = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ]
    )
    xyz = lin @ m.T
    xyz = xyz / np.array([0.95047, 1.0, 1.08883])
    eps, kappa = 216.0 / 24389.0, 24389.0 / 27.0
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16.0) / 116.0)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def _hex(rgb: Iterable[float]) -> str:
    r, g, b = (int(round(float(np.clip(c, 0, 1)) * 255)) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


# --------------------------------------------------------------------------- #
# loading / letterbox crop
# --------------------------------------------------------------------------- #

def _bar_extent(mx_lines: np.ndarray, limit: int) -> int:
    """Length of the letterbox bar starting at line 0 of ``mx_lines``.

    ``mx_lines`` is (n_lines, n_pixels) of per-pixel max-channel values.
    A *black* line has >=97% pixels below 0.02 and a mean below 0.008 (real
    letterbox bars are encoded as ~0 with a little JPEG ringing).  A *bar-ish*
    line has >=75% pixels below 0.03 and a median below 0.01 (bars carrying a
    logo / subtitle / watermark).  The run must start with black lines at the
    border; bar-ish lines are accepted while contiguous.
    """
    n = min(limit, len(mx_lines))
    if n == 0:
        return 0
    v = mx_lines[:n]
    mean = v.mean(axis=1)
    black = ((v < 0.02).mean(axis=1) >= 0.97) & (mean < 0.008)
    barish = ((v < 0.03).mean(axis=1) >= 0.75) & (np.median(v, axis=1) < 0.01)
    if not black[0]:
        return 0
    end = n_black = 0
    for i in range(n):
        if black[i]:
            n_black += 1
            end = i + 1
        elif barish[i]:
            end = i + 1
        else:
            break
    return end if n_black >= 3 else 0


def _pair(a: int, b: int, size: int) -> tuple[int, int]:
    """Keep a pair of opposite bars only if both exist and are similar.

    Letterboxing / pillarboxing is symmetric; a single dark border is far more
    likely to be an unlit ceiling or wall (especially in our own renders).
    """
    if a == 0 or b == 0:
        return 0, 0
    if min(a, b) / max(a, b) < 0.5 and abs(a - b) > 0.03 * size:
        return 0, 0
    return a, b


def detect_letterbox(img: np.ndarray, max_frac: float = 0.3) -> tuple[int, int, int, int]:
    """Return (top, bottom, left, right) letterbox/pillarbox bar thicknesses."""
    mx = img[..., :3].max(axis=-1)
    h, w = mx.shape
    top = _bar_extent(mx, int(h * max_frac))
    bottom = _bar_extent(mx[::-1], int(h * max_frac))
    top, bottom = _pair(top, bottom, h)
    sub = mx[top : h - bottom].T  # columns evaluated on the row-cropped image
    left = _bar_extent(sub, int(w * max_frac))
    right = _bar_extent(sub[::-1], int(w * max_frac))
    left, right = _pair(left, right, w)
    # Small safety margin for JPEG ringing / anti-aliased bar edges.
    pad_h, pad_w = max(2, h // 120), max(2, w // 120)
    top, bottom = (top + pad_h, bottom + pad_h) if top else (0, 0)
    left, right = (left + pad_w, right + pad_w) if left else (0, 0)
    if h - top - bottom < h * 0.4 or w - left - right < w * 0.4:
        return (0, 0, 0, 0)  # implausible: probably a genuinely dark image
    return (top, bottom, left, right)


def _to_float_rgb(src: Any) -> np.ndarray:
    if isinstance(src, (str, Path)):
        with Image.open(src) as im:
            im = im.convert("RGB")
            arr = np.asarray(im, dtype=np.float32) / 255.0
        return arr
    arr = np.asarray(src)
    if arr.ndim == 2:
        arr = np.repeat(arr[..., None], 3, axis=-1)
    if arr.shape[-1] == 4:
        arr = arr[..., :3]
    if arr.dtype == np.uint8:
        arr = arr.astype(np.float32) / 255.0
    elif arr.dtype == np.uint16:
        arr = arr.astype(np.float32) / 65535.0
    else:
        arr = arr.astype(np.float32)
    return np.clip(arr, 0.0, 1.0)


def _resize(arr: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Resize float RGB [0,1] to (w, h) with Pillow (per-channel float)."""
    w, h = size
    chans = [
        np.asarray(Image.fromarray(arr[..., c]).resize((w, h), Image.Resampling.LANCZOS))
        for c in range(arr.shape[-1])
    ]
    return np.clip(np.stack(chans, axis=-1), 0.0, 1.0).astype(np.float32)


def load_image(
    path_or_array: Any,
    max_side: int = REF_SIDE,
    crop_letterbox: bool = True,
    upscale: bool = True,
    _info: dict | None = None,
) -> np.ndarray:
    """Load an image as float32 RGB in [0,1] (sRGB-encoded values as stored).

    Letterbox bars are removed, then the image is resized so its longest side
    equals ``max_side``.  ``upscale=True`` (default) also enlarges smaller
    images so that scale-dependent metrics stay comparable; pass
    ``upscale=False`` to only ever downscale.
    """
    arr = _to_float_rgb(path_or_array)
    src_h, src_w = arr.shape[:2]
    crop = (0, 0, 0, 0)
    if crop_letterbox:
        crop = detect_letterbox(arr)
        t, b, l, r = crop
        arr = arr[t : src_h - b, l : src_w - r]
    h, w = arr.shape[:2]
    if max_side and (max(h, w) > max_side or (upscale and max(h, w) < max_side)):
        s = max_side / max(h, w)
        arr = _resize(arr, (max(1, round(w * s)), max(1, round(h * s))))
    if _info is not None:
        _info.update(source_size=[src_w, src_h], crop_tblr=list(crop))
    return np.ascontiguousarray(arr, dtype=np.float32)


# --------------------------------------------------------------------------- #
# k-means
# --------------------------------------------------------------------------- #


def kmeans(
    x: np.ndarray,
    k: int,
    weights: np.ndarray | None = None,
    iters: int = 25,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Weighted k-means (k-means++ init, fixed seed). Returns (centers, labels)."""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    w = np.ones(n) if weights is None else np.asarray(weights, dtype=np.float64)
    w = np.maximum(w, 0.0)
    if w.sum() <= 0:
        w = np.ones(n)
    k = max(1, min(k, n))
    rng = np.random.default_rng(seed)
    centers = np.empty((k, x.shape[1]))
    centers[0] = x[rng.choice(n, p=w / w.sum())]
    d2 = ((x - centers[0]) ** 2).sum(1)
    for i in range(1, k):
        p = d2 * w
        if p.sum() <= 1e-12:
            centers[i] = x[rng.integers(n)]
        else:
            centers[i] = x[rng.choice(n, p=p / p.sum())]
        d2 = np.minimum(d2, ((x - centers[i]) ** 2).sum(1))
    labels = np.zeros(n, dtype=np.int64)
    for _ in range(iters):
        dist = ((x[:, None, :] - centers[None, :, :]) ** 2).sum(-1)
        new = dist.argmin(1)
        for j in range(k):
            m = new == j
            if m.any() and w[m].sum() > 0:
                centers[j] = (x[m] * w[m, None]).sum(0) / w[m].sum()
        if np.array_equal(new, labels) and _ > 0:
            break
        labels = new
    return centers, labels


def _palette_from_clusters(
    rgb: np.ndarray, labels: np.ndarray, weights: np.ndarray, k: int
) -> list[dict]:
    total = float(weights.sum()) or 1.0
    out = []
    for j in range(k):
        m = labels == j
        if not m.any():
            continue
        wj = weights[m]
        if wj.sum() <= 0:
            continue
        c = (rgb[m] * wj[:, None]).sum(0) / wj.sum()
        out.append(
            {
                "hex": _hex(c),
                "rgb": [round(float(v), 4) for v in c],
                "weight": round(float(wj.sum() / total), 4),
            }
        )
    out.sort(key=lambda e: -e["weight"])
    return out


def palette_distance(pal_a: list[dict], pal_b: list[dict]) -> float:
    """Symmetric weighted chamfer distance (mean min ΔE76 in Lab) between palettes."""
    if not pal_a or not pal_b:
        return float("nan")
    la = srgb_to_lab(np.array([e["rgb"] for e in pal_a]))
    lb = srgb_to_lab(np.array([e["rgb"] for e in pal_b]))
    wa = np.array([max(e.get("weight", 1.0), 0.0) for e in pal_a]) + 1e-9
    wb = np.array([max(e.get("weight", 1.0), 0.0) for e in pal_b]) + 1e-9
    d = np.sqrt(((la[:, None, :] - lb[None, :, :]) ** 2).sum(-1))
    ab = float((d.min(1) * wa).sum() / wa.sum())
    ba = float((d.min(0) * wb).sum() / wb.sum())
    return 0.5 * (ab + ba)


# --------------------------------------------------------------------------- #
# vanishing point
# --------------------------------------------------------------------------- #


def _vanishing_point(edges: np.ndarray, seed: int = 0) -> dict:
    h, w = edges.shape
    diag = math.hypot(w, h)
    try:
        segs = probabilistic_hough_line(
            edges,
            threshold=10,
            line_length=max(12, int(0.035 * diag)),
            line_gap=3,
            rng=seed,
        )
    except TypeError:  # older skimage used `seed`
        segs = probabilistic_hough_line(
            edges, threshold=10, line_length=max(12, int(0.035 * diag)), line_gap=3
        )
    empty = {"vanishing_point": None, "vp_confidence": 0.0, "n_lines": len(segs), "n_oblique": 0}
    if not segs:
        return empty
    s = np.array(segs, dtype=np.float64).reshape(-1, 4)  # x0 y0 x1 y1
    d = s[:, 2:] - s[:, :2]
    length = np.hypot(d[:, 0], d[:, 1])
    ang = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 180.0  # 0 = horizontal
    near_h = np.minimum(ang, 180.0 - ang) < 8.0
    near_v = np.abs(ang - 90.0) < 8.0
    keep = ~(near_h | near_v) & (length > 0)
    s, d, length = s[keep], d[keep], length[keep]
    if len(s) < 4:
        empty["n_oblique"] = int(len(s))
        return empty
    order = np.argsort(-length)[:400]
    s, d, length = s[order], d[order], length[order]
    mid = 0.5 * (s[:, :2] + s[:, 2:])
    u = d / length[:, None]  # unit directions
    # homogeneous lines
    p1 = np.c_[s[:, :2], np.ones(len(s))]
    p2 = np.c_[s[:, 2:], np.ones(len(s))]
    L = np.cross(p1, p2)
    # candidate intersections from the longest lines
    m = min(len(s), 120)
    ii, jj = np.triu_indices(m, 1)
    P = np.cross(L[ii], L[jj])
    ok = np.abs(P[:, 2]) > 1e-9
    P = P[ok]
    ii, jj = ii[ok], jj[ok]
    cand = P[:, :2] / P[:, 2:3]
    # discard intersections of nearly parallel pairs (unstable) & far points
    cosang = np.abs((u[ii] * u[jj]).sum(1))
    inside = (
        (cand[:, 0] > -0.25 * w)
        & (cand[:, 0] < 1.25 * w)
        & (cand[:, 1] > -0.25 * h)
        & (cand[:, 1] < 1.25 * h)
        & (cosang < math.cos(math.radians(4)))
    )
    cand = cand[inside]
    if len(cand) == 0:
        return {**empty, "n_oblique": int(len(s))}
    if len(cand) > 3000:
        rng = np.random.default_rng(seed)
        cand = cand[rng.choice(len(cand), 3000, replace=False)]

    tol_deg = 2.5
    wl = np.sqrt(length)  # damp domination by a couple of very long lines

    def angular_err(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        # angle between each line direction and the direction midpoint->point;
        # also whether the point lies beyond the segment, and the bearing
        v = points[:, None, :] - mid[None, :, :]
        vn = np.linalg.norm(v, axis=-1) + 1e-9
        c = np.abs((v * u[None, :, :]).sum(-1)) / vn
        err = np.degrees(np.arccos(np.clip(c, 0.0, 1.0)))
        beyond = vn > 0.6 * length[None, :]
        bearing = np.arctan2(-v[..., 1], -v[..., 0])  # VP -> line midpoint
        return err, beyond, bearing

    n_bins = 16
    votes = np.zeros(len(cand))
    for k0 in range(0, len(cand), 500):
        e, beyond, bearing = angular_err(cand[k0 : k0 + 500])
        contrib = wl[None, :] * np.exp(-0.5 * (e / tol_deg) ** 2) * beyond
        b = ((bearing + np.pi) / (2 * np.pi) * n_bins).astype(int) % n_bins
        # support from many directions (lines converging from all sides) is
        # worth more than a dense bundle from one cluttered object
        acc = np.zeros((contrib.shape[0], n_bins))
        for j in range(n_bins):
            acc[:, j] = (contrib * (b == j)).sum(1)
        votes[k0 : k0 + 500] = np.sqrt(acc).sum(1)
    best = cand[int(votes.argmax())]

    # refine: weighted least squares on inlier lines (minimise perpendicular dist)
    for _ in range(3):
        e, bey, _ = angular_err(best[None, :])
        inl = (e[0] < 2 * tol_deg) & bey[0]
        if inl.sum() < 2:
            break
        n = np.c_[-u[inl, 1], u[inl, 0]]  # unit normals
        c = (n * mid[inl]).sum(1)
        ww = wl[inl]
        A = n * ww[:, None]
        try:
            sol, *_ = np.linalg.lstsq(A, c * ww, rcond=None)
        except np.linalg.LinAlgError:
            break
        if not np.all(np.isfinite(sol)):
            break
        best = sol
    e, bey, _ = angular_err(best[None, :])
    inl = (e[0] < tol_deg) & bey[0]
    conf = float(inl.sum() / len(s))
    vp = [float(best[0] / w), float(best[1] / h)]
    if inl.sum() < 4 or not (-0.3 <= vp[0] <= 1.3 and -0.3 <= vp[1] <= 1.3):
        return {"vanishing_point": None, "vp_confidence": round(conf, 4),
                "n_lines": len(segs), "n_oblique": int(len(s))}
    return {
        "vanishing_point": [round(vp[0], 4), round(vp[1], 4)],
        "vp_confidence": round(conf, 4),
        "n_lines": len(segs),
        "n_oblique": int(len(s)),
    }


def _orientation_fractions(Y: np.ndarray, edges: np.ndarray) -> dict:
    gy = ndi.sobel(Y, axis=0)
    gx = ndi.sobel(Y, axis=1)
    mag = np.hypot(gx, gy)[edges]
    if mag.size == 0 or mag.sum() <= 0:
        return {"horizontal": 0.0, "vertical": 0.0, "oblique": 0.0}
    # edge direction is perpendicular to the gradient
    theta = (np.degrees(np.arctan2(gy[edges], gx[edges])) + 90.0) % 180.0
    hor = np.minimum(theta, 180.0 - theta) < 15.0
    ver = np.abs(theta - 90.0) < 15.0
    tot = mag.sum()
    fh, fv = float(mag[hor].sum() / tot), float(mag[ver].sum() / tot)
    return {"horizontal": round(fh, 4), "vertical": round(fv, 4),
            "oblique": round(max(0.0, 1.0 - fh - fv), 4)}


# --------------------------------------------------------------------------- #
# profile
# --------------------------------------------------------------------------- #


def _r(x: float, n: int = 5) -> float:
    return round(float(x), n)


def compute_profile(
    path_or_array: Any,
    max_side: int = REF_SIDE,
    seed: int = 0,
    crop_letterbox: bool = True,
) -> dict:
    """Compute the JSON-serialisable visual profile of an image (path or array).

    ``crop_letterbox=False`` disables bar removal (e.g. for renders known to be
    full-frame).
    """
    info: dict = {}
    img = load_image(path_or_array, max_side=max_side, crop_letterbox=crop_letterbox, _info=info)
    h, w = img.shape[:2]
    scale = max(h, w) / REF_SIDE
    Y = luma(img)
    flat = Y.ravel()

    # ---- luminance
    p02, p50, p98 = np.percentile(flat, [2, 50, 98])
    mean = float(flat.mean())
    key = "low" if mean < 0.3 else ("high" if mean > 0.6 else "mid")
    luminance = {
        "mean": _r(mean), "median": _r(p50), "p02": _r(p02), "p50": _r(p50),
        "p98": _r(p98), "std": _r(flat.std()), "key": key,
    }

    # ---- contrast
    blur = ndi.gaussian_filter(Y, sigma=8.0 * scale)
    contrast = {
        "rms": _r(flat.std()),
        "dynamic_range": _r(p98 - p02),
        "local_contrast": _r(np.abs(Y - blur).mean()),
    }

    # ---- colour
    rgb = img.reshape(-1, 3)
    wl = flat + 1e-6
    mx, mn = rgb.max(1), rgb.min(1)
    sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    wsum = wl.sum()
    warmth = float(((rgb[:, 0] - rgb[:, 2]) * wl).sum() / wsum)
    tint = float(((rgb[:, 1] - 0.5 * (rgb[:, 0] + rgb[:, 2])) * wl).sum() / wsum)
    rng = np.random.default_rng(seed)
    n_s = min(len(rgb), 12000)
    idx = rng.choice(len(rgb), n_s, replace=False) if len(rgb) > n_s else np.arange(len(rgb))
    sub = rgb[idx].astype(np.float64)
    lab = srgb_to_lab(sub)
    _, labels = kmeans(lab, 6, iters=25, seed=seed)
    palette = _palette_from_clusters(sub, labels, np.ones(len(sub)), 6)
    color = {
        "mean_rgb": [_r(v) for v in rgb.mean(0)],
        "saturation_mean": _r((sat * wl).sum() / wsum),
        "warmth": _r(warmth),
        "tint": _r(tint),
        "palette": palette,
    }

    # ---- haze (He et al. dark channel)
    patch = max(3, int(round(15 * scale)) | 1)
    dark = ndi.minimum_filter(img.min(axis=-1), size=patch, mode="nearest")
    bright = Y >= np.percentile(Y, 90)
    haze = {
        "dark_channel_mean": _r(dark.mean()),
        "veil": _r(dark[bright].mean() if bright.any() else dark.mean()),
    }

    # ---- detail
    edges = canny(Y, sigma=1.5)
    lap = ndi.laplace(ndi.gaussian_filter(Y, 0.5))
    detail = {
        "edge_density": _r(edges.mean()),
        "hf_energy": _r(np.abs(lap).mean()),
    }

    # ---- structure
    # Lines for VP estimation use contrast-normalised (quantile) Canny so that
    # dark, low-key images still yield enough structure.
    vp_edges = canny(Y, sigma=2.0, low_threshold=0.80, high_threshold=0.90, use_quantiles=True)
    vp = _vanishing_point(vp_edges, seed=seed)
    structure = {
        "vanishing_point": vp["vanishing_point"],
        "vp_confidence": vp["vp_confidence"],
        "dominant_orientations": _orientation_fractions(Y, edges),
    }

    # ---- spatial
    ys = np.linspace(0, h, 4).astype(int)
    xs = np.linspace(0, w, 4).astype(int)
    grid = [[float(Y[ys[r]:ys[r + 1], xs[c]:xs[c + 1]].mean()) for c in range(3)] for r in range(3)]
    g = np.array(grid)
    br = np.unravel_index(int(g.argmax()), g.shape)
    cmask = np.zeros_like(Y, dtype=bool)
    cmask[h // 4 : h - h // 4, w // 4 : w - w // 4] = True
    eps = 1e-4
    spatial = {
        "luma_grid": [[_r(v) for v in row] for row in grid],
        "brightest_cell": [int(br[0]), int(br[1])],
        "center_vs_edge": _r((Y[cmask].mean() + eps) / (Y[~cmask].mean() + eps)),
        "top_vs_bottom": _r((Y[: h // 2].mean() + eps) / (Y[h // 2 :].mean() + eps)),
    }

    clipping = {
        "highlights": _r((flat > 0.98).mean(), 6),
        "shadows": _r((flat < 0.02).mean(), 6),
    }

    extra = {
        "source_size": info.get("source_size"),
        "crop_tblr": info.get("crop_tblr"),
        "luma_grid_cv": _r(g.std() / (g.mean() + eps)),
        "vp_lines": vp["n_lines"],
        "vp_oblique_lines": vp["n_oblique"],
    }
    if isinstance(path_or_array, (str, Path)):
        extra["source"] = str(path_or_array)

    return {
        "luminance": luminance,
        "contrast": contrast,
        "color": color,
        "haze": haze,
        "detail": detail,
        "structure": structure,
        "spatial": spatial,
        "clipping": clipping,
        "size": [int(w), int(h)],
        "extra": extra,
    }


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float, np.integer, np.floating)) and not isinstance(x, bool)


def _stats(vals: list[float]) -> dict:
    a = np.asarray([v for v in vals if v is not None and np.isfinite(v)], dtype=np.float64)
    if a.size == 0:
        return {"mean": None, "std": None, "min": None, "max": None}
    return {"mean": _r(a.mean(), 6), "std": _r(a.std(), 6),
            "min": _r(a.min(), 6), "max": _r(a.max(), 6)}


def _is_num_tree(x: Any) -> bool:
    if _is_num(x):
        return True
    return isinstance(x, list) and len(x) > 0 and all(_is_num_tree(e) for e in x)


def _agg_numeric(values: list[Any]) -> Any:
    """Aggregate a list of same-shaped numbers / nested numeric lists."""
    first = values[0]
    if _is_num(first):
        return _stats([float(v) for v in values if _is_num(v)])
    n = len(first)
    vals = [v for v in values if isinstance(v, list) and len(v) == n]
    return [_agg_numeric([v[i] for v in vals]) for i in range(n)]


def merge_palettes(palettes: list[list[dict]], k: int = 6, seed: int = 0) -> list[dict]:
    """Weighted k-means over all entries of several palettes (in Lab)."""
    entries = [e for p in palettes if p for e in p]
    if not entries:
        return []
    rgb = np.array([e["rgb"] for e in entries], dtype=np.float64)
    wts = np.array([float(e.get("weight", 1.0)) for e in entries], dtype=np.float64)
    _, labels = kmeans(srgb_to_lab(rgb), k, weights=wts, iters=50, seed=seed)
    return _palette_from_clusters(rgb, labels, wts, k)


def aggregate_profiles(profiles: list[dict]) -> dict:
    """Aggregate profiles: {mean,std,min,max} per scalar leaf (same paths).

    * numeric lists (mean_rgb, luma_grid, size, ...) are aggregated element-wise,
    * ``color.palette`` is a merged weighted palette, and
      ``color.palette_distance`` holds stats of each input palette's distance
      to the merged one (used as the tolerance band when scoring),
    * ``structure.vanishing_point`` = {mean:[x,y], std:[..], min, max, count},
    * string leaves (e.g. ``luminance.key``) -> {mode, counts}.
    """
    profiles = [p for p in profiles if p]
    if not profiles:
        raise ValueError("no profiles to aggregate")

    def walk(nodes: list[Any], path: tuple[str, ...]) -> Any:
        first = nodes[0]
        if path == ("color", "palette"):
            return merge_palettes(nodes)
        if path == ("structure", "vanishing_point"):
            pts = [v for v in nodes if isinstance(v, list) and len(v) == 2]
            if not pts:
                return {"mean": None, "std": None, "min": None, "max": None, "count": 0}
            a = np.asarray(pts, dtype=np.float64)
            return {"mean": [_r(v, 4) for v in a.mean(0)], "std": [_r(v, 4) for v in a.std(0)],
                    "min": [_r(v, 4) for v in a.min(0)], "max": [_r(v, 4) for v in a.max(0)],
                    "count": int(len(pts))}
        if isinstance(first, dict):
            keys: list[str] = []
            for nd in nodes:
                if isinstance(nd, dict):
                    keys += [k for k in nd if k not in keys]
            out = {}
            for k in keys:
                sub = [nd[k] for nd in nodes if isinstance(nd, dict) and k in nd and nd[k] is not None]
                if not sub:
                    out[k] = None
                    continue
                r = walk(sub, path + (k,))
                if r is not None:
                    out[k] = r
            return out
        if _is_num_tree(first):
            return _agg_numeric(nodes)
        if isinstance(first, str):
            if path and path[-1] == "source":
                return None
            counts: dict[str, int] = {}
            for v in nodes:
                if isinstance(v, str):
                    counts[v] = counts.get(v, 0) + 1
            return {"mode": max(counts, key=counts.get), "counts": counts}
        return None

    agg = walk(profiles, ())
    merged = agg.get("color", {}).get("palette", [])
    dists = [palette_distance(p["color"]["palette"], merged)
             for p in profiles if p.get("color", {}).get("palette")]
    agg.setdefault("color", {})["palette_distance"] = _stats(dists)
    agg["count"] = len(profiles)
    return agg


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m envgen.vision.profile",
                                 description="Compute visual profiles of images.")
    ap.add_argument("images", nargs="+")
    ap.add_argument("--out", help="write JSON here (default: stdout)")
    ap.add_argument("--aggregate", action="store_true",
                    help="output the aggregate of all image profiles")
    ap.add_argument("--max-side", type=int, default=REF_SIDE)
    args = ap.parse_args(argv)
    profs = [compute_profile(p, max_side=args.max_side) for p in args.images]
    if args.aggregate:
        result: Any = aggregate_profiles(profs)
    elif len(profs) == 1:
        result = profs[0]
    else:
        result = profs
    txt = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(txt + "\n")
    else:
        sys.stdout.write(txt + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
