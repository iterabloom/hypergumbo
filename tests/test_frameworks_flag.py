"""Tests for --frameworks flag (ADR-0003 item 3).

The --frameworks flag controls which frameworks hypergumbo checks for:
- none: Skip framework detection entirely (base analysis only)
- all: Check all known framework patterns (exhaustive)
- explicit list: Only check specified frameworks (e.g., fastapi,celery)
- default (None): Auto-detect based on detected languages (current behavior)
"""
import json
from pathlib import Path

from hypergumbo.cli import run_behavior_map
from hypergumbo.profile import FrameworkMode, resolve_frameworks


class TestFrameworksFlagParsing:
    """Tests for framework specification parsing."""

    def test_frameworks_none_returns_empty(self) -> None:
        """--frameworks=none should return empty set."""
        result = resolve_frameworks("none", detected_languages={"python"})
        assert result.mode == FrameworkMode.NONE
        assert result.frameworks == set()

    def test_frameworks_all_returns_all_for_language(self) -> None:
        """--frameworks=all should return all frameworks for detected languages."""
        result = resolve_frameworks("all", detected_languages={"python"})
        assert result.mode == FrameworkMode.ALL
        # Should include all Python frameworks
        assert "fastapi" in result.frameworks
        assert "flask" in result.frameworks
        assert "django" in result.frameworks
        # Should NOT include JS frameworks (no JS detected)
        assert "express" not in result.frameworks
        assert "react" not in result.frameworks

    def test_frameworks_explicit_list(self) -> None:
        """--frameworks=fastapi,celery should only check those frameworks."""
        result = resolve_frameworks("fastapi,celery", detected_languages={"python"})
        assert result.mode == FrameworkMode.EXPLICIT
        assert result.frameworks == {"fastapi", "celery"}

    def test_frameworks_explicit_ignores_unavailable(self) -> None:
        """Explicit frameworks not relevant to detected languages are ignored."""
        # Specifying express when only Python detected
        result = resolve_frameworks("fastapi,express", detected_languages={"python"})
        assert result.mode == FrameworkMode.EXPLICIT
        # express should still be in the set (user explicitly requested it)
        assert "fastapi" in result.frameworks
        assert "express" in result.frameworks

    def test_frameworks_default_auto_detects(self) -> None:
        """Default (None) should auto-detect based on languages."""
        result = resolve_frameworks(None, detected_languages={"python", "javascript"})
        assert result.mode == FrameworkMode.AUTO
        # Should include all frameworks for both languages
        assert "fastapi" in result.frameworks
        assert "express" in result.frameworks


class TestFrameworksFlagIntegration:
    """Integration tests for --frameworks with run_behavior_map."""

    def test_frameworks_none_skips_detection(self, tmp_path: Path) -> None:
        """--frameworks=none should skip framework detection."""
        # Create a FastAPI project
        (tmp_path / "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "myapp"\ndependencies = ["fastapi"]\n'
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, frameworks="none")

        data = json.loads(out_path.read_text())

        # Frameworks should be empty (detection skipped)
        assert data["profile"]["frameworks"] == []
        # But languages should still be detected
        assert "python" in data["profile"]["languages"]

    def test_frameworks_explicit_only_checks_specified(self, tmp_path: Path) -> None:
        """--frameworks=celery should only detect celery, not fastapi."""
        # Create a project with both FastAPI and Celery
        (tmp_path / "app.py").write_text(
            "from fastapi import FastAPI\nfrom celery import Celery\n"
        )
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["fastapi", "celery"]\n'
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, frameworks="celery")

        data = json.loads(out_path.read_text())

        # Only celery should be in frameworks (we only checked for it)
        assert "celery" in data["profile"]["frameworks"]
        assert "fastapi" not in data["profile"]["frameworks"]

    def test_frameworks_all_checks_all_patterns(self, tmp_path: Path) -> None:
        """--frameworks=all should check all known framework patterns."""
        (tmp_path / "app.py").write_text("from flask import Flask\n")
        (tmp_path / "requirements.txt").write_text("flask\n")

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, frameworks="all")

        data = json.loads(out_path.read_text())

        # Flask should be detected
        assert "flask" in data["profile"]["frameworks"]
        # Profile should indicate exhaustive mode was used
        assert data["profile"].get("framework_mode") == "all"

    def test_frameworks_default_auto_detects(self, tmp_path: Path) -> None:
        """Default should auto-detect frameworks (existing behavior)."""
        (tmp_path / "app.py").write_text("from fastapi import FastAPI\n")
        (tmp_path / "pyproject.toml").write_text('dependencies = ["fastapi"]\n')

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path)  # No frameworks arg

        data = json.loads(out_path.read_text())

        # FastAPI should be detected (auto-detect mode)
        assert "fastapi" in data["profile"]["frameworks"]

    def test_frameworks_explicit_reports_undetected(self, tmp_path: Path) -> None:
        """Explicit frameworks that don't match should be noted."""
        (tmp_path / "app.py").write_text("print('hello')\n")

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, frameworks="fastapi")

        data = json.loads(out_path.read_text())

        # FastAPI NOT detected (no dependencies)
        assert "fastapi" not in data["profile"]["frameworks"]
        # Profile should indicate what was explicitly requested
        assert data["profile"].get("framework_mode") == "explicit"
        assert "fastapi" in data["profile"].get("requested_frameworks", [])


class TestFrameworkModeInOutput:
    """Tests that framework mode is recorded in output."""

    def test_output_records_none_mode(self, tmp_path: Path) -> None:
        """Output should record framework_mode=none."""
        (tmp_path / "app.py").write_text("x = 1\n")

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, frameworks="none")

        data = json.loads(out_path.read_text())
        assert data["profile"]["framework_mode"] == "none"

    def test_output_records_all_mode(self, tmp_path: Path) -> None:
        """Output should record framework_mode=all."""
        (tmp_path / "app.py").write_text("x = 1\n")

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, frameworks="all")

        data = json.loads(out_path.read_text())
        assert data["profile"]["framework_mode"] == "all"

    def test_output_records_explicit_mode(self, tmp_path: Path) -> None:
        """Output should record framework_mode=explicit with requested list."""
        (tmp_path / "app.py").write_text("x = 1\n")

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, frameworks="fastapi,flask")

        data = json.loads(out_path.read_text())
        assert data["profile"]["framework_mode"] == "explicit"
        assert set(data["profile"]["requested_frameworks"]) == {"fastapi", "flask"}

    def test_output_records_auto_mode(self, tmp_path: Path) -> None:
        """Output should record framework_mode=auto (default)."""
        (tmp_path / "app.py").write_text("x = 1\n")

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path)

        data = json.loads(out_path.read_text())
        assert data["profile"]["framework_mode"] == "auto"
