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
    _READ_TARGET_KIND_BOUNDARY,
    _WRITE_TARGET_KIND_BOUNDARY,
    classify_call,
    _narrow_by_target_kind,
    _target_kind_discriminated_keys,
    _target_kind_gated_directions,
    load_catalog,
    resolve_target_kind_across_sites,
    write_boundary_for_target_kind,
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


class TestTheWriteDirection:
    """WI-suhug: the same seam, pointed at the WRITER.

    ``fmt.Fprintf(w, ...)`` and ``io.WriteString(w, s)`` cross whatever ``w``
    is, exactly as ``bufio.NewScanner(r)`` reads whatever ``r`` is, and go.yaml
    gave each a fixed boundary (measurement 0012's vacuous class: a write to a
    child's ``StdinPipe`` reported as a host-filesystem crossing). The
    direction is the ASKER's, so a second map answers writes; the two maps
    share one key vocabulary so a kind one direction knows and the other does
    not is a drift, not a feature.
    """

    def test_host_path_resolves_to_fs_write(self) -> None:
        assert resolve_target_kind_across_sites(
            ["host_path"], direction="write",
        ) == "fs_write"

    def test_std_stream_resolves_to_logging(self) -> None:
        """WI-dutah: terminal output is logging, not IPC."""
        assert resolve_target_kind_across_sites(
            ["std_stream"], direction="write",
        ) == "logging"

    def test_pipe_resolves_to_ipc_send(self) -> None:
        assert resolve_target_kind_across_sites(
            ["pipe"], direction="write",
        ) == "ipc_send"

    def test_net_stream_resolves_to_net_send(self) -> None:
        assert resolve_target_kind_across_sites(
            ["net_stream"], direction="write",
        ) == "net_send"

    def test_the_read_direction_learned_the_new_kinds_too(self) -> None:
        assert resolve_target_kind_across_sites(["pipe"]) == "ipc_recv"
        assert resolve_target_kind_across_sites(["net_stream"]) == "net_recv"

    def test_non_crossing_kinds_abstain_in_both_directions(self) -> None:
        for kind in ("in_memory", "null_device"):
            assert resolve_target_kind_across_sites(
                [kind], direction="write",
            ) is None
            assert write_boundary_for_target_kind(kind) == (True, None)

    def test_an_unknown_kind_is_unknown_in_both_directions(self) -> None:
        assert write_boundary_for_target_kind("unresolved") == (False, None)
        assert resolve_target_kind_across_sites(
            ["host_path", "unresolved"], direction="write",
        ) is None

    def test_the_two_maps_share_one_key_vocabulary(self) -> None:
        assert set(_READ_TARGET_KIND_BOUNDARY) == set(_WRITE_TARGET_KIND_BOUNDARY)


def _writer_catalog() -> IoBoundaryCatalog:
    """``io.WriteString`` under the four write boundaries, fs_write first."""
    rows = [
        IoPrimitive(boundary=b, module="io", name="WriteString", kind="function")
        for b in ("fs_write", "ipc_send", "net_send", "logging")
    ]
    rows.append(IoPrimitive(
        boundary="fs_write", module="os", name="Remove", kind="function",
    ))
    return IoBoundaryCatalog(
        language="go", status="provenance_declared", primitives=rows,
    )


