---
name: generate-environment
description: End-to-end workflow to generate a photoreal environment from reference images with envgen - analyze references, author/validate a spec, run the autonomous QA refinement loop, final render. Use when asked to create, regenerate or improve an environment.
---
# Generate an environment

All commands run from the repo root with the project venv (`.venv/bin/python -m envgen.cli ...`, or `envgen`
if installed with `uv pip install -e .`).

1. **References** - put images in `pics/`, add `NN path` lines to `references/index.txt`, then run the
   `analyze-references` skill. Output: `references/targets/<cluster>.json`.
2. **Spec** - copy `specs/hospital_corridor.yaml` as a template. Set `references`, `reference_target`, layout,
   materials overrides, lighting, story beats, camera shots. Keep every dimension real-world.
3. **Validate** (seconds, no render): `envgen validate specs/<name>.yaml`. Fix every error; review warnings
   (scale, interpenetration, outside_walls, camera_blocked).
4. **Quick look** (about 1-2 min per shot at preview quality on 4 CPU cores):
   `envgen render specs/<name>.yaml --shots hero`. LOOK at `output/<name>/preview_*/hero.jpg`.
5. **Refine** autonomously: `envgen refine specs/<name>.yaml --critic auto --shots hero`.
   - With `ANTHROPIC_API_KEY` set, a Claude vision critic scores each iteration against the references.
   - Without one, metric QA runs and each iteration gets a `review_packet.json`; use the `qa-review` skill to
     act as the critic, then `envgen patch` + refine again.
   - Stops on target_reached / plateau / max_iterations (hard cap 12). Read `QA_REPORT.md`.
6. **Final**: `envgen render <best spec> --quality final --blend` (1920x1080, 160 spp, all shots, .blend saved).

Quality bar: no floating objects, no repeated props in one view, motivated lights only, story beats explain
every prop, measured score >= 0.85 and no critique issue of severity >= 4.
