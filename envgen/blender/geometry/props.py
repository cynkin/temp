"""Procedurally modelled props at real-world scale.

Each builder models in local space (origin = floor contact centre, +Y = prop
forward) and returns one object (multi-material). Every prop declares its
real-world bounding dimensions and category so validation can check scale and
the asset system can swap it for a scanned/library asset of the same slot.
"""
from __future__ import annotations

import math
import random
from typing import Callable

import bpy
from mathutils import Vector

from ..util import MeshBuilder, place_on_floor, tag

PropFn = Callable[..., bpy.types.Object]
REGISTRY: dict[str, dict] = {}


def prop(category: str, dims: tuple[float, float, float], tags: tuple[str, ...] = ()):
    """Register a prop builder with its nominal real-world dimensions (m) for scale validation."""
    def deco(fn: PropFn) -> PropFn:
        REGISTRY[fn.__name__] = {"fn": fn, "category": category, "dims": dims, "tags": tags}
        return fn
    return deco


def _finish(mb: MeshBuilder, coll, mats, location, yaw_deg: float, name: str, grounded: bool = True,
            rotation=None) -> bpy.types.Object:
    rot = rotation if rotation is not None else (0, 0, math.radians(yaw_deg))
    obj = mb.to_object(coll, mats, location=location, rotation=rot)
    info = REGISTRY.get(name, {})
    tag(obj, role="prop", category=info.get("category", "misc"), grounded=grounded,
        nominal_dims=list(info.get("dims", (0, 0, 0))))
    if grounded:
        place_on_floor(obj, location[2])
    return obj


def _caster(mb: MeshBuilder, x: float, y: float, z_top: float, r: float = 0.05, swivel: float = 0.0):
    off = 0.03
    cx, cy = x + off * math.sin(swivel), y + off * math.cos(swivel)
    mb.cylinder((x, y, z_top), (x, y, r * 2 + 0.02), 0.012, "chrome_worn")
    mb.box((cx, cy, r + 0.02), (0.045, 0.02, 0.05), "chrome_worn", bevel=0.004)
    ax = Vector((math.cos(swivel), -math.sin(swivel), 0))
    mb.torus((cx, cy, r), r * 0.8, r * 0.28, "rubber_black", axis=tuple(ax), major_seg=16, minor_seg=6)
    mb.cylinder(tuple(Vector((cx, cy, r)) - ax * 0.02), tuple(Vector((cx, cy, r)) + ax * 0.02), r * 0.55,
                "painted_metal_white", segments=12)


# ============================================================================= medical

@prop("furniture.medical.gurney", (0.82, 2.05, 0.95), ("hospital", "bed", "stretcher", "wheeled"))
def gurney(coll, mats, location, yaw_deg=0.0, rng: random.Random | None = None, sheet: str = "on",
           head_raise_deg: float = 28.0, rail_down: str = "left") -> bpy.types.Object:
    rng = rng or random.Random(0)
    mb = MeshBuilder("gurney")
    L, W, deck = 1.95, 0.7, 0.55
    # chassis: two longitudinal tubes + cross members + column legs + casters
    for sx in (-1, 1):
        mb.cylinder((sx * W / 2, -L / 2 + 0.05, 0.2), (sx * W / 2, L / 2 - 0.05, 0.2), 0.022, "chrome_worn")
        mb.cylinder((sx * (W / 2 - 0.02), -L / 2, deck - 0.05), (sx * (W / 2 - 0.02), L / 2, deck - 0.05), 0.02,
                    "painted_metal_white")
    for y in (-L / 2 + 0.05, L / 2 - 0.05):
        mb.cylinder((-W / 2, y, 0.2), (W / 2, y, 0.2), 0.022, "chrome_worn")
        for sx in (-1, 1):
            mb.cylinder((sx * W / 2, y, 0.2), (sx * (W / 2 - 0.02), y, deck - 0.05), 0.018, "chrome_worn")
            _caster(mb, sx * W / 2, y, 0.2, swivel=rng.uniform(-1.2, 1.2))
    mb.box((0, 0, 0.28), (0.25, 0.4, 0.16), "painted_metal_white", bevel=0.02)  # hydraulic housing
    mb.cylinder((0, 0, 0.36), (0, 0, deck - 0.05), 0.04, "chrome_worn")
    # deck + mattress (foot section flat, head section raised)
    head_len = 0.75
    foot_len = L - head_len
    mb.box((0, -L / 2 + foot_len / 2, deck), (W - 0.04, foot_len, 0.03), "painted_metal_white", bevel=0.006)
    mb.box((0, -L / 2 + foot_len / 2, deck + 0.08), (W - 0.02, foot_len - 0.01, 0.13), "vinyl_mattress", bevel=0.04,
           segments=3)
    a = math.radians(head_raise_deg)
    hy0 = -L / 2 + foot_len
    hc = Vector((0, hy0 + math.cos(a) * head_len / 2, deck + 0.08 + math.sin(a) * head_len / 2))
    mb.box(tuple(hc), (W - 0.02, head_len, 0.13), "vinyl_mattress", bevel=0.04, segments=3, rotation=(a, 0, 0))
    # side rails: one raised, one dropped
    for sx in (-1, 1):
        down = (rail_down == "left" and sx < 0) or (rail_down == "right" and sx > 0)
        z = deck + (0.0 if down else 0.3)
        x = sx * (W / 2 + 0.03)
        mb.cylinder((x, -0.4, z), (x, 0.55, z), 0.014, "chrome_worn")
        mb.cylinder((x, -0.4, z - 0.12), (x, 0.55, z - 0.12), 0.012, "chrome_worn")
        for y in (-0.35, 0.1, 0.5):
            mb.cylinder((x, y, z - 0.12), (x, y, z), 0.01, "chrome_worn")
    # head / foot boards
    for yb, hb in ((L / 2 + 0.02, 0.42), (-L / 2 - 0.02, 0.3)):  # moulded beige end panels with a grab slot
        mb.box((0, yb, deck + hb / 2 + 0.04), (W + 0.02, 0.035, hb), "plastic_beige", bevel=0.016, segments=3)
        mb.box((0, yb, deck + hb + 0.01), (W * 0.5, 0.04, 0.05), "chrome_worn", bevel=0.01)
    # sheet
    if sheet != "none":
        _sheet(mb, W, foot_len, deck + 0.145, L, rng, half_off=(sheet == "half_off"))
    return _finish(mb, coll, mats, location, yaw_deg, "gurney")


