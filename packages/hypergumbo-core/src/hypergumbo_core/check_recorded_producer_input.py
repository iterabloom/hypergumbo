# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static enforcement of ADR-0057 §12: cross-backend tests run on RECORDED
producer input, never on the incumbent arm's output fed back (WI-romuh).

WHY A LINT AND NOT A REVIEW RULE. The stable_id parity test collected a
baseline from ``rust.py``, fed ``rust.py``'s own spans into the SCIP parity
helper, and passed for months while the feature measured 0 of 52 in
production (INV-dolud, retracted in #1044). The test was not wrong about the
helper; it was wrong about what it claimed, and nobody re-read it. A control
that cannot fail is not a control, and the shape recurs every time a backend
CI cannot run (rust-analyzer executes ``build.rs``; pyright will be next)
tempts an author to synthesise its input from the arm CI can run.

WHAT THE RULE IS. A test may run the incumbent analyzer, and it may compare
against the incumbent's output. It may not DERIVE the alternative arm's
input from that output and hand it to a cross-backend consumer. So:

* SOURCES are calls to an incumbent producer — the entry function of the
  non-index backend of every language with two anchored producers, read
  from the registry declarations (:func:`incumbent_producer_names`), plus
  ``run_analyzer("<that analyzer>", ...)`` — and, one hop out, any function
  in the same test module whose body calls a source
  (``_collect_rust_py_stable_ids`` is that shape).
* TAINT starts at a name bound from a source call and follows assignment,
  tuple unpacking, ``for`` targets, ``with ... as`` and comprehension
  targets, within one test function. Anything more is real dataflow and
  would trade a sharp rule for a fuzzy one.
* SINKS are :data:`CROSS_BACKEND_CONSUMERS` — the core SCIP shim, the
  rust-analyzer package's translation and parity helpers, and (when it
  lands) the merge pass. A sink call whose arguments mention a tainted
  name, or contain a source call outright, is an offence.

A test that feeds on purpose says so: ``@pytest.mark.incumbent_fed("<reason>")``.
An extraction contract — "handed rust.py's own item spans, the helper
returns rust.py's ids" — proves the helper agrees with the incumbent and
claims nothing about production input; that is legitimate, and the reason
is there for the next reader. A bare marker with no reason is refused: the
reason IS the review.

THE BOUNDARY, PINNED BY TESTS. Comprehension targets are tainted only inside
the comprehension. A helper that feeds a consumer but is never called from a
test is not reported (helpers propagate; tests offend). Only test modules
under ``packages/*/tests`` are read — the same population smart-test runs.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, Union

#: Cross-backend consumers (sinks): functions whose input is the ALTERNATIVE
#: arm's. The merge pass (``analyze.merge_producers.merge_producer_records``)
#: is deliberately NOT one: its inputs are both arms, and the incumbent's
#: live output is legitimately one of them — the rule is about deriving the
#: alternative arm's input, and a fixture test that hands the pass live
#: tree-sitter records beside recorded SCIP records is the intended shape.
CROSS_BACKEND_CONSUMERS: frozenset[str] = frozenset({
    # hypergumbo_core.scip — the shim every SCIP backend goes through
    "scip_index_to_symbols",
    "scip_index_to_edges",
    "scip_index_to_call_edges",
    # hypergumbo_lang_rust_analyzer / hypergumbo_lang_mainstream.rust_scip
    "translate_scip_to_hg",
    "reassign_rust_stable_ids",
    "compute_rust_stable_id_from_source",
})

#: Which backends are the ALTERNATIVE arm of a language lives with the
#: registry (``analyze.registry.ALTERNATIVE_BACKENDS``), one home shared with
#: the merge pass; re-exported here for the lint's callers and tests.
from .analyze.registry import ALTERNATIVE_BACKENDS as ALTERNATIVE_BACKENDS  # noqa: E402

MARKER = "incumbent_fed"

_TEST_FILE_GLOBS = ("test_*.py", "*_test.py", "BRANCHES_*.py")

_FuncDef = Union[ast.FunctionDef, ast.AsyncFunctionDef]


def incumbent_producer_names() -> frozenset[str]:
    """Entry-function names AND analyzer names of every incumbent producer.

    Derived from the registry: for each language whose declared producers
    pair, every participant the registry names an incumbent
    (:func:`~.analyze.registry.incumbents_of` — not an alternative arm). Both
    spellings are returned because a test may call ``analyze_rust(root)`` or
    ``run_analyzer("rust", root)``.
    """
    from .analyze.registry import (
        ensure_discovered,
        get_all_analyzers,
        incumbents_of,
    )

    ensure_discovered()
    languages = {lang for a in get_all_analyzers() for lang in a.languages}
    names: set[str] = set()
    for language in sorted(languages):
        for analyzer in incumbents_of(language):
            names.add(analyzer.name)
            if analyzer.func_name:
                names.add(analyzer.func_name)
    return frozenset(names)


def find_incumbent_fed_tests(
    repo_root: Path,
    *,
    producers: frozenset[str] | None = None,
    consumers: frozenset[str] = CROSS_BACKEND_CONSUMERS,
) -> list[str]:
    """Every test under ``packages/*/tests`` that feeds incumbent output into
    a cross-backend consumer without an ``incumbent_fed`` reason.

    ``producers`` defaults to :func:`incumbent_producer_names`; tests pass an
    explicit set so the walk is checked without the registry.
    """
    if producers is None:
        producers = incumbent_producer_names()
    offenders: list[str] = []
    for path in sorted(_test_files(repo_root)):
        offenders.extend(_offences_in_file(path, repo_root, producers, consumers))
    return offenders


def _test_files(repo_root: Path) -> Iterator[Path]:
    for tests_dir in sorted((repo_root / "packages").glob("*/tests")):
        for pattern in _TEST_FILE_GLOBS:
            yield from tests_dir.rglob(pattern)


def _callee_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_source_call(call: ast.Call, sources: frozenset[str]) -> str | None:
    """The source's name when ``call`` invokes one, else ``None``."""
    callee = _callee_name(call)
    if callee is None:
        return None
    if callee in sources:
        return callee
    if callee == "run_analyzer" and call.args:
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value in sources:
            return f'run_analyzer("{first.value}")'
    return None


