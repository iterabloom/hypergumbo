# SPDX-License-Identifier: AGPL-3.0-or-later
"""Entrypoint detection for code analysis using YAML-driven pattern matching.

Detects entrypoints via semantic concepts from YAML framework patterns (ADR-3aaa):
- HTTP routes (Django, Flask, FastAPI, Express, Rails, Spring, etc.)
- CLI commands (Click, Typer, argparse, Commander, Cobra, etc.)
- WebSocket handlers
- Background tasks (Celery, etc.)
- Scheduled jobs
- GraphQL resolvers
- Android lifecycle hooks

How It Works
------------
Entrypoint detection uses YAML framework patterns to identify "entry points"
into a codebase - places where execution typically starts or where external
requests arrive.

Detection is based on two mechanisms (ADR-3aaa):

1. **Definition-based patterns** (v1.0.x): Matches decorators, base classes,
   and annotations on symbol definitions. E.g., @app.route, @Get(), extends
   RequestHandler, etc.

2. **Usage-based patterns** (v1.1.x): Matches UsageContext records emitted
   by analyzers for call-based frameworks. E.g., path('/users', views.users)
   in Django, app.get('/users', handler) in Express.

The FRAMEWORK_PATTERNS phase enriches symbols with concept metadata
(meta.concepts) based on pattern matching. This module then maps those
concepts to entrypoint types.

Confidence Scores
-----------------
- 0.95: All semantic detection (based on actual pattern matching)

Connectivity Boost (concept-based only):
- Entrypoints with outgoing edges get a confidence boost (up to +0.25)
- Boost formula: min(0.25, log(1 + out_edges) / 10)
- This ranks "interesting" entrypoints higher (those that call many functions)
- Entrypoints are sorted by final confidence (highest first)
- CONNECTIVITY_BASED fallback entrypoints skip the boost to avoid double-counting

Connectivity-Based Fallback:
- When no concept-based entrypoints are found, the system falls back to
  selecting the most-connected callable symbols (functions, methods, constructors)
- Base confidence: 0.50 (below all concept-based entrypoints)
- At most 5 fallback entrypoints are selected, ranked by out-degree
- This ensures --entry auto never hard-fails, even for repos with no
  matching YAML patterns (e.g., Rust libraries without pub tracking)

Post-Processing Filters (INV-mahap):
- Minimum confidence threshold (MIN_ENTRYPOINT_CONFIDENCE = 0.10): entries below
  this threshold are excluded. This removes noise entries like library_export
  entries demoted to 0.08 when semantic entries exist, and test-file entries
  penalized to 0.08.
- Count cap (MAX_ENTRYPOINT_COUNT = 50): prevents output flooding for repos
  with hundreds of high-confidence entries (e.g., microservice gateways)
"""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

import re

from .ir import Symbol, Edge
from .routes import method_token, route_of
from .paths import is_infrastructure_path, is_test_file, is_utility_file


# File path patterns that indicate frontend UI code (React, Vue, Angular, Svelte).
# Symbols in these paths should not be classified as server-side HTTP routes.
_FRONTEND_PATH_RE = re.compile(
    r"(?:^|/)(?:components|views|pages|layouts|widgets|screens|ui)/"
    r"|\.(?:tsx|jsx|vue|svelte)$",
    re.IGNORECASE,
)

# Import patterns that indicate frontend framework code.
# If a symbol's file imports from these, it's UI code not server routes.
_FRONTEND_IMPORT_PREFIXES = frozenset({
    "react", "react-dom", "@angular/core", "@angular/common",
    "vue", "svelte", "@sveltejs", "solid-js", "preact",
    "next/link", "next/router", "next/navigation",
    "@remix-run/react", "gatsby",
})


def _is_frontend_file(sym: Symbol) -> bool:
    """Check if a symbol is in a frontend UI file.

    Uses file path patterns and import metadata to detect React, Vue,
    Angular, and Svelte component files.  Used to suppress false-positive
    http_route classification on frontend event handlers (WI-ronik).
    """
    if sym.path and _FRONTEND_PATH_RE.search(sym.path):
        return True
    # Check imports in symbol metadata
    if sym.meta:
        imports = sym.meta.get("imports", [])
        for imp in imports:
            mod = imp.get("module", "") if isinstance(imp, dict) else str(imp)
            if any(mod.startswith(prefix) for prefix in _FRONTEND_IMPORT_PREFIXES):
                return True
    return False

# Minimum confidence threshold for entrypoint inclusion.
# Entries below this threshold are filtered from detect_entrypoints() results.
# This addresses INV-mahap: library_export entries demoted to 0.08 (below 0.10)
# should not appear in --list-entries or --entry auto candidate lists.
MIN_ENTRYPOINT_CONFIDENCE: float = 0.10

# Base entrypoint cap for small repos (< 5000 nodes).
# For larger repos, the cap scales proportionally via compute_entrypoint_cap().
BASE_ENTRYPOINT_CAP: int = 50

# Absolute maximum entrypoint cap, even for very large repos.
# Prevents pathological output for repos with 100K+ symbols.
MAX_ENTRYPOINT_CAP: int = 500

# Legacy alias for backwards compatibility with tests importing this name.
MAX_ENTRYPOINT_COUNT: int = BASE_ENTRYPOINT_CAP


