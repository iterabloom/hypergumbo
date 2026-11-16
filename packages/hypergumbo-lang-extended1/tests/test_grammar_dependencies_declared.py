# SPDX-License-Identifier: AGPL-3.0-or-later
"""The package declares every grammar distribution its analyzers load, and no other.

``hypergumbo-lang-extended1`` loaded ``tree-sitter-language-pack`` for D, asm,
BitBake and two dozen more analyzers without declaring it: it was installed only
because ``hypergumbo-lang-common`` and ``hypergumbo-lang-mainstream`` declare it,
so installing this package alone left those analyzers with no grammar. The
analyzers say which distribution they load (``grammar_distribution``, the value
the catalog publishes as ``requires``), so the package's ``dependencies`` can be
checked against them in both directions: every distribution an analyzer loads is
declared (a source-built grammar, vendored in core and built by
``hypergumbo build-grammars``, is not a PyPI distribution and is exempt), and
every declared ``tree-sitter-*`` grammar is one an analyzer loads, so a grammar
an analyzer stops loading (Agda moving from the ``tree-sitter-agda`` wheel to
the pack's build) leaves the dependency list with it. The pack is pinned at the
specifier the other language packages use: one environment holds one pack.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

import pytest

import hypergumbo_lang_extended1
from hypergumbo_core.analyze.base import TreeSitterAnalyzer
from hypergumbo_core.build_grammars import SOURCE_GRAMMARS

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py3.10
    import tomli as tomllib  # type: ignore[no-redef]

_PACKAGES = Path(__file__).resolve().parents[2]
_PACK = "tree-sitter-language-pack"


def _dependencies(package: str) -> dict[str, str]:
    """``{distribution name: full requirement}`` of one package's ``dependencies``."""
    cfg: dict[str, Any] = tomllib.loads((_PACKAGES / package / "pyproject.toml").read_text())
    deps: dict[str, str] = {}
    for requirement in cfg["project"]["dependencies"]:
        deps[re.split(r"[~=<>!\[;\s]", requirement, maxsplit=1)[0]] = requirement
    return deps


def _loaded_distributions() -> dict[str, set[str]]:
    """``{grammar distribution: analyzer modules}`` over this package's analyzers."""
    loaded: dict[str, set[str]] = {}
    for module_name in hypergumbo_lang_extended1.ANALYZER_MODULES:
        module = importlib.import_module(module_name)
        for value in vars(module).values():
            if (
                isinstance(value, type) and issubclass(value, TreeSitterAnalyzer)
                and value is not TreeSitterAnalyzer and value.__module__ == module.__name__
            ):
                distribution = value.grammar_distribution()
                if distribution is not None:
                    loaded.setdefault(distribution, set()).add(module_name)
    return loaded


_SOURCE_BUILT = {f"tree-sitter-{spec.name}" for spec in SOURCE_GRAMMARS}


def test_every_grammar_an_analyzer_loads_is_declared() -> None:
    declared = _dependencies("hypergumbo-lang-extended1")
    loaded = _loaded_distributions()
    missing = {
        dist: sorted(mods) for dist, mods in loaded.items()
        if dist not in declared and dist not in _SOURCE_BUILT
    }
    assert missing == {}
    # The population the check ran over: the pack and the dedicated wheels.
    assert len(loaded[_PACK]) >= 25
    assert len([d for d in loaded if d not in (_PACK, *_SOURCE_BUILT)]) >= 5


def test_every_declared_grammar_is_loaded_by_an_analyzer() -> None:
    loaded = _loaded_distributions()
    unused = sorted(
        dist for dist in _dependencies("hypergumbo-lang-extended1")
        if dist.startswith("tree-sitter-") and dist not in loaded
    )
    assert unused == []


@pytest.mark.parametrize("sibling", ["hypergumbo-lang-common", "hypergumbo-lang-mainstream"])
def test_the_pack_is_pinned_as_the_other_language_packages_pin_it(sibling: str) -> None:
    assert _dependencies("hypergumbo-lang-extended1")[_PACK] == _dependencies(sibling)[_PACK]