def _sheet(mb: MeshBuilder, W: float, foot_len: float, top: float, L: float, rng: random.Random,
           half_off: bool) -> None:
    """Draped cloth: parametric (u = arc length across, v = along) so vertical drops aren't stretched.

    ``half_off`` slides the sheet sideways: one side bunches on the mattress, the other drags to the floor.
    """
    hw = W / 2 + 0.01
    shift = 0.38 if half_off else 0.0
    ul = [-0.45 + shift, 0.45 + shift + (0.12 if half_off else 0.0)]
    vs = (-L / 2 + 0.02, -L / 2 + foot_len + 0.1)
    nu, nv = 40, 26
    phase = rng.uniform(0, 10)
    rows = []
    for j in range(nv + 1):
        v = vs[0] + (vs[1] - vs[0]) * j / nv
        row = []
        for i in range(nu + 1):
            s = ul[0] - hw + (ul[1] - ul[0] + 2 * hw) * i / nu  # arc-length coordinate
            corner_r = 0.05
            if abs(s) <= hw:
                x, z = s, top
                wr = 0.012 * math.sin(s * 23 + v * 3 + phase) * math.sin(v * 9 + phase)
                if half_off and s < -hw + 0.35:  # bunched folds on the far side
                    wr += 0.03 * max(0.0, math.sin(s * 30 + v * 2 + phase))
                z += wr
            else:
                over = abs(s) - hw
                side = math.copysign(1, s)
                if over < corner_r * math.pi / 2:
                    t = over / corner_r
                    x = side * (hw + math.sin(t) * corner_r)
                    z = top - (1 - math.cos(t)) * corner_r
                else:
                    drop = over - corner_r * math.pi / 2
                    flare = 0.04 * drop + 0.02 * math.sin(v * 7 + phase) * drop
                    x = side * (hw + corner_r + flare)
                    z = top - corner_r - drop
                    # floor contact: cloth pools on the ground instead of passing through it
                    if z < 0.004:  # crumples into folds at the floor instead of sliding out sideways
                        x += side * min(0.12, (0.004 - z) * 0.5)
                        z = 0.004 + 0.012 * abs(math.sin(v * 13 + s * 20 + phase))
                    z += 0.01 * math.sin(v * 15 + s * 5 + phase)
            row.append(mb.bm.verts.new((x, v, z)))
        rows.append(row)
    mb._begin()
    for j in range(nv):
        for i in range(nu):
            mb.bm.faces.new((rows[j][i], rows[j][i + 1], rows[j + 1][i + 1], rows[j + 1][i]))
    mb._finish([], "sheet_cloth", smooth=True)


