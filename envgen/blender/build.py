"""Scene build orchestration: spec -> complete Blender scene (deterministic for a given spec+seed)."""
from __future__ import annotations

import copy
import time

import bpy

from ..spec.schema import deep_merge
from . import camera, lighting
from .geometry import architecture
from .materials import library
from .materials.presets import PRESETS
from .util import collection, reset_scene

SPECIAL = {"glass", "puddle_water"}


def build_materials(spec: dict) -> dict[str, bpy.types.Material]:
    overrides = spec.get("materials", {}) or {}
    mats: dict[str, bpy.types.Material] = {}
    for name, recipe in PRESETS.items():
        mats[name] = library.make_surface(name, deep_merge(recipe, overrides.get(name, {})))
    for name, recipe in overrides.items():  # spec-defined new materials
        if name not in mats and name not in SPECIAL:
            mats[name] = library.make_surface(name, recipe)
    g = overrides.get("glass", {})
    mats["glass"] = library.make_glass("glass", dirt=float(g.get("dirt", 0.45)), frosted=float(g.get("frosted", 0.0)))
    mats["puddle_water"] = library.make_water()
    return mats


def build_structures(spec: dict, mats: dict, root) -> list[str]:
    """Explicit spec-placed structures (any layout): pipe runs, stairs, columns, railings, terrain, vegetation."""
    from .geometry import industrial, nature
    from .util import seeded
    coll = collection("Structures", root)
    built = []
    for i, st in enumerate(spec.get("structures", []) or []):
        kind = st["kind"]
        name = st.get("name", f"{kind}_{i}")
        rng = seeded(int(spec.get("seed", 1)), f"structure{i}")
        if kind == "pipe_run":
            industrial.pipe_run(coll, mats, name, st["points"], float(st.get("radius", 0.08)),
                                st.get("material", "galvanized_duct"), float(st.get("flange_every", 3.0)),
                                st.get("support", "wall"), float(st.get("support_every", 2.0)), rng)
        elif kind == "staircase":
            industrial.staircase(coll, mats, name, st["base"], float(st.get("direction_deg", 90)), float(st["rise"]),
                                 float(st.get("width", 1.2)), float(st.get("riser", 0.175)),
                                 float(st.get("going", 0.27)), st.get("material_kind", "concrete"),
                                 st.get("railings", "both"), float(st.get("landing", 1.2)))
        elif kind == "column":
            industrial.steel_column(coll, mats, name, st["base"], float(st["height"]),
                                    yaw_deg=float(st.get("yaw_deg", 0)))
        elif kind == "railing":
            industrial.railing(coll, mats, name, st["points"], float(st.get("height", 1.0)))
        elif kind == "terrain":
            nature.terrain(coll, mats, name, tuple(st["size"]), tuple(st.get("center", (0, 0))),
                           float(st.get("resolution", 0.25)), float(st.get("amplitude", 1.5)),
                           float(st.get("feature_scale", 18.0)), float(st.get("ridged", 0.3)),
                           int(spec.get("seed", 1)))
        elif kind in ("grass", "ferns", "moss"):
            pos = [tuple(p) for p in st["positions"]]
            if kind == "grass":
                nature.grass_clumps(coll, mats, name, pos, rng)
            elif kind == "ferns":
                nature.ferns(coll, mats, name, pos, rng)
            else:
                for k, p in enumerate(pos):
                    nature.moss_patch(coll, mats, f"{name}_{k}", p, float(st.get("radius", 0.4)), rng,
                                      st.get("normal", "+z"))
        else:
            raise ValueError(f"unknown structure kind {kind!r}")
        built.append(name)
    return built


def build_scene(spec: dict) -> dict:
    """Build the whole scene. Returns a build report (timings, counts, context summary)."""
    t0 = time.time()
    reset_scene()
    root = collection(f"ENV_{spec['name']}")
    mats = build_materials(spec)
    layout = spec["layout"]["type"]
    if layout != "corridor":
        raise NotImplementedError(f"layout type {layout!r} not implemented yet (corridor is)")
    ctx = architecture.build_corridor(spec, mats, root)
    structures = build_structures(spec, mats, root)
    from . import story  # local import: story depends on props
    story_report = story.build_story(spec, ctx, mats, root)
    light_report = lighting.build_lighting(spec, ctx, mats, root)
    lighting.build_atmosphere(spec, ctx, root)
    cams = camera.build_cameras(spec, root)
    bpy.context.view_layer.update()
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    tris = sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in meshes)
    return {
        "seconds": round(time.time() - t0, 1),
        "objects": len(bpy.context.scene.objects),
        "meshes": len(meshes),
        "triangles": tris,
        "materials": len(bpy.data.materials),
        "lights": light_report["lights"],
        "story": story_report,
        "structures": structures,
        "cameras": [c.name for c in cams],
        "context": {
            "openings": [copy.copy(o.__dict__) | {"meta": {k: v for k, v in o.meta.items() if k != "blinds"}}
                         for o in ctx.openings],
            "collapse_zones": ctx.collapse_zones,
            "missing_ceiling_cells": len(ctx.missing_cells),
            "fixtures": len(ctx.fixtures),
        },
        "_ctx": ctx,
    }
