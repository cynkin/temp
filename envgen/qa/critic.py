"""Vision critics: compare a render against the reference images and propose targeted spec patches.

Two interchangeable backends share one output schema:

* ``AnthropicCritic`` - calls a vision-capable Claude model through the official SDK
  (requires ``ANTHROPIC_API_KEY`` or another SDK credential). Uses structured output so
  the response is always valid JSON, and only accepts patch ops on an allow-list of
  spec paths with numeric bounds (the critic can steer, never corrupt, a spec).
* ``FileCritic`` - "agent in the loop": the loop writes a review packet (render, references,
  metric report, allowed paths) and the Claude Code *Quality Assurance Artist* agent
  inspects the images and writes ``critique.json`` in the same schema. This is how the
  loop runs inside Claude Code without an API key.

Neither backend is trusted blindly: ops are validated against ALLOWED_PATHS and
clamped by BOUNDS before being applied.
"""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path

from PIL import Image

from ..spec.schema import get_path
from .fixers import BOUNDS

CRITERIA = ["composition", "architectural_accuracy", "material_realism", "lighting", "scale", "object_placement",
            "visual_coherence", "reference_similarity", "environmental_storytelling"]

# Paths a critic may modify (artistic levers that cannot break scene validity).
ALLOWED_PATHS = sorted(set(BOUNDS) | {
    "lighting.sun.azimuth", "lighting.sun.elevation", "lighting.sun.kelvin", "lighting.fixtures.kelvin",
    "atmosphere.color", "atmosphere.noise_amount", "post.shadow_tint", "post.highlight_tint", "post.gamma",
    "camera.shots.0.position", "camera.shots.0.target", "camera.shots.0.focal_mm", "camera.shots.0.fstop",
    "materials.plaster_painted.streaks.amount", "materials.plaster_painted.peel.amount",
    "materials.vinyl_tile_floor.wet.amount", "materials.vinyl_tile_floor.grime.amount",
})
EXTRA_BOUNDS = {
    "lighting.sun.azimuth": (-180, 180), "lighting.sun.elevation": (5, 70), "lighting.sun.kelvin": (3000, 7500),
    "lighting.fixtures.kelvin": (2700, 6500), "camera.shots.0.focal_mm": (14, 50), "camera.shots.0.fstop": (1.4, 16),
    "materials.plaster_painted.streaks.amount": (0, 1), "materials.plaster_painted.peel.amount": (0, 0.8),
    "materials.vinyl_tile_floor.wet.amount": (0, 0.8), "materials.vinyl_tile_floor.grime.amount": (0, 1),
    "atmosphere.noise_amount": (0, 0.9),
}

CRITIQUE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scores", "issues", "patch", "summary"],
    "properties": {
        "scores": {"type": "object", "additionalProperties": False, "required": CRITERIA,
                   "properties": {c: {"type": "integer", "minimum": 1, "maximum": 10} for c in CRITERIA}},
        "issues": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["criterion", "severity", "description"],
            "properties": {"criterion": {"type": "string"}, "severity": {"type": "integer", "minimum": 1, "maximum": 5},
                           "description": {"type": "string"}}}},
        "patch": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["op", "path", "value", "reason"],
            "properties": {"op": {"type": "string", "enum": ["set", "add", "scale"]}, "path": {"type": "string"},
                           "value": {"anyOf": [{"type": "number"}, {"type": "array", "items": {"type": "number"}}]},
                           "reason": {"type": "string"}}}},
        "summary": {"type": "string"},
    },
}

RUBRIC = """You are the Quality Assurance Artist on a AAA environment art team. Compare the RENDER (last image)
against the REFERENCE images (all earlier images). The references define the target art direction: photoreal
abandoned / horror interiors. Judge the render as an environment artist would in a review.

Score each criterion 1-10 (10 = indistinguishable from the references' quality bar):
composition, architectural_accuracy, material_realism, lighting, scale, object_placement, visual_coherence,
reference_similarity, environmental_storytelling.

List concrete issues (what is wrong, where in frame, why it breaks realism). Then propose at most 6 targeted
patch operations on the scene spec, ONLY using these paths (others will be discarded):
{paths}
Current values: {current}
Prefer small, specific moves (e.g. add 0.3 to an exposure, scale fog density by 0.8). Do not propose changes for
problems the patch paths cannot fix; describe those in issues instead."""


def _img_block(path: str | Path, max_side: int = 1024) -> dict:
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(buf.getvalue()).decode()}}


