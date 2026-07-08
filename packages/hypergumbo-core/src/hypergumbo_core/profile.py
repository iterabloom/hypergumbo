# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repo profile detection - language and framework heuristics.

This module provides fast, heuristic-based detection of programming
languages and frameworks in a repository, without requiring full parsing.

How It Works
------------
Language detection scans file extensions using the discovery module:
- Counts files matching each language's extension patterns
- Tallies lines of code (LOC) for each detected language
- Returns a RepoProfile with language statistics

Framework detection examines dependency manifests:
- Python: pyproject.toml, requirements.txt, setup.py, Pipfile
- JavaScript: package.json dependencies and devDependencies
- And more: Rust (Cargo.toml), Go (go.mod), Java (pom.xml, build.gradle), etc.

Recursive Manifest Scanning
---------------------------
Framework detection scans up to 3 directory levels deep to find manifests
in subdirectories. This enables detection in:
- Monorepos (e.g., backend/pyproject.toml, frontend/package.json)
- Non-standard layouts where manifests aren't at root
- Multi-project repositories

Common non-project directories (node_modules, vendor, venv, etc.) are skipped.

Detection is intentionally shallow - we look for package names in
dependency files rather than analyzing imports. This keeps profiling
fast (milliseconds) even for large repos.

Framework Specification (ADR-3aaa)
----------------------------------
The --frameworks flag controls which frameworks to check for:
- none: Skip framework detection (base analysis only)
- all: Check all known framework patterns for detected languages
- explicit: Only check specified frameworks (e.g., "fastapi,celery")
- auto (default): Auto-detect based on detected languages

This enables users to:
- Reduce noise by disabling framework detection (--frameworks=none)
- Exhaustively check all patterns (--frameworks=all)
- Focus on specific frameworks (--frameworks=fastapi,django)

