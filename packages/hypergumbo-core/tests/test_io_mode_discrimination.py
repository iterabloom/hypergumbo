# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for mode-argument discrimination of dual-classified I/O primitives.

WHY THIS EXISTS. A handful of catalogued primitives are declared under two
boundaries at once because the *call* decides which one applies: Python's
``builtins.open`` is ``fs_read`` with its default mode and ``fs_write`` when
handed ``"w"``/``"a"``/``"x"``. ``python.yaml`` states that rule in a ``notes:``
field -- "Dual-classified: fs_read when mode is 'r'/'rb' (default), fs_write
when 'w'/'a'/'x'" -- but ``notes`` is free text that nothing consumes, so the
rule was documented and unimplemented.

The consequence was measured on hypergumbo itself and ran in BOTH directions,
which is why the two consumers have to be fixed in one change:

- ``io-boundaries`` classified every ``open()`` as ``fs_read``, because the
  qualified-name index is first-wins and ``fs_read`` is declared first. Real
  ``open(p, "w")`` writes were invisible -- a FALSE NEGATIVE.
- ``verify-claims`` derives a taint sink from every catalogue row, so
  ``builtins.open`` was always a ``host_fs`` sink. 24 of the 35 distinct
  violations of the shipped ``runtime-cli-no-host-fs`` claim were read-mode
  ``open()`` calls -- a FALSE POSITIVE, and the dominant one.

Fixing only one side moves the error rather than removing it, so these tests
pin both directions.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import (
    IoBoundaryCatalog,
    IoPrimitive,
    load_catalog,
    mode_discriminated_names,
    mode_discriminated_primitives,
    resolve_mode_boundary,
    select_by_mode,
)


def _dual_catalog() -> IoBoundaryCatalog:
    """A catalogue with ``open`` under both fs_read and fs_write, read first.

    Declaration ORDER matters and is deliberate: ``fs_read`` first reproduces
    ``python.yaml``, and therefore reproduces the first-wins qualified-index
    behaviour that produced the false negative.
    """
    return IoBoundaryCatalog(
        language="python",
        status="provenance_declared",
        primitives=[
            IoPrimitive("fs_read", "builtins", "open", "function"),
            IoPrimitive("fs_write", "builtins", "open", "function"),
            IoPrimitive("fs_write", "pathlib.Path", "write_text", "method"),
        ],
    )


class TestModeDiscriminatedNames:
    """Which primitives need a mode argument to classify is DERIVED, not listed.

    Deriving it from the catalogue keeps the rule as data: a language that
    declares a new dual-classified primitive gets discrimination without a
    code change, and hypergumbo cannot drift from its own catalogue.
    """

    def test_dual_classified_name_is_discovered(self) -> None:
        assert "open" in mode_discriminated_names(_dual_catalog())

    def test_single_boundary_name_is_not(self) -> None:
        assert "write_text" not in mode_discriminated_names(_dual_catalog())

    def test_live_python_catalog_flags_open(self) -> None:
        """Against the SHIPPED catalogue, not a fixture.

        A fixture-only test would keep passing if someone split the
        ``builtins.open`` rows apart, which is exactly when this machinery
        would silently stop being needed -- or silently stop firing.
        """
        assert "open" in mode_discriminated_names(load_catalog("python"))

    def test_only_read_write_pairs_qualify(self) -> None:
        """A name under two UNRELATED boundaries is not a mode question.

        ``gen_udp.open`` is net_recv+net_send because one call really does
        both; ``unistd.read`` is fs_read+ipc_recv+net_recv because the fd's
        kind is not knowable at the call site. Neither is discriminable by a
        mode literal, so neither may be swept into this mechanism.
        """
        cat = IoBoundaryCatalog(
            language="erlang",
            status="in_progress",
            primitives=[
                IoPrimitive("net_recv", "gen_udp", "open", "function"),
                IoPrimitive("net_send", "gen_udp", "open", "function"),
            ],
        )
        assert mode_discriminated_names(cat) == frozenset()


