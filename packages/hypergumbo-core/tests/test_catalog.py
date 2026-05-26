# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for catalog module and command."""
import pytest
from unittest.mock import patch

from hypergumbo_core.catalog import (
    Pass,
    Catalog,
    get_default_catalog,
    is_available,
    validate_pass_dependencies,
)


class TestPass:
    """Tests for Pass dataclass."""

    def test_pass_has_required_fields(self) -> None:
        """Pass has id, description, availability."""
        p = Pass(
            id="python",
            description="Python AST parser",
            availability="core",
        )
        assert p.id == "python"
        assert p.description == "Python AST parser"
        assert p.availability == "core"

    def test_pass_to_dict(self) -> None:
        """Pass serializes to dict."""
        p = Pass(
            id="python",
            description="Python AST parser",
            availability="core",
        )
        d = p.to_dict()
        assert d["id"] == "python"
        assert d["description"] == "Python AST parser"
        assert d["availability"] == "core"

    def test_extra_pass_has_requires_field(self) -> None:
        """Extra passes specify required dependency."""
        p = Pass(
            id="javascript",
            description="JS/TS via tree-sitter",
            availability="extra",
            requires="hypergumbo[javascript]",
        )
        assert p.requires == "hypergumbo[javascript]"

    def test_extra_pass_to_dict_includes_requires(self) -> None:
        """Extra pass to_dict includes requires field."""
        p = Pass(
            id="javascript",
            description="JS/TS via tree-sitter",
            availability="extra",
            requires="hypergumbo[javascript]",
        )
        d = p.to_dict()
        assert d["requires"] == "hypergumbo[javascript]"


class TestCatalog:
    """Tests for Catalog dataclass."""

    def test_catalog_has_passes(self) -> None:
        """Catalog contains passes."""
        catalog = Catalog(
            passes=[
                Pass("python", "Python AST parser", "core"),
            ],
        )
        assert len(catalog.passes) == 1

    def test_catalog_to_dict(self) -> None:
        """Catalog serializes to dict."""
        catalog = Catalog(
            passes=[Pass("python", "Python AST parser", "core")],
        )
        d = catalog.to_dict()
        assert "passes" in d

    def test_get_core_passes(self) -> None:
        """Can filter to core passes only."""
        catalog = Catalog(
            passes=[
                Pass("python", "Python AST", "core"),
                Pass("javascript", "JS/TS", "extra", "hypergumbo[javascript]"),
            ],
        )
        core = catalog.get_core_passes()
        assert len(core) == 1
        assert core[0].id == "python"

    def test_get_all_passes(self) -> None:
        """Can get all passes including extras."""
        catalog = Catalog(
            passes=[
                Pass("python", "Python AST", "core"),
                Pass("javascript", "JS/TS", "extra", "hypergumbo[javascript]"),
            ],
        )
        all_passes = catalog.passes
        assert len(all_passes) == 2


class TestDefaultCatalog:
    """Tests for default catalog."""

    def test_default_catalog_has_python_pass(self) -> None:
        """Default catalog includes Python AST pass."""
        catalog = get_default_catalog()
        ids = [p.id for p in catalog.passes]
        assert "python" in ids

    def test_default_catalog_has_html_pass(self) -> None:
        """Default catalog includes HTML pattern pass."""
        catalog = get_default_catalog()
        ids = [p.id for p in catalog.passes]
        assert "html" in ids

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
        p = Pass("python", "Python AST", "core")
        assert is_available(p) is True

    def test_extra_pass_not_available_without_dependency(self) -> None:
        """Extra passes unavailable if dependency missing."""
        p = Pass("javascript", "JS/TS", "extra", "hypergumbo[javascript]")
        # Mock tree_sitter as not installed
        with patch("importlib.util.find_spec", return_value=None):
            assert is_available(p) is False

    def test_extra_pass_unknown_dependency_not_available(self) -> None:
        """Extra passes with unknown dependencies are not available."""
        p = Pass("unknown", "Unknown analyzer", "extra", "hypergumbo[unknown]")
        # Unknown dependency type defaults to not available
        assert is_available(p) is False


