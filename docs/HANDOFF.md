# Hand-off: how to continue this project in a new session

## Prompt to paste into a new Claude Code session

```
Continue the envgen project in this repo (branch claude/brave-volta-rpax5d of cynkin/temp).
It is an autonomous reference-driven 3D environment generator: headless Blender 5.2 (bpy pip module) +
Cycles CPU, spec-driven (specs/*.yaml), with an automated render -> score -> patch QA loop and Claude Code
agents/skills in .claude/. Reference images are in pics/ (index in references/index.txt).

First read, in order: CLAUDE.md, docs/HANDOFF.md (the "Current state" and "Next steps" sections),
README.md, docs/DEVLOG.md. Then set up the environment:
  uv venv .venv --python 3.13 && uv pip install --python .venv/bin/python -e ".[vision,dev]"
and check that it works:
  .venv/bin/python tests/test_spec_qa.py && .venv/bin/python tests/test_profile_metrics.py
  .venv/bin/python -m envgen.cli validate specs/hospital_corridor.yaml

Then work through "Next steps" in docs/HANDOFF.md in order. Rules: LOOK at every render (Read the jpg)
before you claim an improvement. Keep docs/DEVLOG.md updated. Commit and push to the same branch after
each finished step. Renders take about 2 min per 960x540 shot on 4 CPU cores. Run long renders in the
background.
```

## Current state (end of session 1)

- **Done and committed:** the whole system (see README), plus two environment specs:
  `hospital_corridor` (QA-refined: metric score 0.96 in refine round 2, after an agent critique) and
  `overgrown_service_hall` (validated; first preview render was queued when the session ended), and
  `sector_b_service_tunnel` (underground, refs 00/12/13/18/14; tested with quick renders, see DEVLOG "Session 1b").
- **Hospital corridor art direction applied:** sun elevation 16° (blind stripes across the far wall), sky
  strength 7, localised plaster peel, camera levelled to show the collapsed ceiling, a surviving mid-corridor
  tube light, grain 0.014.
- **Renders are not committed** (`output/` is git-ignored). Regenerate them with the commands below.
- **Untested here:** the Claude API critic (no key in the build container), live Poly Haven/ambientCG
  downloads (blocked network), and the UE5 import script (no Unreal).

## Next steps (in priority order)

1. **Hospital final renders.** The hero is done and committed (docs/renders/hospital_corridor_hero.jpg). For the rest, run
   `.venv/bin/python -m envgen.cli render specs/hospital_corridor.yaml --quality final --blend --out output/ward_b_corridor/final`
   (3 shots: hero, gurney, reverse). Look at each. Copy curated JPGs to `docs/renders/` and commit them.
2. **Overgrown service hall.** Run `render specs/overgrown_service_hall.yaml` (preview), look at hero and ferns,
   fix whatever breaks (pipe/stair placement, vegetation scale, roof-hole god rays), then
   `refine specs/overgrown_service_hall.yaml --shots hero` and do an agent critique with the `qa-review` skill.
3. **Hospital polish** (from the final-render critique, docs/renders/hospital_corridor_hero.jpg, score 0.954):
   the lower-left mint wainscot reads speckled. That is the *material* (high-frequency peel, `wainscot_paint.peel.scale`
   2.2), not render noise: use larger, fewer chips. Also add foreground floor interest near the camera, and push the
   far red emergency glow (refs 02/19) a little further.
4. **Vision critic with a real API key** (`ANTHROPIC_API_KEY`): run `refine --critic anthropic` once and record
   the result in DEVLOG.
4b. **Sector B tunnel:** `refine specs/sector_b_service_tunnel.yaml --shots hero` (target underground_tunnel.json),
   critique with `qa-review`, then `render --quality final`. Ideas: a reverse shot from the red end, cobweb masses
   (ref 12), a blast-door variant of the end doors (ref 13: heavy riveted single leaf, red porthole).
5. **New layouts:** add `room` and `tunnel` builders (ribbed tunnel for refs 12/13, metro service level for 05/14).
6. **Unreal:** run `export-unreal` and import on a machine with UE 5.3+; verify orientation and light levels.
7. **Docs:** fill in the "Prototype results" section of docs/DEVLOG.md with scores and renders.