@prop("furniture.medical.wheelchair", (0.66, 1.05, 0.92), ("hospital", "wheelchair", "wheeled"))
def wheelchair(coll, mats, location, yaw_deg=0.0, rng=None, **_) -> bpy.types.Object:
    rng = rng or random.Random(0)
    mb = MeshBuilder("wheelchair")
    W, R = 0.56, 0.3
    seat_z = 0.5
    for sx in (-1, 1):
        x = sx * (W / 2 + 0.04)
        c = (x, -0.1, R)
        mb.torus(c, R - 0.012, 0.014, "rubber_black", axis=(1, 0, 0), major_seg=40, minor_seg=8)
        mb.torus(c, R - 0.03, 0.007, "chrome_worn", axis=(1, 0, 0), major_seg=40, minor_seg=6)
        mb.torus((x + sx * 0.03, -0.1, R), R - 0.035, 0.007, "chrome_worn", axis=(1, 0, 0), major_seg=40,
                 minor_seg=6)  # hand rim
        for k in range(18):
            a = 2 * math.pi * k / 18
            mb.cylinder(c, (x, -0.1 + math.cos(a) * (R - 0.03), R + math.sin(a) * (R - 0.03)), 0.0018,
                        "chrome_worn", segments=4)
        mb.cylinder((x - sx * 0.02, -0.1, R), (x + sx * 0.02, -0.1, R), 0.035, "painted_metal_white")
        # side frame
        xf = sx * W / 2
        mb.tube_path([(xf, 0.35, 0.12), (xf, 0.35, seat_z), (xf, -0.2, seat_z), (xf, -0.25, 0.95)], 0.012,
                     "chrome_worn")
        mb.cylinder((xf, -0.25, 0.95), (xf, -0.38, 0.97), 0.016, "rubber_black")  # push handle grip
        mb.cylinder((xf, 0.35, 0.12), (xf, -0.1, R), 0.011, "chrome_worn")
        mb.tube_path([(xf, -0.2, seat_z + 0.22), (xf, 0.25, seat_z + 0.22)], 0.01, "chrome_worn")  # armrest
        mb.box((xf, 0.02, seat_z + 0.23), (0.05, 0.3, 0.025), "rubber_black", bevel=0.008)
        _caster(mb, xf, 0.38, 0.12, r=0.06, swivel=rng.uniform(-0.6, 0.6))
        # footrest
        mb.cylinder((xf, 0.35, 0.3), (xf * 0.6, 0.48, 0.12), 0.01, "chrome_worn")
        mb.box((xf * 0.55, 0.5, 0.11), (0.2, 0.14, 0.012), "painted_metal_white", bevel=0.004,
               rotation=(0.2 * sx, 0, 0))
    # sagging upholstery
    seat = lambda x, y: -0.03 * (1 - (2 * x / W) ** 2) * (1 - ((y - 0.07) / 0.28) ** 2)  # noqa: E731
    mb.grid((0, 0.07, seat_z + 0.01), (W, 0.55), "vinyl_mattress", xs=8, ys=8, displace=seat)
    back = lambda x, y: -0.035 * (1 - (2 * x / W) ** 2)  # noqa: E731
    mb.grid((0, -0.22, seat_z + 0.24), (W, 0.42), "vinyl_mattress", xs=8, ys=6, rotation=(math.radians(98), 0, 0),
            displace=back)
    mb.cylinder((-W / 2, 0.0, seat_z - 0.05), (W / 2, -0.15, seat_z - 0.05), 0.01, "chrome_worn")  # X brace
    mb.cylinder((-W / 2, -0.15, seat_z - 0.05), (W / 2, 0.0, seat_z - 0.05), 0.01, "chrome_worn")
    return _finish(mb, coll, mats, location, yaw_deg, "wheelchair")


@prop("furniture.medical.iv_stand", (0.6, 0.6, 2.0), ("hospital", "iv", "drip", "pole"))
def iv_stand(coll, mats, location, yaw_deg=0.0, rng=None, toppled=False, **_) -> bpy.types.Object:
    rng = rng or random.Random(0)
    mb = MeshBuilder("iv_stand")
    mb.cylinder((0, 0, 0.1), (0, 0, 1.95), 0.012, "chrome_worn")
    mb.cylinder((0, 0, 0.1), (0, 0, 0.6), 0.016, "chrome_worn")
    for k in range(5):
        a = 2 * math.pi * k / 5
        end = (math.cos(a) * 0.3, math.sin(a) * 0.3, 0.09)
        mb.cylinder((0, 0, 0.12), end, 0.012, "chrome_worn")
        mb.sphere((end[0], end[1], 0.03), 0.028, "rubber_black", subdiv=1)
    for k in range(4):
        a = math.pi / 2 * k + 0.4
        tip = (math.cos(a) * 0.12, math.sin(a) * 0.12, 1.97)
        mb.tube_path([(0, 0, 1.93), tip, (tip[0] * 1.1, tip[1] * 1.1, 1.92)], 0.005, "chrome_worn")
    # IV bag + line (bag hangs from a hook)
    mb.sphere((0.13, 0.05, 1.78), 0.07, "glass", subdiv=2, scale=(0.75, 0.25, 1.4))
    mb.tube_path([(0.13, 0.05, 1.68), (0.14, 0.06, 1.3), (0.1, 0.08, 0.9), (0.18, 0.2, 0.75)], 0.0025, "glass")
    if toppled:
        # lying across the floor: base legs prop one end (~0.3 m), pole tip rests on the floor.
        # tilt past horizontal by asin(leg_reach / pole_length) so both ends make contact.
        tilt = math.pi / 2 + math.asin(0.3 / 1.95)
        return _finish(mb, coll, mats, location, 0.0, "iv_stand", rotation=(tilt, 0, math.radians(yaw_deg)))
    return _finish(mb, coll, mats, location, yaw_deg, "iv_stand")


