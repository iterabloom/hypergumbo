# SPDX-License-Identifier: AGPL-3.0-or-later
"""A source value lost through an unfollowed construct withholds a clean verdict (INV-nohob).

``find_unfollowed_escapes`` runs the §3a walk from each source site with the
source function's ``unfollowed_construct``-stamped call lines as targets; a
reached site is an escape. ``verify_claims`` then withholds every clean taint
verdict on the escaping label, scoped like flows (production sources only by
default). torsocks' getenv'd SOCKS5 password, copied into a global by
``conf_file_set_socks5_pass(password, &tsocks_config)`` and sent from another
function, read ``confirmed_with_caveats`` before this.
"""
from __future__ import annotations

from hypergumbo_core.cfg import DdgEdge
from hypergumbo_core.io_boundary import call_site_unfollowed_constructs
from hypergumbo_core.taint import (
    TaintSource,
    UnfollowedEscape,
    find_unfollowed_escapes,
)
from hypergumbo_core.verify_claims import (
    ClaimVerdict,
    _withhold_for_unfollowed_escapes,
)

SOURCES = [TaintSource(
    taint_label="host_secret", module="stdlib", name="getenv", kind="function",
)]
_FN = "c:src/init.c:1-9:init:function"
_TEST_FN = "c:tests/test_init.c:1-9:t:function"


def _call(dst: str, line: int, construct: str | None = None, src: str = _FN) -> dict:
    edge: dict = {"src": src, "dst": dst, "is_resolved": False, "type": "calls", "line": line}
    if construct is not None:
        edge["meta"] = {"unfollowed_construct": construct}
    return edge


def _ddg(*edges: tuple[str, int, int], fn: str = _FN) -> list[DdgEdge]:
    return [
        DdgEdge(variable=v, def_block="bb_0", def_line=d, use_block="bb_0", use_line=u, symbol_id=fn)
        for v, d, u in edges
    ]


_GETENV = "c:stdlib.h:0-0:getenv:unresolved"
_SET = "c:src/conf.c:3-6:set_pass:function"


def test_a_source_reaching_a_stamped_call_is_an_escape() -> None:
    """``p = getenv(..)`` at 2, ``set_pass(p, &g)`` at 4."""
    escapes = find_unfollowed_escapes(
        _ddg(("p", 2, 4)),
        [_call(_GETENV, 2), _call(_SET, 4, "out_parameter_call")],
        SOURCES, language="c",
        stmt_defuse={_FN: [(2, ("p",), ()), (4, (), ("p",))]},
    )
    assert escapes == [UnfollowedEscape(
        "host_secret", _FN, "stdlib.getenv", 4, "out_parameter_call")]


def test_a_construct_on_the_source_line_is_reached_by_construction() -> None:
    """``strcpy(g, getenv("K"))``: the source defines no variable to seed."""
    escapes = find_unfollowed_escapes(
        _ddg(("x", 1, 3)),
        [_call(_GETENV, 2), _call("c:string.h:0-0:strcpy:unresolved", 2, "non_local_store")],
        SOURCES, language="c",
    )
    assert [e.construct for e in escapes] == ["non_local_store"]


def test_no_escape_when_the_value_does_not_reach_the_stamp() -> None:
    """The stamped call takes ``q``, not the tainted ``p``; a site in ANOTHER
    function is not this source's; and an unstamped run finds nothing."""
    other = "c:src/other.c:1-5:other:function"
    edges = [
        _call(_GETENV, 2),
        _call(_SET, 5, "out_parameter_call"),
        _call(_SET, 3, "out_parameter_call", src=other),
    ]
    assert find_unfollowed_escapes(
        _ddg(("p", 2, 3), ("q", 1, 5)), edges, SOURCES, language="c",
        stmt_defuse={_FN: [(2, ("p",), ()), (3, (), ("p",)), (5, (), ("q",))]},
    ) == []
    assert find_unfollowed_escapes(
        _ddg(("p", 2, 4)), [_call(_GETENV, 2), _call(_SET, 4)], SOURCES, language="c",
    ) == []


