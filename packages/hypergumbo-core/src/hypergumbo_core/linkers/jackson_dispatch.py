# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: Jackson / JavaBean serialization dispatch (WI-gupah).

How It Works
------------
Java, Kotlin, and Scala reflect-at-runtime over bean-convention accessors
(``getX`` / ``setX`` / ``isX``) when Jackson's ``ObjectMapper`` serializes or
deserializes an instance. The static call graph sees no call into those
accessors, so every field-level getter on every serialized type looks like a
dead function to dead-code analysis — WI-tubot's aggregate-v5 run on the
2026-04-16 prospector corpus pinned ``java_bean_accessor`` at 7614 candidates
(8.3 % of the whole 92218-candidate pool), the #1 non-uncategorized class.

This linker identifies classes the Jackson/JAX-B/Spring-binding runtime will
reflect over and emits ``dispatches_to`` edges from the class to each of its
bean-convention accessor methods. Downstream dead-code analysis then walks
those edges and removes the getter / setter / is-getter from the candidate
list, without needing to model the Jackson runtime.

Detection Criteria
------------------
A class is treated as a serialization target when *any* of the following
holds (any single hit is sufficient — the annotations are designed to be
exclusive markers of "this type is serialized"):

* **Class-level Jackson annotation** — ``@JsonSerialize``, ``@JsonDeserialize``,
  ``@JsonIgnoreProperties``, ``@JsonFormat``, ``@JsonAutoDetect``,
  ``@JsonRootName``, ``@JsonTypeName``, ``@JsonTypeInfo``,
  ``@JsonPropertyOrder``, ``@JsonInclude``, ``@JsonNaming``.
* **JAX-B / XML binding** — ``@XmlRootElement``, ``@XmlType``,
  ``@XmlAccessorType``.
* **Spring configuration binding** — ``@ConfigurationProperties``,
  ``@ConstructorBinding``.
* **JPA persistence** — ``@Entity``, ``@MappedSuperclass``, ``@Embeddable``.
  Jackson routinely serializes JPA-mapped types as Spring MVC REST response
  bodies (Spring Data JPA), so the entire JPA mapping surface is in scope.
* **Any method on the class carries a Jackson method-level annotation** —
  ``@JsonProperty``, ``@JsonGetter``, ``@JsonSetter``, ``@JsonCreator``,
  ``@JsonValue``, ``@JsonAnyGetter``, ``@JsonAnySetter``, ``@JsonRawValue``.
  (Field-level ``@JsonProperty`` is carried on the paired accessor in the
  default Java analyzer output, so the method sweep catches it.)

Qualified names like ``com.fasterxml.jackson.annotation.JsonProperty`` or
``jakarta.xml.bind.annotation.XmlRootElement`` are normalized to the short
final segment before matching, so fully-qualified and shortened annotation
forms are treated as the same.

Edge Emission
-------------
For every serialization-target class ``C``, each method on ``C`` (by
``(path, qualified_name)`` where ``qualified_name == C.name + "." + method``)
whose method name is a bean-convention accessor or carries a method-level
Jackson annotation receives a ``dispatches_to`` edge from ``C`` with
confidence 0.90 and evidence ``jackson_bean_dispatch``.

Bean-convention accessors are:

* ``getX`` with zero parameters — the next character after ``get`` must be
  uppercase ASCII, so ``getUser`` qualifies and ``getter`` (no capitalized
  property) does not.
* ``isX`` with zero parameters — same capitalization rule; covers Java /
  Kotlin boolean getters.
* ``setX`` with one parameter — the caller of ``setX`` is Jackson, so the
  paired setter is reached via the same dispatch as the getter.

Parameter-arity filtering uses ``meta.signature`` when present: a signature
of ``"()"`` is zero-arg, ``"(String s)"`` is one-arg, and so on. When the
method has no signature metadata, conservative defaults apply: ``getX`` /
``isX`` are assumed to be zero-arg accessors and ``setX`` is assumed to be a
one-arg setter — both patterns are universal in bean-convention code and
non-conforming outliers (multi-arg ``getX``) are rare enough that the
occasional spurious edge is a smaller cost than missing legitimate accessors.

Scope
-----
Java / Kotlin / Scala internal. The cross-language consequence (the emitted
JSON is consumed by a TypeScript or Python client) is already modelled by
``openapi.py`` and ``http.py``; this linker is about recovering the
in-JVM dispatch, not crossing the language boundary.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..ir import PASS_VERSION, AnalysisRun, Edge, make_pass_id
from ._transitive_bases import (
    build_inheritance_index,
    build_short_name_collisions,
    collect_transitive_base_names,
    short_name_fallback,
)
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    from ..ir import Symbol