@prop("furniture.medical.cart", (0.66, 0.66, 1.04), ("hospital", "cart", "trolley", "records"))
def medical_cart(coll, mats, location, yaw_deg=0.0, rng=None, toppled=False, **_) -> bpy.types.Object:
    rng = rng or random.Random(0)
    mb = MeshBuilder("medical_cart")
    W, D, H = 0.58, 0.45, 0.92
    mb.box((0, 0, H / 2 + 0.1), (W, D, H - 0.06), "painted_metal_white", bevel=0.015)
    mb.box((0, 0, H + 0.1), (W + 0.04, D + 0.04, 0.03), "plastic_blue", bevel=0.01)
    for k in range(4):  # drawers with recessed handles
        z = 0.25 + k * 0.19
        out = 0.0 if not toppled or k != 1 else 0.2
        mb.box((0, D / 2 + 0.003 + out, z), (W - 0.06, 0.012, 0.17), "painted_metal_blue", bevel=0.004)
        mb.box((0, D / 2 + 0.014 + out, z + 0.04), (0.18, 0.02, 0.02), "chrome_worn", bevel=0.005)
    mb.cylinder((-W / 2 - 0.03, -D / 2 + 0.04, H - 0.05), (W / 2 + 0.03, -D / 2 + 0.04, H - 0.05), 0.012, "chrome_worn")
    for sx in (-1, 1):
        mb.cylinder((sx * (W / 2 + 0.03), -D / 2 + 0.04, H - 0.05), (sx * (W / 2 - 0.02), -D / 2 + 0.04, H - 0.15),
                    0.012, "chrome_worn")
        for sy in (-1, 1):
            _caster(mb, sx * (W / 2 - 0.05), sy * (D / 2 - 0.05), 0.12, r=0.045, swivel=rng.uniform(-1, 1))
    if toppled:
        return _finish(mb, coll, mats, location, 0.0, "medical_cart",
                       rotation=(0, math.radians(90), math.radians(yaw_deg)))
    return _finish(mb, coll, mats, location, yaw_deg, "medical_cart")


@prop("furniture.seating.bench", (1.75, 0.6, 0.82), ("hospital", "waiting", "chairs", "bench"))
def waiting_bench(coll, mats, location, yaw_deg=0.0, rng=None, seats=3, broken_seat=-1, **_) -> bpy.types.Object:
    rng = rng or random.Random(0)
    mb = MeshBuilder("waiting_bench")
    pitch = 0.56
    span = pitch * seats
    mb.box((0, 0.0, 0.38), (span, 0.06, 0.05), "chrome_worn", bevel=0.01)  # beam
    for x in (-span / 2 + 0.15, span / 2 - 0.15):  # T legs
        mb.box((x, 0, 0.19), (0.05, 0.05, 0.38), "chrome_worn", bevel=0.01)
        mb.box((x, 0, 0.015), (0.06, 0.55, 0.03), "chrome_worn", bevel=0.008)
    for i in range(seats):
        x = -span / 2 + pitch * (i + 0.5)
        broken = i == broken_seat
        tilt = -0.25 if broken else 0.06
        seat_fn = lambda u, v: 0.025 * (2 * u / 0.48) ** 2 - 0.015 * (1 - (2 * v / 0.44) ** 2)  # noqa: E731
        mb.grid((x, 0.03, 0.44 + (-0.04 if broken else 0)), (0.48, 0.44), "plastic_blue", xs=6, ys=6,
                rotation=(tilt, 0, 0), displace=seat_fn)
        mb.box((x, 0.03, 0.42), (0.36, 0.3, 0.03), "chrome_worn")
        if not broken:
            back_fn = lambda u, v: -0.03 * (1 - (2 * u / 0.48) ** 2)  # noqa: E731
            mb.grid((x, -0.2, 0.68), (0.48, 0.42), "plastic_blue", xs=6, ys=6, rotation=(math.radians(100), 0, 0),
                    displace=back_fn)
            mb.cylinder((x, -0.17, 0.4), (x, -0.21, 0.55), 0.012, "chrome_worn")
        if 0 < i:
            xa = x - pitch / 2
            mb.tube_path([(xa, 0.0, 0.4), (xa, 0.0, 0.62), (xa, 0.22, 0.62), (xa, 0.24, 0.55)], 0.011, "chrome_worn")
    return _finish(mb, coll, mats, location, yaw_deg, "waiting_bench")


