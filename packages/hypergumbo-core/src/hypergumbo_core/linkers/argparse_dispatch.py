# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: Python argparse subcommand dispatch.

Bridges ``parser.set_defaults(func=handler)`` registration sites to
their handler functions. argparse is the stdlib CLI framework: the
idiomatic subcommand pattern registers a handler on each subparser via
``set_defaults`` and dispatches at runtime with ``args.func(args)``.
The dispatch is dynamic — the analyzer records the registration only as
a ``references`` edge, which is structurally honest (the name is
referenced, not called) but leaves every argparse-dispatched handler
without a call-shaped edge from production structure.

INV-zuhig measured the consequence on hypergumbo's own self-proof: all
18 claims declare ``cmd_*`` handlers as ``start_at: callee`` taint
sources, the only in-artifact edges into them were ``references`` and
``contains``, and sources mint on call-shaped edges — so under
``analysis_scope: shipped_artifact`` the sources minted ZERO flows and
every ``confirmed_with_caveats`` verdict was vacuous on the taint side.
This linker is the recall fix: it repairs the graph every consumer
sees (taint, slices, dead-code BFS) for every argparse repo, rather
than special-casing the self-proof.

Matching strategy
-----------------
1. Scan non-test ``.py`` files for a ``.set_defaults(`` call in a file
   that also mentions ``argparse`` — the same two-anchor pre-filter
   shape as ``go_cobra.py`` (the analogous CLI-dispatch linker in Go).
2. Inside each ``set_defaults(...)`` argument span (paren-balanced,
   multi-line), capture ``kwarg=identifier`` pairs. The kwarg name is
   deliberately NOT restricted to ``func``: unlike cobra's fixed
   ``RunE`` field set, argparse's ``set_defaults`` key is user-chosen
   convention (``func`` dominates, but ``handler=`` / ``command=`` /
   ``cls=`` all appear in the wild). Literal values (``True`` /
   ``False`` / ``None`` / numbers / strings), lambdas, and call
   expressions (``func=make_handler()``) are skipped — only a bare
   (possibly dotted) identifier names a statically-known handler.
3. Resolve the identifier via ``ctx.find_symbols_by_name`` (last
   component for dotted names), keep only callable symbol kinds
   (function / method / class — a class registered as a handler is
   instantiated-then-called), and emit a ``dispatches_to`` edge from
   the registration site's enclosing symbol to the handler.

Why regex and not tree-sitter
-----------------------------
Same rationale as go_cobra.py: ``set_defaults(kwarg=identifier)`` is a
narrow syntactic pattern, and resolution-time filtering (unknown or
non-callable handler → no edge) bounds the false-positive cost of the
occasional non-argparse ``set_defaults`` in an argparse-importing file.

Why ``dispatches_to`` edges
---------------------------
The handler is NOT called at the registration site — argparse calls it
later, from ``args.func(args)``. ``dispatches_to`` is the family
convention for "runtime dispatch picks this callee" (go_cobra,
decorator_dispatch, django_orm_dispatch, ...). Taint treats
``dispatches_to`` as call-shaped (``TAINT_CALL_EDGE_TYPES``, INV-zuhig),
so callee-seeded sources mint from these edges.
"""

from __future__ import annotations

import re
import time

from ..discovery import find_non_test_files
from ..ir import AnalysisRun, Edge, PASS_VERSION, make_pass_id
from .registry import LinkerActivation, LinkerContext, LinkerResult, register_linker

PASS_ID = make_pass_id("argparse-dispatch-linker")

# Anchor: a set_defaults method call. Used with the argparse marker
# below as a cheap two-part pre-filter before any parsing happens.
_SET_DEFAULTS_ANCHOR = re.compile(rb"\.set_defaults\s*\(")

# The file must mention argparse somewhere (import line, annotation,
# ...). optparse's OptionParser has set_defaults too, but its dispatch
# idiom differs; scoping to argparse keeps the precision story simple
# and covers the dominant framework.
_ARGPARSE_MARKER = re.compile(rb"\bargparse\b")

# ``kwarg=identifier`` inside the argument span. The lookahead requires
# a terminator (comma, close-paren, or newline) so call expressions
# (``func=make_handler()``) and lambdas (``func=lambda args: 0``) never
# match — their value is followed by ``(`` or a bare identifier.
_KWARG_PATTERN = re.compile(
    rb"(?<![\w.])(?P<kw>[A-Za-z_]\w*)\s*=\s*"
    rb"(?P<val>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)"
    rb"(?=\s*(?:[,)\n]|$))",
)

# Identifier-shaped values that are never dispatch targets.
_NON_HANDLER_VALUES = frozenset({"True", "False", "None", "lambda"})

# Symbol kinds a handler may resolve to. A ``variable`` match
# (``set_defaults(level=DEFAULT_LEVEL)``) is a configuration default,
# not a dispatch target; a ``class`` is kept because handler classes
# (``set_defaults(cls=Command)``) are instantiated and run.
_HANDLER_SYMBOL_KINDS = frozenset({"function", "method", "class"})

# Upper bound on one set_defaults argument span. Real registrations are
# a few lines; the cap keeps the paren scan linear on pathological
# files (e.g. a set_defaults anchor inside a giant generated literal).
_MAX_SPAN_BYTES = 8192


def _file_mentions_argparse(source: bytes) -> bool:
    """Return True if *source* mentions ``argparse`` anywhere."""
    return _ARGPARSE_MARKER.search(source) is not None


def _argument_span(source: bytes, open_paren: int) -> tuple[int, int]:
    """Return (start, end) byte offsets of the ``(...)`` argument span.

    ``open_paren`` indexes the opening paren. The scan is paren-balanced
    so multi-line calls and nested parens (inside string defaults, say)
    resolve to the matching close paren. An unbalanced span — EOF inside
    the call, or a pathological quote-embedded paren — caps at
    ``_MAX_SPAN_BYTES`` past the anchor; the kwarg pattern then simply
    runs over the partial span, which can only lose recall, never
    invent a pair.
    """
    depth = 0
    limit = min(len(source), open_paren + _MAX_SPAN_BYTES)
    for i in range(open_paren, limit):
        c = source[i:i + 1]
        if c == b"(":
            depth += 1
        elif c == b")":
            depth -= 1
            if depth == 0:
                return open_paren + 1, i
    return open_paren + 1, limit


def _find_handler_registrations(
    source: bytes,
) -> list[tuple[str, str, int]]:
    """Locate ``kwarg=identifier`` pairs inside set_defaults() calls.

    Returns ``(kwarg, handler_identifier, line_number)`` tuples in
    source order. Literal-valued kwargs never match the identifier
    pattern; ``True``/``False``/``None``/``lambda`` are filtered
    explicitly.
    """
    results: list[tuple[str, str, int]] = []
    for anchor in _SET_DEFAULTS_ANCHOR.finditer(source):
        open_paren = anchor.end() - 1
        span_start, span_end = _argument_span(source, open_paren)
        span = source[span_start:span_end]
        for m in _KWARG_PATTERN.finditer(span):
            value = m.group("val").decode("utf-8", errors="replace")
            if value.split(".", 1)[0] in _NON_HANDLER_VALUES:
                continue
            kwarg = m.group("kw").decode("utf-8", errors="replace")
            line = source.count(b"\n", 0, span_start + m.start("val")) + 1
            results.append((kwarg, value, line))
    return results


@register_linker(
    "argparse-dispatch-linker",
    priority=45,
    description="Python argparse subcommand dispatch (set_defaults(func=...))",
    activation=LinkerActivation(always=True),
    # CNF: argparse is Python-only.
    depends_on=[["python"]],
)
def argparse_dispatch_linker(ctx: LinkerContext) -> LinkerResult:
    """Emit ``dispatches_to`` edges from set_defaults sites to handlers.

    No-ops when the repo has no Python detected. Edges are deduplicated
    by (src, dst) so re-registrations of the same handler from the same
    enclosing function collapse to one edge.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if "python" not in ctx.detected_languages:
        run.duration_ms = 0
        return LinkerResult(run=run)

    edges: list[Edge] = []
    seen_pairs: set[tuple[str, str]] = set()
    files_analyzed = 0
    files_skipped = 0

    for file_path in find_non_test_files(ctx.repo_root, patterns=["*.py"]):
        try:
            source = file_path.read_bytes()
        except (OSError, IOError) as e:  # pragma: no cover
            files_skipped += 1
            run.record_failed_file(
                str(file_path.relative_to(ctx.repo_root)),
                f"{type(e).__name__}: {e}",
            )
            continue

        files_analyzed += 1
        if not _SET_DEFAULTS_ANCHOR.search(source):
            continue
        if not _file_mentions_argparse(source):
            continue

        for kwarg, handler_name, line in _find_handler_registrations(source):
            candidates = ctx.find_symbols_by_name(handler_name)
            if not candidates and "." in handler_name:
                candidates = ctx.find_symbols_by_name(
                    handler_name.rsplit(".", 1)[-1],
                )
            candidates = [
                s for s in candidates if s.kind in _HANDLER_SYMBOL_KINDS
            ]
            if not candidates:
                continue

            enclosing = ctx.find_enclosing_symbol(str(file_path), line)
            if enclosing is None:
                continue

            # Same shape as go_cobra's INV-zuhub note: a short-name
            # collision means the dispatch target is unresolvable from
            # the registration alone; every edge in the batch is a
            # bare-name fallback.
            is_fallback = len(candidates) > 1
            for handler_sym in candidates:
                pair = (enclosing.id, handler_sym.id)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                confidence = 0.5 if is_fallback else 0.85
                edge_meta: dict[str, object] = {
                    "argparse_kwarg": kwarg,
                    "handler_name": handler_name,
                    "framework_dispatch": "argparse",
                }
                if is_fallback:
                    edge_meta["disambiguation_fallback"] = True
                edges.append(
                    Edge.create(
                        src=enclosing.id,
                        dst=handler_sym.id,
                        edge_type="dispatches_to",
                        confidence=confidence,
                        line=line,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="ast_call_direct",
                        meta=edge_meta,
                        derived_from=[enclosing.id, handler_sym.id],
                    ),
                )

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(run=run, edges=edges)
