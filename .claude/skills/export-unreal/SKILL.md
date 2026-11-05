---
name: export-unreal
description: Export an envgen scene to Unreal Engine 5 and import it with the editor script. Use when the user wants real-time presentation (Lumen, Nanite) of a generated environment.
---
# Export to Unreal Engine 5

1. `python -m envgen.cli export-unreal specs/<name>.yaml --out output/<name>/unreal`
   -> one GLB per unique mesh (instances share a GLB) + `manifest.json` (UE-space transforms, lights with
   intensity hints, CineCamera settings, fog, post/exposure).
2. Copy the folder to the machine with UE 5.3+; enable the Python Editor Script Plugin (+ glTF/Interchange).
3. In the editor Python console: `py "<repo>/unreal/import_envgen_scene.py" "<dir>/manifest.json" --save`
4. Check one asymmetric prop's orientation and the light levels (manual exposure is set from the manifest).
Status: Blender-side export is tested; the UE script is untested (no UE in CI) - report findings.
