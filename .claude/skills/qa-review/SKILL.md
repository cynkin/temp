---
name: qa-review
description: Act as the vision critic for an envgen refinement iteration - inspect render vs references, write critique.json with scores, issues and a bounded spec patch. Use when an iteration directory has a review_packet.json and no API critic ran.
---
# QA review (agent-in-the-loop critic)

1. `cat <iter_dir>/review_packet.json` - render path, reference paths, allowed patch paths, current values,
   metric report, answer schema.
2. Read the render and 3-5 references. Compare like an environment art lead: composition (leading lines, VP,
   framing), architecture, materials (roughness variation, wear logic), lighting (motivation, key/fill ratio,
   shafts), scale, placement (floating, interpenetration, randomness), coherence, similarity, storytelling.
3. Write `<iter_dir>/critique.json`:
   ```json
   {"scores": {"composition": 6, "architectural_accuracy": 7, "material_realism": 6, "lighting": 5, "scale": 8,
               "object_placement": 7, "visual_coherence": 7, "reference_similarity": 6,
               "environmental_storytelling": 7},
    "issues": [{"criterion": "lighting", "severity": 4, "description": "..."}],
    "patch": [{"op": "add", "path": "lighting.exposure", "value": 0.5, "reason": "..."}],
    "summary": "..."}
   ```
4. Apply: `python -m envgen.cli patch <spec> <iter_dir>/critique.json --out specs/<name>_r2.yaml`, then refine
   from the new spec. Paths outside the allow-list are rejected automatically (use --unrestricted only for
   deliberate art-director changes). Non-patchable issues -> hand to the art director as text.
