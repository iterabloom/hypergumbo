# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: view template for connecting controller actions to rendered templates.

Rails controllers render view templates by convention: ``UsersController#show``
renders ``app/views/users/show.html.erb``. Without explicit ``render`` calls,
the template is resolved by controller name + action name + file system probing.

How It Works
------------
1. Collect controller classes by checking ``base_classes`` metadata against
   known controller base classes (``ApplicationController``,
   ``ActionController::Base``, ``ActionController::API``)
2. Find methods belonging to those classes via ``ControllerClass#method`` naming
3. Derive template directory: ``UsersController`` → ``app/views/users/``,
   ``Admin::UsersController`` → ``app/views/admin/users/``
4. Probe file system for template files matching action name with known
   extensions (``.html.erb``, ``.html.haml``, ``.html.slim``, etc.)
5. Create ``renders`` edges from controller method → template file symbol
6. Create template file symbols (kind="template") for matched files

Why This Matters
----------------
In Rails apps, the controller → template link is implicit. Without this linker,
the behavior map shows routes → controllers but stops there. Templates are
disconnected islands. This linker completes the route → controller → template
navigation path that developers mentally traverse when working in Rails.

Supported Frameworks
--------------------
Currently: Rails (Ruby). Future: Django, Laravel, Phoenix, Spring.
"""

from __future__ import annotations

from pathlib import Path

from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from .registry import LinkerActivation, LinkerContext, LinkerResult, register_linker

PASS_ID = make_pass_id("view-template-linker")

# Base classes that indicate a Rails controller
_CONTROLLER_BASES = frozenset({
    "ApplicationController",
    "ActionController::Base",
    "ActionController::API",
})

# Template file extensions to probe, in priority order
_TEMPLATE_EXTENSIONS = [
    ".html.erb",
    ".html.haml",
    ".html.slim",
    ".text.erb",
    ".text.haml",
    ".json.jbuilder",
]

# Language mapping for template extensions
_EXTENSION_LANGUAGE = {
    ".html.erb": "erb",
    ".html.haml": "haml",
    ".html.slim": "slim",
    ".text.erb": "erb",
    ".text.haml": "haml",
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


def _probe_template_files(
    repo_root: Path, view_dir: str, action: str
) -> list[Path]:
    """Probe file system for template files matching an action.

    Args:
        repo_root: Repository root path
        view_dir: Relative view directory (e.g., ``users``)
        action: Action name (e.g., ``index``)

    Returns:
        List of matching template file paths (relative to repo_root)
    """
    base_dir = repo_root / "app" / "views" / view_dir
    matches: list[Path] = []

    for ext in _TEMPLATE_EXTENSIONS:
        candidate = base_dir / f"{action}{ext}"
        if candidate.exists():
            matches.append(candidate.relative_to(repo_root))

    return matches


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


def link_view_templates(
    repo_root: Path,
    symbols: list[Symbol],
    edges: list[Edge],
) -> LinkerResult:
    """Link controller action methods to their convention-based view templates.

    Args:
        repo_root: Repository root path for file system probing
        symbols: All symbols from all analyzers
        edges: Existing edges (not modified)

    Returns:
        LinkerResult with new renders edges and template symbols
    """
    # Step 1: Find controller classes
    controller_classes: set[str] = set()
    for sym in symbols:
        if sym.kind != "class":
            continue
        base_classes = (sym.meta or {}).get("base_classes", [])
        for base in base_classes:
            if base in _CONTROLLER_BASES:
                controller_classes.add(sym.name)
                break

    if not controller_classes:
        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
        return LinkerResult(symbols=[], edges=[], run=run)

    # Step 2: Find methods belonging to controller classes
    # Convention: symbol name is "ControllerClass#method"
    new_edges: list[Edge] = []
    new_symbols: list[Symbol] = []
    seen_templates: set[str] = set()  # Avoid duplicate template symbols

    for sym in symbols:
        if sym.kind != "method":
            continue

        # Check if this method belongs to a controller class
        # Symbol names follow "ClassName#method_name" convention
        if "#" not in sym.name:
            continue

        class_part, method_part = sym.name.rsplit("#", 1)
        if class_part not in controller_classes:
            continue

        if not _is_action_method(method_part):
            continue

        # Step 3: Derive template directory from controller class name
        view_dir = _controller_to_view_dir(class_part)

        # Step 4: Probe file system for template files
        template_files = _probe_template_files(repo_root, view_dir, method_part)

        for template_path in template_files:
            # Determine language from extension
            ext_key = ""
            for ext in _TEMPLATE_EXTENSIONS:
                if str(template_path).endswith(ext):
                    ext_key = ext
                    break

            lang = _EXTENSION_LANGUAGE.get(ext_key, "erb")
            filename = template_path.name

            # Create template symbol if not already created
            template_id = f"{lang}:{template_path}:1-1:{filename}:template"
            if template_id not in seen_templates:
                seen_templates.add(template_id)
                template_sym = Symbol(
                    id=template_id,
                    name=filename,
                    kind="template",
                    language=lang,
                    path=str(template_path),
                    span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
                    origin=PASS_ID,
                )
                new_symbols.append(template_sym)

            # Create renders edge from controller method to template
            edge = Edge.create(
                src=sym.id,
                dst=template_id,
                edge_type="renders",
                line=sym.span.start_line if sym.span else 0,
                origin=PASS_ID,
                evidence_type="implicit_convention",
                confidence=0.85,
            )
            new_edges.append(edge)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    return LinkerResult(
        symbols=new_symbols,
        edges=new_edges,
        run=run,
    )


def _count_controller_methods(ctx: LinkerContext) -> int:
    """Count methods belonging to controller classes."""
    controller_classes: set[str] = set()
    for sym in ctx.symbols:
        if sym.kind != "class":
            continue
        base_classes = (sym.meta or {}).get("base_classes", [])
        for base in base_classes:
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
)
def link_view_template(ctx: LinkerContext) -> LinkerResult:
    """Linker entry point for registry."""
    return link_view_templates(ctx.repo_root, ctx.symbols, ctx.edges)
