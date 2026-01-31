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

Framework Specification (ADR-0003)
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
    "graphql": ["graphql", "@apollo/server", "graphql-yoga", "mercurius"],
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
    "spring-boot": ["spring-boot", "org.springframework.boot"],
    "micronaut": ["micronaut", "io.micronaut"],
    "quarkus": ["quarkus", "io.quarkus"],
    "dropwizard": ["dropwizard", "io.dropwizard"],
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
}

# Swift Package.swift detection patterns
SWIFT_FRAMEWORKS = {
    "vapor": ["vapor"],
    "kitura": ["kitura"],
    "perfect": ["perfectlySoft"],
    "swiftui": ["swiftui"],  # Detected via imports, not SPM
}

# Scala (build.sbt) detection patterns
SCALA_FRAMEWORKS = {
    "play": ["com.typesafe.play", "playframework"],
    "akka-http": ["akka-http", "com.typesafe.akka"],
    "http4s": ["http4s", "org.http4s"],
    "zio-http": ["zio-http", "dev.zio"],
    "finatra": ["finatra", "com.twitter"],
}

# Ruby gem detection patterns (from Gemfile)
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

# Map languages to their framework dictionaries
LANGUAGE_FRAMEWORKS: dict[str, dict[str, list[str]]] = {
    "python": PYTHON_FRAMEWORKS,
    "javascript": JS_FRAMEWORKS,
    "typescript": JS_FRAMEWORKS,  # TypeScript uses same frameworks as JS
    "rust": RUST_FRAMEWORKS,
    "go": GO_FRAMEWORKS,
    "php": PHP_FRAMEWORKS,
    "java": JAVA_FRAMEWORKS,
    "kotlin": JAVA_FRAMEWORKS,  # Kotlin uses same frameworks as Java
    "swift": SWIFT_FRAMEWORKS,
    "scala": SCALA_FRAMEWORKS,
    "solidity": SOLIDITY_FRAMEWORKS,
    "ruby": RUBY_FRAMEWORKS,
    "elixir": ELIXIR_FRAMEWORKS,
}


class FrameworkMode(Enum):
    """Mode for framework detection (ADR-0003).

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
    """Specification for which frameworks to check (ADR-0003).

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
    """Statistics for a detected language."""

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
    framework_mode: str = "auto"  # none, all, explicit, auto
    requested_frameworks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        result = {
            "languages": {k: v.to_dict() for k, v in self.languages.items()},
            "frameworks": sorted(self.frameworks),
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
            framework_mode=d.get("framework_mode", "auto"),
            requested_frameworks=d.get("requested_frameworks", []),
        )


def _count_loc(file_path: Path, max_file_size: int | None = None) -> int:
    """Count non-empty lines in a file.

    Args:
        file_path: Path to the file.
        max_file_size: If set, skip files larger than this (bytes).
            Used by catalog command for quick heuristic scanning.
    """
    try:
        if max_file_size is not None and file_path.stat().st_size > max_file_size:
            return 0
        content = file_path.read_text(errors="ignore")
        return sum(1 for line in content.splitlines() if line.strip())
    except (OSError, IOError):
        return 0


def _detect_languages(
    repo_root: Path,
    extra_excludes: list[str] | None = None,
    max_file_size: int | None = None,
) -> dict[str, LanguageStats]:
    """Detect languages by scanning file extensions.

    Args:
        repo_root: Path to the repository root.
        extra_excludes: Additional exclude patterns beyond DEFAULT_EXCLUDES.
        max_file_size: If set, skip files larger than this when counting LOC.
            Used by catalog command for quick heuristic scanning.
    """
    languages: dict[str, LanguageStats] = {}

    # Combine default and extra excludes
    from .discovery import DEFAULT_EXCLUDES
    excludes = list(DEFAULT_EXCLUDES)
    if extra_excludes:
        excludes.extend(extra_excludes)

    for lang, patterns in LANGUAGE_EXTENSIONS.items():
        # Use a set to deduplicate files (e.g., *.ts and *.d.ts both match foo.d.ts)
        files = set(find_files(repo_root, patterns, excludes=excludes))
        if files:
            stats = LanguageStats(files=len(files))
            for f in files:
                stats.loc += _count_loc(f, max_file_size=max_file_size)
            languages[lang] = stats

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


def _detect_python_frameworks(repo_root: Path) -> list[str]:
    """Detect Python frameworks from dependency files.

    Scans recursively up to 3 levels deep to find manifests in subdirectories
    (e.g., backend/pyproject.toml in monorepos).
    """
    detected = []

    # Check pyproject.toml, requirements.txt, setup.py, Pipfile - recursively
    content = ""
    content += _read_all_manifest_files(repo_root, "pyproject.toml")
    content += _read_all_manifest_files(repo_root, "requirements.txt")
    content += _read_all_manifest_files(repo_root, "setup.py")
    content += _read_all_manifest_files(repo_root, "Pipfile")

    for framework, patterns in PYTHON_FRAMEWORKS.items():
        for pattern in patterns:
            if pattern.lower() in content:
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
    """
    detected = []

    # Concatenate all Cargo.toml files
    content = _read_all_manifest_files(repo_root, "Cargo.toml")

    for framework, patterns in RUST_FRAMEWORKS.items():
        for pattern in patterns:
            # Check for crate in dependencies section
            if pattern.lower() in content:
                detected.append(framework)
                break

    return detected


def _detect_go_frameworks(repo_root: Path) -> list[str]:
    """Detect Go frameworks from go.mod.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    """
    detected = []

    # Concatenate all go.mod files
    content = _read_all_manifest_files(repo_root, "go.mod")

    for framework, patterns in GO_FRAMEWORKS.items():
        for pattern in patterns:
            if pattern.lower() in content:
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
    """
    detected: list[str] = []
    detected_set: set[str] = set()

    # Check pom.xml (Maven) - recursively
    content = _read_all_manifest_files(repo_root, "pom.xml")
    for framework, patterns in JAVA_FRAMEWORKS.items():
        for pattern in patterns:
            if pattern.lower() in content:
                if framework not in detected_set:
                    detected.append(framework)
                    detected_set.add(framework)
                break

    # Check build.gradle (Gradle) - recursively
    for gradle_file in ["build.gradle", "build.gradle.kts"]:
        content = _read_all_manifest_files(repo_root, gradle_file)
        for framework, patterns in JAVA_FRAMEWORKS.items():
            if framework not in detected_set:
                for pattern in patterns:
                    if pattern.lower() in content:
                        detected.append(framework)
                        detected_set.add(framework)
                        break

    # Check for AndroidManifest.xml (definitive Android indicator)
    # If any AndroidManifest.xml exists, this is an Android project
    if "android" not in detected_set:
        manifest_files = list(_find_manifest_files(repo_root, "AndroidManifest.xml"))
        if manifest_files:
            detected.append("android")
            detected_set.add("android")

    return detected


