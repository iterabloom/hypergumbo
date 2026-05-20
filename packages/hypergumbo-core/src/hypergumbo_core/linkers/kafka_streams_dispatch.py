# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: Kafka Streams topology-builder dispatch (WI-lisov).

How It Works
------------
Kafka Streams' DSL (``StreamsBuilder.stream().filter().map().through().to()``)
registers lambda and class implementations of its callback interfaces at
topology-build time. At runtime the framework invokes a fixed, interface-
specific method on each impl once per record. The static call graph sees
the registration site but never the per-record method call, so the impl
classes and their interface-declared methods look like dead code to dead-
code analysis — WI-tubot aggregate-v5 pinned ``kafka_streams_internal`` at
2386 candidates (2.6% of the 92218-candidate pool on the 2026-04-16 corpus).

This linker identifies concrete implementations of the Kafka Streams
callback interfaces and emits ``dispatches_to`` edges from each impl class
to the framework-called method(s) defined by that interface. Downstream
dead-code analysis then walks those edges and removes the callbacks from
the candidate list, without needing to model the Kafka Streams runtime.

Scope
-----
JVM-internal Framework-subcategory linker. Java / Kotlin / Scala classes
that extend or implement an interface from
``org.apache.kafka.streams.kstream`` or ``org.apache.kafka.streams.processor``.
The cross-language story (Kafka records flow to consumers in other
languages) is handled separately by message-queue linkers; this pass only
recovers in-JVM reflective dispatch of the topology callbacks.

Detection Criteria
------------------
A class is treated as a Kafka Streams dispatch target when any of its
declared base classes or interfaces resolves (by short last-segment name)
to a known callback interface. Fully-qualified names like
``org.apache.kafka.streams.kstream.ValueMapper<K, V, VR>`` are normalized
to the short form ``ValueMapper`` before lookup, so both qualified and
unqualified imports are matched the same way.

The ``.streams-scala`` wrapper types (``org.apache.kafka.streams.scala.kstream.*``)
resolve to the same short names via the shared namespace, so Scala
consumers of the DSL are covered by the same interface set.

Edge Emission
-------------
For every Kafka Streams callback impl class ``C`` whose short base-name
is in :data:`KAFKA_STREAMS_CALLBACKS`, each method on ``C`` whose short
name is listed for that interface in :data:`KAFKA_STREAMS_CALLBACKS`
receives a ``dispatches_to`` edge from ``C`` with confidence 0.90 and
evidence ``kafka_streams_dispatch``.

INV-zuhub disambiguation
~~~~~~~~~~~~~~~~~~~~~~~~
The short-name match against :data:`KAFKA_STREAMS_CALLBACKS` cannot
distinguish kafka's external interface from an in-tree JVM class that
happens to share the short name (e.g. a user-defined
``Transformer<T>`` in oauthbearer code that has no relation to
``org.apache.kafka.streams.kstream.Transformer``). Per INV-zuhub item 1,
such ambiguous matches downgrade:

- Raw entry FQN-prefixed with ``org.apache.kafka.*`` → precision match;
  the in-tree collision (if any) is irrelevant. ``confidence=0.90``.
- No in-tree JVM type shares the matched short name → precision match
  by elimination. ``confidence=0.90``.
- Unqualified raw entry whose short name collides with an in-tree JVM
  class / interface / struct → simple-name fallback. ``confidence=0.5``
  and ``meta["disambiguation_fallback"] = True`` so downstream
  consumers can filter the fallback population.

Multiple interfaces can resolve to overlapping method sets
(``ValueTransformer``, ``Transformer``, and ``Processor`` all declare
``init`` and ``close``; ``Aggregator``, ``Reducer``, ``Initializer``,
``Merger``, ``ValueMapper``, ``KeyValueMapper``, and ``ForeachAction``
all use ``apply``). A class that implements more than one still receives
one edge per matched method — the de-duplication key is
``(class_id, method_id, "dispatches_to")``.

