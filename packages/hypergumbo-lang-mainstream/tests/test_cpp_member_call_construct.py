# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-nizom: a C++ member call must say so, or the F3 gate cannot refuse it.

THE GATE DOES TWO JOBS AND THIS IS THE FIRST ONE.
``io_boundary.gate_named_entry`` is reached when there is no usable module
hint, and it opens::

    if call_construct == "method":
        return None

That refusal exists to stop a *method* call from matching a *function*-kind
catalogue entry by bare name — INV-tapat (no receiver verification) and
INV-maluk (``str.replace`` matching ``pathlib.Path.replace``). It is the only
thing separating a real POSIX ``wait()`` from ``std::future::wait()``, because
with no receiver type the two are the same short name.

WHY C++ COULD NOT REACH IT. The analyzer computes ``is_member_call`` while
walking the call — it must, to suppress the STL-method fallback — and then
throws the fact away: every unresolved call edge left this file with the
construct unset, so the refusal above was unreachable for the whole language.
Measured on whisper.cpp before this change: **34,983 cpp call edges, 1 of them
carrying ``call_construct="method"``.** The one came from a two-level
``this->field->method()`` chain, the only branch that stamped it.

``function`` IS NOT A PARTIAL FIX, WHICH IS WHY THE COUNT ABOVE IS THE ONE THAT
MATTERS. 41.1% of those edges *did* carry a construct — ``function``, 14,360 of
them. The gate falls through on anything that is not the literal string
``"method"``, so ``function`` and absent are the same value to it. A census
that counts "edges carrying call_construct" therefore reports C++ as 41%
covered for a gate that sees 0.003%.

THE FILED FALSE POSITIVE, reproduced on a fixture with a passing positive
control (``hypergumbo io-boundaries``, before this change)::

    positive_control()  wait(&status)   -> sys/wait.wait [subprocess]  TRUE
    the_defect()        cv.wait(guard)  -> sys/wait.wait [subprocess]  FALSE
                        fut.wait()      -> sys/wait.wait [subprocess]  FALSE

The file genuinely includes ``<sys/wait.h>`` and genuinely calls the POSIX
function elsewhere, so the include-set module evidence is correct; the call
construct is the only discriminator, and it was absent.

SCOPE, STATED. This fixes the *emission* half for C++ only. It does not touch
the ``non_method`` filter — the gate's second job, where a method-KIND
catalogue entry is dropped for want of a module hint — which is INV-linub's
class and a false-NEGATIVE channel in the opposite direction. The two are
routinely conflated because they are two branches of one function.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.cpp import analyze_cpp

_METHOD = "method"


def _construct(edge) -> str | None:
    """``call_construct`` of an edge, tolerating an absent ``meta``.

    ``make_unresolved_edge`` leaves ``meta`` as ``None`` when no optional
    kwarg is supplied, so a bare ``edge.meta.get`` raises rather than
    reporting the absence this file is about.
    """
    return (edge.meta or {}).get("call_construct")


def _unresolved_by_name(repo: Path) -> dict[str, list]:
    """Unresolved call edges of a repo, bucketed by callee short name."""
    result = analyze_cpp(repo)
    out: dict[str, list] = {}
    for e in result.edges:
        if e.is_resolved:
            continue
        name = e.dst.split(":")[-2]
        out.setdefault(name, []).append(e)
    return out


def test_dot_member_call_declares_method_construct(tmp_path: Path) -> None:
    """``obj.method()`` on an unknown receiver stamps ``method``.

    This is the shape the gate must be able to refuse: the receiver type is
    unknown, so the short name alone must not be allowed to match a
    function-kind catalogue entry.
    """
    (tmp_path / "a.cpp").write_text("""
#include <sys/wait.h>
#include <future>
void f(std::future<int>& fut) {
    fut.wait();
}
""")
    edges = _unresolved_by_name(tmp_path)
    assert "wait" in edges, "the member call must still emit an edge"
    assert all(
        _construct(e) == _METHOD for e in edges["wait"]
    ), f"expected method construct, got {[_construct(e) for e in edges['wait']]}"


def test_arrow_member_call_declares_method_construct(tmp_path: Path) -> None:
    """``ptr->method()`` is a member call too.

    Separate from the dot form because C++ spells receiver access two ways and
    a fix that keys on one token silently leaves the other uncovered.
    """
    (tmp_path / "b.cpp").write_text("""
#include <sys/wait.h>
struct Conn;
void g(Conn* c) {
    c->wait();
}
""")
    edges = _unresolved_by_name(tmp_path)
    assert "wait" in edges
    assert all(_construct(e) == _METHOD for e in edges["wait"])


def test_free_function_call_is_not_marked_method(tmp_path: Path) -> None:
    """POSITIVE CONTROL: a genuine free function must NOT be refused.

    Without this, stamping every call ``method`` would 'fix' the false
    positive by destroying the true positives beside it — the direction this
    project has paid for before. ``wait(&status)`` is real POSIX I/O and must
    stay matchable.
    """
    (tmp_path / "c.cpp").write_text("""
#include <sys/wait.h>
void h() {
    int status = 0;
    wait(&status);
}
""")
    edges = _unresolved_by_name(tmp_path)
    assert "wait" in edges
    assert all(
        _construct(e) != _METHOD for e in edges["wait"]
    ), "a free function call must remain matchable by the F3 gate"


def test_both_constructs_coexist_in_one_translation_unit(tmp_path: Path) -> None:
    """The filed repro: same name, both constructs, one file.

    The discriminator has to work *within* a translation unit — the filed
    whisper.cpp instance is exactly this, which is why the include-set module
    evidence cannot settle it and the construct must.
    """
    (tmp_path / "d.cpp").write_text("""
#include <sys/wait.h>
#include <future>
void positive_control() {
    int status = 0;
    wait(&status);
}
void the_defect(std::future<int>& fut) {
    fut.wait();
}
""")
    result = analyze_cpp(tmp_path)
    by_line = {
        e.line: _construct(e)
        for e in result.edges
        if not e.is_resolved and e.dst.split(":")[-2] == "wait"
    }
    constructs = set(by_line.values())
    assert _METHOD in constructs, f"member call unmarked: {by_line}"
    assert constructs != {_METHOD}, f"free function over-marked: {by_line}"
