# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-tusad enforcement: symbols of any kind without a producer stable_id.

WHAT IT MEASURES. ADR-0035 §1 gives every analyzer-emitted symbol one of three
identities: a producer formula (``compute_stable_id`` / ``make_typed_stable_id``:
scope chain plus structural inputs), the backstop FLOOR key
``make_declaration_stable_id(kind, language, path, name)`` when no producer
computed one and that key is unique in the file, or null when the floor key is
shared. A floor value carries no arity, decorators or scope chain, and a null is
an identity the run could not back. That is the WI-tusad defect: two same-name
overloads, clauses or nested functions in one file null each other, and adding
a sibling turns a floor value into a null. It is not confined to callables: the
same ``ARG`` in two Dockerfile stages, ``.btn`` under two ``@media`` rules, a
``[linux]`` and a ``[macos]`` just recipe did the same, and a symbol a linker
mints with a language (a gRPC service, a view template) is minted after the
backstop ran and was null outright (INV-fufij).

:func:`floor_census` counts, per ``(language, kind)`` over EVERY kind (the gate
began with the callable and member kinds; B8a widened it), the records riding
the floor (``floor``: the id equals its recomputed floor key) and the records
left null (``null``). It reads ``analyze.base.floor_stable_id_key``, the one
definition the backstop stamps with and ``spec_validator.compute_stable_id_stats``
discloses with, so the gate cannot drift from either. A table kind (``file``,
``class``, ``template`` ...) has no floor key, so only its nulls count. ``null``
counts every null with a language, not only the floor's abstentions: a null is
a null to a consumer joining on the field, whoever left it. The pipeline runs
with ``include_docs``, because the default view drops the doc and config kinds
(CSS selectors, TOML tables, LaTeX sections) the census must see.

