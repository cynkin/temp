"""Procedural architecture: corridor / room shells with real openings, drop ceilings, services.

Coordinate convention: metres, Z-up. A corridor runs along +Y from y=0 to
y=length, centred on x=0, finished floor at z=0, suspended ceiling at
z=height, structural slab at z=height+plenum. "left" is -X, "right" is +X.

Everything that later systems need to reason about (where doors are, which
ceiling cells collapsed, where the light fixtures hang, wall planes) is
recorded in an :class:`ArchContext` so lighting and storytelling can place
things *because of* the architecture rather than at random.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import bpy
from mathutils import Vector

from ..util import MeshBuilder, collection, seeded, tag


@dataclass
class Opening:
    side: str            # "left" | "right" | "end" | "start"
    kind: str            # "door" | "window" | "double_door"
    center: float        # y (side walls) or x (end walls)
    width: float
    sill: float
    head: float
    meta: dict = field(default_factory=dict)


@dataclass
class ArchContext:
    length: float
    width: float
    height: float
    slab_z: float
    wall_t: float
    openings: list[Opening] = field(default_factory=list)
    collapse_zones: list[dict] = field(default_factory=list)
    missing_cells: list[tuple[float, float]] = field(default_factory=list)   # (x, y) centres of holes
    fixtures: list[dict] = field(default_factory=list)                       # light fixture slots
    bulkheads: list[float] = field(default_factory=list)
    floor_objects: list[str] = field(default_factory=list)                   # names of support surfaces
    notes: list[str] = field(default_factory=list)

    def wall_x(self, side: str) -> float:
        return -self.width / 2 if side == "left" else self.width / 2

    def doors(self, side: str | None = None) -> list[Opening]:
        return [o for o in self.openings if o.kind in ("door", "double_door") and (side is None or o.side == side)]

    def windows(self) -> list[Opening]:
        return [o for o in self.openings if o.kind == "window"]


def _segments(total: float, holes: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Solid intervals of [0,total] after removing ``holes`` (start, end)."""
    out, cur = [], 0.0
    for a, b in sorted(holes):
        if a > cur + 1e-4:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < total - 1e-4:
        out.append((cur, total))
    return out


# ============================================================================= corridor shell

