# SPDX-License-Identifier: AGPL-3.0-or-later
"""Annotation convention linker for developer-provided pub/sub and dispatch hints.

Scans source files for ``@hg:`` comment directives and creates edges for
pub/sub relationships, route declarations, and dispatch patterns that
can't be automatically detected by framework-specific linkers.

Supported Directives
--------------------
- ``@hg:publishes <channel>`` — marks the enclosing function as a publisher
  to the named channel. Creates an ``annotated_publishes`` edge when matched
  with a ``@hg:subscribes`` directive on the same channel.
- ``@hg:subscribes <channel>`` — marks the enclosing function as a subscriber.
- ``@hg:route <method> <path>`` — creates a route symbol (kind="route")
  with the given method and path/identifier.
- ``@hg:dispatches <target>`` — creates an ``annotated_dispatches`` edge
  from the annotation site to any symbol whose name matches the target.

Matching
--------
Publishers and subscribers are matched by channel name (case-sensitive string
equality). Each publisher creates an edge to every subscriber on the same
channel. Dispatches directives match against existing symbol names. Route
directives create standalone symbols. Confidence is 0.95.

Language Agnostic
-----------------
Works in any language with ``//``, ``#``, ``--``, or ``/* */`` style comments.
The scanner uses a simple regex that matches ``@hg:directive`` anywhere in
a line — it doesn't need to understand comment syntax per-language.

Why This Design
---------------
Some coupling patterns are too abstract for automatic detection: custom CRDT
wrappers, application-specific event buses, binary protocol dispatch tables.
Rather than building per-framework linkers for every possible pattern, a
lightweight annotation convention lets developers hint the analyzer directly.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerResult,
    register_linker,
)

if TYPE_CHECKING:
    pass

PASS_ID = make_pass_id("annotation-convention-linker")

# Matches @hg: directives in comments. Language-agnostic: matches the directive
# regardless of comment syntax (// # -- /* etc.).
# Group 1: directive name (publishes, subscribes, route, dispatches)
# Group 2: arguments (channel name, route spec, etc.)
_HG_DIRECTIVE_PATTERN = re.compile(
    r"""@hg:(publishes|subscribes|route|dispatches)\s+(.+?)(?:\s*$|\s*\*/)""",
    re.MULTILINE,
)


@dataclass
class AnnotationSite:
    """A source location where an @hg: directive was found."""

    directive: str  # "publishes", "subscribes", "route", "dispatches"
    argument: str   # channel name, route spec, target name
    file_path: str  # relative path
    line: int       # 1-indexed


def scan_file_for_annotations(file_path: Path, rel_path: str) -> list[AnnotationSite]:
    """Scan a source file for @hg: directives.

    Args:
        file_path: Absolute path to the source file.
        rel_path: Relative path for symbol IDs.

    Returns:
        List of AnnotationSite objects found in the file.
    """
    try:
        content = file_path.read_text(errors="replace")
    except OSError:  # pragma: no cover
        return []

    sites: list[AnnotationSite] = []
    for m in _HG_DIRECTIVE_PATTERN.finditer(content):
        directive = m.group(1)
        argument = m.group(2).strip()
        # Calculate line number from character offset
        line = content[:m.start()].count("\n") + 1
        sites.append(AnnotationSite(
            directive=directive,
            argument=argument,
            file_path=rel_path,
            line=line,
        ))

    return sites


def link_annotations(
    repo_root: Path,
    symbols: list[Symbol],
) -> LinkerResult:
    """Link @hg: annotation directives across all source files.

    Scans all files referenced by symbols for @hg: directives, then
    matches publishers to subscribers by channel name.

    Args:
        repo_root: Repository root path.
        symbols: All symbols from all analyzers.

    Returns:
        LinkerResult with annotated_publishes edges and synthetic symbols.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    result_edges: list[Edge] = []
    result_symbols: list[Symbol] = []

    # Collect unique file paths from symbols
    seen_paths: set[str] = set()
    file_paths: list[tuple[Path, str]] = []  # (absolute, relative)
    for sym in symbols:
        if sym.path in seen_paths:
            continue
        seen_paths.add(sym.path)
        abs_path = Path(sym.path)
        if not abs_path.is_absolute():
            abs_path = repo_root / sym.path
        file_paths.append((abs_path, sym.path))

    # Scan all files for annotations
    publishers: dict[str, list[AnnotationSite]] = {}  # channel → sites
    subscribers: dict[str, list[AnnotationSite]] = {}  # channel → sites
    routes: list[AnnotationSite] = []
    dispatches: list[AnnotationSite] = []

    for abs_path, rel_path in file_paths:
        if not abs_path.exists():
            continue
        sites = scan_file_for_annotations(abs_path, rel_path)
        for site in sites:
            if site.directive == "publishes":
                publishers.setdefault(site.argument, []).append(site)
            elif site.directive == "subscribes":
                subscribers.setdefault(site.argument, []).append(site)
            elif site.directive == "route":
                routes.append(site)
            elif site.directive == "dispatches":
                dispatches.append(site)

    seen_sym_ids: set[str] = set()

    # --- Match publishers to subscribers by channel name ---
    for channel, pub_sites in publishers.items():
        sub_sites = subscribers.get(channel, [])
        if not sub_sites:
            continue

        for pub in pub_sites:
            pub_id = f"{pub.file_path}:{pub.line}:{channel}:annotated_publisher"
            if pub_id not in seen_sym_ids:
                seen_sym_ids.add(pub_id)
                result_symbols.append(Symbol(
                    id=pub_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=f"@hg:publishes {channel}",
                    fingerprint=hashlib.sha256(pub_id.encode()).hexdigest()[:16],
                    kind="event_publisher",
                    name=channel,
                    path=pub.file_path,
                    language="unknown",
                    span=Span(
                        start_line=pub.line, end_line=pub.line,
                        start_col=0, end_col=0,
                    ),
                    origin=PASS_ID,
                    meta={"hg_annotation": "publishes", "channel": channel},
                    supply_chain_tier=2,
                    supply_chain_reason="@hg:publishes annotation",
                ))

            for sub in sub_sites:
                sub_id = f"{sub.file_path}:{sub.line}:{channel}:annotated_subscriber"
                if sub_id not in seen_sym_ids:
                    seen_sym_ids.add(sub_id)
                    result_symbols.append(Symbol(
                        id=sub_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=f"@hg:subscribes {channel}",
                        fingerprint=hashlib.sha256(sub_id.encode()).hexdigest()[:16],
                        kind="event_subscriber",
                        name=channel,
                        path=sub.file_path,
                        language="unknown",
                        span=Span(
                            start_line=sub.line, end_line=sub.line,
                            start_col=0, end_col=0,
                        ),
                        origin=PASS_ID,
                        meta={"hg_annotation": "subscribes", "channel": channel},
                        supply_chain_tier=2,
                        supply_chain_reason="@hg:subscribes annotation",
                    ))

                result_edges.append(Edge.create(
                    src=pub_id,
                    dst=sub_id,
                    edge_type="annotated_publishes",
                    line=pub.line,
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="hg_annotation",
                    access_mode="write",
                    dest_access_mode="read",
                    channel=channel,
                ))

    # --- Create route symbols for @hg:route directives ---
    for route in routes:
        route_id = f"{route.file_path}:{route.line}:{route.argument}:annotated_route"
        if route_id not in seen_sym_ids:
            seen_sym_ids.add(route_id)
            result_symbols.append(Symbol(
                id=route_id,
                stable_id=None,
                shape_id=None,
                canonical_name=f"@hg:route {route.argument}",
                fingerprint=hashlib.sha256(route_id.encode()).hexdigest()[:16],
                kind="route",
                name=route.argument,
                path=route.file_path,
                language="unknown",
                span=Span(
                    start_line=route.line, end_line=route.line,
                    start_col=0, end_col=0,
                ),
                origin=PASS_ID,
                meta={"hg_annotation": "route", "route_spec": route.argument},
                supply_chain_tier=1,
                supply_chain_reason="@hg:route annotation",
            ))

    # --- Create dispatches_to edges for @hg:dispatches directives ---
    # Build symbol name index for matching dispatch targets
    sym_by_name: dict[str, list[Symbol]] = {}
    for sym in symbols:
        sym_by_name.setdefault(sym.name, []).append(sym)

    for disp in dispatches:
        target_name = disp.argument
        target_syms = sym_by_name.get(target_name, [])
        if not target_syms:
            continue

        disp_id = f"{disp.file_path}:{disp.line}:{target_name}:annotated_dispatcher"
        if disp_id not in seen_sym_ids:
            seen_sym_ids.add(disp_id)
            result_symbols.append(Symbol(
                id=disp_id,
                stable_id=None,
                shape_id=None,
                canonical_name=f"@hg:dispatches {target_name}",
                fingerprint=hashlib.sha256(disp_id.encode()).hexdigest()[:16],
                kind="dispatcher",
                name=target_name,
                path=disp.file_path,
                language="unknown",
                span=Span(
                    start_line=disp.line, end_line=disp.line,
                    start_col=0, end_col=0,
                ),
                origin=PASS_ID,
                meta={"hg_annotation": "dispatches", "channel": target_name},
                supply_chain_tier=2,
                supply_chain_reason="@hg:dispatches annotation",
            ))

        for target in target_syms:
            result_edges.append(Edge.create(
                src=disp_id,
                dst=target.id,
                edge_type="annotated_dispatches",
                line=disp.line,
                confidence=0.95,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="hg_annotation",
                channel=target_name,
            ))

    run.duration_ms = int((time.time() - start_time) * 1000)

    return LinkerResult(
        edges=result_edges, symbols=result_symbols, run=run,
    )


@register_linker(
    "annotation-convention",
    priority=90,  # Run after framework linkers
    activation=LinkerActivation(always=True),
    requirements=[],
)
def annotation_convention_linker(ctx: LinkerContext) -> LinkerResult:
    """Run the annotation convention linker."""
    return link_annotations(ctx.repo_root, ctx.symbols)