WHY A SECOND CHECK, ON DELETION. A zero on one sample says nothing about an
overloading language until a same-name sibling exists (the 2026-10-03 audit:
adding one overload turned cuda/glsl/julia/fortran from 0 nulls into 2). And a
producer formula can still tie two siblings: the C-family ``parameter_list``
sits under the declarator, so ``compute_stable_id`` on the definition hashes
``param_count 0`` for both overloads, the per-file split pass then numbers them
by emission order, and deleting the first renumbers the second. So each
language's fixture carries an EARLIER same-name sibling, and
:func:`sibling_deletion_moves` compares a run with it against a run without it:
every symbol (of any kind) that survives the deletion must hold the same
non-null stable_id in both, matched on its own position. A null in either run is reported as well, because the
contract the campaign ratchets to is "no null", not "no change"; so is a
surviving symbol with no counterpart (an analyzer that drops a same-name
sibling, or one whose span is wrong, as Twig's was). A producer that hashed
LINE NUMBERS (the doc/markup family on ``make_doc_stable_id``) or an
occurrence index failed here on ANY earlier deletion; fixing a value a producer
already computes is a scheme bump (ADR-0035 §6), and the WI-tusad bump did it
once for every language this check listed.

WHY A THIRD RUN, IN ANOTHER DIRECTORY. ``stable_id`` must not depend on where
the repository is checked out: ADR-0035 hashes the repo-relative path. A
producer that folded the ABSOLUTE path (Solidity's every id, Kotlin's top-level
``val``) gave the same file two identities in two clones, and the deletion
check above only caught it by accident (its two runs sit in two directories).
:func:`location_dependent_ids` compares the with-sibling run against the same
files written under a deeper, differently named root, and must find nothing.

**The static companion.** :func:`pass_mints_without_stable_id` scans every
``Symbol(...)`` minted outside a language analyzer (core linkers and passes, the
alternative backends, ``manifest_targets``): one that carries a language must
pass a ``stable_id``, because the floor runs before the linkers and the Class-B
chokepoint only stamps ``language=None`` stand-ins. A fixture reaches a few such
mints (the gRPC service, servicer and stub; a Django view's template; a
``Package.swift``, Gradle and Cabal target, written as ``SiblingCase.extra``
files); the scan reaches the rest.

THE RATCHET. The live gate runs the full pipeline over one fixture per language,
twice, and holds both results against ``.ci/stable-id-floor-baseline.json``:
the census (``test_floor_census_matches_baseline``) and the set of languages
failing the deletion check (``test_sibling_deletion_matches_baseline``).
:func:`census_breaches` and :func:`set_breaches` fail on a count above its
baseline (a regression, or a language the baseline does not list) AND on one
below it (stale: ratchet the baseline down in the same change), the idiom of
``producer_coherence.ratchet_diff``. A language that fails is LISTED in the
baseline, never skipped or xfailed: the campaign ends with both sections empty.

WHY THE GATE LIVES IN THIS FILE. Its only caller is this test. Production code
with test-only callers is what the WI-ratuv ratchet
(``.ci/test-only-reachability-baseline.json``, shrink-only) forbids adding, so
the comparison functions sit beside the corpus instead of in
``hypergumbo_core``; the unit tests below pin them so the gate cannot pass
vacuously.

**What each fixture holds.** The same-name form the language actually has: an
overload (cpp, csharp, java, julia, glsl, nim, ada, sql ...), a clause of
another arity or pattern (clojure), a function of another arity (elixir, whose
clauses of one arity fold into one node), a same-name function in another
scope (methods of two classes, two modules, two inner classes, two attribute
sets, LET-local operators). Where the language has no such form in one file
(elm, gleam, llvm_ir, wgsl, haxe ...), ``earlier`` is an earlier declaration of
another name, so the check still proves a deletion moves nothing. Each fixture
also carries a member where the analyzer emits one, and the doc/config
fixtures their nesting (two stages, two ``@media`` rules, two branches of a
conditional, two plays). :data:`SCOPE_CASES` adds a second fixture for the
languages whose ids can tie across two same-name ENCLOSING scopes: nested
types and their members (INV-fabih: ``A.Inner.go`` / ``B.Inner.go``), two
namespace or module blocks, a local function in two functions. The forms are
recorded per language in the WI-tusad ledger, with the census they produced.
Languages whose analyzer emits only file nodes (html, yaml) have no fixture:
there is nothing for them to count.

**Why the corpus is written at runtime.** As for ``test_doc_family_id_parity``:
the fixtures deliberately reproduce defects (nulls, moved ids), and committing
them under the tree would make hypergumbo's own self-tree analysis carry them;
analyzing under ``tests/fixtures/`` would also subject them to test-path
heuristics.

**Why it runs on more than its imports.** This test reaches every analyzer
through the pipeline and imports none of them, so ``.ci/coverage-only-deps.txt``
maps each covered analyzer to it; a change to any of them runs this gate.
"""
from __future__ import annotations

import ast
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import pytest

from hypergumbo_core.analyze.base import floor_stable_id_key, make_declaration_stable_id
from hypergumbo_core.analyze.registry import ensure_discovered
from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import Span, Symbol

# -- the gate ------------------------------------------------------------------

#: Census cell names, in report order.
FLOOR = "floor"
NULL = "null"

Census = dict[str, dict[str, dict[str, int]]]


def _get(record: Any, name: str) -> Any:
    """A field of a ``Symbol`` or of a behavior-map node dict."""
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


def floor_census(records: Iterable[Any]) -> Census:
    """``{language: {kind: {"floor": n, "null": m}}}`` over every kind.

    Only non-zero cells appear, so an empty dict means no record rides the
    floor or is null. A record without a language is skipped: its identity
    comes from ``make_synthetic_symbol_identity``, not from a language
    producer. A table kind (``file``, ``class``, ``template`` ...) has no
    floor key, so only its nulls can count: a null there is a symbol minted
    after the backstop ran (a linker's) that stamped no identity.
    """
    counts: Counter[tuple[str, str, str]] = Counter()
    for rec in records:
        kind = _get(rec, "kind")
        language = _get(rec, "language")
        if not kind or not language:
            continue
        sid = _get(rec, "stable_id")
        if sid is None:
            counts[(language, kind, NULL)] += 1
            continue
        floor_key = floor_stable_id_key(
            kind, language, _get(rec, "path"), _get(rec, "name"),
        )
        if sid == floor_key:
            counts[(language, kind, FLOOR)] += 1
    census: Census = {}
    for (language, kind, cell), n in sorted(counts.items()):
        census.setdefault(language, {}).setdefault(kind, {})[cell] = n
    return census


def census_breaches(live: Census, baseline: Census) -> list[str]:
    """Every ``(language, kind, cell)`` whose live count differs from its baseline.

    Above the baseline (an unlisted cell has baseline 0) is a regression; below
    it is a stale baseline entry that must be ratcheted down in the same change.
    Returns ``[]`` when they agree.
    """
    breaches: list[str] = []
    languages = sorted(set(live) | set(baseline))
    for language in languages:
        kinds = sorted(set(live.get(language, {})) | set(baseline.get(language, {})))
        for kind in kinds:
            have = live.get(language, {}).get(kind, {})
            want = baseline.get(language, {}).get(kind, {})
            for cell in (FLOOR, NULL):
                n, ceiling = have.get(cell, 0), want.get(cell, 0)
                if n > ceiling:
                    what = "not in the baseline" if ceiling == 0 else f"baseline {ceiling}"
                    breaches.append(
                        f"REGRESSION {language} {kind} {cell}: {n} ({what}). A "
                        "producer must compute the ADR-0035 formula for this kind."
                    )
                elif n < ceiling:
                    breaches.append(
                        f"STALE {language} {kind} {cell}: {n} < baseline {ceiling}. "
                        "Ratchet the baseline down to the live count."
                    )
    return breaches


def set_breaches(live_failing: Iterable[str], baselined: Iterable[str]) -> list[str]:
    """The deletion check's ratchet: the failing set must equal the baseline set."""
    live, base = set(live_failing), set(baselined)
    breaches = [
        f"REGRESSION {lang}: deleting an earlier same-name sibling moved or "
        "nulled another symbol's stable_id, and the language is not baselined."
        for lang in sorted(live - base)
    ]
    breaches += [
        f"STALE {lang}: now passes the deletion check; remove it from the baseline."
        for lang in sorted(base - live)
    ]
    return breaches


@dataclass(frozen=True)
class SiblingCase:
    """One language's fixture: ``head + earlier + tail``, and ``head + tail``.

    ``earlier`` holds a same-name sibling of something in ``tail`` (an overload,
    a clause of another arity, a same-name function in another scope) or, for a
    grammar with no such form, an earlier declaration of another name.

    A surviving symbol is matched across the two runs by kind, name, start
    line and start column, a line in ``tail`` moving up by ``shift`` (default:
    the line count of ``earlier``). ``boundary`` is the last line not shifted
    (default: the line count of ``head``). A notebook overrides both: its
    symbol lines number the cells' source, not the JSON holding them.

    ``extra`` holds other files written beside the fixture, unchanged in both
    runs, as ``(path relative to the language directory, text)`` pairs: the
    manifests and companion sources a pass outside the language analyzer
    reads (a ``Package.swift``, a gRPC servicer). Their symbols are counted by
    the census; the deletion check reads the fixture file only.
    """

    filename: str
    head: str
    earlier: str
    tail: str
    boundary: Optional[int] = None
    shift: Optional[int] = None
    extra: tuple[tuple[str, str], ...] = ()

    def with_sibling(self) -> str:
        """The file with the earlier sibling."""
        return self.head + self.earlier + self.tail

    def without_sibling(self) -> str:
        """The file with the earlier sibling deleted."""
        return self.head + self.tail

    def line_before_deletion(self, line: int) -> int:
        """Where a symbol at ``line`` of :meth:`without_sibling` started before."""
        boundary = self.head.count("\n") if self.boundary is None else self.boundary
        shift = self.earlier.count("\n") if self.shift is None else self.shift
        return line if line <= boundary else line + shift


def _start(record: Any) -> tuple[int, int]:
    """``(start_line, start_col)`` of a record's span; ``(0, 0)`` without one."""
    span = _get(record, "span")
    if isinstance(span, Mapping):
        return int(span.get("start_line", 0)), int(span.get("start_col", 0))
    if span is None:
        return 0, 0
    return int(getattr(span, "start_line", 0)), int(getattr(span, "start_col", 0))


def sibling_deletion_moves(
    case: SiblingCase,
    with_sibling: Sequence[Any],
    without_sibling: Sequence[Any],
) -> list[str]:
    """What deleting ``case.earlier`` did to the other symbols of the file.

    ``with_sibling`` / ``without_sibling`` are the file's records from the two
    runs. Returns one line per symbol (of any kind) of the second run that has
    no counterpart in the first (an analyzer that dropped a same-name sibling
    shows up here), whose stable_id is null in either run, or whose stable_id
    differs between them. ``[]`` means no identity moved.

    Each survivor is matched on its own position: ``(kind, name, start line,
    start column)``, the line mapped back across the deletion. Two records of
    one kind and name at one position are compared in emission order. A
    ``file`` node at line 1 starts there whatever the deletion did, so that
    line is never mapped (a file node starting later, a Vue ``<script>``
    block's, maps like any symbol).
    """
    before: dict[tuple[Any, Any, int, int], list[Any]] = {}
    for rec in with_sibling:
        line, col = _start(rec)
        key = (_get(rec, "kind"), _get(rec, "name"), line, col)
        before.setdefault(key, []).append(rec)
    seen: Counter[tuple[Any, Any, int, int]] = Counter()
    problems: list[str] = []
    for rec in without_sibling:
        kind, name = _get(rec, "kind"), _get(rec, "name")
        line, col = _start(rec)
        was_at = line if (kind, line) == ("file", 1) else case.line_before_deletion(line)
        key = (kind, name, was_at, col)
        matches = before.get(key, [])
        index = seen[key]
        seen[key] += 1
        if index >= len(matches):
            problems.append(f"{kind} {name} (line {line}): no counterpart before the deletion")
            continue
        was, now = _get(matches[index], "stable_id"), _get(rec, "stable_id")
        if was is None or now is None:
            problems.append(f"{kind} {name} (line {line}): stable_id {was} -> {now} (null)")
        elif was != now:
            problems.append(f"{kind} {name} (line {line}): stable_id {was} -> {now} (moved)")
    return problems


def location_dependent_ids(
    here: Sequence[Any], elsewhere: Sequence[Any],
) -> list[str]:
    """Symbols whose stable_id differs between two checkouts of the same files.

    ``here`` and ``elsewhere`` are the records of one run each over identical
    file contents written under two different roots (paths are repo-relative,
    so the records pair up). Each record is matched on ``(path, kind, name,
    start line, start column)``, same-position records in emission order.
    Returns one line per record whose identity differs or has no counterpart;
    ``[]`` means no stable_id depends on the checkout location.
    """
    others: dict[tuple[Any, ...], list[Any]] = {}
    for rec in elsewhere:
        key = (_get(rec, "path"), _get(rec, "kind"), _get(rec, "name"), *_start(rec))
        others.setdefault(key, []).append(rec)
    seen: Counter[tuple[Any, ...]] = Counter()
    problems: list[str] = []
    for rec in here:
        key = (_get(rec, "path"), _get(rec, "kind"), _get(rec, "name"), *_start(rec))
        index = seen[key]
        seen[key] += 1
        matches = others.get(key, [])
        there = _get(matches[index], "stable_id") if index < len(matches) else "(no counterpart)"
        if there != _get(rec, "stable_id"):
            problems.append(
                f"{key[0]} {key[1]} {key[2]} (line {key[3]}): "
                f"{_get(rec, 'stable_id')} here, {there} elsewhere"
            )
    return problems


# -- the corpus ----------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_PATH = REPO_ROOT / ".ci" / "stable-id-floor-baseline.json"

_NB_CELL_1 = (
    '{"cell_type": "code", "metadata": {}, "source": ["def run(x):\\n", '
    '"    return x\\n"], "outputs": [], "execution_count": null},\n'
)
_NB_CELL_2 = (
    '{"cell_type": "code", "metadata": {}, "source": ["def run(x, y):\\n", '
    '"    return x\\n", "def helper():\\n", "    return 2\\n"], "outputs": [], '
    '"execution_count": null}\n], "metadata": {"language_info": {"name": '
    '"python"}}, "nbformat": 4, "nbformat_minor": 5}\n'
)

# language -> fixture. Keyed by the language the analyzer stamps, which is also
# the directory the fixture is written under.
CASES: dict[str, SiblingCase] = {
    "ada": SiblingCase(
        "src/demo.adb", "package body Demo is\n",
        "   function Run return Integer is begin return 1; end Run;\n",
        "   function Run (X : Integer) return Integer is begin return X; end Run;\n"
        "   procedure Helper is begin null; end Helper;\nend Demo;\n"),
    "agda": SiblingCase(
        "src/Demo.agda",
        "module Demo where\n\ndata Nat : Set where\n  zero : Nat\n  suc : Nat -> Nat\n\n",
        "module A where\n  run : Nat -> Nat\n  run x = x\n\n",
        "module B where\n  run : Nat -> Nat\n  run x = suc x\n\nhelper : Nat\nhelper = zero\n\n"
        "record Pair : Set where\n  field\n    fst : Nat\n\n"
        "record Box : Set where\n  field\n    fst : Nat\n"),
    "ansible": SiblingCase(
        "playbooks/site.yml", "---\n- name: Web\n  hosts: web\n  tasks:\n",
        "    - name: Install\n      apt:\n        name: nginx\n",
        "    - name: Start\n      service:\n        name: nginx\n  handlers:\n"
        "    - name: Restart\n      service:\n        name: nginx\n"
        "- name: Db\n  hosts: db\n  vars:\n    port: 5432\n  tasks:\n"
        "    - name: Install\n      apt:\n        name: postgresql\n"),
    "apex": SiblingCase(
        "src/Demo.cls", "public class Demo {\n",
        "    public Integer run() { return 1; }\n",
        "    public Integer run(Integer x) { return x; }\n"
        "    public Integer size { get; set; }\n}\n"),
    "asm": SiblingCase(
        "src/demo.s", ".text\n", ".globl first\nfirst:\n    ret\n",
        ".globl run\nrun:\n    ret\nhelper:\n    ret\n"),
    "astro": SiblingCase(
        "src/pages/index.astro", "---\nconst title = 'Demo';\n",
        "const first = 1;\n",
        "const second = 2;\n---\n<main><slot name=\"header\" /><h1>{title}</h1></main>\n"),
    "bash": SiblingCase(
        "bin/demo.sh", "#!/bin/bash\n", "outer_a() {\n  run() { echo 1; }\n}\n",
        "outer_b() {\n  run() { echo 2; }\n}\nhelper() { echo 3; }\n"),
    "bibtex": SiblingCase(
        "doc/refs.bib", "",
        "@article{first,\n  title = {First},\n}\n",
        "@book{second,\n  title = {Second},\n}\n@misc{third,\n  title = {Third},\n}\n"),
    "bitbake": SiblingCase(
        "meta/recipes/demo_1.0.bb", "SUMMARY = \"demo\"\n",
        "SRC_URI = \"file://a\"\n",
        "LICENSE = \"MIT\"\ndo_install() {\n    install -d ${D}\n}\n"),
    "blade": SiblingCase(
        "resources/views/page.blade.php", "",
        "@section('sidebar')\n<p>side</p>\n@endsection\n",
        "@section('content')\n<p>body</p>\n@endsection\n@yield('footer')\n<x-alert/>\n"),
    "c": SiblingCase(
        "src/mod.c", "", "struct a { int v; };\nstatic int run_a(void) { return 1; }\n",
        "struct b { int v; };\nstatic int run_b(int x) { return x; }\n"
        "int helper(void) { return 2; }\n"),
    "capnp": SiblingCase(
        "src/demo.capnp", "@0xdbb9ad1f14bf0b36;\n",
        "interface A {\n  run @0 (x :Int32) -> (y :Int32);\n}\n",
        "interface B {\n  run @0 (x :Int32, z :Int32) -> (y :Int32);\n}\n"
        "struct Box {\n  size @0 :Int32;\n}\n"),
    "circom": SiblingCase(
        "src/demo.circom", "pragma circom 2.0.0;\n", "function run(x) {\n    return x;\n}\n",
        "template Helper() {\n    signal input a;\n    signal output b;\n    b <== a;\n}\n"
        "function other(x, y) {\n    return x + y;\n}\n"),
    "clojure": SiblingCase(
        "src/demo.clj", "(ns demo)\n", "(defmulti area :shape)\n(defn run [x] x)\n",
        "(defn run\n  ([x] x)\n  ([x y] (+ x y)))\n(defn helper [] 2)\n"
        "(defmethod area :circle [s] 1)\n(defmethod area :square [s] 2)\n(defmacro mk [x] x)\n"),
    "cmake": SiblingCase(
        "src/CMakeLists.txt", "cmake_minimum_required(VERSION 3.10)\n",
        "function(run x)\nendfunction()\n",
        "function(run x y)\nendfunction()\nmacro(helper)\nendmacro()\n"),
    "cobol": SiblingCase(
        "src/demo.cob", "",
        "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. FIRST.\n"
        "       PROCEDURE DIVISION.\n           GOBACK.\n       END PROGRAM FIRST.\n",
        "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. DEMO.\n"
        "       PROCEDURE DIVISION.\n           STOP RUN.\n       END PROGRAM DEMO.\n"),
    "commonlisp": SiblingCase(
        "src/demo.lisp", "", "(defmethod area ((s circle)) 1)\n",
        "(defmethod area ((s square)) 2)\n(defun helper () 2)\n"),
    "cpp": SiblingCase(
        "src/mod.cpp", "", "int run() { return 1; }\n",
        "int run(int x) { return x; }\n"
        "class Box {\n public:\n  int size() { return 0; }\n  int v;\n"
        "  virtual int find(int x) = 0;\n  virtual int find(int x, int y) = 0;\n};\n"),
    "csharp": SiblingCase(
        "src/Mod.cs", "class Mod {\n", "    int Run() { return 1; }\n",
        "    int Run(int x) { return x; }\n    public int Size { get; set; }\n"
        "    private int count;\n    public int this[int i] { get { return i; } }\n}\n"),
    "css": SiblingCase(
        "src/site.css", "",
        "@media (max-width: 600px) {\n  .btn { color: red; }\n}\n",
        "@media print {\n  .btn { color: blue; }\n}\n.btn:hover, #main { color: green; }\n"
        "@font-face { font-family: 'Demo'; src: url(d.woff); }\n"
        "@keyframes spin { from { opacity: 0; } }\n:root { --gap: 4px; }\n"),
    "cuda": SiblingCase(
        "src/demo.cu", "", "__device__ int f(int x) { return x; }\n",
        "__device__ float f(float x) { return x; }\n"
        "__global__ void kern(int *a) { a[0] = 1; }\n"),
    "d": SiblingCase(
        "src/demo.d", "module demo;\n", "int run() { return 1; }\n",
        "int run(int x) { return x; }\nstruct Box {\n  int v;\n  int size() { return 0; }\n}\n"),
    "dart": SiblingCase(
        "lib/mod.dart", "class Mod {\n", "  Mod();\n",
        "  Mod.named(int x);\n  int get size => 0;\n  set size(int v) {}\n"
        "  int run(int x) => x;\n}\nint helper() => 2;\n"),
    "dockerfile": SiblingCase(
        "docker/Dockerfile", "ARG BASE=alpine\n",
        "FROM alpine AS build\nARG VERSION\nENV MODE=build\n",
        "FROM alpine AS run\nARG VERSION\nENV MODE=run\nEXPOSE 8080\n"),
    # The clauses of one name/arity are ONE elixir function node, so the earlier
    # same-name sibling is a function of another arity.
    "elixir": SiblingCase(
        "lib/demo.ex", "defmodule Demo do\n  defstruct [:a, :b]\n",
        "  def greet(a, b, c), do: {a, b, c}\n",
        "  def greet(:a), do: 1\n  def greet(:b), do: 2\n  def greet(x, y), do: {x, y}\n"
        "  def other, do: 1\n  defmacro mk(x), do: x\nend\n"),
    "elm": SiblingCase(
        "src/Demo.elm", "module Demo exposing (..)\n\n\n",
        "first : Int\nfirst =\n    1\n\n\n",
        "run : Int -> Int\nrun x =\n    x\n\n\nhelper : Int\nhelper =\n    2\n"),
    "erlang": SiblingCase(
        "src/demo.erl", "-module(demo).\n-export([greet/1, greet/2]).\n",
        "greet(A) -> A.\n", "greet(A, B) -> {A, B}.\nother() -> 1.\n-define(MAX, 10).\n"),
    "fennel": SiblingCase(
        "src/demo.fnl", "", "(fn run [x] x)\n", "(fn run [x y] x)\n(fn helper [] 2)\n"),
    "fish": SiblingCase(
        "bin/demo.fish", "", "function first\n    echo 1\nend\n",
        "function run\n    echo 2\nend\nfunction helper\n    echo 3\nend\n"),
    "fortran": SiblingCase(
        "src/demo.f90",
        "module demo\n  interface area\n    module procedure area_i, area_r\n"
        "  end interface\ncontains\n",
        "  integer function area_i(x)\n    integer :: x\n    area_i = x\n  end function area_i\n",
        "  real function area_r(x)\n    real :: x\n    area_r = x\n  end function area_r\n"
        "  subroutine helper()\n  end subroutine helper\nend module demo\n"),
    "fsharp": SiblingCase(
        "src/Demo.fs", "module Demo\n\n", "module A =\n    let run x = x\n\n",
        "module B =\n    let run x y = x + y\n\nlet helper x = x\n"),
    "gdscript": SiblingCase(
        "src/demo.gd", "extends Node\n", "class A:\n\tfunc run():\n\t\treturn 1\n\n",
        "class B:\n\tfunc run(x):\n\t\treturn x\n\nfunc helper():\n\treturn 2\n"),
    "gitignore": SiblingCase(
        ".gitignore", "", "*.log\n", "build/\n*.tmp\n"),
    "gleam": SiblingCase(
        "src/demo.gleam", "", "fn first(x) {\n  x\n}\n",
        "pub fn run(x, y) {\n  x + y\n}\n\npub fn helper() {\n  2\n}\n"),
    "glsl": SiblingCase(
        "src/demo.frag", "#version 330\n", "float f(float x) { return x; }\n",
        "vec2 f(vec2 x) { return x; }\nvoid main() { }\n"),
    "gnuplot": SiblingCase(
        "src/plot.gp", "", "f(x) = x\n", "f(x, y) = x + y\ng(x) = x\n"),
    "go": SiblingCase(
        "src/mod.go", "package app\n\ntype A struct{ v int }\ntype B struct{ v int }\n\n",
        "func (a A) Run() int { return 1 }\n",
        "func (b B) Run(x int) int { return x }\nfunc Helper() int { return 2 }\n"),
    "graphql": SiblingCase(
        "api/schema.graphql", "",
        "input First { a: Int }\n",
        "input NewUser { name: String }\nscalar Date\ndirective @auth on FIELD_DEFINITION\n"
        "type Query { u: Int }\nunion Result = Query\nenum Role { ADMIN }\n"
        "query GetUser { u }\nfragment F on Query { u }\nmutation M { u }\n"
        "subscription S { u }\n"),
    "groovy": SiblingCase(
        "src/Demo.groovy", "class Demo {\n", "    int run() { 1 }\n",
        "    int run(int x) { x }\n}\ndef helper() { 2 }\n"),
    "hack": SiblingCase(
        "src/demo.hack", "<?hh\n", "class A {\n  public function run(): int { return 1; }\n}\n",
        "class B {\n  public function run(int $x): int { return $x; }\n}\n"
        "function helper(): int { return 2; }\n"),
    "handlebars": SiblingCase(
        "templates/page.hbs", "",
        "{{#if user}}\n  {{> header}}\n{{/if}}\n",
        "{{#if admin}}\n  {{> header}}\n{{/if}}\n{{#each items}}{{this}}{{/each}}\n"
        "{{#formatDate when}}x{{/formatDate}}\n"),
    "haskell": SiblingCase(
        "src/Demo.hs", "module Demo where\n\nclass Shape a where\n  area :: a -> Int\n\n",
        "instance Shape Int where\n  area x = x\n\n",
        "instance Shape Bool where\n  area _ = 0\n\nhelper :: Int -> Int\nhelper x = x\n",
        extra=(("demo.cabal",
                "name: demo\nversion: 0.1\n\nexecutable demo\n"
                "  main-is: Main.hs\n  hs-source-dirs: app\n"),)),
    "haxe": SiblingCase(
        "src/Demo.hx", "class Demo {\n", "  function first():Int { return 1; }\n",
        "  function run(x:Int):Int { return x; }\n  var size:Int = 0;\n}\n"),
    "hcl": SiblingCase(
        "infra/main.tf", "",
        "provider \"aws\" {\n  region = \"us-east-1\"\n}\n",
        "provider \"aws\" {\n  alias  = \"west\"\n  region = \"us-west-2\"\n}\n"
        "resource \"aws_s3_bucket\" \"b\" {\n  bucket = \"x\"\n}\n"
        "data \"aws_ami\" \"a\" {\n  most_recent = true\n}\n"
        "variable \"v\" {}\noutput \"o\" {\n  value = 1\n}\n"
        "locals {\n  l = 1\n}\nmodule \"m\" {\n  source = \"./m\"\n}\n"),
    "hlsl": SiblingCase(
        "src/demo.hlsl", "", "float f(float x) { return x; }\n",
        "float2 f(float2 x) { return x; }\nfloat4 main() : SV_Target { return 0; }\n"),
    "ini": SiblingCase(
        "conf/app.ini", "",
        "[server]\nport = 80\n",
        "[client]\nport = 8080\nhost = localhost\n"),
    "janet": SiblingCase(
        "src/demo.janet", "", "(defn run [x] x)\n", "(defn run [x y] x)\n(defn helper [] 2)\n"),
    "java": SiblingCase(
        "src/Mod.java", "public class Mod {\n", "    int run() { return 1; }\n",
        "    int run(int x) { return x; }\n    private int size;\n    Mod() {}\n}\n",
        extra=(("build.gradle",
                "plugins { id 'application' }\napplication {\n"
                "    mainClass = 'com.demo.Main'\n}\n"),)),
    "javascript": SiblingCase(
        "src/mod.js", "", "class A {\n  run() { return 1; }\n}\n",
        "class B {\n  run(x) { return x; }\n  get size() { return 0; }\n}\n"
        "function helper() { return 2; }\nconst arrow = () => 3;\n"),
    "json": SiblingCase(
        "web/package.json", "{\n  \"name\": \"demo\",\n",
        "  \"bin\": {\"demo\": \"cli.js\"},\n",
        "  \"dependencies\": {\"lodash\": \"1.0.0\"},\n"
        "  \"peerDependencies\": {\"react\": \"18.0.0\"}\n}\n"),
    "jsonnet": SiblingCase(
        "src/lib.jsonnet", "", "local first(x) = x;\n",
        "local helper() = 2;\n{\n  run(x):: x,\n  other(x, y):: x,\n}\n"),
    "julia": SiblingCase(
        "src/demo.jl", "", "f(x::Int) = x\n",
        "f(x::String) = length(x)\nfunction helper()\n    2\nend\n"
        "function g end\nfunction Base.show(io::IO, x::Int)\n    print(io, x)\nend\n"),
    "jupyter": SiblingCase(
        "nb/demo.ipynb", '{"cells": [\n', _NB_CELL_1, _NB_CELL_2, boundary=0, shift=3),
    "just": SiblingCase(
        "justfile", "",
        "[linux]\nbuild:\n    echo linux\n",
        "[macos]\nbuild:\n    echo mac\nalias b := build\nversion := \"1\"\n"
        "test *args:\n    echo {{args}}\n"),
    "kdl": SiblingCase(
        "conf/app.kdl", "",
        "server {\n  port 80\n}\n",
        "client {\n  port 8080\n}\n"),
    "kotlin": SiblingCase(
        "src/Mod.kt", "class Mod {\n", "    fun run(): Int = 1\n",
        "    fun run(x: Int): Int = x\n    val size: Int = 0\n}\nfun helper(): Int = 2\n"
        "object Registry {\n    fun go(): Int = 1\n}\nval limit: Int = 3\n"),
    "latex": SiblingCase(
        "doc/main.tex",
        "\\documentclass{article}\n\\newcommand{\\foo}{bar}\n"
        "\\newenvironment{note}{}{}\n\\begin{document}\n",
        "\\section{Intro}\n\\subsection{Setup}\n\\label{sec:a}\n",
        "\\section{Usage}\n\\subsection{Setup}\n\\label{sec:b}\n\\end{document}\n"),
    "lean": SiblingCase(
        "src/Demo.lean", "", "namespace A\ndef run (x : Nat) : Nat := x\nend A\n",
        "namespace B\ndef run (x y : Nat) : Nat := x + y\nend B\ndef helper : Nat := 2\n"),
    "llvm_ir": SiblingCase(
        "src/demo.ll", "", "define i32 @first(i32 %x) {\n  ret i32 %x\n}\n",
        "define i32 @run(i32 %x, i32 %y) {\n  ret i32 %x\n}\n"
        "define i32 @helper() {\n  ret i32 2\n}\n"),
    "lua": SiblingCase(
        "src/demo.lua", "local A = {}\nlocal B = {}\n", "function A.run(x) return x end\n",
        "function B.run(x, y) return x + y end\nfunction helper() return 2 end\n"),
    "luau": SiblingCase(
        "src/demo.luau", "local A = {}\nlocal B = {}\n",
        "function A.run(x: number): number return x end\n",
        "function B.run(x: number, y: number): number return x + y end\n"
        "local function helper(): number return 2 end\n"),
    "makefile": SiblingCase(
        "Makefile", "CC = gcc\n",
        "ifeq ($(OS),Windows_NT)\nall:\n\techo win\nendif\n",
        "ifneq ($(OS),Windows_NT)\nall:\n\techo unix\nendif\n%.o: %.c\n\t$(CC) -c $<\n"
        ".PHONY: all\ndefine greet\necho hi\nendef\n"),
    "markdown": SiblingCase(
        "docs/guide.md", "# Guide\n\n",
        "## Install\n\nRun it.\n\n",
        "## Usage\n\nSee [home](index.md).\n\n```sh\nmake\n```\n"),
    "matlab": SiblingCase(
        "src/demo.m", "", "function y = first(x)\n  y = x;\nend\n",
        "function y = run(x)\n  y = x;\nend\nfunction y = helper()\n  y = 2;\nend\n"),
    "mermaid": SiblingCase(
        "docs/flow.mmd", "",
        "sequenceDiagram\n  participant Alice\n",
        "flowchart TD\n  Alice[Start]\n  B{Check}\n"
        "stateDiagram\n  state \"Idle\" as S1\nclassDiagram\n  class Shape\n"),
    "meson": SiblingCase(
        "meson.build", "project('demo', 'c')\n",
        "executable('first', 'a.c')\n",
        "executable('second', 'b.c')\nlibrary('util', 'u.c')\n"),
    "nim": SiblingCase(
        "src/demo.nim", "", "proc run(x: int): int = x\n",
        "proc run(x: string): int = len(x)\nproc helper(): int = 2\n"),
    "nix": SiblingCase(
        "pkgs/default.nix", "{\n", "  a = { run = x: x; };\n",
        "  b = { run = x: y: x; };\n  helper = x: x;\n}\n"),
    "objc": SiblingCase(
        "src/Mod.m", "@implementation Mod\n", "- (int)run { return 1; }\n",
        "- (int)run:(int)x { return x; }\n@end\nint helper(void) { return 2; }\n"),
    "ocaml": SiblingCase(
        "src/demo.ml", "", "module A = struct\n  let run x = x\nend\n",
        "module B = struct\n  let run x y = x + y\nend\nlet helper x = x\n"),
    "odin": SiblingCase(
        "src/demo.odin", "package demo\n\n", "run_i :: proc(x: int) -> int { return x }\n",
        "run_s :: proc(x: string) -> int { return 1 }\nhelper :: proc() -> int { return 2 }\n"),
    "pascal": SiblingCase(
        "src/demo.pas", "unit Demo;\ninterface\nimplementation\n",
        "function Run: Integer; overload;\nbegin\n  Result := 1;\nend;\n",
        "function Run(X: Integer): Integer; overload;\nbegin\n  Result := X;\nend;\n"
        "procedure Helper;\nbegin\nend;\nend.\n"),
    "perl": SiblingCase(
        "lib/Demo.pm", "package Demo;\n", "sub run { return 1; }\n",
        "package Other;\nsub run { return 2; }\nsub helper { return 3; }\n1;\n"),
    "php": SiblingCase(
        "src/Mod.php", "<?php\n", "class A {\n    function run() { return 1; }\n}\n",
        "class B {\n    private $size = 0;\n    function run($x) { return $x; }\n}\n"
        "function helper() { return 2; }\n"),
    "pony": SiblingCase(
        "src/demo.pony", "class Box\n", "  fun run(): U64 => 1\n",
        "  fun helper(x: U64): U64 => x\n  new create() => None\n"),
    "powershell": SiblingCase(
        "src/demo.ps1", "", "function Run-It { param($x) $x }\n",
        "class Box {\n  [int] Size() { return 0 }\n}\nfunction Get-Helper { 2 }\n"),
    "prisma": SiblingCase(
        "prisma/schema.prisma", "datasource db {\n  provider = \"postgresql\"\n}\n",
        "model First {\n  id Int @id\n}\n",
        "model User {\n  id Int @id\n}\nenum Role {\n  ADMIN\n}\n"),
    "properties": SiblingCase(
        "conf/app.properties", "", "server.port=80\n", "client.port=8080\nname=demo\n"),
    "proto": SiblingCase(
        "api/demo.proto", 'syntax = "proto3";\npackage demo;\nmessage Req { int32 x = 1; }\n',
        "service A {\n  rpc Get(Req) returns (Req);\n}\n",
        "service B {\n  rpc Get(Req) returns (Req);\n}\n"),
    "puppet": SiblingCase(
        "manifests/init.pp", "",
        "class first {\n}\n",
        "class demo {\n  file { '/tmp/x':\n    ensure => present,\n  }\n}\n"
        "define demo::thing($name) {\n}\n"),
    "purescript": SiblingCase(
        "src/Demo.purs", "module Demo where\n\nclass Shape a where\n  area :: a -> Int\n\n",
        "instance shapeInt :: Shape Int where\n  area x = x\n\n",
        "instance shapeBool :: Shape Boolean where\n  area _ = 0\n\n"
        "helper :: Int\nhelper = 2\n"),
    "python": SiblingCase(
        "src/mod.py", "", "class A:\n    def run(self):\n        return 1\n\n\n",
        "class B:\n    def run(self, x):\n        return x\n\n    @property\n"
        "    def size(self):\n        return 0\n\n\ndef helper():\n    return 2\n",
        # The gRPC linker's servicer class / stub function (INV-fufij), and a
        # Django view whose template the view-template linker mints.
        extra=(
            ("src/greeter.py",
             "import demo_pb2_grpc\n\n\n"
             "class GreeterServicer(demo_pb2_grpc.GreeterServicer):\n    pass\n\n\n"
             "def connect(channel):\n    return demo_pb2_grpc.GreeterStub(channel)\n"),
            ("requirements.txt", "django\n"),
            ("app/views.py",
             "from django.shortcuts import render\n\n\n"
             "def index(request):\n    return render(request, \"app/page.html\")\n"),
            ("app/templates/app/page.html", "<html><body>hi</body></html>\n"),
        )),
    "qml": SiblingCase(
        "src/Demo.qml", "import QtQuick 2.0\nItem {\n", "    function run() { return 1 }\n",
        "    Rectangle {\n        function run(x) { return x }\n    }\n"
        "    function helper(x) { return x }\n    property int size: 0\n}\n"),
    "r": SiblingCase(
        "R/demo.R", "", "run <- function(x) x\n",
        "run <- function(x, y) x + y\nhelper <- function() 2\n"),
    "racket": SiblingCase(
        "src/demo.rkt", "#lang racket\n", "(module+ a\n  (define (run x) x))\n",
        "(module+ b\n  (define (run x y) x))\n(define (helper) 2)\n"),
    "requirements": SiblingCase(
        "py/requirements.txt", "", "flask==2.0\n", "requests>=2.0\nnumpy\n"),
    "robot": SiblingCase(
        "tests/suite.robot", "*** Variables ***\n${NAME}    demo\n\n*** Test Cases ***\n",
        "First Test\n    Log    one\n",
        "Second Test\n    My Keyword\n\n*** Keywords ***\nMy Keyword\n    Log    kw\n"),
    "rst": SiblingCase(
        "docs/index.rst", "Guide\n=====\n\n",
        "Install\n-------\n\nRun it.\n\n",
        "Usage\n-----\n\n.. _usage:\n\n.. note::\n   See it.\n"),
    "ruby": SiblingCase(
        "lib/mod.rb", "", "class A\n  def run\n    1\n  end\nend\n",
        "class B\n  attr_reader :size\n  def run(x)\n    x\n  end\nend\ndef helper\n  2\nend\n"),
    "rust": SiblingCase(
        "src/lib.rs", "struct A;\nstruct B;\n", "impl A {\n    fn run(&self) -> i32 { 1 }\n}\n",
        "impl B {\n    fn run(&self, x: i32) -> i32 { x }\n}\nfn helper() -> i32 { 2 }\n"),
    "scala": SiblingCase(
        "src/Mod.scala", "class Mod {\n", "  def run(): Int = 1\n",
        "  def run(x: Int): Int = x\n  val size: Int = 0\n}\n"
        "class A {\n  object Inner { def go(): Int = 1 }\n}\n"
        "class B {\n  object Inner { def go(): Int = 2 }\n}\n"
        "object Helper {\n  def go(): Int = 2\n}\n"),
    "scheme": SiblingCase(
        "src/demo.scm", "", "(define (run x) x)\n",
        "(define (run x y) (+ x y))\n(define (helper) 2)\n"),
    "scss": SiblingCase(
        "src/main.scss", "", "@function first($x) { @return $x; }\n",
        "@function run($x, $y) { @return $x; }\n@mixin box($w) { width: $w; }\n"),
    "smithy": SiblingCase(
        "model/main.smithy", "$version: \"2\"\nnamespace example.demo\n",
        "structure First { id: String }\n",
        "service Demo { version: \"1\", operations: [Get] }\n"
        "operation Get { input: GetInput }\nstructure GetInput { id: String }\n"),
    "solidity": SiblingCase(
        "src/Demo.sol", "pragma solidity ^0.8.0;\ncontract Demo {\n",
        "    function run() public pure returns (uint) { return 1; }\n",
        "    function run(uint x) public pure returns (uint) { return x; }\n"
        "    uint public size;\n}\n"
        "library Math {\n    function add(uint a, uint b) internal pure returns (uint) "
        "{ return a + b; }\n}\n"),
    "sparql": SiblingCase(
        "queries/q.rq", "PREFIX ex: <http://example.org/>\n",
        "PREFIX foaf: <http://xmlns.com/foaf/0.1/>\n",
        "SELECT ?s WHERE { ?s ex:p ?o . }\n"),
    "sql": SiblingCase(
        "db/demo.sql", "",
        "CREATE FUNCTION run(x integer) RETURNS integer AS $$ SELECT x $$ LANGUAGE sql;\n",
        "CREATE FUNCTION run(x integer, y integer) RETURNS integer AS $$ SELECT x $$ "
        "LANGUAGE sql;\nCREATE PROCEDURE helper() LANGUAGE sql AS $$ SELECT 1 $$;\n"),
    "starlark": SiblingCase(
        "build/defs.bzl", "", "def first(x):\n    return x\n",
        "def run(x, y):\n    return x\n\ndef helper():\n    return 2\n"),
    "svelte": SiblingCase(
        "src/App.svelte", "<script>\n  let count = 0;\n",
        "  let first = 1;\n",
        "  function inc() { count += 1; }\n</script>\n"
        "<button on:click={inc}>{count}</button>\n{#if count}<slot />{/if}\n"),
    "swift": SiblingCase(
        "src/Mod.swift", "class Mod {\n", "    func run() -> Int { return 1 }\n",
        "    func run(x: Int) -> Int { return x }\n    var size: Int = 0\n"
        "    var area: Int { return 0 }\n    subscript(i: Int) -> Int { return i }\n}\n"
        "func helper() -> Int { return 2 }\n",
        # manifest_targets mints the Package.swift target (INV-fufij).
        extra=(("Package.swift",
                "// swift-tools-version:5.5\nimport PackageDescription\n"
                "let package = Package(name: \"Demo\", targets: [\n"
                "    .executableTarget(name: \"demo\", path: \"Sources/demo\"),\n"
                "])\n"),)),
    "tcl": SiblingCase(
        "src/demo.tcl", "", "namespace eval a {\n  proc run {x} { return $x }\n}\n",
        "namespace eval b {\n  proc run {x y} { return $x }\n}\nproc helper {} { return 2 }\n"),
    "thrift": SiblingCase(
        "api/demo.thrift", "", "service A {\n  i32 run(1: i32 x)\n}\n",
        "service B {\n  i32 run(1: i32 x, 2: i32 y)\n}\nstruct Box {\n  1: i32 size\n}\n"),
    "tlaplus": SiblingCase(
        "spec/Demo.tla", "---- MODULE Demo ----\n", "A == LET f == 1 IN f\n",
        "B == LET f == 2 IN f\nRun(x) == x\n====\n"),
    "toml": SiblingCase(
        "rs/Cargo.toml", "[package]\nname = \"demo\"\n",
        "[[bin]]\nname = \"first\"\npath = \"src/first.rs\"\n",
        "[[bin]]\nname = \"second\"\npath = \"src/second.rs\"\n[lib]\nname = \"demolib\"\n"
        "[workspace]\nmembers = []\n[profile.release]\nlto = true\n"
        "[[test]]\nname = \"t\"\n[dependencies]\nserde = \"1\"\n"),
    "twig": SiblingCase(
        "templates/page.twig", "", "{% macro first(x) %}{{ x }}{% endmacro %}\n",
        "{% macro run(x, y) %}{{ x }}{% endmacro %}\n{% macro helper() %}2{% endmacro %}\n"),
    "typescript": SiblingCase(
        "src/mod.ts", "", "class A {\n  run(): number { return 1; }\n}\n",
        "class B {\n  count: number = 0;\n  run(x: number): number { return x; }\n}\n"
        "export function helper(): number { return 2; }\n"),
    "v": SiblingCase(
        "src/demo.v", "module demo\n\nstruct A {}\nstruct B {}\n\n",
        "fn (a A) run() int {\n\treturn 1\n}\n",
        "fn (b B) run(x int) int {\n\treturn x\n}\n\nfn helper() int {\n\treturn 2\n}\n"),
    "verilog": SiblingCase(
        "rtl/top.sv", "", "module first;\nendmodule\n",
        "module top(input clk);\n  wire a;\nendmodule\ninterface bus_if;\n  logic v;\nendinterface\n"),
    "vhdl": SiblingCase(
        "rtl/top.vhd", "library ieee;\n",
        "entity a is\nend entity a;\narchitecture rtl of a is\nbegin\nend architecture rtl;\n",
        "entity b is\nend entity b;\narchitecture rtl of b is\n"
        "  component sub is port (clk : in bit); end component;\nbegin\nend architecture rtl;\n"
        "package pkg is\nend package pkg;\n"),
    "vue": SiblingCase(
        "src/App.vue", "<template>\n  <div>\n",
        "    <p v-if=\"first\">a</p>\n",
        "    <p v-if=\"second\">b</p>\n  </div>\n</template>\n"
        "<script>\nexport default { props: ['msg'] }\n</script>\n<style>\np { color: red; }\n</style>\n"),
    "wgsl": SiblingCase(
        "src/demo.wgsl", "", "fn first(x: f32) -> f32 { return x; }\n",
        "fn run(x: f32, y: f32) -> f32 { return x; }\n@fragment\n"
        "fn main() -> @location(0) vec4<f32> { return vec4<f32>(0.0); }\n"),
    "wolfram": SiblingCase(
        "src/demo.wl", "", "run[x_Integer] := x\n",
        "run[x_String] := StringLength[x]\nhelper[] := 2\n"),
    "xml": SiblingCase(
        "app/AndroidManifest.xml",
        "<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\" package=\"com.demo\">\n",
        "  <uses-permission android:name=\"android.permission.CAMERA\"/>\n",
        "  <uses-permission android:name=\"android.permission.INTERNET\"/>\n"
        "  <application>\n    <activity android:name=\".MainActivity\">\n"
        "      <intent-filter><action android:name=\"android.intent.action.MAIN\"/></intent-filter>\n"
        "    </activity>\n    <service android:name=\".Sync\"/>\n  </application>\n</manifest>\n"),
    "zig": SiblingCase(
        "src/demo.zig", "",
        "const A = struct {\n    v: i32,\n    fn run(self: A) i32 { return self.v; }\n};\n",
        "const B = struct {\n    v: i32,\n    fn run(self: B, x: i32) i32 { return x; }\n};\n"
        "fn helper() i32 { return 2; }\n"),
}


#: Second fixtures, keyed ``<language>-<shape>`` (the key is only the directory
#: name; the census reads each record's own language). Each nests one
#: same-name scope inside two outer scopes (INV-fabih and its cousins found by
#: the WI-tusad sweep): members and types of two ``Inner`` classes, two
#: namespace or module blocks, two functions holding a same-name local
#: function, two macros, two instances. Every one of them failed the deletion
#: check before the scheme bump (an id hashed the nearest owner only, or no
#: owner at all, or a type took the bare-name table key), and the last three
#: pin the F1 cousins: a Twig ``else`` emitting an ``if`` symbol, a PureScript
#: class member with a bare type emitting nothing, a Scheme rest list.
SCOPE_CASES: dict[str, SiblingCase] = {
    # -- INV-fabih: members and types of two same-name nested owners ---------
    "scala-nested": SiblingCase(
        "src/Outer.scala", "",
        "class A {\n  class Inner {\n    def go(): Int = 1\n    val v: Int = 1\n  }\n"
        "  object Obj {\n    def go(): Int = 1\n  }\n  trait T\n}\n",
        "class B {\n  class Inner {\n    def go(): Int = 2\n    val v: Int = 2\n  }\n"
        "  object Obj {\n    def go(): Int = 2\n  }\n  trait T\n}\n"
        "object Run {\n  def a(): Int = {\n    def local(): Int = 1\n    local()\n  }\n"
        "  def b(): Int = {\n    def local(): Int = 2\n    local()\n  }\n}\n"),
    # The one-line nested body is the tree-sitter-kotlin 1.1.0 shape that
    # turned the whole class into an ERROR (kotlin._close_brace_repair).
    "kotlin-nested": SiblingCase(
        "src/Outer.kt", "",
        "class A {\n    class Inner {\n        fun go(): Int = 1\n        val v: Int = 1\n    }\n"
        "    class Line { fun go(): Int = 1 }\n    interface I {\n        fun f(): Int\n    }\n}\n",
        "class B {\n    class Inner {\n        fun go(): Int = 2\n        val v: Int = 2\n    }\n"
        "    class Line { fun go(): Int = 2 }\n    interface I {\n        fun f(): Int\n    }\n}\n"
        "fun a(): Int {\n    fun local(): Int = 1\n    return local()\n}\n"
        "fun b(): Int {\n    fun local(): Int = 2\n    return local()\n}\n"),
    "csharp-nested": SiblingCase(
        "src/Outer.cs", "",
        "class A {\n    class Inner {\n        int Go() { return 1; }\n        int f;\n"
        "        int P { get; set; }\n        enum E { X }\n    }\n}\n",
        "class B {\n    class Inner {\n        int Go() { return 2; }\n        int f;\n"
        "        int P { get; set; }\n        enum E { X }\n    }\n}\n"),
    "csharp-namespace": SiblingCase(
        "src/Spaces.cs", "",
        "namespace A {\n    class Inner {\n        int Go() { return 1; }\n        int f;\n    }\n"
        "    interface I { }\n    struct S { }\n}\n",
        "namespace B {\n    class Inner {\n        int Go() { return 2; }\n        int f;\n    }\n"
        "    interface I { }\n    struct S { }\n}\n"),
    # A property hashed its qualified name, which held the innermost
    # namespace only: ``Inner.C.P`` in namespaces A and B tied.
    "csharp-namespace-nested": SiblingCase(
        "src/Nested.cs", "",
        "namespace A {\n    namespace Inner {\n        class C {\n"
        "            int P { get; set; }\n        }\n    }\n}\n",
        "namespace B {\n    namespace Inner {\n        class C {\n"
        "            int P { get; set; }\n        }\n    }\n}\n"),
    "swift-nested": SiblingCase(
        "src/Outer.swift", "",
        "class A {\n    class Inner {\n        func go() -> Int { return 1 }\n        var v: Int = 1\n    }\n"
        "    enum E {\n        case x\n    }\n}\n",
        "class B {\n    class Inner {\n        func go() -> Int { return 2 }\n        var v: Int = 2\n    }\n"
        "    enum E {\n        case x\n    }\n}\n"
        "func a() -> Int {\n    struct Local {\n        var v: Int = 1\n    }\n"
        "    func local() -> Int { return 1 }\n    return local()\n}\n"
        "func b() -> Int {\n    struct Local {\n        var v: Int = 2\n    }\n"
        "    func local() -> Int { return 2 }\n    return local()\n}\n"),
    # Member classes were told apart by their dotted names; local classes in
    # two methods (and an anonymous class's methods) were not.
    "java-local": SiblingCase(
        "src/Outer.java", "class Outer {\n",
        "    int a() {\n        class Local {\n            int v;\n            int go() { return 1; }\n"
        "        }\n        return new Local().go();\n    }\n",
        "    int b() {\n        class Local {\n            int v;\n            int go() { return 2; }\n"
        "        }\n        return new Local().go();\n    }\n"
        "    class Inner {\n        int go() { return 3; }\n    }\n}\n"),
    "typescript-nested": SiblingCase(
        "src/outer.ts", "",
        "namespace A {\n  export class Inner {\n    v: number = 1;\n    go(): number { return 1; }\n  }\n"
        "  export interface I { x: number; }\n  export enum E { X }\n  export type T = number;\n"
        "  export function run(): number { return 1; }\n}\n",
        "namespace B {\n  export class Inner {\n    v: number = 2;\n    go(): number { return 2; }\n  }\n"
        "  export interface I { x: number; }\n  export enum E { X }\n  export type T = number;\n"
        "  export function run(): number { return 2; }\n}\n"),
    "javascript-nested": SiblingCase(
        "src/outer.js", "",
        "function a() {\n  class Inner {\n    go() { return 1; }\n  }\n"
        "  function local() { return 1; }\n  return [Inner, local];\n}\n"
        "const objA = {\n  go() { return 1; },\n};\n",
        "function b() {\n  class Inner {\n    go() { return 2; }\n  }\n"
        "  function local() { return 2; }\n  return [Inner, local];\n}\n"
        "const objB = {\n  go() { return 2; },\n};\n"),
    "php-namespace": SiblingCase(
        "src/spaces.php", "<?php\n",
        "namespace A {\n    class Inner {\n        private $v = 1;\n        const C = 1;\n"
        "        function go() { return 1; }\n    }\n    interface I {}\n    trait T {}\n}\n",
        "namespace B {\n    class Inner {\n        private $v = 2;\n        const C = 2;\n"
        "        function go() { return 2; }\n    }\n    interface I {}\n    trait T {}\n}\n"),
    "cpp-nested": SiblingCase(
        "src/outer.cpp", "",
        "class A {\n  class Inner {\n    int go() { return 1; }\n    int v;\n  };\n"
        "  enum E { X };\n};\nnamespace na {\nint run() { return 1; }\nint value = 1;\n"
        "struct S { int v; };\n}\n",
        "class B {\n  class Inner {\n    int go() { return 2; }\n    int v;\n  };\n"
        "  enum E { X };\n};\nnamespace nb {\nint run() { return 2; }\nint value = 2;\n"
        "struct S { int v; };\n}\n"),
    "rust-nested": SiblingCase(
        "src/nested.rs", "",
        "mod a {\n    struct Inner { v: i32 }\n    impl Inner {\n        fn go(&self) -> i32 { 1 }\n    }\n"
        "    enum E { X }\n    trait T { fn t(&self); }\n    fn run() -> i32 { 1 }\n}\n"
        "fn outer_a() -> i32 {\n    fn local() -> i32 { 1 }\n    local()\n}\n",
        "mod b {\n    struct Inner { v: i32 }\n    impl Inner {\n        fn go(&self) -> i32 { 2 }\n    }\n"
        "    enum E { X }\n    trait T { fn t(&self); }\n    fn run() -> i32 { 2 }\n}\n"
        "fn outer_b() -> i32 {\n    fn local() -> i32 { 2 }\n    local()\n}\n"),
    "groovy-nested": SiblingCase(
        "src/Outer.groovy", "",
        "class A {\n    class Inner {\n        int go() { 1 }\n    }\n    interface I {}\n    enum E { X }\n}\n",
        "class B {\n    class Inner {\n        int go() { 2 }\n    }\n    interface I {}\n    enum E { X }\n}\n"),
    "d-nested": SiblingCase(
        "src/outer.d", "module outer;\n",
        "class A {\n    class Inner {\n        int go() { return 1; }\n        int v;\n    }\n}\n"
        "int fa() {\n    int local() { return 1; }\n    return local();\n}\n",
        "class B {\n    class Inner {\n        int go() { return 2; }\n        int v;\n    }\n}\n"
        "int fb() {\n    int local() { return 2; }\n    return local();\n}\n"),
    "julia-nested": SiblingCase(
        "src/nested.jl", "",
        "module A\nstruct Inner\n    v::Int\nend\nlimit = 1\nmodule Sub\nend\n"
        "function run()\n    function inner()\n        1\n    end\n    inner()\nend\nend\n",
        "module B\nstruct Inner\n    v::Int\nend\nlimit = 2\nmodule Sub\nend\n"
        "function run()\n    function inner()\n        2\n    end\n    inner()\nend\nend\n"),
    "zig-nested": SiblingCase(
        "src/nested.zig", "",
        "const A = struct {\n    const Inner = struct {\n        v: i32,\n        fn go() i32 { return 1; }\n"
        "    };\n    const E = enum { x };\n};\n",
        "const B = struct {\n    const Inner = struct {\n        v: i32,\n        fn go() i32 { return 2; }\n"
        "    };\n    const E = enum { x };\n};\n"),
    "gdscript-nested": SiblingCase(
        "src/nested.gd", "extends Node\n",
        "class A:\n\tclass Inner:\n\t\tvar v = 1\n\t\tfunc go():\n\t\t\treturn 1\n\n",
        "class B:\n\tclass Inner:\n\t\tvar v = 2\n\t\tfunc go():\n\t\t\treturn 2\n"),
    "solidity-nested": SiblingCase(
        "src/Nested.sol", "pragma solidity ^0.8.0;\n",
        "contract A {\n    struct Inner { uint v; }\n    function go() public pure returns (uint) { return 1; }\n}\n",
        "contract B {\n    struct Inner { uint v; }\n    function go() public pure returns (uint) { return 2; }\n}\n"),
    "capnp-nested": SiblingCase(
        "src/nested.capnp", "@0xdbb9ad1f14bf0b38;\n",
        "struct A {\n  struct Inner {\n    v @0 :Int32;\n  }\n  enum E {\n    x @0;\n  }\n}\n",
        "struct B {\n  struct Inner {\n    v @0 :Int32;\n  }\n  enum E {\n    x @0;\n  }\n}\n"),
    "tcl-nested": SiblingCase(
        "src/nested.tcl", "",
        "namespace eval a {\n  namespace eval inner {\n    proc go {} { return 1 }\n  }\n}\n",
        "namespace eval b {\n  namespace eval inner {\n    proc go {} { return 2 }\n  }\n}\n"),
    # -- functions nested in two same-name enclosing functions ---------------
    "ada-nested": SiblingCase(
        "src/nested.adb", "package body Nested is\n",
        "   function F return Integer is begin return 1; end F;\n"
        "   procedure A is\n      procedure Inner is begin null; end Inner;\n   begin Inner; end A;\n",
        "   function F return Float is begin return 1.0; end F;\n"
        "   procedure B is\n      procedure Inner is begin null; end Inner;\n   begin Inner; end B;\n"
        "end Nested;\n"),
    "nim-nested": SiblingCase(
        "src/nested.nim", "",
        "proc a() =\n  proc inner() = discard\n  inner()\n",
        "proc b() =\n  proc inner() = discard\n  inner()\n"),
    "matlab-nested": SiblingCase(
        "src/nested.m", "",
        "function a()\n  function inner()\n  end\nend\n",
        "function b()\n  function inner()\n  end\nend\n"),
    "odin-nested": SiblingCase(
        "src/nested.odin", "package nested\n\n",
        "a :: proc() {\n\tinner :: proc() {}\n\tinner()\n}\n",
        "b :: proc() {\n\tinner :: proc() {}\n\tinner()\n}\n"),
    "lua-nested": SiblingCase(
        "src/nested.lua", "",
        "function a()\n  local function inner() return 1 end\n  return inner()\nend\n",
        "function b()\n  local function inner() return 2 end\n  return inner()\nend\n"),
    "luau-nested": SiblingCase(
        "src/nested.luau", "",
        "function a()\n  local function inner(): number return 1 end\n  local Mod = {}\n"
        "  return inner()\nend\n",
        "function b()\n  local function inner(): number return 2 end\n  local Mod = {}\n"
        "  return inner()\nend\n"),
    "starlark-nested": SiblingCase(
        "build/nested.bzl", "",
        "def a():\n    def inner():\n        return 1\n    return inner()\n\n",
        "def b():\n    def inner():\n        return 2\n    return inner()\n"),
    "powershell-nested": SiblingCase(
        "src/nested.ps1", "",
        "function A {\n  function Inner { 1 }\n  Inner\n}\n",
        "function B {\n  function Inner { 2 }\n  Inner\n}\n"),
    "dart-nested": SiblingCase(
        "lib/nested.dart", "",
        "int a() {\n  int inner() => 1;\n  return inner();\n}\n",
        "int b() {\n  int inner() => 2;\n  return inner();\n}\n"),
    "wolfram-nested": SiblingCase(
        "src/nested.wl", "",
        "a[x_] := Module[{inner}, inner[y_] := y; inner[x]]\n",
        "b[x_] := Module[{inner}, inner[y_] := y; inner[x]]\n"),
    "jsonnet-nested": SiblingCase(
        "src/nested.jsonnet", "{\n",
        "  a: { inner(x):: x },\n",
        "  b: { inner(x):: x },\n}\n"),
    # -- B4 table kinds named by their module/namespace ----------------------
    "ocaml-nested": SiblingCase(
        "src/nested.ml", "",
        "module A = struct\n  type t = int\n  module C = struct\n    let v = 1\n  end\nend\n",
        "module B = struct\n  type t = int\n  module C = struct\n    let v = 2\n  end\nend\n"),
    "lean-nested": SiblingCase(
        "src/Nested.lean", "",
        "namespace A\nstructure S where\n  x : Nat\nclass K (a : Type) where\n  k : a\nend A\n",
        "namespace B\nstructure S where\n  x : Nat\nclass K (a : Type) where\n  k : a\nend B\n"),
    # tree-sitter-agda 1.3.3 swallows the declarations after a ONE-constructor
    # data type into it; the two-constructor Nat keeps the modules apart.
    "agda-nested": SiblingCase(
        "src/Nested.agda",
        "module Nested where\n\ndata Nat : Set where\n  zero : Nat\n  suc : Nat -> Nat\n\n",
        "module A where\n  module C where\n    v : Nat\n    v = zero\n"
        "  record R : Set where\n    field\n      x : Nat\n\n",
        "module B where\n  module C where\n    v : Nat\n    v = zero\n"
        "  record R : Set where\n    field\n      x : Nat\n"),
    "fsharp-nested": SiblingCase(
        "src/Nested.fs", "module Nested\n\n",
        "module A =\n    let mutable x = 1\n\n",
        "module B =\n    let mutable x = 1\n"),
    # -- the F1 "noticed, not fixed" cousins ---------------------------------
    "twig-nested": SiblingCase(
        "templates/nested.twig", "",
        "{% macro first(a) %}{% if a %}x{% elseif b %}y{% else %}z{% endif %}{% endmacro %}\n",
        "{% macro second(a) %}{% if a %}x{% elseif b %}y{% else %}z{% endif %}{% endmacro %}\n"),
    "purescript-nested": SiblingCase(
        "src/Nested.purs", "module Nested where\n\n",
        "class Default a where\n  def :: a\n\nhelper :: Int\nhelper = 2\n\n",
        "class Sized a where\n  size :: Int\n\nother :: Int\nother = 3\n"),
    "scheme-nested": SiblingCase(
        "src/nested.scm", "", "(define (f a . rest) a)\n", "(define (g a . rest) a)\n"),
}
CASES.update(SCOPE_CASES)


def _baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text())


# -- unit: the census ----------------------------------------------------------


def _sym(name: str, kind: str, stable_id, *, path: str = "a.x", line: int = 1,
         language: str = "x") -> Symbol:
    return Symbol(
        id=f"{language}:{path}:{line}-{line}:{name}:{kind}", name=name, kind=kind,
        language=language, path=path, span=Span(line, line, 0, 1),
        stable_id=stable_id,
    )


def test_census_counts_floor_and_null_per_language_and_kind() -> None:
    floor = make_declaration_stable_id("function", "x", "a.x", "f")
    records = [
        _sym("f", "function", floor),
        _sym("g", "function", "sha256:producer00000000"),
        _sym("h", "method", None),
        _sym("h", "method", None, line=2),
        {"name": "p", "kind": "property", "language": "y", "path": "b.y",
         "stable_id": make_declaration_stable_id("property", "y", "b.y", "p")},
        _sym("C", "class", None),            # a table kind: only a null counts
        _sym("D", "class", make_declaration_stable_id("class", "x", "a.x", "D")),
        _sym("rule", "target", "sha256:producer00000001"),
        {"name": "s", "kind": "function", "language": None, "path": "c",
         "stable_id": None},                 # no language: a synthetic stand-in
        {"name": "k", "kind": None, "language": "x", "path": "c",
         "stable_id": None},                 # a partial record
    ]
    assert floor_census(records) == {
        "x": {"class": {NULL: 1}, "function": {FLOOR: 1}, "method": {NULL: 2}},
        "y": {"property": {FLOOR: 1}},
    }


def test_census_of_producer_ids_only_is_empty() -> None:
    assert floor_census([_sym("f", "function", "sha256:producer00000000")]) == {}


def test_census_breaches_fail_on_growth_new_cells_and_stale_cells() -> None:
    baseline = {"x": {"function": {FLOOR: 2, NULL: 1}}, "z": {"method": {NULL: 3}}}
    live = {"x": {"function": {FLOOR: 3}}, "y": {"field": {FLOOR: 1}},
            "z": {"method": {NULL: 3}}}
    got = census_breaches(live, baseline)
    assert got == [
        "REGRESSION x function floor: 3 (baseline 2). A producer must compute "
        "the ADR-0035 formula for this kind.",
        "STALE x function null: 0 < baseline 1. Ratchet the baseline down to the "
        "live count.",
        "REGRESSION y field floor: 1 (not in the baseline). A producer must "
        "compute the ADR-0035 formula for this kind.",
    ]
    assert census_breaches(baseline, baseline) == []


def test_set_breaches_fail_both_ways() -> None:
    got = set_breaches({"cpp", "elixir"}, {"elixir", "julia"})
    assert len(got) == 2
    assert got[0].startswith("REGRESSION cpp:")
    assert got[1].startswith("STALE julia:")
    assert set_breaches({"a"}, ["a"]) == []


# -- unit: the deletion check --------------------------------------------------

_CASE = SiblingCase("a.x", "head\n", "earlier\nearlier\n", "later\nother\n")


def test_case_variants_and_line_mapping() -> None:
    assert _CASE.with_sibling() == "head\nearlier\nearlier\nlater\nother\n"
    assert _CASE.without_sibling() == "head\nlater\nother\n"
    assert _CASE.line_before_deletion(1) == 1
    assert _CASE.line_before_deletion(2) == 4
    nb = SiblingCase("n", "{\n", "x\n", "y\n", boundary=0, shift=3)
    assert nb.line_before_deletion(1) == 4


def test_deletion_that_moves_nothing_reports_nothing() -> None:
    before = [_sym("f", "function", None, line=2), _sym("f", "function", "sha256:a", line=4),
              _sym("g", "function", "sha256:b", line=5), _sym("C", "class", "sha256:c", line=1)]
    after = [_sym("f", "function", "sha256:a", line=2), _sym("g", "function", "sha256:b", line=3),
             _sym("C", "class", "sha256:c", line=1)]
    assert sibling_deletion_moves(_CASE, before, after) == []


def test_deletion_matches_each_survivor_by_position() -> None:
    """Same kind and name on one line: the column tells them apart, and two at
    one position compare in emission order (never through a name-keyed map)."""
    def at(sid, line, col):
        return {"kind": "block", "name": "if", "stable_id": sid,
                "span": {"start_line": line, "start_col": col}}
    before = [at("sha256:x", 4, 0), at("sha256:y", 4, 9), at("sha256:z", 5, 0),
              at("sha256:w", 5, 0)]
    after = [at("sha256:x", 2, 0), at("sha256:y", 2, 9), at("sha256:z", 3, 0),
             at("sha256:moved", 3, 0), at("sha256:v", 3, 0)]
    assert sibling_deletion_moves(_CASE, before, after) == [
        "block if (line 3): stable_id sha256:w -> sha256:moved (moved)",
        "block if (line 3): no counterpart before the deletion",
    ]


def test_deletion_reports_moved_null_and_unmatched() -> None:
    before = [{"kind": "function", "name": "f", "span": {"start_line": 4},
               "stable_id": None},
              {"kind": "function", "name": "g", "span": {"start_line": 5},
               "stable_id": "sha256:b"},
              {"kind": "method", "name": "m", "span": {"start_line": 1},
               "stable_id": "sha256:m"}]
    after = [{"kind": "function", "name": "f", "span": {"start_line": 2},
              "stable_id": "sha256:f"},
             {"kind": "function", "name": "g", "span": {"start_line": 3},
              "stable_id": "sha256:moved"},
             {"kind": "method", "name": "m", "span": {"start_line": 1},
              "stable_id": "sha256:m"},
             {"kind": "function", "name": "h", "span": None, "stable_id": "x"}]
    got = sibling_deletion_moves(_CASE, before, after)
    assert got == [
        "function f (line 2): stable_id None -> sha256:f (null)",
        "function g (line 3): stable_id sha256:b -> sha256:moved (moved)",
        "function h (line 0): no counterpart before the deletion",
    ]


def test_symbol_without_span_is_line_zero() -> None:
    sym = _sym("f", "function", "sha256:a")
    sym.span = None
    assert sibling_deletion_moves(_CASE, [], [sym]) == [
        "function f (line 0): no counterpart before the deletion"]
    assert _start(sym) == (0, 0)
    head_less = SiblingCase("a.x", "", "earlier\n", "later\n")
    file_node = {"kind": "file", "name": "a.x", "stable_id": "sha256:f",
                 "span": {"start_line": 1, "start_col": 0}}
    assert sibling_deletion_moves(head_less, [file_node], [file_node]) == []
    assert _start(_sym("f", "function", "sha256:a", line=7)) == (7, 0)


def test_location_check_pairs_records_by_position() -> None:
    here = [_sym("f", "function", "sha256:a", line=1), _sym("f", "function", "sha256:b", line=2)]
    same = [_sym("f", "function", "sha256:a", line=1), _sym("f", "function", "sha256:b", line=2)]
    assert location_dependent_ids(here, same) == []
    moved = [_sym("f", "function", "sha256:a", line=1), _sym("f", "function", "sha256:z", line=2)]
    assert location_dependent_ids(here, moved) == [
        "a.x function f (line 2): sha256:b here, sha256:z elsewhere"]
    assert location_dependent_ids(here, same[:1]) == [
        "a.x function f (line 2): sha256:b here, (no counterpart) elsewhere"]


# -- live: the ratchet over the fixture corpus ---------------------------------


@pytest.fixture(scope="module")
def runs(tmp_path_factory) -> dict:
    """Both variants of every fixture through the full pipeline, nodes by file
    (and the with-sibling variant once more, checked out elsewhere)."""
    ensure_discovered()
    base = tmp_path_factory.mktemp("stable-id-floor")
    out: dict = {}
    # "elsewhere": the with-sibling files again, under a deeper root with
    # another name, for test_stable_ids_do_not_depend_on_the_checkout_location.
    roots = {"with": base / "with", "without": base / "without",
             "elsewhere": base / "another" / "checkout" / "clone"}
    for variant, root in roots.items():
        for lang, case in CASES.items():
            text = case.without_sibling() if variant == "without" else case.with_sibling()
            for rel, body in ((case.filename, text), *case.extra):
                target = root / lang / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body)
        out_path = base / f"{variant}.json"
        # include_docs: the census covers every kind, and the default view
        # drops the doc/config kinds (selectors, TOML tables, LaTeX sections).
        run_behavior_map(root, out_path=out_path, progress=False, include_docs=True)
        nodes = json.loads(out_path.read_text())["nodes"]
        by_file: dict = defaultdict(list)
        for node in nodes:
            by_file[node.get("path")].append(node)
        out[variant] = by_file
    return out


