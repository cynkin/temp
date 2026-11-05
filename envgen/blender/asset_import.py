"""Import library assets into the current bpy scene and build PBR materials from texture sets.

Works headless with Blender-as-module (bpy 5.2). Supported model formats and the operator
used for each (availability is checked at call time; the FBX/glTF importers are bundled
add-ons and are enabled on demand through ``addon_utils``):

=========== =========================================== =======================
extension   importer                                    notes
=========== =========================================== =======================
.blend      ``bpy.data.libraries.load`` (append)        all objects, or one named collection
.glb/.gltf  ``bpy.ops.import_scene.gltf``               add-on ``io_scene_gltf2``
.fbx        ``bpy.ops.import_scene.fbx``                add-on ``io_scene_fbx``
.obj        ``bpy.ops.wm.obj_import``                   built-in (C++)
.usd*       ``bpy.ops.wm.usd_import``                   built-in
=========== =========================================== =======================

``import_model`` normalises every import: new objects are moved into the target collection,
optionally scaled (uniformly) to a target real-world size and re-pivoted so the asset's
**bottom-centre** sits at its origin -- placing it is then just setting ``location``.

``build_pbr_material`` builds a Principled BSDF material from ``{map_type: path}`` (as produced
by ``envgen.assets.registry.Asset.texture_set()``) using **box-projected** image textures in
object space, so it works on procedural geometry without UVs.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import bpy
from mathutils import Matrix, Vector

__all__ = [
    "SUPPORTED_MODEL_EXTS",
    "importer_available",
    "import_model",
    "import_asset",
    "build_pbr_material",
    "build_material_from_asset",
    "set_world_hdri",
    "world_bounds",
]

SUPPORTED_MODEL_EXTS = (".blend", ".glb", ".gltf", ".fbx", ".obj", ".usd", ".usda", ".usdc", ".usdz")

_ADDON_FOR_OP = {("import_scene", "gltf"): "io_scene_gltf2",
                 ("import_scene", "fbx"): "io_scene_fbx",
                 ("export_scene", "gltf"): "io_scene_gltf2",
                 ("export_scene", "fbx"): "io_scene_fbx"}

_DATA_MAPS = {"roughness", "metallic", "normal_gl", "normal_dx", "displacement", "ao", "arm",
              "opacity"}


# ----------------------------------------------------------------------------- operators

def _op_exists(module: str, name: str) -> bool:
    try:
        getattr(getattr(bpy.ops, module), name).get_rna_type()
        return True
    except (KeyError, AttributeError, RuntimeError):
        return False


def importer_available(module: str, name: str, enable: bool = True) -> bool:
    """True if ``bpy.ops.<module>.<name>`` exists, enabling its bundled add-on if needed."""
    if _op_exists(module, name):
        return True
    addon = _ADDON_FOR_OP.get((module, name))
    if enable and addon:
        import addon_utils

        try:
            addon_utils.enable(addon, default_set=True, persistent=True)
        except Exception:  # noqa: BLE001 - add-on registration errors are reported below
            pass
    return _op_exists(module, name)


def _require(module: str, name: str) -> None:
    if not importer_available(module, name):
        raise RuntimeError(f"bpy.ops.{module}.{name} is not available in this Blender build")


def _op_kwargs(module: str, name: str, wanted: Mapping) -> dict:
    """Drop keyword arguments the operator doesn't know (API drift between versions)."""
    props = {p.identifier for p in getattr(getattr(bpy.ops, module), name).get_rna_type().properties}
    return {k: v for k, v in wanted.items() if k in props}


# ----------------------------------------------------------------------------- helpers

def _target_collection(collection) -> bpy.types.Collection:
    scene = bpy.context.scene
    if collection is None:
        return scene.collection
    if isinstance(collection, bpy.types.Collection):
        return collection
    coll = bpy.data.collections.get(str(collection))
    if coll is None:
        coll = bpy.data.collections.new(str(collection))
        scene.collection.children.link(coll)
    return coll