Why This Design
---------------
- Extension-based language detection is simple and reliable
- Dependency file scanning catches frameworks even in empty repos
- Shallow heuristics prioritize speed over precision
- The profile informs which analyzers to run and what to expect
- Results are used by sketch generation for the language breakdown
"""
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .discovery import find_files
from .taxonomy import LANGUAGE_EXTENSIONS

# Framework detection patterns
# Maps framework name -> (file to check, pattern to look for)
PYTHON_FRAMEWORKS = {
    # Web frameworks
    "fastapi": ["fastapi"],
    "flask": ["flask", "Flask"],
    "flask-appbuilder": ["flask_appbuilder", "Flask-AppBuilder"],
    "django": ["django", "Django"],
    "aiohttp": ["aiohttp"],
    "starlette": ["starlette"],
    "quart": ["quart"],
    "sanic": ["sanic"],
    "litestar": ["litestar"],
    "falcon": ["falcon"],
    "bottle": ["bottle"],
    "cherrypy": ["cherrypy", "CherryPy"],
    "pyramid": ["pyramid"],
    "tornado": ["tornado"],
    # Testing
    "pytest": ["pytest"],
    # Data/ORM
    "sqlalchemy": ["sqlalchemy", "SQLAlchemy"],
    "pydantic": ["pydantic"],
    # Task queues
    "celery": ["celery"],
    # ML/AI - Deep Learning
    "pytorch": ["torch", "pytorch"],
    "tensorflow": ["tensorflow"],
    "keras": ["keras"],
    "jax": ["jax", "flax"],
    "paddlepaddle": ["paddlepaddle", "paddle"],
    # ML/AI - NLP/Transformers
    "transformers": ["transformers", "huggingface"],
    "spacy": ["spacy"],
    "nltk": ["nltk"],
    # ML/AI - LLM Orchestration
    "langchain": ["langchain"],
    "langgraph": ["langgraph"],
    "langsmith": ["langsmith"],
    "llamaindex": ["llama-index", "llama_index"],
    "haystack": ["haystack", "farm-haystack"],
    # ML/AI - Classical
    "scikit-learn": ["scikit-learn", "sklearn"],
    "xgboost": ["xgboost"],
    "lightgbm": ["lightgbm"],
    "catboost": ["catboost"],
    # ML/AI - GPU/CUDA
    "cuda": ["cupy", "pycuda", "numba"],
    # ML/AI - MLOps
    "mlflow": ["mlflow"],
    "wandb": ["wandb"],
    "optuna": ["optuna"],
    # ML/AI - Distributed/Serving
    "ray": ["ray"],
    "vllm": ["vllm"],
    "deepspeed": ["deepspeed"],
    # LLM APIs
    "openai": ["openai"],
    "anthropic": ["anthropic"],
    # MCP (Model Context Protocol)
    "mcp-python": ["mcp", "fastmcp"],
    # gRPC
    "grpc": ["grpcio", "grpc"],
    # GraphQL
    "graphql": ["graphql-core"],
    "graphql-python": ["strawberry-graphql", "ariadne", "graphene"],
    # CLI
    "cli": ["click", "typer", "fire", "argparse"],
}

JS_FRAMEWORKS = {
    # Frontend frameworks
    "react": ["react"],
    "vue": ["vue"],
    "angular": ["@angular/core"],
    "svelte": ["svelte"],
    "solid": ["solid-js"],
    "qwik": ["@builder.io/qwik"],
    "preact": ["preact"],
    "lit": ["lit"],
    "alpine": ["alpinejs"],
    "htmx": ["htmx.org"],
    "ember": ["ember-source", "ember-cli"],
    # Meta-frameworks
    "next": ["next"],
    "nuxt": ["nuxt"],
    "remix": ["@remix-run/react", "@remix-run/node"],
    "astro": ["astro"],
    "gatsby": ["gatsby"],
    "sveltekit": ["@sveltejs/kit"],
    # Backend frameworks
    "express": ["express"],
    "nestjs": ["@nestjs/core"],
    "fastify": ["fastify"],
    "koa": ["koa"],
    "hapi": ["@hapi/hapi"],
    "adonis": ["@adonisjs/core"],
    "sails": ["sails"],
    "hono": ["hono"],
    "elysia": ["elysia"],
    # GraphQL
    # WI-rofiz: removed bare "graphql" npm package — it's often installed
    # for type definitions or code generation without implementing a GraphQL
    # server, causing false-positive graphql_resolver nodes.
    "graphql": ["@apollo/server", "graphql-yoga", "mercurius", "type-graphql", "@nestjs/graphql"],
    "apollo": ["@apollo/client", "@apollo/server", "apollo-server"],
    # Mobile
    "react-native": ["react-native"],
    "expo": ["expo"],
    "ionic": ["@ionic/core", "@ionic/react", "@ionic/vue"],
    "capacitor": ["@capacitor/core"],
    "nativescript": ["nativescript", "@nativescript/core"],
    # Desktop
    "electron": ["electron"],
    "tauri": ["@tauri-apps/api"],
    # Blockchain/Web3
    "hardhat": ["hardhat"],
    "web3": ["web3"],
    "ethers": ["ethers"],
    "wagmi": ["wagmi"],
    "viem": ["viem"],
    # CLI
    "cli-js": ["commander", "yargs", "@oclif/core", "cac", "inquirer", "vorpal"],
    # MCP (Model Context Protocol)
    "mcp": ["@modelcontextprotocol/sdk"],
    # Audio
    "web_audio": ["tone", "howler", "pizzicato", "tuna", "resonance-audio", "omnitone", "standardized-audio-context"],
}

# Rust crate detection patterns (from Cargo.toml)
RUST_FRAMEWORKS = {
    # Web frameworks
    "actix-web": ["actix-web"],
    "axum": ["axum"],
    "rocket": ["rocket"],
    "warp": ["warp"],
    "tide": ["tide"],
    "gotham": ["gotham"],
    "poem": ["poem"],
    "salvo": ["salvo"],
    # Async runtimes
    "tokio": ["tokio"],
    "async-std": ["async-std"],
    # Serialization
    "serde": ["serde"],
    # CLI
    "clap": ["clap"],
    "cli-rust": ["clap", "structopt", "argh"],
    # Desktop
    "tauri": ["tauri"],
    # Blockchain - Ethereum/EVM
    "ethers": ["ethers", "ethers-rs"],
    "alloy": ["alloy"],
    "foundry": ["foundry-evm", "forge-std"],
    "revm": ["revm"],
    # Blockchain - Solana
    "solana": ["solana-sdk", "solana-program", "anchor-lang"],
    "anchor": ["anchor-lang", "anchor-spl"],
    # Blockchain - Substrate/Polkadot
    "substrate": ["substrate", "sp-core", "sp-runtime", "frame-support"],
    "polkadot": ["polkadot-sdk"],
    # Blockchain - Cosmos
    "cosmwasm": ["cosmwasm-std", "cosmwasm-schema"],
    # ZKP - General
    "arkworks": ["ark-ff", "ark-ec", "ark-poly", "ark-snark"],
    "bellman": ["bellman"],
    "halo2": ["halo2_proofs", "halo2-base"],
    # ZKP - Proving systems
    "plonky2": ["plonky2", "plonky2_field"],
    "plonky3": ["plonky3", "p3-field", "p3-matrix"],
    "groth16": ["ark-groth16", "bellman"],
    "plonk": ["ark-plonk", "plonk"],
    # ZKP - zkVMs
    "sp1": ["sp1-sdk", "sp1-core", "sp1-zkvm"],
    "risc0": ["risc0-zkvm", "risc0-zkp"],
    "jolt": ["jolt-sdk"],
    # ZKP - Nova/folding
    "nova": ["nova-snark", "supernova"],
    "hypernova": ["hypernova"],
    # Privacy
    "zcash": ["zcash_primitives", "zcash_proofs", "orchard"],
    # IPFS/Content addressing
    "ipfs": ["ipfs-api", "rust-ipfs", "cid"],
    "libp2p": ["libp2p"],
    # Cryptography
    "curve25519": ["curve25519-dalek"],
    "ed25519": ["ed25519-dalek"],
    "secp256k1": ["secp256k1", "k256"],
}

# Go module detection patterns (from go.mod)
GO_FRAMEWORKS = {
    # Web frameworks
    "gin": ["github.com/gin-gonic/gin"],
    "echo": ["github.com/labstack/echo"],
    "fiber": ["github.com/gofiber/fiber"],
    "chi": ["github.com/go-chi/chi"],
    "gorilla": ["github.com/gorilla/mux"],
    "buffalo": ["github.com/gobuffalo/buffalo"],
    "revel": ["github.com/revel/revel"],
    "beego": ["github.com/beego/beego"],
    "iris": ["github.com/kataras/iris"],
    # Prometheus common router (chi-like API) - used by prometheus, alertmanager, etc.
    "prometheus-common": ["github.com/prometheus/common"],
    # gRPC
    "grpc": ["google.golang.org/grpc"],
    # ORM
    "xorm": ["xorm.io/xorm"],
    # CLI
    "cli-go": ["github.com/spf13/cobra", "github.com/urfave/cli", "github.com/alecthomas/kong"],
}

# PHP composer.json detection patterns
PHP_FRAMEWORKS = {
    "laravel": ["laravel/framework"],
    "symfony": ["symfony/framework-bundle", "symfony/symfony"],
    "codeigniter": ["codeigniter4/framework"],
    "cakephp": ["cakephp/cakephp"],
    "yii": ["yiisoft/yii2"],
    "phalcon": ["phalcon/devtools"],
    "slim": ["slim/slim"],
}

# Java/Kotlin (pom.xml, build.gradle) detection patterns
JAVA_FRAMEWORKS = {
    # WI-tolap: Spring's Maven coordinate is org.springframework.boot, but a
    # Spring MVC controller imports its annotations from a DIFFERENT namespace
    # family (org.springframework.web.bind.annotation, org.springframework
    # .stereotype, org.springframework.context.annotation). refine_frameworks'
    # demote phase validates a manifest-detected framework by matching a prod
    # import against these patterns; with only the .boot coord, a real
    # @RestController that never imports org.springframework.boot was demoted to
    # dev_frameworks, starving enrich_symbols of spring-boot.yaml so no route/
    # controller concept fired. These extra entries are IMPORT namespaces, not
    # Maven group coords (spring-web's coord is org.springframework:spring-web),
    # so _pattern_matches_deps never matches them against a real dependency —
    # they widen import matching only, adding no manifest-detection FP.
    "spring-boot": [
        "spring-boot",
        "org.springframework.boot",
        "org.springframework.web",
        "org.springframework.stereotype",
        "org.springframework.context",
    ],
    "micronaut": ["micronaut", "io.micronaut"],
    "quarkus": ["quarkus", "io.quarkus"],
    "dropwizard": ["dropwizard-core", "dropwizard-jersey"],
    "vert.x": ["vertx", "io.vertx"],
    "javalin": ["javalin", "io.javalin"],
    "helidon": ["helidon", "io.helidon"],
    "spark": ["spark-java", "com.sparkjava"],
    # JAX-RS and implementations
    "jax-rs": ["javax.ws.rs", "jakarta.ws.rs"],
    "jersey": ["org.glassfish.jersey", "jersey-server", "jersey-container"],
    "resteasy": ["org.jboss.resteasy", "resteasy-jaxrs"],
    # API documentation
    "swagger": ["io.swagger", "swagger-annotations"],
    # Kotlin-specific
    "ktor": ["ktor", "io.ktor"],
    # Android - detect from build.gradle plugins, dependencies, and android {} blocks
    "android": [
        # Standard plugin IDs
        "com.android.application",
        "com.android.library",
        # Build tools dependency (in buildscript { dependencies { ... } })
        "com.android.tools.build:gradle",
        # Android DSL block (all Android projects have this)
        "android {",
        # Legacy import patterns (less common but valid)
        "android.app.activity",
    ],
    "jetpack-compose": ["androidx.compose", "compose.ui", "compose.runtime", "compose.material"],
    # Kafka Connect
    "kafka-connect": ["org.apache.kafka:connect-api", "kafka-connect", "connect-api"],
    # Google Guice DI
    "guice": ["com.google.inject", "google/inject", "guice"],
    # Kohsuke Stapler (Jenkins URL dispatch)
    "stapler": ["org.kohsuke.stapler", "stapler-core", "stapler-jelly"],
    # Jakarta CDI (standalone or via WildFly/GlassFish)
    "jakarta-cdi": [
        "jakarta.enterprise.context",
        "javax.enterprise.context",
        "jakarta.enterprise.inject",
        "javax.enterprise.inject",
        "weld",
    ],
    # gRPC
    "grpc": ["io.grpc", "grpc-core", "grpc-netty", "grpc-stub", "grpc-protobuf"],
}

# Swift Package.swift detection patterns
SWIFT_FRAMEWORKS = {
    "vapor": ["vapor"],
    "hummingbird": ["hummingbird"],
    "kitura": ["kitura"],
    "perfect": ["perfectlySoft"],
    "swiftui": ["swiftui"],  # Detected via imports, not SPM
}

# Scala (build.sbt) detection patterns.
# WI-nizuv: each carries its real import NAMESPACE alongside the build
# COORDINATE. The coordinate (com.typesafe.play / com.typesafe.akka / dev.zio)
# only appears in build.sbt and never matches the code's import path, so
# import promotion was dark. The namespace is specific enough (dotted, and
# scoped past the base library — akka.http not akka, zio.http not zio) to
# promote without a manifest and not fire on the base library.
SCALA_FRAMEWORKS = {
    "play": ["com.typesafe.play", "playframework", "play.api"],
    "akka-http": ["akka-http", "com.typesafe.akka", "akka.http"],
    "http4s": ["http4s", "org.http4s"],
    "zio-http": ["zio-http", "dev.zio", "zio.http"],
    "finatra": ["finatra", "com.twitter"],
}

# Ruby gem detection patterns (from Gemfile)
# WI-lohok: frameworks loaded by convention rather than explicit import.
# refine_frameworks's default rule (require a production import edge to
# confirm a framework) demotes these to dev_frameworks even on real
# production apps, because no app code says e.g. `require 'rails'` —
# Bundler / mix / similar machinery loads them at boot. enrich_symbols
# only loads framework patterns from profile.frameworks (not
# dev_frameworks), so a demoted framework's YAML never applies.
# Members of this set are exempted from the import-edge demotion check
# and stay in profile.frameworks based on manifest detection alone.
#
# Add to this set ONLY for frameworks whose canonical usage pattern is
# "declared in the manifest, autoloaded at runtime, not imported in app
# code." Counter-examples: Sinatra requires `require 'sinatra'` in app
# code; Django requires `from django.X import Y`; both correctly fall
# through the standard import-edge check.
_AUTOLOAD_BY_CONVENTION_FRAMEWORKS: frozenset[str] = frozenset({
    "rails",  # Ruby on Rails: gem 'rails' in Gemfile, no `require 'rails'` in app code
})


# WI-tosul Phase 2: route frameworks whose BARE module name may promote on an
# EXACT top-level import (not just the compound-submodule prefix arm that the
# WI-pusad bare-name gate otherwise requires). These are the dead-code
# route-monoculture root: a manifest-silent web app whose canonical usage is a
# top-level exact import (`from flask import Flask`, `const app = express()`)
# produced a bare EXACT import edge that the prefix gate rejected, so the
# framework's YAML never loaded and its routes stayed dark.
#
# INCLUSION RULE: add ONLY a bare module name whose exact import RELIABLY means
# "this repo is a backend web app defining routes with this framework" — a
# dedicated web/route framework in a well-tested ecosystem. Do NOT add a
# broadly-imported library, a dual-purpose token, a common word, a middleware
# spec, or a frontend/meta-framework. Counter-examples that MUST stay gated:
# `graphql` (type defs / codegen — WI-rofiz), `react`/`vue`/`solid` (frontend UI
# — WI-palol), `plug` (Elixir middleware spec, app-wide), `aiohttp` (primarily
# an HTTP *client*), `next`/`nex` (generic tokens). The `allowed_langs` gate in
# `_has_prod_import_match` already blocks cross-ecosystem collisions, so the
# residual FP risk of a member is within-language only.
#
# Seeded (WI-tosul Phase-2 scout, 2026-07-07) with the high-confidence dedicated
# web frameworks the current gate starves; expand only in bakeoff-validated
# batches (the NEEDS-BAKEOFF middle set — aiohttp/falcon/grape/vapor/etc. — must
# be measured for FP volume first).
_BARE_EXACT_PROMOTE_ROUTE_FRAMEWORKS: frozenset[str] = frozenset({
    # Python
    "flask", "fastapi", "sanic", "litestar", "quart", "starlette", "bottle",
    "tornado", "pyramid", "django", "flask-appbuilder",
    # JavaScript / TypeScript
    "express", "koa", "fastify",
    # Ruby
    "sinatra", "padrino",
})


RUBY_FRAMEWORKS = {
    # Web frameworks
    "rails": ["rails"],
    "sinatra": ["sinatra"],
    "grape": ["grape"],
    "hanami": ["hanami"],
    "roda": ["roda"],
    "padrino": ["padrino"],
    # GraphQL
    "graphql-ruby": ["graphql", "graphql-ruby"],
    # CLI
    "cli-ruby": ["thor", "gli", "dry-cli"],
    # Testing
    "rspec": ["rspec"],
    "minitest": ["minitest"],
}

# Elixir mix.exs detection patterns
ELIXIR_FRAMEWORKS = {
    # Web frameworks
    "phoenix": ["phoenix"],
    "plug": ["plug"],
    "nex": ["nex", "nex_core"],  # Minimalist web framework
    # Database
    "ecto": ["ecto"],
    # GraphQL
    "absinthe": ["absinthe"],
    # Testing
    "ex_unit": ["ex_unit"],
}

# Solidity framework detection (config file based, not dependency based)
# Maps framework name -> config file names to check for
SOLIDITY_FRAMEWORKS = {
    "foundry": ["foundry.toml"],
    "hardhat": ["hardhat.config.js", "hardhat.config.ts"],
}

# Haskell framework detection patterns (from *.cabal, stack.yaml, package.yaml)
HASKELL_FRAMEWORKS = {
    "servant": ["servant", "servant-server"],
    # WI-nizuv: the bare cabal name "scotty" matches stack's scotty-0.12.1 but
    # never the "Web.Scotty" module import, so import promotion was dark.
    "scotty": ["scotty", "Web.Scotty"],
    # Yesod — Rails-inspired Haskell web framework (haskellers et al.)
    "yesod": ["yesod", "yesod-core", "yesod-auth", "yesod-persistent"],
}

# Clojure framework detection patterns (from deps.edn, project.clj)
CLOJURE_FRAMEWORKS = {
    "ring-compojure": ["ring", "compojure", "ring/ring-core"],
    "pedestal": ["pedestal", "io.pedestal"],
}

# R framework detection patterns (from DESCRIPTION file)
R_FRAMEWORKS = {
    "shiny": ["shiny"],
    "plumber": ["plumber"],
}

# Lua framework detection patterns (from *.rockspec or special files)
LUA_FRAMEWORKS = {
    "openresty": ["openresty", "resty", "ngx"],
    "lapis": ["lapis"],
    "love2d": ["love"],
}

# C++ framework detection patterns (from CMakeLists.txt, *.pro, vcpkg.json)
CPP_FRAMEWORKS = {
    "qt": ["qt5", "qt6", "qtcore", "qtwidgets", "qtgui", "qmake", "qt +=", "qt+="],
}

# Erlang framework detection patterns (from rebar.config)
ERLANG_FRAMEWORKS = {
    "cowboy": ["cowboy"],
}

# F# framework detection patterns (from *.fsproj)
FSHARP_FRAMEWORKS = {
    "giraffe": ["giraffe"],
    "saturn": ["saturn"],
    "suave": ["suave"],
}

# Kotlin framework detection patterns (from build.gradle.kts)
# Separate from JAVA_FRAMEWORKS because Ktor is Kotlin-specific
KOTLIN_FRAMEWORKS = {
    "ktor": ["ktor-server", "io.ktor"],
    "exposed": ["exposed-core", "org.jetbrains.exposed"],
    "koin": ["koin-core", "io.insert-koin"],
    "kodein": ["kodein-di", "org.kodein.di"],
}

# C# framework detection patterns (from *.csproj)
CSHARP_FRAMEWORKS = {
    "aspnetcore": ["microsoft.aspnetcore", "asp.net core"],
    "blazor": ["microsoft.aspnetcore.components", "blazor"],
    "minimal-apis": ["microsoft.aspnetcore.openapi"],
    "entityframework": ["microsoft.entityframeworkcore", "entityframework"],
    "signalr": ["microsoft.aspnetcore.signalr"],
}

# Dart web framework detection patterns (from pubspec.yaml)
# Flutter is detected separately via SDK check
DART_FRAMEWORKS = {
    "shelf": ["shelf:"],
    "aqueduct": ["aqueduct:"],
    "angel": ["angel_framework:"],
    "dart_frog": ["dart_frog:"],
    "serverpod": ["serverpod:"],
}

# Julia framework detection patterns (from Project.toml)
JULIA_FRAMEWORKS = {
    "genie": ["genie"],
    "oxygen": ["oxygen"],
    "http": ["http"],
    "mux": ["mux"],
}

# OCaml framework detection patterns (from dune-project, *.opam)
OCAML_FRAMEWORKS = {
    "dream": ["dream"],
    "opium": ["opium"],
    "cohttp": ["cohttp"],
    "eliom": ["eliom"],
}

# Nim framework detection patterns (from *.nimble)
NIM_FRAMEWORKS = {
    "jester": ["jester"],
    "prologue": ["prologue"],
    "karax": ["karax"],
    "mummy": ["mummy"],
}

# Zig framework detection patterns (from build.zig.zon, build.zig)
ZIG_FRAMEWORKS = {
    "zap": ["zap"],
    "http.zig": ["httpz", "http.zig"],
    "zig-network": ["network"],
}

# D framework detection patterns (from dub.json, dub.sdl)
D_FRAMEWORKS = {
    "vibe-d": ["vibe-d", "vibe.d"],
    "hunt": ["hunt-framework", "hunt"],
    "diamondmvc": ["diamond"],
}

# Groovy framework detection patterns (from build.gradle)
GROOVY_FRAMEWORKS = {
    "grails": ["grails-core", "org.grails"],
    "ratpack": ["ratpack-core", "io.ratpack"],
    "micronaut-groovy": ["micronaut-runtime-groovy"],
}

# Map languages to their framework dictionaries
LANGUAGE_FRAMEWORKS: dict[str, dict[str, list[str]]] = {
    "python": PYTHON_FRAMEWORKS,
    "javascript": JS_FRAMEWORKS,
    "typescript": JS_FRAMEWORKS,  # TypeScript uses same frameworks as JS
    "rust": RUST_FRAMEWORKS,
    "go": GO_FRAMEWORKS,
    "php": PHP_FRAMEWORKS,
    "java": JAVA_FRAMEWORKS,
    "kotlin": KOTLIN_FRAMEWORKS,
    "swift": SWIFT_FRAMEWORKS,
    "scala": SCALA_FRAMEWORKS,
    "solidity": SOLIDITY_FRAMEWORKS,
    "ruby": RUBY_FRAMEWORKS,
    "elixir": ELIXIR_FRAMEWORKS,
    "haskell": HASKELL_FRAMEWORKS,
    "clojure": CLOJURE_FRAMEWORKS,
    "r": R_FRAMEWORKS,
    "lua": LUA_FRAMEWORKS,
    "cpp": CPP_FRAMEWORKS,
    "erlang": ERLANG_FRAMEWORKS,
    "fsharp": FSHARP_FRAMEWORKS,
    "csharp": CSHARP_FRAMEWORKS,
    "dart": DART_FRAMEWORKS,
    "julia": JULIA_FRAMEWORKS,
    "ocaml": OCAML_FRAMEWORKS,
    "nim": NIM_FRAMEWORKS,
    "zig": ZIG_FRAMEWORKS,
    "d": D_FRAMEWORKS,
    "groovy": GROOVY_FRAMEWORKS,
}

# Manifest patterns that differ from their actual import module name.
# Maps the manifest pattern (as it appears in *_FRAMEWORKS values) to the
# module name that appears in import edges.  Most packages import under their
# own name; only the exceptions are listed here.
IMPORT_OVERRIDES: dict[str, str] = {
    # Python: PyPI package name -> import name
    "pytorch": "torch",
    "scikit-learn": "sklearn",
    "grpcio": "grpc",
    "llama-index": "llama_index",
    "graphql-core": "graphql",
    "farm-haystack": "haystack",
    "Flask-AppBuilder": "flask_appbuilder",
    "CherryPy": "cherrypy",
    "SQLAlchemy": "sqlalchemy",
}


class FrameworkMode(Enum):
    """Mode for framework detection (ADR-3aaa).

    - NONE: Skip framework detection entirely
    - ALL: Check all known frameworks for detected languages
    - EXPLICIT: Only check explicitly specified frameworks
    - AUTO: Auto-detect based on detected languages (default)
    """

    NONE = "none"
    ALL = "all"
    EXPLICIT = "explicit"
    AUTO = "auto"


@dataclass
class FrameworkSpec:
    """Specification for which frameworks to check (ADR-3aaa).

    Attributes:
        mode: How frameworks were specified
        frameworks: Set of framework names to check for
        requested: Original user-requested frameworks (for explicit mode)
    """

    mode: FrameworkMode
    frameworks: set[str]
    requested: list[str] = field(default_factory=list)


def resolve_frameworks(
    spec: str | None,
    detected_languages: set[str],
) -> FrameworkSpec:
    """Resolve a framework specification to a concrete set of frameworks.

    Args:
        spec: Framework specification string:
            - None: Auto-detect (default)
            - "none": Skip framework detection
            - "all": Check all frameworks for detected languages
            - "fastapi,celery": Explicit list of frameworks
        detected_languages: Set of detected language names

    Returns:
        FrameworkSpec with mode and resolved framework set
    """
    if spec is None:
        # Auto-detect: return all frameworks for detected languages
        frameworks = _get_frameworks_for_languages(detected_languages)
        return FrameworkSpec(mode=FrameworkMode.AUTO, frameworks=frameworks)

    spec_lower = spec.lower().strip()

    if spec_lower == "none":
        return FrameworkSpec(mode=FrameworkMode.NONE, frameworks=set())

    if spec_lower == "all":
        # All frameworks for detected languages
        frameworks = _get_frameworks_for_languages(detected_languages)
        return FrameworkSpec(mode=FrameworkMode.ALL, frameworks=frameworks)

    # Explicit list: parse comma-separated framework names
    requested = [f.strip() for f in spec.split(",") if f.strip()]
    frameworks = set(requested)
    return FrameworkSpec(
        mode=FrameworkMode.EXPLICIT,
        frameworks=frameworks,
        requested=requested,
    )


def _get_frameworks_for_languages(languages: set[str]) -> set[str]:
    """Get all known frameworks for a set of languages.

    Args:
        languages: Set of language names

    Returns:
        Set of framework names available for those languages
    """
    frameworks: set[str] = set()
    for lang in languages:
        if lang in LANGUAGE_FRAMEWORKS:
            frameworks.update(LANGUAGE_FRAMEWORKS[lang].keys())
    return frameworks


@dataclass
class LanguageStats:
    """Statistics for a detected language.

    ``files`` is the count of files the language analyzer would enumerate
    on this repo — when an analyzer registers a canonical ``find_files``
    callable, that callable's output is the count (so extensionless
    shebang scripts surface for bash); otherwise the count falls back to
    the language's extension globs. Matches ``analysis_runs[L].files_analyzed``
    for languages with a registered analyzer.
    """

    files: int = 0
    loc: int = 0

    def to_dict(self) -> dict:
        return {"files": self.files, "loc": self.loc}

    @classmethod
    def from_dict(cls, d: dict) -> "LanguageStats":
        return cls(files=d.get("files", 0), loc=d.get("loc", 0))


@dataclass
class RepoProfile:
    """Profile of a repository's languages and frameworks."""

    languages: dict[str, LanguageStats] = field(default_factory=dict)
    frameworks: list[str] = field(default_factory=list)
    dev_frameworks: list[str] = field(default_factory=list)
    framework_mode: str = "auto"  # none, all, explicit, auto
    requested_frameworks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        result = {
            "languages": {k: v.to_dict() for k, v in self.languages.items()},
            "frameworks": sorted(self.frameworks),
            "dev_frameworks": sorted(self.dev_frameworks),
            "framework_mode": self.framework_mode,
        }
        # Only include requested_frameworks for explicit mode
        if self.framework_mode == "explicit":
            result["requested_frameworks"] = sorted(self.requested_frameworks)
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "RepoProfile":
        """Reconstruct a RepoProfile from a dict (e.g., from cached results)."""
        languages = {
            k: LanguageStats.from_dict(v)
            for k, v in d.get("languages", {}).items()
        }
        return cls(
            languages=languages,
            frameworks=d.get("frameworks", []),
            dev_frameworks=d.get("dev_frameworks", []),
            framework_mode=d.get("framework_mode", "auto"),
            requested_frameworks=d.get("requested_frameworks", []),
        )


