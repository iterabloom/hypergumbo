# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: third-party Django-ecosystem base allow-list (WI-jifib).

Cascade implementation from WI-jusih's data pass: four families of
well-known third-party Django bases that ``django_orm_dispatch``'s
``DJANGO_BASE_METHODS`` table doesn't (and can't) cover, because they
live in third-party packages whose ``meta.base_classes`` is invisible
to in-tree analysis:

- ``HierarkeyForm`` (hierarkey) — pretix-flavored settings forms
- ``FilterSet`` / ``WagtailFilterSet`` (django-filter + Wagtail extension)
- DRF serializer family — ``Serializer`` / ``ModelSerializer`` /
  ``HyperlinkedModelSerializer``
- ``Page`` (Wagtail) — CMS page models

WI-jusih's data pass (`~/hypergumbo_lab_notebook/wi-jusih-data-pass-20260515/report.md`)
demonstrated bucket-A verdicts for every entry: positive rescue counts
across pretix/bakerydemo/lutris and zero base-extension false-positives
in a 15-repo non-Django corpus.

Gating
------
``LinkerActivation(frameworks=["django"])`` — the linker only runs when
Django is detected on the analyzed repo. The framework detector folds
DRF / django-filter / Wagtail signals under the single "django" name,
so a single gate covers all four families. Combined with the empirical
FP rate of 0/15 across a non-Django corpus, this hard-cutoff gate
delivers AC-3: a non-Django repo emits zero edges from this linker.

INV-zuhub
---------
Same discipline as ``django_orm_dispatch``: when the chain entry that
matched the allow-list is UNQUALIFIED (no recognized FQN prefix) AND
an in-tree Python class shares the matched short name, treat the
match as a simple-name fallback — confidence ≤ 0.5 with
``meta["disambiguation_fallback"] = True``. The empirical collision
rate on the three WI-jusih targets was 0/175, but consistency with the
existing framework-base linker discipline is cheap.

Why not extend DJANGO_BASE_METHODS
-----------------------------------
1. Activation: the existing linker is ``always=True``; folding third-
   party entries there would fire on non-Django repos where the FP
   risk has not been measured.
2. Provenance: a distinct ``framework_dispatch`` meta label keeps the
   edge source identifiable, which matters for downstream consumers
   that may want to weight third-party-allow-list edges differently
   from Django-stdlib edges.
3. Separation: the WI-jusih data pass cleanly separates Django-stdlib
   coverage (already handled, no work needed) from third-party gaps
   (this module). Keeping the modules separate preserves that
   conceptual boundary.
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
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerResult,
    register_linker,
)

if TYPE_CHECKING:
    from ..ir import Symbol

PASS_ID = make_pass_id("django-third-party-dispatch-linker")

# FQN prefixes that unambiguously name third-party Django-ecosystem
# framework types. Per INV-zuhub, an unqualified short-name match whose
# raw base entry starts with one of these is a precision match (high
# confidence) even when an in-tree class shares the matched short name.
_THIRD_PARTY_FQN_PREFIXES: tuple[str, ...] = (
    "hierarkey.",
    "django_filters.",
    "rest_framework.",
    "wagtail.",
)

# DRF Serializer methods called by the framework. ``ModelSerializer``
# and ``HyperlinkedModelSerializer`` add ``create`` / ``update`` (the
# bare ``Serializer`` base doesn't define them) plus build-field hooks.
_DRF_SERIALIZER_METHODS: frozenset[str] = frozenset({
    "to_representation", "to_internal_value",
    "validate", "is_valid", "run_validation",
    "save", "bind",
    "get_fields", "get_initial", "get_attribute",
    "get_value", "get_validators",
})

_DRF_MODEL_SERIALIZER_METHODS: frozenset[str] = (
    _DRF_SERIALIZER_METHODS | frozenset({
        "create", "update",
        "build_standard_field", "build_relational_field",
        "build_property_field", "build_unknown_field",
    })
)

