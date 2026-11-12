# SPDX-License-Identifier: AGPL-3.0-or-later
"""OTP's ``?LOG_*`` level macros are logging calls and must produce call edges.

tree-sitter parses Erlang SOURCE, not preprocessed source, so a
``?LOG_DEBUG("...", [Secret])`` has no call node: left alone it yields no call
edge, therefore no catalogue match, therefore no logging sink. In Erlang
projects these macros, not direct ``logger:`` calls, carry most of the logging
surface. The analyzer turns each into a ``calls`` edge to
``logger:<level>``; these tests pin the rules that keep that sound.

THE VOCABULARY IS EIGHT NAMES, NOT A ``?LOG_`` PREFIX. Projects define their
own ``?LOG_*`` macros that are not logging calls -- rabbitmq has ``?LOG_DIR``
(a directory string), ``?LOG_PREFIX`` and ``?LOG_EXCH_NAME`` (a binary). A
prefix match would mint logging sinks out of string literals.

THE GATE IS THE INCLUDE, NOT THE NAME, and that is what makes it sound rather
than merely probable: a file only gets OTP's macros by including a header
named ``logger.hrl``. The gate accepts any include path ending in
``logger.hrl``, not only the literal
``include_lib("kernel/include/logger.hrl")``, because a project can reach
kernel's header through its own same-named one (ejabberd does).

WHY A NAME TABLE AND NOT A PREPROCESSOR. User-defined function-like macro
names are redefined in more than one file often enough that a global
name->body map is ambiguous; correct expansion needs per-file include
resolution. The eight kernel names are never redefined, which is what licenses
handling them by name.

WHAT THIS DELIBERATELY DOES NOT COVER. Project-local logging macros -- emqx's
``?SLOG`` family (expanding to ``logger:log``), ejabberd's
``?DEBUG``/``?INFO_MSG``/``?WARNING_MSG``/``?ERROR_MSG``/``?CRITICAL_MSG``
(expanding to ``?LOG_*``) -- reach a logger only through include resolution,
which the analyzer does not do, so their sites still produce no logging edge.

(history: INV-zihor)
"""

from pathlib import Path

import pytest

from hypergumbo_lang_common.erlang import (
    OTP_LOGGER_LEVEL_MACROS,
    analyze_erlang,
)

_KERNEL_INCLUDE = '-include_lib("kernel/include/logger.hrl").'


def _write(tmp: Path, name: str, body: str) -> None:
    (tmp / name).write_text(body, encoding="utf-8")


def _seen_call_confidence(tmp: Path) -> float:
    """Confidence of a literal ``logger:info(...)`` in the same shape."""
    (tmp / "zz_ref.erl").write_text(f"""
-module(zz_ref).
{_KERNEL_INCLUDE}
-export([go/0]).
go() ->
    logger:info("m", []),
    ok.
""", encoding="utf-8")
    result = analyze_erlang(tmp)
    ref = [e for e in result.edges
           if e.src.startswith("erlang:zz_ref.erl") and ":logger:" in e.dst]
    return ref[0].confidence


def _logger_edges(tmp: Path) -> list:
    result = analyze_erlang(tmp)
    assert not result.skipped
    return [
        e for e in result.edges
        if e.edge_type == "calls" and ":logger:" in e.dst
    ]


def test_a_gated_macro_use_reaches_logger(tmp_path: Path) -> None:
    _write(tmp_path, "a.erl", f"""
-module(a).
{_KERNEL_INCLUDE}
-export([leak/0]).
leak() ->
    S = os:getenv("API_KEY"),
    ?LOG_DEBUG("secret ~tp", [S]),
    ok.
""")
    edges = _logger_edges(tmp_path)
    assert len(edges) == 1
    assert edges[0].dst.startswith("erlang:logger:0-0:debug:")


def test_every_level_maps_to_its_own_logger_function(tmp_path: Path) -> None:
    """Derived from the map, so a new level cannot be added untested."""
    uses = "\n".join(
        f'    ?{macro}("m", []),' for macro in sorted(OTP_LOGGER_LEVEL_MACROS)
    )
    _write(tmp_path, "b.erl", f"""
-module(b).
{_KERNEL_INCLUDE}
-export([go/0]).
go() ->
{uses}
    ok.
""")
    got = {e.dst.split(":")[3] for e in _logger_edges(tmp_path)}
    assert got == set(OTP_LOGGER_LEVEL_MACROS.values())


