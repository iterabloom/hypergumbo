# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the parameterized axis-drift detector.

The Edge.edge_type-specific behavior is covered by
``test_edge_types.py``; the tests here exercise the parameterization
itself — synthetic registries with arbitrary ``name_filter`` strings,
multi-root scanning, exclusion-substring overrides, and
search-root absence handling. Each new axis-bearing field that
ADR-0024 introduces will use the same parameterized core, so this
file is the regression suite for the shared infrastructure.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.axis_drift import (
    DEFAULT_EXCLUDED_PATH_SUBSTRINGS,
    DEFAULT_SEARCH_ROOTS,
    find_drift,
    iter_axis_set_assignments,
)


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


# --- find_drift parameterization ---


def test_find_drift_with_synthetic_registry(tmp_path: Path):
    """The helper accepts an arbitrary registry, not just EDGE_TYPES."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_FOO_KIND_VALUES = {"alpha", "phantom"}\n',
    )
    offenders = find_drift(
        tmp_path,
        name_filter="FOO_KIND",
        registry_names=frozenset({"alpha", "beta"}),
    )
    assert len(offenders) == 1
    assert "phantom" in offenders[0]
    assert "_FOO_KIND_VALUES" in offenders[0]


def test_find_drift_clean_with_synthetic_registry(tmp_path: Path):
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_FOO_KIND_VALUES = {"alpha", "beta"}\n',
    )
    assert find_drift(
        tmp_path,
        name_filter="FOO_KIND",
        registry_names=frozenset({"alpha", "beta"}),
    ) == []


# --- Multi-root scanning (WI-zisit-hagud) ---


def test_find_drift_scans_scripts_and_agent_by_default(tmp_path: Path):
    """Default scope includes packages/, scripts/, and .agent/."""
    _write(
        tmp_path / "scripts" / "tool.py",
        '_FOO_KIND = {"phantom_in_scripts"}\n',
    )
    _write(
        tmp_path / ".agent" / "hooks" / "h.py",
        '_FOO_KIND = {"phantom_in_agent"}\n',
    )
    offenders = find_drift(
        tmp_path,
        name_filter="FOO_KIND",
        registry_names=frozenset({"alpha"}),
    )
    assert len(offenders) == 2
    assert any("scripts/tool.py" in o for o in offenders)
    assert any(".agent/hooks/h.py" in o for o in offenders)


def test_find_drift_respects_custom_search_roots(tmp_path: Path):
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_FOO_KIND = {"phantom_packages"}\n',
    )
    _write(
        tmp_path / "scripts" / "tool.py",
        '_FOO_KIND = {"phantom_scripts"}\n',
    )
    offenders = find_drift(
        tmp_path,
        name_filter="FOO_KIND",
        registry_names=frozenset({"alpha"}),
        search_roots=["packages"],
    )
    assert len(offenders) == 1
    assert "packages" in offenders[0]
    assert "scripts" not in offenders[0]


def test_find_drift_handles_missing_search_roots(tmp_path: Path):
    """Search roots that don't exist on disk are silently skipped."""
    assert find_drift(
        tmp_path,
        name_filter="FOO_KIND",
        registry_names=frozenset({"alpha"}),
    ) == []


def test_find_drift_skips_test_directories_in_all_roots(tmp_path: Path):
    _write(
        tmp_path / "packages" / "demo" / "tests" / "test_x.py",
        '_FOO_KIND = {"phantom"}\n',
    )
    _write(
        tmp_path / "scripts" / "tests" / "tool_test.py",
        '_FOO_KIND = {"phantom"}\n',
    )
    assert find_drift(
        tmp_path,
        name_filter="FOO_KIND",
        registry_names=frozenset({"alpha"}),
    ) == []


def test_find_drift_respects_custom_excluded_substrings(tmp_path: Path):
    _write(
        tmp_path / "scripts" / "vendor" / "tool.py",
        '_FOO_KIND = {"phantom"}\n',
    )
    assert find_drift(
        tmp_path,
        name_filter="FOO_KIND",
        registry_names=frozenset({"alpha"}),
        excluded_path_substrings=["/vendor/"],
    ) == []


