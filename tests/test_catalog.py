"""Tests for catalog module and command."""
from unittest.mock import patch

from hypergumbo.catalog import (
    Pass,
    Pack,
    Catalog,
    get_default_catalog,
    is_available,
)


class TestPass:
    """Tests for Pass dataclass."""

    def test_pass_has_required_fields(self) -> None:
        """Pass has id, description, availability."""
        p = Pass(
            id="python-ast-v1",
            description="Python AST parser",
            availability="core",
        )
        assert p.id == "python-ast-v1"
        assert p.description == "Python AST parser"
        assert p.availability == "core"

    def test_pass_to_dict(self) -> None:
        """Pass serializes to dict."""
        p = Pass(
            id="python-ast-v1",
            description="Python AST parser",
            availability="core",
        )
        d = p.to_dict()
        assert d["id"] == "python-ast-v1"
        assert d["description"] == "Python AST parser"
        assert d["availability"] == "core"

    def test_extra_pass_has_requires_field(self) -> None:
        """Extra passes specify required dependency."""
        p = Pass(
            id="javascript-ts-v1",
            description="JS/TS via tree-sitter",
            availability="extra",
            requires="hypergumbo[javascript]",
        )
        assert p.requires == "hypergumbo[javascript]"

    def test_extra_pass_to_dict_includes_requires(self) -> None:
        """Extra pass to_dict includes requires field."""
        p = Pass(
            id="javascript-ts-v1",
            description="JS/TS via tree-sitter",
            availability="extra",
            requires="hypergumbo[javascript]",
        )
        d = p.to_dict()
        assert d["requires"] == "hypergumbo[javascript]"


class TestPack:
    """Tests for Pack dataclass."""

    def test_pack_has_required_fields(self) -> None:
        """Pack has id, description, passes list."""
        pack = Pack(
            id="python-fastapi",
            description="FastAPI route detection + call graph",
            passes=["python-ast-v1"],
        )
        assert pack.id == "python-fastapi"
        assert pack.description == "FastAPI route detection + call graph"
        assert "python-ast-v1" in pack.passes

    def test_pack_to_dict(self) -> None:
        """Pack serializes to dict."""
        pack = Pack(
            id="python-fastapi",
            description="FastAPI route detection + call graph",
            passes=["python-ast-v1"],
        )
        d = pack.to_dict()
        assert d["id"] == "python-fastapi"
        assert d["passes"] == ["python-ast-v1"]


class TestCatalog:
    """Tests for Catalog dataclass."""

    def test_catalog_has_passes_and_packs(self) -> None:
        """Catalog contains passes and packs."""
        catalog = Catalog(
            passes=[
                Pass("python-ast-v1", "Python AST parser", "core"),
            ],
            packs=[
                Pack("python-fastapi", "FastAPI detection", ["python-ast-v1"]),
            ],
        )
        assert len(catalog.passes) == 1
        assert len(catalog.packs) == 1

    def test_catalog_to_dict(self) -> None:
        """Catalog serializes to dict."""
        catalog = Catalog(
            passes=[Pass("python-ast-v1", "Python AST parser", "core")],
            packs=[],
        )
        d = catalog.to_dict()
        assert "passes" in d
        assert "packs" in d

    def test_get_core_passes(self) -> None:
        """Can filter to core passes only."""
        catalog = Catalog(
            passes=[
                Pass("python-ast-v1", "Python AST", "core"),
                Pass("javascript-ts-v1", "JS/TS", "extra", "hypergumbo[javascript]"),
            ],
            packs=[],
        )
        core = catalog.get_core_passes()
        assert len(core) == 1
        assert core[0].id == "python-ast-v1"

    def test_get_all_passes(self) -> None:
        """Can get all passes including extras."""
        catalog = Catalog(
            passes=[
                Pass("python-ast-v1", "Python AST", "core"),
                Pass("javascript-ts-v1", "JS/TS", "extra", "hypergumbo[javascript]"),
            ],
            packs=[],
        )
        all_passes = catalog.passes
        assert len(all_passes) == 2


class TestDefaultCatalog:
    """Tests for default catalog."""

    def test_default_catalog_has_python_pass(self) -> None:
        """Default catalog includes Python AST pass."""
        catalog = get_default_catalog()
        ids = [p.id for p in catalog.passes]
        assert "python-ast-v1" in ids

    def test_default_catalog_has_html_pass(self) -> None:
        """Default catalog includes HTML pattern pass."""
        catalog = get_default_catalog()
        ids = [p.id for p in catalog.passes]
        assert "html-pattern-v1" in ids

    def test_default_catalog_has_javascript_extra(self) -> None:
        """Default catalog includes JS/TS as extra."""
        catalog = get_default_catalog()
        js_pass = next((p for p in catalog.passes if "javascript" in p.id), None)
        assert js_pass is not None
        assert js_pass.availability == "extra"


class TestIsAvailable:
    """Tests for availability checking."""

    def test_core_passes_always_available(self) -> None:
        """Core passes are always available."""
        p = Pass("python-ast-v1", "Python AST", "core")
        assert is_available(p) is True

    def test_extra_pass_not_available_without_dependency(self) -> None:
        """Extra passes unavailable if dependency missing."""
        p = Pass("javascript-ts-v1", "JS/TS", "extra", "hypergumbo[javascript]")
        # Mock tree_sitter as not installed
        with patch("importlib.util.find_spec", return_value=None):
            assert is_available(p) is False

    def test_extra_pass_unknown_dependency_not_available(self) -> None:
        """Extra passes with unknown dependencies are not available."""
        p = Pass("unknown-v1", "Unknown analyzer", "extra", "hypergumbo[unknown]")
        # Unknown dependency type defaults to not available
        assert is_available(p) is False