def _fixture_nodes(runs: dict, variant: str) -> list:
    """Every node of every fixture file and of the files written beside it."""
    return [n for lang, case in CASES.items()
            for rel in (case.filename, *(path for path, _ in case.extra))
            for n in runs[variant].get(f"{lang}/{rel}", [])]


def test_every_fixture_emits_a_symbol(runs) -> None:
    """A fixture whose analyzer emitted nothing but the file node (a grammar not
    installed, a file the analyzer does not claim) would pass vacuously. Each
    file written beside a fixture must yield a symbol too: it is there for one."""
    silent = [
        f"{lang}/{rel}" for lang, case in CASES.items()
        for rel in (case.filename, *(path for path, _ in case.extra))
        if not any(n.get("kind") != "file"
                   for n in runs["with"].get(f"{lang}/{rel}", []))
    ]
    assert silent == []


def test_floor_census_matches_baseline(runs) -> None:
    live = floor_census(_fixture_nodes(runs, "with"))
    breaches = census_breaches(live, _baseline()["floor_and_null"])
    assert breaches == [], (
        "stable_id floor ratchet (WI-tusad) breached:\n  " + "\n  ".join(breaches)
        + f"\nLive census for {BASELINE_PATH.name} 'floor_and_null':\n"
        + json.dumps(live, indent=2, sort_keys=True)
    )


