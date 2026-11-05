"""Generated signage / document textures (original artwork via PIL) and UV-mapped decal quads.

Text and graphics are generated procedurally so the system never depends on
copyrighted imagery. Textures are cached per scene build in the output cache dir.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

import bpy
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..config import get_settings
from .materials.library import make_image_material
from .util import tag

_FONT_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", str(Path.home() / ".fonts"),
              "/Library/Fonts", "C:/Windows/Fonts"]
_FONT_CACHE: dict[str, str | None] = {}


def _font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    key = "bold" if bold else "regular"
    if key not in _FONT_CACHE:
        wanted = ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"] if bold else \
            ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf", "arial.ttf"]
        found = None
        for d in _FONT_DIRS:
            p = Path(d)
            if not p.exists():
                continue
            for w in wanted:
                hits = list(p.rglob(w))
                if hits:
                    found = str(hits[0])
                    break
            if found:
                break
        _FONT_CACHE[key] = found
    path = _FONT_CACHE[key]
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def cache_dir() -> Path:
    d = get_settings().output_dir / "_cache" / "decals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _age(img: Image.Image, rng: random.Random, amount: float = 0.5) -> Image.Image:
    """Fade, yellow and blotch an image so it reads as years old."""
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for _ in range(int(14 * amount) + 2):
        r = rng.randint(w // 12, w // 3)
        x, y = rng.randint(-r, w), rng.randint(-r, h)
        d.ellipse((x, y, x + r, y + int(r * rng.uniform(0.5, 1.5))),
                  fill=(120, 95, 50, rng.randint(15, int(70 * amount) + 16)))
    overlay = overlay.filter(ImageFilter.GaussianBlur(w / 60))
    out = Image.alpha_composite(img.convert("RGBA"), overlay)
    fade = Image.new("RGBA", (w, h), (225, 215, 190, int(60 * amount)))
    return Image.alpha_composite(out, fade).convert("RGB")


def door_plate(label: str, seed: int = 0) -> Path:
    p = cache_dir() / f"plate_{label}.png"
    if p.exists():
        return p
    img = Image.new("RGB", (512, 200), (32, 74, 82))
    d = ImageDraw.Draw(img)
    d.rectangle((8, 8, 503, 191), outline=(220, 225, 220), width=4)
    d.text((256, 82), label, font=_font(96), fill=(235, 238, 232), anchor="mm")
    d.text((256, 160), "WARD  ·  RECOVERY", font=_font(30, bold=False), fill=(200, 210, 205), anchor="mm")
    _age(img, random.Random(seed), 0.6).save(p)
    return p


def exit_sign() -> Path:
    p = cache_dir() / "exit.png"
    if p.exists():
        return p
    img = Image.new("RGB", (512, 220), (20, 4, 3))
    d = ImageDraw.Draw(img)
    d.text((300, 110), "EXIT", font=_font(130), fill=(255, 40, 25), anchor="mm")
    d.polygon([(40, 110), (100, 60), (100, 90), (150, 90), (150, 130), (100, 130), (100, 160)], fill=(255, 40, 25))
    img.save(p)
    return p


def poster(kind: str, seed: int) -> Path:
    """Original public-health style posters (abstract graphics + generic slogans)."""
    p = cache_dir() / f"poster_{kind}_{seed}.png"
    if p.exists():
        return p
    rng = random.Random(seed)
    palettes = [((200, 60, 45), (245, 240, 225)), ((40, 90, 140), (240, 238, 228)), ((60, 120, 80), (238, 236, 220))]
    accent, bg = palettes[seed % len(palettes)]
    img = Image.new("RGB", (600, 850), bg)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 600, 190), fill=accent)
    slogans = {"hygiene": ("CLEAN HANDS", "SAVE LIVES"), "quiet": ("PLEASE", "KEEP QUIET"),
               "visiting": ("VISITING", "HOURS"), "evac": ("FIRE", "ASSEMBLY")}
    a, b = slogans.get(kind, ("NOTICE", "STAFF ONLY"))
    d.text((300, 70), a, font=_font(70), fill=bg, anchor="mm")
    d.text((300, 145), b, font=_font(52), fill=bg, anchor="mm")
    cx, cy = 300, 430
    for k in range(5):  # abstract emblem: concentric arcs + cross
        r = 150 - k * 26
        d.arc((cx - r, cy - r, cx + r, cy + r), start=rng.randint(0, 90), end=rng.randint(200, 340), fill=accent,
              width=9)
    d.rectangle((cx - 18, cy - 70, cx + 18, cy + 70), fill=accent)
    d.rectangle((cx - 70, cy - 18, cx + 70, cy + 18), fill=accent)
    for i in range(6):
        y = 640 + i * 30
        d.rectangle((60, y, 60 + rng.randint(300, 480), y + 12), fill=(90, 85, 80))
    img = _age(img, rng, 0.9)
    # torn corner
    mask = Image.new("L", img.size, 255)
    md = ImageDraw.Draw(mask)
    md.polygon([(600, 850), (600, 700 + rng.randint(0, 80)), (470 + rng.randint(0, 60), 850)], fill=0)
    out = img.convert("RGBA")
    out.putalpha(mask)
    out.save(p)
    return p


def document(seed: int, form: bool = True) -> Path:
    p = cache_dir() / f"doc_{seed}.png"
    if p.exists():
        return p
    rng = random.Random(seed)
    img = Image.new("RGB", (420, 594), (236, 232, 220))
    d = ImageDraw.Draw(img)
    d.rectangle((30, 30, 390, 70), outline=(60, 60, 70), width=2)
    d.text((40, 38), "PATIENT RECORD", font=_font(22), fill=(40, 40, 55))
    y = 95
    while y < 560:
        if form and rng.random() < 0.3:
            d.rectangle((30, y, 390, y + 34), outline=(120, 120, 130), width=1)
            y += 44
            continue
        d.rectangle((30, y, 30 + rng.randint(120, 360), y + 6), fill=(70, 70, 80))
        y += 16 + rng.randint(0, 8)
    if rng.random() < 0.5:  # handwritten scrawl
        pts = [(60 + i * 12, 480 + 10 * math.sin(i * 1.3 + seed)) for i in range(24)]
        d.line(pts, fill=(30, 40, 110), width=3)
    _age(img, rng, rng.uniform(0.4, 1.0)).save(p)
    return p


def image_quad(name: str, image: Path, center, size: tuple[float, float], normal: str, coll,
               roughness: float = 0.7, emission: float = 0.0, alpha: bool = False, rotation_deg: float = 0.0,
               grime: float = 0.35) -> bpy.types.Object:
    """UV-mapped rectangle facing ``normal`` in {+x,-x,+y,-y,+z} with an aged image material."""
    w, h = size
    r = math.radians(rotation_deg)
    corners2d = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    corners2d = [(u * math.cos(r) - v * math.sin(r), u * math.sin(r) + v * math.cos(r)) for u, v in corners2d]
    cx, cy, cz = center
    # image-right must map to viewer-right when looking *against* the normal
    if normal == "+x":
        verts = [(cx, cy + u, cz + v) for u, v in corners2d]
    elif normal == "-x":
        verts = [(cx, cy - u, cz + v) for u, v in corners2d]
    elif normal == "-y":
        verts = [(cx + u, cy, cz + v) for u, v in corners2d]
    elif normal == "+y":
        verts = [(cx - u, cy, cz + v) for u, v in corners2d]
    else:
        verts = [(cx + u, cy + v, cz) for u, v in corners2d]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name="UVMap")
    for i, loop in enumerate(mesh.loops):
        uv.data[i].uv = [(0, 0), (1, 0), (1, 1), (0, 1)][loop.vertex_index]
    mesh.materials.append(make_image_material(f"{name}_mat", str(image), roughness, emission, grime, alpha))
    obj = bpy.data.objects.new(name, mesh)
    coll.objects.link(obj)
    tag(obj, role="decal")
    return obj
