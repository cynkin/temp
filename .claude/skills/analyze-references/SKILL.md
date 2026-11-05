---
name: analyze-references
description: Analyze reference images for envgen - visual 12-aspect study plus measured CV profile and an aggregate numeric QA target per cluster. Use when new reference images are added or a new environment type is started.
---
# Analyze references

1. View every image with the Read tool (make 2x2 contact sheets with PIL for speed if there are many).
2. Cluster by environment type; pick 4-8 images per cluster that share lighting/mood.
3. Measure: `python -m envgen.cli analyze-refs --indices 08 09 16 --name <cluster>`
   (or `--images path1 path2`). Writes `references/profiles/*.json` (per-image) and
   `references/targets/<cluster>.json` (mean/std/min/max per metric: luminance, contrast, colour/palette,
   haze, detail, vanishing point, spatial luminance grid, clipping).
4. Write construction requirements to `docs/reference_analysis.md`: one row per requirement ->
   implementation (spec field / preset / beat). Numbers, not adjectives.
Caveats: letterbox bars are auto-cropped; corner watermarks are not removed; VP detection is unreliable on
very dark or non-one-point shots - confirm by eye.
