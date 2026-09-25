# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every multi-boundary primitive declares WHY it has several (INV-vaduk).

What was already fixed, and by what
------------------------------------
INV-zumin built the mechanism: ``simultaneous`` is a declared per-row marker,
``simultaneous_boundaries_for`` is the shared predicate, and a genuinely-both
primitive now carries every boundary in ``meta["io_boundaries"]`` instead of
losing all but the first to YAML row order. INV-kaduh wired ``io_mode`` into
the C/C++ analyzers so the mode-discriminated shape resolves too. Verified on
the live catalogues: scala ``Process.apply``, objc ``NSURLConnection``'s two
request methods, and erlang/elixir ``filelib.ensure_dir`` all resolve to both
boundaries.

What was left
-------------
A census of all 15 shipped catalogues finds **29** multi-boundary
``(module, name)`` primitives. 17 declare a discriminator — 11 by mode, 6 by
``simultaneous``. **12 declare nothing at all**, and *absence is overloaded*.
It means both:

* **ruled undecidable** — C's ``unistd.write`` is fs_write, ipc_send or
  net_send depending on what the fd IS, which is established where the fd was
  opened. Exactly one is true and the call site cannot know which. The rows
  document this in prose and the behaviour is correct.
* **nobody has decided** — haskell's ``Control.Concurrent.STM.newTVar`` is
  declared ``[db_read, db_write]``, and creating a TVar is neither.

A consumer cannot tell those apart, and neither can a reviewer. That is the
same defect shape as the item this file is named for: one slot carrying two
meanings with the reader left guessing.

The fix
-------
``boundary_ruling`` makes the third and fourth cases explicit, and
:func:`multi_boundary_reason` is the ONE predicate that answers "why does this
primitive have several boundaries?" by consulting all three sources. A
multi-boundary primitive with no reason now **fails CI** rather than reading
as a deliberate undecidable.

