# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-kubup: ``is_exported`` states what a producer observed, or nothing.

``Symbol.is_exported`` was ``bool = False``. Seventeen of the shipped
analyzer modules compute exportedness; the other ninety-odd do not, and for
every record they emit the artifact said ``supply_chain.is_exported:
false`` — not "unknown", a positive claim that the symbol is NOT part of
the public API. On this repository's own survey that was 36,659 records,
29,505 of them with no visibility signal of any kind.

The cure is the absence the type could not express, plus a rule that only
a positive input may fill it:

* an analyzer that has a rule sets ``True``/``False`` and is believed;
* an export MODIFIER (``pub`` / ``public`` / ``exported``) fills ``True``;
* a language signal that says the symbol is NOT public fills ``False`` —
  public API membership needs public visibility (finalize, INV-jusot);
* nothing else fills it. ``None`` survives to the artifact as ``null``.

These tests pin both halves: the record's own default, and the pipeline's
refusal to manufacture a ``False`` for a language that never spoke.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import Span, Symbol

PY_SOURCE = '''\
"""A module whose __all__ is the authority on its public API."""

__all__ = ["published"]


def published():
    return 1


def unpublished():
    return 2


def _private():
    return 3
'''

SH_SOURCE = """\
#!/usr/bin/env bash
greet() {
  echo hello
}
greet
"""


def _symbol(**overrides: object) -> Symbol:
    fields: dict[str, object] = {
        "id": "lua:a.lua:1-1:f:function", "name": "f", "kind": "function",
        "language": "lua", "path": "a.lua", "span": Span(1, 1, 0, 0),
    }
    fields.update(overrides)
    return Symbol(**fields)  # type: ignore[arg-type]


class TestTheRecord:
    def test_a_record_nobody_asked_carries_no_claim(self) -> None:
        symbol = _symbol()
        assert symbol.is_exported is None
        assert symbol.to_dict()["supply_chain"]["is_exported"] is None

    def test_an_analyzer_that_measured_not_exported_is_believed(self) -> None:
        """The distinction the type now carries: an explicit ``False`` is an
        observation and must survive serialization as one."""
        symbol = _symbol(is_exported=False)
        as_dict = symbol.to_dict()
        assert as_dict["supply_chain"]["is_exported"] is False
        assert Symbol.from_dict(as_dict).is_exported is False

    def test_an_artifact_without_the_key_is_unknown_not_false(self) -> None:
        as_dict = _symbol().to_dict()
        del as_dict["supply_chain"]["is_exported"]
        assert Symbol.from_dict(as_dict).is_exported is None


def _run(tmp_path: Path) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "mod.py").write_text(PY_SOURCE)
    (repo / "script.sh").write_text(SH_SOURCE)
    out = tmp_path / "results.json"
    run_behavior_map(
        repo_root=repo, out_path=out, budgets="none",
        include_sketch_precomputed=False, enable_handler_slices=False,
    )
    return json.loads(out.read_text())


def _claim(data: dict, name: str, path: str) -> object:
    [node] = [n for n in data["nodes"] if n["name"] == name and n["path"] == path]
    return (node.get("supply_chain") or {})["is_exported"]


class TestTheSketchPath:
    """``sketch.py`` carries its own copy of the supply-chain classification
    loop, so the rule has to hold there too — a fix applied to one copy and
    not the other is how a fact comes to depend on which command you ran."""

    def test_a_modifier_fills_the_field_and_its_absence_does_not(self, tmp_path: Path) -> None:
        """Groovy is the clean case: `groovy.py` has no exportedness rule of
        its own, so the `public` modifier is the ONLY signal, and a method
        without one leaves the field unobserved rather than not-exported."""
        from hypergumbo_core.profile import detect_profile
        from hypergumbo_core.sketch import _run_analysis

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "Thing.groovy").write_text(
            "class Thing {\n"
            "    public void shown() { }\n"
            "    void unmarked() { }\n"
            "}\n"
        )
        symbols, _edges, _coverage = _run_analysis(repo, detect_profile(repo))
        by_name = {s.name.rsplit(".", 1)[-1]: s for s in symbols}
        assert by_name["shown"].is_exported is True
        assert by_name["unmarked"].is_exported is None


class TestThePipeline:
    def test_a_language_with_a_rule_still_says_both_things(self, tmp_path: Path) -> None:
        data = _run(tmp_path)
        assert _claim(data, "published", "mod.py") is True
        assert _claim(data, "unpublished", "mod.py") is False  # __all__ is authoritative
        assert _claim(data, "_private", "mod.py") is False  # name convention -> not public

    def test_a_language_with_no_rule_says_nothing_rather_than_no(self, tmp_path: Path) -> None:
        """`bash.py` has no exportedness rule and bash has no export syntax
        the modifier fold can read. The artifact must say so."""
        data = _run(tmp_path)
        assert _claim(data, "greet", "script.sh") is None