def test_without_the_include_nothing_is_emitted(tmp_path: Path) -> None:
    """THE GATE. Same macro, no logger.hrl — the name alone proves nothing."""
    _write(tmp_path, "c.erl", """
-module(c).
-export([go/0]).
go() ->
    ?LOG_DEBUG("m", []),
    ok.
""")
    assert _logger_edges(tmp_path) == []


def test_a_project_local_logger_hrl_also_gates(tmp_path: Path) -> None:
    """ejabberd's shape: its own logger.hrl includes kernel's.

    A gate on the literal ``kernel/include/logger.hrl`` include would miss
    this file.
    """
    _write(tmp_path, "d.erl", """
-module(d).
-include("logger.hrl").
-export([go/0]).
go() ->
    ?LOG_WARNING("m", []),
    ok.
""")
    assert len(_logger_edges(tmp_path)) == 1


def test_a_non_level_log_macro_is_not_a_logging_call(tmp_path: Path) -> None:
    """CONTROL on the vocabulary. ``?LOG_DIR`` is a directory string."""
    _write(tmp_path, "e.erl", f"""
-module(e).
{_KERNEL_INCLUDE}
-define(LOG_DIR, "/var/tmp/tracing/").
-export([go/0]).
go() ->
    file:write_file(?LOG_DIR, <<"x">>),
    ok.
""")
    assert _logger_edges(tmp_path) == []


def test_a_direct_logger_call_is_unchanged(tmp_path: Path) -> None:
    """CONTROL. A literal ``logger:`` call keeps its ``ast_call``/``remote`` edge."""
    _write(tmp_path, "f.erl", f"""
-module(f).
{_KERNEL_INCLUDE}
-export([go/0]).
go() ->
    logger:info("m", []),
    ok.
""")
    edges = _logger_edges(tmp_path)
    assert len(edges) == 1
    assert edges[0].dst.startswith("erlang:logger:0-0:info:")
    assert edges[0].evidence_type == "ast_call"
    assert edges[0].meta.get("call_construct") == "remote"


def test_the_edge_discloses_that_it_came_from_an_expansion(tmp_path: Path) -> None:
    """A macro use is NOT a literal call, and the edge must say so.

    Reading ``evidence_type`` is how a consumer tells a call it saw from a
    call it inferred; stamping ``ast_call`` here would assert a fidelity the
    analyzer does not have, since tree-sitter never expanded anything.
    """
    _write(tmp_path, "g.erl", f"""
-module(g).
{_KERNEL_INCLUDE}
-export([go/0]).
go() ->
    ?LOG_ERROR("m", []),
    ok.
""")
    edge = _logger_edges(tmp_path)[0]
    assert edge.evidence_type == "macro_expansion"
    # And ONLY there: no call construct was parsed at a macro use, so the edge
    # carries none rather than restating the pathway as ``macro`` (WI-dapap).
    assert "call_construct" not in (edge.meta or {})
    # And it is priced BELOW a call the analyzer actually saw, because the
    # expansion is an extra inference step on top of the same unresolved dst.
    assert edge.confidence < _seen_call_confidence(tmp_path)


def test_a_macro_use_outside_any_function_emits_nothing(tmp_path: Path) -> None:
    """No enclosing function means no caller to attribute the call to."""
    _write(tmp_path, "h.erl", f"""
-module(h).
{_KERNEL_INCLUDE}
-define(WRAP(F, A), ?LOG_INFO(F, A)).
-export([go/0]).
go() ->
    ok.
""")
    assert _logger_edges(tmp_path) == []


def test_the_level_map_is_exactly_otps_eight(tmp_path: Path) -> None:
    """The vocabulary is pinned because widening it is the unsafe direction.

    Every name here is a level in ``kernel/include/logger.hrl``. A ninth entry
    would have to be justified against the same evidence: never redefined in
    the corpus, and reached only through that header.
    """
    assert set(OTP_LOGGER_LEVEL_MACROS) == {
        "LOG_EMERGENCY", "LOG_ALERT", "LOG_CRITICAL", "LOG_ERROR",
        "LOG_WARNING", "LOG_NOTICE", "LOG_INFO", "LOG_DEBUG",
    }
    assert all(v == k.split("_", 1)[1].lower()
               for k, v in OTP_LOGGER_LEVEL_MACROS.items())
