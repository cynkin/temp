"""Scene validation: physical plausibility checks that catch the classic generated-scene failures.

Checks: floating / sunken grounded props, real-world scale vs declared nominal size, prop
interpenetration, props outside the walkable volume, meshes without materials, cameras
embedded in geometry, and an unlit scene. Returns structured findings (errors / warnings).
"""
from __future__ import annotations

import bpy
from mathutils import Vector


def _world_bbox(obj) -> tuple[Vector, Vector]:
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def _overlap_volume(a, b) -> float:
    d = [max(0.0, min(a[1][i], b[1][i]) - max(a[0][i], b[0][i])) for i in range(3)]
    return d[0] * d[1] * d[2]


def validate_scene(ctx=None, floor_z: float = 0.0, scale_tolerance: float = 0.35) -> dict:
    scene = bpy.context.scene
    bpy.context.view_layer.update()
    errors, warnings = [], []
    props = [o for o in scene.objects if o.type == "MESH" and o.get("envgen_role") == "prop"]

    for o in props:
        if o.get("envgen_grounded"):
            mw = o.matrix_world
            min_z = min((mw @ v.co).z for v in o.data.vertices)
            if min_z > floor_z + 0.015:
                errors.append({"check": "floating", "object": o.name, "gap_m": round(min_z - floor_z, 4)})
            elif min_z < floor_z - 0.015:
                errors.append({"check": "sunken", "object": o.name, "depth_m": round(floor_z - min_z, 4)})
        nominal = list(o.get("envgen_nominal_dims", []))
        if nominal and all(nominal):
            # local (object-space) extents: world AABBs inflate with yaw and would false-positive
            actual = sorted(o.dimensions)
            nom = sorted(nominal)
            # rotation (toppled props) permutes axes, so compare sorted extents
            ratios = [a / n for a, n in zip(actual, nom) if n > 0.05]
            if ratios and any(abs(r - 1) > scale_tolerance for r in ratios):
                warnings.append({"check": "scale", "object": o.name, "actual": [round(a, 2) for a in actual],
                                 "nominal": nom})
        if ctx is not None:
            xs = [(o.matrix_world @ v.co).x for v in o.data.vertices]
            lo, hi = Vector((min(xs), 0, 0)), Vector((max(xs), 0, 0))
            if lo.x < -ctx.width / 2 - 0.05 or hi.x > ctx.width / 2 + 0.05:
                warnings.append({"check": "outside_walls", "object": o.name,
                                 "x_range": [round(lo.x, 2), round(hi.x, 2)]})

    boxes = {o.name: _world_bbox(o) for o in props}
    names = list(boxes)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ov = _overlap_volume(boxes[a], boxes[b])
            if ov <= 0:
                continue
            va = _overlap_volume(boxes[a], boxes[a])
            vb = _overlap_volume(boxes[b], boxes[b])
            frac = ov / max(1e-6, min(va, vb))
            if frac > 0.25:
                warnings.append({"check": "interpenetration", "objects": [a, b], "bbox_overlap": round(frac, 2)})

    for o in scene.objects:
        if o.type == "MESH" and len(o.data.polygons) and not any(o.data.materials):
            errors.append({"check": "no_material", "object": o.name})

    depsgraph = bpy.context.evaluated_depsgraph_get()
    for cam in [o for o in scene.objects if o.type == "CAMERA"]:
        origin = cam.matrix_world.translation
        fwd = cam.matrix_world.to_3x3() @ Vector((0, 0, -1))
        hit, loc, *_ = scene.ray_cast(depsgraph, origin, fwd, distance=0.4)
        if hit:
            errors.append({"check": "camera_blocked", "camera": cam.name, "hit_at_m": round((loc - origin).length, 3)})
        if ctx is not None and not (-ctx.width / 2 < origin.x < ctx.width / 2 and 0 < origin.y < ctx.length
                                    and 0 < origin.z < ctx.height):
            warnings.append({"check": "camera_outside_space", "camera": cam.name})

    lights = [o for o in scene.objects if o.type == "LIGHT" and o.data.energy > 0 and
              not getattr(o.data.cycles, "is_portal", False)]
    if not lights:
        errors.append({"check": "unlit", "detail": "no emitting lights"})
    meshes = [o for o in scene.objects if o.type == "MESH"]
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": {"props": len(props), "meshes": len(meshes), "lights": len(lights),
                  "triangles": sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in meshes)},
    }