Parameter-arity is not checked. The selected method names are universal
across the Kafka Streams APIs and there is no legitimate overload of
``apply`` / ``transform`` / ``process`` / ``get`` / ``init`` / ``close``
/ ``test`` inside a concrete callback impl that would dispatch to the
"wrong" overload.

Not In Scope
------------
* Call-site detection for ``streamsBuilder.stream().map(new Mapper())``
  (WI-lisov Phase 2) — would emit per-call-site edges rather than
  class-to-method edges. The class-to-method edges here are sufficient
  to lift the callbacks out of dead-code candidates, which is the stated
  acceptance metric.
* SerDe classes (``Serializer.serialize`` / ``Deserializer.deserialize``).
  They share the reflective-dispatch shape but flow via the
  message-queue record path, which is its own linker scope.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..ir import PASS_VERSION, AnalysisRun, Edge, make_pass_id
from ._transitive_bases import (
    build_inheritance_index,
    collect_transitive_base_names,
)
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    from ..ir import Symbol

PASS_ID = make_pass_id("kafka-streams-dispatch-linker")

# Kafka FQN prefix used by every package under
# ``org.apache.kafka.streams.{kstream,processor,scala}.*``. A raw entry that
# starts with this prefix is unambiguously the external interface — never an
# in-tree class — and qualifies for the precision branch even when the short
# name has an in-tree collision. Anything shorter (bare or partially
# qualified) is treated as the fallback branch under the INV-zuhub
# disambiguation contract.
_KAFKA_FQN_PREFIX = "org.apache.kafka."

# Kafka Streams callback interface → framework-called method names.
# Sources:
# - org.apache.kafka.streams.kstream.*
# - org.apache.kafka.streams.processor.*
# - org.apache.kafka.streams.scala.kstream.* (same short names)
KAFKA_STREAMS_CALLBACKS: dict[str, frozenset[str]] = {
    # Single-record mapping / filtering
    "ValueMapper": frozenset({"apply"}),
    "ValueMapperWithKey": frozenset({"apply"}),
    "KeyValueMapper": frozenset({"apply"}),
    "Predicate": frozenset({"test"}),
    "ForeachAction": frozenset({"apply"}),
    # Aggregation primitives
    "Aggregator": frozenset({"apply"}),
    "Reducer": frozenset({"apply"}),
    "Initializer": frozenset({"apply"}),
    "Merger": frozenset({"apply"}),
    # Stateful transform / process — includes lifecycle methods.
    "ValueTransformer": frozenset({"transform", "init", "close"}),
    "ValueTransformerWithKey": frozenset({"transform", "init", "close"}),
    "Transformer": frozenset({"transform", "init", "close"}),
    "Processor": frozenset({"process", "init", "close"}),
    # Supplier factories return the above — Kafka Streams calls `get()`
    # to produce a fresh impl per task.
    "ValueTransformerSupplier": frozenset({"get"}),
    "ValueTransformerWithKeySupplier": frozenset({"get"}),
    "TransformerSupplier": frozenset({"get"}),
    "ProcessorSupplier": frozenset({"get"}),
}


def _short_type_name(raw: str) -> str:
    """Return the last dotted segment of a qualified type, stripped of generics.

    ``org.apache.kafka.streams.kstream.ValueMapper<K, V, VR>`` → ``ValueMapper``.
    Also handles bare names (``ValueMapper`` → ``ValueMapper``) and shapes
    with only generics (``ValueMapper<K>`` → ``ValueMapper``).
    """
    name = raw.split("<")[0].strip()
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    return name