class TestCatalogCompleteness:
    """Tests to verify catalog includes all analyzers."""

    def test_catalog_includes_all_language_analyzers(self) -> None:
        """Catalog includes passes for all languages in profile.py."""
        from hypergumbo_core.catalog import get_default_catalog

        catalog = get_default_catalog()
        pass_ids = {p.id for p in catalog.passes}

        # Map languages to their expected pass ID patterns
        # Some languages share analyzers (e.g., cpp uses c-ts-v1)
        language_to_pass_pattern = {
            "python": "python",
            "javascript": "javascript",
            "typescript": "javascript",  # shares with JS
            "vue": "javascript",  # shares with JS
            "html": "html",
            "rust": "rust",
            "go": "go",
            "java": "java",
            "c": "c",
            "cpp": "cpp",
            "ruby": "ruby",
            "php": "php",
            "swift": "swift",
            "kotlin": "kotlin",
            "scala": "scala",
            "elixir": "elixir",
            "lua": "lua",
            "clojure": "clojure",
            "erlang": "erlang",
            "elm": "elm",
            "haskell": "haskell",
            "agda": "agda",
            "lean": "lean",
            "wolfram": "wolfram",
            "ocaml": "ocaml",
            "solidity": "solidity",
            "csharp": "csharp",
            "fortran": "fortran",
            "glsl": "glsl",
            "nix": "nix",
            "cuda": "cuda",
            "cmake": "cmake",
            "dockerfile": "dockerfile",
            "sql": "sql",
            "verilog": "verilog",
            "vhdl": "vhdl",
            "graphql": "graphql",
            "zig": "zig",
            "groovy": "groovy",
            "julia": "julia",
            "objc": "objc",
            "hcl": "hcl",
            "dart": "dart",
            "cobol": "cobol",
            "latex": "latex",
            "fsharp": "fsharp",
            "perl": "perl",
            "proto": "proto",
            "thrift": "thrift",
            "capnp": "capnp",
            "powershell": "powershell",
            "gdscript": "gdscript",
            "starlark": "starlark",
            "fish": "fish",
            "hlsl": "hlsl",
            "ada": "ada",
            "d": "d",
            "nim": "nim",
            "shell": "bash",
            # These are config/data formats - optional
            "json": "json",
            "yaml": "yaml_ansible",
            "css": "css",
            "toml": "toml",
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
        from hypergumbo_core.catalog import suggest_passes_for_languages

        suggested = suggest_passes_for_languages({"python"})
        assert any("python" in p.id for p in suggested)

    def test_suggest_passes_for_javascript(self) -> None:
        """Suggests JS pass for JavaScript language."""
        from hypergumbo_core.catalog import suggest_passes_for_languages

        suggested = suggest_passes_for_languages({"javascript"})
        assert any("javascript" in p.id for p in suggested)

    def test_suggest_passes_for_multi_language(self) -> None:
        """Suggests multiple passes for multiple languages."""
        from hypergumbo_core.catalog import suggest_passes_for_languages

        suggested = suggest_passes_for_languages({"python", "rust"})
        pass_ids = [p.id for p in suggested]
        assert any("python" in pid for pid in pass_ids)
        assert any("rust" in pid for pid in pass_ids)

    def test_suggest_passes_empty_languages(self) -> None:
        """Returns empty list for empty language set."""
        from hypergumbo_core.catalog import suggest_passes_for_languages

        suggested = suggest_passes_for_languages(set())
        assert suggested == []

    def test_suggest_passes_excludes_config_languages(self) -> None:
        """Config-only languages don't suggest passes."""
        from hypergumbo_core.catalog import suggest_passes_for_languages

        # JSON, YAML, and markdown are config/doc formats
        suggested = suggest_passes_for_languages({"json", "yaml", "markdown"})
        assert len(suggested) == 0

    def test_suggest_passes_filters_config_from_mixed(self) -> None:
        """Config languages filtered from mixed set."""
        from hypergumbo_core.catalog import suggest_passes_for_languages

        # Mix of code and config languages
        suggested = suggest_passes_for_languages({"python", "json", "yaml"})
        pass_ids = [p.id for p in suggested]

        # Python should be suggested
        assert any("python" in pid for pid in pass_ids)
        # But not JSON/YAML config analyzers
        assert not any("json" in pid for pid in pass_ids)

    def test_suggest_passes_for_dockerfile(self) -> None:
        """Suggests Dockerfile pass."""
        from hypergumbo_core.catalog import suggest_passes_for_languages

        suggested = suggest_passes_for_languages({"dockerfile"})
        assert any("dockerfile" in p.id for p in suggested)


class TestCatalogMethods:
    """Tests for Catalog methods."""

    def test_get_extra_passes(self) -> None:
        """Can filter to extra passes only."""
        catalog = Catalog(
            passes=[
                Pass("python", "Python AST", "core"),
                Pass("javascript", "JS/TS", "extra", "tree-sitter-language-pack"),
                Pass("rust", "Rust", "extra", "tree-sitter-language-pack"),
            ],
        )
        extras = catalog.get_extra_passes()
        assert len(extras) == 2
        assert all(p.availability == "extra" for p in extras)


# ---------------------------------------------------------------------------
# WI-hupaz: Pass.depends_on substrate + validate_pass_dependencies()
# ---------------------------------------------------------------------------


class TestPassDependsOn:
    """Pass.depends_on: list of pass IDs this pass requires upstream (INV-hujog)."""

    def test_pass_has_depends_on_field_defaulting_to_empty_list(self) -> None:
        p = Pass(id="python", description="Python AST", availability="core")
        assert p.depends_on == []

    def test_pass_accepts_explicit_depends_on(self) -> None:
        p = Pass(
            id="jni-linker",
            description="JNI bridge",
            availability="core",
            depends_on=["java", "c"],
        )
        assert p.depends_on == ["java", "c"]

    def test_pass_depends_on_default_is_independent_per_instance(self) -> None:
        # field(default_factory=list) — not a shared mutable default
        p1 = Pass(id="a", description="", availability="core")
        p2 = Pass(id="b", description="", availability="core")
        p1.depends_on.append("x")
        assert p2.depends_on == []


class TestValidatePassDependencies:
    """validate_pass_dependencies(passes) raises when prerequisites unresolved."""

    def test_empty_pass_set_passes_silently(self) -> None:
        validate_pass_dependencies([])

    def test_pass_with_no_depends_on_passes_silently(self) -> None:
        validate_pass_dependencies([
            Pass(id="python", description="", availability="core"),
        ])

    def test_resolved_dependency_passes_silently(self) -> None:
        validate_pass_dependencies([
            Pass(id="python", description="", availability="core"),
            Pass(id="jni-linker", description="", availability="core",
                 depends_on=["python"]),
        ])

    def test_missing_dependency_raises_valueerror(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            validate_pass_dependencies([
                Pass(id="jni-linker", description="", availability="core",
                     depends_on=["java"]),
            ])
        msg = str(excinfo.value)
        assert "jni-linker" in msg
        assert "java" in msg

    def test_error_message_names_all_missing_dependencies(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            validate_pass_dependencies([
                Pass(id="jni-linker", description="", availability="core",
                     depends_on=["java", "c", "cpp"]),
                Pass(id="java", description="", availability="core"),
            ])
        msg = str(excinfo.value)
        assert "c" in msg
        assert "cpp" in msg
        assert "java" not in msg.split("requires")[1] if "requires" in msg else True

    def test_multiple_passes_with_missing_dependencies(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            validate_pass_dependencies([
                Pass(id="jni-linker", description="", availability="core",
                     depends_on=["java"]),
                Pass(id="cgo-linker", description="", availability="core",
                     depends_on=["go"]),
            ])
        msg = str(excinfo.value)
        # Both linkers + both missing prerequisites should be cited.
        assert "jni-linker" in msg
        assert "cgo-linker" in msg
        assert "java" in msg
        assert "go" in msg

    def test_self_dependency_resolves(self) -> None:
        # Pathological but well-defined: a pass declaring itself as a dependency
        # is trivially "active" — no error. (Topo-sort cycle detection is
        # a separate concern, deferred to scheduling-phase work.)
        validate_pass_dependencies([
            Pass(id="weird", description="", availability="core",
                 depends_on=["weird"]),
        ])


class TestCatalogStaticConsistency:
    """get_default_catalog() must pass validate_pass_dependencies()."""

    def test_default_catalog_depends_on_graph_is_internally_consistent(self) -> None:
        catalog = get_default_catalog()
        # Should not raise: every depends_on entry in the default catalog
        # resolves to a registered pass.
        validate_pass_dependencies(catalog.passes)


class TestBridgeLinkerDependsOnPopulated:
    """Language-pair Bridge linkers declare depends_on (the bridged languages).

    Per ADR-0003-ext, Bridge linkers are unambiguous: their dependency set
    is exactly the language analyzers they bridge. These seven serve as the
    canonical pattern for the follow-up per-linker audit WI.
    """

    def _pass_by_id(self, pass_id: str) -> Pass:
        # Linker modules register themselves via @register_linker side-effects
        # at import time. The default catalog only finds them once they're
        # imported (eagerly done in cli.py at runtime); explicitly import the
        # seven Bridge modules here so tests run standalone.
        import hypergumbo_core.linkers.jni
        import hypergumbo_core.linkers.cgo
        import hypergumbo_core.linkers.napi
        import hypergumbo_core.linkers.tauri_ipc
        import hypergumbo_core.linkers.wasm_bindgen
        import hypergumbo_core.linkers.pyffi
        import hypergumbo_core.linkers.lua_ffi
        catalog = get_default_catalog()
        match = next((p for p in catalog.passes if p.id == pass_id), None)
        assert match is not None, f"Pass {pass_id!r} not in default catalog"
        return match

    def test_jni_linker_depends_on_java_c_cpp_rust(self) -> None:
        p = self._pass_by_id("jni-linker")
        assert set(p.depends_on) == {"java", "c", "cpp", "rust"}

    def test_cgo_linker_depends_on_go_c_cpp(self) -> None:
        p = self._pass_by_id("cgo-linker")
        assert set(p.depends_on) == {"go", "c", "cpp"}

    def test_napi_linker_depends_on_javascript_c_cpp(self) -> None:
        # JS analyzer also handles TypeScript (single 'javascript' pass id).
        p = self._pass_by_id("napi-linker")
        assert set(p.depends_on) == {"javascript", "c", "cpp"}

    def test_tauri_ipc_linker_depends_on_javascript_rust(self) -> None:
        p = self._pass_by_id("tauri-ipc-linker")
        assert set(p.depends_on) == {"javascript", "rust"}

    def test_wasm_bindgen_linker_depends_on_javascript_rust(self) -> None:
        p = self._pass_by_id("wasm-bindgen-linker")
        assert set(p.depends_on) == {"javascript", "rust"}

    def test_pyffi_linker_depends_on_python_c_cpp_rust(self) -> None:
        p = self._pass_by_id("pyffi-linker")
        assert set(p.depends_on) == {"python", "c", "cpp", "rust"}

    def test_lua_ffi_linker_depends_on_lua_c_cpp(self) -> None:
        p = self._pass_by_id("lua-ffi-linker")
        assert set(p.depends_on) == {"lua", "c", "cpp"}
