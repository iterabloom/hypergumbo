# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static enforcement of ADR-0034's id-construction discipline (WI-vodin).

WHY A STATIC CHECK WHEN A RUNTIME ONE EXISTS. ``spec_validator._check_id_format``
already classifies a malformed ``Symbol.id``, but only for ids that a given run
actually produced: a linker that only fires on Handlebars templates is invisible
until someone surveys a repository with ``.hbs`` files AND runs the validator.
The producer side is where the grammar is decided, so this module reads the
producers.

WHY THREE HOPS. An f-string written directly at the constructor,
``Symbol(id=f"...")``, is the rarest spelling of a hand-built id. Many ``id=``
arguments are a bare local name or a call to a module-local ``_make_*_id``
helper, and the hand-built strings sit behind those. So a check that stops at
the constructor reaches almost none of them. This one resolves three hops —
the argument, one in-function local assignment, one same-module function's
returns. A fourth hop needs genuine dataflow and would trade a sharp rule for
a fuzzy one; the tests pin the boundary rather than leaving it to be
rediscovered. (That is the ``id=`` rule. Edge endpoints are walked further,
below.)

WHAT IS FORBIDDEN IS NOT THE F-STRING, IT IS THE UNGRAMMATICAL ID. A synthetic
linker stand-in has no source span and legitimately writes ``0-0`` by hand.
Banning the spelling would reject those conformant templates for nothing and
teach authors to route around the check. So the rule for ``Symbol(id=...)`` is
ADR-0036's grammar applied to the TEMPLATE:

    {lang}:{path}:{start}-{end}:{name}:{kind}

parsed from the right (``span, name, kind = parts[-3:]``) because the path slot
is the only one allowed to contain colons. A slot filled by an interpolation is
OPAQUE and is not judged — the check refuses to invent a verdict it has no
evidence for, which is why ``python:{}:1-1:{}:{}`` passes.

A ``NodeId(...)`` OR ``read_node_id(...)`` WRAPPING IS SEEN THROUGH. Once
``Symbol.id`` is typed ``ir.NodeId``, a producer that keeps a hand-built id
wraps it in ``NodeId(f"...")`` to satisfy mypy. The wrapper passes its
argument through unchanged, so every hop judges the expression inside it
(:func:`_unwrap`); otherwise the wrapping that the type checker asks for would
take the template out of this check.

EDGE IDS GET THEIR OWN RULE. An ``Edge.id`` is a content digest,
``edge:sha256:<16hex>``, not a node id, so ``Edge(id=...)`` is judged, through
the same three hops, against that shape (:func:`edge_template_problem`). For
both constructors, an id built by a ``+`` chain is reported as
``concatenated_id``, because a concatenation cannot be checked against
either grammar.

EDGE ENDPOINTS ARE NODE IDS. An edge's ``src`` and ``dst`` name nodes, so an
endpoint written by hand answers to the NODE grammar: the ``src=`` /
``dst=`` keywords of ``Edge(...)`` and ``Edge.create(...)``, and
``Edge.create``'s first two positional arguments
(:func:`_edge_endpoints`), each branch of a conditional or ``or`` chain
judged on its own (:func:`_alternatives`: ``found or f"robot:{path}"``).
About 40 endpoints in 23 producers were spelled
``f"{rel_path}:file"``, ``f"meson:subdir:{d}"``, ``f"r:external:{p}::{f}"``
(WI-tatop); none parses, and finalize folded every edge of one spelling into
one ``<unknown>`` node, a defect the runtime count
(``AnalysisRun.non_id_edge_endpoints``) sees only on a run whose tree holds
the construct. A bare string literal is judged too (``dst="amsmath"``), but
not the EMPTY string: ``ref = ""`` is the sentinel a producer assigns before
the loop that sets it, and the hop reads every assignment to the local.

AN ENDPOINT IS FOLLOWED PAST ITS FUNCTION, ONE LEVEL EACH WAY (WI-jozih).
Endpoints are judged by a walk over the whole tree (:class:`_EndpointWalk`),
not one file: ``make_unresolved_edge(lang, src_id, ...)`` writes
``src=src_id``, and the value is written by its 91 callers. So an endpoint
that is a PARAMETER is followed to every call of its function, by name in its
own module and through an import in another, and what each call passes is
read there (a default when the call omits it); a value returned by a HELPER
OF ANOTHER MODULE is read at that helper's ``return``, its own parameters
bound to what the call passed. Locals, module constants, conditional
branches, tuple parts and same-module helpers are read along the way, and so
are the REGISTRIES analyzers keep: a read ``reg[k]`` / ``reg.get(k, d)`` is
read at every value the module writes into a container of that name
(``reg[k] = v``, ``setdefault``, ``append``, a dict or list literal or
comprehension; one passed to a helper is the one the call passed), an
attribute ``x.attr`` at every value assigned to an attribute of that name or
passed for it to a class, and a ``for`` variable at an element of what it
iterates. Matching is by name in the module, so a second container of the
same name can only add values to read, never hide one. The
depth is one call level for a parameter and one module for a helper: a call
site that passes on its own parameter, or a helper reached through another
module's helper, is not followed further. A minting factory
(:data:`_ENDPOINT_FACTORIES`) and a symbol's ``.id`` end the walk.

