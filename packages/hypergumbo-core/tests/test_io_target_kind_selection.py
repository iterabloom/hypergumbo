# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for TARGET-KIND discrimination of dual-classified read primitives.

WHY THIS EXISTS, and why it is a second seam rather than a widening of the
mode one. ``select_by_mode`` settles a primitive whose boundary a MODE LITERAL
decides -- ``open(p, "w")``. It cannot settle ``fgets(buf, n, stdin)``, because
the discriminator there is not a literal the row can name: it is what the
STREAM ARGUMENT was bound from. That is WI-lipis's whole subject, and INV-bagok
states the invariant it violates -- "a catalogue row's io_boundary must be true
of every call site the row matches".

THE DIRECTION IS THE OPPOSITE OF THE GO CASE AND THAT CHANGES THE DEFAULT.
``go.yaml`` filed ``bufio.NewScanner`` as ``ipc_recv``, which MINTS, so a wrong
argument INVENTED a taint source and the fix removed false positives. ``c.yaml``
and ``haskell.yaml`` file their stream-takers as ``fs_read``, which mints
nothing, so a wrong argument LOSES a real one. Any change here therefore ADDS
findings and must be judged as new surface, which is why every abstention below
resolves to the DECLARED DEFAULT (the ``fs_read`` row) rather than to the
minting one.

ALL SITES MUST AGREE, and that is the mirror of the source-mint gate rather
than a copy of it. ``_source_call_can_mint_taint`` refuses on ANY non-minting
site because it REMOVES a finding and silencing a real receive on a different
site's evidence is the false-negative trade. Selection here ADDS one, so the
conservative quantifier flips: a collapsed edge whose sites disagree keeps
today's behaviour.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import (
    IoBoundaryCatalog,
    IoPrimitive,
    classify_call,
    _narrow_by_target_kind,
    load_catalog,
    resolve_target_kind_across_sites,
)


def _stream_catalog() -> IoBoundaryCatalog:
    """``fgets`` under fs_read AND ipc_recv, read first.

    Declaration ORDER is deliberate and mirrors ``c.yaml``: ``fs_read`` first,
    so an unstamped call keeps the boundary the catalogue has always given it.
    """
    return IoBoundaryCatalog(
        language="c",
        status="provenance_declared",
        primitives=[
            IoPrimitive("fs_read", "stdio", "fgets", "function"),
            IoPrimitive("ipc_recv", "stdio", "fgets", "function"),
            IoPrimitive("fs_read", "stdio", "fopen", "function"),
        ],
    )


class TestResolveTargetKindAcrossSites:
    """The quantifier, isolated from the catalogue."""

    def test_no_kinds_is_no_answer(self) -> None:
        assert resolve_target_kind_across_sites(()) is None

    def test_std_stream_resolves_to_ipc_recv(self) -> None:
        assert resolve_target_kind_across_sites(("std_stream",)) == "ipc_recv"

    def test_host_path_resolves_to_fs_read(self) -> None:
        assert resolve_target_kind_across_sites(("host_path",)) == "fs_read"

    def test_every_site_agreeing_resolves(self) -> None:
        kinds = ("std_stream", "std_stream")
        assert resolve_target_kind_across_sites(kinds) == "ipc_recv"

    def test_disagreeing_sites_abstain(self) -> None:
        """ADD direction: one stdin site must not reclassify a file site."""
        kinds = ("std_stream", "host_path")
        assert resolve_target_kind_across_sites(kinds) is None

    def test_unknown_kind_abstains(self) -> None:
        """The vocabulary having no opinion is not an answer."""
        assert resolve_target_kind_across_sites(("unresolved",)) is None

    def test_one_unknown_among_known_abstains(self) -> None:
        kinds = ("std_stream", "unresolved")
        assert resolve_target_kind_across_sites(kinds) is None

    def test_non_crossing_kind_abstains(self) -> None:
        """``in_memory`` crosses nothing; the MINT gate owns that, not this."""
        assert resolve_target_kind_across_sites(("in_memory",)) is None


