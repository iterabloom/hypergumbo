"""Tests for export-capsule functionality."""
import json
import tarfile
from pathlib import Path


from hypergumbo.export import (
    sanitize_plan,
    is_repo_specific_rule,
    create_shareable_txt,
    compute_checksums,
    export_capsule,
)


class TestSanitizePlan:
    """Tests for plan sanitization."""

    def test_removes_features(self) -> None:
        """Sanitization removes features array."""
        plan = {
            "version": "0.1.0",
            "passes": [{"id": "python-ast-v1", "enabled": True}],
            "packs": [],
            "rules": [],
            "features": [
                {"id": "auth-flow", "query": {"entrypoint": "login"}}
            ],
        }

        sanitized, removed = sanitize_plan(plan)

        assert sanitized["features"] == []
        assert removed["features_count"] == 1

    def test_preserves_generic_rules(self) -> None:
        """Sanitization preserves generic glob rules."""
        plan = {
            "version": "0.1.0",
            "passes": [],
            "packs": [],
            "rules": [
                {"type": "exclude_pattern", "glob": "**/*_test.py"},
                {"type": "exclude_pattern", "glob": "**/node_modules/**"},
            ],
            "features": [],
        }

        sanitized, removed = sanitize_plan(plan)

        assert len(sanitized["rules"]) == 2
        assert removed["rules_removed"] == 0

    def test_removes_repo_specific_rules(self) -> None:
        """Sanitization removes repo-specific rules."""
        plan = {
            "version": "0.1.0",
            "passes": [],
            "packs": [],
            "rules": [
                {"type": "exclude_pattern", "glob": "**/*_test.py"},
                {"type": "entrypoint_pattern", "pattern": "src/main.py"},
                {"type": "exclude_pattern", "glob": "my_project/internal/**"},
            ],
            "features": [],
        }

        sanitized, removed = sanitize_plan(plan)

        # Only generic glob pattern preserved
        assert len(sanitized["rules"]) == 1
        assert sanitized["rules"][0]["glob"] == "**/*_test.py"
        assert removed["rules_removed"] == 2


class TestIsRepoSpecificRule:
    """Tests for repo-specific rule detection."""

    def test_glob_with_wildcards_is_generic(self) -> None:
        """Glob patterns with ** are generic."""
        rule = {"type": "exclude_pattern", "glob": "**/*_test.py"}
        assert is_repo_specific_rule(rule) is False

    def test_glob_without_wildcards_is_specific(self) -> None:
        """Glob patterns without ** are repo-specific."""
        rule = {"type": "exclude_pattern", "glob": "src/utils/helpers.py"}
        assert is_repo_specific_rule(rule) is True

    def test_entrypoint_pattern_is_specific(self) -> None:
        """Entrypoint patterns are always repo-specific."""
        rule = {"type": "entrypoint_pattern", "pattern": "if __name__"}
        assert is_repo_specific_rule(rule) is True

    def test_node_modules_glob_is_generic(self) -> None:
        """Standard exclude patterns are generic."""
        rule = {"type": "exclude_pattern", "glob": "**/node_modules/**"}
        assert is_repo_specific_rule(rule) is False

    def test_unknown_rule_type_is_specific(self) -> None:
        """Unknown rule types default to repo-specific."""
        rule = {"type": "custom_rule"}
        assert is_repo_specific_rule(rule) is True


class TestCreateShareableTxt:
    """Tests for SHAREABLE.txt generation."""

    def test_includes_version(self) -> None:
        """SHAREABLE.txt includes format version."""
        removed = {"features_count": 0, "rules_removed": 0}
        content = create_shareable_txt(removed, {"capsule.json": "abc123"})

        assert "shareable_format_version" in content

    def test_includes_redaction_counts(self) -> None:
        """SHAREABLE.txt includes redaction counts."""
        removed = {"features_count": 3, "rules_removed": 2}
        content = create_shareable_txt(removed, {})

        assert "features_removed: 3" in content
        assert "rules_removed: 2" in content

    def test_includes_checksums(self) -> None:
        """SHAREABLE.txt references checksum file."""
        removed = {"features_count": 0, "rules_removed": 0}
        content = create_shareable_txt(removed, {"capsule.json": "abc123"})

        assert "SHA256SUMS" in content or "checksum" in content.lower()