WHAT IT CANNOT FOLLOW IS JUDGED BY HAND, AND DECLARED. Every endpoint the
walk does not reach a written value for is listed by
:func:`find_id_construction_drift` with one of :data:`UNJUDGED_REASONS` (the
depth limits above, a parameter nothing calls, an argument in ``*args``, a
callable the tree does not define, a value computed some other way). Each was
read by hand and is declared in :data:`KNOWN_UNJUDGED_ENDPOINTS` with what it
holds; :func:`undeclared_unjudged_endpoints` fails the check on one that is
not, and on a declaration nothing matches, so the table only shrinks. When
it was made: 59 sites unjudged, 39 then followed by the walk, 1 fixed at its
source (hcl's raw module path), 19 declared, none a violation.
``scripts/check-id-construction`` lists them with ``--unjudged``. At run time
``AnalysisRun.non_id_edge_endpoints`` (ADR-0034 §"Enforcement") counts what
a pass emits outside the grammar, whichever way it was built.

THE KIND SLOT AGAINST THE SYMBOL'S KIND. A grammatical id can still name the
wrong kind: ADR-0036 Ruling 2 says the kind slot IS ``Symbol.kind``. Four
producers broke it while passing the grammar check, two of them by calling the
canonical factory and handing it a role word (``export_entry`` on a
``kind="export"`` Symbol). So for each ``Symbol(id=..., kind=...)`` the id's
kind word is read through the same three hops, WHERE IT IS WRITTEN: the
``kind`` argument of a canonical factory (:data:`_KIND_ARGUMENT_OF_FACTORY`),
or the last slot of a template. A same-module helper is read at its
``return``: a literal written there is the word, and one of the helper's own
parameters written there is the word the caller passed for it. A helper is
never read by its parameter NAMES: the websocket linker's helper took a
``kind`` it appended to the name slot, and wrote ``"function"`` in the kind
slot itself. Two string literals that differ are a ``kind_slot_mismatch``.
Anything else is not judged: a name on either side, a computed slot, a helper
this module does not define. That is the same refusal to invent a verdict as
the grammar check's, and it means a producer that computes its kind word
(a table lookup, a variable holding a keyword) is checked only by the runtime
validator and by the fixture gates that run the producers.

A mismatch whose correction needs a stable-id scheme bump is DECLARED in
:data:`KNOWN_KIND_SLOT_HOLES` with its reason. A declaration nothing matches
is itself reported (``stale_kind_slot_hole``), so the table cannot outlive
the defect it excuses.

WHY IT IS NOT HYGIENE (INV-kurup's class). A template this check flags
produces ids that hypergumbo's OWN runtime validator classifies as malformed.
The typical defect is a single ``line`` interpolated where the grammar wants
``start-end`` (``grpc:{path}:{line}:...``), which right-anchored parsing then
reads as a span of ``"12"``.
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
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

#: Factories that take the id's kind word as an argument, and its position.
#: ``make_doc_symbol_ids`` mints the node id and the stable id from one
#: argument set, so its ``kind`` is the id's kind slot too.
_KIND_ARGUMENT_OF_FACTORY: dict[str, int] = {
    "make_symbol_id": 5,
    "make_doc_symbol_ids": 2,
}

#: ``(repo-relative path, id kind word, Symbol kind) -> why it stands``.
#: Empty: robot's ``test_case`` and twig's ``function_call``, the two words
#: that fed a stable id, were corrected with the v15 stable-id scheme. A
#: future mismatch whose correction must wait for a scheme bump is declared
#: here the same way.
KNOWN_KIND_SLOT_HOLES: dict[tuple[str, str, str], str] = {}

#: The constructors whose ``id=`` slot this rule governs, and the grammar each
#: one answers to. An ``Edge.id`` is ``edge:sha256:<16hex>``, a content digest,
#: so enforcing the five-slot node grammar on it would flag every edge. The two
#: ids are counterparts in ROLE (per-instance identity) and not in SHAPE, so
#: they get two rules.
_NODE_ID_CONSTRUCTORS = frozenset({"Symbol"})
_EDGE_ID_CONSTRUCTORS = frozenset({"Edge"})

#: The keywords that hold an edge's endpoints, and their positions in
#: ``Edge.create(src, dst, ...)``.
_EDGE_ENDPOINTS = ("src", "dst")

#: ``Edge.id``'s canonical shape, minted at ``ir.py``'s ``edge:sha256:{hash}``
#: and copied into per-language ``_make_edge_id`` helpers.
_EDGE_ID_RE = re.compile(r"^edge:sha256:(\x00|[0-9a-f]{16})$")

_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef

#: Calls that hand their one argument through as a node id, unchanged: the
#: ``NodeId`` NewType and its boundary (``ir.read_node_id``). The id inside is
#: what is judged, so wrapping a hand-built template in ``NodeId(...)`` to
#: satisfy the type checker does not also hide it from this check.
_ID_WRAPPERS = frozenset({"NodeId", "read_node_id"})


def _unwrap(expr: ast.expr) -> ast.expr:
    """*expr* with any ``NodeId(...)`` / ``read_node_id(...)`` wrapping removed."""
    while (
        isinstance(expr, ast.Call)
        and _callee_name(expr) in _ID_WRAPPERS
        and len(expr.args) == 1
        and not expr.keywords
    ):
        expr = expr.args[0]
    return expr


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


def _template_of(node: ast.JoinedStr) -> str:
    """The f-string as the classifiers read it: each interpolation ``{}``."""
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        else:
            parts.append("{}")
    return "".join(parts)


def _callee_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _edge_endpoints(call: ast.Call) -> list[tuple[str, ast.expr]]:
    """``(side, expression)`` for each endpoint an edge construction passes.

    ``Edge(src=..., dst=...)`` and ``Edge.create(src=..., dst=...)`` by
    keyword; ``Edge.create(src, dst, ...)`` by position as well. Any other
    call (another class's ``create``) has none.
    """
    func = call.func
    is_create = (
        isinstance(func, ast.Attribute) and func.attr == "create"
        and isinstance(func.value, ast.Name) and func.value.id == "Edge"
    )
    if not (is_create or (isinstance(func, ast.Name) and func.id == "Edge")):
        return []
    found: list[tuple[str, ast.expr]] = [
        (k.arg, k.value) for k in call.keywords
        if k.arg is not None and k.arg in _EDGE_ENDPOINTS
    ]
    if is_create:
        for side, arg in zip(_EDGE_ENDPOINTS, call.args[:2], strict=False):
            if isinstance(arg, ast.Starred):
                break  # positions after a ``*args`` are not known
            found.append((side, arg))
    return found


def _alternatives(expr: ast.expr) -> list[ast.expr]:
    """The values *expr* can take where it chooses between written ones.

    ``a if c else f"pony:{path}"`` and ``found or f"robot:{path}"`` each
    hold a hand-built fallback the hop would otherwise see as one opaque
    expression; both branches of a conditional and every operand of a
    boolean chain are judged.
    """
    if isinstance(expr, ast.IfExp):
        return _alternatives(expr.body) + _alternatives(expr.orelse)
    if isinstance(expr, ast.BoolOp):
        return [alt for value in expr.values for alt in _alternatives(value)]
    return [expr]


def _endpoint_problem(expr: ast.expr) -> str | None:
    """The grammar problem of one expression an endpoint can hold, or ``None``.

    An f-string or a non-empty literal is judged as a node-id template; a
    ``+`` chain cannot be. The empty string is an initializer, not a value.
    """
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return id_template_problem(expr.value) if expr.value else None
    return _problem_of(expr, id_template_problem)


def _local_assignments(fn: _FuncDef) -> dict[str, list[ast.expr]]:
    assigns: dict[str, list[ast.expr]] = defaultdict(list)
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigns[target.id].append(node.value)
                elif isinstance(target, ast.Tuple):
                    # ``symbol_id, stable_id = make_doc_symbol_ids(...)``: each
                    # name holds a part of the call's result.
                    for element in target.elts:
                        if isinstance(element, ast.Name):
                            assigns[element.id].append(node.value)
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
    """The three hops. Returns every expression the ``id=`` slot can hold.

    A ``NodeId(...)`` wrapping is seen through at each hop (:func:`_unwrap`).
    """
    argument = _unwrap(argument)
    reached: list[ast.expr] = [argument]
    if isinstance(argument, ast.Name):
        reached.extend(_unwrap(value) for value in assigns.get(argument.id, []))
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
            _unwrap(node.value)
            for node in ast.walk(target)
            if isinstance(node, ast.Return) and node.value is not None
        )
    return reached


def _word(expr: ast.expr | None) -> tuple[str, str] | None:
    """``("literal", "task")`` or ``("name", "kind")``; None for anything else."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return ("literal", expr.value)
    if isinstance(expr, ast.Name):
        return ("name", expr.id)
    return None


def _direct_kind_word(expr: ast.expr) -> tuple[str, str] | None:
    """The kind word an expression WRITES into the id, read where it is written.

    A canonical factory call: its ``kind`` argument. A template: its last slot,
    a literal or one bare interpolated name (``...:{name}:{kind}``). None for
    anything else, a call to any other function included.
    """
    expr = _unwrap(expr)
    if isinstance(expr, ast.JoinedStr):
        slot = _template_of(expr).rsplit(":", 1)[-1]
        if _LOWER_RE.match(slot):
            return ("literal", slot)
        if slot != "{}":
            return None
        last = expr.values[-1]
        if isinstance(last, ast.FormattedValue) and isinstance(last.value, ast.Name):
            return _word(last.value)
        return None
    if not isinstance(expr, ast.Call):
        return None
    index = _KIND_ARGUMENT_OF_FACTORY.get(_callee_name(expr) or "")
    if index is None:
        return None
    return _argument(expr, "kind", index)


def _argument(call: ast.Call, name: str, index: int) -> tuple[str, str] | None:
    """The word *call* passes for the parameter *name* at position *index*."""
    for keyword in call.keywords:
        if keyword.arg == name:
            return _word(keyword.value)
    arguments = call.args
    if index >= len(arguments) or any(
        isinstance(a, ast.Starred) for a in arguments[: index + 1]
    ):
        return None
    return _word(arguments[index])


def _id_kind_words(
    expr: ast.expr, module_functions: dict[str, _FuncDef],
) -> list[tuple[str, str]]:
    """The kind words one expression an ``id=`` slot can hold may write.

    A same-module helper is read at its ``return``s, never by its parameter
    names. What a helper does with a parameter called ``kind`` is the helper's
    business: the websocket linker's ``_make_symbol_id(path, line, event, kind,
    language)`` appends it to the NAME slot and writes ``"function"`` in the
    kind slot, so judging the caller's ``"endpoint"`` against the Symbol's kind
    reported a conformant producer. So: a return that writes a literal gives
    that literal; a return that writes one of the helper's own parameters gives
    the word the caller passed for it; anything else gives nothing.
    """
    expr = _unwrap(expr)
    direct = _direct_kind_word(expr)
    if direct is not None:
        return [direct]
    if not isinstance(expr, ast.Call):
        return []
    helper = module_functions.get(_callee_name(expr) or "")
    if helper is None:
        return []
    parameters = [a.arg for a in (*helper.args.posonlyargs, *helper.args.args)]
    words: list[tuple[str, str]] = []
    for node in ast.walk(helper):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        word = _direct_kind_word(node.value)
        if word is not None and word[0] == "name":
            if word[1] not in parameters:
                continue
            word = _argument(expr, word[1], parameters.index(word[1]))
        if word is not None:
            words.append(word)
    return words


def _id_name_uses(fn: _FuncDef) -> dict[str, list[int]]:
    """Per local name, the lines where a Symbol takes it as ``id=``."""
    uses: dict[str, list[int]] = defaultdict(list)
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _callee_name(node) in _NODE_ID_CONSTRUCTORS:
            for keyword in node.keywords:
                if keyword.arg == "id" and isinstance(keyword.value, ast.Name):
                    uses[keyword.value.id].append(node.lineno)
    return uses


def _feeding_this_use(
    argument: ast.expr, call: ast.Call,
    assigns: dict[str, list[ast.expr]], id_uses: dict[str, list[int]],
) -> dict[str, list[ast.expr]]:
    """The assignments that can feed THIS ``Symbol(id=name)``.

    The grammar check may read every assignment to the local: an ungrammatical
    one is wrong whichever Symbol it reaches. The kind comparison may not. A
    function that builds several Symbols reuses one local (``symbol_id = ...``
    then ``Symbol(id=symbol_id, kind="resource")``, again for ``"data"``), and
    pairing every assignment with every Symbol reports each against the
    others' kinds. So only the assignments written after the previous Symbol
    that took the name, and not after this one, are paired with it. That
    keeps both arms of a branch between two uses.
    """
    if not isinstance(argument, ast.Name):
        return assigns
    name = argument.id
    earlier = [line for line in id_uses.get(name, ()) if line < call.lineno]
    after = max(earlier, default=0)
    last = call.end_lineno or call.lineno
    return {name: [v for v in assigns.get(name, ()) if after < v.lineno <= last]}


#: One finding: line, text, and for a kind-slot mismatch its two words.
_Finding = tuple[int, str, "tuple[str, str] | None"]


def _problems_in_tree(tree: ast.Module) -> list[_Finding]:
    module_functions: dict[str, _FuncDef] = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    found: dict[int, tuple[str, tuple[str, str] | None]] = {}
    for fn in module_functions.values():
        assigns = _local_assignments(fn)
        id_uses = _id_name_uses(fn)
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            callee = _callee_name(node)
            # The public classifiers, so the rule the tests pin is the rule
            # the lint runs, not a private twin that can drift (WI-mifup).
            if callee in _NODE_ID_CONSTRUCTORS:
                classify = id_template_problem
            elif callee in _EDGE_ID_CONSTRUCTORS:
                classify = edge_template_problem
            else:
                continue
            symbol_kind = next(
                (_word(k.value) for k in node.keywords if k.arg == "kind"), None,
            )
            for keyword in node.keywords:
                if keyword.arg != "id":
                    continue
                for expr in _resolve(keyword.value, assigns, module_functions):
                    problem = _problem_of(expr, classify)
                    if problem is not None:
                        found.setdefault(expr.lineno, (problem, None))
                if callee not in _NODE_ID_CONSTRUCTORS:
                    continue
                if symbol_kind is None or symbol_kind[0] != "literal":
                    continue
                feeding = _feeding_this_use(keyword.value, node, assigns, id_uses)
                # The argument and its local assignments; the helper hop is
                # taken inside _id_kind_words, which needs the CALL to bind a
                # helper's parameter to what was passed for it.
                held = [keyword.value]
                if isinstance(keyword.value, ast.Name):
                    held.extend(feeding.get(keyword.value.id, []))
                for expr in held:
                    if expr.lineno in found:
                        continue
                    for id_kind in _id_kind_words(expr, module_functions):
                        if id_kind[0] != "literal" or id_kind[1] == symbol_kind[1]:
                            continue
                        found.setdefault(expr.lineno, (
                            f"kind_slot_mismatch — the id's kind slot is "
                            f"{id_kind[1]!r} but the Symbol's kind is "
                            f"{symbol_kind[1]!r}; pass the Symbol's own kind "
                            f"(ADR-0036 Ruling 2)",
                            (id_kind[1], symbol_kind[1]),
                        ))
    return [(line, text, words) for line, (text, words) in sorted(found.items())]


def _problem_of(
    expr: ast.expr, classify: "Callable[[str], str | None]",
) -> str | None:
    if isinstance(expr, ast.JoinedStr):
        return classify(_template_of(expr))
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        return (
            "concatenated_id — a '+' chain cannot be checked against the "
            "grammar; build the id with make_symbol_id()"
        )
    return None


def find_id_construction_drift(
    repo_root: Path,
    known_kind_slot_holes: dict[tuple[str, str, str], str] | None = None,
    unjudged: "list[UnjudgedEndpoint] | None" = None,
) -> list[str]:
    """Return one ``path:line — problem`` string per violation, sorted.

    ``known_kind_slot_holes`` defaults to :data:`KNOWN_KIND_SLOT_HOLES`. A
    declared mismatch is not reported; a declaration whose file is in the tree
    and holds no such mismatch is.

    ``unjudged``, when given, receives one :class:`UnjudgedEndpoint` per edge
    endpoint the check could not follow to a written value, sorted; the
    reasons are :data:`UNJUDGED_REASONS`. They are not violations here:
    :func:`undeclared_unjudged_endpoints` turns each one nobody has judged by
    hand into one, and ``AnalysisRun.non_id_edge_endpoints`` guards them at
    run time (ADR-0034 §"Enforcement").
    """
    holes = KNOWN_KIND_SLOT_HOLES if known_kind_slot_holes is None else known_kind_slot_holes
    matched: set[tuple[str, str, str]] = set()
    offenders: list[str] = []
    modules: list[_Module] = []
    for src_root in sorted(repo_root.glob("packages/*/src")):
        for path in sorted(src_root.rglob("*.py")):
            rel = path.relative_to(repo_root).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
                continue
            modules.append(_index_module(tree, path, rel, src_root))
            for lineno, problem, words in _problems_in_tree(tree):
                if words is not None and (rel, *words) in holes:
                    matched.add((rel, *words))
                    continue
                offenders.append(f"{rel}:{lineno} — {problem}")
    walk = _EndpointWalk(modules)
    walk.run()
    seen_lines = {line.split(" — ", 1)[0] for line in offenders}
    for (rel, lineno), problem in walk.violations.items():
        if f"{rel}:{lineno}" not in seen_lines:
            offenders.append(f"{rel}:{lineno} — {problem}")
    for rel, id_kind, symbol_kind in sorted(set(holes) - matched):
        if not (repo_root / rel).is_file():
            # Not this tree's declaration (a scratch tree in a test). That
            # every declared path exists in the real tree is pinned by a test.
            continue
        offenders.append(
            f"{rel} — stale_kind_slot_hole — a mismatch of id kind slot "
            f"{id_kind!r} against Symbol kind {symbol_kind!r} is declared in "
            f"KNOWN_KIND_SLOT_HOLES and no longer exists; remove the declaration"
        )
    if unjudged is not None:
        unjudged.extend(sorted(walk.unjudged, key=lambda u: (u.rel, u.line, str(u))))
    return sorted(offenders, key=_finding_order)


def _finding_order(finding: str) -> tuple[str, int, str]:
    """File, then line as a number, then text."""
    where = finding.split(" — ", 1)[0]
    rel, _, line = where.rpartition(":")
    if not line.isdigit():
        return (where, 0, finding)
    return (rel, int(line), finding)


# ---------------------------------------------------------------------------
# Edge endpoints, followed across a parameter and a helper of another module
# ---------------------------------------------------------------------------

#: Calls that mint a node id whole. Their result is an id by construction; the
#: grammar lives inside them (``analyze/base.py``, ``analyze/edge_target.py``).
_ENDPOINT_FACTORIES = frozenset({
    *_CANONICAL_FACTORIES, "make_module_import_dst", "make_manifest_target_dst",
    "in_tree_file_id", "external_target",
})

#: Attribute reads that hold a node's own id: ``sym.id`` (judged where that
#: Symbol is constructed) and another edge's ``src`` / ``dst``.
_NODE_ID_ATTRIBUTES = frozenset({"id", "src", "dst"})

#: Why an endpoint was not judged. Each is counted and listed; none is a pass.
UNJUDGED_REASONS: dict[str, str] = {
    "parameter_beyond_one_call": (
        "a parameter whose call site passes another parameter: the check "
        "follows one call level"
    ),
    "parameter_without_call_site": (
        "a parameter of a function no call in the tree names (a callback, an "
        "entry point, a dynamic call)"
    ),
    "argument_not_bound": (
        "a call site that passes the argument through *args / **kwargs, or "
        "omits it with no default"
    ),
    "helper_not_located": (
        "a function the tree does not define (a builtin, a third-party or "
        "dynamically chosen callable)"
    ),
    "helper_beyond_one_level": (
        "a helper of another module reached through another one: the check "
        "follows one"
    ),
    "computed": (
        "a value read from a container, an attribute, a method call or a "
        "loop variable"
    ),
}


@dataclass(frozen=True)
class UnjudgedEndpoint:
    """An edge endpoint the walk could not follow to a written value."""

    rel: str
    line: int
    #: The function holding the Edge construction (``<module>`` at top level).
    function: str
    side: str
    reason: str
    where: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        """What :data:`KNOWN_UNJUDGED_ENDPOINTS` declares: no line number, so
        a declaration survives edits above the site."""
        return (self.rel, self.function, self.side, self.reason)

    def __str__(self) -> str:
        return (
            f"{self.rel}:{self.line} — edge_endpoint ({self.side}) not judged: "
            f"{self.reason} ({self.where})"
        )


#: Edge endpoints the walk cannot follow, each read BY HAND and found to hold
#: a node id: ``(file, function, side, reason) -> what it holds``. Shrink-only:
#: a site the walk learns to follow, or that goes away, makes its entry
#: stale, and :func:`undeclared_unjudged_endpoints` reports the stale entry;
#: a site the walk cannot follow and nobody has declared fails the check.
#: Every site the walk reported unjudged before it read registries (59) was
#: judged by hand: 39 the walk follows now, 1 was raw text and was fixed at
#: its source, and these 19 sites (18 keys) hold node ids.
KNOWN_UNJUDGED_ENDPOINTS: dict[tuple[str, str, str, str], str] = {
    ("packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py",
     "make_unresolved_edge", "src", "parameter_beyond_one_call"):
        "src_id: every caller passes a symbol's .id; perl._package_unresolved_edge "
        "passes on caller_id, which its callers set from caller.id",
    ("packages/hypergumbo-core/src/hypergumbo_core/linkers/ipc.py",
     "link_ipc", "dst", "computed"):
        "preload_send_id: part 1 of a bridge_maps value, built by "
        "_bridge_send_id (a make_symbol_id helper) in a nested comprehension",
    ("packages/hypergumbo-core/src/hypergumbo_core/linkers/method_call_recovery.py",
     "link_method_call_recovery", "src", "computed"):
        "caller_id: a key of class_hints, written as e.src of an analyzer edge",
    ("packages/hypergumbo-core/src/hypergumbo_core/linkers/react_component.py",
     "link_react_components", "src", "computed"):
        "min(enclosing)[1]: part 1 of (span length, s.id) tuples, else a file "
        "symbol's id or make_file_id",
    ("packages/hypergumbo-core/src/hypergumbo_core/linkers/receiver_type_dispatch.py",
     "link_receiver_type_dispatch", "dst", "computed"):
        "candidates[0][0]: an index entry (sym.id, evidence) built from symbols",
    ("packages/hypergumbo-core/src/hypergumbo_core/scip/calls.py",
     "scip_index_to_call_edges", "dst", "helper_not_located"):
        "resolve_external_dst: a callable parameter; its one production value "
        "(scip-python translate._external_dst) returns format_legacy_dst(ref)",
    ("packages/hypergumbo-core/src/hypergumbo_core/scip/calls.py",
     "scip_index_to_call_edges", "src", "helper_not_located"):
        "resolve_symbol: a callable parameter returning an emitted symbol's id",
    ("packages/hypergumbo-core/src/hypergumbo_core/scip/edges.py",
     "scip_index_to_edges", "dst", "helper_not_located"):
        "resolve_external_dst, as in scip/calls.py",
    ("packages/hypergumbo-core/src/hypergumbo_core/scip/edges.py",
     "scip_index_to_edges", "src", "helper_not_located"):
        "resolve_symbol, as in scip/calls.py",
    ("packages/hypergumbo-lang-common/src/hypergumbo_lang_common/rst.py",
     "_resolve_refs", "dst", "computed"):
        "found[0]: labels.setdefault(...).append(sym.id), a target symbol's id",
    ("packages/hypergumbo-lang-common/src/hypergumbo_lang_common/rst.py",
     "_extract_reference", "dst", "computed"):
        "dst: _file_dst (in_tree_file_id or make_module_import_dst) or "
        "external_target, bound by a nested tuple target",
    ("packages/hypergumbo-lang-common/src/hypergumbo_lang_common/starlark.py",
     "_resolve_dependency_edges", "src", "computed"):
        "src_id: part 0 of pending_deps entries, appended as (sym.id, ...)",
    ("packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/ada.py",
     "extract_edges_from_file", "dst", "computed"):
        "self._package_ids(...).get(...): a dict comprehension of package "
        "sym.id, else make_module_import_dst",
    ("packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/apex.py",
     "_add_call_edge", "src", "parameter_beyond_one_call"):
        "src_id: passed by _extract_method_call / _extract_constructor_call from "
        "_extract_edges_recursive, set by _caller_id "
        "(a make_symbol_id / file id helper)",
    ("packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/smithy.py",
     "_make_reference_edge", "src", "parameter_beyond_one_call"):
        "src_id: passed down from _extract_member_types, called with structure.id",
    ("packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/css.py",
     "_process_css_tree", "dst", "helper_not_located"):
        "local_import_id: a callable parameter; its production value "
        "(_local_import_resolver) returns make_file_id or None",
    ("packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/yaml_ansible.py",
     "process_mapping_pairs", "dst", "computed"):
        "RAW TEXT by design: the include's written target, rewritten in "
        "AnsibleAnalyzer.analyze before the pass returns (a file node id, a "
        "fan-out file id, or a module stand-in)",
    ("packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/yaml_ansible.py",
     "analyze", "dst", "computed"):
        "extra_dst: a further _jinja_fanout_candidates entry, file_node_ids values",
}


def undeclared_unjudged_endpoints(
    unjudged: "list[UnjudgedEndpoint]", repo_root: Path,
    declared: dict[tuple[str, str, str, str], str] | None = None,
) -> list[str]:
    """One violation per unjudged endpoint nobody declared, and per stale
    declaration (its file is in the tree and holds no such site)."""
    table = KNOWN_UNJUDGED_ENDPOINTS if declared is None else declared
    found = {u.key for u in unjudged}
    problems = [
        f"{u.rel}:{u.line} — undeclared_unjudged_endpoint ({u.side}, {u.reason}: "
        f"{u.where}) — make the endpoint readable (a factory, a symbol's .id) "
        f"or read it by hand and declare it in KNOWN_UNJUDGED_ENDPOINTS"
        for u in unjudged if u.key not in table
    ]
    problems.extend(
        f"{rel} — stale_unjudged_declaration ({function}, {side}, {reason}) — "
        f"the walk follows it now or it is gone; remove the declaration"
        for rel, function, side, reason in sorted(set(table) - found)
        if (repo_root / rel).is_file()
    )
    return problems


@dataclass(eq=False)
class _Module:
    """One parsed source file, indexed for the endpoint walk."""

    rel: str
    dotted: str
    #: Every definition of each function name: a call by name (``self.f``
    #: among them) can mean any of them, so each is read.
    functions: dict[str, list[_FuncDef]]
    #: local name -> (module, name imported from it); name None for ``import m``.
    imports: dict[str, tuple[str, str | None]]
    #: ``id(call)`` -> the functions enclosing it, innermost first.
    chains: dict[int, tuple[_FuncDef, ...]]
    #: ``id(function)`` -> the function and those enclosing it.
    def_chains: dict[int, tuple[_FuncDef, ...]]
    calls: list[ast.Call]
    module_assigns: dict[str, list[ast.expr]]
    #: container name -> each value written into it, with the functions
    #: enclosing the write (:func:`_container_writes_of`).
    container_writes: dict[str, list[tuple[ast.expr, tuple[_FuncDef, ...]]]]
    #: attribute name -> each value assigned to it (``self.x = v``) or passed
    #: for it to a class (``Emission(action_id=v)``), with its functions.
    attribute_writes: dict[str, list[tuple[ast.expr, tuple[_FuncDef, ...]]]]


def _index_module(tree: ast.Module, path: Path, rel: str, src_root: Path) -> _Module:
    """One pass over *tree*: its imports, functions and calls, and the
    functions enclosing each call and each function."""
    parts = list(path.relative_to(src_root).with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    dotted = ".".join(parts)
    package = parts if is_package else parts[:-1]
    imports: dict[str, tuple[str, str | None]] = {}
    functions: dict[str, list[_FuncDef]] = defaultdict(list)
    chains: dict[int, tuple[_FuncDef, ...]] = {}
    def_chains: dict[int, tuple[_FuncDef, ...]] = {}
    calls: list[ast.Call] = []
    container_writes: dict[str, list[tuple[ast.expr, tuple[_FuncDef, ...]]]] = (
        defaultdict(list)
    )
    attribute_writes: dict[str, list[tuple[ast.expr, tuple[_FuncDef, ...]]]] = (
        defaultdict(list)
    )
    stack: list[tuple[ast.AST, tuple[_FuncDef, ...]]] = [(tree, ())]
    while stack:
        node, chain = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[child.name].append(child)
                def_chains[id(child)] = (child, *chain)
                stack.append((child, (child, *chain)))
                continue
            for value in _container_writes_of(child):
                container_writes[value[0]].append((value[1], chain))
            for value in _attribute_writes_of(child):
                attribute_writes[value[0]].append((value[1], chain))
            if isinstance(child, ast.Call):
                chains[id(child)] = chain
                calls.append(child)
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.asname:
                        imports[alias.asname] = (alias.name, None)
                    else:
                        head = alias.name.split(".")[0]
                        imports[head] = (head, None)
            elif isinstance(child, ast.ImportFrom):
                base = child.module or ""
                if child.level:
                    anchor = package[: len(package) - (child.level - 1)]
                    base = ".".join([*anchor, *([child.module] if child.module else [])])
                for alias in child.names:
                    if alias.name != "*":
                        imports[alias.asname or alias.name] = (base, alias.name)
            stack.append((child, chain))
    module_assigns: dict[str, list[ast.expr]] = defaultdict(list)
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    module_assigns[target.id].append(statement.value)
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.value is not None
        ):
            module_assigns[statement.target.id].append(statement.value)
    return _Module(
        rel=rel, dotted=dotted, functions=functions, imports=imports, chains=chains, def_chains=def_chains, calls=calls,
        module_assigns=module_assigns, container_writes=container_writes,
        attribute_writes=attribute_writes,
    )


def _container_name(expr: ast.expr) -> str | None:
    """The name a container is read or written by: ``reg`` for ``reg`` and
    ``self.reg``, the first such operand of ``(reg or {})``."""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    if isinstance(expr, ast.BoolOp):
        for value in expr.values:
            name = _container_name(value)
            if name is not None:
                return name
    return None


def _container_writes_of(node: ast.AST) -> list[tuple[str, ast.expr]]:
    """``(container name, value)`` for each value *node* puts in a mapping.

    ``reg[k] = v``, ``reg.setdefault(k, v)``, and ``reg = {k: v, ...}`` /
    ``reg = {k: v for ...}`` (an annotated assignment too). A registry an
    analyzer fills with ``sym.id`` and reads back as an endpoint is judged
    through these: a container is matched by NAME in its module, which is how
    the registries are written (one name per role, passed as a parameter).
    """
    found: list[tuple[str, ast.expr]] = []
    if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Subscript):
                name = _container_name(target.value)
                if name is not None:
                    found.append((name, node.value))
                continue
            name = _container_name(target)
            if name is None:
                continue
            for value in _alternatives(node.value):
                if isinstance(value, ast.Dict):
                    found.extend((name, v) for v in value.values)
                elif isinstance(value, ast.DictComp):
                    found.append((name, value.value))
                elif isinstance(value, (ast.List, ast.Tuple)):
                    found.extend((name, v) for v in value.elts)
                elif isinstance(value, ast.ListComp):
                    found.append((name, value.elt))
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        name = _container_name(node.func.value)
        if name is None:
            return found
        if node.func.attr == "setdefault" and len(node.args) == 2:
            found.append((name, node.args[1]))
        elif node.func.attr == "append" and len(node.args) == 1:
            found.append((name, node.args[0]))
    return found


def _attribute_writes_of(node: ast.AST) -> list[tuple[str, ast.expr]]:
    """``(attribute name, value)`` for ``obj.attr = v`` and for a keyword a
    class is constructed with (a callee spelled with a capital):
    ``Emission(action_symbol_id=v)``. Matched by name in the module."""
    found: list[tuple[str, ast.expr]] = []
    if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        found.extend(
            (t.attr, node.value) for t in targets if isinstance(t, ast.Attribute)
        )
    elif isinstance(node, ast.Call) and (_callee_name(node) or "")[:1].isupper():
        found.extend(
            (k.arg, k.value) for k in node.keywords if k.arg is not None
        )
    return found


def _own_nodes(fn: _FuncDef) -> list[ast.AST]:
    """The nodes of *fn*'s body, not those of a function or lambda inside it."""
    found: list[ast.AST] = []
    stack: list[ast.AST] = list(fn.body)
    while stack:
        node = stack.pop()
        found.append(node)
        stack.extend(
            child for child in ast.iter_child_nodes(node)
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        )
    return found


