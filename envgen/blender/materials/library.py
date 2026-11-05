"""Layered procedural PBR material system for Cycles.

Every material is built from a *recipe* (a dict, overridable from the spec) as a
stack of physically-motivated layers, each driven by a mask that has a cause:

    base        colour/roughness variation at two scales (breaks tiling)
    pattern     tiles / bricks / panels with recessed grout (real bump)
    peel        flaking paint revealing an under-layer, with paint-edge relief
    streaks     vertical water-damage streaks, strongest from the ceiling down
    grime       dirt in cavities (AO) and near the floor (height mask)
    wet         puddle/wet-sheen mask that lowers roughness and darkens albedo
    moss        organic growth on low, upward-facing or cavity areas
    rust        oxidation on metals in cavities and broken-paint areas

Coordinates are world-space position (seamless across modular wall pieces) with a
per-object random offset for props, so no two instances share the same wear.
Image-based texture sets from the asset library can replace the base layer via
``recipe["texture_set"]`` (handled by envgen.blender.asset_import).
"""
from __future__ import annotations

import warnings
from typing import Any

import bpy

from ..util import srgb_to_linear

Socket = Any


class Graph:
    """Tiny helper around a node tree to keep shader construction readable."""

    def __init__(self, mat: bpy.types.Material):
        self.nt = mat.node_tree
        self.nodes, self.links = self.nt.nodes, self.nt.links
        self._x = 0

    def node(self, kind: str, inputs: dict | None = None, **props):
        n = self.nodes.new(kind)
        n.location = (self._x, 0)
        self._x += 40
        for k, v in props.items():
            setattr(n, k, v)
        for k, v in (inputs or {}).items():
            self.set(n.inputs[k], v)
        return n

    def set(self, sock, value):
        if hasattr(value, "is_output") or isinstance(value, bpy.types.NodeSocket):
            self.links.new(value, sock)
        else:
            sock.default_value = value

    # -- math
    def math(self, op: str, a, b=0.0, clamp: bool = False) -> Socket:
        n = self.node("ShaderNodeMath", operation=op, use_clamp=clamp)
        self.set(n.inputs[0], a)
        self.set(n.inputs[1], b)
        return n.outputs[0]

    def mul(self, a, b, clamp=False):
        return self.math("MULTIPLY", a, b, clamp)

    def add(self, a, b, clamp=False):
        return self.math("ADD", a, b, clamp)

    def maxi(self, a, b):
        return self.math("MAXIMUM", a, b)

    def map_range(self, value, f0, f1, t0=0.0, t1=1.0, interp="LINEAR") -> Socket:
        n = self.node("ShaderNodeMapRange", interpolation_type=interp, clamp=True)
        self.set(n.inputs["Value"], value)
        for k, v in (("From Min", f0), ("From Max", f1), ("To Min", t0), ("To Max", t1)):
            self.set(n.inputs[k], v)
        return n.outputs["Result"]

    def mix_f(self, fac, a, b) -> Socket:
        n = self.node("ShaderNodeMix", data_type="FLOAT", clamp_factor=True)
        self.set(n.inputs[0], fac)
        self.set(n.inputs[2], a)
        self.set(n.inputs[3], b)
        return n.outputs[0]

    def mix_c(self, fac, a, b, blend: str = "MIX") -> Socket:
        n = self.node("ShaderNodeMix", data_type="RGBA", blend_type=blend, clamp_factor=True)
        self.set(n.inputs[0], fac)
        self.set(n.inputs[6], a)
        self.set(n.inputs[7], b)
        return n.outputs[2]

    def rgb(self, color) -> Socket:
        n = self.node("ShaderNodeRGB")
        n.outputs[0].default_value = (*color, 1.0)
        return n.outputs[0]

    def vec_scale(self, vec, scale) -> Socket:
        n = self.node("ShaderNodeVectorMath", operation="MULTIPLY")
        self.set(n.inputs[0], vec)
        n.inputs[1].default_value = scale if isinstance(scale, (tuple, list)) else (scale, scale, scale)
        return n.outputs[0]

    def vec_add(self, a, b) -> Socket:
        n = self.node("ShaderNodeVectorMath", operation="ADD")
        self.set(n.inputs[0], a)
        self.set(n.inputs[1], b)
        return n.outputs[0]

    def noise(self, vec, scale: float, detail: float = 6.0, rough: float = 0.55, distortion: float = 0.0,
              kind: str = "FBM", dims: str = "3D") -> Socket:
        n = self.node("ShaderNodeTexNoise", noise_type=kind, noise_dimensions=dims)
        self.set(n.inputs["Vector"], vec)
        n.inputs["Scale"].default_value = scale
        n.inputs["Detail"].default_value = detail
        n.inputs["Roughness"].default_value = rough
        n.inputs["Distortion"].default_value = distortion
        return n.outputs["Fac"]

    def voronoi(self, vec, scale: float, feature: str = "F1", output: str = "Distance") -> Socket:
        n = self.node("ShaderNodeTexVoronoi", feature=feature)
        self.set(n.inputs["Vector"], vec)
        n.inputs["Scale"].default_value = scale
        return n.outputs[output]

    def separate(self, vec):
        n = self.node("ShaderNodeSeparateXYZ")
        self.set(n.inputs[0], vec)
        return n.outputs["X"], n.outputs["Y"], n.outputs["Z"]

    def combine(self, x, y, z):
        n = self.node("ShaderNodeCombineXYZ")
        for i, v in enumerate((x, y, z)):
            self.set(n.inputs[i], v)
        return n.outputs[0]