``unruled`` is a legal, counted value rather than an escape hatch, following
the ``write_discipline: unaudited`` precedent — visible debt that a gate
reports beats invisible debt that absence hides. The test below pins the
unruled set EXACTLY, so a new one cannot be added quietly and a resolved one
must be removed here.
"""

from __future__ import annotations

import collections
import glob
from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import (
    BOUNDARY_RULING_UNDECIDABLE,
    BOUNDARY_RULING_UNRULED,
    MULTI_BOUNDARY_REASON_MODE,
    MULTI_BOUNDARY_REASON_SIMULTANEOUS,
    IoBoundaryCatalog,
    load_catalog,
    multi_boundary_reason,
    unruled_multi_boundary_primitives,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _shipped_catalogs() -> dict[str, IoBoundaryCatalog]:
    out: dict[str, IoBoundaryCatalog] = {}
    for path in sorted(
        glob.glob(str(REPO_ROOT / "packages/*/src/*/io_primitives/*.yaml"))
    ):
        lang = Path(path).stem
        cat = load_catalog(lang)
        if cat is not None:
            out[lang] = cat
    return out


def _multi_boundary(cat: IoBoundaryCatalog) -> dict[str, set[str]]:
    byname: dict[str, set[str]] = collections.defaultdict(set)
    for prim in cat.primitives:
        byname[prim.qualified_name].add(prim.boundary)
    return {k: v for k, v in byname.items() if len(v) > 1}


# The primitives whose several boundaries are a genuinely open question.
# Pinned exactly: adding one requires editing this list, and resolving one
# requires deleting from it. Each entry states the question, because an
# unruled row without a stated question is indistinguishable from an
# overlooked one.
#: THE DECLARED-DEBT REGISTER. EMPTIED 2026-08-28 (INV-nular); RE-OPENED
#: 2026-09-03 WITH ONE ENTRY (INV-bofab -> WI-kapak), stated below.
#:
#: Before that it held exactly two questions, and both were answered by removing rows
#: rather than by ruling the rows correct:
#:
#: * ``erlang``/``elixir`` ``gen_udp.open`` — "Is a socket-acquisition call a
#:   net_* crossing at all?"  **No.** Opening a socket transfers nothing at the
#:   call itself, as the row's own note said; ``gen_udp.send``/``recv`` carry
#:   the real transfers. Removed from both boundaries (WI-dosov).
#: * ``haskell`` ``Control.Concurrent.STM.newTVar``/``newTVarIO`` — "creating a
#:   TVar is neither a read nor a write ... the db_* classification of the whole
#:   Data.IORef / STRef / MVar / STM family is the prior question."  Answered by
#:   deleting that family: in-process mutable references are not a database, and
#:   because ``db_read`` is an auto-derived taint SOURCE the rows were minting
#:   ``untrusted_input`` at calls that observe nothing (WI-rigut).
#:
#: AN EMPTY REGISTER IS THE SUCCESS STATE, NOT A BROKEN FIXTURE. The gate below
#: (``test_no_multi_boundary_primitive_lacks_a_reason``) is what keeps it
#: honest: a new multi-boundary row with no reason still fails CI, and the only
#: way to pass is to rule it or add it back here WITH ITS QUESTION STATED.
#:
#: The entry below is the second route taken deliberately. It is NOT a row
#: nobody looked at: both settled reasons were tried against the shipped
#: pydoc source and each asserts something false, so the honest state is a
#: question the vocabulary cannot yet answer, and WI-kapak owns answering it.
EXPECTED_UNRULED: dict[str, str] = {
    "python:builtins.help": (
        "Which ruling describes 'logging ALWAYS, subprocess only when the "
        "ENVIRONMENT supplies a tty and a pager'? help() renders through "
        "pydoc to stdout on every call; on a tty pydoc.getpager probes for "
        "pager/less/more through os.system and then pipes the text through "
        "subprocess.Popen(shell=True). `simultaneous` claims the launch "
        "happens off a tty too; `call_site_undecidable` claims exactly one "
        "boundary is true and the call site decides, and neither half holds. "
        "Rowed when INV-bofab enumerated builtins. WI-kapak holds the "
        "vocabulary gap, and also why help()'s no-argument interactive stdin "
        "loop is disclosed on the row and not rowed: help returns None, so "
        "nothing the far side chose reaches the caller (ADR-0049)."
    ),
}


class TestEveryMultiBoundaryPrimitiveDeclaresAReason:
    def test_no_multi_boundary_primitive_lacks_a_reason(self) -> None:
        """THE GATE. Absence of a reason is now an error, not a default."""
        missing: list[str] = []
        for lang, cat in _shipped_catalogs().items():
            for qn in _multi_boundary(cat):
                if multi_boundary_reason(cat, qn) is None:
                    missing.append(f"{lang}:{qn}")
        assert not missing, (
            "These primitives are catalogued under several boundaries with no "
            "declared reason, so a consumer cannot tell 'ruled undecidable' "
            "from 'nobody looked':\n  " + "\n  ".join(sorted(missing))
            + "\nDeclare `boundary_ruling: call_site_undecidable` (exactly one "
            "boundary is true, the call site cannot know which) or "
            "`boundary_ruling: unruled` (an open question — add it to "
            "EXPECTED_UNRULED with the question stated)."
        )

    def test_the_unruled_set_is_exactly_what_we_expect(self) -> None:
        """Visible debt, pinned. Not an escape hatch."""
        actual = set(unruled_multi_boundary_primitives(_shipped_catalogs()))
        expected = set(EXPECTED_UNRULED)
        assert actual == expected, (
            f"unruled set drifted.\n  newly unruled: {sorted(actual - expected)}"
            f"\n  resolved (strike these out of EXPECTED_UNRULED): "
            f"{sorted(expected - actual)}"
        )

    def test_the_census_is_non_vacuous(self) -> None:
        """A gate over an empty population passes trivially."""
        total = sum(len(_multi_boundary(c)) for c in _shipped_catalogs().values())
        assert total >= 25, (
            f"only {total} multi-boundary primitives found; the catalogues "
            f"were not loaded and this whole file would pass vacuously"
        )

    def test_each_settled_reason_is_actually_represented(self) -> None:
        """Every reason that describes a SETTLED shape occurs live, so no
        branch is dead. A resolver whose branches never fire on the live corpus
        is indistinguishable from one that returns a constant.

        ``unruled`` IS DELIBERATELY NOT ASSERTED HERE. It names an OPEN
        QUESTION, and the live count of open questions is whatever the register
        above holds -- zero after INV-nular answered both, one since INV-bofab
        rowed ``builtins.help``. Requiring one to exist would make the suite
        fail the moment the last piece of declared debt is paid — a test that
        punishes success, and a hardcoded inventory besides. The resolver's
        ``unruled`` branch is exercised on a synthetic catalogue instead, in
        :class:`TestTheResolver`, which is where the invariant actually lives:
        the branch must WORK, not the debt must EXIST."""
        seen = collections.Counter()
        for cat in _shipped_catalogs().values():
            for qn in _multi_boundary(cat):
                seen[multi_boundary_reason(cat, qn)] += 1
        for reason in (
            MULTI_BOUNDARY_REASON_MODE,
            MULTI_BOUNDARY_REASON_SIMULTANEOUS,
            BOUNDARY_RULING_UNDECIDABLE,
        ):
            assert seen[reason] > 0, f"no primitive resolves to {reason!r}: {seen}"
        assert seen[BOUNDARY_RULING_UNRULED] == len(EXPECTED_UNRULED), (
            "the live unruled count must equal the register above; they are "
            "two views of the same debt"
        )


class TestTheResolver:
    def test_single_boundary_primitive_has_no_reason(self) -> None:
        cat = load_catalog("python")
        assert cat is not None
        assert multi_boundary_reason(cat, "subprocess.run") is None

    def test_unknown_primitive_has_no_reason(self) -> None:
        cat = load_catalog("python")
        assert cat is not None
        assert multi_boundary_reason(cat, "nosuch.thing") is None

    def test_mode_beats_the_other_sources(self) -> None:
        cat = load_catalog("python")
        assert cat is not None
        assert (
            multi_boundary_reason(cat, "builtins.open")
            == MULTI_BOUNDARY_REASON_MODE
        )

    def test_simultaneous_is_reported(self) -> None:
        cat = load_catalog("scala")
        assert cat is not None
        assert (
            multi_boundary_reason(cat, "scala.sys.process.Process.apply")
            == MULTI_BOUNDARY_REASON_SIMULTANEOUS
        )

    def test_undecidable_is_reported(self) -> None:
        cat = load_catalog("c")
        assert cat is not None
        assert (
            multi_boundary_reason(cat, "unistd.write")
            == BOUNDARY_RULING_UNDECIDABLE
        )

    def test_unruled_is_reported(self) -> None:
        """Built synthetically rather than read off a shipped catalogue.

        This asserted haskell's ``Control.Concurrent.STM.newTVar`` until
        INV-nular deleted that row as a false taint source. Pinning a RESOLVER
        branch to a live row makes the branch's test hostage to a catalogue
        decision it has nothing to do with — and the correct catalogue decision
        broke it. What must hold is that a row declaring ``unruled`` resolves to
        ``unruled``, which is true whether or not any shipped row does."""
        from hypergumbo_core.io_boundary import IoBoundaryCatalog, IoPrimitive

        cat = IoBoundaryCatalog(
            language="toy",
            primitives=[
                IoPrimitive(boundary="db_read", module="toy.cell", name="make",
                            kind="function", boundary_ruling=BOUNDARY_RULING_UNRULED),
                IoPrimitive(boundary="db_write", module="toy.cell", name="make",
                            kind="function", boundary_ruling=BOUNDARY_RULING_UNRULED),
            ],
        )
        assert (multi_boundary_reason(cat, "toy.cell.make")
                == BOUNDARY_RULING_UNRULED)


class TestTheDeclarationCannotBeMisspelled:
    """A silently-ignored unknown key is a documented landmine here.

    The catalogue loader reads row keys with ``entry.get(...)``, so a
    misspelled key is dropped without complaint — which for THIS field would
    mean a row that reads as ruled while resolving to nothing.
    """

    def test_an_unknown_ruling_value_is_refused(self, tmp_path: Path) -> None:
        from hypergumbo_core.io_boundary import load_overlay_catalog

        p = tmp_path / "bad.yaml"
        p.write_text(
            "language: python\nstatus: overlay\n"
            "fs_read:\n"
            "  - module: m\n    functions: [f]\n"
            "    boundary_ruling: undecidable_typo\n",
            encoding="utf-8",
        )
        with pytest.raises(Exception) as exc:
            load_overlay_catalog(p)
        assert "boundary_ruling" in str(exc.value)

    def test_a_valid_ruling_value_loads(self, tmp_path: Path) -> None:
        """POSITIVE CONTROL — the refusal above is about the VALUE, not the key."""
        from hypergumbo_core.io_boundary import load_overlay_catalog

        p = tmp_path / "ok.yaml"
        p.write_text(
            "language: python\nstatus: overlay\n"
            "fs_read:\n"
            "  - module: m\n    functions: [f]\n"
            f"    boundary_ruling: {BOUNDARY_RULING_UNDECIDABLE}\n",
            encoding="utf-8",
        )
        cat = load_overlay_catalog(p)
        assert any(
            pr.boundary_ruling == BOUNDARY_RULING_UNDECIDABLE
            for pr in cat.primitives
        )

    def test_rows_that_disagree_about_the_ruling_are_refused(
        self, tmp_path: Path
    ) -> None:
        """Same discipline ``simultaneous`` already enforces.

        The ruling is a property of the PRIMITIVE while its rows live in
        different YAML sections by construction, so a half-declared pair
        would be live or inert depending on which section a later editor
        happened to update — the row-order hazard wearing a new hat.
        """
        from hypergumbo_core.io_boundary import load_overlay_catalog

        # net_send/net_recv rather than fs_read/fs_write on purpose: the
        # latter pair IS the mode-discriminated signature, so the resolver
        # would answer "mode" before ever consulting the ruling and this
        # test would pass for the wrong reason.
        p = tmp_path / "split.yaml"
        p.write_text(
            "language: python\nstatus: overlay\n"
            "net_send:\n"
            "  - module: m\n    functions: [f]\n"
            f"    boundary_ruling: {BOUNDARY_RULING_UNDECIDABLE}\n"
            "net_recv:\n"
            "  - module: m\n    functions: [f]\n",
            encoding="utf-8",
        )
        cat = load_overlay_catalog(p)
        with pytest.raises(ValueError) as exc:
            multi_boundary_reason(cat, "m.f")
        assert "boundary_ruling" in str(exc.value)
