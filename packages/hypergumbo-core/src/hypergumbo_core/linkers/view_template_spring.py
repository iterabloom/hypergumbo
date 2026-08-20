# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: Spring MVC view-name → template binding (WI-hogik).

Spring MVC's view resolver maps a controller method's string return value to
a template path. ``return "users/show"`` resolves to
``src/main/resources/templates/users/show.html`` (Thymeleaf default),
``src/main/resources/templates/users/show.ftl`` (FreeMarker),
``src/main/resources/templates/users/show.vm`` (Velocity), or
``src/main/webapp/WEB-INF/views/users/show.jsp`` (JSP). The ``ModelAndView``
constructor's first argument is also a view name.

The Java analyzer captures class and method ``@Annotation`` metadata in
``sym.meta["decorators"]`` as a list of ``{name, args, kwargs}`` dicts but
does not emit return-statement expressions. To recover string return values
the linker re-parses controller source files with tree-sitter Java, walking
``return_statement`` nodes inside ``method_declaration`` bodies.

Action gating
-------------
A method is a candidate Spring action iff:

* its enclosing class has ``@Controller`` (not ``@RestController``;
  ``@RestController`` returns JSON, never a view name); AND
* the method itself has at least one mapping annotation: ``@RequestMapping``,
  ``@GetMapping``, ``@PostMapping``, ``@PutMapping``, ``@DeleteMapping``, or
  ``@PatchMapping``.

Returns starting with ``"redirect:"`` or ``"forward:"`` are Spring view-resolver
directives (not template paths) and are skipped.

Why ExplicitStringStrategy (not MethodNameStrategy)
---------------------------------------------------
Spring view names are literal strings the developer types, not naming
conventions derived from class + method names. Pairs with Laravel
(WI-hokaj), which is the other ExplicitStringStrategy consumer; the
``string_to_candidates`` hook is what differs between the two.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Iterator, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover - typing only
    import tree_sitter

from ..ir import Symbol
from ._view_template_core import (
    ExplicitStringStrategy,
    TemplateCandidate,
    link_via_strategies,
)
from .registry import LinkerActivation, LinkerContext, LinkerResult, register_linker

# Method-level mapping annotations that mark a Spring controller action.
_MAPPING_ANNOTATIONS = frozenset(
    {
        "RequestMapping",
        "GetMapping",
        "PostMapping",
        "PutMapping",
        "DeleteMapping",
        "PatchMapping",
    }
)

# Template root → extension → language (priority order matters for which
# emission's metadata wins on a probe match).
_TEMPLATE_ROOTS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "src/main/resources/templates",
        (
            (".html", "html"),
            (".ftlh", "ftl"),
            (".ftl", "ftl"),
            (".vm", "vm"),
        ),
    ),
    (
        "src/main/webapp/WEB-INF/views",
        (
            (".jsp", "jsp"),
            (".html", "html"),
        ),
    ),
)

# Return-value prefixes that are Spring view-resolver directives, not template paths.
_NON_TEMPLATE_PREFIXES = ("redirect:", "forward:")


@lru_cache(maxsize=64)
def _get_java_parser() -> Optional["tree_sitter.Parser"]:
    try:  # pragma: no cover - exercised only when tree-sitter is installed
        from tree_sitter import Parser
        from tree_sitter_language_pack import get_language

        return Parser(get_language("java"))
    except Exception:  # pragma: no cover - dep import failure
        return None


def _class_has_controller_annotation(class_sym: Symbol) -> bool:
    """``@Controller`` (but not ``@RestController``) on the class symbol."""
    decorators = (class_sym.meta or {}).get("decorators") or []
    has_controller = False
    has_rest_controller = False
    for dec in decorators:
        name = dec.get("name") if isinstance(dec, dict) else None
        if name == "Controller":
            has_controller = True
        elif name == "RestController":
            has_rest_controller = True
    return has_controller and not has_rest_controller


def _method_has_mapping_annotation(method_sym: Symbol) -> bool:
    decorators = (method_sym.meta or {}).get("decorators") or []
    for dec in decorators:
        if isinstance(dec, dict) and dec.get("name") in _MAPPING_ANNOTATIONS:
            return True
    return False


def _enclosing_class_name(method_sym: Symbol) -> Optional[str]:
    """Java method symbols carry a dotted ``Class.method`` name."""
    if "." not in method_sym.name:  # pragma: no cover — analyzer always qualifies
        return None
    return method_sym.name.rsplit(".", 1)[0]


def _parse_java_source(
    view_path: Path,
) -> Optional[Tuple["tree_sitter.Tree", bytes]]:
    """Best-effort tree-sitter parse of a Java source file."""
    parser = _get_java_parser()
    if parser is None:  # pragma: no cover - dep import failure
        return None
    try:
        source_bytes = view_path.read_bytes()
    except OSError:
        return None
    try:
        return parser.parse(source_bytes), source_bytes
    except Exception:  # pragma: no cover - tree-sitter parses are infallible
        return None