class TestNarrowByTargetKind:
    """Dropping the losing rows, given the candidates.

    NARROWING, NOT SELECTING. A ``_narrow_by_target_kind`` twin was written and
    deleted: it could have no production caller without breaking composition
    with the mode seam, which is WI-famig's defect and was caught by its own
    uncovered line. What survives here is the list, and whatever selects from
    it afterwards falls back to the FIRST row -- which is why declaration order
    is pinned against the shipped catalogues below.
    """

    def test_no_candidates_stays_empty(self) -> None:
        assert _narrow_by_target_kind([], ("std_stream",)) == []

    def test_a_single_candidate_passes_through(self) -> None:
        """The overwhelming majority of primitives must not pay for this."""
        only = IoPrimitive("ipc_recv", "bufio", "NewScanner", "function")
        assert _narrow_by_target_kind([only], ("host_path",)) == [only]

    def test_std_stream_keeps_only_the_ipc_recv_row(self) -> None:
        cands = [
            IoPrimitive("fs_read", "stdio", "fgets", "function"),
            IoPrimitive("ipc_recv", "stdio", "fgets", "function"),
        ]
        kept = _narrow_by_target_kind(cands, ("std_stream",))
        assert [c.boundary for c in kept] == ["ipc_recv"]

    def test_host_path_keeps_only_the_fs_read_row(self) -> None:
        cands = [
            IoPrimitive("fs_read", "stdio", "fgets", "function"),
            IoPrimitive("ipc_recv", "stdio", "fgets", "function"),
        ]
        kept = _narrow_by_target_kind(cands, ("host_path",))
        assert [c.boundary for c in kept] == ["fs_read"]

    def test_no_kinds_keeps_every_row_in_declared_order(self) -> None:
        """THE ORDERING GATE. An unstamped call must classify as it always did."""
        cands = [
            IoPrimitive("fs_read", "stdio", "fgets", "function"),
            IoPrimitive("ipc_recv", "stdio", "fgets", "function"),
        ]
        kept = _narrow_by_target_kind(cands, ())
        assert [c.boundary for c in kept] == ["fs_read", "ipc_recv"]

    def test_an_ungated_bucket_is_returned_untouched(self) -> None:
        """Two unrelated primitives sharing a short name settle nothing."""
        cands = [
            IoPrimitive("fs_read", "a", "read", "function"),
            IoPrimitive("net_recv", "b", "read", "method"),
        ]
        kept = _narrow_by_target_kind(cands, ("std_stream",))
        assert kept == cands

    def test_simultaneous_primitive_is_not_narrowed(self) -> None:
        """Both rows are true at once; there is nothing to discriminate."""
        cands = [
            IoPrimitive("fs_read", "m", "both", "function", simultaneous=True),
            IoPrimitive("ipc_recv", "m", "both", "function", simultaneous=True),
        ]
        assert _narrow_by_target_kind(cands, ("std_stream",)) == cands

    def test_mode_discriminated_pair_is_left_to_the_mode_seam(self) -> None:
        """``fs_write`` is not a read boundary, so this seam must not touch it.

        Without this, a ``host_path`` stamp on ``open`` would drop the
        ``fs_write`` row and undo INV-rusof's fix.
        """
        cands = [
            IoPrimitive("fs_read", "builtins", "open", "function"),
            IoPrimitive("fs_write", "builtins", "open", "function"),
        ]
        assert _narrow_by_target_kind(cands, ("host_path",)) == cands
        assert _narrow_by_target_kind(cands, ("std_stream",)) == cands

    def test_a_three_boundary_primitive_narrows_to_the_wanted_row(self) -> None:
        """``unistd.read`` is fs_read + ipc_recv + net_recv (INV-vaduk shape 3)."""
        cands = [
            IoPrimitive("fs_read", "unistd", "read", "function"),
            IoPrimitive("ipc_recv", "unistd", "read", "function"),
            IoPrimitive("net_recv", "unistd", "read", "function"),
        ]
        kept = _narrow_by_target_kind(cands, ("std_stream",))
        assert [c.boundary for c in kept] == ["ipc_recv"]


