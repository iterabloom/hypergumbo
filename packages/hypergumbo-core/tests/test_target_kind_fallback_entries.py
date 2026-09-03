# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-minol: an abstaining stamp derives the entry the classifier selects.

ONE CATALOGUE, TWO CONSUMERS, ONE ANSWER. ``classify_call`` settles an
unstamped target-kind-gated primitive by INV-fatok's ``abstains_to`` row (else
the first declared row). The taint derivation marked every entry of such a
primitive ``requires_target_kind=<boundary>`` and the matcher admitted a gated
entry only on an exact stamp, so an ABSTENTION -- no stamp, an unknown kind,
collapsed sites that disagree -- dropped the primitive entirely. For c's
``fgets`` that equalled the classifier by luck (its fallback, ``fs_read``,
mints nothing); for go's ``bufio.NewScanner`` (fallback ``ipc_recv``) it
silently deleted the 19 unstamped sources on the six-repo corpus that
INV-bagok chose the fallback to KEEP -- alertmanager's TLS connection reader
and caddy's redirect listener among them.

WI-suhug generalises the gate to WRITE primitives, where the same clause would
have deleted every unstamped ``io.WriteString`` SINK: a false all-clear. So
the fallback is now carried on the entry itself (``abstention_fallback``), set
by the derivation from the same reordered row list the classifier reads, and
the matcher admits three things: an unconditional entry; a gated entry whose
boundary the stamp resolves to, in the entry's OWN direction (a source reads,
a sink writes); and, when the stamp abstains, the fallback entry alone.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core import taint as T
from hypergumbo_core.io_boundary import (
    IoBoundaryCatalog,
    IoPrimitive,
    _apply_abstention_targets,
    target_kind_fallback_boundaries,
)

_CAT_DIR = Path(T.__file__).parent / "io_primitives"


@pytest.fixture(scope="module")
def derived():
    return T._derive_auto_imports_from_io_primitives(_CAT_DIR)


def _match(derived, lang, dst, construct, kinds=(), *, sink=False):
    sources, sinks, ambiguous = derived
    index = T._build_callee_index(sinks[lang] if sink else sources[lang])
    return T._match_propagation_entry(
        index, dst, ambiguous.get(lang, frozenset()), construct,
        is_resolved=False, language=lang, io_target_kinds=tuple(kinds),
    )


def _source(derived, dst, kinds=(), *, lang="go", construct="function"):
    hit = _match(derived, lang, dst, construct, kinds)
    return None if hit is None else hit.source_boundary


def _sink(derived, dst, kinds=(), *, lang="go", construct="function"):
    hit = _match(derived, lang, dst, construct, kinds, sink=True)
    return None if hit is None else hit.zone


_SCANNER = "go:bufio:0-0:NewScanner:unresolved"
_READSTRING = "go:bufio:0-0:ReadString:unresolved"
_WRITESTRING = "go:io:0-0:WriteString:unresolved"
_FPRINTF = "go:fmt:0-0:Fprintf:unresolved"


class TestTheReadSideRepro:
    """The item's own repro, on the shipped go catalogue."""

    def test_an_unstamped_wrapper_mints_its_declared_fallback(self, derived):
        assert _source(derived, _SCANNER) == "ipc_recv"

    def test_a_stdin_stamp_still_mints(self, derived):
        assert _source(derived, _SCANNER, ["std_stream"]) == "ipc_recv"

    def test_a_file_stamp_still_mints_nothing(self, derived):
        assert _source(derived, _SCANNER, ["host_path"]) is None

    def test_a_network_stamp_mints_a_network_receive(self, derived):
        """WI-suhug's vocabulary: ``net_stream`` is the value INV-bagok waited on."""
        assert _source(derived, _SCANNER, ["net_stream"]) == "net_recv"

    def test_a_pipe_stamp_mints_an_ipc_receive(self, derived):
        assert _source(derived, _SCANNER, ["pipe"]) == "ipc_recv"

    def test_an_in_memory_stamp_reaches_the_fallback_and_the_mint_gate_refuses(
        self, derived,
    ):
        """Abstention here, refusal there: two gates, one home each."""
        assert _source(derived, _SCANNER, ["in_memory"]) == "ipc_recv"
        assert T._source_call_can_mint_taint(
            {"meta": {"io_target_kind": "in_memory"}},
        ) is False

    def test_disagreeing_sites_fall_back(self, derived):
        assert _source(derived, _SCANNER, ["std_stream", "host_path"]) == "ipc_recv"

    def test_a_read_whose_fallback_mints_nothing_still_mints_nothing(self, derived):
        """WI-vutav's asymmetry is untouched: ``abstains_to: fs_read`` derives no source."""
        assert _source(derived, _READSTRING, construct="method") is None
        assert _source(
            derived, _READSTRING, ["std_stream"], construct="method",
        ) == "ipc_recv"

    def test_the_derived_entries_carry_the_fallback(self, derived):
        sources, _, _ = derived
        by_boundary = {
            s.source_boundary: s for s in sources["go"]
            if s.module == "bufio" and s.name == "NewScanner"
        }
        assert by_boundary["ipc_recv"].requires_target_kind == "ipc_recv"
        assert by_boundary["ipc_recv"].abstention_fallback is True
        assert by_boundary["net_recv"].abstention_fallback is False
        read = {
            s.source_boundary: s for s in sources["go"]
            if s.module == "bufio.Reader" and s.name == "ReadString"
        }
        assert read["ipc_recv"].abstention_fallback is False