def _lin(c) -> tuple:
    return srgb_to_linear(c[:3])


def _new_material(name: str) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mat.use_nodes = True
    mat.node_tree.nodes.clear()
    return mat


# ----------------------------------------------------------------------------- surfaces

def make_surface(name: str, recipe: dict) -> bpy.types.Material:
    """Build a layered Principled BSDF material from ``recipe`` (see module docstring)."""
    mat = _new_material(name)
    g = Graph(mat)
    out = g.node("ShaderNodeOutputMaterial")
    bsdf = g.node("ShaderNodeBsdfPrincipled")
    g.links.new(bsdf.outputs[0], out.inputs["Surface"])

    base = recipe.get("base", {})
    scale = float(recipe.get("scale", 1.0))

    # ---- coordinates: world position (+ per-object random offset for props)
    geo = g.node("ShaderNodeNewGeometry")
    pos = geo.outputs["Position"]
    if recipe.get("per_object_variation", True):
        oi = g.node("ShaderNodeObjectInfo")
        offs = g.vec_scale(g.combine(oi.outputs["Random"], g.mul(oi.outputs["Random"], 7.31),
                                     g.mul(oi.outputs["Random"], 3.17)), 37.0)
        pos = g.vec_add(pos, offs)
    co = g.vec_scale(pos, 1.0 / max(scale, 1e-3))
    x, y, z = g.separate(geo.outputs["Position"])
    world_z = z

    # 2D pattern coordinates: floor/ceiling use XY, walls use (x+y, z) - seamless along any axis-aligned wall.
    proj = recipe.get("projection", "auto")
    if proj == "xy":
        uv = g.combine(x, y, 0.0)
    elif proj == "wall":
        uv = g.combine(g.add(x, y), z, 0.0)
    else:
        uv = None

    # ---- base colour with two-scale variation (macro breaks repetition, micro gives grain)
    col = _lin(base.get("color", (0.6, 0.6, 0.6)))
    var = float(base.get("color_var", 0.12))
    macro = g.noise(co, 0.35, detail=3.0, rough=0.5)
    micro = g.noise(co, 9.0, detail=10.0, rough=0.62)
    v = g.add(g.mul(g.add(macro, -0.5), 2.0 * var), g.mul(g.add(micro, -0.5), var))
    dark = tuple(max(0.0, c * (1 - var * 2.0)) for c in col)
    light = tuple(min(1.0, c * (1 + var * 2.0)) for c in col)
    color = g.mix_c(g.add(v, 0.5, clamp=True), g.rgb(dark), g.rgb(light))
    hue_shift = base.get("hue_var_color")
    if hue_shift:
        color = g.mix_c(g.mul(g.noise(co, 0.6, 2.0), float(base.get("hue_var", 0.35))), color,
                        g.rgb(_lin(hue_shift)))

    rough0 = float(base.get("roughness", 0.65))
    rvar = float(base.get("rough_var", 0.15))
    rough = g.add(rough0 - rvar / 2, g.mul(g.noise(co, 2.2, 8.0, 0.6), rvar))
    metal = float(base.get("metallic", 0.0))
    metallic: Any = metal

    height = g.mul(g.noise(co, float(recipe.get("bump", {}).get("scale", 7.0)),
                           float(recipe.get("bump", {}).get("detail", 9.0)), 0.6), 1.0)
    bump_strength = float(recipe.get("bump", {}).get("strength", 0.12))

    # ---- pattern (tiles, bricks)
    pat = recipe.get("pattern")
    if pat and uv is not None:
        size = pat.get("size", [0.3, 0.3])
        brick = g.node("ShaderNodeTexBrick", offset=float(pat.get("offset", 0.0)),
                       offset_frequency=2, squash=1.0, squash_frequency=2)
        g.set(brick.inputs["Vector"], uv)
        brick.inputs["Scale"].default_value = 1.0
        brick.inputs["Brick Width"].default_value = size[0]
        brick.inputs["Row Height"].default_value = size[1]
        brick.inputs["Mortar Size"].default_value = float(pat.get("mortar", 0.004))
        brick.inputs["Mortar Smooth"].default_value = 0.15
        brick.inputs["Bias"].default_value = 0.0
        tv = float(pat.get("tile_var", 0.08))
        brick.inputs["Color1"].default_value = (1 - tv, 1 - tv, 1 - tv, 1)
        brick.inputs["Color2"].default_value = (1 + tv * 0.5, 1 + tv * 0.5, 1 + tv * 0.5, 1)
        tile_tint = brick.outputs["Color"]
        mortar = brick.outputs["Fac"]
        color = g.mix_c(1.0, color, tile_tint, blend="MULTIPLY")
        if pat.get("alt_color"):  # checker of two tones (vinyl / terrazzo floors)
            chk = g.node("ShaderNodeTexChecker")
            g.set(chk.inputs["Vector"], uv)
            chk.inputs["Scale"].default_value = 1.0 / size[0] * 0.5
            color = g.mix_c(g.mul(chk.outputs["Fac"], float(pat.get("alt_mix", 1.0))), color,
                            g.mix_c(1.0, g.rgb(_lin(pat["alt_color"])), tile_tint, blend="MULTIPLY"))
        mortar_col = g.rgb(_lin(pat.get("mortar_color", (0.18, 0.17, 0.15))))
        color = g.mix_c(mortar, color, mortar_col)
        rough = g.mix_f(mortar, rough, 0.95)
        height = g.add(height, g.mul(g.math("SUBTRACT", 1.0, mortar), float(pat.get("relief", 1.6))))
        bump_strength = max(bump_strength, 0.25)

    # ---- peeling paint (paint layer chips away revealing substrate; edge relief from paint thickness)
    peel = recipe.get("peel")
    if peel and peel.get("amount", 0) > 0:
        n = g.noise(co, float(peel.get("scale", 1.4)), 12.0, 0.68, distortion=0.25)
        n2 = g.noise(co, float(peel.get("scale", 1.4)) * 4.0, 6.0, 0.6)
        n = g.add(g.mul(n, 0.8), g.mul(n2, 0.2))
        thr = 1.0 - float(peel["amount"]) * 0.5 - 0.3
        mask = g.map_range(n, thr, thr + 0.025)
        sub = g.rgb(_lin(peel.get("under_color", (0.45, 0.42, 0.37))))
        sub_var = g.mix_c(g.noise(co, 14.0, 4.0), sub, g.rgb(tuple(c * 0.7 for c in _lin(
            peel.get("under_color", (0.45, 0.42, 0.37))))))
        color = g.mix_c(mask, color, sub_var)
        rough = g.mix_f(mask, rough, 0.9)
        height = g.add(height, g.mul(g.math("SUBTRACT", 1.0, mask), float(peel.get("thickness", 3.0))))
        bump_strength = max(bump_strength, 0.2)

    # ---- water-damage streaks running down from the ceiling
    st = recipe.get("streaks")
    if st and st.get("amount", 0) > 0:
        if uv is None:
            su, sz = g.add(x, y), z
        else:
            su, sz = g.separate(uv)[0], g.separate(uv)[1]
        streak_co = g.combine(g.mul(su, 9.0), g.mul(sz, 0.45), 0.0)
        s = g.noise(streak_co, 1.0, 6.0, 0.55, distortion=0.4)
        s = g.map_range(s, 0.52, 0.72)
        top = float(st.get("top", 2.9))
        vfade = g.map_range(world_z, top - float(st.get("length", 1.6)), top)
        smask = g.mul(g.mul(s, vfade), float(st["amount"]))
        # a stain *ring* where water pooled: tide-mark band near the bottom of the streak area
        color = g.mix_c(smask, color, g.rgb(_lin(st.get("color", (0.33, 0.27, 0.18)))), blend="MULTIPLY")
        rough = g.mix_f(g.mul(smask, 0.5), rough, 0.4)

    # ---- grime: cavities (AO) + floor proximity + broken noise
    gr = recipe.get("grime")
    if gr and gr.get("amount", 0) > 0:
        ao = g.node("ShaderNodeAmbientOcclusion", samples=int(gr.get("ao_samples", 6)), only_local=False)
        ao.inputs["Distance"].default_value = float(gr.get("ao_distance", 0.4))
        cavity = g.math("SUBTRACT", 1.0, ao.outputs["AO"])
        floor_h = float(gr.get("floor_height", 0.35))
        near_floor = g.map_range(world_z, floor_h, 0.0)
        breakup = g.map_range(g.noise(co, 3.0, 8.0, 0.6), 0.35, 0.7)
        gmask = g.mul(g.maxi(g.mul(cavity, 1.4), near_floor), breakup)
        gmask = g.mul(gmask, float(gr["amount"]), clamp=True)
        color = g.mix_c(gmask, color, g.rgb(_lin(gr.get("color", (0.12, 0.1, 0.08)))))
        rough = g.mix_f(gmask, rough, float(gr.get("roughness", 0.92)))

    # ---- rust (metals): cavities + noise, kills metallic response
    rs = recipe.get("rust")
    if rs and rs.get("amount", 0) > 0:
        rn = g.noise(co, float(rs.get("scale", 3.0)), 10.0, 0.7, distortion=0.3)
        rmask = g.map_range(rn, 0.62 - 0.25 * float(rs["amount"]), 0.66 - 0.25 * float(rs["amount"]))
        rust_col = g.mix_c(g.noise(co, 18.0, 4.0), g.rgb(_lin(rs.get("color", (0.36, 0.16, 0.06)))),
                           g.rgb(_lin((0.22, 0.09, 0.04))))
        color = g.mix_c(rmask, color, rust_col)
        rough = g.mix_f(rmask, rough, 0.88)
        metallic = g.mix_f(rmask, metal, 0.0)
        height = g.add(height, g.mul(rmask, 1.5))

    # ---- moss / organic growth: low areas + upward facing + noise
    ms = recipe.get("moss")
    if ms and ms.get("amount", 0) > 0:
        nz = g.separate(geo.outputs["Normal"])[2]
        up = g.map_range(nz, 0.2, 0.9)
        low = g.map_range(world_z, float(ms.get("height", 0.6)), 0.0)
        mn = g.map_range(g.noise(co, float(ms.get("scale", 2.5)), 10.0, 0.7), 0.5, 0.62)
        mmask = g.mul(g.mul(g.maxi(up, low), mn), float(ms["amount"]), clamp=True)
        moss_col = g.mix_c(g.noise(co, 25.0, 4.0), g.rgb(_lin(ms.get("color", (0.17, 0.22, 0.06)))),
                           g.rgb(_lin((0.25, 0.28, 0.08))))
        color = g.mix_c(mmask, color, moss_col)
        rough = g.mix_f(mmask, rough, 0.95)
        height = g.add(height, g.mul(mmask, 2.0))

    # ---- wet patches / puddle sheen (darker albedo, mirror-like roughness)
    wt = recipe.get("wet")
    if wt and wt.get("amount", 0) > 0:
        wn = g.noise(co, float(wt.get("scale", 0.45)), 4.0, 0.5)
        thr = 0.66 - 0.3 * float(wt["amount"])
        wmask = g.map_range(wn, thr, thr + 0.06)
        color = g.mix_c(g.mul(wmask, 0.55), color, g.rgb((0.0, 0.0, 0.0)))
        rough = g.mix_f(wmask, rough, float(wt.get("roughness", 0.04)))
        height = g.mix_f(wmask, height, 0.0)

    # ---- outputs
    g.set(bsdf.inputs["Base Color"], color)
    g.set(bsdf.inputs["Roughness"], rough)
    g.set(bsdf.inputs["Metallic"], metallic)
    if "specular" in base:
        bsdf.inputs["Specular IOR Level"].default_value = float(base["specular"])
    if base.get("coat"):
        bsdf.inputs["Coat Weight"].default_value = float(base["coat"])
        bsdf.inputs["Coat Roughness"].default_value = float(base.get("coat_roughness", 0.1))
    if base.get("transmission"):
        bsdf.inputs["Transmission Weight"].default_value = float(base["transmission"])
        bsdf.inputs["IOR"].default_value = float(base.get("ior", 1.45))
    if base.get("subsurface"):  # thin organic matter (leaves, grass) transmits light
        bsdf.inputs["Subsurface Weight"].default_value = float(base["subsurface"])
        bsdf.inputs["Subsurface Radius"].default_value = (0.05, 0.08, 0.02)
        bsdf.inputs["Subsurface Scale"].default_value = 0.01
    if base.get("sheen"):
        bsdf.inputs["Sheen Weight"].default_value = float(base["sheen"])
    if base.get("alpha") is not None:
        bsdf.inputs["Alpha"].default_value = float(base["alpha"])
    bump = g.node("ShaderNodeBump")
    bump.inputs["Strength"].default_value = bump_strength
    bump.inputs["Distance"].default_value = float(recipe.get("bump", {}).get("distance", 0.004))
    g.set(bump.inputs["Height"], height)
    g.set(bsdf.inputs["Normal"], bump.outputs["Normal"])
    mat["envgen_recipe"] = str(recipe)
    return mat