# --- iter_axis_set_assignments ---


def test_iter_axis_set_assignments_finds_set_literal(tmp_path: Path):
    p = tmp_path / "demo.py"
    p.write_text('_X_KIND = {"a", "b"}\n')
    results = list(iter_axis_set_assignments(p, name_filter="X_KIND"))
    assert len(results) == 1
    lineno, name, values = results[0]
    assert name == "_X_KIND"
    assert values == frozenset({"a", "b"})
    assert lineno == 1


def test_iter_axis_set_assignments_finds_frozenset_literal(tmp_path: Path):
    p = tmp_path / "demo.py"
    p.write_text('_X_KIND = frozenset({"a", "b"})\n')
    results = list(iter_axis_set_assignments(p, name_filter="X_KIND"))
    assert len(results) == 1
    assert results[0][2] == frozenset({"a", "b"})


def test_iter_axis_set_assignments_finds_annotated_assignment(
    tmp_path: Path,
):
    p = tmp_path / "demo.py"
    p.write_text(
        '_X_KIND: frozenset[str] = frozenset({"a"})\n',
    )
    results = list(iter_axis_set_assignments(p, name_filter="X_KIND"))
    assert len(results) == 1
    assert results[0][1] == "_X_KIND"


def test_iter_axis_set_assignments_skips_annotated_without_value(
    tmp_path: Path,
):
    """``_X_KIND: frozenset[str]`` with no RHS is a type declaration,
    not an assignment with a value — must not yield anything."""
    p = tmp_path / "demo.py"
    p.write_text("_X_KIND: frozenset[str]\n")
    assert list(iter_axis_set_assignments(p, name_filter="X_KIND")) == []


def test_iter_axis_set_assignments_filters_by_name(tmp_path: Path):
    p = tmp_path / "demo.py"
    p.write_text('_OTHER = {"a"}\n_X_KIND = {"b"}\n')
    results = list(iter_axis_set_assignments(p, name_filter="X_KIND"))
    assert len(results) == 1
    assert results[0][1] == "_X_KIND"


def test_iter_axis_set_assignments_skips_non_string_elements(
    tmp_path: Path,
):
    """A set containing non-string elements (e.g., ints) is not a
    candidate for axis-value drift detection and must be skipped."""
    p = tmp_path / "demo.py"
    p.write_text("_X_KIND = {1, 2, 3}\n")
    assert list(iter_axis_set_assignments(p, name_filter="X_KIND")) == []


def test_iter_axis_set_assignments_skips_empty_set(tmp_path: Path):
    p = tmp_path / "demo.py"
    p.write_text('_X_KIND = frozenset({})\n')
    assert list(iter_axis_set_assignments(p, name_filter="X_KIND")) == []


def test_iter_axis_set_assignments_skips_non_set_value(tmp_path: Path):
    """RHS that isn't a set literal or frozenset() call (e.g., a list)
    must be skipped."""
    p = tmp_path / "demo.py"
    p.write_text('_X_KIND = ["a", "b"]\n')
    assert list(iter_axis_set_assignments(p, name_filter="X_KIND")) == []


def test_iter_axis_set_assignments_skips_multi_target_assign(
    tmp_path: Path,
):
    """Tuple/multi-target assignments aren't single-name registry
    declarations; the AST walker must skip them."""
    p = tmp_path / "demo.py"
    p.write_text('_A_X_KIND, _B_X_KIND = {"a"}, {"b"}\n')
    assert list(iter_axis_set_assignments(p, name_filter="X_KIND")) == []


# --- Defaults are stable as exposed constants ---


def test_default_search_roots_includes_three_known_dirs():
    assert "packages" in DEFAULT_SEARCH_ROOTS
    assert "scripts" in DEFAULT_SEARCH_ROOTS
    assert ".agent" in DEFAULT_SEARCH_ROOTS


def test_default_excluded_path_substrings_includes_tests():
    assert "/tests/" in DEFAULT_EXCLUDED_PATH_SUBSTRINGS


