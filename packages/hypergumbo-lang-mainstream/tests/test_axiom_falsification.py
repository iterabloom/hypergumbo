# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each declared axiom, tested against what the tool emits (WI-ziruv).

A declared axis (ADR-0024) has an axiom, a registry, a drift linter and a
property test, and together they stop a new SPELLING: an unregistered value
that a producer writes as a literal. They do not stop the MEANING drifting. A
registered value can still be applied to the wrong thing, and nothing ran the
axiom's own sentence against output to find out. These tests do. Each one
states an axiom as a property of real survey or verify-claims output on small
fixtures and tries to falsify it there. The classification of every declared
axiom (mechanically testable, partly testable, or needing per-row judgment),
with the reasons, is ``docs/surveys/axiom-falsifiability.md``.

Every test that fails on day one fails on a counterexample already filed, so it
ships as a strict xfail naming that item. When the fix lands it XPASSes, which
under ``strict`` fails the suite until the mark is removed, and the test then
serves as that item's production-path closure evidence.

Two disciplines from the project's experience with instruments:

* **Reach first.** Each test asserts that its fixture produced the thing it is
  about (the framework twin was enriched, the call pair has one resolved and
  one unresolved edge, the receiver's type was recovered) before it asserts the
  property, so it cannot pass vacuously.
* **A control that can fail.** Each property is one pure checker, and a
  ``Test...Checker`` case runs it on a hand-built counterexample to show it
  reports one. The live tests then run the same checker on real output.

The fixtures are surveyed once per module through ``run_behavior_map``, the
pipeline ``hypergumbo survey`` runs.
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/symbol_kinds.py
from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable, Iterable

import pytest

from hypergumbo_core.cli import build_parser, cmd_verify_claims, run_behavior_map
from hypergumbo_core.symbol_kinds import (
    AXIS_LANGUAGE_CONSTRUCT,
    symbol_kinds_on_axis,
)

Map = dict[str, Any]


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


@pytest.fixture(scope="module")
def survey(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str, dict[str, str]], Map]:
    """Survey a fixture once per module and hand back the behavior map."""
    cache: dict[str, Map] = {}

    def run(name: str, files: dict[str, str]) -> Map:
        if name not in cache:
            repo = _write(tmp_path_factory.mktemp(name), files)
            out = repo / "map.json"
            run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
            cache[name] = json.loads(out.read_text())
        return cache[name]

    return run


def _by_name(bm: Map) -> dict[str, Map]:
    """The fixture's own declarations by name (an external stub can share one)."""
    return {n["name"]: n for n in bm["nodes"]
            if n.get("path") and n["kind"] != "external_symbol"}


def _calls_from(bm: Map, caller: str) -> list[Map]:
    ids = {n["id"] for n in bm["nodes"] if n["name"] == caller}
    return [e for e in bm["edges"] if e["type"] == "calls" and e["src"] in ids]


def _module_slot(external_id: str) -> str:
    """The module slot of an external_symbol id: ``lang:<module>:0-0:name:kind``."""
    return external_id.split(":0-0:", 1)[0].split(":", 1)[1]


# ---------------------------------------------------------------------------
# ADR-0027 -- Symbol.kind
# ---------------------------------------------------------------------------
#: "Symbol.kind names the source-language syntactic construct that the symbol
#: represents. Properties derivable from edges or framework metadata are
#: queried from those structures rather than smuggled into the kind label."
#:
#: Falsifiable two ways on output. (1) A kind that is not on the
#: language_construct section names something else. (2) Metamorphic: giving a
#: declaration a framework role (a route decorator, an ORM base class, a test
#: base class) changes what it participates in, not what construct it is, so
#: the kind must not move.

_KIND_TWINS: dict[str, tuple[dict[str, str], list[tuple[str, str]]]] = {
    "python": ({
        "app.py": (
            "from flask import Flask\nfrom celery import shared_task\n"
            "from django.db import models\n\n"
            "app = Flask(__name__)\n\n\n"
            "def plain():\n    return 1\n\n\n"
            "@app.route('/a')\ndef routed():\n    return 'a'\n\n\n"
            "@shared_task\ndef tasked():\n    return 1\n\n\n"
            "class Plain:\n    def m(self):\n        return 1\n\n\n"
            "class Model(models.Model):\n    def m(self):\n        return 1\n"
        ),
    }, [("plain", "routed"), ("plain", "tasked"), ("Plain", "Model")]),
    "java": ({
        "src/App.java": (
            "import org.springframework.web.bind.annotation.RestController;\n"
            "import org.springframework.web.bind.annotation.GetMapping;\n\n"
            "class Plain { int m() { return 1; } }\n\n"
            "@RestController\nclass Ctl {\n"
            "    @GetMapping(\"/a\")\n    String routed() { return \"a\"; }\n"
            "    String plainMethod() { return \"b\"; }\n}\n"
        ),
    }, [("Plain", "Ctl"), ("Ctl.plainMethod", "Ctl.routed")]),
    "typescript": ({
        "app.ts": (
            "import express from 'express';\nconst app = express();\n"
            "export function plain(): number { return 1; }\n"
            "export function handler(req: any, res: any): void { res.send('a'); }\n"
            "app.get('/a', handler);\n"
        ),
    }, [("plain", "handler")]),
}

_FRAMEWORK_MARKS = ("concepts", "decorators", "framework_role")


def kind_twin_violations(nodes: dict[str, Map], pairs: Iterable[tuple[str, str]]) -> list[str]:
    """Pairs whose framework twin carries a different kind from the plain one."""
    return [
        f"{plain} is {nodes[plain]['kind']} but {twin} is {nodes[twin]['kind']}"
        for plain, twin in pairs
        if nodes[plain]["kind"] != nodes[twin]["kind"]
    ]


def unregistered_kinds(bm: Map) -> set[str]:
    canonical = {spec.name for spec in symbol_kinds_on_axis(AXIS_LANGUAGE_CONSTRUCT)}
    return {n["kind"] for n in bm["nodes"]} - canonical


