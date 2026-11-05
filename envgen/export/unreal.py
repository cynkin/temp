"""Export the current Blender scene for Unreal Engine 5: per-mesh GLBs + ``manifest.json``.

Strategy
--------
**One GLB per unique mesh, instances as transforms.** Every visible, renderable mesh in the
evaluated depsgraph (including geometry-nodes / collection / particle instances) is keyed by
its source geometry:

* a plain object without modifiers -> its mesh datablock + material list, so *linked
  duplicates* (Alt+D) and repeated scatter instances share one GLB;
* an object with modifiers -> its own evaluated mesh (one GLB per object);
* geometry-nodes instances of real objects -> the instanced object's mesh; instances of raw
  geometry -> keyed by the evaluated mesh pointer (shared when the GN tree shares it).

Each unique mesh is written *at identity transform* (modifiers applied, object space) to
``meshes/<name>.glb`` with ``bpy.ops.export_scene.gltf(export_format='GLB', use_selection=True,
export_apply=True, export_yup=True)``. The manifest lists every placement, so the UE importer
(``unreal/import_envgen_scene.py``) creates one StaticMesh asset per GLB and many
StaticMeshActors -- repeated props share a mesh asset (instanced static meshes / auto-
instancing in UE).

Units and axes (documented in the manifest under ``"units"``)
------------------------------------------------------------
Blender: metres, Z-up, right-handed, -Y "forward" for the front view. Unreal: centimetres,
Z-up, **left-handed** (X forward, Y right). Positions convert by mirroring Y::

    x_ue = x * 100        y_ue = -y * 100        z_ue = z * 100

Rotations: with that mirror (S = diag(1,-1,1)) a Blender rotation R becomes S R S. For
Blender XYZ Euler (rx, ry, rz) in radians this gives the UE Rotator (degrees)::

    roll = deg(rx)        pitch = -deg(ry)        yaw = -deg(rz)

Lights and cameras additionally differ in their local "forward" axis (Blender: local -Z,
with +Y up for cameras; UE: local +X with +Z up). For those we first right-multiply the world
matrix by Q (maps +X -> -Z, +Z -> +Y) and then apply the rule above. Scale is unchanged
(the mirror cancels). Every instance/light/camera carries both the raw Blender values and a
pre-converted ``"ue"`` block, so the UE script doesn't need to redo the maths.

Exposure: ``render.ue_ev100_equivalent = log2(683/1.2) - view_settings.exposure`` is the
UE manual EV100 that matches Blender's brightness under these unit conversions; an explicit
``post["exposure_ev100"]`` from ``scene_meta`` takes precedence in the UE script.

Mesh vertices: the GLBs are standard glTF (metres, +Y up); UE's glTF/Interchange importer
converts to cm / Z-up itself. NOTE (untested here): if a UE version's glTF importer mirrors a
different axis than Y, meshes would appear mirrored relative to the instance transforms --
check one asymmetric prop after the first import.

Light intensity hints (approximate; same constants as Blender's glTF exporter, 683 lm/W):

* point / spot: ``lumens = W * 683 * 2**exposure`` (``candela = lumens / 4pi``). Both Blender
  and UE treat spot intensity as that of an equivalent point light (cone only masks).
* area (UE RectLight): ``lumens = W * 683``; ``candela ~ lumens / pi`` (Lambertian emitter).
* sun (UE DirectionalLight): ``lux = strength[W/m2] * 683``.

These are physically-motivated; absolute brightness in UE depends on exposure, so the
manifest's ``post`` block (copied from ``scene_meta["post"]``) should carry the exposure used
to render in Blender (the UE script applies it as manual exposure).
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Iterable

import bpy
from mathutils import Matrix, Vector

__all__ = [
    "export_for_unreal",
    "blender_to_ue_location",
    "blender_to_ue_rotation",
    "blender_light_to_ue_intensity",
    "MANIFEST_VERSION",
]

MANIFEST_VERSION = 1
LUMENS_PER_WATT = 683.0

# Q: maps an "X-forward, Z-up" frame onto Blender's camera/light frame (-Z forward, +Y up).
_Q_FORWARD = Matrix(((0.0, -1.0, 0.0),
                     (0.0, 0.0, 1.0),
                     (-1.0, 0.0, 0.0))).to_4x4()


# ----------------------------------------------------------------------------- conversions

def blender_to_ue_location(loc: Iterable[float]) -> list[float]:
    x, y, z = loc
    return [x * 100.0, -y * 100.0, z * 100.0]


def blender_to_ue_rotation(rot: Matrix | Iterable[float], forward_axis_fix: bool = False) -> dict:
    """Blender rotation (3x3/4x4 matrix, or XYZ Euler radians) -> UE Rotator dict (degrees).

    ``forward_axis_fix=True`` for lights/cameras (Blender -Z forward -> UE +X forward)."""
    if isinstance(rot, Matrix):
        m = rot.to_4x4() if len(rot) == 3 else rot.copy()
    else:
        from mathutils import Euler

        m = Euler(tuple(rot), "XYZ").to_matrix().to_4x4()
    # strip scale
    loc, quat, _ = m.decompose()
    m = quat.to_matrix().to_4x4()
    if forward_axis_fix:
        m = m @ _Q_FORWARD
    e = m.to_euler("XYZ")
    return {"roll": math.degrees(e.x), "pitch": -math.degrees(e.y), "yaw": -math.degrees(e.z)}


def blender_light_to_ue_intensity(light: bpy.types.Light) -> dict:
    exp = 2.0 ** float(getattr(light, "exposure", 0.0) or 0.0)
    if light.type == "SUN":
        lux = float(light.energy) * LUMENS_PER_WATT * exp
        return {"value": lux, "units": "lux", "lux": lux,
                "note": "sun strength W/m^2 * 683 lm/W"}
    lumens = float(light.energy) * LUMENS_PER_WATT * exp
    if light.type == "AREA":
        return {"value": lumens, "units": "lumens", "lumens": lumens,
                "candela": lumens / math.pi,
                "note": "area W * 683 lm/W; candela assumes a Lambertian emitter (lm/pi)"}
    return {"value": lumens, "units": "lumens", "lumens": lumens,
            "candela": lumens / (4.0 * math.pi),
            "note": "point/spot W * 683 lm/W (spot intensity as an equivalent point light)"}


# ----------------------------------------------------------------------------- helpers

_SAFE = re.compile(r"[^A-Za-z0-9_]+")


def _safe_name(name: str, used: set[str]) -> str:
    base = _SAFE.sub("_", name).strip("_") or "mesh"
    if base[0].isdigit():
        base = "M_" + base
    n, i = base, 2
    while n.lower() in used:
        n = f"{base}_{i}"
        i += 1
    used.add(n.lower())
    return n


def _vec(v) -> list[float]:
    return [round(float(c), 6) for c in v]


def _transform_record(mw: Matrix, forward_axis_fix: bool = False) -> dict:
    loc, quat, scale = mw.decompose()
    eul = quat.to_euler("XYZ")
    return {
        "location": _vec(loc),
        "rotation_euler": _vec(eul),
        "rotation_quaternion": _vec(quat),  # (w, x, y, z)
        "scale": _vec(scale),
        "ue": {
            "location_cm": _vec(blender_to_ue_location(loc)),
            "rotation": {k: round(v, 5) for k, v in
                         blender_to_ue_rotation(quat.to_matrix(), forward_axis_fix).items()},
            "scale": _vec(scale),
        },
    }


def _is_visible(ob: bpy.types.Object, view_layer) -> bool:
    try:
        return (not ob.hide_render) and ob.visible_get(view_layer=view_layer)
    except (RuntimeError, TypeError):
        return not ob.hide_render


def _material_names(ob: bpy.types.Object) -> tuple[str, ...]:
    return tuple((s.material.name if s.material else "") for s in ob.material_slots)


def _has_modifiers(ob: bpy.types.Object) -> bool:
    return any(m.show_render for m in getattr(ob, "modifiers", []))


# ----------------------------------------------------------------------------- export

def _collect_mesh_instances(depsgraph, view_layer, include_types=("MESH", "CURVE", "SURFACE",
                                                                  "META", "FONT")):
    """Yield (key, display_name, source_name, eval_object, matrix_world).

    Must be consumed while iterating: instance objects are only valid during iteration."""
    for inst in depsgraph.object_instances:
        ob = inst.object
        if ob.type not in include_types:
            continue
        if ob.type == "MESH" and ob.data is not None and len(ob.data.vertices) == 0:
            continue  # e.g. a geometry-nodes object whose output is only instances
        if inst.is_instance:
            parent = inst.parent.original if inst.parent else None
            if parent is not None and not _is_visible(parent, view_layer):
                continue
            orig = ob.original
            if orig is not None and orig.type == ob.type and orig.name in bpy.data.objects \
                    and (parent is None or orig.name != parent.name) and orig.data is not None:
                if _has_modifiers(orig):
                    key = ("obj", orig.name)
                else:
                    key = ("data", orig.data.name, _material_names(orig))
                name = orig.name
            else:
                data = ob.data
                key = ("ptr", data.as_pointer() if data is not None else id(ob))
                name = f"{parent.name if parent else 'inst'}_geo"
            yield key, name, (parent.name if parent else name), ob, inst.matrix_world.copy()
            continue
        else:
            orig = ob.original
            if not _is_visible(orig, view_layer):
                continue
            if orig.type == "MESH" and not _has_modifiers(orig):
                key = ("data", orig.data.name, _material_names(orig))
                name = orig.data.name
            else:
                key = ("obj", orig.name)
                name = orig.name
        yield key, name, orig.name, ob, inst.matrix_world.copy()


def _copy_evaluated_mesh(eval_ob, depsgraph, raw_instance: bool = False) -> bpy.types.Mesh:
    """Standalone Mesh datablock of ``eval_ob``'s evaluated geometry (object space).

    ``raw_instance``: geometry-nodes instance of raw geometry -- ``new_from_object`` would
    return the *instancer's* own mesh, so the instance's mesh data is copied via bmesh."""
    if raw_instance and eval_ob.type == "MESH" and eval_ob.data is not None:
        import bmesh

        mesh = bpy.data.meshes.new("envgen_tmp")
        bm = bmesh.new()
        try:
            bm.from_mesh(eval_ob.data)
            bm.to_mesh(mesh)
        finally:
            bm.free()
        for m in eval_ob.data.materials:
            mesh.materials.append(m.original if m is not None and getattr(m, "original", None) else m)
        return mesh
    mesh = bpy.data.meshes.new_from_object(eval_ob, preserve_all_data_layers=True,
                                           depsgraph=depsgraph)
    # object-linked material slots are not carried by the mesh: copy explicitly
    mats = [s.material for s in getattr(eval_ob, "material_slots", [])]
    if mats and len(mesh.materials) == len(mats):
        for i, m in enumerate(mats):
            if m is not None:
                mesh.materials[i] = m.original if getattr(m, "original", None) else m
    elif mats and not len(mesh.materials):
        for m in mats:
            mesh.materials.append(m.original if m is not None and getattr(m, "original", None) else m)
    return mesh


def _export_mesh_glb(mesh: bpy.types.Mesh, filepath: Path, mesh_name: str,
                     scene: bpy.types.Scene, gltf_options: dict) -> dict:
    """Write ``mesh`` at identity transform to a GLB (node/mesh name ``SM_<mesh_name>``)."""
    mesh.name = f"SM_{mesh_name}"
    tmp = bpy.data.objects.new(f"SM_{mesh_name}", mesh)
    scene.collection.objects.link(tmp)
    view_layer = bpy.context.view_layer
    prev_sel = [o for o in view_layer.objects if o.select_get()]
    prev_active = view_layer.objects.active
    try:
        for o in prev_sel:
            o.select_set(False)
        tmp.select_set(True)
        view_layer.objects.active = tmp
        kw = dict(filepath=str(filepath), export_format="GLB", use_selection=True,
                  export_apply=True, export_yup=True, export_cameras=False,
                  export_lights=False, export_animations=False, export_extras=True,
                  export_materials="EXPORT")
        kw.update(gltf_options)
        props = {p.identifier for p in bpy.ops.export_scene.gltf.get_rna_type().properties}
        res = bpy.ops.export_scene.gltf(**{k: v for k, v in kw.items() if k in props})
        if "FINISHED" not in res:
            raise RuntimeError(f"glTF export failed for {mesh_name}: {res}")
        lo = Vector((math.inf,) * 3)
        hi = Vector((-math.inf,) * 3)
        for v in mesh.vertices:
            lo = Vector(map(min, lo, v.co))
            hi = Vector(map(max, hi, v.co))
        info = {"vertices": len(mesh.vertices), "polygons": len(mesh.polygons),
                "materials": [m.name for m in mesh.materials if m],
                "bounds_m": [_vec(lo), _vec(hi)] if len(mesh.vertices) else None}
    finally:
        bpy.data.objects.remove(tmp, do_unlink=True)
        for o in prev_sel:
            try:
                o.select_set(True)
            except (ReferenceError, RuntimeError):
                pass
        view_layer.objects.active = prev_active
    return info


def _light_record(ob: bpy.types.Object) -> dict:
    L = ob.data
    t = {"POINT": "point", "SPOT": "spot", "AREA": "rect", "SUN": "sun"}.get(L.type, L.type.lower())
    rec = {
        "name": ob.name,
        "type": t,
        "location": _vec(ob.matrix_world.to_translation()),
        "rotation": _vec(ob.matrix_world.to_euler("XYZ")),
        "direction": _vec((ob.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()),
        "color": _vec(L.color),
        "energy_w": float(L.energy),
        "exposure": float(getattr(L, "exposure", 0.0) or 0.0),
        "radius": float(getattr(L, "shadow_soft_size", 0.0)),
        "cast_shadows": bool(getattr(L, "use_shadow", True)),
        "ue_intensity_hint": blender_light_to_ue_intensity(L),
    }
    if L.type == "SPOT":
        rec["spot_angle"] = math.degrees(L.spot_size)  # full cone angle, degrees
        rec["spot_blend"] = float(L.spot_blend)
        # UE: OuterConeAngle is the half angle; inner ~ outer * (1 - blend)
        rec["ue_outer_cone_deg"] = math.degrees(L.spot_size) / 2.0
        rec["ue_inner_cone_deg"] = math.degrees(L.spot_size) / 2.0 * (1.0 - float(L.spot_blend))
    if L.type == "AREA":
        rec["shape"] = L.shape
        rec["size"] = float(L.size)
        rec["size_y"] = float(L.size_y) if L.shape in ("RECTANGLE", "ELLIPSE") else float(L.size)
        rec["ue_source_width_cm"] = rec["size"] * 100.0 * ob.matrix_world.to_scale().x
        rec["ue_source_height_cm"] = rec["size_y"] * 100.0 * ob.matrix_world.to_scale().y
    if L.type == "SUN":
        rec["angle_deg"] = math.degrees(getattr(L, "angle", 0.0))
    rec["ue"] = _transform_record(ob.matrix_world, forward_axis_fix=True)["ue"]
    rec["ue"]["radius_cm"] = rec["radius"] * 100.0
    return rec


def _camera_record(cam_ob: bpy.types.Object, scene: bpy.types.Scene) -> dict:
    cam = cam_ob.data
    dof = cam.dof
    focus = float(dof.focus_distance)
    if dof.focus_object is not None:
        focus = (dof.focus_object.matrix_world.to_translation()
                 - cam_ob.matrix_world.to_translation()).length
    r = scene.render
    rec = {
        "name": cam_ob.name,
        "location": _vec(cam_ob.matrix_world.to_translation()),
        "rotation": _vec(cam_ob.matrix_world.to_euler("XYZ")),
        "type": cam.type,
        "focal_mm": float(cam.lens),
        "sensor_mm": float(cam.sensor_width),
        "sensor_height_mm": float(cam.sensor_height),
        "sensor_fit": cam.sensor_fit,
        "fstop": float(dof.aperture_fstop),
        "use_dof": bool(dof.use_dof),
        "focus_distance": focus,
        "clip_start": float(cam.clip_start),
        "clip_end": float(cam.clip_end),
        "shift": [float(cam.shift_x), float(cam.shift_y)],
        "resolution": [int(r.resolution_x * r.resolution_percentage / 100),
                       int(r.resolution_y * r.resolution_percentage / 100)],
    }
    aspect = rec["resolution"][0] / max(1, rec["resolution"][1])
    # UE filmback: keep the sensor width, derive height from the render aspect ratio
    rec["ue"] = _transform_record(cam_ob.matrix_world, forward_axis_fix=True)["ue"]
    rec["ue"].update({"sensor_width_mm": rec["sensor_mm"],
                      "sensor_height_mm": rec["sensor_mm"] / aspect,
                      "focus_distance_cm": focus * 100.0})
    return rec


def export_for_unreal(out_dir: Path | str, scene_meta: dict | None = None, *,
                      scene: bpy.types.Scene | None = None,
                      gltf_options: dict | None = None) -> Path:
    """Export the visible scene to ``out_dir`` and return the path of ``manifest.json``.

    ``out_dir/meshes/*.glb`` (one per unique mesh) + ``out_dir/manifest.json``. ``scene_meta``
    supplies ``name``, ``atmosphere`` and ``post`` blocks (copied verbatim) plus any other
    keys, which are stored under ``"scene_meta"``.
    """
    scene_meta = dict(scene_meta or {})
    scene = scene or bpy.context.scene
    out_dir = Path(out_dir)
    mesh_dir = out_dir / "meshes"
    mesh_dir.mkdir(parents=True, exist_ok=True)
    view_layer = bpy.context.view_layer
    view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()

    # Pass 1: gather placements and copy each unique evaluated mesh into a standalone Mesh.
    # This must finish before anything touches the scene: exporting/linking objects
    # re-evaluates the depsgraph and invalidates evaluated/instance objects.
    groups: dict[tuple, dict] = {}
    for key, name, src_name, ob, mw in _collect_mesh_instances(depsgraph, view_layer):
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"name": name, "matrices": [],
                               "mesh": _copy_evaluated_mesh(ob, depsgraph,
                                                            raw_instance=key[0] == "ptr")}
        g["matrices"].append((mw, src_name))

    # Pass 2: export.
    used: set[str] = set()
    meshes, instances = [], []
    for key, g in groups.items():
        mesh_name = _safe_name(g["name"], used)
        fname = f"{mesh_name}.glb"
        mesh = g.pop("mesh")
        try:
            if not len(mesh.vertices):
                continue
            info = _export_mesh_glb(mesh, mesh_dir / fname, mesh_name, scene, gltf_options or {})
        finally:
            bpy.data.meshes.remove(mesh, do_unlink=True)
        meshes.append({"name": mesh_name, "file": f"meshes/{fname}", "instances": len(g["matrices"]),
                       **info})
        for mw, src_name in g["matrices"]:
            rec = {"mesh": mesh_name, "name": src_name}
            rec.update(_transform_record(mw))
            instances.append(rec)

    # Cycles light portals are sampling helpers, not emitters: exporting them would add real lights in UE
    lights = [_light_record(o) for o in scene.objects
              if o.type == "LIGHT" and _is_visible(o, view_layer)
              and not getattr(getattr(o.data, "cycles", None), "is_portal", False)]
    camera = _camera_record(scene.camera, scene) if scene.camera is not None else None

    meta_rest = {k: v for k, v in scene_meta.items() if k not in ("name", "atmosphere", "post")}
    manifest: dict[str, Any] = {
        "format": "envgen-unreal-manifest",
        "version": MANIFEST_VERSION,
        "scene": scene_meta.get("name") or scene.name,
        "generator": f"envgen / Blender {bpy.app.version_string}",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "units": {
            "blender": "metres, Z-up, right-handed; XYZ Euler radians",
            "unreal": "centimetres, Z-up, left-handed (X forward, Y right); Rotator degrees",
            "location": "x_ue = x*100, y_ue = -y*100, z_ue = z*100",
            "rotation": "roll = deg(rx), pitch = -deg(ry), yaw = -deg(rz) (Blender XYZ Euler)",
            "lights_and_camera": "world matrix right-multiplied by Q (local +X -> -Z, +Z -> +Y) "
                                 "before converting, because Blender lights/cameras face local -Z",
            "meshes": "GLB = glTF 2.0 (metres, +Y up); UE's glTF importer converts to cm/Z-up",
            "light_units": "point/spot/rect: lumens = W*683*2^exposure; sun: lux = W/m^2*683",
            "scale_cm_per_unit": 100.0,
        },
        "meshes": meshes,
        "instances": instances,
        "lights": lights,
        "camera": camera,
        "atmosphere": scene_meta.get("atmosphere", {}),
        "post": scene_meta.get("post", {}),
        "render": {"resolution": [scene.render.resolution_x, scene.render.resolution_y],
                   "engine": scene.render.engine,
                   "view_transform": scene.view_settings.view_transform,
                   "exposure": float(scene.view_settings.exposure),
                   # EV100 at which UE (with the 683 lm/W light conversion) shows the same
                   # brightness as Blender: UE exposure = 1/(1.2*2^EV100), Blender = 2^exposure
                   # -> EV100 = log2(683/1.2) - exposure. Used when post has no exposure_ev100.
                   "ue_ev100_equivalent": round(math.log2(LUMENS_PER_WATT / 1.2)
                                                - float(scene.view_settings.exposure), 4)},
        "scene_meta": meta_rest,
    }
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return path
