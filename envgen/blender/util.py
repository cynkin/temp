"""Low-level bpy helpers: scene reset, collections, a bmesh-based MeshBuilder, colour utilities."""
from __future__ import annotations

import math
import random
from typing import Iterable, Sequence

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

Vec3 = Sequence[float]


# ----------------------------------------------------------------------------- scene

def reset_scene() -> bpy.types.Scene:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    return scene


def collection(name: str, parent: bpy.types.Collection | None = None) -> bpy.types.Collection:
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(coll)
    return coll


def tag(obj: bpy.types.Object, **props) -> bpy.types.Object:
    """Attach envgen metadata used by validation / export (stored as custom properties)."""
    for k, v in props.items():
        obj[f"envgen_{k}"] = v
    return obj


# ----------------------------------------------------------------------------- colour

def kelvin_to_rgb(kelvin: float) -> tuple[float, float, float]:
    """Approximate blackbody colour (Tanner Helland fit) returned as *linear* RGB, normalised."""
    t = max(1000.0, min(40000.0, kelvin)) / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(t) - 161.1195681661
        b = 0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    else:
        r = 329.698727446 * ((t - 60) ** -0.1332047592)
        g = 288.1221695283 * ((t - 60) ** -0.0755148492)
        b = 255.0
    srgb = [min(255.0, max(0.0, c)) / 255.0 for c in (r, g, b)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb]
    m = max(lin)
    return tuple(c / m for c in lin)  # type: ignore[return-value]


def srgb_to_linear(rgb: Vec3) -> tuple[float, float, float]:
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb)  # type: ignore


# ----------------------------------------------------------------------------- math

def look_rotation(direction: Vector, up: Vector = Vector((0, 0, 1))) -> Matrix:
    """Rotation matrix whose local +Z axis points along ``direction``."""
    z = direction.normalized()
    if abs(z.dot(up)) > 0.999:
        up = Vector((0, 1, 0))
    x = up.cross(z).normalized()
    y = z.cross(x)
    return Matrix((x, y, z)).transposed().to_4x4()


def smoothstep(e0: float, e1: float, x: float) -> float:
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


# ----------------------------------------------------------------------------- mesh building

