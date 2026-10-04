# SPDX-License-Identifier: AGPL-3.0-or-later
"""A perl class-method call is resolved by its PACKAGE, never by the bare name (WI-mahik).

``Pkg->m(...)`` names its owner: the invocant is the package whose method
table Perl searches. The ``method_call_expression`` arm ignored the invocant
and asked the name resolver for ``m`` by SHORT NAME, so

* when the repository declared no sub called ``m``, the call emitted NO edge
  at all (``HTTP::Tiny->new``, ``File::Spec->catfile``), and no catalogue or
  overlay row could ever classify it;
* when it declared one in ANY package, the call bound to it whatever its
  package (git: ``SVN::Pool->new`` bound ``Git::new``; postgresql:
  ``IO::Socket::INET->new`` bound ``LdapServer::new``, hiding a socket open).

A bareword invocant (and ``__PACKAGE__``) now looks up ``Pkg::m`` exactly. A
miss emits the unresolved edge the function-call arm already emits for
``LWP::Simple::get()``: ``perl:<Pkg>:0-0:<m>:unresolved``, with the package as
the module slot (ADR-0051's owner path), and ``call_construct="function"``
because the call is made on the module itself (ADR-0059). An invocant that is
an expression (``$obj->m``) is unchanged: its type is unknown here.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_lang_mainstream.perl import analyze_perl

_SRC = """package Foo;
sub catfile { 1 }
sub helper { 2 }

package Bar;
sub catfile { 3 }
sub helper { 4 }
sub run {
    __PACKAGE__->helper();
}

package main;
sub ext {
    my $u = shift;
    HTTP::Tiny->new;
    File::Spec->catfile($u);
    LWP::UserAgent->new(agent => 'x');
}
sub own_foo { Foo->catfile("a"); }
sub own_bar { Bar->catfile("b"); }
sub trailing { Foo::->catfile("c"); }
sub rooted { ::Bar->catfile("d"); }
sub missing { Foo->absent(); }
sub on_main { main->top(); }
sub dynamic { my $m = "x"; Foo->$m(); }
sub top { 5 }
IO::Socket::INET->new(PeerAddr => 'h');
Foo->helper();
"""


def _calls(tmp_path: Path) -> dict[str, list]:
    (tmp_path / "a.pl").write_text(_SRC)
    by_caller: dict[str, list] = {}
    for e in analyze_perl(tmp_path).edges:
        if e.edge_type == "calls":
            caller = e.src.split(":")[-2]
            by_caller.setdefault(caller, []).append(e)
    return by_caller


@pytest.fixture
def calls(tmp_path: Path) -> dict[str, list]:
    return _calls(tmp_path)


def _sub_id(name: str, line: int) -> str:
    return f"perl:a.pl:{line}-{line}:{name}:function"


def _dsts(calls: dict[str, list], caller: str) -> set[str]:
    return {e.dst for e in calls.get(caller, [])}


def test_an_undeclared_package_emits_its_external_edge(calls: dict[str, list]) -> None:
    assert _dsts(calls, "ext") == {
        "perl:HTTP::Tiny:0-0:new:unresolved",
        "perl:File::Spec:0-0:catfile:unresolved",
        "perl:LWP::UserAgent:0-0:new:unresolved",
    }, _dsts(calls, "ext")


def test_the_external_edge_carries_the_package_and_the_construct(
    calls: dict[str, list],
) -> None:
    (edge,) = [e for e in calls["ext"] if "File::Spec" in e.dst]
    assert edge.dst_ref is not None
    assert (edge.dst_ref.module_path, edge.dst_ref.name) == ("File::Spec", "catfile")
    assert edge.meta["call_construct"] == "function"


@pytest.mark.parametrize("caller,line", [
    ("own_foo", 2), ("trailing", 2), ("own_bar", 6), ("rooted", 6),
])
def test_the_invocant_picks_the_package_that_declares_the_method(
    calls: dict[str, list], caller: str, line: int,
) -> None:
    """Both packages declare ``catfile``: each call must reach ITS package's
    sub (a name-keyed registry keeps one per short name, so both sides are
    called)."""
    assert _dsts(calls, caller) == {_sub_id("catfile", line)}, _dsts(calls, caller)
    (edge,) = calls[caller]
    assert edge.meta["call_construct"] == "function"


def test_dunder_package_is_the_enclosing_package(calls: dict[str, list]) -> None:
    assert _dsts(calls, "run") == {_sub_id("helper", 7)}, _dsts(calls, "run")


def test_a_declared_package_without_the_method_is_not_bound_elsewhere(
    calls: dict[str, list],
) -> None:
    """``Foo->absent`` may be inherited or AUTOLOADed; it is not some other
    package's ``absent``. The edge names Foo, the owner the source spells."""
    assert _dsts(calls, "missing") == {"perl:Foo:0-0:absent:unresolved"}


