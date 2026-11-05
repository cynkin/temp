"""Lighting & atmosphere: motivated lights only.

Every light in a scene has a physical source the viewer can identify: the sun
through windows, a fluorescent troffer, an exit sign, a fallen tube. Fixture
*states* (on / off / dim / hanging / flicker) come from the spec and are a
storytelling tool as much as a lighting one (a dead row of lights = power
failure; one hanging fixture = collapse).
"""
from __future__ import annotations

import math

import bpy
from mathutils import Vector

from .geometry.architecture import ArchContext
from .materials.library import make_emissive, make_volume
from .util import MeshBuilder, collection, kelvin_to_rgb, look_rotation, seeded, srgb_to_linear, tag


def setup_world(spec: dict) -> None:
    w = spec["lighting"].get("world", {})
    world = bpy.data.worlds.new("envgen_world")
    bpy.context.scene.world = world
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    sky = w.get("sky_color")
    if sky:  # vertical gradient: horizon -> zenith (overcast)
        tc = nt.nodes.new("ShaderNodeTexCoord")
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        nt.links.new(tc.outputs["Generated"], sep.inputs[0])
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.elements[0].color = (*srgb_to_linear(w.get("horizon_color", sky)), 1)
        ramp.color_ramp.elements[1].color = (*srgb_to_linear(sky), 1)
        nt.links.new(sep.outputs["Z"], ramp.inputs[0])
        # camera rays see the sky; diffuse/glossy bounces get the (dimmer) ambient colour
        lp = nt.nodes.new("ShaderNodeLightPath")
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs[0])
        mix.inputs[6].default_value = (*srgb_to_linear(w.get("color", (0.05, 0.05, 0.05))), 1)
        nt.links.new(ramp.outputs[0], mix.inputs[7])
        nt.links.new(mix.outputs[2], bg.inputs["Color"])
    else:
        bg.inputs["Color"].default_value = (*srgb_to_linear(w.get("color", (0.02, 0.02, 0.02))), 1)
    bg.inputs["Strength"].default_value = float(w.get("strength", 1.0))
    nt.links.new(bg.outputs[0], out.inputs["Surface"])


def add_light(name: str, kind: str, location, energy: float, color=None, kelvin: float | None = None,
              direction=None, size: float | tuple = 0.1, spot_deg: float = 60.0, blend: float = 0.3,
              coll=None, shadow_soft: float | None = None) -> bpy.types.Object:
    data = bpy.data.lights.new(name, kind)
    data.energy = energy
    rgb = kelvin_to_rgb(kelvin) if kelvin else tuple(color or (1, 1, 1))
    data.color = rgb
    if kind == "AREA":
        if isinstance(size, (tuple, list)):
            data.shape = "RECTANGLE"
            data.size, data.size_y = size
        else:
            data.shape = "DISK"
            data.size = size
    elif kind in ("POINT", "SPOT"):
        data.shadow_soft_size = size if shadow_soft is None else shadow_soft
        if kind == "SPOT":
            data.spot_size = math.radians(spot_deg)
            data.spot_blend = blend
    elif kind == "SUN":
        data.angle = math.radians(size if isinstance(size, (int, float)) else 0.5)
    obj = bpy.data.objects.new(name, data)
    obj.location = location
    if direction is not None:
        # Blender lights emit along local -Z
        obj.matrix_world = obj.matrix_world.Translation(Vector(location)) @ look_rotation(-Vector(direction))
    (coll or bpy.context.scene.collection).objects.link(obj)
    tag(obj, role="light")
    return obj