class TestComputeChecksums:
    """Tests for checksum computation."""

    def test_computes_sha256(self, tmp_path: Path) -> None:
        """Computes SHA256 checksums for files."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        checksums = compute_checksums([test_file])

        assert "test.txt" in checksums
        # SHA256 of "hello world" is known
        assert len(checksums["test.txt"]) == 64  # SHA256 hex length


class TestExportCapsule:
    """Tests for export_capsule function."""

    def test_creates_tarball(self, tmp_path: Path) -> None:
        """Export creates a tarball."""
        # Setup capsule directory
        capsule_dir = tmp_path / ".hypergumbo"
        capsule_dir.mkdir()
        (capsule_dir / "capsule.json").write_text('{"repo_root": "/tmp"}')
        (capsule_dir / "capsule_plan.json").write_text(
            '{"version": "0.1.0", "passes": [], "packs": [], "rules": [], "features": []}'
        )

        out_path = tmp_path / "capsule.tar.gz"
        export_capsule(tmp_path, out_path, shareable=False)

        assert out_path.exists()
        assert tarfile.is_tarfile(out_path)

    def test_shareable_mode_sanitizes_plan(self, tmp_path: Path) -> None:
        """Shareable mode sanitizes the plan."""
        capsule_dir = tmp_path / ".hypergumbo"
        capsule_dir.mkdir()
        (capsule_dir / "capsule.json").write_text('{"repo_root": "/tmp"}')
        (capsule_dir / "capsule_plan.json").write_text(json.dumps({
            "version": "0.1.0",
            "passes": [],
            "packs": [],
            "rules": [],
            "features": [{"id": "secret-feature"}],
        }))

        out_path = tmp_path / "capsule.tar.gz"
        export_capsule(tmp_path, out_path, shareable=True)

        # Extract and verify plan was sanitized
        with tarfile.open(out_path, "r:gz") as tar:
            plan_file = tar.extractfile("capsule_plan.json")
            assert plan_file is not None
            plan = json.load(plan_file)
            assert plan["features"] == []

    def test_shareable_mode_includes_shareable_txt(self, tmp_path: Path) -> None:
        """Shareable mode includes SHAREABLE.txt."""
        capsule_dir = tmp_path / ".hypergumbo"
        capsule_dir.mkdir()
        (capsule_dir / "capsule.json").write_text('{"repo_root": "/tmp"}')
        (capsule_dir / "capsule_plan.json").write_text(
            '{"version": "0.1.0", "passes": [], "packs": [], "rules": [], "features": []}'
        )

        out_path = tmp_path / "capsule.tar.gz"
        export_capsule(tmp_path, out_path, shareable=True)

        with tarfile.open(out_path, "r:gz") as tar:
            names = tar.getnames()
            assert "SHAREABLE.txt" in names

    def test_shareable_mode_includes_checksums(self, tmp_path: Path) -> None:
        """Shareable mode includes SHA256SUMS."""
        capsule_dir = tmp_path / ".hypergumbo"
        capsule_dir.mkdir()
        (capsule_dir / "capsule.json").write_text('{"repo_root": "/tmp"}')
        (capsule_dir / "capsule_plan.json").write_text(
            '{"version": "0.1.0", "passes": [], "packs": [], "rules": [], "features": []}'
        )

        out_path = tmp_path / "capsule.tar.gz"
        export_capsule(tmp_path, out_path, shareable=True)

        with tarfile.open(out_path, "r:gz") as tar:
            names = tar.getnames()
            assert "SHA256SUMS" in names

    def test_shareable_excludes_profile(self, tmp_path: Path) -> None:
        """Shareable mode excludes profile.json."""
        capsule_dir = tmp_path / ".hypergumbo"
        capsule_dir.mkdir()
        (capsule_dir / "capsule.json").write_text('{"repo_root": "/tmp"}')
        (capsule_dir / "capsule_plan.json").write_text(
            '{"version": "0.1.0", "passes": [], "packs": [], "rules": [], "features": []}'
        )
        (capsule_dir / "profile.json").write_text('{"languages": {"python": {}}}')

        out_path = tmp_path / "capsule.tar.gz"
        export_capsule(tmp_path, out_path, shareable=True)

        with tarfile.open(out_path, "r:gz") as tar:
            names = tar.getnames()
            assert "profile.json" not in names

    def test_shareable_strips_repo_root(self, tmp_path: Path) -> None:
        """Shareable mode strips repo_root from capsule.json."""
        capsule_dir = tmp_path / ".hypergumbo"
        capsule_dir.mkdir()
        (capsule_dir / "capsule.json").write_text(
            '{"repo_root": "/home/user/secret_project", "assistant": "template"}'
        )
        (capsule_dir / "capsule_plan.json").write_text(
            '{"version": "0.1.0", "passes": [], "packs": [], "rules": [], "features": []}'
        )

        out_path = tmp_path / "capsule.tar.gz"
        export_capsule(tmp_path, out_path, shareable=True)

        with tarfile.open(out_path, "r:gz") as tar:
            capsule_file = tar.extractfile("capsule.json")
            assert capsule_file is not None
            capsule = json.load(capsule_file)
            assert capsule["repo_root"] == "<redacted>"

    def test_non_shareable_preserves_all(self, tmp_path: Path) -> None:
        """Non-shareable mode preserves all content."""
        capsule_dir = tmp_path / ".hypergumbo"
        capsule_dir.mkdir()
        (capsule_dir / "capsule.json").write_text(
            '{"repo_root": "/home/user/project"}'
        )
        (capsule_dir / "capsule_plan.json").write_text(json.dumps({
            "version": "0.1.0",
            "passes": [],
            "packs": [],
            "rules": [],
            "features": [{"id": "my-feature"}],
        }))
        (capsule_dir / "profile.json").write_text('{"languages": {}}')

        out_path = tmp_path / "capsule.tar.gz"
        export_capsule(tmp_path, out_path, shareable=False)

        with tarfile.open(out_path, "r:gz") as tar:
            names = tar.getnames()
            assert "profile.json" in names

            plan_file = tar.extractfile("capsule_plan.json")
            assert plan_file is not None
            plan = json.load(plan_file)
            assert len(plan["features"]) == 1

    def test_shareable_includes_extra_files(self, tmp_path: Path) -> None:
        """Shareable mode includes non-excluded extra files."""
        capsule_dir = tmp_path / ".hypergumbo"
        capsule_dir.mkdir()
        (capsule_dir / "capsule.json").write_text('{"repo_root": "/tmp"}')
        (capsule_dir / "capsule_plan.json").write_text(
            '{"version": "0.1.0", "passes": [], "packs": [], "rules": [], "features": []}'
        )
        # Add a custom file that should be included
        (capsule_dir / "custom_config.json").write_text('{"custom": true}')

        out_path = tmp_path / "capsule.tar.gz"
        export_capsule(tmp_path, out_path, shareable=True)

        with tarfile.open(out_path, "r:gz") as tar:
            names = tar.getnames()
            assert "custom_config.json" in names
