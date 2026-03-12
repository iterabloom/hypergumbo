# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for requirements.py analyzer.

Tests specific branch paths in the Python requirements.txt analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Package requirement extraction
- Version spec and extras
- URL-based requirements
- Global options (-r, -c, -e)
- Dependency edges
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_mainstream.requirements import (
    _make_symbol_id,
    analyze_requirements,
    find_requirements_files,
)


def make_requirements_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a requirements file with given content."""
    (tmp_path / name).write_text(content)


class TestRequirementsHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id(Path("requirements.txt"), "requests", "requirement")
        assert symbol_id == "requirements:requirements.txt:requirement:requests"


class TestPackageRequirementExtraction:
    """Branch coverage for package requirement extraction."""

    def test_simple_requirement(self, tmp_path: Path) -> None:
        """Test simple package requirement."""
        make_requirements_file(tmp_path, "requirements.txt", """
requests
click
""")
        result = analyze_requirements(tmp_path)
        assert not result.skipped

        reqs = [s for s in result.symbols if s.kind == "requirement"]
        assert len(reqs) >= 2
        assert any(r.name == "requests" for r in reqs)
        assert any(r.name == "click" for r in reqs)

    def test_requirement_with_version(self, tmp_path: Path) -> None:
        """Test package with version specifier."""
        make_requirements_file(tmp_path, "requirements.txt", """
requests>=2.28.0
click==8.0.0
numpy<2.0
""")
        result = analyze_requirements(tmp_path)
        reqs = [s for s in result.symbols if s.kind == "requirement"]

        requests_req = next((r for r in reqs if r.name == "requests"), None)
        assert requests_req is not None
        assert requests_req.meta is not None
        assert ">=2.28.0" in requests_req.meta.get("version_spec", "")

    def test_requirement_with_extras(self, tmp_path: Path) -> None:
        """Test package with extras."""
        make_requirements_file(tmp_path, "requirements.txt", """
requests[security,socks]>=2.28.0
""")
        result = analyze_requirements(tmp_path)
        reqs = [s for s in result.symbols if s.kind == "requirement"]

        requests_req = next((r for r in reqs if r.name == "requests"), None)
        assert requests_req is not None
        assert requests_req.meta is not None
        extras = requests_req.meta.get("extras", [])
        assert "security" in extras or len(extras) > 0

    def test_requirement_with_marker(self, tmp_path: Path) -> None:
        """Test package with environment marker."""
        make_requirements_file(tmp_path, "requirements.txt", """
pywin32>=300; sys_platform == 'win32'
""")
        result = analyze_requirements(tmp_path)
        reqs = [s for s in result.symbols if s.kind == "requirement"]

        win_req = next((r for r in reqs if r.name == "pywin32"), None)
        assert win_req is not None
        assert win_req.meta is not None
        marker = win_req.meta.get("marker", "")
        assert "win32" in marker or marker != ""


class TestURLRequirements:
    """Branch coverage for URL-based requirements."""

    def test_git_url_requirement(self, tmp_path: Path) -> None:
        """Test git+ URL requirement."""
        make_requirements_file(tmp_path, "requirements.txt", """
git+https://github.com/user/repo.git#egg=mypackage
""")
        result = analyze_requirements(tmp_path)
        url_reqs = [s for s in result.symbols if s.kind == "url_requirement"]

        assert len(url_reqs) >= 1
        url_req = url_reqs[0]
        assert url_req.meta is not None
        assert url_req.meta.get("source_type") == "git"

    def test_url_with_version_tag(self, tmp_path: Path) -> None:
        """Test URL with version tag."""
        make_requirements_file(tmp_path, "requirements.txt", """
git+https://github.com/user/repo.git@v1.0.0#egg=mypackage
""")
        result = analyze_requirements(tmp_path)
        url_reqs = [s for s in result.symbols if s.kind == "url_requirement"]

        assert len(url_reqs) >= 1


class TestGlobalOptions:
    """Branch coverage for global options (-r, -c, -e)."""

    def test_include_requirements_option(self, tmp_path: Path) -> None:
        """Test -r option creates includes edge."""
        make_requirements_file(tmp_path, "requirements.txt", """
-r base.txt
requests
""")
        result = analyze_requirements(tmp_path)
        include_edges = [e for e in result.edges if e.edge_type == "includes"]

        assert len(include_edges) >= 1

    def test_constraint_option(self, tmp_path: Path) -> None:
        """Test -c option creates constrains edge."""
        make_requirements_file(tmp_path, "requirements.txt", """
-c constraints.txt
requests
""")
        result = analyze_requirements(tmp_path)
        constrain_edges = [e for e in result.edges if e.edge_type == "constrains"]

        assert len(constrain_edges) >= 1

    def test_editable_option(self, tmp_path: Path) -> None:
        """Test -e option creates editable symbol."""
        make_requirements_file(tmp_path, "requirements.txt", """
-e ./local_package
""")
        result = analyze_requirements(tmp_path)
        editables = [s for s in result.symbols if s.kind == "editable"]

        assert len(editables) >= 1
        assert editables[0].meta is not None
        assert editables[0].meta.get("editable") is True


class TestDependencyEdges:
    """Branch coverage for dependency edge extraction."""

    def test_dependency_edge_created(self, tmp_path: Path) -> None:
        """Test dependency edges are created."""
        make_requirements_file(tmp_path, "requirements.txt", """
requests>=2.0
""")
        result = analyze_requirements(tmp_path)
        dep_edges = [e for e in result.edges if e.edge_type == "depends"]

        assert len(dep_edges) >= 1
        assert any("pypi:package:requests" in e.dst for e in dep_edges)


class TestFindRequirementsFiles:
    """Branch coverage for file discovery."""

    def test_finds_requirements_txt(self, tmp_path: Path) -> None:
        """Test requirements.txt is discovered."""
        (tmp_path / "requirements.txt").write_text("requests")

        files = find_requirements_files(tmp_path)
        assert len(files) >= 1
        assert any(f.name == "requirements.txt" for f in files)

    def test_finds_requirements_variations(self, tmp_path: Path) -> None:
        """Test requirements variations are discovered."""
        (tmp_path / "requirements-dev.txt").write_text("pytest")

        files = find_requirements_files(tmp_path)
        assert len(files) >= 1


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_requirements_files(self, tmp_path: Path) -> None:
        """Test directory with no requirements files."""
        result = analyze_requirements(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_requirements(self, tmp_path: Path) -> None:
        """Test minimal requirements file."""
        make_requirements_file(tmp_path, "requirements.txt", """
requests
""")
        result = analyze_requirements(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_requirements_file(tmp_path, "requirements.txt", """
requests>=2.0
""")
        result = analyze_requirements(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1