def test_sibling_deletion_matches_baseline(runs) -> None:
    failing: dict[str, list[str]] = {}
    for lang, case in CASES.items():
        path = f"{lang}/{case.filename}"
        moves = sibling_deletion_moves(
            case, runs["with"].get(path, []), runs["without"].get(path, []),
        )
        if moves:
            failing[lang] = moves
    breaches = set_breaches(failing, _baseline()["sibling_deletion_moves"])
    detail = "\n".join(f"  {lang}: {m}" for lang, ms in sorted(failing.items()) for m in ms)
    assert breaches == [], (
        "delete-an-earlier-sibling ratchet (WI-tusad) breached:\n  "
        + "\n  ".join(breaches) + f"\nWhat moved:\n{detail}\nLive failing set: "
        + json.dumps(sorted(failing))
    )


def test_stable_ids_do_not_depend_on_the_checkout_location(runs) -> None:
    """The same files at another path carry the same ids (ADR-0035 hashes the
    repo-relative path; an absolute one makes every clone a different graph)."""
    moved = location_dependent_ids(
        _fixture_nodes(runs, "with"), _fixture_nodes(runs, "elsewhere"),
    )
    assert moved == [], "stable_id depends on the checkout path:\n  " + "\n  ".join(moved)


def test_baseline_names_only_fixture_languages() -> None:
    """A baseline entry for a language with no fixture could never shrink."""
    baseline = _baseline()
    assert set(baseline["sibling_deletion_moves"]) <= set(CASES)
    assert set(baseline["floor_and_null"]) <= set(CASES)


