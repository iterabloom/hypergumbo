# SPDX-License-Identifier: MPL-2.0
"""Tests for YAML serialization round-trip correctness.

Verifies that the ruamel.yaml write path and PyYAML CSafeLoader read path
produce identical Python dicts for all op types, including adversarial
string inputs that trigger YAML implicit type coercion.

Also verifies nonce-on-every-line format, canonical field ordering,
double-quoting rules, and flow-style lists in update ops.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

import yaml

from hypergumbo_tracker.store import (
    _make_nonce,
    _parse_ops_bytes,
    _serialize_op,
)


COMMON: dict[str, Any] = {
    "at": "2026-02-11T18:00:00Z",
    "by": "agent",
    "actor": "test_agent",
    "clock": 1,
    "nonce": "f7a2",
}


class TestNonceOnEveryLine:
    """Verify that every non-empty line carries `# <nonce>`."""

    def test_create_op_nonce_on_every_line(self) -> None:
        op = {
            "op": "create",
            **COMMON,
            "data": {
                "kind": "invariant",
                "title": "Test title",
                "status": "todo_hard",
                "priority": 2,
                "parent": None,
                "tags": ["analysis_quality"],
                "before": [],
                "duplicate_of": [],
                "not_duplicate_of": [],
                "pr_ref": None,
                "justification": None,
                "description": "A test description",
                "fields": {"statement": "test statement"},
            },
        }
        serialized = _serialize_op(op)
        for line in serialized.split("\n"):
            if line.strip():
                assert line.rstrip().endswith("# f7a2"), (
                    f"Line missing nonce comment: {line!r}"
                )

    def test_update_op_nonce_on_every_line(self) -> None:
        op = {"op": "update", **COMMON, "set": {"status": "done", "priority": 0}}
        serialized = _serialize_op(op)
        for line in serialized.split("\n"):
            if line.strip():
                assert line.rstrip().endswith("# f7a2"), (
                    f"Line missing nonce comment: {line!r}"
                )

    def test_discuss_op_nonce_on_every_line(self) -> None:
        op = {"op": "discuss", **COMMON, "message": "Some discussion text"}
        serialized = _serialize_op(op)
        for line in serialized.split("\n"):
            if line.strip():
                assert line.rstrip().endswith("# f7a2")

    def test_lock_op_nonce_on_every_line(self) -> None:
        op = {"op": "lock", **COMMON, "lock": ["priority", "status"]}
        serialized = _serialize_op(op)
        for line in serialized.split("\n"):
            if line.strip():
                assert line.rstrip().endswith("# f7a2")


class TestCanonicalFieldOrder:
    """Verify fields appear in canonical order."""

    def test_common_field_order(self) -> None:
        op = {"op": "update", **COMMON, "set": {"status": "done"}}
        serialized = _serialize_op(op)
        lines = [l.strip() for l in serialized.split("\n") if l.strip()]

        # First line should start with `- op:`
        assert lines[0].startswith("- op:")
        # Then at, by, actor, clock, nonce in order
        field_order = []
        for line in lines:
            # Extract field name from "- key: value  # nonce" or "  key: value  # nonce"
            match = re.match(r"^-?\s*(\w+):", line)
            if match:
                field_order.append(match.group(1))

        expected_prefix = ["op", "at", "by", "actor", "clock", "nonce"]
        assert field_order[:6] == expected_prefix

    def test_create_data_field_order(self) -> None:
        op = {
            "op": "create",
            **COMMON,
            "data": {
                "kind": "invariant",
                "title": "Test",
                "status": "todo_hard",
                "priority": 2,
                "parent": None,
                "tags": [],
                "before": [],
                "duplicate_of": [],
                "not_duplicate_of": [],
                "pr_ref": None,
                "justification": None,
                "description": "",
                "fields": {},
            },
        }
        serialized = _serialize_op(op)

        # Extract data block field order
        in_data = False
        data_fields = []
        for line in serialized.split("\n"):
            stripped = line.strip().split("  # ")[0].strip()
            if stripped.startswith("data:"):
                in_data = True
                continue
            if in_data:
                match = re.match(r"(\w+):", stripped)
                if match:
                    data_fields.append(match.group(1))

        expected = [
            "kind", "title", "status", "priority", "parent", "tags",
            "before", "duplicate_of", "not_duplicate_of", "pr_ref",
            "justification", "description", "fields",
        ]
        assert data_fields == expected


class TestDoubleQuoting:
    """Verify double-quoting for fields that need it."""

    def test_title_double_quoted(self) -> None:
        op = {
            "op": "create",
            **COMMON,
            "data": {
                "kind": "invariant",
                "title": "yes",  # Would be coerced to True without quoting
                "status": "todo_hard",
                "priority": 2,
                "description": "null",  # Would be coerced to None
                "fields": {"statement": "3.0"},  # Would be coerced to float
            },
        }
        serialized = _serialize_op(op)

        # The word "yes" must appear in double quotes
        assert '"yes"' in serialized
        # "null" must appear in double quotes
        assert '"null"' in serialized
        # "3.0" must appear in double quotes
        assert '"3.0"' in serialized

    def test_discuss_message_double_quoted(self) -> None:
        op = {"op": "discuss", **COMMON, "message": "yes"}
        serialized = _serialize_op(op)
        assert '"yes"' in serialized


class TestAdversarialStrings:
    """Verify round-trip safety for strings that trigger YAML type coercion."""

    ADVERSARIAL: ClassVar[list[str]] = [
        "yes",
        "no",
        "true",
        "false",
        "null",
        "3.0",
        "1e10",
        "0x1F",
        "*bold*",
        "key: value",
        "  leading spaces",
        "trailing spaces  ",
        "emoji: \U0001f600",
        "newline\nin\nstring",
        "tab\there",
        "",
        "0",
        "1",
        "on",
        "off",
    ]

    def test_roundtrip_title_values(self) -> None:
        for adversarial in self.ADVERSARIAL:
            op = {
                "op": "create",
                **COMMON,
                "data": {
                    "kind": "invariant",
                    "title": adversarial,
                    "status": "todo_hard",
                    "priority": 2,
                    "description": "",
                    "fields": {},
                },
            }
            serialized = _serialize_op(op)
            # Strip nonce comments for parsing
            clean_lines = []
            for line in serialized.split("\n"):
                # Remove trailing nonce comment
                cleaned = re.sub(r"\s+# [0-9a-f]{4}$", "", line)
                clean_lines.append(cleaned)
            clean_yaml = "\n".join(clean_lines)

            parsed = yaml.load(clean_yaml, Loader=yaml.CSafeLoader)
            assert parsed is not None, f"Parsing failed for title={adversarial!r}"
            assert isinstance(parsed, list)
            assert len(parsed) == 1
            data = parsed[0].get("data", {})
            assert data.get("title") == adversarial, (
                f"Round-trip mismatch for title={adversarial!r}: "
                f"got {data.get('title')!r}"
            )

    def test_roundtrip_field_values(self) -> None:
        for adversarial in self.ADVERSARIAL:
            op = {
                "op": "create",
                **COMMON,
                "data": {
                    "kind": "invariant",
                    "title": "test",
                    "status": "todo_hard",
                    "priority": 2,
                    "description": "",
                    "fields": {"statement": adversarial},
                },
            }
            serialized = _serialize_op(op)
            clean_lines = []
            for line in serialized.split("\n"):
                cleaned = re.sub(r"\s+# [0-9a-f]{4}$", "", line)
                clean_lines.append(cleaned)
            clean_yaml = "\n".join(clean_lines)

            parsed = yaml.load(clean_yaml, Loader=yaml.CSafeLoader)
            assert parsed is not None
            fields = parsed[0].get("data", {}).get("fields", {})
            assert fields.get("statement") == adversarial, (
                f"Round-trip mismatch for field={adversarial!r}: "
                f"got {fields.get('statement')!r}"
            )

    def test_roundtrip_discuss_message(self) -> None:
        for adversarial in self.ADVERSARIAL:
            op = {"op": "discuss", **COMMON, "message": adversarial}
            serialized = _serialize_op(op)
            clean_lines = []
            for line in serialized.split("\n"):
                cleaned = re.sub(r"\s+# [0-9a-f]{4}$", "", line)
                clean_lines.append(cleaned)
            clean_yaml = "\n".join(clean_lines)

            parsed = yaml.load(clean_yaml, Loader=yaml.CSafeLoader)
            assert parsed is not None
            assert parsed[0].get("message") == adversarial


class TestCSafeLoaderRuamelParity:
    """Verify CSafeLoader and ruamel.yaml produce identical parsed output."""

    def test_parity_create_op(self) -> None:
        from ruamel.yaml import YAML as RuamelYAML

        op = {
            "op": "create",
            **COMMON,
            "data": {
                "kind": "invariant",
                "title": "Test with special chars: yes null 3.0",
                "status": "todo_hard",
                "priority": 2,
                "parent": None,
                "tags": ["analysis_quality"],
                "before": [],
                "duplicate_of": [],
                "not_duplicate_of": [],
                "pr_ref": None,
                "justification": None,
                "description": "A description with *markdown*",
                "fields": {
                    "statement": "true",
                    "root_cause": "null",
                },
            },
        }
        serialized = _serialize_op(op)

        # Strip nonce comments for clean parsing
        clean_lines = []
        for line in serialized.split("\n"):
            cleaned = re.sub(r"\s+# [0-9a-f]{4}$", "", line)
            clean_lines.append(cleaned)
        clean_yaml = "\n".join(clean_lines)

        # Parse with CSafeLoader
        csafe_result = yaml.load(clean_yaml, Loader=yaml.CSafeLoader)

        # Parse with ruamel.yaml
        ry = RuamelYAML()
        from io import StringIO
        ruamel_result = ry.load(StringIO(clean_yaml))

        # Convert ruamel CommentedMap to plain dict for comparison
        def to_plain(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: to_plain(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [to_plain(v) for v in obj]
            return obj

        assert to_plain(csafe_result) == to_plain(ruamel_result)


class TestTrailingNewline:
    """Verify serialized ops always end with a trailing newline."""

    def test_trailing_newline(self) -> None:
        op = {"op": "update", **COMMON, "set": {"status": "done"}}
        serialized = _serialize_op(op)
        assert serialized.endswith("\n")

    def test_create_trailing_newline(self) -> None:
        op = {
            "op": "create",
            **COMMON,
            "data": {"kind": "invariant", "title": "Test", "status": "todo_hard",
                     "priority": 2, "description": "", "fields": {}},
        }
        serialized = _serialize_op(op)
        assert serialized.endswith("\n")


class TestMakeNonce:
    """Verify nonce generation."""

    def test_nonce_length(self) -> None:
        nonce = _make_nonce()
        assert len(nonce) == 4

    def test_nonce_hex(self) -> None:
        nonce = _make_nonce()
        int(nonce, 16)  # Should not raise

    def test_nonces_unique(self) -> None:
        nonces = {_make_nonce() for _ in range(100)}
        # With 4 hex chars (65536 values), 100 samples should be mostly unique
        assert len(nonces) > 90


class TestReconcileOpSerialization:
    """Verify reconcile op serialization."""

    def test_reconcile_fields(self) -> None:
        op = {
            "op": "reconcile",
            **COMMON,
            "from_tier": "workspace",
            "reason": "Duplicate from interrupted promote",
        }
        serialized = _serialize_op(op)
        # Verify key fields present
        assert "from_tier:" in serialized
        assert "reason:" in serialized
        # reason should be double-quoted
        assert '"Duplicate from interrupted promote"' in serialized
