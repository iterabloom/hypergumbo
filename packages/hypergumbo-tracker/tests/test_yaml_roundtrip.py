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
                "isbefore": [],
                "duplicate_of": [],
                "not_duplicate_of": [],
                "pr_ref": None,

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
                "isbefore": [],
                "duplicate_of": [],
                "not_duplicate_of": [],
                "pr_ref": None,

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
            "isbefore", "duplicate_of", "not_duplicate_of", "pr_ref",
            "description", "fields",
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
                "isbefore": [],
                "duplicate_of": [],
                "not_duplicate_of": [],
                "pr_ref": None,

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


class TestFlowStyleUpdateLists:
    """Verify that list-valued fields in update ops use YAML flow-style.

    Flow-style keeps the entire list on one line, making it atomic under
    merge=union — git cannot interleave lines from different ops.
    """

    def test_add_dict_lists_are_flow_style(self) -> None:
        op = {
            "op": "update",
            **COMMON,
            "set": {},
            "add": {"tags": ["ci_infrastructure", "analysis_quality"]},
        }
        serialized = _serialize_op(op)
        # Flow-style list should appear as [ci_infrastructure, analysis_quality]
        # on a single line, not as block-style with leading dashes
        assert "[ci_infrastructure, analysis_quality]" in serialized
        # Should NOT have block-style dashes for the tag values
        assert "- ci_infrastructure" not in serialized

    def test_remove_dict_lists_are_flow_style(self) -> None:
        op = {
            "op": "update",
            **COMMON,
            "set": {},
            "remove": {"tags": ["old_tag"], "isbefore": ["WI-xxx", "WI-yyy"]},
        }
        serialized = _serialize_op(op)
        assert "[old_tag]" in serialized
        assert "[WI-xxx, WI-yyy]" in serialized

    def test_flow_style_roundtrip(self) -> None:
        """Flow-style lists round-trip correctly through CSafeLoader."""
        op = {
            "op": "update",
            **COMMON,
            "set": {},
            "add": {"tags": ["a", "b", "c"]},
            "remove": {"isbefore": ["WI-x"]},
        }
        serialized = _serialize_op(op)
        # Strip nonce comments for parsing
        clean_lines = []
        for line in serialized.split("\n"):
            cleaned = re.sub(r"\s+# [0-9a-f]{4}$", "", line)
            clean_lines.append(cleaned)
        clean_yaml = "\n".join(clean_lines)

        parsed = yaml.load(clean_yaml, Loader=yaml.CSafeLoader)
        assert parsed is not None
        assert parsed[0]["add"]["tags"] == ["a", "b", "c"]
        assert parsed[0]["remove"]["isbefore"] == ["WI-x"]

    def test_add_empty_list_flow_style(self) -> None:
        """Empty lists in add/remove should also be flow-style."""
        op = {
            "op": "update",
            **COMMON,
            "set": {},
            "add": {"tags": []},
        }
        serialized = _serialize_op(op)
        assert "[]" in serialized


