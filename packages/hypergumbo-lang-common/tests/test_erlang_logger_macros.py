# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-zihor: OTP's ?LOG_* macros are logging calls and produced no edge at all.

tree-sitter parses Erlang SOURCE, not preprocessed source, so a
``?LOG_DEBUG("...", [Secret])`` was invisible: no call edge, therefore no
catalogue match, therefore no logging sink. On rabbitmq that is **1,910 macro
uses against 183 direct `logger:` calls** — the analyzer saw 9% of the project's
logging surface.

MEASURED, and the measurement is why this is scoped to eight names rather than
built as a preprocessor. Expanding the eight kernel level macros across
rabbitmq source-to-source and running the six generic taint claims cold on
separate caches, `host-secret-no-logging` moves **68 -> 138 situations**
(93 -> 2028 collapsed source->sink pairs) while the other six claims are
**byte-identical**. So 1,910 macro sites convert to +70 reported situations —
**3.7%** — which is the item's own hypothesis confirmed: a recall hole no source
reaches is worth far less than its size suggests. Keyed on (source, caller)
rather than (source, sink, caller), **all 68 of the pre-expansion situations
survive**; 61 of them merely re-root their sink from `io:format` to `logger:*`.

THE VOCABULARY IS EIGHT NAMES, NOT A ``?LOG_`` PREFIX. rabbitmq also defines
``?LOG_DIR`` (a directory string), ``?LOG_PREFIX`` (a prefix string) and
``?LOG_EXCH_NAME`` (a binary). A prefix match would mint three logging sinks
out of string literals — the name-based-gate failure this subsystem keeps
paying for.

THE GATE IS THE INCLUDE, NOT THE NAME, and that is what makes it sound rather
than merely probable. A file only gets OTP's macros by including
``kernel/include/logger.hrl``. Measured across rabbitmq, emqx, ejabberd and
vernemq: gating on an include of any header named ``logger.hrl`` covers
**2267 of 2267** sites, because ejabberd reaches kernel's header through its own
same-named one. Gating on the literal ``include_lib("kernel/include/logger.hrl")``
covers 2266 and misses exactly that file.

WHY NOT A REAL PREPROCESSOR (the item's option (b)). Because macro NAMES
collide at a material rate — 87 of 693 function-like macros in emqx are defined
in more than one file, 21 of 124 in ejabberd — so a global name→body map is
ambiguous and correct expansion needs per-file include resolution. The eight
kernel names have the opposite property: they are **never** redefined anywhere
in six erlang repositories. That asymmetry is what licenses handling these
eight by name and refusing to generalise on the cheap.

WHAT THIS DELIBERATELY DOES NOT COVER, stated because the hole is bigger than
this item's title. emqx routes 1,233 logging sites through its own ``?SLOG``
family (which expands to ``logger:log``), and ejabberd 1,330 through
``?DEBUG``/``?INFO_MSG``/``?WARNING_MSG``/``?ERROR_MSG``/``?CRITICAL_MSG``
(which expand to ``?LOG_*``). Those are project-local macros and need the
include-resolving expander this change declines to build. OTP's eight names are
2,267 of roughly 3,530 macro-mediated logging sites in the corpus — about 64%.
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

    Measured: this is the difference between covering 2266 and 2267 of the
    corpus's OTP macro sites.
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
    """CONTROL. The path that already worked must keep working."""
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
    assert edge.meta.get("call_construct") == "macro"
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