def build_lighting(spec: dict, ctx: ArchContext, mats: dict, root) -> dict:
    lcfg = spec["lighting"]
    seed = int(spec.get("seed", 1))
    rng = seeded(seed, "lights")
    coll = collection("Lighting", root)
    setup_world(spec)
    report = {"lights": []}

    # ---- sun through windows
    sun = lcfg.get("sun")
    if sun:
        az, el = math.radians(float(sun.get("azimuth", 120))), math.radians(float(sun.get("elevation", 30)))
        d = Vector((math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), -math.sin(el)))
        add_light("sun", "SUN", (0, 0, 20), float(sun.get("strength", 4.0)), kelvin=float(sun.get("kelvin", 5600)),
                  direction=tuple(d), size=float(sun.get("angle", 0.6)), coll=coll)
        report["lights"].append({"name": "sun", "dir": list(d)})
        # skylight portals on windows: guide Cycles to sample sky light through openings (less noise)
        for o in ctx.windows():
            sx = -1 if o.side == "left" else 1
            portal = add_light(f"portal_{o.meta['index']}", "AREA", (sx * (ctx.width / 2 + ctx.wall_t + 0.02), o.center,
                                                                     (o.sill + o.head) / 2), 1.0,
                               direction=(-sx, 0, 0), size=(o.head - o.sill, o.width), coll=coll)
            portal.data.cycles.is_portal = True

    # ---- ceiling troffers
    fx = lcfg.get("fixtures", {})
    states = {int(k): v for k, v in (fx.get("states") or {}).items()}
    default_state = fx.get("default", "off")
    kelvin = float(fx.get("kelvin", 4800))
    energy = float(fx.get("energy", 40.0))
    tube_on = make_emissive("tube_on", kelvin_to_rgb(kelvin), float(fx.get("emission", 6.0)), flicker_noise=True)
    tube_dim = make_emissive("tube_dim", kelvin_to_rgb(kelvin - 600), float(fx.get("emission", 6.0)) * 0.06,
                             flicker_noise=True)
    tube_off = make_emissive("tube_off", (1, 1, 1), 0.0, base_color=(0.62, 0.62, 0.58))
    fmats = dict(mats, tube_on=tube_on, tube_dim=tube_dim, tube_off=tube_off)
    for f in ctx.fixtures:
        state = states.get(f["index"], default_state)
        x, y, z, w, l = f["x"], f["y"], f["z"], f["w"], f["l"]
        fb = MeshBuilder(f"troffer_{f['index']}")
        hanging = state.startswith("hanging")
        lit = state in ("on", "hanging_on", "flicker")
        dim = state in ("dim", "flicker")
        panel_mat = "tube_on" if (lit and not dim) else ("tube_dim" if dim else "tube_off")
        fb.box((0, 0, 0.05), (w, l, 0.1), "fixture_housing", bevel=0.006)
        fb.box((0, 0, -0.002), (w - 0.04, l - 0.04, 0.01), panel_mat)
        for k in (-1, 1):  # parabolic louvre rails
            fb.box((k * (w / 2 - 0.03), 0, -0.01), (0.012, l - 0.04, 0.03), "fixture_housing")
        loc = (x, y, z)
        rot = (0, 0, 0)
        if hanging:
            # one end dropped, swinging on its supply cable
            drop = float(fx.get("hang_drop", 0.75))
            ang = math.asin(min(0.95, drop / l))
            rot = (ang, 0, 0)
            loc = (x, y + l / 2 - math.cos(ang) * l / 2, z - drop / 2)
            cb = MeshBuilder(f"troffer_cable_{f['index']}")
            p_top = Vector((x + 0.1, y - l / 2 + 0.1, ctx.slab_z - 0.05))
            p_bot = Vector((x, y - l / 2 + 0.25, z - drop + 0.05))
            pts = [p_top.lerp(p_bot, t) + Vector((0.03 * math.sin(t * 5), 0, -0.05 * math.sin(t * math.pi)))
                   for t in (0, .25, .5, .75, 1)]
            cb.tube_path([tuple(p) for p in pts], 0.006, "cable_black", segments=6)
            tag(cb.to_object(coll, fmats), role="cable")
        obj = fb.to_object(coll, fmats, location=loc, rotation=rot)
        tag(obj, role="fixture", state=state)
        if lit or dim:
            e = energy * (0.12 if dim else 1.0) * rng.uniform(0.85, 1.1)
            n = obj.matrix_world.to_3x3() @ Vector((0, 0, -1))
            lamp = add_light(f"troffer_light_{f['index']}", "AREA",
                             tuple(Vector(loc) + n * 0.03), e, kelvin=kelvin - (600 if dim else 0),
                             direction=tuple(n), size=(w - 0.06, l - 0.06), coll=coll)
            lamp.rotation_euler = obj.rotation_euler.copy()
            lamp.location = Vector(loc) + n * 0.03
            lamp.data.spread = math.radians(float(fx.get("spread", 150)))
            report["lights"].append({"name": lamp.name, "state": state, "energy": e})

    # ---- practicals (exit signs, fallen tubes, bulbs, emergency strobes)
    for i, p in enumerate(lcfg.get("practicals", [])):
        _build_practical(i, p, ctx, fmats, coll, report)
    return report


