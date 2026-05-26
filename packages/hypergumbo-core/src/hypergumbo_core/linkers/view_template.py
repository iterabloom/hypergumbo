# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: Rails controller action to view template binding.

Rails controllers render view templates by convention: ``UsersController#show``
renders ``app/views/users/show.html.erb``. Without explicit ``render`` calls,
the template is resolved by controller name + action name + file system probing.

How It Works
------------
The Rails behavior is now implemented as a thin :class:`RailsStrategy`
subclass of :class:`~._view_template_core.MethodNameStrategy`. The shared
core (see ``_view_template_core.py``) handles the filesystem probe loop,
``renders`` edge emission, and template-Symbol deduplication. The Rails
strategy provides:

* a controller-class predicate (transitive base walk for
  ``ApplicationController`` / ``ActionController::Base`` /
  ``ActionController::API`` per WI-vigih),
* an action-method predicate (skip ``initialize`` and ``_``-prefixed helpers,
  skip class methods using ``.`` instead of ``#``),
* a class-name → view-directory mapping (``Admin::UsersController`` →
  ``admin/users`` per WI-votut's CSV-export edge cases and the existing
  CamelCase-to-snake_case rules).

Why This Matters
----------------
In Rails apps, the controller → template link is implicit. Without this
linker, the behavior map shows routes → controllers but stops there.
Templates are disconnected islands. This linker completes the
route → controller → template navigation path that developers mentally
traverse when working in Rails.

Sister Frameworks
-----------------
* Django (WI-mifif): ``view_template_django.py``
* Phoenix (WI-dajom), Spring MVC (WI-hogik), Laravel Blade (WI-hokaj):
  follow-up PRs against the same shared core.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ..ir import Edge, Symbol
from ._transitive_bases import (
    build_inheritance_index,
    collect_transitive_base_names,
)
from ._view_template_core import (
    PASS_ID,
    MethodNameStrategy,
    TemplateCandidate,
    link_via_strategies,
)
from .registry import LinkerActivation, LinkerContext, LinkerResult, register_linker

# Base classes that indicate a Rails controller
_CONTROLLER_BASES = frozenset({
    "ApplicationController",
    "ActionController::Base",
    "ActionController::API",
})

# Template file extensions to probe, in priority order.
# WI-votut: ``.csv.erb`` is a non-default but legitimate Rails template
# format used by CSV-export endpoints (e.g. chatwoot
# Api::V2::Accounts::ReportsController#inboxes). Recognized here so the
# 11 such actions in chatwoot get the ``renders`` edge they should.
_EXTENSION_LANGUAGE: dict[str, str] = {
    ".html.erb": "erb",
    ".html.haml": "haml",
    ".html.slim": "slim",
    ".text.erb": "erb",
    ".text.haml": "haml",
    ".csv.erb": "erb",
    ".json.jbuilder": "ruby",
}

# Methods that are not controller actions
_SKIP_PREFIXES = ("_",)


def _controller_to_view_dir(class_name: str) -> str:
    """Convert controller class name to view directory path.

    Inverse of ``_normalize_rails_controller()`` in route_handler.py.

    Examples::

        UsersController → users
        Admin::UsersController → admin/users
        Api::V1::AccountsController → api/v1/accounts

    Args:
        class_name: The controller class name (e.g., ``UsersController``)

    Returns:
        Relative view directory path (e.g., ``users``)
    """
    # Split on :: for namespaced controllers
    parts = class_name.split("::")

    result_parts = []
    for part in parts:
        # Remove "Controller" suffix from last part
        if part.endswith("Controller"):
            part = part[: -len("Controller")]

        # Convert CamelCase to snake_case
        result_parts.append(_camel_to_snake(part))

    return "/".join(result_parts)


def _camel_to_snake(name: str) -> str:
    """Convert CamelCase to snake_case.

    Examples::

        Users → users
        IPPoolRules → ip_pool_rules
        AdminUsers → admin_users
    """
    result: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper():
            if i > 0:
                # Insert underscore before uppercase that follows lowercase
                # or before uppercase that starts a new word (e.g., "IP" in "IPPool")
                prev_lower = name[i - 1].islower()
                next_lower = (i + 1 < len(name)) and name[i + 1].islower()
                if prev_lower or (next_lower and i > 0):
                    result.append("_")
            result.append(ch.lower())
        else:
            result.append(ch)
    return "".join(result)


def _is_action_method(method_name: str) -> bool:
    """Check if a method name represents a controller action.

    Skips initializers, private helpers (prefixed with _), and class methods
    (using . separator instead of #).

    Args:
        method_name: The method portion of the symbol name (after #)

    Returns:
        True if the method could be a controller action
    """
    if method_name == "initialize":
        return False

    for prefix in _SKIP_PREFIXES:
        if method_name.startswith(prefix):
            return False

    return True


def _probe_template_files(
    repo_root: Path, view_dir: str, action: str
) -> list[Path]:
    """Probe file system for template files matching an action.

    Retained for the diagnostic ``_count_controller_methods`` companion and
    for backwards compatibility with downstream callers. The main linker
    path now flows through the shared core.

    Args:
        repo_root: Repository root path
        view_dir: Relative view directory (e.g., ``users``)
        action: Action name (e.g., ``index``)

    Returns:
        List of matching template file paths (relative to repo_root)
    """
    base_dir = repo_root / "app" / "views" / view_dir
    matches: list[Path] = []

    for ext in _EXTENSION_LANGUAGE:
        candidate = base_dir / f"{action}{ext}"
        if candidate.exists():
            matches.append(candidate.relative_to(repo_root))

    return matches


class RailsStrategy(MethodNameStrategy):
    """Rails ``UsersController#show`` → ``app/views/users/show.html.erb``.

    Walks transitive base classes (WI-vigih) so app-defined intermediate
    controllers (the dominant Rails pattern) participate. Class methods
    using ``.`` separator are skipped at the symbol-name parsing level by
    the shared core's ``"#" not in sym.name`` guard.
    """

    def is_action_class(self, sym: Symbol, ctx: LinkerContext) -> bool:
        if sym.kind != "class":
            return False
        inheritance_index = build_inheritance_index(ctx.edges)
        symbol_by_id = {s.id: s for s in ctx.symbols}
        chain = collect_transitive_base_names(
            sym, symbol_by_id, inheritance_index
        )
        for base in chain:
            if base in _CONTROLLER_BASES:
                return True
        return False

    def is_action_method(self, method_name: str) -> bool:
        return _is_action_method(method_name)

    def candidates_for(
        self, class_name: str, method: Symbol, ctx: LinkerContext
    ) -> Iterable[TemplateCandidate]:
        view_dir = _controller_to_view_dir(class_name)
        action = method.name.rsplit("#", 1)[1]
        candidates: list[TemplateCandidate] = []
        for ext, lang in _EXTENSION_LANGUAGE.items():
            rel = Path("app") / "views" / view_dir / f"{action}{ext}"
            candidates.append(TemplateCandidate(path=rel, language=lang))
        return candidates


def link_view_templates(
    repo_root: Path,
    symbols: list[Symbol],
    edges: list[Edge],
) -> LinkerResult:
    """Link Rails controller action methods to their convention-based templates.

    Thin wrapper around :func:`_view_template_core.link_via_strategies` that
    constructs a one-strategy run for the Rails framework. Kept for tests
    and downstream callers that wired against the original module-level
    function.
    """
    ctx = LinkerContext(repo_root=repo_root, symbols=symbols, edges=edges)
    return link_via_strategies(ctx, [RailsStrategy()])


def _count_controller_methods(ctx: LinkerContext) -> int:
    """Count methods belonging to controller classes (transitive base walk per WI-vigih)."""
    inheritance_index = build_inheritance_index(ctx.edges)
    symbol_by_id = {sym.id: sym for sym in ctx.symbols}
    controller_classes: set[str] = set()
    for sym in ctx.symbols:
        if sym.kind != "class":
            continue
        chain = collect_transitive_base_names(sym, symbol_by_id, inheritance_index)
        for base in chain:
            if base in _CONTROLLER_BASES:
                controller_classes.add(sym.name)
                break

    count = 0
    for sym in ctx.symbols:
        if sym.kind != "method" or "#" not in sym.name:
            continue
        class_part = sym.name.rsplit("#", 1)[0]
        if class_part in controller_classes:
            count += 1
    return count


@register_linker(
    "view_template",
    priority=65,  # After route_handler (60)
    description="Rails controller action to view template binding",
    activation=LinkerActivation(frameworks=["rails"]),
    # CNF: Rails is Ruby-only.
    depends_on=[["ruby"]],
)
def link_view_template(ctx: LinkerContext) -> LinkerResult:
    """Linker entry point for registry."""
    return link_via_strategies(ctx, [RailsStrategy()])


__all__ = [
    "PASS_ID",
    "RailsStrategy",
    "_camel_to_snake",
    "_controller_to_view_dir",
    "_count_controller_methods",
    "_is_action_method",
    "_probe_template_files",
    "link_view_template",
    "link_view_templates",
]