#: A local's bindings: each assigned value, with its position when the
#: assignment unpacks a tuple (``dst, ref = external_target(...)``).
_Assigned = dict[str, list[tuple[ast.expr, "int | None"]]]


def _bindings(fn: _FuncDef) -> tuple[_Assigned, set[str]]:
    """``(assigned, otherwise_bound)`` for the locals of *fn* itself.

    ``assigned`` holds what an ``=`` / annotated ``=`` / ``:=`` writes.
    ``otherwise_bound`` names those a loop, a comprehension, a ``with``, an
    ``except`` or an augmented assignment binds: their value is computed, and
    a local bound both ways is reported as computed as well as followed.
    """
    assigned: _Assigned = defaultdict(list)
    other: set[str] = set()

    def target(node: ast.expr, value: ast.expr | None, index: int | None) -> None:
        if isinstance(node, ast.Name):
            if value is None:
                other.add(node.id)
            else:
                assigned[node.id].append((value, index))
        elif isinstance(node, (ast.Tuple, ast.List)):
            plain = index is None and value is not None and not any(
                isinstance(e, ast.Starred) for e in node.elts
            )
            for i, element in enumerate(node.elts):
                target(element, value if plain else None, i if plain else None)
        elif isinstance(node, ast.Starred):
            target(node.value, None, None)

    for node in _own_nodes(fn):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                target(t, node.value, None)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target(node.target, node.value, None)
        elif isinstance(node, ast.NamedExpr):
            target(node.target, node.value, None)
        elif isinstance(node, ast.AugAssign):
            target(node.target, None, None)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            _bind_loop_target(node, target)
        elif isinstance(node, ast.comprehension):
            target(node.target, None, None)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            target(node.optional_vars, None, None)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            other.add(node.name)
    return assigned, other