def _move_to_collection(objs: Iterable[bpy.types.Object], coll: bpy.types.Collection) -> None:
    for ob in objs:
        for c in list(ob.users_collection):
            if c != coll:
                c.objects.unlink(ob)
        if coll not in ob.users_collection:
            coll.objects.link(ob)


def world_bounds(objs: Iterable[bpy.types.Object]) -> tuple[Vector, Vector] | None:
    """World-space AABB (min, max) of the geometry of ``objs`` (empties etc. ignored)."""
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    found = False
    for ob in objs:
        if ob.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD",
                           "VOLUME", "GREASEPENCIL"}:
            continue
        mw = ob.matrix_world
        for corner in ob.bound_box:
            v = mw @ Vector(corner)
            lo = Vector(map(min, lo, v))
            hi = Vector(map(max, hi, v))
            found = True
    return (lo, hi) if found else None


def _descendants(roots: Iterable[bpy.types.Object]) -> list[bpy.types.Object]:
    out = []
    for r in roots:
        out.append(r)
        out.extend(r.children_recursive)
    return out


def _import_blend(path: Path, blend_collection: str | None) -> list[bpy.types.Object]:
    with bpy.data.libraries.load(str(path), link=False) as (data_from, data_to):
        if blend_collection:
            if blend_collection not in data_from.collections:
                raise KeyError(f"collection {blend_collection!r} not in {path.name}: "
                               f"{list(data_from.collections)}")
            data_to.collections = [blend_collection]
        else:
            data_to.objects = list(data_from.objects)
    objs: list[bpy.types.Object] = []
    if blend_collection:
        for coll in data_to.collections:
            if coll is not None:
                objs.extend(coll.all_objects)
                # The appended collection only lives in bpy.data; objects are relinked by caller.
    else:
        objs = [o for o in data_to.objects if o is not None]
    return objs


# ----------------------------------------------------------------------------- import_model

