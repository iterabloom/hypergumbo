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
    validate_pass_name_resolution,
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
# WI-dilab: Pass.depends_on CNF substrate + validators
# (Migrated from WI-hupaz's flat-list shape; same intent, richer semantics.)
# ---------------------------------------------------------------------------


class TestPassDependsOn:
    """Pass.depends_on: CNF (outer-AND of inner-OR clauses). Empty list = no deps."""

    def test_pass_has_depends_on_field_defaulting_to_empty_list(self) -> None:
        p = Pass(id="python", description="Python AST", availability="core")
        assert p.depends_on == []

    def test_pass_accepts_explicit_depends_on_cnf(self) -> None:
        # JNI's real requirement: java AND (c OR cpp OR rust).
        p = Pass(
            id="jni-linker",
            description="JNI bridge",
            availability="core",
            depends_on=[["java"], ["c", "cpp", "rust"]],
        )
        assert p.depends_on == [["java"], ["c", "cpp", "rust"]]

    def test_pass_depends_on_default_is_independent_per_instance(self) -> None:
        # field(default_factory=list) — not a shared mutable default
        p1 = Pass(id="a", description="", availability="core")
        p2 = Pass(id="b", description="", availability="core")
        p1.depends_on.append(["x"])
        assert p2.depends_on == []


