"""Local asset library: scanning, sidecar metadata, JSON index and scale-aware search.

The library is a plain directory tree (default ``<repo>/asset_library``, override with the
``ENVGEN_ASSET_LIBRARY`` environment variable). It may contain:

* **Model files** -- ``.blend .glb .gltf .fbx .obj .usd .usda .usdc .usdz``. Each model file
  is one asset unless the folder holds an ``asset.json`` sidecar, in which case the whole
  folder is one asset (its primary model file is picked automatically, or named by the
  sidecar's ``"file"`` key).
* **PBR texture-set folders** -- a folder whose images carry map tokens in their file names
  (``*_basecolor.png``, ``*_nor_gl_2k.jpg``, ``*_Roughness.jpg`` ...). See ``MAP_TOKENS``.
* **HDRIs** -- ``.hdr``/``.exr`` files that are not texture maps.

Sidecar metadata (``asset.json`` in the asset folder, or ``<file stem>.asset.json`` next to a
single file)::

    {"id": "...", "name": "...", "kind": "model"|"material"|"hdri", "category": "...",
     "tags": [...], "dimensions_m": [x, y, z], "license": "...", "source": "...",
     "source_url": "...", "author": "...",
     "file": "optional/relative/primary_file.glb", "maps": {"base_color": "rel/path.jpg", ...}}

``dimensions_m`` is the *real-world* bounding-box size in metres, Z-up (x = width,
y = depth, z = height). For materials it is the physical size of one texture tile.
When the sidecar has no dimensions, the scanner reads them cheaply from glTF accessor
bounds or OBJ vertex positions (both assumed metres, Y-up -> converted to Z-up).

Scale-aware selection (``AssetLibrary.select_for_slot``) is the core consumer: a slot
describes what the scene needs (kind, category, tags, target size) and the library never
returns an asset whose real size is outside the slot's tolerance (e.g. no 3 m asset for a
0.5 m slot). Assets with unknown size are allowed but ranked lower.

CLI::

    python -m envgen.assets.registry scan [--root DIR ...] [--no-save]
    python -m envgen.assets.registry search [QUERY] [--kind K] [--category C] [--tags a,b]
                                             [--max-dims X Y Z] [--min-dims X Y Z] [--json]
    python -m envgen.assets.registry select --kind model --tags chair,wood --target 0.5 0.5 0.9
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import struct
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Iterable, Sequence

__all__ = [
    "MODEL_EXTS",
    "IMAGE_EXTS",
    "HDRI_EXTS",
    "MAP_TOKENS",
    "Asset",
    "AssetLibrary",
    "default_library_root",
    "detect_map_type",
    "find_texture_maps",
    "read_model_dimensions",
    "tokenize",
]

INDEX_VERSION = 1

MODEL_EXTS = (".blend", ".glb", ".gltf", ".fbx", ".obj", ".usd", ".usda", ".usdc", ".usdz")
# Preference order when a folder holds several model files for the same asset.
_MODEL_PREFERENCE = (".glb", ".gltf", ".blend", ".fbx", ".usdz", ".usdc", ".usd", ".usda", ".obj")
_USD_EXTS = (".usd", ".usda", ".usdc", ".usdz")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".hdr", ".tga", ".bmp", ".webp")
HDRI_EXTS = (".hdr", ".exr")

# Canonical map type -> filename tokens (lower-case, separators removed for multi-word tokens).
MAP_TOKENS: dict[str, tuple[str, ...]] = {
    "base_color": ("basecolor", "albedo", "diffuse", "diff", "color", "colour", "col"),
    "roughness": ("roughness", "rough"),
    "metallic": ("metallic", "metalness", "metal"),
    "normal_gl": ("normalgl", "norgl", "normal", "nor", "nrm"),
    "normal_dx": ("normaldx", "nordx"),
    "displacement": ("displacement", "disp", "height"),
    "ao": ("ambientocclusion", "ao", "occlusion"),
    "arm": ("arm",),
    "opacity": ("opacity", "alpha"),
    "emission": ("emission", "emissive"),
}
_TOKEN_TO_MAP = {tok: kind for kind, toks in MAP_TOKENS.items() for tok in toks}
# Tokens that commonly trail the map token and must be skipped when scanning from the end.
_TRAILING_RE = re.compile(r"^(\d+k|\d+px|\d+|jpg|jpeg|png|exr|tif|tiff|8bit|16bit|32bit|lq|hq|sq)$")
_SKIP_TOKENS = {"map", "tex", "texture", "srgb", "linear", "raw", "lin", "8b", "16b", "32b"}
_SPLIT_RE = re.compile(r"[^a-z0-9]+")
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")

_STOPWORDS = {"the", "a", "an", "of", "and", "with", "for", "in", "on", "asset", "model",
              "material", "texture", "textures", "2k", "4k", "1k", "8k", "jpg", "png"}

KINDS = ("model", "material", "hdri")
# Folder names that say nothing about the asset itself (not used as tags).
_GENERIC_DIRS = {"models", "meshes", "props", "textures", "materials", "hdris", "hdri", "assets",
                 "library", "polyhaven", "ambientcg", "downloads", "local", "x"}


def repo_root() -> Path:
    """Repository root = parent of the ``envgen`` package directory."""
    return Path(__file__).resolve().parents[2]


def default_library_root() -> Path:
    """``$ENVGEN_ASSET_LIBRARY`` or ``<repo>/asset_library``."""
    env = os.environ.get("ENVGEN_ASSET_LIBRARY")
    return Path(env).expanduser() if env else repo_root() / "asset_library"


def tokenize(text: str | Iterable[str] | None) -> list[str]:
    """Lower-case word tokens from a string (camelCase and separators split), stopwords removed."""
    if text is None:
        return []
    if not isinstance(text, str):
        out: list[str] = []
        for t in text:
            out.extend(tokenize(t))
        return out
    text = _CAMEL_RE.sub(" ", text).lower()
    toks = [t for t in _SPLIT_RE.split(text) if t and t not in _STOPWORDS]
    # also strip trailing digits ("rock01" -> "rock")
    extra = [re.sub(r"\d+$", "", t) for t in toks]
    return list(dict.fromkeys(toks + [e for e in extra if e and e not in _STOPWORDS]))


def _stem_tokens(stem: str) -> list[str]:
    return [t for t in _SPLIT_RE.split(stem.lower()) if t]


def detect_map_type(filename: str | Path) -> str | None:
    """Map type of a texture file from its name, or None.

    Tokens are scanned from the end of the stem (map tokens are suffixes, possibly followed by
    resolution/format tokens), checking two-token joins first so ``nor_gl``/``Normal_DX``/
    ``base_color`` resolve correctly and ``metal_plate_diff_2k`` is a base colour map.
    """
    p = Path(filename)
    if p.suffix.lower() not in IMAGE_EXTS:
        return None
    toks = _stem_tokens(p.stem)
    for i in range(len(toks) - 1, -1, -1):
        tok = toks[i]
        if i > 0:
            joined = toks[i - 1] + tok
            if joined in _TOKEN_TO_MAP:
                return _TOKEN_TO_MAP[joined]
        if tok in _TOKEN_TO_MAP:
            return _TOKEN_TO_MAP[tok]
        if tok in ("gl", "dx") or tok in _SKIP_TOKENS or _TRAILING_RE.match(tok):
            continue
        # First unknown token from the end: not a map ("metal_plate_preview" is not metallic).
        return None
    return None


def _set_prefix(stem: str) -> str:
    """Filename stem with map and trailing tokens removed (groups maps into sets)."""
    toks = _stem_tokens(stem)
    keep = [t for t in toks if t not in _TOKEN_TO_MAP and t not in ("gl", "dx")
            and not _TRAILING_RE.match(t)]
    return "_".join(keep)


def find_texture_maps(folder: Path, recursive: bool = False) -> dict[str, dict[str, Path]]:
    """Group texture maps in ``folder`` into sets keyed by filename prefix.

    Returns ``{prefix: {map_type: path}}``. When both GL and DX normals exist, both are listed
    (consumers prefer ``normal_gl``). Among duplicates of the same map, PNG/JPG beat EXR for
    colour maps and EXR/PNG beat JPG for displacement.
    """
    files = folder.rglob("*") if recursive else folder.iterdir()
    sets: dict[str, dict[str, Path]] = {}
    for f in sorted(files):
        if not f.is_file():
            continue
        mt = detect_map_type(f.name)
        if mt is None:
            continue
        prefix = _set_prefix(f.stem)
        cur = sets.setdefault(prefix, {})
        if mt in cur and _ext_rank(mt, cur[mt]) <= _ext_rank(mt, f):
            continue
        cur[mt] = f
    return sets


def _ext_rank(map_type: str, p: Path) -> int:
    ext = p.suffix.lower()
    order = ((".exr", ".tif", ".tiff", ".png", ".jpg", ".jpeg") if map_type == "displacement"
             else (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".webp", ".exr"))
    return order.index(ext) if ext in order else len(order)


def _is_material_set(maps: dict[str, Path]) -> bool:
    return "base_color" in maps or len(maps) >= 2


# ----------------------------------------------------------------------------- dimensions

def _quat_to_mat(q: Sequence[float]):
    x, y, z, w = q
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def _node_matrix(node: dict):
    """4x4 (row-major nested list) local matrix of a glTF node."""
    if "matrix" in node:
        m = node["matrix"]  # column-major
        return [[m[c * 4 + r] for c in range(4)] for r in range(4)]
    t = node.get("translation", [0, 0, 0])
    r = _quat_to_mat(node.get("rotation", [0, 0, 0, 1]))
    s = node.get("scale", [1, 1, 1])
    return [[r[0][0] * s[0], r[0][1] * s[1], r[0][2] * s[2], t[0]],
            [r[1][0] * s[0], r[1][1] * s[1], r[1][2] * s[2], t[1]],
            [r[2][0] * s[0], r[2][1] * s[1], r[2][2] * s[2], t[2]],
            [0, 0, 0, 1]]


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _gltf_json(path: Path) -> dict | None:
    with open(path, "rb") as fh:
        if path.suffix.lower() == ".glb":
            header = fh.read(12)
            if len(header) < 12 or header[:4] != b"glTF":
                return None
            clen, ctype = struct.unpack("<II", fh.read(8))
            if ctype != 0x4E4F534A:  # 'JSON'
                return None
            return json.loads(fh.read(clen).decode("utf-8"))
        return json.loads(fh.read().decode("utf-8"))


def _gltf_dimensions(path: Path) -> list[float] | None:
    doc = _gltf_json(path)
    if not doc:
        return None
    accessors = doc.get("accessors", [])
    meshes = doc.get("meshes", [])
    nodes = doc.get("nodes", [])
    lo = [math.inf] * 3
    hi = [-math.inf] * 3

    def visit(idx: int, parent, depth=0):
        if depth > 64:
            return
        node = nodes[idx]
        m = _matmul(parent, _node_matrix(node))
        if "mesh" in node:
            for prim in meshes[node["mesh"]].get("primitives", []):
                acc = accessors[prim.get("attributes", {}).get("POSITION", -1)] \
                    if "POSITION" in prim.get("attributes", {}) else None
                if not acc or "min" not in acc or "max" not in acc:
                    continue
                mn, mx = acc["min"], acc["max"]
                for cx in (mn[0], mx[0]):
                    for cy in (mn[1], mx[1]):
                        for cz in (mn[2], mx[2]):
                            for a in range(3):
                                v = m[a][0] * cx + m[a][1] * cy + m[a][2] * cz + m[a][3]
                                lo[a] = min(lo[a], v)
                                hi[a] = max(hi[a], v)
        for c in node.get("children", []):
            visit(c, m, depth + 1)

    ident = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    scenes = doc.get("scenes")
    if scenes:
        roots = scenes[doc.get("scene", 0)].get("nodes", [])
    else:
        children = {c for n in nodes for c in n.get("children", [])}
        roots = [i for i in range(len(nodes)) if i not in children]
    for r in roots:
        visit(r, ident)
    if not all(math.isfinite(v) for v in lo + hi):
        return None
    ext = [hi[a] - lo[a] for a in range(3)]
    # glTF is Y-up -> Z-up: (x, y, z)_gltf extents -> (x, z, y)
    return [round(ext[0], 4), round(ext[2], 4), round(ext[1], 4)]


def _obj_dimensions(path: Path, max_bytes: int = 64 * 1024 * 1024) -> list[float] | None:
    if path.stat().st_size > max_bytes:
        return None
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("v "):
                parts = line.split()
                try:
                    v = [float(parts[1]), float(parts[2]), float(parts[3])]
                except (IndexError, ValueError):
                    continue
                for a in range(3):
                    lo[a] = min(lo[a], v[a])
                    hi[a] = max(hi[a], v[a])
    if not all(math.isfinite(v) for v in lo + hi):
        return None
    ext = [hi[a] - lo[a] for a in range(3)]
    return [round(ext[0], 4), round(ext[2], 4), round(ext[1], 4)]  # OBJ is Y-up by convention


def read_model_dimensions(path: Path) -> list[float] | None:
    """Best-effort bounding-box size (m, Z-up) read without Blender; None if unsupported."""
    try:
        ext = path.suffix.lower()
        if ext in (".gltf", ".glb"):
            return _gltf_dimensions(path)
        if ext == ".obj":
            return _obj_dimensions(path)
    except (OSError, ValueError, KeyError, IndexError, TypeError, struct.error):
        return None
    return None


# ----------------------------------------------------------------------------- Asset

@dataclass
class Asset:
    """One library asset. ``path`` is the model file, material folder or HDRI file."""

    id: str
    name: str
    kind: str  # "model" | "material" | "hdri"
    path: str
    category: str = ""
    tags: list[str] = field(default_factory=list)
    dimensions_m: list[float] | None = None
    dims_source: str = ""  # "sidecar" | "gltf" | "obj" | ""
    maps: dict[str, str] = field(default_factory=dict)  # material: map_type -> file
    format: str = ""  # model/hdri file extension, e.g. ".glb"
    license: str = ""
    source: str = "local"
    source_url: str = ""
    author: str = ""
    sidecar: str = ""

    # -- convenience -------------------------------------------------------------------
    @property
    def file(self) -> Path:
        return Path(self.path)

    def texture_set(self) -> dict[str, Path]:
        """``{map_type: Path}`` for ``envgen.blender.asset_import.build_pbr_material``."""
        return {k: Path(v) for k, v in self.maps.items()}

    def keywords(self) -> set[str]:
        return set(tokenize([self.name, self.id, self.category])) | set(tokenize(self.tags))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Asset":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


def _read_sidecar(p: Path) -> dict:
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _clean_dims(d) -> list[float] | None:
    if not isinstance(d, (list, tuple)) or not d:
        return None
    try:
        vals = [float(v) for v in d]
    except (TypeError, ValueError):
        return None
    if any(v < 0 or not math.isfinite(v) for v in vals) or all(v == 0 for v in vals):
        return None
    return vals


def _primary_model(files: Iterable[Path]) -> Path | None:
    by_ext = {}
    for f in files:
        by_ext.setdefault(f.suffix.lower(), []).append(f)
    for ext in _MODEL_PREFERENCE:
        if ext in by_ext:
            return sorted(by_ext[ext], key=lambda p: (len(p.parts), p.name))[0]
    return None


_TEXTURE_SUBDIRS = {"textures", "texture", "tex", "maps", "images", "materials"}


# ----------------------------------------------------------------------------- library

class AssetLibrary:
    """Scan, index and query one or more asset-library roots."""

    def __init__(self, roots: Sequence[Path | str] | None = None):
        self.roots = [Path(r).expanduser() for r in (roots or [default_library_root()])]
        self.assets: list[Asset] = []

    # -- scanning ----------------------------------------------------------------------
    def scan(self) -> list[Asset]:
        """Walk all roots and rebuild ``self.assets``."""
        found: dict[str, Asset] = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            for a in self._scan_dir(root, root):
                key = a.id
                n = 2
                while key in found and found[key].path != a.path:
                    key = f"{a.id}_{n}"
                    n += 1
                a.id = key
                found[key] = a
        self.assets = sorted(found.values(), key=lambda a: (a.kind, a.id))
        return self.assets

    def _scan_dir(self, d: Path, root: Path) -> list[Asset]:
        try:
            entries = sorted(d.iterdir())
        except OSError:
            return []
        if d != root and d.name.startswith("."):
            return []
        files = [e for e in entries if e.is_file()]
        subdirs = [e for e in entries if e.is_dir() and not e.name.startswith(".")]

        sidecar = d / "asset.json"
        if sidecar.is_file():
            a = self._asset_from_folder_sidecar(d, root, _read_sidecar(sidecar), sidecar)
            return [a] if a else []

        out: list[Asset] = []
        models = [f for f in files if f.suffix.lower() in MODEL_EXTS
                  and not f.name.endswith(".asset.json")]
        non_usd = [f for f in models if f.suffix.lower() not in _USD_EXTS]
        mat_sets = {k: v for k, v in find_texture_maps(d).items() if _is_material_set(v)}

        skip_subdirs: set[Path] = set()
        if non_usd or (models and not mat_sets):
            # A model folder: images here and in texture sub-folders belong to the models.
            for f in models:
                out.append(self._asset_from_model_file(f, root))
            skip_subdirs = {s for s in subdirs if s.name.lower() in _TEXTURE_SUBDIRS}
        else:
            # Material folder(s) (ambientCG zips also ship .usdc/.mtlx previews -- ignored).
            for prefix, maps in mat_sets.items():
                out.append(self._asset_from_maps(d, root, prefix, maps, multi=len(mat_sets) > 1))
            map_files = {p for m in mat_sets.values() for p in m.values()}
            for f in files:
                if (f.suffix.lower() in HDRI_EXTS and f not in map_files
                        and detect_map_type(f.name) is None):
                    out.append(self._asset_from_hdri_file(f, root))
        for s in subdirs:
            if s not in skip_subdirs:
                out.extend(self._scan_dir(s, root))
        return out

    @staticmethod
    def _rel_tags(p: Path, root: Path) -> list[str]:
        try:
            rel = p.relative_to(root)
        except ValueError:
            rel = Path(p.name)
        parts = list(rel.parts[:-1]) + [Path(rel.parts[-1]).stem] if rel.parts else []
        return [t for t in tokenize(parts) if not t.isdigit() and t not in _GENERIC_DIRS]

    def _apply_meta(self, a: Asset, meta: dict, sidecar: Path | None) -> Asset:
        if not meta:
            return a
        for key in ("id", "name", "category", "license", "source", "source_url", "author"):
            if isinstance(meta.get(key), str) and meta[key]:
                setattr(a, key, meta[key])
        if meta.get("kind") in KINDS:
            a.kind = meta["kind"]
        if isinstance(meta.get("tags"), list):
            a.tags = list(dict.fromkeys([str(t).lower() for t in meta["tags"]] + a.tags))
        dims = _clean_dims(meta.get("dimensions_m"))
        if dims:
            a.dimensions_m = dims
            a.dims_source = "sidecar"
        if sidecar is not None:
            a.sidecar = str(sidecar)
        return a

    def _asset_from_model_file(self, f: Path, root: Path) -> Asset:
        dims = read_model_dimensions(f)
        a = Asset(id=f.stem, name=f.stem.replace("_", " "), kind="model", path=str(f),
                  tags=self._rel_tags(f, root), format=f.suffix.lower(),
                  dimensions_m=dims, dims_source=(f.suffix.lower().lstrip(".")
                                                  .replace("glb", "gltf") if dims else ""))
        side = f.with_name(f.stem + ".asset.json")
        return self._apply_meta(a, _read_sidecar(side) if side.is_file() else {},
                                side if side.is_file() else None)

    def _asset_from_hdri_file(self, f: Path, root: Path) -> Asset:
        a = Asset(id=f.stem, name=f.stem.replace("_", " "), kind="hdri", path=str(f),
                  tags=self._rel_tags(f, root), format=f.suffix.lower())
        side = f.with_name(f.stem + ".asset.json")
        return self._apply_meta(a, _read_sidecar(side) if side.is_file() else {},
                                side if side.is_file() else None)

    def _asset_from_maps(self, d: Path, root: Path, prefix: str, maps: dict[str, Path],
                         multi: bool) -> Asset:
        aid = f"{d.name}_{prefix}" if multi and prefix else d.name
        a = Asset(id=aid, name=(prefix if multi and prefix else d.name).replace("_", " "),
                  kind="material", path=str(d),
                  tags=list(dict.fromkeys(self._rel_tags(d, root) + tokenize(prefix))),
                  maps={k: str(v) for k, v in maps.items()})
        side = d / f"{prefix}.asset.json" if prefix else None
        meta = _read_sidecar(side) if side is not None and side.is_file() else {}
        return self._apply_meta(a, meta, side if meta else None)

    def _asset_from_folder_sidecar(self, d: Path, root: Path, meta: dict,
                                   sidecar: Path) -> Asset | None:
        all_files = [f for f in d.rglob("*") if f.is_file()]
        explicit = meta.get("file")
        primary: Path | None = None
        if isinstance(explicit, str) and explicit:
            cand = (d / explicit).resolve()
            if cand.is_file() and d.resolve() in cand.parents:
                primary = cand
        kind = meta.get("kind") if meta.get("kind") in KINDS else None
        models = [f for f in all_files if f.suffix.lower() in MODEL_EXTS]
        sets = {k: v for k, v in find_texture_maps(d, recursive=True).items()
                if _is_material_set(v)}
        if kind is None:
            if primary is not None:
                kind = ("hdri" if primary.suffix.lower() in HDRI_EXTS and
                        detect_map_type(primary.name) is None else
                        "model" if primary.suffix.lower() in MODEL_EXTS else "material")
            elif [m for m in models if m.suffix.lower() not in _USD_EXTS] or (models and not sets):
                kind = "model"
            elif sets:
                kind = "material"
            elif any(f.suffix.lower() in HDRI_EXTS for f in all_files):
                kind = "hdri"
            else:
                return None
        a = Asset(id=d.name, name=d.name.replace("_", " "), kind=kind, path=str(d),
                  tags=self._rel_tags(d, root))
        if kind == "model":
            primary = primary or _primary_model(models)
            if primary is None:
                return None
            a.path, a.format = str(primary), primary.suffix.lower()
            dims = read_model_dimensions(primary)
            if dims:
                a.dimensions_m, a.dims_source = dims, primary.suffix.lower().lstrip(".")
        elif kind == "hdri":
            hdrs = sorted([f for f in all_files if f.suffix.lower() in HDRI_EXTS],
                          key=lambda p: (p.suffix.lower() != ".hdr", p.name))
            primary = primary or (hdrs[0] if hdrs else None)
            if primary is None:
                return None
            a.path, a.format = str(primary), primary.suffix.lower()
        else:
            maps: dict[str, Path] = {}
            if isinstance(meta.get("maps"), dict):
                for k, v in meta["maps"].items():
                    p = d / str(v)
                    if k in MAP_TOKENS and p.is_file():
                        maps[k] = p
            if not maps and sets:
                maps = max(sets.values(), key=len)
            if not maps:
                return None
            a.maps = {k: str(v) for k, v in maps.items()}
        return self._apply_meta(a, meta, sidecar)

    # -- index -------------------------------------------------------------------------
    def save_index(self, path: Path | str | None = None) -> Path:
        """Write the JSON index (default ``<first root>/index.json``); paths are stored
        relative to the index folder when inside it, so the library stays relocatable."""
        path = Path(path) if path else self.roots[0] / "index.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        base = path.parent.resolve()

        def rel(p: str) -> str:
            if not p:
                return p
            try:
                return Path(p).resolve().relative_to(base).as_posix()
            except ValueError:
                return p

        recs = []
        for a in self.assets:
            d = a.to_dict()
            d["path"] = rel(a.path)
            d["sidecar"] = rel(a.sidecar)
            d["maps"] = {k: rel(v) for k, v in a.maps.items()}
            recs.append(d)
        doc = {"version": INDEX_VERSION,
               "roots": [rel(str(r)) or "." for r in self.roots],
               "assets": recs}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        tmp.replace(path)
        return path

    def load_index(self, path: Path | str | None = None) -> list[Asset]:
        """Load a JSON index written by ``save_index`` (replaces ``self.assets``)."""
        path = Path(path) if path else self.roots[0] / "index.json"
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        if doc.get("version") != INDEX_VERSION:
            raise ValueError(f"unsupported asset index version {doc.get('version')!r}")
        base = Path(path).parent.resolve()

        def ab(p: str) -> str:
            if not p:
                return p
            q = Path(p)
            return str(q if q.is_absolute() else (base / q))

        self.assets = []
        for d in doc.get("assets", []):
            a = Asset.from_dict(d)
            a.path = ab(a.path)
            a.sidecar = ab(a.sidecar)
            a.maps = {k: ab(v) for k, v in a.maps.items()}
            self.assets.append(a)
        return self.assets

    @classmethod
    def open(cls, roots: Sequence[Path | str] | None = None, rescan: bool = False) -> "AssetLibrary":
        """Load ``<root>/index.json`` if present (and ``rescan`` is False), else scan+save."""
        lib = cls(roots)
        idx = lib.roots[0] / "index.json"
        if idx.is_file() and not rescan:
            try:
                lib.load_index(idx)
                return lib
            except (OSError, ValueError, TypeError):
                pass
        lib.scan()
        if lib.roots[0].is_dir():
            lib.save_index(idx)
        return lib

    # -- queries -----------------------------------------------------------------------
    @staticmethod
    def _relevance(a: Asset, words: set[str], tags: set[str], category: str | None) -> float:
        kw_tags = set(tokenize(a.tags))
        kw_name = set(tokenize([a.name, a.id]))
        kw_cat = set(tokenize(a.category))
        score = 0.0
        for t in tags | words:
            if t in kw_tags:
                score += 2.0
            elif t in kw_cat:
                score += 1.5
            elif t in kw_name:
                score += 1.0
            elif len(t) >= 4 and any(t in k or k in t for k in kw_tags | kw_name if len(k) >= 4):
                score += 0.4  # partial match ("plank" ~ "planks", "wooden" ~ "wood")
        if category:
            cat_toks = set(tokenize(category))
            if a.category.lower() == category.lower():
                score += 3.0
            elif cat_toks and cat_toks <= (kw_cat | kw_tags):
                score += 2.0
        return score

    def search(self, query: str | None = None, kind: str | None = None,
               category: str | None = None, tags: Sequence[str] = (),
               max_dims: Sequence[float] | None = None,
               min_dims: Sequence[float] | None = None) -> list[Asset]:
        """Filter by kind/dims and rank by tag/name overlap.

        ``max_dims``/``min_dims`` are per-axis bounds in metres (use ``None``/0 for an
        unconstrained axis). Assets with unknown dimensions pass the dimension filter but
        rank below assets of equal relevance. If query/tags/category are given, only assets
        with positive relevance are returned.
        """
        words = set(tokenize(query))
        tagset = set(tokenize(list(tags)))
        want_rel = bool(words or tagset or category)
        results: list[tuple[float, str, Asset]] = []
        for a in self.assets:
            if kind and a.kind != kind:
                continue
            d = a.dimensions_m
            if d is not None:
                if max_dims and any(m is not None and m > 0 and i < len(d) and d[i] > m
                                    for i, m in enumerate(max_dims)):
                    continue
                if min_dims and any(m is not None and m > 0 and i < len(d) and d[i] < m
                                    for i, m in enumerate(min_dims)):
                    continue
            rel = self._relevance(a, words, tagset, category)
            if want_rel and rel <= 0:
                continue
            if d is None and (max_dims or min_dims):
                rel -= 0.5
            results.append((rel, a.id, a))
        results.sort(key=lambda r: (-r[0], r[1]))
        return [r[2] for r in results]

    @staticmethod
    def scale_error(dims: Sequence[float], target: Sequence[float]) -> tuple[float, float]:
        """(largest-axis log ratio, worst per-axis log ratio) between asset and target size.

        Rotation about Z is free when placing, so the two horizontal axes are compared sorted.
        Axes where the target is 0/None are ignored. Tiny absolute differences (<= 2 cm) count
        as a perfect match so thin axes (rugs, panels) don't dominate.
        """
        d = list(dims) + [0.0] * (3 - len(dims))
        t = [(v if v else 0.0) for v in (list(target) + [0.0] * (3 - len(target)))]
        if t[0] > 0 and t[1] > 0:
            dh, th = sorted(d[:2]), sorted(t[:2])
            pairs = [(dh[0], th[0]), (dh[1], th[1]), (d[2], t[2])]
        else:
            pairs = [(d[0], t[0]), (d[1], t[1]), (d[2], t[2])]
        pairs = [(a, b) for a, b in pairs if b > 0]
        if not pairs:
            return 0.0, 0.0

        def lr(a: float, b: float) -> float:
            if abs(a - b) <= 0.02:
                return 0.0
            return abs(math.log(max(a, 1e-4) / b))

        big = max(range(len(pairs)), key=lambda i: pairs[i][1])
        return lr(*pairs[big]), max(lr(a, b) for a, b in pairs)

    def rank_for_slot(self, slot: dict) -> list[tuple[float, Asset]]:
        """All acceptable candidates for ``slot`` as (score, asset), best first."""
        kind = slot.get("kind") or "model"
        category = slot.get("category") or None
        tags = list(slot.get("tags") or [])
        query = slot.get("query") or slot.get("name")
        target = slot.get("target_dims_m")
        tol = float(slot.get("scale_tolerance", 0.35))
        max_main = math.log(1.0 + tol)
        max_axis = math.log(1.0 + 2.0 * tol)  # shape check is looser than overall size
        words = set(tokenize(query))
        tagset = set(tokenize(tags))
        need_rel = bool(words or tagset or category)
        out: list[tuple[float, Asset]] = []
        for a in self.assets:
            if a.kind != kind:
                continue
            rel = self._relevance(a, words, tagset, category)
            if need_rel and rel <= 0:
                continue
            score = rel
            if target and kind != "hdri":
                if a.dimensions_m:
                    main, worst = self.scale_error(a.dimensions_m, target)
                    if kind == "model" and (main > max_main or worst > max_axis):
                        continue
                    if kind == "material":
                        # tile size only nudges ranking; materials can be re-scaled freely
                        score -= 0.25 * main
                    else:
                        score += 1.0 - main / max(max_main, 1e-6) * 0.5 - worst / max(max_axis, 1e-6) * 0.5
                else:
                    score -= 1.0  # unknown size: allowed, but one tag-match below a fit
            out.append((score, a))
        out.sort(key=lambda r: (-r[0], r[1].id))
        return out

    def select_for_slot(self, slot: dict) -> Asset | None:
        """Best asset for a scene slot, or None.

        ``slot = {"kind": "model", "category": "furniture", "tags": ["chair", "wood"],
        "target_dims_m": [0.5, 0.5, 0.9], "scale_tolerance": 0.35}``. For models the asset's
        largest dimension must be within ``(1+tol)`` (log-symmetric) of the target's, and
        every axis within ``(1+2*tol)``; assets of unknown size are allowed but rank lower.
        """
        ranked = self.rank_for_slot(slot)
        return ranked[0][1] if ranked else None

    def get(self, asset_id: str) -> Asset | None:
        return next((a for a in self.assets if a.id == asset_id), None)


# ----------------------------------------------------------------------------- CLI

def _fmt(a: Asset) -> str:
    dims = "x".join(f"{v:.2f}" for v in a.dimensions_m) if a.dimensions_m else "?"
    return f"{a.kind:8s} {a.id:40s} dims={dims:16s} cat={a.category or '-':12s} tags={','.join(a.tags[:8])}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m envgen.assets.registry",
                                 description="Scan / search the envgen asset library.")
    ap.add_argument("--root", action="append", type=Path,
                    help="library root (repeatable; default $ENVGEN_ASSET_LIBRARY or <repo>/asset_library)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scan", help="scan roots and write <root>/index.json")
    sc.add_argument("--no-save", action="store_true")
    sc.add_argument("--json", action="store_true")
    se = sub.add_parser("search", help="search assets (uses index.json when present)")
    se.add_argument("query", nargs="?")
    se.add_argument("--kind", choices=KINDS)
    se.add_argument("--category")
    se.add_argument("--tags", default="")
    se.add_argument("--max-dims", nargs=3, type=float)
    se.add_argument("--min-dims", nargs=3, type=float)
    se.add_argument("--limit", type=int, default=20)
    se.add_argument("--rescan", action="store_true")
    se.add_argument("--json", action="store_true")
    sl = sub.add_parser("select", help="pick the best asset for a slot")
    sl.add_argument("--kind", choices=KINDS, default="model")
    sl.add_argument("--category")
    sl.add_argument("--tags", default="")
    sl.add_argument("--target", nargs=3, type=float, metavar=("X", "Y", "Z"))
    sl.add_argument("--tol", type=float, default=0.35)
    sl.add_argument("--rescan", action="store_true")
    sl.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "scan":
        lib = AssetLibrary(args.root)
        assets = lib.scan()
        if args.json:
            print(json.dumps([a.to_dict() for a in assets], indent=1))
        else:
            for a in assets:
                print(_fmt(a))
        counts = {k: sum(1 for a in assets if a.kind == k) for k in KINDS}
        if not args.no_save and lib.roots[0].is_dir():
            p = lib.save_index()
            print(f"# {len(assets)} assets {counts} -> {p}", file=sys.stderr)
        else:
            print(f"# {len(assets)} assets {counts}", file=sys.stderr)
        return 0

    lib = AssetLibrary.open(args.root, rescan=args.rescan)
    tags = [t for t in args.tags.split(",") if t]
    if args.cmd == "search":
        res = lib.search(args.query, kind=args.kind, category=args.category, tags=tags,
                         max_dims=args.max_dims, min_dims=args.min_dims)[: args.limit]
        if args.json:
            print(json.dumps([a.to_dict() for a in res], indent=1))
        else:
            for a in res:
                print(_fmt(a))
        return 0
    slot = {"kind": args.kind, "category": args.category, "tags": tags,
            "target_dims_m": args.target, "scale_tolerance": args.tol}
    a = lib.select_for_slot(slot)
    if a is None:
        print("no matching asset", file=sys.stderr)
        return 1
    print(json.dumps(a.to_dict(), indent=1) if args.json else _fmt(a))
    return 0


if __name__ == "__main__":
    sys.exit(main())
