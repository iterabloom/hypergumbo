# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static enforcement of ADR-0034's id-construction discipline (WI-vodin).

WHY A STATIC CHECK WHEN A RUNTIME ONE EXISTS. ``spec_validator._check_id_format``
already classifies a malformed ``Symbol.id``, but only for ids that a given run
actually produced: a linker that only fires on Handlebars templates is invisible
until someone surveys a repository with ``.hbs`` files AND runs the validator.
The producer side is where the grammar is decided, and it was never checked.
This module reads the producers.

THE POPULATION IS THE POINT, AND THE FILED SHAPE IS THE RAREST SPELLING OF IT.
WI-vodin names ``Symbol(id=f"...")``. Measured over ``packages/*/src`` on
2026-09-11 there is exactly ONE such site. The real distribution of the 470
``id=`` arguments is:

    231  a bare local name          <- 20 of these resolve to an f-string
    168  make_symbol_id(...)        <- canonical
     56  a module-local _make_*_id  <- 55 such helpers hand-build a string
     11  make_file_id(...)          <- canonical
      1  an f-string at the call    <- the filed shape

So a check that stops at the constructor reaches 1 site in 470. This one
resolves three hops — the argument, one in-function local assignment, one
same-module function's returns — which is what it takes to reach the 40 places
that really hand-build a node id. A fourth hop needs genuine dataflow and would
trade a sharp rule for a fuzzy one; the tests pin the boundary rather than
leaving it to be rediscovered.

WHAT IS FORBIDDEN IS NOT THE F-STRING, IT IS THE UNGRAMMATICAL ID. A synthetic
linker stand-in has no source span and legitimately writes ``0-0`` by hand;
14 of the 40 hand-built templates are conformant and stay. Banning the spelling
would have deleted those for nothing and taught authors to route around the
check. So the rule is ADR-0036's grammar applied to the TEMPLATE:

    {lang}:{path}:{start}-{end}:{name}:{kind}

parsed from the right (``span, name, kind = parts[-3:]``) because the path slot
is the only one allowed to contain colons. A slot filled by an interpolation is
OPAQUE and is not judged — the check refuses to invent a verdict it has no
evidence for, which is why ``python:{}:1-1:{}:{}`` passes.

WHAT IT FOUND ON ITS FIRST RUN, and the reason this is INV-kurup's class rather
than hygiene: 26 of the 40 templates produce ids that hypergumbo's OWN runtime
validator classifies as malformed. The dominant defect is a single ``line``
interpolated where the grammar wants ``start-end`` (``grpc:{path}:{line}:...``),
which right-anchored parsing then reads as a span of ``"12"``.
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

#: The placeholder marker used inside a skeleton. A real f-string literal part
#: cannot contain NUL, so an interpolation can never be confused with text.
_PH = "\x00"

#: ``lang`` and ``kind`` are lowercase identifiers; the same shapes
#: ``spec_validator._classify_id_format_problem`` enforces at runtime.
_LOWER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
#: A span is ``start-end``; either end may be interpolated.
_SPAN_RE = re.compile(r"^(\x00|\d+)-(\x00|\d+)$")

#: The ADR-0034 canonical factories. A call to one of these needs no further
#: inspection — the grammar lives inside them, and ``make_symbol_id`` is
#: additionally the WI-sikar chokepoint that sanitizes the name slot.
_CANONICAL_FACTORIES = frozenset({"make_symbol_id", "make_file_id"})

#: The constructors whose ``id=`` slot this rule governs, and the grammar each
#: one answers to. WI-vodin asked for ``Edge.id`` to be checked "for the same
#: canonical shape it enforces on Symbol.id"; MEASURED on a live survey, that
#: premise is false — every one of apollo-server's 18,283 edge ids is
#: ``edge:sha256:<16hex>``, a content digest, and enforcing the five-slot node
#: grammar on it would flag all of them. The two ids are counterparts in ROLE
#: (per-instance identity) and not in SHAPE, so they get two rules.
_NODE_ID_CONSTRUCTORS = frozenset({"Symbol"})
_EDGE_ID_CONSTRUCTORS = frozenset({"Edge"})

#: ``Edge.id``'s canonical shape, minted at ``ir.py``'s ``edge:sha256:{hash}``
#: and copied into ~15 per-language ``_make_edge_id`` helpers.
_EDGE_ID_RE = re.compile(r"^edge:sha256:(\x00|[0-9a-f]{16})$")

_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


def edge_template_problem(template: str) -> str | None:
    """Classify an ``Edge.id`` TEMPLATE, or ``None`` when it is conformant."""
    if _EDGE_ID_RE.match(template.replace("{}", _PH)):
        return None
    return (
        f"non_canonical_edge_id ({template!r}) — the grammar is "
        "edge:sha256:<16hex>"
    )


def id_template_problem(template: str) -> str | None:
    """Classify an id TEMPLATE, or return ``None`` when it is conformant.

    ``template`` is the id with each interpolation written ``{}`` — the form a
    reader sees in a diff. Slots filled by an interpolation are opaque and are
    not judged.
    """
    return _skeleton_problem(template.replace("{}", _PH))


def _skeleton_problem(skeleton: str) -> str | None:
    fields = skeleton.split(":")
    if len(fields) < 5:
        return f"wrong_field_count (expected at least 5, got {len(fields)})"
    lang, span, name, kind = fields[0], fields[-3], fields[-2], fields[-1]
    if not (lang == _PH or _LOWER_RE.match(lang)):
        return f"non_canonical_language_prefix ({_show(lang)})"
    if not _SPAN_RE.match(span):
        return (
            f"malformed_span_segment ({_show(span)}) — the grammar is "
            "{start}-{end}; a bare line number is not a span"
        )
    if not (kind == _PH or _LOWER_RE.match(kind)):
        return f"non_canonical_kind_suffix ({_show(kind)})"
    if not name:
        return "empty_name_slot"
    return None


def _show(field: str) -> str:
    return repr(field.replace(_PH, "{}"))


def _skeleton_of(node: ast.JoinedStr) -> str:
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        else:
            parts.append(_PH)
    return "".join(parts)


def _callee_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _local_assignments(fn: _FuncDef) -> dict[str, list[ast.expr]]:
    assigns: dict[str, list[ast.expr]] = defaultdict(list)
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigns[target.id].append(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            assigns[node.target.id].append(node.value)
    return assigns


def _resolve(
    argument: ast.expr,
    assigns: dict[str, list[ast.expr]],
    module_functions: dict[str, _FuncDef],
) -> list[ast.expr]:
    """The three hops. Returns every expression the ``id=`` slot can hold."""
    reached: list[ast.expr] = [argument]
    if isinstance(argument, ast.Name):
        reached.extend(assigns.get(argument.id, []))
    for expr in list(reached):
        if not isinstance(expr, ast.Call):
            continue
        name = _callee_name(expr)
        if name is None or name in _CANONICAL_FACTORIES:
            continue
        target = module_functions.get(name)
        if target is None:
            continue
        reached.extend(
            node.value
            for node in ast.walk(target)
            if isinstance(node, ast.Return) and node.value is not None
        )
    return reached


def _problems_in_file(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
        return []
    module_functions: dict[str, _FuncDef] = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    found: dict[int, str] = {}
    for fn in module_functions.values():
        assigns = _local_assignments(fn)
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            callee = _callee_name(node)
            if callee in _NODE_ID_CONSTRUCTORS:
                classify = _skeleton_problem
            elif callee in _EDGE_ID_CONSTRUCTORS:
                classify = _edge_skeleton_problem
            else:
                continue
            for keyword in node.keywords:
                if keyword.arg != "id":
                    continue
                for expr in _resolve(keyword.value, assigns, module_functions):
                    problem = _problem_of(expr, classify)
                    if problem is not None:
                        found.setdefault(expr.lineno, problem)
    return sorted(found.items())


def _edge_skeleton_problem(skeleton: str) -> str | None:
    if _EDGE_ID_RE.match(skeleton):
        return None
    return (
        f"non_canonical_edge_id ({_show(skeleton)}) — the grammar is "
        "edge:sha256:<16hex>"
    )


def _problem_of(
    expr: ast.expr, classify: "Callable[[str], str | None]",
) -> str | None:
    if isinstance(expr, ast.JoinedStr):
        return classify(_skeleton_of(expr))
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        return (
            "concatenated_id — a '+' chain cannot be checked against the "
            "grammar; build the id with make_symbol_id()"
        )
    return None


def find_id_construction_drift(repo_root: Path) -> list[str]:
    """Return one ``path:line — problem`` string per violation, sorted."""
    offenders: list[str] = []
    for src_root in sorted(repo_root.glob("packages/*/src")):
        for path in sorted(src_root.rglob("*.py")):
            for lineno, problem in _problems_in_file(path):
                rel = path.relative_to(repo_root)
                offenders.append(f"{rel}:{lineno} — {problem}")
    return offenders
