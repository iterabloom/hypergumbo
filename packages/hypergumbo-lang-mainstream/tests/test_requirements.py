"""Tests for the Python requirements.txt analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_mainstream import requirements as requirements_module
from hypergumbo_lang_mainstream.requirements import (
    RequirementsAnalysisResult,
    analyze_requirements,
    find_requirements_files,
    is_requirements_tree_sitter_available,
)


def make_requirements_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a requirements file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindRequirementsFiles:
    """Tests for find_requirements_files function."""

    def test_finds_requirements_txt(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", "flask>=2.0")
        files = find_requirements_files(tmp_path)
        assert len(files) >= 1
        names = {f.name for f in files}
        assert "requirements.txt" in names

    def test_finds_requirements_variants(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements-dev.txt", "pytest")
        make_requirements_file(tmp_path, "requirements_test.txt", "coverage")
        make_requirements_file(tmp_path, "dev-requirements.txt", "black")
        files = find_requirements_files(tmp_path)
        assert len(files) >= 3

    def test_finds_in_subdirectories(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements/base.txt", "flask")
        make_requirements_file(tmp_path, "requirements/dev.txt", "pytest")
        files = find_requirements_files(tmp_path)
        assert len(files) >= 2

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_requirements_files(tmp_path)
        assert files == []


class TestIsRequirementsTreeSitterAvailable:
    """Tests for is_requirements_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_requirements_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(requirements_module, "is_requirements_tree_sitter_available", return_value=False):
            assert requirements_module.is_requirements_tree_sitter_available() is False


class TestAnalyzeRequirements:
    """Tests for analyze_requirements function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", "flask>=2.0")
        with patch.object(requirements_module, "is_requirements_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Requirements analysis skipped"):
                result = requirements_module.analyze_requirements(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_simple_requirement(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", "flask\n")
        result = analyze_requirements(tmp_path)
        assert not result.skipped
        req = next((s for s in result.symbols if s.kind == "requirement"), None)
        assert req is not None
        assert req.name == "flask"
        assert req.language == "requirements"

    def test_extracts_requirement_with_version(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", "flask>=2.0.0")
        result = analyze_requirements(tmp_path)
        req = next((s for s in result.symbols if s.kind == "requirement"), None)
        assert req is not None
        assert req.name == "flask"
        assert req.meta.get("version_spec") == ">=2.0.0"
        assert ">=2.0.0" in req.signature

    def test_extracts_requirement_with_exact_version(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", "requests==2.28.0")
        result = analyze_requirements(tmp_path)
        req = next((s for s in result.symbols if s.kind == "requirement"), None)
        assert req is not None
        assert req.meta.get("version_spec") == "==2.28.0"

    def test_extracts_requirement_with_range(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", "numpy>=1.20,<2.0")
        result = analyze_requirements(tmp_path)
        req = next((s for s in result.symbols if s.kind == "requirement"), None)
        assert req is not None
        assert ">=1.20,<2.0" in req.meta.get("version_spec", "")

    def test_extracts_requirement_with_extras(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", "django[argon2]>=4.0")
        result = analyze_requirements(tmp_path)
        req = next((s for s in result.symbols if s.kind == "requirement"), None)
        assert req is not None
        assert "argon2" in req.meta.get("extras", [])
        assert "[argon2]" in req.signature

    def test_extracts_requirement_with_marker(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", 'matplotlib; python_version >= "3.8"')
        result = analyze_requirements(tmp_path)
        req = next((s for s in result.symbols if s.kind == "requirement"), None)
        assert req is not None
        assert "python_version" in req.meta.get("marker", "")

    def test_extracts_multiple_requirements(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", """
flask>=2.0
requests==2.28.0
numpy
""")
        result = analyze_requirements(tmp_path)
        reqs = [s for s in result.symbols if s.kind == "requirement"]
        assert len(reqs) == 3
        names = {r.name for r in reqs}
        assert names == {"flask", "requests", "numpy"}

    def test_creates_depends_edges(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", """
flask>=2.0
requests
""")
        result = analyze_requirements(tmp_path)
        dep_edges = [e for e in result.edges if e.edge_type == "depends"]
        assert len(dep_edges) >= 2
        dsts = {e.dst for e in dep_edges}
        assert "pypi:package:flask" in dsts
        assert "pypi:package:requests" in dsts

    def test_extracts_git_url_requirement(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", """
git+https://github.com/user/repo.git@v1.0.0#egg=mypackage
""")
        result = analyze_requirements(tmp_path)
        url_req = next((s for s in result.symbols if s.kind == "url_requirement"), None)
        assert url_req is not None
        assert url_req.meta.get("source_type") == "git"
        assert url_req.meta.get("package_name") == "mypackage"

    def test_extracts_url_requirement_without_egg(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", """
git+https://github.com/user/myrepo.git
""")
        result = analyze_requirements(tmp_path)
        url_req = next((s for s in result.symbols if s.kind == "url_requirement"), None)
        assert url_req is not None
        assert url_req.meta.get("package_name") == "myrepo"

    def test_extracts_url_with_ref(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", """
git+https://github.com/user/repo.git@main
""")
        result = analyze_requirements(tmp_path)
        url_req = next((s for s in result.symbols if s.kind == "url_requirement"), None)
        assert url_req is not None
        assert url_req.meta.get("package_name") == "repo"

    def test_extracts_include_option(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", """
