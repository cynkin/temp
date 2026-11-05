"""Named material recipes. Specs override any field via ``materials: {name: {...}}``.

Colours are sRGB (as an artist would pick them); the builder linearises them.
Values are tuned for physically plausible albedo (no pure whites: painted
plaster ~0.7, dirty concrete ~0.35, asphalt ~0.08).
"""
from __future__ import annotations

PRESETS: dict[str, dict] = {
    # ---------------------------------------------------------------- architecture
    "plaster_painted": {  # upper wall: off-white emulsion over plaster, peeling + water damage
        "projection": "wall", "per_object_variation": False,
        "base": {"color": (0.70, 0.69, 0.64), "color_var": 0.07, "roughness": 0.78, "rough_var": 0.12,
                 "hue_var_color": (0.62, 0.56, 0.44), "hue_var": 0.4},
        "bump": {"strength": 0.08, "scale": 5.0},
        "peel": {"amount": 0.35, "scale": 1.3, "under_color": (0.6, 0.57, 0.5), "thickness": 2.5},
        "streaks": {"amount": 0.75, "top": 2.85, "length": 1.9, "color": (0.7, 0.6, 0.45)},
        "grime": {"amount": 0.7, "color": (0.2, 0.17, 0.13), "ao_distance": 0.35, "floor_height": 0.25},
    },
    "wainscot_paint": {  # lower wall: institutional mint/green gloss enamel (scuffed by trolleys)
        "projection": "wall", "per_object_variation": False,
        "base": {"color": (0.45, 0.54, 0.50), "color_var": 0.06, "roughness": 0.38, "rough_var": 0.2,
                 "hue_var_color": (0.48, 0.52, 0.42), "hue_var": 0.5},
        "bump": {"strength": 0.06, "scale": 6.0},
        "peel": {"amount": 0.28, "scale": 2.2, "under_color": (0.6, 0.58, 0.52), "thickness": 3.0},
        "grime": {"amount": 0.85, "color": (0.13, 0.12, 0.09), "ao_distance": 0.3, "floor_height": 0.3},
    },
    "vinyl_tile_floor": {  # 300 mm vinyl tiles, two tones, wet patches, heavy floor grime
        "projection": "xy", "per_object_variation": False,
        "base": {"color": (0.50, 0.49, 0.44), "color_var": 0.12, "roughness": 0.42, "rough_var": 0.3,
                 "hue_var_color": (0.38, 0.33, 0.26), "hue_var": 0.55, "specular": 0.55},
        "pattern": {"size": [0.3, 0.3], "mortar": 0.003, "mortar_color": (0.12, 0.11, 0.09),
                    "tile_var": 0.09, "alt_color": (0.36, 0.40, 0.38), "alt_mix": 0.75, "relief": 0.6},
        "bump": {"strength": 0.1, "scale": 12.0},
        "grime": {"amount": 0.8, "color": (0.1, 0.085, 0.065), "ao_distance": 0.6, "floor_height": 0.0},
        "wet": {"amount": 0.35, "scale": 0.35, "roughness": 0.05},
    },
    "ceiling_tile": {  # mineral-fibre acoustic tile, yellowed + water rings
        "projection": "xy", "per_object_variation": True,
        "base": {"color": (0.66, 0.64, 0.58), "color_var": 0.14, "roughness": 0.92, "rough_var": 0.05,
                 "hue_var_color": (0.55, 0.45, 0.3), "hue_var": 0.5},
        "bump": {"strength": 0.35, "scale": 60.0, "detail": 4.0},
        "streaks": {"amount": 0.0},
        "grime": {"amount": 0.35, "color": (0.35, 0.28, 0.18), "ao_distance": 0.15, "floor_height": 0.0},
    },
    "concrete_slab": {
        "projection": "auto",
        "base": {"color": (0.36, 0.35, 0.33), "color_var": 0.15, "roughness": 0.9, "rough_var": 0.08},
        "bump": {"strength": 0.25, "scale": 9.0},
        "grime": {"amount": 0.6, "color": (0.08, 0.07, 0.06), "ao_distance": 0.5},
        "streaks": {"amount": 0.5, "top": 3.6, "length": 1.2, "color": (0.4, 0.33, 0.22)},
    },
    "concrete_floor": {
        "projection": "xy", "per_object_variation": False,
        "base": {"color": (0.34, 0.33, 0.31), "color_var": 0.16, "roughness": 0.75, "rough_var": 0.2},
        "bump": {"strength": 0.2, "scale": 14.0},
        "grime": {"amount": 0.7, "color": (0.07, 0.06, 0.05), "ao_distance": 0.6},
        "wet": {"amount": 0.4, "scale": 0.4},
    },
    "brick_painted": {
        "projection": "wall", "per_object_variation": False,
        "base": {"color": (0.72, 0.71, 0.68), "color_var": 0.08, "roughness": 0.85},
        "pattern": {"size": [0.215, 0.075], "offset": 0.5, "mortar": 0.009, "mortar_color": (0.45, 0.44, 0.41),
                    "tile_var": 0.06, "relief": 2.5},
        "peel": {"amount": 0.45, "scale": 1.6, "under_color": (0.48, 0.26, 0.18), "thickness": 2.0},
        "grime": {"amount": 0.7, "color": (0.15, 0.13, 0.1), "ao_distance": 0.4, "floor_height": 0.5},
        "streaks": {"amount": 0.6, "top": 3.0, "length": 2.0},
    },
    "brick_raw": {
        "projection": "wall", "per_object_variation": False,
        "base": {"color": (0.42, 0.22, 0.15), "color_var": 0.2, "roughness": 0.88,
                 "hue_var_color": (0.3, 0.2, 0.16), "hue_var": 0.5},
        "pattern": {"size": [0.215, 0.075], "offset": 0.5, "mortar": 0.01, "mortar_color": (0.4, 0.38, 0.33),
                    "tile_var": 0.18, "relief": 2.5},
        "grime": {"amount": 0.6, "color": (0.1, 0.08, 0.06), "ao_distance": 0.4, "floor_height": 0.6},
        "moss": {"amount": 0.0},
    },
    "steel_tbar": {  # suspended-ceiling grid: painted steel, slightly rusty
        "base": {"color": (0.72, 0.71, 0.68), "roughness": 0.45, "metallic": 0.0, "color_var": 0.05},
        "rust": {"amount": 0.25, "scale": 6.0},
        "grime": {"amount": 0.3, "color": (0.3, 0.25, 0.18), "ao_distance": 0.1},
    },
    "galvanized_duct": {
        "base": {"color": (0.6, 0.6, 0.6), "roughness": 0.4, "rough_var": 0.3, "metallic": 1.0, "color_var": 0.1},
        "grime": {"amount": 0.6, "color": (0.15, 0.13, 0.1), "ao_distance": 0.3},
        "rust": {"amount": 0.15, "scale": 4.0},
    },
    "door_paint": {  # painted timber ward door, kicked & scraped at the bottom
        "base": {"color": (0.55, 0.6, 0.56), "roughness": 0.42, "rough_var": 0.15, "color_var": 0.06},
        "peel": {"amount": 0.2, "scale": 3.0, "under_color": (0.45, 0.33, 0.22)},
        "grime": {"amount": 0.7, "color": (0.12, 0.1, 0.08), "ao_distance": 0.15, "floor_height": 0.45},
    },
    "door_frame_metal": {
        "base": {"color": (0.62, 0.62, 0.6), "roughness": 0.4, "color_var": 0.05},
        "peel": {"amount": 0.2, "scale": 6.0, "under_color": (0.32, 0.2, 0.12)},
        "rust": {"amount": 0.2, "scale": 8.0},
        "grime": {"amount": 0.5, "color": (0.12, 0.1, 0.08), "ao_distance": 0.1},
    },
    "window_frame": {
        "base": {"color": (0.78, 0.77, 0.73), "roughness": 0.5, "color_var": 0.05},
        "rust": {"amount": 0.15, "scale": 9.0},
        "grime": {"amount": 0.6, "color": (0.18, 0.15, 0.11), "ao_distance": 0.08},
    },
    "blind_slat": {
        "base": {"color": (0.8, 0.79, 0.74), "roughness": 0.45, "color_var": 0.08,
                 "hue_var_color": (0.7, 0.62, 0.5), "hue_var": 0.4},
        "grime": {"amount": 0.4, "color": (0.3, 0.25, 0.2), "ao_distance": 0.03},
    },
    "radiator": {
        "base": {"color": (0.74, 0.73, 0.68), "roughness": 0.4},
        "peel": {"amount": 0.25, "scale": 5.0, "under_color": (0.35, 0.2, 0.1)},
        "rust": {"amount": 0.35, "scale": 5.0},
        "grime": {"amount": 0.6, "color": (0.15, 0.12, 0.09), "ao_distance": 0.05},
    },
    "handrail_vinyl": {
        "base": {"color": (0.66, 0.66, 0.6), "roughness": 0.3, "color_var": 0.05},
        "grime": {"amount": 0.5, "color": (0.2, 0.17, 0.12), "ao_distance": 0.05},
    },
    # ---------------------------------------------------------------- props
    "chrome_worn": {
        "base": {"color": (0.78, 0.78, 0.78), "roughness": 0.22, "rough_var": 0.25, "metallic": 1.0,
                 "color_var": 0.05},
        "rust": {"amount": 0.2, "scale": 9.0},
        "grime": {"amount": 0.45, "color": (0.12, 0.1, 0.08), "ao_distance": 0.06},
    },
    "painted_metal_white": {
        "base": {"color": (0.78, 0.78, 0.75), "roughness": 0.35, "color_var": 0.05},
        "peel": {"amount": 0.15, "scale": 7.0, "under_color": (0.35, 0.3, 0.27)},
        "rust": {"amount": 0.15, "scale": 7.0},
        "grime": {"amount": 0.55, "color": (0.15, 0.13, 0.1), "ao_distance": 0.08},
    },
    "painted_metal_blue": {
        "base": {"color": (0.22, 0.38, 0.55), "roughness": 0.38, "color_var": 0.06},
        "peel": {"amount": 0.12, "scale": 6.0, "under_color": (0.5, 0.5, 0.5)},
        "grime": {"amount": 0.5, "color": (0.1, 0.09, 0.08), "ao_distance": 0.08},
    },
    "plastic_blue": {  # perforated waiting-chair shells / bins
        "base": {"color": (0.2, 0.35, 0.55), "roughness": 0.45, "rough_var": 0.2, "color_var": 0.06},
        "grime": {"amount": 0.55, "color": (0.12, 0.11, 0.1), "ao_distance": 0.06},
    },
    "plastic_beige": {  # moulded ABS bed panels, yellowed by UV
        "base": {"color": (0.74, 0.71, 0.63), "roughness": 0.5, "color_var": 0.07,
                 "hue_var_color": (0.7, 0.62, 0.45), "hue_var": 0.4},
        "grime": {"amount": 0.55, "color": (0.15, 0.13, 0.1), "ao_distance": 0.06},
    },
    "plastic_yellow": {
        "base": {"color": (0.85, 0.66, 0.08), "roughness": 0.4, "color_var": 0.08},
        "grime": {"amount": 0.45, "color": (0.18, 0.15, 0.1), "ao_distance": 0.05, "floor_height": 0.15},
    },
    "rubber_black": {
        "base": {"color": (0.04, 0.04, 0.04), "roughness": 0.75, "color_var": 0.1},
        "grime": {"amount": 0.4, "color": (0.15, 0.13, 0.1), "ao_distance": 0.03},
    },
    "vinyl_mattress": {  # wipe-clean mattress cover
        "base": {"color": (0.18, 0.32, 0.36), "roughness": 0.4, "color_var": 0.06, "sheen": 0.2},
        "grime": {"amount": 0.6, "color": (0.12, 0.1, 0.08), "ao_distance": 0.1},
    },
    "sheet_cloth": {  # stained cotton sheet; sheen for cloth response
        "base": {"color": (0.62, 0.66, 0.62), "roughness": 0.85, "color_var": 0.1, "sheen": 0.6,
                 "hue_var_color": (0.55, 0.48, 0.36), "hue_var": 0.6},
        "bump": {"strength": 0.1, "scale": 40.0},
        "grime": {"amount": 0.5, "color": (0.32, 0.26, 0.18), "ao_distance": 0.08},
        "streaks": {"amount": 0.0},
    },
    "paper": {
        "base": {"color": (0.78, 0.76, 0.7), "roughness": 0.8, "color_var": 0.12, "sheen": 0.2,
                 "hue_var_color": (0.62, 0.55, 0.4), "hue_var": 0.6},
        "grime": {"amount": 0.35, "color": (0.3, 0.26, 0.2), "ao_distance": 0.02},
    },
    "debris_plaster": {
        "base": {"color": (0.62, 0.6, 0.55), "roughness": 0.92, "color_var": 0.2},
        "bump": {"strength": 0.3, "scale": 30.0},
        "grime": {"amount": 0.4, "color": (0.2, 0.17, 0.13), "ao_distance": 0.05},
    },
    "wood_worn": {
        "base": {"color": (0.42, 0.3, 0.2), "roughness": 0.75, "color_var": 0.2,
                 "hue_var_color": (0.3, 0.22, 0.15), "hue_var": 0.6},
        "bump": {"strength": 0.2, "scale": 18.0},
        "grime": {"amount": 0.5, "color": (0.1, 0.08, 0.06), "ao_distance": 0.06},
    },
    "corkboard": {
        "base": {"color": (0.5, 0.36, 0.22), "roughness": 0.95, "color_var": 0.2},
        "bump": {"strength": 0.4, "scale": 120.0, "detail": 2.0},
    },
    # ---------------------------------------------------------------- organic
    "grass_dry": {
        "base": {"color": (0.55, 0.47, 0.26), "roughness": 0.7, "color_var": 0.25, "subsurface": 0.25,
                 "hue_var_color": (0.38, 0.42, 0.18), "hue_var": 0.6},
    },
    "fern_leaf": {
        "base": {"color": (0.2, 0.33, 0.09), "roughness": 0.55, "color_var": 0.2, "subsurface": 0.3,
                 "hue_var_color": (0.32, 0.36, 0.12), "hue_var": 0.4},
    },
    "moss": {
        "base": {"color": (0.2, 0.27, 0.07), "roughness": 0.95, "color_var": 0.25, "sheen": 0.5,
                 "hue_var_color": (0.3, 0.32, 0.1), "hue_var": 0.5},
        "bump": {"strength": 0.5, "scale": 60.0, "detail": 4.0},
    },
    "soil": {
        "projection": "xy", "per_object_variation": False,
        "base": {"color": (0.24, 0.2, 0.15), "roughness": 0.92, "color_var": 0.2,
                 "hue_var_color": (0.3, 0.28, 0.22), "hue_var": 0.5},
        "bump": {"strength": 0.35, "scale": 18.0},
        "wet": {"amount": 0.15, "scale": 0.5, "roughness": 0.2},
    },
    "cable_black": {
        "base": {"color": (0.03, 0.03, 0.03), "roughness": 0.5},
    },
    "fixture_housing": {
        "base": {"color": (0.74, 0.73, 0.7), "roughness": 0.4},
        "rust": {"amount": 0.15, "scale": 8.0},
        "grime": {"amount": 0.5, "color": (0.2, 0.17, 0.12), "ao_distance": 0.05},
    },
    "red_metal": {  # fire extinguisher
        "base": {"color": (0.55, 0.06, 0.04), "roughness": 0.3, "coat": 0.4},
        "grime": {"amount": 0.4, "color": (0.12, 0.1, 0.08), "ao_distance": 0.05},
    },
}
