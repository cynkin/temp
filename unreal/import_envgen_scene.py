"""Import an envgen scene export (``manifest.json`` + ``meshes/*.glb``) into the open UE level.

!!! UNTESTED !!!  This script was written against the documented Unreal Engine 5.3+ editor
Python API but has NOT been run inside Unreal (no engine available where it was written).
It is syntax-checked only. Every engine call is wrapped defensively and failures are
logged and skipped rather than aborting the whole import; check the Output Log.

Usage (inside the UE 5.3+ editor, Python Editor Script Plugin enabled):

    # Output Log, "Python" console / "Cmd" with the py command:
    py "C:/path/to/unreal/import_envgen_scene.py" "C:/path/to/export/manifest.json"

    # or headless-ish from the command line:
    UnrealEditor-Cmd.exe MyProject.uproject -run=pythonscript \
        -script="C:/path/import_envgen_scene.py C:/path/manifest.json"
    UnrealEditor.exe MyProject.uproject -ExecutePythonScript="C:/path/import_envgen_scene.py C:/path/manifest.json"

    # or set ENVGEN_MANIFEST=/path/to/manifest.json and run the script without arguments.

Options (after the manifest path): ``--dest /Game/EnvGen`` (content root),
``--ism-threshold N`` (meshes with >= N placements become one actor with an
InstancedStaticMeshComponent; default 16, 0 disables), ``--no-replace`` (keep existing assets),
``--light-scale F`` (multiply all light intensities), ``--save`` (save assets and level).

What it does:
1. Imports each GLB with ``unreal.AssetImportTask`` (automated, replace_existing) into
   ``/Game/EnvGen/<scene>/<mesh>/`` (one folder per GLB so asset names can't collide).
2. Removes actors from a previous import of the same scene (actor tag ``envgen:<scene>``),
   then spawns StaticMeshActors (or ISM actors) using the manifest's pre-converted ``ue``
   transforms (cm, left-handed; see ``envgen/export/unreal.py`` for the maths).
3. Spawns Point/Spot/Rect/Directional lights with intensity units (lumens / lux).
4. Spawns ExponentialHeightFog with volumetric fog, an unbound PostProcessVolume (manual
   exposure via physical-camera EV100, Lumen GI + reflections) and a CineCameraActor with
   focal length, filmback, aperture and manual focus.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

try:
    import unreal  # type: ignore[import-not-found]
except ImportError:  # allows `python -m py_compile` / import outside the editor
    unreal = None  # type: ignore[assignment]

LOG_PREFIX = "[envgen]"


# ----------------------------------------------------------------------------- logging

def log(msg: str) -> None:
    if unreal is not None:
        unreal.log(f"{LOG_PREFIX} {msg}")
    else:
        print(f"{LOG_PREFIX} {msg}")


def warn(msg: str) -> None:
    if unreal is not None:
        unreal.log_warning(f"{LOG_PREFIX} {msg}")
    else:
        print(f"{LOG_PREFIX} WARNING {msg}")


# ----------------------------------------------------------------------------- math fallback
# Used only if a manifest record lacks the pre-converted "ue" block.

def _euler_xyz_to_matrix(rx: float, ry: float, rz: float):
    cx, sx, cy, sy, cz, sz = (math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry),
                              math.cos(rz), math.sin(rz))
    # R = Rz @ Ry @ Rx  (Blender XYZ Euler)
    return [[cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
            [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
            [-sy, cy * sx, cy * cx]]


def _matmul3(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _matrix_to_euler_xyz(m):
    sy = -m[2][0]
    sy = max(-1.0, min(1.0, sy))
    ry = math.asin(sy)
    if abs(sy) < 0.999999:
        rx = math.atan2(m[2][1], m[2][2])
        rz = math.atan2(m[1][0], m[0][0])
    else:
        rx = math.atan2(-m[1][2], m[1][1])
        rz = 0.0
    return rx, ry, rz


_Q = [[0.0, -1.0, 0.0], [0.0, 0.0, 1.0], [-1.0, 0.0, 0.0]]  # X-forward/Z-up -> -Z fwd/+Y up


def blender_to_ue(location, rotation_euler, forward_fix: bool = False):
    """(Vector cm, Rotator) from Blender metres + XYZ Euler radians (see exporter docs)."""
    x, y, z = location
    loc = (x * 100.0, -y * 100.0, z * 100.0)
    m = _euler_xyz_to_matrix(*rotation_euler)
    if forward_fix:
        m = _matmul3(m, _Q)
    rx, ry, rz = _matrix_to_euler_xyz(m)
    return loc, {"roll": math.degrees(rx), "pitch": -math.degrees(ry), "yaw": -math.degrees(rz)}


def _ue_block(rec: dict, forward_fix: bool = False) -> dict:
    ue = rec.get("ue")
    if isinstance(ue, dict) and "location_cm" in ue and "rotation" in ue:
        return ue
    loc, rot = blender_to_ue(rec.get("location", (0, 0, 0)),
                             rec.get("rotation_euler", rec.get("rotation", (0, 0, 0))),
                             forward_fix)
    return {"location_cm": list(loc), "rotation": rot, "scale": rec.get("scale", [1, 1, 1])}


# ----------------------------------------------------------------------------- unreal helpers

def _vec(v):
    return unreal.Vector(float(v[0]), float(v[1]), float(v[2]))


def _rot(r: dict):
    return unreal.Rotator(roll=float(r.get("roll", 0.0)), pitch=float(r.get("pitch", 0.0)),
                          yaw=float(r.get("yaw", 0.0)))


def _color(c):
    c = list(c) + [1.0] * (4 - len(c))
    return unreal.LinearColor(float(c[0]), float(c[1]), float(c[2]), float(c[3]))


def _set(obj, names, value) -> bool:
    """set_editor_property trying several property names (API drift between UE versions)."""
    if obj is None:
        return False
    for n in ([names] if isinstance(names, str) else names):
        try:
            obj.set_editor_property(n, value)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _enum(enum_cls_name: str, *members):
    cls = getattr(unreal, enum_cls_name, None)
    if cls is None:
        return None
    for m in members:
        if hasattr(cls, m):
            return getattr(cls, m)
    return None


def _actor_subsystem():
    try:
        return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    except Exception:  # noqa: BLE001
        return None


def spawn(actor_class, location=(0, 0, 0), rotation=None):
    loc = _vec(location)
    rot = rotation if rotation is not None else unreal.Rotator(0.0, 0.0, 0.0)
    subsys = _actor_subsystem()
    if subsys is not None:
        return subsys.spawn_actor_from_class(actor_class, loc, rot)
    return unreal.EditorLevelLibrary.spawn_actor_from_class(actor_class, loc, rot)


def _all_level_actors():
    subsys = _actor_subsystem()
    if subsys is not None:
        return subsys.get_all_level_actors()
    return unreal.EditorLevelLibrary.get_all_level_actors()


def _destroy(actor) -> None:
    subsys = _actor_subsystem()
    if subsys is not None:
        subsys.destroy_actor(actor)
    else:
        unreal.EditorLevelLibrary.destroy_actor(actor)


def _tag_actor(actor, scene_tag: str, label: str, folder: str) -> None:
    try:
        actor.set_actor_label(label)
    except Exception:  # noqa: BLE001
        pass
    try:
        actor.set_folder_path(folder)
    except Exception:  # noqa: BLE001
        pass
    try:
        tags = list(actor.get_editor_property("tags"))
        tags.append(unreal.Name(scene_tag))
        actor.set_editor_property("tags", tags)
    except Exception:  # noqa: BLE001
        pass


def _component(actor, prop_names, cls_name):
    for n in prop_names:
        try:
            c = actor.get_editor_property(n)
            if c is not None:
                return c
        except Exception:  # noqa: BLE001
            continue
    cls = getattr(unreal, cls_name, None)
    if cls is not None:
        try:
            return actor.get_component_by_class(cls)
        except Exception:  # noqa: BLE001
            pass
    return None


# ----------------------------------------------------------------------------- import

def import_glb(file_path: Path, dest_path: str, replace: bool = True):
    """Import one GLB; return the first StaticMesh asset (or None)."""
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", str(file_path))
    task.set_editor_property("destination_path", dest_path)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_existing", replace)
    task.set_editor_property("save", False)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

    paths = []
    try:
        paths = list(task.get_editor_property("imported_object_paths") or [])
    except Exception:  # noqa: BLE001
        pass
    if not paths:
        try:
            paths = [o.get_path_name() for o in (task.get_objects() or [])]
        except Exception:  # noqa: BLE001
            paths = []
    if not paths:  # Interchange may not fill imported_object_paths: list the folder instead
        try:
            paths = list(unreal.EditorAssetLibrary.list_assets(dest_path, recursive=True))
        except Exception:  # noqa: BLE001
            paths = []
    for p in paths:
        try:
            asset = unreal.EditorAssetLibrary.load_asset(p)
        except Exception:  # noqa: BLE001
            asset = None
        if asset is not None and isinstance(asset, unreal.StaticMesh):
            return asset
    return None


def _spawn_ism(mesh, insts, scene_tag, folder, label):
    """One actor with an InstancedStaticMeshComponent holding all placements, or None."""
    try:
        actor = spawn(unreal.Actor, (0, 0, 0))
        sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
        handles = sds.k2_gather_subobject_data_for_instance(actor)
        params = unreal.AddNewSubobjectParams(parent_handle=handles[0],
                                              new_class=unreal.InstancedStaticMeshComponent,
                                              blueprint_context=None)
        result = sds.add_new_subobject(params)
        handle = result[0] if isinstance(result, (tuple, list)) else result
        data = unreal.SubobjectDataBlueprintFunctionLibrary.get_data(handle)
        comp = unreal.SubobjectDataBlueprintFunctionLibrary.get_object(data)
        if comp is None:
            raise RuntimeError("could not create InstancedStaticMeshComponent")
        comp.set_static_mesh(mesh)
        for rec in insts:
            ue = _ue_block(rec)
            xf = unreal.Transform(_vec(ue["location_cm"]), _rot(ue["rotation"]),
                                  _vec(ue.get("scale", [1, 1, 1])))
            comp.add_instance(xf, True)
        _tag_actor(actor, scene_tag, label, folder)
        return actor
    except Exception as e:  # noqa: BLE001
        warn(f"ISM creation failed for {label} ({e}); falling back to StaticMeshActors")
        try:
            _destroy(actor)  # type: ignore[possibly-undefined]
        except Exception:  # noqa: BLE001
            pass
        return None


def spawn_meshes(man: dict, root: Path, dest: str, scene_tag: str, folder: str,
                 replace: bool, ism_threshold: int) -> int:
    by_mesh: dict[str, list] = {}
    for inst in man.get("instances", []):
        by_mesh.setdefault(inst["mesh"], []).append(inst)
    count = 0
    for m in man.get("meshes", []):
        glb = root / m["file"]
        if not glb.is_file():
            warn(f"missing GLB {glb}")
            continue
        try:
            asset = import_glb(glb, f"{dest}/{m['name']}", replace)
        except Exception as e:  # noqa: BLE001
            warn(f"import failed for {glb.name}: {e}")
            continue
        if asset is None:
            warn(f"no StaticMesh produced by {glb.name}")
            continue
        insts = by_mesh.get(m["name"], [])
        if ism_threshold and len(insts) >= ism_threshold:
            if _spawn_ism(asset, insts, scene_tag, f"{folder}/Instanced", f"ISM_{m['name']}"):
                count += len(insts)
                continue
        for rec in insts:
            try:
                ue = _ue_block(rec)
                actor = spawn(unreal.StaticMeshActor, ue["location_cm"], _rot(ue["rotation"]))
                actor.set_actor_scale3d(_vec(ue.get("scale", [1, 1, 1])))
                smc = _component(actor, ["static_mesh_component"], "StaticMeshComponent")
                smc.set_static_mesh(asset)
                _tag_actor(actor, scene_tag, rec.get("name", m["name"]), f"{folder}/Meshes")
                count += 1
            except Exception as e:  # noqa: BLE001
                warn(f"spawn failed for {rec.get('name')}: {e}")
    return count


# ----------------------------------------------------------------------------- lights

_LIGHT_CLASSES = {"point": "PointLight", "spot": "SpotLight", "rect": "RectLight",
                  "area": "RectLight", "sun": "DirectionalLight"}


def spawn_lights(man: dict, scene_tag: str, folder: str, light_scale: float) -> int:
    n = 0
    lumens = _enum("LightUnits", "LUMENS", "Lumens")
    candelas = _enum("LightUnits", "CANDELAS", "Candelas")
    for L in man.get("lights", []):
        cls = getattr(unreal, _LIGHT_CLASSES.get(L.get("type"), ""), None)
        if cls is None:
            warn(f"unsupported light type {L.get('type')}")
            continue
        try:
            ue = _ue_block(L, forward_fix=True)
            actor = spawn(cls, ue["location_cm"], _rot(ue["rotation"]))
            comp = _component(actor, ["light_component", "point_light_component",
                                      "spot_light_component", "rect_light_component",
                                      "directional_light_component"], "LightComponent")
            hint = L.get("ue_intensity_hint", {})
            value = float(hint.get("value", L.get("energy_w", 1.0))) * light_scale
            if L["type"] != "sun":
                units = lumens if hint.get("units") == "lumens" else (
                    candelas if hint.get("units") == "candela" else lumens)
                if units is not None:
                    _set(comp, "intensity_units", units)
            comp.set_intensity(value)
            comp.set_light_color(_color(L.get("color", (1, 1, 1))))
            try:
                comp.set_cast_shadows(bool(L.get("cast_shadows", True)))
            except Exception:  # noqa: BLE001
                pass
            radius_cm = float(L.get("radius", 0.0)) * 100.0
            if L["type"] in ("point", "spot"):
                _set(comp, "source_radius", radius_cm)
                _set(comp, "use_inverse_squared_falloff", True)
            if L["type"] == "spot":
                outer = float(L.get("ue_outer_cone_deg", L.get("spot_angle", 45.0) / 2.0))
                inner = float(L.get("ue_inner_cone_deg", outer * 0.8))
                try:
                    comp.set_outer_cone_angle(outer)
                    comp.set_inner_cone_angle(inner)
                except Exception:  # noqa: BLE001
                    _set(comp, "outer_cone_angle", outer)
                    _set(comp, "inner_cone_angle", inner)
            if L["type"] in ("rect", "area"):
                w = float(L.get("ue_source_width_cm", L.get("size", 1.0) * 100.0))
                h = float(L.get("ue_source_height_cm", L.get("size_y", L.get("size", 1.0)) * 100.0))
                try:
                    comp.set_source_width(w)
                    comp.set_source_height(h)
                except Exception:  # noqa: BLE001
                    _set(comp, "source_width", w)
                    _set(comp, "source_height", h)
            if L["type"] == "sun":
                _set(comp, ["atmosphere_sun_light", "used_as_atmosphere_sun_light"], True)
                if "angle_deg" in L:
                    _set(comp, "light_source_angle", float(L["angle_deg"]))
            _tag_actor(actor, scene_tag, L.get("name", L["type"]), f"{folder}/Lights")
            n += 1
        except Exception as e:  # noqa: BLE001
            warn(f"light {L.get('name')} failed: {e}")
    return n


# ----------------------------------------------------------------------------- atmosphere/post

def _first(d: dict, keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def spawn_fog(man: dict, scene_tag: str, folder: str):
    atm = man.get("atmosphere") or {}
    try:
        actor = spawn(unreal.ExponentialHeightFog, (0, 0, 0))
        comp = _component(actor, ["component"], "ExponentialHeightFogComponent")
        # NOTE: envgen's Blender volume density (1/m) is not the same quantity as UE's
        # unitless FogDensity; "ue_fog_density" in the manifest wins when present.
        _set(comp, "fog_density", float(_first(atm, ["ue_fog_density", "fog_density", "density"], 0.02)))
        _set(comp, "fog_height_falloff", float(_first(atm, ["ue_height_falloff", "height_falloff"], 0.2)))
        col = _first(atm, ["fog_color", "color", "inscattering_color"])
        if col is not None:
            _set(comp, ["fog_inscattering_luminance", "fog_inscattering_color"], _color(col))
        start = _first(atm, ["start_distance_m"])
        if start is not None:
            _set(comp, "start_distance", float(start) * 100.0)
        _set(comp, "enable_volumetric_fog", bool(_first(atm, ["volumetric", "volumetric_fog"], True)))
        g = _first(atm, ["anisotropy", "scattering_distribution", "volumetric_anisotropy"])
        if g is not None:
            _set(comp, "volumetric_fog_scattering_distribution", float(g))
        alb = _first(atm, ["albedo", "volumetric_albedo"])
        if alb is not None:
            if isinstance(alb, (int, float)):
                alb = (alb, alb, alb)
            a8 = [int(max(0, min(255, round(float(c) * 255)))) for c in list(alb)[:3]]
            _set(comp, "volumetric_fog_albedo", unreal.Color(r=a8[0], g=a8[1], b=a8[2], a=255))
        ext = _first(atm, ["extinction_scale", "volumetric_extinction_scale"])
        if ext is not None:
            _set(comp, "volumetric_fog_extinction_scale", float(ext))
        _tag_actor(actor, scene_tag, "EnvGen_HeightFog", f"{folder}/Atmosphere")
        return actor
    except Exception as e:  # noqa: BLE001
        warn(f"fog failed: {e}")
        return None


def spawn_post(man: dict, scene_tag: str, folder: str):
    post = man.get("post") or {}
    cam = man.get("camera") or {}
    render = man.get("render") or {}
    try:
        actor = spawn(unreal.PostProcessVolume, (0, 0, 0))
        _set(actor, "unbound", True)
        _set(actor, "priority", 10.0)
        s = actor.get_editor_property("settings")

        def ov(name, value):
            _set(s, f"override_{name}", True)
            _set(s, name, value)

        # Lumen GI + reflections
        gi = _enum("DynamicGlobalIlluminationMethod", "LUMEN")
        if gi is not None:
            ov("dynamic_global_illumination_method", gi)
        refl = _enum("ReflectionMethod", "LUMEN")
        if refl is not None:
            ov("reflection_method", refl)

        # Manual exposure through the physical camera EV100 formula:
        #   EV100 = log2(N^2 / t)  (ISO 100)  ->  t = N^2 / 2^EV100
        ev100 = _first(post, ["exposure_ev100", "ev100"], render.get("ue_ev100_equivalent"))
        manual = _enum("AutoExposureMethod", "AEM_MANUAL", "MANUAL")
        if manual is not None:
            ov("auto_exposure_method", manual)
        if ev100 is not None:
            fstop = float(cam.get("fstop", 4.0) or 4.0)
            shutter_s = fstop * fstop / (2.0 ** float(ev100))
            ov("auto_exposure_apply_physical_camera_exposure", True)
            ov("camera_iso", 100.0)
            ov("camera_shutter_speed", 1.0 / max(shutter_s, 1e-6))  # UE stores 1/seconds
            ov("depth_of_field_fstop", fstop)
            ov("auto_exposure_bias", float(post.get("exposure_compensation", 0.0)))
        elif "exposure_compensation" in post:
            ov("auto_exposure_apply_physical_camera_exposure", False)
            ov("auto_exposure_bias", float(post["exposure_compensation"]))

        simple = {"bloom": "bloom_intensity", "bloom_intensity": "bloom_intensity",
                  "vignette": "vignette_intensity", "vignette_intensity": "vignette_intensity",
                  "film_grain": "film_grain_intensity", "chromatic_aberration": "scene_fringe_intensity",
                  "white_balance_k": "white_temp"}
        for k, ue_name in simple.items():
            if isinstance(post.get(k), (int, float)):
                ov(ue_name, float(post[k]))
        actor.set_editor_property("settings", s)
        _tag_actor(actor, scene_tag, "EnvGen_PostProcess", f"{folder}/Atmosphere")
        return actor
    except Exception as e:  # noqa: BLE001
        warn(f"post process volume failed: {e}")
        return None


# ----------------------------------------------------------------------------- camera

def spawn_camera(man: dict, scene_tag: str, folder: str):
    cam = man.get("camera")
    if not cam:
        return None
    try:
        ue = _ue_block(cam, forward_fix=True)
        actor = spawn(unreal.CineCameraActor, ue["location_cm"], _rot(ue["rotation"]))
        comp = actor.get_cine_camera_component()
        sw = float(ue.get("sensor_width_mm", cam.get("sensor_mm", 36.0)))
        sh = float(ue.get("sensor_height_mm", sw * 9.0 / 16.0))
        fb = comp.get_editor_property("filmback")
        _set(fb, "sensor_width", sw)
        _set(fb, "sensor_height", sh)
        _set(comp, "filmback", fb)
        focal = float(cam.get("focal_mm", 35.0))
        fstop = float(cam.get("fstop", 4.0))
        lens = comp.get_editor_property("lens_settings")
        if focal < lens.get_editor_property("min_focal_length"):
            _set(lens, "min_focal_length", focal)
        if focal > lens.get_editor_property("max_focal_length"):
            _set(lens, "max_focal_length", focal)
        if fstop < lens.get_editor_property("min_f_stop"):
            _set(lens, "min_f_stop", fstop)
        if fstop > lens.get_editor_property("max_f_stop"):
            _set(lens, "max_f_stop", fstop)
        _set(comp, "lens_settings", lens)
        _set(comp, "current_focal_length", focal)
        _set(comp, "current_aperture", fstop)
        fs = comp.get_editor_property("focus_settings")
        if cam.get("use_dof", True):
            m = _enum("CameraFocusMethod", "MANUAL")
            if m is not None:
                _set(fs, "focus_method", m)
            _set(fs, "manual_focus_distance",
                 float(ue.get("focus_distance_cm", float(cam.get("focus_distance", 10.0)) * 100.0)))
        else:
            m = _enum("CameraFocusMethod", "DISABLE", "DO_NOT_OVERRIDE")
            if m is not None:
                _set(fs, "focus_method", m)
        _set(comp, "focus_settings", fs)
        clip = cam.get("clip_start")
        if clip:
            _set(comp, "custom_near_clipping_plane", float(clip) * 100.0)
            _set(comp, "override_custom_near_clipping_plane", True)
        _tag_actor(actor, scene_tag, cam.get("name", "EnvGen_Camera"), folder)
        try:  # move the editor viewport to the shot (best effort)
            ues = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
            ues.set_level_viewport_camera_info(_vec(ue["location_cm"]), _rot(ue["rotation"]))
        except Exception:  # noqa: BLE001
            pass
        return actor
    except Exception as e:  # noqa: BLE001
        warn(f"camera failed: {e}")
        return None


# ----------------------------------------------------------------------------- main

def _parse_args(argv):
    opts = {"manifest": os.environ.get("ENVGEN_MANIFEST"), "dest": "/Game/EnvGen",
            "ism_threshold": 16, "replace": True, "light_scale": 1.0, "save": False}
    it = iter(argv)
    for a in it:
        if a == "--dest":
            opts["dest"] = next(it)
        elif a == "--ism-threshold":
            opts["ism_threshold"] = int(next(it))
        elif a == "--no-replace":
            opts["replace"] = False
        elif a == "--light-scale":
            opts["light_scale"] = float(next(it))
        elif a == "--save":
            opts["save"] = True
        elif not a.startswith("--"):
            opts["manifest"] = a
    return opts


def run(manifest_path: str | os.PathLike, dest: str = "/Game/EnvGen", ism_threshold: int = 16,
        replace: bool = True, light_scale: float = 1.0, save: bool = False) -> dict:
    if unreal is None:
        raise RuntimeError("this script must run inside the Unreal Editor (module 'unreal')")
    mpath = Path(manifest_path)
    man = json.loads(mpath.read_text(encoding="utf-8"))
    if man.get("format") != "envgen-unreal-manifest":
        warn("manifest 'format' field missing/unknown; continuing anyway")
    scene = "".join(c if c.isalnum() or c == "_" else "_" for c in str(man.get("scene", "scene")))
    dest_root = f"{dest.rstrip('/')}/{scene}"
    scene_tag = f"envgen:{scene}"
    folder = f"EnvGen/{scene}"

    # Remove actors from a previous import of this scene (idempotent re-runs).
    removed = 0
    for a in list(_all_level_actors()):
        try:
            if unreal.Name(scene_tag) in list(a.get_editor_property("tags")):
                _destroy(a)
                removed += 1
        except Exception:  # noqa: BLE001
            continue
    if removed:
        log(f"removed {removed} actors from a previous import")

    stats = {"removed": removed}
    with unreal.ScopedSlowTask(5, f"Importing envgen scene {scene}") as task:
        task.make_dialog(True)
        task.enter_progress_frame(1, "meshes")
        stats["mesh_actors"] = spawn_meshes(man, mpath.parent, dest_root, scene_tag, folder,
                                            replace, ism_threshold)
        task.enter_progress_frame(1, "lights")
        stats["lights"] = spawn_lights(man, scene_tag, folder, light_scale)
        task.enter_progress_frame(1, "fog")
        stats["fog"] = spawn_fog(man, scene_tag, folder) is not None
        task.enter_progress_frame(1, "post process")
        stats["post"] = spawn_post(man, scene_tag, folder) is not None
        task.enter_progress_frame(1, "camera")
        stats["camera"] = spawn_camera(man, scene_tag, folder) is not None

    if save:
        try:
            unreal.EditorAssetLibrary.save_directory(dest_root, only_if_is_dirty=True, recursive=True)
            unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level()
        except Exception as e:  # noqa: BLE001
            warn(f"save failed: {e}")
    log(f"done: {stats}")
    return stats


def main(argv=None) -> int:
    opts = _parse_args(sys.argv[1:] if argv is None else argv)
    if not opts["manifest"]:
        warn("usage: import_envgen_scene.py <manifest.json> [--dest /Game/EnvGen] "
             "[--ism-threshold N] [--no-replace] [--light-scale F] [--save] (or ENVGEN_MANIFEST)")
        return 2
    run(opts.pop("manifest"), **opts)
    return 0


if __name__ == "__main__":
    main()
