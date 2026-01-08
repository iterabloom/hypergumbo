"""Entrypoint detection heuristics for code analysis.

Detects common entrypoint patterns:
- HTTP routes (FastAPI, Flask decorators)
- CLI entrypoints (main guard, Click commands)
- Electron app entry points (electron.js, preload.js)
- Django views (functions imported by urls.py)
- Express.js routes (routes.js, router.js, routes/ directory)
- NestJS controllers (*.controller.ts files)
- Spring Boot controllers (*Controller.java/*Resource.java files)
- Rails controllers (app/controllers/*_controller.rb files)
- Phoenix controllers (lib/*_web/controllers/*_controller.ex, LiveView)
- Go handlers (Gin, Echo, Fiber, Chi - *_handler.go, handlers/)
- Laravel controllers (app/Http/Controllers/*Controller.php)
- Rust handlers (Actix-web, Axum, Rocket, Warp - *_handler.rs, handlers/)
- ASP.NET Core controllers (Controllers/*Controller.cs)
- Sinatra routes (app.rb, application.rb, server.rb, routes/)
- Ktor routes (*Routes.kt, *Routing.kt, routes/)
- Vapor routes (*Controller.swift, routes.swift, Controllers/)
- Plug routes (router.ex, *_router.ex, *_plug.ex)
- Hapi routes (*routes.js/ts, routes/, plugins/)
- Fastify routes (*.routes.js/ts, *.route.js/ts, *.schema.js/ts)
- Koa routes (*.router.js/ts, *.controller.js/ts, *.middleware.js/ts)
- Grape APIs (*_api.rb, api/, endpoints/, entities/)
- Tornado handlers (*_handler.py, handlers/, views/)
- Aiohttp views (*_view.py, *_resource.py, routes.py, resources/)
- Slim routes (routes.php, *Middleware.php, Actions/, Handlers/)
- Micronaut HTTP clients (*Client.java/kt, client/)
- GraphQL servers (resolvers.js/ts, schema.js/ts, *.resolver.js/ts, resolvers/, graphql/)

How It Works
------------
Entrypoint detection uses heuristics to identify likely "entry points"
into a codebase - places where execution typically starts or where
external requests arrive. This enables `--entry auto` in the slicer.

Detection is based on:

1. **Decorators** (high confidence ~0.95): Functions decorated with
   `@get`, `@post`, `@route`, `@command` etc. are almost certainly
   entrypoints. We extract decorator names from the Symbol's stable_id
   field during analysis.

2. **Name patterns** (lower confidence ~0.70): Functions named `main`,
   `cli`, `run` are *probably* entrypoints but could be false positives.
   The lower confidence lets callers filter if desired.

3. **File patterns** (medium-high confidence ~0.85): For Electron apps,
   files named `electron.js`, `preload.js` indicate entry points.
   For Express.js, files named `routes.js`, `router.js`, or files in
   a `routes/` directory are detected. Generic names like `renderer.js`
   and `index.js` are NOT matched to avoid false positives.

4. **Import patterns** (high confidence ~0.90): For Django, functions
   imported by urls.py files are likely views. This leverages the fact
   that Django's URL configuration explicitly references view functions.

Confidence Scores
-----------------
- 0.95: Decorator-based (very reliable)
- 0.90: Django urls.py imports, NestJS *.controller.ts (explicit conventions)
- 0.85: File-pattern-based (Electron, Express route files)
- 0.70: Name-pattern-based (heuristic, may have false positives)

Current Limitations
-------------------
- Decorator detection relies on stable_id containing decorator names,
  which is a temporary hack. Proper decorator storage in IR is needed.
- Django detection doesn't catch views defined inline in urls.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List

from .ir import Symbol, Edge


class EntrypointKind(Enum):
    """Types of entrypoints that can be detected."""

    HTTP_ROUTE = "http_route"
    CLI_MAIN = "cli_main"
    CLI_COMMAND = "cli_command"
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


# HTTP route decorator patterns (high confidence)
HTTP_ROUTE_DECORATORS = {
    "get", "post", "put", "delete", "patch", "head", "options",
    "route", "api_route",
}

# CLI-related decorators
CLI_DECORATORS = {
    "command", "group", "click.command", "click.group",
    "app.command", "typer.command",
}

# CLI function name patterns (lower confidence)
CLI_NAME_PATTERNS = {
    "main", "cli", "run", "execute", "start",
}

# Languages where CLI_NAME_PATTERNS (like "main") indicate program entry points.
# Excludes shader languages (GLSL/HLSL/WGSL), hardware description (Verilog/VHDL),
# and config/data languages where "main" has no CLI meaning.
CLI_CAPABLE_LANGUAGES = {
    "c", "cpp", "python", "go", "java", "rust",
    "javascript", "typescript", "kotlin", "swift",
    "ruby", "php", "csharp", "fsharp", "scala",
    "perl", "lua", "nim", "zig", "d", "haskell",
    "ocaml", "elixir", "erlang", "crystal", "julia",
}

# Electron file patterns (only specific patterns to avoid false positives)
# Note: renderer.js/ts and index.js are too generic - many frameworks use these names
ELECTRON_MAIN_FILES = {"electron.js", "electron.ts", "electron-main.js", "electron-main.ts"}
ELECTRON_PRELOAD_FILES = {"preload.js", "preload.ts", "electron-preload.js", "electron-preload.ts"}

# Express.js route file patterns
# Files named routes.js/ts or router.js/ts, or files in a routes/ directory
EXPRESS_ROUTE_FILENAMES = {"routes.js", "routes.ts", "router.js", "router.ts"}
EXPRESS_ROUTE_DIRS = {"routes", "routers"}

# GraphQL server patterns (Apollo Server, GraphQL Yoga, etc.)
# Files named with resolver, schema, or typeDefs conventions
GRAPHQL_SERVER_FILENAMES = {
    "resolvers.js", "resolvers.ts",
    "schema.js", "schema.ts",
    "typeDefs.js", "typeDefs.ts",
}
GRAPHQL_SERVER_SUFFIXES = {
    ".resolver.js", ".resolver.ts",
    ".resolvers.js", ".resolvers.ts",
}
GRAPHQL_SERVER_DIRS = {"resolvers", "graphql"}

# NestJS controller patterns
# Files ending in .controller.ts, or files in a controllers/ directory
NESTJS_CONTROLLER_SUFFIX = ".controller.ts"
NESTJS_CONTROLLER_DIRS = {"controllers"}

# Spring Boot controller patterns
# Files ending in Controller.java/kt or Resource.java/kt, or in controller(s)/ directory
SPRING_CONTROLLER_SUFFIXES = {"Controller.java", "Controller.kt", "Resource.java", "Resource.kt"}
SPRING_CONTROLLER_DIRS = {"controller", "controllers"}

# Rails controller patterns
# Files ending in _controller.rb inside app/controllers/ directory
RAILS_CONTROLLER_SUFFIX = "_controller.rb"
RAILS_CONTROLLER_PATH = "app/controllers/"

# Phoenix controller patterns
# Files ending in _controller.ex inside lib/*_web/controllers/, or
# Files ending in _live.ex inside lib/*_web/live/ (LiveView)
PHOENIX_CONTROLLER_SUFFIX = "_controller.ex"
PHOENIX_LIVEVIEW_SUFFIX = "_live.ex"
PHOENIX_CONTROLLER_PATH_PATTERN = "_web/controllers/"
PHOENIX_LIVEVIEW_PATH_PATTERN = "_web/live/"

# Go framework handler patterns (Gin, Echo, Fiber, Chi)
# Files ending in _handler.go or _controller.go, or in handlers/controllers/ directory
GO_HANDLER_SUFFIXES = {"_handler.go", "_controller.go"}
GO_HANDLER_DIRS = {"handlers", "controllers"}

# Laravel controller patterns
# Files ending in Controller.php inside app/Http/Controllers/
LARAVEL_CONTROLLER_SUFFIX = "Controller.php"
LARAVEL_CONTROLLER_PATH = "app/Http/Controllers/"

# Rust framework handler patterns (Actix-web, Axum, Rocket, Warp)
# Files ending in _handler.rs or _controller.rs, or in handlers/controllers/ directory
RUST_HANDLER_SUFFIXES = {"_handler.rs", "_controller.rs"}
RUST_HANDLER_DIRS = {"handlers", "controllers"}

# ASP.NET Core controller patterns
# Files ending in Controller.cs, typically in Controllers/ directory
ASPNET_CONTROLLER_SUFFIX = "Controller.cs"
ASPNET_CONTROLLER_DIRS = {"Controllers"}

# Sinatra (Ruby) route patterns
# Common entry point filenames and routes directory
SINATRA_ROUTE_FILES = {"app.rb", "application.rb", "server.rb"}
SINATRA_ROUTE_DIRS = {"routes"}

# Ktor (Kotlin) route patterns
# Files ending in Routes.kt or Routing.kt, or in routes/routing directories
KTOR_ROUTE_SUFFIXES = {"Routes.kt", "Routing.kt"}
KTOR_ROUTE_DIRS = {"routes", "routing"}

# Vapor (Swift) route patterns
# Files ending in Controller.swift or routes.swift, or in Controllers/Routes directories
VAPOR_CONTROLLER_SUFFIX = "Controller.swift"
VAPOR_ROUTE_FILES = {"routes.swift"}
VAPOR_ROUTE_DIRS = {"Controllers", "Routes"}

# Plug (Elixir) route patterns
# Files named router.ex or ending in _router.ex/_plug.ex, or in plugs/ directory
PLUG_ROUTE_FILES = {"router.ex"}
PLUG_ROUTE_SUFFIXES = {"_router.ex", "_plug.ex"}
PLUG_ROUTE_DIRS = {"plugs"}

# Hapi (Node.js) route patterns
# Files ending in routes.js/ts or Routes.js/ts, or in plugins directory
# Note: We don't include routes/ since Express.js catches that first
HAPI_ROUTE_SUFFIXES = {"routes.js", "routes.ts", "Routes.js", "Routes.ts"}
HAPI_ROUTE_DIRS = {"plugins"}

# Fastify (Node.js) route patterns
# Files with .routes. or .route. or .schema. in the name (Fastify convention)
FASTIFY_ROUTE_PATTERNS = {".routes.", ".route.", ".schema."}

# Koa (Node.js) route patterns
# Files with .router. or .controller. or .middleware. in the name (Koa convention)
KOA_ROUTE_PATTERNS = {".router.", ".controller.", ".middleware."}

# Grape (Ruby) API patterns
# Files ending in _api.rb, or in api/endpoints/entities directories
GRAPE_API_SUFFIX = "_api.rb"
GRAPE_API_DIRS = {"api", "endpoints", "entities"}

# Tornado (Python) handler patterns
# Files ending in _handler.py, or in handlers/views directories
TORNADO_HANDLER_SUFFIX = "_handler.py"
TORNADO_HANDLER_DIRS = {"handlers", "views"}

# Aiohttp (Python) view patterns
# Files ending in _view.py or _resource.py, or in resources/ directory, or routes.py
AIOHTTP_VIEW_SUFFIXES = {"_view.py", "_resource.py"}
AIOHTTP_VIEW_FILES = {"routes.py"}
AIOHTTP_VIEW_DIRS = {"resources"}

# Slim (PHP) route patterns
# Files named routes.php, *Middleware.php, or in Actions/Handlers directories
SLIM_ROUTE_FILES = {"routes.php"}
SLIM_ROUTE_SUFFIXES = {"Middleware.php"}
SLIM_ROUTE_DIRS = {"Actions", "Handlers"}

# Micronaut (Java/Kotlin) HTTP client patterns
# Micronaut-specific: declarative HTTP clients (*Client.java/kt) or client/ directory
# Controllers are detected by Spring detection which covers both frameworks
MICRONAUT_CLIENT_SUFFIXES = {"Client.java", "Client.kt"}
MICRONAUT_CLIENT_DIRS = {"client"}


def _get_decorators(symbol: Symbol) -> set[str]:
    """Extract decorator names from symbol.

    The stable_id field is used to store comma-separated decorator names
    during analysis (this is a temporary solution until we have proper
    decorator storage in the IR).
    """
    if symbol.stable_id and not symbol.stable_id.startswith("sha256:"):
        return set(symbol.stable_id.split(","))
    return set()


def _get_filename(path: str) -> str:
    """Extract filename from path."""
    return path.rsplit("/", 1)[-1] if "/" in path else path


def _is_test_file(path: str) -> bool:
    """Check if a file path appears to be a test file.

    Excludes:
    - Files starting with test_ or ending with _test.py
    - Go test files (*_test.go)
    - Mock/fake files (*_mock.*, *_fake.*, fake_*.*, mock_*.*)
    - Files in tests/, test/, spec/, fakes/, mocks/, fixtures/ directories
    """
    filename = _get_filename(path)
    filename_lower = filename.lower()

    # Check filename patterns - Python tests
    if filename.startswith("test_") or filename.endswith("_test.py"):
        return True
    if filename.startswith("spec_") or filename.endswith("_spec.py"):
        return True

    # Check filename patterns - Go tests
    if filename.endswith("_test.go"):
        return True

    # Check filename patterns - Mock/fake files (any language)
    name_without_ext = filename_lower.rsplit(".", 1)[0] if "." in filename_lower else filename_lower
    if name_without_ext.endswith("_mock") or name_without_ext.endswith("_fake"):
        return True
    if name_without_ext.startswith("mock_") or name_without_ext.startswith("fake_"):
        return True

    # Check directory patterns - test and mock directories
    path_parts = path.replace("\\", "/").split("/")
    test_dirs = {
        "tests", "test", "spec", "__tests__",  # Test directories
        "fakes", "mocks", "testfakes", "testmocks",  # Mock directories
        "fixtures", "testdata", "testutils",  # Test support directories
    }
    # Also match compound names like "transportfakes" that end with "fakes"/"mocks"
    for part in path_parts:
        part_lower = part.lower()
        if part_lower in test_dirs:
            return True
        if part_lower.endswith("fakes") or part_lower.endswith("mocks"):
            return True
    return False


def _detect_http_routes(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect HTTP route entrypoints from decorators."""
    entrypoints = []

    for sym in symbols:
        decorators = _get_decorators(sym)
        matching = decorators & HTTP_ROUTE_DECORATORS

        if matching:
            # High confidence for decorator-based detection
            label = f"HTTP {next(iter(matching)).upper()}"
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.HTTP_ROUTE,
                confidence=0.95,
                label=label,
            ))

    return entrypoints


