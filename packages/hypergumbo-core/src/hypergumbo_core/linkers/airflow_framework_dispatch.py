# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: Airflow class-based plugin framework dispatch (WI-nutav).

How It Works
------------
Airflow's scheduler/executor invokes a fixed set of lifecycle methods
(``execute``, ``poke``, ``run``, ``get_conn``, ``on_kill``, …) on user-defined
subclasses of ``BaseOperator`` / ``BaseHook`` / ``BaseSensor`` / ``BaseTrigger``
at runtime. The static call graph never sees those invocations — they happen
through the Airflow framework's own dynamic dispatch — so every override
method looks like a dead function to dead-code analysis.

This linker scans symbols for classes that declare any Airflow base class in
``meta.base_classes``, finds the framework-called methods defined on those
classes (Python analyzer emits method names as ``ClassName.method_name``), and
emits ``dispatches_to`` edges from each Airflow subclass to each of its
override methods. With the class reachable from its enclosing module, the
override methods are pulled out of the dead-code set.

Why a Framework Linker (Not Per-Analyzer Logic)
------------------------------------------------
The inheritance-detection half is language-agnostic and already ships via the
``inheritance`` linker's ``base_classes`` extraction. The Airflow-specific
knowledge is limited to two literals — the base class names and the
framework-called method names — which belong in a single place, not smeared
across every analyzer. Extending to other Python frameworks (Celery tasks,
Django Channels consumers, Scrapy spiders) is a new entry in the
``AIRFLOW_BASE_METHODS`` map, not new per-analyzer code.

Scope (WI-nutav)
----------------
Python-internal: the dispatch happens inside Airflow's scheduler, all in the
same interpreter. The cross-language consequence (``Hook.get_conn`` eventually
invokes cloud-SDK clients) is an IO leaf, not an edge type produced here.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..ir import PASS_VERSION, AnalysisRun, Edge, make_pass_id
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    from ..ir import Symbol

PASS_ID = make_pass_id("airflow-framework-dispatch-linker")

# Map of Airflow base class name -> framework-called method names on that base.
# A subclass of BaseOperator only has execute/pre_execute/... called on it;
# the hook methods (get_conn/test_connection) are BaseHook territory, etc.
# Keeping the mapping per-base avoids emitting spurious edges like
# "BaseOperator subclass's execute dispatches to an unrelated get_conn".
AIRFLOW_BASE_METHODS: dict[str, frozenset[str]] = {
    "BaseOperator": frozenset(
        {"execute", "execute_complete", "pre_execute", "post_execute", "on_kill"},
    ),
    "BaseHook": frozenset({"get_conn", "test_connection"}),
    "BaseSensor": frozenset(
        {"poke", "execute", "execute_complete", "on_kill"},
    ),
    "BaseTrigger": frozenset({"run", "hook"}),
    # SensorOperator / BaseSensorOperator is a common alias for BaseSensor in
    # older plugins; honour both so we don't lose edges on classic codebases.
    "BaseSensorOperator": frozenset(
        {"poke", "execute", "execute_complete", "on_kill"},
    ),
}


def _short_base_name(raw: str) -> str:
    """Strip qualifiers/generics so ``airflow.models.BaseOperator`` matches ``BaseOperator``."""
    name = raw.split("<")[0].strip()
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    if "::" in name:
        name = name.rsplit("::", 1)[-1]
    return name


def _find_airflow_subclasses(
    symbols: list[Symbol],
) -> list[tuple[Symbol, frozenset[str]]]:
    """Return (class_symbol, framework_method_names) for every Airflow subclass.

    A class whose ``base_classes`` metadata names more than one Airflow base
    (e.g., a multi-inherit shim) has the union of both bases' method sets —
    every framework-called method on any matched base is in scope.
    """
    results: list[tuple[Symbol, frozenset[str]]] = []
    for sym in symbols:
        if sym.kind not in ("class", "struct"):
            continue
        base_classes = sym.meta.get("base_classes", []) if sym.meta else []
        if not base_classes:
            continue
        methods: set[str] = set()
        for raw in base_classes:
            short = _short_base_name(raw)
            if short in AIRFLOW_BASE_METHODS:
                methods.update(AIRFLOW_BASE_METHODS[short])
        if methods:
            results.append((sym, frozenset(methods)))
    return results


def _build_method_index(
    symbols: list[Symbol],
) -> dict[tuple[str, str], Symbol]:
    """Index Python methods by (file_path, qualified_name).

    The Python analyzer emits method symbols with ``name`` set to
    ``"ClassName.method_name"`` — see ``py.py`` ~line 1928. Keying by
    ``(path, qualified_name)`` disambiguates same-named methods across
    classes in different files.
    """
    index: dict[tuple[str, str], Symbol] = {}
    for sym in symbols:
        if sym.kind != "method":
            continue
        if "." not in sym.name:
            continue
        # First occurrence wins; ties within a single file are rare and the
        # linker's edge deduper would handle duplicates anyway.
        key = (sym.path or "", sym.name)
        index.setdefault(key, sym)
    return index


@register_linker(
    "airflow-framework-dispatch",
    priority=20,
    description="Emit dispatches_to edges from Airflow subclasses to their framework-called override methods (WI-nutav)",
)
def link_airflow_framework_dispatch(ctx: LinkerContext) -> LinkerResult:
    """Create dispatches_to edges from Airflow subclasses to their framework overrides.

    For each class C whose ``base_classes`` metadata names an Airflow base, walk
    the symbol table for methods named ``"{C.name}.{method}"`` where ``method``
    is a framework-called name for that base, and emit a ``dispatches_to`` edge
    from C to each matching method. The edge makes the method reachable from
    dead-code analysis without requiring the Airflow runtime to be modeled.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    subclasses = _find_airflow_subclasses(ctx.symbols)
    if not subclasses:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LinkerResult(symbols=[], edges=[], run=run)

    method_index = _build_method_index(ctx.symbols)

    # Dedupe against any prior dispatches_to edges already in the graph so
    # re-runs of the linker stack don't multiply edges.
    existing_keys: set[tuple[str, str, str]] = {
        (e.src, e.dst, e.edge_type)
        for e in ctx.edges
        if e.edge_type == "dispatches_to"
    }

    edges: list[Edge] = []
    for class_sym, framework_methods in subclasses:
        for method_name in framework_methods:
            qualified = f"{class_sym.name}.{method_name}"
            target = method_index.get((class_sym.path or "", qualified))
            if target is None:
                continue  # user didn't override this method
            key = (class_sym.id, target.id, "dispatches_to")
            if key in existing_keys:
                continue
            existing_keys.add(key)
            edges.append(
                Edge.create(
                    src=class_sym.id,
                    dst=target.id,
                    edge_type="dispatches_to",
                    line=class_sym.span.start_line if class_sym.span else 0,
                    confidence=0.90,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="airflow_framework_dispatch",
                ),
            )

    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(symbols=[], edges=edges, run=run)