@prop("signage.caution_sign", (0.3, 0.5, 0.62), ("caution", "wet floor", "sign"))
def caution_sign(coll, mats, location, yaw_deg=0.0, rng=None, **_) -> bpy.types.Object:
    mb = MeshBuilder("caution_sign")
    a = math.radians(12)
    for s in (-1, 1):
        c = (0, s * (0.12 + math.sin(a) * 0.3), 0.3)
        mb.box(c, (0.3, 0.012, 0.6), "plastic_yellow", bevel=0.01, rotation=(s * a, 0, 0))
        band = (0, s * (0.12 + math.sin(a) * 0.3 + 0.007), 0.36)
        mb.box(band, (0.24, 0.004, 0.16), "rubber_black", rotation=(s * a, 0, 0))
    mb.box((0, 0, 0.61), (0.08, 0.06, 0.04), "plastic_yellow", bevel=0.01)
    return _finish(mb, coll, mats, location, yaw_deg, "caution_sign")


@prop("fixture.extinguisher", (0.2, 0.2, 0.62), ("fire", "extinguisher", "wall"))
def extinguisher(coll, mats, location, yaw_deg=0.0, rng=None, **_) -> bpy.types.Object:
    mb = MeshBuilder("extinguisher")
    mb.cylinder((0, 0, 0.0), (0, 0, 0.48), 0.075, "red_metal", segments=20)
    mb.sphere((0, 0, 0.48), 0.075, "red_metal", subdiv=2, scale=(1, 1, 0.5))
    mb.cylinder((0, 0, 0.5), (0, 0, 0.58), 0.02, "chrome_worn")
    mb.box((0.03, 0, 0.6), (0.11, 0.02, 0.015), "chrome_worn", rotation=(0, 0.2, 0))
    mb.tube_path([(-0.02, 0, 0.56), (-0.09, 0, 0.5), (-0.1, 0, 0.25), (-0.08, 0.02, 0.15)], 0.009, "rubber_black")
    mb.box((0, -0.09, 0.3), (0.12, 0.02, 0.42), "chrome_worn", bevel=0.004)  # wall bracket
    return _finish(mb, coll, mats, location, yaw_deg, "extinguisher", grounded=False)


@prop("fixture.notice_board", (1.2, 0.04, 0.9), ("notice", "board", "cork"))
def notice_board(coll, mats, location, yaw_deg=0.0, rng=None, **_) -> bpy.types.Object:
    mb = MeshBuilder("notice_board")
    mb.box((0, 0, 0), (1.2, 0.025, 0.9), "corkboard")
    for (x, z, w, h) in ((0, 0.46, 1.24, 0.04), (0, -0.46, 1.24, 0.04), (-0.61, 0, 0.04, 0.96), (0.61, 0, 0.04, 0.96)):
        mb.box((x, 0.005, z), (w, 0.04, h), "wood_worn", bevel=0.006)
    return _finish(mb, coll, mats, location, yaw_deg, "notice_board", grounded=False)


# ============================================================================= debris & decay

