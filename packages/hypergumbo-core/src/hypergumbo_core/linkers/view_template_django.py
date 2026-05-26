# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: Django controller action to view template binding (WI-mifif).

Django apps connect view callables to templates by three routes:

1. **Explicit string in a ``render()`` call.** ``render(request, "users/show.html",
   ctx)`` resolves under ``<app>/templates/users/show.html`` (Django's standard
   ``APP_DIRS`` lookup) or, less commonly, project-level ``templates/users/show.html``.
   The view callable doing the render is the source of the ``renders`` edge.

2. **Class-attribute string on a ``TemplateView``.** ``class HomeView(TemplateView):
   template_name = "home/index.html"`` declares the template path at class scope.
   Source of the edge is the view class itself.

3. **Method/class-name default on a generic CBV.** ``class UserDetailView(DetailView):
   model = User`` derives ``<app>/templates/<app>/user_detail.html`` from the model
   and a per-CBV suffix (``_detail.html``, ``_list.html``, ``_form.html``,
   ``_confirm_delete.html``). Source of the edge is the view class.

Why parse Python source inside the linker
-----------------------------------------
The Python analyzer captures class ``base_classes`` in symbol meta but does not
emit call-site string arguments or class-body attribute values into the IR. To
keep the IR small and avoid coupling analyzer schema to per-framework linker
needs, the Django linker re-parses the view source files it cares about using
the stdlib ``ast`` module. Scan scope is narrowed by the
``_is_django_view_path`` heuristic (paths matching ``views.py``, ``views_*.py``,
or somewhere under a ``views/`` directory), keeping the work bounded.

Why explicit-string and CBV-default are separate strategies
-----------------------------------------------------------
``DjangoExplicitStringStrategy`` produces ``(action, string)`` pairs and lets
the shared core do candidate generation via ``string_to_candidates``. The CBV
default path cannot be expressed as an ``(action, string)`` pair because the
template name is derived from class-name regex matching plus a model attribute
lookup, not from a string literal. ``DjangoCBVDefaultStrategy`` subclasses
``TemplateStrategy`` directly so it can yield emissions whose ``action_symbol``
is the view class and whose detection pattern is ``cbv_default_template``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, Iterator, Optional, Tuple

from ..ir import Symbol
from ._transitive_bases import (
    build_inheritance_index,
    collect_transitive_base_names,
)
from ._view_template_core import (
    ExplicitStringStrategy,
    TemplateCandidate,
    TemplateRenderEmission,
    TemplateStrategy,
    link_via_strategies,
)
from .registry import LinkerActivation, LinkerContext, LinkerResult, register_linker

# CBV bases whose subclasses get model-derived default templates.
_CBV_DEFAULT_SUFFIXES: dict[str, str] = {
    "DetailView": "_detail.html",
    "ListView": "_list.html",
    "CreateView": "_form.html",
    "UpdateView": "_form.html",
    "DeleteView": "_confirm_delete.html",
    "FormView": "_form.html",
}

# Bases recognised as Django CBV roots (transitively walked).
_CBV_TEMPLATE_NAME_BASES = frozenset(
    {
        "TemplateView",
        "View",
        "ListView",
        "DetailView",
        "CreateView",
        "UpdateView",
        "DeleteView",
        "FormView",
        "ArchiveIndexView",
        "YearArchiveView",
        "MonthArchiveView",
        "WeekArchiveView",
        "DayArchiveView",
        "DateDetailView",
        "RedirectView",
    }
)


def _is_django_view_path(path: str) -> bool:
    """Heuristic: does this Python file look like Django view source?

    Matches ``app/views.py``, ``app/views/anything.py``, ``app/views_foo.py``.
    Avoids the false-positive where unrelated utilities call a function
    named ``render``.
    """
    parts = Path(path).parts
    if any(p == "views" for p in parts):
        return True
    name = Path(path).name
    return name == "views.py" or name.startswith("views_")


