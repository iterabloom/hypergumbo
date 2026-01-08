"""Tests for linker filtering by framework/language detection (ADR-0003 v0.8.x).

Linkers should only run when their activation conditions are met:
- Framework linkers: only when framework is detected
- Language-pair linkers: only when both languages are present
- Protocol linkers: always run (default)
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hypergumbo.linkers.registry import (
    LinkerActivation,
    LinkerContext,
    LinkerResult,
    RegisteredLinker,
    _LINKER_REGISTRY,
    get_all_linkers,
    register_linker,
    run_all_linkers,
)


class TestLinkerContextWithDetection:
    """Tests for LinkerContext with framework/language detection fields."""

    def test_context_has_detected_frameworks(self) -> None:
        """LinkerContext should have detected_frameworks field."""
        ctx = LinkerContext(
            repo_root=Path("/tmp"),
            detected_frameworks={"fastapi", "pydantic"},
        )
        assert ctx.detected_frameworks == {"fastapi", "pydantic"}

    def test_context_has_detected_languages(self) -> None:
        """LinkerContext should have detected_languages field."""
        ctx = LinkerContext(
            repo_root=Path("/tmp"),
            detected_languages={"python", "javascript"},
        )
        assert ctx.detected_languages == {"python", "javascript"}

    def test_context_defaults_empty_sets(self) -> None:
        """Detection fields should default to empty sets."""
        ctx = LinkerContext(repo_root=Path("/tmp"))
        assert ctx.detected_frameworks == set()
        assert ctx.detected_languages == set()


class TestRunAllLinkersFiltering:
    """Tests for linker filtering in run_all_linkers()."""

    def test_always_linkers_run_regardless_of_frameworks(self, tmp_path: Path) -> None:
        """Linkers with always=True should always run."""
        # Create a mock linker with always=True
        mock_result = LinkerResult()
        mock_func = MagicMock(return_value=mock_result)

        # Register temporarily
        test_linker = RegisteredLinker(
            name="test_always",
            func=mock_func,
            priority=100,
            activation=LinkerActivation(always=True),
        )

        with patch.dict(_LINKER_REGISTRY, {"test_always": test_linker}, clear=True):
            ctx = LinkerContext(
                repo_root=tmp_path,
                detected_frameworks=set(),  # No frameworks
                detected_languages={"python"},
            )
            results = run_all_linkers(ctx)

        # Should have been called
        mock_func.assert_called_once()
        assert any(name == "test_always" for name, _ in results)

    def test_framework_linker_runs_when_framework_detected(self, tmp_path: Path) -> None:
        """Framework linkers should run when their framework is detected."""
        mock_result = LinkerResult()
        mock_func = MagicMock(return_value=mock_result)

        test_linker = RegisteredLinker(
            name="test_grpc",
            func=mock_func,
            priority=100,
            activation=LinkerActivation(frameworks=["grpc"]),
        )

        with patch.dict(_LINKER_REGISTRY, {"test_grpc": test_linker}, clear=True):
            ctx = LinkerContext(
                repo_root=tmp_path,
                detected_frameworks={"grpc", "protobuf"},  # gRPC detected
                detected_languages={"python"},
            )
            results = run_all_linkers(ctx)

        mock_func.assert_called_once()
        assert any(name == "test_grpc" for name, _ in results)

    def test_framework_linker_skipped_when_framework_not_detected(self, tmp_path: Path) -> None:
        """Framework linkers should NOT run when their framework is not detected."""
        mock_result = LinkerResult()
        mock_func = MagicMock(return_value=mock_result)

        test_linker = RegisteredLinker(
            name="test_grpc",
            func=mock_func,
            priority=100,
            activation=LinkerActivation(frameworks=["grpc"]),
        )

        with patch.dict(_LINKER_REGISTRY, {"test_grpc": test_linker}, clear=True):
            ctx = LinkerContext(
                repo_root=tmp_path,
                detected_frameworks={"fastapi"},  # No gRPC
                detected_languages={"python"},
            )
            results = run_all_linkers(ctx)

        # Should NOT have been called
        mock_func.assert_not_called()
        assert not any(name == "test_grpc" for name, _ in results)

    def test_language_pair_linker_runs_when_both_present(self, tmp_path: Path) -> None:
        """Language-pair linkers should run when both languages are present."""
        mock_result = LinkerResult()
        mock_func = MagicMock(return_value=mock_result)

        test_linker = RegisteredLinker(
            name="test_jni",
            func=mock_func,
            priority=100,
            activation=LinkerActivation(language_pairs=[("java", "c")]),
        )

        with patch.dict(_LINKER_REGISTRY, {"test_jni": test_linker}, clear=True):
            ctx = LinkerContext(
                repo_root=tmp_path,
                detected_frameworks=set(),
                detected_languages={"java", "c", "python"},  # Both Java and C
            )
            results = run_all_linkers(ctx)

        mock_func.assert_called_once()
        assert any(name == "test_jni" for name, _ in results)

    def test_language_pair_linker_skipped_when_pair_incomplete(self, tmp_path: Path) -> None:
        """Language-pair linkers should NOT run when pair is incomplete."""
        mock_result = LinkerResult()
        mock_func = MagicMock(return_value=mock_result)

        test_linker = RegisteredLinker(
            name="test_jni",
            func=mock_func,
            priority=100,
            activation=LinkerActivation(language_pairs=[("java", "c")]),
        )

        with patch.dict(_LINKER_REGISTRY, {"test_jni": test_linker}, clear=True):
            ctx = LinkerContext(
                repo_root=tmp_path,
                detected_frameworks=set(),
                detected_languages={"java", "python"},  # No C
            )
            results = run_all_linkers(ctx)

        mock_func.assert_not_called()
        assert not any(name == "test_jni" for name, _ in results)

    def test_multiple_linkers_filtered_correctly(self, tmp_path: Path) -> None:
        """Multiple linkers should be filtered based on their conditions."""
        results_tracker = {"always": False, "framework": False, "language": False}

        def make_mock(key: str) -> MagicMock:
            def tracker(ctx: LinkerContext) -> LinkerResult:
                results_tracker[key] = True
                return LinkerResult()
            return MagicMock(side_effect=tracker)

        linkers = {
            "always_linker": RegisteredLinker(
                name="always_linker",
                func=make_mock("always"),
                priority=100,
                activation=LinkerActivation(always=True),
            ),
            "framework_linker": RegisteredLinker(
                name="framework_linker",
                func=make_mock("framework"),
                priority=101,
                activation=LinkerActivation(frameworks=["graphql"]),  # Not detected
            ),
            "language_linker": RegisteredLinker(
                name="language_linker",
                func=make_mock("language"),
                priority=102,
                activation=LinkerActivation(language_pairs=[("swift", "objc")]),  # Not detected
            ),
        }

        with patch.dict(_LINKER_REGISTRY, linkers, clear=True):
            ctx = LinkerContext(
                repo_root=tmp_path,
                detected_frameworks={"fastapi"},  # Not graphql
                detected_languages={"python"},    # Not swift/objc
            )
            run_all_linkers(ctx)

        # Only always_linker should have run
        assert results_tracker["always"] is True
        assert results_tracker["framework"] is False
        assert results_tracker["language"] is False


class TestLinkerActivationFromRegistry:
    """Tests that real linkers have proper activation conditions."""

    def test_grpc_linker_has_framework_activation(self) -> None:
        """gRPC linker should have framework-based activation."""
        # Import to trigger registration
        import hypergumbo.linkers.grpc

        linker = _LINKER_REGISTRY.get("grpc")
        assert linker is not None
        # Should activate for grpc or protobuf frameworks
        assert linker.activation.frameworks == ["grpc", "protobuf"]
        assert linker.activation.always is False

    def test_graphql_linker_has_framework_activation(self) -> None:
        """GraphQL linker should have framework-based activation."""
        import hypergumbo.linkers.graphql

        linker = _LINKER_REGISTRY.get("graphql")
        assert linker is not None
        assert linker.activation.frameworks == ["graphql"]
        assert linker.activation.always is False

    def test_phoenix_linker_has_framework_activation(self) -> None:
        """Phoenix IPC linker should have framework-based activation."""
        import hypergumbo.linkers.phoenix_ipc

        linker = _LINKER_REGISTRY.get("phoenix_ipc")
        assert linker is not None
        assert linker.activation.frameworks == ["phoenix"]
        assert linker.activation.always is False

    def test_jni_linker_has_language_pair_activation(self) -> None:
        """JNI linker should have language-pair activation."""
        import hypergumbo.linkers.jni

        linker = _LINKER_REGISTRY.get("jni")
        assert linker is not None
        # Should activate when both java and c/cpp are present
        assert ("java", "c") in linker.activation.language_pairs
        assert ("java", "cpp") in linker.activation.language_pairs
        assert linker.activation.always is False

    def test_swift_objc_linker_has_language_pair_activation(self) -> None:
        """Swift/Objective-C linker should have language-pair activation."""
        import hypergumbo.linkers.swift_objc

        linker = _LINKER_REGISTRY.get("swift_objc")
        assert linker is not None
        assert ("swift", "objc") in linker.activation.language_pairs
        assert linker.activation.always is False

    def test_http_linker_always_runs(self) -> None:
        """HTTP linker (protocol) should always run."""
        import hypergumbo.linkers.http

        linker = _LINKER_REGISTRY.get("http")
        assert linker is not None
        # Protocol linkers should have always=True
        assert linker.activation.always is True

    def test_websocket_linker_always_runs(self) -> None:
        """WebSocket linker (protocol) should always run."""
        import hypergumbo.linkers.websocket

        linker = _LINKER_REGISTRY.get("websocket")
        assert linker is not None
        assert linker.activation.always is True

    def test_message_queue_linker_always_runs(self) -> None:
        """Message queue linker (protocol) should always run."""
        import hypergumbo.linkers.message_queue

        linker = _LINKER_REGISTRY.get("message_queue")
        assert linker is not None
        assert linker.activation.always is True
