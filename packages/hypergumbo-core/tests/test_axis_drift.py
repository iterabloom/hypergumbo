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


def test_iter_axis_set_assignments_skips_a_computed_value(tmp_path: Path):
    """An RHS the walker cannot enumerate statically must be skipped.

    THIS TEST MOVED RATHER THAN BEING DELETED (WI-jinuj). It used to assert
    that a LIST is skipped, which was true and is no longer: a bare list or
    tuple is now collected, because a hardcoded copy of a vocabulary is a
    hardcoded copy however it is bracketed. What remains genuinely skipped is
    a value produced by a CALL — and that case is not a gap, since a constant
    DERIVED from the registry cannot drift from it.
    """
    p = tmp_path / "demo.py"
    p.write_text('_X_KIND = frozenset(_A_KINDS + _B_KINDS)\n')
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


# --------------------------------------------------------------------------
# WI-jinuj: the collector saw only string-literal SETS, so a hardcoded copy
# of a vocabulary written as a TUPLE, a LIST, a DICT or a set of module-local
# NAME references was invisible to all four drift linters at once.
#
# Each widening below was first run in REPORT-ONLY mode over the live tree
# (the item's mandatory first step) before any gate changed; the measured
# yield is recorded in the item. The tests here pin the SHAPES, not that
# yield, because the yield is a property of today's tree.
# --------------------------------------------------------------------------


def _collect(tmp_path, source: str, *, name_filter: str = "EDGE_TYPE"):
    path = tmp_path / "m.py"
    path.write_text(source)
    return {
        target: values
        for _lineno, target, values in iter_axis_set_assignments(
            path, name_filter=name_filter,
        )
    }


def test_bare_tuple_is_collected(tmp_path):
    """``CATALOG_BOUNDARY_TYPES`` was exactly this shape and was invisible."""
    got = _collect(tmp_path, 'EDGE_TYPE_ORDER = ("calls", "imports")\n')
    assert got == {"EDGE_TYPE_ORDER": frozenset({"calls", "imports"})}


def test_bare_list_is_collected(tmp_path):
    got = _collect(tmp_path, 'EDGE_TYPE_ORDER = ["calls", "imports"]\n')
    assert got == {"EDGE_TYPE_ORDER": frozenset({"calls", "imports"})}


def test_dict_keys_are_collected(tmp_path):
    got = _collect(tmp_path, 'EDGE_TYPE_WEIGHTS = {"calls": 1.0, "imports": 0.5}\n')
    assert got == {"EDGE_TYPE_WEIGHTS": frozenset({"calls", "imports"})}


def test_dict_values_are_collected(tmp_path):
    """A dict VALUED by axis values is as much a consumer as one keyed by
    them — ``DEFERRED_CROSSING_SHADOWS`` maps one boundary to another, and
    BOTH of its sides are io-boundary names, so both sides are checked."""
    got = _collect(
        tmp_path, 'EDGE_TYPE_SHADOWS = {"calls": "imports"}\n',
    )
    assert got == {
        "EDGE_TYPE_SHADOWS:keys": frozenset({"calls"}),
        "EDGE_TYPE_SHADOWS:values": frozenset({"imports"}),
    }


def test_dict_with_non_string_keys_reports_its_values_plainly(tmp_path):
    """Only the value side enumerates the vocabulary here, so there is no
    ambiguity to resolve and the plain target name is kept."""
    got = _collect(tmp_path, 'EDGE_TYPE_BY_RANK = {1: "calls", 2: "imports"}\n')
    assert got == {"EDGE_TYPE_BY_RANK": frozenset({"calls", "imports"})}


def test_a_cross_axis_dict_yields_its_two_SIDES_separately(tmp_path):
    """THE SHAPE THAT FORCED THE SIDE SPLIT. ``_READ_TARGET_KIND_BOUNDARY``
    is keyed by ``io_target_kind`` values and VALUED by io-boundary names —
    two different axes in one assignment. Folding both sides into one set
    would force an axis to exclude the whole constant to silence the side
    that is not its own, losing the check on the side that IS."""
    path = tmp_path / "m.py"
    path.write_text('EDGE_TYPE_MAP = {"host_path": "calls"}\n')
    rows = list(iter_axis_set_assignments(path, name_filter="EDGE_TYPE"))
    by_target = {t: v for _, t, v in rows}
    assert by_target["EDGE_TYPE_MAP:keys"] == frozenset({"host_path"})
    assert by_target["EDGE_TYPE_MAP:values"] == frozenset({"calls"})


