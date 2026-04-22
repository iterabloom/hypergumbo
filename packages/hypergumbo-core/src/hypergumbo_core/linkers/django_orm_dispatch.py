# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: Django ORM method dispatch (WI-nosug).

How It Works
------------
Django's ORM, admin, and form machinery dispatch a fixed set of lifecycle
and hook methods on user-defined ``Model`` / ``Manager`` / ``QuerySet``
subclasses at runtime — ``save``, ``delete``, ``clean``, ``get_queryset``,
``__str__``, and the rest. The static call graph never sees those calls
because they happen through ``MyModel.objects.filter(...)`` /
``instance.save()`` / admin-site reflection, so every override the user
writes looks like a dead function.

This linker scans symbols for classes that extend any Django base class
named in ``DJANGO_BASE_METHODS`` and emits ``dispatches_to`` edges from
each subclass to each of its framework-called override methods. With the
class reachable from its enclosing module, the override methods are
pulled out of the dead-code set.

Why a Framework Linker (Not Per-Analyzer Logic)
------------------------------------------------
Identical reasoning to the Airflow framework-dispatch linker (WI-nutav):
the inheritance detection is already handled by the ``inheritance``
linker's ``base_classes`` metadata; the Django-specific knowledge is a
single per-base method map that belongs in one place, not smeared across
the Python analyzer. Extending to other Python ORM frameworks (e.g.,
SQLAlchemy declarative bases, Peewee models) is a new entry in the map,
not per-analyzer code.

Scope (WI-nosug)
----------------
Python-internal. The signal this linker addresses comes from WI-tubot's
aggregate-v5 prospector run (2026-04-11), which pinned
``python_orm_dispatch`` at 2441 dead-code-maybe candidates. Manager /
QuerySet subclasses, Admin subclasses, and Model subclasses with
user-defined overrides of framework-called methods all fall into that
bucket.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..ir import PASS_VERSION, AnalysisRun, Edge, make_pass_id
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    from ..ir import Symbol

PASS_ID = make_pass_id("django-orm-dispatch-linker")

# Map of Django base class name -> framework-called method names on that
# base. A subclass of ``Model`` only has Model-lifecycle methods called on
# it; Manager / QuerySet territory is separate. Keeping the mapping
# per-base avoids spurious edges like ``Model.get_queryset`` (which is a
# Manager-only method) dispatching on a pure Model subclass.
DJANGO_BASE_METHODS: dict[str, frozenset[str]] = {
    # ORM Model hierarchy.
    "Model": frozenset({
        "save", "save_base", "delete",
        "clean", "clean_fields", "full_clean",
        "validate_unique", "validate_constraints",
        "get_absolute_url", "get_admin_url",
        "__str__", "__repr__", "__eq__", "__hash__",
        "refresh_from_db", "get_deferred_fields",
        "serializable_value",
        "pre_save", "post_save", "pre_delete", "post_delete",
    }),
    # Abstract user bases reuse the Model lifecycle AND add auth hooks.
    "AbstractUser": frozenset({
        "save", "save_base", "delete",
        "clean", "clean_fields", "full_clean",
        "validate_unique", "validate_constraints",
        "get_absolute_url", "__str__", "__repr__",
        "get_full_name", "get_short_name", "email_user",
        "check_password", "set_password",
    }),
    "AbstractBaseUser": frozenset({
        "save", "save_base", "delete",
        "__str__", "__repr__",
        "get_username", "natural_key",
        "check_password", "set_password",
        "get_session_auth_hash",
    }),
    # Manager / QuerySet: custom managers override get_queryset / as_manager.
    "Manager": frozenset({
        "get_queryset", "contribute_to_class",
        "all", "filter", "exclude", "get",
        "create", "bulk_create", "update_or_create", "get_or_create",
        "none", "using",
        "check",
    }),
    "QuerySet": frozenset({
        "__iter__", "__len__", "__getitem__", "__bool__",
        "all", "filter", "exclude", "get",
        "annotate", "aggregate", "values", "values_list",
        "order_by", "reverse", "distinct",
        "select_related", "prefetch_related",
        "iterator", "first", "last", "exists", "count",
        "create", "bulk_create", "bulk_update",
        "delete", "update", "as_manager",
    }),
    # Admin: ModelAdmin subclasses override display / form / action hooks.
    "ModelAdmin": frozenset({
        "get_queryset", "get_list_display", "get_list_filter",
        "get_search_fields", "get_readonly_fields", "get_fieldsets",
        "get_inline_instances", "get_form", "get_formsets_with_inlines",
        "save_model", "delete_model", "delete_queryset",
        "save_formset", "save_related",
        "get_urls", "response_add", "response_change", "response_delete",
        "has_add_permission", "has_change_permission",
        "has_delete_permission", "has_view_permission",
        "get_actions", "log_addition", "log_change", "log_deletion",
        "formfield_for_foreignkey", "formfield_for_manytomany",
        "formfield_for_choice_field",
    }),
    # Form / ModelForm: validated via full_clean at form.is_valid() time.
    "ModelForm": frozenset({
        "clean", "full_clean", "save",
        "is_valid", "has_changed",
        "_post_clean", "_clean_form", "_clean_fields",
    }),
    "Form": frozenset({
        "clean", "full_clean", "is_valid", "has_changed",
        "_post_clean", "_clean_form", "_clean_fields",
    }),
    # View hierarchy — HTTP methods dispatched by the view's dispatch().
    "View": frozenset({
        "dispatch", "http_method_not_allowed", "options",
        "get", "post", "put", "patch", "delete", "head", "trace",
        "setup",
    }),
    "TemplateView": frozenset({
        "get", "post", "get_context_data", "get_template_names",
        "render_to_response",
    }),
    "ListView": frozenset({
        "get", "get_queryset", "get_context_data",
        "get_paginate_by", "get_ordering",
    }),
    "DetailView": frozenset({
        "get", "get_object", "get_context_data",
    }),
    "CreateView": frozenset({
        "get", "post", "form_valid", "form_invalid",
        "get_form_class", "get_form_kwargs", "get_success_url",
        "get_initial", "get_context_data",
    }),
    "UpdateView": frozenset({
        "get", "post", "form_valid", "form_invalid",
        "get_form_class", "get_object", "get_success_url",
        "get_initial", "get_context_data",
    }),
    "DeleteView": frozenset({
        "get", "post", "delete", "get_object", "get_success_url",
    }),
    # Migration Operation subclasses — called by the migration runner.
    "Migration": frozenset({
        "apply", "unapply",
    }),
}