def _count_loc(file_path: Path, max_file_size: int | None = None) -> int:
    """Count non-empty lines (SLOC convention).

    Excludes whitespace-only lines; does NOT strip comments. Matches the
    "code" tally produced by tools like `cloc` and `tokei` before any
    further comment-stripping pass. Expect ~10-20% lower than raw `wc -l`
    for typical source files (the gap is blank lines).

    Args:
        file_path: Path to the file.
        max_file_size: If set, skip files larger than this (bytes).
            Used by catalog command for quick heuristic scanning.
    """
    try:
        if max_file_size is not None and file_path.stat().st_size > max_file_size:
            return 0
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        return sum(1 for line in content.splitlines() if line.strip())
    except (OSError, IOError):  # pragma: no cover - defensive
        return 0


def _detect_languages(
    repo_root: Path,
    extra_excludes: list[str] | None = None,
    count_loc: bool = False,
) -> dict[str, LanguageStats]:
    """Detect languages by scanning file extensions.

    Returns file counts and optionally LOC per language.  When
    ``count_loc`` is False (the default), LOC is set to zero for speed.
    When True, each discovered file is read to count non-empty lines
    via ``_count_loc``.

    Args:
        repo_root: Path to the repository root.
        extra_excludes: Additional exclude patterns beyond DEFAULT_EXCLUDES.
        count_loc: If True, read files and compute LOC per language.
    """
    languages: dict[str, LanguageStats] = {}

    # Combine default and extra excludes
    from .discovery import DEFAULT_EXCLUDES
    excludes = list(DEFAULT_EXCLUDES)
    if extra_excludes:
        excludes.extend(extra_excludes)

    # INV-hokig: when a registered analyzer supplies a canonical
    # ``find_files`` callable, use it so this count agrees with the
    # analyzer's file enumeration (e.g., bash includes extensionless
    # shebang scripts that the extension-only glob would miss).
    from .analyze.registry import ensure_discovered, get_analyzer
    ensure_discovered()

    for lang, patterns in LANGUAGE_EXTENSIONS.items():
        analyzer = get_analyzer(lang)
        if analyzer is not None and analyzer.find_files is not None:
            files: set[Path] = set(analyzer.find_files(repo_root))
        else:
            # Use a set to deduplicate files (e.g., *.ts and *.d.ts both match foo.d.ts)
            files = set(find_files(repo_root, patterns, excludes=excludes))
        if files:
            loc = sum(_count_loc(f) for f in files) if count_loc else 0
            languages[lang] = LanguageStats(files=len(files), loc=loc)

    return languages


def _find_manifest_files(repo_root: Path, filename: str, max_depth: int = 3) -> list[Path]:
    """Find manifest files recursively up to max_depth.

    This enables framework detection in monorepos and projects with non-standard
    layouts where manifests are in subdirectories (e.g., backend/pyproject.toml).

    Args:
        repo_root: Path to the repository root.
        filename: Name of the manifest file to find (e.g., "pyproject.toml").
        max_depth: Maximum directory depth to search (default 3).

    Returns:
        List of paths to found manifest files.
    """
    found: list[Path] = []

    # Check root first
    root_file = repo_root / filename
    if root_file.exists() and root_file.is_file():
        found.append(root_file)

    # Search subdirectories up to max_depth
    # Use glob pattern that respects depth
    for depth in range(1, max_depth + 1):
        pattern = "/".join(["*"] * depth) + f"/{filename}"
        for path in repo_root.glob(pattern):
            if path.is_file():
                # Skip common non-project directories
                parts = path.relative_to(repo_root).parts
                if any(
                    p.startswith(".")
                    or p in ("node_modules", "vendor", "venv", ".venv", "__pycache__")
                    for p in parts[:-1]
                ):
                    continue
                # WI-sudug: skip manifests inside test-fixture directories.
                # detekt (Kotlin static-analysis tool) triggered a false
                # positive "react framework detected" because its test
                # fixtures contained package.json files referencing react.
                # Test fixtures do NOT represent real project dependencies.
                from .paths import is_test_file as _is_test_file
                rel_for_fixture_check = "/".join(parts)
                if _is_test_file(rel_for_fixture_check):
                    continue
                found.append(path)

    return found


def _read_all_manifest_files(repo_root: Path, filename: str, max_depth: int = 3) -> str:
    """Read all manifest files with given name, recursively.

    Args:
        repo_root: Path to the repository root.
        filename: Name of the manifest file to find.
        max_depth: Maximum directory depth to search.

    Returns:
        Concatenated lowercase content of all found files.
    """
    content_parts: list[str] = []
    for path in _find_manifest_files(repo_root, filename, max_depth):
        try:
            content_parts.append(path.read_text(errors="ignore").lower())
        except (OSError, IOError):  # pragma: no cover
            pass
    return "\n".join(content_parts)


def _manifest_has_package(content: str, package: str) -> bool:
    """Check if a package name appears as a distinct token in manifest content.

    Uses conditional word boundaries to avoid false positives from substring
    collisions. For example, 'bottle' should NOT match in 'bottleneck',
    but SHOULD match in 'bottle==0.12' or '"bottle"'.

    Word boundaries are only added at positions where the pattern starts/ends
    with a word character (alphanumeric or underscore). Patterns ending with
    non-word characters like ':' or '{' (e.g., 'shelf:', 'android {') don't
    get a trailing boundary, since the non-word character itself provides
    sufficient delimitation.

    DEPRECATED: New code should use ``_pattern_matches_deps`` against a
    structured ``set[str]`` of declared dep names from one of the
    ``_parse_*_deps`` helpers below.  Retained for DSL-marker fallbacks
    (e.g., ``"android {"``, ``"qt +="``) that aren't package names.

    Args:
        content: Lowercased concatenated manifest file content.
        package: Package/library name to search for (case-insensitive).

    Returns:
        True if the package name appears as a distinct token in the content.
    """
    escaped = re.escape(package.lower())
    # Add word boundary only where pattern starts/ends with a word character
    prefix = r"\b" if re.match(r"\w", package) else ""
    suffix = r"\b" if re.search(r"\w$", package) else ""
    return bool(re.search(prefix + escaped + suffix, content))


# ---------------------------------------------------------------------------
# INV-vunaf: structured manifest dep-name parsers.
#
# The historical detector flow concatenated all manifest text and ran
# word-boundary regex against framework-name patterns.  That produced
# false positives from:
#
#   * comments (e.g., "# torch was considered")
#   * pytest marker names (e.g., ``markers = ["torch: ..."]``)
#   * partial-substring collisions (``transformers`` in
#     ``sentence-transformers``)
#
# The parsers below extract the *declared dep names* from each format,
# returning a normalized lowercase ``set[str]`` that detectors check
# against with exact or token-boundary matching.  This mirrors the safe
# pattern that ``_detect_js_frameworks`` / ``_detect_php_frameworks``
# already use (JSON-parse, inspect ``dependencies`` keys).
# ---------------------------------------------------------------------------


def _load_toml(content: str) -> dict | None:
    """Parse TOML content; return None on failure.

    Resolves a TOML loader (tomllib on 3.11+, tomli fallback) following the
    pattern in ``linkers/subprocess_cli.py``.
    """
    try:
        import tomllib
    except ImportError:  # pragma: no cover - py3.10 fallback
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:  # pragma: no cover
            return None
    try:
        return tomllib.loads(content)
    except (ValueError, TypeError):
        return None


_PEP508_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _pep508_dist_name(spec: str) -> str | None:
    """Extract the distribution name from a PEP 508 requirement spec.

    Examples::

        "flask"               -> "flask"
        "flask>=2.0"          -> "flask"
        "flask[extra]>=2.0"   -> "flask"
        "sentence-transformers~=5.2.2" -> "sentence-transformers"
        "flask @ git+https://..." -> "flask"

    Returns None when the spec doesn't start with a valid PEP 508 name.
    """
    spec = spec.strip()
    if not spec or spec.startswith("#"):
        return None
    match = _PEP508_NAME_RE.match(spec)
    return match.group(1).lower() if match else None


def _parse_pyproject_deps(content: str) -> set[str]:
    """Extract dep names from pyproject.toml content.

    Covers PEP 621 (``[project]``), Poetry (``[tool.poetry]``), and PEP 735
    (``[dependency-groups]``).
    """
    deps: set[str] = set()
    data = _load_toml(content)
    if not isinstance(data, dict):
        return deps

    project = data.get("project")
    if isinstance(project, dict):
        for entry in project.get("dependencies") or []:
            if isinstance(entry, str):
                name = _pep508_dist_name(entry)
                if name:
                    deps.add(name)
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for entries in optional.values():
                if isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, str):
                            name = _pep508_dist_name(entry)
                            if name:
                                deps.add(name)

    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for entries in groups.values():
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, str):
                        name = _pep508_dist_name(entry)
                        if name:
                            deps.add(name)

    poetry = data.get("tool", {}).get("poetry") if isinstance(data.get("tool"), dict) else None
    if isinstance(poetry, dict):
        for key in ("dependencies", "dev-dependencies"):
            entries = poetry.get(key)
            if isinstance(entries, dict):
                for name in entries:
                    if isinstance(name, str) and name.lower() != "python":
                        deps.add(name.lower())
        groups_p = poetry.get("group")
        if isinstance(groups_p, dict):
            for group_data in groups_p.values():
                if isinstance(group_data, dict):
                    entries = group_data.get("dependencies")
                    if isinstance(entries, dict):
                        for name in entries:
                            if isinstance(name, str) and name.lower() != "python":
                                deps.add(name.lower())

    return deps


def _parse_requirements_txt_deps(content: str) -> set[str]:
    """Extract dep names from a requirements.txt-style file.

    Strips ``#`` comments, ignores ``-e`` / ``--editable`` / ``-r`` / ``-c``
    options, and extracts the PEP 508 distribution name from each line.
    """
    deps: set[str] = set()
    for raw in content.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        # Strip option prefixes like "-e", "--editable", "-r requirements.txt"
        if line.startswith("-"):
            # Skip pip options entirely; in-line VCS specs after `-e` rarely
            # include a clean PEP 508 name we can rely on.
            continue
        name = _pep508_dist_name(line)
        if name:
            deps.add(name)
    return deps


def _parse_requirements_txt_dash_r_includes(content: str) -> list[str]:
    """Return the relative paths that this requirements file ``-r``-includes.

    WI-himas: pip's ``-r <file>`` and ``-c <file>`` directives compose a
    dependency closure across multiple requirements files (the Wagtail /
    bakerydemo layout uses ``requirements/dev.txt`` opening with
    ``-r base.txt``). The framework detector needs to follow these to
    surface the full transitive dep set.
    """
    includes: list[str] = []
    for raw in content.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        # Match ``-r <path>`` / ``--requirement <path>`` / ``-c <path>`` /
        # ``--constraint <path>``. The PEP 508 grammar reserves both r/c.
        for prefix in ("-r ", "--requirement ", "-c ", "--constraint ",
                       "-r=", "--requirement=", "-c=", "--constraint="):
            if line.startswith(prefix):
                ref = line[len(prefix):].strip().strip("'\"")
                if ref:
                    includes.append(ref)
                break
    return includes


def _collect_pip_requirements_deps(repo_root: Path, max_depth: int = 3) -> set[str]:
    """Find every pip-requirements-shaped file and union their parsed deps.

    WI-himas: the manifest set is the closure of ``requirements.txt``,
    every ``requirements/*.txt``, every ``requirements-*.txt``, and any
    file each of those ``-r``-includes (transitively, bounded by
    ``repo_root``). Outside-repo references are dropped — following
    ``-r ../host-file.txt`` would let a malicious manifest parse
    arbitrary files on the host.
    """
    deps: set[str] = set()
    seeds: set[Path] = set()

    # Seed 1: literal "requirements.txt" anywhere in the manifest tree.
    seeds.update(_find_manifest_files(repo_root, "requirements.txt", max_depth))
    # Seed 2: requirements/*.txt and requirements-*.txt layouts.
    for pattern in ("requirements/*.txt", "requirements-*.txt"):
        for depth in range(max_depth + 1):
            glob_pattern = "/".join(["*"] * depth + [pattern]) if depth else pattern
            for path in repo_root.glob(glob_pattern):
                if not path.is_file():  # pragma: no cover - glob('*.txt') shouldn't return dirs
                    continue
                parts = path.relative_to(repo_root).parts
                # Mirror _find_manifest_files's directory exclusions.
                if any(
                    p.startswith(".")
                    or p in ("node_modules", "vendor", "venv", ".venv", "__pycache__")
                    for p in parts[:-1]
                ):
                    continue
                from .paths import is_test_file as _is_test_file
                rel_for_fixture_check = "/".join(parts)
                if _is_test_file(rel_for_fixture_check):  # pragma: no cover - requirements/ rarely under test/
                    continue
                seeds.add(path)

    # Resolve the -r include chain. Each visited file contributes its own
    # parsed deps; outside-repo references are dropped at resolution time.
    visited: set[Path] = set()
    work: list[Path] = list(seeds)
    repo_root_resolved = repo_root.resolve()
    while work:
        current = work.pop()
        try:
            current_resolved = current.resolve()
        except OSError:  # pragma: no cover - defensive for race / symlink loops
            continue
        if current_resolved in visited:
            continue
        visited.add(current_resolved)
        # Bound to repo: drop anything outside repo_root. Defensive — seeds
        # are all repo-rooted and -r candidates are bounds-checked before
        # enqueue, but resolved paths can drift across symlinks.
        try:
            current_resolved.relative_to(repo_root_resolved)
        except ValueError:  # pragma: no cover - defensive symlink-escape guard
            continue
        text = _read_manifest_text(current)
        if not text:  # pragma: no cover - defensive for empty/unreadable file
            continue
        deps |= _parse_requirements_txt_deps(text)
        for ref in _parse_requirements_txt_dash_r_includes(text):
            # ``-r`` paths are relative to the including file's directory.
            candidate = (current.parent / ref).resolve()
            try:
                candidate.relative_to(repo_root_resolved)
            except ValueError:
                continue
            if candidate.is_file():
                work.append(candidate)

    return deps


