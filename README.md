# envgen — reference-driven 3D environment generation

`envgen` turns a set of reference images into a procedurally built, physically lit 3D environment.
It then improves the environment through a bounded **render → inspect → modify** loop that scores
every iteration against the references.

It runs **headless** with Blender 5.2 as a Python module (`bpy`) and Cycles path tracing on CPU.
An exporter writes GLB files plus a manifest for presentation in **Unreal Engine 5**.

The prototype environment is `specs/hospital_corridor.yaml`, an abandoned hospital ward corridor built
from the reference cluster in `pics/` (see `docs/reference_analysis.md`).

```
pics/ (references) ──► analyze-refs ──► references/targets/*.json   (numeric targets: exposure, contrast, palette, haze, VP …)
                                         docs/reference_analysis.md  (construction requirements)
specs/*.yaml ──► build (architecture → materials → story beats → lights → fog → cameras) ──► validate
            ──► Cycles render ──► numpy grade ──► profile + score ──► fixers / vision critic ──► patch spec ──► repeat
            ──► best iteration + QA_REPORT.md ──► final render (.blend) ──► optional UE5 export
```

## Setup

You need Python 3.13. bpy 5.2 wheels are cp313 only (Linux x86-64, macOS arm64, Windows).

```bash
uv venv .venv --python 3.13
```

```bash
uv pip install --python .venv/bin/python -e ".[vision,dev]"
```

The `bpy` wheel is about 400 MB. Use `pip` if you don't use `uv`. No GPU is needed: Cycles runs on CPU.
EEVEE does **not** work in a headless container without EGL.

Optional environment variables (no machine-specific paths are hard-coded):

| Variable | Default | Purpose |
|---|---|---|
| `ENVGEN_OUTPUT_DIR` | `./output` | Renders, logs, .blend files |
| `ENVGEN_ASSET_LIBRARY` | `./asset_library` | Local asset library (CC0 downloads, your own assets) |
| `ENVGEN_REFERENCES_DIR` | `./references` | Profiles and targets |
| `ENVGEN_RENDER_THREADS` | auto | Cycles CPU threads |
| `ANTHROPIC_API_KEY` | – | Enables the Claude vision critic in the QA loop (optional) |
| `ENVGEN_VISION_MODEL` | `claude-opus-5-5` | Critic model |

## Commands

Run each as `envgen …` after the editable install, or as `.venv/bin/python -m envgen.cli …`.

| Command | What it does |
|---|---|
| `envgen analyze-refs --indices 08 09 16 --name hospital_corridor` | Profiles the reference images and writes `references/targets/hospital_corridor.json` |
| `envgen validate specs/hospital_corridor.yaml` | Builds the scene and checks floating/sunken props, scale, interpenetration, wall penetration, materials, camera clearance and lights. Takes seconds. |
| `envgen build specs/hospital_corridor.yaml --blend` | Builds and validates, and saves a `.blend` file you can open in Blender |
| `envgen render specs/hospital_corridor.yaml [--quality final] [--shots hero]` | Renders and grades. Preview is about 2 min per shot at 960×540 on 4 cores; final is 1920×1080 at 160 spp. |
| `envgen refine specs/hospital_corridor.yaml --critic auto` | Runs the autonomous QA loop (below) |
| `envgen score image.jpg --target references/targets/hospital_corridor.json` | Gives objective scores for any image |
| `envgen patch SPEC critique.json --out NEW.yaml` | Applies a critic or agent patch (allow-listed and bounded) |
| `envgen export-unreal SPEC --out DIR` | Writes a GLB per unique mesh plus `manifest.json` for `unreal/import_envgen_scene.py` |
| `envgen assets scan` / `search` / `select` | Manages the local asset library (scale-aware selection) |
| `python -m envgen.assets.sources polyhaven search concrete` | Downloads CC0 assets with licence sidecars |

Tests run with plain Python (no pytest needed):

```bash
.venv/bin/python tests/test_spec_qa.py
```

```bash
.venv/bin/python tests/test_profile_metrics.py
```

## The autonomous QA loop

Each iteration does the following:
1. Builds the scene from the spec and validates it. Errors abort the iteration.
2. Renders the QA shots and grades them.
3. Profiles each image and scores 7 objective criteria against the reference target: exposure, contrast, colour grade, atmosphere, detail, lighting structure and composition. The target is the measured mean ± std of the cluster.
4. Runs the vision critic:
   - **API mode**: with `ANTHROPIC_API_KEY` set, a Claude vision model compares the render to the references. It scores 9 criteria (composition, architectural accuracy, material realism, lighting, scale, placement, coherence, reference similarity, storytelling) and proposes patches.
   - **Agent mode**: without a key, the loop writes `review_packet.json`, and the Claude Code `qa-artist` agent (skill `qa-review`) acts as the critic.
