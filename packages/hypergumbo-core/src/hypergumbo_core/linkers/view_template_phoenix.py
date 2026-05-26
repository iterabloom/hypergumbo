# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: Phoenix controller action to view template (WI-dajom).

Phoenix binds controller actions to templates by convention:

* **Phoenix 1.x** stores templates under
  ``lib/<app_web>/templates/<resource>/<action>.html.<ext>``. The controller
  module ``MyAppWeb.UserController`` resolves to ``lib/my_app_web/templates/user/``;
  each action function ``def show(conn, params)`` resolves to ``show.html.eex``
  (or ``.heex`` / ``.leex`` / ``.text.eex`` / ``.json.eex`` / ``.xml.eex``).

* **Phoenix 1.7+** co-locates templates with the controller under
  ``lib/<app_web>/controllers/<resource>_html/<action>.html.heex``. The parallel
  HTML helper module ``MyAppWeb.UserHTML`` (function-component shape) shares the
  same template directory, so ``MyAppWeb.UserHTML.show`` and
  ``MyAppWeb.UserController.show`` both bind to
  ``lib/my_app_web/controllers/user_html/show.html.heex``.

Out of scope: Phoenix LiveView (``*_live.ex``). LiveView modules embed their
templates inline; a separate linker (file one if the corpus demands it) would
be needed for the inline shape.

The Elixir analyzer emits modules with ``kind="module"`` and full dotted names
(``MyAppWeb.UserController``), and functions with ``kind="function"`` and names
like ``MyAppWeb.UserController.show``. Container/action extraction splits on
the final ``.``; the analyzer's ``modifiers=["private"]`` marks ``defp``
functions, which cannot be render targets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Tuple

from ..ir import Symbol
from ._view_template_core import (
    MethodNameStrategy,
    TemplateCandidate,
    link_via_strategies,
)
from .registry import LinkerActivation, LinkerContext, LinkerResult, register_linker

# Extensions to probe, in priority order. Maps each extension to the language
# label for the synthesized template Symbol.
_EXTENSION_LANGUAGE: dict[str, str] = {
    ".html.eex": "eex",
    ".html.heex": "heex",
    ".html.leex": "leex",
    ".text.eex": "eex",
    ".json.eex": "eex",
    ".xml.eex": "eex",
}

# Function names that exist on Phoenix controllers but are not action targets:
#
# * ``init`` / ``call`` — Plug protocol callbacks injected by ``use
#   Phoenix.Controller``; not user-facing routes.
# * ``action`` — Phoenix's per-request action dispatcher (override hook); not
#   itself a view-rendering action.
_NON_ACTION_FUNCTION_NAMES = frozenset({"init", "call", "action"})


def _camel_to_snake(name: str) -> str:
    """Convert ``CamelCase`` to ``snake_case`` for Elixir module segments.

    Examples::

        MyAppWeb → my_app_web
        UserController → user_controller
        IPPoolRules → ip_pool_rules
    """
    result: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper():
            if i > 0:
                prev_lower = name[i - 1].islower()
                next_lower = (i + 1 < len(name)) and name[i + 1].islower()
                if prev_lower or (next_lower and i > 0):
                    result.append("_")
            result.append(ch.lower())
        else:
            result.append(ch)
    return "".join(result)


def _template_dirs_for_module(module_name: str) -> list[Path]:
    """Yield candidate template directories for a Phoenix controller module.

    ``MyAppWeb.UserController`` → [
        ``lib/my_app_web/templates/user``,                # Phoenix 1.x
        ``lib/my_app_web/controllers/user_html``,         # Phoenix 1.7+
    ]

    ``MyAppWeb.UserHTML`` (function-component shape) → [
        ``lib/my_app_web/controllers/user_html``,
    ]

    ``MyAppWeb.Admin.UserController`` → [
        ``lib/my_app_web/templates/admin/user``,
        ``lib/my_app_web/controllers/admin/user_html``,
    ]
    """
    parts = module_name.split(".")
    if len(parts) < 2:
        return []  # pragma: no cover — is_action_class gates on suffix presence
    web_namespace = _camel_to_snake(parts[0])
    middle_segments = [_camel_to_snake(p) for p in parts[1:-1]]
    last = parts[-1]
    if last.endswith("Controller"):
        resource = _camel_to_snake(last[: -len("Controller")])
        return [
            Path("lib", web_namespace, "templates", *middle_segments, resource),
            Path(
                "lib",
                web_namespace,
                "controllers",
                *middle_segments,
                f"{resource}_html",
            ),
        ]
    if last.endswith("HTML"):
        resource = _camel_to_snake(last[: -len("HTML")])
        return [
            Path(
                "lib",
                web_namespace,
                "controllers",
                *middle_segments,
                f"{resource}_html",
            ),
        ]
    return []  # pragma: no cover — is_action_class gates on suffix presence


class PhoenixStrategy(MethodNameStrategy):
    """Phoenix controller actions → template files via class+action convention.

    Method-name strategy: container is the Elixir module symbol (``kind="module"``,
    name like ``MyAppWeb.UserController``); action is the function symbol
    (``kind="function"``, name like ``MyAppWeb.UserController.show``). Templates
    are probed under both Phoenix 1.x and 1.7+ directory layouts.
    """

    def is_action_class(self, sym: Symbol, ctx: LinkerContext) -> bool:
        if sym.kind != "module":
            return False
        # Controllers (1.x + 1.7+) and HTML helper modules (1.7+ function
        # components) both have associated templates.
        return sym.name.endswith("Controller") or sym.name.endswith("HTML")

    def is_action_method(self, method_name: str) -> bool:
        if method_name.startswith("_"):
            return False
        if method_name in _NON_ACTION_FUNCTION_NAMES:
            return False
        return True

    def extract_class_method(
        self, sym: Symbol
    ) -> Optional[Tuple[str, str]]:
        if sym.kind != "function":
            return None
        if "private" in (sym.modifiers or []):
            return None
        if "." not in sym.name:
            return None  # pragma: no cover — analyzer always emits qualified names
        container, short = sym.name.rsplit(".", 1)
        return container, short

    def candidates_for(
        self, class_name: str, method: Symbol, ctx: LinkerContext
    ) -> Iterable[TemplateCandidate]:
        action = method.name.rsplit(".", 1)[1]
        candidates: list[TemplateCandidate] = []
        for template_dir in _template_dirs_for_module(class_name):
            for ext, lang in _EXTENSION_LANGUAGE.items():
                candidates.append(
                    TemplateCandidate(
                        path=template_dir / f"{action}{ext}",
                        language=lang,
                    )
                )
        return candidates


@register_linker(
    "view_template_phoenix",
    priority=67,  # After Rails (65) and Django (66); shares the renders edge type.
    description="Phoenix controller action → template binding",
    activation=LinkerActivation(frameworks=["phoenix"]),
    # CNF: Phoenix is Elixir-only.
    depends_on=[["elixir"]],
)
def link_view_template_phoenix(ctx: LinkerContext) -> LinkerResult:
    """Linker entry point for registry."""
    return link_via_strategies(ctx, [PhoenixStrategy()])


__all__ = ["PhoenixStrategy", "link_view_template_phoenix"]
