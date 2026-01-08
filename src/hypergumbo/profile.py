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

# Language extensions mapping
LANGUAGE_EXTENSIONS: dict[str, list[str]] = {
    "python": ["*.py", "*.pyi"],
    "javascript": ["*.js", "*.mjs", "*.cjs", "*.jsx"],
    "typescript": ["*.ts", "*.tsx", "*.d.ts"],
    "vue": ["*.vue"],
    "html": ["*.html", "*.htm"],
    "css": ["*.css", "*.scss", "*.sass", "*.less"],
    "json": ["*.json"],
    "yaml": ["*.yaml", "*.yml"],
    "markdown": ["*.md", "*.markdown"],
    "rust": ["*.rs"],
    "go": ["*.go"],
    "java": ["*.java"],
    "c": ["*.c", "*.h"],
    "cpp": ["*.cpp", "*.cc", "*.cxx", "*.hpp", "*.hxx"],
    "ruby": ["*.rb"],
    "php": ["*.php"],
    "swift": ["*.swift"],
    "kotlin": ["*.kt", "*.kts"],
    "shell": ["*.sh", "*.bash", "*.zsh"],
    "scala": ["*.scala", "*.sc"],
    "elixir": ["*.ex", "*.exs"],
    "lua": ["*.lua"],
    "clojure": ["*.clj", "*.cljs", "*.cljc", "*.edn"],
    "erlang": ["*.erl", "*.hrl"],
    "elm": ["*.elm"],
    "haskell": ["*.hs", "*.lhs"],
    "agda": ["*.agda", "*.lagda", "*.lagda.md"],
    "lean": ["*.lean"],
    "wolfram": ["*.wl", "*.wls", "*.nb"],
    "ocaml": ["*.ml", "*.mli"],
    "solidity": ["*.sol"],
    "csharp": ["*.cs"],
    # New languages added for extended analyzer support
    "fortran": ["*.f", "*.f90", "*.f95", "*.f03", "*.f08", "*.F", "*.F90"],
    "glsl": ["*.glsl", "*.vert", "*.frag", "*.geom", "*.comp", "*.tesc", "*.tese"],
    "nix": ["*.nix"],
    "cuda": ["*.cu", "*.cuh"],
    "cmake": ["CMakeLists.txt", "*.cmake"],
    "dockerfile": ["Dockerfile", "Dockerfile.*", "*.dockerfile"],
    "sql": ["*.sql"],
    "verilog": ["*.v", "*.sv", "*.svh"],
    "vhdl": ["*.vhd", "*.vhdl"],
    "graphql": ["*.graphql", "*.gql"],
    "zig": ["*.zig"],
    "groovy": ["*.groovy", "*.gradle"],
    "julia": ["*.jl"],
    "objc": ["*.m", "*.mm"],
    "hcl": ["*.tf", "*.hcl"],
    "dart": ["*.dart"],
    "cobol": ["*.cob", "*.cbl", "*.cobol", "*.cpy"],
    "latex": ["*.tex", "*.sty", "*.cls"],
    "fsharp": ["*.fs", "*.fsi", "*.fsx"],
    "perl": ["*.pl", "*.pm", "*.t"],
    "proto": ["*.proto"],
    "thrift": ["*.thrift"],
    "capnp": ["*.capnp"],
    "powershell": ["*.ps1", "*.psm1", "*.psd1"],
    "gdscript": ["*.gd"],
    "starlark": ["BUILD", "BUILD.bazel", "BUCK", "*.bzl"],
    "fish": ["*.fish"],
    "hlsl": ["*.hlsl", "*.hlsli", "*.fx"],
    "ada": ["*.ads", "*.adb", "*.ada"],
    "d": ["*.d", "*.di"],
    "nim": ["*.nim", "*.nims", "*.nimble"],
}

