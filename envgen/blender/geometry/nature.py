"""Natural elements: terrain heightfields, grass clumps, ferns, moss growth, weeds in cracks.

Vegetation is placed by *habitat* masks rather than uniform scatter: plants grow where light,
moisture and a foothold exist (cracks, wall bases, under leaks, sunlit patches), which is
what makes overgrowth in references 07 and 10 read as believable.
"""
from __future__ import annotations

import math
import random
from typing import Callable

from mathutils import Vector, noise

from ..util import MeshBuilder, tag


def fbm(x: float, y: float, octaves: int = 5, lacunarity: float = 2.0, gain: float = 0.5, seed: float = 0.0) -> float:
    amp, freq, total = 1.0, 1.0, 0.0
    for _ in range(octaves):
        total += amp * noise.noise(Vector((x * freq + seed, y * freq - seed, seed * 0.37)))
        amp *= gain
        freq *= lacunarity
    return total


def terrain(coll, mats, name: str, size: tuple[float, float], center=(0.0, 0.0), resolution: float = 0.25,
            amplitude: float = 1.5, feature_scale: float = 18.0, ridged: float = 0.3, seed: int = 0,
            flatten: Callable[[float, float], float] | None = None, material: str = "soil") -> object:
    """Heightfield terrain: fBm + ridged noise, optional flatten mask (0..1) for building pads / paths."""
    nx, ny = int(size[0] / resolution), int(size[1] / resolution)
    s = seed * 13.17

    def h(x: float, y: float) -> float:
        u, v = x / feature_scale, y / feature_scale
        base = fbm(u, v, 5, seed=s)
        ridge = 1.0 - abs(fbm(u * 1.7, v * 1.7, 4, seed=s + 5))
        z = amplitude * ((1 - ridged) * base + ridged * (ridge - 0.5))
        if flatten:
            z *= 1.0 - flatten(x, y)
        return z

    mb = MeshBuilder(name)
    mb.grid((center[0], center[1], 0.0), size, material, xs=nx, ys=ny,
            displace=lambda x, y: h(x + center[0], y + center[1]))
    obj = mb.to_object(coll, mats)
    tag(obj, role="terrain", support=True)
    obj["envgen_height_fn"] = "fbm"
    return obj


def grass_clumps(coll, mats, name: str, positions, rng: random.Random, blades: int = 22, height=(0.15, 0.45),
                 material: str = "grass_dry", lean: float = 0.35) -> object:
    """Grass/weed clumps from tapered, curved blades (real geometry, not cards) at given (x, y, z) positions."""
    mb = MeshBuilder(name)
    for (cx, cy, cz) in positions:
        for _ in range(blades):
            a = rng.uniform(0, 2 * math.pi)
            r0 = rng.uniform(0, 0.05)
            hgt = rng.uniform(*height)
            bend = rng.uniform(0.1, lean)
            base = Vector((cx + r0 * math.cos(a), cy + r0 * math.sin(a), cz))
            out = Vector((math.cos(a), math.sin(a), 0))
            w0 = rng.uniform(0.004, 0.008)
            segs = 4
            prev_l = prev_r = None
            side = Vector((-out.y, out.x, 0))
            mb._begin()
            for k in range(segs + 1):
                t = k / segs
                p = base + out * (bend * hgt * t * t) + Vector((0, 0, hgt * t * (1 - 0.25 * bend * t)))
                w = w0 * (1 - t) + 0.0005
                vl = mb.bm.verts.new(p - side * w)
                vr = mb.bm.verts.new(p + side * w)
                if prev_l is not None:
                    mb.bm.faces.new((prev_l, prev_r, vr, vl))
                prev_l, prev_r = vl, vr
            mb._finish([], material, smooth=True)
    obj = mb.to_object(coll, mats)
    tag(obj, role="vegetation", grounded=False)
    return obj


def ferns(coll, mats, name: str, positions, rng: random.Random, fronds=(6, 11), length=(0.35, 0.7),
          material: str = "fern_leaf") -> object:
    """Ferns: arching fronds (rachis curve) with alternating tapered pinnae."""
    mb = MeshBuilder(name)
    for (cx, cy, cz) in positions:
        for _ in range(rng.randint(*fronds)):
            a = rng.uniform(0, 2 * math.pi)
            L = rng.uniform(*length)
            up = rng.uniform(0.6, 1.1)
            out = Vector((math.cos(a), math.sin(a), 0))
            side = Vector((-out.y, out.x, 0))
            pts = []
            for k in range(9):
                t = k / 8
                pts.append(Vector((cx, cy, cz)) + out * (L * t) + Vector((0, 0, L * up * t * (1 - t) * 1.6)))
            mb.tube_path([tuple(p) for p in pts], 0.003, material, segments=4)
            for k in range(1, 8):
                t = k / 8
                p = pts[k]
                pl = 0.12 * L * math.sin(math.pi * t) + 0.01
                for s in (-1, 1):
                    tip = p + side * (s * pl) + out * (pl * 0.3) - Vector((0, 0, pl * 0.25))
                    w = pl * 0.22
                    q = [p, p + (tip - p) * 0.5 + out * w, tip, p + (tip - p) * 0.5 - out * w * 0.6]
                    mb.quad([tuple(v) for v in q], material, smooth=True)
    obj = mb.to_object(coll, mats)
    tag(obj, role="vegetation", grounded=False)
    return obj


def moss_patch(coll, mats, name: str, center, radius: float, rng: random.Random, normal: str = "+z",
               thickness: float = 0.02) -> object:
    """Cushion moss: a lumpy displaced mat conforming to a floor (+z) or wall face (±x)."""
    mb = MeshBuilder(name)
    ph = rng.uniform(0, 100)

    def disp(u: float, v: float) -> float:
        r = math.hypot(u, v) / radius
        edge = max(0.0, 1 - r ** 3)
        return thickness * edge * (0.6 + 0.4 * noise.noise(Vector((u * 25 + ph, v * 25, ph))))

    rot = {"+z": (0, 0, 0), "+x": (0, math.pi / 2, 0), "-x": (0, -math.pi / 2, 0)}[normal]
    mb.grid(center, (radius * 2, radius * 2), "moss", xs=16, ys=16, rotation=rot, displace=disp)
    obj = mb.to_object(coll, mats)
    tag(obj, role="vegetation", grounded=False)
    return obj


def habitat_positions(rng: random.Random, candidates, count: int, jitter: float = 0.15):
    """Pick ``count`` positions from weighted habitat candidates [(x, y, z, weight), ...]."""
    if not candidates:
        return []
    total = sum(c[3] for c in candidates)
    out = []
    for _ in range(count):
        r = rng.uniform(0, total)
        acc = 0.0
        for x, y, z, w in candidates:
            acc += w
            if acc >= r:
                out.append((x + rng.uniform(-jitter, jitter), y + rng.uniform(-jitter, jitter), z))
                break
    return out
