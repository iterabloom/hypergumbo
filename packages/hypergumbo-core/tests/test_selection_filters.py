# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the selection.filters module."""

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.selection.filters import (
    is_test_path,
    is_example_path,
    is_excluded_kind,
    is_key_symbol,
    key_symbols,
    production_edges,
    EXCLUDED_KINDS,
    KEY_SYMBOL_KINDS,
    EXAMPLE_PATH_PATTERNS,
)


def _sym(name, path="src/a.py", kind="function", tier=1):
    s = Symbol(
        id=f"python:{path}:1-2:{name}:{kind}",
        name=name, kind=kind, language="python", path=path,
        span=Span(1, 2, 0, 0),
    )
    s.supply_chain_tier = tier
    return s


class TestIsTestPath:
    """Tests for is_test_path function."""

    def test_test_directory(self):
        """Paths in test directories detected."""
        assert is_test_path("tests/test_main.py")
        assert is_test_path("test/test_utils.py")
        assert is_test_path("src/__tests__/Component.test.js")

    def test_test_prefix(self):
        """Files with test_ prefix detected."""
        assert is_test_path("test_main.py")
        assert is_test_path("src/test_utils.py")

    def test_test_suffix(self):
        """Files with test/spec suffix detected."""
        assert is_test_path("main.test.py")
        assert is_test_path("main.spec.js")
        assert is_test_path("Component.test.tsx")
        assert is_test_path("utils_test.py")

    def test_production_files(self):
        """Production files not matched."""
        assert not is_test_path("src/main.py")
        assert not is_test_path("lib/utils.js")
        assert not is_test_path("contest.py")  # contains 'test' but not a test file

    def test_empty_path(self):
        """Empty path returns False."""
        assert not is_test_path("")

    def test_gradle_test_fixtures(self):
        """Gradle test fixtures directory detected."""
        assert is_test_path("src/testFixtures/java/Utils.java")
        assert is_test_path("lib/testfixtures/Helper.kt")

    def test_gradle_integration_tests(self):
        """Gradle integration test directories detected."""
        assert is_test_path("src/intTest/java/IntegrationTest.java")
        assert is_test_path("src/integrationTest/kotlin/ApiTest.kt")

    def test_typescript_type_tests(self):
        """TypeScript type definition test files detected."""
        assert is_test_path("types/index.test-d.ts")
        assert is_test_path("src/types/api.test-d.tsx")

    def test_go_test_files(self):
        """Go test files detected."""
        assert is_test_path("pkg/handler_test.go")
        assert is_test_path("main_test.go")

    def test_rust_test_files(self):
        """Rust test files detected."""
        assert is_test_path("src/lib_test.rs")
        assert is_test_path("tests/integration_test.rs")

    def test_swift_test_files(self):
        """Swift test files detected."""
        assert is_test_path("Tests/RouteTests.swift")
        assert is_test_path("AppTests.swift")
        # Should not match TestHelpers.swift
        assert not is_test_path("src/TestHelpers.swift")

    def test_java_kotlin_test_files(self):
        """Java/Kotlin test files detected."""
        assert is_test_path("src/test/java/AppTest.java")
        assert is_test_path("UserServiceTests.java")
        assert is_test_path("HandlerTest.kt")
        assert is_test_path("RepositoryTests.kt")

    def test_python_tests_module(self):
        """Python tests.py single-file module detected."""
        assert is_test_path("tests.py")
        assert is_test_path("src/tests.py")
        # But not files that just contain 'tests'
        assert not is_test_path("contests.py")

    def test_ruby_rspec_files(self):
        """Ruby RSpec *_spec.rb files detected."""
        assert is_test_path("user_spec.rb")
        assert is_test_path("spec/models/user_spec.rb")
        assert is_test_path("app_spec.rb")
        # But not files that just end in .rb
        assert not is_test_path("helper.rb")


class TestIsExamplePath:
    """Tests for is_example_path function."""

    def test_examples_directory(self):
        """examples/ directory detected."""
        assert is_example_path("/project/examples/demo.py")
        assert is_example_path("examples/basic/main.js")

    def test_example_singular(self):
        """example/ (singular) directory detected."""
        assert is_example_path("/project/example/demo.py")
        assert is_example_path("example/main.js")

    def test_demos_directory(self):
        """demos/ directory detected."""
        assert is_example_path("/project/demos/showcase.py")
        assert is_example_path("demos/app.js")

    def test_demo_singular(self):
        """demo/ (singular) directory detected."""
        assert is_example_path("/project/demo/app.py")
        assert is_example_path("demo/main.js")

    def test_samples_directory(self):
        """samples/ directory detected."""
        assert is_example_path("/project/samples/quick.py")
        assert is_example_path("samples/api.js")

    def test_sample_singular(self):
        """sample/ (singular) directory detected."""
        assert is_example_path("/project/sample/app.py")
        assert is_example_path("sample/main.js")

    def test_playground_directory(self):
        """playground/ directory detected."""
        assert is_example_path("/project/playground/test.py")
        assert is_example_path("playground/experiment.js")

    def test_tutorials_directory(self):
        """tutorials/ and tutorial/ directories detected."""
        assert is_example_path("/project/tutorials/lesson1.py")
        assert is_example_path("tutorial/getting-started.md")

    def test_production_files(self):
        """Production files not matched."""
        assert not is_example_path("src/main.py")
        assert not is_example_path("lib/utils.js")
        assert not is_example_path("app/example_helper.py")

    def test_case_insensitive(self):
        """Matching is case-insensitive."""
        assert is_example_path("Examples/demo.py")
        assert is_example_path("DEMOS/app.js")