def _find_enclosing_method_node(
    root: "tree_sitter.Node", method_short_name: str, body_line: int,
) -> Optional["tree_sitter.Node"]:
    """Find the ``method_declaration`` node whose name matches and which
    surrounds the given ``body_line``."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "method_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = (
                    name_node.text.decode("utf-8", "replace")
                    if name_node.text else ""
                )
                if name == method_short_name:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    if start_line <= body_line <= end_line:
                        return node
        stack.extend(node.children)
    return None


def _extract_view_name_from_return(
    node: "tree_sitter.Node",
) -> Optional[Tuple[str, str, int]]:
    """Extract ``(view_name, detection_pattern, lineno)`` from a ``return_statement``.

    Recognised shapes:

    * ``return "users/show";`` → string literal
    * ``return new ModelAndView("users/show", model);`` → first arg of
      ``object_creation_expression`` whose type is ``ModelAndView``
    """
    expr = None
    for child in node.children:
        if child.type not in ("return", ";"):
            expr = child
            break
    if expr is None:
        return None

    if expr.type == "string_literal":
        if expr.text is None:  # pragma: no cover - defensive; source is retained
            return None
        text = expr.text.decode("utf-8", "replace")
        view_name = _strip_string_literal_quotes(text)
        return view_name, "return_string", node.start_point[0] + 1

    if expr.type == "object_creation_expression":
        type_node = expr.child_by_field_name("type")
        if type_node is None:
            return None  # pragma: no cover — well-formed ModelAndView always has a type
        type_text = (
            type_node.text.decode("utf-8", "replace") if type_node.text else ""
        )
        if type_text != "ModelAndView":
            return None
        args_node = expr.child_by_field_name("arguments")
        if args_node is None:
            return None  # pragma: no cover — well-formed call always has args node
        for arg in args_node.children:
            if arg.type == "string_literal":
                if arg.text is None:  # pragma: no cover - defensive; source is retained
                    continue
                text = arg.text.decode("utf-8", "replace")
                view_name = _strip_string_literal_quotes(text)
                return (
                    view_name,
                    "model_and_view_first_arg",
                    expr.start_point[0] + 1,
                )
            if arg.type in ("(", ",", ")"):
                continue
            # First non-literal argument blocks resolution.
            return None
    return None


def _strip_string_literal_quotes(text: str) -> str:
    """Strip the leading/trailing ``"`` from a Java string literal."""
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return text[1:-1]
    return text  # pragma: no cover — tree-sitter string_literal always has quotes


def _walk_returns_in_method(
    method_node: "tree_sitter.Node",
) -> Iterator["tree_sitter.Node"]:
    """Yield every ``return_statement`` reachable from a method body."""
    body = method_node.child_by_field_name("body")
    if body is None:
        return  # pragma: no cover — abstract methods don't have @*Mapping
    stack = [body]
    while stack:
        node = stack.pop()
        if node.type == "return_statement":
            yield node
            continue
        stack.extend(node.children)


class SpringStrategy(ExplicitStringStrategy):
    """Spring MVC ``@Controller`` view-name returns → template files."""

    def find_string_sites(
        self, ctx: LinkerContext
    ) -> Iterator[Tuple[Symbol, str, int, str]]:
        controller_classes: dict[str, Symbol] = {}
        for sym in ctx.symbols:
            if sym.kind != "class" or sym.language != "java":
                continue
            if _class_has_controller_annotation(sym):
                controller_classes[sym.name] = sym

        if not controller_classes:
            return

        # Group action methods by source file so we parse each file once.
        action_methods_by_path: dict[str, list[Symbol]] = {}
        for sym in ctx.symbols:
            if sym.kind != "method" or sym.language != "java":
                continue
            enclosing = _enclosing_class_name(sym)
            if enclosing is None or enclosing not in controller_classes:
                continue
            if not _method_has_mapping_annotation(sym):
                continue
            action_methods_by_path.setdefault(sym.path, []).append(sym)

        for rel_path, methods in action_methods_by_path.items():
            view_path = ctx.repo_root / rel_path
            parsed = _parse_java_source(view_path)
            if parsed is None:
                continue
            tree, _ = parsed
            for method in methods:
                short_name = method.name.rsplit(".", 1)[1]
                body_line = (
                    method.span.start_line if method.span is not None else 1
                )
                method_node = _find_enclosing_method_node(
                    tree.root_node, short_name, body_line
                )
                if method_node is None:
                    continue
                for return_node in _walk_returns_in_method(method_node):
                    extracted = _extract_view_name_from_return(return_node)
                    if extracted is None:
                        continue
                    view_name, pattern, lineno = extracted
                    if any(
                        view_name.startswith(prefix)
                        for prefix in _NON_TEMPLATE_PREFIXES
                    ):
                        continue
                    yield method, view_name, lineno, pattern

    def string_to_candidates(
        self, string_value: str, action_symbol: Symbol, ctx: LinkerContext
    ) -> Iterable[TemplateCandidate]:
        candidates: list[TemplateCandidate] = []
        for root, ext_table in _TEMPLATE_ROOTS:
            for ext, lang in ext_table:
                candidates.append(
                    TemplateCandidate(
                        path=Path(root) / f"{string_value}{ext}",
                        language=lang,
                    )
                )
        return candidates


@register_linker(
    "view_template_spring",
    priority=68,  # After Rails (65), Django (66), Phoenix (67).
    description="Spring MVC controller view-name return → template binding",
    activation=LinkerActivation(frameworks=["spring", "spring-mvc"]),
    # CNF: Spring MVC is Java-only.
    depends_on=[["java"]],
)
def link_view_template_spring(ctx: LinkerContext) -> LinkerResult:
    """Linker entry point for registry."""
    return link_via_strategies(ctx, [SpringStrategy()])


__all__ = ["SpringStrategy", "link_view_template_spring"]