def test_floor_key_is_the_backstop_definition() -> None:
    """The census reads the same floor key the backstop stamps (one definition)."""
    assert floor_stable_id_key("function", "x", "a.x", "f") == (
        make_declaration_stable_id("function", "x", "a.x", "f")
    )


# -- static: every symbol minted outside a language analyzer -------------------

#: Where a language-bearing Symbol is minted by something that is not that
#: language's analyzer: every core module (linkers, post-passes), the
#: alternative backends, and the format pass that runs as an analyzer but is
#: no language's producer. The floor never reaches a linker's mint (it runs
#: before the linkers), so each such mint must stamp its own stable_id.
_PASS_MINT_SOURCES = (
    "packages/hypergumbo-core/src/hypergumbo_core/**/*.py",
    "packages/hypergumbo-lang-rust-analyzer/src/**/*.py",
    "packages/hypergumbo-lang-scip-python/src/**/*.py",
    "packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/manifest_targets.py",
)

#: ``(path, enclosing function)`` of the mints that may leave stable_id unset,
#: each with its reason. Keyed by function, not line, so it does not drift.
_PASS_MINT_EXEMPT: dict[tuple[str, str], str] = {
    ("packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py",
     "file_anchor_symbol"): "an edge src only, never emitted (its docstring)",
    ("packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py",
     "synthesize_file_symbols_for_dangling_edges"):
        "kind=file, minted in analyze.all_analyzers BEFORE the backstop, which "
        "stamps the file table formula",
    ("packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py",
     "synthesize_file_anchors_for_node_bearing_paths"): "as above",
    ("packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py",
     "synthesize_file_anchors_for_paths"): "as above",
    ("packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py",
     "_analyze_body"): "kind=file, a TreeSitterAnalyzer's own file node: "
        "analyzer output, stamped by the backstop's file table formula",
    ("packages/hypergumbo-core/src/hypergumbo_core/cli.py", "cmd_dead_code_maybe"):
        "a transient Symbol rebuilt from a saved map's node dict for "
        "entrypoint detection; never emitted",
}