class TestKindChecker:
    def test_it_reports_a_kind_that_follows_the_framework_role(self) -> None:
        nodes = {"plain": {"kind": "function"}, "routed": {"kind": "route"}}
        assert kind_twin_violations(nodes, [("plain", "routed")]) == [
            "plain is function but routed is route"
        ]

    def test_it_reports_a_kind_off_the_language_construct_section(self) -> None:
        assert unregistered_kinds({"nodes": [{"kind": "event_publisher"}]}) == {
            "event_publisher"
        }


class TestKindIsTheConstruct:
    @pytest.mark.parametrize("language", sorted(_KIND_TWINS))
    def test_a_framework_role_does_not_move_the_kind(self, survey, language: str) -> None:
        files, pairs = _KIND_TWINS[language]
        nodes = _by_name(survey(f"kind_{language}", files))
        for _plain, twin in pairs:  # reach: the twin really was given a role
            meta = nodes[twin].get("meta") or {}
            assert any(k in meta for k in _FRAMEWORK_MARKS), (twin, sorted(meta))
        assert kind_twin_violations(nodes, pairs) == []

    @pytest.mark.parametrize("language", sorted(_KIND_TWINS))
    def test_every_emitted_kind_is_a_language_construct(self, survey, language: str) -> None:
        bm = survey(f"kind_{language}", _KIND_TWINS[language][0])
        assert unregistered_kinds(bm) == set()


# ---------------------------------------------------------------------------
# ADR-0028 -- Edge.evidence_type
# ---------------------------------------------------------------------------
#: "Edge.evidence_type names the inference pathway by which the analyzer
#: concluded this edge exists. Properties of the dst's resolvedness ... are
#: queried from siblings (Edge.is_resolved) ... not smuggled into the evidence
#: label."
#:
#: Metamorphic: two bare calls of the same construct in one function, one to a
#: callee defined in the file and one to a name defined nowhere. Resolution
#: differs, the inference pathway does not, so the label must not.

_GO_MOD = "module example.com/m\n\ngo 1.21\n"
_CARGO = '[package]\nname = "m"\nversion = "0.1.0"\n'

_CALL_PAIRS: dict[str, dict[str, str]] = {
    "python": {"a.py": "def in_tree():\n    return 1\n\n\ndef caller():\n    in_tree()\n    missing()\n"},
    "java": {"A.java": "class A {\n  int inTree() { return 1; }\n  void caller() { inTree(); missing(); }\n}\n"},
    "typescript": {"a.ts": "function inTree(): number { return 1; }\nexport function caller(): void { inTree(); missing(); }\n"},
    "php": {"a.php": "<?php\nfunction in_tree() { return 1; }\nfunction caller() { in_tree(); missing(); }\n"},
    "go": {"go.mod": _GO_MOD, "a.go": "package main\n\nfunc inTree() int { return 1 }\n\nfunc caller() { inTree(); missing() }\n\nfunc main() { caller() }\n"},
    "swift": {"a.swift": "func inTree() -> Int { return 1 }\nfunc caller() { _ = inTree(); missing() }\n"},
    "kotlin": {"a.kt": "fun inTree(): Int = 1\nfun caller() { inTree(); missing() }\n"},
    "rust": {"Cargo.toml": _CARGO, "src/lib.rs": "fn in_tree() -> i32 { 1 }\npub fn caller() { in_tree(); missing(); }\n"},
    "ruby": {"a.rb": "def in_tree\n  1\nend\n\ndef caller\n  in_tree()\n  missing()\nend\n"},
    "csharp": {"a.cs": "class C {\n  static int InTree() { return 1; }\n  static void Caller() { InTree(); Missing(); }\n}\n"},
    "scala": {"a.scala": "object O {\n  def inTree(): Int = 1\n  def caller(): Unit = { inTree(); missing() }\n}\n"},
    "cpp": {"a.cpp": "int in_tree() { return 1; }\nvoid caller() { in_tree(); missing(); }\n"},
}
_CALLERS = {"java": "A.caller", "csharp": "C.Caller", "scala": "O.caller"}

#: Measured 2026-10-27: each analyzer's resolver arm emits ``ast_call`` (with
#: ``call_construct``) and the shared unresolved emitter ``ast_call_direct``.
_RESOLUTION_LABEL_HOLES = dict.fromkeys(
    ("go", "swift", "kotlin", "rust", "ruby", "csharp", "scala", "cpp"),
    "WI-sukug: the resolved bare call is ast_call, the unresolved one ast_call_direct",
)


def resolution_label_violations(calls: list[Map]) -> list[str]:
    """Evidence labels that differ between the resolved and unresolved call."""
    labels = {e["is_resolved"]: e["meta"].get("evidence_type") for e in calls}
    if labels.get(True) == labels.get(False):
        return []
    return [f"resolved={labels.get(True)} unresolved={labels.get(False)}"]


class TestEvidenceLabelChecker:
    def test_it_reports_a_label_that_tracks_resolution(self) -> None:
        calls = [
            {"is_resolved": True, "meta": {"evidence_type": "ast_call"}},
            {"is_resolved": False, "meta": {"evidence_type": "ast_call_direct"}},
        ]
        assert resolution_label_violations(calls) == [
            "resolved=ast_call unresolved=ast_call_direct"
        ]


@pytest.mark.parametrize("language", [
    pytest.param(lang, marks=pytest.mark.xfail(strict=True, reason=_RESOLUTION_LABEL_HOLES[lang]))
    if lang in _RESOLUTION_LABEL_HOLES else lang
    for lang in sorted(_CALL_PAIRS)
])
def test_a_bare_call_label_does_not_encode_resolution(survey, language: str) -> None:
    bm = survey(f"calls_{language}", _CALL_PAIRS[language])
    calls = _calls_from(bm, _CALLERS.get(language, "caller"))
    assert sorted(e["is_resolved"] for e in calls) == [False, True]  # reach
    assert resolution_label_violations(calls) == []


# ---------------------------------------------------------------------------
# ADR-0051 -- the module key
# ---------------------------------------------------------------------------
#: "The module key names the static owner path of the called symbol ... It is
#: not a property of the call site: not the receiver's variable name, not a
#: set of candidates, and not a marker for the absence of an answer."
#:
#: Metamorphic: the same method reached through two differently-named locals
#: of one recovered type must get the same key, and the key must not be either
#: variable's name. Each fixture is one whose receiver type the analyzer
#: recovers, so the key is an owner path rather than the ``external``
#: sentinel; a sentinel slot is the documented uncertainty notion (WI-marok),
#: which this property does not test.