def debris_field(coll, mats, name: str, center, radius: float, count: int, rng: random.Random,
                 tile_fragments: int = 0, ceiling_h: float = 2.75, bias=(0.0, 0.0)) -> bpy.types.Object:
    """Collapse debris: plaster chunks, broken ceiling tile fragments and a fine dust bed.

    Distribution follows a fall: dense directly beneath the hole, sparse at the edges,
    larger pieces nearer the centre, fragments resting flat or leaning on each other.
    """
    mb = MeshBuilder(name)
    cx, cy = center
    for _ in range(count):
        r = radius * (rng.random() ** 1.6)
        a = rng.uniform(0, 2 * math.pi)
        x, y = cx + r * math.cos(a) + bias[0] * r, cy + r * math.sin(a) + bias[1] * r
        s = rng.uniform(0.012, 0.05) * (1.4 - r / radius)
        mb.sphere((x, y, s * 0.35), s, "debris_plaster", subdiv=1,
                  scale=(rng.uniform(0.7, 1.5), rng.uniform(0.7, 1.5), rng.uniform(0.3, 0.6)))
    for _ in range(tile_fragments):
        r = radius * (rng.random() ** 1.2)
        a = rng.uniform(0, 2 * math.pi)
        x, y = cx + r * math.cos(a), cy + r * math.sin(a)
        w, h = rng.uniform(0.12, 0.45), rng.uniform(0.1, 0.4)
        pts = [(-w / 2 + rng.uniform(0, 0.05), -h / 2), (w / 2, -h / 2 + rng.uniform(0, 0.06)),
               (w / 2 - rng.uniform(0, 0.08), h / 2), (-w / 2 + rng.uniform(0, 0.12), h / 2 - rng.uniform(0, 0.05))]
        if rng.random() < 0.5:
            pts.insert(2, (w / 2 + rng.uniform(-0.04, 0.02), 0.0))
        from mathutils import Euler, Matrix
        lean = rng.uniform(0, 0.35) if rng.random() < 0.35 else rng.uniform(0, 0.05)
        lift = abs(math.sin(lean)) * h / 2 + 0.001  # leaning pieces rest on their lower edge, never below floor
        m = Matrix.Translation((x, y, lift)) @ Euler((lean, rng.uniform(-0.05, 0.05), rng.uniform(0, 6.28))
                                                     ).to_matrix().to_4x4()
        mb.prism(pts, 0.0, 0.016, "ceiling_tile", transform=m)
    # dust bed: a low, irregular mound of fine plaster dust
    dust = lambda u, v: max(0.0, 0.012 * (1 - (u * u + v * v) / (radius * radius)) + 0.004 * math.sin(u * 17) *  # noqa
                            math.cos(v * 13))
    mb.grid((cx, cy, 0.0015), (radius * 2.0, radius * 2.0), "debris_plaster", xs=18, ys=18, displace=dust)
    obj = mb.to_object(coll, mats)
    tag(obj, role="debris", grounded=False)
    return obj


def puddle(coll, mats, name: str, center, radius: float, rng: random.Random, z: float = 0.0015,
           elongation: float = 1.0) -> bpy.types.Object:
    """Irregular standing-water decal (noise-modulated outline, a few mm thick)."""
    mb = MeshBuilder(name)
    n = 48
    ph = [rng.uniform(0, 6.28) for _ in range(4)]
    pts = []
    for k in range(n):
        a = 2 * math.pi * k / n
        r = radius * (1 + 0.25 * math.sin(3 * a + ph[0]) + 0.12 * math.sin(7 * a + ph[1]) + 0.06 *
                      math.sin(13 * a + ph[2]))
        pts.append((math.cos(a) * r * elongation, math.sin(a) * r))
    from mathutils import Matrix
    mb.prism(pts, 0.0, z, "puddle_water", transform=Matrix.Translation((center[0], center[1], 0.0)))
    obj = mb.to_object(coll, mats)
    tag(obj, role="decal_water", grounded=False)
    return obj


def papers(coll, mats, name: str, origin, count: int, drift, spread: float, rng: random.Random,
           folder_ratio: float = 0.15) -> bpy.types.Object:
    """Scattered sheets: a dense spill at ``origin`` thinning out along ``drift`` (direction of flight / draught)."""
    mb = MeshBuilder(name)
    dx, dy = drift
    norm = math.hypot(dx, dy) or 1.0
    dx, dy = dx / norm, dy / norm
    for k in range(count):
        t = rng.random() ** 1.8 * spread
        lateral = rng.gauss(0, 0.25 + t * 0.12)
        x = origin[0] + dx * t + -dy * lateral
        y = origin[1] + dy * t + dx * lateral
        folder = rng.random() < folder_ratio
        w, h = (0.24, 0.32) if folder else (0.21, 0.297)
        curl = rng.uniform(0.0, 0.03)
        ph = rng.uniform(0, 6.28)
        disp = lambda u, v, c=curl, p=ph: c * (u / 0.12) ** 2 + 0.004 * math.sin(v * 25 + p)  # noqa: E731
        mb.grid((x, y, 0.003 + 0.0015 * (k % 4)), (w, h), "paper" if not folder else "wood_worn",
                xs=4, ys=4, rotation=(0, 0, rng.uniform(0, 6.28)), displace=disp)
    obj = mb.to_object(coll, mats)
    tag(obj, role="papers", grounded=False)
    return obj


def hanging_cables(coll, mats, name: str, center, ceiling_h: float, slab_z: float, count: int,
                   rng: random.Random) -> bpy.types.Object:
    """Cables and a flex conduit spilling out of a ceiling hole, sagging under their own weight."""
    mb = MeshBuilder(name)
    for _ in range(count):
        x0, y0 = center[0] + rng.uniform(-0.4, 0.4), center[1] + rng.uniform(-0.4, 0.4)
        x1, y1 = x0 + rng.uniform(-0.5, 0.5), y0 + rng.uniform(-0.5, 0.5)
        drop = rng.uniform(0.4, 1.4)
        pts = []
        for k in range(9):
            t = k / 8
            sag = math.sin(t * math.pi) * drop
            pts.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, slab_z - 0.1 - sag - (ceiling_h - slab_z) * 0.0))
        # free end hangs down
        end = pts[-1]
        pts.append((end[0], end[1], end[2] - rng.uniform(0.2, 0.9)))
        mb.tube_path(pts, rng.uniform(0.005, 0.012), "cable_black", segments=6)
    obj = mb.to_object(coll, mats)
    tag(obj, role="cables", grounded=False)
    return obj


