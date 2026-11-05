"""Industrial structures: pipe runs, staircases, steel columns / beams, railings, grating.

All builders take explicit world-space parameters (metres) so they can be placed by
specs (``structures:`` list) or story beats in any layout.
"""
from __future__ import annotations

import math
import random

from mathutils import Vector

from ..util import MeshBuilder, tag


def pipe_run(coll, mats, name: str, points, radius: float = 0.08, material: str = "galvanized_duct",
             flange_every: float = 3.0, support: str | None = "wall", support_every: float = 2.0,
             rng: random.Random | None = None) -> object:
    """Pipe along a polyline with bent elbows, bolted flanges at joints and supports (wall brackets / floor stands)."""
    rng = rng or random.Random(0)
    mb = MeshBuilder(name)
    pts = [Vector(p) for p in points]
    seg = max(16, int(radius * 200))
    bend_r = radius * 2.0
    trimmed = []  # shorten straight segments to leave room for elbows
    for i, (a, b) in enumerate(zip(pts[:-1], pts[1:])):
        d = (b - a).normalized()
        a2 = a + d * (bend_r if i > 0 else 0)
        b2 = b - d * (bend_r if i < len(pts) - 2 else 0)
        trimmed.append((a2, b2))
        mb.cylinder(tuple(a2), tuple(b2), radius, material, segments=seg)
        length = (b2 - a2).length
        n = int(length / flange_every)
        for k in range(1, n + 1):  # flanged joints with bolt heads
            c = a2 + d * (k * length / (n + 1))
            mb.cylinder(tuple(c - d * 0.015), tuple(c + d * 0.015), radius * 1.45, material, segments=seg)
            side = d.orthogonal().normalized()
            for j in range(8):
                ang = 2 * math.pi * j / 8
                off = (side * math.cos(ang) + d.cross(side) * math.sin(ang)) * radius * 1.25
                mb.cylinder(tuple(c + off - d * 0.03), tuple(c + off + d * 0.03), radius * 0.09, "chrome_worn",
                            segments=6)
        if support:
            k = support_every / 2
            while k < length:
                c = a2 + d * k
                if support == "floor" and c.z > 0.1:
                    mb.cylinder((c.x, c.y, c.z - radius), (c.x, c.y, 0.0), radius * 0.35, "steel_tbar", segments=8)
                    mb.box((c.x, c.y, 0.01), (radius * 2.2, radius * 2.2, 0.02), "steel_tbar")
                elif support == "wall":
                    mb.torus(tuple(c), radius * 1.08, radius * 0.08, "steel_tbar", axis=tuple(d), major_seg=20,
                             minor_seg=4)
                k += support_every
    # elbows: swept arc between consecutive segments
    for i in range(len(pts) - 2):
        a_end = trimmed[i][1]
        b_start = trimmed[i + 1][0]
        corner = pts[i + 1]
        prev = a_end
        for t in [k / 6 for k in range(1, 7)]:
            p = (1 - t) ** 2 * a_end + 2 * (1 - t) * t * corner + t * t * b_start  # quadratic Bezier
            mb.cylinder(tuple(prev), tuple(p), radius, material, segments=seg)
            mb.sphere(tuple(p), radius, material, subdiv=2)
            prev = p
    obj = mb.to_object(coll, mats)
    tag(obj, role="structure", kind="pipe_run")
    return obj


def staircase(coll, mats, name: str, base, direction_deg: float, rise: float, width: float = 1.2,
              riser: float = 0.175, going: float = 0.27, kind: str = "concrete", railings: str = "both",
              landing: float = 1.2) -> object:
    """Straight flight (+ top landing) with stringers, nosings and railings. Riser/going follow building codes
    (2R + G within 0.55-0.70 m)."""
    n = max(1, round(rise / riser))
    r = rise / n
    yaw = math.radians(direction_deg)
    fwd = Vector((math.cos(yaw), math.sin(yaw), 0))
    side = Vector((-fwd.y, fwd.x, 0))
    b = Vector(base)
    mb = MeshBuilder(name)
    tread_mat = "concrete_floor" if kind == "concrete" else "steel_tbar"
    for i in range(n):
        c = b + fwd * (going * (i + 0.5)) + Vector((0, 0, r * (i + 1) - (r / 2 if kind == "concrete" else 0.02)))
        h = r if kind == "concrete" else 0.04
        _oriented_box(mb, c, (going + 0.02, width, h), yaw, tread_mat)
        if kind == "concrete" and i:  # solid under-fill so the flight reads as cast concrete
            fill = c - Vector((0, 0, r * i / 2 + r / 2))
            _oriented_box(mb, fill, (going, width, r * i + 0.001), yaw, tread_mat)
        # worn steel nosing strip
        nose = b + fwd * (going * i + 0.01) + Vector((0, 0, r * (i + 1)))
        _oriented_box(mb, nose, (0.04, width, 0.012), yaw, "chrome_worn")
    top = b + fwd * (going * n) + Vector((0, 0, rise))
    _oriented_box(mb, top + fwd * (landing / 2) - Vector((0, 0, 0.1)), (landing, width, 0.2), yaw, tread_mat)
    if kind == "steel":
        for s in (-1, 1):
            a = b + side * (s * width / 2)
            mb.cylinder(tuple(a), tuple(a + fwd * going * n + Vector((0, 0, rise))), 0.05, "steel_tbar", segments=4)
    if railings in ("both", "left", "right"):
        sides = (-1, 1) if railings == "both" else ((1,) if railings == "left" else (-1,))
        for s in sides:
            off = side * (s * (width / 2 - 0.04))
            p0 = b + off + Vector((0, 0, r + 0.9))
            p1 = top + off + Vector((0, 0, 0.9))
            p2 = p1 + fwd * landing
            mb.tube_path([tuple(p0), tuple(p1), tuple(p2)], 0.021, "painted_metal_white")
            k = 0
            while k <= n:
                post_base = b + off + fwd * (going * min(k, n) + going * 0.5 * (k < n)) + Vector((0, 0, r * min(k + 1, n)))
                mb.cylinder(tuple(post_base), tuple(post_base + Vector((0, 0, 0.9))), 0.018, "painted_metal_white")
                k += 3
            mid0, mid1 = p0 - Vector((0, 0, 0.45)), p1 - Vector((0, 0, 0.45))
            mb.cylinder(tuple(mid0), tuple(mid1), 0.012, "painted_metal_white")
    obj = mb.to_object(coll, mats)
    tag(obj, role="structure", kind="staircase", support=True, steps=n, riser=round(r, 3), going=going)
    return obj