class TestTheKeyIsThePRIMITIVE_NotTheShortName:
    """INV-kaduh's control finding: short-name keying gates the wrong rows.

    ``mode_discriminated_names`` answers "which SHORT NAMES need a mode", which
    is the right question for an EMITTER — it sees ``open(`` before it knows the
    receiver. It is the wrong question for the SINK DERIVATION, which holds the
    whole ``IoPrimitive`` and was keying ``requires_mode`` on ``prim.name``.

    Rust pays for that. ``std::fs::File.open`` is fs_read and
    ``std::fs::OpenOptions.open`` is fs_write — two DIFFERENT primitives that
    share a short name, exactly the shape ``lookup_with_module`` exists to keep
    apart. Under short-name keying the OpenOptions sink inherited
    ``requires_mode="fs_write"``, no Rust analyzer stamps ``io_mode``, and
    ``resolve_mode_boundary(None)`` is ``fs_read`` — so Rust's only mode-gated
    host_fs write sink matched NOTHING, in every repo, unconditionally.

    That is the fail-open direction: a deleted sink is a clean verdict.
    """

    def test_same_name_in_different_modules_is_not_a_mode_question(
        self,
    ) -> None:
        cat = IoBoundaryCatalog(
            language="rust",
            status="in_progress",
            primitives=[
                IoPrimitive("fs_read", "std::fs::File", "open", "method"),
                IoPrimitive(
                    "fs_write", "std::fs::OpenOptions", "open", "method",
                ),
            ],
        )
        assert mode_discriminated_primitives(cat) == frozenset()

    def test_the_same_primitive_under_both_boundaries_still_qualifies(
        self,
    ) -> None:
        assert mode_discriminated_primitives(_dual_catalog()) == frozenset(
            {("builtins", "open", "function")},
        )

    def test_live_rust_openoptions_sink_is_not_mode_gated(self) -> None:
        """The behavioural half, on the SHIPPED catalogue.

        A fixture-only version of this test would go green while production
        kept deriving the gated sink from ``rust.yaml``.
        """
        from hypergumbo_core.taint import load_builtin_taint_catalog

        sinks = load_builtin_taint_catalog().sinks_for_language("rust")
        opens = [
            s for s in sinks
            if s.qualified_name == "std::fs::OpenOptions.open"
        ]
        assert opens, "rust must still derive an OpenOptions.open sink"
        assert all(s.requires_mode == "" for s in opens), (
            "OpenOptions.open is unconditionally write-capable; gating it on a "
            "mode no rust analyzer stamps deletes the sink outright"
        )


class TestSimultaneousIsNotAModeQuestion:
    """A primitive that crosses BOTH boundaries in one call has no mode to read.

    ``filelib:ensure_dir/1`` stats the path and creates the missing parents —
    fs_read and fs_write are both true, at once, and there is no mode argument
    anywhere in its signature. ``IoPrimitive.simultaneous`` is the declared
    marker for exactly this, so the derivation must consult it instead of
    inferring a mode question from the boundary pair alone.

    Left un-consulted it was the same deletion as rust's: erlang and elixir
    (which inherits erlang) both derived a ``requires_mode="fs_write"``
    ensure_dir sink that no analyzer could ever satisfy.
    """

    def test_simultaneous_pair_is_excluded(self) -> None:
        cat = IoBoundaryCatalog(
            language="erlang",
            status="in_progress",
            primitives=[
                IoPrimitive(
                    "fs_read", "filelib", "ensure_dir", "function",
                    simultaneous=True,
                ),
                IoPrimitive(
                    "fs_write", "filelib", "ensure_dir", "function",
                    simultaneous=True,
                ),
            ],
        )
        assert mode_discriminated_primitives(cat) == frozenset()

    @pytest.mark.parametrize("language", ["erlang", "elixir"])
    def test_live_ensure_dir_sink_is_not_mode_gated(
        self, language: str,
    ) -> None:
        from hypergumbo_core.taint import load_builtin_taint_catalog

        sinks = load_builtin_taint_catalog().sinks_for_language(language)
        rows = [
            s for s in sinks if s.qualified_name == "filelib.ensure_dir"
        ]
        assert rows, f"{language} must still derive an ensure_dir sink"
        assert all(s.requires_mode == "" for s in rows)