class TestEndToEndThroughClassifyCall:
    """The predicate is inert until every call site passes it (c's ``fopen``)."""

    def _catalogs(self) -> dict[str, IoBoundaryCatalog]:
        return {"c": _stream_catalog()}

    def test_stamped_std_stream_classifies_ipc_recv(self) -> None:
        got = classify_call(
            self._catalogs(),
            "c:external:0-0:fgets:external_symbol",
            {"io_target_kind": "std_stream"},
        )
        assert got is not None and got.boundary == "ipc_recv"

    def test_stamped_host_path_classifies_fs_read(self) -> None:
        got = classify_call(
            self._catalogs(),
            "c:external:0-0:fgets:external_symbol",
            {"io_target_kind": "host_path"},
        )
        assert got is not None and got.boundary == "fs_read"

    def test_unstamped_keeps_fs_read(self) -> None:
        got = classify_call(
            self._catalogs(),
            "c:external:0-0:fgets:external_symbol",
            {},
        )
        assert got is not None and got.boundary == "fs_read"

    def test_collapsed_values_key_is_read(self) -> None:
        """INV-vukiv: a disagreeing collapse moves to ``io_target_kind_values``."""
        got = classify_call(
            self._catalogs(),
            "c:external:0-0:fgets:external_symbol",
            {"io_target_kind_values": ["std_stream", "std_stream"]},
        )
        assert got is not None and got.boundary == "ipc_recv"

    def test_collapsed_disagreement_keeps_the_default(self) -> None:
        got = classify_call(
            self._catalogs(),
            "c:external:0-0:fgets:external_symbol",
            {"io_target_kind_values": ["std_stream", "host_path"]},
        )
        assert got is not None and got.boundary == "fs_read"


class TestShippedCatalogues:
    """The rows themselves, so a YAML edit cannot silently undo this."""

    @pytest.mark.parametrize("lang,module,name", [
        ("c", "stdio", "fgets"),
        ("c", "stdio", "fscanf"),
        ("c", "stdio", "fread"),
        ("c", "stdio", "getc"),
        ("c", "stdio", "fgetc"),
        ("haskell", "System.IO", "hGetLine"),
        ("haskell", "System.IO", "hGetContents"),
        ("haskell", "System.IO", "hGetChar"),
        # WI-vutav: the read one binding after go's wrapper.
        ("go", "bufio.Reader", "ReadString"),
        ("go", "bufio.Reader", "Read"),
        ("go", "bufio.Scanner", "Scan"),
        ("go", "bufio.Scanner", "Text"),
        ("go", "bufio.Scanner", "Bytes"),
    ])
    def test_stream_taker_is_declared_under_both(
        self, lang: str, module: str, name: str,
    ) -> None:
        cat = load_catalog(lang)
        boundaries = {
            p.boundary for p in cat.primitives
            if p.module == module and p.name == name
        }
        assert {"fs_read", "ipc_recv"} <= boundaries

    @pytest.mark.parametrize("lang,module,name", [
        ("c", "stdio", "fgets"),
        ("haskell", "System.IO", "hGetLine"),
        ("go", "bufio.Reader", "ReadString"),
        ("go", "bufio.Scanner", "Scan"),
    ])
    def test_fs_read_is_declared_first(
        self, lang: str, module: str, name: str,
    ) -> None:
        """The DEFAULT for an unstamped call, pinned where it is decided.

        ``_narrow_by_target_kind`` abstains to ``candidates[0]``, so declaration
        order IS the default. Reordering the YAML would make every unstamped
        stream read an ``ipc_recv`` -- a corpus-wide false positive -- and this
        is the gate that catches it.
        """
        cat = load_catalog(lang)
        rows = [
            p for p in cat.primitives
            if p.module == module and p.name == name
        ]
        assert rows[0].boundary == "fs_read"