class TestLongStringRoundtrip:
    """Verify long string scalars round-trip without nonce-injection corruption.

    Regression for WI-pusif-bukor: at the previous ry.width=4096 setting,
    ruamel.yaml folded long double-quoted scalars across physical lines.
    The per-line nonce post-processor in `_serialize_op` then appended
    `  # <nonce>` to every physical line, including continuation lines,
    embedding the nonce comment INSIDE the string value. CSafeLoader treats
    `#` as literal inside double-quoted scalars, so on read-back the value
    contained `# <nonce>` substrings the user never wrote.

    The fix sets `ry.width = sys.maxsize`, so no realistic scalar folds.
    Each scalar is one physical line, the nonce-on-every-line invariant
    remains intact, and the corruption class is structurally impossible.

    These tests use `_parse_ops_bytes` (which delegates to CSafeLoader) so
    they exercise the same parser the production read path uses — no nonce
    pre-stripping required, because `#` outside a string is a normal YAML
    comment.
    """

    @staticmethod
    def _roundtrip(op: dict[str, Any]) -> list[dict[str, Any]]:
        serialized = _serialize_op(op)
        return _parse_ops_bytes(serialized.encode("utf-8"))

    def test_5000_byte_description_with_multibyte_chars(self) -> None:
        """5000+ UTF-8 bytes of description with multi-byte characters."""
        long_desc = (
            "Lorem ipsum dolor sit amet — em-dash → arrow ≈ tilde ★ star. "
        ) * 80
        assert len(long_desc.encode("utf-8")) > 5000
        op = {
            "op": "create",
            **COMMON,
            "data": {
                "kind": "invariant",
                "title": "Long description test",
                "status": "todo_hard",
                "priority": 2,
                "description": long_desc,
                "fields": {},
            },
        }
        parsed = self._roundtrip(op)
        assert parsed[0]["data"]["description"] == long_desc

    def test_5000_byte_field_value(self) -> None:
        """5000+ byte fields.<key> value must survive round-trip."""
        long_value = (
            "alpha beta gamma delta epsilon zeta eta theta iota kappa "
        ) * 100
        assert len(long_value.encode("utf-8")) > 5000
        op = {
            "op": "create",
            **COMMON,
            "data": {
                "kind": "invariant",
                "title": "Long field test",
                "status": "todo_hard",
                "priority": 2,
                "description": "",
                "fields": {"statement": long_value},
            },
        }
        parsed = self._roundtrip(op)
        assert parsed[0]["data"]["fields"]["statement"] == long_value

    def test_10000_byte_single_line_no_spaces(self) -> None:
        """10 KB scalar with no whitespace — adversarial fold target.

        Whitespace gives the YAML folder natural break points; a long run
        of non-whitespace must still be emitted without folding. With the
        WI-pusif fix this is structurally guaranteed.
        """
        long_desc = "x" * 10000
        op = {
            "op": "create",
            **COMMON,
            "data": {
                "kind": "invariant",
                "title": "No-spaces long description",
                "status": "todo_hard",
                "priority": 2,
                "description": long_desc,
                "fields": {},
            },
        }
        parsed = self._roundtrip(op)
        assert parsed[0]["data"]["description"] == long_desc

    def test_adversarial_literal_hash_pattern_in_long_string(self) -> None:
        """User-supplied text containing literal '  # NNNN' must round-trip.

        The original WI-pusif description noted that the literal sequence
        `'  # 1234'` or `'  # abcd'` inside user content is at risk of
        being mistaken for a nonce comment by any naive post-processor.
        With ry.width=sys.maxsize the entire scalar lives on one line and
        CSafeLoader correctly preserves these literal substrings.
        """
        long_desc = (
            "intro padding padding padding padding padding padding padding "
            * 30
            + "the literal sequence '  # abcd' must survive verbatim "
            + "padding padding padding padding padding padding padding "
            * 30
            + "another literal '  # 1234' here too "
            + "padding padding padding padding padding padding padding "
            * 30
        )
        assert len(long_desc.encode("utf-8")) > 4096
        assert "  # abcd" in long_desc
        assert "  # 1234" in long_desc
        op = {"op": "discuss", **COMMON, "message": long_desc}
        parsed = self._roundtrip(op)
        assert parsed[0]["message"] == long_desc

    def test_inv_gajap_size_regression(self) -> None:
        """A description ~4106 YAML bytes — the original failure size class.

        WI-pusif was first observed updating INV-gajap with a 4106-byte
        description that corrupted at position `'periodic  # f084 audit'`.
        Any description in the 4080-5000 byte range is at risk under the
        old serializer; this test pins one such size.
        """
        long_desc = (
            "Sentence about the invariant that violates expectation. "
        ) * 75
        size = len(long_desc.encode("utf-8"))
        assert 4000 < size < 5000, f"unexpected size {size}"
        op = {
            "op": "create",
            **COMMON,
            "data": {
                "kind": "invariant",
                "title": "INV size regression",
                "status": "violated",
                "priority": 2,
                "description": long_desc,
                "fields": {},
            },
        }
        parsed = self._roundtrip(op)
        assert parsed[0]["data"]["description"] == long_desc

    def test_no_nonce_substring_leaks_into_long_scalar(self) -> None:
        """Direct check: parsed long scalar must NOT contain '# <hex>'.

        Under the old fold-then-nonce-inject bug, the parsed string would
        contain `# <4-hex-chars>` substrings injected at every fold boundary
        (~4096 bytes apart). This test detects exactly that corruption
        signature on a multi-fold-length input.
        """
        # ~12 KB — would have ~3 fold boundaries under width=4096.
        long_desc = "ipsum dolor sit amet consectetur adipiscing elit " * 250
        op = {
            "op": "create",
            **COMMON,
            "data": {
                "kind": "invariant",
                "title": "fold-boundary nonce-injection canary",
                "status": "todo_hard",
                "priority": 2,
                "description": long_desc,
                "fields": {},
            },
        }
        parsed = self._roundtrip(op)
        result = parsed[0]["data"]["description"]
        assert result == long_desc
        # Defense in depth: even if equality somehow passed, no nonce comment
        # substring should be present.
        assert not re.search(r"#\s+[0-9a-f]{4}\b", result), (
            f"nonce-comment substring leaked into description: {result!r}"
        )

    def test_serializer_does_not_fold_large_scalars(self) -> None:
        """Width-guard test: a 100 KB scalar must serialize without folding.

        This is an observable-property test, not an internal-constant test:
        if a future refactor lowers ry.width below ~100 KB the bug class
        becomes possible again, and this guard fires. Counts the number of
        non-empty physical lines in the serialized op — folding would
        produce ~25 lines per fold-boundary multiple, while no-fold should
        produce a small constant.
        """
        long_msg = "x" * 100_000
        op = {"op": "discuss", **COMMON, "message": long_msg}
        serialized = _serialize_op(op)
        non_empty = [l for l in serialized.split("\n") if l.strip()]
        # Header fields (op, at, by, actor, clock, nonce) + 1 message line.
        # With width=4096 we'd see ~25 extra lines from message folding.
        assert len(non_empty) < 15, (
            f"suspected folding: {len(non_empty)} non-empty physical lines "
            f"for a single 100 KB scalar; expected <15"
        )
        # And it must round-trip cleanly.
        parsed = _parse_ops_bytes(serialized.encode("utf-8"))
        assert parsed[0]["message"] == long_msg


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