class TestEveryModeGatedLanguageHasAProducer:
    """INV-kaduh proper: a gate whose input nobody produces is a deletion.

    ``requires_mode`` and ``select_by_mode`` both consume ``meta["io_mode"]``.
    Python's analyzer stamps it; C's did not, so ``fopen(p, "w")`` tagged
    ``fs_read`` and the ``stdio.fopen`` write sink never matched — an EXAMINED
    negative for the boundary that is actually true, which reads clean rather
    than inconclusive.

    This is the parity test the item asked for, and it is written over the LIVE
    catalogues so the NEXT language to declare a mode-discriminated primitive
    fails HERE instead of silently classifying every write as a read.
    """

    def test_every_mode_discriminated_language_declares_where_the_mode_sits(
        self,
    ) -> None:
        import pathlib

        import hypergumbo_core.io_boundary as io_boundary_mod
        from hypergumbo_core.io_boundary import mode_argument_for

        primitives_dir = (
            pathlib.Path(io_boundary_mod.__file__).parent / "io_primitives"
        )
        unproduceable: list[str] = []
        for yaml_path in sorted(primitives_dir.glob("*.yaml")):
            language = yaml_path.stem
            catalog = load_catalog(language)
            for module, name, _kind in mode_discriminated_primitives(catalog):
                if mode_argument_for(language, name) is None:
                    unproduceable.append(f"{language}:{module}.{name}")
        assert not unproduceable, (
            "these primitives are boundary-decided by io_mode but no analyzer "
            "knows where their mode argument sits, so every call is "
            f"classified as fs_read: {unproduceable}"
        )


class TestModeArgumentResolution:
    """Which languages can answer "where does the mode sit", and which cannot.

    ``None`` is the load-bearing answer here, not a fallthrough: it is exactly
    the condition :class:`TestEveryModeGatedLanguageHasAProducer` refuses when
    the catalogue ALSO declares the primitive mode-discriminated. A language
    that silently returned some other language's table would satisfy that
    parity test while stamping the wrong argument.
    """

    def test_a_language_with_its_own_table_answers(self) -> None:
        from hypergumbo_core.io_boundary import mode_argument_for

        spec = mode_argument_for("python", "open")
        assert spec is not None
        assert (spec.position, spec.keyword) == (1, "mode")

    def test_c_declares_no_keyword_because_c_has_none(self) -> None:
        """Not a placeholder — a keyword lookup in C would never match."""
        from hypergumbo_core.io_boundary import mode_argument_for

        spec = mode_argument_for("c", "fopen")
        assert spec is not None
        assert (spec.position, spec.keyword) == (1, None)

    def test_a_child_language_inherits_through_the_catalogue_parent(
        self,
    ) -> None:
        """cpp gets C's answer by the SAME link it gets C's rows by.

        A copied ``cpp`` entry would be a second home for one fact and would
        drift on the first edit — the failure this module has paid for.
        """
        from hypergumbo_core.io_boundary import mode_argument_for

        assert mode_argument_for("cpp", "fopen") == mode_argument_for(
            "c", "fopen",
        )

    def test_javascript_declares_the_flags_position(self) -> None:
        """WI-nolut: node's flags string sits at positional 1, no keyword."""
        from hypergumbo_core.io_boundary import mode_argument_for

        for name in ("open", "openSync"):
            spec = mode_argument_for("javascript", name)
            assert spec is not None, name
            assert (spec.position, spec.keyword) == (1, None)

    def test_an_alias_language_answers_through_its_target(self) -> None:
        """typescript is javascript's ALIAS (one catalogue, one table), which
        is a different relation from cpp's PARENT hop and resolved beside it."""
        from hypergumbo_core.io_boundary import mode_argument_for

        assert mode_argument_for("typescript", "open") == mode_argument_for(
            "javascript", "open",
        )

    def test_a_language_with_no_table_and_no_parent_is_none(self) -> None:
        from hypergumbo_core.io_boundary import mode_argument_for

        assert mode_argument_for("go", "open") is None

    def test_a_language_whose_parent_also_has_no_table_is_none(self) -> None:
        """kotlin's catalogue parent is java, and java stamps no mode.

        Inheritance must not manufacture an answer out of an absent parent
        table — that would be the "clean extreme" this repo distrusts.
        """
        from hypergumbo_core.io_boundary import mode_argument_for

        assert mode_argument_for("kotlin", "open") is None

    def test_a_known_language_asked_about_an_unlisted_name_is_none(
        self,
    ) -> None:
        from hypergumbo_core.io_boundary import mode_argument_for

        assert mode_argument_for("c", "fwrite") is None


