# SPDX-License-Identifier: AGPL-3.0-or-later
"""One write is one sink, and a stream-object sink names its call (INV-hopib).

An io_primitives row may name a stream OBJECT (``sys.stderr``, ``os.Stderr``,
``System.out``) rather than a callable. Its taint sink is matched through the
``module_attr_ref`` edge the analyzer emits wherever the object is USED, so in
``print(k, file=sys.stderr)`` the stream is matched as a sink of its own, beside
the ``print`` that actually writes: two findings for one write, and one of them
names a sink that has no arguments and no receiver -- uncheckable against its
own record.

The analyzers now stamp ``attr_carrier`` on each attribute use: the call at
whose argument, keyword or receiver position the stream appeared, as
``<callee as spelled>@<call line>``. Taint then drops the stream's sink site
when EVERY use has a carrier and each carrier is itself a matched sink of the
same name, at the same caller and line, in the same zone -- the write is
reported once, by the call. A stream whose carrier is not a sink
(``json.dump(secret, sys.stdout)``) keeps its finding and names the carrier in
``sink_carriers``, which is what makes it checkable.

These tests pin the pieces with no analyzer in the loop; the verdicts a user
reads are pinned in the lang-mainstream package.
"""

from __future__ import annotations

from hypergumbo_core.axis_meta_keys import META_KEYS, per_call_site_keys
from hypergumbo_core.io_boundary import call_site_attr_carriers
from hypergumbo_core.taint import (
    TaintFlowFinding,
    TaintSink,
    _subsume_sink_sites,
    collapse_unadjudicated_flows,
)

_CALLER = "python:a.py:1-9:main:function"
_STREAM = "python:sys:0-0:sys.stderr:attribute"
_PRINT = "python:builtins:0-0:print:external_symbol"
_DUMP = "python:json:0-0:dump:external_symbol"


def _stream_sink(zone: str = "logging") -> TaintSink:
    return TaintSink(zone=zone, trust_level="untrusted", module="sys",
                     name="stderr", kind="attribute")


def _call_sink(name: str = "print", zone: str = "logging") -> TaintSink:
    return TaintSink(zone=zone, trust_level="untrusted", module="builtins",
                     name=name, kind="function")


def _subsume(sites, carriers, lines):
    index = {_CALLER: list(sites)}
    _subsume_sink_sites(index, carriers=carriers, site_lines=lines)
    return index[_CALLER]


class TestTheKey:
    def test_it_is_registered_per_call_site(self) -> None:
        assert "attr_carrier" in {s.name for s in META_KEYS}
        assert "attr_carrier" in per_call_site_keys()

    def test_the_reader_reads_both_spellings(self) -> None:
        assert call_site_attr_carriers({"attr_carrier": "print@7"}) == ("print@7",)
        assert call_site_attr_carriers(
            {"attr_carrier_values": [None, "json.dump@9", "print@7"]}
        ) == (None, "json.dump@9", "print@7")
        assert call_site_attr_carriers({}) == ()
        assert call_site_attr_carriers(None) == ()