PASS_ID = make_pass_id("jackson-dispatch-linker")

# FQN prefixes that unambiguously name Spring / Jakarta / Jackson framework
# types whose short name appears in :data:`BEAN_MARKER_BASE_CLASSES`. An
# unqualified short-name match against the bean-marker set whose raw base
# entry starts with any of these is precision (not fallback) per
# INV-zuhub. Spring's ``@ConfigurationProperties`` lives under
# ``org.springframework.``; JPA `@Entity` / `@MappedSuperclass` /
# `@Embeddable` live under ``jakarta.persistence.`` (newer) or
# ``javax.persistence.`` (older).
_JACKSON_BEAN_FQN_PREFIXES: tuple[str, ...] = (
    "org.springframework.",
    "jakarta.persistence.",
    "javax.persistence.",
)

# Annotations at class level that designate the type as a serialization
# target. Any one of these on the class declaration is sufficient.
CLASS_LEVEL_SERIALIZATION_ANNOTATIONS: frozenset[str] = frozenset({
    # Jackson
    "JsonSerialize",
    "JsonDeserialize",
    "JsonIgnoreProperties",
    "JsonFormat",
    "JsonAutoDetect",
    "JsonRootName",
    "JsonTypeName",
    "JsonTypeInfo",
    "JsonPropertyOrder",
    "JsonInclude",
    "JsonNaming",
    # JAX-B / XML binding
    "XmlRootElement",
    "XmlType",
    "XmlAccessorType",
    # Spring binding
    "ConfigurationProperties",
    "ConstructorBinding",
    # JPA — Jackson routinely serializes @Entity classes as Spring MVC REST
    # response bodies (the Spring Data JPA + spring-petclinic shape). WI-sokaz:
    # without these triggers the linker emits zero edges for the dominant
    # serialization surface in JPA-backed services.
    "Entity",
    "MappedSuperclass",
    "Embeddable",
})

# Annotations on methods (or the methods paired with annotated fields) that
# designate the method as a directly-dispatched accessor. A class that has
# *any* method carrying one of these is itself a serialization target, and
# each annotated method receives an edge regardless of its bean-convention
# name.
METHOD_LEVEL_SERIALIZATION_ANNOTATIONS: frozenset[str] = frozenset({
    "JsonProperty",
    "JsonGetter",
    "JsonSetter",
    "JsonCreator",
    "JsonValue",
    "JsonAnyGetter",
    "JsonAnySetter",
    "JsonRawValue",
})

# Classes treated as serialization-target base classes even without a
# class-level annotation. Extending one of these is idiomatic "I am a
# serialized type" in the surveyed corpora (see WI-tubot). Kept tight to
# avoid the false-positive cascade of matching every Serializable.
BEAN_MARKER_BASE_CLASSES: frozenset[str] = frozenset({
    # Jackson's companion classes for JSON-backed config objects.
    "ConfigurationProperties",
})


def _short_annotation_name(raw: str) -> str:
    """Return the last dotted segment of a possibly-qualified annotation name."""
    name = raw.split("<")[0].strip()
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    return name


def _decorator_names(meta: object | None) -> set[str]:
    """Return the set of decorator short names on a symbol's meta dict."""
    if not isinstance(meta, dict):
        return set()
    decorators = meta.get("decorators") or meta.get("annotations") or []
    if not isinstance(decorators, list):
        return set()
    names: set[str] = set()
    for dec in decorators:
        if not isinstance(dec, dict):
            continue
        name = dec.get("name")
        if isinstance(name, str) and name:
            names.add(_short_annotation_name(name))
    return names


def _class_has_serialization_hint(
    sym: "Symbol",
    symbol_by_id: dict[str, "Symbol"] | None = None,
    inheritance_index: dict[str, list[str]] | None = None,
) -> bool:
    """True when a class symbol carries a serialization annotation or extends a bean-marker base.

    The bean-marker base check walks the class's transitive base-class
    chain (WI-vigih) so a class that extends an in-tree intermediate
    which itself extends ``ConfigurationProperties`` is matched the same
    as a direct subclass. ``symbol_by_id`` and ``inheritance_index`` are
    optional for backward compatibility — when omitted, only the class's
    own ``meta.base_classes`` is consulted.
    """
    names = _decorator_names(sym.meta)
    if names & CLASS_LEVEL_SERIALIZATION_ANNOTATIONS:
        return True
    if symbol_by_id is None:
        symbol_by_id = {sym.id: sym}
    if inheritance_index is None:
        inheritance_index = {}
    chain = collect_transitive_base_names(sym, symbol_by_id, inheritance_index)
    for raw in chain:
        if _short_annotation_name(raw) in BEAN_MARKER_BASE_CLASSES:
            return True
    return False


