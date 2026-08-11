# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Python analyzer records the mode literal of a dual-classified call.

WHY THE ANALYZER AND NOT THE MATCHER. Whether ``open(p, "w")`` is a filesystem
READ or a filesystem WRITE is decided by an argument, and the argument exists
only in the AST. By the time the boundary tagger and the taint sink matcher run
they hold edges, not syntax — so if the analyzer does not record the mode, no
downstream consumer can recover it, and both were forced to guess. They guessed
in OPPOSITE directions: ``io-boundaries`` called every ``open()`` a read (real
writes invisible), the taint sink derivation called every ``open()`` a write
(24 of 35 distinct ``runtime-cli-no-host-fs`` violations were read-mode calls).

WHY NOT REUSE ``access_mode``. It is already stamped on these very edges, and
it is already ``"read"`` for ``open(p, "w")`` — it derives from the AST ROLE of
the reference, per ADR-0038, not from the call's arguments. Consolidating onto
it would have turned real writes into false NEGATIVES, which for a security
claim is the expensive direction. Measured before minting a new key, per the
axis discipline; ``TestAccessModeIsNotTheSameFact`` keeps that check live.

WHY STAMPED ONCE AT THE CALL SITE. ``_process_call`` has nine ``Edge.create``
sites; stamping at each is the "N places that should share one rule will drift"
shape this project keeps paying for. It is applied once, at the single
invocation, over the edges that call produced. And it keys on the exact
``ast.Call`` node rather than on a line number — ADR-0038's retired classifier
was line-granular, which is precisely why it stamped the reads on an
assignment's right-hand side as writes.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _open_edges(tmp_path: Path, source: str) -> list[object]:
    from hypergumbo_lang_mainstream.py import analyze_python

    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "app.py").write_text(source)
    analysis = analyze_python(repo)
    # ``:unresolved``, not ``:external_symbol`` — at analyzer stage the
    # builtin has not been through the linkers that mint the external
    # boundary node, and matching the post-link suffix silently found
    # nothing rather than failing loudly.
    return [
        e for e in analysis.edges
        if e.edge_type == "calls" and ":open:" in e.dst
    ]


def _sole_io_mode(tmp_path: Path, source: str) -> object:
    edges = _open_edges(tmp_path, source)
    assert len(edges) == 1, (
        f"fixture must produce exactly one open() call edge, got {len(edges)}"
    )
    return (edges[0].meta or {}).get("io_mode")  # type: ignore[attr-defined]


class TestModeLiteralIsRecorded:
    """The four call shapes that decide the boundary."""

    def test_default_mode_records_nothing(self, tmp_path: Path) -> None:
        """``open(p)`` carries no mode; the DEFAULT is applied downstream.

        Recording ``"r"`` here would be the analyzer inventing a token the
        source does not contain. Absence is the honest record, and
        ``resolve_mode_boundary(None)`` maps it to ``fs_read`` because the
        language documents that default.
        """
        assert _sole_io_mode(tmp_path, "def f(p):\n    return open(p)\n") is None

    def test_positional_write_mode(self, tmp_path: Path) -> None:
        assert _sole_io_mode(
            tmp_path, 'def f(p):\n    return open(p, "w")\n',
        ) == "w"

    def test_keyword_write_mode(self, tmp_path: Path) -> None:
        assert _sole_io_mode(
            tmp_path, 'def f(p):\n    return open(p, mode="a")\n',
        ) == "a"

    def test_positional_read_mode(self, tmp_path: Path) -> None:
        assert _sole_io_mode(
            tmp_path, 'def f(p):\n    return open(p, "rb")\n',
        ) == "rb"

    def test_computed_mode_records_nothing(self, tmp_path: Path) -> None:
        """``open(p, m)`` is ignorance, and ignorance licenses nothing.

        It must NOT be recorded as a write on suspicion — that would rebuild
        the false-positive population this change removes — and the downstream
        default sends it to ``fs_read``.
        """
        assert _sole_io_mode(
            tmp_path, "def f(p, m):\n    return open(p, m)\n",
        ) is None


class TestShapesThatCarryNoReadableMode:
    """Call shapes that must record nothing rather than guess.

    Each exercises a distinct early return in ``_io_mode_literal``; they are
    separated because collapsing them into one case would let a branch rot
    unnoticed behind a sibling that happens to take the same path.
    """

    def test_computed_keyword_mode(self, tmp_path: Path) -> None:
        assert _sole_io_mode(
            tmp_path, "def f(p, m):\n    return open(p, mode=m)\n",
        ) is None

    def test_callee_is_neither_a_name_nor_an_attribute(
        self, tmp_path: Path,
    ) -> None:
        """``d["open"](p, "w")`` — a subscript callee has no static name.

        The mode literal is right there, but nothing establishes that the
        callee IS ``builtins.open``, so recording ``"w"`` would attach a
        mode to a call that may not be a filesystem primitive at all.
        """
        from hypergumbo_lang_mainstream.py import analyze_python

        repo = tmp_path / "r"
        repo.mkdir()
        (repo / "app.py").write_text(
            'def f(d, p):\n    return d["open"](p, "w")\n',
        )
        analysis = analyze_python(repo)
        assert [
            e for e in analysis.edges if (e.meta or {}).get("io_mode")
        ] == []


class TestOnlyDiscriminatedNamesAreStamped:
    """The stamp is driven by the catalogue, not by a hardcoded name."""

    def test_a_non_dual_classified_call_is_untouched(
        self, tmp_path: Path,
    ) -> None:
        """``os.remove`` is fs_write only — no mode question, no stamp.

        If this ever carried ``io_mode`` the mechanism would be firing on
        the whole call graph rather than on the handful of rows that need it.
        """
        from hypergumbo_lang_mainstream.py import analyze_python

        repo = tmp_path / "r"
        repo.mkdir()
        (repo / "app.py").write_text(
            'import os\n\ndef f(p):\n    os.remove(p)\n',
        )
        analysis = analyze_python(repo)
        stamped = [
            e for e in analysis.edges
            if (e.meta or {}).get("io_mode") is not None
        ]
        assert stamped == []


class TestAccessModeIsNotTheSameFact:
    """Keeps the reason a new key was minted from quietly expiring.

    If a future ADR-0038 rebuild makes ``access_mode`` argument-aware, this
    test goes red and the two keys should be reconsidered for merging. Until
    then it documents, executably, that they encode different facts.
    """

    def test_access_mode_does_not_track_the_mode_argument(
        self, tmp_path: Path,
    ) -> None:
        edges = _open_edges(tmp_path, 'def f(p):\n    return open(p, "w")\n')
        assert len(edges) == 1
        meta = edges[0].meta or {}  # type: ignore[attr-defined]
        assert meta.get("io_mode") == "w"
        assert meta.get("access_mode") != "write"


class TestCatalogueParity:
    """Every dual-classified python name must be stampable by the analyzer.

    The failure mode is drift: someone adds a second boundary to a primitive
    in ``python.yaml`` and the analyzer silently keeps not recording its mode,
    so the new row is decided by declaration order forever. This asserts over
    the LIVE catalogue so tomorrow's entry fails here instead.
    """

    def test_every_dual_classified_python_name_is_handled(self) -> None:
        from hypergumbo_core.io_boundary import (
            load_catalog,
            mode_discriminated_names,
        )
        from hypergumbo_lang_mainstream.py import _MODE_ARG_POSITION

        for name in mode_discriminated_names(load_catalog("python")):
            assert name in _MODE_ARG_POSITION, (
                f"{name!r} is dual-classified in python.yaml but the analyzer "
                f"does not know where its mode argument sits"
            )