def test_a_uniform_dict_still_reports_under_its_plain_name(tmp_path):
    """When only one side is string-valued there is no ambiguity to resolve,
    so the plain target name is kept and existing exclusions keep working."""
    got = _collect(tmp_path, 'EDGE_TYPE_WEIGHTS = {"calls": 1.0}\n')
    assert set(got) == {"EDGE_TYPE_WEIGHTS"}


def test_module_local_name_references_resolve(tmp_path):
    """``VALID_BOUNDARY_RULINGS`` is ``frozenset({NAME, NAME})``; the whole
    assignment used to be dropped because the elements are not literals."""
    got = _collect(
        tmp_path,
        'A = "calls"\nB: str = "imports"\n'
        "EDGE_TYPE_SET = frozenset({A, B})\n",
    )
    assert got == {"EDGE_TYPE_SET": frozenset({"calls", "imports"})}


def test_an_unresolvable_name_still_drops_the_assignment(tmp_path):
    """Resolution is deliberately shallow: a name this module does not
    define as a string constant is NOT guessed at. Reporting a partial set
    would let a real drift value hide behind an unresolved sibling."""
    got = _collect(tmp_path, "EDGE_TYPE_SET = frozenset({SOME_IMPORTED_NAME})\n")
    assert got == {}


def test_a_computed_set_remains_invisible_and_that_is_correct(tmp_path):
    """``KNOWN_IO_BOUNDARIES = all_io_boundary_names()`` is DERIVED from the
    registry, so it cannot drift from it — there is nothing for a drift
    linter to check. Recorded as a test so the next reader does not add a
    call-evaluating widening believing it closes a gap."""
    got = _collect(tmp_path, "EDGE_TYPE_SET = frozenset(A + B)\n")
    assert got == {}


def test_a_bare_exclusion_silences_both_sides_of_a_dict(tmp_path: Path):
    """Exclusions written before the side split keep their meaning."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_FOO_KIND_MAP = {"phantom_key": "phantom_value"}\n',
    )
    assert find_drift(
        tmp_path,
        name_filter="FOO_KIND",
        registry_names=frozenset({"alpha"}),
        excluded_target_names=("_FOO_KIND_MAP",),
    ) == []


def test_excluding_one_side_of_a_dict_KEEPS_THE_OTHER_CHECKED(tmp_path: Path):
    """THE POINT OF THE SIDE SPLIT, and the thing that would silently rot.

    Excluding ``NAME:keys`` must not silence ``NAME:values``. Without this
    test the per-side exclusion could degrade into a whole-constant one and
    nothing would fail — the linter would go quiet, which is exactly how a
    drift gate stops being a gate.
    """
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_FOO_KIND_MAP = {"off_axis_key": "phantom_value"}\n',
    )
    offenders = find_drift(
        tmp_path,
        name_filter="FOO_KIND",
        registry_names=frozenset({"alpha"}),
        excluded_target_names=("_FOO_KIND_MAP:keys",),
    )
    assert len(offenders) == 1
    assert "_FOO_KIND_MAP:values" in offenders[0]
    assert "phantom_value" in offenders[0]
    assert "off_axis_key" not in offenders[0]


def test_the_widened_shapes_are_reachable_through_find_drift(tmp_path: Path):
    """Non-vacuity for the widening itself: a planted offender in each newly
    collected shape must be REPORTED, not merely collected."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "t.py",
        '_FOO_KIND_ORDER = ("phantom_tuple",)\n',
    )
    _write(
        tmp_path / "packages" / "demo" / "src" / "d.py",
        '_FOO_KIND_W = {"phantom_dictkey": 1}\n',
    )
    _write(
        tmp_path / "packages" / "demo" / "src" / "n.py",
        'A = "phantom_name"\n_FOO_KIND_S = frozenset({A})\n',
    )
    offenders = find_drift(
        tmp_path,
        name_filter="FOO_KIND",
        registry_names=frozenset({"alpha"}),
    )
    blob = " ".join(offenders)
    assert "phantom_tuple" in blob
    assert "phantom_dictkey" in blob
    assert "phantom_name" in blob