def _detect_cli_entrypoints(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect CLI entrypoints from decorators and name patterns."""
    entrypoints = []

    for sym in symbols:
        decorators = _get_decorators(sym)

        # Check for CLI decorators (high confidence)
        if decorators & CLI_DECORATORS:
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.CLI_COMMAND,
                confidence=0.95,
                label="CLI command",
            ))
            continue

        # Check for name patterns (lower confidence)
        # Only match CLI patterns for languages where "main" means program entry point
        # (excludes shaders like GLSL/HLSL/WGSL, HDL like Verilog/VHDL, etc.)
        if sym.language in CLI_CAPABLE_LANGUAGES and sym.name.lower() in CLI_NAME_PATTERNS:
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.CLI_MAIN,
                confidence=0.70,
                label="CLI main",
            ))

    return entrypoints


def _detect_electron_entrypoints(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Electron app entrypoints from file names.

    Only matches specific Electron file patterns to minimize false positives.
    Tracks files already seen to emit one entry point per file, not per symbol.
    """
    entrypoints = []
    seen_files: set[str] = set()

    for sym in symbols:
        if sym.language not in ("javascript", "typescript"):
            continue

        # Only emit one entry point per file
        if sym.path in seen_files:
            continue

        filename = _get_filename(sym.path)

        if filename in ELECTRON_MAIN_FILES:
            seen_files.add(sym.path)
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.ELECTRON_MAIN,
                confidence=0.85,
                label="Electron main",
            ))
        elif filename in ELECTRON_PRELOAD_FILES:
            seen_files.add(sym.path)
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.ELECTRON_PRELOAD,
                confidence=0.85,
                label="Electron preload",
            ))

    return entrypoints