def build_corridor(spec: dict, mats: dict, root: bpy.types.Collection) -> ArchContext:
    lay, arch = spec["layout"], spec.get("architecture", {})
    seed = int(spec.get("seed", 1))
    L, W, H = float(lay["length"]), float(lay["width"]), float(lay["height"])
    plenum = float(lay.get("plenum", 0.65))
    t = float(arch.get("wall_thickness", 0.18))
    ctx = ArchContext(length=L, width=W, height=H, slab_z=H + plenum, wall_t=t)
    coll = collection("Architecture", root)
    bay = float(lay.get("bay", 3.0))

    # ---- openings from spec
    win = arch.get("windows")
    if win:
        for i, yc in enumerate(_bay_centres(L, bay, win.get("bays"))):
            ctx.openings.append(Opening(win.get("side", "left"), "window", yc, float(win.get("width", 1.8)),
                                        float(win.get("sill", 1.0)), float(win.get("head", 2.4)),
                                        meta={"index": i, **(win.get("overrides", {}).get(str(i), {}))}))
    doors = arch.get("doors", {})
    for i, d in enumerate(doors.get("list", [])):
        ctx.openings.append(Opening(d.get("side", "right"), "door", float(d["y"]), float(d.get("width", 1.1)),
                                    0.0, float(d.get("height", 2.15)),
                                    meta={"open": float(d.get("open", 0.0)), "label": d.get("label", f"{i + 1}"),
                                          "index": i}))
    end = arch.get("end", {"kind": "double_door"})
    if end and end.get("kind") == "double_door":
        ctx.openings.append(Opening("end", "double_door", 0.0, float(end.get("width", 1.8)), 0.0,
                                    float(end.get("height", 2.2)), meta=end))

    # ---- floor & structural slab
    fb = MeshBuilder("floor")
    fb.box((0, L / 2, -0.1), (W + 2 * t, L + 2 * t, 0.2), "vinyl_tile_floor")
    floor = fb.to_object(coll, mats)
    tag(floor, role="floor", support=True)
    ctx.floor_objects.append(floor.name)
    sb = MeshBuilder("slab")
    sb.box((0, L / 2, ctx.slab_z + 0.15), (W + 2 * t, L + 2 * t, 0.3), "concrete_slab")
    tag(sb.to_object(coll, mats), role="slab")

    wainscot = arch.get("wainscot", {"height": 1.05})
    wh = float(wainscot.get("height", 1.05))
    # ---- side walls
    for side in ("left", "right"):
        _build_side_wall(ctx, side, wh, mats, coll, arch)
    # ---- end walls
    _build_end_wall(ctx, "start", wh, mats, coll)
    _build_end_wall(ctx, "end", wh, mats, coll)

    # ---- pilasters + ceiling bulkheads give the structural rhythm of the references
    pil = arch.get("pilasters", {"every": bay, "depth": 0.1, "width": 0.32})
    if pil:
        pb = MeshBuilder("pilasters")
        every = float(pil.get("every", bay))
        y = every
        while y < L - 0.5:
            if not any(abs(o.center - y) < o.width / 2 + 0.25 for o in ctx.openings if o.side in ("left", "right")):
                for side in ("left", "right"):
                    sx = 1 if side == "right" else -1
                    d = float(pil.get("depth", 0.1))
                    pb.box((sx * (W / 2 - d / 2), y, (H + 0.0) / 2), (d, float(pil.get("width", 0.32)), H),
                           "plaster_painted", bevel=0.01)
                    pb.box((sx * (W / 2 - d / 2 - 0.006), y, wh / 2), (d + 0.012, float(pil.get("width", 0.32)) + 0.012,
                                                                       wh), "wainscot_paint", bevel=0.008)
            # bulkhead (downstand beam) across the corridor at every bay line
            pb.box((0, y, H - 0.09 + 0.15), (W, 0.34, 0.48), "plaster_painted", bevel=0.01)
            ctx.bulkheads.append(y)
            y += every
        tag(pb.to_object(coll, mats), role="structure")

    _build_ceiling(ctx, spec, mats, coll, seed)
    _build_services(ctx, spec, mats, coll, seed)
    if arch.get("handrail"):
        _build_handrail(ctx, arch["handrail"], mats, coll)
    _build_exterior(ctx, spec, mats, root)
    return ctx


def _bay_centres(L: float, bay: float, bays) -> list[float]:
    centres, y, i = [], bay / 2, 0
    while y < L - bay / 4:
        if bays is None or i in bays:
            centres.append(y)
        y += bay
        i += 1
    return centres


def _build_side_wall(ctx: ArchContext, side: str, wh: float, mats, coll, arch) -> None:
    L, W, H, t = ctx.length, ctx.width, ctx.height, ctx.wall_t
    sx = -1 if side == "left" else 1
    xc = sx * (W / 2 + t / 2)
    top = ctx.slab_z
    ops = [o for o in ctx.openings if o.side == side]
    wb = MeshBuilder(f"wall_{side}")
    holes = [(o.center - o.width / 2, o.center + o.width / 2) for o in ops]
    for a, b in _segments(L, holes):
        wb.box((xc, (a + b) / 2, top / 2), (t, b - a, top), "plaster_painted")
    for o in ops:
        y0, y1 = o.center - o.width / 2, o.center + o.width / 2
        if o.sill > 0:
            wb.box((xc, o.center, o.sill / 2), (t, o.width, o.sill), "plaster_painted")
        wb.box((xc, o.center, (o.head + top) / 2), (t, o.width, top - o.head), "plaster_painted")
    wall = wb.to_object(coll, mats)
    tag(wall, role="wall", side=side)

    # wainscot (proud of the wall), chair-rail cap and coved rubber skirting
    xb = MeshBuilder(f"wainscot_{side}")
    door_holes = [(o.center - o.width / 2 - 0.08, o.center + o.width / 2 + 0.08) for o in ops if o.kind == "door"]
    xi = sx * (W / 2 - 0.006)
    for a, b in _segments(L, door_holes):
        xb.box((xi, (a + b) / 2, wh / 2), (0.012, b - a, wh), "wainscot_paint")
        xb.box((sx * (W / 2 - 0.016), (a + b) / 2, wh + 0.015), (0.032, b - a, 0.035), "handrail_vinyl", bevel=0.008)
        xb.box((sx * (W / 2 - 0.012), (a + b) / 2, 0.05), (0.024, b - a, 0.1), "rubber_black", bevel=0.006)
    tag(xb.to_object(coll, mats), role="trim", side=side)

    for o in ops:
        if o.kind == "window":
            _build_window(ctx, o, mats, coll)
        elif o.kind == "door":
            _build_door(ctx, o, mats, coll)


