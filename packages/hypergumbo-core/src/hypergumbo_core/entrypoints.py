"""Entrypoint detection for code analysis using YAML-driven pattern matching.

Detects entrypoints via semantic concepts from YAML framework patterns (ADR-0003):
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

Detection is based on two mechanisms (ADR-0003):

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

Connectivity Boost:
- Entrypoints with outgoing edges get a confidence boost (up to +0.25)
- Boost formula: min(0.25, log(1 + out_edges) / 10)
- This ranks "interesting" entrypoints higher (those that call many functions)
- Entrypoints are sorted by final confidence (highest first)
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import List

from .ir import Symbol, Edge
from .paths import is_test_file, is_utility_file


class EntrypointKind(Enum):
    """Types of entrypoints that can be detected."""

    HTTP_ROUTE = "http_route"
    CLI_MAIN = "cli_main"
    CLI_COMMAND = "cli_command"
    # Language-level main() entry points (detected via YAML patterns)
    MAIN_FUNCTION = "main_function"
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
    # Semantic concept-based entry kinds (ADR-0003 v0.9.x)
    CONTROLLER = "controller"  # Generic controller from concept metadata
    BACKGROUND_TASK = "background_task"  # Async/background task
    WEBSOCKET_HANDLER = "websocket_handler"  # WebSocket event handler
    EVENT_HANDLER = "event_handler"  # Event/message handler
    SCHEDULED_TASK = "scheduled_task"  # Cron/scheduled job
    # Library entry points (exported API)
    LIBRARY_EXPORT = "library_export"  # Exported function/class (library entry)


@dataclass
class Entrypoint:
    """A detected entrypoint in the codebase.

    Attributes:
        symbol_id: ID of the symbol that is an entrypoint.
        kind: Type of entrypoint detected.
        confidence: Confidence score (0.0-1.0).
        label: Human-readable label for the entrypoint.
    """

    symbol_id: str
    kind: EntrypointKind
    confidence: float
    label: str

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "symbol_id": self.symbol_id,
            "kind": self.kind.value,
            "confidence": self.confidence,
            "label": self.label,
        }


def _detect_from_concepts(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect entrypoints from semantic concept metadata.

    ADR-0003 introduces semantic entry detection: the FRAMEWORK_PATTERNS phase
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
    - "event_handler" -> EVENT_HANDLER (event/message handler)
    - "command" -> CLI_COMMAND (CLI command handler)
    - "liveview" -> CONTROLLER (Phoenix LiveView - real-time UI)
    - "graphql_resolver" -> GRAPHQL_SERVER (GraphQL resolver)
    - "graphql_schema" -> GRAPHQL_SERVER (GraphQL schema definition)
    - "lifecycle_hook" -> ANDROID_ACTIVITY/ANDROID_APPLICATION/CONTROLLER (Android lifecycle)

    Detected concepts (language conventions, confidence=0.80):
    - "main_function" -> MAIN_FUNCTION (Go, Java, Python, C, etc.)

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

            # Route concept -> HTTP_ROUTE
            if concept_type == "route":
                if EntrypointKind.HTTP_ROUTE in added_kinds:
                    continue
                method = concept.get("method", "")
                path = concept.get("path", "")
                if method and path:
                    label = f"HTTP {method.upper()} {path}"
                elif method:
                    label = f"HTTP {method.upper()} route"
                elif path:
                    label = f"HTTP route {path}"
                else:
                    label = "HTTP route"
                entrypoints.append(Entrypoint(
                    symbol_id=sym.id,
                    kind=EntrypointKind.HTTP_ROUTE,
                    confidence=0.95,
                    label=label,
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
                entrypoints.append(Entrypoint(
                    symbol_id=sym.id,
                    kind=EntrypointKind.CONTROLLER,
                    confidence=0.95,
                    label=label,
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
                entrypoints.append(Entrypoint(
                    symbol_id=sym.id,
                    kind=EntrypointKind.BACKGROUND_TASK,
                    confidence=0.95,
                    label=label,
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
                entrypoints.append(Entrypoint(
                    symbol_id=sym.id,
                    kind=EntrypointKind.SCHEDULED_TASK,
                    confidence=0.95,
                    label=label,
                ))
                added_kinds.add(EntrypointKind.SCHEDULED_TASK)

            # WebSocket handler concepts -> WEBSOCKET_HANDLER
            elif concept_type in ("websocket_handler", "websocket_gateway"):
                if EntrypointKind.WEBSOCKET_HANDLER in added_kinds:
                    continue
                if framework:
                    label = f"{framework.title()} WebSocket handler"
                else:
                    label = "WebSocket handler"
                entrypoints.append(Entrypoint(
                    symbol_id=sym.id,
                    kind=EntrypointKind.WEBSOCKET_HANDLER,
                    confidence=0.95,
                    label=label,
                ))
                added_kinds.add(EntrypointKind.WEBSOCKET_HANDLER)

            # Event handler concept -> EVENT_HANDLER
            elif concept_type == "event_handler":
                if EntrypointKind.EVENT_HANDLER in added_kinds:
                    continue
                if framework:
                    label = f"{framework.title()} event handler"
                else:
                    label = "Event handler"
                entrypoints.append(Entrypoint(
                    symbol_id=sym.id,
                    kind=EntrypointKind.EVENT_HANDLER,
                    confidence=0.95,
                    label=label,
                ))
                added_kinds.add(EntrypointKind.EVENT_HANDLER)

            # Command concept -> CLI_COMMAND
            elif concept_type == "command":
                if EntrypointKind.CLI_COMMAND in added_kinds:
                    continue
                if framework:
                    label = f"{framework.title()} command"
                else:
                    label = "CLI command"
                entrypoints.append(Entrypoint(
                    symbol_id=sym.id,
                    kind=EntrypointKind.CLI_COMMAND,
                    confidence=0.95,
                    label=label,
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
                entrypoints.append(Entrypoint(
                    symbol_id=sym.id,
                    kind=EntrypointKind.CLI_COMMAND,
                    confidence=0.99,  # Declared in manifest - highest confidence
                    label=label,
                ))
                added_kinds.add(EntrypointKind.CLI_COMMAND)

            # LiveView concept -> CONTROLLER (real-time UI is an entry point)
            elif concept_type == "liveview":
                if EntrypointKind.CONTROLLER in added_kinds:
                    continue
                label = "Phoenix LiveView"
                entrypoints.append(Entrypoint(
                    symbol_id=sym.id,
                    kind=EntrypointKind.CONTROLLER,
                    confidence=0.95,
                    label=label,
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
                entrypoints.append(Entrypoint(
                    symbol_id=sym.id,
                    kind=EntrypointKind.GRAPHQL_SERVER,
                    confidence=0.95,
                    label=label,
                ))
                added_kinds.add(EntrypointKind.GRAPHQL_SERVER)

            # Lifecycle hook concept -> ANDROID_ACTIVITY or ANDROID_APPLICATION
            # (ADR-0003 v1.1.x - pattern-based Android detection)
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
                    entrypoints.append(Entrypoint(
                        symbol_id=sym.id,
                        kind=EntrypointKind.ANDROID_ACTIVITY,
                        confidence=0.95,
                        label=f"Android Activity ({class_name})",
                    ))
                    added_kinds.add(EntrypointKind.ANDROID_ACTIVITY)
                elif matched_base in ("Application", "MultiDexApplication"):
                    if EntrypointKind.ANDROID_APPLICATION in added_kinds:
                        continue  # pragma: no cover - defensive deduplication
                    parts = sym.name.rsplit(".", 1)
                    class_name = parts[0] if len(parts) == 2 else sym.name
                    entrypoints.append(Entrypoint(
                        symbol_id=sym.id,
                        kind=EntrypointKind.ANDROID_APPLICATION,
                        confidence=0.95,
                        label=f"Android Application ({class_name})",
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
                    entrypoints.append(Entrypoint(
                        symbol_id=sym.id,
                        kind=EntrypointKind.CONTROLLER,
                        confidence=0.95,
                        label=f"Android {matched_base} ({class_name})",
                    ))
                    added_kinds.add(EntrypointKind.CONTROLLER)

            # Language-level main() function concept -> MAIN_FUNCTION
            # (ADR-0003 v1.2.x - YAML-based language convention patterns)
            elif concept_type == "main_function":
                if EntrypointKind.MAIN_FUNCTION in added_kinds:
                    continue
                # Derive label from symbol's language
                lang = sym.language.title() if sym.language else "Unknown"
                entrypoints.append(Entrypoint(
                    symbol_id=sym.id,
                    kind=EntrypointKind.MAIN_FUNCTION,
                    confidence=0.80,  # Lower than framework patterns
                    label=f"{lang} main()",
                ))
                added_kinds.add(EntrypointKind.MAIN_FUNCTION)

            # Python main guard concept -> MAIN_FUNCTION
            # Structural entrypoint: `if __name__ == "__main__":` pattern
            elif concept_type == "main_guard":
                if EntrypointKind.MAIN_FUNCTION in added_kinds:
                    continue
                entrypoints.append(Entrypoint(
                    symbol_id=sym.id,
                    kind=EntrypointKind.MAIN_FUNCTION,
                    confidence=0.85,  # Structural pattern (higher than naming heuristic)
                    label="Python script (if __name__ == '__main__')",
                ))
                added_kinds.add(EntrypointKind.MAIN_FUNCTION)

            # Library export concept -> LIBRARY_EXPORT
            # (ADR-0003 v1.3.x - Library public API detection)
            # Exports from index files (index.ts, index.js) are treated as library
            # entry points since they define the public API surface.
            elif concept_type == "library_export":
                if EntrypointKind.LIBRARY_EXPORT in added_kinds:
                    continue
                export_name = concept.get("export_name", sym.name)
                # Handle is_default as bool or string "true"/"false"
                is_default_raw = concept.get("is_default", False)
                is_default = is_default_raw is True or is_default_raw == "true"
                if is_default:
                    label = "Library default export"
                else:
                    label = f"Library export: {export_name}"
                entrypoints.append(Entrypoint(
                    symbol_id=sym.id,
                    kind=EntrypointKind.LIBRARY_EXPORT,
                    confidence=0.75,  # Lower than routes, similar to main()
                    label=label,
                ))
                added_kinds.add(EntrypointKind.LIBRARY_EXPORT)

            # Naming-based heuristics (lowest confidence tier)
            # These are fallbacks when no explicit annotation/base class is found
            # ADR-0003 v1.4.x - naming-conventions.yaml
            elif concept_type == "controller_by_name":
                if EntrypointKind.CONTROLLER in added_kinds:
                    continue  # Already detected via framework pattern
                entrypoints.append(Entrypoint(
                    symbol_id=sym.id,
                    kind=EntrypointKind.CONTROLLER,
                    confidence=0.70,  # Naming heuristic - lowest tier
                    label=f"Controller (by name): {sym.name}",
                ))
                added_kinds.add(EntrypointKind.CONTROLLER)

            elif concept_type == "handler_by_name":
                if EntrypointKind.CONTROLLER in added_kinds:
                    continue  # Handlers are treated as controllers
                entrypoints.append(Entrypoint(
                    symbol_id=sym.id,
                    kind=EntrypointKind.CONTROLLER,
                    confidence=0.70,  # Naming heuristic - lowest tier
                    label=f"Handler (by name): {sym.name}",
                ))
                added_kinds.add(EntrypointKind.CONTROLLER)

            elif concept_type == "service_by_name":
                # Services are not entrypoints by default, but we track
                # them for potential future use. Skip for now.
                pass

    return entrypoints


def detect_entrypoints(
    nodes: List[Symbol],
    edges: List[Edge],
) -> List[Entrypoint]:
    """Detect entrypoints in the codebase using semantic detection.

    Detection sources (ADR-0003 v1.2.x):
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
    - Test files: 50% penalty (deprioritized, not excluded)
    - Vendor/external deps (tier >= 3): 70% penalty
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

    # Remove duplicates (same symbol detected by multiple strategies)
    # Keep the first (highest confidence) entry for each symbol
    seen_ids: set[str] = set()
    unique_entrypoints: List[Entrypoint] = []
    for ep in entrypoints:
        if ep.symbol_id not in seen_ids:
            seen_ids.add(ep.symbol_id)
            unique_entrypoints.append(ep)

    # Build lookup from symbol_id to Symbol for penalty calculations
    symbol_lookup: dict[str, Symbol] = {node.id: node for node in nodes}

    # Apply penalties for test files and vendor code
    # This deprioritizes them without removing them from the list
    for ep in unique_entrypoints:
        sym = symbol_lookup.get(ep.symbol_id)
        if sym is None:
            continue  # pragma: no cover - symbol should always exist

        # Penalty for test files (50% reduction)
        if sym.path and is_test_file(sym.path):
            ep.confidence *= 0.5

        # Penalty for utility/example/docs files (50% reduction)
        # These are demonstration code, not production entrypoints
        if sym.path and is_utility_file(sym.path):
            ep.confidence *= 0.5

        # Penalty for vendor/external dependencies (70% reduction)
        # Tier 3 = external deps, Tier 4 = derived/build artifacts
        if sym.supply_chain_tier >= 3:
            ep.confidence *= 0.3

    # Boost confidence based on connectivity (outgoing edges)
    # An entrypoint that calls many other functions is more "interesting"
    outgoing_counts: dict[str, int] = defaultdict(int)
    for edge in edges:
        outgoing_counts[edge.src] += 1

    for ep in unique_entrypoints:
        out_edges = outgoing_counts.get(ep.symbol_id, 0)
        if out_edges > 0:
            # Logarithmic boost: diminishing returns for very high counts
            # log(1 + 10) / 10 ≈ 0.24, log(1 + 100) / 10 ≈ 0.46
            # Cap at 0.25 to avoid overwhelming the base confidence
            connectivity_boost = min(0.25, math.log(1 + out_edges) / 10)
            ep.confidence = min(1.0, ep.confidence + connectivity_boost)

    # Sort by confidence (highest first) for better --entry auto behavior
    unique_entrypoints.sort(key=lambda ep: ep.confidence, reverse=True)

    return unique_entrypoints
