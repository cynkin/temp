---
name: add-prop
description: Add a new procedurally modelled prop (or library-asset slot) to envgen with correct scale, materials and story-beat placement. Use when a scene needs an object that does not exist yet.
---
# Add a prop

1. In `envgen/blender/geometry/props.py` add:
   ```python
   @prop("furniture.office.desk", (1.4, 0.7, 0.75), ("office", "desk"))
   def desk(coll, mats, location, yaw_deg=0.0, rng=None, **_):
       mb = MeshBuilder("desk")
       mb.box((0, 0, 0.73), (1.4, 0.7, 0.03), "wood_worn", bevel=0.006)   # real bevels catch highlights
       ...
       return _finish(mb, coll, mats, location, yaw_deg, "desk")         # grounds it on the floor
   ```
   Model in metres, origin at floor centre, +Y forward. Use `box/cylinder/tube_path/torus/grid(displace=)/prism`.
   Break silhouettes (bevels, separate parts, asymmetry); give each part its own preset material.
2. If a material is missing, add a preset in `materials/presets.py` (sRGB colour, roughness, wear layers).
3. Place it from a story beat in `envgen/blender/story.py` (claim a footprint, `fit_inside`). Never scatter.
4. `python -m envgen.cli validate <spec>` -> no floating/sunken/scale/outside_walls findings for the new prop.
5. Render a close shot (add a temporary camera shot in the spec) and LOOK at it.
Library alternative: put a GLB/FBX with an `asset.json` (dimensions_m, license) in the asset library and use
`AssetLibrary.select_for_slot` + `envgen.blender.asset_import.import_model(..., target_dims_m=...)`.
