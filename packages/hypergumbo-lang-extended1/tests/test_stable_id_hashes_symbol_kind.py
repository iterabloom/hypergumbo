# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/fennel.py
"""A stable id hashes the kind its Symbol reports (WI-lilab).

Nine analyzers minted the node id from ``Symbol.kind`` (ADR-0036 Ruling 2)
but handed the stable-id formula a private role word: ``fn``, ``var``,
``proc``, ``model``, ``struct``, ``union``, ``type_alias``, a Prisma block's
``datasource`` / ``generator`` keyword. The stable id then named a kind no
consumer could see, and two producers of one construct could not agree on
it. Each analyzer in :data:`CASES` now passes the Symbol's own kind.

HOW IT IS READ. The test does not recompute an id: it records the ``kind``
argument of every call into the two formulas an untyped or typed producer id
comes from (``assemble_stable_id``, ``make_typed_stable_id``), keyed by the
value returned, and looks each emitted Symbol's ``stable_id`` up in that
record. The real formulas run; the record only reads their arguments. A
Symbol whose stable id came from neither formula is reported too, so the
check cannot pass by not looking.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import hypergumbo_core.analyze.base as base


def _record_hashed_kinds(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Wrap both formulas wherever a hypergumbo module holds them by name."""
    record: dict[str, str] = {}
    originals = (base.assemble_stable_id, base.make_typed_stable_id)

    def spy(formula: Callable[..., Any]) -> Callable[..., Any]:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            value = formula(*args, **kwargs)
            record[value] = kwargs["kind"] if "kind" in kwargs else args[0]
            return value
        return wrapped

    spies = {id(f): spy(f) for f in originals}
    for module in list(sys.modules.values()):
        if not getattr(module, "__name__", "").startswith("hypergumbo"):
            continue
        for attr, value in list(vars(module).items()):
            if id(value) in spies and any(value is f for f in originals):
                monkeypatch.setattr(module, attr, spies[id(value)])
    return record


_FENNEL = """\
(fn add [a b] (+ a b))
(local limit 10)
"""


#: language -> (analyzer module, entry point, file name, source, the kinds the
#: source must reach). The reach set holds every kind whose hashed word was a
#: role word, and at least one that already matched (the control).
CASES: dict[str, tuple[str, str, str, str, frozenset[str]]] = {
    "fennel": ("fennel", "analyze_fennel", "demo.fnl", _FENNEL,
               frozenset({"function", "variable"})),
}


def _analyze(lang: str, tmp_path: Path) -> list[Any]:
    module_name, entry, filename, source, _ = CASES[lang]
    module = __import__(f"hypergumbo_lang_extended1.{module_name}", fromlist=[entry])
    (tmp_path / filename).write_text(source)
    result = getattr(module, entry)(tmp_path)
    return [s for s in result.symbols if s.kind != "file"]


@pytest.mark.parametrize("lang", sorted(CASES))
def test_every_stable_id_hashes_the_kind_its_symbol_reports(
    lang: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record_hashed_kinds(monkeypatch)
    symbols = _analyze(lang, tmp_path)
    assert CASES[lang][4] <= {s.kind for s in symbols}, sorted({s.kind for s in symbols})
    unrecorded = [(s.kind, s.name) for s in symbols if s.stable_id not in record]
    assert unrecorded == []
    wrong = sorted((s.kind, record[s.stable_id], s.name) for s in symbols
                   if record[s.stable_id] != s.kind)
    assert wrong == []