def _build_end_wall(ctx: ArchContext, which: str, wh: float, mats, coll) -> None:
    W, t, top = ctx.width, ctx.wall_t, ctx.slab_z
    y = -t / 2 if which == "start" else ctx.length + t / 2
    ops = [o for o in ctx.openings if o.side == which]
    eb = MeshBuilder(f"wall_{which}")
    holes = [(o.center - o.width / 2 + W / 2, o.center + o.width / 2 + W / 2) for o in ops]
    for a, b in _segments(W, holes):
        eb.box(((a + b) / 2 - W / 2, y, top / 2), (b - a, t, top), "plaster_painted")
    for o in ops:
        eb.box((o.center, y, (o.head + top) / 2), (o.width, t, top - o.head), "plaster_painted")
    sy = 1 if which == "start" else -1
    for a, b in _segments(W, holes):
        eb.box(((a + b) / 2 - W / 2, y + sy * (t / 2 + 0.006), wh / 2), (b - a, 0.012, wh), "wainscot_paint")
    tag(eb.to_object(coll, mats), role="wall", side=which)
    for o in ops:
        if o.kind == "double_door":
            _build_double_door(ctx, o, mats, coll)


# ============================================================================= openings

def _build_window(ctx: ArchContext, o: Opening, mats, coll) -> None:
    sx = -1 if o.side == "left" else 1
    W, t = ctx.width, ctx.wall_t
    xw = sx * (W / 2 + t * 0.55)
    hgt = o.head - o.sill
    fb = MeshBuilder(f"window_{o.meta['index']}")
    fw = 0.06
    # outer frame + mullion + transom
    fb.box((xw, o.center - o.width / 2 + fw / 2, o.sill + hgt / 2), (0.07, fw, hgt), "window_frame", bevel=0.006)
    fb.box((xw, o.center + o.width / 2 - fw / 2, o.sill + hgt / 2), (0.07, fw, hgt), "window_frame", bevel=0.006)
    fb.box((xw, o.center, o.sill + fw / 2), (0.07, o.width, fw), "window_frame", bevel=0.006)
    fb.box((xw, o.center, o.head - fw / 2), (0.07, o.width, fw), "window_frame", bevel=0.006)
    fb.box((xw, o.center, o.sill + hgt / 2), (0.06, 0.05, hgt), "window_frame", bevel=0.006)
    transom_z = o.sill + hgt * 0.72
    fb.box((xw, o.center, transom_z), (0.06, o.width, 0.045), "window_frame", bevel=0.006)
    # interior sill board
    fb.box((sx * (W / 2 + 0.02), o.center, o.sill - 0.015), (0.16, o.width + 0.08, 0.03), "window_frame", bevel=0.006)
    # glazing: 4 panes, some broken (recorded so storytelling can drop glass shards beneath)
    broken = set(o.meta.get("broken_panes", []))
    panes = [(o.center - o.width / 4, (o.sill + transom_z) / 2, transom_z - o.sill),
             (o.center + o.width / 4, (o.sill + transom_z) / 2, transom_z - o.sill),
             (o.center - o.width / 4, (transom_z + o.head) / 2, o.head - transom_z),
             (o.center + o.width / 4, (transom_z + o.head) / 2, o.head - transom_z)]
    for i, (yc, zc, h) in enumerate(panes):
        if i in broken:
            # jagged remnant along the bottom edge of the frame
            fb.prism([(-0.0, -o.width / 4 + 0.03), (0.0, o.width / 4 - 0.03), (0.0, o.width / 8)],
                     zc - h / 2 + 0.02, zc - h / 2 + 0.16, "glass",
                     transform=_mat_translate((xw, yc, 0)))
            continue
        fb.box((xw, yc, zc), (0.006, o.width / 2 - 0.08, h - 0.06), "glass")
    obj = fb.to_object(coll, mats)
    tag(obj, role="window")
    _build_blinds(ctx, o, mats, coll, o.meta.get("blind_state", "tilted"))
    if o.meta.get("radiator", True):
        rb = MeshBuilder(f"radiator_{o.meta['index']}")
        rx = sx * (W / 2 - 0.09)
        n = 14
        rw = min(1.2, o.width - 0.3)
        for k in range(n):
            yy = o.center - rw / 2 + rw * (k + 0.5) / n
            rb.box((rx, yy, 0.42), (0.08, rw / n * 0.7, 0.55), "radiator", bevel=0.012)
        rb.cylinder((rx, o.center - rw / 2, 0.16), (rx, o.center + rw / 2, 0.16), 0.018, "radiator")
        rb.cylinder((rx, o.center - rw / 2, 0.68), (rx, o.center + rw / 2, 0.68), 0.018, "radiator")
        for yy in (o.center - rw / 2 + 0.05, o.center + rw / 2 - 0.05):
            rb.cylinder((rx, yy, 0.16), (rx, yy, 0.0), 0.012, "chrome_worn")
        tag(rb.to_object(coll, mats), role="fixture")