_SETUP_INSTALL_REQUIRES_RE = re.compile(
    r"install_requires\s*=\s*[\[\(](?P<body>[^\)\]]*)[\)\]]",
    re.DOTALL,
)
_SETUP_EXTRAS_REQUIRE_RE = re.compile(
    r"extras_require\s*=\s*\{(?P<body>[^\}]*)\}",
    re.DOTALL,
)
_QUOTED_STRING_RE = re.compile(r"['\"]([^'\"]+)['\"]")


def _parse_setup_py_deps(content: str) -> set[str]:
    """Extract dep names from setup.py's ``install_requires`` / ``extras_require``.

    Best-effort regex parse: scoped to the literal list/tuple/dict body to
    avoid catching unrelated quoted strings (e.g., comments at module top
    that mention package names).
    """
    deps: set[str] = set()
    stripped = _strip_python_line_comments(content)
    for match in _SETUP_INSTALL_REQUIRES_RE.finditer(stripped):
        for spec in _QUOTED_STRING_RE.findall(match.group("body")):
            name = _pep508_dist_name(spec)
            if name:
                deps.add(name)
    for match in _SETUP_EXTRAS_REQUIRE_RE.finditer(stripped):
        for spec in _QUOTED_STRING_RE.findall(match.group("body")):
            name = _pep508_dist_name(spec)
            if name:
                deps.add(name)
    return deps


def _strip_python_line_comments(content: str) -> str:
    """Drop ``#``-prefixed line comments without disturbing literal ``#``
    characters inside quoted strings.

    Approach: walk character-by-character tracking single/double/triple
    quote state. When outside a string, ``#`` to end-of-line is dropped.
    """
    out: list[str] = []
    i = 0
    n = len(content)
    in_single = False
    in_double = False
    in_triple_single = False
    in_triple_double = False
    while i < n:
        ch = content[i]
        rest3 = content[i : i + 3]
        if not (in_single or in_double or in_triple_single or in_triple_double):
            if rest3 == "'''":
                in_triple_single = True
                out.append(rest3)
                i += 3
                continue
            if rest3 == '"""':
                in_triple_double = True
                out.append(rest3)
                i += 3
                continue
            if ch == "'":
                in_single = True
                out.append(ch)
                i += 1
                continue
            if ch == '"':
                in_double = True
                out.append(ch)
                i += 1
                continue
            if ch == "#":
                # Skip to end of line
                while i < n and content[i] != "\n":
                    i += 1
                continue
            out.append(ch)
            i += 1
            continue
        # Inside some string
        if in_triple_single and rest3 == "'''":
            in_triple_single = False
            out.append(rest3)
            i += 3
            continue
        if in_triple_double and rest3 == '"""':
            in_triple_double = False
            out.append(rest3)
            i += 3
            continue
        if in_single and ch == "'" and (i == 0 or content[i - 1] != "\\"):
            in_single = False
        if in_double and ch == '"' and (i == 0 or content[i - 1] != "\\"):
            in_double = False
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_pipfile_deps(content: str) -> set[str]:
    """Extract dep names from a Pipfile (TOML format).

    Walks ``[packages]`` and ``[dev-packages]`` sections.
    """
    deps: set[str] = set()
    data = _load_toml(content)
    if not isinstance(data, dict):
        return deps
    for key in ("packages", "dev-packages"):
        section = data.get(key)
        if isinstance(section, dict):
            for name in section:
                if isinstance(name, str):
                    deps.add(name.lower())
    return deps


