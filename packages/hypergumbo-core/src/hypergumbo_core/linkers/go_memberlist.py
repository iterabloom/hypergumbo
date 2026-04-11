# SPDX-License-Identifier: AGPL-3.0-or-later
"""Go hashicorp/memberlist cluster delegate callback linker.

Bridges ``memberlist.Create(...)`` cluster construction sites to the
delegate callback methods that the memberlist event loop invokes.

Context
-------
``github.com/hashicorp/memberlist`` implements gossip-based cluster
membership used by alertmanager, consul, nomad, serf, and vault.
Delegates are registered via ``memberlist.Config.Delegate =
myDelegate`` (and the sibling ``EventDelegate``, ``ConflictDelegate``,
``MergeDelegate``, ``AliveDelegate``, ``PingDelegate`` fields).
Memberlist invokes methods on those delegates at runtime —
``NotifyJoin``, ``NotifyLeave``, ``NotifyUpdate``, ``NotifyMsg``,
``GetBroadcasts``, ``LocalState``, ``MergeRemoteState``,
``NotifyConflict``, ``NotifyMerge``, ``NotifyAlive``,
``NotifyPingComplete``, and ``AckPayload``. Without an explicit
linker edge, these methods look dead to dead-code-maybe because no
textual call site reaches them.

Matching strategy
-----------------
1. Scan ``.go`` files that import ``github.com/hashicorp/memberlist``.
2. Find the anchor call site — any function that calls
   ``memberlist.Create(`` on its own. That function is taken as the
   dispatch source because it is the concrete construction point
   that causes the runtime to start invoking the delegate methods.
3. Find any method symbol whose bare name matches one of the
   canonical delegate names AND whose defining file imports
   memberlist. Treat those as dispatch targets.
4. Emit ``dispatches_to`` edges from each anchor function to each
   matched delegate method. Dedup by ``(src, dst)``.

Why name-based detection
------------------------
The twelve canonical delegate methods have distinctive names that
essentially never collide with user code in a repository that also
imports memberlist. A cheap name match plus the import gate is
sufficient for V1. A signature check would be stricter but requires
type info the Go analyzer doesn't currently surface to linkers.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, PASS_VERSION, make_pass_id
from .registry import LinkerActivation, LinkerContext, LinkerResult, register_linker

PASS_ID = make_pass_id("go-memberlist-linker")

# Canonical memberlist delegate method names. If a Go method bearing
# one of these names is defined in a file that also imports
# ``github.com/hashicorp/memberlist``, we treat it as a delegate
# callback for the memberlist runtime.
_DELEGATE_METHOD_NAMES = frozenset({
    # core Delegate interface
    "NotifyMsg",
    "GetBroadcasts",
    "LocalState",
    "MergeRemoteState",
    # EventDelegate interface
    "NotifyJoin",
    "NotifyLeave",
    "NotifyUpdate",
    # ConflictDelegate / MergeDelegate / AliveDelegate / PingDelegate
    "NotifyConflict",
    "NotifyMerge",
    "NotifyAlive",
    "NotifyPingComplete",
    "AckPayload",
})

_MEMBERLIST_IMPORT_MARKER = re.compile(
    rb'"github\.com/hashicorp/memberlist"',
)

_MEMBERLIST_CREATE_ANCHOR = re.compile(
    rb"\bmemberlist\.Create\s*\(",
)


def _file_imports_memberlist(source: bytes) -> bool:
    """Return True if *source* imports hashicorp/memberlist."""
    return _MEMBERLIST_IMPORT_MARKER.search(source) is not None


def _find_memberlist_create_lines(source: bytes) -> list[int]:
    """Return 1-based line numbers of ``memberlist.Create(`` occurrences."""
    lines: list[int] = []
    for m in _MEMBERLIST_CREATE_ANCHOR.finditer(source):
        lines.append(source.count(b"\n", 0, m.start()) + 1)
    return lines


def _collect_memberlist_files(
    repo_root: Path,
) -> dict[Path, bytes]:
    """Return a {file_path: source_bytes} map for .go files importing memberlist."""
    result: dict[Path, bytes] = {}
    for p in find_files(repo_root, patterns=["*.go"]):
        try:
            data = p.read_bytes()
        except (OSError, IOError):  # pragma: no cover
            continue
        if _file_imports_memberlist(data):
            result[p] = data
    return result


def _short_name(symbol_name: str) -> str:
    """Return the last component of a possibly-qualified symbol name."""
    return symbol_name.rsplit(".", 1)[-1] if "." in symbol_name else symbol_name


@register_linker(
    "go_memberlist",
    priority=45,
    description="Go hashicorp/memberlist cluster delegate callbacks",
    activation=LinkerActivation(always=True),
)
def go_memberlist_linker(ctx: LinkerContext) -> LinkerResult:
    """Emit ``dispatches_to`` edges from memberlist.Create anchors to delegates.

    No-op when the repo is not Go or when no ``.go`` file imports
    memberlist. When a delegate method is found but no
    ``memberlist.Create(`` anchor exists in the same file, fall back
    to any other in-file function symbol as the anchor — the goal is
    that dead-code-maybe's BFS reaches the delegate, not to pick the
    semantically "most correct" anchor.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if "go" not in ctx.detected_languages:
        run.duration_ms = 0
        return LinkerResult(run=run)

    memberlist_files = _collect_memberlist_files(ctx.repo_root)
    if not memberlist_files:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LinkerResult(run=run)

    # Build a set of paths that import memberlist, for cheap lookups.
    memberlist_paths: set[str] = {str(p) for p in memberlist_files}

    # Find delegate method symbols defined in any memberlist-importing
    # file. A delegate method is: kind in {method, function} AND
    # short name in _DELEGATE_METHOD_NAMES AND path imports memberlist.
    delegate_symbols = [
        sym
        for sym in ctx.symbols
        if sym.kind in ("method", "function")
        and _short_name(sym.name) in _DELEGATE_METHOD_NAMES
        and sym.path in memberlist_paths
    ]

    if not delegate_symbols:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LinkerResult(run=run)

    # Group delegates by path so we can look for anchors file-by-file.
    by_path: dict[str, list] = {}
    for sym in delegate_symbols:
        by_path.setdefault(sym.path, []).append(sym)

    edges: list[Edge] = []
    seen_pairs: set[tuple[str, str]] = set()
    files_analyzed = 0

    for path_str, delegates in by_path.items():
        source = memberlist_files.get(Path(path_str))
        if source is None:  # pragma: no cover
            continue
        files_analyzed += 1

        create_lines = _find_memberlist_create_lines(source)

        # Build the list of anchor symbols for this file. Prefer
        # functions that directly contain a memberlist.Create(
        # call; fall back to any function in the same file that is
        # not itself a delegate method.
        anchor_syms = []
        for line in create_lines:
            enc = ctx.find_enclosing_symbol(path_str, line)
            if enc is not None and enc not in anchor_syms:
                anchor_syms.append(enc)
        if not anchor_syms:
            # No explicit Create anchor — fall back to init() or the
            # first non-delegate function symbol in the file.
            delegate_ids = {d.id for d in delegates}
            fallback_candidates = [
                s
                for s in ctx.symbols
                if s.path == path_str
                and s.kind in ("function", "method")
                and s.id not in delegate_ids
            ]
            if fallback_candidates:
                anchor_syms = [fallback_candidates[0]]

        if not anchor_syms:
            continue

        for anchor in anchor_syms:
            for target in delegates:
                pair = (anchor.id, target.id)
                if pair in seen_pairs:  # pragma: no cover
                    # Defensive: anchor_syms and delegates are
                    # deduped above, so each (anchor, target) pair is
                    # visited at most once per file. Retained because
                    # the loop structure cannot enforce the invariant
                    # at the type level.
                    continue
                seen_pairs.add(pair)
                edges.append(
                    Edge.create(
                        src=anchor.id,
                        dst=target.id,
                        edge_type="dispatches_to",
                        confidence=0.80,
                        line=target.span.start_line,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="go_memberlist_delegate",
                        meta={
                            "delegate_method": _short_name(target.name),
                        },
                    ),
                )

    run.files_analyzed = files_analyzed
    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(run=run, edges=edges)