class TestNarrowingAWriter:
    def _writer_rows(self) -> list[IoPrimitive]:
        return _writer_catalog().primitives[:4]

    def test_a_pipe_keeps_only_the_ipc_send_row(self) -> None:
        got = _narrow_by_target_kind(self._writer_rows(), ["pipe"])
        assert [p.boundary for p in got] == ["ipc_send"]

    def test_an_abstention_keeps_every_row_in_declared_order(self) -> None:
        order = ["fs_write", "ipc_send", "net_send", "logging"]
        for kinds in (None, [], ["in_memory"], ["pipe", "host_path"]):
            got = _narrow_by_target_kind(self._writer_rows(), kinds)
            assert [p.boundary for p in got] == order

    def test_a_kind_is_answered_in_the_primitives_own_direction(self) -> None:
        """``std_stream`` is ``ipc_recv`` for a reader and ``logging`` for a writer."""
        got = _narrow_by_target_kind(self._writer_rows(), ["std_stream"])
        assert [p.boundary for p in got] == ["logging"]

    def test_an_ungated_sibling_in_the_bucket_is_untouched(self) -> None:
        rows = _writer_catalog().primitives
        got = _narrow_by_target_kind(rows, ["net_stream"])
        assert [(p.name, p.boundary) for p in got] == [
            ("WriteString", "net_send"), ("Remove", "fs_write"),
        ]

    def test_end_to_end_through_classify_call(self) -> None:
        cats = {"go": _writer_catalog()}
        dst = "go:io:0-0:WriteString:unresolved"
        stamped = classify_call(cats, dst, {"io_target_kind": "net_stream"})
        assert stamped is not None and stamped.boundary == "net_send"
        plain = classify_call(cats, dst, {})
        assert plain is not None and plain.boundary == "fs_write"
        file = classify_call(cats, dst, {"io_target_kind": "host_path"})
        assert file is not None and file.boundary == "fs_write"

    def test_builtins_open_is_still_the_mode_seams(self) -> None:
        """fs_read + fs_write is ONE of each direction, so neither gates it."""
        rows = [
            IoPrimitive(boundary=b, module="builtins", name="open", kind="function")
            for b in ("fs_read", "fs_write")
        ]
        assert _target_kind_discriminated_keys(rows) == frozenset()
        assert _target_kind_gated_directions(rows) == {}

    def test_each_gated_key_reports_its_direction(self) -> None:
        rows = _writer_catalog().primitives + [
            IoPrimitive(boundary=b, module="stdio", name="fgets", kind="function")
            for b in ("fs_read", "ipc_recv")
        ]
        assert _target_kind_gated_directions(rows) == {
            ("io", "WriteString", "function"): "write",
            ("stdio", "fgets", "function"): "read",
        }

    def test_a_primitive_gated_in_both_directions_is_refused(self) -> None:
        """``io.Copy`` rowed as fs_read+ipc_recv AND fs_write+ipc_send: one
        stamp cannot say which direction it names. Refused, not guessed."""
        rows = [
            IoPrimitive(boundary=b, module="io", name="Copy", kind="function")
            for b in ("fs_read", "ipc_recv", "fs_write", "ipc_send")
        ]
        with pytest.raises(ValueError, match="both"):
            _target_kind_gated_directions(rows)

    def test_the_loader_refuses_it_too(self) -> None:
        data = {
            "language": "probe", "status": "in_progress",
            "fs_read": [{"module": "io", "functions": ["Copy"]}],
            "ipc_recv": [{"module": "io", "functions": ["Copy"]}],
            "fs_write": [{"module": "io", "functions": ["Copy"]}],
            "ipc_send": [{"module": "io", "functions": ["Copy"]}],
        }
        with pytest.raises(ValueError, match="both"):
            IoBoundaryCatalog._from_dict(data)

    def test_a_simultaneous_primitive_is_not_gated_in_either_direction(self) -> None:
        rows = [
            IoPrimitive(
                boundary=b, module="p", name="w", kind="function", simultaneous=True,
            )
            for b in ("fs_write", "ipc_send")
        ]
        assert _target_kind_gated_directions(rows) == {}


class TestShippedWriteRows:
    """The go rows themselves, so a YAML edit cannot silently undo this."""

    @pytest.mark.parametrize("module,name", [
        ("io", "WriteString"),
        ("fmt", "Fprint"), ("fmt", "Fprintln"), ("fmt", "Fprintf"),
    ])
    def test_the_writer_is_declared_under_every_write_boundary(
        self, module: str, name: str,
    ) -> None:
        rows = [
            p for p in load_catalog("go").primitives
            if p.module == module and p.name == name
        ]
        assert {r.boundary for r in rows} == {
            "fs_write", "ipc_send", "net_send", "logging",
        }
        assert {r.boundary_ruling for r in rows} == {"call_site_undecidable"}

    @pytest.mark.parametrize("module,name,fallback", [
        ("io", "WriteString", "fs_write"),
        ("fmt", "Fprint", "logging"),
        ("fmt", "Fprintln", "logging"),
        ("fmt", "Fprintf", "logging"),
    ])
    def test_the_fallback_is_todays_answer(
        self, module: str, name: str, fallback: str,
    ) -> None:
        """INV-fatok: an unstamped call classifies exactly as before the rows."""
        rows = [
            p for p in load_catalog("go").primitives
            if p.module == module and p.name == name
        ]
        assert rows[0].boundary == fallback
        assert {r.abstains_to for r in rows} == {fallback}

    @pytest.mark.parametrize("name", ["Print", "Println", "Printf"])
    def test_the_unconditional_printers_stay_single_row(self, name: str) -> None:
        rows = [
            p for p in load_catalog("go").primitives
            if p.module == "fmt" and p.name == name
        ]
        assert [r.boundary for r in rows] == ["logging"]
        assert rows[0].boundary_ruling is None

    @pytest.mark.parametrize("module,name", [
        ("bufio", "NewScanner"), ("bufio", "NewReader"),
        ("bufio.Reader", "ReadString"), ("bufio.Scanner", "Scan"),
    ])
    def test_the_readers_gained_the_network_row_the_vocabulary_now_reaches(
        self, module: str, name: str,
    ) -> None:
        """INV-bagok's own note withheld this row until ``net_stream`` existed."""
        rows = [
            p for p in load_catalog("go").primitives
            if p.module == module and p.name == name
        ]
        assert {r.boundary for r in rows} == {"fs_read", "ipc_recv", "net_recv"}
        assert len({r.abstains_to for r in rows}) == 1

    def test_io_copy_is_deferred_and_still_single_row(self) -> None:
        """A mode-seam pair (fs_read + fs_write), deferred on the row's note."""
        rows = [
            p for p in load_catalog("go").primitives
            if p.module == "io" and p.name == "Copy"
        ]
        assert [r.boundary for r in rows] == ["fs_read"]
