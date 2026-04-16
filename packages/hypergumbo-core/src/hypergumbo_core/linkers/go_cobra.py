# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: Go spf13/cobra CLI command dispatch.

Bridges ``cobra.Command`` struct literals to their handler functions.
The ``github.com/spf13/cobra`` library dispatches CLI subcommand
handlers via callback fields (``RunE``, ``PreRunE``, etc.) on the
``cobra.Command`` struct. Runtime dispatch happens inside
``cobra.Command.Execute()``, which the analyzer cannot reach without
an explicit edge from the construction site to the handler.

Supported handler fields
------------------------
- ``Run``, ``RunE``
- ``PreRun``, ``PreRunE``
- ``PostRun``, ``PostRunE``
- ``PersistentPreRun``, ``PersistentPreRunE``
- ``PersistentPostRun``, ``PersistentPostRunE``

Matching strategy
-----------------
1. Scan ``.go`` files for the literal ``cobra.Command{`` (or the
   pointer form ``&cobra.Command{``) — this is the idiom used by every
   cobra-based CLI. Only files that also import
   ``github.com/spf13/cobra`` are considered.
2. Inside the struct body, capture lines of the form
   ``FieldName: identifier`` where ``FieldName`` is one of the handler
   fields above and ``identifier`` is a Go identifier (optionally
   package-qualified like ``pkg.runCmd``). Lines whose value starts
   with ``func`` are ignored — those are inline function literals,
   already reachable via containment.
3. For each captured ``(field, identifier)`` pair, resolve
   ``identifier`` to a Symbol via ``ctx.find_symbols_by_name`` and emit
   a ``dispatches_to`` edge from the enclosing function to the handler.
   When the cobra.Command literal is at package level (inside a
   ``var … = &cobra.Command{…}`` declaration), the linker falls back
   to the enclosing variable symbol as the edge source.

Why regex and not tree-sitter
-----------------------------
Cobra struct literals are a narrow syntactic pattern — we only need to
match the ``FieldName: identifier`` shape inside the brace pair that
follows ``cobra.Command{``. A tree-sitter query would walk the full
composite-literal syntax, which is overkill for a five-line regex with
existing sibling linkers (``otp.py``, ``http.py``,
``database_query.py``) establishing the convention. The regex is
intentionally conservative: it skips function literals and does not
attempt to track brace nesting beyond the line-local match.

Why ``dispatches_to`` edges
---------------------------
The handler is NOT directly called from the construction site. Cobra
calls it later from within ``Execute()``. ``dispatches_to`` is the
schema-correct edge type for "runtime dispatch picks one of these at
call time" — the same edge type used by the decorator-dispatch linker
for registry-based dispatch. BFS over ``dispatches_to`` is how
dead-code-maybe's forward slice crosses the framework boundary.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, PASS_VERSION, make_pass_id
from .registry import LinkerActivation, LinkerContext, LinkerResult, register_linker

PASS_ID = make_pass_id("go-cobra-linker")

# Handler field names on cobra.Command that take a function reference.
# Listed explicitly (rather than a regex alternation inside a larger
# regex) so the match group captures the exact field name for
# edge metadata.
_HANDLER_FIELDS = (
    "Run",
    "RunE",
    "PreRun",
    "PreRunE",
    "PostRun",
    "PostRunE",
    "PersistentPreRun",
    "PersistentPreRunE",
    "PersistentPostRun",
    "PersistentPostRunE",
)

# Anchor: match both ``cobra.Command{`` and ``&cobra.Command{``.
# Used only to decide whether a file is a cobra client; the handler
# match below is brace-agnostic and runs over the full file.  Compiled
# as a bytes pattern because callers pass raw file contents.
_COBRA_COMMAND_ANCHOR = re.compile(rb"\bcobra\.Command\s*\{")

# Matches ``FieldName: identifier`` where FieldName is one of the
# handler fields and identifier is a Go identifier (optionally
# package-qualified). A trailing comma or close-brace must follow so
# we don't accidentally match method-call chains. Lines whose value is
# ``func(`` are ignored inside the caller, not the regex, so we can
# still log them if debugging.
_FIELD_ASSIGN_PATTERN = re.compile(
    rb"\b(?P<field>"
    + "|".join(_HANDLER_FIELDS).encode()
    + rb")\s*:\s*"
    rb"(?P<handler>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)"
    rb"\s*(?:,|\}|$)",
)

_COBRA_IMPORT_MARKER = re.compile(
    rb'"github\.com/spf13/cobra"',
)


def _file_imports_cobra(source: bytes) -> bool:
    """Return True if *source* imports ``github.com/spf13/cobra``."""
    return _COBRA_IMPORT_MARKER.search(source) is not None


def _find_handler_assignments(
    source: bytes,
) -> list[tuple[str, str, int]]:
    """Locate ``FieldName: handler`` inside cobra.Command literals.

    Returns a list of ``(field, handler_identifier, line_number)``
    tuples. Only yields sites that sit in a file containing at least
    one ``cobra.Command{`` anchor (caller ensures this); does not
    verify the match is textually inside a matching brace pair —
    cobra.Command struct literal is idiomatic and the field names are
    distinctive enough that false positives are rare, and would be
    filtered at resolution time anyway (unknown handler → no edge).

    A handler value of ``func`` (an inline function literal) or
    ``nil`` is skipped: the first is already reachable via
    containment, and the second is a no-op registration.
    """
    results: list[tuple[str, str, int]] = []
    for m in _FIELD_ASSIGN_PATTERN.finditer(source):
        # Line number (1-based) of the match start.
        line = source.count(b"\n", 0, m.start()) + 1
        field = m.group("field").decode("utf-8", errors="replace")
        handler = m.group("handler").decode("utf-8", errors="replace")
        if not handler or handler in {"nil", "func"}:
            continue
        results.append((field, handler, line))
    return results


def _find_go_files(repo_root: Path):
    """Yield all ``.go`` files under the repo, respecting default excludes."""
    yield from find_files(
        repo_root,
        patterns=["*.go"],
    )


@register_linker(
    "go_cobra",
    priority=45,
    description="Go spf13/cobra CLI command dispatch (RunE, PreRunE, etc.)",
    activation=LinkerActivation(always=True),
)
def go_cobra_linker(ctx: LinkerContext) -> LinkerResult:
    """Emit ``dispatches_to`` edges from cobra.Command sites to handlers.

    No-ops when the repo has no Go files. Each emitted edge is
    deduplicated by (src, dst, edge_type) so that multiple cobra.Command
    literals referencing the same handler from the same enclosing
    function do not produce duplicate edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if "go" not in ctx.detected_languages:
        run.duration_ms = 0
        return LinkerResult(run=run)

    edges: list[Edge] = []
    seen_pairs: set[tuple[str, str]] = set()
    files_analyzed = 0
    files_skipped = 0

    for file_path in _find_go_files(ctx.repo_root):
        try:
            source = file_path.read_bytes()
        except (OSError, IOError):  # pragma: no cover
            files_skipped += 1
            continue

        # Cheap pre-filter: both the anchor "cobra.Command{" and the
        # spf13/cobra import must be present. Skip otherwise.
        if not _COBRA_COMMAND_ANCHOR.search(source):
            files_analyzed += 1
            continue
        if not _file_imports_cobra(source):
            files_analyzed += 1
            continue

        assignments = _find_handler_assignments(source)
        if not assignments:
            files_analyzed += 1
            continue

        for field, handler_name, line in assignments:
            # Resolve handler. Try the bare name first; if the
            # analyzer records receiver-qualified method names
            # (``Type.Method``), strip the package prefix and retry.
            candidates = ctx.find_symbols_by_name(handler_name)
            if not candidates and "." in handler_name:
                candidates = ctx.find_symbols_by_name(
                    handler_name.split(".", 1)[-1],
                )
            if not candidates:
                continue

            # Find the enclosing function. If the cobra.Command literal
            # is outside any function (e.g., a package-level var init),
            # fall back to the enclosing variable symbol — the most
            # common Go cobra pattern is ``var rootCmd = &cobra.Command{…}``.
            enclosing = ctx.find_enclosing_symbol(str(file_path), line)
            if enclosing is None:
                enclosing = ctx.find_enclosing_symbol(
                    str(file_path), line, kinds=("variable",),
                )
            if enclosing is None:
                continue

            for handler_sym in candidates:
                pair = (enclosing.id, handler_sym.id)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                edges.append(
                    Edge.create(
                        src=enclosing.id,
                        dst=handler_sym.id,
                        edge_type="dispatches_to",
                        confidence=0.85,
                        line=line,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="go_cobra_dispatch",
                        meta={
                            "cobra_field": field,
                            "handler_name": handler_name,
                        },
                    ),
                )

        files_analyzed += 1

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(run=run, edges=edges)
