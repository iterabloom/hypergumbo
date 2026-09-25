# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-kigub: `:module.function(...)` — Elixir's atom-module call form.

Elixir reaches every Erlang/OTP module through an ATOM module: `:ets.insert`,
`:gen_tcp.send`, `:httpc.request`. `_handle_dot_call` used to require the dot
node to carry an ``alias`` child, and an atom module parses as ``atom``, so the
handler returned before emitting anything — not an untyped edge and not a
misbound one, but *no edge at all*, which is an INV-linub L2 failure: no amount
of receiver evidence downstream can rescue a call site that produces nothing.

WHAT THESE TESTS PIN, AND WHY EACH ONE EXISTS.

1. *The module slot is spelled WITHOUT the colon.* This is the whole L3 half of
   the cell. ``_module_matches`` is component-aware and does no colon
   stripping, so ``catalog='ets'`` against ``hint=':ets'`` is False: emitting
   the atom verbatim would have produced an L2 gain that reaches no catalogue
   row — the exact shape INV-linub exists to catch. The erlang analyzer, which
   calls these same OTP modules, emits ``erlang:ets:0-0:insert:function``, so
   stripping is also the cross-language parity answer.

2. *Atom calls bypass first-party resolution entirely.* The alias path ends in
   a ``any(module_dot in k for k in global_symbols)`` SUBSTRING test (the
   sibling item WI-kafor is that test being wrong). ``"ets."`` is a substring
   of any project key like ``Foo.Sockets.insert``, so routing atoms through
   that branch would bind `:ets.insert` to a first-party function. An atom
   module is an OTP module by construction, so it goes straight to the
   unresolved fallback and the resolver never sees it.

3. *The shapes that stay gaps stay gaps.* Probing the grammar found SIX
   early-returning dot shapes, not the one filed. Scope is set by parity with
   the working alias path — fix the shapes whose module is written literally at
   the call site — and the rest are pinned here as known gaps so a later change
   has to acknowledge them rather than drift into them. In particular
   ``mod.fun(1)`` puts TWO ``identifier`` children on the dot node, so
   ``find_child_by_type(dot_node, "identifier")`` binds to ``mod`` — the
   RECEIVER, not the function. Loosening the guard generally would emit an edge
   named after a local variable.
"""
from pathlib import Path

import pytest


def _analyze(tmp_path: Path, source: str):
    from hypergumbo_lang_common.elixir import analyze_elixir

    (tmp_path / "probe.ex").write_text(source)
    return analyze_elixir(tmp_path)


def _call_dsts(result) -> list[str]:
    return [e.dst for e in result.edges if e.edge_type == "calls"]


MODULE = """\
defmodule Probe do
  def go(t, k, v) do
%s
  end