def compute_entrypoint_cap(node_count: int) -> int:
    """Compute the entrypoint cap based on repo size (number of symbols).

    Small repos (< 5000 nodes): BASE_ENTRYPOINT_CAP (50).
    Larger repos: scales as node_count // 100 (1% of symbols),
    floored at BASE_ENTRYPOINT_CAP, capped at MAX_ENTRYPOINT_CAP.

    Examples:
        500 nodes   → 50  (base)
        5000 nodes  → 50  (base)
        10000 nodes → 100
        25000 nodes → 250
        90000 nodes → 500 (capped)

    Args:
        node_count: Total number of symbols in the behavior map.

    Returns:
        Maximum number of entrypoints to return.
    """
    scaled = max(BASE_ENTRYPOINT_CAP, node_count // 100)
    return min(scaled, MAX_ENTRYPOINT_CAP)


class EntrypointKind(Enum):
    """Types of entrypoints that can be detected."""

    HTTP_ROUTE = "http_route"
    CLI_MAIN = "cli_main"
    CLI_COMMAND = "cli_command"
    # Language-level main() entry points (detected via YAML patterns)
    MAIN_FUNCTION = "main_function"
    # Module-level script guard: `if __name__ == "__main__":` with no separate
    # main() function. Targets the FILE node (not a callable), so it is a
    # distinct kind from MAIN_FUNCTION — kind alone disambiguates the target
    # type (function vs file) rather than the free-text label (WI-tuvun).
    MAIN_GUARD = "main_guard"
    ELECTRON_MAIN = "electron_main"
    ELECTRON_PRELOAD = "electron_preload"
    ELECTRON_RENDERER = "electron_renderer"
    DJANGO_VIEW = "django_view"
    EXPRESS_ROUTE = "express_route"
    NESTJS_CONTROLLER = "nestjs_controller"
    SPRING_CONTROLLER = "spring_controller"
    RAILS_CONTROLLER = "rails_controller"
    PHOENIX_CONTROLLER = "phoenix_controller"
    GO_HANDLER = "go_handler"
    LARAVEL_CONTROLLER = "laravel_controller"
    RUST_HANDLER = "rust_handler"
    ASPNET_CONTROLLER = "aspnet_controller"
    SINATRA_ROUTE = "sinatra_route"
    KTOR_ROUTE = "ktor_route"
    VAPOR_ROUTE = "vapor_route"
    PLUG_ROUTE = "plug_route"
    HAPI_ROUTE = "hapi_route"
    FASTIFY_ROUTE = "fastify_route"
    KOA_ROUTE = "koa_route"
    GRAPE_API = "grape_api"
    TORNADO_HANDLER = "tornado_handler"
    AIOHTTP_VIEW = "aiohttp_view"
    SLIM_ROUTE = "slim_route"
    MICRONAUT_CONTROLLER = "micronaut_controller"
    GRAPHQL_SERVER = "graphql_server"
    # Mobile app entry kinds
    ANDROID_ACTIVITY = "android_activity"  # Android Activity.onCreate()
    ANDROID_APPLICATION = "android_application"  # Android Application.onCreate()
    # Semantic concept-based entry kinds (ADR-3aaa v0.9.x)
    CONTROLLER = "controller"  # Generic controller from concept metadata
    BACKGROUND_TASK = "background_task"  # Async/background task
    WEBSOCKET_HANDLER = "websocket_handler"  # WebSocket event handler
    MIDDLEWARE_HANDLER = "middleware_handler"  # HTTP middleware handler
    EVENT_HANDLER = "event_handler"  # Event/message handler
    ERROR_HANDLER = "error_handler"  # HTTP / middleware exception handler
    FORM = "form"  # Framework-reflected form class (is_valid/save/authorize)
    SERIALIZER = "serializer"  # Framework-reflected serializer/DTO class (to_representation / dump / toArray)
    SCHEDULED_TASK = "scheduled_task"  # Cron/scheduled job
    # Library entry points (exported API)
    LIBRARY_EXPORT = "library_export"  # Exported function/class (library entry)
    # Executable scripts in non-Python languages (INV-tajap)
    SHELL_SCRIPT = "shell_script"  # Bash/sh script (shebang or .sh/.bash file)
    HTML_ENTRY = "html_entry"  # SPA root / convention-named index.html
    SCRIPT_MODULE = "script_module"  # TS/JS file with top-level code, no inbound imports
    # Test/benchmark entry points (from test-frameworks.yaml patterns)
    TEST_FUNCTION = "test_function"  # Test function/method (pytest, JUnit, etc.)
    # SPA bootstrap (createRoot, ReactDOM.render, hydrateRoot)
    SPA_BOOTSTRAP = "spa_bootstrap"  # SPA app bootstrap function
    # Connectivity-based fallback (no patterns matched)
    CONNECTIVITY_BASED = "connectivity_based"  # High-connectivity callable


def all_known_entrypoint_kinds() -> frozenset[str]:
    """Single-source catalog of every entrypoint-kind value (WI-pupiz).

    The canonical vocabulary IS the :class:`EntrypointKind` enum; this
    resolver exposes it as a lightweight catalog-derived axis (ADR-0024
    §4 "use judgment" carveout), mirroring ``edge_types.all_edge_type_names``
    and ``catalog.all_known_languages``. Wired into
    :func:`hypergumbo_core.multi_value_field_axis._known_axes` under the
    ``entrypoint-kind`` axis name so any future ``str`` field carrying an
    entrypoint-kind validates against the catalog, and used as the single
    source for the schema ``Entrypoint.kind`` enumeration (``generate-schema``).

    Distinct from the ``entrypoint_meta`` ``evidence_type`` axis (WI-rukam):
    ``kind`` names WHAT the entrypoint is (an HTTP route, a CLI command),
    while ``evidence_type`` names HOW it was detected. The empirically
    dark-on-self-corpus kinds (e.g. ``cli_command`` for argparse CLIs —
    a detection gap tracked under WI-lubap) are still *known* kinds and
    appear here.
    """
    return frozenset(k.value for k in EntrypointKind)


# --- Entrypoint provenance vocabularies (WI-rukam) -------------------
# The Entrypoint record mirrors Edge's provenance shape: a producer pass
# (``source``, analog of ``Edge.origin``) and an inference pathway
# (``evidence_type``, analog of ``Edge.evidence_type``), both nested under
# ``Entrypoint.meta``. These two frozensets are the single source of truth
# for the value spaces; ``Entrypoint.create`` validates against them and
# ``axis_meta_keys`` documents them under the ``entrypoint_meta`` axis.

# Producer passes that emit Entrypoints (the three functions below).
ENTRYPOINT_SOURCES: frozenset[str] = frozenset({
    "concept_detector",        # _detect_from_concepts (YAML-concept matches)
    "connectivity_fallback",   # _connectivity_fallback (no-patterns fallback)
    "script_module_detector",  # _detect_script_modules (TS/JS standalone)
})

# Inference pathways — aligns 1:1 with the spec §8/§9 confidence tiers.
ENTRYPOINT_EVIDENCE_TYPES: frozenset[str] = frozenset({
    "manifest_declared",       # 0.99 — package-manifest bin/scripts entry
    "framework_pattern",       # framework decorator/base-class/usage match
    "structural",              # main-guard, shebang, filename, import-graph
    "language_convention",     # main()/test functions/library exports
    "naming_heuristic",        # *Controller/*Handler/cmd_* name patterns
    "connectivity_heuristic",  # 0.50 — out-degree fallback
})


def _entrypoint_id(kind: "EntrypointKind", symbol_id: str, label: str) -> str:
    """Content-hash identity for an Entrypoint record (WI-rukam).

    A pure function of (kind, symbol_id, label) — the record's identity,
    NOT its confidence (a ranking value). Mirrors ``Edge``'s
    ``edge:sha256:<16hex>`` id shape with an ``entrypoint:`` namespace.
    """
    payload = f"{kind.value}:{symbol_id}:{label}"
    return "entrypoint:sha256:" + hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass
class Entrypoint:
    """A detected entrypoint in the codebase.

    Attributes:
        symbol_id: ID of the symbol that is an entrypoint.
        kind: Type of entrypoint detected.
        confidence: Detection reliability (0.0-1.0) — how sure the detector
            is that this symbol IS an entrypoint. NOT a prominence value;
            ADR-0039 ruling 3 relocates the ranking boosts/penalties to
            ``rank_score``.
        rank_score: Ranking prominence (0.0-1.0). Initializes from
            ``confidence`` and accumulates the multiplicative penalties,
            library-export demotion, and degree boosts (ADR-0039 ruling 3).
            Entrypoint ordering + the ``MIN_ENTRYPOINT_CONFIDENCE`` filter
            key on this; equal to ``confidence`` until those adjustments
            relocate.
        label: Human-readable label for the entrypoint.
        meta: Provenance dict mirroring ``Edge.meta`` (WI-rukam). Carries
            ``id`` (auto-stamped content hash), and — when built via
            :meth:`create` — ``source`` (producer pass) and
            ``evidence_type`` (inference pathway). Keys are registered on
            the ``entrypoint_meta`` axis in :mod:`hypergumbo_core.axis_meta_keys`.
    """

    symbol_id: str
    kind: EntrypointKind
    confidence: float
    label: str
    meta: dict = field(default_factory=dict)
    rank_score: Optional[float] = None

    def __post_init__(self) -> None:
        # ``id`` is a pure function of (kind, symbol_id, label); stamp it
        # unconditionally so EVERY Entrypoint — including those built
        # directly (e.g. in tests) rather than via ``create`` — carries a
        # stable identity. ``setdefault`` lets a caller pre-seed it.
        self.meta.setdefault("id", _entrypoint_id(self.kind, self.symbol_id, self.label))
        # ADR-0039 ruling 3: ``rank_score`` mirrors detection ``confidence``
        # until the ranking adjustments relocate onto it.
        if self.rank_score is None:
            self.rank_score = self.confidence

    @classmethod
    def create(
        cls,
        symbol_id: str,
        kind: EntrypointKind,
        confidence: float,
        label: str,
        *,
        source: str,
        evidence_type: str,
    ) -> "Entrypoint":
        """Build an Entrypoint with full provenance (WI-rukam).

        ``source`` and ``evidence_type`` are validated against
        :data:`ENTRYPOINT_SOURCES` / :data:`ENTRYPOINT_EVIDENCE_TYPES`
        (mirroring ``Edge.create``'s access-mode validation) so a typo'd
        pathway is a construction-time error, not a silent free-text leak.
        The ``id`` is stamped by ``__post_init__``.
        """
        if source not in ENTRYPOINT_SOURCES:
            raise ValueError(
                f"Entrypoint source={source!r} not in "
                f"{sorted(ENTRYPOINT_SOURCES)}"
            )
        if evidence_type not in ENTRYPOINT_EVIDENCE_TYPES:
            raise ValueError(
                f"Entrypoint evidence_type={evidence_type!r} not in "
                f"{sorted(ENTRYPOINT_EVIDENCE_TYPES)}"
            )
        return cls(
            symbol_id=symbol_id,
            kind=kind,
            confidence=confidence,
            label=label,
            meta={"source": source, "evidence_type": evidence_type},
        )

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "symbol_id": self.symbol_id,
            "kind": self.kind.value,
            "confidence": self.confidence,
            "rank_score": self.rank_score,
            "label": self.label,
            "meta": dict(self.meta),
        }


# Priority order for disambiguating app_bootstrap framework labels.
# When multiple frameworks match the same bootstrap call (e.g., both React
# and Solid match bare createRoot()), we prefer the more specific framework.
# React is deprioritized because its patterns match Solid.js function names
# (createRoot, hydrateRoot) — Solid uses the same names for different APIs.
# Frameworks earlier in this list win over those later.
_BOOTSTRAP_FRAMEWORK_PRIORITY: list[str] = [
    "solid",    # Solid's createRoot/render are always Solid-specific
    "svelte",   # Svelte's mount/hydrate are Svelte-specific
    "vue",      # Vue's createApp is Vue-specific
    "react",    # React patterns overlap with Solid (createRoot, hydrateRoot)
]


def _pick_best_bootstrap_framework(concepts: list[dict]) -> str:
    """Pick the best framework label when multiple claim app_bootstrap.

    When a symbol has app_bootstrap concepts from multiple frameworks (e.g.,
    both React and Solid match bare createRoot()), this function picks the
    most appropriate one based on framework priority.

    Args:
        concepts: The symbol's concept list (meta.concepts).

    Returns:
        Best framework name, or empty string if no app_bootstrap concepts.
    """
    bootstrap_fws: list[str] = []
    for c in concepts:
        if not isinstance(c, dict):
            continue
        if c.get("concept") == "app_bootstrap":
            fw = c.get("framework", "")
            if fw:
                bootstrap_fws.append(fw)

    if not bootstrap_fws:
        return ""
    if len(bootstrap_fws) == 1:
        return bootstrap_fws[0]

    # Multiple frameworks claim app_bootstrap — pick by priority
    fw_set = set(bootstrap_fws)
    for fw in _BOOTSTRAP_FRAMEWORK_PRIORITY:
        if fw in fw_set:
            return fw

    # Fallback: return first framework found
    return bootstrap_fws[0]


def _ws_route_label(path: str, framework: str = "") -> str:
    """Label for a WebSocket entrypoint (WI-kuvig/WI-lomoz).

    Path-bearing WS routes (e.g. Starlette ``WebSocketRoute("/ws", ...)``) get
    a ``"WS <path>"`` label — parallel to the HTTP ``"HTTP <method> <path>"``
    shape — so the handler-concept entrypoint and the minted route-symbol
    entrypoint for the same route collapse to one in the label-dedup pass.
    Pathless concept handlers (NestJS gateways, etc.) keep the framework label
    and are not collapsed.
    """
    if path:
        return f"WS {path}"
    if framework:
        return f"{framework.title()} WebSocket handler"
    return "WebSocket handler"