def _short_base_name(raw: str) -> str:
    """Strip qualifiers/generics so ``django.db.models.Model`` matches ``Model``."""
    name = raw.split("[")[0].split("<")[0].strip()
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    return name


def _find_django_subclasses(
    symbols: list["Symbol"],
) -> list[tuple["Symbol", frozenset[str]]]:
    """Return (class_symbol, framework_method_names) for every Django subclass.

    A class whose ``base_classes`` metadata names more than one Django
    base (e.g., a CBV that inherits ``ListView`` and ``LoginRequiredMixin``
    where the mixin resolves to ``View``) has the union of both bases'
    method sets — every framework-called method on any matched base is
    in scope.
    """
    results: list[tuple[Symbol, frozenset[str]]] = []
    for sym in symbols:
        if sym.kind not in ("class", "struct"):
            continue
        if sym.language != "python":
            continue
        base_classes = sym.meta.get("base_classes", []) if sym.meta else []
        if not base_classes:
            continue
        methods: set[str] = set()
        for raw in base_classes:
            if not isinstance(raw, str):
                continue
            short = _short_base_name(raw)
            if short in DJANGO_BASE_METHODS:
                methods.update(DJANGO_BASE_METHODS[short])
        if methods:
            results.append((sym, frozenset(methods)))
    return results


def _build_method_index(
    symbols: list["Symbol"],
) -> dict[tuple[str, str], "Symbol"]:
    """Index Python methods by ``(file_path, qualified_name)``.

    The Python analyzer emits method symbols with ``name`` set to
    ``"ClassName.method_name"``. Keying by ``(path, qualified_name)``
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
    "django-orm-dispatch",
    priority=22,
    description="Emit dispatches_to edges from Django Model/Manager/View/Form/Admin subclasses to their framework-called override methods (WI-nosug)",
)
def link_django_orm_dispatch(ctx: LinkerContext) -> LinkerResult:
    """Create dispatches_to edges from Django subclasses to their framework overrides.

    For each class ``C`` whose ``base_classes`` metadata names a Django
    base in ``DJANGO_BASE_METHODS``, walk the symbol table for methods
    named ``"{C.name}.{method}"`` where ``method`` is a framework-called
    name for that base, and emit a ``dispatches_to`` edge from ``C`` to
    each matching method. The edge makes the method reachable from
    dead-code analysis without requiring the Django runtime to be modeled.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    subclasses = _find_django_subclasses(ctx.symbols)
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
                    evidence_type="django_orm_dispatch",
                ),
            )

    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(symbols=[], edges=edges, run=run)