_RECEIVER_RENAMES: dict[str, tuple[dict[str, str], str, tuple[str, str], tuple[str, str]]] = {
    "python": ({"a.py": (
        "import pathlib\n\n\ndef read_a():\n    path = pathlib.Path('x')\n    return path.read_text()\n\n\n"
        "def read_b():\n    p = pathlib.Path('x')\n    return p.read_text()\n"
    )}, "read_text", ("read_a", "read_b"), ("path", "p")),
    "java": ({"A.java": (
        "import java.io.BufferedReader;\nimport java.io.FileReader;\n"
        "class A {\n"
        "  String readA() throws Exception { BufferedReader reader = new BufferedReader(new FileReader(\"f\")); return reader.readLine(); }\n"
        "  String readB() throws Exception { BufferedReader br = new BufferedReader(new FileReader(\"f\")); return br.readLine(); }\n}\n"
    )}, "readLine", ("A.readA", "A.readB"), ("reader", "br")),
    "go": ({"go.mod": _GO_MOD, "a.go": (
        "package main\n\nimport \"os\"\n\n"
        "func readA() { f, _ := os.Open(\"x\"); buf := make([]byte, 4); f.Read(buf) }\n\n"
        "func readB() { file, _ := os.Open(\"x\"); buf := make([]byte, 4); file.Read(buf) }\n\n"
        "func main() { readA(); readB() }\n"
    )}, "Read", ("readA", "readB"), ("f", "file")),
    "kotlin": ({"A.kt": (
        "import java.io.File\nfun readA() { val file = File(\"x\"); file.readText() }\n"
        "fun readB() { val f = File(\"x\"); f.readText() }\n"
    )}, "readText", ("readA", "readB"), ("file", "f")),
    "rust": ({"Cargo.toml": _CARGO, "src/lib.rs": (
        "use std::fs::File;\nuse std::io::Read;\n"
        "pub fn read_a() { let mut file = File::open(\"x\").unwrap(); let mut s = String::new(); file.read_to_string(&mut s).unwrap(); }\n"
        "pub fn read_b() { let mut f = File::open(\"x\").unwrap(); let mut s = String::new(); f.read_to_string(&mut s).unwrap(); }\n"
    )}, "read_to_string", ("read_a", "read_b"), ("file", "f")),
    "swift": ({"a.swift": (
        "import Foundation\n\n"
        "func readA(session: URLSession) { let task = session.dataTask(with: URL(string: \"http://x\")!); task.resume() }\n"
        "func readB(s: URLSession) { let t = s.dataTask(with: URL(string: \"http://x\")!); t.resume() }\n"
    )}, "dataTask", ("readA", "readB"), ("session", "s")),
}

_SENTINELS = {"external", "unresolved"}


def module_key_violations(keys: dict[str, str], receivers: tuple[str, str]) -> list[str]:
    """Keys that differ by caller, or that name one of the receiver variables."""
    found = []
    if len(set(keys.values())) != 1:
        found.append(f"keys differ by receiver: {keys}")
    found += [f"{caller} keyed by its receiver {key!r}" for caller, key in keys.items() if key in receivers]
    return found


class TestModuleKeyChecker:
    def test_it_reports_a_receiver_variable_in_the_slot(self) -> None:
        assert module_key_violations({"a": "resp", "b": "r"}, ("resp", "r")) == [
            "keys differ by receiver: {'a': 'resp', 'b': 'r'}",
            "a keyed by its receiver 'resp'",
            "b keyed by its receiver 'r'",
        ]


@pytest.mark.parametrize("language", sorted(_RECEIVER_RENAMES))
def test_the_module_key_does_not_follow_the_receiver_name(survey, language: str) -> None:
    files, method, callers, receivers = _RECEIVER_RENAMES[language]
    bm = survey(f"recv_{language}", files)
    keys = {}
    for caller in callers:
        (edge,) = [e for e in _calls_from(bm, caller) if e["dst"].split(":0-0:", 1)[-1].startswith(f"{method}:")]
        keys[caller] = _module_slot(edge["dst"])
    assert not set(keys.values()) & _SENTINELS, keys  # reach: the type was recovered
    assert module_key_violations(keys, receivers) == []


# ---------------------------------------------------------------------------
# ADR-0041 -- directness and ecosystem
# ---------------------------------------------------------------------------
#: directness "records the declaration relationship between the project's
#: manifests and an external dependency"; ecosystem "names the provenance class
#: of an external dependency's distribution channel". supply_chain_summary
#: counts every external node under both keys, so every node it counts must
#: carry both stamps, or the count's ``unknown`` bucket is a reader with no
#: producer. (The tier axiom's own falsification test is INV-kutub's
#: test_in_tree_declaration_tier.py.)

_PROJECT = {
    "pyproject.toml": '[project]\nname = "eco"\nversion = "0.1.0"\ndependencies = ["requests"]\n',
    "eco/__init__.py": (
        "import json\nimport os\nimport requests\nimport yaml\n\n\n"
        "def f():\n    return json.dumps(os.environ.get('X')), requests.get('u'), yaml.safe_load('a')\n"
    ),
}
_STAMPS = ("directness", "ecosystem")


def unstamped(nodes: Iterable[Map]) -> list[tuple[str, str]]:
    """(node name, missing stamp) for every tier-3 node missing a stamp."""
    return [
        (n["name"], key)
        for n in nodes
        if (n.get("supply_chain") or {}).get("tier") == 3
        for key in _STAMPS
        if key not in (n.get("meta") or {})
    ]


class TestStampChecker:
    def test_it_reports_a_tier_three_node_without_a_stamp(self) -> None:
        node = {"name": "x", "supply_chain": {"tier": 3}, "meta": {"ecosystem": "stdlib"}}
        assert unstamped([node]) == [("x", "directness")]