class TestExcludedKinds:
    """Tests for EXCLUDED_KINDS constant."""

    def test_dependency_excluded(self):
        """Dependency kinds are in the set.

        Post-Phase-4b (ADR-0027 §6, PR #3633): ``devDependency`` no
        longer carries Symbol.kind status — producers emit
        ``kind="dependency"`` + ``meta["dependency_scope"]="dev"``.
        The dev case is excluded by virtue of the ``dependency``
        fold target already being in this set."""
        assert "dependency" in EXCLUDED_KINDS

    def test_file_excluded(self):
        """File-level kinds are in the set."""
        assert "file" in EXCLUDED_KINDS
        assert "target" in EXCLUDED_KINDS
        assert "special_target" in EXCLUDED_KINDS

    def test_css_kinds_excluded(self):
        """CSS-specific kinds are in the set."""
        assert "class_selector" in EXCLUDED_KINDS
        assert "id_selector" in EXCLUDED_KINDS
        assert "variable" in EXCLUDED_KINDS
        assert "keyframes" in EXCLUDED_KINDS
        assert "media" in EXCLUDED_KINDS
        assert "font_face" in EXCLUDED_KINDS

    def test_npm_and_module_file_fold_targets_excluded(self):
        """Post-Phase-4b (ADR-0027 §6, PR #3633): ``npm_package`` /
        ``module_file`` no longer carry Symbol.kind status — producers
        emit ``kind="package"`` + ``meta["package_ecosystem"]="npm"``
        and ``kind="file"`` + ``meta["module_system"]`` respectively.
        The fold targets ``package`` and ``file`` are already in the
        set, so the post-fold shapes are excluded automatically."""
        assert "package" in EXCLUDED_KINDS
        assert "file" in EXCLUDED_KINDS

    def test_documentation_kinds_excluded(self):
        """Markdown documentation kinds are in the set."""
        assert "section" in EXCLUDED_KINDS
        assert "code_block" in EXCLUDED_KINDS
        assert "link" in EXCLUDED_KINDS

    def test_code_kinds_not_excluded(self):
        """Actual code kinds are NOT in the set."""
        assert "function" not in EXCLUDED_KINDS
        assert "class" not in EXCLUDED_KINDS
        assert "method" not in EXCLUDED_KINDS


class TestIsExcludedKind:
    """Tests for is_excluded_kind dual-shape predicate (WI-jukav slice 2).

    Post-Phase-4b (ADR-0027 §6, PR #3633) the predicate has two layers:

    1. Direct exclusion via :data:`EXCLUDED_KINDS` for canonical
       Symbol.kind values (``dependency``, ``file``, ``target``, CSS
       structural kinds, etc.).
    2. Post-fold exclusion via :data:`EXCLUDED_FRAMEWORK_ROLES` for
       symbols whose canonical ``Symbol.kind`` is ``function`` or
       ``method`` but whose ``Symbol.meta["framework_role"]`` is a
       framework role we want to suppress (``event_subscriber``).

    Pre-Phase-4b, both layers collapsed into ``EXCLUDED_KINDS`` because
    framework-role labels were still on ``Symbol.kind``. After Phase 4b
    removed those kinds, the meta-key vocabulary needed its own home."""

    def test_canonical_kind_in_set_is_excluded(self):
        """Direct exclusion: canonical Symbol.kind is in
        :data:`EXCLUDED_KINDS`."""
        assert is_excluded_kind("dependency") is True
        assert is_excluded_kind("file") is True

    def test_legacy_kind_not_in_set_is_not_excluded(self):
        """A kind not in the set is not excluded regardless of meta."""
        assert is_excluded_kind("function") is False
        assert is_excluded_kind("class") is False

    def test_post_fold_method_with_excluded_role_is_excluded(self):
        """Post-fold shape: kind=method + meta.framework_role=excluded."""
        assert is_excluded_kind(
            "method", {"framework_role": "event_subscriber"},
        ) is True

    def test_post_fold_function_with_excluded_role_is_excluded(self):
        """Post-fold shape: kind=function + meta.framework_role=excluded."""
        assert is_excluded_kind(
            "function", {"framework_role": "event_subscriber"},
        ) is True

    def test_post_fold_method_with_unrelated_role_is_not_excluded(self):
        """A function-role that's NOT in EXCLUDED_KINDS (e.g.,
        framework_role='route') doesn't trigger exclusion. Routes are
        first-class endpoints — the user wants to see them."""
        assert is_excluded_kind(
            "method", {"framework_role": "route"},
        ) is False

    def test_bare_method_no_meta_is_not_excluded(self):
        """Real methods (no framework_role meta) must not be excluded."""
        assert is_excluded_kind("method") is False
        assert is_excluded_kind("method", {}) is False
        assert is_excluded_kind("method", None) is False

    def test_meta_without_framework_role_is_not_excluded(self):
        """Meta dict present but no framework_role key — not excluded."""
        assert is_excluded_kind("method", {"signature": "foo()"}) is False

    def test_non_callable_kind_with_meta_role_is_not_excluded(self):
        """Only kind in {function, method} triggers the meta lookup —
        a struct or class with framework_role meta isn't excluded by
        this dual-shape rule. Defends against accidentally over-excluding
        framework-role-tagged container kinds."""
        assert is_excluded_kind(
            "class", {"framework_role": "event_subscriber"},
        ) is False


