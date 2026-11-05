"""Environmental storytelling: props placed by *narrative beats*, never by uniform scatter.

A beat is a small causal story with spatial logic tied to the architecture:

    collapse      ceiling holes (ArchContext.collapse_zones / missing_cells) -> debris directly
                  beneath, dust bed, dangling cables, water puddle from the roof leak, then a
                  caution sign someone placed next to the puddle (before the place was abandoned)
    evacuation    gurney abandoned mid-route beside an ajar ward door, sheet dragged half off,
                  IV stand knocked over beside it, wheelchair left facing the wall further on
    paper_trail   toppled records cart; papers spill and thin out along the flight direction
    waiting_area  bench of linked seats against the wall between doors, one seat broken
    barricade     a stripped bed rammed against a closed ward door
    wall_dressing door plates, notice board with pinned documents, posters, extinguisher
    breach        an underground checkpoint held and then abandoned: a low sandbag line from one wall,
                  a work lamp's extension cable run to it, a toolbox dropped open beside it with spanners
                  spilled, fuel drums stacked against the wall behind it, rubble at the wall base
    stencils      sector letters and bay numbers sprayed on the walls, worn hazard bands at thresholds

A footprint registry prevents interpenetration and keeps a walkable lane clear,
because an un-walkable corridor reads as set dressing, not a real place.
"""
from __future__ import annotations

import math

from .decals import document, door_plate, hazard_band, image_quad, poster, stencil
from .geometry import props as P
from .geometry.architecture import ArchContext
from .util import collection, seeded, tag