def make_emissive(name: str, color, strength: float, base_color=(0.9, 0.9, 0.9), glow_mix: float = 1.0,
                  flicker_noise: bool = False) -> bpy.types.Material:
    """Diffuser / sign material: emission on top of a diffuse base (so dead lamps still read as objects)."""
    mat = _new_material(name)
    g = Graph(mat)
    out = g.node("ShaderNodeOutputMaterial")
    bsdf = g.node("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (*_lin(base_color), 1)
    bsdf.inputs["Roughness"].default_value = 0.35
    bsdf.inputs["Emission Color"].default_value = (*color[:3], 1)
    if flicker_noise:  # uneven phosphor ageing along the tube: darker ends
        geo = g.node("ShaderNodeNewGeometry")
        n = g.map_range(g.noise(geo.outputs["Position"], 6.0, 2.0), 0.3, 0.7, 0.55, 1.0)
        g.set(bsdf.inputs["Emission Strength"], g.mul(n, strength * glow_mix))
    else:
        bsdf.inputs["Emission Strength"].default_value = strength * glow_mix
    g.links.new(bsdf.outputs[0], out.inputs["Surface"])
    return mat


def make_glass(name: str, dirt: float = 0.4, tint=(0.85, 0.9, 0.88), frosted: float = 0.0) -> bpy.types.Material:
    """Dirty window glass: transmission with grime-driven roughness and slight tint."""
    mat = _new_material(name)
    g = Graph(mat)
    out = g.node("ShaderNodeOutputMaterial")
    bsdf = g.node("ShaderNodeBsdfPrincipled")
    geo = g.node("ShaderNodeNewGeometry")
    n = g.noise(geo.outputs["Position"], 3.0, 8.0, 0.65)
    dmask = g.map_range(n, 0.62 - dirt * 0.4, 0.75)
    bsdf.inputs["Transmission Weight"].default_value = 1.0
    bsdf.inputs["IOR"].default_value = 1.5
    g.set(bsdf.inputs["Base Color"], g.mix_c(dmask, g.rgb(_lin(tint)), g.rgb((0.25, 0.23, 0.2))))
    g.set(bsdf.inputs["Roughness"], g.mix_f(dmask, 0.03 + frosted, 0.55))
    # Cycles blocks shadow rays at transmissive surfaces: let shadow rays pass through a (dirt-tinted)
    # transparent BSDF so sunlight actually streams through windows and is striped by the blinds.
    lp = g.node("ShaderNodeLightPath")
    transp = g.node("ShaderNodeBsdfTransparent")
    g.set(transp.inputs["Color"], g.mix_c(dmask, g.rgb((0.92, 0.95, 0.93)), g.rgb((0.45, 0.42, 0.38))))
    mix = g.node("ShaderNodeMixShader")
    g.links.new(lp.outputs["Is Shadow Ray"], mix.inputs[0])
    g.links.new(bsdf.outputs[0], mix.inputs[1])
    g.links.new(transp.outputs[0], mix.inputs[2])
    g.links.new(mix.outputs[0], out.inputs["Surface"])
    mat["envgen_recipe"] = "glass"
    return mat


def make_water(name: str = "puddle_water") -> bpy.types.Material:
    """Shallow standing water: near-black albedo, mirror roughness, tiny ripples."""
    mat = _new_material(name)
    g = Graph(mat)
    out = g.node("ShaderNodeOutputMaterial")
    bsdf = g.node("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (0.012, 0.012, 0.011, 1)
    bsdf.inputs["Roughness"].default_value = 0.02
    bsdf.inputs["IOR"].default_value = 1.33
    bsdf.inputs["Specular IOR Level"].default_value = 0.5
    geo = g.node("ShaderNodeNewGeometry")
    bump = g.node("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.04
    g.set(bump.inputs["Height"], g.noise(geo.outputs["Position"], 14.0, 3.0, 0.5))
    g.links.new(bump.outputs[0], bsdf.inputs["Normal"])
    g.links.new(bsdf.outputs[0], out.inputs["Surface"])
    return mat


def make_image_material(name: str, image_path: str, roughness: float = 0.7, emission: float = 0.0,
                        grime: float = 0.35, alpha: bool = False) -> bpy.types.Material:
    """UV-mapped image (signage, posters, documents) aged with grime/fade so it never looks pasted on."""
    mat = _new_material(name)
    g = Graph(mat)
    out = g.node("ShaderNodeOutputMaterial")
    bsdf = g.node("ShaderNodeBsdfPrincipled")
    tex = g.node("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(image_path, check_existing=True)
    tc = g.node("ShaderNodeTexCoord")
    g.links.new(tc.outputs["UV"], tex.inputs["Vector"])
    geo = g.node("ShaderNodeNewGeometry")
    dirt = g.map_range(g.noise(geo.outputs["Position"], 5.0, 8.0, 0.65), 0.45, 0.8)
    faded = g.mix_c(g.mul(dirt, grime), tex.outputs["Color"], g.rgb((0.3, 0.26, 0.2)))
    g.set(bsdf.inputs["Base Color"], faded)
    bsdf.inputs["Roughness"].default_value = roughness
    if emission > 0:
        g.set(bsdf.inputs["Emission Color"], tex.outputs["Color"])
        bsdf.inputs["Emission Strength"].default_value = emission
    if alpha:
        g.set(bsdf.inputs["Alpha"], tex.outputs["Alpha"])
    g.links.new(bsdf.outputs[0], out.inputs["Surface"])
    return mat


def make_volume(name: str, density: float, color, anisotropy: float, absorption: float = 0.0,
                noise_scale: float = 0.6, noise_amount: float = 0.6, height_falloff: float = 0.0
                ) -> bpy.types.Material:
    """Heterogeneous participating medium: noise-modulated density (+ optional ground-hugging falloff)."""
    mat = _new_material(name)
    g = Graph(mat)
    out = g.node("ShaderNodeOutputMaterial")
    vol = g.node("ShaderNodeVolumePrincipled")
    vol.inputs["Color"].default_value = (*color[:3], 1)
    vol.inputs["Anisotropy"].default_value = anisotropy
    vol.inputs["Absorption Color"].default_value = (absorption, absorption, absorption, 1)
    geo = g.node("ShaderNodeNewGeometry")
    n = g.noise(geo.outputs["Position"], noise_scale, 3.0, 0.5)
    dens = g.mul(g.map_range(n, 0.3, 0.7, 1.0 - noise_amount, 1.0 + noise_amount * 0.5), density)
    if height_falloff > 0:
        z = g.separate(geo.outputs["Position"])[2]
        dens = g.mul(dens, g.map_range(z, 0.0, 1.0 / height_falloff, 1.6, 0.6))
    g.set(vol.inputs["Density"], dens)
    g.links.new(vol.outputs[0], out.inputs["Volume"])
    return mat
