# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-pakof: a pass's ``requires`` names the grammar distribution it loads.

The catalog stamped ``requires="tree-sitter-language-pack"`` on every
tree-sitter pass, and 39 of those 63 load a dedicated wheel instead
(``rust.py``: ``grammar_module = "tree_sitter_rust"``). Removing the language
pack left rust, go, java and the rest running. ``is_available`` could not
notice, because it tested the field for the substring ``"tree-sitter"`` and
then checked only the base ``tree_sitter`` package -- so ADR-0056's bare-venv
acceptance test, run as written, came back empty.
"""
from __future__ import annotations

import importlib.util
from typing import Any
from unittest.mock import patch

from hypergumbo_core.analyze.base import TreeSitterAnalyzer
from hypergumbo_core.catalog import Pass, get_default_catalog, is_available


class _Direct(TreeSitterAnalyzer):
    lang = "x"
    grammar_module = "tree_sitter_c_sharp"


class _Packed(TreeSitterAnalyzer):
    lang = "y"
    language_pack_name = "apex"


class _Neither(TreeSitterAnalyzer):
    lang = "z"


class TestTheDeclaredGrammarNamesItsDistribution:
    def test_a_dedicated_wheel(self) -> None:
        """PyPI spells the module's underscores as hyphens."""
        assert _Direct.grammar_distribution() == "tree-sitter-c-sharp"

    def test_a_language_pack_grammar(self) -> None:
        assert _Packed.grammar_distribution() == "tree-sitter-language-pack"

    def test_no_declaration_names_nothing(self) -> None:
        assert _Neither.grammar_distribution() is None


class TestTheCatalogPublishesIt:
    def _requires(self) -> dict[str, Any]:
        return {p.id: p.requires for p in get_default_catalog().passes}

    def test_a_dedicated_wheel_pass_names_its_wheel(self) -> None:
        requires = self._requires()
        assert requires["rust"] == "tree-sitter-rust"
        assert requires["csharp"] == "tree-sitter-c-sharp"
        assert requires["go"] == "tree-sitter-go"

    def test_a_language_pack_pass_still_names_the_pack(self) -> None:
        assert self._requires()["apex"] == "tree-sitter-language-pack"

    def test_every_published_grammar_is_the_one_the_analyzer_loads(self) -> None:
        """The whole population, not three named rows: for every pass whose
        module defines a TreeSitterAnalyzer, ``requires`` is that class's
        grammar distribution."""
        import sys

        from hypergumbo_core.analyze.registry import _ANALYZER_REGISTRY

        requires = self._requires()
        checked = 0
        for name, reg in _ANALYZER_REGISTRY.items():
            module = sys.modules[reg.get_func().__module__]
            classes = {
                value for value in vars(module).values()
                if isinstance(value, type) and issubclass(value, TreeSitterAnalyzer)
                and value is not TreeSitterAnalyzer and value.__module__ == module.__name__
            }
            if len(classes) != 1:
                continue
            declared = next(iter(classes)).grammar_distribution()
            if declared is None:
                continue
            assert requires[name] == declared, name
            checked += 1
        assert checked >= 60


class TestAvailabilityChecksThatDistribution:
    def test_a_missing_dedicated_wheel_makes_its_pass_unavailable(self) -> None:
        """The defect: with tree_sitter present and tree_sitter_rust absent,
        the substring test reported rust available."""
        real = importlib.util.find_spec
        p = Pass("rust", "Rust", "extra", "tree-sitter-rust")
        with patch(
            "importlib.util.find_spec",
            side_effect=lambda name, *a: None if name == "tree_sitter_rust" else real(name, *a),
        ):
            assert is_available(p) is False
        assert is_available(p) is True

    def test_the_base_library_is_still_required(self) -> None:
        p = Pass("rust", "Rust", "extra", "tree-sitter-rust")
        with patch("importlib.util.find_spec", return_value=None):
            assert is_available(p) is False
