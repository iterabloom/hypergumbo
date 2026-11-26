# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each Perl call route stamps what scoped its choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) names the
evidence that chose a call-family edge's target. One fixture line per route:

* ``Svc->make`` / ``Svc->new`` on the package written, ``Far::far_fn()`` and
  ``$s->Svc::run()`` naming their package are ``qualified``;
* ``$self->helper()`` (``$self`` typed by ``my $self = shift``) and
  ``$s->run()`` (``$s`` typed by ``Svc->new``) are ``receiver_type``;
* ``shift->helper()``, the invocant itself, is ``own_type``;
* a bare ``far_fn()`` is not bound by the analyzer (ADR-0064): it goes out as a
  stub for the shared linkers, which look for it in ``Main`` and the file's
  ``use`` lines (``test_perl_call_facts.py``). It was bound by name to
  ``Far::far_fn``, which Perl does not call from package ``Main``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/perl.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.perl import analyze_perl

_FILES = {
    "lib/Svc.pm": (
        "package Svc;\n\nsub new { my $class = shift; return bless {}, $class; }\n"
        "sub run { my $self = shift; }\nsub make { }\n\n1;\n"
    ),
    "lib/Far.pm": "package Far;\n\nsub far_fn { }\n\n1;\n",
    "lib/Main.pm": """package Main;

sub helper { }

sub go {
    my $self = shift;
    $self->helper();
    shift->helper();
    Svc->make();
    my $s = Svc->new();
    $s->run();
    Far::far_fn();
    far_fn();
    $s->Svc::run();
}

1;
""",
}


def _edges(root: Path) -> list[Edge]:
    for rel, text in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return analyze_perl(root).edges


def _mark(edges: list[Edge], line: int, dst_name: str) -> str:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.is_resolved and e.line == line
        and e.dst.split(":")[-2] == dst_name
    ]
    assert len(hits) == 1, [(e.line, e.dst) for e in edges]
    return hits[0].meta["resolved_by"]


def test_every_route_of_one_file(tmp_path: Path) -> None:
    edges = _edges(tmp_path)
    assert _mark(edges, 7, "Main..helper") == "receiver_type"
    assert _mark(edges, 8, "Main..helper") == "own_type"
    assert _mark(edges, 9, "Svc..make") == "qualified"
    assert _mark(edges, 10, "Svc..new") == "qualified"
    assert _mark(edges, 11, "Svc..run") == "receiver_type"
    assert _mark(edges, 12, "Far..far_fn") == "qualified"
    assert not [e for e in edges if e.line == 13 and e.is_resolved]
    assert [e.dst for e in edges if e.line == 13] == ["perl:external:0-0:far_fn:unresolved"]
    assert _mark(edges, 14, "Svc..run") == "qualified"