def _camel_to_snake(name: str) -> str:
    """Inline copy: Django CBV default model names use the same convention.

    Kept independent of the Rails helper so the two linkers can evolve without
    cross-coupling. Identical behavior is verified by the shared
    ``test_view_template_core`` smoke; minor drift would be caught by the
    Django-specific test that exercises this code path.
    """
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper():
            if i > 0:
                prev_lower = name[i - 1].islower()
                next_lower = (i + 1 < len(name)) and name[i + 1].islower()
                if prev_lower or (next_lower and i > 0):
                    out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def _app_dir_path_for_view_file(path: Path) -> Optional[Path]:
    """Map a Django view file path → app directory path, if recognisable.

    ``users/views.py`` → ``users``
    ``users/views/foo.py`` → ``users``
    ``apps/users/views.py`` → ``apps/users``

    Returns ``None`` for ``views.py`` / ``views/foo.py`` at the repo root,
    or for paths that don't look like Django view files.
    """
    parts = path.parts
    if "views" in parts[:-1]:
        idx = parts.index("views")
        if idx == 0:
            return None
        return Path(*parts[:idx])
    name = parts[-1] if parts else ""
    if name == "views.py" or name.startswith("views_"):
        if len(parts) < 2:
            return None
        return Path(*parts[:-1])
    return None  # pragma: no cover — _is_django_view_path gates upstream


def _app_dir_name(path: Path) -> Optional[str]:
    """App directory name (last component) for a Django view file path."""
    app_dir = _app_dir_path_for_view_file(path)
    if app_dir is None:
        return None
    return app_dir.parts[-1]


def _template_candidates_for_string(
    string_value: str, view_path: Path
) -> list[TemplateCandidate]:
    """Generate ordered template candidates for an explicit string path."""
    extension = Path(string_value).suffix
    language = _language_for_extension(extension)
    candidates: list[TemplateCandidate] = []

    # App-scoped: <app>/templates/<string>
    app_dir = _app_dir_path_for_view_file(view_path)
    if app_dir is not None:
        candidates.append(
            TemplateCandidate(
                path=app_dir / "templates" / string_value,
                language=language,
            )
        )

    # Project-scoped: templates/<string>
    candidates.append(
        TemplateCandidate(
            path=Path("templates") / string_value, language=language
        )
    )

    return candidates


def _language_for_extension(extension: str) -> str:
    """Map a template extension to a Symbol language label."""
    mapping = {
        ".html": "html",
        ".jinja": "jinja",
        ".jinja2": "jinja",
        ".j2": "jinja",
        ".djinja": "html",
    }
    return mapping.get(extension, "html")


def _parse_view_file(view_path: Path) -> Optional[ast.Module]:
    """Best-effort parse of a Django views file."""
    try:
        source = view_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _enclosing_symbol_for_lineno(
    lineno: int,
    symbols_in_file: list[Symbol],
) -> Optional[Symbol]:
    """Find the innermost function/method symbol whose span contains ``lineno``."""
    best: Optional[Symbol] = None
    for sym in symbols_in_file:
        if sym.kind not in {"function", "method"}:
            continue
        if sym.span is None:  # pragma: no cover — real symbols always have spans
            continue
        if sym.span.start_line <= lineno <= sym.span.end_line:
            if best is None:
                best = sym
                continue
            assert best.span is not None
            best_span_len = best.span.end_line - best.span.start_line
            this_span_len = sym.span.end_line - sym.span.start_line
            if this_span_len < best_span_len:
                best = sym
    return best


def _class_symbol_for_lineno(
    lineno: int,
    symbols_in_file: list[Symbol],
) -> Optional[Symbol]:
    """Find a class symbol whose span contains ``lineno``."""
    for sym in symbols_in_file:
        if sym.kind != "class" or sym.span is None:
            continue
        if sym.span.start_line <= lineno <= sym.span.end_line:
            return sym
    return None