class TestDirectnessAndEcosystem:
    def test_every_imported_external_carries_both_stamps(self, survey) -> None:
        bm = survey("supply", _PROJECT)
        imported = [n for n in bm["nodes"] if n["kind"] == "external_symbol"]
        assert {n["name"] for n in imported} >= {"json", "requests", "yaml"}  # reach
        assert unstamped(imported) == []

    @pytest.mark.xfail(strict=True, reason="INV-fuhin: a kind=dependency declaration carries neither stamp")
    def test_a_dependency_declaration_carries_both_stamps(self, survey) -> None:
        bm = survey("supply", _PROJECT)
        declarations = [n for n in bm["nodes"] if n["kind"] == "dependency"]
        assert [n["name"] for n in declarations] == ["requests"]  # reach
        assert unstamped(declarations) == []
        summary = bm["supply_chain_summary"]["external_dep"]
        assert "unknown" not in summary["directness"]
        assert "unknown" not in summary["ecosystem"]


# ---------------------------------------------------------------------------
# Pass-silence reasons (ADR-0054 / ADR-0056)
# ---------------------------------------------------------------------------
#: "A pass-silence reason names WHY a pass produced no symbols and no edges."
#: So a run that emitted a symbol or an edge carries no reason, and
#: ``no_candidate_files`` -- whose one state is "nothing to find" -- is claimed
#: only by a run that analysed no file. Checked over every fixture survey above.

_SURVEYED: list[tuple[str, dict[str, str]]] = (
    [(f"kind_{lang}", files) for lang, (files, _pairs) in _KIND_TWINS.items()]
    + [(f"calls_{lang}", files) for lang, files in _CALL_PAIRS.items()]
    + [("supply", _PROJECT)]
)


def silence_violations(runs: Iterable[Map]) -> list[str]:
    found = []
    for run in runs:
        reason = run.get("silence_reason")
        if reason and (run.get("nodes_emitted") or run.get("edges_emitted")):
            found.append(f"{run['pass']} emitted output and says {reason}")
        if reason == "no_candidate_files" and run.get("files_analyzed"):
            found.append(f"{run['pass']} analysed {run['files_analyzed']} file(s) and says no_candidate_files")
    return found


class TestSilenceChecker:
    def test_it_reports_a_reason_on_a_run_with_output(self) -> None:
        runs = [{"pass": "p", "silence_reason": "no_candidate_files", "nodes_emitted": 2,
                 "files_analyzed": 1}]
        assert silence_violations(runs) == [
            "p emitted output and says no_candidate_files",
            "p analysed 1 file(s) and says no_candidate_files",
        ]


@pytest.mark.parametrize("name", [name for name, _files in _SURVEYED])
def test_a_silence_reason_only_explains_silence(survey, name: str) -> None:
    bm = survey(name, dict(_SURVEYED)[name])
    runs = bm["analysis_runs"]
    assert any(r.get("nodes_emitted") for r in runs)  # reach: a pass produced output
    assert any(r.get("silence_reason") == "no_candidate_files" for r in runs)  # and one had nothing
    assert silence_violations(runs) == []


# ---------------------------------------------------------------------------
# ADR-0031 -- Symbol.language
# ---------------------------------------------------------------------------
#: "Symbol.language names the source language in which this Symbol's
#: declaration appears, or None if the Symbol does not represent a declaration
#: in any source language." A node in a source file that carries a language
#: must carry that file's language.

_EXTENSION_LANGUAGE = {
    ".py": "python", ".java": "java", ".ts": "typescript", ".php": "php",
    ".go": "go", ".swift": "swift", ".kt": "kotlin", ".rs": "rust", ".rb": "ruby",
    ".cs": "csharp", ".scala": "scala", ".cpp": "cpp",
}


def language_violations(nodes: Iterable[Map]) -> list[str]:
    found = []
    for n in nodes:
        expected = _EXTENSION_LANGUAGE.get(os.path.splitext(n.get("path") or "")[1])
        if expected and n.get("language") is not None and n["language"] != expected:
            found.append(f"{n['name']} in {n['path']} is {n['language']}")
    return found


class TestLanguageChecker:
    def test_it_reports_a_language_that_is_not_the_files(self) -> None:
        assert language_violations([{"name": "f", "path": "a.ts", "language": "javascript"}]) == [
            "f in a.ts is javascript"
        ]


@pytest.mark.parametrize("name", [name for name, _files in _SURVEYED])
def test_a_declaration_carries_its_files_language(survey, name: str) -> None:
    bm = survey(name, dict(_SURVEYED)[name])
    in_source = [n for n in bm["nodes"] if os.path.splitext(n.get("path") or "")[1] in _EXTENSION_LANGUAGE]
    assert any(n.get("language") for n in in_source)  # reach
    assert language_violations(in_source) == []


# ---------------------------------------------------------------------------
# Symbol.visibility
# ---------------------------------------------------------------------------
#: The visibility axis is "one computed canonical visibility level
#: (public/private/protected/internal/package)" (ir.py, INV-jusot). The
#: language's own rule decides each row below; a row the tool reads wrong is
#: filed and held as a strict xfail.

_VISIBILITY: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "python": ({"v.py": (
        "def public_fn():\n    return 1\n\n\ndef _private_fn():\n    return 1\n\n\n"
        "class K:\n    def pub(self):\n        return 1\n\n    def __priv(self):\n        return 1\n"
    )}, {"public_fn": "public", "_private_fn": "private", "K.pub": "public", "K.__priv": "private"}),
    "java": ({"V.java": (
        "class V {\n  public int pub() { return 1; }\n  private int priv() { return 1; }\n"
        "  protected int prot() { return 1; }\n  int pkg() { return 1; }\n}\n"
    )}, {"V.pub": "public", "V.priv": "private", "V.prot": "protected", "V.pkg": "package"}),
    "go": ({"go.mod": _GO_MOD, "v.go": (
        "package v\n\nfunc Exported() int { return 1 }\nfunc unexported() int { return 1 }\n"
    )}, {"Exported": "public", "unexported": "private"}),
    "kotlin": ({"k.kt": (
        "class K {\n  fun pub(): Int = 1\n  private fun priv(): Int = 1\n"
        "  internal fun inter(): Int = 1\n  protected fun prot(): Int = 1\n}\n"
    )}, {"K.pub": "public", "K.priv": "private", "K.inter": "internal", "K.prot": "protected"}),
    "csharp": ({"k.cs": (
        "public class K {\n  public int Pub() { return 1; }\n  private int Priv() { return 1; }\n"
        "  internal int Inter() { return 1; }\n  protected int Prot() { return 1; }\n"
        "  int Default() { return 1; }\n}\n"
    )}, {"K.Pub": "public", "K.Priv": "private", "K.Inter": "internal", "K.Prot": "protected",
         "K.Default": "private"}),
    "rust": ({"Cargo.toml": _CARGO, "src/lib.rs": (
        "pub fn exported() -> i32 { 1 }\nfn private_fn() -> i32 { 1 }\n"
    )}, {"exported": "public", "private_fn": "private"}),
    "typescript": ({"v.ts": (
        "export class C {\n  public pub(): number { return 1; }\n"
        "  private priv(): number { return 1; }\n  protected prot(): number { return 1; }\n}\n"
    )}, {"C.pub": "public", "C.priv": "private", "C.prot": "protected"}),
}

