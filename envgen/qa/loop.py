"""Autonomous artistic refinement loop.

    generate -> render QA shots -> grade -> profile -> score vs reference target
             -> (optional) vision critique -> propose bounded patch -> apply -> repeat

Stopping criteria (whichever comes first):
    * target_reached   combined score >= qa.target_score
    * max_iterations   hard cap (qa.max_iterations, itself capped at 12 by spec validation)
    * plateau          no improvement >= qa.min_improvement for qa.patience consecutive iterations
    * no_actionable    neither metrics nor critic produced an applicable patch

The best iteration (not the last) is kept. Every iteration writes its spec, patch,
scores, issues and images, so the run is fully auditable and reproducible.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from ..post.grade import grade_file
from ..spec.schema import apply_patch, save_spec, spec_hash
from ..vision.profile import compute_profile
from . import metrics
from .critic import AnthropicCritic, CriticUnavailable, FileCritic, all_bounds, write_review_packet
from .fixers import BOUNDS, needs_rerender, propose_fixes


def resolve_references(spec: dict, root: Path) -> list[Path]:
    index = {}
    idx_file = root / "references" / "index.txt"
    if idx_file.exists():
        for line in idx_file.read_text().splitlines():
            if line.strip():
                k, v = line.split(" ", 1)
                index[k] = root / v
    out = []
    for r in spec.get("references", []):
        p = index.get(str(r)) or (root / str(r))
        if p.exists():
            out.append(p)
    return out


def load_target(spec: dict, root: Path) -> dict:
    t = spec.get("reference_target")
    if not t:
        raise FileNotFoundError("spec.reference_target is not set; run `envgen analyze-refs` first")
    p = Path(t) if Path(t).is_absolute() else root / t
    return json.loads(p.read_text())


def _make_critic(mode: str, iter_dir: Path):
    if mode == "none":
        return None
    if mode == "file":
        return FileCritic(iter_dir / "critique.json")
    if mode in ("anthropic", "auto"):
        try:
            return AnthropicCritic()
        except CriticUnavailable as e:
            if mode == "anthropic":
                raise
            print(f"[qa] vision critic unavailable ({e}); continuing with metric QA + review packets")
    return None


def evaluate(images: dict[str, Path], target: dict) -> dict:
    """Score each graded shot against the target; the combined metric score is the mean."""
    per_shot = {}
    for shot, img in images.items():
        prof = compute_profile(str(img), crop_letterbox=False)
        per_shot[shot] = {"profile": prof, "report": metrics.score_render(prof, target)}
    overall = sum(v["report"]["overall"] for v in per_shot.values()) / max(1, len(per_shot))
    return {"overall": overall, "shots": per_shot}


def run(spec: dict, out_dir: Path, root: Path, critic_mode: str = "auto", quality: str = "preview",
        shots: list[str] | None = None, builder=None) -> dict:
    """Run the refinement loop. ``builder(spec, raw_dir, quality, shots) -> {shot: raw_png}`` renders a spec."""
    if builder is None:
        from ..pipeline import render_spec as builder  # lazy: imports bpy
    qa = spec["qa"]
    target = load_target(spec, root)
    refs = resolve_references(spec, root)
    out_dir.mkdir(parents=True, exist_ok=True)
    shots = shots or qa.get("shots") or [spec["camera"]["shots"][0]["name"]]
    history: list[dict] = []
    best = None
    stale = 0
    stop_reason = "max_iterations"
    raw: dict[str, str] = {}
    bounds = all_bounds()
    current = spec

    for it in range(int(qa["max_iterations"])):
        t0 = time.time()
        it_dir = out_dir / f"iter_{it:02d}"
        it_dir.mkdir(parents=True, exist_ok=True)
        save_spec(current, it_dir / "spec.yaml")
        rerender = it == 0 or needs_rerender(history[-1]["applied_patch"])
        if rerender:
            raw = builder(current, it_dir, quality, shots)
        else:  # post-only change: re-grade the previous iteration's raw renders (no path tracing needed)
            for shot, p in raw.items():
                shutil.copy(p, it_dir / Path(p).name)
            raw = {s: str(it_dir / Path(p).name) for s, p in raw.items()}
        graded = {s: grade_file(p, it_dir / f"{s}.jpg", current["post"], seed=int(current["seed"]))
                  for s, p in raw.items()}
        ev = evaluate(graded, target)
        hero = shots[0]
        hero_report = ev["shots"][hero]["report"]

        critique = None
        critic = _make_critic(critic_mode, it_dir)
        if critic is not None:
            try:
                critique = critic.critique(graded[hero], refs, current, hero_report)
            except CriticUnavailable as e:
                print(f"[qa] critic skipped this iteration: {e}")
        if critique is None:
            write_review_packet(it_dir, graded[hero], refs, current, hero_report)

        score = ev["overall"] if critique is None else 0.5 * ev["overall"] + 0.5 * critique["score"]
        fixes = propose_fixes(hero_report["issues"], current)
        patch = list(fixes["ops"])
        reasons = list(fixes["reasons"])
        if critique:
            metric_paths = {op["path"] for op in patch}
            for op in critique["patch"]:
                if op["path"] not in metric_paths:  # metrics own the paths they touch: no double correction
                    patch.append({k: op[k] for k in ("op", "path", "value")})
                    reasons.append(f"critic: {op.get('reason', '')}")

        record = {
            "iteration": it, "spec_hash": spec_hash(current), "rerendered": rerender,
            "score": round(score, 4), "metric_score": round(ev["overall"], 4),
            "critic_score": None if critique is None else round(critique["score"], 4),
            "criteria": {s: v["report"]["scores"] for s, v in ev["shots"].items()},
            "issues": [{k: i[k] for k in ("code", "criterion", "severity", "message")} for i in hero_report["issues"]],
            "critique": critique, "unresolved": [i["code"] for i in fixes["unresolved"]],
            "images": {s: str(p) for s, p in graded.items()}, "raw": raw,
            "proposed_patch": patch, "reasons": reasons, "seconds": round(time.time() - t0, 1),
        }
        improved = best is None or score >= best["score"] + float(qa["min_improvement"])
        if best is None or score > best["score"]:
            best = {"score": score, "iteration": it, "spec": current, "images": graded}
        stale = 0 if improved else stale + 1
        print(f"[qa] iter {it}: score={score:.3f} (metric {ev['overall']:.3f}"
              f"{'' if critique is None else f', critic {critique['score']:.3f}'}) issues="
              f"{[i['code'] for i in hero_report['issues']]} -> {len(patch)} ops")

        if score >= float(qa["target_score"]):
            stop_reason = "target_reached"
        elif stale >= int(qa["patience"]):
            stop_reason = "plateau"
        elif not patch:
            stop_reason = "no_actionable_fixes"
        elif it == int(qa["max_iterations"]) - 1:
            stop_reason = "max_iterations"
        else:
            stop_reason = ""
        record["applied_patch"] = [] if stop_reason else patch
        history.append(record)
        (it_dir / "iteration.json").write_text(json.dumps(record, indent=2, default=str))
        if stop_reason:
            break
        current = apply_patch(current, patch, bounds={**BOUNDS, **bounds})

    best_dir = out_dir / "best"
    best_dir.mkdir(exist_ok=True)
    save_spec(best["spec"], best_dir / "spec.yaml")
    for s, p in best["images"].items():
        shutil.copy(p, best_dir / f"{s}.jpg")
    summary = {"stop_reason": stop_reason, "best_iteration": best["iteration"], "best_score": round(best["score"], 4),
               "iterations": len(history), "history": [{k: h[k] for k in ("iteration", "score", "metric_score",
                                                                          "critic_score", "rerendered", "reasons",
                                                                          "unresolved", "seconds")}
                                                         for h in history]}
    (out_dir / "qa_log.json").write_text(json.dumps(summary, indent=2, default=str))
    _write_report(out_dir, summary, history)
    return summary


def _write_report(out_dir: Path, summary: dict, history: list[dict]) -> None:
    lines = [f"# QA refinement log\n", f"- stop reason: **{summary['stop_reason']}**",
             f"- best iteration: {summary['best_iteration']} (score {summary['best_score']})\n",
             "| iter | score | metric | critic | re-rendered | changes |", "|---|---|---|---|---|---|"]
    for h in history:
        critic = "-" if h["critic_score"] is None else f"{h['critic_score']:.3f}"
        lines.append(f"| {h['iteration']} | {h['score']:.3f} | {h['metric_score']:.3f} | {critic} | "
                     f"{'yes' if h['rerendered'] else 'grade only'} | {'; '.join(h['reasons']) or '-'} |")
    for h in history:
        lines.append(f"\n## Iteration {h['iteration']}\n")
        for s, crit in h["criteria"].items():
            lines.append(f"- **{s}**: " + ", ".join(f"{k} {v:.2f}" for k, v in crit.items()))
        if h["issues"]:
            lines.append("- issues: " + "; ".join(f"{i['code']} ({i['severity']:.2f})" for i in h["issues"]))
        if h["unresolved"]:
            lines.append("- needs artistic review: " + ", ".join(h["unresolved"]))
    (out_dir / "QA_REPORT.md").write_text("\n".join(lines) + "\n")
