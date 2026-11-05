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

## Session 1b — third environment: Sector B service tunnel

Goal: prove the system generalises to a windowless underground space (refs 00/12/13/18, plus 14/05 for props).
Analysis and construction requirements: `docs/reference_analysis.md` → "Construction requirements extracted
(Sector B service tunnel)". New target: `references/targets/underground_tunnel.json` (4 refs, luma 0.165).

New building blocks (all reusable by any spec): `caged_lamp` practical with a modelled wire guard; props `toolbox`,
`oil_drum`, `sandbag` (+ `sandbag_wall`, `floor_cable` helpers); structure `cable_sag`; beats `breach` and `stencils`;
generated `stencil` and `hazard_band` decals; presets `drum_paint`, `hessian`, `hazard_yellow`.

| Symptom (quick 480×270 test renders) | Root cause | Fix |
|---|---|---|
| Everything near-black | 30–40 W point lights at 2–3 m throw | 110–170 W bulbs |
| Walls read as red brick, "oversaturated" issue (score 0.68) | Preset peel under-colour is red brick | Grey-brown under-colour, less peel, cooler 4400–4800 K lamps |
| Breach area black under a lit lamp | Lamps on bay lines sat **inside the downstand beams** (z 2.82–3.30) | Lamps mid-bay; cable hooks on rib faces; pipes moved off the pilasters |
| Sandbag line read as a few loose pillows | One row, two bags per course | Two-row stepped position, one object |
| Validator interpenetration warning between sandbag rows | AABB check on two angled rows (false positive) | Rows built as one object |

### Overgrown service hall: first preview (960×540)
| Symptom | Root cause | Fix |
|---|---|---|
| Hall near-black, sun lit only the stair | Sun at azimuth 62°/elevation 52° put the roof-hole patch about 3 m away from the fern habitat | Sun 100°/72° so the shaft lands on the wet corner; sky 5→9; sun 6.5→9 |
| Still too little light for a 14 × 4.2 × 4.6 m hall | One 1.3 m hole plus high clerestories | Hole radius 1.7 plus a second failure over the gallery; haze 0.04→0.05 |
| Ferns shot showed only a drain pipe | Camera behind the pipe at 0.9 m | Camera moved left of the pipe, 1.15 m high, 26 mm |
Open: the sun patch is squared off (the hole is made of whole slab panels) and blows out. Next: `refine --shots hero ferns`.

### Sector B: preview, QA and fixes
| Run | Score (target underground_tunnel) | Notes |
|---|---|---|
| Preview hero | 0.790 | Dark regular mortar grid → `excessive_detail`, `high_contrast`; "SECTOR B" clipped to "ECTOR"; chrome-looking pipes |
| Preview breach | 0.933 | Composition reads well (sandbags, open toolbox, drums, cold tube) → `docs/renders/sector_b_breach_preview.jpg` |
| Refine (metric) hero, iter 0 | 0.925 | After the fixes below; target 0.85 reached, so no patch applied |

Fixes: limewash over the mortar (lighter, lower relief), stencil text fitted to its canvas, finer stencil wear, painted
pipes. **Decal bug (affects every scene):** the wall shader's AO-driven grime ignores alpha, so each decal quad printed a
dark rectangle on the wall behind it. Decals are now invisible to shadow and diffuse rays (`decals.image_quad`);
verified with a close-up test render.
| Final 1600×900 / 96 spp hero | 0.922 | `docs/renders/sector_b_hero.jpg` (decal rectangles gone) |
| Final 1600×900 / 96 spp breach | 0.929 | `docs/renders/sector_b_breach.jpg` |

Honest critique of the finals (next polish):
- **Sandbags** read as smooth pillows. Their flat underside box shows as a plank edge. They need cloth wrinkles, a
  seam, and slump onto the bag below instead of a flat base.
- **Loose spanners** are flat bars. They need modelled jaw/ring ends.
- **Brick** reads as clean painted/glazed brick (closer to ref 18's tile than ref 00's chalky limewash). It needs more
  roughness variation, soot above the lamps, and damp staining at the dado line.
- The hero is still brighter and lower-contrast than the low-key refs (luma target 0.165). Try fewer lit lamps, or
  let `refine` run more iterations with a target_score of 0.95.