def _bind_loop_target(
    loop: "ast.For | ast.AsyncFor",
    target: "Callable[[ast.expr, ast.expr | None, int | None], None]",
) -> None:
    """Bind a ``for`` target to an ELEMENT of what it iterates.

    The element is written as a subscript of the iterable, so the walk reads
    it as a container read (:meth:`_EndpointWalk._read_container`):
    ``for src, label in pending`` binds ``src`` to part 0 of an element of
    ``pending``, and ``for k, v in reg.items()`` binds ``v`` to a value of
    ``reg``. Any other iterable leaves the element computed.
    """
    iterable = loop.iter
    method = (
        iterable.func.attr
        if isinstance(iterable, ast.Call) and isinstance(iterable.func, ast.Attribute)
        and not iterable.args else None
    )
    container = iterable
    if method in ("items", "values"):
        assert isinstance(iterable, ast.Call) and isinstance(iterable.func, ast.Attribute)
        container = iterable.func.value
    element = ast.copy_location(
        ast.Subscript(value=container, slice=ast.Constant(0), ctx=ast.Load()), loop,
    )
    node = loop.target
    if method == "items":
        if isinstance(node, ast.Tuple) and len(node.elts) == 2:
            target(node.elts[0], None, None)
            target(node.elts[1], element, None)
            return
        target(node, None, None)
        return
    if isinstance(node, ast.Name):
        target(node, element, None)
    elif isinstance(node, ast.Tuple) and all(isinstance(e, ast.Name) for e in node.elts):
        for i, part in enumerate(node.elts):
            target(part, element, i)
    else:
        target(node, None, None)


