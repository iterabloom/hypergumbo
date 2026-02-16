# SPDX-License-Identifier: MPL-2.0
"""Tests for hypergumbo_tracker.validation.

Covers single-file structural checks, value validation, field schema validation,
cross-file validation, config comparison, lock violation detection, and SimHash
duplicate warnings.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hypergumbo_tracker.models import (
    FieldSchema,
    KindConfig,
    TrackerConfig,
)
from hypergumbo_tracker.validation import (
    ValidationResult,
    _check_before_cycles,
    _check_config_comparison,
    _check_dangling_parents,
    _check_deferred_justification,
    _check_embedding_duplicates,
    _check_id_prefix_mismatch,
    _check_lock_violations,
    _check_simhash_duplicates,
    _edit_distance,
    _suggest_field,
    _validate_create_values,
    _validate_field_value,
    _validate_fields_schema,
    _validate_timestamp,
    _validate_update_values,
    validate_all,
    validate_ops_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> TrackerConfig:
    """Create a minimal TrackerConfig for testing."""
    return TrackerConfig(
        kinds={
            "invariant": KindConfig(
                prefix="INV",
                description="Test invariant",
                fields_schema={
                    "statement": FieldSchema(type="text", required=True),
                    "root_cause": FieldSchema(type="text", required=True),
                    "progress_pct": FieldSchema(type="integer", min=0, max=100),
                    "regression_tests": FieldSchema(type="list"),
                    "verified": FieldSchema(type="boolean"),
                },
            ),
            "work_item": KindConfig(prefix="WI", description="Test work item"),
        },
        statuses=["todo_hard", "todo_soft", "in_progress", "done", "deferred", "wont_do"],
        blocking_statuses=["todo_hard", "todo_soft"],
        resolved_statuses=["done", "deferred", "wont_do"],
        agent_usernames=["*_agent"],
        lamport_branches=["dev", "main"],
    )


def _write_ops(ops_dir: Path, item_id: str, yaml_text: str) -> Path:
    """Write ops YAML to a file."""
    path = ops_dir / f".{item_id}.ops"
    path.write_text(textwrap.dedent(yaml_text))
    return path


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------


class TestValidationResult:
    def test_ok_when_no_errors(self) -> None:
        r = ValidationResult()
        assert r.ok

    def test_not_ok_when_errors(self) -> None:
        r = ValidationResult(errors=["bad"])
        assert not r.ok

    def test_merge(self) -> None:
        r1 = ValidationResult(errors=["e1"], warnings=["w1"])
        r2 = ValidationResult(errors=["e2"], warnings=["w2"])
        r1.merge(r2)
        assert r1.errors == ["e1", "e2"]
        assert r1.warnings == ["w1", "w2"]


# ---------------------------------------------------------------------------
# Edit distance
# ---------------------------------------------------------------------------


class TestEditDistance:
    def test_identical(self) -> None:
        assert _edit_distance("abc", "abc") == 0

    def test_one_insertion(self) -> None:
        assert _edit_distance("abc", "abcd") == 1

    def test_one_substitution(self) -> None:
        assert _edit_distance("abc", "axc") == 1

    def test_empty(self) -> None:
        assert _edit_distance("", "abc") == 3
        assert _edit_distance("abc", "") == 3

    def test_both_empty(self) -> None:
        assert _edit_distance("", "") == 0


# ---------------------------------------------------------------------------
# Single-file structural checks
# ---------------------------------------------------------------------------


class TestValidateOpsFileStructural:
    def test_malformed_yaml(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = ops_dir / ".TEST-id.ops"
        path.write_text("{{{{bad yaml")
        result = validate_ops_file(path, _make_config())
        assert not result.ok
        assert any("malformed YAML" in e for e in result.errors)

    def test_empty_file(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = ops_dir / ".TEST-id.ops"
        path.write_text("")
        result = validate_ops_file(path, _make_config())
        assert not result.ok
        assert any("empty ops file" in e for e in result.errors)

    def test_first_op_not_create(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: update
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              set:
                status: done
        """)
        result = validate_ops_file(path, _make_config())
        assert not result.ok
        assert any("first op must be 'create'" in e for e in result.errors)

    def test_missing_required_field(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              clock: 1
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 2
        """)
        result = validate_ops_file(path, _make_config())
        assert not result.ok
        assert any("missing required field 'nonce'" in e for e in result.errors)

    def test_unknown_op_type(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 2
            - op: foobar
              at: "2026-01-01T00:01:00Z"
              by: agent
              actor: test_agent
              clock: 2
              nonce: b2c3
        """)
        result = validate_ops_file(path, _make_config())
        assert not result.ok
        assert any("unknown op type 'foobar'" in e for e in result.errors)

    def test_valid_file(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "WI-test", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 2
        """)
        result = validate_ops_file(path, _make_config())
        assert result.ok


# ---------------------------------------------------------------------------
# Value validation
# ---------------------------------------------------------------------------


class TestValueValidation:
    def test_unknown_kind(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: nonexistent
                title: "Test"
                status: todo_hard
                priority: 2
        """)
        result = validate_ops_file(path, _make_config())
        assert any("unknown kind 'nonexistent'" in e for e in result.errors)

    def test_invalid_status(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: invalid_status
                priority: 2
        """)
        result = validate_ops_file(path, _make_config())
        assert any("unknown status 'invalid_status'" in e for e in result.errors)

    def test_priority_not_int(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: "high"
        """)
        result = validate_ops_file(path, _make_config())
        assert any("priority must be int 0-4" in e for e in result.errors)

    def test_priority_out_of_range(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 9
        """)
        result = validate_ops_file(path, _make_config())
        assert any("priority must be int 0-4, got 9" in e for e in result.errors)

    def test_priority_is_bool(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: true
        """)
        result = validate_ops_file(path, _make_config())
        assert any("priority must be int 0-4" in e for e in result.errors)

    def test_invalid_timestamp(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: create
              at: "not-a-timestamp"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 2
        """)
        result = validate_ops_file(path, _make_config())
        assert any("invalid timestamp" in e for e in result.errors)

    def test_valid_timestamp(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 2
        """)
        result = validate_ops_file(path, _make_config())
        assert result.ok

    def test_update_invalid_status(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 2
            - op: update
              at: "2026-01-01T00:01:00Z"
              by: agent
              actor: test_agent
              clock: 2
              nonce: b2c3
              set:
                status: bad_status
        """)
        result = validate_ops_file(path, _make_config())
        assert any("unknown status 'bad_status'" in e for e in result.errors)

    def test_update_invalid_priority(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 2
            - op: update
              at: "2026-01-01T00:01:00Z"
              by: agent
              actor: test_agent
              clock: 2
              nonce: b2c3
              set:
                priority: "high"
        """)
        result = validate_ops_file(path, _make_config())
        assert any("priority must be int 0-4" in e for e in result.errors)

    def test_update_priority_out_of_range(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 2
            - op: update
              at: "2026-01-01T00:01:00Z"
              by: agent
              actor: test_agent
              clock: 2
              nonce: b2c3
              set:
                priority: 7
        """)
        result = validate_ops_file(path, _make_config())
        assert any("priority must be int 0-4, got 7" in e for e in result.errors)

    def test_update_priority_bool(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 2
            - op: update
              at: "2026-01-01T00:01:00Z"
              by: agent
              actor: test_agent
              clock: 2
              nonce: b2c3
              set:
                priority: false
        """)
        result = validate_ops_file(path, _make_config())
        assert any("priority must be int 0-4" in e for e in result.errors)

    def test_create_data_not_dict(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data: "not a dict"
        """)
        result = validate_ops_file(path, _make_config())
        assert any("'data' must be a dict" in e for e in result.errors)

    def test_update_set_not_dict(self, tmp_path: Path) -> None:
        """update op with 'set' as non-dict is silently skipped (line 232)."""
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 2
            - op: update
              at: "2026-01-01T00:01:00Z"
              by: agent
              actor: test_agent
              clock: 2
              nonce: b2c3
              set: "not a dict"
        """)
        result = validate_ops_file(path, _make_config())
        # No errors about update values since set is not a dict — early return
        assert not any("unknown status" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Field schema validation
# ---------------------------------------------------------------------------


class TestFieldSchemaValidation:
    def test_required_field_missing(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "INV-test", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: invariant
                title: "Test"
                status: todo_hard
                priority: 2
                fields:
                  statement: "test statement"
        """)
        result = validate_ops_file(path, _make_config())
        assert any("required field 'root_cause' missing" in e for e in result.errors)

    def test_type_mismatch_text(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "INV-test", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: invariant
                title: "Test"
                status: todo_hard
                priority: 2
                fields:
                  statement: 42
                  root_cause: "test"
        """)
        result = validate_ops_file(path, _make_config())
        assert any("expects text, got int" in e for e in result.errors)

    def test_type_mismatch_integer(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "INV-test", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: invariant
                title: "Test"
                status: todo_hard
                priority: 2
                fields:
                  statement: "test"
                  root_cause: "test"
                  progress_pct: "fifty"
        """)
        result = validate_ops_file(path, _make_config())
        assert any("expects integer, got str" in e for e in result.errors)

    def test_integer_below_min(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "INV-test", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: invariant
                title: "Test"
                status: todo_hard
                priority: 2
                fields:
                  statement: "test"
                  root_cause: "test"
                  progress_pct: -5
        """)
        result = validate_ops_file(path, _make_config())
        assert any("below minimum 0" in e for e in result.errors)

    def test_integer_above_max(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "INV-test", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: invariant
                title: "Test"
                status: todo_hard
                priority: 2
                fields:
                  statement: "test"
                  root_cause: "test"
                  progress_pct: 150
        """)
        result = validate_ops_file(path, _make_config())
        assert any("above maximum 100" in e for e in result.errors)

    def test_integer_is_bool(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "INV-test", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: invariant
                title: "Test"
                status: todo_hard
                priority: 2
                fields:
                  statement: "test"
                  root_cause: "test"
                  progress_pct: true
        """)
        result = validate_ops_file(path, _make_config())
        assert any("expects integer, got bool" in e for e in result.errors)

    def test_type_mismatch_list(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "INV-test", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: invariant
                title: "Test"
                status: todo_hard
                priority: 2
                fields:
                  statement: "test"
                  root_cause: "test"
                  regression_tests: "not a list"
        """)
        result = validate_ops_file(path, _make_config())
        assert any("expects list, got str" in e for e in result.errors)

    def test_type_mismatch_boolean(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "INV-test", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: invariant
                title: "Test"
                status: todo_hard
                priority: 2
                fields:
                  statement: "test"
                  root_cause: "test"
                  verified: "yes"
        """)
        result = validate_ops_file(path, _make_config())
        assert any("expects boolean, got str" in e for e in result.errors)

    def test_unknown_field_with_suggestion(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "INV-test", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: invariant
                title: "Test"
                status: todo_hard
                priority: 2
                fields:
                  statement: "test"
                  root_cause: "test"
                  root_cuase: "typo"
        """)
        result = validate_ops_file(path, _make_config())
        assert any("Did you mean 'root_cause'" in w for w in result.warnings)

    def test_unknown_field_no_suggestion(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "INV-test", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: invariant
                title: "Test"
                status: todo_hard
                priority: 2
                fields:
                  statement: "test"
                  root_cause: "test"
                  zzzzzzzzz: "very different"
        """)
        result = validate_ops_file(path, _make_config())
        assert any("unknown field 'zzzzzzzzz'" in w for w in result.warnings)
        assert not any("Did you mean" in w for w in result.warnings)

    def test_fields_not_dict(self, tmp_path: Path) -> None:
        """When fields is not a dict, validate_fields_schema should handle it."""
        result = ValidationResult()
        schema = {"statement": FieldSchema(type="text", required=True)}
        _validate_fields_schema(".test.ops", 0, "not a dict", schema, result)
        assert any("required field 'statement' missing" in e for e in result.errors)

    def test_null_field_value_valid(self, tmp_path: Path) -> None:
        """Null values should pass type checks (they're just unset)."""
        result = ValidationResult()
        fs = FieldSchema(type="text")
        _validate_field_value(".test.ops", 0, "statement", None, fs, result)
        assert result.ok

    def test_null_integer_value_valid(self) -> None:
        result = ValidationResult()
        fs = FieldSchema(type="integer", min=0, max=100)
        _validate_field_value(".test.ops", 0, "pct", None, fs, result)
        assert result.ok

    def test_null_list_value_valid(self) -> None:
        result = ValidationResult()
        fs = FieldSchema(type="list")
        _validate_field_value(".test.ops", 0, "items", None, fs, result)
        assert result.ok

    def test_null_boolean_value_valid(self) -> None:
        result = ValidationResult()
        fs = FieldSchema(type="boolean")
        _validate_field_value(".test.ops", 0, "flag", None, fs, result)
        assert result.ok


# ---------------------------------------------------------------------------
# Suggest field
# ---------------------------------------------------------------------------


class TestSuggestField:
    def test_close_match(self) -> None:
        assert _suggest_field("statment", ["statement", "root_cause"]) == ["statement"]

    def test_no_match(self) -> None:
        assert _suggest_field("zzzzzzz", ["statement", "root_cause"]) == []


# ---------------------------------------------------------------------------
# Lock violation detection
# ---------------------------------------------------------------------------


class TestLockViolation:
    def test_agent_touches_locked_field(self) -> None:
        result = ValidationResult()
        ops = [
            {"op": "lock", "lock": ["priority"]},
            {"op": "update", "by": "agent", "set": {"priority": 0}},
        ]
        _check_lock_violations(".test.ops", ops, result)
        assert any("locked field(s)" in w for w in result.warnings)

    def test_human_touches_locked_field_ok(self) -> None:
        result = ValidationResult()
        ops = [
            {"op": "lock", "lock": ["priority"]},
            {"op": "update", "by": "human", "set": {"priority": 0}},
        ]
        _check_lock_violations(".test.ops", ops, result)
        assert not result.warnings

    def test_unlock_removes_lock(self) -> None:
        result = ValidationResult()
        ops = [
            {"op": "lock", "lock": ["priority"]},
            {"op": "unlock", "unlock": ["priority"]},
            {"op": "update", "by": "agent", "set": {"priority": 0}},
        ]
        _check_lock_violations(".test.ops", ops, result)
        assert not result.warnings

    def test_agent_touches_locked_via_add(self) -> None:
        result = ValidationResult()
        ops = [
            {"op": "lock", "lock": ["tags"]},
            {"op": "update", "by": "agent", "set": {}, "add": {"tags": ["new"]}},
        ]
        _check_lock_violations(".test.ops", ops, result)
        assert any("locked field(s)" in w for w in result.warnings)

    def test_agent_touches_locked_via_remove(self) -> None:
        result = ValidationResult()
        ops = [
            {"op": "lock", "lock": ["tags"]},
            {"op": "update", "by": "agent", "set": {}, "remove": {"tags": ["old"]}},
        ]
        _check_lock_violations(".test.ops", ops, result)
        assert any("locked field(s)" in w for w in result.warnings)

    def test_check_locks_via_validate_ops_file(self, tmp_path: Path) -> None:
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        path = _write_ops(ops_dir, "TEST-id", """\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 2
            - op: lock
              at: "2026-01-01T00:01:00Z"
              by: human
              actor: jgstern
              clock: 2
              nonce: b2c3
              lock: [priority]
            - op: update
              at: "2026-01-01T00:02:00Z"
              by: agent
              actor: test_agent
              clock: 3
              nonce: c3d4
              set:
                priority: 0
        """)
        result = validate_ops_file(path, _make_config(), check_locks=True)
        assert any("locked field(s)" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Cross-file validation
# ---------------------------------------------------------------------------


class TestCrossFileValidation:
    def test_duplicate_ids_across_tiers(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        canonical_ops = tracker_root / "tracker" / ".ops"
        workspace_ops = tracker_root / "tracker-workspace" / ".ops"
        canonical_ops.mkdir(parents=True)
        workspace_ops.mkdir(parents=True)

        ops_content = textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 2
        """)
        (canonical_ops / ".WI-test-id.ops").write_text(ops_content)
        (workspace_ops / ".WI-test-id.ops").write_text(ops_content)

        config = _make_config()
        result = validate_all(tracker_root, config)
        assert any("duplicate ID" in e for e in result.errors)

    def test_dangling_parent(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        canonical_ops = tracker_root / "tracker" / ".ops"
        canonical_ops.mkdir(parents=True)
        # Ensure workspace dirs exist
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)

        ops_content = textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 2
                parent: WI-nonexistent
        """)
        (canonical_ops / ".WI-test.ops").write_text(ops_content)

        result = validate_all(tracker_root, _make_config())
        assert any("dangling parent" in e for e in result.errors)

    def test_id_prefix_mismatch(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        canonical_ops = tracker_root / "tracker" / ".ops"
        canonical_ops.mkdir(parents=True)
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)

        ops_content = textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: invariant
                title: "Test"
                status: todo_hard
                priority: 2
        """)
        # File named WI- but kind is invariant
        (canonical_ops / ".WI-wrong-prefix.ops").write_text(ops_content)

        result = validate_all(tracker_root, _make_config())
        assert any("ID prefix" in e for e in result.errors)

    def test_id_without_hyphen_skipped(self) -> None:
        """IDs without a hyphen are skipped in prefix check (line 517)."""
        from hypergumbo_tracker.models import CompiledItem
        items = {
            "nohyphen": ("canonical", CompiledItem(
                id="nohyphen", kind="work_item", title="Test",
                status="todo_hard",
            )),
        }
        result = ValidationResult()
        _check_id_prefix_mismatch(items, _make_config(), result)
        assert not result.errors

    def test_before_cycle(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        canonical_ops = tracker_root / "tracker" / ".ops"
        canonical_ops.mkdir(parents=True)
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)

        # Item A has before: [B], Item B has before: [A] → cycle
        ops_a = textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "A"
                status: todo_hard
                priority: 2
                before: [WI-item-b]
        """)
        ops_b = textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: b2c3
              data:
                kind: work_item
                title: "B"
                status: todo_hard
                priority: 2
                before: [WI-item-a]
        """)
        (canonical_ops / ".WI-item-a.ops").write_text(ops_a)
        (canonical_ops / ".WI-item-b.ops").write_text(ops_b)

        result = validate_all(tracker_root, _make_config())
        assert any("cycle in before links" in e for e in result.errors)

    def test_deferred_without_justification(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        canonical_ops = tracker_root / "tracker" / ".ops"
        canonical_ops.mkdir(parents=True)
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)

        ops_content = textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: deferred
                priority: 2
        """)
        (canonical_ops / ".WI-test.ops").write_text(ops_content)

        result = validate_all(tracker_root, _make_config())
        assert any("deferred" in e and "justification" in e for e in result.errors)

    def test_deferred_with_justification_ok(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        canonical_ops = tracker_root / "tracker" / ".ops"
        canonical_ops.mkdir(parents=True)
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)

        ops_content = textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: deferred
                priority: 2
                justification: "Blocked on external dependency"
        """)
        (canonical_ops / ".WI-test.ops").write_text(ops_content)

        result = validate_all(tracker_root, _make_config())
        assert not any("justification" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Config comparison
# ---------------------------------------------------------------------------


class TestConfigComparison:
    def test_extra_kind_in_config(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        tracker_dir = tracker_root / "tracker"
        tracker_dir.mkdir(parents=True)

        import yaml
        config_data = {"kinds": {"invariant": {"prefix": "INV"}, "extra": {"prefix": "EX"}}}
        template_data = {"kinds": {"invariant": {"prefix": "INV"}}}
        (tracker_dir / "config.yaml").write_text(yaml.dump(config_data))
        (tracker_dir / "config.yaml.template").write_text(yaml.dump(template_data))

        result = ValidationResult()
        _check_config_comparison(tracker_root, result)
        assert any("'extra' in config.yaml but not in config.yaml.template" in w for w in result.warnings)

    def test_extra_kind_in_template(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        tracker_dir = tracker_root / "tracker"
        tracker_dir.mkdir(parents=True)

        import yaml
        config_data = {"kinds": {"invariant": {"prefix": "INV"}}}
        template_data = {"kinds": {"invariant": {"prefix": "INV"}, "template_only": {"prefix": "TO"}}}
        (tracker_dir / "config.yaml").write_text(yaml.dump(config_data))
        (tracker_dir / "config.yaml.template").write_text(yaml.dump(template_data))

        result = ValidationResult()
        _check_config_comparison(tracker_root, result)
        assert any("'template_only' in config.yaml.template but not in config.yaml" in w for w in result.warnings)

    def test_no_comparison_when_files_missing(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        tracker_dir = tracker_root / "tracker"
        tracker_dir.mkdir(parents=True)

        result = ValidationResult()
        _check_config_comparison(tracker_root, result)
        assert not result.warnings


# ---------------------------------------------------------------------------
# SimHash duplicate warnings
# ---------------------------------------------------------------------------


class TestSimHashDuplicates:
    def test_near_duplicate_warning(self) -> None:
        from hypergumbo_tracker.models import CompiledItem
        items = {
            "WI-a": ("canonical", CompiledItem(
                id="WI-a", kind="work_item", title="Fix the login bug in auth module",
                status="todo_hard", description="The login bug in auth module needs fixing",
            )),
            "WI-b": ("canonical", CompiledItem(
                id="WI-b", kind="work_item", title="Fix the login bug in auth module",
                status="todo_hard", description="The login bug in auth module needs fixing",
            )),
        }
        result = ValidationResult()
        _check_simhash_duplicates(items, result)
        assert any("near-duplicate" in w for w in result.warnings)

    def test_no_warning_when_marked_not_duplicate(self) -> None:
        from hypergumbo_tracker.models import CompiledItem
        items = {
            "WI-a": ("canonical", CompiledItem(
                id="WI-a", kind="work_item", title="Fix the login bug",
                status="todo_hard", not_duplicate_of=["WI-b"],
            )),
            "WI-b": ("canonical", CompiledItem(
                id="WI-b", kind="work_item", title="Fix the login bug",
                status="todo_hard",
            )),
        }
        result = ValidationResult()
        _check_simhash_duplicates(items, result)
        assert not result.warnings

    def test_no_warning_when_marked_duplicate(self) -> None:
        from hypergumbo_tracker.models import CompiledItem
        items = {
            "WI-a": ("canonical", CompiledItem(
                id="WI-a", kind="work_item", title="Fix the login bug",
                status="todo_hard", duplicate_of=["WI-b"],
            )),
            "WI-b": ("canonical", CompiledItem(
                id="WI-b", kind="work_item", title="Fix the login bug",
                status="todo_hard",
            )),
        }
        result = ValidationResult()
        _check_simhash_duplicates(items, result)
        assert not result.warnings

    def test_simhash_with_field_values(self) -> None:
        """SimHash incorporates string and list field values (lines 625-628)."""
        from hypergumbo_tracker.models import CompiledItem
        items = {
            "WI-a": ("canonical", CompiledItem(
                id="WI-a", kind="work_item", title="Fix the login bug in auth module",
                status="todo_hard", description="The login bug in auth module needs fixing",
                fields={"statement": "identical statement text", "tags_list": ["tag1", "tag2"]},
            )),
            "WI-b": ("canonical", CompiledItem(
                id="WI-b", kind="work_item", title="Fix the login bug in auth module",
                status="todo_hard", description="The login bug in auth module needs fixing",
                fields={"statement": "identical statement text", "tags_list": ["tag1", "tag2"]},
            )),
        }
        result = ValidationResult()
        _check_simhash_duplicates(items, result)
        # With identical content + fields, should detect near-duplicate
        assert any("near-duplicate" in w for w in result.warnings)

    def test_different_items_no_warning(self) -> None:
        from hypergumbo_tracker.models import CompiledItem
        items = {
            "WI-a": ("canonical", CompiledItem(
                id="WI-a", kind="work_item", title="Fix auth login",
                status="todo_hard", description="Authentication module has a login bug",
            )),
            "WI-b": ("canonical", CompiledItem(
                id="WI-b", kind="work_item",
                title="Add database migration for new table schema version upgrade process",
                status="todo_hard",
                description="Database migration must handle existing data and rollback scenarios",
            )),
        }
        result = ValidationResult()
        _check_simhash_duplicates(items, result)
        assert not result.warnings


# ---------------------------------------------------------------------------
# Strict mode and validate_all integration
# ---------------------------------------------------------------------------


class TestValidateAllIntegration:
    def test_strict_mode_promotes_warnings(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        canonical_ops = tracker_root / "tracker" / ".ops"
        canonical_ops.mkdir(parents=True)
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)

        # Create a file that will produce a warning (unknown field)
        ops_content = textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: invariant
                title: "Test"
                status: todo_hard
                priority: 2
                fields:
                  statement: "test"
                  root_cause: "test"
                  unknown_fld: "extra"
        """)
        (canonical_ops / ".INV-test.ops").write_text(ops_content)

        result = validate_all(tracker_root, _make_config(), strict=True)
        # Warnings promoted to errors
        assert not result.ok
        assert not result.warnings

    def test_check_similar_flag(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        canonical_ops = tracker_root / "tracker" / ".ops"
        canonical_ops.mkdir(parents=True)
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)

        # Two identical items
        ops_a = textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Fix auth bug"
                status: todo_hard
                priority: 2
                description: "The auth module login flow has a bug"
        """)
        ops_b = textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: b2c3
              data:
                kind: work_item
                title: "Fix auth bug"
                status: todo_hard
                priority: 2
                description: "The auth module login flow has a bug"
        """)
        (canonical_ops / ".WI-item-a.ops").write_text(ops_a)
        (canonical_ops / ".WI-item-b.ops").write_text(ops_b)

        result = validate_all(tracker_root, _make_config(), check_similar=True)
        assert any("near-duplicate" in w for w in result.warnings)

    def test_empty_tracker(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        (tracker_root / "tracker" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)

        result = validate_all(tracker_root, _make_config())
        assert result.ok

    def test_nonexistent_dirs(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        (tracker_root / "tracker").mkdir(parents=True)

        result = validate_all(tracker_root, _make_config())
        assert result.ok

    def test_skips_non_ops_files(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        canonical_ops = tracker_root / "tracker" / ".ops"
        canonical_ops.mkdir(parents=True)
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)

        # Create a non-ops file
        (canonical_ops / "readme.txt").write_text("not an ops file")

        result = validate_all(tracker_root, _make_config())
        assert result.ok

    def test_corrupt_file_skipped_in_cross_file(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        canonical_ops = tracker_root / "tracker" / ".ops"
        canonical_ops.mkdir(parents=True)
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)

        # Malformed file
        (canonical_ops / ".WI-bad.ops").write_text("{{{{bad")

        result = validate_all(tracker_root, _make_config())
        assert any("malformed YAML" in e for e in result.errors)
        # Cross-file checks should still run without crashing

    def test_validate_all_loads_config(self, tmp_path: Path) -> None:
        """validate_all should load config from tracker_root when config=None."""
        tracker_root = tmp_path / ".agent"
        tracker_dir = tracker_root / "tracker"
        tracker_dir.mkdir(parents=True)
        (tracker_dir / ".ops").mkdir()
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)

        # Write a valid config
        import yaml
        config_data = {
            "kinds": {"work_item": {"prefix": "WI"}},
            "statuses": ["todo_hard", "done"],
            "stop_hook": {"blocking_statuses": ["todo_hard"], "resolved_statuses": ["done"]},
        }
        (tracker_dir / "config.yaml").write_text(yaml.dump(config_data))

        result = validate_all(tracker_root)
        assert result.ok


# ---------------------------------------------------------------------------
# Embedding duplicate warnings (mocked — always runnable)
# ---------------------------------------------------------------------------


class TestEmbeddingDuplicates:
    def test_check_deep_similar_calls_embedding_check(self, tmp_path: Path) -> None:
        """validate_all with check_deep_similar=True calls _check_embedding_duplicates."""
        tracker_root = tmp_path / ".agent"
        canonical_ops = tracker_root / "tracker" / ".ops"
        canonical_ops.mkdir(parents=True)
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)

        ops_a = textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Fix auth bug"
                status: todo_hard
                priority: 2
        """)
        (canonical_ops / ".WI-item-a.ops").write_text(ops_a)

        with patch(
            "hypergumbo_tracker.validation._check_embedding_duplicates"
        ) as mock_check:
            validate_all(tracker_root, _make_config(), check_deep_similar=True)
            mock_check.assert_called_once()

    def test_check_deep_similar_not_called_by_default(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        (tracker_root / "tracker" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)

        with patch(
            "hypergumbo_tracker.validation._check_embedding_duplicates"
        ) as mock_check:
            validate_all(tracker_root, _make_config())
            mock_check.assert_not_called()

    def test_embedding_check_warns_when_deps_missing(self) -> None:
        """_check_embedding_duplicates warns when dedup deps unavailable."""
        from hypergumbo_tracker.models import CompiledItem

        items: dict[str, tuple[str, Any]] = {
            "WI-a": ("canonical", CompiledItem(
                id="WI-a", kind="work_item", title="Test",
                status="todo_hard",
            )),
        }
        result = ValidationResult()

        with patch(
            "hypergumbo_tracker.embeddings.is_dedup_available", return_value=False
        ):
            _check_embedding_duplicates(items, result)

        assert any("deep-similar: skipped" in w for w in result.warnings)

    def test_embedding_check_reports_duplicates(self) -> None:
        """_check_embedding_duplicates appends warnings with cosine + tags."""
        from hypergumbo_tracker.embeddings import EmbeddingDuplicateResult
        from hypergumbo_tracker.models import CompiledItem

        items: dict[str, tuple[str, Any]] = {
            "WI-a": ("canonical", CompiledItem(
                id="WI-a", kind="work_item", title="Test",
                status="todo_hard",
            )),
            "WI-b": ("canonical", CompiledItem(
                id="WI-b", kind="work_item", title="Test",
                status="todo_hard",
            )),
        }
        result = ValidationResult()

        mock_result = EmbeddingDuplicateResult(
            id_a="WI-a", id_b="WI-b",
            cosine_sim=0.92, shared_tags=["call_graph", "performance"],
        )

        with patch(
            "hypergumbo_tracker.embeddings.is_dedup_available", return_value=True
        ), patch(
            "hypergumbo_tracker.embeddings.check_embedding_duplicates",
            return_value=[mock_result],
        ):
            _check_embedding_duplicates(items, result)

        assert len(result.warnings) == 1
        w = result.warnings[0]
        assert "deep-similar: WI-a and WI-b" in w
        assert "cosine similarity: 0.920" in w
        assert "call_graph" in w
        assert "performance" in w

    def test_embedding_check_no_shared_tags(self) -> None:
        """When no shared tags, report 'none'."""
        from hypergumbo_tracker.embeddings import EmbeddingDuplicateResult
        from hypergumbo_tracker.models import CompiledItem

        items: dict[str, tuple[str, Any]] = {
            "WI-a": ("canonical", CompiledItem(
                id="WI-a", kind="work_item", title="A", status="todo_hard",
            )),
            "WI-b": ("canonical", CompiledItem(
                id="WI-b", kind="work_item", title="B", status="todo_hard",
            )),
        }
        result = ValidationResult()

        mock_result = EmbeddingDuplicateResult(
            id_a="WI-a", id_b="WI-b",
            cosine_sim=0.87, shared_tags=[],
        )

        with patch(
            "hypergumbo_tracker.embeddings.is_dedup_available", return_value=True
        ), patch(
            "hypergumbo_tracker.embeddings.check_embedding_duplicates",
            return_value=[mock_result],
        ):
            _check_embedding_duplicates(items, result)

        assert "shared tags: none" in result.warnings[0]
