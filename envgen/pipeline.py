"""High-level pipeline entry points shared by the CLI and the QA loop."""
from __future__ import annotations

import json
from pathlib import Path


def build_and_validate(spec: dict, out_dir: Path | None = None, save_blend: bool = False) -> dict:
    from .blender.build import build_scene
    from .blender.validate import validate_scene

    report = build_scene(spec)
    ctx = report.pop("_ctx")
    report["validation"] = validate_scene(ctx)
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "build_report.json").write_text(json.dumps(report, indent=2, default=str))
        if save_blend:
            import bpy
            bpy.ops.wm.save_as_mainfile(filepath=str(out_dir / f"{spec['name']}.blend"), compress=True)
    report["_ctx"] = ctx
    return report


def render_spec(spec: dict, out_dir: Path, quality: str = "preview", shots: list[str] | None = None,
                save_blend: bool = False) -> dict[str, str]:
    """Build, validate and render ``spec``. Returns {shot: raw_png_path}. Raises on validation errors."""
    from .blender import render

    report = build_and_validate(spec, out_dir, save_blend=save_blend)
    val = report["validation"]
    if not val["ok"]:
        raise RuntimeError(f"scene validation failed: {json.dumps(val['errors'], indent=1)}")
    render.configure(spec, quality)
    results = render.render_shots(out_dir, shots)
    (out_dir / "render_times.json").write_text(json.dumps(results, indent=2))
    return {shot: r["raw"] for shot, r in results.items()}
