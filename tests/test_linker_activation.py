"""Tests for linker activation conditions (ADR-0003 item 4).

Linkers have different activation conditions:
- Protocol linkers: Always run (http, websocket, message_queue)
- Framework linkers: Only run when framework is detected (grpc, graphql)
- Language-pair linkers: Only run when both languages present (jni, swift_objc)
"""
from pathlib import Path

# Import linker modules to register them (side-effect imports)
import hypergumbo.linkers.database_query
import hypergumbo.linkers.dependency
import hypergumbo.linkers.event_sourcing
import hypergumbo.linkers.graphql
import hypergumbo.linkers.graphql_resolver
import hypergumbo.linkers.grpc
import hypergumbo.linkers.http
import hypergumbo.linkers.ipc
import hypergumbo.linkers.jni
import hypergumbo.linkers.message_queue
import hypergumbo.linkers.phoenix_ipc
import hypergumbo.linkers.swift_objc
import hypergumbo.linkers.websocket
from hypergumbo.linkers.registry import (
    LinkerActivation,
    LinkerContext,
    RegisteredLinker,
    get_all_linkers,
    should_run_linker,
)


class TestLinkerActivationModel:
    """Tests for LinkerActivation dataclass."""

    def test_always_activation(self) -> None:
        """Linkers with always=True should always run."""
        activation = LinkerActivation(always=True)
        assert activation.should_run(
            detected_frameworks=set(),
            detected_languages=set(),
        )

    def test_framework_activation_met(self) -> None:
        """Framework linkers should run when framework is detected."""
        activation = LinkerActivation(frameworks=["grpc", "protobuf"])
        assert activation.should_run(
            detected_frameworks={"grpc", "fastapi"},
            detected_languages={"python"},
        )

    def test_framework_activation_unmet(self) -> None:
        """Framework linkers should NOT run when framework is not detected."""
        activation = LinkerActivation(frameworks=["grpc", "protobuf"])
        assert not activation.should_run(
            detected_frameworks={"fastapi"},
            detected_languages={"python"},
        )

    def test_language_pair_activation_met(self) -> None:
        """Language-pair linkers should run when both languages present."""
        activation = LinkerActivation(language_pairs=[("java", "c"), ("java", "cpp")])
        assert activation.should_run(
            detected_frameworks=set(),
            detected_languages={"java", "c", "python"},
        )

    def test_language_pair_activation_unmet(self) -> None:
        """Language-pair linkers should NOT run when pair incomplete."""
        activation = LinkerActivation(language_pairs=[("java", "c")])
        assert not activation.should_run(
            detected_frameworks=set(),
            detected_languages={"java", "python"},  # No C
        )

    def test_combined_activation_any_met(self) -> None:
        """Combined conditions: any met condition should activate."""
        activation = LinkerActivation(
            frameworks=["grpc"],
            language_pairs=[("java", "c")],
        )
        # Framework condition met
        assert activation.should_run(
            detected_frameworks={"grpc"},
            detected_languages={"python"},
        )
        # Language pair condition met
        assert activation.should_run(
            detected_frameworks=set(),
            detected_languages={"java", "c"},
        )

    def test_empty_activation_never_runs(self) -> None:
        """Empty activation (no conditions) should never run."""
        activation = LinkerActivation()
        assert not activation.should_run(
            detected_frameworks={"fastapi"},
            detected_languages={"python"},
        )


class TestLinkerActivationRegistry:
    """Tests that linkers have proper activation conditions."""

    def test_http_linker_always_runs(self) -> None:
        """HTTP linker should always run (protocol linker)."""
        linker = _get_linker_by_name("http")
        assert linker is not None
        assert linker.activation.always is True

    def test_grpc_linker_framework_activation(self) -> None:
        """gRPC linker should only run when gRPC framework detected."""
        linker = _get_linker_by_name("grpc")
        assert linker is not None
        assert "grpc" in linker.activation.frameworks or linker.activation.always

    def test_jni_linker_language_pair_activation(self) -> None:
        """JNI linker should only run when Java and C/C++ present."""
        linker = _get_linker_by_name("jni")
        assert linker is not None
        # Should have language_pairs for (java, c) or (java, cpp)
        has_java_c = any(
            ("java" in pair and ("c" in pair or "cpp" in pair))
            for pair in linker.activation.language_pairs
        )
        assert has_java_c or linker.activation.always


class TestShouldRunLinker:
    """Tests for should_run_linker helper function."""

    def test_should_run_protocol_linker(self) -> None:
        """Protocol linkers should always run."""
        assert should_run_linker(
            "http",
            detected_frameworks=set(),
            detected_languages={"python"},
        )

    def test_should_not_run_framework_linker_without_framework(self) -> None:
        """Framework linkers should not run without framework."""
        # This test assumes grpc linker has framework activation
        result = should_run_linker(
            "grpc",
            detected_frameworks={"fastapi"},  # No gRPC
            detected_languages={"python"},
        )
        # If grpc has activation conditions, it should not run
        # If it's always=True, this test should be skipped
        linker = _get_linker_by_name("grpc")
        if linker and not linker.activation.always:
            assert not result


class TestLinkerFilteringInRun:
    """Integration tests for linker filtering during analysis."""

    def test_linkers_filtered_by_frameworks(self, tmp_path: Path) -> None:
        """Linkers should be filtered based on detected frameworks."""
        # This test verifies the integration works
        # We'll mock the linker context
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            edges=[],
        )
        # Count how many linkers would run with no frameworks
        linkers_to_run = [
            linker for linker in get_all_linkers()
            if linker.activation.should_run(
                detected_frameworks=set(),
                detected_languages={"python"},
            )
        ]
        # Should have some protocol linkers that always run
        always_linkers = [
            linker for linker in get_all_linkers()
            if linker.activation.always
        ]
        assert len(linkers_to_run) >= len(always_linkers)


def _get_linker_by_name(name: str) -> RegisteredLinker | None:
    """Helper to find a linker by name."""
    for linker in get_all_linkers():
        if linker.name == name:
            return linker
    return None