def _method_is_serialization_annotated(sym: "Symbol") -> bool:
    """True when a method symbol carries a Jackson method-level annotation."""
    return bool(_decorator_names(sym.meta) & METHOD_LEVEL_SERIALIZATION_ANNOTATIONS)


def _is_bean_accessor_name(name: str, signature: str | None) -> bool:
    """True when a method's name matches the bean get/is/set convention.

    The next character after the prefix must be an uppercase ASCII letter,
    so ``get`` alone or ``getter`` don't qualify — only ``getUser``,
    ``isActive``, ``setPath``, etc. When a signature is available, the
    parameter count is checked (zero for get/is, one for set). When no
    signature is present, the conservative default matches bean-convention
    expectations.
    """
    if len(name) <= 3:
        return False
    if name.startswith("get") and name[3].isascii() and name[3].isupper():
        return _signature_arity(signature, default=0) == 0
    if name.startswith("set") and name[3].isascii() and name[3].isupper():
        return _signature_arity(signature, default=1) == 1
    if len(name) > 2 and name.startswith("is") and name[2].isascii() and name[2].isupper():
        return _signature_arity(signature, default=0) == 0
    return False


def _signature_arity(signature: str | None, *, default: int) -> int:
    """Count the parameters in ``signature`` or fall back to ``default``.

    The Java analyzer emits signatures like ``"(String name, int age) -> Foo"``.
    Counting commas inside the outermost parenthesised argument list works for
    these simple cases; when no argument list is present or the parse fails,
    ``default`` is returned so bean-convention name matching still functions.
    """
    if not signature:
        return default
    try:
        open_idx = signature.index("(")
        depth = 0
        close_idx = -1
        for i in range(open_idx, len(signature)):
            c = signature[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    close_idx = i
                    break
        if close_idx <= open_idx:
            return default
    except ValueError:
        return default
    inner = signature[open_idx + 1:close_idx].strip()
    if not inner:
        return 0
    depth = 0
    count = 1
    for c in inner:
        if c in "([<{":
            depth += 1
        elif c in ")]>}":
            depth -= 1
        elif c == "," and depth == 0:
            count += 1
    return count


def _get_signature(sym: "Symbol") -> str | None:
    """Prefer Symbol.signature; fall back to meta['signature'] when the analyzer stores it there."""
    sig = getattr(sym, "signature", None)
    if sig:
        return sig
    if isinstance(sym.meta, dict):
        alt = sym.meta.get("signature")
        if isinstance(alt, str) and alt:
            return alt
    return None


def _build_class_method_index(
    symbols: list["Symbol"],
) -> dict[tuple[str, str], list["Symbol"]]:
    """Group method symbols by ``(path, owning_class_name)``.

    Java / Kotlin / Scala analyzers emit method ``Symbol.name`` as
    ``"ClassName.methodName"`` (see java.py ~line 129). Splitting on the
    last ``.`` recovers the owning class; methods without a dotted name
    (top-level Kotlin functions, Scala object methods) are skipped — this
    linker only cares about class-owned bean accessors.
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


def _find_bean_target_classes(
    symbols: list["Symbol"],
    method_index: dict[tuple[str, str], list["Symbol"]],
    edges: list[Edge] | None = None,
    in_tree_collisions: frozenset[str] = frozenset(),
) -> list[tuple["Symbol", bool]]:
    """Return ``(class_sym, is_fallback)`` for each bean-dispatch target class.

    A class is a target when it carries a class-level serialization hint, or
    when any of its methods carries a method-level Jackson annotation. The
    second clause covers the ``@JsonProperty`` on a field → getter case: the
    Java analyzer carries field-level annotations onto the paired accessor
    method's decorators, so a method sweep under the class finds the hint
    even if the class declaration itself is unannotated.

    INV-zuhub: ``is_fallback`` is ``True`` iff the class qualified
    **only** via a bean-marker base whose short name has an in-tree
    collision and whose raw entry was unqualified. The class-level
    annotation and method-level annotation paths are precision (the
    Jackson / Spring annotation namespace is the canonical
    disambiguator), and any FQN-qualified bean-marker base is also
    precision. A class with mixed paths (one precision match + one
    fallback match) resolves as precision.
    """
    edges = edges or []
    inheritance_index = build_inheritance_index(edges)
    symbol_by_id = {sym.id: sym for sym in symbols}

    targets: list[tuple[Symbol, bool]] = []
    for sym in symbols:
        if sym.kind not in {"class", "interface", "struct"}:
            continue
        if sym.language not in {"java", "kotlin", "scala"}:
            continue
        # Class-level annotation path — precision.
        if _decorator_names(sym.meta) & CLASS_LEVEL_SERIALIZATION_ANNOTATIONS:
            targets.append((sym, False))
            continue
        # Bean-marker base path — INV-zuhub fallback risk. Track each
        # base-marker match and resolve precision-wins-over-fallback.
        chain = collect_transitive_base_names(sym, symbol_by_id, inheritance_index)
        any_precision_base = False
        any_fallback_base = False
        for raw in chain:
            short = _short_annotation_name(raw)
            if short in BEAN_MARKER_BASE_CLASSES:
                if short_name_fallback(
                    raw, short, in_tree_collisions, _JACKSON_BEAN_FQN_PREFIXES,
                ):
                    any_fallback_base = True
                else:
                    any_precision_base = True
        if any_precision_base or any_fallback_base:
            targets.append((sym, any_fallback_base and not any_precision_base))
            continue
        # Method-level annotation path — precision.
        key = (sym.path or "", sym.name)
        class_methods = method_index.get(key, [])
        if any(_method_is_serialization_annotated(m) for m in class_methods):
            targets.append((sym, False))
    return targets


def _select_dispatch_targets(methods: list["Symbol"]) -> list["Symbol"]:
    """Filter a class's methods down to the ones Jackson will reflectively call."""
    selected: list[Symbol] = []
    for m in methods:
        method_name = m.name.rsplit(".", 1)[-1]
        signature = _get_signature(m)
        if _method_is_serialization_annotated(m):
            selected.append(m)
            continue
        if _is_bean_accessor_name(method_name, signature):
            selected.append(m)
    return selected


@register_linker(
    "jackson-dispatch-linker",
    priority=21,
    description="Emit dispatches_to edges from Jackson/JavaBean serialization targets to their bean accessors (WI-gupah)",
)
def link_jackson_dispatch(ctx: LinkerContext) -> LinkerResult:
    """Recover Jackson / JavaBean reflective dispatch edges.

    See module docstring for the detection criteria and edge semantics.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    method_index = _build_class_method_index(ctx.symbols)
    # INV-zuhub: build the in-tree-collision set for bean-marker base
    # short names so the target walker can flag short-name fallback matches.
    in_tree_collisions = build_short_name_collisions(
        ctx.symbols,
        BEAN_MARKER_BASE_CLASSES,
        kinds=frozenset({"class", "interface", "struct"}),
        languages=frozenset({"java", "kotlin", "scala"}),
    )
    targets = _find_bean_target_classes(
        ctx.symbols, method_index, ctx.edges, in_tree_collisions,
    )
    if not targets:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LinkerResult(symbols=[], edges=[], run=run)

    existing_keys: set[tuple[str, str, str]] = {
        (e.src, e.dst, e.edge_type)
        for e in ctx.edges
        if e.edge_type == "dispatches_to"
    }

    edges: list[Edge] = []
    for class_sym, is_fallback in targets:
        key = (class_sym.path or "", class_sym.name)
        class_methods = method_index.get(key, [])
        for method in _select_dispatch_targets(class_methods):
            edge_key = (class_sym.id, method.id, "dispatches_to")
            if edge_key in existing_keys:
                continue
            existing_keys.add(edge_key)
            confidence = 0.5 if is_fallback else 0.90
            edge_meta: dict[str, object] = {"framework_dispatch": "jackson_bean"}
            if is_fallback:
                edge_meta["disambiguation_fallback"] = True
            edges.append(
                Edge.create(
                    src=class_sym.id,
                    dst=method.id,
                    edge_type="dispatches_to",
                    line=class_sym.span.start_line if class_sym.span else 0,
                    confidence=confidence,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_decorator",
                    meta=edge_meta,
                ),
            )

    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(symbols=[], edges=edges, run=run)
