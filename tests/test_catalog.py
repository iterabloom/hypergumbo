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


class TestCatalogCompleteness:
    """Tests to verify catalog includes all analyzers."""

    def test_catalog_includes_all_language_analyzers(self) -> None:
        """Catalog includes passes for all languages in profile.py."""
        from hypergumbo.catalog import get_default_catalog

        catalog = get_default_catalog()
        pass_ids = {p.id for p in catalog.passes}

        # Map languages to their expected pass ID patterns
        # Some languages share analyzers (e.g., cpp uses c-ts-v1)
        language_to_pass_pattern = {
            "python": "python-ast-v1",
            "javascript": "javascript-ts-v1",
            "typescript": "javascript-ts-v1",  # shares with JS
            "vue": "javascript-ts-v1",  # shares with JS
            "html": "html-pattern-v1",
            "rust": "rust-ts-v1",
            "go": "go-ts-v1",
            "java": "java-ts-v1",
            "c": "c-ts-v1",
            "cpp": "cpp-ts-v1",
            "ruby": "ruby-ts-v1",
            "php": "php-ts-v1",
            "swift": "swift-ts-v1",
            "kotlin": "kotlin-ts-v1",
            "scala": "scala-ts-v1",
            "elixir": "elixir-ts-v1",
            "lua": "lua-ts-v1",
            "clojure": "clojure-ts-v1",
            "erlang": "erlang-ts-v1",
            "elm": "elm-ts-v1",
            "haskell": "haskell-ts-v1",
            "agda": "agda-v1",
            "lean": "lean-v1",
            "wolfram": "wolfram-v1",
            "ocaml": "ocaml-ts-v1",
            "solidity": "solidity-ts-v1",
            "csharp": "csharp-ts-v1",
            "fortran": "fortran-v1",
            "glsl": "glsl-v1",
            "nix": "nix-v1",
            "cuda": "cuda-v1",
            "cmake": "cmake-v1",
            "dockerfile": "dockerfile-v1",
            "sql": "sql-v1",
            "verilog": "verilog-v1",
            "vhdl": "vhdl-v1",
            "graphql": "graphql-v1",
            "zig": "zig-ts-v1",
            "groovy": "groovy-ts-v1",
            "julia": "julia-ts-v1",
            "objc": "objc-ts-v1",
            "hcl": "hcl-ts-v1",
            "dart": "dart-ts-v1",
            "cobol": "cobol-v1",
            "latex": "latex-v1",
            "fsharp": "fsharp-ts-v1",
            "perl": "perl-ts-v1",
            "proto": "proto-v1",
            "thrift": "thrift-v1",
            "capnp": "capnp-v1",
            "powershell": "powershell-v1",
            "gdscript": "gdscript-v1",
            "starlark": "starlark-v1",
            "fish": "fish-v1",
            "hlsl": "hlsl-v1",
            "ada": "ada-v1",
            "d": "d-v1",
            "nim": "nim-v1",
            "shell": "bash-v1",
            # These are config/data formats - optional
            "json": "json-config-v1",
            "yaml": "yaml-ansible-v1",
            "css": "css-v1",
            "toml": "toml-v1",
            # markdown is doc-only, no analyzer
        }

        # Check all mapped languages have their pass in catalog
        for lang, expected_pass in language_to_pass_pattern.items():
            assert expected_pass in pass_ids, f"Missing pass {expected_pass} for language {lang}"

    def test_catalog_has_at_least_60_passes(self) -> None:
        """Catalog should have at least 60 passes (sanity check)."""
        catalog = get_default_catalog()
        assert len(catalog.passes) >= 60, f"Only {len(catalog.passes)} passes in catalog"


class TestSuggestedPasses:
    """Tests for language-based pass suggestions."""

    def test_suggest_passes_for_python(self) -> None:
        """Suggests Python pass for Python language."""
        from hypergumbo.catalog import suggest_passes_for_languages

        suggested = suggest_passes_for_languages({"python"})
        assert any("python" in p.id for p in suggested)

    def test_suggest_passes_for_javascript(self) -> None:
        """Suggests JS pass for JavaScript language."""
        from hypergumbo.catalog import suggest_passes_for_languages

        suggested = suggest_passes_for_languages({"javascript"})
        assert any("javascript" in p.id for p in suggested)

    def test_suggest_passes_for_multi_language(self) -> None:
        """Suggests multiple passes for multiple languages."""
        from hypergumbo.catalog import suggest_passes_for_languages

        suggested = suggest_passes_for_languages({"python", "rust"})
        pass_ids = [p.id for p in suggested]
        assert any("python" in pid for pid in pass_ids)
        assert any("rust" in pid for pid in pass_ids)

    def test_suggest_passes_empty_languages(self) -> None:
        """Returns empty list for empty language set."""
        from hypergumbo.catalog import suggest_passes_for_languages

        suggested = suggest_passes_for_languages(set())
        assert suggested == []

    def test_suggest_passes_excludes_config_languages(self) -> None:
        """Config-only languages don't suggest passes."""
        from hypergumbo.catalog import suggest_passes_for_languages

        # JSON, YAML, and markdown are config/doc formats
        suggested = suggest_passes_for_languages({"json", "yaml", "markdown"})
        assert len(suggested) == 0

    def test_suggest_passes_filters_config_from_mixed(self) -> None:
        """Config languages filtered from mixed set."""
        from hypergumbo.catalog import suggest_passes_for_languages

        # Mix of code and config languages
        suggested = suggest_passes_for_languages({"python", "json", "yaml"})
        pass_ids = [p.id for p in suggested]

        # Python should be suggested
        assert any("python" in pid for pid in pass_ids)
        # But not JSON/YAML config analyzers
        assert not any("json" in pid for pid in pass_ids)

    def test_suggest_passes_for_dockerfile(self) -> None:
        """Suggests Dockerfile pass."""
        from hypergumbo.catalog import suggest_passes_for_languages

        suggested = suggest_passes_for_languages({"dockerfile"})
        assert any("dockerfile" in p.id for p in suggested)


class TestCatalogMethods:
    """Tests for Catalog methods."""

    def test_get_extra_passes(self) -> None:
        """Can filter to extra passes only."""
        catalog = Catalog(
            passes=[
                Pass("python-ast-v1", "Python AST", "core"),
                Pass("javascript-ts-v1", "JS/TS", "extra", "tree-sitter-language-pack"),
                Pass("rust-ts-v1", "Rust", "extra", "tree-sitter-language-pack"),
            ],
            packs=[],
        )
        extras = catalog.get_extra_passes()
        assert len(extras) == 2
        assert all(p.availability == "extra" for p in extras)