class TestResolveModeBoundary:
    """The mode string -> boundary rule, stated once."""

    @pytest.mark.parametrize("mode", ["w", "wb", "a", "ab", "x", "w+", "r+"])
    def test_write_modes(self, mode: str) -> None:
        assert resolve_mode_boundary(mode) == "fs_write"

    @pytest.mark.parametrize("mode", ["r", "rb", "rt"])
    def test_read_modes(self, mode: str) -> None:
        assert resolve_mode_boundary(mode) == "fs_read"

    def test_absent_mode_is_read(self) -> None:
        """Python's ``open`` defaults to ``'r'``; absence is not ignorance."""
        assert resolve_mode_boundary(None) == "fs_read"

    def test_r_plus_is_write_because_it_can_write(self) -> None:
        """``r+`` opens for update. A security claim cares that it CAN write."""
        assert resolve_mode_boundary("r+") == "fs_write"


class TestSelectByMode:
    """The shared predicate both consumers route through."""

    def test_write_mode_selects_the_write_row(self) -> None:
        cat = _dual_catalog()
        got = select_by_mode(cat.lookup_all("builtins.open"), "w")
        assert got is not None
        assert got.boundary == "fs_write"

    def test_read_mode_selects_the_read_row(self) -> None:
        cat = _dual_catalog()
        got = select_by_mode(cat.lookup_all("builtins.open"), "r")
        assert got is not None
        assert got.boundary == "fs_read"

    def test_unknown_mode_falls_back_to_read(self) -> None:
        """A non-literal mode (``open(p, m)``) must not invent a write.

        Guessing ``fs_write`` here would re-create the false positive this
        whole change exists to remove; guessing from nothing is not evidence.
        """
        cat = _dual_catalog()
        got = select_by_mode(cat.lookup_all("builtins.open"), None)
        assert got is not None
        assert got.boundary == "fs_read"

    def test_single_candidate_is_returned_unchanged(self) -> None:
        cat = _dual_catalog()
        got = select_by_mode(cat.lookup_all("pathlib.Path.write_text"), None)
        assert got is not None
        assert got.boundary == "fs_write"

    def test_empty_candidates_is_none(self) -> None:
        assert select_by_mode([], "w") is None

    def test_no_candidate_matches_the_wanted_boundary(self) -> None:
        """Reachable from the SHORT-NAME arm, where the rows share a name
        but not a read/write pairing.

        ``lookup_with_module`` funnels every multi-candidate list here, not
        only mode-discriminated ones — two same-named primitives from
        different modules land here too. Falling back to the first candidate
        preserves the pre-existing ``filtered[0]`` behaviour for them rather
        than dropping a match that used to resolve.
        """
        got = select_by_mode(
            [
                IoPrimitive("net_recv", "gen_udp", "open", "function"),
                IoPrimitive("net_send", "gen_udp", "open", "function"),
            ],
            "w",
        )
        assert got is not None
        assert got.boundary == "net_recv"