class DjangoExplicitStringStrategy(ExplicitStringStrategy):
    """``render(request, "...")`` and class-attribute ``template_name = "..."``."""

    def find_string_sites(
        self, ctx: LinkerContext
    ) -> Iterator[Tuple[Symbol, str, int, str]]:
        # Group Django-view-looking symbols by file path so we parse each
        # file once.
        by_path: dict[str, list[Symbol]] = {}
        for sym in ctx.symbols:
            if sym.language != "python":
                continue
            if not _is_django_view_path(sym.path):
                continue
            by_path.setdefault(sym.path, []).append(sym)

        for path_str, syms_in_file in by_path.items():
            view_path = ctx.repo_root / path_str
            tree = _parse_view_file(view_path)
            if tree is None:
                continue
            yield from self._render_call_sites(tree, syms_in_file)
            yield from self._template_name_sites(tree, syms_in_file)

    def _render_call_sites(
        self, tree: ast.Module, symbols_in_file: list[Symbol]
    ) -> Iterator[Tuple[Symbol, str, int, str]]:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "render":
                pass
            elif (
                isinstance(func, ast.Attribute)
                and func.attr == "render"
            ):
                pass
            else:
                continue
            if len(node.args) < 2:
                continue
            tpl_arg = node.args[1]
            if not (isinstance(tpl_arg, ast.Constant) and isinstance(tpl_arg.value, str)):
                continue
            enclosing = _enclosing_symbol_for_lineno(node.lineno, symbols_in_file)
            if enclosing is None:
                continue
            yield enclosing, tpl_arg.value, node.lineno, "render_call"

    def _template_name_sites(
        self, tree: ast.Module, symbols_in_file: list[Symbol]
    ) -> Iterator[Tuple[Symbol, str, int, str]]:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign):
                    continue
                if len(stmt.targets) != 1:
                    continue
                target = stmt.targets[0]
                if not isinstance(target, ast.Name):
                    continue
                if target.id != "template_name":
                    continue
                if not (
                    isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    continue
                class_sym = _class_symbol_for_lineno(node.lineno, symbols_in_file)
                if class_sym is None:
                    continue
                yield class_sym, stmt.value.value, stmt.lineno, "template_name_attribute"

    def string_to_candidates(
        self, string_value: str, action_symbol: Symbol, ctx: LinkerContext
    ) -> Iterable[TemplateCandidate]:
        return _template_candidates_for_string(
            string_value, Path(action_symbol.path)
        )


class DjangoCBVDefaultStrategy(TemplateStrategy):
    """DetailView / ListView / CreateView / UpdateView / DeleteView defaults."""

    def find_emissions(
        self, ctx: LinkerContext
    ) -> Iterator[TemplateRenderEmission]:
        inheritance_index = build_inheritance_index(ctx.edges)
        symbol_by_id = {sym.id: sym for sym in ctx.symbols}

        for sym in ctx.symbols:
            if sym.kind != "class" or sym.language != "python":
                continue
            chain = collect_transitive_base_names(
                sym, symbol_by_id, inheritance_index
            )
            matching_cbv = next(
                (b for b in chain if b in _CBV_DEFAULT_SUFFIXES), None
            )
            if matching_cbv is None:
                continue

            # Need to read source to find ``model = <Name>``.
            view_path = ctx.repo_root / sym.path
            tree = _parse_view_file(view_path)
            if tree is None:
                continue

            model_name = _find_model_attr(tree, sym.name)
            if model_name is None:
                continue

            suffix = _CBV_DEFAULT_SUFFIXES[matching_cbv]
            template_name = f"{_camel_to_snake(model_name)}{suffix}"
            app_name = _app_dir_name(Path(sym.path)) or ""
            template_path = (
                f"{app_name}/{template_name}" if app_name else template_name
            )
            candidates = tuple(
                _template_candidates_for_string(
                    template_path, Path(sym.path)
                )
            )

            yield TemplateRenderEmission(
                action_symbol_id=sym.id,
                line=sym.span.start_line if sym.span else 0,
                detection_pattern="cbv_default_template",
                candidates=candidates,
            )


def _find_model_attr(tree: ast.Module, class_name: str) -> Optional[str]:
    """Return the ``model = <Name>`` attribute's RHS class-name, if present."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name != class_name:
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            if len(stmt.targets) != 1:
                continue
            target = stmt.targets[0]
            if not isinstance(target, ast.Name) or target.id != "model":
                continue
            if isinstance(stmt.value, ast.Name):
                return stmt.value.id
    return None


@register_linker(
    "view_template_django",
    priority=66,  # After Rails view_template (65), shares the same edge type.
    description="Django view function / CBV → template binding",
    activation=LinkerActivation(frameworks=["django"]),
    # CNF: Django is Python-only.
    depends_on=[["python"]],
)
def link_view_template_django(ctx: LinkerContext) -> LinkerResult:
    """Linker entry point for registry."""
    return link_via_strategies(
        ctx,
        [
            DjangoExplicitStringStrategy(),
            DjangoCBVDefaultStrategy(),
        ],
    )


__all__ = [
    "DjangoCBVDefaultStrategy",
    "DjangoExplicitStringStrategy",
    "link_view_template_django",
]