end
"""


class TestTheFiledSpelling:
    """The six rows WI-kigub measured on phoenix-framework."""

    @pytest.mark.parametrize(
        ("call", "module", "func"),
        [
            (":ets.insert(t, {k, v})", "ets", "insert"),
            (":ets.lookup(t, k)", "ets", "lookup"),
            (":ets.new(:tab, [])", "ets", "new"),
            (":ets.info(t)", "ets", "info"),
            (":ets.delete(t, k)", "ets", "delete"),
            (':httpc.request(:get, {~c"http://x", []}, [], [])', "httpc", "request"),
        ],
    )
    def test_atom_module_call_emits_an_edge(
        self, tmp_path: Path, call: str, module: str, func: str,
    ) -> None:
        """Each measured row now produces a call edge carrying its module."""
        result = _analyze(tmp_path, MODULE % f"    {call}")

        assert f"elixir:{module}:0-0:{func}:unresolved" in _call_dsts(result)

    def test_the_destination_carries_a_structured_external_ref(
        self, tmp_path: Path,
    ) -> None:
        """dst_ref is what the boundary engine reads, so it is populated."""
        result = _analyze(tmp_path, MODULE % "    :ets.insert(t, {k, v})")

        refs = [
            e.dst_ref for e in result.edges
            if e.edge_type == "calls" and e.dst_ref is not None
            and e.dst_ref.name == "insert"
        ]
        assert len(refs) == 1
        assert refs[0].lang == "elixir"
        assert refs[0].module_path == "ets"

    def test_the_edge_is_marked_unresolved(self, tmp_path: Path) -> None:
        """An OTP module is external: the edge must not claim resolution."""
        result = _analyze(tmp_path, MODULE % "    :ets.insert(t, {k, v})")

        edges = [
            e for e in result.edges
            if e.dst == "elixir:ets:0-0:insert:unresolved"
        ]
        assert len(edges) == 1
        assert edges[0].is_resolved is False
        assert edges[0].evidence_type == "ast_call_direct"


class TestTheColonIsStrippedBecauseTheCatalogueSaysSo:
    """The L3 half: an emitted module that reaches no row is not a fix."""

    def test_module_slot_has_no_leading_colon(self, tmp_path: Path) -> None:
        """`:ets` in source becomes `ets` in the module slot."""
        result = _analyze(tmp_path, MODULE % "    :ets.insert(t, {k, v})")

        assert "elixir::ets:0-0:insert:unresolved" not in _call_dsts(result)
        assert "elixir:ets:0-0:insert:unresolved" in _call_dsts(result)

    def test_the_emitted_module_actually_matches_its_catalogue_row(
        self, tmp_path: Path,
    ) -> None:
        """End-to-end on the real matcher, not on a restatement of it.

        This is the test that would have caught emitting `:ets` verbatim.
        """
        from hypergumbo_core.io_boundary import _module_matches, load_catalog

        result = _analyze(tmp_path, MODULE % "    :ets.insert(t, {k, v})")
        ref = next(
            e.dst_ref for e in result.edges
            if e.dst_ref is not None and e.dst_ref.name == "insert"
        )
        rows = [
            p for p in load_catalog("elixir").primitives
            if p.name == "insert" and p.module
            and _module_matches(p.module, ref.module_path)
        ]
        assert rows, "emitted module reaches no catalogue row"
        assert any(p.boundary == "db_write" for p in rows)


class TestAtomCallsNeverBindToFirstPartyCode:
    """The WI-kafor hazard, kept off this path by construction."""

    def test_a_project_module_whose_key_contains_ets_does_not_capture_it(
        self, tmp_path: Path,
    ) -> None:
        """`Foo.Sockets.insert` contains the substring `ets.` — and must lose.

        The alias path's `module_known` guard is a substring test, so if atom
        calls were routed through it this project would steal `:ets.insert`.
        """
        (tmp_path / "sockets.ex").write_text(
            "defmodule Foo.Sockets do\n"
            "  def insert(a, b), do: {a, b}\n"
            "end\n"
        )
        result = _analyze(tmp_path, MODULE % "    :ets.insert(t, {k, v})")

        # The theft condition is precisely: an edge OUT OF the caller that
        # lands on the project function. (A `def` line emits a spurious
        # self-edge on every definition in either arm -- pre-existing, filed
        # separately, and not what this test is about.)
        out_of_caller = [
            e.dst for e in result.edges
            if e.edge_type == "calls" and e.src.endswith(":Probe.go:function")
        ]
        assert "elixir:ets:0-0:insert:unresolved" in out_of_caller
        assert not [d for d in out_of_caller if "Foo.Sockets" in d]


class TestThePipeFormComesAlongFree:
    """`t |> :ets.insert(x)` parses as a call with an atom dot."""

    def test_piped_atom_call_emits_the_same_edge(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, MODULE % "    t |> :ets.insert({k, v})")

        assert "elixir:ets:0-0:insert:unresolved" in _call_dsts(result)


class TestKnownGapsStayGaps:
    """Shapes deliberately left unfixed — pinned so a drift has to notice."""

    @pytest.mark.parametrize(
        ("call", "why"),
        [
            ("mod.fun(1)", "receiver is a variable: no module evidence"),
            ("@attr.thing(1)", "module attribute: needs attribute tracking"),
            ("f.(1)", "anonymous call: there is no named callee at all"),
            ('   :"Elixir.Foo".bar(1)', "quoted atom: 0 corpus sites, and the "
                                        ":\"Elixir.Foo\" <-> Foo normalisation "
                                        "has no evidence to decide it"),
            ("__MODULE__.helper(1)", "enclosing-module receiver: a different "
                                     "mechanism, and it targets first-party "
                                     "code, so it cannot reach a catalogue row"),
        ],
    )
    def test_shape_emits_no_unresolved_call_edge(
        self, tmp_path: Path, call: str, why: str,
    ) -> None:
        result = _analyze(tmp_path, MODULE % f"    {call}")

        assert not [
            d for d in _call_dsts(result) if d.endswith(":unresolved")
        ], f"expected no edge ({why})"

    @pytest.mark.parametrize(
        ("call", "why"),
        [
            (":erlang.+(1, 2)", "an ATOM module with an operator callee: the "
                                "dot node's second child is an "
                                "`operator_identifier`, not an `identifier`, "
                                "so there is no name to key a destination on"),
            ("Kernel.+(1, 2)", "the alias-path twin of the same shape"),
            ("(fn x -> x end).(2)", "an immediately-called function literal"),
            ("foo().(3)", "calling the result of a call"),
        ],
    )
    def test_a_dot_call_with_no_named_callee_emits_nothing(
        self, tmp_path: Path, call: str, why: str,
    ) -> None:
        """A dot node can carry a receiver and still name no function.

        Worth its own case because the operator form reaches the atom branch:
        `:erlang.+(1, 2)` has exactly the `atom` receiver this change started
        emitting for, and it must still emit nothing, since a destination id
        of `elixir:erlang:0-0:+:unresolved` names no catalogue row and no
        project symbol. Refused deliberately, not by accident.
        """
        result = _analyze(tmp_path, MODULE % f"    {call}")

        assert not [
            d for d in _call_dsts(result) if d.endswith(":unresolved")
        ], f"expected no edge ({why})"

    def test_a_variable_receiver_never_becomes_the_callee_name(
        self, tmp_path: Path,
    ) -> None:
        """The specific way loosening the guard would go wrong.

        `mod.fun(1)` gives the dot node two `identifier` children, and
        `find_child_by_type` returns the FIRST — `mod`. An edge named after a
        local variable is worse than no edge.
        """
        result = _analyze(tmp_path, MODULE % "    mod.fun(1)")

        assert not [d for d in _call_dsts(result) if ":mod:" in d or d.endswith(":mod:unresolved")]


class TestTheAliasPathIsUnchanged:
    """P4's control, at unit scale: the fix is additive."""

    def test_external_alias_call_still_emits_its_module(
        self, tmp_path: Path,
    ) -> None:
        result = _analyze(tmp_path, MODULE % '    Logger.error("boom")')

        assert "elixir:Logger:0-0:error:unresolved" in _call_dsts(result)

    def test_first_party_qualified_call_still_resolves(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "helper.ex").write_text(
            "defmodule Helper do\n  def greet(n), do: n\nend\n"
        )
        result = _analyze(tmp_path, MODULE % "    Helper.greet(t)")

        assert [
            d for d in _call_dsts(result)
            if "Helper.greet" in d and not d.endswith(":unresolved")
        ]