def _detect_swift_frameworks(repo_root: Path) -> list[str]:
    """Detect Swift frameworks from Package.swift.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    """
    detected = []

    # Concatenate all Package.swift files
    content = _read_all_manifest_files(repo_root, "Package.swift")

    for framework, patterns in SWIFT_FRAMEWORKS.items():
        for pattern in patterns:
            if pattern.lower() in content:
                detected.append(framework)
                break

    return detected


def _detect_scala_frameworks(repo_root: Path) -> list[str]:
    """Detect Scala frameworks from build.sbt.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    """
    detected = []

    # Concatenate all build.sbt files
    content = _read_all_manifest_files(repo_root, "build.sbt")

    for framework, patterns in SCALA_FRAMEWORKS.items():
        for pattern in patterns:
            if pattern.lower() in content:
                detected.append(framework)
                break

    return detected


def _detect_dart_frameworks(repo_root: Path) -> list[str]:
    """Detect Dart/Flutter frameworks from pubspec.yaml.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    """
    detected = []
    detected_set: set[str] = set()

    # Find all pubspec.yaml files recursively
    for pubspec in _find_manifest_files(repo_root, "pubspec.yaml"):
        try:
            content = pubspec.read_text(errors="ignore").lower()
            # Check for Flutter SDK
            if "flutter:" in content and "sdk: flutter" in content:
                if "flutter" not in detected_set:
                    detected.append("flutter")
                    detected_set.add("flutter")

            # Check for common Flutter packages
            flutter_packages = {
                "flutter_bloc": ["flutter_bloc", "bloc"],
                "riverpod": ["flutter_riverpod", "riverpod"],
                "provider": ["provider"],
                "getx": ["get:"],
                "mobx": ["flutter_mobx", "mobx"],
                "dio": ["dio:"],
                "freezed": ["freezed"],
                "go_router": ["go_router"],
                "flame": ["flame:"],
            }
            for framework, patterns in flutter_packages.items():
                if framework not in detected_set:
                    for pattern in patterns:
                        if pattern in content:
                            detected.append(framework)
                            detected_set.add(framework)
                            break
        except (OSError, IOError):  # pragma: no cover
            pass

    return detected


def _detect_ruby_frameworks(repo_root: Path) -> list[str]:
    """Detect Ruby frameworks from Gemfile.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    """
    detected = []

    # Concatenate all Gemfile files
    content = _read_all_manifest_files(repo_root, "Gemfile")

    for framework, patterns in RUBY_FRAMEWORKS.items():
        for pattern in patterns:
            if pattern.lower() in content:
                detected.append(framework)
                break

    return detected


def _detect_elixir_frameworks(repo_root: Path) -> list[str]:
    """Detect Elixir frameworks from mix.exs.

    Scans recursively up to 3 levels deep to find manifests in subdirectories.
    Uses word boundary matching to avoid false positives (e.g., "nex" in "next").
    """
    detected = []

    # Concatenate all mix.exs files
    content = _read_all_manifest_files(repo_root, "mix.exs")

    for framework, patterns in ELIXIR_FRAMEWORKS.items():
        for pattern in patterns:
            # Use regex for word boundary matching to avoid substring false positives
            # Match :pattern, "pattern", or 'pattern' (Elixir atom/string syntax)
            import re

            # Pattern matches :nex, {:nex, or "nex" but not "next"
            regex = rf'[:"\']{re.escape(pattern)}["\',\s\}}]'
            if re.search(regex, content, re.IGNORECASE):
                detected.append(framework)
                break

    return detected


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
    return frameworks


def detect_profile(
    repo_root: Path,
    extra_excludes: list[str] | None = None,
    frameworks: str | None = None,
    max_file_size: int | None = None,
) -> RepoProfile:
    """Detect the profile of a repository.

    Args:
        repo_root: Path to the repository root.
        extra_excludes: Additional exclude patterns beyond DEFAULT_EXCLUDES.
        frameworks: Framework specification (ADR-0003):
            - None: Auto-detect (default)
            - "none": Skip framework detection
            - "all": Check all frameworks for detected languages
            - "fastapi,celery": Only check specified frameworks
        max_file_size: If set, skip files larger than this when counting LOC.
            Used by catalog command for quick heuristic scanning.

    Returns a RepoProfile with detected languages and frameworks.
    """
    languages = _detect_languages(
        repo_root, extra_excludes=extra_excludes, max_file_size=max_file_size
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
