# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/perl.py
"""What the Perl analyzer binds itself and what it reports (ADR-0064, batch 5b).

The analyzer binds a call only where the call site writes the package in
full (``Pkg::f()``, ``Pkg->m()``, ``$o->Pkg::m()``, a value whose package the
file states): Perl package names are absolute, so the sub is ``Pkg::m``
exactly. Declared in one file, that file's last declaration; in several, the
caller's own file's, else a stub. Every other call goes out as an unresolved
stub with what the call site says, and the shared linkers decide through the
file's import table (``_perl_import_scope``):

* ``f()`` / ``&f``: no fact (the caller's package, or a ``use`` line);
* ``Pkg::f()`` / ``Pkg->m()`` / ``$o->Pkg::m()`` not bound: ``qualifier``;
* ``$o->m()`` on ``Pkg->new``: ``receiver_type_hint``; on ``$self`` /
  ``shift``: ``enclosing_class``; ``SUPER::m``: ``enclosing_class`` and
  ``super_receiver``; on anything else: no fact.

The first part pins each route at the analyzer; the second runs the whole
pipeline with a NAMESAKE in another package beside each target, so that a
bind by name would show as wrong or as a tie.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.perl import PerlAnalyzer, analyze_perl


def _analyze(root: Path, files: dict[str, str]):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return analyze_perl(root)


def _calls(result) -> list[Edge]:
    return [e for e in result.edges if e.edge_type == "calls"]


def _stub(edges: list[Edge], callee: str, line: int | None = None) -> dict:
    """The facts of the one unresolved stub of ``callee`` (on ``line``), and the
    module its ``dst_ref`` names under ``module``."""
    found = [
        e for e in edges if not e.is_resolved and (e.meta or {}).get("callee_name") == callee
        and (line is None or e.line == line)
    ]
    assert len(found) == 1, [(e.line, e.dst, e.meta) for e in edges]
    facts = {k: v for k, v in found[0].meta.items()
             if k not in ("callee_name", "evidence_lang", "evidence_type", "access_mode")}
    facts["module"] = found[0].dst_ref.module_path if found[0].dst_ref else "external"
    return facts


def _bound(edges: list[Edge], line: int) -> list[tuple[str, str, float]]:
    """(target id's name slot, mark, confidence) of each resolved call on ``line``."""
    return [
        (e.dst.split(":")[-2], e.meta["resolved_by"], e.confidence)
        for e in edges if e.is_resolved and e.line == line
    ]


# --- the table --------------------------------------------------------------------


_UTIL = "package Util;\nuse Exporter 'import';\nour @EXPORT = qw(helper $config);\n" \
        "push @EXPORT, 'other';\nour @EXPORT_OK = qw(spare);\nsub helper { 1 }\n1;\n"


def test_every_kind_of_use_is_in_the_table(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {
        "lib/Util.pm": _UTIL,
        "lib/Partial.pm": "package Partial;\nour @EXPORT = (@Base::EXPORT, qw(known), &also);\n1;\n",
        "lib/NoExport.pm": "package NoExport;\nsub x { }\n1;\n",
        "main.pl": "use 5.010;\nuse strict;\nuse Util qw(spare &amp $var @arr %h :tag);\nuse Lib::Ext 'ext_f', \"ext_g\";\n"
                   "use Ver 1.2 qw(vf);\nuse Util;\nuse Partial;\nuse NoExport;\nuse Library;\n"
                   "use Silent ();\nuse parent -norequire, 'Base';\nuse base qw(A);\nuse lib 'x';\n"
                   "use Def qw(:DEFAULT d1);\nno Util qw(spare2);\nuse if 1, Cond => qw(c1);\n",
    })
    scope = result.import_scopes["main.pl"]
    named = {local: (c.module, c.name) for local, c in scope._named.items()}
    assert named == {
        "spare": ("Util", "spare"), "amp": ("Util", "amp"),
        "ext_f": ("Lib::Ext", "ext_f"), "ext_g": ("Lib::Ext", "ext_g"), "vf": ("Ver", "vf"),
        "helper": ("Util", "helper"), "other": ("Util", "other"),
        "known": ("Partial", "known"), "also": ("Partial", "also"),
        "d1": ("Def", "d1"),
    }
    assert scope.wildcard_modules == ()
    assert scope.own_namespace == ""
    assert scope.library_names_screened is False


def test_default_names_the_exports_and_a_disagreement_names_nothing(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {
        "lib/Util.pm": _UTIL,
        "a/Twice.pm": "package Twice;\nour @EXPORT = qw(one);\n1;\n",
        "b/Twice.pm": "package Twice;\nour @EXPORT = qw(two);\n1;\n",
        "main.pl": "use Util qw(:DEFAULT);\nuse Twice;\n",
    })
    named = {local: (c.module, c.name) for local, c in result.import_scopes["main.pl"]._named.items()}
    assert named == {"helper": ("Util", "helper"), "other": ("Util", "other")}


def test_the_files_own_package_is_its_one_package(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {
        "One.pm": "package One::Two;\nsub f { }\n1;\n",
        "script.pl": "sub main_sub { }\nmain_sub();\n",
        "Both.pm": "package Both;\nsub f { }\npackage Other;\nsub g { }\n1;\n",
        "Block.pm": "package Block { sub f { } }\nsub in_main { }\n",
        "Declared.pm": "package Declared;\n1;\n",
        "Empty.pl": "",
    })
    own = {path: scope.own_namespace for path, scope in result.import_scopes.items()}
    assert own == {
        "One.pm": "One::Two", "script.pl": "", "Both.pm": None, "Block.pm": None,
        "Declared.pm": "Declared", "Empty.pl": "",
    }
    assert all(not s._named for s in result.import_scopes.values())


def test_a_symbols_qualified_name_is_the_full_name_its_stable_id_hashes(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {"a.pm": "package A::B;\nsub f { }\npackage main;\nsub g { }\n1;\n"})
    by_kind = {(s.kind, s.name): s.qualified_name for s in result.symbols}
    assert by_kind == {("file", "a.pm"): None, ("module", "A::B"): "A::B",
                       ("module", "main"): "main", ("function", "A::B::f"): "A::B::f",
                       ("function", "g"): "g"}


def test_a_second_run_starts_from_an_empty_state(tmp_path: Path) -> None:
    analyzer = PerlAnalyzer()
    for name in ("one", "two"):
        root = tmp_path / name
        root.mkdir()
        (root / f"{name}.pm").write_text(f"package {name};\nour @EXPORT = qw({name}_f);\n1;\n")
        (root / "main.pl").write_text(f"use {name};\n")
        result = analyzer.analyze(root)
        assert set(result.import_scopes) == {f"{name}.pm", "main.pl"}
        assert set(result.import_scopes["main.pl"]._named) == {f"{name}_f"}


# --- the calls ----------------------------------------------------------------------


def test_a_bare_call_goes_out_with_no_fact(tmp_path: Path) -> None:
    edges = _calls(_analyze(tmp_path, {
        "lib/Util.pm": "package Util;\nsub helper { }\n1;\n",
        "main.pl": "sub run {\n    helper();\n    &other;\n    main::third(1);\n    ::fourth();\n"
                   "    &$code(1);\n    print 'x';\n}\nsub helper { }\n",
    }))
    for callee, line in (("helper", 2), ("other", 3), ("third", 4), ("fourth", 5)):
        assert _stub(edges, callee, line) == {"module": "external"}
    assert {e.line for e in edges} == {2, 3, 4, 5}


def test_a_package_written_in_full_is_bound_where_one_file_declares_the_sub(tmp_path: Path) -> None:
    edges = _calls(_analyze(tmp_path, {
        "lib/Util.pm": "package Util;\nsub helper { 1 }\nsub helper { 2 }\nsub new { }\n1;\n",
        "main.pl": "sub run {\n    Util::helper();\n    Util->helper();\n    Util::->helper();\n"
                   "    main::Util::helper();\n    my $o = Util->new;\n    $o->Util::helper();\n}\n",
    }))
    # The second ``sub helper`` replaces the first (line 3).
    assert _bound(edges, 2) == [("Util..helper", "qualified", 0.85)]
    assert edges[0].dst.startswith("perl:lib/Util.pm:3-3:")
    for line in (3, 4, 5, 7):
        assert [b[:2] for b in _bound(edges, line)] == [("Util..helper", "qualified")]
    by_line = {e.line: e.meta["call_construct"] for e in edges if e.is_resolved}
    assert by_line == {2: "function", 3: "function", 4: "function", 5: "function",
                       6: "function", 7: "method"}


def test_a_package_declared_in_two_files_is_the_callers_own_or_a_stub(tmp_path: Path) -> None:
    twin = "package Twin;\nsub go { }\n1;\n"
    edges = _calls(_analyze(tmp_path, {
        "a/Twin.pm": twin,
        "b/Twin.pm": twin + "sub local_call { Twin::go(); }\n",
        "main.pl": "sub run {\n    Twin::go();\n    Twin->go();\n}\n",
    }))
    assert _stub(edges, "go", 2) == {"qualifier": "Twin", "module": "Twin"}
    assert _stub(edges, "go", 3) == {
        "qualifier": "Twin", "call_construct": "function", "module": "Twin",
    }
    own = [e for e in edges if e.is_resolved]
    assert [(e.line, e.dst.split(":")[1], e.meta["resolved_by"]) for e in own] == [
        (4, "b/Twin.pm", "qualified"),
    ]


def test_a_package_that_lacks_the_sub_goes_out_with_the_package(tmp_path: Path) -> None:
    edges = _calls(_analyze(tmp_path, {
        "lib/Svc.pm": "package Svc;\nsub new { }\n1;\n",
        "main.pl": "sub run {\n    Svc::gone();\n    Svc->gone();\n    my $o = Svc->new;\n"
                   "    $o->gone();\n    $o->Svc::gone();\n    Ext::Lib->get();\n}\n",
    }))
    assert _stub(edges, "gone", 2) == {"qualifier": "Svc", "module": "Svc"}
    assert _stub(edges, "gone", 3) == {
        "qualifier": "Svc", "call_construct": "function", "module": "Svc"}
    assert _stub(edges, "gone", 5) == {
        "receiver_type_hint": "Svc", "call_construct": "method", "module": "Svc"}
    assert _stub(edges, "gone", 6) == {
        "qualifier": "Svc", "call_construct": "method", "module": "Svc"}
    assert _stub(edges, "get", 7) == {
        "qualifier": "Ext::Lib", "call_construct": "function", "module": "Ext::Lib"}


def test_the_invocant_goes_out_as_the_enclosing_package(tmp_path: Path) -> None:
    edges = _calls(_analyze(tmp_path, {
        "lib/Svc.pm": "package Svc;\nsub own { }\nsub run {\n    my $self = shift;\n"
                      "    $self->own();\n    $self->inherited();\n    shift->inherited2();\n"
                      "    $self->SUPER::new(1);\n    Svc->SUPER::init();\n    $self->$dynamic();\n"
                      "    my $o = get();\n    $o->untyped();\n}\n1;\n",
    }))
    assert _bound(edges, 5) == [("Svc..own", "receiver_type", 0.85)]
    assert _stub(edges, "inherited") == {
        "enclosing_class": "Svc", "call_construct": "method", "module": "Svc"}
    assert _stub(edges, "inherited2") == {
        "enclosing_class": "Svc", "call_construct": "method", "module": "Svc"}
    for callee in ("new", "init"):
        assert _stub(edges, callee) == {
            "enclosing_class": "Svc", "super_receiver": True, "call_construct": "method",
            "module": "external"}
    assert _stub(edges, "untyped") == {"call_construct": "method", "module": "external"}
    assert not [e for e in edges if e.line == 10]


def test_every_file_hands_over_a_table_keyed_like_its_symbols(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {"lib/A.pm": "package A;\n1;\n", "bin/run.pl": "print 1;\n"})
    paths = {s.path for s in result.symbols}
    assert set(result.import_scopes) == paths == {"lib/A.pm", "bin/run.pl"}


# --- through the whole pipeline, with a namesake beside every target ---------------------------


def _map(root: Path, files: dict[str, str]) -> tuple[dict[str, dict], list[dict]]:
    for rel, text in files.items():
        path = root / "repo" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    out = root / "out.json"
    run_behavior_map(repo_root=root / "repo", out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    return {n["id"]: n for n in data["nodes"]}, data["edges"]


def _resolved(nodes: dict[str, dict], edges: list[dict]) -> set[tuple]:
    """(caller, line, target's path and qualified name, mark) of each resolved Perl call."""
    return {
        (nodes[e["src"]].get("qualified_name") or nodes[e["src"]]["name"], e["line"],
         f'{nodes[e["dst"]]["path"]}:{nodes[e["dst"]]["qualified_name"]}',
         (e.get("meta") or {}).get("resolved_by"))
        for e in edges
        if e["type"] == "calls" and e.get("is_resolved") and e["src"].startswith("perl:")
    }


def _helper(package: str, extra: str = "") -> str:
    return f"package {package};\nuse Exporter 'import';\n{extra}sub helper {{ 1 }}\nsub new {{ }}\n" \
           "sub execute { }\nsub floor { }\n1;\n"


def test_a_call_through_an_import_and_a_qualified_call_reach_util_not_its_namesake(
    tmp_path: Path,
) -> None:
    nodes, edges = _map(tmp_path, {
        "lib/Util.pm": _helper("Util", "our @EXPORT_OK = qw(helper);\n"),
        "lib/Decoy.pm": _helper("Decoy"),
        "lib/Exp.pm": "package Exp;\nour @EXPORT = qw(exported);\nsub exported { }\n1;\n",
        "lib/Other.pm": "package Other;\nsub exported { }\n1;\n",
        "main.pl": "use Util qw(helper);\nuse Exp;\nsub via_import {\n    helper();\n}\n"
                   "sub via_qual {\n    Util::helper();\n}\nsub via_export {\n    exported();\n}\n",
    })
    assert _resolved(nodes, edges) == {
        ("via_import", 4, "lib/Util.pm:Util::helper", "import"),
        ("via_qual", 7, "lib/Util.pm:Util::helper", "qualified"),
        ("via_export", 10, "lib/Exp.pm:Exp::exported", "import"),
    }


def test_a_typed_receiver_reaches_the_used_package_not_its_namesake(tmp_path: Path) -> None:
    nodes, edges = _map(tmp_path, {
        "lib/R.pm": _helper("R"),
        "lib/D.pm": _helper("D"),
        "main.pl": "use R;\nsub via_ctor {\n    my $c = R->new();\n    $c->execute();\n}\n",
    })
    assert _resolved(nodes, edges) == {
        ("via_ctor", 3, "lib/R.pm:R::new", "qualified"),
        ("via_ctor", 4, "lib/R.pm:R::execute", "receiver_type"),
    }


def test_a_bare_call_means_the_callers_own_package(tmp_path: Path) -> None:
    nodes, edges = _map(tmp_path, {
        "lib/Util.pm": _helper("Util") + "package Util;\nsub run {\n    helper();\n}\n",
        "lib/Decoy.pm": _helper("Decoy"),
        "main.pl": "sub helper { }\nsub main_run {\n    helper();\n}\nmain_run();\n",
    })
    assert _resolved(nodes, edges) == {
        ("Util::run", 10, "lib/Util.pm:Util::helper", "same_module"),
        ("main_run", 3, "main.pl:helper", "same_module"),
        ("main.pl", 5, "main.pl:main_run", "same_module"),
    }


def test_a_script_calls_its_own_sub_not_another_scripts_namesake(tmp_path: Path) -> None:
    script = "sub setup { }\nsetup();\n"
    nodes, edges = _map(tmp_path, {"t/a.pl": script, "t/b.pl": script})
    assert _resolved(nodes, edges) == {
        ("t/a.pl", 2, "t/a.pl:setup", "same_module"),
        ("t/b.pl", 2, "t/b.pl:setup", "same_module"),
    }


def test_an_untyped_receiver_stays_unresolved_with_one_holder(tmp_path: Path) -> None:
    nodes, edges = _map(tmp_path, {
        "lib/R.pm": "package R;\nsub execute { }\n1;\n",
        "main.pl": "sub via_param {\n    my ($x) = @_;\n    $x->execute();\n}\n",
    })
    assert _resolved(nodes, edges) == set()


def test_a_library_sub_the_tree_also_declares_is_not_bound(tmp_path: Path) -> None:
    nodes, edges = _map(tmp_path, {
        "lib/Util.pm": _helper("Util", "our @EXPORT = qw(floor);\n"),
        "lib/Git.pm": "package Git;\nsub croak { }\n1;\n",
        "lib/Svc.pm": "package Svc;\nuse POSIX qw(floor);\nuse Carp qw(croak);\n"
                      "sub run {\n    floor(1.5);\n    croak 'x';\n    unknown();\n}\n1;\n",
    })
    assert _resolved(nodes, edges) == set()