# Wagtail Page lifecycle. Page inherits from Django's models.Model so
# save/delete/clean are present, but the Wagtail-specific hooks (serve,
# route, get_context, etc.) are what the framework dispatches at
# request time.
_WAGTAIL_PAGE_METHODS: frozenset[str] = frozenset({
    "serve", "route",
    "get_template", "get_context", "get_url_parts", "get_url",
    "get_sitemap_urls", "get_cached_paths",
    "get_admin_display_title", "with_content_json",
    "can_create_at", "can_move_to",
    "save", "delete", "clean",
})

# django-filter FilterSet methods. Most FilterSets are declarative
# (field assignments only) but custom filter logic overrides
# ``filter_queryset`` / ``qs`` / ``get_form_class``.
_DJANGO_FILTER_METHODS: frozenset[str] = frozenset({
    "filter_queryset", "qs",
    "get_form_class", "get_filters",
    "is_valid",
})

# HierarkeyForm extends Django's forms.Form and adds settings-store
# persistence — the framework calls Form's clean/full_clean/is_valid
# alongside HierarkeyForm's save hook.
_HIERARKEY_FORM_METHODS: frozenset[str] = frozenset({
    "clean", "full_clean", "is_valid", "has_changed",
    "_post_clean", "_clean_form", "_clean_fields",
    "save",
})

# Map of third-party base class name -> framework-called method names.
# Each entry's simple name is unique across the table (asserted by
# property test in test_third_party_bases_linker.py). The table is the
# single source of truth — adding a new third-party base means one
# entry here plus an FQN prefix in ``_THIRD_PARTY_FQN_PREFIXES`` if
# its namespace isn't already covered.
THIRD_PARTY_BASE_METHODS: dict[str, frozenset[str]] = {
    "HierarkeyForm": _HIERARKEY_FORM_METHODS,
    "FilterSet": _DJANGO_FILTER_METHODS,
    "WagtailFilterSet": _DJANGO_FILTER_METHODS,
    "Serializer": _DRF_SERIALIZER_METHODS,
    "ModelSerializer": _DRF_MODEL_SERIALIZER_METHODS,
    "HyperlinkedModelSerializer": _DRF_MODEL_SERIALIZER_METHODS,
    "Page": _WAGTAIL_PAGE_METHODS,
}


def _short_base_name(raw: str) -> str:
    """Strip qualifiers/generics so ``wagtail.models.Page`` → ``Page``.

    Mirrors django_orm_dispatch._short_base_name. The duplication is
    deliberate: the two modules consume the helper at different
    semantic layers (Django stdlib vs third-party allow-list) and
    sharing would require pulling a tiny string-munging helper into
    the shared ``_transitive_bases`` module, which it doesn't really
    own.
    """
    name = raw.split("[")[0].split("<")[0].strip()
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    return name


def _find_third_party_subclasses(
    symbols: list["Symbol"],
    edges: list[Edge] | None = None,
    in_tree_collisions: frozenset[str] = frozenset(),
) -> list[tuple["Symbol", frozenset[str], bool]]:
    """Return (class_symbol, framework_method_names, is_fallback) for every match.

    A class qualifies when **any** name in its transitive ``base_classes``
    chain matches a key in ``THIRD_PARTY_BASE_METHODS``. The chain is
    walked the same way ``_find_django_subclasses`` walks it
    (BFS via extends/implements edges), so intermediate in-tree
    wrappers don't break detection.

    INV-zuhub: ``is_fallback`` is ``True`` iff **any** matching raw
    entry on the chain was unqualified (no FQN prefix from
    ``_THIRD_PARTY_FQN_PREFIXES``) and its short name collides with an
    in-tree Python class.
    """
    edges = edges or []
    inheritance_index = build_inheritance_index(edges)
    symbol_by_id = {sym.id: sym for sym in symbols}

    results: list[tuple[Symbol, frozenset[str], bool]] = []
    for sym in symbols:
        if sym.kind not in ("class", "struct"):
            continue
        if sym.language != "python":
            continue
        if not sym.meta or not sym.meta.get("base_classes"):
            continue
        chain = collect_transitive_base_names(
            sym, symbol_by_id, inheritance_index,
        )
        methods: set[str] = set()
        is_fallback = False
        for raw in chain:
            short = _short_base_name(raw)
            if short in THIRD_PARTY_BASE_METHODS:
                methods.update(THIRD_PARTY_BASE_METHODS[short])
                if short_name_fallback(
                    raw, short, in_tree_collisions,
                    _THIRD_PARTY_FQN_PREFIXES,
                ):
                    is_fallback = True
        if methods:
            results.append((sym, frozenset(methods), is_fallback))
    return results