def pass_mints_without_stable_id(
    repo_root: Path,
) -> tuple[list[str], int, set[tuple[str, str]]]:
    """``(violations, mints seen, exemptions used)``: every ``Symbol(...)``
    call under :data:`_PASS_MINT_SOURCES` whose ``language`` is not the
    literal ``None`` (a Class-B stand-in, stamped by its chokepoint) and that
    passes no ``stable_id`` or the literal ``None``, outside
    :data:`_PASS_MINT_EXEMPT`.
    """
    violations: list[str] = []
    used: set[tuple[str, str]] = set()
    seen = 0
    files = sorted({p for pattern in _PASS_MINT_SOURCES for p in repo_root.glob(pattern)})
    for path in files:
        rel = path.relative_to(repo_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for call in ast.walk(func):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id == "Symbol"):
                    continue
                kwargs = {kw.arg: kw.value for kw in call.keywords if kw.arg}
                language = kwargs.get("language")
                if isinstance(language, ast.Constant) and language.value is None:
                    continue
                seen += 1
                sid = kwargs.get("stable_id")
                if sid is not None and not (isinstance(sid, ast.Constant) and sid.value is None):
                    continue
                if (rel, func.name) in _PASS_MINT_EXEMPT:
                    used.add((rel, func.name))
                    continue
                violations.append(f"{rel}:{call.lineno} ({func.name})")
    return sorted(set(violations)), seen, used