def _functions(tree: ast.AST) -> Iterator[_FuncDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _module_sources(tree: ast.Module, producers: frozenset[str]) -> frozenset[str]:
    """``producers`` plus every same-module function that calls one, to a fixpoint."""
    sources = set(producers)
    grew = True
    while grew:
        grew = False
        for fn in _functions(tree):
            if fn.name in sources:
                continue
            calls = (n for n in ast.walk(fn) if isinstance(n, ast.Call))
            if any(_is_source_call(c, frozenset(sources)) for c in calls):
                sources.add(fn.name)
                grew = True
    return frozenset(sources)


def _incumbent_fed_reason(fn: _FuncDef) -> str | None:
    """``None`` when unmarked; ``""`` when marked without a reason; else the reason."""
    for decorator in fn.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr == MARKER:
            if isinstance(decorator, ast.Call) and decorator.args:
                first = decorator.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.strip():
                    return first.value
            return ""
    return None


class _TaintWalk(ast.NodeVisitor):
    """One test function: bind taint from sources, report sink calls fed by it."""

    def __init__(self, sources: frozenset[str], sinks: frozenset[str]) -> None:
        self.sources = sources
        self.sinks = sinks
        self.origin: dict[str, str] = {}  # tainted name -> the source it came from
        self.found: list[tuple[int, str, str]] = []  # (lineno, source, sink)

    # -- what taints an expression -------------------------------------------------

    def _origin_of(self, node: ast.AST) -> str | None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                source = _is_source_call(sub, self.sources)
                if source is not None:
                    return source
            elif isinstance(sub, ast.Name) and sub.id in self.origin:
                return self.origin[sub.id]
        return None

    def _bind(self, target: ast.AST, origin: str) -> None:
        for sub in ast.walk(target):
            if isinstance(sub, ast.Name):
                self.origin[sub.id] = origin

    # -- binding forms ------------------------------------------------------------

    def visit_Assign(self, node: ast.Assign) -> None:
        origin = self._origin_of(node.value)
        if origin is not None:
            for target in node.targets:
                self._bind(target, origin)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            origin = self._origin_of(node.value)
            if origin is not None:
                self._bind(node.target, origin)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        origin = self._origin_of(node.value)
        if origin is not None:
            self._bind(node.target, origin)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        origin = self._origin_of(node.value)
        if origin is not None:
            self._bind(node.target, origin)
        self.generic_visit(node)

    def _visit_for(self, node: ast.For | ast.AsyncFor) -> None:
        origin = self._origin_of(node.iter)
        if origin is not None:
            self._bind(node.target, origin)
        self.generic_visit(node)

    visit_For = _visit_for
    visit_AsyncFor = _visit_for

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                origin = self._origin_of(item.context_expr)
                if origin is not None:
                    self._bind(item.optional_vars, origin)
        self.generic_visit(node)

    visit_With = _visit_with
    visit_AsyncWith = _visit_with

    def _visit_comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
    ) -> None:
        # Comprehension targets are scoped to the comprehension: taint them
        # while inside, restore afterwards.
        saved = dict(self.origin)
        for generator in node.generators:
            origin = self._origin_of(generator.iter)
            if origin is not None:
                self._bind(generator.target, origin)
        self.generic_visit(node)
        self.origin = saved

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension

    # -- the sink ------------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        callee = _callee_name(node)
        if callee in self.sinks:
            fed: list[ast.expr] = list(node.args) + [kw.value for kw in node.keywords]
            for arg in fed:
                origin = self._origin_of(arg)
                if origin is not None:
                    self.found.append((node.lineno, origin, callee))
                    break
        self.generic_visit(node)


def _test_functions(tree: ast.Module) -> Iterator[_FuncDef]:
    for fn in _functions(tree):
        if fn.name.startswith("test_"):
            yield fn


def _offences_in_file(
    path: Path,
    repo_root: Path,
    producers: frozenset[str],
    consumers: frozenset[str],
) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    sources = _module_sources(tree, producers)
    rel = path.relative_to(repo_root).as_posix()
    out: list[str] = []
    for fn in _test_functions(tree):
        walk = _TaintWalk(sources, consumers)
        walk.visit(fn)
        if not walk.found:
            continue
        reason = _incumbent_fed_reason(fn)
        if reason:
            continue
        lineno, source, sink = walk.found[0]
        if reason == "":
            out.append(
                f"{rel}:{lineno}: {fn.name} is marked @pytest.mark.{MARKER} but gives no "
                f"reason — the reason is the review; say what the test proves about "
                f"{sink} when fed {source} output"
            )
        else:
            out.append(
                f"{rel}:{lineno}: {fn.name} feeds {source} output into {sink} — a "
                f"cross-backend test runs on RECORDED producer input (ADR-0057 §12; "
                f"import from recorded_rust_analyzer_1_94_0), or, if this is an "
                f"extraction contract, mark it @pytest.mark.{MARKER}(\"<reason>\")"
            )
    return out