class TestTheSinkSideOfTheSameDefect:
    """The taint consumer, which failed in the OPPOSITE direction.

    ``_derive_auto_sinks_from_io_primitives`` turns every ``fs_write`` row into
    a ``host_fs`` sink. For ``builtins.open`` that made EVERY ``open()`` call a
    filesystem-write sink regardless of mode, which is where 24 of the 35
    distinct ``runtime-cli-no-host-fs`` violations came from.

    The gate has to be narrow. ``os.remove`` is ``fs_write`` only and carries
    no mode argument, so a rule of "no ``io_mode`` means not a write" would
    delete every genuine unconditional write in the catalogue — a false
    negative in a security gate, the expensive direction. Only a
    DUAL-CLASSIFIED sink may be mode-gated, which is what ``requires_mode``
    records at derivation time.
    """

    def test_dual_classified_write_sink_declares_its_mode_requirement(
        self,
    ) -> None:
        from hypergumbo_core.taint import load_builtin_taint_catalog

        sinks = load_builtin_taint_catalog().sinks_for_language("python")
        opens = [s for s in sinks if s.qualified_name == "builtins.open"]
        assert opens, "builtins.open must still be derivable as a sink"
        assert all(s.requires_mode == "fs_write" for s in opens)

    def test_unconditional_write_sink_declares_no_requirement(self) -> None:
        """``os.remove`` must stay unconditional or real deletions vanish."""
        from hypergumbo_core.taint import load_builtin_taint_catalog

        sinks = load_builtin_taint_catalog().sinks_for_language("python")
        removes = [s for s in sinks if s.qualified_name == "os.remove"]
        assert removes
        assert all(s.requires_mode == "" for s in removes)

    def test_read_mode_call_does_not_match_a_mode_gated_sink(self) -> None:
        from hypergumbo_core.taint import TaintSink, _match_propagation_entry

        gated = TaintSink(
            zone="host_fs", trust_level="untrusted", module="builtins",
            name="open", kind="function", requires_mode="fs_write",
        )
        assert _match_propagation_entry(
            {"open": [gated]}, "python:builtins:0-0:open:external_symbol",
            frozenset(), is_resolved=False, language="python", io_modes=None,
        ) is None

    def test_write_mode_call_still_matches(self) -> None:
        from hypergumbo_core.taint import TaintSink, _match_propagation_entry

        gated = TaintSink(
            zone="host_fs", trust_level="untrusted", module="builtins",
            name="open", kind="function", requires_mode="fs_write",
        )
        assert _match_propagation_entry(
            {"open": [gated]}, "python:builtins:0-0:open:external_symbol",
            frozenset(), is_resolved=False, language="python", io_modes=("w",),
        ) is gated

    def test_every_matcher_call_site_actually_threads_io_mode(self) -> None:
        """The gate is worthless if no caller supplies the evidence.

THE ARGUMENT IS NOW ``io_modes`` (plural, INV-vukiv): a collapsed edge
        carries EVERY site's mode, and asking the survivor's singular
        ``io_mode`` answered for whichever site arrived first — measured, that
        deleted a real ``open(p,'w')`` when an ``open(p,'r')`` sat above it.
        This probe follows the rename because an unwired call site is the same
        failure under either spelling.

        THIS IS THE TEST THAT WAS MISSING. The unit tests above pass
        the mode explicitly and went green while all four production call
        sites still omitted it — so ``io_mode`` was always ``None``, always
        resolved to ``fs_read``, and the ``builtins.open`` sink matched
        NOTHING rather than matching writes only. The corpus number was
        identical (24 -> 0) because this repo's ``open()`` calls happen to be
        reads, so the measurement could not tell suppression from
        discrimination. A genuine ``open(p, "w")`` would have been silently
        dropped from the violation set — a false negative in a security gate.

        Asserted structurally rather than behaviourally because the failure
        is an ABSENT argument: there is no input that makes an unwired call
        site behave differently from a correctly-wired one on read-mode data,
        which is exactly why it survived a full-suite run.
        """
        import ast
        import inspect

        from hypergumbo_core import taint as taint_mod

        tree = ast.parse(inspect.getsource(taint_mod))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "_match_propagation_entry"
        ]
        assert calls, "no call sites found — the probe itself is broken"
        unwired = [
            node.lineno for node in calls
            if "io_modes" not in {kw.arg for kw in node.keywords}
        ]
        assert not unwired, (
            f"_match_propagation_entry called without io_modes at lines "
            f"{unwired}: the mode gate degrades to blanket suppression there"
        )

    def test_ungated_sink_is_unaffected_by_absent_mode(self) -> None:
        """The regression guard for the 99% that carry no mode at all."""
        from hypergumbo_core.taint import TaintSink, _match_propagation_entry

        plain = TaintSink(
            zone="host_fs", trust_level="untrusted", module="os",
            name="remove", kind="function",
        )
        assert _match_propagation_entry(
            {"remove": [plain]}, "python:os:0-0:remove:external_symbol",
            frozenset(), is_resolved=False, language="python", io_modes=None,
        ) is plain


