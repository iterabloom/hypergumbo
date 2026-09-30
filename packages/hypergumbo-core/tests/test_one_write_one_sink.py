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

from typing import Any

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


class TestThePropagator:
    """The carrier travels from the edge into the propagator's sink index."""

    def _edges(self, carrier: str | None) -> list[dict]:
        attr = {"src": _CALLER, "dst": _STREAM, "type": "module_attr_ref", "line": 7}
        if carrier is not None:
            attr["meta"] = {"attr_carrier": carrier}
        return [
            {"src": _CALLER, "dst": "python:os:0-0:getenv:unresolved", "type": "calls",
             "line": 6, "is_resolved": False,
             "meta": {"evidence_type": "ast_call_direct"}},
            {"src": _CALLER, "dst": _PRINT, "type": "calls", "line": 7,
             "is_resolved": False, "meta": {"evidence_type": "ast_call_direct"}},
            attr,
        ]

    def _flows(self, carrier: str | None) -> list[TaintFlowFinding]:
        from hypergumbo_core.taint import TaintSource, propagate_taint_structural
        return propagate_taint_structural(
            self._edges(carrier),
            [TaintSource(taint_label="host_secret", module="os", name="getenv",
                         kind="function")],
            [_stream_sink(), _call_sink()],
            [],
        )

    def test_a_carried_stream_is_not_a_second_finding(self) -> None:
        sinks = {p for f in self._flows("print@7") for p in f.sink_primitives}
        assert sinks == {"builtins.print"}

    def test_an_uncarried_stream_still_is(self) -> None:
        """THE CONTROL: the same edges without the stamp keep both."""
        sinks = {p for f in self._flows(None) for p in f.sink_primitives}
        assert sinks == {"builtins.print", "sys.stderr"}

    def test_a_surviving_stream_names_its_carrier(self) -> None:
        (stream,) = [f for f in self._flows("json.dump@7")
                     if "sys.stderr" in f.sink_primitives]
        assert stream.sink_carriers == ("json.dump@7",)


def _carriers(parse, lang: str, source: str, imports: dict[str, str], **kinds) -> list:
    from hypergumbo_core.analyze.base import emit_module_attribute_refs
    from hypergumbo_core.ir import Span, Symbol

    caller = Symbol(id=f"{lang}:f:1-9:f:function", name="f", kind="function",
                    language=lang, path="f",
                    span=Span(start_line=1, end_line=9, start_col=0, end_col=0))
    edges: list = []
    emit_module_attribute_refs(
        parse(source), source.encode(), imports, caller, lang, edges,
        pass_id="t", run_id="t", **kinds,
    )
    return [(e.dst.split(":")[-2], (e.meta or {}).get("attr_carrier")) for e in edges]


def _parser(module: str):
    import importlib

    import tree_sitter

    grammar = importlib.import_module(module)
    parser = tree_sitter.Parser(tree_sitter.Language(grammar.language()))
    return lambda src: parser.parse(src.encode()).root_node


_GO_KINDS: dict[str, Any] = {
    "node_kinds": ("selector_expression",), "object_field_names": ("operand",),
    "property_field_names": ("field",), "call_node_kinds": ("call_expression",),
    "call_function_field_names": ("function",),
    "carrier_call_kinds": ("call_expression",),
}
_JS_KINDS: dict[str, Any] = {
    "node_kinds": ("member_expression",), "object_field_names": ("object",),
    "property_field_names": ("property",), "call_node_kinds": ("call_expression",),
    "call_function_field_names": ("function",),
    "carrier_call_kinds": ("call_expression", "new_expression"),
}
_JAVA_KINDS: dict[str, Any] = {
    "node_kinds": ("field_access",), "object_field_names": ("object",),
    "property_field_names": ("field",), "call_node_kinds": ("__never__",),
    "call_function_field_names": ("__unused__",),
    "carrier_call_kinds": ("method_invocation",),
    "carrier_receiver_fields": ("object",),
}


class TestTheHelperStamp:
    """``base.emit_module_attribute_refs`` stamps the carrier in each shape."""

    def test_an_argument(self) -> None:
        got = _carriers(_parser("tree_sitter_go"), "go",
                        'package m\nfunc f() {\n\tio.WriteString(os.Stderr, "x")\n}\n',
                        {"os": "os"}, **_GO_KINDS)
        assert got == [("os.Stderr", "io.WriteString@3")]

    def test_the_receiver_of_a_member_callee(self) -> None:
        got = _carriers(_parser("tree_sitter_javascript"), "javascript",
                        "function f() {\n  process.stdout.write(k);\n}\n",
                        {"process": "process"}, **_JS_KINDS)
        assert got == [("process.stdout", "process.stdout.write@2")]

    def test_the_receiver_field_of_the_call(self) -> None:
        got = _carriers(_parser("tree_sitter_java"), "java",
                        "class A { void f() {\n  System.out.println(k);\n} }\n",
                        {"System": "System"}, **_JAVA_KINDS)
        assert got == [("System.out", "System.out.println@2")]

    def test_a_use_outside_any_call_has_none(self) -> None:
        got = _carriers(_parser("tree_sitter_javascript"), "javascript",
                        "function f() {\n  const s = process.stdout;\n}\n",
                        {"process": "process"}, **_JS_KINDS)
        assert got == [("process.stdout", None)]

    def test_no_carrier_kinds_stamps_nothing(self) -> None:
        """THE CONTROL: a language that passes no carrier kinds is untouched."""
        kinds = {k: v for k, v in _GO_KINDS.items() if k != "carrier_call_kinds"}
        got = _carriers(_parser("tree_sitter_go"), "go",
                        'package m\nfunc f() {\n\tio.WriteString(os.Stderr, "x")\n}\n',
                        {"os": "os"}, **kinds)
        assert got == [("os.Stderr", None)]
