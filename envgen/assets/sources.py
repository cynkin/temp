"""CC0 online asset sources: Poly Haven and ambientCG.

Both sources are CC0-1.0. Downloads land in ``<library>/<source>/<asset_id>/`` together with
an ``asset.json`` sidecar (see ``envgen.assets.registry``) so a subsequent library scan picks
them up with correct kind, tags, licence and -- where the API reports it -- real-world size.

APIs used (public, no key):

* Poly Haven -- ``GET https://api.polyhaven.com/assets?t=textures|models|hdris[&categories=..]``
  (dict ``id -> info``), ``GET /info/{id}``, ``GET /files/{id}`` (nested dict, e.g.
  ``files["Diffuse"]["2k"]["jpg"]["url"]``; models: ``files["gltf"]["2k"]["gltf"]`` with an
  ``"include"`` dict of ``relative/path -> {url, size, md5}``; HDRIs: ``files["hdri"]["2k"]["hdr"]``).
  Poly Haven asks API users to send a unique User-Agent; we send ``envgen-asset-fetcher/0.1``.
* ambientCG -- ``GET https://ambientcg.com/api/v2/full_json?type=Material&q=..&include=downloadData``
  -> ``foundAssets[].downloadFolders.default.downloadFiletypeCategories.zip.downloads[]`` with
  ``attribute`` (e.g. ``"2K-JPG"``) and ``downloadLink`` (a zip that is extracted safely).

NOTE: these code paths were written against the documented API shapes but could NOT be
exercised against the live services from the development sandbox (both hosts are blocked
there). They were tested against a local HTTP server that serves responses of the same
shape. Any network/HTTP/JSON failure raises :class:`AssetSourceError` with a clear message.

CLI::

    python -m envgen.assets.sources polyhaven search "brick wall" --kind material
    python -m envgen.assets.sources polyhaven download brick_wall_001 --res 2k
    python -m envgen.assets.sources ambientcg search planks --limit 5
    python -m envgen.assets.sources ambientcg download Planks021 --res 2k
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .registry import default_library_root, detect_map_type, tokenize

__all__ = [
    "AssetSourceError",
    "RemoteAsset",
    "PolyHavenSource",
    "AmbientCGSource",
    "get_source",
    "USER_AGENT",
]

USER_AGENT = "envgen-asset-fetcher/0.1 (+automated CC0 asset fetcher for envgen)"
DEFAULT_TIMEOUT = 30.0
_MAX_ZIP_BYTES = 4 * 1024 ** 3  # refuse to extract more than 4 GiB


class AssetSourceError(RuntimeError):
    """Raised for any failure talking to an online asset source."""


@dataclass
class RemoteAsset:
    id: str
    name: str
    kind: str  # model | material | hdri
    source: str
    url: str
    tags: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------------- HTTP

def _request(url: str, timeout: float) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                "Accept": "application/json, */*"})


def http_get_json(url: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """GET ``url`` and decode JSON; raise AssetSourceError on any failure."""
    try:
        with urllib.request.urlopen(_request(url, timeout), timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise AssetSourceError(f"HTTP {e.code} {e.reason} for {url}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise AssetSourceError(f"network error fetching {url}: {reason}") from e
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise AssetSourceError(f"invalid JSON from {url}: {e}") from e


def http_download(url: str, dest: Path, md5: str | None = None,
                  timeout: float = DEFAULT_TIMEOUT, retries: int = 2) -> Path:
    """Stream ``url`` to ``dest`` atomically (temp file + rename), optionally checking md5."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for attempt in range(retries + 1):
        tmp = dest.with_name(dest.name + ".part")
        try:
            h = hashlib.md5()
            with urllib.request.urlopen(_request(url, timeout), timeout=timeout) as resp, \
                    open(tmp, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    h.update(chunk)
                    fh.write(chunk)
            if md5 and h.hexdigest().lower() != md5.lower():
                raise AssetSourceError(f"md5 mismatch for {url}")
            tmp.replace(dest)
            return dest
        except urllib.error.HTTPError as e:
            last = AssetSourceError(f"HTTP {e.code} {e.reason} downloading {url}")
            if e.code < 500:
                break
        except AssetSourceError as e:
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = AssetSourceError(f"network error downloading {url}: {getattr(e, 'reason', e)}")
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        if attempt < retries:
            time.sleep(min(2 ** attempt, 5))
    assert last is not None
    raise last


def _safe_relpath(rel: str) -> PurePosixPath:
    """Validate a server-supplied relative path (no absolute paths, no '..')."""
    p = PurePosixPath(rel.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or not p.parts or ":" in p.parts[0]:
        raise AssetSourceError(f"refusing unsafe path from server: {rel!r}")
    return p


def _safe_name(name: str) -> str:
    base = PurePosixPath(urllib.parse.unquote(name.split("?")[0])).name
    if not base or base in (".", ".."):
        raise AssetSourceError(f"cannot derive a file name from {name!r}")
    return base


def _safe_asset_id(asset_id: str) -> str:
    if not asset_id or any(c in asset_id for c in "/\\") or asset_id in (".", ".."):
        raise AssetSourceError(f"invalid asset id {asset_id!r}")
    return asset_id


def _res_key(res: str) -> int:
    try:
        return int(res.lower().rstrip("k"))
    except ValueError:
        return 0


def _pick_res(available: list[str], wanted: str) -> str | None:
    """Exact resolution, else the nearest one (preferring lower on ties)."""
    if not available:
        return None
    if wanted in available:
        return wanted
    w = _res_key(wanted)
    return min(available, key=lambda r: (abs(_res_key(r) - w), _res_key(r)))


def _score(words: set[str], *fields_: Any) -> float:
    toks: set[str] = set()
    for f in fields_:
        toks |= set(tokenize(f if isinstance(f, (str, list, tuple)) else str(f)))
    s = 0.0
    for w in words:
        if w in toks:
            s += 1.0
        elif len(w) >= 4 and any(w in t or t in w for t in toks if len(t) >= 4):
            s += 0.4
    return s


def _write_sidecar(folder: Path, meta: dict) -> Path:
    p = folder / "asset.json"
    p.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return p


# ----------------------------------------------------------------------------- Poly Haven

class PolyHavenSource:
    """Poly Haven (https://polyhaven.com) CC0 textures, models and HDRIs."""

    name = "polyhaven"
    API = "https://api.polyhaven.com"
    SITE = "https://polyhaven.com/a/"
    KIND_TO_TYPE = {"material": "textures", "model": "models", "hdri": "hdris"}
    TYPE_CODE_TO_KIND = {0: "hdri", 1: "material", 2: "model"}
    # Poly Haven map key -> registry map type (only these are downloaded for materials).
    MAP_KEYS = {
        "Diffuse": "base_color",
        "Rough": "roughness",
        "Metal": "metallic",
        "nor_gl": "normal_gl",
        "Displacement": "displacement",
        "AO": "ao",
        "arm": "arm",
        "Opacity": "opacity",
        "Alpha": "opacity",
        "Emission": "emission",
    }
    _FMT_PREF = {"displacement": ("png", "exr", "jpg"), "default": ("jpg", "png", "exr")}

    def __init__(self, library_root: Path | None = None, timeout: float = DEFAULT_TIMEOUT,
                 api_base: str | None = None):
        self.library_root = Path(library_root) if library_root else default_library_root()
        self.timeout = timeout
        self.api = (api_base or os.environ.get("ENVGEN_POLYHAVEN_API") or self.API).rstrip("/")

    # -- API ---------------------------------------------------------------------------
    def list_assets(self, kind: str = "material", categories: str | None = None) -> dict:
        t = self.KIND_TO_TYPE.get(kind)
        if t is None:
            raise ValueError(f"kind must be one of {list(self.KIND_TO_TYPE)}")
        q = {"t": t}
        if categories:
            q["categories"] = categories
        data = http_get_json(f"{self.api}/assets?{urllib.parse.urlencode(q)}", self.timeout)
        if not isinstance(data, dict):
            raise AssetSourceError("unexpected Poly Haven /assets response (not an object)")
        return data

    def info(self, asset_id: str) -> dict:
        data = http_get_json(f"{self.api}/info/{urllib.parse.quote(_safe_asset_id(asset_id))}",
                             self.timeout)
        if not isinstance(data, dict):
            raise AssetSourceError("unexpected Poly Haven /info response")
        return data

    def files(self, asset_id: str) -> dict:
        data = http_get_json(f"{self.api}/files/{urllib.parse.quote(_safe_asset_id(asset_id))}",
                             self.timeout)
        if not isinstance(data, dict):
            raise AssetSourceError("unexpected Poly Haven /files response")
        return data

    def search(self, query: str | None, kind: str = "material", limit: int = 20,
               categories: str | None = None) -> list[RemoteAsset]:
        data = self.list_assets(kind, categories)
        words = set(tokenize(query))
        out = []
        for aid, info in data.items():
            if not isinstance(info, dict):
                continue
            tags = [str(t) for t in info.get("tags", [])]
            cats = [str(c) for c in info.get("categories", [])]
            s = _score(words, aid, info.get("name", ""), tags, cats) if words else 0.0
            if words and s <= 0:
                continue
            out.append(RemoteAsset(id=aid, name=info.get("name", aid), kind=kind,
                                   source=self.name, url=self.SITE + aid, tags=tags,
                                   categories=cats, score=s))
        out.sort(key=lambda r: (-r.score, -int(data[r.id].get("download_count", 0) or 0), r.id))
        return out[:limit]

    # -- download ----------------------------------------------------------------------
    def _pick_file(self, entry: dict, res: str, fmts: tuple[str, ...]) -> tuple[dict, str] | None:
        if not isinstance(entry, dict):
            return None
        r = _pick_res([k for k, v in entry.items() if isinstance(v, dict)], res)
        if r is None:
            return None
        for f in fmts:
            item = entry[r].get(f)
            if isinstance(item, dict) and item.get("url"):
                return item, r
        for f, item in entry[r].items():  # any format
            if isinstance(item, dict) and item.get("url"):
                return item, r
        return None

    def download(self, asset_id: str, resolution: str = "2k", dest_root: Path | None = None,
                 kind: str | None = None, model_format: str = "gltf") -> Path:
        """Download one asset into ``<dest_root or library>/polyhaven/<asset_id>/``.

        ``kind`` is taken from ``/info`` (type 0/1/2) when not given. Materials get the maps in
        ``MAP_KEYS`` (GL normals); models get the ``model_format`` file (``gltf`` default,
        falling back to ``blend``/``fbx``) plus all of its ``include`` files; HDRIs get the
        ``.hdr``. Returns the asset folder.
        """
        asset_id = _safe_asset_id(asset_id)
        info = self.info(asset_id)
        files = self.files(asset_id)
        if kind is None:
            kind = self.TYPE_CODE_TO_KIND.get(info.get("type"))
            if kind is None:
                kind = ("hdri" if "hdri" in files else
                        "model" if any(k in files for k in ("gltf", "blend", "fbx")) else "material")
        root = Path(dest_root) if dest_root else self.library_root
        final = root / self.name / asset_id
        with _staged(final) as folder:
            written: list[str] = []
            main_file = ""
            maps: dict[str, str] = {}

            if kind == "material":
                for key, mt in self.MAP_KEYS.items():
                    if key not in files or mt in maps:
                        continue
                    picked = self._pick_file(files[key], resolution,
                                             self._FMT_PREF.get(mt, self._FMT_PREF["default"]))
                    if not picked:
                        continue
                    item, _ = picked
                    name = _safe_name(item["url"])
                    # Make sure the registry recognises the map even if the server name is odd.
                    if detect_map_type(name) != mt:
                        name = f"{asset_id}_{mt}{Path(name).suffix}"
                    http_download(item["url"], folder / name, item.get("md5"), self.timeout)
                    maps[mt] = name
                    written.append(name)
                if not maps:
                    raise AssetSourceError(f"Poly Haven asset {asset_id!r} has no texture maps")
            elif kind == "model":
                order = [model_format] + [f for f in ("gltf", "blend", "fbx") if f != model_format]
                picked = None
                for fmt in order:
                    if fmt in files:
                        picked = self._pick_file(files[fmt], resolution, (fmt,))
                        if picked:
                            break
                if not picked:
                    raise AssetSourceError(f"Poly Haven model {asset_id!r} has no gltf/blend/fbx files")
                item, _ = picked
                main_file = _safe_name(item["url"])
                http_download(item["url"], folder / main_file, item.get("md5"), self.timeout)
                written.append(main_file)
                for rel, inc in (item.get("include") or {}).items():
                    if not isinstance(inc, dict) or not inc.get("url"):
                        continue
                    relp = _safe_relpath(rel)
                    http_download(inc["url"], folder.joinpath(*relp.parts), inc.get("md5"), self.timeout)
                    written.append(relp.as_posix())
            elif kind == "hdri":
                picked = self._pick_file(files.get("hdri", {}), resolution, ("hdr", "exr"))
                if not picked:
                    raise AssetSourceError(f"Poly Haven HDRI {asset_id!r} has no hdri files")
                item, _ = picked
                main_file = _safe_name(item["url"])
                http_download(item["url"], folder / main_file, item.get("md5"), self.timeout)
                written.append(main_file)
            else:
                raise ValueError(f"unknown kind {kind!r}")

            cats = [str(c) for c in info.get("categories", [])]
            tags = list(dict.fromkeys([str(t).lower() for t in info.get("tags", [])] +
                                      [c.lower() for c in cats]))
            authors = info.get("authors") or {}
            meta = {
                "id": asset_id,
                "name": info.get("name", asset_id),
                "kind": kind,
                "category": cats[0] if cats else "",
                "tags": tags,
                "license": "CC0-1.0",
                "source": self.name,
                "source_url": self.SITE + asset_id,
                "author": ", ".join(authors.keys()) if isinstance(authors, dict) else str(authors),
                "resolution": resolution,
                "files": written,
                "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            if main_file:
                meta["file"] = main_file
            if maps:
                meta["maps"] = maps
            # Poly Haven reports real-world "dimensions" in millimetres for textures/models.
            dims = info.get("dimensions")
            if isinstance(dims, list) and dims and all(isinstance(v, (int, float)) for v in dims):
                meta["dimensions_m"] = [round(float(v) / 1000.0, 4) for v in dims]
            _write_sidecar(folder, meta)
        return final


# ----------------------------------------------------------------------------- ambientCG

class AmbientCGSource:
    """ambientCG (https://ambientcg.com) CC0 materials (also HDRIs / 3D models)."""

    name = "ambientcg"
    API = "https://ambientcg.com/api/v2/full_json"
    SITE = "https://ambientcg.com/view?id="
    KIND_TO_TYPE = {"material": "Material", "model": "3DModel", "hdri": "HDRI"}
    TYPE_TO_KIND = {"Material": "material", "3DModel": "model", "HDRI": "hdri",
                    "Decal": "material", "Atlas": "material"}

    def __init__(self, library_root: Path | None = None, timeout: float = DEFAULT_TIMEOUT,
                 api_base: str | None = None):
        self.library_root = Path(library_root) if library_root else default_library_root()
        self.timeout = timeout
        self.api = (api_base or os.environ.get("ENVGEN_AMBIENTCG_API") or self.API)

    def _query(self, **params) -> dict:
        params = {k: v for k, v in params.items() if v not in (None, "")}
        data = http_get_json(f"{self.api}?{urllib.parse.urlencode(params)}", self.timeout)
        if not isinstance(data, dict) or not isinstance(data.get("foundAssets", []), list):
            raise AssetSourceError("unexpected ambientCG response shape (no foundAssets list)")
        return data

    @staticmethod
    def _downloads(asset: dict) -> list[dict]:
        try:
            dl = asset["downloadFolders"]["default"]["downloadFiletypeCategories"]
        except (KeyError, TypeError):
            return []
        out = []
        for cat in dl.values() if isinstance(dl, dict) else []:
            if isinstance(cat, dict):
                out.extend(d for d in cat.get("downloads", []) if isinstance(d, dict))
        return out

    def search(self, query: str | None, kind: str = "material", limit: int = 20) -> list[RemoteAsset]:
        t = self.KIND_TO_TYPE.get(kind)
        if t is None:
            raise ValueError(f"kind must be one of {list(self.KIND_TO_TYPE)}")
        data = self._query(type=t, q=query, include="tagData,displayData",
                           limit=max(1, int(limit)), sort="Popular")
        out = []
        for a in data.get("foundAssets", []):
            aid = a.get("assetId")
            if not aid:
                continue
            out.append(RemoteAsset(
                id=aid, name=a.get("displayName", aid), kind=kind, source=self.name,
                url=self.SITE + aid, tags=[str(t) for t in a.get("tags", []) or []],
                categories=[a["displayCategory"]] if a.get("displayCategory") else []))
        return out[:limit]

    def download(self, asset_id: str, resolution: str = "2k", dest_root: Path | None = None,
                 file_type: str = "JPG") -> Path:
        """Download and extract the ``<RES>-<file_type>`` zip (e.g. ``2K-JPG``), falling back
        to the nearest resolution / PNG, into ``<library>/ambientcg/<asset_id>/``."""
        asset_id = _safe_asset_id(asset_id)
        data = self._query(id=asset_id, include="downloadData,tagData,displayData")
        found = [a for a in data.get("foundAssets", []) if a.get("assetId") == asset_id]
        if not found:
            raise AssetSourceError(f"ambientCG asset {asset_id!r} not found")
        asset = found[0]
        downloads = self._downloads(asset)
        if not downloads:
            raise AssetSourceError(f"ambientCG asset {asset_id!r} has no downloads")
        choice = self._choose_download(downloads, resolution, file_type)
        url = choice.get("downloadLink") or choice.get("fullDownloadPath")
        if not url:
            raise AssetSourceError(f"ambientCG download entry without link for {asset_id!r}")

        root = Path(dest_root) if dest_root else self.library_root
        final = root / self.name / asset_id
        with _staged(final) as folder:
            fname = _safe_name(choice.get("fileName") or url)
            written: list[str] = []
            with tempfile.TemporaryDirectory(prefix="envgen_acg_") as td:
                tmp = Path(td) / fname
                http_download(url, tmp, None, self.timeout)
                if zipfile.is_zipfile(tmp):
                    written = _safe_extract_zip(tmp, folder)
                else:
                    shutil.move(str(tmp), folder / fname)
                    written = [fname]

            kind = self.TYPE_TO_KIND.get(asset.get("dataType", ""), "material")
            tags = [str(t).lower() for t in asset.get("tags", []) or []]
            cat = asset.get("displayCategory", "") or ""
            meta = {
                "id": asset_id,
                "name": asset.get("displayName", asset_id),
                "kind": kind,
                "category": cat,
                "tags": list(dict.fromkeys(tags + ([cat.lower()] if cat else []))),
                "license": "CC0-1.0",
                "source": self.name,
                "source_url": self.SITE + asset_id,
                "author": "ambientCG",
                "resolution": choice.get("attribute", resolution),
                "files": written,
                "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            maps = {}
            for rel in written:
                mt = detect_map_type(rel)
                if mt and (mt not in maps or (mt == "normal_gl")):
                    maps.setdefault(mt, rel)
            if maps:
                meta["maps"] = maps
            _write_sidecar(folder, meta)
        return final

    @staticmethod
    def _choose_download(downloads: list[dict], resolution: str, file_type: str) -> dict:
        def parse(attr: str) -> tuple[str, str]:
            parts = attr.upper().split("-", 1)
            return (parts[0], parts[1] if len(parts) > 1 else "")

        want_res = resolution.upper()
        ft = file_type.upper()
        by_attr = {d.get("attribute", ""): d for d in downloads}
        exact = by_attr.get(f"{want_res}-{ft}")
        if exact:
            return exact
        cands = []
        for attr, d in by_attr.items():
            r, t = parse(attr)
            type_rank = 0 if t == ft else 1 if t in ("JPG", "PNG") else 2
            cands.append((type_rank, abs(_res_key(r) - _res_key(want_res)), _res_key(r), attr, d))
        cands.sort(key=lambda c: c[:4])
        return cands[0][4]


@contextmanager
def _staged(final: Path):
    """Yield a hidden staging folder next to ``final``; on success it atomically replaces
    ``final``, on any error it is deleted (no half-downloaded assets in the library)."""
    final.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{final.name}.partial-", dir=final.parent))
    try:
        yield stage
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    if final.exists():
        backup = final.with_name(f".{final.name}.old-{os.getpid()}")
        final.replace(backup)
        stage.replace(final)
        shutil.rmtree(backup, ignore_errors=True)
    else:
        stage.replace(final)


def _safe_extract_zip(zpath: Path, dest: Path) -> list[str]:
    """Extract a zip refusing absolute/parent paths, symlinks and zip bombs."""
    out = []
    with zipfile.ZipFile(zpath) as zf:
        total = sum(i.file_size for i in zf.infolist())
        if total > _MAX_ZIP_BYTES:
            raise AssetSourceError(f"zip too large to extract ({total} bytes)")
        for info in zf.infolist():
            if info.is_dir():
                continue
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                continue  # symlink
            rel = _safe_relpath(info.filename)
            target = dest.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            out.append(rel.as_posix())
    return out


def get_source(name: str, **kw):
    name = name.lower()
    if name in ("polyhaven", "poly_haven", "ph"):
        return PolyHavenSource(**kw)
    if name in ("ambientcg", "acg"):
        return AmbientCGSource(**kw)
    raise ValueError(f"unknown source {name!r}")


# ----------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m envgen.assets.sources",
                                 description="Search / download CC0 assets (Poly Haven, ambientCG).")
    ap.add_argument("source", choices=["polyhaven", "ambientcg"])
    ap.add_argument("action", choices=["search", "download"])
    ap.add_argument("term", help="search query, or asset id for download")
    ap.add_argument("--kind", choices=["material", "model", "hdri"], default=None)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--res", default="2k")
    ap.add_argument("--dest", type=Path, default=None,
                    help="library root (default $ENVGEN_ASSET_LIBRARY or <repo>/asset_library)")
    ap.add_argument("--categories", default=None, help="Poly Haven categories filter")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    src = get_source(args.source, library_root=args.dest, timeout=args.timeout)
    try:
        if args.action == "search":
            kw = {"categories": args.categories} if args.source == "polyhaven" else {}
            res = src.search(args.term, kind=args.kind or "material", limit=args.limit, **kw)
            if args.json:
                print(json.dumps([r.to_dict() for r in res], indent=1))
            else:
                for r in res:
                    print(f"{r.id:36s} {r.kind:8s} {r.name[:40]:40s} {','.join(r.tags[:6])}")
        else:
            if args.source == "polyhaven":
                folder = src.download(args.term, args.res, args.dest, kind=args.kind)
            else:
                folder = src.download(args.term, args.res, args.dest)
            print(folder)
    except AssetSourceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