class TestSubsumption:
    def test_a_stream_carried_by_a_sink_call_is_dropped(self) -> None:
        kept = _subsume(
            [(_STREAM, _stream_sink()), (_PRINT, _call_sink())],
            carriers={(_CALLER, _STREAM): ("print@7",)},
            lines={(_CALLER, _PRINT): [7]},
        )
        assert kept == [(_PRINT, _call_sink())]

    def test_the_call_keeps_its_own_finding(self) -> None:
        """THE CONTROL: only the stream goes; the write is still reported."""
        kept = _subsume(
            [(_PRINT, _call_sink()), (_STREAM, _stream_sink())],
            carriers={(_CALLER, _STREAM): ("print@7",)},
            lines={(_CALLER, _PRINT): [7]},
        )
        assert (_PRINT, _call_sink()) in kept

    def test_a_carrier_that_is_not_a_sink_keeps_the_stream(self) -> None:
        kept = _subsume(
            [(_STREAM, _stream_sink()), (_PRINT, _call_sink())],
            carriers={(_CALLER, _STREAM): ("json.dump@9",)},
            lines={(_CALLER, _PRINT): [7]},
        )
        assert (_STREAM, _stream_sink()) in kept

    def test_one_uncarried_use_keeps_the_stream(self) -> None:
        """``None`` is a use outside any call (``out = sys.stderr``); the
        stream may be written through it, so nothing vouches for dropping it."""
        kept = _subsume(
            [(_STREAM, _stream_sink()), (_PRINT, _call_sink())],
            carriers={(_CALLER, _STREAM): (None, "print@7")},
            lines={(_CALLER, _PRINT): [7]},
        )
        assert (_STREAM, _stream_sink()) in kept

    def test_one_non_sink_carrier_among_several_keeps_the_stream(self) -> None:
        kept = _subsume(
            [(_STREAM, _stream_sink()), (_PRINT, _call_sink()),
             (_DUMP, _call_sink("dump", zone="host_fs"))],
            carriers={(_CALLER, _STREAM): ("json.dump@9", "print@7")},
            lines={(_CALLER, _PRINT): [7], (_CALLER, _DUMP): [9]},
        )
        assert (_STREAM, _stream_sink()) in kept

    def test_the_carrier_must_be_on_the_same_line(self) -> None:
        kept = _subsume(
            [(_STREAM, _stream_sink()), (_PRINT, _call_sink())],
            carriers={(_CALLER, _STREAM): ("print@7",)},
            lines={(_CALLER, _PRINT): [12]},
        )
        assert (_STREAM, _stream_sink()) in kept

    def test_the_carrier_must_be_in_the_same_zone(self) -> None:
        """A different zone is a different claim: dropping the stream there
        would remove the only finding that claim has."""
        kept = _subsume(
            [(_STREAM, _stream_sink(zone="ipc")), (_PRINT, _call_sink())],
            carriers={(_CALLER, _STREAM): ("print@7",)},
            lines={(_CALLER, _PRINT): [7]},
        )
        assert (_STREAM, _stream_sink(zone="ipc")) in kept

    def test_an_uncarried_stream_is_untouched(self) -> None:
        kept = _subsume(
            [(_STREAM, _stream_sink()), (_PRINT, _call_sink())],
            carriers={}, lines={(_CALLER, _PRINT): [7]},
        )
        assert (_STREAM, _stream_sink()) in kept

    def test_the_receiver_form_matches_on_the_last_name(self) -> None:
        """``System.out.println(x)`` spells the callee through the stream; the
        carrier matches the sink by the called name, ``println``."""
        kept = _subsume(
            [(_STREAM, _stream_sink()), (_PRINT, _call_sink("println"))],
            carriers={(_CALLER, _STREAM): ("System.out.println@4",)},
            lines={(_CALLER, _PRINT): [4]},
        )
        assert kept == [(_PRINT, _call_sink("println"))]


class TestTheRecord:
    def _finding(self, carriers: tuple[str, ...], method: str = "structural") -> TaintFlowFinding:
        return TaintFlowFinding(
            taint_label="host_secret", source_symbol=_CALLER,
            source_primitive="environ", sink_symbol=_STREAM,
            sink_primitive="stderr", sink_zone="logging", path=[_CALLER],
            sanitized=False, confidence="approximate",
            source_module="os", sink_module="sys", analysis_method=method,
            sink_carriers=carriers,
        )

    def test_it_serializes(self) -> None:
        assert self._finding(("json.dump@9",)).to_dict()["sink_carriers"] == ["json.dump@9"]

    def test_a_collapsed_row_carries_the_union(self) -> None:
        (row,) = collapse_unadjudicated_flows([
            self._finding(("json.dump@9",)), self._finding(("log.SetOutput@3",)),
        ])
        assert row.sink_carriers == ("json.dump@9", "log.SetOutput@3")