def _mat_translate(v):
    from mathutils import Matrix
    return Matrix.Translation(Vector(v))


def _build_blinds(ctx: ArchContext, o: Opening, mats, coll, state: str) -> None:
    """Venetian blinds. ``state``: tilted | raised | broken (one cord snapped -> slats hang diagonally) | none."""
    if state == "none":
        return
    sx = -1 if o.side == "left" else 1
    W = ctx.width
    x = sx * (W / 2 + 0.035)
    bb = MeshBuilder(f"blinds_{o.meta['index']}")
    pitch, slat_w = 0.032, 0.03
    top = o.head - 0.04
    drop = o.head - o.sill - 0.08
    if state == "raised":
        drop *= 0.25
    tilt = math.radians(float(o.meta.get("blind_tilt", 52.0)))
    n = int(drop / pitch)
    rng = seeded(int(o.center * 100), "blind")
    bb.box((x, o.center, top + 0.02), (0.05, o.width - 0.06, 0.035), "blind_slat", bevel=0.005)  # head rail
    for k in range(n):
        z = top - k * pitch
        if state == "broken":
            frac = k / max(1, n - 1)
            z0 = z
            z1 = z - frac * frac * 0.9  # one side drops, slats fan out
            p0 = Vector((x, o.center - o.width / 2 + 0.04, z0))
            p1 = Vector((x, o.center + o.width / 2 - 0.04, max(o.sill + 0.02, z1)))
            mid = (p0 + p1) / 2
            ang = math.atan2(p1.z - p0.z, p1.y - p0.y)
            bb.box(tuple(mid), (slat_w, (p1 - p0).length, 0.0012), "blind_slat")
            # rotate the just-created slat around X by `ang` and tilt about its long axis
            _rotate_last(bb, mid, ang, -sx * tilt * 0.6)
        else:
            missing = rng.random() < 0.04
            if missing:
                continue
            jitter = rng.uniform(-0.12, 0.12)
            bb.box((x, o.center, z), (slat_w, o.width - 0.08, 0.0012), "blind_slat",
                   rotation=(0, -sx * (tilt + jitter), 0))
    # ladder cords
    for yy in (o.center - o.width / 3, o.center + o.width / 3):
        bb.cylinder((x, yy, top), (x, yy, top - n * pitch), 0.0012, "blind_slat", segments=4)
    tag(bb.to_object(coll, mats), role="blinds")


def _rotate_last(builder: MeshBuilder, pivot: Vector, ang_x: float, tilt: float) -> None:
    from mathutils import Matrix
    new_verts = {v for f in builder.bm.faces if f not in builder._before for v in f.verts}
    rot = Matrix.Translation(pivot) @ Matrix.Rotation(ang_x, 4, "X") @ Matrix.Rotation(tilt, 4, "Y") @ \
        Matrix.Translation(-pivot)
    for v in new_verts:
        v.co = rot @ v.co