def _callback_interface_matches(
    sym: "Symbol",
    symbol_by_id: dict[str, "Symbol"] | None = None,
    inheritance_index: dict[str, list[str]] | None = None,
) -> list[tuple[str, str]]:
    """Return ``(short_interface_name, raw_entry)`` pairs the class declares.

    Same shape as :func:`_callback_interfaces_on` but preserves the raw
    base-class string the analyzer recorded, so the caller can apply the
    INV-zuhub disambiguation rule (precise when the raw entry is
    FQN-qualified with the kafka namespace; fallback when an unqualified
    short name collides with an in-tree class).
    """
    if not isinstance(sym.meta, dict):
        return []
    if symbol_by_id is None:
        symbol_by_id = {sym.id: sym}
    if inheritance_index is None:
        inheritance_index = {}
    chain = collect_transitive_base_names(
        sym, symbol_by_id, inheritance_index,
        meta_keys=("base_classes", "interfaces"),
    )
    seen: set[str] = set()
    found: list[tuple[str, str]] = []
    for entry in chain:
        short = _short_type_name(entry)
        if short in KAFKA_STREAMS_CALLBACKS and short not in seen:
            seen.add(short)
            found.append((short, entry))
    return found


def _callback_interfaces_on(
    sym: "Symbol",
    symbol_by_id: dict[str, "Symbol"] | None = None,
    inheritance_index: dict[str, list[str]] | None = None,
) -> list[str]:
    """Return the Kafka Streams callback interfaces the class declares.

    Walks the transitive base-class chain (extends + implements) so a
    class that inherits its callback interface through an in-tree
    intermediate (Kotlin / Scala SAM-style wrapper) matches the same as
    a direct subclass. Java, Kotlin, and Scala analyzers store the
    ``extends`` / ``implements`` lists under separate metadata keys, so
    both are passed to the helper. Returns the ordered, deduplicated
    list of short interface names present in
    :data:`KAFKA_STREAMS_CALLBACKS`. (WI-vigih.)

    ``symbol_by_id`` and ``inheritance_index`` are optional for
    backward compatibility — when omitted, only the class's own
    ``meta.base_classes`` and ``meta.interfaces`` are consulted.

    This is a short-name-only projection of
    :func:`_callback_interface_matches`; new callers that need the raw
    entry (e.g. for disambiguation decisions) should call the underlying
    helper directly.
    """
    return [short for short, _raw in _callback_interface_matches(
        sym, symbol_by_id, inheritance_index,
    )]


def _build_in_tree_callback_name_collisions(
    symbols: list["Symbol"],
) -> frozenset[str]:
    """Return short names of in-tree JVM types that collide with kafka callback interfaces.

    A class / interface / struct on the JVM (java / kotlin / scala) whose
    short name matches a key in :data:`KAFKA_STREAMS_CALLBACKS` creates
    a structural ambiguity: a class declaring an unqualified base of
    ``Transformer`` could mean either the in-tree ``Transformer`` or
    kafka's external one, and the static analysis cannot tell them
    apart without import-context resolution (which this linker does
    not have).

    Per INV-zuhub, the dispatch edge in such cases must carry
    ``confidence <= 0.5`` and ``meta["disambiguation_fallback"] = True``.
    Edges whose declaring base was FQN-qualified with the kafka
    namespace are unambiguously precision matches regardless of the
    in-tree collision set.
    """
    collisions: set[str] = set()
    for sym in symbols:
        if sym.kind not in {"class", "interface", "struct"}:
            continue
        if sym.language not in {"java", "kotlin", "scala"}:
            continue
        if sym.name in KAFKA_STREAMS_CALLBACKS:
            collisions.add(sym.name)
    return frozenset(collisions)


def _is_fallback_match(
    raw_entry: str, short_name: str, in_tree_collisions: frozenset[str],
) -> bool:
    """Whether ``(raw_entry → short_name)`` is a simple-name fallback under INV-zuhub.

    A match is precise (not fallback) when either:

    - The raw entry is FQN-qualified with ``org.apache.kafka.*`` — the
      external interface is named in full, so the in-tree collision
      (if any) is irrelevant.
    - There is no in-tree JVM type whose short name matches — the
      unqualified ``Transformer`` cannot mean anything else, so the
      external interface is the only candidate.

    A match is fallback when an unqualified raw entry's short name
    has an in-tree collision: kafka's ``Transformer`` and the in-tree
    ``Transformer`` are both candidates and the static analysis cannot
    disambiguate them.
    """
    if raw_entry.startswith(_KAFKA_FQN_PREFIX):
        return False
    return short_name in in_tree_collisions


