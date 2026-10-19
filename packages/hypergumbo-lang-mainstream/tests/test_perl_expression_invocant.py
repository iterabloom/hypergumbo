# SPDX-License-Identifier: AGPL-3.0-or-later
"""A method call on an EXPRESSION invocant resolves on the invocant's type (WI-mugir).

``$obj->m``, ``shift->m`` and ``Pkg->new->m`` looked ``m`` up by short name
across every package (receiver-blind), so a repo sub named ``get`` anywhere
captured ``HTTP::Tiny->new->get($u)``, and nothing was emitted outside a sub.
Now the invocant is typed where the file establishes it -- ``Pkg->new`` (a
constructor), a scalar assigned one, or the method invocant (``shift`` /
``my $self = shift`` / ``my ($self, ...) = @_``) which is the enclosing
package -- and an untyped invocant gets an honest unresolved method edge.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/perl.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.perl import analyze_perl


def _calls(tmp_path: Path, files: dict[str, str]) -> dict[str, list[tuple[str, dict]]]:
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    result = analyze_perl(tmp_path)
    names = {s.id: s.name for s in result.symbols}
    out: dict[str, list[tuple[str, dict]]] = {}
    for e in result.edges:
        if e.edge_type == "calls" and e.src in names:
            out.setdefault(names[e.src], []).append((names.get(e.dst, e.dst), dict(e.meta or {})))
    return out


_DECOY = "package Decoy;\nsub get { }\nsub go { }\nsub b { }\n1;\n"


def test_a_constructed_invocant_names_its_package(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {
        "decoy.pm": _DECOY,
        "a.pl": (
            "package P;\n"
            "sub f {\n"
            "  my $ua = LWP::UserAgent->new(agent => 1);\n"
            "  $ua->get($u);\n"
            "  HTTP::Tiny->new->get($u);\n"
            "}\n"
        ),
    })
    edges = dict(calls["P::f"])
    assert "Decoy::get" not in edges
    assert edges["perl:LWP::UserAgent:0-0:get:unresolved"]["call_construct"] == "method"
    assert edges["perl:HTTP::Tiny:0-0:get:unresolved"]["call_construct"] == "method"


def test_the_method_invocant_is_the_enclosing_package(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {
        "decoy.pm": _DECOY,
        "p.pm": (
            "package P;\n"
            "sub b { }\n"
            "sub one { my $self = shift; $self->b; }\n"
            "sub two { shift->b; }\n"
            "sub three { my ($class, %opt) = @_; $class->b; }\n"
            "sub four { my $self = shift; $self->missing; }\n"
            "1;\n"
        ),
    })
    for sub in ("P::one", "P::two", "P::three"):
        assert [t for t, _ in calls[sub]] == ["P::b"], (sub, calls[sub])
    # Not declared on P: inherited or AUTOLOADed, never another package's.
    assert [t for t, _ in calls["P::four"]] == ["perl:P:0-0:missing:unresolved"]


def test_only_the_named_invocant_of_a_package_sub_is_typed(tmp_path: Path) -> None:
    """A plain function's first argument, a second ``shift``, an anonymous
    sub's ``shift`` and a script's bare ``shift->m`` are not the invocant."""
    calls = _calls(tmp_path, {
        "decoy.pm": _DECOY,
        "p.pm": (
            "package P;\n"
            "sub b { }\n"
            "sub f { my $self = shift; my $node = shift; $node->b; }\n"
            "sub g { my ($e) = @_; $e->b; }\n"
            "sub h { my $cb = sub { my $self = shift; $self->b; shift->b; }; }\n"
            "1;\n"
        ),
        "s.pl": "sub run { shift->b; }\n",
    })
    for sub in ("P::f", "P::g", "P::h", "run"):
        assert {t for t, _ in calls[sub]} == {"perl:external:0-0:b:unresolved"}, (sub, calls[sub])


def test_an_untyped_invocant_is_not_bound_by_short_name(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {
        "decoy.pm": _DECOY,
        "u.pl": (
            "package U;\n"
            "sub f {\n"
            "  my ($x) = grep { 1 } @list;\n"
            "  $x->get(1);\n"
            "  my $y = Foo->new;\n"
            "  $y = make();\n"
            "  $y->go;\n"
            "  $h->{k}->b;\n"
            "  my $dbh = DBI->connect($dsn);\n"
            "  $dbh->get;\n"
            "}\n"
        ),
    })
    edges = calls["U::f"]
    assert not any(t.startswith("Decoy::") for t, _ in edges), edges
    # Only ``new`` constructs: ``DBI->connect`` returns a handle, not a DBI.
    assert "perl:DBI:0-0:get:unresolved" not in [t for t, _ in edges]
    externals = {t: m for t, m in edges if t.startswith("perl:external:")}
    for name in ("get", "go", "b"):
        assert externals[f"perl:external:0-0:{name}:unresolved"]["call_construct"] == "method"


def test_a_top_level_call_is_attributed_to_the_file(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {
        "foo.pm": "package Foo;\nsub new { }\nsub go { }\n1;\n",
        "main.pl": "use Foo;\nmy $t = Foo->new;\n$t->go;\n",
    })
    assert "Foo::go" in [t for t, _ in calls["main.pl"]]


def test_a_dynamic_or_qualified_method_keeps_its_old_path(tmp_path: Path) -> None:
    """``$o->$m`` names no sub; ``$self->Other::m`` names its package."""
    calls = _calls(tmp_path, {
        "o.pm": "package Other;\nsub m { }\n1;\n",
        "p.pm": (
            "package P;\n"
            "sub f { my $self = shift; $self->$name(); $self->Other::m(); }\n"
            "1;\n"
        ),
    })
    assert [t for t, _ in calls["P::f"]] == ["Other::m"]