def _parse_cargo_toml_deps(content: str) -> set[str]:
    """Extract crate names from a Cargo.toml.

    Walks ``[dependencies]``, ``[dev-dependencies]``,
    ``[build-dependencies]`` and ``[target.*.dependencies]``.
    """
    deps: set[str] = set()
    data = _load_toml(content)
    if not isinstance(data, dict):
        return deps
    for key in ("dependencies", "dev-dependencies", "build-dependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            for name in section:
                if isinstance(name, str):
                    deps.add(name.lower())
    targets = data.get("target")
    if isinstance(targets, dict):
        for tgt in targets.values():
            if isinstance(tgt, dict):
                for key in ("dependencies", "dev-dependencies", "build-dependencies"):
                    section = tgt.get(key)
                    if isinstance(section, dict):
                        for name in section:
                            if isinstance(name, str):
                                deps.add(name.lower())
    workspace = data.get("workspace")
    if isinstance(workspace, dict):
        section = workspace.get("dependencies")
        if isinstance(section, dict):
            for name in section:
                if isinstance(name, str):
                    deps.add(name.lower())
    return deps


_GO_MOD_REQUIRE_BLOCK_RE = re.compile(r"require\s*\(([^)]*)\)", re.DOTALL)
_GO_MOD_REQUIRE_LINE_RE = re.compile(r"^\s*require\s+(\S+)\s+\S", re.MULTILINE)


def _parse_go_mod_deps(content: str) -> set[str]:
    """Extract module paths from a go.mod file.

    Handles both block (``require ( ... )``) and single-line forms; strips
    ``//`` line comments before tokenizing.
    """
    deps: set[str] = set()
    # Strip line comments (// to end-of-line) while leaving block comments
    # intact -- go.mod doesn't use /* */ in practice.
    stripped_lines = []
    for line in content.splitlines():
        comment = line.find("//")
        if comment >= 0:
            line = line[:comment]
        stripped_lines.append(line)
    stripped = "\n".join(stripped_lines)

    for block in _GO_MOD_REQUIRE_BLOCK_RE.findall(stripped):
        for raw in block.splitlines():
            line = raw.strip()
            if not line:
                continue
            tokens = line.split()
            if tokens:
                deps.add(tokens[0].lower())
    for path in _GO_MOD_REQUIRE_LINE_RE.findall(stripped):
        deps.add(path.lower())
    return deps


_XML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_POM_DEP_RE = re.compile(
    r"<(?:dependency|plugin|parent)>(.*?)</(?:dependency|plugin|parent)>",
    re.DOTALL | re.IGNORECASE,
)
_POM_GROUP_RE = re.compile(r"<groupId>\s*([^<]+?)\s*</groupId>", re.IGNORECASE)
_POM_ARTIFACT_RE = re.compile(r"<artifactId>\s*([^<]+?)\s*</artifactId>", re.IGNORECASE)


def _parse_pom_xml_deps(content: str) -> set[str]:
    """Extract group / artifact / coordinate strings from a pom.xml.

    Strips XML comments first.  Returns the set of group, artifact, and
    ``group:artifact`` strings declared in ``<dependency>``, ``<plugin>``,
    and ``<parent>`` blocks.
    """
    deps: set[str] = set()
    stripped = _XML_COMMENT_RE.sub("", content)
    for block in _POM_DEP_RE.findall(stripped):
        group_m = _POM_GROUP_RE.search(block)
        artifact_m = _POM_ARTIFACT_RE.search(block)
        group = group_m.group(1).strip().lower() if group_m else None
        artifact = artifact_m.group(1).strip().lower() if artifact_m else None
        if group:
            deps.add(group)
        if artifact:
            deps.add(artifact)
        if group and artifact:
            deps.add(f"{group}:{artifact}")
    return deps


# Gradle dependency declarations (subset; covers common configurations).
_GRADLE_DEP_CONFIGURATIONS = (
    "implementation",
    "api",
    "compile",
    "compileOnly",
    "runtimeOnly",
    "testImplementation",
    "testCompile",
    "testRuntime",
    "annotationProcessor",
    "kapt",
    "ksp",
    "classpath",
    "platform",
)
_GRADLE_DEP_RE = re.compile(
    r"^\s*(?:" + "|".join(_GRADLE_DEP_CONFIGURATIONS) + r")\s*[\(\s]\s*"
    r"(?:platform\s*\()?\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)
_GRADLE_PLUGIN_ID_RE = re.compile(
    r"\bid\s*[\(\s]\s*['\"]([^'\"]+)['\"]",
)
# Maven-shaped coordinates inside any quoted string: ``"<group>:<artifact>[:<version>]"``
# with the group containing at least one ``.`` (rules out generic ``"key:value"``
# pairs that aren't deps).  Captures multi-module Gradle projects that
# declare coordinates in helper map structures rather than ``implementation(...)``
# blocks (e.g., Apache Kafka's ``gradle/dependencies.gradle``).
_GRADLE_MAVEN_COORD_RE = re.compile(
    r"['\"]([a-zA-Z][\w.-]*\.[\w.-]+):([\w.-]+)(?::[^'\"]*)?['\"]"
)


def _strip_cstyle_comments(content: str) -> str:
    """Strip ``//`` line and ``/* */`` block comments while respecting strings.

    Walks character-by-character tracking single/double-quoted string state
    so that a ``//`` or ``/*`` inside a string literal is preserved (e.g.,
    URLs like ``"https://example.com"`` survive intact).
    """
    out: list[str] = []
    i = 0
    n = len(content)
    in_double = False
    in_single = False
    while i < n:
        ch = content[i]
        if in_double:
            out.append(ch)
            if ch == '"' and content[i - 1] != "\\":
                in_double = False
            i += 1
            continue
        if in_single:
            out.append(ch)
            if ch == "'" and content[i - 1] != "\\":
                in_single = False
            i += 1
            continue
        if ch == '"':
            in_double = True
            out.append(ch)
            i += 1
            continue
        if ch == "'":
            in_single = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = content[i + 1]
            if nxt == "/":
                # Line comment: skip to end-of-line (don't consume the newline).
                while i < n and content[i] != "\n":
                    i += 1
                continue
            if nxt == "*":
                # Block comment: skip until */.
                i += 2
                while i + 1 < n and not (content[i] == "*" and content[i + 1] == "/"):
                    i += 1
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_gradle_comments(content: str) -> str:
    """Strip C-style comments while respecting string literals."""
    return _strip_cstyle_comments(content)


def _parse_gradle_deps(content: str) -> set[str]:
    """Extract Maven coordinates and plugin IDs from a Gradle script.

    For each ``implementation("group:artifact:version")``-style line, emits
    ``group``, ``artifact``, and ``group:artifact`` tokens.  Also extracts
    plugin IDs from ``id("plugin.id")`` declarations and any free-floating
    Maven-coord-shaped quoted string (helper map structures used by
    multi-module projects).  Strips ``//`` and ``/* */`` comments first
    while respecting string literals.
    """
    deps: set[str] = set()
    stripped = _strip_gradle_comments(content)
    for coord in _GRADLE_DEP_RE.findall(stripped):
        coord = coord.strip().lower()
        if not coord:
            continue
        parts = coord.split(":")
        if len(parts) >= 2:
            group, artifact = parts[0], parts[1]
            deps.add(group)
            deps.add(artifact)
            deps.add(f"{group}:{artifact}")
        else:
            deps.add(coord)
    for plugin_id in _GRADLE_PLUGIN_ID_RE.findall(stripped):
        deps.add(plugin_id.strip().lower())
    for group, artifact in _GRADLE_MAVEN_COORD_RE.findall(stripped):
        group_lc = group.lower()
        artifact_lc = artifact.lower()
        deps.add(group_lc)
        deps.add(artifact_lc)
        deps.add(f"{group_lc}:{artifact_lc}")
    return deps


_GEMFILE_GEM_RE = re.compile(r"^\s*gem\s+['\"]([^'\"]+)['\"]", re.MULTILINE)


def _parse_gemfile_deps(content: str) -> set[str]:
    """Extract gem names from a Gemfile, stripping ``#`` line comments."""
    deps: set[str] = set()
    stripped_lines = []
    for line in content.splitlines():
        idx = line.find("#")
        if idx >= 0:
            line = line[:idx]
        stripped_lines.append(line)
    stripped = "\n".join(stripped_lines)
    for name in _GEMFILE_GEM_RE.findall(stripped):
        deps.add(name.strip().lower())
    return deps


_MIX_EXS_DEP_RE = re.compile(r"\{\s*:([a-zA-Z_][a-zA-Z0-9_]*)\s*,")


def _parse_mix_exs_deps(content: str) -> set[str]:
    """Extract Hex package names from a mix.exs ``deps`` function.

    Looks for ``{:atom_name, ...}`` tuples, stripping ``#`` line comments
    first.  Elixir-style triple-quote heredoc comments are uncommon in
    mix.exs and not handled.
    """
    deps: set[str] = set()
    stripped_lines = []
    for line in content.splitlines():
        idx = line.find("#")
        if idx >= 0:
            line = line[:idx]
        stripped_lines.append(line)
    stripped = "\n".join(stripped_lines)
    for atom in _MIX_EXS_DEP_RE.findall(stripped):
        deps.add(atom.lower())
    return deps


_SBT_LIB_DEP_RE = re.compile(
    r"['\"]([A-Za-z0-9._-]+)['\"]\s*%{1,2}\s*['\"]([A-Za-z0-9._-]+)['\"]"
)
_SBT_PLUGIN_RE = re.compile(
    r"addSbtPlugin\(\s*['\"]([A-Za-z0-9._-]+)['\"]\s*%\s*['\"]([A-Za-z0-9._-]+)['\"]",
)


def _parse_sbt_deps(content: str) -> set[str]:
    """Extract org/artifact coordinates from SBT-style content.

    Handles both ``"org" %% "artifact"`` library dependencies and
    ``addSbtPlugin("org" % "plugin")`` declarations.  Strips ``//`` and
    ``/* */`` comments first.
    """
    deps: set[str] = set()
    stripped = _strip_gradle_comments(content)  # same comment syntax as Gradle/Java
    for group, artifact in _SBT_LIB_DEP_RE.findall(stripped):
        group_lc = group.lower()
        artifact_lc = artifact.lower()
        deps.add(group_lc)
        deps.add(artifact_lc)
        deps.add(f"{group_lc}:{artifact_lc}")
    for group, artifact in _SBT_PLUGIN_RE.findall(stripped):
        deps.add(group.lower())
        deps.add(artifact.lower())
    return deps


_SWIFT_PACKAGE_URL_RE = re.compile(r"\.package\s*\([^)]*?url:\s*['\"]([^'\"]+)['\"]")
_SWIFT_PACKAGE_NAME_RE = re.compile(r"\.package\s*\([^)]*?name:\s*['\"]([^'\"]+)['\"]")
_SWIFT_PACKAGE_PATH_RE = re.compile(r"\.package\s*\(\s*path:\s*['\"]([^'\"]+)['\"]")


def _parse_package_swift_deps(content: str) -> set[str]:
    """Extract dependency names from a Package.swift manifest.

    Strips ``//`` and ``/* */`` comments first, then walks ``.package(...)``
    declarations and extracts the last URL path component (or the explicit
    ``name:`` argument when present).
    """
    deps: set[str] = set()
    stripped = _strip_gradle_comments(content)  # Swift comments match Gradle/Java
    for url in _SWIFT_PACKAGE_URL_RE.findall(stripped):
        # Drop trailing ".git" and take last path segment as the package name.
        tail = url.rstrip("/").rsplit("/", 1)[-1]
        if tail.endswith(".git"):
            tail = tail[:-4]
        if tail:
            deps.add(tail.lower())
    for name in _SWIFT_PACKAGE_NAME_RE.findall(stripped):
        deps.add(name.strip().lower())
    for path in _SWIFT_PACKAGE_PATH_RE.findall(stripped):
        tail = path.rstrip("/").rsplit("/", 1)[-1]
        if tail:
            deps.add(tail.lower())
    return deps


def _parse_description_deps(content: str) -> set[str]:
    """Extract package names from an R DESCRIPTION file.

    Walks ``Imports:``, ``Depends:``, ``LinkingTo:``, ``Suggests:``, and
    ``Enhances:`` fields (RFC822-style, supporting continuation lines).
    Strips ``#`` line comments first.
    """
    deps: set[str] = set()
    stripped_lines = []
    for line in content.splitlines():
        idx = line.find("#")
        if idx >= 0:
            line = line[:idx]
        stripped_lines.append(line)
    stripped = "\n".join(stripped_lines)

    field_names = ("Imports", "Depends", "LinkingTo", "Suggests", "Enhances")
    field_re = re.compile(
        r"^(" + "|".join(field_names) + r")\s*:\s*(.*?)(?=^\S|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    for _name, body in field_re.findall(stripped):
        for raw in body.split(","):
            # Strip version constraints "(>= 1.0)" and whitespace.
            name = re.split(r"[\s\(]", raw.strip(), maxsplit=1)[0]
            if name and name.lower() != "r":
                deps.add(name.lower())
    return deps


def _parse_project_toml_deps(content: str) -> set[str]:
    """Extract Julia dep names from a ``Project.toml`` ``[deps]`` section."""
    deps: set[str] = set()
    data = _load_toml(content)
    if not isinstance(data, dict):
        return deps
    section = data.get("deps")
    if isinstance(section, dict):
        for name in section:
            if isinstance(name, str):
                deps.add(name.lower())
    return deps


def _parse_pubspec_yaml_deps(content: str) -> set[str]:
    """Extract Dart dep names from ``pubspec.yaml``.

    No PyYAML dep available; uses a hand-rolled indent-aware line parser
    for the ``dependencies:`` / ``dev_dependencies:`` /
    ``dependency_overrides:`` sections.  Strips ``#`` comments.
    """
    deps: set[str] = set()
    in_section = False
    section_indent = -1
    for raw in content.splitlines():
        # Strip line comments.
        idx = raw.find("#")
        line = raw[:idx] if idx >= 0 else raw
        if not line.strip():
            continue
        leading = len(line) - len(line.lstrip())
        stripped = line.strip()
        if leading == 0:
            in_section = stripped.rstrip(":") in (
                "dependencies",
                "dev_dependencies",
                "dependency_overrides",
            ) and stripped.endswith(":")
            section_indent = 0
            continue
        if in_section and leading > section_indent:
            # Dep entries look like "name:" or "name: version".
            match = re.match(r"^([A-Za-z0-9_][A-Za-z0-9_-]*)\s*:", stripped)
            if match:
                deps.add(match.group(1).lower())
    return deps


_PACKAGE_REF_RE = re.compile(
    r'<PackageReference[^>]*?Include\s*=\s*"([^"]+)"', re.IGNORECASE
)
_REFERENCE_RE = re.compile(
    r'<Reference[^>]*?Include\s*=\s*"([^"]+)"', re.IGNORECASE
)


def _parse_msbuild_proj_deps(content: str) -> set[str]:
    """Extract NuGet package names from a *.csproj / *.fsproj / *.vcxproj.

    Strips XML comments, then walks ``<PackageReference Include="...">``
    and ``<Reference Include="...">`` elements.
    """
    deps: set[str] = set()
    stripped = _XML_COMMENT_RE.sub("", content)
    for name in _PACKAGE_REF_RE.findall(stripped):
        deps.add(name.strip().lower())
    for name in _REFERENCE_RE.findall(stripped):
        # ``<Reference Include="Foo, Version=1.0">`` -> "foo"
        head = name.split(",", 1)[0].strip()
        if head:
            deps.add(head.lower())
    return deps


def _strip_comment_lines(
    content: str, line_comment_prefixes: tuple[str, ...]
) -> str:
    """Drop everything from a line-comment prefix to end-of-line.

    Best-effort: does not handle the prefix appearing inside string
    literals.  Used for line-oriented manifests (``.cabal``, ``rebar.config``,
    ``deps.edn``, ``project.clj``, ``rockspec``, ``.nimble``, ``build.zig``,
    etc.) where the comment-inside-string case is exceedingly rare.
    """
    out_lines = []
    for line in content.splitlines():
        best = len(line)
        for prefix in line_comment_prefixes:
            idx = line.find(prefix)
            if idx >= 0 and idx < best:
                best = idx
        out_lines.append(line[:best])
    return "\n".join(out_lines)


def _read_manifest_text(path: Path) -> str:
    """Read a manifest file's raw (case-preserved) text; '' on error."""
    try:
        return path.read_text(errors="ignore")
    except (OSError, IOError):  # pragma: no cover - defensive
        return ""


def _collect_parsed_deps(
    repo_root: Path,
    filename: str,
    parser,
    *,
    max_depth: int = 3,
) -> set[str]:
    """Find all ``filename`` manifests and union their parsed dep sets."""
    deps: set[str] = set()
    for path in _find_manifest_files(repo_root, filename, max_depth):
        text = _read_manifest_text(path)
        if text:
            deps |= parser(text)
    return deps


def _pattern_matches_deps(pattern: str, deps: set[str]) -> bool:
    """Check whether a framework pattern matches one of ``deps``.

    Match rules (all lowercase):

    * Exact match: ``pattern in deps``.
    * Coordinate-prefix match: ``pattern:<anything>`` (Maven-style
      ``"org.springframework.boot"`` matches the coordinate
      ``"org.springframework.boot:spring-boot-starter"``).
    * Module-path suffix: ``pattern/<anything>`` (versioned Go modules:
      ``"github.com/labstack/echo"`` matches ``"github.com/labstack/echo/v4"``).
    * Dotted-namespace prefix: ``pattern.<anything>`` (group-ID-style
      ``"androidx.compose"`` matches ``"androidx.compose.ui"``,
      ``"microsoft.aspnetcore"`` matches ``"microsoft.aspnetcore.mvc"``).
    * Hyphenated-package family: ``pattern-<anything>`` (Hex / cabal /
      opam patterns: ``"scotty"`` matches stack's ``"scotty-0.12.1"``,
      ``"cohttp"`` matches OCaml's ``"cohttp-lwt-unix"``).

    The hyphen-prefix mode is *strict prefix* -- pattern ``"transformers"``
    does **not** match ``"sentence-transformers"`` (since that dep starts
    with ``"sentence"``, not ``"transformers"``).  Word-boundary
    substring matching from the legacy ``_manifest_has_package`` is gone
    by design (INV-vunaf).
    """
    p = pattern.lower()
    if p in deps:
        return True
    for dep in deps:
        if ":" not in p and dep.startswith(p + ":"):
            return True
        if "/" in p and dep.startswith(p + "/"):
            return True
        if "." in p and dep.startswith(p + "."):
            return True
        if dep.startswith(p + "-"):
            return True
    return False


def _read_dsl_marker_text(repo_root: Path, filenames: tuple[str, ...]) -> str:
    """Read the lowercased concatenation of selected manifest files.

    Used only for *DSL-marker* fallback patterns -- those containing
    structural tokens like ``{`` or ``+=`` that aren't package names.  The
    primary detection path uses structured parsing; this exists only so
    the rare marker-style patterns keep working.
    """
    parts: list[str] = []
    for filename in filenames:
        parts.append(_read_all_manifest_files(repo_root, filename))
    return "\n".join(parts)


def _is_dsl_marker(pattern: str) -> bool:
    """Identify DSL-marker patterns (not package names).

    Patterns with structural tokens like ``{`` or ``+=`` indicate the
    framework is being detected from a build-script syntax fragment, not
    from a declared dependency name.
    """
    return any(tok in pattern for tok in ("{", "+="))


def _detect_python_frameworks(repo_root: Path) -> list[str]:
    """Detect Python frameworks from dependency files.

    Scans recursively up to 3 levels deep to find manifests in subdirectories
    (e.g., backend/pyproject.toml in monorepos).  Per INV-vunaf, dep names
    are extracted via structured parsing -- *not* substring match on raw
    text -- so comments, pytest marker names, and partial-substring
    collisions (``sentence-transformers`` -> ``transformers``) no longer
    produce false positives.
    """
    detected = []
    deps: set[str] = set()
    deps |= _collect_parsed_deps(repo_root, "pyproject.toml", _parse_pyproject_deps)
    # WI-himas: pip requirements use a layered manifest set (requirements.txt,
    # requirements/*.txt, requirements-*.txt) with -r/-c includes between
    # files. Resolve the full closure instead of matching only the literal
    # "requirements.txt" filename.
    deps |= _collect_pip_requirements_deps(repo_root)
    deps |= _collect_parsed_deps(repo_root, "setup.py", _parse_setup_py_deps)
    deps |= _collect_parsed_deps(repo_root, "Pipfile", _parse_pipfile_deps)

    for framework, patterns in PYTHON_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


def _detect_js_frameworks(repo_root: Path) -> list[str]:
    """Detect JavaScript/TypeScript frameworks from package.json.

    Scans recursively up to 3 levels deep to find manifests in subdirectories
    (e.g., frontend/package.json in monorepos).
    """
    detected = []
    deps: set[str] = set()

    # Find all package.json files recursively
    for package_json in _find_manifest_files(repo_root, "package.json"):
        try:
            content = package_json.read_text(errors="ignore")
            data = json.loads(content)
            # Skip non-dict package.json files (e.g., string or array at top level)
            if not isinstance(data, dict):
                continue
            deps.update(data.get("dependencies", {}).keys())
            deps.update(data.get("devDependencies", {}).keys())
        except (OSError, IOError, json.JSONDecodeError):
            pass

    for framework, patterns in JS_FRAMEWORKS.items():
        for pattern in patterns:
            if pattern in deps:
                detected.append(framework)
                break

    return detected


def _detect_rust_frameworks(repo_root: Path) -> list[str]:
    """Detect Rust frameworks/crates from Cargo.toml.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, crate names are extracted from ``[dependencies]`` /
    ``[dev-dependencies]`` / ``[build-dependencies]`` / target tables via
    TOML parse rather than substring match on raw text.
    """
    detected = []
    deps = _collect_parsed_deps(repo_root, "Cargo.toml", _parse_cargo_toml_deps)

    for framework, patterns in RUST_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


def _detect_go_frameworks(repo_root: Path) -> list[str]:
    """Detect Go frameworks from go.mod.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, module paths are extracted from ``require`` directives
    after stripping ``//`` comments, so commented-out modules can no longer
    trigger framework detection.
    """
    detected = []
    deps = _collect_parsed_deps(repo_root, "go.mod", _parse_go_mod_deps)

    for framework, patterns in GO_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


def _detect_php_frameworks(repo_root: Path) -> list[str]:
    """Detect PHP frameworks from composer.json.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    """
    detected = []
    deps: set[str] = set()

    # Find all composer.json files recursively
    for composer_json in _find_manifest_files(repo_root, "composer.json"):
        try:
            content = composer_json.read_text(errors="ignore")
            data = json.loads(content)
            # Skip non-dict composer.json files
            if not isinstance(data, dict):
                continue
            deps.update(data.get("require", {}).keys())
            deps.update(data.get("require-dev", {}).keys())
        except (OSError, IOError, json.JSONDecodeError):
            pass

    for framework, patterns in PHP_FRAMEWORKS.items():
        for pattern in patterns:
            if pattern in deps:
                detected.append(framework)
                break

    return detected


def _detect_java_frameworks(repo_root: Path) -> list[str]:
    """Detect Java/Kotlin frameworks from pom.xml, build.gradle, or AndroidManifest.xml.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Also scans auxiliary Gradle files in the gradle/ directory (e.g.,
    gradle/dependencies.gradle) used by multi-module Gradle projects like
    Apache Kafka to declare dependencies outside of build.gradle.

    Per INV-vunaf, package-name patterns are matched against dep names
    extracted via XML / Gradle structured parsing.  DSL-marker patterns
    (e.g., ``"android {"``) keep a raw-text fallback because they are
    intentionally build-script syntax fragments rather than package
    names.
    """
    detected: list[str] = []
    detected_set: set[str] = set()

    # Gather structured deps from pom.xml and build.gradle*
    deps: set[str] = set()
    deps |= _collect_parsed_deps(repo_root, "pom.xml", _parse_pom_xml_deps)
    deps |= _collect_parsed_deps(repo_root, "build.gradle", _parse_gradle_deps)
    deps |= _collect_parsed_deps(repo_root, "build.gradle.kts", _parse_gradle_deps)

    # Auxiliary Gradle files under gradle/ (e.g., gradle/dependencies.gradle).
    gradle_dir = repo_root / "gradle"
    if gradle_dir.is_dir():
        for aux_pattern in ("*.gradle", "*.gradle.kts"):
            for aux_file in gradle_dir.glob(aux_pattern):
                text = _read_manifest_text(aux_file)
                if text:
                    deps |= _parse_gradle_deps(text)

    # DSL-marker fallback content: only build-script files (not pom.xml).
    marker_content = _read_dsl_marker_text(
        repo_root, ("build.gradle", "build.gradle.kts")
    )
    if gradle_dir.is_dir():
        aux_parts: list[str] = []
        for aux_pattern in ("*.gradle", "*.gradle.kts"):
            for aux_file in gradle_dir.glob(aux_pattern):
                aux_parts.append(_read_manifest_text(aux_file).lower())
        if aux_parts:
            marker_content = marker_content + "\n" + "\n".join(aux_parts)

    for framework, patterns in JAVA_FRAMEWORKS.items():
        # Note: a single union of deps means we visit each framework once;
        # the historical "skip if already detected" guard from the
        # multi-loop ancestor is no longer needed.
        for pattern in patterns:
            matched = (
                _manifest_has_package(marker_content, pattern)
                if _is_dsl_marker(pattern)
                else _pattern_matches_deps(pattern, deps)
            )
            if matched:
                detected.append(framework)
                detected_set.add(framework)
                break

    # AndroidManifest.xml presence is a definitive Android indicator.
    if "android" not in detected_set:
        manifest_files = list(_find_manifest_files(repo_root, "AndroidManifest.xml"))
        if manifest_files:
            detected.append("android")
            detected_set.add("android")

    return detected


def _detect_swift_frameworks(repo_root: Path) -> list[str]:
    """Detect Swift frameworks from Package.swift.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, dep names are extracted from ``.package(url:)`` /
    ``.package(name:)`` / ``.package(path:)`` declarations after comment
    stripping rather than substring match.
    """
    detected = []
    deps = _collect_parsed_deps(repo_root, "Package.swift", _parse_package_swift_deps)

    for framework, patterns in SWIFT_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


def _detect_scala_frameworks(repo_root: Path) -> list[str]:
    """Detect Scala frameworks from SBT manifests.

    Standard SBT convention splits dependency declarations across two
    locations:

    - Top-level ``build.sbt`` — may contain ``libraryDependencies += ...``
      coordinates directly, OR may reference scala helper objects.
    - ``project/*.scala`` (typically ``project/Dependencies.scala``) —
      where real-world SBT projects keep the actual
      ``"groupId" %% "artifact" % version`` strings.
    - ``project/*.sbt`` (typically ``project/plugins.sbt``) — declares
      SBT plugins like Play's ``sbt-plugin``.

    All three are concatenated and searched, because many real projects
    (e.g. docspell) put every library coordinate in
    ``project/Dependencies.scala`` and a detector that only reads
    ``build.sbt`` would see nothing. WI-piban landed this expansion after
    docspell — which imports org.http4s on hundreds of lines — produced
    an empty ``profile.frameworks``.
    """
    detected = []
    deps: set[str] = set()
    deps |= _collect_parsed_deps(repo_root, "build.sbt", _parse_sbt_deps)

    project_dir = repo_root / "project"
    if project_dir.is_dir():
        for child in project_dir.iterdir():
            if child.is_file() and child.suffix in (".scala", ".sbt"):
                text = _read_manifest_text(child)
                if text:
                    deps |= _parse_sbt_deps(text)

    for framework, patterns in SCALA_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


def _detect_dart_frameworks(repo_root: Path) -> list[str]:
    """Detect Dart/Flutter frameworks from pubspec.yaml.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, dep names are extracted via the indent-aware pubspec
    parser; commented-out entries no longer trigger detection.
    """
    detected = []
    detected_set: set[str] = set()

    flutter_packages = {
        "flutter_bloc": ["flutter_bloc", "bloc"],
        "riverpod": ["flutter_riverpod", "riverpod"],
        "provider": ["provider"],
        "getx": ["get"],
        "mobx": ["flutter_mobx", "mobx"],
        "dio": ["dio"],
        "freezed": ["freezed"],
        "go_router": ["go_router"],
        "flame": ["flame"],
    }

    for pubspec in _find_manifest_files(repo_root, "pubspec.yaml"):
        text = _read_manifest_text(pubspec)
        if not text:
            continue
        deps = _parse_pubspec_yaml_deps(text)
        # Flutter SDK uses ``sdk: flutter`` inside dependencies; signal that
        # by looking for the canonical pair of tokens on non-comment lines.
        non_comment = "\n".join(
            line.split("#", 1)[0] for line in text.splitlines()
        ).lower()
        if "flutter:" in non_comment and "sdk: flutter" in non_comment:
            if "flutter" not in detected_set:
                detected.append("flutter")
                detected_set.add("flutter")
        for framework, patterns in flutter_packages.items():
            if framework in detected_set:
                continue
            for pattern in patterns:
                if pattern in deps:
                    detected.append(framework)
                    detected_set.add(framework)
                    break

    return detected


def _detect_ruby_frameworks(repo_root: Path) -> list[str]:
    """Detect Ruby frameworks from Gemfile.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, gem names are extracted from ``gem '...'`` declarations
    after stripping ``#`` comments.
    """
    detected = []
    deps = _collect_parsed_deps(repo_root, "Gemfile", _parse_gemfile_deps)

    for framework, patterns in RUBY_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


def _detect_elixir_frameworks(repo_root: Path) -> list[str]:
    """Detect Elixir frameworks from mix.exs.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, atom names are extracted from ``{:atom, ...}`` tuples
    after comment stripping rather than substring match.
    """
    detected = []
    deps = _collect_parsed_deps(repo_root, "mix.exs", _parse_mix_exs_deps)

    for framework, patterns in ELIXIR_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


_CABAL_BUILD_DEPENDS_RE = re.compile(
    r"^[ \t]*build-depends\s*:\s*(.*?)(?=^\S|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)


def _parse_cabal_deps(content: str) -> set[str]:
    """Extract package names from a Haskell ``.cabal`` ``build-depends`` field."""
    deps: set[str] = set()
    stripped = _strip_comment_lines(content, ("--",))
    for match in _CABAL_BUILD_DEPENDS_RE.finditer(stripped):
        for raw in match.group(1).split(","):
            name = re.split(r"[\s\(]", raw.strip(), maxsplit=1)[0]
            if name:
                deps.add(name.lower())
    return deps


def _parse_haskell_yaml_deps(content: str) -> set[str]:
    """Extract package names from stack.yaml ``extra-deps`` / package.yaml ``dependencies``.

    Both are YAML; we use a hand-rolled indent-aware parser similar to
    ``_parse_pubspec_yaml_deps``.
    """
    deps: set[str] = set()
    in_section = False
    section_indent = -1
    for raw in content.splitlines():
        idx = raw.find("#")
        line = raw[:idx] if idx >= 0 else raw
        if not line.strip():
            continue
        leading = len(line) - len(line.lstrip())
        stripped_line = line.strip()
        if leading == 0:
            in_section = stripped_line.rstrip(":") in (
                "dependencies",
                "extra-deps",
                "library",
                "executable",
            ) and stripped_line.endswith(":")
            section_indent = 0
            continue
        if in_section and leading > section_indent:
            # Entry forms:
            #   - some-pkg
            #   - some-pkg ==1.2.3
            #   - some-pkg-1.2.3@sha256:...
            entry = stripped_line.lstrip("-").strip()
            if not entry or entry.startswith("#"):
                continue
            match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", entry)
            if match:
                deps.add(match.group(1).lower())
    return deps


def _detect_haskell_frameworks(repo_root: Path) -> list[str]:
    """Detect Haskell frameworks from *.cabal, stack.yaml, or package.yaml.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, dep names are extracted from ``build-depends`` /
    ``extra-deps`` / ``dependencies`` fields after comment stripping.
    """
    detected = []
    deps: set[str] = set()

    for depth in range(4):
        pat = "/".join(["*"] * depth) + "/*.cabal" if depth > 0 else "*.cabal"
        for cabal_file in repo_root.glob(pat):
            text = _read_manifest_text(cabal_file)
            if text:
                deps |= _parse_cabal_deps(text)

    deps |= _collect_parsed_deps(repo_root, "stack.yaml", _parse_haskell_yaml_deps)
    deps |= _collect_parsed_deps(repo_root, "package.yaml", _parse_haskell_yaml_deps)

    for framework, patterns in HASKELL_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


_CLOJURE_DEPS_EDN_KEY_RE = re.compile(
    r"([A-Za-z0-9._/-]+)\s*\{[^{}]*?(?:mvn/version|:mvn/version|sha)",
    re.IGNORECASE,
)
_CLOJURE_PROJECT_CLJ_RE = re.compile(
    r"\[\s*([A-Za-z0-9._/-]+)\s+\"[^\"]+\"",
)


def _parse_clojure_deps_edn(content: str) -> set[str]:
    """Extract dep coordinates from a Clojure ``deps.edn`` file.

    deps.edn maps each lib (e.g., ``org.clojure/clojure``) to a map with
    ``:mvn/version`` / ``:local/root`` / ``:git/url`` etc.  Strip ``;``
    comments first.
    """
    deps: set[str] = set()
    stripped = _strip_comment_lines(content, (";",))
    for coord in _CLOJURE_DEPS_EDN_KEY_RE.findall(stripped):
        coord_lc = coord.lower()
        deps.add(coord_lc)
        if "/" in coord_lc:
            head, tail = coord_lc.split("/", 1)
            deps.add(head)
            deps.add(tail)
    return deps


def _parse_clojure_project_clj(content: str) -> set[str]:
    """Extract dep coordinates from a Leiningen ``project.clj`` file."""
    deps: set[str] = set()
    stripped = _strip_comment_lines(content, (";",))
    for coord in _CLOJURE_PROJECT_CLJ_RE.findall(stripped):
        coord_lc = coord.lower()
        deps.add(coord_lc)
        if "/" in coord_lc:
            head, tail = coord_lc.split("/", 1)
            deps.add(head)
            deps.add(tail)
    return deps


def _detect_clojure_frameworks(repo_root: Path) -> list[str]:
    """Detect Clojure frameworks from deps.edn or project.clj.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, dep coordinates are extracted via best-effort parsers
    after stripping ``;`` line comments.
    """
    detected = []
    deps: set[str] = set()
    deps |= _collect_parsed_deps(repo_root, "deps.edn", _parse_clojure_deps_edn)
    deps |= _collect_parsed_deps(repo_root, "project.clj", _parse_clojure_project_clj)

    for framework, patterns in CLOJURE_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


def _detect_r_frameworks(repo_root: Path) -> list[str]:
    """Detect R frameworks from DESCRIPTION file.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, package names are extracted from the Imports / Depends /
    LinkingTo / Suggests / Enhances fields after stripping ``#`` comments.
    """
    detected = []
    deps = _collect_parsed_deps(repo_root, "DESCRIPTION", _parse_description_deps)

    for framework, patterns in R_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


_ROCKSPEC_DEP_RE = re.compile(r"['\"]([A-Za-z0-9._-]+)(?:\s*[<>=~^]+\s*[\w.]+)?['\"]")


def _parse_rockspec_deps(content: str) -> set[str]:
    """Extract dep names from a LuaRocks ``*.rockspec`` file.

    Strips ``--`` line and ``--[[ ]]--`` block comments, then scans the
    ``dependencies`` table for quoted package names.
    """
    deps: set[str] = set()
    # Strip block comments.
    stripped = re.sub(r"--\[\[.*?\]\]--?", "", content, flags=re.DOTALL)
    stripped = _strip_comment_lines(stripped, ("--",))
    # Scope to the dependencies table.
    for match in re.finditer(
        r"dependencies\s*=\s*\{([^}]*)\}", stripped, re.DOTALL
    ):
        for spec in _ROCKSPEC_DEP_RE.findall(match.group(1)):
            deps.add(spec.lower())
    return deps


def _detect_lua_frameworks(repo_root: Path) -> list[str]:
    """Detect Lua frameworks from *.rockspec files or special markers.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, rockspec dep names are extracted from the
    ``dependencies`` table; the OpenResty marker (``resty`` / ``ngx`` in
    nginx.conf) remains a content-style heuristic.
    """
    detected = []
    deps: set[str] = set()
    for depth in range(4):
        pat = "/".join(["*"] * depth) + "/*.rockspec" if depth > 0 else "*.rockspec"
        for rockspec_file in repo_root.glob(pat):
            text = _read_manifest_text(rockspec_file)
            if text:
                deps |= _parse_rockspec_deps(text)

    # OpenResty markers (``resty``, ``ngx``, etc.) appear in nginx.conf and
    # aren't package names; keep content-style detection for those.
    nginx_content = _read_all_manifest_files(repo_root, "nginx.conf")

    for framework, patterns in LUA_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break
            if nginx_content and _manifest_has_package(nginx_content, pattern):
                detected.append(framework)
                break

    return detected


_CMAKE_FIND_PACKAGE_RE = re.compile(
    r"find_package\s*\(\s*([A-Za-z0-9_]+)", re.IGNORECASE
)


def _parse_cmake_deps(content: str) -> set[str]:
    """Extract package names from CMakeLists.txt ``find_package`` calls.

    Strips ``#`` line comments first.
    """
    deps: set[str] = set()
    stripped = _strip_comment_lines(content, ("#",))
    for name in _CMAKE_FIND_PACKAGE_RE.findall(stripped):
        deps.add(name.lower())
    return deps


_QMAKE_QT_MODULES_RE = re.compile(r"^\s*QT\s*\+?=\s*(.+)$", re.MULTILINE)


def _parse_qmake_deps(content: str) -> set[str]:
    """Extract Qt module names from a qmake ``.pro`` file ``QT +=`` line."""
    deps: set[str] = set()
    stripped = _strip_comment_lines(content, ("#",))
    for tail in _QMAKE_QT_MODULES_RE.findall(stripped):
        for token in tail.split():
            tok = token.strip()
            if tok:
                deps.add("qt" + tok.lower())
                deps.add(tok.lower())
    return deps


def _parse_vcpkg_deps(content: str) -> set[str]:
    """Extract dep names from a vcpkg.json manifest."""
    deps: set[str] = set()
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return deps
    if not isinstance(data, dict):
        return deps
    for entry in data.get("dependencies") or []:
        if isinstance(entry, str):
            deps.add(entry.lower())
        elif isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str):
                deps.add(name.lower())
    return deps


def _detect_cpp_frameworks(repo_root: Path) -> list[str]:
    """Detect C++ frameworks from CMakeLists.txt, *.pro, or vcpkg.json.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, Qt is detected via structured parsing of
    ``find_package(Qt*)`` calls, qmake ``QT += <modules>`` lines, and
    vcpkg.json dep entries.  DSL-marker patterns (``qmake``, ``qt +=``)
    keep a content-style fallback because they aren't package names.
    """
    detected = []
    deps: set[str] = set()
    deps |= _collect_parsed_deps(repo_root, "CMakeLists.txt", _parse_cmake_deps)
    for depth in range(4):
        pat = "/".join(["*"] * depth) + "/*.pro" if depth > 0 else "*.pro"
        for pro_file in repo_root.glob(pat):
            text = _read_manifest_text(pro_file)
            if text:
                deps |= _parse_qmake_deps(text)
    deps |= _collect_parsed_deps(repo_root, "vcpkg.json", _parse_vcpkg_deps)

    marker_content = _read_all_manifest_files(repo_root, "CMakeLists.txt")
    for depth in range(4):
        pat = "/".join(["*"] * depth) + "/*.pro" if depth > 0 else "*.pro"
        for pro_file in repo_root.glob(pat):
            text = _read_manifest_text(pro_file)
            if text:
                marker_content = marker_content + "\n" + text.lower()

    for framework, patterns in CPP_FRAMEWORKS.items():
        for pattern in patterns:
            matched = (
                _manifest_has_package(marker_content, pattern)
                if _is_dsl_marker_or_special(pattern)
                else _pattern_matches_deps(pattern, deps)
            )
            if matched:
                detected.append(framework)
                break

    return detected


def _is_dsl_marker_or_special(pattern: str) -> bool:
    """C++ patterns that aren't package names (qmake DSL fragments, etc.)."""
    if _is_dsl_marker(pattern):
        return True
    return pattern.lower() in ("qmake", "qt +=", "qt+=")


_REBAR_DEP_ATOM_RE = re.compile(r"\{\s*([a-z][a-zA-Z0-9_]*)\s*,")
_ERLANGMK_DEP_RE = re.compile(r"^\s*DEPS\s*[+:?]?=\s*(.+)$", re.MULTILINE)


def _parse_rebar_config_deps(content: str) -> set[str]:
    """Extract dep atom names from a rebar.config ``{deps, [...]}`` term.

    Strips ``%`` line comments first.
    """
    deps: set[str] = set()
    stripped = _strip_comment_lines(content, ("%",))
    for match in re.finditer(r"\{\s*deps\s*,\s*\[([^\]]*)\]", stripped, re.DOTALL):
        for atom in _REBAR_DEP_ATOM_RE.findall(match.group(1)):
            deps.add(atom.lower())
    return deps


def _parse_erlangmk_deps(content: str) -> set[str]:
    """Extract dep names from an erlang.mk Makefile ``DEPS = ...`` line."""
    deps: set[str] = set()
    stripped = _strip_comment_lines(content, ("#",))
    for tail in _ERLANGMK_DEP_RE.findall(stripped):
        for token in tail.split():
            tok = token.strip()
            if tok:
                deps.add(tok.lower())
    return deps


def _detect_erlang_frameworks(repo_root: Path) -> list[str]:
    """Detect Erlang frameworks from rebar.config or erlang.mk.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, dep atom names are extracted from the ``{deps, [...]}``
    term in rebar.config and the ``DEPS = ...`` lines in erlang.mk after
    stripping comments.
    """
    detected = []
    deps: set[str] = set()
    deps |= _collect_parsed_deps(repo_root, "rebar.config", _parse_rebar_config_deps)
    deps |= _collect_parsed_deps(repo_root, "erlang.mk", _parse_erlangmk_deps)

    for framework, patterns in ERLANG_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


def _detect_fsharp_frameworks(repo_root: Path) -> list[str]:
    """Detect F# frameworks from *.fsproj files.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, NuGet package names are extracted from
    ``<PackageReference Include="...">`` elements after XML-comment
    stripping.
    """
    detected = []
    deps: set[str] = set()
    for depth in range(4):
        pat = "/".join(["*"] * depth) + "/*.fsproj" if depth > 0 else "*.fsproj"
        for fsproj_file in repo_root.glob(pat):
            text = _read_manifest_text(fsproj_file)
            if text:
                deps |= _parse_msbuild_proj_deps(text)

    for framework, patterns in FSHARP_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


def _detect_kotlin_frameworks(repo_root: Path) -> list[str]:
    """Detect Kotlin-specific frameworks from build.gradle.kts or build.gradle.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Note: Java frameworks (Spring, etc.) are detected by _detect_java_frameworks.
    This function detects Kotlin-specific frameworks like Ktor.

    Per INV-vunaf, dep coordinates are extracted via structured Gradle
    parsing rather than substring match.
    """
    detected = []
    deps: set[str] = set()
    deps |= _collect_parsed_deps(repo_root, "build.gradle.kts", _parse_gradle_deps)
    deps |= _collect_parsed_deps(repo_root, "build.gradle", _parse_gradle_deps)

    for framework, patterns in KOTLIN_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


def _detect_csharp_frameworks(repo_root: Path) -> list[str]:
    """Detect C# frameworks from *.csproj files.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    C# projects use .csproj (MSBuild) with PackageReference elements.
    Per INV-vunaf, package names come from those elements after XML-comment
    stripping.
    """
    detected = []
    deps: set[str] = set()
    for depth in range(4):
        pat = "/".join(["*"] * depth) + "/*.csproj" if depth > 0 else "*.csproj"
        for csproj_file in repo_root.glob(pat):
            text = _read_manifest_text(csproj_file)
            if text:
                deps |= _parse_msbuild_proj_deps(text)

    for framework, patterns in CSHARP_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


def _detect_dart_web_frameworks(repo_root: Path) -> list[str]:
    """Detect Dart web frameworks (non-Flutter) from pubspec.yaml.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Note: Flutter is detected separately in _detect_dart_frameworks.

    Per INV-vunaf, dep names come from the indent-aware pubspec parser; the
    legacy ``"<name>:"`` pattern values now match by stripping the trailing
    ``:`` and looking up exact dep keys.
    """
    detected = []
    deps = _collect_parsed_deps(repo_root, "pubspec.yaml", _parse_pubspec_yaml_deps)

    for framework, patterns in DART_FRAMEWORKS.items():
        for pattern in patterns:
            normalized = pattern.rstrip(":").lower()
            if normalized and normalized in deps:
                detected.append(framework)
                break

    return detected


def _detect_julia_frameworks(repo_root: Path) -> list[str]:
    """Detect Julia frameworks from Project.toml.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, dep names come from the ``[deps]`` section via TOML
    parsing rather than substring match on raw text.
    """
    detected = []
    deps = _collect_parsed_deps(repo_root, "Project.toml", _parse_project_toml_deps)

    for framework, patterns in JULIA_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


_DUNE_DEPENDS_RE = re.compile(r"\(depends\s+([^)]+)\)", re.DOTALL)
_OPAM_DEPENDS_RE = re.compile(r"depends\s*:\s*\[([^\]]*)\]", re.DOTALL)


def _parse_dune_project_deps(content: str) -> set[str]:
    """Extract OCaml dep names from a ``dune-project`` file ``depends`` form."""
    deps: set[str] = set()
    stripped = _strip_comment_lines(content, (";",))
    for body in _DUNE_DEPENDS_RE.findall(stripped):
        for token in body.split():
            token = token.strip().strip("()").strip()
            if token and not token.startswith((":", "(")):
                deps.add(token.lower())
    return deps


def _parse_opam_deps(content: str) -> set[str]:
    """Extract OCaml dep names from an opam ``depends:`` field."""
    deps: set[str] = set()
    stripped = _strip_comment_lines(content, ("#",))
    for body in _OPAM_DEPENDS_RE.findall(stripped):
        for match in _QUOTED_STRING_RE.findall(body):
            name = re.split(r"[\s\{]", match, maxsplit=1)[0]
            if name:
                deps.add(name.lower())
    return deps


def _detect_ocaml_frameworks(repo_root: Path) -> list[str]:
    """Detect OCaml frameworks from dune-project or *.opam files.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, dep names are extracted from the ``(depends ...)`` form
    in dune-project and the ``depends: [ ... ]`` field in .opam files
    after stripping comments.
    """
    detected = []
    deps = _collect_parsed_deps(repo_root, "dune-project", _parse_dune_project_deps)
    for depth in range(4):
        pat = "/".join(["*"] * depth) + "/*.opam" if depth > 0 else "*.opam"
        for opam_file in repo_root.glob(pat):
            text = _read_manifest_text(opam_file)
            if text:
                deps |= _parse_opam_deps(text)

    for framework, patterns in OCAML_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


_NIMBLE_REQUIRES_RE = re.compile(
    r"requires\s+['\"]([A-Za-z0-9_][A-Za-z0-9_-]*)", re.IGNORECASE
)


def _parse_nimble_deps(content: str) -> set[str]:
    """Extract Nim dep names from ``.nimble`` ``requires`` declarations."""
    deps: set[str] = set()
    stripped = _strip_comment_lines(content, ("#",))
    for name in _NIMBLE_REQUIRES_RE.findall(stripped):
        deps.add(name.lower())
    return deps


def _detect_nim_frameworks(repo_root: Path) -> list[str]:
    """Detect Nim frameworks from *.nimble files.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, dep names are extracted from ``requires "..."`` lines
    after stripping ``#`` comments.
    """
    detected = []
    deps: set[str] = set()
    for depth in range(4):
        pat = "/".join(["*"] * depth) + "/*.nimble" if depth > 0 else "*.nimble"
        for nimble_file in repo_root.glob(pat):
            text = _read_manifest_text(nimble_file)
            if text:
                deps |= _parse_nimble_deps(text)

    for framework, patterns in NIM_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


_ZIG_ZON_DEPS_RE = re.compile(r"\.dependencies\s*=\s*\.\{(.*?)\}", re.DOTALL)
_ZIG_ZON_KEY_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\.\{")


def _parse_zig_zon_deps(content: str) -> set[str]:
    """Extract dep names from a Zig ``build.zig.zon`` ``.dependencies`` block.

    Strips ``//`` line comments first.
    """
    deps: set[str] = set()
    stripped = _strip_comment_lines(content, ("//",))
    for body in _ZIG_ZON_DEPS_RE.findall(stripped):
        for name in _ZIG_ZON_KEY_RE.findall(body):
            deps.add(name.lower())
    return deps


def _parse_zig_build_deps(content: str) -> set[str]:
    """Extract dep names referenced via ``b.dependency("name", ...)`` calls."""
    deps: set[str] = set()
    stripped = _strip_comment_lines(content, ("//",))
    for name in re.findall(
        r"\bdependency\s*\(\s*['\"]([A-Za-z_][A-Za-z0-9_-]*)", stripped
    ):
        deps.add(name.lower())
    return deps


def _detect_zig_frameworks(repo_root: Path) -> list[str]:
    """Detect Zig frameworks from build.zig.zon or build.zig.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, dep names are extracted from the ``.dependencies`` block
    in build.zig.zon and ``b.dependency("...")`` calls in build.zig after
    stripping ``//`` comments.
    """
    detected = []
    deps: set[str] = set()
    deps |= _collect_parsed_deps(repo_root, "build.zig.zon", _parse_zig_zon_deps)
    deps |= _collect_parsed_deps(repo_root, "build.zig", _parse_zig_build_deps)

    for framework, patterns in ZIG_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


def _parse_dub_json_deps(content: str) -> set[str]:
    """Extract dep names from a dub.json file's ``dependencies`` map."""
    deps: set[str] = set()
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return deps
    if not isinstance(data, dict):
        return deps
    section = data.get("dependencies")
    if isinstance(section, dict):
        for name in section:
            if isinstance(name, str):
                deps.add(name.lower())
    return deps


_DUB_SDL_DEP_RE = re.compile(
    r"^\s*dependency\s+['\"]([A-Za-z0-9_][A-Za-z0-9._-]*)['\"]",
    re.MULTILINE | re.IGNORECASE,
)


def _parse_dub_sdl_deps(content: str) -> set[str]:
    """Extract dep names from a ``dub.sdl`` ``dependency "name"`` line."""
    deps: set[str] = set()
    stripped = _strip_comment_lines(content, ("//",))
    for name in _DUB_SDL_DEP_RE.findall(stripped):
        deps.add(name.lower())
    return deps


def _detect_d_frameworks(repo_root: Path) -> list[str]:
    """Detect D frameworks from dub.json or dub.sdl.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Per INV-vunaf, dep names come from JSON ``dependencies`` keys (dub.json)
    or ``dependency "name"`` declarations (dub.sdl) after comment stripping.
    """
    detected = []
    deps: set[str] = set()
    deps |= _collect_parsed_deps(repo_root, "dub.json", _parse_dub_json_deps)
    deps |= _collect_parsed_deps(repo_root, "dub.sdl", _parse_dub_sdl_deps)

    for framework, patterns in D_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


def _detect_groovy_frameworks(repo_root: Path) -> list[str]:
    """Detect Groovy frameworks from build.gradle.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Groovy frameworks like Grails and Ratpack use Gradle for builds.
    Per INV-vunaf, dep coordinates are extracted via the Gradle parser.
    """
    detected = []
    deps = _collect_parsed_deps(repo_root, "build.gradle", _parse_gradle_deps)

    for framework, patterns in GROOVY_FRAMEWORKS.items():
        for pattern in patterns:
            if _pattern_matches_deps(pattern, deps):
                detected.append(framework)
                break

    return detected


def _detect_protobuf(repo_root: Path) -> list[str]:
    """Detect protobuf/gRPC by the presence of .proto files.

    This is language-agnostic: any repo with .proto service definitions
    gets the ``protobuf`` framework tag, which activates the gRPC linker.
    The gRPC linker creates route symbols for proto RPC methods.

    Uses ``find_files`` with a limit of 1 for efficiency — we only need
    to know if at least one .proto file exists.
    """
    from .discovery import find_files

    for _ in find_files(repo_root, ["*.proto"]):
        return ["protobuf"]
    return []


def _detect_solidity_frameworks(repo_root: Path) -> list[str]:
    """Detect Solidity frameworks from config files.

    Unlike other language frameworks which are detected from dependency files,
    Solidity frameworks (Foundry, Hardhat) are detected by the presence of
    their configuration files.

    Scans recursively up to 3 levels deep to find config files in subdirectories.
    """
    detected = []

    for framework, config_files in SOLIDITY_FRAMEWORKS.items():
        for config_file in config_files:
            if _find_manifest_files(repo_root, config_file):
                detected.append(framework)
                break  # Found this framework, check next

    return detected


def _detect_frameworks(repo_root: Path) -> list[str]:
    """Detect frameworks in the repository by scanning dependency files.

    This is used for AUTO mode only. EXPLICIT and ALL modes bypass this
    function and use frameworks directly without dependency scanning.

    Args:
        repo_root: Path to the repository root.

    Returns:
        List of detected framework names.
    """
    frameworks: list[str] = []
    frameworks.extend(_detect_python_frameworks(repo_root))
    frameworks.extend(_detect_js_frameworks(repo_root))
    frameworks.extend(_detect_rust_frameworks(repo_root))
    frameworks.extend(_detect_go_frameworks(repo_root))
    frameworks.extend(_detect_php_frameworks(repo_root))
    frameworks.extend(_detect_java_frameworks(repo_root))
    frameworks.extend(_detect_swift_frameworks(repo_root))
    frameworks.extend(_detect_scala_frameworks(repo_root))
    frameworks.extend(_detect_dart_frameworks(repo_root))
    frameworks.extend(_detect_solidity_frameworks(repo_root))
    frameworks.extend(_detect_ruby_frameworks(repo_root))
    frameworks.extend(_detect_elixir_frameworks(repo_root))
    frameworks.extend(_detect_haskell_frameworks(repo_root))
    frameworks.extend(_detect_clojure_frameworks(repo_root))
    frameworks.extend(_detect_r_frameworks(repo_root))
    frameworks.extend(_detect_lua_frameworks(repo_root))
    frameworks.extend(_detect_cpp_frameworks(repo_root))
    frameworks.extend(_detect_erlang_frameworks(repo_root))
    frameworks.extend(_detect_fsharp_frameworks(repo_root))
    frameworks.extend(_detect_kotlin_frameworks(repo_root))
    frameworks.extend(_detect_csharp_frameworks(repo_root))
    frameworks.extend(_detect_dart_web_frameworks(repo_root))
    frameworks.extend(_detect_julia_frameworks(repo_root))
    frameworks.extend(_detect_ocaml_frameworks(repo_root))
    frameworks.extend(_detect_nim_frameworks(repo_root))
    frameworks.extend(_detect_zig_frameworks(repo_root))
    frameworks.extend(_detect_d_frameworks(repo_root))
    frameworks.extend(_detect_groovy_frameworks(repo_root))
    frameworks.extend(_detect_protobuf(repo_root))
    return frameworks


def _import_modules_for_framework(framework: str) -> set[str]:
    """Return the set of import module names that correspond to a framework.

    For each manifest pattern in the framework's *_FRAMEWORKS dict entry,
    applies IMPORT_OVERRIDES to translate PyPI/npm names that differ from
    their actual import name, then lowercases for matching.

    Args:
        framework: Framework name (e.g., "pytorch", "flask").

    Returns:
        Set of lowercased import module names.
    """
    modules: set[str] = set()
    for _lang, fw_dict in LANGUAGE_FRAMEWORKS.items():
        if framework in fw_dict:
            for pattern in fw_dict[framework]:
                canonical = IMPORT_OVERRIDES.get(pattern, pattern)
                modules.add(canonical.lower())
    return modules


def _framework_languages(framework: str) -> set[str]:
    """Return the set of languages a framework belongs to.

    Uses LANGUAGE_FRAMEWORKS to map framework name → {language, ...}.

    Args:
        framework: Framework name (e.g., "spring-boot", "flask").

    Returns:
        Set of language identifiers (e.g., {"python"}, {"java", "kotlin"}).
    """
    langs: set[str] = set()
    for lang, fw_dict in LANGUAGE_FRAMEWORKS.items():
        if framework in fw_dict:
            langs.add(lang)
    return langs


def _has_prod_import_match(
    patterns: Iterable[str],
    module_importers: dict[str, list[str]],
    is_test_fn: Callable[[str], bool],
    *,
    require_prefix_arm: bool = False,
    module_languages: dict[str, set[str]] | None = None,
    allowed_langs: set[str] | None = None,
) -> bool:
    """Return True if any (module, importer) pair satisfies all of:

    - ``imported`` module matches at least one of ``patterns`` (per
      ``_module_match_kind``),
    - at least one importer of that module is a non-empty path that
      ``is_test_fn`` says is not a test file,
    - if ``allowed_langs`` is set: the matched module's language
      appears in that set (consulted via ``module_languages``).

    When ``require_prefix_arm`` is True, the matching arm must be
    ``"prefix"`` (compound submodule like ``django.db`` or
    ``rails/generators``) — exact matches against the pattern are
    rejected. This is the WI-pusad bare-name gate: bare-pattern
    promotion requires evidence of a compound submodule import,
    distinguishing real framework use (``from django.db import X``)
    from generic single-name imports (``import graphql`` for typedefs).

    The ``allowed_langs`` gate prevents cross-ecosystem FPs: e.g., the
    Julia ``http`` framework has bare pattern ``http``, and Python's
    ``http.client`` stdlib import would otherwise match it via the
    prefix arm. Surfaced by WI-pusad on the django source bakeoff.

    Used by both the promote and demote phases of
    ``refine_frameworks``. The promote phase calls with
    ``require_prefix_arm=False`` for the WI-palol specific-pattern arm
    and ``require_prefix_arm=True`` for the WI-pusad bare-pattern arm,
    both with ``allowed_langs`` set to the framework's languages. The
    demote phase always uses ``require_prefix_arm=False`` and
    ``allowed_langs=None`` (a framework with any prod import stays
    confirmed, exact or prefix, regardless of language — the coarser
    ``fw_langs & import_edge_langs`` check upstream of the call already
    short-circuits the demote path for language-mismatched frameworks).

    Args:
        patterns: Lowercased import-module patterns to match against.
        module_importers: Mapping of lowercased imported-module name to
            the list of source file paths that imported it.
        is_test_fn: Callable returning True when a path is a test file.
        require_prefix_arm: When True, only ``"prefix"``-kind matches
            count toward promotion. Default False preserves the
            WI-palol / pre-WI-pusad behavior.
        module_languages: Mapping of lowercased imported-module name to
            the set of languages whose edges referenced it. Required
            when ``allowed_langs`` is provided.
        allowed_langs: When set, restricts matching to modules whose
            language appears in this set. Used by the promote phase to
            prevent cross-ecosystem FPs.

    Returns:
        True if a prod-non-test importer of a pattern-matching module
        exists under the active gate.
    """
    for module_key, importers in module_importers.items():
        if allowed_langs is not None and module_languages is not None:
            mod_langs = module_languages.get(module_key, set())
            if not mod_langs & allowed_langs:
                continue
        match_kind = None
        for pat in patterns:
            kind = _module_match_kind(module_key, pat)
            if kind is not None:
                # Prefer the strongest arm seen so far: prefix beats exact.
                if match_kind != "prefix":
                    match_kind = kind
                if match_kind == "prefix":
                    break
        if match_kind is None:
            continue
        if require_prefix_arm and match_kind != "prefix":
            continue
        if any(p and not is_test_fn(p) for p in importers):
            return True
    return False


def _is_specific_pattern(pattern: str) -> bool:
    """Return True if an import-module pattern is "specific enough" to
    promote a framework from import edges alone (no manifest evidence).

    A pattern is specific when it is scoped (``@scope/name``),
    slash-compound (``github.com/owner/repo``), or dot-compound
    (``org.springframework.boot``). Bare single-token names (``react``,
    ``flask``, ``rails``, ``tokio``) do not qualify — their import
    surface is too generic to safely promote without manifest backing,
    per the lesson of WI-rofiz (bare ``graphql`` triggered FPs from
    repos using ``graphql`` only for type definitions or codegen).

    The gate is consulted only by the promote phase of
    ``refine_frameworks``; the demote phase ignores it. Bare-name
    frameworks therefore continue to flow through manifest detection
    unchanged (Cargo.toml for Rust, requirements.txt + pyproject.toml
    for Python, Gemfile for Ruby, package.json for npm bare names).

    Args:
        pattern: Lowercased import-module pattern, as produced by
            ``_import_modules_for_framework``.

    Returns:
        True if the pattern is structurally specific.
    """
    return pattern.startswith("@") or "/" in pattern or "." in pattern


def _module_match_kind(imported: str, pattern: str) -> str | None:
    """Classify how an imported module matches a framework pattern.

    Three possible outcomes:

    - ``"exact"`` — ``imported`` equals ``pattern`` (case-insensitive).
      Example: imported = ``"django"``, pattern = ``"django"``.
    - ``"prefix"`` — ``imported`` starts with the pattern followed by
      a separator (``.`` for Python / Maven coords, ``/`` for npm
      scoped paths and Go paths). Example: imported = ``"django.db"``,
      pattern = ``"django"``; imported = ``"@apollo/server/standalone"``,
      pattern = ``"@apollo/server"``.
    - ``None`` — no match.

    Consumed by ``_has_prod_import_match`` for the WI-pusad
    compound-import-required gate (bare-name promotion requires a
    ``"prefix"`` match; ``"exact"`` alone is too weak — see WI-rofiz's
    lesson on bare ``graphql`` triggering FPs from typedef installs).

    Args:
        imported: The imported module name (2nd colon-field of the edge dst).
        pattern: The framework import module name (lowercased).

    Returns:
        ``"exact"``, ``"prefix"``, or ``None``.
    """
    imported_lower = imported.lower()
    if imported_lower == pattern:
        return "exact"
    if imported_lower.startswith(pattern + ".") or imported_lower.startswith(pattern + "/"):
        return "prefix"
    return None


def refine_frameworks(
    profile: "RepoProfile",
    edges: list,
    symbols: list,
) -> "RepoProfile":
    """Validate and supplement detected frameworks using import edges.

    Two-phase pipeline (WI-palol / INV-rojip):

    1. **Promote** — for every framework F that is *not* already in
       ``profile.frameworks`` or ``profile.dev_frameworks``: if some
       prod-non-test source file imports a module matching one of F's
       registered import-module patterns *and* that pattern passes the
       specificity gate (``_is_specific_pattern``), promote F to the
       working framework list. The specificity gate prevents bare-name
       FPs (``react`` imported by build tooling, ``graphql`` imported
       for type definitions) while letting through scoped npm packages
       (``@apollo/server``), Go full paths (``github.com/.../gin``),
       and Maven coords (``org.springframework.boot``). This phase
       resolves the workspace-import surface of INV-rojip (WI-donud's
       motivating case: apollo-server smoke-test consumer).

    2. **Demote** — for every framework in the working list, check
       import edges in its languages. Frameworks with no prod-non-test
       imports (or only test imports) move to ``dev_frameworks``.
       Members of ``_AUTOLOAD_BY_CONVENTION_FRAMEWORKS`` skip this
       step (WI-lohok: Rails autoload).

    Only applies in AUTO mode — explicit/all/none modes are returned
    unchanged because the user specified the frameworks intentionally.

    For languages whose analyzer emits no ``imports`` edges, frameworks
    are kept as confirmed to avoid false negatives (the ``fw_langs &
    import_edge_langs`` guard in the demote loop). Java *does* now emit
    import edges (INV-gojit), so Java/Kotlin frameworks participate in
    the prod-vs-dev demotion like every other import-emitting language —
    their import-module patterns are matched against the full Java import
    specifier (``import org.springframework.boot.X`` prefix-matches the
    ``org.springframework.boot`` pattern).

    Args:
        profile: The repo profile with candidate frameworks from manifest
            scanning.
        edges: All edges from the analysis (Symbol-level IR Edge objects).
        symbols: All symbols from the analysis (used to extract source
            file paths for test classification).

    Returns:
        A new RepoProfile with ``frameworks`` (production-confirmed +
        import-promoted) and ``dev_frameworks`` (dev/test-only)
        populated.
    """
    from .paths import is_test_file

    if profile.framework_mode != "auto":
        return profile

    # Build symbol ID → file path lookup for source classification.
    sym_path: dict[str, str] = {s.id: s.path for s in symbols}

    # Collect import edges and track which languages have them.
    # Each entry: (source_file_path, imported_module, language_of_import)
    import_edge_langs: set[str] = set()
    # Map: lowercased_module → list of source file paths
    module_importers: dict[str, list[str]] = {}
    # WI-pusad / INV-rojip cohort-2 FP: same import path-string can match
    # framework patterns from different language ecosystems (e.g. Python
    # ``http.client`` would match the Julia ``http`` framework's bare
    # pattern via the prefix arm without this gate). Track per-module
    # languages so the promote phase only fires when an import's
    # language matches one of the framework's registered languages.
    module_languages: dict[str, set[str]] = {}

    for edge in edges:
        if edge.edge_type != "imports":
            continue
        dst = edge.dst
        parts = dst.split(":")
        if len(parts) < 2:
            continue  # pragma: no cover
        lang = parts[0]
        imported_module = parts[1]
        import_edge_langs.add(lang)

        # Resolve source file path from the symbol table, falling back
        # to extracting it from the edge src ID.
        src_path = sym_path.get(edge.src, "")
        if not src_path and ":" in edge.src:
            src_path = edge.src.split(":")[1] if len(edge.src.split(":")) > 1 else ""

        module_key = imported_module.lower()
        module_importers.setdefault(module_key, []).append(src_path)
        module_languages.setdefault(module_key, set()).add(lang)

    # === PROMOTE PHASE (WI-palol + WI-pusad / INV-rojip) ===
    # Find frameworks reached only by import edges (manifest silent) and
    # add them to the working framework list. The demote phase below
    # then runs over the combined list. Two arms:
    #
    # - WI-palol specific-pattern arm: scoped (@apollo/server),
    #   slash-compound (github.com/.../gin), or dot-compound
    #   (org.springframework.boot) patterns promote on any prod-non-test
    #   importer (exact or prefix match).
    # - WI-pusad bare-name arm: bare patterns (django, flask, rails,
    #   react) promote ONLY when at least one prod-non-test importer
    #   matched via the prefix arm — i.e., a compound submodule like
    #   `django.db` or `react/jsx-runtime`. Exact bare imports alone
    #   (just `import graphql`) are too weak a signal (WI-rofiz lesson).
    existing: set[str] = set(profile.frameworks) | set(profile.dev_frameworks)
    promoted: list[str] = []
    for _lang, fw_dict in LANGUAGE_FRAMEWORKS.items():
        for fw in fw_dict:
            if fw in existing or fw in promoted:
                continue
            patterns = _import_modules_for_framework(fw)
            if not patterns:  # pragma: no cover  -- every framework in LANGUAGE_FRAMEWORKS has at least one pattern
                continue
            fw_langs = _framework_languages(fw)
            specific_patterns = [p for p in patterns if _is_specific_pattern(p)]
            if specific_patterns and _has_prod_import_match(
                specific_patterns,
                module_importers,
                is_test_file,
                module_languages=module_languages,
                allowed_langs=fw_langs,
            ):
                promoted.append(fw)
                continue
            bare_patterns = [p for p in patterns if not _is_specific_pattern(p)]
            if bare_patterns and _has_prod_import_match(
                bare_patterns,
                module_importers,
                is_test_file,
                # WI-tosul Phase 2: allowlisted route frameworks may promote on a
                # bare EXACT import (the dead-code monoculture root); all others
                # keep the WI-pusad compound-submodule prefix requirement.
                require_prefix_arm=(fw not in _BARE_EXACT_PROMOTE_ROUTE_FRAMEWORKS),
                module_languages=module_languages,
                allowed_langs=fw_langs,
            ):
                promoted.append(fw)

    working_frameworks: list[str] = list(profile.frameworks) + promoted

    confirmed: list[str] = []
    dev_only: list[str] = list(profile.dev_frameworks)

    for fw in working_frameworks:
        # WI-lohok: some frameworks are loaded by convention (Bundler /
        # mix / etc.), not by an explicit production-code import. The
        # import-edge demotion check would incorrectly demote them to
        # dev_frameworks even on real production apps. The downstream
        # cost is severe: enrich_symbols only loads framework patterns
        # for profile.frameworks (not dev_frameworks), so a demoted
        # framework's YAML never applies, starving the concept-tag
        # linkers of inputs (observed on chatwoot / cohort-001/iter-001
        # — Rails detected from Gemfile then demoted, rails.yaml never
        # loaded, 0 controller/route/form/serializer concept hits).
        if fw in _AUTOLOAD_BY_CONVENTION_FRAMEWORKS:
            confirmed.append(fw)
            continue

        fw_langs = _framework_languages(fw)

        # Fallback: if none of this framework's languages produced import
        # edges, we can't validate — keep as confirmed.
        if not fw_langs & import_edge_langs:
            confirmed.append(fw)
            continue

        if _has_prod_import_match(
            _import_modules_for_framework(fw), module_importers, is_test_file
        ):
            confirmed.append(fw)
        else:
            dev_only.append(fw)

    return RepoProfile(
        languages=profile.languages,
        frameworks=confirmed,
        dev_frameworks=dev_only,
        framework_mode=profile.framework_mode,
        requested_frameworks=profile.requested_frameworks,
    )


def detect_profile(
    repo_root: Path,
    extra_excludes: list[str] | None = None,
    frameworks: str | None = None,
    count_loc: bool = False,
) -> RepoProfile:
    """Detect the profile of a repository.

    Args:
        repo_root: Path to the repository root.
        extra_excludes: Additional exclude patterns beyond DEFAULT_EXCLUDES.
        frameworks: Framework specification (ADR-3aaa):
            - None: Auto-detect (default)
            - "none": Skip framework detection
            - "all": Check all frameworks for detected languages
            - "fastapi,celery": Only check specified frameworks
        count_loc: If True, compute LOC per language (reads all source files).

    Returns a RepoProfile with detected languages and frameworks.
    """
    languages = _detect_languages(
        repo_root, extra_excludes=extra_excludes, count_loc=count_loc,
    )
    detected_languages = set(languages.keys())

    # Resolve framework specification
    framework_spec = resolve_frameworks(frameworks, detected_languages)

    if framework_spec.mode == FrameworkMode.NONE:
        # Skip framework detection
        detected_frameworks: list[str] = []
    elif framework_spec.mode == FrameworkMode.ALL:
        # Use ALL known frameworks for detected languages (don't scan dependency files)
        # This enables pattern matching even when frameworks aren't in dependency manifests
        detected_frameworks = list(framework_spec.frameworks)
    elif framework_spec.mode == FrameworkMode.EXPLICIT:
        # User explicitly requested these frameworks - trust them, don't scan dependency files
        # This enables pattern matching even when frameworks aren't in manifest files
        detected_frameworks = list(framework_spec.requested)
    else:
        # AUTO: Detect frameworks from dependency files
        detected_frameworks = _detect_frameworks(repo_root)

    return RepoProfile(
        languages=languages,
        frameworks=detected_frameworks,
        framework_mode=framework_spec.mode.value,
        requested_frameworks=framework_spec.requested,
    )