5. Turns the findings into **bounded, allow-listed spec patches**. Changes that only affect the grade skip re-rendering.
6. Stops on `target_reached`, a `plateau` (no gain ≥ `min_improvement` for `patience` iterations), `no_actionable_fixes` or `max_iterations`, which specs cap at 12. The **best** iteration is kept.

Every iteration writes `spec.yaml`, `iteration.json` (scores, issues, patch, reasons, timings) and its images.
The run writes `qa_log.json` and a human-readable `QA_REPORT.md`.

## Project layout

```
envgen/
  spec/schema.py          spec defaults, validation, patch ops, hashing (reproducibility)
  vision/profile.py       CV profile of an image (luma, contrast, palette, haze, detail, VP, spatial)
  qa/metrics.py           objective scoring against a reference target → issues
  qa/fixers.py            issue → bounded spec patch (damped, conflict-resolving)
  qa/critic.py            Claude vision critic + agent-in-the-loop file critic (same schema)
  qa/loop.py              the iteration loop, stopping criteria, logging
  blender/util.py         MeshBuilder (bmesh primitives with real bevels, multi-material), colour utils
  blender/geometry/architecture.py  corridor shell, openings, blinds, doors, drop ceiling, plenum services
  blender/geometry/props.py         procedurally modelled props at real-world scale (+ nominal dims registry)
  blender/materials/      layered PBR shader builder + presets (peel, grime, streaks, wet, moss, rust)
  blender/story.py        storytelling beats (collapse, evacuation, paper trail, barricade, …)
  blender/lighting.py     motivated lights (sun + portals, fixture states, practicals), volumetric fog
  blender/decals.py       generated signage, posters, documents (original artwork) as UV decals
  blender/asset_import.py glTF/FBX/OBJ/USD/.blend import with scale-to-fit; image-based PBR materials
  blender/validate.py     physical plausibility checks
  post/grade.py           exposure, bloom, lift/gamma/gain, contrast, split-tone, CA, vignette, grain
  assets/                 asset registry (scale-aware) + Poly Haven / ambientCG CC0 downloaders
  export/unreal.py        GLB + manifest exporter (Blender → UE units, axes, light intensities)
unreal/import_envgen_scene.py   UE5 editor import script (untested: no Unreal in CI)
specs/                    environment specs (YAML)
references/               index of reference images, per-image profiles, cluster targets
.claude/agents, .claude/skills  Claude Code specialist agents and workflows
docs/                     architecture, reference analysis, dev log
```

## Using it from Claude Code

- **Agents** (`.claude/agents/`): `env-art-director` (orchestrator), `reference-analyst`, `environment-architect`, `material-artist`, `lighting-artist`, `set-dresser`, `asset-researcher`, `qa-artist`, `unreal-integration`.
- **Skills** (`.claude/skills/`): `generate-environment`, `analyze-references`, `qa-review`, `add-prop`, `export-unreal`.

For example: *"Use the env-art-director agent to build a Soviet metro service level from references 05 and 14."*

To **create a new environment**, see the `generate-environment` skill. In short:
1. Add references and run `analyze-refs`.
2. Copy and edit a spec.
3. Run `validate`, then `render --shots hero` and look at the result.
4. Run `refine`, then render at `--quality final`.

## Status and limitations (honest)

- **Working and tested:** the corridor layout generator, all materials, props, story beats, lighting, fog, decals, validation, Cycles rendering, grading, reference profiling, metric QA, the refinement loop (metric plus agent-file critic), the asset registry/import, and the Blender-side Unreal export.
- **Written but not run here:**
  - The Claude API critic (no API key in the build environment). The code follows the current SDK, and its JSON handling is unit-tested.
  - The Poly Haven/ambientCG downloaders (hosts blocked in the build sandbox; tested against a local mock).
  - The UE5 import script.
- **Only `corridor` layouts are implemented.** `room` and `tunnel` are accepted by the schema but raise `NotImplementedError`.
- **CPU render time is the bottleneck,** at about 2 min per preview shot on 4 cores. Grade-only QA changes avoid re-renders.
- **Vanishing-point detection is unreliable** on very dark images, so the composition score is a soft signal.