def _build_door(ctx: ArchContext, o: Opening, mats, coll) -> None:
    sx = -1 if o.side == "left" else 1
    W, t = ctx.width, ctx.wall_t
    db = MeshBuilder(f"door_{o.meta['index']}")
    xf = sx * (W / 2 + t / 2)
    fw = 0.07
    # metal frame (architrave proud of the wall on the corridor side)
    for yy in (o.center - o.width / 2 + fw / 2, o.center + o.width / 2 - fw / 2):
        db.box((xf - sx * 0.02, yy, o.head / 2), (t + 0.05, fw, o.head), "door_frame_metal", bevel=0.008)
    db.box((xf - sx * 0.02, o.center, o.head - fw / 2), (t + 0.05, o.width, fw), "door_frame_metal", bevel=0.008)
    tag(db.to_object(coll, mats), role="door_frame")

    # leaf, hinged on the -Y jamb, swinging into the room
    leaf_w, leaf_h = o.width - 2 * fw - 0.01, o.head - fw - 0.012
    lb = MeshBuilder(f"door_leaf_{o.meta['index']}")
    lb.box((0, leaf_w / 2, leaf_h / 2), (0.045, leaf_w, leaf_h), "door_paint", bevel=0.006)
    # vision panel + kick plate + push plate + lever
    lb.box((0, leaf_w / 2, leaf_h * 0.72), (0.05, leaf_w * 0.32, leaf_h * 0.28), "door_frame_metal", bevel=0.004)
    lb.box((0, leaf_w / 2, leaf_h * 0.72), (0.052, leaf_w * 0.26, leaf_h * 0.24), "glass")
    lb.box((-sx * 0.024, leaf_w / 2, 0.15), (0.004, leaf_w - 0.04, 0.28), "chrome_worn")
    lb.cylinder((-sx * 0.05, leaf_w - 0.09, 1.02), (-sx * 0.05, leaf_w - 0.22, 1.02), 0.011, "chrome_worn")
    lb.cylinder((-sx * 0.025, leaf_w - 0.09, 1.02), (-sx * 0.05, leaf_w - 0.09, 1.02), 0.012, "chrome_worn")
    leaf = lb.to_object(coll, mats, location=(sx * (W / 2 + t * 0.5), o.center - o.width / 2 + fw, 0.006))
    leaf.rotation_euler.z = sx * math.radians(float(o.meta.get("open", 0.0)) * 95.0)
    tag(leaf, role="door")

    # room behind the door (dark ward), so an ajar door reveals depth rather than void
    rb = MeshBuilder(f"ward_{o.meta['index']}")
    depth = 3.5
    x0 = sx * (W / 2 + t)
    xc = x0 + sx * depth / 2
    rb.box((xc, o.center, -0.05), (depth, 4.0, 0.1), "vinyl_tile_floor")
    rb.box((xc, o.center, ctx.height + 0.05), (depth, 4.0, 0.1), "ceiling_tile")
    rb.box((x0 + sx * depth, o.center, ctx.height / 2), (0.1, 4.0, ctx.height), "plaster_painted")
    for yy in (o.center - 2.0, o.center + 2.0):
        rb.box((xc, yy, ctx.height / 2), (depth, 0.1, ctx.height), "plaster_painted")
    tag(rb.to_object(coll, mats), role="room")

    # door number plaque (signage image generated by decals module)
    o.meta["plaque_pos"] = (sx * (W / 2 - 0.005), o.center + o.width / 2 + 0.22, 1.62)


