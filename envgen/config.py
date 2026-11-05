"""Runtime configuration resolved from environment variables (never machine-specific paths)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _path_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default


@dataclass(frozen=True)
class Settings:
    repo_root: Path
    output_dir: Path
    asset_library: Path
    references_dir: Path
    render_threads: int
    anthropic_model: str
    anthropic_api_key_present: bool


def get_settings() -> Settings:
    return Settings(
        repo_root=REPO_ROOT,
        output_dir=_path_env("ENVGEN_OUTPUT_DIR", REPO_ROOT / "output"),
        asset_library=_path_env("ENVGEN_ASSET_LIBRARY", REPO_ROOT / "asset_library"),
        references_dir=_path_env("ENVGEN_REFERENCES_DIR", REPO_ROOT / "references"),
        render_threads=int(os.environ.get("ENVGEN_RENDER_THREADS", "0")),  # 0 = auto
        anthropic_model=os.environ.get("ENVGEN_VISION_MODEL", "claude-opus-5-5"),
        anthropic_api_key_present=bool(os.environ.get("ANTHROPIC_API_KEY")),
    )