# Framework detection patterns
# Maps framework name -> (file to check, pattern to look for)
PYTHON_FRAMEWORKS = {
    # Web frameworks
    "fastapi": ["fastapi"],
    "flask": ["flask", "Flask"],
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
    # Kotlin-specific
    "ktor": ["ktor", "io.ktor"],
    # Android
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


def _count_loc(file_path: Path) -> int:
    """Count non-empty lines in a file."""
    try:
        content = file_path.read_text(errors="ignore")
        return sum(1 for line in content.splitlines() if line.strip())
    except (OSError, IOError):
        return 0


def _detect_languages(
    repo_root: Path, extra_excludes: list[str] | None = None
) -> dict[str, LanguageStats]:
    """Detect languages by scanning file extensions.

    Args:
        repo_root: Path to the repository root.
        extra_excludes: Additional exclude patterns beyond DEFAULT_EXCLUDES.
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
                stats.loc += _count_loc(f)
            languages[lang] = stats

    return languages


def _read_dependency_file(repo_root: Path, filename: str) -> str:
    """Read a dependency file if it exists."""
    path = repo_root / filename
    if path.exists():
        try:
            return path.read_text(errors="ignore").lower()
        except (OSError, IOError):
            pass
    return ""


def _detect_python_frameworks(repo_root: Path) -> list[str]:
    """Detect Python frameworks from dependency files."""
    detected = []

    # Check pyproject.toml, requirements.txt, setup.py
    content = ""
    content += _read_dependency_file(repo_root, "pyproject.toml")
    content += _read_dependency_file(repo_root, "requirements.txt")
    content += _read_dependency_file(repo_root, "setup.py")
    content += _read_dependency_file(repo_root, "Pipfile")

    for framework, patterns in PYTHON_FRAMEWORKS.items():
        for pattern in patterns:
            if pattern.lower() in content:
                detected.append(framework)
                break

    return detected


def _detect_js_frameworks(repo_root: Path) -> list[str]:
    """Detect JavaScript/TypeScript frameworks from package.json."""
    detected = []

    package_json = repo_root / "package.json"
    if package_json.exists():
        try:
            content = package_json.read_text(errors="ignore")
            data = json.loads(content)
            deps = set()
            deps.update(data.get("dependencies", {}).keys())
            deps.update(data.get("devDependencies", {}).keys())

            for framework, patterns in JS_FRAMEWORKS.items():
                for pattern in patterns:
                    if pattern in deps:
                        detected.append(framework)
                        break
        except (OSError, IOError, json.JSONDecodeError):
            pass

    return detected


def _detect_rust_frameworks(repo_root: Path) -> list[str]:
    """Detect Rust frameworks/crates from Cargo.toml."""
    detected = []

    cargo_toml = repo_root / "Cargo.toml"
    if cargo_toml.exists():
        try:
            content = cargo_toml.read_text(errors="ignore").lower()

            for framework, patterns in RUST_FRAMEWORKS.items():
                for pattern in patterns:
                    # Check for crate in dependencies section
                    if pattern.lower() in content:
                        detected.append(framework)
                        break
        except (OSError, IOError):
            pass

    return detected


def _detect_go_frameworks(repo_root: Path) -> list[str]:
    """Detect Go frameworks from go.mod."""
    detected = []

    go_mod = repo_root / "go.mod"
    if go_mod.exists():
        try:
            content = go_mod.read_text(errors="ignore").lower()

            for framework, patterns in GO_FRAMEWORKS.items():
                for pattern in patterns:
                    if pattern.lower() in content:
                        detected.append(framework)
                        break
        except (OSError, IOError):  # pragma: no cover
            pass

    return detected


def _detect_php_frameworks(repo_root: Path) -> list[str]:
    """Detect PHP frameworks from composer.json."""
    detected = []

    composer_json = repo_root / "composer.json"
    if composer_json.exists():
        try:
            content = composer_json.read_text(errors="ignore")
            data = json.loads(content)
            deps = set()
            deps.update(data.get("require", {}).keys())
            deps.update(data.get("require-dev", {}).keys())

            for framework, patterns in PHP_FRAMEWORKS.items():
                for pattern in patterns:
                    if pattern in deps:
                        detected.append(framework)
                        break
        except (OSError, IOError, json.JSONDecodeError):  # pragma: no cover
            pass

    return detected


def _detect_java_frameworks(repo_root: Path) -> list[str]:
    """Detect Java/Kotlin frameworks from pom.xml or build.gradle."""
    detected = []

    # Check pom.xml (Maven)
    pom_xml = repo_root / "pom.xml"
    if pom_xml.exists():
        try:
            content = pom_xml.read_text(errors="ignore").lower()
            for framework, patterns in JAVA_FRAMEWORKS.items():
                for pattern in patterns:
                    if pattern.lower() in content:
                        detected.append(framework)
                        break
        except (OSError, IOError):  # pragma: no cover
            pass

    # Check build.gradle (Gradle)
    for gradle_file in ["build.gradle", "build.gradle.kts"]:
        gradle_path = repo_root / gradle_file
        if gradle_path.exists():
            try:
                content = gradle_path.read_text(errors="ignore").lower()
                for framework, patterns in JAVA_FRAMEWORKS.items():
                    if framework not in detected:
                        for pattern in patterns:
                            if pattern.lower() in content:
                                detected.append(framework)
                                break
            except (OSError, IOError):  # pragma: no cover
                pass

    return detected


def _detect_swift_frameworks(repo_root: Path) -> list[str]:
    """Detect Swift frameworks from Package.swift."""
    detected = []

    package_swift = repo_root / "Package.swift"
    if package_swift.exists():
        try:
            content = package_swift.read_text(errors="ignore").lower()
            for framework, patterns in SWIFT_FRAMEWORKS.items():
                for pattern in patterns:
                    if pattern.lower() in content:
                        detected.append(framework)
                        break
        except (OSError, IOError):  # pragma: no cover
            pass

    return detected


def _detect_scala_frameworks(repo_root: Path) -> list[str]:
    """Detect Scala frameworks from build.sbt."""
    detected = []

    build_sbt = repo_root / "build.sbt"
    if build_sbt.exists():
        try:
            content = build_sbt.read_text(errors="ignore").lower()
            for framework, patterns in SCALA_FRAMEWORKS.items():
                for pattern in patterns:
                    if pattern.lower() in content:
                        detected.append(framework)
                        break
        except (OSError, IOError):  # pragma: no cover
            pass

    return detected


def _detect_dart_frameworks(repo_root: Path) -> list[str]:
    """Detect Dart/Flutter frameworks from pubspec.yaml."""
    detected = []

    pubspec = repo_root / "pubspec.yaml"
    if pubspec.exists():
        try:
            content = pubspec.read_text(errors="ignore").lower()
            # Check for Flutter SDK
            if "flutter:" in content and "sdk: flutter" in content:
                detected.append("flutter")

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
                for pattern in patterns:
                    if pattern in content:
                        detected.append(framework)
                        break
        except (OSError, IOError):  # pragma: no cover
            pass

    return detected


def _detect_frameworks(
    repo_root: Path,
    allowed_frameworks: set[str] | None = None,
) -> list[str]:
    """Detect frameworks in the repository.

    Args:
        repo_root: Path to the repository root.
        allowed_frameworks: If provided, only check for these frameworks.
            If None, check all known frameworks.

    Returns:
        List of detected framework names.
    """
    frameworks = []

    # Collect all detections
    all_detected = []
    all_detected.extend(_detect_python_frameworks(repo_root))
    all_detected.extend(_detect_js_frameworks(repo_root))
    all_detected.extend(_detect_rust_frameworks(repo_root))
    all_detected.extend(_detect_go_frameworks(repo_root))
    all_detected.extend(_detect_php_frameworks(repo_root))
    all_detected.extend(_detect_java_frameworks(repo_root))
    all_detected.extend(_detect_swift_frameworks(repo_root))
    all_detected.extend(_detect_scala_frameworks(repo_root))
    all_detected.extend(_detect_dart_frameworks(repo_root))

    # Filter by allowed frameworks if specified
    if allowed_frameworks is not None:
        frameworks = [f for f in all_detected if f in allowed_frameworks]
    else:
        frameworks = all_detected

    return frameworks


def detect_profile(
    repo_root: Path,
    extra_excludes: list[str] | None = None,
    frameworks: str | None = None,
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

    Returns a RepoProfile with detected languages and frameworks.
    """
    languages = _detect_languages(repo_root, extra_excludes=extra_excludes)
    detected_languages = set(languages.keys())

    # Resolve framework specification
    framework_spec = resolve_frameworks(frameworks, detected_languages)

    if framework_spec.mode == FrameworkMode.NONE:
        # Skip framework detection
        detected_frameworks: list[str] = []
    else:
        # Detect frameworks (filtered by allowed set if specified)
        allowed = framework_spec.frameworks if framework_spec.frameworks else None
        detected_frameworks = _detect_frameworks(repo_root, allowed_frameworks=allowed)

    return RepoProfile(
        languages=languages,
        frameworks=detected_frameworks,
        framework_mode=framework_spec.mode.value,
        requested_frameworks=framework_spec.requested,
    )