def _is_express_route_file(path: str, language: str) -> bool:
    """Check if a file path matches Express route patterns.

    Matches:
    - Files named routes.js/ts or router.js/ts (not .tsx)
    - Any .js/.ts file inside a routes/ or routers/ directory (not .tsx)

    Excludes .tsx files because they're typically React file-based routing
    (TanStack Router, Next.js app router, etc.), not Express routes.
    """
    if language not in ("javascript", "typescript"):
        return False

    # Exclude .tsx/.jsx files - these are React components, not Express routes
    # React file-based routing (TanStack Router, Next.js) uses routes/*.tsx
    if path.endswith(".tsx") or path.endswith(".jsx"):
        return False

    filename = _get_filename(path)
    if filename in EXPRESS_ROUTE_FILENAMES:
        return True

    # Check if file is in a routes/ or routers/ directory
    path_parts = path.replace("\\", "/").split("/")
    return any(part in EXPRESS_ROUTE_DIRS for part in path_parts)


def _detect_express_routes(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Express.js route handlers from file patterns.

    Express routes are typically defined in files named routes.js/ts,
    router.js/ts, or files inside a routes/ directory.

    Only function-like symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_express_route_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.EXPRESS_ROUTE,
                confidence=0.85,
                label="Express route",
            ))

    return entrypoints


