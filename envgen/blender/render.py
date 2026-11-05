"""Cycles render configuration and per-shot rendering (headless, CPU-friendly)."""
from __future__ import annotations

import time
from pathlib import Path

import bpy

from ..config import get_settings


def configure(spec: dict, quality: str = "preview") -> None:
    scene = bpy.context.scene
    r = spec["render"]
    q = r[quality]
    scene.render.engine = "CYCLES"
    cy = scene.cycles
    cy.device = "CPU"
    cy.samples = int(q["samples"])
    cy.use_adaptive_sampling = True
    cy.adaptive_threshold = float(r.get("adaptive_threshold", 0.03))
    cy.use_denoising = True
    cy.denoiser = "OPENIMAGEDENOISE"
    cy.denoising_input_passes = "RGB_ALBEDO_NORMAL"
    cy.use_light_tree = True
    cy.max_bounces = int(r.get("max_bounces", 6))
    cy.diffuse_bounces = min(4, cy.max_bounces)
    cy.glossy_bounces = min(4, cy.max_bounces)
    cy.transmission_bounces = 6
    cy.volume_bounces = int(r.get("volume_bounces", 1))
    cy.transparent_max_bounces = 16
    cy.caustics_reflective = False
    cy.caustics_refractive = False
    cy.blur_glossy = 0.7
    cy.sample_clamp_direct = 0.0
    cy.sample_clamp_indirect = float(r.get("clamp_indirect", 8.0))
    cy.volume_step_rate = float(r.get("volume_step_rate", 2.0))
    cy.volume_max_steps = 256
    threads = get_settings().render_threads
    scene.render.threads_mode = "FIXED" if threads else "AUTO"
    if threads:
        scene.render.threads = threads
    w, h = q["resolution"]
    scene.render.resolution_x, scene.render.resolution_y = int(w), int(h)
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.use_persistent_data = True
    vs = scene.view_settings
    vs.view_transform = spec["lighting"].get("view_transform", "AgX")
    look = spec["lighting"].get("look", "None")
    try:
        vs.look = look
    except TypeError:
        vs.look = "None"
    vs.exposure = float(spec["lighting"].get("exposure", 0.0))
    vs.gamma = 1.0
    scene.display_settings.display_device = "sRGB"
    img = scene.render.image_settings
    if hasattr(img, "media_type"):
        img.media_type = "IMAGE"
    img.file_format = "PNG"
    img.color_depth = "16"
    img.color_mode = "RGB"


def render_shots(out_dir: Path, shots: list[str] | None = None) -> dict[str, dict]:
    """Render each camera tagged as a shot to ``out_dir/raw_<shot>.png``. Returns timings/paths."""
    scene = bpy.context.scene
    out_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for cam in [o for o in scene.objects if o.type == "CAMERA"]:
        shot = cam.get("envgen_shot", cam.name)
        if shots and shot not in shots:
            continue
        scene.camera = cam
        path = out_dir / f"raw_{shot}.png"
        scene.render.filepath = str(path)
        t = time.time()
        bpy.ops.render.render(write_still=True)
        results[shot] = {"raw": str(path), "seconds": round(time.time() - t, 1)}
    return results