#: Measured 2026-10-27: one language-agnostic ``public`` default, and the TS
#: analyzer never reads the member keyword.
_VISIBILITY_HOLES = {
    ("java", "V.pkg"), ("csharp", "K.Default"), ("rust", "private_fn"),
    ("typescript", "C.priv"), ("typescript", "C.prot"),
}


@pytest.mark.parametrize("language,symbol", [
    pytest.param(lang, sym, marks=pytest.mark.xfail(
        strict=True, reason="WI-gisip: the language's own default or keyword is not read"))
    if (lang, sym) in _VISIBILITY_HOLES else (lang, sym)
    for lang, (_files, rows) in sorted(_VISIBILITY.items())
    for sym in sorted(rows)
])
def test_visibility_follows_the_languages_rule(survey, language: str, symbol: str) -> None:
    files, rows = _VISIBILITY[language]
    nodes = _by_name(survey(f"vis_{language}", files))
    assert nodes[symbol]["visibility"] == rows[symbol]


# ---------------------------------------------------------------------------
# entrypoint-kind: "kind names WHAT the entrypoint is ... evidence_type names
# HOW it was detected" (entrypoints.all_known_entrypoint_kinds)
# ---------------------------------------------------------------------------

#: One entrypoint, detected by two routes. The route shows in evidence_type
#: or in the detection mechanism (ADR-3aaa: a decorator on the definition vs
#: a registration call), and the kind must not follow it. The CLI function is
#: not named ``main``, so the main() convention does not add a third kind; the
#: package is not ``t/``, which the test-file heuristic reads as a Perl test
#: directory.
_ROUTE_TWINS: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "cli": (
        {"requirements.txt": "click\n", "tool/__init__.py": "",
         "tool/app.py": "import click\n\n\n@click.command()\ndef run():\n    print('x')\n"},
        {"pyproject.toml": '[project]\nname = "tool"\nversion = "0"\n[project.scripts]\ntool = "tool.app:run"\n',
         "tool/__init__.py": "", "tool/app.py": "def run():\n    print('x')\n"},
    ),
    "flask": (
        {"app.py": "from flask import Flask\napp = Flask(__name__)\n\n\n@app.route('/u')\ndef users():\n    return ''\n"},
        {"app.py": ("from flask import Flask\napp = Flask(__name__)\n\n\ndef users():\n    return ''\n\n\n"
                    "app.add_url_rule('/u', view_func=users)\n")},
    ),
    "express": (
        {"app.js": ("const express = require('express');\nconst app = express();\n"
                    "function users(req, res) { res.send(''); }\napp.get('/u', users);\n")},
        {"app.js": ("const express = require('express');\nconst app = express();\n"
                    "app.get('/u', function users(req, res) { res.send(''); });\n")},
    ),
}

#: The kinds whose definition names a CALLABLE target (EntrypointKind's own
#: comments; WI-tuvun split main_guard out of main_function on exactly this).
_CALLABLE_ENTRYPOINT_KINDS = frozenset({"main_function", "cli_command", "http_route"})
_CALLABLE_SYMBOL_KINDS = frozenset({"function", "method"})


def entrypoint_kinds(bm: Map) -> set[str]:
    return {ep["kind"] for ep in bm["entrypoints"]}


def callable_target_violations(bm: Map) -> list[str]:
    """Entrypoints of a callable kind whose target is not a callable."""
    nodes = {n["id"]: n for n in bm["nodes"]}
    out = []
    for ep in bm["entrypoints"]:
        target = nodes.get(ep["symbol_id"], {})
        if ep["kind"] in _CALLABLE_ENTRYPOINT_KINDS and target.get("kind") not in _CALLABLE_SYMBOL_KINDS:
            # cli_command from a manifest names the script FILE by design.
            if ep["meta"].get("evidence_type") == "manifest_declared" and target.get("kind") == "file":
                continue
            out.append(f"{ep['kind']} -> {target.get('kind')} {target.get('name')}")
    return out


class TestEntrypointChecker:
    def test_it_reports_a_callable_kind_on_a_variable(self) -> None:
        bm = {"nodes": [{"id": "v", "kind": "variable", "name": "app"}],
              "entrypoints": [{"symbol_id": "v", "kind": "main_function", "meta": {"evidence_type": "framework_pattern"}}]}
        assert callable_target_violations(bm) == ["main_function -> variable app"]