-r base-requirements.txt
""")
        result = analyze_requirements(tmp_path)
        include_edges = [e for e in result.edges if e.edge_type == "includes"]
        assert len(include_edges) == 1
        assert "base-requirements.txt" in include_edges[0].dst

    def test_extracts_constraint_option(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", """
-c constraints.txt
""")
        result = analyze_requirements(tmp_path)
        constraint_edges = [e for e in result.edges if e.edge_type == "constrains"]
        assert len(constraint_edges) == 1
        assert "constraints.txt" in constraint_edges[0].dst

    def test_extracts_editable_option(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", """
-e ./local_package
""")
        result = analyze_requirements(tmp_path)
        editable = next((s for s in result.symbols if s.kind == "editable"), None)
        assert editable is not None
        assert editable.name == "./local_package"
        assert editable.meta.get("editable") is True

    def test_pass_id(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", "flask\n")
        result = analyze_requirements(tmp_path)
        req = next((s for s in result.symbols if s.kind == "requirement"), None)
        assert req is not None
        assert req.origin == "requirements.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", "flask")
        result = analyze_requirements(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "requirements.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_requirements(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", "flask>=2.0")
        result = analyze_requirements(tmp_path)
        req = next((s for s in result.symbols if s.kind == "requirement"), None)
        assert req is not None
        assert req.id == req.stable_id
        assert "requirements:" in req.id
        assert "requirements.txt" in req.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", "flask>=2.0")
        result = analyze_requirements(tmp_path)
        req = next((s for s in result.symbols if s.kind == "requirement"), None)
        assert req is not None
        assert req.span is not None
        assert req.span.start_line >= 1

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", "flask\n")
        make_requirements_file(tmp_path, "requirements-dev.txt", "pytest\n")
        result = analyze_requirements(tmp_path)
        reqs = [s for s in result.symbols if s.kind == "requirement"]
        assert len(reqs) == 2
        names = {r.name for r in reqs}
        assert "flask" in names
        assert "pytest" in names

    def test_run_files_analyzed(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", "flask")
        make_requirements_file(tmp_path, "requirements-dev.txt", "pytest")
        make_requirements_file(tmp_path, "requirements-test.txt", "coverage")
        result = analyze_requirements(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 3

    def test_long_url_truncation(self, tmp_path: Path) -> None:
        long_url = "git+https://github.com/user/" + "x" * 100 + ".git"
        make_requirements_file(tmp_path, "requirements.txt", long_url)
        result = analyze_requirements(tmp_path)
        url_req = next((s for s in result.symbols if s.kind == "url_requirement"), None)
        assert url_req is not None
        assert len(url_req.signature) < len(long_url)
        assert "..." in url_req.signature

    def test_complete_requirements_file(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", """
# Core dependencies
flask>=2.0.0
requests==2.28.0
numpy>=1.20,<2.0

# Optional extras
django[argon2]>=4.0

# From git
git+https://github.com/user/repo.git#egg=mypackage

# Include other files
-r base.txt

# Editable install
-e ./local_package
""")
        result = analyze_requirements(tmp_path)

        # Check requirements
        reqs = [s for s in result.symbols if s.kind == "requirement"]
        assert len(reqs) >= 4

        # Check URL requirement
        url_reqs = [s for s in result.symbols if s.kind == "url_requirement"]
        assert len(url_reqs) == 1

        # Check editable
        editables = [s for s in result.symbols if s.kind == "editable"]
        assert len(editables) == 1

        # Check edges
        dep_edges = [e for e in result.edges if e.edge_type == "depends"]
        assert len(dep_edges) >= 4

        include_edges = [e for e in result.edges if e.edge_type == "includes"]
        assert len(include_edges) == 1

    def test_handles_comments(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", """
# This is a comment
flask  # inline comment
requests
""")
        result = analyze_requirements(tmp_path)
        reqs = [s for s in result.symbols if s.kind == "requirement"]
        assert len(reqs) == 2
        names = {r.name for r in reqs}
        assert "flask" in names
        assert "requests" in names

    def test_vcs_url_creates_depends_edge(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", """
git+https://github.com/user/repo.git#egg=mypackage
""")
        result = analyze_requirements(tmp_path)
        dep_edges = [e for e in result.edges if e.edge_type == "depends"]
        assert len(dep_edges) == 1
        assert "vcs:package:mypackage" in dep_edges[0].dst
        assert dep_edges[0].confidence == 0.9

    def test_mercurial_url_requirement(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", """
hg+https://bitbucket.org/user/repo#egg=mypackage
""")
        result = analyze_requirements(tmp_path)
        url_req = next((s for s in result.symbols if s.kind == "url_requirement"), None)
        assert url_req is not None
        assert url_req.meta.get("source_type") == "mercurial"

    def test_svn_url_requirement(self, tmp_path: Path) -> None:
        make_requirements_file(tmp_path, "requirements.txt", """
svn+https://svn.example.com/repo/trunk#egg=mypackage
""")
        result = analyze_requirements(tmp_path)
        url_req = next((s for s in result.symbols if s.kind == "url_requirement"), None)
        assert url_req is not None
        assert url_req.meta.get("source_type") == "svn"