def _is_nestjs_controller_file(path: str, language: str) -> bool:
    """Check if a file path matches NestJS controller patterns.

    Matches:
    - Files ending in .controller.ts (NestJS naming convention)
    - Any .ts file inside a controllers/ directory
    """
    if language != "typescript":
        return False

    filename = _get_filename(path)
    if filename.endswith(NESTJS_CONTROLLER_SUFFIX):
        return True

    # Check if file is in a controllers/ directory
    path_parts = path.replace("\\", "/").split("/")
    return any(part in NESTJS_CONTROLLER_DIRS for part in path_parts)


def _detect_nestjs_controllers(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect NestJS controller endpoints from file patterns.

    NestJS uses a naming convention of *.controller.ts for controller files.
    Classes and methods in these files are API endpoints.

    Only function/class/method symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like and class symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_nestjs_controller_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.NESTJS_CONTROLLER,
                confidence=0.90,
                label="NestJS controller",
            ))

    return entrypoints


def _is_spring_controller_file(path: str, language: str) -> bool:
    """Check if a file path matches Spring Boot controller patterns.

    Matches:
    - Files ending in Controller.java/kt or Resource.java/kt
    - Any .java/.kt file inside a controller/ or controllers/ directory
    """
    if language not in ("java", "kotlin"):
        return False

    filename = _get_filename(path)
    if any(filename.endswith(suffix) for suffix in SPRING_CONTROLLER_SUFFIXES):
        return True

    # Check if file is in a controller/ or controllers/ directory
    path_parts = path.replace("\\", "/").split("/")
    return any(part in SPRING_CONTROLLER_DIRS for part in path_parts)


def _detect_spring_controllers(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Spring Boot controller endpoints from file patterns.

    Spring Boot uses a naming convention of *Controller.java or *Resource.java
    for controller files. Classes and methods in these files are API endpoints.

    Only function/class/method symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like and class symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_spring_controller_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.SPRING_CONTROLLER,
                confidence=0.90,
                label="Spring controller",
            ))

    return entrypoints


def _is_rails_controller_file(path: str, language: str) -> bool:
    """Check if a file path matches Rails controller patterns.

    Matches:
    - Files ending in _controller.rb inside app/controllers/ directory
    """
    if language != "ruby":
        return False

    # Normalize path separators
    normalized_path = path.replace("\\", "/")

    # Must be in app/controllers/ directory
    if RAILS_CONTROLLER_PATH not in normalized_path:
        return False

    filename = _get_filename(path)
    return filename.endswith(RAILS_CONTROLLER_SUFFIX)


def _detect_rails_controllers(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Rails controller actions from file patterns.

    Rails uses a naming convention of *_controller.rb for controller files
    inside the app/controllers/ directory. Methods in these files are actions.

    Only function/class/method symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like and class symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_rails_controller_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.RAILS_CONTROLLER,
                confidence=0.90,
                label="Rails controller",
            ))

    return entrypoints


def _is_phoenix_controller_file(path: str, language: str) -> bool:
    """Check if a file path matches Phoenix controller patterns.

    Matches:
    - Files ending in _controller.ex inside lib/*_web/controllers/
    - Files ending in _live.ex inside lib/*_web/live/ (LiveView)
    """
    if language != "elixir":
        return False

    # Normalize path separators
    normalized_path = path.replace("\\", "/")
    filename = _get_filename(path)

    # Check for controller files in _web/controllers/
    if (PHOENIX_CONTROLLER_PATH_PATTERN in normalized_path and
            filename.endswith(PHOENIX_CONTROLLER_SUFFIX)):
        return True

    # Check for LiveView files in _web/live/
    if (PHOENIX_LIVEVIEW_PATH_PATTERN in normalized_path and
            filename.endswith(PHOENIX_LIVEVIEW_SUFFIX)):
        return True

    return False