class TestEntrypointKindIsTheWhat:
    @pytest.mark.parametrize("name", sorted(_ROUTE_TWINS))
    def test_the_detection_route_does_not_move_the_kind(self, survey, name: str) -> None:
        one, two = (survey(f"ep_{name}_{i}", files) for i, files in enumerate(_ROUTE_TWINS[name]))
        assert one["entrypoints"] and two["entrypoints"]  # reach
        assert entrypoint_kinds(one) == entrypoint_kinds(two)

    def test_control_the_cli_twins_really_took_two_routes(self, survey) -> None:
        one, two = (survey(f"ep_cli_{i}", files) for i, files in enumerate(_ROUTE_TWINS["cli"]))
        routes = [{ep["meta"]["evidence_type"] for ep in bm["entrypoints"]} for bm in (one, two)]
        assert routes == [{"framework_pattern"}, {"manifest_declared"}]

    @pytest.mark.parametrize("name", ["cli", "express"])
    def test_a_callable_kind_targets_a_callable(self, survey, name: str) -> None:
        for i, files in enumerate(_ROUTE_TWINS[name]):
            assert callable_target_violations(survey(f"ep_{name}_{i}", files)) == []

    @pytest.mark.xfail(strict=True, reason="WI-dofiz: the Flask application variable is a main_function entrypoint")
    def test_a_flask_application_is_not_a_main_function(self, survey) -> None:
        bm = survey("ep_flask_0", _ROUTE_TWINS["flask"][0])
        assert "http_route" in entrypoint_kinds(bm)  # reach
        assert callable_target_violations(bm) == []


# ---------------------------------------------------------------------------
# verify-claims: ADR-0061 catalogue tiers, and the choice-shaped-source caveat
# ---------------------------------------------------------------------------

def _verify(repo: Path, *flags: str) -> dict:
    buf = io.StringIO()
    args = build_parser().parse_args(
        ["verify-claims", str(repo), "--claims", str(repo / "claims.yaml"), "--format", "json", *flags]
    )
    with redirect_stdout(buf):
        cmd_verify_claims(args)
    return json.loads(buf.getvalue())


_SECRET_CLAIM = (
    "claims:\n  - id: secret-no-network\n    text: secrets never go to the network\n"
    "    constraint:\n      taint_flow:\n        source_taint: host_secret\n"
    "        prohibited_sink_zone: network\n"
)
_SHIP = "import os\nimport mylib\n\n\ndef ship():\n    mylib.ship(os.getenv('TOKEN'))\n"


def _sink_file(trust_level: str, provenance: str) -> str:
    header = f"provenance: {provenance}\nretrieved: 2026-10-01\n" if provenance else ""
    return (
        f"{header}description: a sink row\nzone: network\ntrust_level: {trust_level}\n\n"
        "sinks:\n  python:\n    - module: mylib\n      functions: [ship]\n"
    )


def tier_of(provenance: dict, path: Path) -> list[str]:
    """Every tier ``catalog_provenance`` lists *path* under."""
    return [tier for tier, files in provenance["tiers"].items()
            if any(f.get("path") == str(path) for f in files)]


class TestTierChecker:
    def test_it_finds_every_tier_a_file_is_listed_under(self) -> None:
        provenance = {"tiers": {"in_repo": [{"path": "/r/x.yaml"}], "yours": [{"path": "/r/x.yaml"}]}}
        assert tier_of(provenance, Path("/r/x.yaml")) == ["in_repo", "yours"]


