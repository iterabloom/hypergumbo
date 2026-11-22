# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py
"""``adopts:`` matches a type by the modules it adopts without inheriting.

Elixir tells Phoenix which module is a LiveView with ``use Phoenix.LiveView``;
Ruby tells Sidekiq which class is a worker with ``include Sidekiq::Worker``.
Neither is a base class, so no ``base_class:`` row can say it, and the rows
that tried to say it with ``decorator: "^use Phoenix\\.LiveView$"`` matched
nothing. The key reads what the analyzers record: ``meta["adopted_modules"]``
and ``meta["included_modules"]``.

These tests pin the matcher. That an analyzer WRITES the names a row is
written against is tested in the language packages, on source.
"""

from __future__ import annotations

from typing import Any

from hypergumbo_core.framework_patterns import FrameworkPatternDef, Pattern
from hypergumbo_core.ir import Span, Symbol


def _module(name: str, **meta: Any) -> Symbol:
    return Symbol(
        id=f"elixir:lib/x.ex:1-9:{name}:module", name=name, kind="module",
        language="elixir", path="lib/x.ex", span=Span(1, 9, 0, 0), meta=dict(meta),
    )


def test_it_reads_adopted_modules_and_included_modules() -> None:
    live = Pattern(concept="liveview", adopts=r"^Phoenix\.LiveView$")
    worker = Pattern(concept="task", adopts=r"^Sidekiq::Worker$")

    via_use = _module("AppWeb.PageLive", adopted_modules=["AppWeb", "Phoenix.LiveView"])
    assert live.matches(via_use) == {
        "concept": "liveview", "matched_adopts": "Phoenix.LiveView"}
    via_include = _module("HardJob", included_modules=["Comparable", "Sidekiq::Worker"])
    assert worker.matches(via_include) == {
        "concept": "task", "matched_adopts": "Sidekiq::Worker"}

    # A module that adopts something else, or nothing, does not match.
    assert live.matches(_module("AppWeb.Other", adopted_modules=["Phoenix.Component"])) is None
    assert live.matches(_module("Plain")) is None


def test_the_name_is_matched_whole_from_its_start() -> None:
    """``re.match`` on the full name: no fallback to the last segment (a row
    for ``Worker`` must not take ``Oban.Worker``), and no match inside it."""
    row = Pattern(concept="task", adopts=r"^Worker$")

    assert row.matches(_module("A", adopted_modules=["Worker"])) is not None
    assert row.matches(_module("B", adopted_modules=["Oban.Worker"])) is None
    unanchored = Pattern(concept="task", adopts=r"Oban\.Worker")
    assert unanchored.matches(_module("C", adopted_modules=["MyApp.Oban.Worker"])) is None
    assert unanchored.matches(_module("D", adopted_modules=["Oban.Worker"])) is not None


def test_inheritance_is_not_adoption() -> None:
    row = Pattern(concept="task", adopts=r"^GenServer$")

    assert row.matches(_module("A", base_classes=["GenServer"])) is None
    # A row that declares ``adopts`` and does not match by it does not fall
    # back to matching by its kind alone.
    kinded = Pattern(concept="task", adopts=r"^GenServer$", symbol_kind=r"^module$")
    assert kinded.matches(_module("B", adopted_modules=["Agent"])) is None


def test_a_value_that_is_not_a_list_of_names_matches_nothing() -> None:
    row = Pattern(concept="task", adopts=r"^GenServer$")

    assert row.matches(_module("A", adopted_modules="GenServer")) is None
    # A string is not a list of one-letter names.
    letter = Pattern(concept="task", adopts=r"^G$")
    assert letter.matches(_module("A2", adopted_modules="GenServer")) is None
    assert letter.matches(_module("A3", adopted_modules=["G"])) is not None
    assert row.matches(_module("B", adopted_modules=[None, 3, "GenServer"])) is not None
    assert row.matches(_module("C", adopted_modules=None, included_modules=["GenServer"])) is not None


def test_the_key_is_read_from_a_framework_file() -> None:
    loaded = FrameworkPatternDef.from_dict({
        "id": "otp", "language": "elixir",
        "patterns": [{"concept": "task", "adopts": "^GenServer$"}],
    })

    assert [(row.concept, row.adopts) for row in loaded.patterns] == [("task", "^GenServer$")]
    assert loaded.patterns[0].matches(_module("A", adopted_modules=["GenServer"])) is not None