def test_a_stamp_in_another_function_is_not_this_source_s() -> None:
    """The source's own function holds no stamped call; one in another
    function cannot be where THIS value was lost."""
    other = "c:src/other.c:1-5:other:function"
    assert find_unfollowed_escapes(
        _ddg(("p", 2, 4)),
        [_call(_GETENV, 2), _call(_SET, 3, "out_parameter_call", src=other)],
        SOURCES, language="c",
    ) == []


def test_an_escaped_walk_is_not_an_escape_here() -> None:
    """A walk that returns None (the value went somewhere untracked for some
    OTHER reason) has its own disclosures; pooling it would withhold every
    verdict in a function with any stamped call."""
    assert find_unfollowed_escapes(
        [], [_call(_GETENV, 2), _call(_SET, 4, "macro_call")], SOURCES, language="c",
    ) == []
    assert find_unfollowed_escapes(
        _ddg(("unrelated", 9, 10)),
        [_call(_GETENV, 2), _call(_SET, 4, "macro_call")], SOURCES, language="c",
    ) == []


def test_the_collapsed_stamp_is_read_through_its_values_list() -> None:
    assert call_site_unfollowed_constructs({"unfollowed_construct": "macro_call"}) == ("macro_call",)
    assert call_site_unfollowed_constructs(
        {"unfollowed_construct_values": ["variadic_call", None]}) == ("variadic_call", None)
    assert call_site_unfollowed_constructs(None) == ()


def _verdict(v: str) -> ClaimVerdict:
    return ClaimVerdict(claim_id="C", claim_text="t", verdict=v, details="clean.")


_ESCAPE = UnfollowedEscape("host_secret", _FN, "stdlib.getenv", 4, "out_parameter_call")


def test_a_clean_verdict_on_the_label_is_withheld() -> None:
    for clean in ("confirmed", "confirmed_with_caveats"):
        out = _withhold_for_unfollowed_escapes(_verdict(clean), "host_secret", [_ESCAPE], False)
        assert out.verdict == "inconclusive"
        assert "out_parameter_call at src/init.c:4 (stdlib.getenv)" in out.details


def test_other_labels_findings_and_test_sources_do_not_withhold() -> None:
    assert _withhold_for_unfollowed_escapes(
        _verdict("confirmed"), "untrusted_input", [_ESCAPE], False).verdict == "confirmed"
    assert _withhold_for_unfollowed_escapes(
        _verdict("violated"), "host_secret", [_ESCAPE], False).verdict == "violated"
    in_test = _ESCAPE._replace(source_symbol=_TEST_FN)
    assert _withhold_for_unfollowed_escapes(
        _verdict("confirmed"), "host_secret", [in_test], False).verdict == "confirmed"
    assert _withhold_for_unfollowed_escapes(
        _verdict("confirmed"), "host_secret", [in_test], True).verdict == "inconclusive"


def test_a_function_s_store_lines_are_targets_with_or_without_ddg() -> None:
    """C's ``meta.non_local_store_lines`` (INV-nohob 3/3): ``g = getenv("K")``
    is reached on the source line with NO DDG edge at all, and a later
    ``g = p`` is reached by the walk."""
    on_source_line = find_unfollowed_escapes(
        [], [_call(_GETENV, 2)], SOURCES, language="c", store_lines={_FN: [2]},
    )
    assert [(e.site_line, e.construct) for e in on_source_line] == [(2, "non_local_store")]
    later = find_unfollowed_escapes(
        _ddg(("p", 2, 5)), [_call(_GETENV, 2)], SOURCES, language="c",
        stmt_defuse={_FN: [(2, ("p",), ()), (5, ("g",), ("p",))]},
        store_lines={_FN: [5]},
    )
    assert [(e.site_line, e.construct) for e in later] == [(5, "non_local_store")]


def test_no_sources_means_no_escapes() -> None:
    """A language whose catalogue derives no source has nothing to lose."""
    assert find_unfollowed_escapes(
        _ddg(("p", 2, 4)), [_call(_SET, 4, "macro_call")], [], language="c",
    ) == []
