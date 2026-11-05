# Development log

## Session 1 — 2026-11-05

### Environment inspection
- Container: 4 CPU cores, 15 GB RAM, no GPU, Python 3.11–3.13. Network: only PyPI and GitHub reachable
  (download.blender.org, Poly Haven, ambientCG and Hugging Face blocked).
- Blender: not installed as an app. `bpy==5.2.3` (Blender 5.2 LTS as a Python module, cp313 wheel, ~400 MB)
  installed from PyPI. **Cycles CPU works**; OIDN denoising works; **EEVEE crashes** (no libEGL).
- Unreal Engine: not available (no GPU, not downloadable here).
- Anthropic API key: not present → the vision critic can't call the API here; the agent-in-the-loop path is used.

### Phase 1 — research and architecture
- Studied all 20 reference images (contact sheets) → `docs/reference_analysis.md`.
- Decision: Blender-first, with an optional UE5 export (see `docs/architecture.md`).
- MCP research (subagent): ahujasid/mcp-for-blender needs a GUI Blender; the official Blender Lab MCP is
  experimental and its host was unreachable; Epic Unreal MCP is editor-only → no MCP in the pipeline.
- Blender 5.x API facts verified: no `scene.node_tree`; AgX looks are prefixed; `Mesh.set_sharp_from_angle`;
  Principled BSDF input names; Musgrave merged into Noise.

### Phase 2–3 — foundation and generation
- Parallel subagents: (a) CV profile + QA metrics (9 tests pass), (b) asset registry/sources/import + Unreal
  exporter and UE script (Blender side tested; live downloads and UE untested).
- Built: spec schema, MeshBuilder, layered materials, corridor architecture, props, story beats, lighting, fog,
  decals, validation, render, grade, CLI.

### Bugs found by rendering / validation (and fixed)
| Symptom | Root cause | Fix |
|---|---|---|
| Corridor almost black | Opposite hospital wing (14 m tall, 13 m away) blocked the 27° sun | Wing reduced to 2 floors, moved to 18 m |
| No sun through windows (anticipated) | Cycles shadow rays stop at transmissive glass | Glass mixes in Transparent BSDF for shadow rays |
| Blind stripes smeared | 0.8° sun disc → ~4 cm penumbra at 3 m > 3.2 cm slat pitch | Sun angle 0.25° |
| Door plates mirrored | UV orientation for ±X-facing decals | Swapped the u mapping |
| Bed panels read as blue slabs | Saturated plastic on large flat panels | Beige moulded ABS preset + grab slot |
| Toppled cart through the wall | Fell along its height axis toward the wall | Toppled carts fall along the corridor + `fit_inside` safety net |
| Sheet pooled 0.7 m sideways through the wall | Drape "pooling" slid cloth outward | Cloth crumples in place at the floor |
| Validator scale false positives | World AABB inflates with yaw | Compare local object dimensions |
| IV stand tip floating 0.4 m | 86° topple ignores leg reach | Tilt = 90° + asin(leg_reach/pole_length) |
| QA raised contrast on a high-contrast image | `low_local_contrast` (texture) treated as global contrast | Separated; local contrast → unresolved for the critic |
| QA acted on contradictory issues | Different sub-metrics disagree (too_dark & too_bright) | Severity floor + net-direction resolution |

### Phase 4–5 — autonomous iteration and validation
### Prototype results (hospital corridor, hero shot, target = refs 02/08/09/15/16/17/19)
| Run | Score | What changed |
|---|---|---|
| Refine round 1, iter 0 | 0.803 | Baseline (too dark, high contrast, crushed blacks) |
| Refine round 1, iter 1 | 0.945 | Grade-only fixes by metric fixers (exposure, lift, contrast) |
| Agent critique (qa-artist) | – | Metrics were satisfied, but visual review found murky walls, a hidden ceiling story and mottled paint. Patch: sun elevation 27→16°, sky 4.5→7, plaster peel 0.3→0.18, camera levelled |
| Refine round 2, iter 0 | 0.960 | target_reached; blind stripes now rake the far wall (the ref 17 look) |
| Final 1600×900 / 96 spp | 0.954 | docs/renders/hospital_corridor_hero.jpg |

Lesson: metric QA alone converges on "statistically similar" images; the visual critic is needed for
structural lighting and composition decisions. Remaining issue: speckled wainscot material (fix applied in
the spec after the final render; not yet re-rendered).