class TestExamplePathPatterns:
    """Tests for EXAMPLE_PATH_PATTERNS constant."""

    def test_expected_patterns(self):
        """All expected patterns are present."""
        patterns = EXAMPLE_PATH_PATTERNS
        assert "/examples/" in patterns
        assert "/example/" in patterns
        assert "/demos/" in patterns
        assert "/demo/" in patterns
        assert "/samples/" in patterns
        assert "/sample/" in patterns
        assert "/playground/" in patterns
        assert "/tutorial/" in patterns
        assert "/tutorials/" in patterns


class TestKeySymbolPolicy:
    """WI-zulij: the shared "is this worth showing a reader" policy.

    This lived inside ``sketch._format_symbols``'s body, unreachable by anything
    else, which is how compact's default came to advertise the same thing while
    ranking a different population. It has one home now, so a change to it moves
    both surfaces together instead of letting one drift.
    """

    def test_accepts_a_plain_declaration(self):
        assert is_key_symbol(_sym("handler"))

    def test_rejects_non_key_kind(self):
        """``file`` is the shape that actually led compact's output."""
        assert not is_key_symbol(_sym("src/main.py", kind="file"))

    def test_rejects_test_path(self):
        assert not is_key_symbol(_sym("helper", path="tests/conftest.py"))

    def test_rejects_test_named_symbol(self):
        """Substring, not prefix — kept verbatim from the sketch original."""
        assert not is_key_symbol(_sym("test_thing"))
        assert not is_key_symbol(_sym("helper_test_case"))

    def test_rejects_derived_artifact(self):
        """Tier 4 is minified/bundled/generated output."""
        assert not is_key_symbol(_sym("chunk", tier=4))

    def test_key_symbols_filters_the_population(self):
        syms = [
            _sym("real"),
            _sym("src/main.py", kind="file"),
            _sym("test_x", path="tests/test_x.py"),
        ]
        assert [s.name for s in key_symbols(syms)] == ["real"]

    def test_variable_is_in_both_sets_deliberately(self):
        """The allowlist and denylist are NOT complements, and this is the
        overlap that proves it: a Terraform variable is a real declaration
        surface while a CSS custom property is zero-edge noise. Whichever set a
        consumer applies is a policy choice about its own output.
        """
        assert "variable" in KEY_SYMBOL_KINDS
        assert "variable" in EXCLUDED_KINDS


class TestProductionEdges:
    """Edges ORIGINATING in a test file are dropped before centrality.

    Measured the dominant clause: on the eight 2026-07-17 bakeoff maps this
    alone moved sketch/compact top-10 agreement more than the symbol filter did.
    """

    def _edge(self, src, dst):
        return Edge(
            id=f"e:{src}->{dst}", src=src, dst=dst, edge_type="calls",
            line=1, confidence=0.9, origin="test", origin_run_id="test",
        )

    def test_drops_edge_from_a_test_file(self):
        prod = _sym("svc")
        tst = _sym("test_svc", path="tests/test_svc.py")
        edges = [self._edge(tst.id, prod.id)]
        assert production_edges([prod, tst], edges) == []

    def test_keeps_edge_into_a_test_file(self):
        """Direction matters: an edge INTO a test is still evidence about its
        target, so only the source side is filtered."""
        prod = _sym("svc")
        tst = _sym("test_svc", path="tests/test_svc.py")
        edges = [self._edge(prod.id, tst.id)]
        assert len(production_edges([prod, tst], edges)) == 1

    def test_keeps_production_edge(self):
        a, b = _sym("a"), _sym("b", path="src/b.py")
        edges = [self._edge(a.id, b.id)]
        assert len(production_edges([a, b], edges)) == 1

    def test_unknown_src_is_kept(self):
        """An edge whose src resolves to no symbol cannot be shown to originate
        in a test, so it is kept — failing open rather than silently dropping
        linker-minted edges whose endpoints live outside the passed set."""
        b = _sym("b")
        edges = [self._edge("python:nowhere:0-0:ghost:function", b.id)]
        assert len(production_edges([b], edges)) == 1