def test_every_pass_mint_with_a_language_stamps_a_stable_id() -> None:
    """INV-fufij: a linker's language-bearing mint (the gRPC services, the
    view templates) and a format pass's (``manifest_targets``) shipped null or
    on the floor. Each now mints through ``pass_minted_stable_id``; a new mint
    of that shape without a stable_id fails here, whether or not a fixture
    reaches it."""
    violations, seen, used = pass_mints_without_stable_id(REPO_ROOT)
    assert seen >= 10, f"matched only {seen} language-bearing mints: scanner broken?"
    assert set(_PASS_MINT_EXEMPT) - used == set(), "stale exemption: remove it"
    assert violations == [], (
        "language-bearing Symbol minted outside a language analyzer with no "
        "stable_id (mint it with overload_arity.pass_minted_stable_id, or "
        "exempt it with a reason):\n  " + "\n  ".join(violations)
    )


def test_pass_mint_scanner_flags_a_null_mint(tmp_path) -> None:
    """The scanner is not vacuous: it flags an unset and a literal-None
    stable_id, and passes a Class-B stand-in and a stamped mint."""
    src = tmp_path / "packages/hypergumbo-core/src/hypergumbo_core/linkers/x.py"
    src.parent.mkdir(parents=True)
    src.write_text(
        "def mint(lang):\n"
        "    a = Symbol(id='a', language=lang)\n"
        "    b = Symbol(id='b', language=lang, stable_id=None)\n"
        "    c = Symbol(id='c', language=None)\n"
        "    d = Symbol(id='d', language=lang, stable_id=f(lang))\n"
    )
    violations, seen, used = pass_mints_without_stable_id(tmp_path)
    assert seen == 3 and used == set()
    assert violations == [
        "packages/hypergumbo-core/src/hypergumbo_core/linkers/x.py:2 (mint)",
        "packages/hypergumbo-core/src/hypergumbo_core/linkers/x.py:3 (mint)",
    ]
