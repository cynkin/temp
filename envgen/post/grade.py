"""Post-processing / colour grading of display-referred renders (numpy, no Blender needed).

Separating the grade from the render lets the QA loop iterate on exposure,
contrast and colour *without re-rendering*, which on a CPU is the expensive
step. Operations run in a linearised space where that is physically meaningful
(exposure, bloom) and in display space where artists expect it (lift/gamma/gain,
contrast, vignette, grain).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def _to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def _blur(img: np.ndarray, sigma_px: float) -> np.ndarray:
    """Separable gaussian blur (numpy only)."""
    if sigma_px <= 0.3:
        return img
    r = int(3 * sigma_px)
    x = np.arange(-r, r + 1)
    k = np.exp(-(x ** 2) / (2 * sigma_px ** 2))
    k /= k.sum()
    pad = np.pad(img, ((r, r), (0, 0), (0, 0)), mode="reflect")
    tmp = sum(k[i] * pad[i:i + img.shape[0]] for i in range(len(k)))
    pad = np.pad(tmp, ((0, 0), (r, r), (0, 0)), mode="reflect")
    return sum(k[i] * pad[:, i:i + img.shape[1]] for i in range(len(k)))


def load(path: str | Path) -> np.ndarray:
    im = Image.open(path)
    arr = np.asarray(im)
    if arr.dtype == np.uint16 or im.mode.startswith("I;16"):
        return arr.astype(np.float32) / 65535.0
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, -1)
    return arr[..., :3].astype(np.float32) / (65535.0 if arr.max() > 255 else 255.0)


def _load_rgb16(path: str | Path) -> np.ndarray:
    # Pillow reads 16-bit RGB PNGs as 8-bit 'RGB' on some versions; use imageio-free fallback via PIL 'I;16'
    try:
        import skimage.io as skio  # scikit-image keeps 16-bit precision
        arr = skio.imread(str(path))
        arr = arr[..., :3].astype(np.float32)
        return arr / (65535.0 if arr.max() > 255.5 else 255.0)
    except Exception:  # noqa: BLE001
        return load(path)


def grade(img: np.ndarray, p: dict, seed: int = 0) -> np.ndarray:
    h, w, _ = img.shape
    lin = _to_linear(np.clip(img, 0, 1))
    lin = lin * (2.0 ** float(p.get("exposure", 0.0)))

    b = p.get("bloom") or {}
    if b.get("intensity", 0) > 0:
        lum = lin @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
        thr = float(b.get("threshold", 0.8))
        mask = np.clip((lum - thr) / max(1e-3, 1 - thr), 0, None)[..., None]
        bright = lin * mask
        rad = float(b.get("radius", 0.02)) * w
        glow = 0.6 * _blur(bright, rad * 0.35) + 0.4 * _blur(bright, rad)
        lin = lin + glow * float(b["intensity"])

    c = _to_srgb(lin)
    # lift / gamma / gain (ASC-CDL-like, per channel, display space)
    lift = np.array(p.get("lift", [0, 0, 0]), dtype=np.float32)
    gain = np.array(p.get("gain", [1, 1, 1]), dtype=np.float32)
    gamma = np.array(p.get("gamma", [1, 1, 1]), dtype=np.float32)
    c = np.clip(c * gain + lift * (1 - c), 0, None)
    c = np.power(c, 1.0 / np.maximum(gamma, 1e-3))
    # contrast around mid-grey (filmic S via smooth pivot)
    k = float(p.get("contrast", 1.0))
    if abs(k - 1.0) > 1e-3:
        pivot = 0.18 ** (1 / 2.2)
        c = np.clip(pivot + (c - pivot) * k, 0, None)
    # split toning
    luma = (c @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32))[..., None]
    sh = np.array(p.get("shadow_tint", [0, 0, 0]), dtype=np.float32)
    hi = np.array(p.get("highlight_tint", [0, 0, 0]), dtype=np.float32)
    c = c + sh * np.clip(1 - luma * 2, 0, 1) + hi * np.clip(luma * 2 - 1, 0, 1)
    # saturation
    s = float(p.get("saturation", 1.0))
    c = luma + (c - luma) * s
    # chromatic aberration (radial, red out / blue in)
    ca = float(p.get("chromatic_aberration", 0.0))
    if ca > 0:
        c = _radial_ca(c, ca)
    # vignette (natural cos^4-like falloff)
    v = float(p.get("vignette", 0.0))
    if v > 0:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / np.sqrt(2)
        c = c * (1 - v * np.clip(r, 0, 1) ** 2.2)[..., None]
    # film grain (luma-weighted, stronger in mids)
    gr = float(p.get("grain", 0.0))
    if gr > 0:
        rng = np.random.default_rng(seed)
        noise = rng.normal(0, 1, (h, w, 1)).astype(np.float32)
        noise = 0.7 * noise + 0.3 * _blur(noise, 0.8)
        c = c + noise * gr * (0.35 + np.sqrt(np.clip(luma, 0, 1)) * 0.65) * 0.5
    return np.clip(c, 0, 1)


def _radial_ca(c: np.ndarray, amount: float) -> np.ndarray:
    h, w, _ = c.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = (w - 1) / 2, (h - 1) / 2
    out = c.copy()
    for ch, s in ((0, 1 + amount), (2, 1 - amount)):
        sx = np.clip(cx + (xx - cx) / s, 0, w - 1).astype(np.int32)
        sy = np.clip(cy + (yy - cy) / s, 0, h - 1).astype(np.int32)
        out[..., ch] = c[sy, sx, ch]
    return out


def grade_file(raw_path: str | Path, out_path: str | Path, params: dict, seed: int = 0) -> Path:
    img = _load_rgb16(raw_path)
    out = grade(img, params, seed)
    out_path = Path(out_path)
    Image.fromarray((out * 255 + 0.5).astype(np.uint8)).save(out_path, quality=93)
    return out_path