def glass_shards(coll, mats, name: str, center, spread: float, count: int, rng: random.Random) -> bpy.types.Object:
    mb = MeshBuilder(name)
    from mathutils import Euler, Matrix
    for _ in range(count):
        x = center[0] + rng.gauss(0, spread * 0.5)
        y = center[1] + rng.gauss(0, spread)
        s = rng.uniform(0.02, 0.1)
        pts = [(0, 0), (s, rng.uniform(-s / 3, s / 3)), (rng.uniform(0, s), rng.uniform(s / 2, s))]
        m = Matrix.Translation((x, y, 0.0)) @ Euler((0, 0, rng.uniform(0, 6.28))).to_matrix().to_4x4()
        mb.prism(pts, 0.0, 0.004, "glass", transform=m)
    obj = mb.to_object(coll, mats)
    tag(obj, role="debris", grounded=False)
    return obj


# ============================================================================= industrial / underground

@prop("tool.toolbox", (0.52, 0.24, 0.42), ("toolbox", "tools", "maintenance", "underground"))
def toolbox(coll, mats, location, yaw_deg=0.0, rng=None, open_lid=True, **_) -> bpy.types.Object:
    """Pressed-steel cantilever toolbox (red enamel), lid thrown open, a tray of spanners inside."""
    rng = rng or random.Random(0)
    mb = MeshBuilder("toolbox")
    W, D, H = 0.5, 0.22, 0.2
    mb.box((0, 0, H / 2), (W, D, H), "red_metal", bevel=0.006)
    mb.box((0, 0, H + 0.002), (W - 0.02, D - 0.02, 0.006), "rubber_black")  # dark interior read from above
    if open_lid:  # lid hinged on the back edge, swung past vertical against its stay
        a = math.radians(110)
        mb.box((0, D / 2 + math.sin(a - math.pi / 2) * 0.05 + 0.005, H + math.cos(a - math.pi / 2) * 0.05),
               (W, 0.1, 0.012), "red_metal", bevel=0.004, rotation=(a, 0, 0))
        for k in range(5):  # spanner handles poking out of the top tray
            x = -0.18 + k * 0.08 + rng.uniform(-0.01, 0.01)
            mb.box((x, rng.uniform(-0.04, 0.04), H + 0.03), (0.018, 0.006, 0.09), "chrome_worn",
                   rotation=(rng.uniform(-0.4, 0.4), rng.uniform(-0.3, 0.3), 0))
    else:
        mb.box((0, 0, H + 0.03), (W, D, 0.06), "red_metal", bevel=0.006)
    # carry handle on swivel lugs
    mb.cylinder((-0.14, 0, H + 0.075), (0.14, 0, H + 0.075), 0.009, "rubber_black")
    for s in (-1, 1):
        mb.cylinder((s * 0.15, 0, H - 0.01), (s * 0.15, 0, H + 0.08), 0.006, "chrome_worn")
        mb.box((s * (W / 2 + 0.004), 0, H * 0.75), (0.008, 0.05, 0.04), "chrome_worn")  # latch
    return _finish(mb, coll, mats, location, yaw_deg, "toolbox")


@prop("container.oil_drum", (0.6, 0.6, 0.88), ("drum", "barrel", "oil", "industrial"))
def oil_drum(coll, mats, location, yaw_deg=0.0, rng=None, lying=False, dented=0.0, **_) -> bpy.types.Object:
    """205 l steel drum: two rolling hoops, chimes, bung caps. ``lying`` rolls it onto its side."""
    mb = MeshBuilder("oil_drum")
    r, h = 0.286, 0.88
    mb.cylinder((0, 0, 0.012), (0, 0, h - 0.012), r, "drum_paint", segments=32)
    for z in (0.012, h - 0.012):  # chimes (rolled rims)
        mb.torus((0, 0, z), r - 0.004, 0.012, "drum_paint", axis=(0, 0, 1), major_seg=32, minor_seg=6)
    for z in (h / 3, 2 * h / 3):  # rolling hoops
        mb.torus((0, 0, z), r + 0.002, 0.009, "drum_paint", axis=(0, 0, 1), major_seg=32, minor_seg=6)
    mb.cylinder((0.17, 0, h - 0.012), (0.17, 0, h + 0.004), 0.03, "chrome_worn", segments=10)
    mb.cylinder((-0.19, 0.05, h - 0.012), (-0.19, 0.05, h + 0.002), 0.016, "chrome_worn", segments=8)
    if lying:
        obj = _finish(mb, coll, mats, location, 0.0, "oil_drum", grounded=False,
                      rotation=(0, math.pi / 2, math.radians(yaw_deg)))
        obj.location.z = location[2] + r + 0.012
        return obj
    return _finish(mb, coll, mats, location, yaw_deg, "oil_drum")