def _build_double_door(ctx: ArchContext, o: Opening, mats, coll) -> None:
    y = ctx.length
    db = MeshBuilder("end_doors")
    fw = 0.08
    for xx in (o.center - o.width / 2 + fw / 2, o.center + o.width / 2 - fw / 2):
        db.box((xx, y - 0.02, o.head / 2), (fw, ctx.wall_t + 0.05, o.head), "door_frame_metal", bevel=0.008)
    db.box((o.center, y - 0.02, o.head - fw / 2), (o.width, ctx.wall_t + 0.05, fw), "door_frame_metal", bevel=0.008)
    leaf_w = (o.width - 2 * fw) / 2 - 0.006
    for k, sgn in enumerate((-1, 1)):
        cx = o.center + sgn * (leaf_w / 2 + 0.003)
        ang = math.radians(float(o.meta.get("open", [12, 0])[k] if isinstance(o.meta.get("open"), list) else 0))
        # leaves swing away from camera (into the next zone)
        hinge_x = o.center + sgn * (o.width / 2 - fw)
        lb = MeshBuilder(f"end_leaf_{k}")
        lx = -sgn * leaf_w / 2
        lb.box((lx, 0, (o.head - fw) / 2), (leaf_w, 0.05, o.head - fw - 0.01), "door_paint", bevel=0.006)
        lb.box((lx, 0, 1.45), (leaf_w * 0.5, 0.055, 0.7), "door_frame_metal", bevel=0.004)
        lb.box((lx, 0, 1.45), (leaf_w * 0.42, 0.057, 0.62), "glass")
        lb.box((lx, -0.028, 0.16), (leaf_w - 0.04, 0.004, 0.3), "chrome_worn")
        lb.box((lx - sgn * leaf_w * 0.3, -0.04, 1.05), (0.03, 0.03, 0.3), "chrome_worn", bevel=0.008)
        leaf = lb.to_object(coll, mats, location=(hinge_x, y, 0.005))
        leaf.rotation_euler.z = -sgn * ang
        tag(leaf, role="door")
    tag(db.to_object(coll, mats), role="door_frame")
    # the zone beyond: a short dark corridor stub so the glass reads depth
    zb = MeshBuilder("beyond")
    zb.box((0, y + 3.0, -0.05), (ctx.width + 1.5, 6.0, 0.1), "vinyl_tile_floor")
    zb.box((0, y + 3.0, ctx.height + 0.05), (ctx.width + 1.5, 6.0, 0.1), "ceiling_tile")
    for xx in (-(ctx.width / 2 + 0.8), ctx.width / 2 + 0.8):
        zb.box((xx, y + 3.0, ctx.height / 2), (0.1, 6.0, ctx.height), "plaster_painted")
    zb.box((0, y + 6.0, ctx.height / 2), (ctx.width + 1.6, 0.1, ctx.height), "plaster_painted")
    tag(zb.to_object(coll, mats), role="room")


# ============================================================================= ceiling

