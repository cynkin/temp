"""Tests for spec patching and QA decision logic (pure Python, no Blender needed).

Run: .venv/bin/python tests/test_spec_qa.py   (or pytest)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from envgen.qa.critic import all_bounds, normalize, sanitize_patch  # noqa: E402
from envgen.qa.fixers import needs_rerender, propose_fixes  # noqa: E402
from envgen.spec.schema import SpecError, apply_patch, get_path, load_spec, spec_hash  # noqa: E402

SPEC = Path(__file__).resolve().parent.parent / "specs" / "hospital_corridor.yaml"


def test_load_and_hash_deterministic():
    a, b = load_spec(SPEC), load_spec(SPEC)
    assert spec_hash(a) == spec_hash(b)
    assert a["layout"]["type"] == "corridor"
    assert get_path(a, "camera.shots.0.name") == "hero"


def test_minimal_save_roundtrip():
    import tempfile
    from envgen.spec.schema import save_spec
    a = load_spec(SPEC)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.yaml"
        save_spec(a, p)
        b = load_spec(p)
        assert spec_hash(a) == spec_hash(b), "minimal save must be lossless"
        assert len(p.read_text().splitlines()) < 220


def test_patch_ops_and_bounds():
    s = load_spec(SPEC)
    out = apply_patch(s, [
        {"op": "add", "path": "post.exposure", "value": 5.0},              # clamped to 1.5
        {"op": "scale", "path": "atmosphere.density", "value": 2.0},
        {"op": "add", "path": "post.gain", "value": [0.1, 0.0, -0.1]},
        {"op": "set", "path": "lighting.sun.kelvin", "value": 4800},
    ], bounds=all_bounds())
    assert out["post"]["exposure"] == 1.5
    assert abs(out["atmosphere"]["density"] - 2 * s["atmosphere"]["density"]) < 1e-9
    assert out["post"]["gain"][0] > s["post"]["gain"][0]
    assert out["lighting"]["sun"]["kelvin"] == 4800
    assert s["post"]["exposure"] == 0.0, "apply_patch must not mutate its input"


def test_patch_rejects_invalid_result():
    s = load_spec(SPEC)
    try:
        apply_patch(s, [{"op": "set", "path": "layout.width", "value": 0.5}])
    except SpecError:
        return
    raise AssertionError("an implausible corridor width must fail validation")


def test_fixers_resolve_contradictions():
    spec = load_spec(SPEC)
    issues = [{"code": "too_dark", "severity": 0.6}, {"code": "too_bright", "severity": 0.2},
              {"code": "high_contrast", "severity": 0.75}, {"code": "low_contrast", "severity": 0.16},
              {"code": "low_local_contrast", "severity": 0.31}, {"code": "vp_offcenter", "severity": 0.3}]
    fx = propose_fixes(issues, spec)
    paths = {(o["path"], o["op"]): o["value"] for o in fx["ops"]}
    assert paths[("lighting.exposure", "add")] > 0 or paths.get(("post.exposure", "add"), 0) > 0
    assert paths[("post.contrast", "add")] < 0, "high contrast must lower contrast"
    assert {i["code"] for i in fx["unresolved"]} >= {"vp_offcenter", "low_local_contrast"}


def test_minor_issues_ignored_and_post_only_detection():
    spec = load_spec(SPEC)
    fx = propose_fixes([{"code": "too_dark", "severity": 0.05}, {"code": "oversaturated", "severity": 0.3}], spec)
    assert all(o["path"].startswith("post.") for o in fx["ops"])
    assert not needs_rerender(fx["ops"])
    fx2 = propose_fixes([{"code": "too_hazy", "severity": 0.5}], spec)
    assert needs_rerender(fx2["ops"])


def test_critic_sanitizer():
    ok, rejected = sanitize_patch([
        {"op": "add", "path": "post.exposure", "value": 0.3, "reason": "brighter"},
        {"op": "set", "path": "layout.length", "value": 400},                 # not allowed
        {"op": "scale", "path": "atmosphere.density", "value": 50},           # absurd factor
        {"op": "delete", "path": "post.grain", "value": 0},                   # bad op
    ])
    assert [o["path"] for o in ok] == ["post.exposure"]
    assert len(rejected) == 3
    n = normalize({"scores": {"lighting": 4}, "patch": [], "issues": []}, source="t")
    assert 0 < n["score"] < 1 and n["scores"]["lighting"] == 4


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} passed")
