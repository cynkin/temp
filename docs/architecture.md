# Architecture and technology decision

## Decision: Blender-first, Unreal as an optional presentation target

| Criterion | A. Blender-only | B. Unreal-only | C. Hybrid (chosen: Blender core + UE export) |
|---|---|---|---|
| Visual quality | Cycles path tracing: correct GI, volumetrics, glossy and transmissive light, and caustic-free glass shadows via shadow-ray tricks. Offline quality. | Lumen/Nanite/VSM: excellent real-time quality, massive geometry | Cycles for iteration and offline frames; UE for real-time if wanted |
| Automation | `bpy` is a pip-installable module. Fully headless, deterministic, every part of the scene is scriptable. | Editor Python needs a running editor (GPU, Epic account, large install). Commandlet imports are partly asynchronous. | Automation lives in Blender; UE import is one script |
| Development complexity | Low: one process, one language | High: C++/Blueprint/Python, editor lifecycle | Medium: the UE side is one optional script |
| Iteration speed | About 2 min per 960×540 preview shot on 4 CPU cores; grade-only edits are instant | Real-time on a GPU, but there is no GPU here | Same as A |
| Deliverables | Renders, .blend, GLB/FBX/USD | Levels, real-time builds | Both |
| Can run in this environment | **Yes** (verified: bpy 5.2.3, Cycles CPU, OIDN) | **No** (no GPU, UE not installable from this network) | Blender part yes, UE part needs the user's machine |

The autonomous QA loop needs to render, inspect and modify without a human, many times, on whatever machine is
available. Blender-as-a-module is the only option that does that end to end here. Unreal adds real value for
final real-time presentation, so the exporter and UE import script exist. They are kept outside the critical
path, so their being untested here does not block the system.

### MCP evaluation (researched November 2026)

- **ahujasid/mcp-for-blender** (MIT, very active): socket add-on inside a running Blender GUI. Good for
  interactive sessions (live scene inspection, Poly Haven/Sketchfab search). Not used in the pipeline because it
  needs a GUI Blender and adds a process boundary that headless `bpy` does not need.
- **Blender Lab official MCP** (experimental, Blender 5.1+, has CLI/background variants): promising for
  headless use, but its host was unreachable from the build sandbox. Re-evaluate later.
- **Epic Unreal MCP** (UE 5.8, experimental, editor-only) and **chongdashu/unreal-mcp** (experimental, stale):
  useful interactively; for scripted import, Python editor scripting is simpler and more robust.

The decision is **not to install any MCP server by default**. To drive a GUI Blender interactively from Claude Code,
add `mcp-for-blender` yourself (`uvx mcp-for-blender`, with telemetry turned off). The envgen CLI does not depend on it.

## Data flow

```
references (pics/)
   │  vision/profile.py  (CV metrics; letterbox crop; k-means Lab palette; vanishing point via Hough voting)
   ▼
references/targets/<cluster>.json   ◄── numeric target = mean/std/min/max per metric
   │
spec (YAML) ── schema.validate ──► blender/build.py
   │                                 ├─ materials (presets ⊕ spec overrides → layered Cycles shaders)
   │                                 ├─ architecture → ArchContext (openings, collapse zones, fixtures, bulkheads)
   │                                 ├─ story beats (use ArchContext; footprint registry; walkable lane)
   │                                 ├─ lighting (sun + portals, fixture states, practicals) + fog volume
   │                                 └─ cameras
   ▼
validate.py (floating/sunken, scale, interpenetration, walls, materials, camera, lights)
   ▼
render.py (Cycles CPU, adaptive sampling, OIDN, light tree, AgX) → raw 16-bit PNG
   ▼
post/grade.py (exposure, bloom, LGG, contrast, split-tone, CA, vignette, grain) → graded JPG
   ▼
qa/metrics.py → issues ──► qa/fixers.py (bounded, damped, conflict-resolving)
qa/critic.py (Claude vision, or the Claude Code qa-artist agent through review packets) ──► allow-listed patch
   ▼
spec.apply_patch (validated, clamped) → next iteration     [stop: target / plateau / no-fix / max]
```

## Design principles

1. **The spec is the single source of truth.** Scenes are deterministic functions of (spec, seed). The QA loop
   changes scenes only through patches, so every iteration can be reproduced and diffed.
2. **Causal placement.** Architecture records *why* things are where they are (`ArchContext`); lighting and
   story beats consume it. Debris sits under the ceiling holes it fell from, puddles sit under the leak, and the
   caution sign is next to the puddle.
3. **Real-world scale everywhere.** Props declare nominal dimensions, and the validator compares them against
   actual geometry. Asset selection is scale-aware.
4. **Wear has a cause.** Every material layer is masked by a physical driver: AO cavities, floor proximity,
   vertical water paths, upward normals, metal edges. Two noise scales (macro and micro) plus a per-object
   random offset prevent visible tiling and repetition.
5. **Motivated light only.** Every light has a visible source; fixture states tell part of the story.
6. **Bounded autonomy.** Numeric bounds on every patchable parameter, an allow-list for critic patches, an
   iteration cap, plateau detection, and keeping the best iteration rather than the last.

## Extending

- **New layout type:** add a builder that returns an `ArchContext` and dispatch it in `blender/build.py`.
- **New prop:** use `@prop(category, nominal_dims, tags)` in `geometry/props.py` (see the `add-prop` skill).
- **New story beat:** add a function to `BEATS` in `blender/story.py`.
- **New material:** add a recipe in `materials/presets.py`, or a scanned texture set via the asset library.
- **New QA metric:** extend `vision/profile.py` and `qa/metrics.py`, then map its issue code in `qa/fixers.py`.
