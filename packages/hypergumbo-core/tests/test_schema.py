# SPDX-License-Identifier: AGPL-3.0-or-later
"""Schema validation tests.

These tests close the TDD loop for the JSON Schema:
1. Verify that hypergumbo output validates against docs/schema.json
2. Verify that docs/schema.json is up-to-date with the dataclasses

This ensures the schema is a contract that both implementation and tests verify.

Philosophy: "Spec Driven Development" - tests are specifications of behavior.
The JSON Schema is a formal spec that both implementation and tests verify.
"""

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import jsonschema
import pytest
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


def _has_hypergumbo_meta() -> bool:
    """Check if hypergumbo meta-package is installed."""
    try:
        import hypergumbo
        del hypergumbo
        return True
    except ImportError:
        return False

# Find repo root by walking up until we find .git
def _find_repo_root() -> Path:
    current = Path(__file__).parent
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    raise RuntimeError("Could not find repo root")

REPO_ROOT = _find_repo_root()
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def load_schema() -> dict:
    """Load the JSON Schema from docs/schema.json."""
    schema_path = REPO_ROOT / "docs" / "schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def make_validator(schema: dict, sub_schema_name: str | None = None):
    """Create a validator with proper $ref resolution.

    Args:
        schema: The full schema dict
        sub_schema_name: If provided, validate against $defs/{sub_schema_name}
    """
    # Create a registry with the schema
    schema_id = schema.get("$id", "https://example.com/schema")
    resource = Resource.from_contents(schema, default_specification=DRAFT202012)
    registry = Registry().with_resource(schema_id, resource)

    # Get the sub-schema if requested - use absolute URI so resolver can find it
    if sub_schema_name:
        # Use absolute URI so nested references like #/$defs/Span still resolve
        target_schema = {"$ref": f"{schema_id}#/$defs/{sub_schema_name}"}
    else:
        target_schema = schema

    return jsonschema.Draft202012Validator(target_schema, registry=registry)


