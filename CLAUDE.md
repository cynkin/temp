# envgen — notes for Claude Code

- Python: always `.venv/bin/python` (3.13, has `bpy==5.2.3`). CLI: `.venv/bin/python -m envgen.cli <cmd>`.
- Headless Cycles CPU only: EEVEE needs EGL/GPU and crashes here. Blender 5.x API: no `scene.node_tree`
  (compositor) — grading is done in numpy (`envgen/post/grade.py`); AgX looks are named `"AgX - ..."`.
- The spec (YAML) is the source of truth; never hand-edit a built scene. Change specs, or code + spec.
- Before rendering, run `envgen validate <spec>` (seconds). Renders take ~2 min per 960x540 shot.
- After any visual change, LOOK at the render (Read the .jpg) before claiming improvement.
- Props only via story beats; every prop needs `@prop(..., nominal_dims)`; keep the walkable lane.
- Tests: `.venv/bin/python tests/test_spec_qa.py` and `tests/test_profile_metrics.py`.
- Agents: `.claude/agents/*`, workflows: `.claude/skills/*`. Dev log: `docs/DEVLOG.md`.
- Generated output goes to `output/` (git-ignored). Never commit renders except curated ones in `docs/renders/`.
