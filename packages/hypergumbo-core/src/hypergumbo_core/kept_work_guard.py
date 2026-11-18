# SPDX-License-Identifier: AGPL-3.0-or-later
"""The guard over work kept by hand: every function of the repository that
writes module state or a lazy attribute is marked or exempted with a reason.

WHY. A function that keeps what it computed runs that work under the first
test that calls it (``kept_work``'s docstring). Selection by coverage is
sound only if every such function either marks its fill (``with
kept_work.filling(...)``) or keeps nothing another test could be handed. The
second is a claim about the code, so it is written down here, per function,
with the reason; this module reads the syntax tree of the repository's
Python and fails on a state-writing function that is in neither set.

WHAT COUNTS AS WRITING STATE (one function at a time, nested functions
judged on their own):

- ``global``: it assigns a name it declares ``global``;
- ``mutate``: it changes a container bound at module level to a dict, list,
  set or deque display or constructor (``NAME[k] = v``, ``del NAME[k]``,
  ``NAME.append/add/update/setdefault/...(...)``, ``NAME |= ...``), and has
  not rebound that name locally;
- ``lazy``: it assigns ``self.<attr>`` under ``if self.<attr> is None``,
  ``if not self.<attr>``, ``if not hasattr(self, ...)`` or ``if '<attr>' not
  in self.__dict__``;
- ``cached_property``: it is decorated ``cached_property``.

WHAT IT CANNOT SEE: state reached through an alias (``d = _CACHE; d[k] =
v``, a container passed to a function), a module's state written from
another module (``mod.X[k] = v``), class-level containers changed through
``cls``/``self``, ``setattr`` and ``globals()`` writes, lazy attributes on
an object other than ``self`` (``entry.index = ...``), the body of a
``lambda``, and C extensions.
``functools`` caches are not its business: the recorder replaces
``functools.lru_cache`` itself.

Where it reads: ``packages/*/src``, ``packages/*/tests`` and ``tests``, not
``scripts/`` (not measured by coverage) and not test fixtures (``fixtures``
directories hold analysis input, not code that runs).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Mapping, NamedTuple, Optional, Union

FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]

_MUTATORS = frozenset({
    "append", "add", "update", "setdefault", "extend", "insert", "pop", "popitem",
    "remove", "discard", "clear", "move_to_end", "appendleft", "extendleft"})
_CONTAINER_CALLS = frozenset({
    "dict", "list", "set", "OrderedDict", "defaultdict", "deque", "Counter"})
_SCANNED = ("packages/*/src/**/*.py", "packages/*/tests/**/*.py", "tests/**/*.py")


@dataclass(frozen=True)
class StateWrite:
    """One place a function writes state."""

    kind: str  # axis: bounded-enum — global, mutate, lazy, cached_property
    function: str  # axis: identity — <repository-relative path>::<qualified name>
    name: str  # axis: free-text — the module name or attribute written
    line: int


@dataclass(frozen=True)
class Verdict:
    """What the guard found: each list holds ``<path>::<qualname>`` keys."""

    #: State-writing functions neither marked nor exempted.
    unmarked: tuple[str, ...]
    #: Exempted functions that mark a fill too (one of the two is wrong).
    marked_and_exempt: tuple[str, ...]
    #: Exemptions naming no state-writing function (renamed, moved, or fixed).
    stale: tuple[str, ...]
    #: State-writing functions that mark a fill and no use: a test handed the
    #: kept value records nothing, and the fill's rows reach only the filler.
    fills_without_use: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not (self.unmarked or self.marked_and_exempt or self.stale
                    or self.fills_without_use)


class FileScan(NamedTuple):
    """What one file holds: its state writes, and the functions in it that
    mark a fill and that mark a use (``<path>::<qualname>`` keys)."""

    writes: list[StateWrite]
    marked: set[str]
    used: set[str]


def _is_container(node: ast.expr) -> bool:
    if isinstance(node, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp,
                         ast.SetComp)):
        return True
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        return name in _CONTAINER_CALLS
    return False


def module_containers(tree: ast.Module) -> frozenset[str]:
    """Names a module binds, at its top level, to a mutable container."""
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and _is_container(node.value):
            out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif (isinstance(node, ast.AnnAssign) and node.value is not None
              and _is_container(node.value) and isinstance(node.target, ast.Name)):
            out.add(node.target.id)
    return frozenset(out)


def _subscripted_name(node: ast.expr) -> Optional[str]:
    """``X`` of ``X[a]``, ``X[a][b]`` and ``X[a].b[c]``; None otherwise."""
    while isinstance(node, (ast.Subscript, ast.Attribute)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _own_nodes(fn: FunctionNode) -> Iterator[ast.AST]:
    """The nodes of ``fn``, not of the functions, lambdas or classes in it."""
    stack: list[ast.AST] = list(fn.body)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                                 ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(node))


def _self_attr(node: ast.expr) -> Optional[str]:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
            and node.value.id == "self":
        return node.attr
    return None


def _lazy_attribute(test: ast.expr) -> Optional[str]:
    """The attribute an ``if`` test asks is not yet set, if it is that shape."""
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        op, right = test.ops[0], test.comparators[0]
        if isinstance(op, ast.Is) and isinstance(right, ast.Constant) and right.value is None:
            return _self_attr(test.left)
        if isinstance(op, ast.NotIn) and isinstance(test.left, ast.Constant) \
                and isinstance(right, ast.Attribute) and right.attr == "__dict__":
            return str(test.left.value)
        return None
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        operand = test.operand
        if isinstance(operand, ast.Call) and isinstance(operand.func, ast.Name) \
                and operand.func.id == "hasattr" and len(operand.args) == 2 \
                and isinstance(operand.args[1], ast.Constant):
            return str(operand.args[1].value)
        return _self_attr(operand)
    return None


def _writes(fn: FunctionNode, containers: frozenset[str]) -> Iterator[tuple[str, str, int]]:
    declared = {name for node in _own_nodes(fn) if isinstance(node, ast.Global)
                for name in node.names}
    nodes = sorted(_own_nodes(fn), key=lambda n: (getattr(n, "lineno", 0),
                                                  getattr(n, "col_offset", 0)))
    local: set[str] = set()
    for node in nodes:
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    if target.id in declared:
                        yield "global", target.id, node.lineno
                    elif isinstance(node, ast.AugAssign) and target.id in containers:
                        yield "mutate", target.id, node.lineno
                    else:
                        local.add(target.id)
                elif isinstance(target, ast.Subscript):
                    base = _subscripted_name(target)
                    if base in containers and base not in local:
                        yield "mutate", base, node.lineno
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                base = _subscripted_name(target) if isinstance(target, ast.Subscript) else None
                if base in containers and base not in local:
                    yield "mutate", base, node.lineno
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in _MUTATORS:
            base = _subscripted_name(node.func.value)
            if isinstance(node.func.value, (ast.Name, ast.Subscript)) \
                    and base in containers and base not in local:
                yield "mutate", base, node.lineno
        elif isinstance(node, ast.If):
            attr = _lazy_attribute(node.test)
            if attr is not None and any(
                    isinstance(inner, ast.Assign) and any(
                        _self_attr(t) == attr for t in inner.targets)
                    for inner in ast.walk(node)):
                yield "lazy", attr, node.lineno


def _is_mark(call: ast.expr, name: str) -> bool:
    """Whether ``call`` is ``kept_work.<name>(...)`` or a bare ``<name>(...)``."""
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    return (isinstance(func, ast.Name) and func.id == name) or (
        isinstance(func, ast.Attribute) and func.attr == name
        and isinstance(func.value, ast.Name) and func.value.id == "kept_work")


def marks_a_fill(fn: FunctionNode) -> bool:
    """Whether ``fn`` has a ``with kept_work.filling(...)`` (or ``filling(...)``)."""
    return any(_is_mark(item.context_expr, "filling") for node in _own_nodes(fn)
               if isinstance(node, (ast.With, ast.AsyncWith)) for item in node.items)


def marks_a_use(fn: FunctionNode) -> bool:
    """Whether ``fn`` calls ``kept_work.used(...)`` (or ``used(...)``)."""
    return any(_is_mark(node, "used") for node in _own_nodes(fn) if isinstance(node, ast.Call))


def _functions(body: Iterable[ast.stmt], prefix: str) -> Iterator[tuple[str, FunctionNode]]:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = prefix + node.name
            yield qualname, node
            yield from _functions(node.body, qualname + ".<locals>.")
        elif isinstance(node, ast.ClassDef):
            yield from _functions(node.body, prefix + node.name + ".")


def scan_source(source: str, rel: str) -> FileScan:
    """The state writes of one file, and the functions in it that mark."""
    tree = ast.parse(source)
    containers = module_containers(tree)
    writes: list[StateWrite] = []
    marked: set[str] = set()
    used: set[str] = set()
    for qualname, fn in _functions(tree.body, ""):
        key = f"{rel}::{qualname}"
        if marks_a_fill(fn):
            marked.add(key)
        if marks_a_use(fn):
            used.add(key)
        for decorator in fn.decorator_list:
            name = decorator.attr if isinstance(decorator, ast.Attribute) else (
                decorator.id if isinstance(decorator, ast.Name) else None)
            if name == "cached_property":
                writes.append(StateWrite("cached_property", key, fn.name, fn.lineno))
        writes.extend(StateWrite(kind, key, name, line)
                      for kind, name, line in _writes(fn, containers))
    return FileScan(writes, marked, used)


def scanned_files(root: Path) -> list[Path]:
    """The files the guard reads (see the module docstring)."""
    found = {path for pattern in _SCANNED for path in root.glob(pattern)}
    return sorted(path for path in found if "fixtures" not in path.relative_to(root).parts)


def judge(root: Path, exempt: Mapping[str, str]) -> Verdict:
    """Scan the repository at ``root`` against the exemptions ``exempt``."""
    writers: set[str] = set()
    marked: set[str] = set()
    used: set[str] = set()
    for path in scanned_files(root):
        scan = scan_source(path.read_text(encoding="utf-8"), path.relative_to(root).as_posix())
        writers |= {w.function for w in scan.writes}
        marked |= scan.marked
        used |= scan.used
    return Verdict(
        unmarked=tuple(sorted(writers - marked - set(exempt))),
        marked_and_exempt=tuple(sorted(marked & set(exempt))),
        stale=tuple(sorted(set(exempt) - writers)),
        fills_without_use=tuple(sorted((writers & marked) - used)),
    )


# ── The exemptions ──────────────────────────────────────────────────────────
#
# A function here writes state and keeps nothing another test could be handed
# in place of running code itself. The reason is the claim; the guard only
# checks that the function still exists and still writes state.

_CLEARS = ("empties what a marked function keeps, or a registry: the next fill runs "
           "under the caller that comes after it")
_REGISTRY = ("a registry filled by declarations when their module is imported (a "
             "decorator, a module body): code run at import is paired by the index's "
             "import-time rule, and a test registering its own entry runs the "
             "registration itself")
_SETTER = ("sets configuration for the run that calls it; a test that reads a value "
           "another test left behind depends on a leak between tests, not on work "
           "kept for it (whether every caller resets it is not checked here)")
_RESTORES = ("sets state for one unit of work and restores it when that ends: "
             "nothing outlives the call")
_CONSTRUCTION = ("fills a field of the instance being constructed, from that "
                 "instance's own fields: nothing is kept across calls")
_STORES_ITS_ARGUMENT = ("stores, on every call, a value computed from the call's own "
                        "argument: every caller runs the store itself")


def _per_run(where: str) -> str:
    return f"lazy state on an object made for one analysis run ({where}): not kept across runs"


_CORE = "packages/hypergumbo-core/src/hypergumbo_core/"
_EXT1 = "packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/"
_MAIN = "packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/"
_TRACKER = "packages/hypergumbo-tracker/src/hypergumbo_tracker/"

EXEMPT: dict[str, str] = {
    _CORE + "analyze/registry.py::_register_from_specs":
        "called only from inside ensure_discovered's fill (_load_entry_point_modules)",
    _CORE + "analyze/registry.py::clear_registry": _CLEARS,
    _CORE + "analyze/registry.py::register_analyzer.<locals>.decorator": _REGISTRY,
    _CORE + "analyzer_identity.py::reset_cache_for_testing": _CLEARS,
    _CORE + "cfg.py::clear_cfg_mapping_cache": _CLEARS,
    _CORE + "cfg.py::clear_def_use_extractors": _CLEARS,
    _CORE + "cfg.py::register_def_use_extractor.<locals>.decorator": _REGISTRY,
    _CORE + "ddg_build.py::clear_ddg_languages": _CLEARS,
    _CORE + "ddg_build.py::register_ddg_language": _REGISTRY,
    _CORE + "declared_test_inputs.py::install_declaration":
        _RESTORES + " (the caller restores the value it returns)",
    _CORE + "discovery.py::FileIndex.children":
        _per_run("a FileIndex is installed per run by set_file_index"),
    _CORE + "discovery.py::set_active_trace_deps": _SETTER,
    _CORE + "discovery.py::set_file_index": _SETTER,
    _CORE + "discovery.py::set_global_on_file_skipped": _SETTER,
    _CORE + "discovery.py::set_locale_excludes": _SETTER,
    _CORE + "discovery.py::set_max_file_bytes": _SETTER,
    _CORE + "discovery.py::set_trace_deps_override": _SETTER,
    _CORE + "entrypoints.py::Entrypoint.__post_init__": _CONSTRUCTION,
    _CORE + "framework_patterns.py::clear_pattern_cache": _CLEARS,
    _CORE + "function_summaries/__init__.py::clear_summary_cache": _CLEARS,
    _CORE + "ir.py::AnalysisRun.__post_init__": _CONSTRUCTION,
    _CORE + "ir.py::Edge.__post_init__": _CONSTRUCTION,
    _CORE + "limits.py::Limits.record_crashed_pass": _per_run("the run's own Limits"),
    _CORE + "linkers/registry.py::LinkerContext._ensure_indexes":
        _per_run("one LinkerContext per run_all_linkers"),
    _CORE + "linkers/registry.py::LinkerContext.base_name_origins":
        _per_run("one LinkerContext per run_all_linkers"),
    _CORE + "linkers/registry.py::clear_registry": _CLEARS,
    _CORE + "linkers/registry.py::register_linker.<locals>.decorator": _REGISTRY,
    _CORE + "linkers/route_handler.py::_NameTable.rails_index":
        _per_run("name tables are built per linker run"),
    _CORE + "linkers/subprocess_cli.py::_reset_cli_scan_cache": _CLEARS,
    _CORE + "module_sets.py::module_slot": _STORES_ITS_ARGUMENT,
    _CORE + "module_sets.py::register_module_sets":
        _STORES_ITS_ARGUMENT + " (the reader of a stored graph registers its table)",
    _CORE + "run_status.py::worker_pass": _RESTORES,
    _CORE + "sketch.py::_TestAnalysis.__post_init__": _CONSTRUCTION,
    _CORE + "symbol_resolution.py::SymbolResolver.clear_indexes": _CLEARS,
    _CORE + "symbol_resolution.py::clear_registry_index_cache": _CLEARS,
    _CORE + "taint.py::TaintFlowFinding.__post_init__": _CONSTRUCTION,
    _CORE + "verify_claims.py::TaintFlowConstraint.__post_init__": _CONSTRUCTION,
    "packages/hypergumbo-core/tests/test_check_schema_coverage.py::_BaselineSwap.__enter__":
        _RESTORES,
    "packages/hypergumbo-core/tests/test_check_schema_coverage.py::_BaselineSwap.__exit__":
        _RESTORES,
    _EXT1 + "ada.py::AdaAnalyzer._package_ids": _per_run("reset when a run starts and ends"),
    _EXT1 + "bitbake.py::BitBakeAnalyzer.analyze":
        _per_run("the pending-reference store is cleared when a run starts"),
    _EXT1 + "bitbake.py::BitBakeAnalyzer.extract_symbols_from_file":
        _per_run("the pending-reference store is cleared when a run starts"),
    _EXT1 + "circom.py::CircomTreeSitterAnalyzer._pass1_record":
        _per_run("reset when a run starts and ends"),
    _EXT1 + "circom.py::CircomTreeSitterAnalyzer.extract_edges_from_file":
        _per_run("reset when a run starts and ends"),
    _EXT1 + "nim.py::_NimScope.module_of": _per_run("a _NimScope is made per run"),
    _EXT1 + "nim.py::_NimScope.resolve_module": _per_run("a _NimScope is made per run"),
    _EXT1 + "verilog.py::VerilogAnalyzer._run_record": _per_run("reset when a run starts and ends"),
    _MAIN + "_pyscope.py::ScopeStack.value_shadowed": _per_run("a ScopeStack is made per scope"),
    _MAIN + "include_closure.py::IncludeClosure._index":
        _per_run("an IncludeClosure is made per file or per run"),
    _MAIN + "rust.py::RustAnalyzer.in_tree_crates": _per_run("reset when a run starts"),
    _MAIN + "scala.py::ScalaAnalyzer._project_package_readings":
        _per_run("reset when a run starts"),
    "packages/hypergumbo-lang-rust-analyzer/src/hypergumbo_lang_rust_analyzer/"
    "graceful_degrade.py::_reset_logged_fallback_for_tests": _CLEARS,
    _TRACKER + "preview.py::clear_cache": _CLEARS,
    _TRACKER + "preview.py::render_svg_preview":
        ("keyed by an absolute file path and its mtime, so an entry is shared only by "
         "renders of one file; the tracker cannot import hypergumbo_core (it does not "
         "depend on it), and its tests render files under their own tmp_path and clear "
         "the cache"),
    _TRACKER + "race_log.py::configure_race_log": _SETTER,
    _TRACKER + "serve.py::_ws_handler":
        "the connected clients of a running server: a connection adds and removes itself",
    _TRACKER + "serve.py::broadcast_state_snapshot":
        "the connected clients of a running server: a dead connection is removed",
    _TRACKER + "serve.py::create_app": _SETTER,
    _TRACKER + "serve.py::start_watcher": "the watcher task of a running server",
    _TRACKER + "serve.py::stop_watcher": "the watcher task of a running server",
    _TRACKER + "sync_log.py::_close_log": _SETTER,
    _TRACKER + "sync_log.py::init_sync_log": _SETTER,
}


def report(verdict: Verdict) -> list[str]:
    """What to tell the author, one line per finding; empty when clean."""
    lines = [f"writes state, neither marked nor exempt: {key}" for key in verdict.unmarked]
    lines += [f"marks a fill AND is exempt (drop one): {key}"
              for key in verdict.marked_and_exempt]
    lines += [f"exempt but writes no state (renamed, moved or fixed?): {key}"
              for key in verdict.stale]
    lines += [f"marks a fill and no use (kept_work.used on every call): {key}"
              for key in verdict.fills_without_use]
    if lines:
        lines.append(
            "Fix: mark the work it keeps (`kept_work.used(fn, entry)` on every call, "
            "`with kept_work.filling(fn, entry):` around the work; see "
            "hypergumbo_core/kept_work.py), or add it to kept_work_guard.EXEMPT with "
            "the reason it keeps nothing another test could be handed.")
    return lines
