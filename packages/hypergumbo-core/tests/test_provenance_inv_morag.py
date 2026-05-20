# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for INV-morag PR 1 scaffolding: pass_version + reproducibility_context.

Covers two additive provenance fields landed as foundation for the full
pass-ID redesign (INV-morag tracker):

- ``AnalysisRun.pass_version``: code-hash of the pass implementation. Replaces
  the fake ``-v1`` suffix in pass IDs with a real per-pass version derived
  from ``sha256(inspect.getsource(module))``. The hash changes when the
  module changes; doesn't change when unrelated package code is bumped.

- ``behavior_map['reproducibility_context']``: top-level block capturing the
  L2 reproducibility level (direct deps: hypergumbo version, Python interpreter
  version, tree-sitter library version, per-grammar versions). Explicitly
  documents L3-L5 (transitive deps, OS, hardware) as ``not_captured`` — the
  honest version of "we capture what we can; we disclaim the rest."

Both fields are additive: existing producers default to safe values, existing
consumers ignore unknown fields per the spec's Appendix C.

These tests pin the contract before PR 2 propagates the rename.
"""
from __future__ import annotations

import hashlib
import inspect
import re
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Option A: compute_pass_version helper
# ---------------------------------------------------------------------------


def test_compute_pass_version_returns_sha256_string() -> None:
    """The pass_version is a sha256 hash prefixed with 'sha256:' for clarity."""
    from hypergumbo_core.ir import compute_pass_version

    # Pass any module; result is a sha256:hex string.
    from hypergumbo_core import ir as ir_module
    result = compute_pass_version(ir_module)

    assert result.startswith("sha256:"), result
    # 64 hex chars after the 'sha256:' prefix.
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", result), result


def test_compute_pass_version_is_stable_for_same_module() -> None:
    """Calling twice on the same module yields the same hash."""
    from hypergumbo_core.ir import compute_pass_version
    from hypergumbo_core import ir as ir_module

    a = compute_pass_version(ir_module)
    b = compute_pass_version(ir_module)

    assert a == b


def test_compute_pass_version_differs_across_modules() -> None:
    """Different module source produces different hashes."""
    from hypergumbo_core.ir import compute_pass_version
    from hypergumbo_core import ir as ir_module
    from hypergumbo_core import schema as schema_module

    a = compute_pass_version(ir_module)
    b = compute_pass_version(schema_module)

    assert a != b


def test_compute_pass_version_accepts_function() -> None:
    """Passing a function falls back to hashing its module's source."""
    from hypergumbo_core.ir import compute_pass_version, make_pass_id

    result = compute_pass_version(make_pass_id)
    assert result.startswith("sha256:")


def test_compute_pass_version_matches_explicit_sha256() -> None:
    """The hash matches what you'd get by hashing the module source directly."""
    from hypergumbo_core.ir import compute_pass_version
    from hypergumbo_core import ir as ir_module

    expected_source = inspect.getsource(ir_module)
    expected_hash = (
        "sha256:" + hashlib.sha256(expected_source.encode("utf-8")).hexdigest()
    )

    assert compute_pass_version(ir_module) == expected_hash


# ---------------------------------------------------------------------------
# Option A: AnalysisRun.pass_version field
# ---------------------------------------------------------------------------


def test_analysis_run_has_pass_version_field() -> None:
    """AnalysisRun carries pass_version; defaults to empty string."""
    from hypergumbo_core.ir import AnalysisRun, make_pass_id, PASS_VERSION

    run = AnalysisRun.create(pass_id=make_pass_id("test"), version=PASS_VERSION)
    # Default value is empty string (backward compat — producers opt in).
    assert run.pass_version == ""


def test_analysis_run_create_accepts_pass_version() -> None:
    """AnalysisRun.create accepts and stores pass_version."""
    from hypergumbo_core.ir import AnalysisRun, make_pass_id, PASS_VERSION

    run = AnalysisRun.create(
        pass_id=make_pass_id("test"),
        version=PASS_VERSION,
        pass_version="sha256:abc123",
    )

    assert run.pass_version == "sha256:abc123"