class TestTheWholeChainThroughTheProducer:
    """A write-mode ``open`` must still produce a FINDING, end to end.

    THE NON-VACUITY FLOOR FOR THIS ENTIRE CHANGE. Everything else here can
    pass while the gate merely suppresses ``builtins.open`` universally — and
    it briefly did: the corpus went 24 -> 0 distinct violations under blanket
    suppression, which is the identical number discrimination produces on a
    repo whose ``open()`` calls are all reads. A test that only asserts the
    reads disappear cannot tell the two apart. This one asserts the write
    SURVIVES, which only discrimination can satisfy.
    """

    @staticmethod
    def _edges(io_mode: str | None) -> list[dict]:
        src = "py:a.py:1-5:source_func:function"
        edges: list[dict] = [
            {
                "src": src,
                "dst": "py:external:0-0:Fernet.decrypt:unresolved",
                "type": "calls", "is_resolved": False,
            },
            {
                "src": src,
                "dst": "py:builtins:0-0:open:unresolved",
                "type": "calls", "is_resolved": False,
                "meta": {"io_mode": io_mode} if io_mode else {},
            },
        ]
        return edges

    @staticmethod
    def _run(io_mode: str | None):
        from hypergumbo_core.taint import (
            TaintSink,
            TaintSource,
            propagate_taint_structural,
        )

        sources = [TaintSource(
            taint_label="plaintext", module="cryptography.fernet",
            name="Fernet.decrypt", kind="function", return_tainted=True,
        )]
        sinks = [TaintSink(
            zone="host_fs", trust_level="untrusted", module="builtins",
            name="open", kind="function", requires_mode="fs_write",
        )]
        return propagate_taint_structural(
            TestTheWholeChainThroughTheProducer._edges(io_mode),
            sources, sinks, [],
        )

    def test_write_mode_open_still_reports_a_host_fs_finding(self) -> None:
        findings = self._run("w")
        assert len(findings) == 1, (
            "a genuine open(p, 'w') receiving tainted data must still be "
            "reported — if this is 0 the gate is suppressing, not "
            "discriminating"
        )
        assert findings[0].sink_zone == "host_fs"

    def test_read_mode_open_reports_nothing(self) -> None:
        assert self._run(None) == []


class TestQualifiedIndexKeepsBothRows:
    """The regression that produced the false negative.

    ``_rebuild_indices`` kept ONE row per qualified name ("first one wins --
    shouldn't have duplicates"), so the ``fs_write`` row for ``builtins.open``
    was dropped from the qualified index entirely and no amount of mode
    information could have recovered it.
    """

    def test_lookup_all_returns_both_boundaries_for_qualified_name(self) -> None:
        cat = _dual_catalog()
        boundaries = {p.boundary for p in cat.lookup_all("builtins.open")}
        assert boundaries == {"fs_read", "fs_write"}

    def test_live_python_catalog_keeps_both_open_rows(self) -> None:
        cat = load_catalog("python")
        boundaries = {p.boundary for p in cat.lookup_all("builtins.open")}
        assert boundaries == {"fs_read", "fs_write"}

    def test_the_dot_normalised_alias_is_in_the_all_index_too(self) -> None:
        """Regression: a second index that skipped WI-vipur's ``::`` alias.

        ``_rebuild_indices`` registers ``std.env.consts`` alongside
        ``std::env.consts`` so edges emitted in scoped-path mode still hit
        the catalogue. Adding ``_by_qualified_all`` without the alias made
        every Rust and C++ ``::`` primitive stop matching — caught by
        ``test_rust.py``'s ``std::env.consts`` case, pinned here at the
        index level so the two indices cannot drift apart again.
        """
        cat = IoBoundaryCatalog(
            language="rust",
            status="in_progress",
            primitives=[
                IoPrimitive("env_read", "std::env", "consts", "attribute"),
            ],
        )
        assert cat.lookup_all("std.env.consts") == cat.lookup_all(
            "std::env.consts",
        )
        assert cat.lookup_with_module("std.env.consts") is not None