class TestSchemaValidation:
    """Tests that verify output validates against the schema."""

    def test_empty_behavior_map_validates(self):
        """An empty behavior map from new_behavior_map() validates."""
        from hypergumbo_core.schema import new_behavior_map

        schema = load_schema()
        behavior_map = new_behavior_map()

        # Should not raise
        jsonschema.validate(behavior_map, schema)

    @pytest.mark.skipif(
        not _has_hypergumbo_meta(),
        reason="requires hypergumbo meta-package"
    )
    def test_real_analysis_output_validates(self, tmp_path: Path):
        """Real analysis output validates against the schema."""
        # Create a simple Python file to analyze
        py_file = tmp_path / "example.py"
        py_file.write_text(dedent('''
            def hello():
                """Say hello."""
                print("Hello, world!")

            def goodbye():
                """Say goodbye."""
                hello()
                print("Goodbye!")

            class Greeter:
                def greet(self):
                    hello()
        '''))

        # Run hypergumbo analysis
        output_file = tmp_path / "results.json"
        result = subprocess.run(
            [sys.executable, "-m", "hypergumbo", "run", str(tmp_path),
             "--out", str(output_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"hypergumbo run failed: {result.stderr}"

        # Load and validate
        schema = load_schema()
        behavior_map = json.loads(output_file.read_text(encoding="utf-8"))

        # Should not raise
        jsonschema.validate(behavior_map, schema)

        # Verify we got actual content
        assert len(behavior_map["nodes"]) >= 3  # hello, goodbye, Greeter
        assert len(behavior_map["edges"]) >= 2  # goodbye->hello, greet->hello

    def test_symbol_with_all_fields_validates(self):
        """A Symbol with all optional fields validates."""
        from hypergumbo_core.ir import Span, Symbol

        schema = load_schema()

        symbol = Symbol(
            id="test::func::1-5",
            name="test_func",
            kind="function",
            language="python",
            path="/path/to/file.py",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=10),
            origin="python",
            origin_run_id="uuid:12345",
            stable_id="sha256:stable123",
            shape_id="sha256:shape456",
            qualified_name="module.test_func",
            fingerprint="sha256:content789",
            quality={"score": 0.95, "reason": "well-documented"},
            meta={"decorator": "@pytest.fixture"},
            supply_chain_tier=1,
            supply_chain_reason="matches ^src/",
        )

        validator = make_validator(schema, "Symbol")
        validator.validate(symbol.to_dict())

    def test_external_boundary_symbol_validates(self):
        """A synthetic external boundary Symbol (kind=external_symbol,
        path=<external>, meta.external_boundary=True) validates against
        the schema. Stop-stripping plan PR1 starts emitting these in
        behavior_map['nodes']; without external_symbol in the kind enum,
        any consumer JSON-schema-validating the output would fail.
        """
        from hypergumbo_core.ir import Span, Symbol

        schema = load_schema()
        boundary = Symbol(
            id="python:urllib.request:0-0:urlopen:unresolved",
            name="urlopen",
            kind="external_symbol",
            language="python",
            path="<external>",
            span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
            meta={"external_boundary": True},
            supply_chain_tier=3,
            supply_chain_reason="unresolved external reference",
        )
        validator = make_validator(schema, "Symbol")
        validator.validate(boundary.to_dict())

    def test_edge_with_all_fields_validates(self):
        """An Edge with all optional fields validates."""
        from hypergumbo_core.ir import Edge

        schema = load_schema()

        edge = Edge.create(
            src="test::caller::1-5",
            dst="test::callee::10-15",
            edge_type="calls",
            line=3,
            origin="python",
            origin_run_id="uuid:12345",
            evidence_type="ast_call_direct",
            confidence=0.95,
            evidence_lang="python",
            evidence_spans=[{"line": 3, "col": 4}],
        )
        edge.meta = {"call_style": "direct"}

        validator = make_validator(schema, "Edge")
        validator.validate(edge.to_dict())

    def test_analysis_run_validates(self):
        """An AnalysisRun validates."""
        from hypergumbo_core.ir import AnalysisRun

        schema = load_schema()

        run = AnalysisRun.create(
            pass_id="python",
            version="0.1.0",
        )
        run.files_analyzed = 10
        run.duration_ms = 500

        validator = make_validator(schema, "AnalysisRun")
        validator.validate(run.to_dict())

    def test_invalid_edge_type_fails_validation(self):
        """An edge with an invalid type fails validation."""
        schema = load_schema()

        invalid_edge = {
            "id": "edge:test",
            "src": "a",
            "dst": "b",
            "type": "invalid_type_that_does_not_exist",
            "line": 1,
            "confidence": 0.5,
        }

        validator = make_validator(schema, "Edge")
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(invalid_edge)

    def test_arbitrary_symbol_kind_passes_validation_until_phase_4b(self):
        """Per ADR-0028 §"Path B", Symbol.kind's schema enum is OPEN
        (`type: "string"` without enum constraint) until per-cluster
        Phase 4b producer migrations land. While the L3 producer-
        coherence linter at runtime gates registry membership at the
        producer side, the JSON Schema itself accepts arbitrary
        strings — that's the honest representation of what Phase 1
        actually delivers.

        When Phase 4b ships and the enum closes per cluster, replace
        this test with one that verifies a known-invalid value is
        rejected."""
        schema = load_schema()

        symbol_with_unregistered_kind = {
            "id": "test::sym",
            "name": "test",
            "kind": "kind_value_not_in_registry",
            "language": "python",
            "path": "/test.py",
            "span": {"start_line": 1, "end_line": 1, "start_col": 0, "end_col": 4},
        }

        # The schema accepts the unregistered kind — open enum posture.
        validator = make_validator(schema, "Symbol")
        validator.validate(symbol_with_unregistered_kind)

        # But the registry-derived x-axis-of-values map does NOT contain
        # it (consistency with the registry is documented through the
        # extension annotation, not through enum validation).
        kind_node = schema["$defs"]["Symbol"]["properties"]["kind"]
        assert "kind_value_not_in_registry" not in kind_node[
            "x-axis-of-values"
        ]


class TestSchemaUpToDate:
    """Tests that verify the schema is in sync with the dataclasses."""

    def test_schema_matches_generated(self):
        """docs/schema.json matches what generate-schema would produce.

        This ensures the schema stays in sync with the dataclasses.
        If this test fails, run: ./scripts/generate-schema
        """
        # Import the generation function
        # We need to run it as a subprocess since the script modifies sys.path
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "generate-schema"), "--check"],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            pytest.fail(
                f"Schema is out of date. Run: ./scripts/generate-schema\n"
                f"Output: {result.stdout}\n{result.stderr}"
            )

    def test_schema_version_matches_code(self):
        """schema.json schema_version matches schema.py SCHEMA_VERSION."""
        from hypergumbo_core.schema import SCHEMA_VERSION

        schema = load_schema()
        schema_version_in_json = schema["properties"]["schema_version"]["const"]

        assert schema_version_in_json == SCHEMA_VERSION, (
            f"Schema version mismatch: schema.json has {schema_version_in_json}, "
            f"but schema.py has {SCHEMA_VERSION}. Run: ./scripts/generate-schema"
        )

    def test_all_edge_types_in_schema(self):
        """All edge types used in linkers are in the schema enum."""
        schema = load_schema()
        edge_types_in_schema = set(schema["$defs"]["Edge"]["properties"]["type"]["enum"])

        # Known edge types from linkers and analyzers (post Phase 4b —
        # WI-vomoj-suhaz removed bridge / IPC / route / DI / publish-family
        # endpoint_shape values whose producers Phase 3 migrated to
        # canonical 'calls' / 'dispatches_to' / 'event_publishes' edges).
        known_edge_types = {
            # From analyzers
            "calls", "imports", "instantiates", "extends", "implements",
            "references", "depends_on", "links", "sources",
            "script_src", "base_image", "kernel_launch",
            # From linkers (still endpoint_shape — pending future Phase-3-style
            # migrations of their respective protocol-specialized linkers)
            "grpc_calls",  # gRPC
            "http_calls",  # HTTP
            "graphql_calls",  # GraphQL
            # Canonical Phase-3 fold targets
            "event_publishes",  # async producer→consumer
            "dispatches_to",   # runtime dispatch indirection
            # GraphQL Resolver — pending_classification per-family audit
            "resolver_implements", "resolver_for_type",
        }

        missing = known_edge_types - edge_types_in_schema
        assert not missing, (
            f"Edge types missing from schema: {missing}. "
            f"Update scripts/generate-schema and run it."
        )

    def test_schema_kind_is_open_string_until_phase_4b(self):
        """Per ADR-0028 §"Path B", the Symbol.kind schema enum is
        intentionally OPEN (`type: "string"` without an `enum`
        constraint) until per-cluster Phase 4b producer migrations
        land. Current production includes dynamic ``kind=f"ipc_..."``
        emits at ``ipc.py`` / ``phoenix_ipc.py`` that produce values
        outside the static registry; closing the enum prematurely
        would canonize the leak the ADR is fixing.

        ADR-0027 Phase 1 originally shipped a closed enum; ADR-0028
        Phase 1 retroactively reopened it for honesty (the L3
        producer-coherence linter at
        ``scripts/check-producer-axis-coherence`` is what now actually
        gates registry membership at the producer side).
        """
        schema = load_schema()
        kind_node = schema["$defs"]["Symbol"]["properties"]["kind"]
        assert kind_node["type"] == "string"
        assert "enum" not in kind_node, (
            "Symbol.kind schema enum must stay OPEN until Phase 4b "
            "producer migrations land. If you closed the enum, you "
            "either (a) have shipped Phase 4b for at least one cluster, "
            "in which case update this test to reflect the new closure "
            "policy, or (b) have introduced a regression — re-open it."
        )

    def test_schema_kind_carries_axis_annotations(self):
        """Per ADR-0024, the registry's x-axis-of-values annotation
        documents every Symbol.kind value's axis classification.
        Replaces the closed-enum constraint per ADR-0028 §"Path B"."""
        from hypergumbo_core.symbol_kinds import (
            VALID_AXES,
            all_symbol_kind_names,
        )

        schema = load_schema()
        kind_node = schema["$defs"]["Symbol"]["properties"]["kind"]
        assert "x-axis-of-values" in kind_node, (
            "Symbol.kind must carry an x-axis-of-values annotation "
            "per ADR-0024."
        )
        annotations = kind_node["x-axis-of-values"]
        assert set(annotations.keys()) == set(all_symbol_kind_names()), (
            "x-axis-of-values keys must exactly match the registry."
        )
        for name, axis in annotations.items():
            assert axis in VALID_AXES, (
                f"{name}: axis {axis!r} is not a valid axis name"
            )

    def test_schema_evidence_type_is_open_string_until_phase_4b(self):
        """Per ADR-0028 §"Path B", the Edge.evidence_type schema is
        OPEN (`type: "string"` without enum) until per-cluster Phase
        4b producer migrations land. Current production includes
        dynamic f-string emits at ``websocket.py``, ``inheritance.py``,
        and ``di_resolution.py`` that produce values outside the
        static registry."""
        schema = load_schema()
        evidence_node = (
            schema["$defs"]["Edge"]["properties"]["meta"]
            ["properties"]["evidence_type"]
        )
        assert evidence_node["type"] == "string"
        assert "enum" not in evidence_node, (
            "Edge.evidence_type schema enum must stay OPEN until "
            "Phase 4b producer migrations land."
        )

    def test_schema_evidence_type_carries_axis_annotations(self):
        """The Edge.evidence_type schema property's x-axis-of-values
        map exactly mirrors the canonical registry."""
        from hypergumbo_core.evidence_types import (
            VALID_AXES,
            all_evidence_type_names,
        )

        schema = load_schema()
        evidence_node = (
            schema["$defs"]["Edge"]["properties"]["meta"]
            ["properties"]["evidence_type"]
        )
        assert "x-axis-of-values" in evidence_node, (
            "Edge.evidence_type must carry an x-axis-of-values "
            "annotation per ADR-0024."
        )
        annotations = evidence_node["x-axis-of-values"]
        assert set(annotations.keys()) == set(all_evidence_type_names())
        for name, axis in annotations.items():
            assert axis in VALID_AXES, (
                f"{name}: axis {axis!r} is not a valid axis name"
            )

    def test_schema_edge_is_resolved_present(self):
        """Per ADR-0028 §"Sibling-field design call-out", the new
        sibling field appears at the top level of Edge with default
        True."""
        schema = load_schema()
        is_resolved = schema["$defs"]["Edge"]["properties"]["is_resolved"]
        assert is_resolved["type"] == "boolean"
        assert is_resolved["default"] is True
