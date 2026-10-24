# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-ratuv: a production function whose only callers live in test modules.

WHAT THIS EXISTS TO PREVENT. Six ADR-0017 artifacts were closed ``done`` on "N
tests, 100% coverage" while having no production caller — ``infer_summary``,
``is_field_tainted``, ``select_ddg_targets`` and peers. Every reference to them
was PROSE: an ADR section, a sibling docstring, a test docstring. Each parsed,
linted, and sat at 100% coverage held there by its own tests. This module is the
executable trigger that prose could not be.

THE QUESTION IS ABOUT CALLERS, NOT SEEDS, AND THAT DISTINCTION IS THE ITEM'S
WHOLE HISTORY. ``dead-code-maybe`` asks "is this reachable from the production
seed set", and the seeds are entrypoints PLUS exported public API — so a public
function with zero in-repo callers can never be flagged, which is exactly the
shape the ADR-0017 family has. Measured on that question the gate caught 3 of
its own 6 motivating examples and would have fired ~1,747 times on day one; the
owner refused it. Asking instead "does every DIRECT CALLER of this symbol live
in a test module" catches all of them and is immune to the seeding.

METHODS ARE GATED ONLY ON A MAP THAT CARRIES RECEIVER TYPES (WI-mifag). In the
syntax-only call graph a method's identity is its SHORT name, so ``Store.add``
cannot be told from ``set.add`` and ``Pattern.matches`` once scored 497
production hits off regex ``.matches``. Gating such rows would freeze
unadjudicable keys into a shrink-only baseline, and *"a rubber-stamped ratchet
is worse than none"* is WI-ratuv's governing constraint. The scip-python
backend (WI-nanom, pyright/SCIP under ADR-0012 multi-fidelity coexistence)
supplies the types, and the Woodpecker full-suite map is built with it
(WI-losig). On such a map ``typed_references`` reads them out and methods are
judged; on any other map they are set aside as ``SET_ASIDE_UNTYPED`` and
counted, never gated.