class Footprints:
    def __init__(self, ctx: ArchContext, lane_width: float = 0.7):
        self.ctx = ctx
        self.items: list[tuple[float, float, float, str]] = []  # x, y, radius, name
        self.lane = lane_width

    def free(self, x: float, y: float, r: float) -> bool:
        if abs(x) + r > self.ctx.width / 2 + 0.05:
            return False
        return all(math.hypot(x - a, y - b) > r + rb for a, b, rb, _ in self.items)

    def claim(self, x: float, y: float, r: float, name: str) -> tuple[float, float]:
        """Claim a footprint, nudging along Y (then X) until free. Returns final position."""
        for k in range(40):
            dy = (k // 2 + 1) * 0.12 * (1 if k % 2 else -1) if k else 0.0
            xx = max(-self.ctx.width / 2 + r, min(self.ctx.width / 2 - r, x))
            if self.free(xx, y + dy, r):
                self.items.append((xx, y + dy, r, name))
                return xx, y + dy
        self.items.append((x, y, r, name))
        return x, y

    def lane_clear(self) -> bool:
        """True if some x-offset lane of ``self.lane`` width exists along every metre of the corridor."""
        for yi in range(int(self.ctx.length)):
            y = yi + 0.5
            blocked = [(a - rb, a + rb) for a, b, rb, _ in self.items if abs(b - y) < rb]
            xs = sorted(blocked)
            cur = -self.ctx.width / 2
            gap = 0.0
            for a, b in xs:
                gap = max(gap, a - cur)
                cur = max(cur, b)
            gap = max(gap, self.ctx.width / 2 - cur)
            if gap < self.lane:
                return False
        return True


def build_story(spec: dict, ctx: ArchContext, mats: dict, root) -> dict:
    seed = int(spec.get("seed", 1))
    coll = collection("Story", root)
    fp = Footprints(ctx)
    report: dict = {"beats": []}
    # architecture-driven reservations: door swings stay clear
    for d in ctx.doors():
        if d.side in ("left", "right"):
            sx = -1 if d.side == "left" else 1
            fp.items.append((sx * (ctx.width / 2 - 0.35), d.center, 0.3, f"door_clear_{d.meta.get('index')}"))
    for i, beat in enumerate(spec.get("story", {}).get("beats", [])):
        kind = beat.get("kind")
        fn = BEATS.get(kind)
        if fn is None:
            raise ValueError(f"unknown story beat {kind!r}; known: {sorted(BEATS)}")
        rng = seeded(seed, f"beat{i}:{kind}")
        placed = fn(beat, ctx, mats, coll, fp, rng)
        report["beats"].append({"kind": kind, "placed": placed})
    report["walkable_lane"] = fp.lane_clear()
    return report


def fit_inside(obj, ctx: ArchContext, margin: float = 0.02) -> None:
    """Shift a placed prop along X so no vertex penetrates the side walls (generic safety net)."""
    import bpy
    bpy.context.view_layer.update()
    xs = [(obj.matrix_world @ v.co).x for v in obj.data.vertices]
    lo, hi = min(xs), max(xs)
    limit = ctx.width / 2 - margin
    if hi > limit:
        obj.location.x -= hi - limit
    elif lo < -limit:
        obj.location.x += -limit - lo


# ============================================================================= beats

def _collapse(beat, ctx: ArchContext, mats, coll, fp: Footprints, rng) -> list[str]:
    placed = []
    for zi, z in enumerate(ctx.collapse_zones):
        cx, cy, r = float(z.get("x", 0.0)), float(z["y"]), float(z.get("radius", 1.2))
        P.debris_field(coll, mats, f"debris_{zi}", (cx * 0.8, cy), r * 0.75, int(70 * r), rng,
                       tile_fragments=int(9 * r), ceiling_h=ctx.height)
        P.hanging_cables(coll, mats, f"cables_{zi}", (cx, cy), ctx.height, ctx.slab_z, 3 + int(2 * r), rng)
        placed += [f"debris_{zi}", f"cables_{zi}"]
        if beat.get("puddles", True):
            # water collects slightly downhill of the leak, spreading along the corridor axis
            P.puddle(coll, mats, f"puddle_{zi}", (cx * 0.6 + rng.uniform(-0.2, 0.2), cy + rng.uniform(-0.4, 0.4)),
                     r * 0.55, rng, elongation=0.7)
            placed.append(f"puddle_{zi}")
        fp.items.append((cx * 0.8, cy, r * 0.35, f"debris_core_{zi}"))
    # isolated single fallen tiles (from random missing cells) get a fragment + dust below them
    for k, (x, y) in enumerate(ctx.missing_cells):
        if any(math.hypot(x - float(z.get("x", 0)), y - float(z["y"])) < float(z.get("radius", 1.2))
               for z in ctx.collapse_zones):
            continue
        P.debris_field(coll, mats, f"tile_fall_{k}", (x + rng.uniform(-0.2, 0.2), y + rng.uniform(-0.3, 0.3)), 0.35,
                       10, rng, tile_fragments=2, ceiling_h=ctx.height)
        placed.append(f"tile_fall_{k}")
    cs = beat.get("caution_sign")
    if cs and ctx.collapse_zones:
        z = ctx.collapse_zones[int(cs.get("zone", 0))]
        ox, oy = cs.get("offset", (-0.5, -1.0))
        x, y = fp.claim(float(z.get("x", 0)) + ox, float(z["y"]) + oy, 0.2, "caution_sign")
        P.caution_sign(coll, mats, (x, y, 0.0), yaw_deg=rng.uniform(-30, 30), rng=rng)
        placed.append("caution_sign")
    # glass shards below broken window panes
    for o in ctx.windows():
        if o.meta.get("broken_panes"):
            sx = -1 if o.side == "left" else 1
            P.glass_shards(coll, mats, f"shards_{o.meta['index']}", (sx * (ctx.width / 2 - 0.25), o.center), 0.3,
                           16, rng)
            placed.append(f"shards_{o.meta['index']}")
    return placed


def _evacuation(beat, ctx: ArchContext, mats, coll, fp: Footprints, rng) -> list[str]:
    placed = []
    g = beat.get("gurney")
    if g:
        x, y = fp.claim(float(g.get("x", 0.3)), float(g["y"]), 0.75, "gurney")
        gobj = P.gurney(coll, mats, (x, y, 0.0), yaw_deg=float(g.get("yaw", 20)), rng=rng,
                        sheet=g.get("sheet", "half_off"))
        fit_inside(gobj, ctx)
        placed.append("gurney")
        iv = beat.get("iv_stand")
        if iv:
            ix, iy = fp.claim(x - 0.55, y + 0.9, 0.35, "iv_stand")
            P.iv_stand(coll, mats, (ix, iy, 0.0), yaw_deg=rng.uniform(150, 210), rng=rng,
                       toppled=bool(iv.get("toppled", True)))
            placed.append("iv_stand")
    wc = beat.get("wheelchair")
    if wc:
        x, y = fp.claim(float(wc.get("x", -0.7)), float(wc["y"]), 0.45, "wheelchair")
        fit_inside(P.wheelchair(coll, mats, (x, y, 0.0), yaw_deg=float(wc.get("yaw", 200)), rng=rng), ctx)
        placed.append("wheelchair")
    return placed


def _paper_trail(beat, ctx: ArchContext, mats, coll, fp: Footprints, rng) -> list[str]:
    c = beat.get("cart", {"y": ctx.length / 2, "x": 0.9})
    x, y = fp.claim(float(c.get("x", 0.9)), float(c["y"]), 0.45, "records_cart")
    toppled = bool(c.get("toppled", True))
    # a toppled cart falls along the corridor (its height axis ends up along Y), never into a wall
    yaw = (90 + rng.uniform(-15, 15)) if toppled else rng.uniform(-20, 20)
    cart = P.medical_cart(coll, mats, (x, y, 0.0), yaw_deg=yaw, rng=rng, toppled=toppled)
    fit_inside(cart, ctx)
    P.papers(coll, mats, "papers_trail", (x - 0.3, y + 0.2), int(beat.get("papers", 30)), beat.get("drift", (0, 1)),
             float(beat.get("spread", 4.5)), rng)
    return ["records_cart", "papers_trail"]


def _waiting_area(beat, ctx: ArchContext, mats, coll, fp: Footprints, rng) -> list[str]:
    b = beat.get("bench", {})
    side = b.get("side", "right")
    sx = -1 if side == "left" else 1
    x = sx * (ctx.width / 2 - 0.36)
    xx, y = fp.claim(x, float(b.get("y", 7.5)), 0.6, "bench")
    P.waiting_bench(coll, mats, (xx, y, 0.0), yaw_deg=90 * sx + rng.uniform(-3, 3), rng=rng,
                    seats=int(b.get("seats", 3)), broken_seat=int(b.get("broken_seat", -1)))
    return ["bench"]


def _barricade(beat, ctx: ArchContext, mats, coll, fp: Footprints, rng) -> list[str]:
    doors = [d for d in ctx.doors() if d.side in ("left", "right")]
    d = doors[int(beat.get("door", 0)) % len(doors)]
    sx = -1 if d.side == "left" else 1
    # bed shoved lengthwise against the door, slightly skewed from the impact
    x = sx * (ctx.width / 2 - 0.45)
    fp.items = [it for it in fp.items if it[3] != f"door_clear_{d.meta.get('index')}"]
    xx, y = fp.claim(x, d.center, 0.5, "barricade_bed")
    P.gurney(coll, mats, (xx, y, 0.0), yaw_deg=rng.uniform(-8, 8), rng=rng, sheet="none", head_raise_deg=0,
             rail_down="none")
    return ["barricade_bed"]


def _wall_dressing(beat, ctx: ArchContext, mats, coll, fp: Footprints, rng) -> list[str]:
    placed = []
    # door plates: every ward door gets its number
    for d in ctx.doors():
        if d.side not in ("left", "right"):
            continue
        sx = -1 if d.side == "left" else 1
        img = door_plate(str(d.meta.get("label", "B")), seed=int(d.center * 10))
        image_quad(f"plate_{d.meta.get('index')}", img, (sx * (ctx.width / 2 - 0.004), d.center + d.width / 2 + 0.2,
                                                          1.6), (0.3, 0.12), "-x" if sx > 0 else "+x", coll,
                   roughness=0.35)
        placed.append(f"plate_{d.meta.get('index')}")
    nb = beat.get("notice_board")
    if nb:
        sx = -1 if nb.get("side", "right") == "left" else 1
        y = float(nb["y"])
        x = sx * (ctx.width / 2 - 0.02)
        P.notice_board(coll, mats, (x, y, 1.55), yaw_deg=90 * sx, rng=rng)
        for k in range(7):
            u, v = rng.uniform(-0.45, 0.45), rng.uniform(-0.3, 0.3)
            image_quad(f"note_{k}", document(1000 + k), (x - sx * 0.018 - sx * 0.001 * k, y + u, 1.55 + v),
                       (0.18, 0.25), "-x" if sx > 0 else "+x", coll, rotation_deg=rng.uniform(-8, 8))
        # one sheet torn off and lying on the floor beneath
        image_quad("note_fallen", document(1099), (x - sx * 0.35, y + 0.2, 0.004), (0.21, 0.297), "+z", coll,
                   rotation_deg=rng.uniform(0, 360))
        placed.append("notice_board")
    ex = beat.get("extinguisher")
    if ex:
        sx = -1 if ex.get("side", "right") == "left" else 1
        P.extinguisher(coll, mats, (sx * (ctx.width / 2 - 0.1), float(ex["y"]), 0.55), yaw_deg=90 * sx + 180, rng=rng)
        placed.append("extinguisher")
    kinds = ["hygiene", "quiet", "visiting", "evac"]
    for k, pst in enumerate(beat.get("posters", [])):
        sx = -1 if pst.get("side", "right") == "left" else 1
        image_quad(f"poster_{k}", poster(kinds[k % len(kinds)], 77 + k),
                   (sx * (ctx.width / 2 - 0.003), float(pst["y"]), 1.55), (0.42, 0.6), "-x" if sx > 0 else "+x", coll,
                   rotation_deg=rng.uniform(-3, 3), alpha=True, grime=0.5)
        placed.append(f"poster_{k}")
    return placed


def _overgrowth(beat, ctx: ArchContext, mats, coll, fp: Footprints, rng) -> list[str]:
    """Plants colonise habitats with light + water + foothold: under roof leaks, below broken windows, wall bases."""
    from .geometry import nature
    cand = []
    for z in ctx.collapse_zones:  # wettest, lit from the hole above
        for _ in range(6):
            cand.append((float(z.get("x", 0)) + rng.uniform(-0.6, 0.6), float(z["y"]) + rng.uniform(-0.8, 0.8),
                         0.0, 3.0))
    for o in ctx.windows():  # rain through broken panes, sun on the floor below
        sx = -1 if o.side == "left" else 1
        w = 2.5 if o.meta.get("broken_panes") else 0.6
        cand.append((sx * (ctx.width / 2 - 0.12), o.center + rng.uniform(-0.6, 0.6), 0.0, w))
    for k in range(int(ctx.length)):  # wall-base cracks
        side = rng.choice((-1, 1))
        cand.append((side * (ctx.width / 2 - 0.08), k + rng.uniform(0, 1), 0.0, 0.3))
    density = float(beat.get("density", 1.0))
    placed = []
    gp = nature.habitat_positions(rng, cand, int(14 * density))
    if gp:
        nature.grass_clumps(coll, mats, "overgrowth_grass", gp, rng, height=(0.1, 0.35))
        placed.append("overgrowth_grass")
    fpos = nature.habitat_positions(rng, [c for c in cand if c[3] >= 2.5], int(4 * density))
    if fpos:
        nature.ferns(coll, mats, "overgrowth_ferns", fpos, rng)
        placed.append("overgrowth_ferns")
    for k, (x, y, z) in enumerate(nature.habitat_positions(rng, [c for c in cand if c[3] >= 2.5],
                                                           int(5 * density))):
        nature.moss_patch(coll, mats, f"moss_{k}", (x, y, 0.002), rng.uniform(0.2, 0.5), rng)
        placed.append(f"moss_{k}")
    return placed


def _breach(beat, ctx: ArchContext, mats, coll, fp: Footprints, rng) -> list[str]:
    placed = []
    y = float(beat.get("y", ctx.length * 0.45))
    side = beat.get("side", "left")
    sx = -1 if side == "left" else 1
    wall = sx * ctx.width / 2
    reach = min(float(beat.get("reach", 0.55)), 0.62) * ctx.width  # always leave a walkable gap
    courses = int(beat.get("courses", 3))
    x0, x1 = wall - sx * 0.05, wall - sx * reach
    # the line is angled slightly: built in a hurry, facing whatever came from beyond (+y)
    P.sandbag_wall(coll, mats, "sandbag_line", (x0, y), (x1, y - 0.25), courses, rng, rows=int(beat.get("rows", 2)))
    for k in range(int(reach / 0.4) + 1):
        t = k / max(1, int(reach / 0.4))
        fp.items.append((x0 + (x1 - x0) * t, y - 0.12 * t + 0.17, 0.42, f"sandbag_{k}"))
    placed.append("sandbag_line")
    # a fallen bag that slid off the top course
    P.sandbag(coll, mats, (x1 - sx * 0.25, y + 0.75, 0.0), yaw_deg=rng.uniform(40, 80), rng=rng)
    # toolbox dropped open on the defenders' side (towards the camera), spanners skittered out
    tx, ty = fp.claim(x1 - sx * 0.05, y - 0.85, 0.3, "toolbox")
    P.toolbox(coll, mats, (tx, ty, 0.0), yaw_deg=rng.uniform(-35, 35), rng=rng)
    mb_name = "loose_spanners"
    from .util import MeshBuilder
    sp = MeshBuilder(mb_name)
    for k in range(4):
        a = rng.uniform(0, 6.28)
        r = rng.uniform(0.25, 0.6)
        px, py = tx + math.cos(a) * r, ty + math.sin(a) * r
        if abs(px) > ctx.width / 2 - 0.05:
            px = math.copysign(ctx.width / 2 - 0.08, px)
        sp.box((px, py, 0.004), (0.2, 0.022, 0.008), "chrome_worn", rotation=(0, 0, rng.uniform(0, 6.28)))
    tag(sp.to_object(coll, mats), role="debris", grounded=False)
    placed += ["toolbox", mb_name]
    # fuel drums behind the line, against the same wall; one knocked over and rolled toward the middle
    dy = y + float(beat.get("drums_offset", 1.6))
    for k in range(int(beat.get("drums", 2))):
        xx, yy = fp.claim(wall - sx * 0.33, dy + k * 0.62, 0.31, f"drum_{k}")
        P.oil_drum(coll, mats, (xx, yy, 0.0), yaw_deg=rng.uniform(0, 360), rng=rng)
        placed.append(f"drum_{k}")
    if beat.get("drum_toppled", True):
        xx, yy = fp.claim(wall - sx * 0.95, dy + 0.5, 0.45, "drum_lying")
        P.oil_drum(coll, mats, (xx, yy, 0.0), yaw_deg=rng.uniform(60, 120), rng=rng, lying=True)
        placed.append("drum_lying")
    # extension cable from a wall socket further back, snaking to the line (the work lamp is gone)
    src = (wall - sx * 0.03, y - float(beat.get("cable_run", 3.5)))
    mid = (wall - sx * 0.35, y - 1.6)
    P.floor_cable(coll, mats, "extension_cable", [src, mid, (x0 - sx * 0.2, y - 0.2)], rng)
    placed.append("extension_cable")
    # rubble that has crumbled from the walls along the base (slow decay, not a collapse)
    for k, yy in enumerate(beat.get("rubble_at", [])):
        s2 = -1 if k % 2 else 1
        P.debris_field(coll, mats, f"wall_rubble_{k}", (s2 * (ctx.width / 2 - 0.12), float(yy)), 0.35, 40, rng,
                       ceiling_h=ctx.height)
        placed.append(f"wall_rubble_{k}")
    if beat.get("papers"):
        P.papers(coll, mats, "breach_papers", (x1, y - 0.6), int(beat["papers"]), (0.2, -1.0), 2.5, rng)
        placed.append("breach_papers")
    return placed


def _stencils(beat, ctx: ArchContext, mats, coll, fp: Footprints, rng) -> list[str]:
    """Sprayed sector letters and bay numbers (wayfinding in a place with no windows), hazard bands."""
    placed = []
    for k, st in enumerate(beat.get("walls", [])):
        sx = -1 if st.get("side", "right") == "left" else 1
        h = float(st.get("size", 0.55))
        text = str(st["text"])
        aspect = 2.0 if len(text) > 2 else 1.0
        color = tuple(st.get("color", (225, 222, 210)))
        img = stencil(text, seed=31 + k, color=color, size=(1024, 512) if aspect > 1 else (512, 512))
        image_quad(f"stencil_{k}", img, (sx * (ctx.width / 2 - 0.003 - 0.0005 * k), float(st["y"]),
                                         float(st.get("z", 1.7))),
                   (h * aspect, h), "-x" if sx > 0 else "+x", coll, roughness=0.6, alpha=True, grime=0.4,
                   rotation_deg=rng.uniform(-1.5, 1.5))
        placed.append(f"stencil_{k}")
    for k, band in enumerate(beat.get("floor_bands", [])):
        width = float(band.get("width", 0.12))
        if band.get("across"):  # threshold band across the corridor at y
            image_quad(f"hazard_{k}", hazard_band(70 + k), (0.0, float(band["y"]), 0.0012 + 0.0002 * k),
                       (ctx.width - 0.02, width), "+z", coll, roughness=0.55, alpha=True, grime=0.6)
        else:  # edge line along one wall from y0 to y1
            sx = -1 if band.get("side", "left") == "left" else 1
            y0, y1 = float(band["y0"]), float(band["y1"])
            image_quad(f"hazard_{k}", hazard_band(70 + k), (sx * (ctx.width / 2 - 0.12), (y0 + y1) / 2,
                                                            0.0012 + 0.0002 * k),
                       (y1 - y0, width), "+z", coll, roughness=0.55, alpha=True, grime=0.6, rotation_deg=90)
        placed.append(f"hazard_{k}")
    return placed


BEATS = {
    "overgrowth": _overgrowth,
    "collapse": _collapse,
    "evacuation": _evacuation,
    "paper_trail": _paper_trail,
    "waiting_area": _waiting_area,
    "barricade": _barricade,
    "wall_dressing": _wall_dressing,
    "breach": _breach,
    "stencils": _stencils,
}

__all__ = ["build_story", "BEATS", "Footprints", "tag"]
