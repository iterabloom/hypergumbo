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

    def test_view_enum_accepts_all_projected_views(self):
        """WI-tagaj: the published schema pins ``view`` to an enum of all
        projected view names, so compact/tiered outputs validate (it was a
        ``const`` of 'behavior_map', which rejected the projections)."""
        from hypergumbo_core.schema import VIEW_NAMES

        schema = load_schema()
        view_schema = schema["properties"]["view"]
        assert "const" not in view_schema, "view must be an enum, not a const"
        assert set(view_schema["enum"]) == set(VIEW_NAMES)
        # every projected view name validates against the view sub-schema
        for name in ("behavior_map", "compact", "tiered"):
            jsonschema.validate(name, view_schema)
        # a bogus view is rejected
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate("bogus_view", view_schema)

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
        4b producer migrations land. Current production includes a
        dynamic f-string emit at ``inheritance.py`` (``evidence_type=
        f"ast_{edge_type}"``) that produces values outside the static
        registry."""
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


class TestSchemaDataclassSync:
    """WI-kufib / WI-kutas: the $defs must track the dataclasses.

    These tests pin the introspection-driven generator contract: every
    $def's property set, nullability, and required-ness derive from the
    dataclass (via the generator's serialization specs), so a field
    add / remove / retype cannot silently drift docs/schema.json.
    """

    def test_symbol_language_nullable(self):
        """ADR-0031 Class B stand-ins emit language=None; the schema
        must tolerate null (WI-kufib: 262 self-analysis nodes)."""
        schema = load_schema()
        symbol_def = schema["$defs"]["Symbol"]
        validator = make_validator(schema, "Symbol")

        class_b_node = {
            "id": "python:app.py:3-3:db_query:call_site",
            "name": "SELECT users",
            "kind": "call_site",
            "language": None,
            "path": "app.py",
            "span": {"start_line": 3, "end_line": 3, "start_col": 0, "end_col": 0},
            "discovery_language": "python",
            "protocol_origin": "database_query",
        }
        validator.validate(class_b_node)  # should not raise
        assert "language" not in symbol_def.get("required", [])

    def test_symbol_canonical_name_absent(self):
        """ADR-0032 removed Symbol.canonical_name from the dataclass at
        SCHEMA_VERSION 0.13.0; the hand-coded schema kept it (stale)."""
        schema = load_schema()
        assert "canonical_name" not in schema["$defs"]["Symbol"]["properties"]

    def test_symbol_axis_sibling_fields_present(self):
        """The four ADR-0031/0032 typed sibling fields are emitted on
        every node (34396/34396 in self-analysis) and must be declared."""
        schema = load_schema()
        props = schema["$defs"]["Symbol"]["properties"]
        for field_name in (
            "discovery_language", "protocol_origin",
            "display_label", "qualified_name",
        ):
            assert field_name in props, f"missing {field_name}"

    def test_analysis_run_failed_files_and_pass_version_present(self):
        """AnalysisRun emits failed_files and pass_version on every run
        (84/84 in self-analysis); the schema must declare them."""
        schema = load_schema()
        props = schema["$defs"]["AnalysisRun"]["properties"]
        assert "failed_files" in props
        assert "pass_version" in props

    def test_to_dict_keys_match_schema_properties(self):
        """Round-trip writer contract: for each $def, a fully-populated
        instance's to_dict() key set equals the schema property set."""
        from hypergumbo_core.ir import AnalysisRun, Edge, ExternalRef, Span, Symbol

        schema = load_schema()
        span = Span(start_line=1, end_line=2, start_col=0, end_col=1)
        samples = {
            "Span": span,
            "Symbol": Symbol(
                id="python:a.py:1-2:f:function", name="f", kind="function",
                language="python", path="a.py", span=span,
                origin=["python"], origin_run_id="uuid:1",
            ),
            "Edge": Edge.create(
                src="a", dst="b", edge_type="calls", line=1,
                origin="python", origin_run_id="uuid:1",
                evidence_lang="python", evidence_spans=[{"line": 1}],
                dst_ref=ExternalRef(lang="python", module_path="os", name="getcwd"),
                derived_from=["sym:1"],
            ),
            "AnalysisRun": AnalysisRun.create(pass_id="python", version="1.0"),
        }
        for def_name, instance in samples.items():
            schema_keys = set(schema["$defs"][def_name]["properties"])
            dict_keys = set(instance.to_dict())
            assert dict_keys == schema_keys, (
                f"{def_name}: to_dict() and schema disagree. "
                f"only-in-to_dict={sorted(dict_keys - schema_keys)}, "
                f"only-in-schema={sorted(schema_keys - dict_keys)}"
            )

    def test_generator_rejects_stale_decoration(self):
        """A decoration for a field the dataclass no longer has must
        fail generation (the canonical_name failure mode)."""
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        gen = __import__("generate_schema_lib")
        spec = gen.spec_for("Symbol")
        spec.decorations["definitely_not_a_field"] = {"description": "stale"}
        with pytest.raises(gen.SchemaDriftError, match="definitely_not_a_field"):
            gen.build_def(spec)

    def test_generator_rejects_undecorated_field(self):
        """A dataclass field with no decoration / override / composite
        must fail generation (the discovery_language failure mode)."""
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        gen = __import__("generate_schema_lib")
        spec = gen.spec_for("Symbol")
        del spec.decorations["docstring"]
        with pytest.raises(gen.SchemaDriftError, match="docstring"):
            gen.build_def(spec)

    @pytest.mark.skipif(
        not _has_hypergumbo_meta(),
        reason="requires hypergumbo meta-package"
    )
    def test_linker_synthetic_output_validates(self, tmp_path: Path):
        """End-to-end fixture-blindness closure: an analysis whose input
        triggers a protocol linker (Class B language=None stand-ins)
        validates whole-document. The prior conformance fixture was
        single-file pure Python, so no linker ever fired and the
        WI-kufib drift was invisible to CI.
        """
        (tmp_path / "schema.sql").write_text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);\n"
        )
        (tmp_path / "app.py").write_text(dedent('''
            import sqlite3

            def fetch_users():
                conn = sqlite3.connect("app.db")
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users")
                return cursor.fetchall()
        '''))

        output_file = tmp_path / "results.json"
        result = subprocess.run(
            [sys.executable, "-m", "hypergumbo", "run", str(tmp_path),
             "--out", str(output_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"hypergumbo run failed: {result.stderr}"

        behavior_map = json.loads(output_file.read_text(encoding="utf-8"))
        class_b_nodes = [
            n for n in behavior_map["nodes"] if n.get("language") is None
        ]
        assert class_b_nodes, (
            "fixture failed to produce a Class B (language=None) synthetic "
            "stand-in; the conformance gate is blind to linker output again"
        )
        jsonschema.validate(behavior_map, load_schema())


class TestTopLevelBlockTyping:
    """WI-kutas PR2: top-level blocks typed, missing keys declared.

    Each previously-opaque block (metrics / limits / features) gets a
    real schema, and each test pins the schema's key set to the actual
    producer's output so the block cannot silently drift again.
    """

    def test_missing_top_level_keys_declared(self):
        """reproducibility_context / symbol_fingerprint_scheme /
        validation_report are emitted on every run but were absent from
        the schema's top-level properties."""
        schema = load_schema()
        for key in (
            "reproducibility_context",
            "symbol_fingerprint_scheme",
            "validation_report",
        ):
            assert key in schema["properties"], f"missing top-level {key}"

    def test_new_behavior_map_keys_all_declared(self):
        """Every key new_behavior_map() emits is declared in the schema."""
        from hypergumbo_core.schema import new_behavior_map

        schema = load_schema()
        missing = set(new_behavior_map()) - set(schema["properties"])
        assert not missing, f"emitted but undeclared: {sorted(missing)}"

    def test_limits_def_matches_to_dict(self):
        """The limits block references a Limits $def whose property set
        equals Limits.to_dict() output (conditionals populated)."""
        from hypergumbo_core.limits import Limits

        schema = load_schema()
        assert schema["properties"]["limits"].get("$ref") == "#/$defs/Limits"
        limits_def = schema["$defs"]["Limits"]
        lim = Limits(
            max_tier_applied=2,
            max_files_per_analyzer=10,
            test_files_excluded=True,
            partial_results_reason="one or more passes crashed; results are partial",
        )
        assert set(lim.to_dict()) == set(limits_def["properties"])

    def test_limits_block_validates_real_output(self):
        """A fully-populated Limits.to_dict() validates against the $def."""
        from hypergumbo_core.limits import Limits

        schema = load_schema()
        lim = Limits(
            max_tier_applied=2,
            max_files_per_analyzer=10,
            test_files_excluded=True,
        )
        lim.add_failed_file("a.py", "boom", "python")
        lim.add_skipped_language("cobol")
        lim.add_truncated_file("big.py", 10_000_000, "too large")
        lim.add_classification_failure("weird.py", "outside repo")
        lim.add_ambiguous_path("vendor/x.py", 3, "vendored?")
        validator = make_validator(schema, "Limits")
        validator.validate(lim.to_dict())

    def test_metrics_properties_match_compute_metrics(self):
        """The metrics block's property set equals compute_metrics() output."""
        from hypergumbo_core.metrics import compute_metrics

        schema = load_schema()
        metrics_props = schema["properties"]["metrics"]["properties"]
        metrics = compute_metrics(
            [{"id": "n1", "language": "python", "path": "a.py", "kind": "function",
              "supply_chain": {"tier_name": "first_party"}}],
            [{"src": "n1", "dst": "n2", "confidence": 0.9}],
            profile={"languages": {"python": {"files": 1}}},
        )
        assert set(metrics) == set(metrics_props)
        # debug sub-block keys declared too
        debug_props = metrics_props["debug"]["properties"]
        assert set(metrics["debug"]) <= set(debug_props)

    def test_feature_def_matches_slice_result(self):
        """features[] items reference a Feature $def whose property set
        equals SliceResult.to_dict() output (conditionals populated)."""
        from hypergumbo_core.slice import SliceQuery, SliceResult

        schema = load_schema()
        assert (
            schema["properties"]["features"]["items"].get("$ref")
            == "#/$defs/Feature"
        )
        feature_def = schema["$defs"]["Feature"]
        result = SliceResult(
            entry_nodes=["n1"],
            node_ids={"n1", "n2"},
            edge_ids={"e1"},
            query=SliceQuery(
                entrypoint="main",
                max_tier=2,
                language="python",
                hub_threshold=50,
                exclude_imports=True,
                dataflow=True,
            ),
            limits_hit=["hop_limit"],
            node_depths={"n1": 0},
            node_tiers={"n1": 1},
            admission_stats={"admitted_writer_src": 1},
        )
        feature = result.to_dict()
        assert set(feature) == set(feature_def["properties"])
        # the nested query shape is pinned via the SliceQuery $def
        assert set(feature["query"]) <= set(
            schema["$defs"]["SliceQuery"]["properties"]
        )
        validator = make_validator(schema, "Feature")
        validator.validate(feature)

    def test_validation_report_block(self):
        """validation_report carries schema_version, violations
        ($ref ValidationViolation matching asdict output), and
        violations_by_class."""
        from dataclasses import asdict

        from hypergumbo_core.spec_validator import (
            ValidationViolation,
            build_validation_report,
        )

        schema = load_schema()
        vr_props = schema["properties"]["validation_report"]["properties"]
        assert set(vr_props) == {
            "schema_version", "violations", "violations_by_class",
            "wired_checks", "stable_id_stats",
        }
        assert (
            vr_props["violations"]["items"].get("$ref")
            == "#/$defs/ValidationViolation"
        )
        violation = ValidationViolation(
            severity="warning",
            validator_class="cross_field",
            message="m",
        )
        vv_def = schema["$defs"]["ValidationViolation"]
        assert set(asdict(violation)) == set(vv_def["properties"])
        report = build_validation_report([violation])
        # full report validates against the top-level property schema
        sub = {"$ref": f"{schema['$id']}#/properties/validation_report"}
        resource = Resource.from_contents(
            schema, default_specification=DRAFT202012,
        )
        registry = Registry().with_resource(schema["$id"], resource)
        jsonschema.Draft202012Validator(sub, registry=registry).validate(report)

    def test_reproducibility_context_block(self):
        """reproducibility_context's schema matches the builder output."""
        from hypergumbo_core.schema import build_reproducibility_context

        schema = load_schema()
        rc_props = schema["properties"]["reproducibility_context"]["properties"]
        rc = build_reproducibility_context()
        assert set(rc) == set(rc_props)
        captured_props = rc_props["captured"]["properties"]
        assert set(rc["captured"]) <= set(captured_props)

    def test_repro_implications_reference_capturable_fields(self):
        """The implications text must only direct consumers at fields the
        behavior map actually carries: captured.* keys or the explicit
        analysis_runs[].pass_version pointer. Previously it named a
        captured.pass_versions key that was never captured."""
        from hypergumbo_core.schema import build_reproducibility_context

        rc = build_reproducibility_context()
        implications = rc["implications"]
        assert "pass_versions" not in implications, (
            "implications references 'pass_versions' which captured never "
            "carries; point at analysis_runs[].pass_version instead"
        )
        assert "analysis_runs[].pass_version" in implications
        for named in ("hypergumbo_version", "python_version"):
            assert named in rc["captured"]
