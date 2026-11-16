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
rediscovered.

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

EDGE IDS GET THEIR OWN RULE. An ``Edge.id`` is a content digest,
``edge:sha256:<16hex>``, not a node id, so ``Edge(id=...)`` is judged, through
the same three hops, against that shape (:func:`edge_template_problem`). For
both constructors, an id built by a ``+`` chain is reported as
``concatenated_id``, because a concatenation cannot be checked against
either grammar.

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
never read by its parameter NAMES: the websocket linker's helper takes a
``kind`` it appends to the name slot, and writes ``"function"`` in the kind
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
KNOWN_KIND_SLOT_HOLES: dict[tuple[str, str, str], str] = {
    (
        "packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/twig.py",
        "function_call", "call_site",
    ): (
        "the word is also the kind input of make_doc_stable_id, so correcting "
        "it changes the stable id of every twig function call (measured: 2 of "
        "2 on a two-call fixture); it goes with the next stable-id scheme bump"
    ),
}

#: The constructors whose ``id=`` slot this rule governs, and the grammar each
#: one answers to. An ``Edge.id`` is ``edge:sha256:<16hex>``, a content digest,
#: so enforcing the five-slot node grammar on it would flag every edge. The two
#: ids are counterparts in ROLE (per-instance identity) and not in SHAPE, so
#: they get two rules.
_NODE_ID_CONSTRUCTORS = frozenset({"Symbol"})
_EDGE_ID_CONSTRUCTORS = frozenset({"Edge"})

#: ``Edge.id``'s canonical shape, minted at ``ir.py``'s ``edge:sha256:{hash}``
#: and copied into per-language ``_make_edge_id`` helpers.
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


def _problems_in_file(path: Path) -> list[_Finding]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
        return []
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
) -> list[str]:
    """Return one ``path:line — problem`` string per violation, sorted.

    ``known_kind_slot_holes`` defaults to :data:`KNOWN_KIND_SLOT_HOLES`. A
    declared mismatch is not reported; a declaration whose file is in the tree
    and holds no such mismatch is.
    """
    holes = KNOWN_KIND_SLOT_HOLES if known_kind_slot_holes is None else known_kind_slot_holes
    matched: set[tuple[str, str, str]] = set()
    offenders: list[str] = []
    for src_root in sorted(repo_root.glob("packages/*/src")):
        for path in sorted(src_root.rglob("*.py")):
            rel = path.relative_to(repo_root).as_posix()
            for lineno, problem, words in _problems_in_file(path):
                if words is not None and (rel, *words) in holes:
                    matched.add((rel, *words))
                    continue
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
    return offenders
