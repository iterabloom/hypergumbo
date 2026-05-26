# SPDX-License-Identifier: AGPL-3.0-or-later
r"""Framework linker: Laravel Blade view-helper → template binding (WI-hokaj).

Laravel controllers render templates via the ``view()`` helper or the
``View::make()`` facade. The string argument is a dotted view name; Laravel
maps dots to directory separators under ``resources/views/``::

    view('users.show', $data)        → resources/views/users/show.blade.php
    View::make('users.show', $data)  → resources/views/users/show.blade.php

The PHP analyzer captures class ``base_classes`` in symbol meta but does not
emit call-site string arguments. To recover view names the linker re-parses
controller source files with tree-sitter PHP, walking ``function_call_expression``
and ``scoped_call_expression`` nodes.

Action gating
-------------
* The class file path lives under ``app/Http/Controllers/`` (Laravel's
  conventional directory). Off-path classes are skipped even if they
  extend ``Controller`` — too ambiguous on the PHP corpus to be safe.
* The class transitively extends ``Controller`` (covers
  ``Illuminate\Routing\Controller`` and the app's
  ``App\Http\Controllers\Controller`` shorthand, plus deeper chains).

Why ExplicitStringStrategy
--------------------------
Laravel view names are literal strings the developer types, not naming
conventions. Pairs with Spring (WI-hogik), the other ExplicitStringStrategy
consumer; the ``string_to_candidates`` hook is what differs between the
two — Laravel maps dots to slashes and probes one root with two extensions,
Spring probes multiple roots with multiple extensions per root.
"""

from __future__ import annotations

from functools import lru_cache
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
    link_via_strategies,
)
from .registry import LinkerActivation, LinkerContext, LinkerResult, register_linker

# Bases that mark a class as a Laravel controller (transitive walk).
_CONTROLLER_BASES = frozenset(
    {
        "Controller",
        "App\\Http\\Controllers\\Controller",
        "Illuminate\\Routing\\Controller",
    }
)

# Path segments that mark a class file as a Laravel controller. The full
# directory ``app/Http/Controllers`` may appear with mixed separators; matching
# on the joined ``parts`` covers both.
_CONTROLLER_DIR_MARKER = ("app", "Http", "Controllers")

# Template extension → language label.
_TEMPLATE_EXTENSIONS: tuple[tuple[str, str], ...] = (
    (".blade.php", "blade"),
    (".php", "php"),
)


@lru_cache(maxsize=64)
def _get_php_parser():
    try:  # pragma: no cover - exercised only when tree-sitter is installed
        from tree_sitter import Parser
        from tree_sitter_language_pack import get_language

        return Parser(get_language("php"))
    except Exception:  # pragma: no cover - dep import failure
        return None


def _is_controller_path(path: str) -> bool:
    """Check whether a class file path matches the Laravel controllers dir."""
    parts = Path(path).parts
    if len(parts) < len(_CONTROLLER_DIR_MARKER):  # pragma: no cover — analyzer paths
        return False
    for i in range(len(parts) - len(_CONTROLLER_DIR_MARKER) + 1):
        if parts[i : i + len(_CONTROLLER_DIR_MARKER)] == _CONTROLLER_DIR_MARKER:
            return True
    return False


def _parse_php_source(view_path: Path):
    """Best-effort tree-sitter parse of a PHP source file."""
    parser = _get_php_parser()
    if parser is None:  # pragma: no cover - dep import failure
        return None
    try:
        source_bytes = view_path.read_bytes()
    except OSError:  # pragma: no cover — missing-file path tested at the caller
        return None
    return parser.parse(source_bytes)


def _walk(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.children)


def _extract_string_arg(node, source_bytes: bytes) -> Optional[str]:
    """Extract the first string literal argument from a PHP call node."""
    args_node = node.child_by_field_name("arguments")
    if args_node is None:
        return None  # pragma: no cover — call always has args node
    for arg in args_node.children:
        if arg.type != "argument":
            continue
        for child in arg.children:
            if child.type in ("string", "encapsed_string"):
                text = source_bytes[child.start_byte : child.end_byte].decode(
                    "utf-8", "replace"
                )
                return _strip_php_string_quotes(text)
            # First non-string child of the first argument blocks resolution.
            return None
    return None  # pragma: no cover — args present but no argument child


def _strip_php_string_quotes(text: str) -> str:
    """Strip PHP quote characters and the inner ``'`` or ``"`` boundary."""
    if len(text) < 2:
        return text  # pragma: no cover — tree-sitter strings always have quotes
    first, last = text[0], text[-1]
    if first in ("'", '"') and last in ("'", '"'):
        return text[1:-1]
    return text  # pragma: no cover — tree-sitter strings always have quotes


def _find_method_by_short_name_and_line(
    root, method_short_name: str, body_line: int, source_bytes: bytes
):
    """Find a ``method_declaration`` whose name matches and that contains the line."""
    for n in _walk(root):
        if n.type != "method_declaration":
            continue
        name_node = n.child_by_field_name("name")
        if name_node is None:
            continue  # pragma: no cover — defensive
        name = source_bytes[name_node.start_byte : name_node.end_byte].decode(
            "utf-8", "replace"
        )
        if name != method_short_name:
            continue
        start_line = n.start_point[0] + 1
        end_line = n.end_point[0] + 1
        if start_line <= body_line <= end_line:
            return n
    return None  # pragma: no cover — symbol-to-tree alignment always succeeds