class TestTheWriteSideSinks:
    """WI-suhug: the writer decides, and the fallback is today's answer."""

    def test_unstamped_write_string_is_a_host_fs_sink(self, derived):
        assert _sink(derived, _WRITESTRING) == "host_fs"

    def test_unstamped_fprintf_is_a_logging_sink(self, derived):
        assert _sink(derived, _FPRINTF) == "logging"

    @pytest.mark.parametrize("kind,zone", [
        ("host_path", "host_fs"),
        ("std_stream", "logging"),
        ("pipe", "ipc"),
        ("net_stream", "network"),
    ])
    def test_a_stamp_selects_the_writers_zone(self, derived, kind, zone):
        assert _sink(derived, _WRITESTRING, [kind]) == zone
        assert _sink(derived, _FPRINTF, [kind]) == zone

    def test_an_in_memory_write_reaches_the_fallback_and_the_carry_gate_refuses(
        self, derived,
    ):
        assert _sink(derived, _FPRINTF, ["in_memory"]) == "logging"
        assert T._sink_call_can_carry_taint(
            {"meta": {"io_target_kind": "in_memory"}},
        ) is False

    def test_disagreeing_sites_fall_back(self, derived):
        assert _sink(derived, _WRITESTRING, ["pipe", "host_path"]) == "host_fs"

    def test_the_derived_sinks_carry_direction_and_fallback(self, derived):
        _, sinks, _ = derived
        rows = {
            s.zone: s for s in sinks["go"]
            if s.module == "io" and s.name == "WriteString"
        }
        assert set(rows) == {"host_fs", "ipc", "network", "logging"}
        assert {z: s.requires_target_kind for z, s in rows.items()} == {
            "host_fs": "fs_write", "ipc": "ipc_send",
            "network": "net_send", "logging": "logging",
        }
        assert [z for z, s in rows.items() if s.abstention_fallback] == ["host_fs"]


class TestEveryOtherGatedWriteIsUnchanged:
    """The generalised rule reaches c/cpp ``unistd.write`` and haskell's
    ``hPut*``; their unstamped answer is pinned to what it was, measured on
    dev de3538531b before the change (``host_fs``, the first-declared row)."""

    def test_c_write_still_falls_back_to_host_fs(self, derived):
        assert _sink(
            derived, "c:unistd:0-0:write:unresolved", lang="c",
        ) == "host_fs"

    def test_cpp_inherits_the_same_answer(self, derived):
        assert _sink(
            derived, "cpp:unistd:0-0:write:unresolved", lang="cpp",
        ) == "host_fs"

    def test_c_write_stamped_pipe_is_an_ipc_sink(self, derived):
        """WI-baran's landing zone: the seam is live for c the day it stamps."""
        assert _sink(
            derived, "c:unistd:0-0:write:unresolved", ["pipe"], lang="c",
        ) == "ipc"

    def test_haskell_hputstr_still_falls_back_to_host_fs(self, derived):
        assert _sink(
            derived, "haskell:System.IO:0-0:hPutStr:unresolved",
            lang="haskell", construct="application",
        ) == "host_fs"

    def test_haskell_hputstr_stamped_std_stream_is_logging(self, derived):
        assert _sink(
            derived, "haskell:System.IO:0-0:hPutStr:unresolved", ["std_stream"],
            lang="haskell", construct="application",
        ) == "logging"

    def test_an_unconditional_sink_is_untouched_by_any_stamp(self, derived):
        assert _sink(derived, "go:os:0-0:Remove:unresolved") == "host_fs"
        assert _sink(
            derived, "go:os:0-0:Remove:unresolved", ["in_memory"],
        ) == "host_fs"


class TestTheFallbackRowIsTheClassifiersRow:
    """Synthetic catalogue: the named row when declared, else the first."""

    @staticmethod
    def _catalog(abstains: str | None) -> IoBoundaryCatalog:
        rows = [
            IoPrimitive(
                boundary="logging", module="m", name="w", kind="function",
                abstains_to=abstains,
            ),
            IoPrimitive(
                boundary="fs_write", module="m", name="w", kind="function",
                abstains_to=abstains,
            ),
            IoPrimitive(
                boundary="fs_write", module="m", name="plain", kind="function",
            ),
        ]
        return IoBoundaryCatalog(
            language="x", status="provenance_declared",
            primitives=_apply_abstention_targets("x", rows),
        )

    def test_without_a_declaration_the_first_row_is_the_fallback(self):
        assert target_kind_fallback_boundaries(self._catalog(None)) == {
            ("m", "w", "function"): "logging",
        }

    def test_the_declaration_names_the_fallback(self):
        assert target_kind_fallback_boundaries(self._catalog("fs_write")) == {
            ("m", "w", "function"): "fs_write",
        }

    def test_a_single_boundary_primitive_has_no_fallback(self):
        assert ("m", "plain", "function") not in target_kind_fallback_boundaries(
            self._catalog(None),
        )
