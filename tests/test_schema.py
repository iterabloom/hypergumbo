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

# Add scripts to path for generate-schema imports
REPO_ROOT = Path(__file__).parent.parent
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
        from hypergumbo.schema import new_behavior_map

        schema = load_schema()
        behavior_map = new_behavior_map()

        # Should not raise
        jsonschema.validate(behavior_map, schema)

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
        from hypergumbo.ir import Span, Symbol

        schema = load_schema()

        symbol = Symbol(
            id="test::func::1-5",
            name="test_func",
            kind="function",
            language="python",
            path="/path/to/file.py",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=10),
            origin="python-ast-v1",
            origin_run_id="uuid:12345",
            origin_run_signature="sha256:abc123",
            stable_id="sha256:stable123",
            shape_id="sha256:shape456",
            canonical_name="module.test_func",
            fingerprint="sha256:content789",
            quality={"score": 0.95, "reason": "well-documented"},
            meta={"decorator": "@pytest.fixture"},
            supply_chain_tier=1,
            supply_chain_reason="matches ^src/",
        )

        validator = make_validator(schema, "Symbol")
        validator.validate(symbol.to_dict())

    def test_edge_with_all_fields_validates(self):
        """An Edge with all optional fields validates."""
        from hypergumbo.ir import Edge

        schema = load_schema()

        edge = Edge.create(
            src="test::caller::1-5",
            dst="test::callee::10-15",
            edge_type="calls",
            line=3,
            origin="python-ast-v1",
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
        from hypergumbo.ir import AnalysisRun

        schema = load_schema()

        run = AnalysisRun.create(
            pass_id="python-ast-v1",
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

    def test_invalid_symbol_kind_fails_validation(self):
        """A symbol with an invalid kind fails validation."""
        schema = load_schema()

        invalid_symbol = {
            "id": "test::sym",
            "name": "test",
            "kind": "invalid_kind_that_does_not_exist",
            "language": "python",
            "path": "/test.py",
            "span": {"start_line": 1, "end_line": 1, "start_col": 0, "end_col": 4},
        }

        validator = make_validator(schema, "Symbol")
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(invalid_symbol)


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
        from hypergumbo.schema import SCHEMA_VERSION

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

        # Known edge types from linkers and analyzers
        known_edge_types = {
            # From analyzers
            "calls", "imports", "instantiates", "extends", "implements",
            "references", "depends_on", "links", "sources",
            "script_src", "base_image", "kernel_launch",
            # From linkers
            "native_bridge",  # JNI
            "message_send", "message_receive",  # IPC, Phoenix
            "websocket_message", "websocket_connection",  # WebSocket
            "grpc_calls",  # gRPC
            "http_calls",  # HTTP
            "graphql_calls",  # GraphQL
            "message_queue",  # Message Queue
            "query_references",  # Database Query
            "event_publishes",  # Event Sourcing
            "resolver_implements", "resolver_for_type",  # GraphQL Resolver
        }

        missing = known_edge_types - edge_types_in_schema
        assert not missing, (
            f"Edge types missing from schema: {missing}. "
            f"Update scripts/generate-schema and run it."
        )

    def test_all_symbol_kinds_in_schema(self):
        """All symbol kinds used in analyzers are in the schema enum."""
        schema = load_schema()
        kinds_in_schema = set(schema["$defs"]["Symbol"]["properties"]["kind"]["enum"])

        # Known symbol kinds from analyzers and linkers
        known_kinds = {
            # Common
            "function", "class", "method", "constructor", "property",
            "interface", "type", "enum", "struct", "trait", "module",
            "route", "getter", "setter", "macro", "data", "instance",
            # Solidity
            "contract", "event", "modifier", "library",
            # SQL
            "table", "view", "trigger", "index", "procedure",
            # Terraform
            "resource", "variable", "output", "provider",
            # CUDA
            "kernel", "device_function", "host_device_function",
            # VHDL
            "entity", "architecture", "package", "component",
            # Shaders (GLSL/WGSL)
            "uniform", "input", "storage",
            # CSS
            "keyframes", "media", "font_face",
            # Ansible
            "playbook", "task", "handler",
            # Linkers
            "event_publisher", "event_subscriber",
            "ipc_send", "ipc_receive", "websocket_endpoint",
            "grpc_service", "grpc_servicer", "grpc_stub", "grpc_client", "grpc_server",
            "http_client", "graphql_client", "graphql_resolver",
            "mq_publisher", "mq_subscriber", "db_query",
        }

        missing = known_kinds - kinds_in_schema
        assert not missing, (
            f"Symbol kinds missing from schema: {missing}. "
            f"Update scripts/generate-schema and run it."
        )