TYPING THE CALL GRAPH IS NOT ENOUGH; THE DEMOTION ARM NEEDS THE TYPES TOO. The
bare-name indexes below put ``set.add`` and ``Store.add`` in one bucket
(``refs.loads["add"]``) however well the call graph is typed. For a method they
are not consulted. Instead every production ATTRIBUTE site naming the method
(``member_sites``: the ``.add`` token's file and line) resolves through the
typed producer's edges out of its innermost enclosing callable: typed to the
method, or untyped (a receiver pyright saw as ``Any``, or a site outside every
callable, which SCIP drops) demotes; typed only to other receivers does not.
Resolution is per ENCLOSER rather than per line because the map keeps one edge
per ``(src, dst, type)`` on its first line, so a second ``.add`` on a set in one
function has no edge of its own. The residual that costs: an untyped ``.add``
sharing a callable with a typed one reads as typed. A bare ``Name`` is never a
member site except in a class body (``x = property(_get)``): in a function it
is a local, and 42 locals called ``ancestors`` once demoted ``Store.ancestors``.

FOUR DISPATCH MECHANISMS REACH A METHOD WITH NO CALL EDGE, each found by
adjudicating the first typed cohort row by row (148 methods, 2026-10-24):

    an OVERRIDE of a production-reached method   ``self.parse_source()`` in the
                                                 base types to the base; the
                                                 subclass's override runs
    a class on an EXTERNAL base                  Textual calls ``action_*`` by
                                                 binding name and ``on_*`` by
                                                 event (42 rows); ``typing.
                                                 Protocol`` and ``abc.ABC``
                                                 declare a shape, run nothing,
                                                 and do not count
    a PROTOCOL method production reaches         ``extractor.extract()`` types
                                                 to ``DefUseExtractor.extract``;
                                                 ``CDefUseExtractor`` conforms
                                                 structurally, with no edge
    a DUNDER                                     the runtime calls it
                                                 (``__post_init__``)

Each demotes; a decorator demotes only the method that carries it, keyed by
``(path, Class.method)``.

THE DEMOTION ARM, AND WHY IT IS NOT OPTIONAL. Three reference mechanisms are
invisible to a ``calls``/``instantiates`` edge set, and each produced a
FALSE POSITIVE on live production code in the first measurement:

    ``p_slice.set_defaults(func=cmd_slice)``   a VALUE BINDING, not a call —
                                              and ``cmd_slice`` is the entry
                                              point of ``hypergumbo slice``
    ``@register_linker(...)``                 decorator APPLICATION
    a plain cross-module reference            the call graph missed the edge

Demoting them costs one AST pass over production source and removes 53 of 585
functions. Without it the gate fires on user-facing CLI commands, which is
disqualifying however good the rest of the signal is.

AST, NOT GREP, AND THAT CHOICE HAS ALREADY PAID. A grep proxy once scored
``infer_summary`` as having one production reference; the reference was a
COMMENT in ``taint.py`` reading "``infer_summary`` still has zero production
callers". Prose about deadness counted as evidence of life. This module collects
``ast.Name`` loads and ``ast.Attribute`` accesses, so a comment cannot vote.

REPOSITORY SCRIPTS VOTE TOO (WI-famub). The first cut scanned ``packages/*/src``
alone, and the map it reads is built from the same tree -- so a core module
whose only production caller is a ``scripts/check-*`` gate had, as far as the
gate could see, only its tests. ``check_id_construction`` was baselined for it;
``confidence_sites`` failed the cron the day its gate shipped; a 2026-10-14
commit ratified ``unregistered_edge_meta_keys`` into the baseline for the same
reason and counted 37 keys of that shape already there. A ``scripts/`` gate is
production in the only sense this module cares about: it is run, and not by a
test runner. So ``production_source_files`` includes the repository's Python
scripts, found by ``.py`` suffix or by a Python shebang since the gates are
extensionless. They vote as referencers only -- their own functions are not
under ``packages/*/src`` and are never in the cohort.

THE MAP MUST CARRY THE TYPING IT CLAIMS. The Woodpecker full-suite map is
built with the scip-python backend, whose preconditions (the opt-in, the
binary on ``PATH``) fail silently into the plain map.
``pass_contribution_problem`` lets the gate's ``--require-pass`` refuse a map
in which the named pass was skipped, never ran, or emitted nothing, and
``typed_method_call_share`` reports how many Python method calls that pass
actually typed.
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .axis_meta_keys import call_family_edge_types
from .paths import is_test_file

#: Node kinds this gate adjudicates. A method only on a map with receiver
#: typing -- see the module docstring.
GATED_KINDS: frozenset[str] = frozenset({"function", "method"})

#: Keyword arguments whose VALUE being a bare name is a dispatch binding rather
#: than a call. ``func`` is argparse's own (``set_defaults(func=cmd_x)``); the
#: rest are the same shape under other names.
_DISPATCH_KEYWORDS: frozenset[str] = frozenset(
    {"func", "handler", "callback", "default"}
)

DEMOTION_DISPATCH = "dispatch_binding"
DEMOTION_DECORATOR = "decorator_application"
DEMOTION_REGISTERED = "carries_a_decorator"
DEMOTION_CROSS_MODULE = "production_reference"
DEMOTION_OVERRIDE = "override_dispatch"
DEMOTION_EXTERNAL_BASE = "external_base_dispatch"
DEMOTION_PROTOCOL = "protocol_dispatch"
DEMOTION_RUNTIME = "runtime_dunder"
#: A method on a map without receiver typing: counted, not judged.
SET_ASIDE_UNTYPED = "method_without_receiver_typing"

#: Bases that declare a shape and call nothing, so a class on one is not on
#: an external CALLER.
_SHAPE_ONLY_BASES: frozenset[str] = frozenset({
    "python:typing:0-0:Protocol:external_symbol",
    "python:abc:0-0:ABC:external_symbol",
})
_PROTOCOL_BASE = "python:typing:0-0:Protocol:external_symbol"


@dataclass(frozen=True)
class TestOnlySymbol:
    """One production symbol whose every direct caller is a test module."""

    #: Not a pytest test class. The name is the domain term and worth keeping,
    #: so the collection opt-out is declared rather than the name bent around
    #: a test runner's prefix convention.
    __test__ = False

    symbol_id: str
    name: str
    kind: str
    path: str

    @property
    def key(self) -> str:
        """The BASELINE key, which deliberately omits the line span.

        A symbol id carries its span (``…:11826-12011:main:function``), so an
        id-keyed baseline would churn on every edit that moves a line — a
        diff nobody could read, which is how a ratchet becomes a rubber stamp.
        ``path::name`` is stable under line movement and changes only on a
        rename or a move, both of which SHOULD be re-adjudicated.
        """
        return f"{self.path}::{self.name}"


@dataclass
class ProductionReferences:
    """Where production source mentions a bare name, by mechanism.

    Built by parsing, never by grepping — see the module docstring.
    """

    loads: dict[str, set[str]] = field(default_factory=dict)
    decorated: dict[str, set[str]] = field(default_factory=dict)
    dispatch_bound: dict[str, set[str]] = field(default_factory=dict)
    #: Functions that CARRY a decorator. Distinct from ``decorated``, which
    #: indexes the decorator's own name — and confusing the two was a real
    #: bug: ``@register_linker`` put ``register_linker`` in ``decorated`` and
    #: left ``link_decorator_dispatch``, the registered function, gated.
    carries_decorator: dict[str, set[str]] = field(default_factory=dict)
    #: Where a MEMBER is named: ``{attr: {(path, line)}}`` for every attribute
    #: load (the line of the attribute token) and every class-body name load.
    #: What a method's references are resolved from on a typed map.
    member_sites: dict[str, set[tuple[str, int]]] = field(default_factory=dict)
    #: ``(path, "Class.method")`` for each decorated method of a class.
    decorated_methods: set[tuple[str, str]] = field(default_factory=set)


def _index(store: dict[str, set[str]], name: str, where: str) -> None:
    store.setdefault(name, set()).add(where)


def collect_production_references(
    files: Iterable[Path], root: Path,
) -> ProductionReferences:
    """Parse production modules and index every non-call reference mechanism.

    A file that does not parse is SKIPPED rather than failing the run: this is
    a lint over a tree that may legitimately contain a file mid-edit, and the
    conservative direction for a gate that can DELETE work from a baseline is
    to see fewer references, not to crash.
    """
    refs = ProductionReferences()
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        rel = str(path.relative_to(root))
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                for dec in node.decorator_list:
                    target = dec.func if isinstance(dec, ast.Call) else dec
                    named = getattr(target, "id", None) or getattr(
                        target, "attr", None,
                    )
                    if named:
                        _index(refs.decorated, named, rel)
                        _index(refs.carries_decorator, node.name, rel)
                if isinstance(node, ast.ClassDef):
                    _index_class_body(refs, node, rel)
            elif isinstance(node, ast.keyword):
                if node.arg in _DISPATCH_KEYWORDS and isinstance(
                    node.value, ast.Name,
                ):
                    _index(refs.dispatch_bound, node.value.id, rel)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                _index(refs.loads, node.id, rel)
            elif isinstance(node, ast.Attribute) and isinstance(
                node.ctx, ast.Load,
            ):
                _index(refs.loads, node.attr, rel)
                refs.member_sites.setdefault(node.attr, set()).add(
                    (rel, node.end_lineno or node.lineno),
                )
    return refs


def _index_class_body(
    refs: ProductionReferences, cls: ast.ClassDef, rel: str,
) -> None:
    """A class's decorated methods, and the names its body (not its methods) loads."""
    for stmt in cls.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if stmt.decorator_list:
                refs.decorated_methods.add((rel, f"{cls.name}.{stmt.name}"))
            continue
        if isinstance(stmt, ast.ClassDef):
            continue
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                refs.member_sites.setdefault(sub.id, set()).add((rel, sub.lineno))


def _has_python_shebang(path: Path) -> bool:
    """Whether the file's first line hands it to a Python interpreter."""
    try:
        with path.open("rb") as fh:
            first = fh.readline(256)
    except OSError:
        return False
    return first.startswith(b"#!") and b"python" in first


def repository_script_files(root: Path) -> list[Path]:
    """Python under ``scripts/``: ``.py`` at any depth, or a Python shebang.

    Repository gates are extensionless executables (``scripts/check-*``), so
    a suffix glob cannot find them; the shebang is what names them Python.
    ``__pycache__`` is skipped rather than read for a shebang it cannot have.
    """
    scripts = root / "scripts"
    if not scripts.is_dir():
        return []
    found = [
        path for path in scripts.rglob("*")
        if "__pycache__" not in path.parts and path.is_file()
        and (path.suffix == ".py" or _has_python_shebang(path))
    ]
    return sorted(found)


def production_source_files(root: Path) -> list[Path]:
    """Every file production can call FROM, sorted for determinism.

    The shipped modules, ``packages/*/src/**/*.py``, and the repository's own
    Python scripts (see the module docstring on why scripts vote). Scripts are
    REFERENCERS only: ``_is_production`` still admits just ``packages/*/src``
    symbols to the gated cohort, so a script's own functions are never judged.
    """
    return sorted(root.glob("packages/*/src/**/*.py")) + repository_script_files(root)


def _is_production(path: str) -> bool:
    return path.startswith("packages/") and "/src/" in path


def find_test_only_symbols(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
) -> list[TestOnlySymbol]:
    """Production callables whose every direct caller is a test module.

    A symbol with ZERO callers is NOT in this cohort. That is the load-bearing
    difference from ``dead-code-maybe``: a registry-dispatched analyzer method
    has no direct callers at all, so dynamic dispatch separates itself into the
    zero-caller population instead of drowning this one.
    """
    call_types = call_family_edge_types()
    by_id = {n["id"]: n for n in nodes if n.get("id")}
    incoming: dict[str, list[str]] = {}
    for edge in edges:
        etype = edge.get("type") or edge.get("edge_type")
        if etype not in call_types:
            continue
        dst, src = edge.get("dst"), edge.get("src")
        if dst and src:
            incoming.setdefault(dst, []).append(src)

    found: list[TestOnlySymbol] = []
    for node in nodes:
        if node.get("kind") not in GATED_KINDS:
            continue
        path = node.get("path") or ""
        if not _is_production(path) or is_test_file(path):
            continue
        callers = [
            by_id[c] for c in incoming.get(node["id"], []) if c in by_id
        ]
        if not callers:
            continue
        if all(is_test_file(c.get("path") or "") for c in callers):
            found.append(TestOnlySymbol(
                symbol_id=node["id"], name=node.get("name") or "",
                kind=node["kind"], path=path,
            ))
    return sorted(found, key=lambda s: s.key)


@dataclass
class TypedReferences:
    """What a map the typed producer contributed to says about methods.

    Built by :func:`typed_references`; see the module docstring for how a
    method's demotion reads it.
    """

    #: ``{path: [(start_line, end_line, id)]}`` for each callable or class the
    #: typed producer saw -- the enclosers its edges come out of.
    enclosers: dict[str, list[tuple[int, int, str]]] = field(default_factory=dict)
    #: ``{(encloser id, member name): {dst id}}`` over the typed producer's
    #: call-family and ``references`` edges.
    resolved: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    #: ``{method id: {the method ids it overrides}}`` (``implements`` edges).
    overrides: dict[str, set[str]] = field(default_factory=dict)
    #: ``{class id: {in-repo base class ids}}`` and ``{class id: {external
    #: base ids}}`` (``extends`` edges).
    parents: dict[str, set[str]] = field(default_factory=dict)
    external_bases: dict[str, set[str]] = field(default_factory=dict)
    #: ``{(path, class name): class id}``.
    classes: dict[tuple[str, str], str] = field(default_factory=dict)
    #: Ids a production (non-test) node calls or references.
    production_reached: set[str] = field(default_factory=set)
    #: Member names of Protocol-class methods in ``production_reached``.
    protocol_member_names: set[str] = field(default_factory=set)


def _member_name(dst: str, by_id: Mapping[str, Mapping[str, Any]]) -> str:
    """The member an edge's target names: its name's last component.

    An external target is not a node; its id is
    ``<lang>:<module>:<span>:<name>:<kind>`` (``python:builtins.set:0-0:add:
    external_symbol``), so the name is the second-to-last field.
    """
    node = by_id.get(dst)
    name = node.get("name") if node else None
    if not name:
        parts = dst.split(":")
        name = parts[-2] if len(parts) >= 3 else dst
    return str(name).rsplit(".", 1)[-1]


def _is_production_caller(path: str) -> bool:
    return (_is_production(path) or path.startswith("scripts/")) and not is_test_file(path)


def typed_references(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    *, origin: str = "scip",
) -> TypedReferences:
    """Index what the typed producer ``origin`` says about methods in a map.

    An edge or node is the typed producer's when ``origin`` is among its
    producers. ``overrides`` and the class bases take any producer's edge:
    an override or a base is declared in the source, not inferred from a
    receiver.
    """
    by_id = {n["id"]: n for n in nodes if n.get("id")}
    typed = TypedReferences()
    for node in nodes:
        span, path = node.get("span") or {}, node.get("path")
        if node.get("kind") == "class" and path:
            typed.classes[(path, node.get("name") or "")] = node["id"]
        if (origin in _origins(node) and path and span
                and node.get("kind") in ("function", "method", "class")):
            typed.enclosers.setdefault(path, []).append(
                (span["start_line"], span["end_line"], node["id"]),
            )
    reference_types = call_family_edge_types() | {"references"}
    for edge in edges:
        etype, src, dst = edge.get("type"), edge.get("src"), edge.get("dst")
        if not src or not dst:
            continue
        if etype == "implements":
            typed.overrides.setdefault(src, set()).add(dst)
        elif etype == "extends":
            base = by_id.get(dst)
            in_repo = base is not None and base.get("kind") == "class" and base.get("path")
            (typed.parents if in_repo else typed.external_bases).setdefault(
                src, set()).add(dst)
        elif etype in reference_types:
            caller = by_id.get(src)
            if caller is not None and _is_production_caller(caller.get("path") or ""):
                typed.production_reached.add(dst)
            if origin in _origins(edge):
                typed.resolved.setdefault(
                    (src, _member_name(dst, by_id)), set()).add(dst)
    protocols = {c for c, bases in typed.external_bases.items() if _PROTOCOL_BASE in bases}
    for node in nodes:
        name = node.get("name") or ""
        owner = typed.classes.get((node.get("path") or "", name.rsplit(".", 1)[0]))
        if (node.get("kind") == "method" and owner in protocols
                and node["id"] in typed.production_reached):
            typed.protocol_member_names.add(name.rsplit(".", 1)[-1])
    return typed


def _innermost_encloser(typed: TypedReferences, path: str, line: int) -> str | None:
    best: tuple[int, str] | None = None
    for start, end, nid in typed.enclosers.get(path, ()):
        if start <= line <= end and (best is None or end - start < best[0]):
            best = (end - start, nid)
    return best[1] if best else None


def _ancestry(start: str, step: Mapping[str, set[str]]) -> set[str]:
    """``start`` and everything reachable from it through ``step``."""
    seen: set[str] = set()
    todo = [start]
    while todo:
        current = todo.pop()
        if current not in seen:
            seen.add(current)
            todo.extend(step.get(current, ()))
    return seen


def _method_demotion_reason(
    symbol: TestOnlySymbol, refs: ProductionReferences, typed: TypedReferences,
) -> str | None:
    """:func:`demotion_reason` for a method on a typed map."""
    owner_name, _, member = symbol.name.rpartition(".")
    if member.startswith("__") and member.endswith("__"):
        return DEMOTION_RUNTIME
    owner = typed.classes.get((symbol.path, owner_name))
    if owner is not None and any(
        typed.external_bases.get(c, set()) - _SHAPE_ONLY_BASES
        for c in _ancestry(owner, typed.parents)
    ):
        return DEMOTION_EXTERNAL_BASE
    if member in typed.protocol_member_names:
        return DEMOTION_PROTOCOL
    overridden = _ancestry(symbol.symbol_id, typed.overrides) - {symbol.symbol_id}
    if overridden & typed.production_reached:
        return DEMOTION_OVERRIDE
    if (symbol.path, ".".join(symbol.name.split(".")[-2:])) in refs.decorated_methods:
        return DEMOTION_REGISTERED
    for path, line in refs.member_sites.get(member, ()):
        encloser = _innermost_encloser(typed, path, line)
        targets = typed.resolved.get((encloser, member)) if encloser else None
        if not targets or symbol.symbol_id in targets:
            return DEMOTION_CROSS_MODULE
    return None


def demotion_reason(
    symbol: TestOnlySymbol, refs: ProductionReferences,
    typed: TypedReferences | None = None,
) -> str | None:
    """Why this symbol is reachable by something the call graph cannot see.

    ``None`` means no such mechanism was found — the symbol stays in the gated
    cohort. A method on a typed map (``typed`` given) is judged by
    :func:`_method_demotion_reason`; otherwise only the last name component is
    consulted, which for a method is ambiguous by construction -- the reason
    :func:`gated_cohort` sets methods aside without ``typed``.

    SAME-MODULE REFERENCES COUNT, and the first cut excluded them. A ``def``
    is not a ``Load``, so the only way a module mentions its own function is a
    real reference — ``cli.py`` passes ``cli_mod._install_rust_analyzer_…`` as
    a value one screen above the definition, which is production use the call
    graph does not model. The one thing this admits is a recursive dead
    function demoting itself; that is a false NEGATIVE, which is the safe
    direction for a gate that can block CI.
    """
    if typed is not None and symbol.kind == "method":
        return _method_demotion_reason(symbol, refs, typed)
    bare = symbol.name.rsplit(".", 1)[-1]
    if refs.dispatch_bound.get(bare):
        return DEMOTION_DISPATCH
    if refs.decorated.get(bare):
        return DEMOTION_DECORATOR
    if refs.carries_decorator.get(bare):
        return DEMOTION_REGISTERED
    if refs.loads.get(bare):
        return DEMOTION_CROSS_MODULE
    return None


def gated_cohort(
    symbols: Sequence[TestOnlySymbol], refs: ProductionReferences,
    typed: TypedReferences | None = None,
) -> tuple[list[TestOnlySymbol], dict[str, int]]:
    """The cohort the ratchet acts on, plus a census of what was set aside.

    Methods are judged only with ``typed``; without it each is counted under
    ``SET_ASIDE_UNTYPED``.
    """
    kept: list[TestOnlySymbol] = []
    census: dict[str, int] = {}
    for symbol in symbols:
        if symbol.kind == "method" and typed is None:
            census[SET_ASIDE_UNTYPED] = census.get(SET_ASIDE_UNTYPED, 0) + 1
            continue
        reason = demotion_reason(symbol, refs, typed)
        if reason:
            census[reason] = census.get(reason, 0) + 1
            continue
        kept.append(symbol)
    return kept, census


def load_baseline(path: Path, *, methods: bool = True) -> set[str]:
    """Baseline keys, or an EMPTY set when the file does not exist.

    Absent is not zero-and-clean: the caller treats a missing baseline as an
    infrastructure condition, not as "everything is new". ``methods=False``
    leaves out ``method_keys``, for a map that cannot judge them.
    """
    if not path.is_file():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    keys = set(data.get("keys") or [])
    return keys | set(data.get("method_keys") or []) if methods else keys


def baseline_document(
    symbols: Sequence[TestOnlySymbol], *, keep_method_keys: Iterable[str] = (),
) -> dict[str, Any]:
    """The on-disk baseline. Sorted so a regeneration diffs line by line.

    Functions in ``keys``, methods in ``method_keys``: a write from a map
    without receiver typing judges no method, and passes the existing method
    keys through ``keep_method_keys`` instead of dropping them.
    """
    return {
        "_comment": (
            "WI-ratuv shrink-only ratchet. Each key is a production FUNCTION "
            "(keys) or METHOD (method_keys, judged on a map with receiver "
            "typing, WI-mifag) whose only callers are test modules. These "
            "lists may only SHRINK: adding a key requires wiring the callable "
            "to production or retiring it. Regenerate with "
            "scripts/check-test-only-reachability --write-baseline."
        ),
        "keys": sorted({s.key for s in symbols if s.kind != "method"}),
        "method_keys": sorted(
            {s.key for s in symbols if s.kind == "method"} | set(keep_method_keys),
        ),
    }


def compare_to_baseline(
    symbols: Sequence[TestOnlySymbol], baseline: set[str],
) -> tuple[list[str], list[str]]:
    """``(newly test-only, no longer test-only)`` — the ratchet's verdict.

    Shrink-only: the first list failing the gate is the whole contract. The
    second is reported so a drained entry can be removed from the baseline
    rather than accumulating as dead weight.
    """
    current = {s.key for s in symbols}
    return sorted(current - baseline), sorted(baseline - current)


def pass_contribution_problem(data: Mapping[str, Any], pass_id: str) -> str | None:
    """Why ``pass_id`` contributed nothing to the map ``data``, or ``None``.

    WI-losig: the full-suite self-map is built with the scip-python backend so
    a method reference resolves to its owning type. Both of the backend's
    preconditions (the opt-in, the binary on ``PATH``) fail SILENTLY into the
    plain map, and so did WI-nijod's wrong ``cwd`` for as long as the backend
    existed. A gate told to require the pass therefore checks the map itself:
    a skip record, no run record, and a run that emitted nothing are each a
    map the step did not ask for.
    """
    for skipped in (data.get("limits") or {}).get("skipped_passes") or []:
        if skipped.get("pass") == pass_id:
            return (f"{pass_id} was skipped: {skipped.get('reason')} "
                    f"({skipped.get('silence_reason')})")
    runs = [r for r in data.get("analysis_runs") or [] if r.get("pass") == pass_id]
    if not runs:
        return f"{pass_id} has no analysis_runs record: it did not run"
    if not any(r.get("nodes_emitted") for r in runs):
        return (f"{pass_id} ran and emitted nothing "
                f"(silence_reason {runs[0].get('silence_reason')})")
    return None


def typed_method_call_share(
    nodes: Iterable[Mapping[str, Any]], edges: Iterable[Mapping[str, Any]],
    *, origin: str = "scip",
) -> tuple[int, int]:
    """``(typed, total)`` over Python ``calls`` edges whose target is a method.

    Typed means ``origin`` is among the edge's producers: the receiver was
    resolved by type, not by the bare last-name component the syntax arm uses
    -- the property WI-mifag needs before methods can be gated. Measured on
    the self-tree (2026-10-20): 9,435 of 9,619.
    """
    methods = {
        n["id"] for n in nodes
        if n.get("language") == "python" and n.get("kind") == "method"
    }
    calls = [e for e in edges if e.get("type") == "calls" and e.get("dst") in methods]
    typed = sum(1 for e in calls if origin in _origins(e))
    return typed, len(calls)


def _origins(edge: Mapping[str, Any]) -> list[str]:
    """``Edge.origin`` as a list: one producer serialises as a bare string."""
    origin = edge.get("origin") or []
    return [origin] if isinstance(origin, str) else list(origin)