def _walk_view_calls_in_method(method_node, source_bytes: bytes):
    """Yield (detection_pattern, view_name, line) for each ``view(...)`` or
    ``View::make(...)`` call inside the method body."""
    body = method_node.child_by_field_name("body")
    if body is None:
        return  # pragma: no cover — abstract methods aren't action methods
    for n in _walk(body):
        if n.type == "function_call_expression":
            func_node = n.child_by_field_name("function")
            if func_node is None or func_node.type != "name":
                continue  # pragma: no cover — every call has a name-typed function
            func_name = source_bytes[
                func_node.start_byte : func_node.end_byte
            ].decode("utf-8", "replace")
            if func_name != "view":
                continue
            view_name = _extract_string_arg(n, source_bytes)
            if view_name is None:
                continue
            yield "view_helper_call", view_name, n.start_point[0] + 1
        elif n.type == "scoped_call_expression":
            scope_node = n.child_by_field_name("scope")
            name_node = n.child_by_field_name("name")
            if scope_node is None or name_node is None:
                continue  # pragma: no cover — defensive
            scope = source_bytes[
                scope_node.start_byte : scope_node.end_byte
            ].decode("utf-8", "replace")
            method = source_bytes[
                name_node.start_byte : name_node.end_byte
            ].decode("utf-8", "replace")
            if scope != "View" or method != "make":
                continue
            view_name = _extract_string_arg(n, source_bytes)
            if view_name is None:
                continue
            yield "view_facade_make", view_name, n.start_point[0] + 1


class LaravelStrategy(ExplicitStringStrategy):
    """Laravel ``view()`` / ``View::make()`` → template files."""

    def find_string_sites(
        self, ctx: LinkerContext
    ) -> Iterator[Tuple[Symbol, str, int, str]]:
        inheritance_index = build_inheritance_index(ctx.edges)
        symbol_by_id = {sym.id: sym for sym in ctx.symbols}

        controller_classes: dict[str, Symbol] = {}
        for sym in ctx.symbols:
            if sym.kind != "class" or sym.language != "php":
                continue
            if not _is_controller_path(sym.path):
                continue
            chain = collect_transitive_base_names(
                sym, symbol_by_id, inheritance_index
            )
            if any(base in _CONTROLLER_BASES for base in chain):
                controller_classes[sym.name] = sym

        if not controller_classes:
            return

        action_methods_by_path: dict[str, list[Symbol]] = {}
        for sym in ctx.symbols:
            if sym.kind != "method" or sym.language != "php":
                continue
            if "." not in sym.name:
                continue  # pragma: no cover — analyzer always qualifies
            class_part = sym.name.rsplit(".", 1)[0]
            if class_part not in controller_classes:
                continue
            action_methods_by_path.setdefault(sym.path, []).append(sym)

        for rel_path, methods in action_methods_by_path.items():
            view_path = ctx.repo_root / rel_path
            tree = _parse_php_source(view_path)
            if tree is None:
                continue
            try:
                source_bytes = view_path.read_bytes()
            except OSError:  # pragma: no cover — parse just succeeded so file exists
                continue
            for method in methods:
                short_name = method.name.rsplit(".", 1)[1]
                body_line = (
                    method.span.start_line if method.span is not None else 1
                )
                method_node = _find_method_by_short_name_and_line(
                    tree.root_node, short_name, body_line, source_bytes
                )
                if method_node is None:
                    continue  # pragma: no cover — symbol-to-tree alignment always succeeds
                for pattern, view_name, lineno in _walk_view_calls_in_method(
                    method_node, source_bytes
                ):
                    yield method, view_name, lineno, pattern

    def string_to_candidates(
        self, string_value: str, action_symbol: Symbol, ctx: LinkerContext
    ) -> Iterable[TemplateCandidate]:
        rel_view_path = string_value.replace(".", "/")
        candidates: list[TemplateCandidate] = []
        for ext, lang in _TEMPLATE_EXTENSIONS:
            candidates.append(
                TemplateCandidate(
                    path=Path("resources/views") / f"{rel_view_path}{ext}",
                    language=lang,
                )
            )
        return candidates


@register_linker(
    "view_template_laravel",
    priority=69,  # After Rails (65), Django (66), Phoenix (67), Spring (68).
    description="Laravel Blade controller view() / View::make() → template binding",
    activation=LinkerActivation(frameworks=["laravel"]),
    # CNF: Laravel is PHP; templates are Blade. Both analyzers contribute the
    # symbols this linker consumes — PHP for the controller view() call,
    # Blade for the template target.
    depends_on=[["php"], ["blade"]],
)
def link_view_template_laravel(ctx: LinkerContext) -> LinkerResult:
    """Linker entry point for registry."""
    return link_via_strategies(ctx, [LaravelStrategy()])


__all__ = ["LaravelStrategy", "link_view_template_laravel"]