def _build_practical(i: int, p: dict, ctx: ArchContext, mats: dict, coll, report: dict) -> None:
    kind = p.get("kind", "point")
    pos = Vector(p.get("position", (0, 0, 2)))
    color = p.get("color")
    lin = srgb_to_linear(color) if color else kelvin_to_rgb(float(p.get("kelvin", 3000)))
    energy = float(p.get("energy", 20))
    if kind == "exit_sign":
        sign_mat = make_emissive(f"exit_sign_{i}", lin, float(p.get("emission", 12.0)), base_color=(0.6, 0.1, 0.08))
        sb = MeshBuilder(f"exit_sign_{i}")
        sb.box((0, 0, 0), (0.36, 0.06, 0.17), "fixture_housing", bevel=0.008)
        sb.box((0, -0.031, 0), (0.32, 0.004, 0.13), "sign")
        obj = sb.to_object(coll, dict(mats, sign=sign_mat), location=tuple(pos),
                           rotation=(0, 0, math.radians(float(p.get("yaw", 0)))))
        tag(obj, role="practical")
        add_light(f"exit_light_{i}", "AREA", tuple(pos + Vector(p.get("light_offset", (0, -0.12, -0.05)))), energy,
                  color=lin, direction=p.get("direction", (0, -1, -0.4)), size=0.3, coll=coll)
    elif kind == "fallen_tube":
        tube_mat = make_emissive(f"tube_practical_{i}", lin, float(p.get("emission", 8.0)), flicker_noise=True)
        tb = MeshBuilder(f"fallen_tube_{i}")
        yaw = math.radians(float(p.get("yaw", 30)))
        d = Vector((math.cos(yaw), math.sin(yaw), 0)) * float(p.get("length", 1.2)) / 2
        a, b = pos - d, pos + d
        tb.cylinder(tuple(a), tuple(b), 0.013, "tube")
        tb.cylinder(tuple(a - d * 0.03), tuple(a + d * 0.02), 0.016, "fixture_housing")
        tb.cylinder(tuple(b - d * 0.02), tuple(b + d * 0.03), 0.016, "fixture_housing")
        tag(tb.to_object(coll, dict(mats, tube=tube_mat)), role="practical", grounded=True)
        L = add_light(f"fallen_tube_light_{i}", "AREA", tuple(pos + Vector((0, 0, 0.02))), energy, color=lin,
                      direction=(0, 0, 1), size=(float(p.get("length", 1.2)), 0.03), coll=coll)
        L.rotation_euler.z = yaw + math.pi / 2
        L.data.spread = math.radians(180)
    elif kind == "spot":
        add_light(f"spot_{i}", "SPOT", tuple(pos), energy, color=lin, direction=p.get("direction", (0, 0, -1)),
                  size=float(p.get("radius", 0.05)), spot_deg=float(p.get("angle", 50)), coll=coll)
    elif kind == "area":
        add_light(f"area_{i}", "AREA", tuple(pos), energy, color=lin, direction=p.get("direction", (0, 0, -1)),
                  size=tuple(p.get("size", (0.5, 0.5))), coll=coll)
    else:
        add_light(f"point_{i}", "POINT", tuple(pos), energy, color=lin, size=float(p.get("radius", 0.05)), coll=coll)
    report["lights"].append({"name": f"practical_{i}", "kind": kind, "energy": energy})


def build_atmosphere(spec: dict, ctx: ArchContext, root) -> None:
    a = spec.get("atmosphere", {})
    if not a.get("enabled", True) or float(a.get("density", 0)) <= 0:
        return
    coll = collection("Atmosphere", root)
    mat = make_volume("fog", float(a["density"]), srgb_to_linear(a.get("color", (0.9, 0.9, 0.9))),
                      float(a.get("anisotropy", 0.4)), float(a.get("absorption", 0.0)),
                      noise_scale=float(a.get("noise_scale", 0.5)), noise_amount=float(a.get("noise_amount", 0.5)),
                      height_falloff=float(a.get("height_falloff", 0.0)))
    vb = MeshBuilder("fog_volume")
    m = 0.02
    vb.box((0, ctx.length / 2 + 2.0, ctx.height / 2), (ctx.width + 2 * ctx.wall_t + 3.0, ctx.length + 6.0,
                                                       ctx.height + 0.2 - m), "fog")
    obj = vb.to_object(coll, {"fog": mat})
    obj.display_type = "BOUNDS"
    tag(obj, role="volume")
    # dust motes: sparse tiny specks in the air, densest where light shafts are (near windows)
    n = int(a.get("dust_motes", 0))
    if n:
        rng = seeded(int(spec.get("seed", 1)), "dust")
        db = MeshBuilder("dust_motes")
        wins = ctx.windows()
        for _ in range(n):
            if wins and rng.random() < 0.7:
                o = rng.choice(wins)
                sx = -1 if o.side == "left" else 1
                p = (sx * (ctx.width / 2 - rng.uniform(0, ctx.width * 0.7)), o.center + rng.uniform(-1.5, 1.5),
                     rng.uniform(0.3, ctx.height - 0.2))
            else:
                p = (rng.uniform(-ctx.width / 2, ctx.width / 2), rng.uniform(0.5, 8.0), rng.uniform(0.3, ctx.height))
            db.sphere(p, rng.uniform(0.0006, 0.0016), "debris_plaster", subdiv=1)
        from .materials.library import make_surface
        dm = make_surface("dust_mote", {"base": {"color": (0.85, 0.82, 0.75), "roughness": 0.9}})
        tag(db.to_object(coll, {"debris_plaster": dm}), role="dust")