def _build_ceiling(ctx: ArchContext, spec: dict, mats, coll, seed: int) -> None:
    cfg = spec.get("architecture", {}).get("ceiling", {})
    rng = seeded(seed, "ceiling")
    tile = float(cfg.get("tile", 0.6))
    H, W, L = ctx.height, ctx.width, ctx.length
    ctx.collapse_zones = [dict(z) for z in cfg.get("collapse_zones", [])]
    random_missing = float(cfg.get("random_missing", 0.03))
    fixture_every = float(cfg.get("fixture_every", 2.4))
    nx = max(1, round(W / tile))
    cell_w = W / nx
    ny = int(L / tile)
    cb = MeshBuilder("ceiling_tiles")
    gb = MeshBuilder("ceiling_grid")
    hang = MeshBuilder("ceiling_hanging")

    def collapse_weight(x: float, y: float) -> float:
        w = 0.0
        for z in ctx.collapse_zones:
            d = math.hypot((x - float(z.get("x", 0.0))) * 1.3, y - float(z["y"]))
            r = float(z.get("radius", 1.2))
            w = max(w, 1.0 - d / r)
        return w

    # fixture slots: 600x1200 troffers on the centreline (spanning two cells along Y)
    fixture_cells: set[tuple[int, int]] = set()
    j = int(fixture_every / tile / 2)
    k = 0
    while j < ny - 1:
        i = nx // 2 if nx % 2 else nx // 2 - (k % 2)
        yc = (j + 1) * tile
        if not any(abs(yc - b) < 0.5 for b in ctx.bulkheads):
            fixture_cells |= {(i, j), (i, j + 1)}
            ctx.fixtures.append({"x": -W / 2 + (i + 0.5) * cell_w, "y": yc, "z": H, "w": cell_w * 0.9,
                                 "l": tile * 2 * 0.95, "index": k})
            k += 1
        j += max(2, int(round(fixture_every / tile)))

    for j in range(ny):
        y = (j + 0.5) * tile
        if any(abs(y - b) < 0.17 + tile / 2 - 0.02 for b in ctx.bulkheads):
            continue  # bulkhead occupies this strip
        for i in range(nx):
            x = -W / 2 + (i + 0.5) * cell_w
            if (i, j) in fixture_cells:
                continue
            cw = collapse_weight(x, y)
            if cw > 0.15 or rng.random() < random_missing:
                ctx.missing_cells.append((x, y))
                if 0.15 < cw < 0.55 and rng.random() < 0.5:
                    # tile hinged down from one T-bar, hanging into the corridor
                    ang = rng.uniform(0.5, 1.2) * (1 if rng.random() < 0.5 else -1)
                    hang.box((x, y, H), (cell_w - 0.01, tile - 0.01, 0.016), "ceiling_tile")
                    from mathutils import Matrix
                    pivot = Vector((x - math.copysign(cell_w / 2, ang), y, H))
                    rot = Matrix.Translation(pivot) @ Matrix.Rotation(ang, 4, "Y") @ Matrix.Translation(-pivot)
                    for v in {v for f in hang.bm.faces if f not in hang._before for v in f.verts}:
                        v.co = rot @ v.co
                continue
            sag = rng.uniform(0, 0.012) + 0.03 * max(0.0, cw)
            tilt = (rng.uniform(-0.02, 0.02), rng.uniform(-0.02, 0.02), 0)
            cb.box((x, y, H + 0.008 - sag), (cell_w - 0.012, tile - 0.012, 0.016), "ceiling_tile",
                   rotation=tilt)
    # T-bar grid: longitudinal and transverse runners (bent / missing in collapse zones)
    for i in range(nx + 1):
        x = -W / 2 + i * cell_w
        segs = []
        y0 = 0.0
        while y0 < L:
            y1 = min(L, y0 + tile)
            if collapse_weight(x, (y0 + y1) / 2) < 0.6:
                segs.append((y0, y1))
            y0 = y1
        for a, b in segs:
            gb.box((x, (a + b) / 2, H - 0.004), (0.024, b - a, 0.03), "steel_tbar")
    for j in range(ny + 1):
        y = j * tile
        if any(abs(y - b) < 0.2 for b in ctx.bulkheads):
            continue
        for i in range(nx):
            x = -W / 2 + (i + 0.5) * cell_w
            cw = collapse_weight(x, y)
            if cw > 0.6:
                continue
            if cw > 0.3 and rng.random() < 0.5:  # runner dropped at one end
                droop = rng.uniform(0.15, 0.6)
                p0 = Vector((x - cell_w / 2, y, H))
                p1 = Vector((x + cell_w / 2, y, H - droop))
                gb.cylinder(tuple(p0), tuple(p1), 0.012, "steel_tbar", segments=4)
                continue
            gb.box((x, y, H - 0.004), (cell_w, 0.024, 0.03), "steel_tbar")
    tag(cb.to_object(coll, mats), role="ceiling")
    tag(gb.to_object(coll, mats), role="ceiling_grid")
    tag(hang.to_object(coll, mats), role="ceiling_debris")


# ============================================================================= services in the plenum