def _detect_phoenix_controllers(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Phoenix controller actions from file patterns.

    Phoenix uses a naming convention of *_controller.ex for controller files
    inside the lib/*_web/controllers/ directory. Also detects LiveView files
    (*_live.ex in lib/*_web/live/).

    Only function/class/module symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like and class/module symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_phoenix_controller_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.PHOENIX_CONTROLLER,
                confidence=0.90,
                label="Phoenix controller",
            ))

    return entrypoints


def _is_go_handler_file(path: str, language: str) -> bool:
    """Check if a file path matches Go handler patterns.

    Matches:
    - Files ending in _handler.go or _controller.go
    - Any .go file inside a handlers/ or controllers/ directory
    """
    if language != "go":
        return False

    filename = _get_filename(path)

    # Check for handler/controller suffix
    if any(filename.endswith(suffix) for suffix in GO_HANDLER_SUFFIXES):
        return True

    # Check if file is in a handlers/ or controllers/ directory
    path_parts = path.replace("\\", "/").split("/")
    return any(part in GO_HANDLER_DIRS for part in path_parts)


def _detect_go_handlers(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Go framework handlers from file patterns.

    Go web frameworks (Gin, Echo, Fiber, Chi) typically use:
    - *_handler.go or *_controller.go naming
    - handlers/ or controllers/ directories

    Only function/struct symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_go_handler_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.GO_HANDLER,
                confidence=0.85,
                label="Go handler",
            ))

    return entrypoints


def _is_laravel_controller_file(path: str, language: str) -> bool:
    """Check if a file path matches Laravel controller patterns.

    Matches:
    - Files ending in Controller.php inside app/Http/Controllers/
    """
    if language != "php":
        return False

    # Normalize path separators
    normalized_path = path.replace("\\", "/")

    # Must be in app/Http/Controllers/ directory
    if LARAVEL_CONTROLLER_PATH not in normalized_path:
        return False

    filename = _get_filename(path)
    return filename.endswith(LARAVEL_CONTROLLER_SUFFIX)


def _detect_laravel_controllers(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Laravel controller actions from file patterns.

    Laravel uses a naming convention of *Controller.php for controller files
    inside the app/Http/Controllers/ directory.

    Only function/class symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like and class symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_laravel_controller_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.LARAVEL_CONTROLLER,
                confidence=0.90,
                label="Laravel controller",
            ))

    return entrypoints


def _is_rust_handler_file(path: str, language: str) -> bool:
    """Check if a file path matches Rust handler patterns.

    Matches:
    - Files ending in _handler.rs or _controller.rs
    - Any .rs file inside a handlers/ or controllers/ directory
    """
    if language != "rust":
        return False

    filename = _get_filename(path)

    # Check for handler/controller suffix
    if any(filename.endswith(suffix) for suffix in RUST_HANDLER_SUFFIXES):
        return True

    # Check if file is in a handlers/ or controllers/ directory
    path_parts = path.replace("\\", "/").split("/")
    return any(part in RUST_HANDLER_DIRS for part in path_parts)


def _detect_rust_handlers(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Rust framework handlers from file patterns.

    Rust web frameworks (Actix-web, Axum, Rocket, Warp) typically use:
    - *_handler.rs or *_controller.rs naming
    - handlers/ or controllers/ directories

    Only function/struct symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_rust_handler_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.RUST_HANDLER,
                confidence=0.85,
                label="Rust handler",
            ))

    return entrypoints


def _is_aspnet_controller_file(path: str, language: str) -> bool:
    """Check if a file path matches ASP.NET Core controller patterns.

    Matches:
    - Files ending in Controller.cs
    - Must be C# files and typically in a Controllers/ directory
    """
    if language != "csharp":
        return False

    filename = _get_filename(path)

    # Check for Controller.cs suffix
    if not filename.endswith(ASPNET_CONTROLLER_SUFFIX):
        return False

    # Check if file is in a Controllers/ directory (common convention)
    path_parts = path.replace("\\", "/").split("/")
    return any(part in ASPNET_CONTROLLER_DIRS for part in path_parts)


def _detect_aspnet_controllers(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect ASP.NET Core controller endpoints from file patterns.

    ASP.NET Core uses a naming convention of *Controller.cs for controller files,
    typically in a Controllers/ directory. Methods in these files are actions.

    Only function/class symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like and class symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_aspnet_controller_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.ASPNET_CONTROLLER,
                confidence=0.90,
                label="ASP.NET controller",
            ))

    return entrypoints


def _is_sinatra_route_file(path: str, language: str) -> bool:
    """Check if a file path matches Sinatra route patterns.

    Matches:
    - Files named app.rb, application.rb, server.rb
    - Any .rb file inside a routes/ directory

    Excludes test files.
    """
    if language != "ruby":
        return False

    # Exclude test files
    if _is_test_file(path):
        return False

    filename = _get_filename(path)

    # Check for Sinatra main files
    if filename in SINATRA_ROUTE_FILES:
        return True

    # Check if file is in a routes/ directory
    path_parts = path.replace("\\", "/").split("/")
    return any(part in SINATRA_ROUTE_DIRS for part in path_parts)


def _detect_sinatra_routes(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Sinatra route endpoints from file patterns.

    Sinatra is a lightweight Ruby web framework. Routes are typically
    defined in app.rb, application.rb, server.rb, or files in routes/.

    Only function/method symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_sinatra_route_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.SINATRA_ROUTE,
                confidence=0.85,
                label="Sinatra route",
            ))

    return entrypoints


def _is_ktor_route_file(path: str, language: str) -> bool:
    """Check if a file path matches Ktor route patterns.

    Matches:
    - Files ending in Routes.kt or Routing.kt
    - Any .kt file inside a routes/ or routing/ directory
    """
    if language != "kotlin":
        return False

    filename = _get_filename(path)

    # Check for Ktor route file suffixes
    if any(filename.endswith(suffix) for suffix in KTOR_ROUTE_SUFFIXES):
        return True

    # Check if file is in a routes/ or routing/ directory
    path_parts = path.replace("\\", "/").split("/")
    return any(part in KTOR_ROUTE_DIRS for part in path_parts)


def _detect_ktor_routes(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Ktor route endpoints from file patterns.

    Ktor is a Kotlin web framework. Routes are typically defined in
    files named *Routes.kt, *Routing.kt, or in routes/routing directories.

    Only function/method symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_ktor_route_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.KTOR_ROUTE,
                confidence=0.85,
                label="Ktor route",
            ))

    return entrypoints


def _is_vapor_route_file(path: str, language: str) -> bool:
    """Check if a file path matches Vapor route patterns.

    Matches:
    - Files ending in Controller.swift
    - Files named routes.swift
    - Any .swift file inside a Controllers/ or Routes/ directory
    """
    if language != "swift":
        return False

    filename = _get_filename(path)

    # Check for Controller.swift suffix
    if filename.endswith(VAPOR_CONTROLLER_SUFFIX):
        return True

    # Check for routes.swift
    if filename in VAPOR_ROUTE_FILES:
        return True

    # Check if file is in a Controllers/ or Routes/ directory
    path_parts = path.replace("\\", "/").split("/")
    return any(part in VAPOR_ROUTE_DIRS for part in path_parts)


def _detect_vapor_routes(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Vapor route endpoints from file patterns.

    Vapor is a Swift web framework. Routes are typically defined in
    files named *Controller.swift, routes.swift, or in Controllers/Routes directories.

    Only function/method symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_vapor_route_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.VAPOR_ROUTE,
                confidence=0.85,
                label="Vapor route",
            ))

    return entrypoints


def _is_plug_route_file(path: str, language: str) -> bool:
    """Check if a file path matches Plug route patterns.

    Matches:
    - Files named router.ex
    - Files ending in _router.ex or _plug.ex
    - Any .ex file inside a plugs/ directory
    """
    if language != "elixir":
        return False

    filename = _get_filename(path)

    # Check for router.ex
    if filename in PLUG_ROUTE_FILES:
        return True

    # Check for *_router.ex or *_plug.ex suffix
    if any(filename.endswith(suffix) for suffix in PLUG_ROUTE_SUFFIXES):
        return True

    # Check if file is in a plugs/ directory
    path_parts = path.replace("\\", "/").split("/")
    return any(part in PLUG_ROUTE_DIRS for part in path_parts)


def _detect_plug_routes(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Plug route endpoints from file patterns.

    Plug is Elixir's HTTP middleware library. Routes are typically defined
    in files named router.ex, *_router.ex, or *_plug.ex.

    Only function/module symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_plug_route_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.PLUG_ROUTE,
                confidence=0.85,
                label="Plug route",
            ))

    return entrypoints


def _is_hapi_route_file(path: str, language: str) -> bool:
    """Check if a file path matches Hapi route patterns.

    Matches:
    - Files ending in routes.js/ts or Routes.js/ts
    - Any .js/.ts file inside a routes/ or plugins/ directory

    Excludes .tsx/.jsx files (React components, not Hapi routes).
    """
    if language not in ("javascript", "typescript"):
        return False

    # Exclude React components - same as Express fix
    if path.endswith(".tsx") or path.endswith(".jsx"):
        return False

    filename = _get_filename(path)

    # Check for *routes.js/ts suffix
    if any(filename.endswith(suffix) for suffix in HAPI_ROUTE_SUFFIXES):
        return True

    # Check if file is in a routes/ or plugins/ directory
    path_parts = path.replace("\\", "/").split("/")
    return any(part in HAPI_ROUTE_DIRS for part in path_parts)


def _detect_hapi_routes(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Hapi route endpoints from file patterns.

    Hapi is a Node.js web framework. Routes are typically defined in
    files ending in routes.js/ts or in routes/plugins directories.

    Only function/method symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_hapi_route_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.HAPI_ROUTE,
                confidence=0.85,
                label="Hapi route",
            ))

    return entrypoints


def _is_fastify_route_file(path: str, language: str) -> bool:
    """Check if a file path matches Fastify route patterns.

    Matches:
    - Files with .routes. pattern (e.g., user.routes.js)
    - Files with .route. pattern (e.g., user.route.js)
    - Files with .schema. pattern (e.g., user.schema.js)
    """
    if language not in ("javascript", "typescript"):
        return False

    filename = _get_filename(path)

    # Check for Fastify patterns (.routes., .route., .schema.)
    return any(pattern in filename for pattern in FASTIFY_ROUTE_PATTERNS)


def _detect_fastify_routes(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Fastify route endpoints from file patterns.

    Fastify is a Node.js web framework. Routes are typically defined in
    files with .routes., .route., or .schema. patterns.

    Only function/method symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_fastify_route_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.FASTIFY_ROUTE,
                confidence=0.85,
                label="Fastify route",
            ))

    return entrypoints


def _is_koa_route_file(path: str, language: str) -> bool:
    """Check if a file path matches Koa route patterns.

    Matches:
    - Files with .router. pattern (e.g., user.router.js)
    - Files with .controller. pattern (e.g., user.controller.js)
    - Files with .middleware. pattern (e.g., auth.middleware.js)

    Excludes .tsx/.jsx files (React components, not Koa routes).
    """
    if language not in ("javascript", "typescript"):
        return False

    # Exclude React components - same as Express fix
    if path.endswith(".tsx") or path.endswith(".jsx"):
        return False

    filename = _get_filename(path)

    # Check for Koa patterns (.router., .controller., .middleware.)
    return any(pattern in filename for pattern in KOA_ROUTE_PATTERNS)


def _detect_koa_routes(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Koa route endpoints from file patterns.

    Koa is a Node.js web framework. Routes are typically defined in
    files with .router., .controller., or .middleware. patterns.

    Only function/method symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_koa_route_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.KOA_ROUTE,
                confidence=0.85,
                label="Koa route",
            ))

    return entrypoints


def _is_grape_api_file(path: str, language: str) -> bool:
    """Check if a file path matches Grape API patterns.

    Matches:
    - Files ending in _api.rb
    - Any .rb file inside an api/, endpoints/, or entities/ directory

    Excludes test files.
    """
    if language != "ruby":
        return False

    # Exclude test files
    if _is_test_file(path):
        return False

    filename = _get_filename(path)

    # Check for *_api.rb suffix
    if filename.endswith(GRAPE_API_SUFFIX):
        return True

    # Check if file is in an api/, endpoints/, or entities/ directory
    path_parts = path.replace("\\", "/").split("/")
    return any(part in GRAPE_API_DIRS for part in path_parts)


def _detect_grape_apis(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Grape API endpoints from file patterns.

    Grape is a Ruby API framework. APIs are typically defined in
    files ending in _api.rb or in api/endpoints/entities directories.

    Only function/class symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_grape_api_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.GRAPE_API,
                confidence=0.85,
                label="Grape API",
            ))

    return entrypoints


def _is_tornado_handler_file(path: str, language: str) -> bool:
    """Check if a file path matches Tornado handler patterns.

    Matches:
    - Files ending in _handler.py
    - Any .py file inside a handlers/ or views/ directory

    Excludes:
    - Test files
    - Non-web handler patterns (error handlers, signal handlers, event handlers)
    """
    if language != "python":
        return False

    # Exclude test files (e.g. test_user_error_handler.py)
    if _is_test_file(path):
        return False

    filename = _get_filename(path)

    # Exclude common non-web handler patterns
    # These are typically internal infrastructure, not HTTP endpoints
    # Match patterns like error_handler.py, custom_error_handler.py, etc.
    non_web_handler_patterns = (
        "error_handler.py",
        "signal_handler.py",
        "event_handler.py",
        "exception_handler.py",
        "logging_handler.py",
        "log_handler.py",
    )
    if any(filename.endswith(pattern) for pattern in non_web_handler_patterns):
        return False

    # Check for *_handler.py suffix
    if filename.endswith(TORNADO_HANDLER_SUFFIX):
        return True

    # Check if file is in a handlers/ or views/ directory
    path_parts = path.replace("\\", "/").split("/")
    return any(part in TORNADO_HANDLER_DIRS for part in path_parts)


def _detect_tornado_handlers(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Tornado handler endpoints from file patterns.

    Tornado is a Python web framework. Handlers are typically defined in
    files ending in _handler.py or in handlers/views directories.

    Only function/class symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_tornado_handler_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.TORNADO_HANDLER,
                confidence=0.85,
                label="Tornado handler",
            ))

    return entrypoints


def _is_aiohttp_view_file(path: str, language: str) -> bool:
    """Check if a file path matches Aiohttp view patterns.

    Matches:
    - Files ending in _view.py or _resource.py
    - Files named routes.py
    - Any .py file inside a resources/ directory

    Excludes test files.
    """
    if language != "python":
        return False

    # Exclude test files
    if _is_test_file(path):
        return False

    filename = _get_filename(path)

    # Check for *_view.py or *_resource.py suffix
    if any(filename.endswith(suffix) for suffix in AIOHTTP_VIEW_SUFFIXES):
        return True

    # Check for routes.py
    if filename in AIOHTTP_VIEW_FILES:
        return True

    # Check if file is in a resources/ directory
    path_parts = path.replace("\\", "/").split("/")
    return any(part in AIOHTTP_VIEW_DIRS for part in path_parts)


def _detect_aiohttp_views(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Aiohttp view endpoints from file patterns.

    Aiohttp is a Python async web framework. Views are typically defined in
    files ending in _view.py or _resource.py, or in resources/ directories.

    Only function/class symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_aiohttp_view_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.AIOHTTP_VIEW,
                confidence=0.85,
                label="Aiohttp view",
            ))

    return entrypoints


def _is_slim_route_file(path: str, language: str) -> bool:
    """Check if a file path matches Slim route patterns.

    Matches:
    - Files named routes.php
    - Files ending in Middleware.php
    - Any .php file inside an Actions/ or Handlers/ directory
    """
    if language != "php":
        return False

    filename = _get_filename(path)

    # Check for routes.php
    if filename in SLIM_ROUTE_FILES:
        return True

    # Check for *Middleware.php suffix
    if any(filename.endswith(suffix) for suffix in SLIM_ROUTE_SUFFIXES):
        return True

    # Check if file is in an Actions/ or Handlers/ directory
    path_parts = path.replace("\\", "/").split("/")
    return any(part in SLIM_ROUTE_DIRS for part in path_parts)


def _detect_slim_routes(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Slim route endpoints from file patterns.

    Slim is a PHP micro framework. Routes are typically defined in
    routes.php files, middleware files, or in Actions/Handlers directories.

    Only function/class symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_slim_route_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.SLIM_ROUTE,
                confidence=0.85,
                label="Slim route",
            ))

    return entrypoints


def _is_micronaut_controller_file(path: str, language: str) -> bool:
    """Check if a file path matches Micronaut HTTP client patterns.

    Matches:
    - Files ending in Client.java/kt (Micronaut declarative HTTP clients)
    - Any .java/.kt file inside a client/ directory

    Excludes common false positives:
    - *ServiceClient.java/kt (usually gRPC stub wrappers)
    - *GrpcClient.java/kt (gRPC clients)
    - *RpcClient.java/kt (RPC clients)
    """
    if language not in ("java", "kotlin"):
        return False

    filename = _get_filename(path)

    # Exclude common false positives: gRPC/RPC clients
    # These patterns are typically gRPC stub wrappers, not Micronaut HTTP clients
    non_micronaut_patterns = ("ServiceClient.", "GrpcClient.", "RpcClient.")
    if any(pattern in filename for pattern in non_micronaut_patterns):
        return False

    # Check for Client suffix
    if any(filename.endswith(suffix) for suffix in MICRONAUT_CLIENT_SUFFIXES):
        return True

    # Check if file is in a client/ directory
    path_parts = path.replace("\\", "/").split("/")
    return any(part in MICRONAUT_CLIENT_DIRS for part in path_parts)


def _detect_micronaut_controllers(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Micronaut controller endpoints from file patterns.

    Micronaut is a Java/Kotlin microframework. Controllers are typically
    in *Controller.java/kt files or in controller/ directories.
    Also detects Micronaut HTTP clients (*Client.java/kt).

    Only function/class symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like and class symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_micronaut_controller_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.MICRONAUT_CONTROLLER,
                confidence=0.85,
                label="Micronaut controller",
            ))

    return entrypoints


def _is_graphql_server_file(path: str, language: str) -> bool:
    """Check if a file path matches GraphQL server patterns.

    Matches:
    - Files named resolvers.js/ts, schema.js/ts, typeDefs.js/ts
    - Files with .resolver.js/ts or .resolvers.js/ts suffix
    - Any .js/.ts file inside a resolvers/ or graphql/ directory

    Excludes:
    - Test files
    - React components (.tsx/.jsx)
    - Non-GraphQL resolver patterns (dns, promise, dependency resolvers)
    """
    if language not in ("javascript", "typescript"):
        return False

    # Exclude test files
    if _is_test_file(path):
        return False

    # Exclude React components - these aren't GraphQL servers
    if path.endswith(".tsx") or path.endswith(".jsx"):
        return False

    filename = _get_filename(path)

    # Exclude non-GraphQL resolver patterns
    # These are typically infrastructure utilities, not GraphQL resolvers
    non_graphql_patterns = (
        "dns-resolver",
        "dns_resolver",
        "promise-resolver",
        "promise_resolver",
        "dependency-resolver",
        "dependency_resolver",
        "path-resolver",
        "path_resolver",
        "module-resolver",
        "module_resolver",
    )
    filename_lower = filename.lower()
    if any(pattern in filename_lower for pattern in non_graphql_patterns):
        return False

    # Check for GraphQL server file names
    if filename in GRAPHQL_SERVER_FILENAMES:
        return True

    # Check for resolver suffix patterns (.resolver.js, .resolvers.ts, etc.)
    if any(filename.endswith(suffix) for suffix in GRAPHQL_SERVER_SUFFIXES):
        return True

    # Check if file is in a resolvers/ or graphql/ directory
    path_parts = path.replace("\\", "/").split("/")
    return any(part in GRAPHQL_SERVER_DIRS for part in path_parts)


def _detect_graphql_servers(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect GraphQL server entry points from file patterns.

    GraphQL servers (Apollo Server, GraphQL Yoga, Mercurius, etc.) typically
    define resolvers in files named resolvers.js/ts, or with .resolver.js/ts
    suffixes. Schema files and typeDefs files are also entry points.

    Only function/class symbols are detected (not file symbols).
    """
    entrypoints = []

    for sym in symbols:
        # Only detect function-like symbols, not file symbols
        if sym.kind == "file":
            continue

        if _is_graphql_server_file(sym.path, sym.language):
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.GRAPHQL_SERVER,
                confidence=0.85,
                label="GraphQL resolver",
            ))

    return entrypoints


def _detect_django_views(
    symbols: List[Symbol],
    edges: List[Edge],
) -> List[Entrypoint]:
    """Detect Django view entrypoints from urls.py imports.

    Django uses path() and url() calls in urls.py files to map URLs to views.
    Rather than parsing the Python AST for these calls, we use a simpler heuristic:
    any function imported by a urls.py file is likely a Django view.

    This has high precision (urls.py imports are intentional) but may miss
    views defined inline or in the same file.
    """
    entrypoints = []

    # Find all urls.py file nodes
    urls_files = {
        sym.id for sym in symbols
        if sym.path.endswith("urls.py") and sym.kind == "file"
    }

    if not urls_files:
        return entrypoints

    # Find all imports from urls.py files
    for edge in edges:
        if edge.src in urls_files and edge.edge_type == "imports":
            # The destination is a symbol imported by urls.py - likely a view
            entrypoints.append(Entrypoint(
                symbol_id=edge.dst,
                kind=EntrypointKind.DJANGO_VIEW,
                confidence=0.90,
                label="Django view",
            ))

    return entrypoints


def detect_entrypoints(
    nodes: List[Symbol],
    edges: List[Edge],
) -> List[Entrypoint]:
    """Detect entrypoints in the codebase.

    Uses heuristics to find:
    - HTTP routes (FastAPI, Flask decorators)
    - CLI entrypoints (main guard, Click commands)
    - Electron entry points (main, preload, renderer files)

    Args:
        nodes: All symbols in the codebase.
        edges: All edges (currently unused, for future IPC detection).

    Returns:
        List of detected entrypoints with confidence scores.
    """
    entrypoints: List[Entrypoint] = []

    # Detect different types of entrypoints
    entrypoints.extend(_detect_http_routes(nodes))
    entrypoints.extend(_detect_cli_entrypoints(nodes))
    entrypoints.extend(_detect_electron_entrypoints(nodes))
    entrypoints.extend(_detect_django_views(nodes, edges))
    entrypoints.extend(_detect_express_routes(nodes))
    entrypoints.extend(_detect_nestjs_controllers(nodes))
    entrypoints.extend(_detect_micronaut_controllers(nodes))
    entrypoints.extend(_detect_spring_controllers(nodes))
    entrypoints.extend(_detect_rails_controllers(nodes))
    entrypoints.extend(_detect_phoenix_controllers(nodes))
    entrypoints.extend(_detect_go_handlers(nodes))
    entrypoints.extend(_detect_laravel_controllers(nodes))
    entrypoints.extend(_detect_rust_handlers(nodes))
    entrypoints.extend(_detect_aspnet_controllers(nodes))
    entrypoints.extend(_detect_sinatra_routes(nodes))
    entrypoints.extend(_detect_ktor_routes(nodes))
    entrypoints.extend(_detect_vapor_routes(nodes))
    entrypoints.extend(_detect_plug_routes(nodes))
    entrypoints.extend(_detect_koa_routes(nodes))
    entrypoints.extend(_detect_fastify_routes(nodes))
    entrypoints.extend(_detect_hapi_routes(nodes))
    entrypoints.extend(_detect_grape_apis(nodes))
    entrypoints.extend(_detect_tornado_handlers(nodes))
    entrypoints.extend(_detect_aiohttp_views(nodes))
    entrypoints.extend(_detect_slim_routes(nodes))
    entrypoints.extend(_detect_graphql_servers(nodes))

    # Remove duplicates (same symbol detected by multiple heuristics)
    seen_ids: set[str] = set()
    unique_entrypoints: List[Entrypoint] = []
    for ep in entrypoints:
        if ep.symbol_id not in seen_ids:
            seen_ids.add(ep.symbol_id)
            unique_entrypoints.append(ep)

    return unique_entrypoints