def _build_method_index(
    symbols: list["Symbol"],
) -> dict[tuple[str, str], "Symbol"]:
    """Index Python methods by ``(file_path, qualified_name)``.

    Mirrors django_orm_dispatch._build_method_index. The Python
    analyzer emits method symbols with ``name`` set to
    ``"ClassName.method_name"``; keying by ``(path, qualified_name)``
    disambiguates same-named methods across classes in different files.
    """
    index: dict[tuple[str, str], Symbol] = {}
    for sym in symbols:
        if sym.kind != "method":
            continue
        if sym.language != "python":
            continue
        if "." not in sym.name:
            continue
        key = (sym.path or "", sym.name)
        index.setdefault(key, sym)
    return index


@register_linker(
    "django-third-party-dispatch-linker",
    priority=23,  # one slot after django-orm-dispatch (22) so dedup catches overlaps
    description="Emit dispatches_to edges from third-party Django/DRF/Wagtail/hierarkey subclasses to framework-called override methods (WI-jifib)",
    activation=LinkerActivation(frameworks=["django"]),
    # CNF: Django and the third-party Django ecosystem are Python-only.
    depends_on=[["python"]],
)
def link_django_third_party_dispatch(ctx: LinkerContext) -> LinkerResult:
    """Create dispatches_to edges from third-party-base subclasses to their overrides.

    For each class ``C`` whose transitive ``base_classes`` chain reaches
    a key in ``THIRD_PARTY_BASE_METHODS``, walk the symbol table for
    methods named ``"{C.name}.{method}"`` where ``method`` is a
    framework-called name for that base, and emit a ``dispatches_to``
    edge from ``C`` to each matching method. Pulls the override
    methods out of the dead-code set without requiring the third-party
    runtime to be modeled.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # INV-zuhub: short-name collisions for third-party base names.
    in_tree_collisions = build_short_name_collisions(
        ctx.symbols,
        frozenset(THIRD_PARTY_BASE_METHODS),
        kinds=frozenset({"class", "struct"}),
        languages=frozenset({"python"}),
    )

    subclasses = _find_third_party_subclasses(
        ctx.symbols, ctx.edges, in_tree_collisions,
    )
    if not subclasses:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LinkerResult(symbols=[], edges=[], run=run)

    method_index = _build_method_index(ctx.symbols)

    existing_keys: set[tuple[str, str, str]] = {
        (e.src, e.dst, e.edge_type)
        for e in ctx.edges
        if e.edge_type == "dispatches_to"
    }

    edges: list[Edge] = []
    for class_sym, framework_methods, is_fallback in subclasses:
        for method_name in framework_methods:
            qualified = f"{class_sym.name}.{method_name}"
            target = method_index.get((class_sym.path or "", qualified))
            if target is None:
                continue  # user didn't override this method
            key = (class_sym.id, target.id, "dispatches_to")
            if key in existing_keys:
                continue
            existing_keys.add(key)
            confidence = 0.5 if is_fallback else 0.90
            edge_meta: dict[str, object] = {
                "framework_dispatch": "django_third_party",
            }
            if is_fallback:
                edge_meta["disambiguation_fallback"] = True
            edges.append(
                Edge.create(
                    src=class_sym.id,
                    dst=target.id,
                    edge_type="dispatches_to",
                    line=class_sym.span.start_line if class_sym.span else 0,
                    confidence=confidence,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_call_direct",
                    meta=edge_meta,
                ),
            )

    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(symbols=[], edges=edges, run=run)