def current_values(spec: dict) -> dict:
    return {p: get_path(spec, p) for p in ALLOWED_PATHS if get_path(spec, p) is not None}


def sanitize_patch(ops: list[dict]) -> tuple[list[dict], list[str]]:
    """Keep only allow-listed ops; attach bounds. Returns (ops, rejected-reasons)."""
    ok, rejected = [], []
    for op in ops or []:
        path = op.get("path", "")
        if path not in ALLOWED_PATHS:
            rejected.append(f"path not allowed: {path}")
            continue
        if op.get("op") not in ("set", "add", "scale"):
            rejected.append(f"bad op {op.get('op')} on {path}")
            continue
        if op["op"] == "scale" and isinstance(op.get("value"), (int, float)) and not 0.25 <= op["value"] <= 4:
            rejected.append(f"scale factor out of range on {path}")
            continue
        ok.append({"op": op["op"], "path": path, "value": op["value"], "reason": op.get("reason", "")})
    return ok, rejected


def all_bounds() -> dict:
    return {**BOUNDS, **EXTRA_BOUNDS}


class CriticUnavailable(RuntimeError):
    pass


class AnthropicCritic:
    name = "anthropic"

    def __init__(self, model: str | None = None, max_refs: int = 5):
        try:
            import anthropic  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise CriticUnavailable("install the 'vision' extra: pip install anthropic") from e
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise CriticUnavailable("no ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN in environment")
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("ENVGEN_VISION_MODEL", "claude-opus-5-5")
        self.max_refs = max_refs

    def critique(self, render_path: Path, reference_paths: list[Path], spec: dict, metric_report: dict) -> dict:
        import anthropic
        refs = reference_paths[: self.max_refs]
        content = [_img_block(p) for p in refs] + [_img_block(render_path)]
        content.append({"type": "text", "text": RUBRIC.format(paths=", ".join(ALLOWED_PATHS),
                                                              current=json.dumps(current_values(spec)))
                        + "\n\nObjective metric report for the render (for context):\n"
                        + json.dumps({k: metric_report.get(k) for k in ("scores", "issues")}, default=str)[:6000]})
        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "high", "format": {"type": "json_schema", "schema": CRITIQUE_SCHEMA}},
                messages=[{"role": "user", "content": content}],
            )
        except anthropic.APIConnectionError as e:
            raise CriticUnavailable(f"network error: {e}") from e
        except anthropic.AuthenticationError as e:
            raise CriticUnavailable("authentication failed") from e
        except anthropic.APIStatusError as e:
            raise CriticUnavailable(f"API error {e.status_code}: {e.message}") from e
        if resp.stop_reason == "refusal":
            raise CriticUnavailable(f"model declined: {getattr(resp.stop_details, 'category', None)}")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return normalize(json.loads(text), source=f"anthropic:{self.model}")


class FileCritic:
    """Reads a critique written by the Claude Code QA agent (same schema as AnthropicCritic)."""
    name = "file"

    def __init__(self, path: Path):
        self.path = Path(path)

    def critique(self, render_path, reference_paths, spec, metric_report) -> dict:
        if not self.path.exists():
            raise CriticUnavailable(f"critique file not found: {self.path}")
        return normalize(json.loads(self.path.read_text()), source=f"file:{self.path.name}")


def normalize(raw: dict, source: str) -> dict:
    scores = {c: int(raw.get("scores", {}).get(c, 5)) for c in CRITERIA}
    ops, rejected = sanitize_patch(raw.get("patch", []))
    return {"source": source, "scores": scores, "score": sum(scores.values()) / (10 * len(scores)),
            "issues": raw.get("issues", []), "patch": ops, "rejected": rejected, "summary": raw.get("summary", "")}


def write_review_packet(out_dir: Path, render_path: Path, reference_paths: list[Path], spec: dict,
                        metric_report: dict) -> Path:
    """Materials for an agent-in-the-loop review: what to look at and the exact schema to answer in."""
    packet = {
        "render": str(render_path),
        "references": [str(p) for p in reference_paths],
        "criteria": CRITERIA,
        "allowed_paths": ALLOWED_PATHS,
        "current_values": current_values(spec),
        "metric_report": {k: metric_report.get(k) for k in ("overall", "scores", "issues")},
        "answer_schema": CRITIQUE_SCHEMA,
        "write_answer_to": str(out_dir / "critique.json"),
    }
    p = out_dir / "review_packet.json"
    p.write_text(json.dumps(packet, indent=2, default=str))
    return p
