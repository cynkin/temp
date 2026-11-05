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

A footprint registry prevents interpenetration and keeps a walkable lane clear,
because an un-walkable corridor reads as set dressing, not a real place.
"""
from __future__ import annotations

import math

from .decals import document, door_plate, image_quad, poster
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


BEATS = {
    "collapse": _collapse,
    "evacuation": _evacuation,
    "paper_trail": _paper_trail,
    "waiting_area": _waiting_area,
    "barricade": _barricade,
    "wall_dressing": _wall_dressing,
}

__all__ = ["build_story", "BEATS", "Footprints", "tag"]