@prop("barrier.sandbag", (0.62, 0.36, 0.14), ("sandbag", "barricade", "military", "flood"))
def sandbag(coll, mats, location, yaw_deg=0.0, rng=None, **_) -> bpy.types.Object:
    rng = rng or random.Random(0)
    mb = MeshBuilder("sandbag")
    _sandbag(mb, (0, 0, 0), 0.0, rng)
    return _finish(mb, coll, mats, location, yaw_deg, "sandbag")


def _sandbag(mb: MeshBuilder, c, yaw: float, rng: random.Random, z0: float = 0.0) -> None:
    """One filled hessian bag: a slumped pillow (top bulge, flat underside) with a tied, folded end."""
    L, Wd, T = 0.6, 0.34, 0.13
    ca, sa = math.cos(yaw), math.sin(yaw)

    def disp(u, v, ph=rng.uniform(0, 6.28)):
        fu, fv = 1 - (2 * u / L) ** 4, 1 - (2 * v / Wd) ** 2
        return max(0.0, T * fu * fv) + 0.006 * math.sin(u * 23 + ph) * math.sin(v * 17)
    mb.grid((c[0], c[1], z0 + 0.004), (L, Wd), "hessian", xs=10, ys=6, rotation=(0, 0, yaw), displace=disp)
    mb.box((c[0], c[1], z0 + 0.014), (L * 0.94, Wd * 0.9, 0.028), "hessian", bevel=0.012, rotation=(0, 0, yaw))
    ex, ey = c[0] + ca * (L / 2 + 0.03), c[1] + sa * (L / 2 + 0.03)
    mb.box((ex, ey, z0 + 0.035), (0.07, 0.16, 0.05), "hessian", bevel=0.01, rotation=(0, 0, yaw))


def sandbag_wall(coll, mats, name: str, start, end, courses: int, rng: random.Random, rows: int = 1,
                 row_gap: float = 0.38) -> bpy.types.Object:
    """Stretcher-bonded sandbag wall between two floor points; upper courses stagger by half a bag and slump.

    ``rows`` > 1 adds rows behind the first (+normal side), each one course lower (a stepped, battered position).
    """
    mb = MeshBuilder(name)
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    yaw = math.atan2(dy, dx)
    ux, uy = dx / length, dy / length
    for row in range(rows):
        sx, sy = start[0], start[1] + row_gap * row
        _sandbag_courses(mb, sx, sy, ux, uy, length, yaw, max(1, courses - row), rng)
    obj = mb.to_object(coll, mats)
    tag(obj, role="prop", category="barrier.sandbag_wall", grounded=False)
    return obj


def _sandbag_courses(mb, sx, sy, ux, uy, length, yaw, courses, rng) -> None:
    for c in range(courses):
        n = max(1, int((length - (0.3 if c % 2 else 0.0)) / 0.6))
        off = 0.3 if c % 2 else 0.0
        inset = 0.04 * c  # battered wall: each course steps back a little
        for k in range(n):
            t = off + 0.3 + k * 0.6 + rng.uniform(-0.03, 0.03)
            x, y = sx + ux * t - uy * inset, sy + uy * t + ux * inset
            _sandbag(mb, (x, y), yaw + rng.uniform(-0.06, 0.06), rng, z0=c * 0.12)


def floor_cable(coll, mats, name: str, points, rng: random.Random, radius: float = 0.008) -> bpy.types.Object:
    """An extension cable snaking across the floor between waypoints (lies flat, never straight)."""
    mb = MeshBuilder(name)
    pts = []
    for (x0, y0), (x1, y1) in zip(points[:-1], points[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        n = max(3, int(seg / 0.25))
        ph = rng.uniform(0, 6.28)
        for k in range(n):
            t = k / n
            wob = 0.06 * math.sin(t * 9 + ph)
            nx, ny = -(y1 - y0) / seg, (x1 - x0) / seg
            pts.append((x0 + (x1 - x0) * t + nx * wob, y0 + (y1 - y0) * t + ny * wob, radius))
    pts.append((points[-1][0], points[-1][1], radius))
    mb.tube_path(pts, radius, "cable_black", segments=6)
    obj = mb.to_object(coll, mats)
    tag(obj, role="cables", grounded=False)
    return obj
