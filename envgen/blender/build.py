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