def _build_services(ctx: ArchContext, spec: dict, mats, coll, seed: int) -> None:
    cfg = spec.get("architecture", {}).get("services", {"duct": True, "pipes": 2, "cable_tray": True})
    rng = seeded(seed, "services")
    H, L, W, top = ctx.height, ctx.length, ctx.width, ctx.slab_z
    sb = MeshBuilder("services")
    if cfg.get("duct", True):
        dz = (H + top) / 2 + 0.03
        dx = -W * 0.18
        sb.box((dx, L / 2, dz), (0.55, L, 0.32), "galvanized_duct")
        y = 0.0
        while y < L:  # seams + hanger rods every 1.2 m
            sb.box((dx, y, dz), (0.57, 0.03, 0.34), "galvanized_duct")
            for xx in (dx - 0.3, dx + 0.3):
                sb.cylinder((xx, y, dz - 0.17), (xx, y, top), 0.005, "steel_tbar", segments=6)
            sb.box((dx, y, dz - 0.175), (0.66, 0.03, 0.01), "steel_tbar")
            y += 1.2
    for p in range(int(cfg.get("pipes", 2))):
        px = W * (0.12 + 0.13 * p)
        pz = top - 0.12 - 0.09 * p
        r = 0.035 + 0.02 * (p % 2)
        sb.cylinder((px, 0, pz), (px, L, pz), r, "galvanized_duct" if p % 2 else "radiator", segments=14)
        y = 0.4
        while y < L:
            sb.cylinder((px, y - 0.02, pz), (px, y + 0.02, pz), r + 0.01, "steel_tbar", segments=14)  # couplings
            sb.cylinder((px, y, pz), (px, y, top), 0.006, "steel_tbar", segments=6)
            y += 1.5 + rng.uniform(-0.2, 0.2)
    if cfg.get("cable_tray", True):
        tx, tz = W * 0.36, top - 0.1
        sb.box((tx, L / 2, tz), (0.3, L, 0.004), "steel_tbar")
        for side in (-1, 1):
            sb.box((tx + side * 0.15, L / 2, tz + 0.03), (0.004, L, 0.06), "steel_tbar")
        for c in range(5):
            cx = tx - 0.1 + 0.05 * c
            pts = []
            for k in range(int(L / 0.5) + 1):
                yy = k * 0.5
                pts.append((cx + rng.uniform(-0.01, 0.01), yy, tz + 0.012 + 0.006 * c))
            sb.tube_path(pts, 0.007, "cable_black", segments=6)
    tag(sb.to_object(coll, mats), role="services")


def _build_handrail(ctx: ArchContext, cfg: dict, mats, coll) -> None:
    hb = MeshBuilder("handrails")
    h = float(cfg.get("height", 0.9))
    for side in cfg.get("sides", ["right"]):
        sx = -1 if side == "left" else 1
        ops = [(o.center - o.width / 2 - 0.15, o.center + o.width / 2 + 0.15) for o in ctx.openings
               if o.side == side and o.kind == "door"]
        for a, b in _segments(ctx.length, ops):
            if b - a < 0.6:
                continue
            x = sx * (ctx.width / 2 - 0.075)
            hb.cylinder((x, a + 0.05, h), (x, b - 0.05, h), 0.022, "handrail_vinyl", segments=12)
            y = a + 0.3
            while y < b - 0.2:
                hb.cylinder((x, y, h - 0.02), (sx * (ctx.width / 2 - 0.01), y, h - 0.06), 0.009, "chrome_worn")
                y += 1.2
    tag(hb.to_object(coll, mats), role="handrail")


def _build_exterior(ctx: ArchContext, spec: dict, mats, root) -> None:
    """Courtyard + opposite hospital wing seen (mostly blown out) through the windows."""
    if not ctx.windows():
        return
    side = ctx.windows()[0].side
    sx = -1 if side == "left" else 1
    coll = collection("Exterior", root)
    eb = MeshBuilder("exterior")
    dist = float(spec.get("architecture", {}).get("exterior_distance", 14.0))
    eb.box((sx * (ctx.width / 2 + dist / 2), ctx.length / 2, -1.2), (dist + 2, ctx.length + 40, 0.2), "concrete_floor")
    # opposite wing: facade with window grid (reads as silhouette against the overcast sky)
    fx = sx * (ctx.width / 2 + dist)
    floors = int(spec.get("architecture", {}).get("exterior_floors", 2))
    fh = floors * 3.4 + 0.4
    # keep the facade below the sun ray grazing the window heads, or the courtyard wing shadows the corridor
    eb.box((fx + sx * 4, ctx.length / 2, -1.2 + fh / 2), (8, ctx.length + 30, fh), "concrete_slab")
    for fl in range(floors):
        for k in range(int((ctx.length + 24) / 3.2)):
            yy = -12 + k * 3.2
            eb.box((fx - sx * 0.01, yy, -1.2 + 0.9 + fl * 3.4 + 0.8), (0.05, 1.7, 1.5), "glass")
    tag(eb.to_object(coll, mats), role="exterior")
