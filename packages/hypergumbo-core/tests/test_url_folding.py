# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the url_folding package (WI-mugog Phase A).

Three groups:

1. ``test_fold_string_interpolation_*`` — direct engine tests asserting the
   generalised folder reproduces the behaviour of the legacy
   ``_fold_template_literal`` from ``linkers/http.py``. Each test mirrors a
   case from ``TestFoldTemplateLiteral`` in ``test_http_linker.py`` so the
   refactor's behaviour-preservation is checked at the engine level (not
   only via the http.py wrapper, which would mask engine bugs).
2. ``test_fold_array_join_*`` — direct engine tests for the second engine,
   matching the legacy ``_fold_elm_string_join`` cases.
3. ``test_active_route_languages_are_covered_or_scoped`` — the property test
   that anchors the YAML+SCOPE substrate. Every language that has an HTTP-
   linker scanner must be listed either under some YAML's ``languages:``
   mapping or in ``SCOPE.md``. New scanners can't slip through silently.
4. ``test_registry_*`` — sanity checks on the registry loader (engine names
   resolve, config round-trips, scope-file parse handles the actual SCOPE.md
   shape).
"""

from __future__ import annotations

import pytest

from hypergumbo_core.url_folding import (
    ENGINES,
    UrlFoldingVariant,
    fold_array_join,
    fold_string_interpolation,
    get_covered_languages,
    get_scoped_languages,
    load_url_folding_registry,
)


_JS_PLACEHOLDER = r"\$\{([^}]+)\}"


class TestFoldStringInterpolation:
    """Mirrors TestFoldTemplateLiteral in test_http_linker.py against the
    generalised engine (parametrised by ``_JS_PLACEHOLDER`` to match the
    JS/TS template-literal slot shape the legacy function used).
    """

    def test_no_interpolation_yields_literal(self):
        url, url_type = fold_string_interpolation(
            "/api/users", {}, _JS_PLACEHOLDER,
        )
        assert url == "/api/users"
        assert url_type == "literal"

    def test_all_interpolations_resolved_yields_literal(self):
        url, url_type = fold_string_interpolation(
            "${BASE}/users", {"BASE": "/api/v1"}, _JS_PLACEHOLDER,
        )
        assert url == "/api/v1/users"
        assert url_type == "literal"

    def test_unresolved_leading_prefix_is_stripped(self):
        url, url_type = fold_string_interpolation(
            "${pathPrefix}/api/users", {}, _JS_PLACEHOLDER,
        )
        assert url == "/api/users"
        assert url_type == "literal"

    def test_middle_unresolved_slot_becomes_param(self):
        url, url_type = fold_string_interpolation(
            "/api/users/${id}", {}, _JS_PLACEHOLDER,
        )
        assert url == "/api/users/{id}"
        assert url_type == "literal"

    def test_trailing_non_path_slot_is_stripped_as_variable(self):
        url, url_type = fold_string_interpolation(
            "/api/items${queryString}", {}, _JS_PLACEHOLDER,
        )
        assert url == "/api/items"
        assert url_type == "variable"

    def test_mixed_folded_const_and_param(self):
        url, url_type = fold_string_interpolation(
            "${BASE}/users/${id}",
            {"BASE": "/api/v1"},
            _JS_PLACEHOLDER,
        )
        assert url == "/api/v1/users/{id}"
        assert url_type == "literal"

    def test_fully_unresolved_stays_variable(self):
        url, url_type = fold_string_interpolation(
            "${base}${path}", {}, _JS_PLACEHOLDER,
        )
        assert url == ""
        assert url_type == "variable"

    def test_mantine_ui_case_folds_prefix_and_strips_leading_host(self):
        url, url_type = fold_string_interpolation(
            "${pathPrefix}/${API_PATH}${path}${queryString}",
            {"API_PATH": "api/v1"},
            _JS_PLACEHOLDER,
        )
        assert url == "/api/v1"
        assert url_type == "variable"

    def test_engine_accepts_arbitrary_placeholder_pattern(self):
        """Phase-B forward check: a Python f-string-style placeholder
        (``{name}`` without leading ``$``) folds via the same engine when the
        caller supplies a matching regex. Confirms the engine is genuinely
        generalised, not JS/TS-hardcoded."""
        py_placeholder = r"\{([^}!:]+)\}"
        url, url_type = fold_string_interpolation(
            "/api/users/{user_id}",
            {},
            py_placeholder,
        )
        assert url == "/api/users/{user_id}"
        assert url_type == "literal"


class TestFoldArrayJoin:
    """Mirrors the _fold_elm_string_join cases against the generalised engine."""

    def test_returns_none_on_empty_items(self):
        assert fold_array_join([], "/") is None

    def test_returns_none_on_single_item(self):
        # One item is the assumed base-URL variable; no path segments remain.
        assert fold_array_join([("apiUrl", False)], "/") is None

    def test_literal_segments_joined_by_separator(self):
        items = [
            ("apiUrl", False),    # dropped as host prefix
            ("silence", True),
            ("alerts", True),
        ]
        assert fold_array_join(items, "/") == "/silence/alerts"

    def test_non_literal_items_become_named_placeholders(self):
        items = [
            ("apiUrl", False),
            ("silence", True),
            ("uuid", False),
        ]
        assert fold_array_join(items, "/") == "/silence/{uuid}"

    def test_separator_is_parametric(self):
        """A future Clojure/Lisp variant might use a different separator."""
        items = [
            ("base", False),
            ("a", True),
            ("b", True),
        ]
        assert fold_array_join(items, ".") == "/a.b"


class TestActiveRouteLanguageCoverage:
    """Property test: every active HTTP-linker scanner language is either
    covered by a YAML entry under url_folding/ or listed in SCOPE.md."""

    # The set of languages whose HTTP-client scanner is wired up in
    # ``linkers/http.py``. Keep this in sync with the suffix-dispatch table in
    # ``link_http`` (linkers/http.py:1395-1411). When a new scanner ships,
    # adding its language here forces a YAML entry or a SCOPE.md row.
    ACTIVE_ROUTE_LANGUAGES: frozenset[str] = frozenset(
        {"python", "javascript", "typescript", "go", "ruby", "java", "elm"},
    )

    def test_active_route_languages_are_covered_or_scoped(self):
        covered = get_covered_languages()
        scoped = get_scoped_languages()
        missing = self.ACTIVE_ROUTE_LANGUAGES - covered - scoped
        assert not missing, (
            f"Active route languages neither covered by a url_folding/*.yaml "
            f"entry nor declared literal-only in SCOPE.md: {sorted(missing)}. "
            f"Add a YAML entry under url_folding/ for the folding idiom this "
            f"language uses, or add a SCOPE.md row documenting the literal-"
            f"only scope decision."
        )

    def test_covered_and_scoped_are_disjoint(self):
        """A language must be exactly ONE of covered or scoped — never both,
        which would indicate a half-finished migration leaving stale scope."""
        covered = get_covered_languages()
        scoped = get_scoped_languages()
        overlap = covered & scoped
        assert not overlap, (
            f"Languages appear in both a YAML and SCOPE.md: {sorted(overlap)}. "
            f"Remove from SCOPE.md once a folding YAML entry exists."
        )


class TestRegistryLoader:
    """Sanity checks on the YAML loader output shape."""

    def test_load_returns_one_variant_per_language(self):
        variants = load_url_folding_registry()
        # Each YAML's ``languages:`` mapping is expanded into individual records.
        # Phase A YAMLs declare {javascript, typescript} for string_interpolation
        # and {elm} for array_join → 3 variants.
        assert len(variants) == 3
        assert all(isinstance(v, UrlFoldingVariant) for v in variants)

    def test_every_engine_name_resolves(self):
        """Every YAML's ``engine:`` value must be a key in ENGINES."""
        for variant in load_url_folding_registry():
            assert variant.engine in ENGINES, (
                f"YAML variant for {variant.language!r} references unknown "
                f"engine {variant.engine!r}; add it to url_folding/__init__.py "
                f"ENGINES or fix the YAML."
            )

    def test_string_interpolation_languages_carry_placeholder_pattern(self):
        for variant in load_url_folding_registry():
            if variant.idiom == "string_interpolation":
                config = variant.config_dict()
                assert "placeholder_pattern" in config

    def test_array_join_languages_carry_separator(self):
        for variant in load_url_folding_registry():
            if variant.idiom == "array_join":
                config = variant.config_dict()
                assert "separator" in config

    def test_elm_separator_is_forward_slash(self):
        variants = load_url_folding_registry()
        elm_variants = [v for v in variants if v.language == "elm"]
        assert len(elm_variants) == 1
        assert elm_variants[0].config_dict()["separator"] == "/"

    def test_scope_md_parses_phase_a_literal_only_languages(self):
        """SCOPE.md table should yield the Phase-A literal-only set."""
        assert get_scoped_languages() == {"python", "go", "ruby", "java"}


class TestEngineRegistry:
    """Verify the ENGINES dict is shaped as callers expect."""

    def test_engines_dict_has_both_phase_a_engines(self):
        assert "fold_string_interpolation" in ENGINES
        assert "fold_array_join" in ENGINES

    def test_engines_are_callable(self):
        for name, fn in ENGINES.items():
            assert callable(fn), f"Engine {name!r} is not callable"