def import_model(path: str | Path, collection: bpy.types.Collection | str | None = None,
                 target_dims_m: Sequence[float] | None = None, *,
                 location: Sequence[float] = (0.0, 0.0, 0.0),
                 blend_collection: str | None = None,
                 fit: str = "largest",
                 recenter: bool = True,
                 name: str | None = None) -> list[bpy.types.Object]:
    """Import a model file and return its root object(s).

    * New objects are moved into ``collection`` (a Collection, a name -- created under the
      scene collection if missing -- or None for the scene collection).
    * ``target_dims_m=[x, y, z]`` (metres, 0/None = unconstrained) scales the asset uniformly.
      ``fit="largest"`` (default) uses the ratio along the asset's largest constrained axis;
      ``fit="contain"`` uses the smallest ratio so the asset fits inside the box.
    * ``recenter`` puts the bottom-centre of the asset's world AABB at ``location``. A single
      parent-less mesh is baked (its mesh data is transformed so the object origin *is* the
      bottom-centre, object transform = translation only); anything else (hierarchies,
      several roots) is parented under a new Empty at the bottom-centre, which is then the
      single returned root.

    Each root gets custom props ``envgen_asset_path`` and ``envgen_import_scale``.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    ext = path.suffix.lower()
    if ext not in SUPPORTED_MODEL_EXTS:
        raise ValueError(f"unsupported model format {ext!r}")
    coll = _target_collection(collection)
    before = set(bpy.data.objects)

    if ext == ".blend":
        new_objs = _import_blend(path, blend_collection)
    else:
        if ext in (".glb", ".gltf"):
            _require("import_scene", "gltf")
            kw = _op_kwargs("import_scene", "gltf", {"filepath": str(path),
                                                     "import_scene_as_collection": False})
            bpy.ops.import_scene.gltf(**kw)
        elif ext == ".fbx":
            _require("import_scene", "fbx")
            bpy.ops.import_scene.fbx(filepath=str(path))
        elif ext == ".obj":
            _require("wm", "obj_import")
            bpy.ops.wm.obj_import(filepath=str(path))
        else:  # USD
            _require("wm", "usd_import")
            bpy.ops.wm.usd_import(filepath=str(path))
        new_objs = [o for o in bpy.data.objects if o not in before]

    if not new_objs:
        raise RuntimeError(f"importing {path} produced no objects")
    new_set = set(new_objs)
    _move_to_collection(new_objs, coll)
    bpy.context.view_layer.update()
    roots = [o for o in new_objs if o.parent is None or o.parent not in new_set]

    bounds = world_bounds(new_objs)
    scale = 1.0
    if bounds is not None and target_dims_m:
        lo, hi = bounds
        dims = hi - lo
        ratios = [(float(t) / dims[i], dims[i]) for i, t in enumerate(target_dims_m[:3])
                  if t and dims[i] > 1e-9]
        if ratios:
            if fit == "contain":
                scale = min(r for r, _ in ratios)
            else:
                scale = max(ratios, key=lambda r: r[1])[0]
    loc = Vector(location)

    if recenter and bounds is not None:
        lo, hi = bounds
        pivot = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
        to_local = Matrix.Diagonal((scale, scale, scale, 1.0)) @ Matrix.Translation(-pivot)
        single = (len(roots) == 1 and roots[0].type == "MESH" and not roots[0].children)
        if single:
            ob = roots[0]
            if ob.data.users > 1:
                ob.data = ob.data.copy()
            ob.data.transform(to_local @ ob.matrix_world)
            ob.data.update()
            ob.matrix_world = Matrix.Translation(loc)
        else:
            empty = bpy.data.objects.new(name or f"{path.stem}_root", None)
            empty.empty_display_type = "PLAIN_AXES"
            empty.empty_display_size = max(0.1, 0.25 * max((hi - lo) * scale))
            coll.objects.link(empty)
            empty.matrix_world = Matrix.Translation(loc)
            for r in roots:
                mw = Matrix.Translation(loc) @ to_local @ r.matrix_world
                r.parent = empty
                r.matrix_parent_inverse = Matrix.Identity(4)
                r.matrix_world = mw
            roots = [empty]
    elif scale != 1.0:
        for r in roots:
            r.matrix_world = Matrix.Diagonal((scale, scale, scale, 1.0)) @ r.matrix_world

    if name and len(roots) == 1 and roots[0].name != name:
        roots[0].name = name
    for r in roots:
        r["envgen_asset_path"] = str(path)
        r["envgen_import_scale"] = float(scale)
    bpy.context.view_layer.update()
    return roots


def import_asset(asset, collection=None, target_dims_m=None, **kw) -> list[bpy.types.Object]:
    """``import_model`` for an ``envgen.assets.registry.Asset`` (kind ``model``)."""
    if asset.kind != "model":
        raise ValueError(f"asset {asset.id!r} is a {asset.kind}, not a model")
    roots = import_model(asset.path, collection, target_dims_m, name=kw.pop("name", asset.id), **kw)
    for r in roots:
        r["envgen_asset_id"] = asset.id
        if asset.license:
            r["envgen_asset_license"] = asset.license
    return roots


# ----------------------------------------------------------------------------- materials

def _set_colorspace(img: bpy.types.Image, data: bool) -> None:
    names = ("Non-Color", "Raw", "Generic Data") if data else ("sRGB",)
    for n in names:
        try:
            img.colorspace_settings.name = n
            return
        except TypeError:
            continue


def _load_image(path: Path, data: bool) -> bpy.types.Image:
    img = bpy.data.images.load(str(path), check_existing=True)
    if data and img.colorspace_settings.name not in ("Non-Color", "Raw", "Generic Data"):
        # A colour map could be shared by another material: copy rather than mutate.
        if img.users > 0 and not img.get("envgen_data"):
            img = img.copy()
        _set_colorspace(img, True)
        img["envgen_data"] = True
    elif not data:
        _set_colorspace(img, False)
    return img


def build_pbr_material(texture_set: Mapping[str, str | Path], name: str, scale_m: float = 1.0,
                       *, projection: str = "BOX", projection_blend: float = 0.2,
                       coords: str = "object", displacement_scale_m: float = 0.03,
                       displacement_midlevel: float = 0.5, normal_strength: float = 1.0,
                       ao_strength: float = 0.0,
                       displacement_method: str = "BOTH") -> bpy.types.Material:
    """Build a Principled-BSDF material from ``{map_type: path}``.

    Map types: ``base_color, roughness, metallic, normal_gl, normal_dx, displacement, ao, arm,
    opacity, emission``. Data maps are loaded Non-Color. ``arm`` (R=AO, G=roughness,
    B=metallic) is unpacked with Separate Color and only feeds channels that have no dedicated
    map. GL normals are preferred; DX normals get their green channel inverted.

    Texture coordinates: ``coords="object"`` (Texture Coordinate > Object) or ``"world"``
    (Geometry > Position), divided by ``scale_m`` so one texture tile spans ``scale_m`` metres;
    image nodes use ``projection`` (``BOX`` with ``projection_blend``) so no UVs are needed.
    Tangent-space normal maps need tangents (i.e. a UV map); on UV-less geometry Cycles leaves
    the normal unperturbed, so surface detail then comes from the displacement map, wired to
    the Material Output with ``material.displacement_method = displacement_method`` ('BOTH' =
    true displacement where the mesh is subdivided + bump for the rest).

    ``ao_strength`` > 0 multiplies AO into base colour (off by default: Cycles computes AO).
    """
    ts = {k: Path(v) for k, v in texture_set.items() if v}
    if "base_color" not in ts and "arm" not in ts and not ts:
        raise ValueError("empty texture set")
    mat = bpy.data.materials.new(name)
    if hasattr(mat, "use_nodes") and not mat.use_nodes:
        try:
            mat.use_nodes = True  # deprecated (always on) in Blender 5.x
        except (AttributeError, TypeError):
            pass
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    nodes.clear()

    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (900, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (550, 0)
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    # coordinates -> mapping (scale = 1/scale_m)
    s = 1.0 / max(float(scale_m), 1e-6)
    if coords == "world":
        src = nodes.new("ShaderNodeNewGeometry")
        src_socket = src.outputs["Position"]
    else:
        src = nodes.new("ShaderNodeTexCoord")
        src_socket = src.outputs["Object"]
    src.location = (-1300, 0)
    mapping = nodes.new("ShaderNodeMapping")
    mapping.location = (-1100, 0)
    mapping.vector_type = "POINT"
    mapping.inputs["Scale"].default_value = (s, s, s)
    links.new(src_socket, mapping.inputs["Vector"])

    y = 400

    def tex(map_type: str) -> bpy.types.Node:
        nonlocal y
        node = nodes.new("ShaderNodeTexImage")
        node.name = node.label = map_type
        node.image = _load_image(ts[map_type], map_type in _DATA_MAPS)
        node.projection = projection
        if projection == "BOX":
            node.projection_blend = projection_blend
        node.interpolation = "Linear"
        node.location = (-800, y)
        y -= 280
        links.new(mapping.outputs["Vector"], node.inputs["Vector"])
        return node

    arm_sep = None
    if "arm" in ts:
        arm = tex("arm")
        arm_sep = nodes.new("ShaderNodeSeparateColor")
        arm_sep.mode = "RGB"
        arm_sep.location = (-500, arm.location.y)
        links.new(arm.outputs["Color"], arm_sep.inputs["Color"])

    ao_socket = None
    if ao_strength > 0:
        if "ao" in ts:
            ao_socket = tex("ao").outputs["Color"]
        elif arm_sep is not None:
            ao_socket = arm_sep.outputs["Red"]

    if "base_color" in ts:
        bc = tex("base_color")
        col_socket = bc.outputs["Color"]
        if ao_socket is not None:
            mix = nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.blend_type = "MULTIPLY"
            mix.location = (-250, bc.location.y)
            mix.inputs["Factor"].default_value = float(ao_strength)
            links.new(col_socket, mix.inputs["A"])
            links.new(ao_socket, mix.inputs["B"])
            col_socket = mix.outputs["Result"]
        links.new(col_socket, bsdf.inputs["Base Color"])

    if "roughness" in ts:
        links.new(tex("roughness").outputs["Color"], bsdf.inputs["Roughness"])
    elif arm_sep is not None:
        links.new(arm_sep.outputs["Green"], bsdf.inputs["Roughness"])

    if "metallic" in ts:
        links.new(tex("metallic").outputs["Color"], bsdf.inputs["Metallic"])
    elif arm_sep is not None:
        links.new(arm_sep.outputs["Blue"], bsdf.inputs["Metallic"])

    if "opacity" in ts:
        links.new(tex("opacity").outputs["Color"], bsdf.inputs["Alpha"])

    if "emission" in ts:
        links.new(tex("emission").outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = 1.0

    normal_key = "normal_gl" if "normal_gl" in ts else ("normal_dx" if "normal_dx" in ts else None)
    if normal_key:
        nimg = tex(normal_key)
        nmap = nodes.new("ShaderNodeNormalMap")
        nmap.space = "TANGENT"
        nmap.location = (250, -300)
        nmap.inputs["Strength"].default_value = normal_strength
        col = nimg.outputs["Color"]
        if normal_key == "normal_dx":  # DirectX -> OpenGL: invert green
            sep = nodes.new("ShaderNodeSeparateColor")
            sep.location = (-500, nimg.location.y)
            inv = nodes.new("ShaderNodeMath")
            inv.operation = "SUBTRACT"
            inv.inputs[0].default_value = 1.0
            inv.location = (-300, nimg.location.y - 60)
            comb = nodes.new("ShaderNodeCombineColor")
            comb.location = (-100, nimg.location.y)
            links.new(col, sep.inputs["Color"])
            links.new(sep.outputs["Red"], comb.inputs["Red"])
            links.new(sep.outputs["Green"], inv.inputs[1])
            links.new(inv.outputs["Value"], comb.inputs["Green"])
            links.new(sep.outputs["Blue"], comb.inputs["Blue"])
            col = comb.outputs["Color"]
        links.new(col, nmap.inputs["Color"])
        links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])

    if "displacement" in ts:
        dimg = tex("displacement")
        disp = nodes.new("ShaderNodeDisplacement")
        disp.location = (550, -500)
        disp.space = "OBJECT"
        disp.inputs["Midlevel"].default_value = displacement_midlevel
        disp.inputs["Scale"].default_value = displacement_scale_m
        links.new(dimg.outputs["Color"], disp.inputs["Height"])
        links.new(disp.outputs["Displacement"], out.inputs["Displacement"])
        if hasattr(mat, "displacement_method"):
            mat.displacement_method = displacement_method
        elif hasattr(mat, "cycles") and hasattr(mat.cycles, "displacement_method"):
            mat.cycles.displacement_method = displacement_method

    mat["envgen_texture_set"] = json.dumps({k: str(v) for k, v in ts.items()})
    mat["envgen_scale_m"] = float(scale_m)
    return mat


def build_material_from_asset(asset, scale_m: float | None = None, **kw) -> bpy.types.Material:
    """``build_pbr_material`` for a registry Asset (kind ``material``). ``scale_m`` defaults to
    the asset's tile size (``dimensions_m[0]``) or 1 m."""
    if asset.kind != "material":
        raise ValueError(f"asset {asset.id!r} is a {asset.kind}, not a material")
    if scale_m is None:
        scale_m = float(asset.dimensions_m[0]) if asset.dimensions_m else 1.0
    return build_pbr_material(asset.texture_set(), asset.id, scale_m, **kw)


def set_world_hdri(path: str | Path, strength: float = 1.0, rotation_z_deg: float = 0.0,
                   world: bpy.types.World | None = None) -> bpy.types.World:
    """Environment-texture world from an .hdr/.exr (equirectangular)."""
    scene = bpy.context.scene
    world = world or scene.world or bpy.data.worlds.new("World")
    scene.world = world
    if hasattr(world, "use_nodes") and not world.use_nodes:
        try:
            world.use_nodes = True
        except (AttributeError, TypeError):
            pass
    nt = world.node_tree
    nt.nodes.clear()
    coord = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(rotation_z_deg))
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(str(path), check_existing=True)
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = strength
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    for i, n in enumerate((coord, mapping, env, bg, out)):
        n.location = (-800 + 220 * i, 0)
    return world
