"""envgen command line.

    envgen analyze-refs  --indices 08 09 15 --name hospital_corridor   # reference profiles + target
    envgen validate      specs/hospital_corridor.yaml                  # build + physical checks (no render)
    envgen build         specs/hospital_corridor.yaml --blend          # build, validate, save .blend
    envgen render        specs/hospital_corridor.yaml --quality final  # render + grade all shots
    envgen refine        specs/hospital_corridor.yaml --critic auto    # autonomous QA loop
    envgen score         output/x/hero.jpg --target references/targets/hospital_corridor.json
    envgen patch         SPEC PATCH.yaml --out NEW_SPEC.yaml           # apply a critic/agent patch
    envgen export-unreal specs/hospital_corridor.yaml                  # GLB + manifest for UE5
    envgen assets ...                                                  # asset library (scan/search/select)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import get_settings


def _out_dir(spec: dict, sub: str, override: str | None) -> Path:
    if override:
        return Path(override)
    return get_settings().output_dir / spec["name"] / f"{sub}_{time.strftime('%Y%m%d_%H%M%S')}"


def cmd_analyze_refs(a) -> int:
    from .vision.profile import aggregate_profiles, compute_profile
    root = get_settings().repo_root
    images: list[Path] = [Path(p) for p in a.images or []]
    if a.indices:
        index = dict(line.split(" ", 1) for line in (root / "references/index.txt").read_text().splitlines() if line)
        images += [root / index[i] for i in a.indices]
    if not images:
        print("no images given (use --images or --indices)", file=sys.stderr)
        return 2
    profiles = []
    pdir = get_settings().references_dir / "profiles"
    pdir.mkdir(parents=True, exist_ok=True)
    for img in images:
        prof = compute_profile(str(img))
        profiles.append(prof)
        print(f"{img.name[:24]:24s} lum={prof['luminance']['mean']:.3f} rms={prof['contrast']['rms']:.3f} "
              f"warm={prof['color']['warmth']:+.3f} haze={prof['haze']['dark_channel_mean']:.3f} "
              f"vp={prof['structure']['vanishing_point']}")
    target = aggregate_profiles(profiles)
    out = get_settings().references_dir / "targets" / f"{a.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(target, indent=1))
    print(f"target written: {out}")
    return 0


def _load(spec_path: str, seed: int | None = None) -> dict:
    from .spec.schema import load_spec
    spec = load_spec(spec_path)
    if seed is not None:
        spec["seed"] = seed
    return spec


def cmd_validate(a) -> int:
    from .pipeline import build_and_validate
    spec = _load(a.spec, a.seed)
    rep = build_and_validate(spec)
    v = rep["validation"]
    print(json.dumps({"ok": v["ok"], "errors": v["errors"], "warnings": v["warnings"], "stats": v["stats"],
                      "walkable_lane": rep["story"].get("walkable_lane")}, indent=1))
    return 0 if v["ok"] else 1


def cmd_build(a) -> int:
    from .pipeline import build_and_validate
    spec = _load(a.spec, a.seed)
    out = _out_dir(spec, "build", a.out)
    rep = build_and_validate(spec, out, save_blend=a.blend)
    print(f"built {spec['name']} in {rep['seconds']}s: {rep['objects']} objects, {rep['triangles']} tris; "
          f"validation ok={rep['validation']['ok']} -> {out}")
    return 0 if rep["validation"]["ok"] else 1


def cmd_render(a) -> int:
    from .pipeline import render_spec
    from .post.grade import grade_file
    from .spec.schema import save_spec
    spec = _load(a.spec, a.seed)
    out = _out_dir(spec, a.quality, a.out)
    raw = render_spec(spec, out, a.quality, a.shots, save_blend=a.blend)
    save_spec(spec, out / "spec.yaml")
    for shot, p in raw.items():
        g = grade_file(p, out / f"{shot}.jpg", spec["post"], seed=int(spec["seed"]))
        print(f"{shot}: {g}")
    return 0


def cmd_refine(a) -> int:
    from .qa.loop import run
    spec = _load(a.spec, a.seed)
    if a.max_iterations:
        spec["qa"]["max_iterations"] = a.max_iterations
    out = _out_dir(spec, "refine", a.out)
    summary = run(spec, out, get_settings().repo_root, critic_mode=a.critic, quality=a.quality, shots=a.shots)
    print(json.dumps({k: summary[k] for k in ("stop_reason", "best_iteration", "best_score", "iterations")}, indent=1))
    print(f"report: {out / 'QA_REPORT.md'}   best: {out / 'best'}")
    return 0


def cmd_score(a) -> int:
    from .qa.metrics import score_render
    from .vision.profile import compute_profile
    target = json.loads(Path(a.target).read_text())
    rep = score_render(compute_profile(a.image, crop_letterbox=False), target)
    print(json.dumps({"overall": rep["overall"], "scores": rep["scores"],
                      "issues": [(i["code"], round(i["severity"], 2)) for i in rep["issues"]]}, indent=1))
    return 0


def cmd_patch(a) -> int:
    import yaml

    from .qa.critic import all_bounds, sanitize_patch
    from .spec.schema import apply_patch, save_spec
    spec = _load(a.spec)
    raw = yaml.safe_load(Path(a.patch).read_text())
    ops = raw.get("patch", raw) if isinstance(raw, dict) else raw
    if a.unrestricted:
        safe, rejected = ops, []
    else:
        safe, rejected = sanitize_patch(ops)
    for r in rejected:
        print(f"rejected: {r}", file=sys.stderr)
    new = apply_patch(spec, [{k: o[k] for k in ("op", "path", "value")} for o in safe], bounds=all_bounds())
    save_spec(new, a.out)
    print(f"applied {len(safe)} ops -> {a.out}")
    return 0


def cmd_export_unreal(a) -> int:
    from .export.unreal import export_for_unreal
    from .pipeline import build_and_validate
    spec = _load(a.spec, a.seed)
    out = _out_dir(spec, "unreal", a.out)
    build_and_validate(spec)
    manifest = export_for_unreal(out, {"name": spec["name"], "atmosphere": spec["atmosphere"], "post": spec["post"]})
    print(f"manifest: {manifest}")
    return 0


def cmd_assets(a) -> int:
    from .assets.registry import main as assets_main
    return assets_main(a.rest) or 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="envgen", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("analyze-refs", help="profile reference images and write an aggregate QA target")
    s.add_argument("--images", nargs="*")
    s.add_argument("--indices", nargs="*", help="indices from references/index.txt")
    s.add_argument("--name", required=True)
    s.set_defaults(fn=cmd_analyze_refs)

    for name, fn, hlp in (("validate", cmd_validate, "build + physical validation"),
                          ("build", cmd_build, "build scene (optionally save .blend)"),
                          ("render", cmd_render, "build, render and grade"),
                          ("refine", cmd_refine, "autonomous render-inspect-modify loop"),
                          ("export-unreal", cmd_export_unreal, "export GLB + manifest for UE5")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("spec")
        s.add_argument("--seed", type=int)
        s.add_argument("--out")
        if name in ("build", "render"):
            s.add_argument("--blend", action="store_true", help="also save the .blend file")
        if name in ("render", "refine"):
            s.add_argument("--quality", choices=["preview", "final"], default="preview")
            s.add_argument("--shots", nargs="*")
        if name == "refine":
            s.add_argument("--critic", choices=["auto", "anthropic", "file", "none"], default="auto")
            s.add_argument("--max-iterations", type=int)
        s.set_defaults(fn=fn)

    s = sub.add_parser("score", help="score an image against a reference target")
    s.add_argument("image")
    s.add_argument("--target", required=True)
    s.set_defaults(fn=cmd_score)

    s = sub.add_parser("patch", help="apply a patch (critic/agent output) to a spec")
    s.add_argument("spec")
    s.add_argument("patch")
    s.add_argument("--out", required=True)
    s.add_argument("--unrestricted", action="store_true", help="allow any path (still validated + bounded)")
    s.set_defaults(fn=cmd_patch)

    s = sub.add_parser("assets", help="asset library commands (see envgen.assets.registry)")
    s.add_argument("rest", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_assets)

    a = p.parse_args(argv)
    return int(a.fn(a) or 0)


if __name__ == "__main__":
    sys.exit(main())