def test_main_names_the_unqualified_subs(calls: dict[str, list]) -> None:
    assert _dsts(calls, "on_main") == {_sub_id("top", 26)}, _dsts(calls, "on_main")


def test_a_dynamic_method_name_emits_nothing(calls: dict[str, list]) -> None:
    assert "dynamic" not in calls, calls.get("dynamic")


def test_a_top_level_class_method_call_is_attributed_to_the_file(
    calls: dict[str, list],
) -> None:
    """WI-jadaf gave the function arm a file-level caller; the class-method
    arm had none, so a script's top-level ``IO::Socket::INET->new`` was lost."""
    assert _dsts(calls, "file") >= {
        "perl:IO::Socket::INET:0-0:new:unresolved", _sub_id("helper", 3),
    }, _dsts(calls, "file")


def test_a_receiver_expression_keeps_the_short_name_lookup(tmp_path: Path) -> None:
    """Out of scope (a variable's type is unknown here): ``$obj->helper``
    still binds by short name, as test_perl's method-call test pins."""
    (tmp_path / "a.pl").write_text(
        "sub helper { 1 }\nsub go { my $o = shift; $o->helper(); }\n")
    dsts = {e.dst for e in analyze_perl(tmp_path).edges if e.edge_type == "calls"}
    assert dsts == {_sub_id("helper", 1)}, dsts


def _run(argv: list[str], cache: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(argv)
    return buf.getvalue()


def _fs_read_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                     module: str, invocant: str) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.pl").write_text(
        "package Local;\nsub new { 1 }\n"
        "package main;\n"
        f"sub slurp {{ my $p = shift; my $fh = {invocant}->new($p, 'r'); }}\n")
    overlay = tmp_path / "ov.yaml"
    overlay.write_text("language: perl\nstatus: overlay\nfs_read:\n"
                       f"  - module: {module}\n    functions: [new]\n")
    claims = tmp_path / "claims.yaml"
    claims.write_text("claims:\n  - id: C\n    text: t\n    constraint:\n"
                      "      boundary: fs_read\n      must_not_exist: true\n")
    out = _run(["verify-claims", str(repo), "--claims", str(claims),
                "--io-primitives", str(overlay), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    (verdict,) = json.loads(out)["verdicts"]
    return verdict["verdict"]


def test_an_overlay_row_for_the_package_reaches_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The behaviour the item states. ``IO::File->new`` bound ``Local::new``
    (the repo's only ``new``), so the claim came back ``confirmed``: a false
    all-clear over a file open."""
    assert _fs_read_verdict(tmp_path, monkeypatch, "IO::File", "IO::File") == "violated"


@pytest.mark.parametrize("invocant,verdict", [
    ("Local", "confirmed"),
    # FAIL-CLOSED: the unclassified package withholds a clean verdict. A
    # mutant that drops the package from the module slot turns this one
    # ``violated`` through the name-only fallback (the control can fail).
    ("Other::File", "inconclusive"),
])
def test_the_row_does_not_reach_another_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invocant: str, verdict: str,
) -> None:
    """Control: an ``IO::File`` row must not classify another package's ``new``."""
    assert _fs_read_verdict(tmp_path, monkeypatch, "IO::File", invocant) == verdict