def _parameters(fn: _FuncDef) -> list[str]:
    a = fn.args
    names = [p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    names.extend(p.arg for p in (a.vararg, a.kwarg) if p is not None)
    return names


def _default_of(fn: _FuncDef, parameter: str) -> ast.expr | None:
    a = fn.args
    positional = [*a.posonlyargs, *a.args]
    first_default = len(positional) - len(a.defaults)
    for i, p in enumerate(positional):
        if p.arg == parameter and i >= first_default:
            return a.defaults[i - first_default]
    for p, default in zip(a.kwonlyargs, a.kw_defaults, strict=True):
        if p.arg == parameter:
            return default
    return None


def _bound_argument(
    fn: _FuncDef, call: ast.Call, parameter: str,
) -> tuple[ast.expr, bool] | None:
    """What *call* passes for *parameter*, and whether it is *fn*'s default.

    ``None`` when the call does not say: the argument rides ``*args`` or
    ``**kwargs``, or is omitted with no default.
    """
    for keyword in call.keywords:
        if keyword.arg == parameter:
            return keyword.value, False
    positional = [p.arg for p in (*fn.args.posonlyargs, *fn.args.args)]
    if parameter in positional:
        # ``self.f(x)`` binds ``x`` to f's second parameter.
        bound_self = (
            isinstance(call.func, ast.Attribute)
            and positional[0] in ("self", "cls")
        )
        index = positional.index(parameter) - (1 if bound_self else 0)
        if index < 0 or any(
            isinstance(a, ast.Starred) for a in call.args[: index + 1]
        ):
            return None
        if index < len(call.args):
            return call.args[index], False
    if any(keyword.arg is None for keyword in call.keywords):
        return None
    default = _default_of(fn, parameter)
    return (default, True) if default is not None else None


@dataclass(eq=False)
class _Ctx:
    """Where an expression is read: its module, the functions enclosing it
    (innermost first), and, inside a helper reached by a direct call, what that
    call passed for each of the helper's parameters (``None``: not bound)."""

    module: _Module
    chain: tuple[_FuncDef, ...]
    bound: dict[str, "tuple[ast.expr, _Ctx] | None"]


@dataclass(frozen=True)
class _Budget:
    """How many call levels a parameter, and a helper of another module, may
    still be followed through."""

    parameters: int = 1
    helpers: int = 1


class _EndpointWalk:
    """Follows every edge endpoint of the tree to the values it can hold.

    A value written by hand (an f-string, a ``+`` chain, a non-empty literal)
    is judged against the node grammar; a minting factory's result, a
    symbol's ``.id`` and the empty initializer pass; everything else is
    recorded with an :data:`UNJUDGED_REASONS` reason.
    """

    def __init__(self, modules: list[_Module]) -> None:
        self.modules = modules
        self.by_dotted = {m.dotted: m for m in modules}
        self.violations: dict[tuple[str, int], str] = {}
        self.unjudged: list[UnjudgedEndpoint] = []
        self._calls_by_name: dict[str, list[tuple[_Module, ast.Call]]] = defaultdict(list)
        for module in modules:
            for call in module.calls:
                name = _callee_name(call)
                if name is not None:
                    self._calls_by_name[name].append((module, call))
        self._bindings: dict[int, tuple[_Assigned, set[str]]] = {}
        self._active: set[int] = set()
        self._site = ("", 0, "")

    def run(self) -> None:
        for module in self.modules:
            for call in module.calls:
                for side, endpoint in _edge_endpoints(call):
                    self._site = (module.rel, call.lineno, side)
                    missed: dict[str, str] = {}
                    self._visit(
                        endpoint, _Ctx(module, module.chains[id(call)], {}),
                        _Budget(), set(), missed, None,
                    )
                    chain = module.chains[id(call)]
                    function = chain[0].name if chain else "<module>"
                    for reason, where in sorted(missed.items()):
                        self.unjudged.append(UnjudgedEndpoint(
                            module.rel, call.lineno, function, side, reason, where,
                        ))

    def _judge(self, expr: ast.expr, ctx: _Ctx) -> None:
        problem = _endpoint_problem(expr)
        if problem is None:
            return
        rel, line, side = self._site
        via = "" if (ctx.module.rel, expr.lineno) == (rel, line) else (
            f" (it reaches the edge at {rel}:{line})"
        )
        self.violations.setdefault((ctx.module.rel, expr.lineno), (
            f"edge_endpoint ({side}): {problem} — an endpoint is a node id: "
            "mint it (make_file_id, make_module_import_dst, edge_target."
            f"external_target) or take a symbol's id{via}"
        ))

    def _visit(
        self, expr: ast.expr, ctx: _Ctx, budget: _Budget,
        seen: set[tuple[int, int, int, int | None]], missed: dict[str, str],
        index: int | None,
    ) -> None:
        expr = _unwrap(expr)
        key = (id(expr), id(ctx.chain[0]) if ctx.chain else 0, id(ctx.bound), index)
        if key in seen:
            return
        seen.add(key)
        where = f"{ctx.module.rel}:{getattr(expr, 'lineno', 0)}"
        if isinstance(expr, (ast.IfExp, ast.BoolOp)):
            for alternative in _alternatives(expr):
                self._visit(alternative, ctx, budget, seen, missed, index)
            return
        if index is not None and isinstance(expr, ast.Tuple):
            if index < len(expr.elts) and not any(
                isinstance(e, ast.Starred) for e in expr.elts
            ):
                self._visit(expr.elts[index], ctx, budget, seen, missed, None)
                return
            missed.setdefault("computed", where)
            return
        if isinstance(expr, ast.Name):
            self._visit_name(expr, ctx, budget, seen, missed, index)
            return
        if isinstance(expr, ast.Call):
            self._visit_call(expr, ctx, budget, seen, missed, index)
            return
        if isinstance(expr, ast.Subscript):
            inner = _unwrap(expr.value)
            if isinstance(inner, ast.Call) and _callee_name(inner) in _ENDPOINT_FACTORIES:
                return
            if self._read_container(inner, ctx, budget, seen, missed, index):
                return
            missed.setdefault("computed", f"{where} {ast.unparse(expr)[:60]}")
            return
        if isinstance(expr, ast.Attribute) and expr.attr not in _NODE_ID_ATTRIBUTES:
            writes = ctx.module.attribute_writes.get(expr.attr)
            if writes:
                for value, chain in writes:
                    self._visit(
                        value, _Ctx(ctx.module, chain, {}), budget, seen, missed, index,
                    )
                return
        if index is not None:
            missed.setdefault("computed", where)
            return
        if isinstance(expr, ast.Constant):
            if isinstance(expr.value, str) and expr.value:
                self._judge(expr, ctx)
            return
        if isinstance(expr, ast.JoinedStr) or (
            isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add)
        ):
            self._judge(expr, ctx)
            return
        if isinstance(expr, ast.Attribute) and expr.attr in _NODE_ID_ATTRIBUTES:
            return
        missed.setdefault("computed", f"{where} {ast.unparse(expr)[:60]}")

    def _read_container(
        self, container: ast.expr, ctx: _Ctx, budget: _Budget,
        seen: set[tuple[int, int, int, int | None]], missed: dict[str, str],
        index: int | None,
    ) -> bool:
        """Visit every value the module writes into *container*; False when
        it writes none (the read stays computed). A container that is a
        parameter of a helper reached by a direct call is the container the
        call passed (``local_id_registry`` for ``symbol_registry``)."""
        if isinstance(container, ast.Name) and ctx.bound.get(container.id) is not None:
            passed = ctx.bound[container.id]
            assert passed is not None
            container, ctx = passed
        name = _container_name(container)
        writes = ctx.module.container_writes.get(name or "")
        if not writes:
            return False
        for value, chain in writes:
            self._visit(value, _Ctx(ctx.module, chain, {}), budget, seen, missed, index)
        return True

    def _visit_name(
        self, expr: ast.Name, ctx: _Ctx, budget: _Budget,
        seen: set[tuple[int, int, int, int | None]], missed: dict[str, str],
        index: int | None,
    ) -> None:
        name = expr.id
        if name in ctx.bound:
            bound = ctx.bound[name]
            if bound is None:
                missed.setdefault(
                    "argument_not_bound", f"{ctx.module.rel}:{expr.lineno} {name}",
                )
                return
            self._visit(bound[0], bound[1], budget, seen, missed, index)
            return
        for depth, fn in enumerate(ctx.chain):
            assigned, other = self._bindings_of(fn)
            outer = _Ctx(ctx.module, ctx.chain[depth:], ctx.bound if depth == 0 else {})
            hit = False
            for value, position in assigned.get(name, ()):
                hit = True
                if position is not None and index is not None:
                    missed.setdefault("computed", f"{ctx.module.rel}:{value.lineno} {name}")
                    continue
                self._visit(
                    value, outer, budget, seen, missed,
                    position if position is not None else index,
                )
            if name in other:
                hit = True
                missed.setdefault("computed", f"{ctx.module.rel}:{expr.lineno} {name}")
            if name in _parameters(fn):
                hit = True
                self._follow_parameter(ctx.module, fn, name, budget, seen, missed, index)
            if hit:
                return
        values = ctx.module.module_assigns.get(name)
        if values:
            for value in values:
                self._visit(value, _Ctx(ctx.module, (), {}), budget, seen, missed, index)
            return
        missed.setdefault("computed", f"{ctx.module.rel}:{expr.lineno} {name}")

    def _bindings_of(self, fn: _FuncDef) -> tuple[_Assigned, set[str]]:
        found = self._bindings.get(id(fn))
        if found is None:
            found = self._bindings[id(fn)] = _bindings(fn)
        return found

    def _follow_parameter(
        self, module: _Module, fn: _FuncDef, parameter: str, budget: _Budget,
        seen: set[tuple[int, int, int, int | None]], missed: dict[str, str],
        index: int | None,
    ) -> None:
        where = f"{module.rel}:{fn.lineno} {fn.name}({parameter})"
        if budget.parameters == 0:
            missed.setdefault("parameter_beyond_one_call", where)
            return
        sites = self._call_sites(module, fn)
        if not sites:
            missed.setdefault("parameter_without_call_site", where)
            return
        deeper = _Budget(budget.parameters - 1, budget.helpers)
        for caller, call in sites:
            argument = _bound_argument(fn, call, parameter)
            if argument is None:
                missed.setdefault("argument_not_bound", f"{caller.rel}:{call.lineno} {where}")
                continue
            value, is_default = argument
            ctx = (
                _Ctx(module, (), {}) if is_default
                else _Ctx(caller, caller.chains[id(call)], {})
            )
            self._visit(value, ctx, deeper, seen, missed, index)

    def _call_sites(self, module: _Module, fn: _FuncDef) -> list[tuple[_Module, ast.Call]]:
        """The calls of *fn*: by name in its own module (as the helper hop
        reads a same-module helper), through an import in another."""
        sites: list[tuple[_Module, ast.Call]] = []
        for caller, call in self._calls_by_name.get(fn.name, ()):
            if caller is module:
                # ``f(...)`` or ``self.f(...)`` / ``cls.f(...)``; never
                # ``seen.add(x)`` for a function named ``add``.
                func = call.func
                if isinstance(func, ast.Name) or (
                    isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id in ("self", "cls")
                ):
                    sites.append((caller, call))
                continue
            target = self._callee(caller, call)
            if target is not None and any(f is fn for f in target[1]):
                sites.append((caller, call))
        return sites

    def _visit_call(
        self, expr: ast.Call, ctx: _Ctx, budget: _Budget,
        seen: set[tuple[int, int, int, int | None]], missed: dict[str, str],
        index: int | None,
    ) -> None:
        where = f"{ctx.module.rel}:{expr.lineno} {ast.unparse(expr.func)[:60]}(...)"
        if _callee_name(expr) in _ENDPOINT_FACTORIES:
            return
        if (
            isinstance(expr.func, ast.Attribute) and expr.func.attr == "get"
            and expr.args and len(expr.args) <= 2
            and self._read_container(expr.func.value, ctx, budget, seen, missed, index)
        ):
            # ``reg.get(k, default)``: the registry's values, and the default.
            for default in expr.args[1:]:
                self._visit(default, ctx, budget, seen, missed, index)
            return
        target = self._callee(ctx.module, expr)
        if target is None:
            reason = "helper_not_located" if isinstance(expr.func, ast.Name) else "computed"
            missed.setdefault(reason, where)
            return
        owner, definitions = target
        if owner is not ctx.module:
            if budget.helpers == 0:
                missed.setdefault("helper_beyond_one_level", where)
                return
            budget = _Budget(budget.parameters, budget.helpers - 1)
        for fn in definitions:
            self._read_returns(expr, ctx, owner, fn, budget, seen, missed, index, where)

    def _read_returns(
        self, expr: ast.Call, ctx: _Ctx, owner: _Module, fn: _FuncDef,
        budget: _Budget, seen: set[tuple[int, int, int, int | None]],
        missed: dict[str, str], index: int | None, where: str,
    ) -> None:
        """Visit each value *fn* returns, its parameters bound to what the
        call *expr* passes."""
        bound: dict[str, tuple[ast.expr, _Ctx] | None] = {}
        for parameter in _parameters(fn):
            argument = _bound_argument(fn, expr, parameter)
            if argument is None:
                bound[parameter] = None
            else:
                value, is_default = argument
                bound[parameter] = (value, _Ctx(owner, (), {}) if is_default else ctx)
        if id(fn) in self._active:
            return  # a helper that calls itself: its other returns are being read
        inner = _Ctx(owner, owner.def_chains[id(fn)], bound)
        returns = [
            node.value for node in _own_nodes(fn)
            if isinstance(node, ast.Return) and node.value is not None
        ]
        if not returns:
            missed.setdefault("computed", f"{where}: returns no value")
        self._active.add(id(fn))
        try:
            for value in returns:
                self._visit(value, inner, budget, seen, missed, index)
        finally:
            self._active.discard(id(fn))

    def _callee(
        self, module: _Module, call: ast.Call,
    ) -> tuple[_Module, list[_FuncDef]] | None:
        """The module and the definitions *call* can call, when the tree
        defines them."""
        func = call.func
        if isinstance(func, ast.Name):
            if func.id in module.functions:
                return module, module.functions[func.id]
            imported = module.imports.get(func.id)
            return None if imported is None else self._imported_function(*imported)
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id in module.imports:
                owner = self._imported_module(*module.imports[func.value.id])
                if owner is not None and func.attr in owner.functions:
                    return owner, owner.functions[func.attr]
            if func.attr in module.functions:
                return module, module.functions[func.attr]
        return None

    def _imported_function(
        self, dotted: str, name: str | None,
    ) -> tuple[_Module, list[_FuncDef]] | None:
        owner = self.by_dotted.get(dotted)
        if owner is None or name is None:
            return None
        if name in owner.functions:
            return owner, owner.functions[name]
        # One re-export: ``from .base import make_symbol_id`` in a package.
        again = owner.imports.get(name)
        if again is not None and again[1] is not None:
            source = self.by_dotted.get(again[0])
            if source is not None and again[1] in source.functions:
                return source, source.functions[again[1]]
        return None

    def _imported_module(self, dotted: str, name: str | None) -> _Module | None:
        return self.by_dotted.get(dotted if name is None else f"{dotted}.{name}")
