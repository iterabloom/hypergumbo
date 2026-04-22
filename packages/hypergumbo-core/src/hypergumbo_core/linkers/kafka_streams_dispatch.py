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
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    from ..ir import Symbol

PASS_ID = make_pass_id("kafka-streams-dispatch-linker")

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


def _callback_interfaces_on(sym: "Symbol") -> list[str]:
    """Return the Kafka Streams callback interfaces the class declares.

    Reads ``sym.meta['base_classes']`` and ``sym.meta['interfaces']`` — Java,
    Kotlin, and Scala analyzers store the ``extends`` / ``implements`` lists
    under one of those keys depending on the language surface. Returns the
    ordered, deduplicated list of short interface names present in
    :data:`KAFKA_STREAMS_CALLBACKS`.
    """
    if not isinstance(sym.meta, dict):
        return []
    seen: set[str] = set()
    found: list[str] = []
    for key in ("base_classes", "interfaces"):
        raw = sym.meta.get(key) or []
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if not isinstance(entry, str):
                continue
            short = _short_type_name(entry)
            if short in KAFKA_STREAMS_CALLBACKS and short not in seen:
                seen.add(short)
                found.append(short)
    return found


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
    "kafka-streams-dispatch",
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
        interfaces = _callback_interfaces_on(sym)
        if not interfaces:
            continue
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
            edges.append(
                Edge.create(
                    src=sym.id,
                    dst=method.id,
                    edge_type="dispatches_to",
                    line=sym.span.start_line if sym.span else 0,
                    confidence=0.90,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="kafka_streams_dispatch",
                ),
            )

    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(symbols=[], edges=edges, run=run)