def _oriented_box(mb: MeshBuilder, center, size, yaw: float, material: str):
    mb.box(tuple(center), size, material, bevel=0.006, rotation=(0, 0, yaw))


def steel_column(coll, mats, name: str, base, height: float, flange: float = 0.2, depth: float = 0.2,
                 yaw_deg: float = 0.0, material: str = "galvanized_duct") -> object:
    """Universal (I) column with base plate and anchor bolts."""
    mb = MeshBuilder(name)
    x, y, z = base
    yaw = math.radians(yaw_deg)
    t = 0.015
    for s in (-1, 1):
        off = Vector((math.cos(yaw), math.sin(yaw), 0)) * (s * (depth / 2 - t / 2))
        mb.box((x + off.x, y + off.y, z + height / 2), (t, flange, height), material, rotation=(0, 0, yaw))
    mb.box((x, y, z + height / 2), (depth - 2 * t, 0.01, height), material, rotation=(0, 0, yaw))
    mb.box((x, y, z + 0.01), (depth + 0.15, flange + 0.15, 0.02), material, bevel=0.003, rotation=(0, 0, yaw))
    for dx in (-1, 1):
        for dy in (-1, 1):
            mb.cylinder((x + dx * (depth / 2 + 0.05), y + dy * (flange / 2 + 0.05), z + 0.02),
                        (x + dx * (depth / 2 + 0.05), y + dy * (flange / 2 + 0.05), z + 0.06), 0.012, "chrome_worn",
                        segments=6)
    obj = mb.to_object(coll, mats)
    tag(obj, role="structure", kind="column")
    return obj


def railing(coll, mats, name: str, points, height: float = 1.0, post_every: float = 1.2,
            material: str = "painted_metal_white") -> object:
    """Industrial tube railing (top rail, knee rail, posts, toe board) along a polyline at floor level."""
    mb = MeshBuilder(name)
    pts = [Vector(p) for p in points]
    mb.tube_path([tuple(p + Vector((0, 0, height))) for p in pts], 0.022, material)
    mb.tube_path([tuple(p + Vector((0, 0, height / 2))) for p in pts], 0.016, material)
    for a, b in zip(pts[:-1], pts[1:]):
        L = (b - a).length
        n = max(1, int(L / post_every))
        for k in range(n + 1):
            p = a.lerp(b, k / n)
            mb.cylinder(tuple(p), tuple(p + Vector((0, 0, height))), 0.02, material)
        d = (b - a).normalized()
        yaw = math.atan2(d.y, d.x)
        mb.box(tuple((a + b) / 2 + Vector((0, 0, 0.05))), (L, 0.008, 0.1), material, rotation=(0, 0, yaw))
    obj = mb.to_object(coll, mats)
    tag(obj, role="structure", kind="railing")
    return obj


def cable_sag(coll, mats, name: str, hooks, count: int = 3, sag: float = 0.18, radius: float = 0.009,
              rng: random.Random | None = None, broken_span: int = -1):
    """Cable bundle hung between wall hooks: each span is a catenary-like droop; each cable sags differently.

    ``broken_span`` drops one span's cables to hang free from the previous hook (cut or torn loose).
    """
    rng = rng or random.Random(0)
    mb = MeshBuilder(name)
    hooks = [Vector(h) for h in hooks]
    for h in hooks:  # J-hooks screwed into the wall
        mb.box(tuple(h + Vector((0, 0, 0.02))), (0.03, 0.03, 0.06), "fixture_housing")
    for c in range(count):
        dz = -0.018 * c
        s_c = sag * rng.uniform(0.75, 1.35)
        r = radius * rng.uniform(0.7, 1.3)
        pts = []
        for si, (a, b) in enumerate(zip(hooks[:-1], hooks[1:])):
            if si == broken_span:
                if pts:
                    mb.tube_path(pts + [tuple(a + Vector((0, 0, dz)))], r, "cable_black", segments=6)
                tail = [a + Vector((0.02 * c, 0.15 * k, dz - 0.35 * k * (1 + 0.3 * c))) for k in range(4)]
                mb.tube_path([tuple(p) for p in tail], r, "cable_black", segments=6)
                pts = []
                continue
            n = 8
            for k in range(n + (1 if si == len(hooks) - 2 else 0)):
                t = k / n
                p = a.lerp(b, t) + Vector((0.006 * c, 0, dz - s_c * 4 * t * (1 - t)))
                pts.append(tuple(p))
        if len(pts) > 1:
            mb.tube_path(pts, r, "cable_black", segments=6)
    obj = mb.to_object(coll, mats)
    tag(obj, role="cables", grounded=False)
    return obj
