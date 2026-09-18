# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for catalog module and command."""
import pytest
from unittest.mock import patch

from hypergumbo_core.catalog import (
    Pass,
    Catalog,
    emit_falsified_dependency_summary,
    find_falsified_dependencies,
    format_falsified_dependencies,
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
    """get_default_catalog() must pass validate_pass_name_resolution().

    WI-jijor: this assertion was a CONTROL THAT COULD NOT FAIL. Linker modules
    register via ``@register_linker`` import side-effects, and this class sits
    ABOVE the one class in this file that imports them — so run in isolation
    (or first in file order) the catalog held 118 analyzers, **zero** of which
    declare ``depends_on``, and the validator passed over 0 conjuncts. Whether
    it validated 73 clauses or nothing at all depended on which other test
    module had happened to import ``hypergumbo_core.cli`` first.

    Fixed by importing the linkers here and PINNING THE AIM, not just the
    volume: a floor alone catches an EMPTY instrument but not a MIS-AIMED one
    (LIVE.md §1.4), so the test also names a specific declaration it must have
    actually seen.
    """

    def _full_catalog(self) -> Catalog:
        # The registry is populated by import side-effect; cli.py carries the
        # explicit import block that the runtime relies on.
        import hypergumbo_core.cli
        return get_default_catalog()

    def test_default_catalog_depends_on_names_all_resolve(self) -> None:
        catalog = self._full_catalog()
        # Should not raise: every literal in every clause resolves to a
        # registered pass.
        validate_pass_name_resolution(catalog.passes)

    def test_the_validated_set_is_not_empty(self) -> None:
        """The control's own control: prove there was something to validate."""
        catalog = self._full_catalog()
        conjuncts = sum(len(p.depends_on) for p in catalog.passes)
        assert conjuncts >= 50, (
            f"only {conjuncts} depends_on conjuncts visible — the linker "
            "registry is unpopulated and the name-resolution assertion above "
            "is passing vacuously"
        )

    def test_a_named_declaration_is_actually_present(self) -> None:
        """Pins the AIM: a floor can be met by the wrong population."""
        catalog = self._full_catalog()
        by_id = {p.id: p for p in catalog.passes}
        assert by_id["tauri-ipc-linker"].depends_on == [["javascript"], ["rust", "rust_analyzer"]]

    def test_every_depends_on_declarer_is_a_linker(self) -> None:
        """The premise ``find_falsified_dependencies`` keys falsification on.

        A pass proves its own declaration wrong by emitting EDGES, because a
        linker's product is edges (ADR-3bbb: Tier-2 edge recovery) and the
        ``depends_on`` field is documented as what a linker needs to "produce
        its intended edges at all". That reading is only safe while every
        declarer is a linker. If an ANALYZER ever declares ``depends_on``, its
        product is nodes and the edge-keyed detector would silently stop
        auditing it — under-reporting, never lying, but stopping. This test is
        the trigger to revisit the rule, not a prohibition on the declaration.
        """
        catalog = self._full_catalog()
        from hypergumbo_core.linkers.registry import _LINKER_REGISTRY

        declarers = {p.id for p in catalog.passes if p.depends_on}
        assert declarers, "no declarations visible — registry unpopulated"
        non_linkers = sorted(declarers - set(_LINKER_REGISTRY))
        assert non_linkers == [], (
            f"{non_linkers} declare depends_on but are not linkers; "
            "find_falsified_dependencies keys falsification on edges_emitted "
            "and will not audit a node-producing pass"
        )


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
        assert p.depends_on == [["java"], ["c", "cpp", "rust", "rust_analyzer"]]  # WI-juzig

    def test_cgo_linker_cnf(self) -> None:
        p = self._pass_by_id("cgo-linker")
        assert p.depends_on == [["go"], ["c", "cpp"]]

    def test_napi_linker_cnf(self) -> None:
        # JS analyzer also handles TypeScript (single 'javascript' pass id).
        p = self._pass_by_id("napi-linker")
        assert p.depends_on == [["javascript"], ["c", "cpp"]]

    def test_tauri_ipc_linker_cnf(self) -> None:
        p = self._pass_by_id("tauri-ipc-linker")
        assert p.depends_on == [["javascript"], ["rust", "rust_analyzer"]]  # WI-juzig

    def test_wasm_bindgen_linker_cnf(self) -> None:
        p = self._pass_by_id("wasm-bindgen-linker")
        assert p.depends_on == [["javascript"], ["rust", "rust_analyzer"]]  # WI-juzig

    def test_pyffi_linker_cnf(self) -> None:
        p = self._pass_by_id("pyffi-linker")
        assert p.depends_on == [["python"], ["c", "cpp", "rust", "rust_analyzer"]]  # WI-juzig

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


class TestSyntheticPassIds:
    """ADR-0044: synthesis/import values in Symbol.origin are legitimate
    synthetic pass IDs, not a separate synthesis-mechanism axis."""

    def test_synthetic_pass_ids_set(self) -> None:
        from hypergumbo_core.catalog import _SYNTHETIC_PASS_IDS
        assert _SYNTHETIC_PASS_IDS == frozenset({
            "orchestrator_file_symbol_synthesis",
            "boundary_external_symbol_synthesis",
            "scip",
        })
        # The stale 'inheritance' phantom was dropped (zero producers).
        assert "inheritance" not in _SYNTHETIC_PASS_IDS

    def test_synthetic_pass_ids_are_known_pass_ids(self) -> None:
        from hypergumbo_core.catalog import (
            _SYNTHETIC_PASS_IDS,
            all_known_pass_ids,
        )
        known = all_known_pass_ids()
        assert _SYNTHETIC_PASS_IDS <= known
        assert "inheritance" not in known  # no producer → not a known pass id

    def test_old_synthesis_mechanisms_name_is_gone(self) -> None:
        import hypergumbo_core.catalog as cat
        assert not hasattr(cat, "_SYNTHESIS_MECHANISMS")

    def test_known_pass_ids_include_divergent_linker_emitted_pass_id(self) -> None:
        """WI-gobip: a linker's EMITTED pass_id (its module-level ``PASS_ID``)
        can diverge from its registration name — the view_template family
        registers under ``view_template`` / ``view_template_phoenix`` / … but all
        EMIT ``view-template-linker`` via ``_view_template_core.PASS_ID``. The
        pass-id axis validates the EMITTED value (``Symbol.origin`` /
        ``Edge.origin`` / ``AnalysisRun.pass_id``), so ``all_known_pass_ids()``
        must include it, or every view-template edge/symbol trips
        axis_conformance. Non-divergent linkers add nothing new (``make_pass_id``
        is identity, so their emitted pass_id equals their registration name)."""
        import hypergumbo_core.cli  # registers linkers (import side effect)
        from hypergumbo_core.catalog import all_known_pass_ids

        known = all_known_pass_ids()
        assert "view-template-linker" in known

    def test_known_languages_include_taxonomy_languages(self) -> None:
        """WI-kunut: the language axis catalog is analyzer/linker languages UNION
        the taxonomy's recognized LanguageSpecs. Discovery and the orchestrator
        file-anchor synthesis label file nodes with taxonomy languages (a
        ``.adoc`` → ``asciidoc``, a ``Makefile`` → ``makefile``) that have a
        LanguageSpec but no dedicated ``@register_analyzer`` — so they were
        absent from the registration-only catalog and every such file-anchor
        tripped axis_conformance. ``all_known_languages()`` now unions the
        taxonomy names, so the catalog is the complete recognized-language set."""
        from hypergumbo_core.catalog import all_known_languages

        known = all_known_languages()
        assert "asciidoc" in known
        assert "makefile" in known

    def test_known_languages_include_rails_view_templates(self) -> None:
        """WI-novob: erb/haml/slim are Rails view-template languages that the
        view-template linker stamps as ``Symbol.language`` (``.html.erb`` →
        ``erb``), but they had no ``@register_analyzer`` and no LanguageSpec,
        so every such file-anchor tripped axis_conformance (19 on chatwoot).
        Registered as taxonomy LanguageSpecs (human ruling: roles=CONFIG, like
        html), so the language-axis catalog now recognizes them."""
        from hypergumbo_core.catalog import all_known_languages

        known = all_known_languages()
        assert "erb" in known
        assert "haml" in known
        assert "slim" in known


class TestFindFalsifiedDependencies:
    """WI-jijor / ADR-0056 W1: the same CNF predicate, re-pointed.

    ``validate_pass_dependencies`` asks "may these passes run together?" and
    RAISES. This asks "did a pass that already ran prove its own declaration
    wrong?" and REPORTS. Falsification never suppresses anything, so it cannot
    lose an edge — which is why it may be wired where a gate may not
    (LIVE.md §2: raising is the withholding direction).
    """

    def _p(self, pass_id: str, depends_on: list[list[str]] | None = None) -> Pass:
        return Pass(id=pass_id, description="", availability="core",
                    depends_on=depends_on or [])

    def _run(self, pass_id: str, *, nodes: int = 0, edges: int = 0) -> dict:
        return {"pass": pass_id, "nodes_emitted": nodes, "edges_emitted": edges}

    def test_empty_inputs_report_nothing(self) -> None:
        assert find_falsified_dependencies([], []) == []

    def test_satisfied_declaration_is_not_falsified(self) -> None:
        passes = [self._p("rust"), self._p("tauri-ipc-linker", [["rust"]])]
        runs = [self._run("rust", nodes=40), self._run("tauri-ipc-linker", edges=3)]
        assert find_falsified_dependencies(passes, runs) == []

    def test_emitting_pass_with_unsatisfied_conjunct_is_falsified(self) -> None:
        """The headline case: it emitted, so the declaration is provably wrong."""
        passes = [self._p("tauri-ipc-linker", [["rust"]])]
        runs = [self._run("tauri-ipc-linker", edges=3)]
        assert find_falsified_dependencies(passes, runs) == [
            ("tauri-ipc-linker", [["rust"]]),
        ]

    def test_nodes_alone_do_not_falsify_a_declaration(self) -> None:
        """RE-POINTED by WI-rasal's corpus re-read, not by taste.

        The first cut counted ``nodes_emitted`` as proof, on the precedent that
        keying on ``edges_emitted`` alone once called a 91-node pass silent.
        That precedent is about SILENCE — "did this pass say anything?" — and
        importing it here answered a different question wrong.
        ``depends_on`` is documented as naming what a linker needs to "produce
        its intended edges at all", and all 59 declarers are linkers (pinned by
        ``test_every_depends_on_declarer_is_a_linker``).

        The measurement: 68 of the 261 falsifying surveys were
        ``database-query-linker`` runs with nodes and ZERO edges. It mints one
        query node per SQL string it scrapes out of a host file, then needs the
        ``sql`` analyzer's ``kind="table"`` symbols for the dst of every edge.
        Nodes-with-no-edges is precisely the shape of "could NOT do its job
        without the thing it declared" — the detector was reading a correct
        declaration as a falsified one, on 26% of its own hits.
        """
        passes = [self._p("db-linker", [["sql"]])]
        runs = [self._run("db-linker", nodes=15)]
        assert find_falsified_dependencies(passes, runs) == []

    def test_edges_falsify_even_when_no_nodes_were_minted(self) -> None:
        """The complement: the five surviving clauses all emit edges, no nodes."""
        passes = [self._p("inheritance-linker", [["java"]])]
        runs = [self._run("inheritance-linker", edges=30)]
        assert find_falsified_dependencies(passes, runs) == [
            ("inheritance-linker", [["java"]]),
        ]

    def test_silent_pass_with_unsatisfied_conjunct_is_NOT_falsified(self) -> None:
        """A pass that emitted nothing proves nothing about its declaration.

        This is the whole difference from the gate. The declaration may be
        perfectly correct and the repo may simply lack the construct; only
        OUTPUT despite an unsatisfied conjunct is proof.
        """
        passes = [self._p("tauri-ipc-linker", [["rust"]])]
        runs = [self._run("tauri-ipc-linker")]
        assert find_falsified_dependencies(passes, runs) == []

    def test_a_pass_that_ran_and_produced_NOTHING_does_not_satisfy_a_clause(
        self,
    ) -> None:
        """RE-POINTED by WI-rasal. The old rule was not a function of its fact.

        "Active" used to mean "has an ``analysis_runs`` row". Under the
        WI-jadig / INV-manov file-presence pre-filter, an analyzer is
        short-circuited into ``limits.skipped_passes`` only when EVERY language
        it declares is in the taxonomy's ``LANGUAGE_EXTENSIONS``; every other
        analyzer runs anyway and records a 0-file / 0-node row. So on caddy —
        a Go repository — ``apex``, ``astro`` and ``pony`` are "active" and
        ``java``, ``ruby`` and ``python`` are not, for the identical underlying
        fact that the repo has no such files. Membership in ``analysis_runs``
        answers "is this language in the taxonomy?", not "did this pass
        contribute anything?".

        Producing output is the same fact under both representations, so that
        is what a clause now tests.
        """
        passes = [self._p("rust"), self._p("tauri-ipc-linker", [["rust"]])]
        runs = [self._run("rust"), self._run("tauri-ipc-linker", edges=3)]
        assert find_falsified_dependencies(passes, runs) == [
            ("tauri-ipc-linker", [["rust"]]),
        ]

    def test_a_prerequisite_satisfies_a_clause_with_nodes_alone(self) -> None:
        """The asymmetry is deliberate: an ANALYZER's product is nodes.

        A pass falsifies its own declaration only by emitting edges (a linker's
        product); a pass SATISFIES someone else's clause with nodes or edges
        (an analyzer contributes symbols, a linker prerequisite contributes
        edges). Collapsing the two halves onto one counter breaks one of them.
        """
        passes = [self._p("rust"), self._p("tauri-ipc-linker", [["rust"]])]
        runs = [self._run("rust", nodes=40), self._run("tauri-ipc-linker", edges=3)]
        assert find_falsified_dependencies(passes, runs) == []

    def test_a_prerequisite_satisfies_a_clause_with_edges_alone(self) -> None:
        """``type-hierarchy-linker`` depends on ``inheritance-linker``, which
        emits edges and mints no nodes."""
        passes = [
            self._p("inheritance-linker"),
            self._p("type-hierarchy-linker", [["inheritance-linker"]]),
        ]
        runs = [
            self._run("inheritance-linker", edges=788),
            self._run("type-hierarchy-linker", edges=12),
        ]
        assert find_falsified_dependencies(passes, runs) == []

    def test_or_clause_satisfied_by_any_single_member(self) -> None:
        passes = [self._p("c"), self._p("jni-linker", [["java"], ["c", "cpp", "rust"]])]
        runs = [self._run("c", nodes=9), self._run("java", nodes=4),
                self._run("jni-linker", edges=1)]
        assert find_falsified_dependencies(passes, runs) == []

    def test_multi_conjunct_reports_only_the_unsatisfied_ones(self) -> None:
        passes = [self._p("java"), self._p("jni-linker", [["java"], ["c", "cpp"]])]
        runs = [self._run("java", nodes=7), self._run("jni-linker", edges=1)]
        assert find_falsified_dependencies(passes, runs) == [
            ("jni-linker", [["c", "cpp"]]),
        ]

    def test_pass_absent_from_runs_is_never_reported(self) -> None:
        """A pass that did not run cannot falsify its own declaration."""
        passes = [self._p("tauri-ipc-linker", [["rust"]])]
        assert find_falsified_dependencies(passes, []) == []

    def test_run_with_no_catalog_entry_is_skipped(self) -> None:
        """Synthetic pipeline passes (enclosure-linker, route-materializer)
        emit real AnalysisRuns but are not registry entries, so they carry no
        declaration to falsify."""
        passes = [self._p("tauri-ipc-linker", [["rust"]])]
        runs = [self._run("enclosure-linker", edges=99)]
        assert find_falsified_dependencies(passes, runs) == []

    def test_results_are_sorted_by_pass_id(self) -> None:
        """Deterministic across runs so two surveys diff without churn."""
        passes = [self._p("zed-linker", [["x"]]), self._p("abe-linker", [["x"]])]
        runs = [self._run("zed-linker", edges=1), self._run("abe-linker", edges=1)]
        assert [pid for pid, _ in find_falsified_dependencies(passes, runs)] == [
            "abe-linker", "zed-linker",
        ]

    def test_run_without_a_pass_id_is_ignored(self) -> None:
        """A malformed record must not be counted into the active set.

        ABSENT is not EMPTY: a record with no ``pass`` key names no pass, and
        admitting ``""`` to ``active_ids`` would let a clause be "satisfied" by
        a literal nothing produced.
        """
        passes = [self._p("tauri-ipc-linker", [["rust"]])]
        runs = [{"nodes_emitted": 5}, {"pass": "", "edges_emitted": 5},
                self._run("tauri-ipc-linker", edges=1)]
        assert find_falsified_dependencies(passes, runs) == [
            ("tauri-ipc-linker", [["rust"]]),
        ]

    def test_missing_counter_keys_read_as_zero_output(self) -> None:
        """A serialized run always carries both counters, but a hand-built or
        older record may not; absent must read as "emitted nothing", never as
        a falsification."""
        passes = [self._p("tauri-ipc-linker", [["rust"]])]
        assert find_falsified_dependencies(passes, [{"pass": "tauri-ipc-linker"}]) == []


class TestFormatFalsifiedDependencies:
    """The reader half. LIVE.md §1.7: a detector with no reader is the defect."""

    def test_empty_gives_none_so_a_clean_run_is_silent(self) -> None:
        assert format_falsified_dependencies([]) is None

    def test_line_names_the_pass_and_the_unsatisfied_clause(self) -> None:
        line = format_falsified_dependencies([("jni-linker", [["c", "cpp"]])])
        assert line is not None
        assert "jni-linker" in line
        assert "c" in line and "cpp" in line

    def test_counts_passes_not_clauses(self) -> None:
        line = format_falsified_dependencies([("a", [["x"], ["y"]])])
        assert line is not None
        assert line.startswith("[passes] 1 ")


class TestEmitFalsifiedDependencySummary:
    """The wire-up, proven to fire — LIVE.md §1.7 wants the wire-up proven."""

    def test_silent_when_nothing_is_falsified(self, capsys) -> None:
        emit_falsified_dependency_summary([], [])
        assert capsys.readouterr().err == ""

    def test_writes_one_line_to_stderr_when_falsified(self, capsys) -> None:
        passes = [Pass(id="jni-linker", description="", availability="core",
                       depends_on=[["c"]])]
        emit_falsified_dependency_summary(
            passes, [{"pass": "jni-linker", "nodes_emitted": 0, "edges_emitted": 2}],
        )
        err = capsys.readouterr().err
        assert err.count("\n") == 1
        assert "jni-linker" in err