def _build_class_method_index(
    symbols: list["Symbol"],
) -> dict[tuple[str, str], list["Symbol"]]:
    """Group method symbols by ``(path, owning_class_name)``.

    Mirrors :func:`hypergumbo_core.linkers.jackson_dispatch._build_class_method_index`:
    Java / Kotlin / Scala analyzers emit method ``Symbol.name`` as
    ``"ClassName.methodName"``. Splitting on the last ``.`` recovers the
    owner; top-level functions without a dotted name are skipped because
    Kafka Streams callbacks are always declared inside a class / object.
    """
    index: dict[tuple[str, str], list["Symbol"]] = {}
    for sym in symbols:
        if sym.kind != "method":
            continue
        if sym.language not in {"java", "kotlin", "scala"}:
            continue
        if "." not in sym.name:
            continue
        class_name, _method_name = sym.name.rsplit(".", 1)
        key = (sym.path or "", class_name)
        index.setdefault(key, []).append(sym)
    return index


def _expected_method_names(interfaces: list[str]) -> frozenset[str]:
    """Union the callback-method sets for every matched interface."""
    expected: set[str] = set()
    for iface in interfaces:
        expected.update(KAFKA_STREAMS_CALLBACKS[iface])
    return frozenset(expected)


@register_linker(
    "kafka-streams-dispatch-linker",
    priority=21,
    description=(
        "Emit dispatches_to edges from Kafka Streams callback impls "
        "to their framework-called methods (WI-lisov)"
    ),
)
def link_kafka_streams_dispatch(ctx: LinkerContext) -> LinkerResult:
    """Recover Kafka Streams topology-callback dispatch edges.

    See module docstring for the detection criteria and edge semantics.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    method_index = _build_class_method_index(ctx.symbols)
    inheritance_index = build_inheritance_index(ctx.edges)
    symbol_by_id = {sym.id: sym for sym in ctx.symbols}
    in_tree_collisions = _build_in_tree_callback_name_collisions(ctx.symbols)
    existing_keys: set[tuple[str, str, str]] = {
        (e.src, e.dst, e.edge_type)
        for e in ctx.edges
        if e.edge_type == "dispatches_to"
    }

    edges: list[Edge] = []
    for sym in ctx.symbols:
        if sym.kind not in {"class", "interface", "struct"}:
            continue
        if sym.language not in {"java", "kotlin", "scala"}:
            continue
        interface_matches = _callback_interface_matches(
            sym, symbol_by_id, inheritance_index,
        )
        if not interface_matches:
            continue
        # Per INV-zuhub: when any matched interface short-name collides with
        # an in-tree JVM type and the raw declaration was unqualified, the
        # static resolution is a simple-name fallback and the resulting
        # edges must downgrade.
        is_fallback = any(
            _is_fallback_match(raw, short, in_tree_collisions)
            for short, raw in interface_matches
        )
        interfaces = [short for short, _raw in interface_matches]
        expected = _expected_method_names(interfaces)
        class_methods = method_index.get((sym.path or "", sym.name), [])
        for method in class_methods:
            short_method_name = method.name.rsplit(".", 1)[-1]
            if short_method_name not in expected:
                continue
            edge_key = (sym.id, method.id, "dispatches_to")
            if edge_key in existing_keys:
                continue
            existing_keys.add(edge_key)
            confidence = 0.5 if is_fallback else 0.90
            edge_meta = (
                {"framework_dispatch": "kafka_streams",
                 "disambiguation_fallback": True}
                if is_fallback
                else {"framework_dispatch": "kafka_streams"}
            )
            edges.append(
                Edge.create(
                    src=sym.id,
                    dst=method.id,
                    edge_type="dispatches_to",
                    line=sym.span.start_line if sym.span else 0,
                    confidence=confidence,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_call_direct",
                    meta=edge_meta,
                ),
            )

    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(symbols=[], edges=edges, run=run)