class TestCatalogueTiers:
    """ADR-0061: ruling 1, "Four tiers, named by who vouches" -- a file the
    operator names on the command line is ``yours``; ruling 5, "Every loaded
    file is disclosed by tier" -- once; ruling 2, "Community and un-opted
    in-repo rows may add findings, but never make a verdict cleaner"."""

    def test_a_flag_named_file_is_disclosed_under_exactly_one_tier(self, tmp_path: Path) -> None:
        repo = _write(tmp_path, {"app.py": _SHIP, "claims.yaml": _SECRET_CLAIM,
                                 "tools/sinks.yaml": _sink_file("untrusted", "")})
        data = _verify(repo, "--taint-sinks", str(repo / "tools/sinks.yaml"))
        assert len(tier_of(data["catalog_provenance"], repo / "tools/sinks.yaml")) == 1

    @pytest.mark.xfail(strict=True, reason="WI-dagun: an in-repo file named on the command line is tiered in_repo")
    def test_a_flag_named_file_inside_the_repo_is_yours(self, tmp_path: Path) -> None:
        repo = _write(tmp_path, {"app.py": _SHIP, "claims.yaml": _SECRET_CLAIM,
                                 "tools/sinks.yaml": _sink_file("untrusted", "")})
        data = _verify(repo, "--taint-sinks", str(repo / "tools/sinks.yaml"))
        assert tier_of(data["catalog_provenance"], repo / "tools/sinks.yaml") == ["yours"]

    def _verdicts(self, tmp_path: Path, provenance: str) -> dict[str, str]:
        verdicts = {}
        for trust in ("untrusted", "trusted"):
            repo = _write(tmp_path / trust, {"app.py": _SHIP, "claims.yaml": _SECRET_CLAIM})
            sinks = _write(tmp_path / f"{trust}-sinks", {"s.yaml": _sink_file(trust, provenance)})
            verdicts[trust] = _verify(repo, "--taint-sinks", str(sinks / "s.yaml"))["verdicts"][0]["verdict"]
        return verdicts

    def test_control_a_vouched_trusted_sink_does_clear_the_flow(self, tmp_path: Path) -> None:
        """Reach: the fixture's flow exists, and trust from YOUR file removes it."""
        assert self._verdicts(tmp_path, "") == {"untrusted": "violated", "trusted": "inconclusive"}

    @pytest.mark.xfail(strict=True, reason="WI-savoh: a community file's trust_level: trusted is honoured")
    def test_a_community_trusted_sink_does_not_make_the_verdict_cleaner(self, tmp_path: Path) -> None:
        verdicts = self._verdicts(tmp_path, "community")
        assert verdicts["trusted"] == verdicts["untrusted"] == "violated"

    def test_your_taint_channel_is_one_home_for_every_repository(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Ruling 7, "Your taint model gets one persistent home": a file in
        ``taint_sinks.d/`` is read for a repository that names nothing, and
        for a second, unrelated one, as ``yours``. What each channel does to a
        verdict is ``test_taint_channels_are_read.py`` (WI-mimap)."""
        for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
            monkeypatch.setenv(var, str(tmp_path / var.lower()))
        files = {"app.py": _SHIP, "claims.yaml": _SECRET_CLAIM}
        before = _verify(_write(tmp_path / "zero", files))["verdicts"][0]["verdict"]
        assert before != "violated"  # control: without the file there is no sink
        sink = _write(tmp_path / "xdg_config_home/hypergumbo/taint_sinks.d",
                      {"s.yaml": _sink_file("untrusted", "")}) / "s.yaml"
        for name in ("one", "two"):
            data = _verify(_write(tmp_path / name, files))
            assert data["verdicts"][0]["verdict"] == "violated"
            assert tier_of(data["catalog_provenance"], sink) == ["yours"]


#: verify_claims.py: a finding is "rooted at a source whose value the far side
#: CHOSE from a constrained set" (CHOICE_SHAPED_SOURCE_NAMES) -- a reading of
#: untrusted input. So the choice_shaped_source caveat belongs only on a claim
#: about untrusted_input. The checker reads the claim, not the witness row.
#: Whether the caveat reads a collapsed row's group or its witness is the
#: member-order test in TestCollapsedRowIsItsGroup below.

def _claims(*pairs: tuple[str, str, str]) -> str:
    body = "".join(
        f"  - id: {cid}\n    text: {cid}\n    constraint:\n      taint_flow:\n"
        f"        source_taint: {label}\n        prohibited_sink_zone: {zone}\n"
        for cid, label, zone in pairs
    )
    return "claims:\n" + body


def choice_caveat_violations(data: dict, labels: dict[str, str]) -> list[str]:
    return [
        f"{v['claim_id']} ({labels[v['claim_id']]}) carries choice_shaped_source"
        for v in data["verdicts"]
        if any(c["kind"] == "choice_shaped_source" for c in v.get("caveats", []))
        and labels[v["claim_id"]] != "untrusted_input"
    ]


class TestChoiceCaveatChecker:
    def test_it_reports_the_caveat_on_a_non_untrusted_input_claim(self) -> None:
        data = {"verdicts": [{"claim_id": "c", "caveats": [{"kind": "choice_shaped_source"}]}]}
        assert choice_caveat_violations(data, {"c": "host_description"}) == [
            "c (host_description) carries choice_shaped_source"
        ]


class TestChoiceShapedSource:
    def test_control_an_accepted_peer_is_choice_shaped(self, tmp_path: Path) -> None:
        repo = _write(tmp_path, {
            "claims.yaml": _claims(("in-no-subprocess", "untrusted_input", "subprocess")),
            "app.py": (
                "import socket\nimport subprocess\n\n\ndef serve():\n    srv = socket.socket()\n"
                "    conn, addr = srv.accept()\n    subprocess.run(conn, shell=True)\n"
            ),
        })
        (verdict,) = _verify(repo)["verdicts"]
        assert verdict["verdict"] == "violated"
        assert "choice_shaped_source" in {c["kind"] for c in verdict["caveats"]}

    @pytest.mark.xfail(strict=True, reason="WI-zakil part 3: gethostname (host_info_read) still counts as choice-shaped")
    def test_the_caveat_is_only_on_an_untrusted_input_claim(self, tmp_path: Path) -> None:
        labels = {"desc-no-network": "host_description"}
        repo = _write(tmp_path, {
            "claims.yaml": _claims(("desc-no-network", "host_description", "network")),
            "app.py": (
                "import socket\n\n\ndef leak():\n    s = socket.create_connection(('example.com', 80))\n"
                "    name = socket.gethostname()\n    s.send(name.encode())\n"
            ),
        })
        data = _verify(repo)
        assert data["verdicts"][0]["verdict"] == "violated"  # reach
        assert choice_caveat_violations(data, labels) == []


#: taint.collapse_unadjudicated_flows: a structural or ddg_mixed row stands for
#: a GROUP, and its scalar fields are the first member's, a witness. Every
#: question verify-claims asks of the group must come out the same whichever
#: member is first. Member order is call order, so each twin swaps two lines.
#: The calls go through a helper so the flow is ddg_mixed and collapses (a
#: direct data dependence is ``ddg`` and passes through uncollapsed).

def _choice_twin(order: tuple[str, str]) -> dict[str, str]:
    calls = {"recv": "    a = s.recv(10)\n", "accept": "    b, _ = s.accept()\n"}
    return {
        "claims.yaml": _claims(("in-no-subprocess", "untrusted_input", "subprocess")),
        "app.py": ("import socket\nimport subprocess\n\n\ndef run_it(x):\n    subprocess.run(x, shell=True)\n\n\n"
                   "def serve():\n    s = socket.socket()\n" + "".join(calls[k] for k in order)
                   + "    run_it(a + str(b))\n"),
    }


_DOM_SINK = ("description: a sink row\nzone: dom_injection\ntrust_level: untrusted\n\n"
             "sinks:\n  javascript:\n    - module: zlib\n      functions: [gzipSync]\n")


def _evaluation_twin(order: tuple[str, str]) -> dict[str, str]:
    """A yours dom_injection sink on an enumerated module (zlib) and
    ``document.write``, both reached by a secret through one helper."""
    calls = {"zlib": "  zlib.gzipSync(x);\n", "write": "  document.write(x);\n"}
    return {
        "claims.yaml": _claims(("secret-no-network", "host_secret", "network")),
        "app.js": ("const zlib = require('zlib');\n\nfunction out(x) {\n" + "".join(calls[k] for k in order)
                   + "}\n\nfunction render() {\n  const data = process.env.TOKEN;\n  out(data);\n}\n\n"
                   "module.exports = { render };\n"),
    }


class TestCollapsedRowIsItsGroup:
    _CHOICE_ORDERS = (("recv", "accept"), ("accept", "recv"))
    _EVALUATION_ORDERS = (("zlib", "write"), ("write", "zlib"))

    def _choice(self, tmp_path: Path) -> list[dict]:
        return [_verify(_write(tmp_path / o[0], _choice_twin(o)))["verdicts"][0] for o in self._CHOICE_ORDERS]

    def _evaluation(self, tmp_path: Path) -> list[str]:
        sinks = _write(tmp_path / "sinks", {"s.yaml": _DOM_SINK}) / "s.yaml"
        return [_verify(_write(tmp_path / o[0], _evaluation_twin(o)), "--taint-sinks", str(sinks))["verdicts"][0]["verdict"]
                for o in self._EVALUATION_ORDERS]

    def test_control_the_two_reads_collapse_into_one_row(self, tmp_path: Path) -> None:
        for verdict in self._choice(tmp_path):
            (row,) = verdict["evidence"]
            assert (row["analysis_method"], row["collapsed_flow_count"]) == ("ddg_mixed", 2)
            assert row["source_primitives"] == ["socket.socket.accept", "socket.socket.recv"]

    @pytest.mark.xfail(strict=True, reason="WI-kufos (2) / WI-zakil part 2: the caveat tests the witness source")
    def test_the_choice_caveat_does_not_depend_on_the_witness(self, tmp_path: Path) -> None:
        kinds = [{c["kind"] for c in v["caveats"]} for v in self._choice(tmp_path)]
        assert kinds[0] == kinds[1]

    def test_control_the_secret_reaching_document_write_withdraws_the_qualification(self, tmp_path: Path) -> None:
        """INV-dudal: with ``document.write`` as the witness the opaque-site
        qualification is withdrawn, so the fixture reaches the gate."""
        assert self._evaluation(tmp_path)[1] == "inconclusive"

    @pytest.mark.xfail(strict=True, reason="WI-kufos (1): the INV-dudal withdrawal reads the witness sink")
    def test_the_evaluation_withdrawal_does_not_depend_on_the_witness(self, tmp_path: Path) -> None:
        assert self._evaluation(tmp_path) == ["inconclusive", "inconclusive"]


# ---------------------------------------------------------------------------
# ADR-0031 Class B: a route marker is a synthetic stand-in for the ROUTE
# (WI-mupal, owner ruling 2026-10-27)
# ---------------------------------------------------------------------------

#: One fixture per route-marker producer: the javascript and go analyzers,
#: the ruby and python (Django) analyzers, and route-materializer (spring).
_ROUTE_PRODUCERS: dict[str, tuple[str, dict[str, str]]] = {
    "express": ("javascript", {"app.js": ("const express = require('express');\nconst app = express();\n"
                                          "function users(req, res) { res.send(''); }\napp.get('/u', users);\n")}),
    "go": ("go", {"go.mod": "module x\n",
                  "main.go": ("package main\nimport \"net/http\"\n"
                              "func handler(w http.ResponseWriter, r *http.Request) {}\n"
                              "func main() { http.HandleFunc(\"/a\", handler) }\n")}),
    "rails": ("ruby", {"Gemfile": "gem 'rails'\n",
                       "config/routes.rb": "Rails.application.routes.draw do\n  get '/a', to: 'users#index'\nend\n",
                       "app/controllers/users_controller.rb": "class UsersController < ApplicationController\n  def index; end\nend\n"}),
    "django": ("python", {"requirements.txt": "django\n", "app/views.py": "def users(request):\n    return None\n",
                          "app/urls.py": "from django.urls import path\nfrom . import views\n\nurlpatterns = [path('u/', views.users)]\n"}),
    "spring": ("java", {"src/main/java/a/Ctl.java": (
        "package a;\nimport org.springframework.web.bind.annotation.*;\n@RestController\npublic class Ctl {\n"
        "  @GetMapping(\"/a\")\n  public String routed() { return \"\"; }\n}\n")}),
}

_ROUTE_ROLES = frozenset({"route", "route_mount", "route_include"})


def route_markers(bm: Map) -> list[Map]:
    return [n for n in bm["nodes"] if (n.get("meta") or {}).get("framework_role") in _ROUTE_ROLES]


def class_b_violations(markers: Iterable[Map], host: str) -> list[str]:
    """A marker that is not a Class-B stand-in discovered in *host*."""
    want = {"kind": "function", "language": None, "discovery_language": host, "protocol_origin": "http"}
    return [f"{m['name']}: {k}={m.get(k)!r}" for m in markers for k, v in want.items() if m.get(k) != v]


class TestClassBChecker:
    def test_it_reports_a_marker_that_carries_its_host_language(self) -> None:
        marker = {"name": "GET /a", "kind": "function", "language": "go", "discovery_language": None,
                  "protocol_origin": None}
        assert class_b_violations([marker], "go") == [
            "GET /a: language='go'", "GET /a: discovery_language=None", "GET /a: protocol_origin=None",
        ]


class TestRouteMarkerIsAStandIn:
    @pytest.mark.parametrize("name", sorted(_ROUTE_PRODUCERS))
    def test_a_route_marker_is_a_class_b_stand_in(self, survey, name: str) -> None:
        host, files = _ROUTE_PRODUCERS[name]
        markers = route_markers(survey(f"route_{name}", files))
        assert markers  # reach
        assert class_b_violations(markers, host) == []

    def test_a_file_holding_only_route_markers_keeps_its_anchor(self, survey) -> None:
        """A Rails config/routes.rb holds nothing but route markers. With the
        markers' language None the file-anchor pass skipped it, and on chatwoot
        the file node and its 623 contains edges vanished."""
        bm = survey("route_rails", _ROUTE_PRODUCERS["rails"][1])
        (anchor,) = [n for n in bm["nodes"] if n["kind"] == "file" and n["path"] == "config/routes.rb"]
        contained = {e["dst"] for e in bm["edges"] if e["type"] == "contains" and e["src"] == anchor["id"]}
        assert {m["id"] for m in route_markers(bm)} <= contained

    # What the marker's rank must not lose with its ``language`` (the language
    # weight, and the library-export demotion) is pinned metamorphically in
    # hypergumbo-core's test_route_marker_host_language.py.

    @pytest.mark.parametrize("name", ["express", "spring"])
    def test_dead_code_does_not_report_a_route_marker(self, tmp_path: Path, name: str) -> None:
        """Before WI-mupal, dead-code-maybe listed the express and spring
        markers ("GET /u", "GET /a") as dead functions: they carried a host
        language, so the INV-disin protocol_origin filter let them in, and
        nothing calls a route. The handler is the callable, and it stays a
        candidate."""
        from hypergumbo_core.cli import main

        repo = _write(tmp_path / name, _ROUTE_PRODUCERS[name][1])
        out = io.StringIO()
        with redirect_stdout(out):
            assert main(["dead-code-maybe", str(repo)]) == 0
        report = out.getvalue()
        assert "Production functions: 1" in report  # reach: the handler is counted
        assert not any(m in report for m in ("GET /u", "GET /a"))
