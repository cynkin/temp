"""Cinematic cameras from spec shots (physical focal length, sensor, aperture, focus)."""
from __future__ import annotations

import math

import bpy
from mathutils import Matrix, Vector

from .util import collection, look_rotation, tag


def build_cameras(spec: dict, root) -> list[bpy.types.Object]:
    cam_cfg = spec["camera"]
    coll = collection("Cameras", root)
    cams = []
    for shot in cam_cfg.get("shots", []):
        data = bpy.data.cameras.new(shot["name"])
        data.lens = float(shot.get("focal_mm", 20.0))
        data.sensor_width = float(cam_cfg.get("sensor_mm", 36.0))
        data.sensor_fit = "HORIZONTAL"
        data.clip_start, data.clip_end = 0.03, 250.0
        if shot.get("shift_y"):
            data.shift_y = float(shot["shift_y"])
        if shot.get("fstop"):
            data.dof.use_dof = True
            data.dof.aperture_fstop = float(shot["fstop"])
            data.dof.focus_distance = float(shot.get("focus_distance", 5.0))
        obj = bpy.data.objects.new(f"cam_{shot['name']}", data)
        pos, target = Vector(shot["position"]), Vector(shot["target"])
        m = Matrix.Translation(pos) @ look_rotation(pos - target)
        if shot.get("roll_deg"):
            m = m @ Matrix.Rotation(math.radians(float(shot["roll_deg"])), 4, "Z")
        obj.matrix_world = m
        coll.objects.link(obj)
        tag(obj, role="camera", shot=shot["name"])
        cams.append(obj)
    if cams:
        bpy.context.scene.camera = cams[0]
    return cams