class TestValidatePassNameResolution:
    """Static check: every literal in every depends_on clause names a known pass."""

    def test_empty_pass_set_passes_silently(self) -> None:
        validate_pass_name_resolution([])

    def test_pass_with_no_depends_on_passes_silently(self) -> None:
        validate_pass_name_resolution([
            Pass(id="python", description="", availability="core"),
        ])

    def test_resolved_literals_pass_silently(self) -> None:
        # Every literal resolves — no error.
        validate_pass_name_resolution([
            Pass(id="python", description="", availability="core"),
            Pass(id="javascript", description="", availability="core"),
            Pass(id="some-linker", description="", availability="core",
                 depends_on=[["python", "javascript"]]),
        ])

    def test_unknown_literal_raises_valueerror(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            validate_pass_name_resolution([
                Pass(id="jni-linker", description="", availability="core",
                     depends_on=[["jaba"]]),  # typo
            ])
        msg = str(excinfo.value)
        assert "jni-linker" in msg
        assert "jaba" in msg

    def test_error_message_lists_all_unknown_literals_across_clauses(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            validate_pass_name_resolution([
                Pass(id="jni-linker", description="", availability="core",
                     depends_on=[["jaba"], ["see", "ceepeepee", "rust"]]),
                Pass(id="rust", description="", availability="core"),
            ])
        msg = str(excinfo.value)
        # All three unknown literals should be cited.
        assert "jaba" in msg
        assert "see" in msg
        assert "ceepeepee" in msg
        # The known literal must NOT be flagged.
        assert "depends_on names unknown passes: ['jaba', 'see', 'ceepeepee']" in msg


class TestValidatePassDependencies:
    """Runtime CNF check: every AND-conjunct contains at least one active literal."""

    def test_empty_pass_set_passes_silently(self) -> None:
        validate_pass_dependencies([])

    def test_pass_with_no_depends_on_passes_silently(self) -> None:
        validate_pass_dependencies([
            Pass(id="python", description="", availability="core"),
        ])

    def test_single_clause_single_literal_active(self) -> None:
        validate_pass_dependencies([
            Pass(id="python", description="", availability="core"),
            Pass(id="airflow-linker", description="", availability="core",
                 depends_on=[["python"]]),
        ])

    def test_single_clause_or_satisfied_by_any_member(self) -> None:
        # http-linker: [["python", "javascript", "java"]] — any one of these
        # being active satisfies the (single) AND-conjunct.
        validate_pass_dependencies([
            Pass(id="python", description="", availability="core"),
            # javascript and java NOT active — but python is.
            Pass(id="http-linker", description="", availability="core",
                 depends_on=[["python", "javascript", "java"]]),
        ])

    def test_single_clause_or_no_members_active_raises(self) -> None:
        # http-linker needs at least one of [python, javascript, java].
        # If none are active, the conjunct is unsatisfied.
        with pytest.raises(ValueError) as excinfo:
            validate_pass_dependencies([
                # Only ruby is active.
                Pass(id="ruby", description="", availability="core"),
                Pass(id="http-linker", description="", availability="core",
                     depends_on=[["python", "javascript", "java"]]),
            ])
        msg = str(excinfo.value)
        assert "http-linker" in msg
        assert "python" in msg
        assert "javascript" in msg
        assert "java" in msg

    def test_multi_clause_all_satisfied(self) -> None:
        # JNI's actual shape: java AND (c OR cpp OR rust).
        validate_pass_dependencies([
            Pass(id="java", description="", availability="core"),
            Pass(id="c", description="", availability="core"),
            # No cpp, no rust — but the (c OR cpp OR rust) clause is satisfied by c.
            Pass(id="jni-linker", description="", availability="core",
                 depends_on=[["java"], ["c", "cpp", "rust"]]),
        ])

    def test_multi_clause_first_unsatisfied_raises(self) -> None:
        # JNI: java AND (c OR cpp OR rust). Missing java.
        with pytest.raises(ValueError) as excinfo:
            validate_pass_dependencies([
                Pass(id="c", description="", availability="core"),
                Pass(id="jni-linker", description="", availability="core",
                     depends_on=[["java"], ["c", "cpp", "rust"]]),
            ])
        msg = str(excinfo.value)
        assert "jni-linker" in msg
        # The unsatisfied "java"-only clause should appear.
        assert "['java']" in msg

    def test_multi_clause_second_unsatisfied_raises(self) -> None:
        # JNI: java AND (c OR cpp OR rust). java present, but no impl lang.
        with pytest.raises(ValueError) as excinfo:
            validate_pass_dependencies([
                Pass(id="java", description="", availability="core"),
                Pass(id="jni-linker", description="", availability="core",
                     depends_on=[["java"], ["c", "cpp", "rust"]]),
            ])
        msg = str(excinfo.value)
        assert "jni-linker" in msg
        # The unsatisfied OR-of-impls clause should appear.
        assert "['c', 'cpp', 'rust']" in msg

    def test_both_clauses_unsatisfied_both_reported(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            validate_pass_dependencies([
                # Neither java nor any impl lang active.
                Pass(id="ruby", description="", availability="core"),
                Pass(id="jni-linker", description="", availability="core",
                     depends_on=[["java"], ["c", "cpp", "rust"]]),
            ])
        msg = str(excinfo.value)
        assert "['java']" in msg
        assert "['c', 'cpp', 'rust']" in msg

    def test_multiple_passes_reported_together(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            validate_pass_dependencies([
                Pass(id="jni-linker", description="", availability="core",
                     depends_on=[["java"], ["c", "cpp", "rust"]]),
                Pass(id="cgo-linker", description="", availability="core",
                     depends_on=[["go"], ["c", "cpp"]]),
            ])
        msg = str(excinfo.value)
        # Both unsatisfied passes cited.
        assert "jni-linker" in msg
        assert "cgo-linker" in msg

    def test_self_dependency_resolves(self) -> None:
        # Pathological: a pass listing itself in some clause.
        validate_pass_dependencies([
            Pass(id="weird", description="", availability="core",
                 depends_on=[["weird"]]),
        ])


class TestCatalogStaticConsistency:
    """get_default_catalog() must pass validate_pass_name_resolution()."""

    def test_default_catalog_depends_on_names_all_resolve(self) -> None:
        catalog = get_default_catalog()
        # Should not raise: every literal in every clause resolves to a
        # registered pass.
        validate_pass_name_resolution(catalog.passes)


class TestBridgeLinkerDependsOnPopulated:
    """Language-pair Bridge linkers declare CNF: anchor AND any-of-impls.

    Per ADR-3bbb, Bridge linkers are unambiguous: their dependency set
    has one anchor language and a set of impl languages. CNF expresses this
    as ``[[anchor], [impl1, impl2, ...]]``.
    """

    def _pass_by_id(self, pass_id: str) -> Pass:
        # Linker modules register themselves via @register_linker side-effects
        # at import time. The default catalog only finds them once they're
        # imported (eagerly done in cli.py at runtime); explicitly import the
        # bridge modules here so tests run standalone.
        import hypergumbo_core.linkers.jni
        import hypergumbo_core.linkers.cgo
        import hypergumbo_core.linkers.napi
        import hypergumbo_core.linkers.tauri_ipc
        import hypergumbo_core.linkers.wasm_bindgen
        import hypergumbo_core.linkers.pyffi
        import hypergumbo_core.linkers.lua_ffi
        import hypergumbo_core.linkers.ruby_ffi
        import hypergumbo_core.linkers.solidity_abi
        import hypergumbo_core.linkers.swift_objc
        catalog = get_default_catalog()
        match = next((p for p in catalog.passes if p.id == pass_id), None)
        assert match is not None, f"Pass {pass_id!r} not in default catalog"
        return match

    def test_jni_linker_cnf(self) -> None:
        p = self._pass_by_id("jni-linker")
        assert p.depends_on == [["java"], ["c", "cpp", "rust"]]

    def test_cgo_linker_cnf(self) -> None:
        p = self._pass_by_id("cgo-linker")
        assert p.depends_on == [["go"], ["c", "cpp"]]

    def test_napi_linker_cnf(self) -> None:
        # JS analyzer also handles TypeScript (single 'javascript' pass id).
        p = self._pass_by_id("napi-linker")
        assert p.depends_on == [["javascript"], ["c", "cpp"]]

    def test_tauri_ipc_linker_cnf(self) -> None:
        p = self._pass_by_id("tauri-ipc-linker")
        assert p.depends_on == [["javascript"], ["rust"]]

    def test_wasm_bindgen_linker_cnf(self) -> None:
        p = self._pass_by_id("wasm-bindgen-linker")
        assert p.depends_on == [["javascript"], ["rust"]]

    def test_pyffi_linker_cnf(self) -> None:
        p = self._pass_by_id("pyffi-linker")
        assert p.depends_on == [["python"], ["c", "cpp", "rust"]]

    def test_lua_ffi_linker_cnf(self) -> None:
        p = self._pass_by_id("lua-ffi-linker")
        assert p.depends_on == [["lua"], ["c", "cpp"]]

    def test_ruby_ffi_linker_cnf(self) -> None:
        p = self._pass_by_id("ruby-ffi-linker")
        assert p.depends_on == [["ruby"], ["c", "cpp"]]

    def test_solidity_abi_linker_cnf(self) -> None:
        p = self._pass_by_id("solidity-abi-linker")
        assert p.depends_on == [["javascript"], ["solidity"]]

    def test_swift_objc_linker_cnf(self) -> None:
        p = self._pass_by_id("swift-objc-linker")
        assert p.depends_on == [["swift"], ["objc"]]


class TestINVHujogClosureCriterion:
    """Every Bridge/Framework/Protocol linker has non-empty depends_on.

    Infrastructure linkers may have empty depends_on (declared explicitly, with
    a docstring note explaining why). Per the WI-dilab closure criterion.
    """

    def _all_linkers_with_subcategory(self) -> dict[str, str]:
        """Returns {pass_id: subcategory} by parsing linker module docstrings."""
        # Importing cli triggers eager linker module imports.
        import hypergumbo_core.cli
        from hypergumbo_core.linkers.registry import _LINKER_REGISTRY
        import importlib
        result: dict[str, str] = {}
        for name, reg in _LINKER_REGISTRY.items():
            module = importlib.import_module(reg.func.__module__)
            doc = module.__doc__ or ""
            first_line = doc.lstrip().split("\n", 1)[0]
            # Convention: "<Subcategory> linker: <one-line purpose>."
            for cat in ("Bridge", "Framework", "Protocol", "Infrastructure"):
                if first_line.startswith(f"{cat} linker"):
                    result[name] = cat
                    break
        return result

    def test_every_bridge_framework_protocol_linker_has_non_empty_depends_on(self) -> None:
        catalog = get_default_catalog()
        subcategories = self._all_linkers_with_subcategory()
        passes_by_id = {p.id: p for p in catalog.passes}
        offenders: list[str] = []
        for name, cat in subcategories.items():
            if cat in ("Bridge", "Framework", "Protocol"):
                p = passes_by_id.get(name)
                if p is None:
                    continue
                if not p.depends_on or all(not clause for clause in p.depends_on):
                    offenders.append(f"{name} ({cat})")
        assert not offenders, (
            f"Linkers in Bridge/Framework/Protocol categories must declare "
            f"non-empty depends_on (per WI-dilab closure criterion). Offenders: "
            f"{offenders}"
        )

    def test_infrastructure_linkers_have_explicit_depends_on(self) -> None:
        # Empty list is OK for Infrastructure; the requirement is that the
        # field is explicitly declared (in the @register_linker call) rather
        # than left to default. We can't introspect "was the kwarg passed",
        # but we CAN assert the field reads as a list (vs missing/None).
        catalog = get_default_catalog()
        subcategories = self._all_linkers_with_subcategory()
        passes_by_id = {p.id: p for p in catalog.passes}
        for name, cat in subcategories.items():
            if cat == "Infrastructure":
                p = passes_by_id.get(name)
                if p is None:
                    continue
                assert isinstance(p.depends_on, list)