# --- Strict-mode axis-principle enforcement (WI-variv-lujug) ---


def test_find_drift_strict_flags_off_axis_registry_member(tmp_path: Path):
    """Strict mode flags consumer-set values that are in the registry
    but on a disallowed axis. The membership check passes (no
    unregistered values), but the off-axis check fires."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_X_KIND = {"alpha", "deprecated_x"}\n',
    )
    offenders = find_drift(
        tmp_path,
        name_filter="X_KIND",
        registry_names=frozenset({"alpha", "deprecated_x"}),
        allowed_axis_names=frozenset({"canonical"}),
        name_to_axis={"alpha": "canonical", "deprecated_x": "endpoint_shape"},
    )
    assert len(offenders) == 1
    assert "deprecated_x" in offenders[0]
    assert "not on allowed axis" in offenders[0]
    assert "['canonical']" in offenders[0]


def test_find_drift_strict_clean_when_all_values_on_allowed_axis(
    tmp_path: Path,
):
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_X_KIND = {"alpha", "beta"}\n',
    )
    assert find_drift(
        tmp_path,
        name_filter="X_KIND",
        registry_names=frozenset({"alpha", "beta"}),
        allowed_axis_names=frozenset({"canonical"}),
        name_to_axis={"alpha": "canonical", "beta": "canonical"},
    ) == []


def test_find_drift_strict_permissive_pending(tmp_path: Path):
    """Per WI-variv-lujug design: pending_classification values are
    real edges produced by analyzers awaiting per-family audit;
    consumers may legitimately reference them. Strict mode allows
    multiple axis names — typically {relationship, pending_classification}.
    """
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_X_KIND = {"alpha", "pending_value"}\n',
    )
    assert find_drift(
        tmp_path,
        name_filter="X_KIND",
        registry_names=frozenset({"alpha", "pending_value"}),
        allowed_axis_names=frozenset({"canonical", "pending"}),
        name_to_axis={"alpha": "canonical", "pending_value": "pending"},
    ) == []


def test_find_drift_strict_reports_both_drift_and_off_axis(tmp_path: Path):
    """When a consumer set has both unregistered drift AND off-axis
    registry values, strict mode reports both as separate offenders."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_X_KIND = {"alpha", "phantom_value", "deprecated_x"}\n',
    )
    offenders = find_drift(
        tmp_path,
        name_filter="X_KIND",
        registry_names=frozenset({"alpha", "deprecated_x"}),
        allowed_axis_names=frozenset({"canonical"}),
        name_to_axis={"alpha": "canonical", "deprecated_x": "endpoint_shape"},
    )
    assert len(offenders) == 2
    drift_msgs = [o for o in offenders if "not in canonical registry" in o]
    off_axis_msgs = [o for o in offenders if "not on allowed axis" in o]
    assert len(drift_msgs) == 1
    assert "phantom_value" in drift_msgs[0]
    assert len(off_axis_msgs) == 1
    assert "deprecated_x" in off_axis_msgs[0]


def test_find_drift_default_does_not_enforce_axis_principle(tmp_path: Path):
    """Default mode (allowed_axis_names=None) keeps the original
    'subset of registry' behavior — off-axis registry values are
    silently allowed. Preserves backward compatibility."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_X_KIND = {"alpha", "deprecated_x"}\n',
    )
    assert find_drift(
        tmp_path,
        name_filter="X_KIND",
        registry_names=frozenset({"alpha", "deprecated_x"}),
    ) == []


def test_find_drift_strict_requires_name_to_axis():
    """Passing allowed_axis_names without name_to_axis raises ValueError —
    the linter has no way to look up axes otherwise. Catches caller
    bugs at the call site rather than producing a silent wrong answer.
    """
    import pytest
    with pytest.raises(ValueError, match="name_to_axis"):
        find_drift(
            Path("/nonexistent"),
            name_filter="X_KIND",
            registry_names=frozenset({"alpha"}),
            allowed_axis_names=frozenset({"canonical"}),
        )