def test_analysis_run_to_dict_serializes_pass_version() -> None:
    """to_dict() round-trips pass_version under the 'pass_version' key."""
    from hypergumbo_core.ir import AnalysisRun, make_pass_id, PASS_VERSION

    run = AnalysisRun.create(
        pass_id=make_pass_id("test"),
        version=PASS_VERSION,
        pass_version="sha256:xyz789",
    )
    serialized = run.to_dict()

    assert serialized["pass_version"] == "sha256:xyz789"


def test_analysis_run_to_dict_omits_empty_pass_version_key_present() -> None:
    """Even when pass_version defaults to empty, the key is present in the dict.

    Honest about "we don't know" rather than implicit. Consumers that ignore
    unknown / empty fields are unaffected; consumers that read the key see
    an explicit empty-string sentinel.
    """
    from hypergumbo_core.ir import AnalysisRun, make_pass_id, PASS_VERSION

    run = AnalysisRun.create(pass_id=make_pass_id("test"), version=PASS_VERSION)
    serialized = run.to_dict()

    assert "pass_version" in serialized
    assert serialized["pass_version"] == ""


# ---------------------------------------------------------------------------
# Option B: reproducibility_context block
# ---------------------------------------------------------------------------


def test_build_reproducibility_context_has_expected_shape() -> None:
    """The reproducibility context block has the L2 shape with not_captured."""
    from hypergumbo_core.schema import build_reproducibility_context

    ctx = build_reproducibility_context()

    assert ctx["level"] == "L2"
    assert "captured" in ctx
    assert "not_captured" in ctx
    assert "implications" in ctx


def test_build_reproducibility_context_captured_includes_l2_fields() -> None:
    """Captured block records hypergumbo, python, tree-sitter (when available)."""
    from hypergumbo_core.schema import build_reproducibility_context

    ctx = build_reproducibility_context()
    captured = ctx["captured"]

    # Always captured.
    assert "hypergumbo_version" in captured
    assert "python_version" in captured
    assert captured["python_version"]  # non-empty
    assert captured["hypergumbo_version"]  # non-empty

    # tree_sitter and grammars are conditional on import success.
    # When available, they are dict / version string respectively.
    if "tree_sitter_version" in captured:
        assert isinstance(captured["tree_sitter_version"], str)
    if "grammars" in captured:
        assert isinstance(captured["grammars"], dict)


def test_build_reproducibility_context_not_captured_documents_disclaim() -> None:
    """not_captured array explicitly disclaims L3-L5 factors."""
    from hypergumbo_core.schema import build_reproducibility_context

    ctx = build_reproducibility_context()
    not_captured = ctx["not_captured"]

    assert isinstance(not_captured, list)
    assert len(not_captured) > 0
    # Disclaim text should mention concrete factors we don't capture.
    joined = " ".join(not_captured).lower()
    assert "os" in joined or "operating" in joined
    assert "transitive" in joined or "pip" in joined or "libc" in joined


def test_build_reproducibility_context_implications_is_string() -> None:
    """implications is a human-readable string describing the contract."""
    from hypergumbo_core.schema import build_reproducibility_context

    ctx = build_reproducibility_context()

    assert isinstance(ctx["implications"], str)
    assert len(ctx["implications"]) > 0


def test_behavior_map_includes_reproducibility_context(tmp_path: Path) -> None:
    """A fresh hypergumbo run on a small fixture writes reproducibility_context."""
    import json
    from hypergumbo_core.cli import run_behavior_map

    (tmp_path / "app.py").write_text("def main():\n    return 42\n")

    out_path = tmp_path / "behavior_map.json"
    run_behavior_map(
        repo_root=tmp_path,
        out_path=out_path,
        include_sketch_precomputed=False,
        progress=False,
    )

    behavior_map = json.loads(out_path.read_text())
    assert "reproducibility_context" in behavior_map, list(behavior_map.keys())

    ctx = behavior_map["reproducibility_context"]
    assert ctx["level"] == "L2"
    assert "captured" in ctx
    assert "not_captured" in ctx