class MeshBuilder:
    """Accumulate primitives into one bmesh with per-part material slots.

    Joining parts into a single object per prop keeps the scene light while
    allowing each part its own material. Boxes get *real* geometric bevels so
    edges catch highlights like scanned/hand-modelled assets do.
    """

    def __init__(self, name: str):
        self.name = name
        self.bm = bmesh.new()
        self.materials: list[str] = []

    # -- helpers
    def _mat_index(self, material: str) -> int:
        if material not in self.materials:
            self.materials.append(material)
        return self.materials.index(material)

    def _begin(self) -> None:
        self._before = set(self.bm.faces)

    def _finish(self, verts: Iterable[bmesh.types.BMVert], material: str, smooth: bool) -> list:
        """Assign material/shading to every face created since the matching ``_begin``."""
        idx = self._mat_index(material)
        new = [f for f in self.bm.faces if f not in self._before]
        for f in new:
            f.material_index = idx
            f.smooth = smooth
        return list(verts)

    # -- primitives
    def box(self, center: Vec3, size: Vec3, material: str, bevel: float = 0.0,
            rotation: Vec3 = (0, 0, 0), segments: int = 2) -> list:
        self._begin()
        m = Matrix.Translation(Vector(center)) @ Euler(rotation).to_matrix().to_4x4() @ Matrix.Diagonal(
            (*size, 1.0))
        res = bmesh.ops.create_cube(self.bm, size=1.0, matrix=m)
        verts = res["verts"]
        if bevel > 0:
            edges = list({e for v in verts for e in v.link_edges})
            bevel = min(bevel, 0.45 * min(size))
            bmesh.ops.bevel(self.bm, geom=edges, offset=bevel, offset_type="OFFSET", segments=segments,
                            profile=0.5, affect="EDGES", clamp_overlap=True)
            verts = [v for v in verts if v.is_valid]
        return self._finish(verts, material, smooth=bevel > 0)

    def cylinder(self, p0: Vec3, p1: Vec3, radius: float, material: str, segments: int = 12,
                 radius2: float | None = None, caps: bool = True) -> list:
        self._begin()
        a, b = Vector(p0), Vector(p1)
        d = b - a
        length = d.length
        if length < 1e-6:
            return []
        m = Matrix.Translation((a + b) / 2) @ look_rotation(d)
        res = bmesh.ops.create_cone(self.bm, cap_ends=caps, cap_tris=False, segments=segments,
                                    radius1=radius, radius2=radius if radius2 is None else radius2,
                                    depth=length, matrix=m)
        return self._finish(res["verts"], material, smooth=True)

    def tube_path(self, points: Sequence[Vec3], radius: float, material: str, segments: int = 10) -> None:
        for p0, p1 in zip(points[:-1], points[1:]):
            self.cylinder(p0, p1, radius, material, segments=segments)
        for p in points[1:-1]:
            self.sphere(p, radius, material, subdiv=1)

    def sphere(self, center: Vec3, radius: float, material: str, subdiv: int = 2,
               scale: Vec3 = (1, 1, 1)) -> list:
        self._begin()
        m = Matrix.Translation(Vector(center)) @ Matrix.Diagonal((*scale, 1.0))
        res = bmesh.ops.create_icosphere(self.bm, subdivisions=subdiv, radius=radius, matrix=m)
        return self._finish(res["verts"], material, smooth=True)

    def torus(self, center: Vec3, major: float, minor: float, material: str, axis: Vec3 = (1, 0, 0),
              major_seg: int = 32, minor_seg: int = 8) -> list:
        self._begin()
        rot = look_rotation(Vector(axis))
        base = Matrix.Translation(Vector(center)) @ rot
        verts_rings = []
        for i in range(major_seg):
            u = 2 * math.pi * i / major_seg
            ring = []
            for j in range(minor_seg):
                v = 2 * math.pi * j / minor_seg
                r = major + minor * math.cos(v)
                local = Vector((r * math.cos(u), r * math.sin(u), minor * math.sin(v)))
                ring.append(self.bm.verts.new(base @ local))
            verts_rings.append(ring)
        for i in range(major_seg):
            r0, r1 = verts_rings[i], verts_rings[(i + 1) % major_seg]
            for j in range(minor_seg):
                self.bm.faces.new((r0[j], r1[j], r1[(j + 1) % minor_seg], r0[(j + 1) % minor_seg]))
        return self._finish([v for r in verts_rings for v in r], material, smooth=True)

    def quad(self, corners: Sequence[Vec3], material: str, smooth: bool = False) -> list:
        self._begin()
        verts = [self.bm.verts.new(Vector(c)) for c in corners]
        self.bm.faces.new(verts)
        return self._finish(verts, material, smooth)

    def grid(self, center: Vec3, size: tuple[float, float], material: str, xs: int = 8, ys: int = 8,
             rotation: Vec3 = (0, 0, 0), displace=None) -> list:
        """Subdivided plane; ``displace(x, y) -> dz`` sculpts it (cloth, paper bends, puddles)."""
        self._begin()
        rot = Euler(rotation).to_matrix().to_4x4()
        verts = []
        rows = []
        for j in range(ys + 1):
            row = []
            for i in range(xs + 1):
                u, v = i / xs - 0.5, j / ys - 0.5
                x, y = u * size[0], v * size[1]
                z = displace(x, y) if displace else 0.0
                co = Matrix.Translation(Vector(center)) @ rot @ Vector((x, y, z))
                vert = self.bm.verts.new(co)
                row.append(vert)
                verts.append(vert)
            rows.append(row)
        for j in range(ys):
            for i in range(xs):
                self.bm.faces.new((rows[j][i], rows[j][i + 1], rows[j + 1][i + 1], rows[j + 1][i]))
        return self._finish(verts, material, smooth=True)

    def prism(self, outline: Sequence[tuple[float, float]], z0: float, z1: float, material: str,
              transform: Matrix | None = None) -> list:
        """Extrude a 2D polygon (XY) between z0 and z1."""
        self._begin()
        t = transform or Matrix.Identity(4)
        bottom = [self.bm.verts.new(t @ Vector((x, y, z0))) for x, y in outline]
        top = [self.bm.verts.new(t @ Vector((x, y, z1))) for x, y in outline]
        self.bm.faces.new(list(reversed(bottom)))
        self.bm.faces.new(top)
        n = len(outline)
        for i in range(n):
            self.bm.faces.new((bottom[i], bottom[(i + 1) % n], top[(i + 1) % n], top[i]))
        return self._finish(bottom + top, material, smooth=False)

    # -- output
    def to_object(self, coll: bpy.types.Collection, materials: dict[str, bpy.types.Material],
                  location: Vec3 = (0, 0, 0), rotation: Vec3 = (0, 0, 0),
                  sharp_angle_deg: float = 35.0) -> bpy.types.Object:
        bmesh.ops.remove_doubles(self.bm, verts=self.bm.verts, dist=1e-5)
        bmesh.ops.recalc_face_normals(self.bm, faces=self.bm.faces)
        mesh = bpy.data.meshes.new(self.name)
        self.bm.to_mesh(mesh)
        self.bm.free()
        if hasattr(mesh, "set_sharp_from_angle"):
            mesh.set_sharp_from_angle(angle=math.radians(sharp_angle_deg))
        obj = bpy.data.objects.new(self.name, mesh)
        for name in self.materials:
            mat = materials.get(name)
            if mat is None:
                raise KeyError(f"MeshBuilder {self.name}: unknown material {name!r}")
            mesh.materials.append(mat)
        obj.location = location
        obj.rotation_euler = rotation
        coll.objects.link(obj)
        return obj


def place_on_floor(obj: bpy.types.Object, floor_z: float = 0.0) -> None:
    """Translate ``obj`` so its lowest world-space vertex rests exactly on ``floor_z``."""
    bpy.context.view_layer.update()
    mw = obj.matrix_world
    min_z = min((mw @ v.co).z for v in obj.data.vertices)
    obj.location.z += floor_z - min_z


def seeded(seed: int, salt: str) -> random.Random:
    return random.Random(f"{seed}:{salt}")