def _detect_from_concepts(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect entrypoints from semantic concept metadata.

    ADR-3aaa introduces semantic entry detection: the FRAMEWORK_PATTERNS phase
    enriches symbols with concept metadata (meta.concepts) based on YAML pattern
    matching. This function checks for entrypoint-worthy concepts and maps them
    to EntrypointKind values.

    Benefits:
    - YAML-driven: All detection is data-driven, not hardcoded
    - Eliminates false positives (e.g., React Router files won't have route concepts)
    - Framework-aware detection (concepts include framework info)

    Detected concepts (framework patterns, confidence=0.95):
    - "route" -> HTTP_ROUTE (HTTP endpoint handler)
    - "controller" -> CONTROLLER (request handler class/method)
    - "task" -> BACKGROUND_TASK (async/background job)
    - "scheduled_task" -> SCHEDULED_TASK (cron/periodic job)
    - "websocket_handler" -> WEBSOCKET_HANDLER (WebSocket event handler)
    - "websocket_gateway" -> WEBSOCKET_HANDLER (NestJS WebSocket gateway)
    - "middleware" -> MIDDLEWARE_HANDLER (HTTP middleware handler)
    - "event_handler" -> EVENT_HANDLER (event/message handler)
    - "error_handler" -> ERROR_HANDLER (HTTP / middleware exception handler)
    - "form" -> FORM (framework-reflected form class)
    - "serializer" -> SERIALIZER (framework-reflected serializer/DTO class)
    - "command" -> CLI_COMMAND (CLI command handler)
    - "liveview" -> CONTROLLER (Phoenix LiveView - real-time UI)
    - "graphql_resolver" -> GRAPHQL_SERVER (GraphQL resolver)
    - "graphql_schema" -> GRAPHQL_SERVER (GraphQL schema definition)
    - "lifecycle_hook" -> ANDROID_ACTIVITY/ANDROID_APPLICATION/CONTROLLER (Android lifecycle)

    Detected concepts (generic entrypoint, confidence=0.90):
    - "entrypoint" -> ELECTRON_MAIN (electron) / MAIN_FUNCTION (others)
    - "app_bootstrap" -> SPA_BOOTSTRAP (createRoot, ReactDOM.render, hydrateRoot)

    Detected concepts (language conventions, confidence=0.80):
    - "main_function" -> MAIN_FUNCTION (function target: Go, Java, Python, C, etc.)
    - "main_guard" -> MAIN_GUARD (file target: Python `if __name__ == "__main__"`
      with no separate main(); WI-tuvun — kind disambiguates target from
      MAIN_FUNCTION)
    - "test_function" -> TEST_FUNCTION (pytest, JUnit, Go testing, etc.)
    - "benchmark_function" -> TEST_FUNCTION (Go Benchmark*, Rust #[bench])
    - "example_function" -> TEST_FUNCTION (Go Example*)

    Args:
        symbols: All symbols with potential concept metadata.

    Returns:
        List of entrypoints detected via semantic concepts.
    """
    entrypoints = []

    for sym in symbols:
        if not sym.meta:
            continue
        concepts = sym.meta.get("concepts", [])
        if not concepts:
            continue

        # Track which entry kinds we've added to avoid duplicates per symbol
        added_kinds: set[EntrypointKind] = set()

        for concept in concepts:
            if not isinstance(concept, dict):
                continue

            concept_type = concept.get("concept")
            framework = concept.get("framework", "")

            # Route concept -> HTTP_ROUTE (or WEBSOCKET_HANDLER when the
            # route's method is the synthetic "WS" marker — WI-kuvig: a
            # WebSocket route is not an HTTP route; meta.http_method="WS" is
            # retained on the symbol, only the entrypoint kind changes).
            if concept_type == "route":
                # WI-kilal: when the concept dict doesn't carry method/path,
                # fall back to the Symbol's meta-level fields. Rails routes
                # are emitted by the ruby analyzer with framework_role="route"
                # and http_method/route_path at sym.meta level (not in the
                # concept dict), because rails.yaml matches them via
                # ``framework_role: "^route$"`` definition-based enrichment
                # rather than the usage-based ``extract`` that stamps method
                # and path into the concept dict. Without this fallback,
                # every Rails route produced the same generic "HTTP route"
                # label, and the label-dedup in detect_entrypoints collapsed
                # all of them to one entrypoint (e.g., chatwoot: 623 routes
                # -> 1 HTTP_ROUTE entrypoint).
                info = route_of(sym)
                protocol = info["protocol"]
                method = method_token(info) or ""
                path = info["path"] or ""
                # Suppress false-positive route classification on frontend
                # UI code (WI-ronik).  React/Vue/Angular component event
                # handlers syntactically resemble Express route handlers
                # but are not server-side routes.
                confidence = 0.95
                if _is_frontend_file(sym):
                    confidence = 0.05  # Below MIN_ENTRYPOINT_CONFIDENCE
                if protocol == "websocket":
                    if EntrypointKind.WEBSOCKET_HANDLER in added_kinds:
                        continue
                    entrypoints.append(Entrypoint.create(
                        symbol_id=sym.id,
                        kind=EntrypointKind.WEBSOCKET_HANDLER,
                        confidence=confidence,
                        label=_ws_route_label(path, concept.get("framework", "")),
                        source="concept_detector",
                        evidence_type="framework_pattern",
                    ))
                    added_kinds.add(EntrypointKind.WEBSOCKET_HANDLER)
                    continue
                if EntrypointKind.HTTP_ROUTE in added_kinds:
                    continue
                if method and path:
                    label = f"HTTP {method.upper()} {path}"
                elif method:
                    label = f"HTTP {method.upper()} route"
                elif path:
                    label = f"HTTP route {path}"
                else:
                    label = "HTTP route"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.HTTP_ROUTE,
                    confidence=confidence,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.HTTP_ROUTE)

            # Controller concept -> CONTROLLER
            elif concept_type == "controller":
                if EntrypointKind.CONTROLLER in added_kinds:
                    continue
                if framework:
                    label = f"{framework.title()} controller"
                else:
                    label = "Controller"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.CONTROLLER,
                    confidence=0.95,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.CONTROLLER)

            # Task concept -> BACKGROUND_TASK
            elif concept_type == "task":
                if EntrypointKind.BACKGROUND_TASK in added_kinds:
                    continue
                if framework:
                    label = f"{framework.title()} task"
                else:
                    label = "Background task"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.BACKGROUND_TASK,
                    confidence=0.95,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.BACKGROUND_TASK)

            # Scheduled task concept -> SCHEDULED_TASK
            elif concept_type == "scheduled_task":
                if EntrypointKind.SCHEDULED_TASK in added_kinds:
                    continue
                if framework:
                    label = f"{framework.title()} scheduled task"
                else:
                    label = "Scheduled task"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.SCHEDULED_TASK,
                    confidence=0.95,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.SCHEDULED_TASK)

            # WebSocket handler concepts -> WEBSOCKET_HANDLER
            elif concept_type in ("websocket_handler", "websocket_gateway"):
                if EntrypointKind.WEBSOCKET_HANDLER in added_kinds:
                    continue
                # Path-bearing WS routes (Starlette WebSocketRoute) get a
                # "WS <path>" label so they dedup with the minted route
                # symbol; pathless handlers (NestJS gateways) keep the
                # framework label (WI-kuvig/WI-lomoz).
                ws_path = concept.get("path", "") or sym.meta.get("route_path", "")
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.WEBSOCKET_HANDLER,
                    confidence=0.95,
                    label=_ws_route_label(ws_path, framework),
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.WEBSOCKET_HANDLER)

            # Middleware concept -> MIDDLEWARE_HANDLER
            # Middleware intercepts HTTP requests/responses and is a
            # primary entry point in web frameworks (Express, Koa, Vapor,
            # Hummingbird, Django, etc.)
            elif concept_type == "middleware":
                if EntrypointKind.MIDDLEWARE_HANDLER in added_kinds:
                    continue
                if framework:
                    label = f"{framework.title()} middleware"
                else:
                    label = "HTTP middleware"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.MIDDLEWARE_HANDLER,
                    confidence=0.95,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.MIDDLEWARE_HANDLER)

            # Event handler concept -> EVENT_HANDLER
            elif concept_type == "event_handler":
                if EntrypointKind.EVENT_HANDLER in added_kinds:
                    continue
                if framework:
                    label = f"{framework.title()} event handler"
                else:
                    label = "Event handler"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.EVENT_HANDLER,
                    confidence=0.95,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.EVENT_HANDLER)

            # Error handler concept -> ERROR_HANDLER (WI-gudob Phase 1):
            # HTTP / middleware exception handlers are invoked by the framework
            # when a route handler raises; the call graph never sees those
            # invocations, so every decorator-registered error handler
            # (@app.errorhandler, @app.exception_handler, app.use(errorMid),
            # rescue_from, #[catch(…)], etc.) looks dead. 37 framework YAML
            # patterns emit this concept (fastapi, express, django, aspnet,
            # flask, actix, axum, gin, nestjs, rails, laravel, symfony,
            # phoenix, akka-http, ktor, vapor, etc.) — per the WI-dajul
            # concept registry audit it's the #1 inert concept by producer
            # count. Classifying it as a framework-invoked entrypoint makes
            # the bodies reachable from dead-code analysis uniformly across
            # every framework that already emits the concept, without
            # needing per-framework dispatch logic.
            elif concept_type == "error_handler":
                if EntrypointKind.ERROR_HANDLER in added_kinds:
                    continue
                if framework:
                    label = f"{framework.title()} error handler"
                else:
                    label = "Error handler"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.ERROR_HANDLER,
                    confidence=0.95,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.ERROR_HANDLER)

            # Form concept -> FORM (WI-gudob Phase 4):
            # Framework-reflected form classes (Django Form/ModelForm,
            # Flask-WTF FlaskForm, Laminas Form/Fieldset, FuelPHP Fieldset,
            # CakePHP Form, Laravel FormRequest, Symfony AbstractType, Yii
            # Model-as-form, Pyramid Colander schemas, Rails Form, Remix /
            # SvelteKit form-action modules). The framework instantiates the
            # form class from request data and reflectively calls
            # is_valid() / save() / authorize() / clean() / rules() — the
            # class body therefore looks dead to the static call graph even
            # when wired into a reachable route. Classifying the form class
            # itself as an entrypoint restores its reachability at class
            # level; internal method dispatch (form.clean_FIELD / form.save)
            # is a separate concern deferred to a dispatch-registry follow-
            # up because each framework names its reflective methods
            # differently. 12 framework YAML patterns emit this concept per
            # the WI-dajul concept registry audit (#4 inert by producer
            # count after Phase-1 error_handler / Phase-2 controller /
            # Phase-3 router).
            elif concept_type == "form":
                if EntrypointKind.FORM in added_kinds:
                    continue
                if framework:
                    label = f"{framework.title()} form"
                else:
                    label = "Form"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.FORM,
                    confidence=0.90,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.FORM)

            # Serializer concept -> SERIALIZER (WI-gudob Phase 5):
            # Framework-reflected serializer / DTO classes (Django REST
            # Framework Serializer / ModelSerializer, Marshmallow Schema and
            # SQLAlchemySchema, Grape Entity, Laravel JsonResource /
            # ResourceCollection, Litestar AbstractDTO / DTOData, ...).
            # The framework instantiates the class from a model / payload
            # and reflectively calls a serialize-like method
            # (``to_representation``, ``dump``, ``to_internal_value``,
            # ``toArray``, ``exposure``) — the class body therefore looks
            # dead to the static call graph even when wired into a
            # reachable route. Classifying the serializer class itself as
            # an entrypoint restores its reachability at class level;
            # per-method dispatch (``to_representation`` → field methods)
            # is deferred to a per-framework dispatch-registry follow-up
            # because each framework names its reflective methods
            # differently. 9 framework YAML patterns emit this concept
            # (django, flask, flask-restful, grape, laravel, litestar,
            # hanami, django-ninja, pyramid per the WI-dajul concept
            # registry audit) and the producers are uniformly class-level
            # ``base_class`` matches, so the scope is clean.
            elif concept_type == "serializer":
                if EntrypointKind.SERIALIZER in added_kinds:
                    continue
                if framework:
                    label = f"{framework.title()} serializer"
                else:
                    label = "Serializer"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.SERIALIZER,
                    confidence=0.90,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.SERIALIZER)

            # Command concept -> CLI_COMMAND
            elif concept_type == "command":
                if EntrypointKind.CLI_COMMAND in added_kinds:
                    continue
                if framework:
                    label = f"{framework.title()} command"
                else:
                    label = "CLI command"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.CLI_COMMAND,
                    confidence=0.95,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.CLI_COMMAND)

            # Manifest-declared CLI entry points (highest confidence)
            # npm_bin: package.json "bin" entries
            # cargo_binary: Cargo.toml [[bin]] entries
            # pyproject_script: pyproject.toml [project.scripts] entries
            elif concept_type in ("npm_bin", "cargo_binary", "pyproject_script"):
                if EntrypointKind.CLI_COMMAND in added_kinds:
                    continue
                if concept_type == "npm_bin":
                    label = f"npm CLI: {sym.name}"
                elif concept_type == "cargo_binary":
                    label = f"Cargo binary: {sym.name}"
                else:
                    label = f"Python CLI: {sym.name}"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.CLI_COMMAND,
                    confidence=0.99,  # Declared in manifest - highest confidence
                    label=label,
                    source="concept_detector",
                    evidence_type="manifest_declared",
                ))
                added_kinds.add(EntrypointKind.CLI_COMMAND)

            # LiveView concept -> CONTROLLER (real-time UI is an entry point)
            elif concept_type == "liveview":
                if EntrypointKind.CONTROLLER in added_kinds:
                    continue
                label = "Phoenix LiveView"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.CONTROLLER,
                    confidence=0.95,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.CONTROLLER)

            # GraphQL concepts -> GRAPHQL_SERVER
            elif concept_type in ("graphql_resolver", "graphql_schema"):
                if EntrypointKind.GRAPHQL_SERVER in added_kinds:
                    continue
                if concept_type == "graphql_resolver":
                    label = "GraphQL resolver"
                else:
                    label = "GraphQL schema"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.GRAPHQL_SERVER,
                    confidence=0.95,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.GRAPHQL_SERVER)

            # Lifecycle hook concept -> ANDROID_ACTIVITY or ANDROID_APPLICATION
            # (ADR-3aaa v1.1.x - pattern-based Android detection)
            elif concept_type == "lifecycle_hook":
                # Determine the specific Android entrypoint kind from matched_parent_base_class
                matched_base = concept.get("matched_parent_base_class", "")
                if matched_base in (
                    "Activity", "NativeActivity", "AppCompatActivity",
                    "FragmentActivity", "ComponentActivity", "ListActivity",
                    "PreferenceActivity",
                ):
                    if EntrypointKind.ANDROID_ACTIVITY in added_kinds:
                        continue
                    # Extract class name from qualified method name
                    parts = sym.name.rsplit(".", 1)
                    class_name = parts[0] if len(parts) == 2 else sym.name
                    entrypoints.append(Entrypoint.create(
                        symbol_id=sym.id,
                        kind=EntrypointKind.ANDROID_ACTIVITY,
                        confidence=0.95,
                        label=f"Android Activity ({class_name})",
                        source="concept_detector",
                        evidence_type="framework_pattern",
                    ))
                    added_kinds.add(EntrypointKind.ANDROID_ACTIVITY)
                elif matched_base in ("Application", "MultiDexApplication"):
                    if EntrypointKind.ANDROID_APPLICATION in added_kinds:
                        continue  # pragma: no cover - defensive deduplication
                    parts = sym.name.rsplit(".", 1)
                    class_name = parts[0] if len(parts) == 2 else sym.name
                    entrypoints.append(Entrypoint.create(
                        symbol_id=sym.id,
                        kind=EntrypointKind.ANDROID_APPLICATION,
                        confidence=0.95,
                        label=f"Android Application ({class_name})",
                        source="concept_detector",
                        evidence_type="framework_pattern",
                    ))
                    added_kinds.add(EntrypointKind.ANDROID_APPLICATION)
                # For Fragment, Service, BroadcastReceiver, ContentProvider - use CONTROLLER
                elif matched_base in (
                    "Fragment", "Service", "IntentService", "JobService",
                    "BroadcastReceiver", "ContentProvider",
                ):
                    if EntrypointKind.CONTROLLER in added_kinds:
                        continue  # pragma: no cover - defensive deduplication
                    parts = sym.name.rsplit(".", 1)
                    class_name = parts[0] if len(parts) == 2 else sym.name
                    entrypoints.append(Entrypoint.create(
                        symbol_id=sym.id,
                        kind=EntrypointKind.CONTROLLER,
                        confidence=0.95,
                        label=f"Android {matched_base} ({class_name})",
                        source="concept_detector",
                        evidence_type="framework_pattern",
                    ))
                    added_kinds.add(EntrypointKind.CONTROLLER)

            # Language-level main() function concept -> MAIN_FUNCTION
            # (ADR-3aaa v1.2.x - YAML-based language convention patterns)
            elif concept_type == "main_function":
                if EntrypointKind.MAIN_FUNCTION in added_kinds:
                    continue
                # Derive label from symbol's language
                lang = sym.language.title() if sym.language else "Unknown"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.MAIN_FUNCTION,
                    confidence=0.80,  # Lower than framework patterns
                    label=f"{lang} main()",
                    source="concept_detector",
                    evidence_type="language_convention",
                ))
                added_kinds.add(EntrypointKind.MAIN_FUNCTION)

            # INV-tajap: shell_script concept -> SHELL_SCRIPT
            # Bash/sh script (shebang line or .sh/.bash file extension).
            # Stamped on the file-kind Symbol by the bash analyzer; every
            # such file is treated as an executable entry point because the
            # bash analyzer's file-discovery criterion (find_bash_files)
            # already requires either a shebang or a known shell extension.
            elif concept_type == "shell_script":
                if EntrypointKind.SHELL_SCRIPT in added_kinds:
                    continue
                # The label points at the script path so consumers can
                # distinguish multiple bash entrypoints in the same repo.
                script_path = sym.path or sym.name
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.SHELL_SCRIPT,
                    confidence=0.85,  # Structural pattern, same tier as main_guard
                    label=f"Shell script ({script_path})",
                    source="concept_detector",
                    evidence_type="structural",
                ))
                added_kinds.add(EntrypointKind.SHELL_SCRIPT)

            # INV-tajap PR 2: html_entry concept -> HTML_ENTRY
            # SPA root / convention-named index.html that bootstraps a JS
            # bundle. Stamped on the file-kind Symbol by the HTML analyzer
            # when the filename matches ``index.html`` (case-insensitive).
            elif concept_type == "html_entry":
                if EntrypointKind.HTML_ENTRY in added_kinds:
                    continue
                html_path = sym.path or sym.name
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.HTML_ENTRY,
                    confidence=0.85,  # Structural pattern, same tier as shell_script
                    label=f"HTML entry ({html_path})",
                    source="concept_detector",
                    evidence_type="structural",
                ))
                added_kinds.add(EntrypointKind.HTML_ENTRY)

            # Python main guard concept -> MAIN_GUARD (WI-tuvun)
            # Structural entrypoint: `if __name__ == "__main__":` pattern.
            # Distinct from MAIN_FUNCTION: the guard marks a FILE node
            # (module-as-script), not a callable. When this symbol already
            # emitted a MAIN_FUNCTION (a named main() on the same symbol), the
            # guard is redundant and skipped; the cross-symbol case (guard on
            # the file node, main() on the function node) is deduped in the
            # INV-hosuh post-pass below.
            elif concept_type == "main_guard":
                if EntrypointKind.MAIN_FUNCTION in added_kinds:
                    continue
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.MAIN_GUARD,
                    confidence=0.85,  # Structural pattern (higher than naming heuristic)
                    label="Python script (if __name__ == '__main__')",
                    source="concept_detector",
                    evidence_type="structural",
                ))
                added_kinds.add(EntrypointKind.MAIN_GUARD)

            # Library export concept -> LIBRARY_EXPORT
            # (ADR-3aaa v1.3.x - Library public API detection)
            # Exports from index files (index.ts, index.js) are treated as library
            # entry points since they define the public API surface.
            elif concept_type == "library_export":
                if EntrypointKind.LIBRARY_EXPORT in added_kinds:
                    continue
                # WI-pikib: a library_export entrypoint must be part of the
                # package's PUBLIC API — externally importable. The concept
                # detector also fires on non-public symbols such as a nested
                # private closure (`fold_string_interpolation._sub`, the
                # callback passed to `re.sub`) that no consumer can import.
                # INV-jusot's canonical `Symbol.visibility` axis (computed in
                # finalize step 7b, which runs before detect_entrypoints — see
                # cli.run_behavior_map) lets us reject those. Key on the
                # canonical `visibility` axis (per the Wave-3 §E-5 constraint),
                # not the older is_exported heuristic. A None visibility
                # (uncomputed / legacy map) is left permissive so a genuine
                # export is never suppressed on a missing signal.
                if sym.visibility is not None and sym.visibility != "public":
                    continue
                export_name = concept.get("export_name", sym.name)
                # Handle is_default as bool or string "true"/"false"
                is_default_raw = concept.get("is_default", False)
                is_default = is_default_raw is True or is_default_raw == "true"
                if is_default:
                    label = "Library default export"
                else:
                    label = f"Library export: {export_name}"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.LIBRARY_EXPORT,
                    confidence=0.75,  # Lower than routes, similar to main()
                    label=label,
                    source="concept_detector",
                    evidence_type="language_convention",
                ))
                added_kinds.add(EntrypointKind.LIBRARY_EXPORT)

            # WI-pimig: Go encoding callbacks (UnmarshalJSON, MarshalYAML, etc.).
            # These methods are dispatched reflectively by the encoding library
            # rather than called by name from anywhere in the codebase, so the
            # call-graph BFS treats them as unreachable and dead-code-maybe
            # reports them as candidates. Marking them as LIBRARY_EXPORT
            # entrypoints makes them seeds for the BFS so they (and anything
            # they call) become reachable. On alertmanager this covers 82
            # functions / ~1540 LOC — the largest WI-juhov triage category.
            elif concept_type == "serialization_callback":
                if EntrypointKind.LIBRARY_EXPORT in added_kinds:
                    continue
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.LIBRARY_EXPORT,
                    confidence=0.80,  # Specific signature names → high confidence
                    label=f"Serialization callback: {sym.name}",
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.LIBRARY_EXPORT)

            # Naming-based heuristics (lowest confidence tier)
            # These are fallbacks when no explicit annotation/base class is found
            # ADR-3aaa v1.4.x - naming-conventions.yaml
            elif concept_type == "controller_by_name":
                if EntrypointKind.CONTROLLER in added_kinds:
                    continue  # Already detected via framework pattern
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.CONTROLLER,
                    confidence=0.70,  # Naming heuristic - lowest tier
                    label=f"Controller (by name): {sym.name}",
                    source="concept_detector",
                    evidence_type="naming_heuristic",
                ))
                added_kinds.add(EntrypointKind.CONTROLLER)

            elif concept_type == "handler_by_name":
                if EntrypointKind.CONTROLLER in added_kinds:
                    continue  # Handlers are treated as controllers
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.CONTROLLER,
                    confidence=0.70,  # Naming heuristic - lowest tier
                    label=f"Handler (by name): {sym.name}",
                    source="concept_detector",
                    evidence_type="naming_heuristic",
                ))
                added_kinds.add(EntrypointKind.CONTROLLER)

            # WI-nazir: broker / server lifecycle methods (Kafka-style).
            # ``BrokerServer.startup``, ``KafkaApis.handleProduceRequest``,
            # ``SocketServer.Acceptor.run``, etc. are surfaced as CONTROLLER
            # entrypoints so reverse-slice consumers can root analysis at the
            # broker request-dispatch surface, not just the outer ``Kafka.main``.
            elif concept_type == "broker_lifecycle_by_name":
                if EntrypointKind.CONTROLLER in added_kinds:
                    continue
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.CONTROLLER,
                    confidence=0.70,  # Naming heuristic - lowest tier
                    label=f"Server lifecycle (by name): {sym.name}",
                    source="concept_detector",
                    evidence_type="naming_heuristic",
                ))
                added_kinds.add(EntrypointKind.CONTROLLER)

            elif concept_type == "service_by_name":
                # Services are not entrypoints by default, but we track
                # them for potential future use. Skip for now.
                pass

            # C subcommand functions (cmd_<name>) -> CLI_COMMAND
            # Common in git, systemd, busybox: function pointer dispatch
            # tables map string names to cmd_* handlers.
            elif concept_type == "command_by_name":
                if EntrypointKind.CLI_COMMAND in added_kinds:
                    continue
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.CLI_COMMAND,
                    confidence=0.80,  # Naming convention - higher than generic
                    label=f"CLI command (by name): {sym.name}",
                    source="concept_detector",
                    evidence_type="naming_heuristic",
                ))
                added_kinds.add(EntrypointKind.CLI_COMMAND)

            # Test/benchmark concepts -> TEST_FUNCTION
            # Confidence is moderate (0.80) since test functions are real
            # entry points for test runners. The test-file penalty (0.1x)
            # applied later ensures they don't dominate --entry auto.
            elif concept_type in (
                "test_function", "benchmark_function", "example_function",
            ):
                if EntrypointKind.TEST_FUNCTION in added_kinds:
                    continue
                label_prefix = {
                    "test_function": "Test",
                    "benchmark_function": "Benchmark",
                    "example_function": "Example",
                }.get(concept_type, "Test")
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.TEST_FUNCTION,
                    confidence=0.80,
                    label=f"{label_prefix}: {sym.name}",
                    source="concept_detector",
                    evidence_type="language_convention",
                ))
                added_kinds.add(EntrypointKind.TEST_FUNCTION)

            # Generic entrypoint concept -> framework-specific or MAIN_FUNCTION
            # Used by electron.yaml (app.whenReady), cli.yaml (fire.Fire,
            # argparse.ArgumentParser), cli-go.yaml (kong.Parse),
            # cli-ruby.yaml (OptionParser), akka-http.yaml (Http().newServerAt).
            elif concept_type == "entrypoint":
                # Map to framework-specific kind when possible
                fw_lower = framework.lower() if framework else ""
                if fw_lower == "electron":
                    target_kind = EntrypointKind.ELECTRON_MAIN
                    label = "Electron app entrypoint"
                else:
                    target_kind = EntrypointKind.MAIN_FUNCTION
                    if framework:
                        label = f"{framework.title()} entrypoint"
                    else:
                        label = "App entrypoint"
                if target_kind in added_kinds:
                    continue
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=target_kind,
                    confidence=0.90,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(target_kind)

            # SPA bootstrap concept -> SPA_BOOTSTRAP
            # Detected from react.yaml, solid.yaml, etc. patterns:
            # createRoot(), ReactDOM.render(), render(), hydrateRoot(),
            # createBrowserRouter().
            #
            # When multiple frameworks claim app_bootstrap for the same
            # symbol (e.g., both React and Solid match bare createRoot()),
            # disambiguate by preferring the more specific framework.
            elif concept_type == "app_bootstrap":
                if EntrypointKind.SPA_BOOTSTRAP in added_kinds:
                    continue
                best_fw = _pick_best_bootstrap_framework(concepts)
                if best_fw:
                    label = f"{best_fw.title()} app bootstrap"
                else:
                    label = "SPA bootstrap"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.SPA_BOOTSTRAP,
                    confidence=0.90,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.SPA_BOOTSTRAP)

            # IPC handler concept -> EVENT_HANDLER
            # Used by electron.yaml (ipcMain.on/handle) and tauri.yaml
            # (#[tauri::command]). IPC handlers are cross-language entry
            # points analogous to HTTP routes.
            elif concept_type == "ipc_handler":
                if EntrypointKind.EVENT_HANDLER in added_kinds:
                    continue
                if framework:
                    label = f"{framework.title()} IPC handler"
                else:
                    label = "IPC handler"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.EVENT_HANDLER,
                    confidence=0.90,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.EVENT_HANDLER)

            # Application concept -> MAIN_FUNCTION
            # Used by http4s.yaml (extends IOApp, extends IOApp.Simple),
            # zio.yaml (extends ZIOAppDefault), and similar patterns where
            # a class/object IS the application entry point.
            elif concept_type == "application":
                if EntrypointKind.MAIN_FUNCTION in added_kinds:
                    continue
                if framework:
                    label = f"{framework.title()} application"
                else:
                    label = "Application entrypoint"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.MAIN_FUNCTION,
                    confidence=0.90,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.MAIN_FUNCTION)

            # Servlet concept -> CONTROLLER
            # Used by scalatra.yaml (extends ScalatraServlet/ScalatraFilter),
            # servlet.yaml (extends HttpServlet).
            elif concept_type == "servlet":
                if EntrypointKind.CONTROLLER in added_kinds:
                    continue
                if framework:
                    label = f"{framework.title()} servlet"
                else:
                    label = "Servlet"
                entrypoints.append(Entrypoint.create(
                    symbol_id=sym.id,
                    kind=EntrypointKind.CONTROLLER,
                    confidence=0.90,
                    label=label,
                    source="concept_detector",
                    evidence_type="framework_pattern",
                ))
                added_kinds.add(EntrypointKind.CONTROLLER)

    # --- Pass 2: Direct route symbol detection ---
    # Go (and potentially other analyzers) create symbols with kind="route"
    # that carry route metadata (route_path, http_method) directly in sym.meta
    # rather than going through YAML concept enrichment.  These symbols are
    # found by the routes CLI command (which checks both concepts and kind)
    # but were missed by entrypoint detection until now.
    # Track existing route/websocket entrypoint symbol_ids to avoid duplicates.
    route_ep_ids = {
        ep.symbol_id for ep in entrypoints
        if ep.kind in (EntrypointKind.HTTP_ROUTE, EntrypointKind.WEBSOCKET_HANDLER)
    }

    for sym in symbols:
        if (sym.meta or {}).get("framework_role") != "route":
            continue
        if sym.id in route_ep_ids:
            continue
        info = route_of(sym)
        protocol = info["protocol"]
        method = method_token(info) or ""
        path = info["path"] or ""
        # WI-kuvig: a route whose transport is WebSocket is a WebSocket handler,
        # not an HTTP route (INV-tibap: the transport now lives in route_protocol,
        # normalized by route_of; only the entrypoint kind reflects it).
        if protocol == "websocket":
            entrypoints.append(Entrypoint.create(
                symbol_id=sym.id,
                kind=EntrypointKind.WEBSOCKET_HANDLER,
                confidence=0.90,  # Slightly lower than concept-enriched (0.95)
                label=_ws_route_label(path, info["framework"] or ""),
                source="concept_detector",
                evidence_type="framework_pattern",
            ))
            continue
        if method and path:
            label = f"HTTP {method.upper()} {path}"
        elif method:
            label = f"HTTP {method.upper()} route"
        elif path:
            label = f"HTTP route {path}"
        else:
            label = "HTTP route"
        entrypoints.append(Entrypoint.create(
            symbol_id=sym.id,
            kind=EntrypointKind.HTTP_ROUTE,
            confidence=0.90,  # Slightly lower than concept-enriched (0.95)
            label=label,
            source="concept_detector",
            evidence_type="framework_pattern",
        ))

    return entrypoints


# ADR-0027 Phase-2 audit (WI-jukav): all members are AXIS_LANGUAGE_CONSTRUCT
# (Cluster A) and stable across Phase 3. Intentionally wider than
# ``ranking._CALLABLE_KINDS`` — connectivity-fallback entrypoints can
# legitimately be constructors (e.g., a service object's __init__ wires
# up dependencies and forms a real graph hub). Forward-compatible.
_CALLABLE_KINDS = frozenset({"function", "method", "constructor"})
_MAX_CONNECTIVITY_ENTRYPOINTS = 5


def _connectivity_fallback(
    nodes: List[Symbol],
    edges: List[Edge],
) -> List[Entrypoint]:
    """Select entrypoints based on outgoing edge count when no patterns match.

    This is a last-resort fallback for repos where no YAML patterns produce
    entrypoints (e.g., Rust libraries without visibility tracking, or repos
    in languages without any framework/convention patterns).

    Only callable symbols (functions, methods, constructors) are considered.
    Confidence is set to 0.50, lower than any concept-based entrypoint, so
    these never outrank pattern-detected entries in mixed scenarios.

    Returns at most _MAX_CONNECTIVITY_ENTRYPOINTS entries, sorted by
    descending out-degree.
    """
    # Count outgoing edges per symbol
    outgoing: dict[str, int] = defaultdict(int)
    for edge in edges:
        outgoing[edge.src] += 1

    # Filter to callable symbols with at least one outgoing edge
    candidates: list[tuple[Symbol, int]] = []
    for node in nodes:
        if node.kind not in _CALLABLE_KINDS:
            continue
        out_count = outgoing.get(node.id, 0)
        if out_count > 0:
            candidates.append((node, out_count))

    if not candidates:
        return []

    # Sort by out-degree descending, take top N
    candidates.sort(key=lambda x: x[1], reverse=True)
    top = candidates[:_MAX_CONNECTIVITY_ENTRYPOINTS]

    entrypoints: List[Entrypoint] = []
    for sym, _out_count in top:
        lang = sym.language or "unknown"
        entrypoints.append(Entrypoint.create(
            symbol_id=sym.id,
            kind=EntrypointKind.CONNECTIVITY_BASED,
            confidence=0.50,
            label=f"High-connectivity {lang} {sym.kind}: {sym.name}",
            source="connectivity_fallback",
            evidence_type="connectivity_heuristic",
        ))

    return entrypoints


_SCRIPT_MODULE_LANGUAGES = frozenset({"typescript", "javascript"})


def _detect_script_modules(
    nodes: List[Symbol],
    edges: List[Edge],
) -> List[Entrypoint]:
    """INV-tajap PR 3: detect TS/JS standalone-script entrypoints.

    A ``SCRIPT_MODULE`` entrypoint is a TS/JS file-kind Symbol that satisfies
    both:

    1. **No inbound ``imports`` edges** — no other file imports this one,
       so the file isn't a library module.
    2. **At least one outbound ``calls`` edge** — the file does work at
       module load (the proxy for "top-level executable code", spec rule
       from the INV-tajap tracker).

    Unlike SHELL_SCRIPT / HTML_ENTRY which derive from analyzer-stamped
    ``meta.concepts``, this rule needs the full edge set (inbound-imports
    can only be answered after every analyzer + linker has run), so the
    detection lives in ``detect_entrypoints`` itself instead of in
    ``_detect_from_concepts``.

    Confidence 0.80 (one tier below shell_script / html_entry / main_guard
    at 0.85, because the rule is a structural inference rather than a
    direct concept match).
    """
    # Precompute lookup sets so the per-symbol checks are O(1).
    inbound_import_dsts = {
        e.dst for e in edges if e.edge_type == "imports"
    }
    outbound_call_srcs = {
        e.src for e in edges if e.edge_type == "calls"
    }

    entrypoints: List[Entrypoint] = []
    for sym in nodes:
        if sym.kind != "file":
            continue
        if sym.language not in _SCRIPT_MODULE_LANGUAGES:
            continue
        if sym.id in inbound_import_dsts:
            continue  # someone imports this file → library module, not a script
        if sym.id not in outbound_call_srcs:
            continue  # no outbound calls → inert data, not executable code
        script_path = sym.path or sym.name
        entrypoints.append(Entrypoint.create(
            symbol_id=sym.id,
            kind=EntrypointKind.SCRIPT_MODULE,
            confidence=0.80,
            label=f"Script module ({script_path})",
            source="script_module_detector",
            evidence_type="structural",
        ))
    return entrypoints


def detect_entrypoints(
    nodes: List[Symbol],
    edges: List[Edge],
) -> List[Entrypoint]:
    """Detect entrypoints in the codebase using semantic detection.

    Detection sources (ADR-3aaa v1.2.x):
    1. Framework patterns (HTTP routes, CLI commands, controllers, etc.)
       - Highest confidence (0.95)
       - Detected via YAML patterns matching decorators, base classes, etc.
    2. Language conventions (main() functions)
       - Lower confidence (0.80) - may have many in a repo
       - Detected via YAML patterns matching symbol name + kind + language

    All detection is now YAML-driven via framework_patterns.py. Symbols are
    enriched with concept metadata during the FRAMEWORK_PATTERNS phase, and
    this function maps those concepts to entrypoint kinds.

    Confidence Adjustments:
    - Test files: 90% penalty (x 0.1, deprioritized not excluded)
    - Utility files: 50% penalty (x 0.5)
    - Vendor/external deps (tier >= 3): 70% penalty (x 0.3)
    - Connectivity: Up to +25% boost for entrypoints with many outgoing edges

    Args:
        nodes: All symbols in the codebase (with concept metadata from enrichment).
        edges: All edges (used for connectivity boost).

    Returns:
        List of detected entrypoints with confidence scores, sorted by confidence.
    """
    # Semantic detection from concept metadata (YAML patterns)
    # This includes both framework patterns (routes, commands) and
    # language conventions (main functions)
    entrypoints = _detect_from_concepts(nodes)

    # INV-tajap PR 3: TS/JS standalone scripts — needs the edge set, so
    # detected here rather than via a concept handler.
    entrypoints.extend(_detect_script_modules(nodes, edges))

    # Remove duplicates (same symbol detected by multiple strategies)
    # Keep the first (highest confidence) entry for each symbol
    seen_ids: set[str] = set()
    unique_entrypoints: List[Entrypoint] = []
    for ep in entrypoints:
        if ep.symbol_id not in seen_ids:
            seen_ids.add(ep.symbol_id)
            unique_entrypoints.append(ep)

    # Deduplicate declaration vs definition entrypoints (C forward declarations).
    # Functions like cmd_add appear twice: once from builtin.h (declaration) and
    # once from builtin/add.c (definition). They have different symbol_ids so the
    # above dedup doesn't catch them. When both exist for the same symbol name,
    # keep only the definition (non-declaration).
    symbol_lookup_for_dedup: dict[str, Symbol] = {node.id: node for node in nodes}
    name_groups: dict[str, list[Entrypoint]] = defaultdict(list)
    for ep in unique_entrypoints:
        sym = symbol_lookup_for_dedup.get(ep.symbol_id)
        if sym:
            name_groups[sym.name].append(ep)
    deduped_entrypoints: List[Entrypoint] = []
    for _name, eps in name_groups.items():
        if len(eps) > 1:
            # Prefer non-declaration symbols over declarations
            non_decl = [
                ep for ep in eps
                if "declaration" not in (
                    symbol_lookup_for_dedup.get(ep.symbol_id).modifiers
                    if symbol_lookup_for_dedup.get(ep.symbol_id) else []
                )
            ]
            deduped_entrypoints.extend(non_decl if non_decl else eps)
        else:
            deduped_entrypoints.extend(eps)
    unique_entrypoints = deduped_entrypoints

    # INV-hosuh + WI-tuvun: collapse module-level main-guard + function-level
    # main() in the same script to a single entry. The main-guard
    # (kind=MAIN_GUARD on the module/file symbol, from the ``main_guard``
    # concept) and the main() function (kind=MAIN_FUNCTION on the function
    # symbol, from the ``main_function`` concept) both fire for the same
    # script when both are present. The function-level entry is the canonical
    # "real" entrypoint; the main-guard marker is redundant once we have it
    # (the ``fn_eps`` filter keeps only function-target entries per path).
    # Scripts that have only one of the two (e.g., __main__.py with no
    # separate main() -> lone MAIN_GUARD, or a bare main() with no __main__
    # block -> lone MAIN_FUNCTION) keep their single entry.
    main_by_path: dict[str, list[Entrypoint]] = defaultdict(list)
    non_main_eps: list[Entrypoint] = []
    for ep in unique_entrypoints:
        if ep.kind not in (
            EntrypointKind.MAIN_FUNCTION,
            EntrypointKind.MAIN_GUARD,
        ):
            non_main_eps.append(ep)
            continue
        sym = symbol_lookup_for_dedup.get(ep.symbol_id)
        if sym is None or not sym.path:  # pragma: no cover - defensive: entrypoints are built from nodes
            non_main_eps.append(ep)
            continue
        main_by_path[sym.path].append(ep)
    deduped_main_eps: list[Entrypoint] = []
    for _path, eps in main_by_path.items():
        if len(eps) <= 1:
            deduped_main_eps.extend(eps)
            continue
        fn_eps = [
            ep for ep in eps
            if symbol_lookup_for_dedup[ep.symbol_id].kind == "function"
        ]
        deduped_main_eps.extend(fn_eps if fn_eps else eps)
    unique_entrypoints = non_main_eps + deduped_main_eps

    # Route-label deduplication: multiple symbols (route symbol, handler
    # method, different registration sites) can produce HTTP_ROUTE entrypoints
    # with the same (method, path).  In prometheus, POST /-/quit appears 14x.
    # Group by label and keep the highest-confidence entry per unique label.
    _ROUTE_KINDS = frozenset({
        EntrypointKind.HTTP_ROUTE,
        EntrypointKind.EXPRESS_ROUTE,
        EntrypointKind.SINATRA_ROUTE,
        EntrypointKind.KTOR_ROUTE,
        EntrypointKind.VAPOR_ROUTE,
        EntrypointKind.PLUG_ROUTE,
        EntrypointKind.HAPI_ROUTE,
        EntrypointKind.FASTIFY_ROUTE,
        EntrypointKind.KOA_ROUTE,
        EntrypointKind.SLIM_ROUTE,
        EntrypointKind.RUST_HANDLER,
    })
    route_label_groups: dict[str, list[Entrypoint]] = defaultdict(list)
    non_route_eps: list[Entrypoint] = []
    for ep in unique_entrypoints:
        if ep.kind in _ROUTE_KINDS and ep.label.startswith("HTTP "):
            route_label_groups[ep.label].append(ep)
        elif ep.kind == EntrypointKind.WEBSOCKET_HANDLER and ep.label.startswith("WS "):
            # Path-bearing WS routes ("WS /ws") dedup like HTTP routes so the
            # handler concept and the minted route symbol collapse to one;
            # pathless WS handlers ("<Framework> WebSocket handler") fall
            # through and stay distinct (WI-kuvig/WI-lomoz).
            route_label_groups[ep.label].append(ep)
        else:
            non_route_eps.append(ep)
    for _label, eps in route_label_groups.items():
        eps.sort(key=lambda e: e.confidence, reverse=True)
        non_route_eps.append(eps[0])
    unique_entrypoints = non_route_eps

    # Connectivity-based fallback: when no concept-based entrypoints found,
    # select the most-connected callable symbols as pseudo-entrypoints.
    # This ensures --entry auto never hard-fails, even for repos with no
    # matching YAML patterns (e.g., Rust libraries without pub tracking).
    if not unique_entrypoints:
        unique_entrypoints = _connectivity_fallback(nodes, edges)

    # Build lookup from symbol_id to Symbol for penalty calculations
    symbol_lookup: dict[str, Symbol] = {node.id: node for node in nodes}

    # Apply penalties for test files and vendor code
    # This deprioritizes them without removing them from the list
    for ep in unique_entrypoints:
        sym = symbol_lookup.get(ep.symbol_id)
        if sym is None:
            continue  # pragma: no cover - symbol should always exist

        # Penalty for test files (90% reduction).
        # Must be aggressive: in repos like DMD where 98% of main()
        # functions are in test files, a modest penalty (0.5) leaves
        # test entrypoints at 0.40 confidence — high enough to dominate
        # auto-slicing selections.  0.1 pushes them to 0.08, well below
        # any production entrypoint.
        if sym.path and is_test_file(sym.path):
            ep.confidence *= 0.1

        # Penalty for utility/example/docs files (50% reduction)
        # These are demonstration code, not production entrypoints
        if sym.path and is_utility_file(sym.path):
            ep.confidence *= 0.5

        # Penalty for vendor/external dependencies (70% reduction)
        # Tier 3 = external deps, Tier 4 = derived/build artifacts
        if sym.supply_chain_tier >= 3:
            ep.confidence *= 0.3

        # Penalty for Go main()s in deeply nested cmd/ directories (50%).
        # In Go repos, top-level cmd/<name>/ holds the primary application
        # binaries.  Deeply nested cmd/ (e.g., builtin/logical/consul/cmd/)
        # holds plugin shims and tool binaries that should not outrank the
        # actual server entrypoint.  Depth threshold: cmd/ preceded by 2+
        # path components is considered "deeply nested".
        if (
            ep.kind == EntrypointKind.MAIN_FUNCTION
            and sym.language == "go"
            and sym.path
        ):
            parts = sym.path.split("/")
            for i, part in enumerate(parts):
                if part == "cmd" and i >= 2:
                    ep.confidence *= 0.5
                    break

    # Application demotion: when real semantic entrypoints exist (routes,
    # commands, main, controllers), library_export entries are just API
    # visibility markers, not developer-facing entrypoints.  Heavily demote
    # them so routes/commands dominate the entrypoint list.
    #
    # This fixes the "forgejo problem": 7,474 Go uppercase-symbol exports
    # drowning out 772 meaningful HTTP routes.  The demotion is language-
    # agnostic — any app with routes+exports benefits.
    #
    # Only semantic entrypoints trigger demotion.  TEST_FUNCTION and
    # LIBRARY_EXPORT themselves do not count (a library with tests but
    # no routes should keep its exports at full confidence).
    _SEMANTIC_KINDS = frozenset({
        EntrypointKind.HTTP_ROUTE,
        EntrypointKind.CLI_COMMAND,
        EntrypointKind.CLI_MAIN,
        EntrypointKind.MAIN_FUNCTION,
        EntrypointKind.CONTROLLER,
        EntrypointKind.DJANGO_VIEW,
        EntrypointKind.EXPRESS_ROUTE,
        EntrypointKind.NESTJS_CONTROLLER,
        EntrypointKind.SPRING_CONTROLLER,
        EntrypointKind.RAILS_CONTROLLER,
        EntrypointKind.PHOENIX_CONTROLLER,
        EntrypointKind.GO_HANDLER,
        EntrypointKind.LARAVEL_CONTROLLER,
        EntrypointKind.RUST_HANDLER,
        EntrypointKind.ASPNET_CONTROLLER,
        EntrypointKind.SINATRA_ROUTE,
        EntrypointKind.KTOR_ROUTE,
        EntrypointKind.VAPOR_ROUTE,
        EntrypointKind.PLUG_ROUTE,
        EntrypointKind.HAPI_ROUTE,
        EntrypointKind.FASTIFY_ROUTE,
        EntrypointKind.KOA_ROUTE,
        EntrypointKind.GRAPE_API,
        EntrypointKind.TORNADO_HANDLER,
        EntrypointKind.AIOHTTP_VIEW,
        EntrypointKind.SLIM_ROUTE,
        EntrypointKind.MICRONAUT_CONTROLLER,
        EntrypointKind.GRAPHQL_SERVER,
        EntrypointKind.BACKGROUND_TASK,
        EntrypointKind.WEBSOCKET_HANDLER,
        EntrypointKind.EVENT_HANDLER,
        EntrypointKind.SCHEDULED_TASK,
        EntrypointKind.ELECTRON_MAIN,
        EntrypointKind.ANDROID_ACTIVITY,
        EntrypointKind.ANDROID_APPLICATION,
    })
    # Per-language demotion: only demote library_export entries when their
    # own language has semantic entrypoints.  This prevents the ArkLib
    # problem: 6 Python helper scripts (with main()) should NOT suppress
    # 163 Lean library_export entries.  Lean exports ARE the right
    # entrypoints for a Lean library — Python main() is incidental tooling.
    langs_with_semantic: set[str] = set()
    for ep in unique_entrypoints:
        if ep.kind in _SEMANTIC_KINDS:
            sym = symbol_lookup.get(ep.symbol_id)
            if sym and sym.language:
                # Skip entrypoints from example/utility files — these are
                # demonstration code, not real application entrypoints that
                # should trigger library_export demotion.  Fixes the
                # "livekit problem": example HTTP routes in examples/rpc/
                # were demoting core library exports like Room.
                if sym.path and is_utility_file(sym.path):
                    continue
                langs_with_semantic.add(sym.language)
    infra_export_ids: set[str] = set()
    if langs_with_semantic:
        for ep in unique_entrypoints:
            if ep.kind == EntrypointKind.LIBRARY_EXPORT:
                sym = symbol_lookup.get(ep.symbol_id)
                lang = sym.language if sym else None
                if lang and lang in langs_with_semantic:
                    ep.confidence *= 0.1  # 90% reduction, same as test penalty
                    # Infrastructure-path exports (telemetry/, metrics/, logging/)
                    # are internal plumbing, not developer-facing API.  Track
                    # them so connectivity boost skips them (like tests).
                    if sym and sym.path and is_infrastructure_path(sym.path):
                        infra_export_ids.add(ep.symbol_id)

    # Language dominance: prefer entrypoints from the dominant language.
    # In a repo that's 95% C and 5% Python, a C main() should rank above
    # a Python script even when the script has higher connectivity.
    # Weight = symbol_count / max_language_count, so the dominant language
    # always gets 1.0 and minority languages get proportionally less.
    lang_symbol_counts: dict[str, int] = defaultdict(int)
    for node in nodes:
        if node.language:
            lang_symbol_counts[node.language] += 1
    max_lang_count = max(lang_symbol_counts.values()) if lang_symbol_counts else 1

    def _language_weight(symbol_id: str) -> float:
        """Language dominance weight: 1.0 for dominant, less for minority."""
        sym = symbol_lookup.get(symbol_id)
        if not sym or not sym.language:
            return 0.0
        return lang_symbol_counts.get(sym.language, 0) / max_lang_count

    # Boost confidence based on connectivity (outgoing edges).
    # Uses one-hop transitive reach: an entrypoint's effective out-degree
    # includes the out-degree of its immediate callees (at 50% weight).
    # This handles the "thin wrapper" pattern: main() → tryMain() (178 edges)
    # gets credit for tryMain's connectivity, outranking utility mains that
    # call many small functions directly.
    #
    # The boost is scaled by language dominance: entrypoints from the
    # dominant language get the full boost, while minority languages get
    # a reduced boost (minimum 50%). This prevents a Python script with
    # many outgoing edges from outranking a C main() in a 95% C codebase.
    outgoing_counts: dict[str, int] = defaultdict(int)
    incoming_counts: dict[str, int] = defaultdict(int)
    direct_callees: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        outgoing_counts[edge.src] += 1
        incoming_counts[edge.dst] += 1
        direct_callees[edge.src].append(edge.dst)

    def _effective_out_degree(symbol_id: str) -> float:
        """Compute effective out-degree with one-hop transitive credit."""
        direct = outgoing_counts.get(symbol_id, 0)
        # Add callees' out-degree at 50% weight (one hop only, no recursion)
        transitive = sum(
            outgoing_counts.get(callee, 0) for callee in direct_callees[symbol_id]
        )
        return direct + 0.5 * transitive

    for ep in unique_entrypoints:
        # Skip connectivity boost for CONNECTIVITY_BASED entrypoints:
        # their selection already incorporates out-degree ranking, so
        # boosting again would double-count connectivity.
        if ep.kind == EntrypointKind.CONNECTIVITY_BASED:
            continue
        # Skip connectivity boost for TEST_FUNCTION entrypoints and any
        # entrypoint in a test file.  Without this, a test function with
        # 0.80 * 0.1 (test penalty) = 0.08 gets boosted to 0.33 by
        # connectivity, passing the MIN_ENTRYPOINT_CONFIDENCE filter and
        # flooding entries.txt with hundreds of test functions.
        if ep.kind == EntrypointKind.TEST_FUNCTION:
            continue
        sym = symbol_lookup.get(ep.symbol_id)
        if sym and sym.path and is_test_file(sym.path):
            continue
        # Skip connectivity boost for infrastructure-path library exports
        # (telemetry/, metrics/, logging/).  Like test functions, these are
        # already demoted and the additive boost would bring them back above
        # the confidence threshold.  In gemini-cli, 77 telemetry exports
        # survived at ~0.14 confidence via connectivity boost.
        if ep.symbol_id in infra_export_ids:
            continue
        effective_edges = _effective_out_degree(ep.symbol_id)
        in_degree = incoming_counts.get(ep.symbol_id, 0)

        # For library_export entrypoints, in-degree (how many callers
        # depend on this) is more relevant than out-degree. A class like
        # ByteBuf (in-degree 787) should rank above a microbenchmark main
        # (in-degree 0) even though both have high out-degree. Cap at 0.35
        # (higher than the 0.25 out-degree cap) because in-degree directly
        # measures architectural importance.
        if ep.kind == EntrypointKind.LIBRARY_EXPORT and in_degree > 0:
            in_boost = min(0.35, math.log(1 + in_degree) / 8)
            lang_scale = 0.5 + 0.5 * _language_weight(ep.symbol_id)
            ep.confidence = min(1.0, ep.confidence + in_boost * lang_scale)

        if effective_edges > 0:
            # Logarithmic boost: diminishing returns for very high counts
            # log(1 + 10) / 10 ≈ 0.24, log(1 + 100) / 10 ≈ 0.46
            # Cap at 0.25 to avoid overwhelming the base confidence
            raw_boost = min(0.25, math.log(1 + effective_edges) / 10)
            # Scale by language dominance: dominant language (1.0) gets
            # full boost, minority language (0.05) gets ~53% of boost.
            # Formula: 0.5 + 0.5 * weight → range [0.5, 1.0]
            lang_scale = 0.5 + 0.5 * _language_weight(ep.symbol_id)
            connectivity_boost = raw_boost * lang_scale
            ep.confidence = min(1.0, ep.confidence + connectivity_boost)

    # Sort by confidence (highest first) for better --entry auto behavior.
    # Tiebreakers (in order):
    # 1. Language dominance: prefer entrypoints from the dominant language.
    #    In a C-heavy repo, C main() ranks above Python script even if
    #    the Python script has higher connectivity.
    # 2. Effective out-degree: when two entrypoints from the same language
    #    tie on confidence, prefer the one with higher transitive reach.
    unique_entrypoints.sort(
        key=lambda ep: (
            ep.confidence,
            _language_weight(ep.symbol_id),
            _effective_out_degree(ep.symbol_id),
        ),
        reverse=True,
    )

    # Filter out entries below minimum confidence threshold.
    # After all adjustments (test penalty, vendor penalty, library demotion),
    # entries with very low confidence are noise — e.g., library_export
    # entries demoted from 0.80 to 0.08 when semantic entries exist.
    unique_entrypoints = [
        ep for ep in unique_entrypoints
        if ep.confidence >= MIN_ENTRYPOINT_CONFIDENCE
    ]

    # Cap the number of returned entrypoints.
    # The cap scales with repo size: small repos get 50, large repos up to 500.
    # This ensures large codebases (GitLab: 90K+ files, 693+ controllers)
    # don't lose most of their entrypoints to a fixed cap.
    cap = compute_entrypoint_cap(len(nodes))
    if len(unique_entrypoints) > cap:
        unique_entrypoints = _diversity_cap(unique_entrypoints, cap)

    return unique_entrypoints


# Maximum fraction of the entrypoint cap that a single kind may occupy.
# With 0.4, at most 40% of entrypoints can be of one kind (e.g., max 200
# GraphQL resolvers out of 500 total). This ensures diverse entrypoint types.
_MAX_KIND_FRACTION: float = 0.4


def _diversity_cap(
    entrypoints: list[Entrypoint],
    cap: int,
) -> list[Entrypoint]:
    """Apply per-kind diversity capping to entrypoints.

    Ensures no single EntrypointKind takes more than _MAX_KIND_FRACTION of the
    total cap. Entrypoints are assumed to be pre-sorted by score (best first).
    When a kind exceeds its quota, remaining entrypoints of that kind are
    deferred to fill any remaining slots after all kinds have had their share.

    Args:
        entrypoints: Sorted list of entrypoints (best first).
        cap: Maximum total entrypoints to return.

    Returns:
        Capped list with per-kind diversity guarantee.
    """
    max_per_kind = max(1, int(cap * _MAX_KIND_FRACTION))
    kind_counts: dict[EntrypointKind, int] = {}
    result: list[Entrypoint] = []
    overflow: list[Entrypoint] = []

    for ep in entrypoints:
        count = kind_counts.get(ep.kind, 0)
        if count < max_per_kind:
            result.append(ep)
            kind_counts[ep.kind] = count + 1
        else:
            overflow.append(ep)

        if len(result) >= cap:
            return result[:cap]

    # Fill remaining slots from overflow (kinds that exceeded their quota)
    remaining = cap - len(result)
    if remaining > 0 and overflow:
        result.extend(overflow[:remaining])

    return result
