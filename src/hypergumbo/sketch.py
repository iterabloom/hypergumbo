"""Token-budgeted Markdown sketch generation.

This module generates human/LLM-readable Markdown summaries of repositories,
optimized for pasting into LLM chat interfaces. Output is token-budgeted
to fill the available context.

How It Works
------------
The sketch is generated progressively to fill the token budget:
1. Header: repo name, language breakdown, LOC estimate (always included)
2. Structure: top-level directory overview
3. Frameworks: detected build systems and dependencies
4. Source files: files in source directories (expands to fill budget)
5. All files: complete file listing (for very large budgets)

Token budgeting uses a simple heuristic (~4 chars per token) which is
accurate enough for approximate sizing. For precise counting, tiktoken
can be used as an optional dependency.

Why Progressive Expansion
-------------------------
Rather than truncating, we progressively add content until approaching
the token budget. This ensures the output uses available context space
effectively while remaining coherent.
"""
from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import List, Optional

from .discovery import find_files, is_excluded, DEFAULT_EXCLUDES
from .profile import detect_profile, RepoProfile
from .ir import Symbol
from .entrypoints import detect_entrypoints, Entrypoint
from .ranking import (
    compute_centrality,
    apply_tier_weights,
    compute_file_scores,
    _is_test_path,
)
from .selection.language_proportional import (
    allocate_language_budget as _allocate_language_budget,
    group_files_by_language as _group_files_by_language,
)
from .selection.token_budget import (
    estimate_tokens,
    truncate_to_tokens,
)


class ConfigExtractionMode(Enum):
    """Mode for extracting config file content.

    - HEURISTIC: Extract known fields using pattern matching (fast, no model)
    - EMBEDDING: Use semantic similarity to prototype questions (requires model)
    - HYBRID: Extract known fields first, then use embeddings for remaining budget
    """

    HEURISTIC = "heuristic"
    EMBEDDING = "embedding"
    HYBRID = "hybrid"


# Probe system for embedding-based config extraction:
# 1. ANSWER_PATTERNS: Example config lines that contain factual metadata
# 2. BIG_PICTURE_QUESTIONS: Open-ended questions for architectural context
#
# Similarity is computed as top-k mean across all probes (k=3). This requires
# multiple probes to "agree" on relevance, reducing sensitivity to spurious
# single-probe matches while preserving signal for underrepresented languages.

# Conceptual answer patterns - what config metadata IS (not syntax examples)
# The embedding model generalizes these concepts across language syntaxes.
ANSWER_PATTERNS = [
    # Project identity
    "project name declaration",
    "package name",
    "module name",
    "application name",

    # Versioning
    "version number",
    "semantic version",
    "edition or language version",
    "minimum required version",

    # Dependencies
    "dependency declaration",
    "package dependency",
    "library dependency",
    "dev dependency",
    "build dependency",
    "optional dependency",

    # Licensing
    "license identifier",
    "SPDX license expression",
    "open source license",

    # Build configuration
    "build system configuration",
    "build target",
    "compilation settings",
    "entry point",
    "main module",
    "script definition",
    "command definition",

    # Runtime configuration
    "environment variable",
    "configuration option",
    "feature flag",
    "runtime setting",

    # Repository and authorship
    "repository URL",
    "homepage URL",
    "author name",
    "maintainer",
    "contributors list",

    # Documentation
    "project description",
    "readme file",

    # Discovery
    "package keywords",
    "package categories",
    "package tags",

    # Exports and binaries
    "binary executable",
    "library exports",
    "public API",
]

# Open-ended questions for big-picture/architectural context
# NOTE: License questions removed - ANSWER_PATTERNS already captures compact
# license declarations (e.g., 'license = "MIT"') without matching verbose
# LICENSE file boilerplate.
BIG_PICTURE_QUESTIONS = [
    # Machine learning and AI
    "What ML framework does this use?",
    "Does this use PyTorch?",
    "Does this use TensorFlow?",
    "Does this use JAX?",
    "Does this use scikit-learn?",
    "Does this use Hugging Face Transformers?",
    "What model architecture does this implement?",
    "Does this support GPU acceleration?",
    "Does this support TPU?",
    "Does this use CUDA?",
    "What quantization methods are supported?",
    "Does this use ONNX?",
    "What inference runtime does this use?",

    # Version and release info
    "What version is this project?",
    "What is the current version number?",
    "When was the last release?",
    "What version of Node.js does this require?",
    "What Python version is required?",
    "What is the minimum supported version?",

    # Database and storage
    "What database does this project use?",
    "Does this use PostgreSQL?",
    "Does this use MySQL?",
    "Does this use MongoDB?",
    "Does this use Redis?",
    "Does this use SQLite?",
    "What ORM does this use?",
    "How does this store data?",

    # Web frameworks and HTTP
    "What web framework does this use?",
    "Is this built with Express?",
    "Is this built with FastAPI?",
    "Is this built with Django?",
    "Is this built with Flask?",
    "Is this built with Rails?",
    "Is this built with Spring?",
    "Is this a REST API?",
    "Does this use GraphQL?",

    # Frontend frameworks
    "What frontend framework does this use?",
    "Is this built with React?",
    "Is this built with Vue?",
    "Is this built with Angular?",
    "Is this built with Svelte?",
    "Does this use TypeScript?",
    "What CSS framework does this use?",

    # Testing
    "What testing framework does this use?",
    "Does this use Jest?",
    "Does this use pytest?",
    "Does this use JUnit?",
    "How do I run the tests?",
    "What is the test coverage?",

    # Build and tooling
    "What build system does this use?",
    "Does this use webpack?",
    "Does this use Vite?",
    "Does this use Maven?",
    "Does this use Gradle?",
    "Does this use Cargo?",
    "How do I build this project?",

    # Package management
    "What package manager does this use?",
    "Does this use npm or yarn?",
    "Does this use pnpm?",
    "Does this use pip?",
    "What are the main dependencies?",
    "What are the dev dependencies?",

    # Language and runtime
    "What programming language is this?",
    "What runtime does this require?",
    "Is this a TypeScript project?",
    "Is this a Python project?",
    "Is this a Go project?",
    "Is this a Rust project?",
    "Is this a Java project?",

    # Project identity
    "What is this project called?",
    "What is the project name?",
    "Who maintains this project?",
    "What organization owns this?",
    "Who are the contributors?",

    # Deployment and infrastructure
    "How do I deploy this?",
    "Does this use Docker?",
    "Does this use Kubernetes?",
    "What cloud platform does this target?",
    "Is this serverless?",
    "Does this run on AWS?",
    "Does this run on GCP?",
    "Does this run on Azure?",
    "Does this use Terraform?",
    "Does this use Helm?",
    "What container registry does this use?",
    "Does this use GitHub Actions?",
    "Does this use GitLab CI?",
    "What infrastructure as code tool is used?",

    # API and protocols
    "What API does this expose?",
    "Does this use WebSockets?",
    "Does this use gRPC?",
    "What ports does this use?",

    # Miscellaneous metadata
    "What is the project description?",
    "What problem does this solve?",
    "Is this a library or application?",
    "Is this a CLI tool?",
    "Is this production ready?",

    # Architecture and design (harder, open-ended)
    "What is the overall architecture of this project?",
    "How is the codebase organized?",
    "What design patterns does this use?",
    "How do the components communicate?",
    "What is the data flow through the system?",
    "How does authentication work?",
    "How does authorization work?",
    "What are the main modules or services?",
    "Is this a monolith or microservices?",
    "How is state managed?",

    # Scale and complexity
    "How large is this codebase?",
    "How many services does this have?",
    "What are the performance characteristics?",
    "How does this handle concurrency?",
    "What are the scaling considerations?",

    # Integration and external systems
    "What external services does this integrate with?",
    "What third-party APIs does this call?",
    "How does this communicate with other systems?",
    "What message queues or event buses are used?",
    "What caching strategy is used?",

    # Security and reliability
    "How are secrets managed?",
    "What security measures are in place?",
    "How are errors handled?",
    "What logging and monitoring is used?",
    "How is configuration managed across environments?",

    # Development workflow
    "How do I set up the development environment?",
    "What are the contribution guidelines?",
    "How is code review done?",
    "What CI/CD pipeline is used?",
    "How are database migrations handled?",
]


# Config files to extract project metadata from
# Config files grouped by language/ecosystem for targeted discovery
CONFIG_FILES_BY_LANG: dict[str, list[str]] = {
    # JavaScript/TypeScript ecosystem
    "javascript": ["package.json"],
    "typescript": ["package.json", "tsconfig.json"],
    # Go
    "go": ["go.mod", "go.sum"],
    # Java/JVM ecosystem
    "java": ["pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"],
    "kotlin": ["build.gradle.kts", "settings.gradle.kts", "pom.xml"],
    "scala": ["build.sbt", "build.gradle"],
    "groovy": ["build.gradle", "settings.gradle"],
    # Rust
    "rust": ["Cargo.toml", "Cargo.lock"],
    # Python
    "python": ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"],
    # PHP
    "php": ["composer.json", "composer.lock"],
    # Ruby
    "ruby": ["Gemfile", "Gemfile.lock", ".ruby-version"],
    # Elixir/Erlang
    "elixir": ["mix.exs", "mix.lock"],
    "erlang": ["rebar.config"],
    # Haskell
    "haskell": ["package.yaml", "stack.yaml", "cabal.project"],
    # Swift/Objective-C
    "swift": ["Package.swift"],
    # .NET/C#/F#
    "csharp": ["*.csproj", "Directory.Build.props", "*.sln"],
    "fsharp": ["*.fsproj", "Directory.Build.props"],
    # C/C++
    "c": ["CMakeLists.txt", "Makefile", "configure.ac", "meson.build", "conanfile.txt"],
    "cpp": ["CMakeLists.txt", "Makefile", "configure.ac", "meson.build", "conanfile.txt"],
    # OCaml
    "ocaml": ["dune-project", "dune"],
    # Clojure
    "clojure": ["deps.edn", "project.clj"],
    # Zig
    "zig": ["build.zig"],
    # Nim
    "nim": ["*.nimble"],
    # Dart/Flutter
    "dart": ["pubspec.yaml"],
    # Julia
    "julia": ["Project.toml", "Manifest.toml"],
    # Nix
    "nix": ["flake.nix", "flake.lock", "default.nix", "shell.nix"],
    # Elm
    "elm": ["elm.json"],
    # PureScript
    "purescript": ["spago.dhall", "packages.dhall"],
    # Crystal
    "crystal": ["shard.yml", "shard.lock"],
    # Lua
    "lua": ["*.rockspec", ".luacheckrc"],
    # R
    "r": ["DESCRIPTION", "renv.lock", "NAMESPACE"],
    # Perl
    "perl": ["cpanfile", "Makefile.PL", "Build.PL", "META.json"],
    # HCL/Terraform
    "hcl": ["*.tf", "terraform.tfvars", "*.tfvars"],
    # Common/fallback
    "_common": ["Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml"],
}

# Flatten for backwards compatibility
CONFIG_FILES = list({
    f for files in CONFIG_FILES_BY_LANG.values() for f in files
})

# Subdirectories to check for config files (monorepo support)
CONFIG_SUBDIRS = ["", "server", "client", "backend", "frontend", "src", "app", "api"]

# Key dependencies to highlight (db drivers, frameworks, etc.)
INTERESTING_DEPS = frozenset({
    # Databases
    "pg", "postgres", "postgresql", "mysql", "mysql2", "mongodb", "mongoose",
    "redis", "sqlite", "sqlite3", "prisma", "typeorm", "sequelize", "knex",
    # Frameworks
    "express", "fastify", "koa", "hapi", "nestjs", "next", "nuxt", "gatsby",
    "react", "vue", "angular", "svelte", "django", "flask", "fastapi",
    "spring", "rails", "laravel", "gin", "echo", "fiber",
    # Testing
    "jest", "vitest", "mocha", "pytest", "junit", "rspec",
    # Build/tooling
    "typescript", "webpack", "vite", "esbuild", "rollup", "babel",
})

# License file names to check
LICENSE_FILES = ["LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"]


def _extract_config_heuristic(repo_root: Path) -> list[str]:
    """Extract config metadata using heuristic pattern matching.

    This is the fast path that extracts known fields from common config files
    without requiring any ML models.

    Args:
        repo_root: Path to repository root.

    Returns:
        List of extracted metadata lines.
    """
    import json
    import re

    lines: list[str] = []

    def _extract_package_json(path: Path, prefix: str) -> list[str]:
        """Extract key fields from package.json."""
        result = []
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            info = []

            # Core metadata
            for key in ["name", "version", "license"]:
                if key in data:
                    info.append(f"{key}: {data[key]}")

            # Interesting dependencies with versions
            for dep_type in ["dependencies", "devDependencies"]:
                if dep_type in data and isinstance(data[dep_type], dict):
                    deps = data[dep_type]
                    for dep_name in INTERESTING_DEPS:
                        if dep_name in deps:
                            info.append(f"{dep_name}: {deps[dep_name]}")

            if info:
                result.append(f"{prefix}package.json: {'; '.join(info)}")
        except (json.JSONDecodeError, OSError):
            pass
        return result

    def _extract_go_mod(path: Path, prefix: str) -> list[str]:
        """Extract module name and key dependencies from go.mod."""
        result = []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            extracted = []

            # Module name
            module_match = re.search(r"^module\s+(\S+)", content, re.MULTILINE)
            if module_match:
                extracted.append(f"module: {module_match.group(1)}")

            # Go version
            go_match = re.search(r"^go\s+([\d.]+)", content, re.MULTILINE)
            if go_match:
                extracted.append(f"go: {go_match.group(1)}")

            # Key require statements (look for database drivers, web frameworks)
            interesting_go = {
                "gorilla/websocket", "gorilla/mux", "gin-gonic/gin",
                "labstack/echo", "gofiber/fiber", "lib/pq", "go-sql-driver/mysql",
                "jackc/pgx", "go-redis/redis", "mongodb/mongo-go-driver",
            }
            for dep in interesting_go:
                if dep in content:
                    extracted.append(dep.split("/")[-1])

            if extracted:
                result.append(f"{prefix}go.mod: {'; '.join(extracted)}")
        except OSError:  # pragma: no cover
            pass  # pragma: no cover
        return result

    def _extract_pom_xml(path: Path, prefix: str) -> list[str]:
        """Extract Maven coordinates from pom.xml."""
        result = []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")[:4000]
            extracted = []

            for tag in ["groupId", "artifactId", "version", "packaging"]:
                match = re.search(f"<{tag}>([^<]+)</{tag}>", content)
                if match:
                    extracted.append(f"{tag}: {match.group(1)}")

            if extracted:
                result.append(f"{prefix}pom.xml: {'; '.join(extracted)}")
        except OSError:  # pragma: no cover
            pass  # pragma: no cover
        return result

    def _extract_cargo_toml(path: Path, prefix: str) -> list[str]:
        """Extract Rust package info from Cargo.toml."""
        result = []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            extracted = []

            # Parse [package] section fields (including edition and rust-version)
            for field in ["name", "version", "edition", "rust-version", "license"]:
                match = re.search(rf'^{field}\s*=\s*"([^"]+)"', content, re.MULTILINE)
                if match:
                    extracted.append(f"{field}: {match.group(1)}")

            if extracted:
                result.append(f"{prefix}Cargo.toml: {'; '.join(extracted)}")
        except OSError:  # pragma: no cover
            pass  # pragma: no cover
        return result

    def _extract_pyproject_toml(path: Path, prefix: str) -> list[str]:
        """Extract Python project info from pyproject.toml."""
        result = []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            extracted = []

            for field in ["name", "version", "license"]:
                # Handle both quoted and unquoted values
                match = re.search(rf'^{field}\s*=\s*["\']?([^"\'#\n]+)', content, re.MULTILINE)
                if match:
                    extracted.append(f"{field}: {match.group(1).strip()}")

            if extracted:
                result.append(f"{prefix}pyproject.toml: {'; '.join(extracted)}")
        except OSError:  # pragma: no cover
            pass  # pragma: no cover
        return result

    def _extract_mix_exs(path: Path, prefix: str) -> list[str]:
        """Extract Elixir project info from mix.exs."""
        result = []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            extracted = []

            # App name
            app_match = re.search(r'app:\s*:(\w+)', content)
            if app_match:
                extracted.append(f"app: {app_match.group(1)}")

            # Version
            version_match = re.search(r'version:\s*"([^"]+)"', content)
            if version_match:
                extracted.append(f"version: {version_match.group(1)}")

            # Elixir requirement
            elixir_match = re.search(r'elixir:\s*"([^"]+)"', content)
            if elixir_match:
                extracted.append(f"elixir: {elixir_match.group(1)}")

            if extracted:
                result.append(f"{prefix}mix.exs: {'; '.join(extracted)}")
        except OSError:  # pragma: no cover
            pass  # pragma: no cover
        return result

    def _extract_build_gradle(path: Path, prefix: str) -> list[str]:
        """Extract Kotlin/Java project info from build.gradle or build.gradle.kts."""
        result = []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")[:4000]
            extracted = []

            # Group
            group_match = re.search(r'group\s*[=:]\s*["\']?([^"\'\s]+)', content)
            if group_match:
                extracted.append(f"group: {group_match.group(1)}")

            # Version
            version_match = re.search(r'version\s*[=:]\s*["\']?([^"\'\s]+)', content)
            if version_match and version_match.group(1) != "=":
                extracted.append(f"version: {version_match.group(1)}")

            # Look for plugins (kotlin, java, application)
            for plugin in ["kotlin", "java", "application", "dokka"]:
                if f'"{plugin}"' in content or f"'{plugin}'" in content or "kotlin(" in content:
                    extracted.append(plugin)

            if extracted:
                fname = path.name
                result.append(f"{prefix}{fname}: {'; '.join(extracted)}")
        except OSError:  # pragma: no cover
            pass  # pragma: no cover
        return result

    def _extract_gemfile(path: Path, prefix: str) -> list[str]:
        """Extract Ruby gems from Gemfile."""
        result = []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            extracted = []

            # Ruby version
            ruby_match = re.search(r'ruby\s+["\']([^"\']+)', content)
            if ruby_match:
                extracted.append(f"ruby: {ruby_match.group(1)}")

            # Key gems
            interesting_gems = {"rails", "sinatra", "puma", "devise", "sidekiq", "redis", "pg", "mysql2"}
            for gem in interesting_gems:
                if re.search(rf"gem\s+['\"]({gem})['\"]", content):
                    extracted.append(gem)

            if extracted:
                result.append(f"{prefix}Gemfile: {'; '.join(extracted)}")
        except OSError:  # pragma: no cover
            pass  # pragma: no cover
        return result

    # Scan config files in root and common subdirectories
    for config_name in CONFIG_FILES:
        for subdir in CONFIG_SUBDIRS:
            config_path = repo_root / subdir / config_name if subdir else repo_root / config_name
            if not config_path.exists():
                continue

            prefix = f"{subdir}/" if subdir else ""

            if config_name == "package.json":
                lines.extend(_extract_package_json(config_path, prefix))
            elif config_name == "go.mod":
                lines.extend(_extract_go_mod(config_path, prefix))
            elif config_name == "pom.xml":
                lines.extend(_extract_pom_xml(config_path, prefix))
            elif config_name == "Cargo.toml":
                lines.extend(_extract_cargo_toml(config_path, prefix))
            elif config_name == "pyproject.toml":
                lines.extend(_extract_pyproject_toml(config_path, prefix))
            elif config_name == "mix.exs":
                lines.extend(_extract_mix_exs(config_path, prefix))
            elif config_name in ("build.gradle", "build.gradle.kts"):
                lines.extend(_extract_build_gradle(config_path, prefix))
            elif config_name == "Gemfile":
                lines.extend(_extract_gemfile(config_path, prefix))

    # Detect license type from LICENSE files
    for license_name in LICENSE_FILES:
        license_path = repo_root / license_name
        if license_path.exists():
            try:
                # Read just enough to detect license type
                content = license_path.read_text(encoding="utf-8", errors="replace")[:500]
                license_type = None

                # Check for common license types (order matters: AGPL before GPL)
                content_upper = content.upper()
                if "AGPL" in content_upper or "AFFERO" in content_upper:
                    license_type = "AGPL"
                elif "GPL" in content_upper and "LESSER" in content_upper:
                    license_type = "LGPL"
                elif "GPL" in content_upper:
                    license_type = "GPL"
                elif "MIT LICENSE" in content_upper or "PERMISSION IS HEREBY GRANTED" in content_upper:
                    license_type = "MIT"
                elif "APACHE LICENSE" in content_upper:
                    license_type = "Apache"
                elif "BSD" in content_upper:
                    license_type = "BSD"
                elif "MOZILLA PUBLIC LICENSE" in content_upper:
                    license_type = "MPL"
                elif "ISC LICENSE" in content_upper:
                    license_type = "ISC"
                elif "UNLICENSE" in content_upper:
                    license_type = "Unlicense"

                if license_type:
                    lines.append(f"LICENSE: {license_type}")
                break  # Only process first found license file
            except OSError:  # pragma: no cover
                pass  # pragma: no cover

    return lines


def _collect_config_content(repo_root: Path) -> list[tuple[str, str]]:
    """Collect all config file content as (filename, content) pairs.

    Used by embedding mode to have raw content for semantic selection.

    Args:
        repo_root: Path to repository root.

    Returns:
        List of (prefixed_filename, content) tuples.
    """
    config_content: list[tuple[str, str]] = []

    for config_name in CONFIG_FILES:
        for subdir in CONFIG_SUBDIRS:
            config_path = repo_root / subdir / config_name if subdir else repo_root / config_name
            if not config_path.exists():
                continue

            try:
                content = config_path.read_text(encoding="utf-8", errors="replace")
                prefix = f"{subdir}/" if subdir else ""
                config_content.append((f"{prefix}{config_name}", content))
            except OSError:  # pragma: no cover
                pass  # pragma: no cover

    # Also include LICENSE file content
    for license_name in LICENSE_FILES:
        license_path = repo_root / license_name
        if license_path.exists():
            try:
                content = license_path.read_text(encoding="utf-8", errors="replace")[:2000]
                config_content.append((license_name, content))
                break  # Only first license file
            except OSError:  # pragma: no cover
                pass  # pragma: no cover

    return config_content


def _get_repo_languages(repo_root: Path) -> set[str]:
    """Detect languages in a repo by scanning for common file extensions."""
    ext_to_lang = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
        ".scala": "scala", ".rb": "ruby", ".php": "php",
        ".ex": "elixir", ".exs": "elixir", ".erl": "erlang",
        ".hs": "haskell", ".swift": "swift", ".cs": "csharp",
        ".fs": "fsharp", ".c": "c", ".cpp": "cpp", ".cc": "cpp",
        ".ml": "ocaml", ".clj": "clojure", ".zig": "zig",
        ".nim": "nim", ".dart": "dart", ".jl": "julia",
        ".groovy": "groovy",
    }
    languages: set[str] = set()
    try:
        for item in repo_root.rglob("*"):
            if item.is_file():
                ext = item.suffix.lower()
                if ext in ext_to_lang:
                    languages.add(ext_to_lang[ext])
                    if len(languages) > 10:  # pragma: no cover - early exit
                        break
    except OSError:  # pragma: no cover
        pass
    return languages if languages else {"_common"}


def _discover_config_files_embedding(
    repo_root: Path,
    similarity_threshold: float = 0.85,
    max_dir_size: int = 200,
    detected_languages: set[str] | None = None,
) -> set[Path]:
    """Discover potential config files using embedding similarity.

    Uses language-specific probe embeddings to reduce false positives.
    A Kotlin project won't match on "Pipfile" because Python config patterns
    aren't included when only Kotlin is detected.

    Uses sentence-transformers to find files with names similar to known
    CONFIG_FILES patterns. This catches config files in unfamiliar formats.

    Algorithm:
    1. Compute embeddings for known CONFIG_FILES names
    2. Collect unique filenames from repo (excluding large directories)
    3. Find repo files with high similarity to known config file names
    4. Return discovered files as a set

    Args:
        repo_root: Path to repository root.
        similarity_threshold: Minimum cosine similarity to consider a match.
        max_dir_size: Skip directories with more than this many items.

    Returns:
        Set of discovered config file paths.
    """
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError:  # pragma: no cover
        return set()  # No discovery without sentence-transformers

    # Detect languages if not provided
    if detected_languages is None:
        detected_languages = _get_repo_languages(repo_root)

    # Build language-specific config file list
    relevant_configs: set[str] = set()
    for lang in detected_languages:
        if lang in CONFIG_FILES_BY_LANG:
            relevant_configs.update(CONFIG_FILES_BY_LANG[lang])
    # Always include common configs
    relevant_configs.update(CONFIG_FILES_BY_LANG.get("_common", []))

    # If no language detected, fall back to all configs
    if not relevant_configs:  # pragma: no cover
        relevant_configs = set(CONFIG_FILES)

    # Get base names (strip glob patterns)
    known_names = []
    for name in relevant_configs:
        if "*" in name:  # pragma: no cover - glob patterns
            # For patterns like "*.csproj", use the extension as semantic hint
            known_names.append(name.replace("*", "config"))
        else:
            known_names.append(name)

    # Collect unique filenames from repo, excluding large directories
    repo_files: dict[str, list[Path]] = {}  # filename -> list of paths
    try:
        for item in repo_root.rglob("*"):
            if not item.is_file():  # pragma: no cover - directory traversal
                continue
            # Skip hidden directories and common non-config paths
            parts = item.relative_to(repo_root).parts
            if any(p.startswith(".") and p not in {".ruby-version"} for p in parts[:-1]):
                continue  # pragma: no cover - hidden dir filtering
            if any(p in {"node_modules", "vendor", "venv", ".venv", "__pycache__",
                        "dist", "build", "target", "_build", "deps"} for p in parts):
                continue  # pragma: no cover - common non-config dirs

            # Check parent directory size (skip if too large)
            parent = item.parent
            try:
                dir_size = sum(1 for _ in parent.iterdir())
                if dir_size > max_dir_size:
                    continue  # pragma: no cover - large dir filtering
            except OSError:  # pragma: no cover
                continue

            filename = item.name
            repo_files.setdefault(filename, []).append(item)
    except OSError:  # pragma: no cover
        return set()

    if not repo_files:
        return set()  # pragma: no cover

    # Get unique filenames that aren't already in our language-specific configs
    candidate_names = [
        name for name in repo_files.keys()
        if name not in relevant_configs
        and not name.endswith((".md", ".txt", ".rst", ".html", ".css", ".js",
                               ".ts", ".py", ".go", ".rs", ".java", ".c", ".h",
                               ".cpp", ".hpp", ".rb", ".ex", ".exs"))  # Skip source files
        and len(name) > 2  # Skip trivial names
    ]

    if not candidate_names:
        return set()

    # Pre-filter using character n-gram similarity (fast)
    # pragma: no cover - discovery requires real repos with diverse file names
    def ngram_similarity(s1: str, s2: str, n: int = 3) -> float:  # pragma: no cover
        """Compute character n-gram Jaccard similarity."""
        if len(s1) < n or len(s2) < n:
            return 1.0 if s1 == s2 else 0.0
        ngrams1 = {s1[i:i+n] for i in range(len(s1) - n + 1)}
        ngrams2 = {s2[i:i+n] for i in range(len(s2) - n + 1)}
        if not ngrams1 or not ngrams2:
            return 0.0
        intersection = len(ngrams1 & ngrams2)
        union = len(ngrams1 | ngrams2)
        return intersection / union if union > 0 else 0.0

    # Filter candidates by n-gram similarity to known config files
    ngram_threshold = 0.15  # Low threshold - just filter obvious non-matches
    filtered_candidates = []  # pragma: no cover
    for name in candidate_names:  # pragma: no cover
        max_sim = max(ngram_similarity(name.lower(), known.lower())
                     for known in known_names)
        if max_sim >= ngram_threshold:
            filtered_candidates.append(name)

    if not filtered_candidates:  # pragma: no cover
        return set()

    # Limit remaining candidates for embedding
    max_candidates = 50  # pragma: no cover
    if len(filtered_candidates) > max_candidates:  # pragma: no cover
        # Sort by best n-gram similarity and take top
        filtered_candidates = sorted(
            filtered_candidates,
            key=lambda n: max(ngram_similarity(n.lower(), k.lower()) for k in known_names),
            reverse=True
        )[:max_candidates]

    # Load embedding model and compute similarities
    model = SentenceTransformer("microsoft/unixcoder-base")  # pragma: no cover

    # Embed known config file names
    known_embeddings = model.encode(known_names, convert_to_numpy=True)  # pragma: no cover

    # Embed candidate filenames (pre-filtered by n-grams)
    candidate_embeddings = model.encode(filtered_candidates, convert_to_numpy=True)  # pragma: no cover

    # Normalize for cosine similarity
    known_norms = np.linalg.norm(known_embeddings, axis=1, keepdims=True)  # pragma: no cover
    known_normalized = known_embeddings / (known_norms + 1e-8)  # pragma: no cover

    candidate_norms = np.linalg.norm(candidate_embeddings, axis=1, keepdims=True)  # pragma: no cover
    candidate_normalized = candidate_embeddings / (candidate_norms + 1e-8)  # pragma: no cover

    # Compute pairwise similarities (candidates x known)
    similarities = np.dot(candidate_normalized, known_normalized.T)  # pragma: no cover

    # Find candidates that match any known config file pattern
    discovered: set[Path] = set()  # pragma: no cover
    max_sims = np.max(similarities, axis=1)  # pragma: no cover

    for name, max_sim in zip(filtered_candidates, max_sims, strict=True):  # pragma: no cover
        if max_sim >= similarity_threshold:
            # Add all paths with this filename (could be in multiple subdirs)
            for path in repo_files[name]:
                discovered.add(path)

    return discovered  # pragma: no cover


def _collect_config_content_with_discovery(
    repo_root: Path,
    use_discovery: bool = True,
) -> list[tuple[str, str]]:
    """Collect config file content, optionally with embedding-based discovery.

    Extends _collect_config_content by also including files discovered through
    embedding similarity matching.

    Args:
        repo_root: Path to repository root.
        use_discovery: If True, use embedding-based discovery for additional files.

    Returns:
        List of (prefixed_filename, content) tuples.
    """
    # Start with standard config collection
    config_content = _collect_config_content(repo_root)
    seen_paths: set[Path] = set()

    # Track which files we already have
    for config_name in CONFIG_FILES:
        for subdir in CONFIG_SUBDIRS:
            if "*" in config_name:  # pragma: no cover - glob patterns rare in tests
                # Handle glob patterns
                pattern = config_name
                search_dir = repo_root / subdir if subdir else repo_root
                if search_dir.exists():
                    for match in search_dir.glob(pattern):
                        if match.is_file():
                            seen_paths.add(match)
            else:
                config_path = repo_root / subdir / config_name if subdir else repo_root / config_name
                if config_path.exists():
                    seen_paths.add(config_path)

    # Also handle glob patterns from CONFIG_FILES
    for config_name in CONFIG_FILES:
        if "*" in config_name:  # pragma: no cover - glob patterns rare in tests
            for subdir in CONFIG_SUBDIRS:
                search_dir = repo_root / subdir if subdir else repo_root
                if search_dir.exists():
                    for match in search_dir.glob(config_name):
                        if match.is_file() and match not in seen_paths:
                            try:
                                content = match.read_text(encoding="utf-8", errors="replace")
                                rel_path = match.relative_to(repo_root)
                                config_content.append((str(rel_path), content))
                                seen_paths.add(match)
                            except OSError:
                                pass

    if not use_discovery:
        return config_content  # pragma: no cover - discovery disabled

    # Discover additional config files using embeddings
    discovered = _discover_config_files_embedding(repo_root)

    for path in discovered:  # pragma: no cover - discovery integration
        if path in seen_paths:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            rel_path = path.relative_to(repo_root)
            config_content.append((str(rel_path), content))
            seen_paths.add(path)
        except OSError:
            pass

    return config_content


def _compute_log_sample_size(num_lines: int, fleximax: int) -> int:
    """Compute log-scaled sample size for a file.

    For small files (num_lines <= fleximax), samples all lines.
    For larger files, uses formula: fleximax + log10(num_lines) * (fleximax/10)

    This ensures large files get more samples but growth is logarithmic.
    """
    import math
    if num_lines <= fleximax:
        return num_lines
    # log10(1000) = 3, so a 1000-line file with fleximax=100 gets 100 + 3*10 = 130
    return int(fleximax + math.log10(num_lines) * (fleximax / 10))


def _compute_stride(num_lines: int, sample_size: int) -> int:
    """Compute stride N for sampling, ensuring N >= 4 for context windows.

    Returns the smallest N >= 4 such that num_lines / N <= sample_size.
    If num_lines <= sample_size, returns 1 (sample all).
    """
    if num_lines <= sample_size:
        return 1
    # Find N such that ceil(num_lines / N) <= sample_size
    # N = ceil(num_lines / sample_size)
    n = (num_lines + sample_size - 1) // sample_size
    return max(4, n)


def _build_context_chunk(
    lines: list[str],
    center_idx: int,
    max_chunk_chars: int,
    fleximax_words: int = 50,
) -> str:
    """Build a 3-line chunk with context, subsampling words if too long.

    Takes lines [center_idx-1, center_idx, center_idx+1] and joins them.
    If the result exceeds max_chunk_chars, applies word-level subsampling
    with ellipsis to indicate elision.

    Args:
        lines: All lines in the file.
        center_idx: Index of the center line to build chunk around.
        max_chunk_chars: Maximum characters for the chunk.
        fleximax_words: Base sample size for word-level subsampling.

    Returns:
        Chunk string, possibly with ellipsis if words were subsampled.
    """
    import math

    # Get context lines (before, center, after)
    start_idx = max(0, center_idx - 1)
    end_idx = min(len(lines), center_idx + 2)
    context_lines = [lines[i] for i in range(start_idx, end_idx) if lines[i]]

    chunk = " ".join(context_lines)

    # If within limit, return as-is
    if len(chunk) <= max_chunk_chars:
        return chunk

    # Need to subsample at word level
    words = chunk.split()
    num_words = len(words)

    if num_words <= fleximax_words:
        # Just truncate to max_chars
        return chunk[:max_chunk_chars]

    # Compute log-scaled sample size for words
    sample_size = int(fleximax_words + math.log10(num_words) * (fleximax_words / 10))
    stride = max(4, (num_words + sample_size - 1) // sample_size)

    # Sample words with context (before, target, after) and ellipsis
    result_parts: list[str] = []
    i = 0
    while i < num_words:
        # Get context: before, center, after
        before_idx = max(0, i - 1)
        after_idx = min(num_words - 1, i + 1)

        context_words = []
        if before_idx < i:
            context_words.append(words[before_idx])
        context_words.append(words[i])
        if after_idx > i:
            context_words.append(words[after_idx])

        result_parts.append(" ".join(context_words))
        i += stride

    # Join with ellipsis
    result = " ... ".join(result_parts)

    # Final truncation if still too long
    if len(result) > max_chunk_chars:
        result = result[:max_chunk_chars - 3] + "..."

    return result


def _extract_config_embedding(
    repo_root: Path,
    max_lines: int = 30,
    similarity_threshold: float = 0.25,
    max_lines_per_file: int = 8,
    max_config_files: int = 15,
    fleximax_lines: int = 100,
    max_chunk_chars: int = 800,
) -> list[str]:
    """Extract config metadata using dual-probe stratified embedding selection.

    Uses a dual-probe system with sentence-transformers:
    1. ANSWER_PATTERNS probe: Matches factual metadata lines (version, name, etc.)
    2. BIG_PICTURE_QUESTIONS probe: Matches architectural/contextual lines

    Each file is searched independently (stratified) to prevent large files
    from crowding out smaller ones. Uses log-scaled sampling for large files:
    files with more lines get proportionally more samples (logarithmically).

    Lines are sampled with context (before/after) and combined into chunks
    for embedding. If chunks exceed max_chunk_chars, word-level subsampling
    with ellipsis is applied.

    Args:
        repo_root: Path to repository root.
        max_lines: Maximum total lines to extract across all files.
        similarity_threshold: Minimum similarity score to include a line.
        max_lines_per_file: Maximum lines to extract per config file.
        max_config_files: Maximum number of config files to process.
        fleximax_lines: Base sample size for log-scaled line sampling.
        max_chunk_chars: Maximum characters per chunk for embedding.

    Returns:
        List of extracted metadata lines, ordered by file then relevance.
    """
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError:  # pragma: no cover
        # Fall back to heuristic if sentence-transformers not available
        return _extract_config_heuristic(repo_root)[:max_lines]

    # Collect all config content (with embedding-based discovery)
    config_content = _collect_config_content_with_discovery(repo_root, use_discovery=True)
    if not config_content:
        return []  # pragma: no cover - defensive, caller checks for config files

    # Verbose logging setup
    import sys as _sys
    import time as _time
    _verbose = "HYPERGUMBO_VERBOSE" in os.environ

    def _vlog(msg: str) -> None:
        if _verbose:  # pragma: no cover
            print(f"[embed] {msg}", file=_sys.stderr)

    # Load embedding model once
    _t_load = _time.time()
    model = SentenceTransformer("microsoft/unixcoder-base")
    _vlog(f"Model loaded in {_time.time() - _t_load:.1f}s")

    # Compute normalized embeddings for both probes
    # Using max-to-any-pattern approach (not centroid) for better exact matching
    _t_probes = _time.time()
    # Probe 1: Answer patterns (factual metadata lines)
    answer_embeddings = model.encode(ANSWER_PATTERNS, convert_to_numpy=True)
    answer_norms = np.linalg.norm(answer_embeddings, axis=1, keepdims=True)
    normalized_answer_patterns = answer_embeddings / (answer_norms + 1e-8)

    # Probe 2: Big-picture questions (architectural context)
    question_embeddings = model.encode(BIG_PICTURE_QUESTIONS, convert_to_numpy=True)
    question_norms = np.linalg.norm(question_embeddings, axis=1, keepdims=True)
    normalized_question_patterns = question_embeddings / (question_norms + 1e-8)
    _vlog(f"Probe embeddings ({len(ANSWER_PATTERNS)}+{len(BIG_PICTURE_QUESTIONS)}) in {_time.time() - _t_probes:.1f}s")

    # === PASS 1: Score all files, collect top candidates from each ===
    # Structure: {source: [(sim, center_idx, chunk_text, file_lines), ...]}
    file_candidates: dict[str, list[tuple[float, int, str, list[str]]]] = {}
    processed_files = 0

    for source, content in config_content:
        if processed_files >= max_config_files:  # pragma: no cover
            break

        file_lines = [ln.strip() for ln in content.split("\n")]
        _vlog(f"Processing {source} ({len(file_lines)} lines)...")

        # Get non-empty lines with their indices
        non_empty = [(idx, line) for idx, line in enumerate(file_lines)
                     if line and len(line) > 3]

        if not non_empty:  # pragma: no cover
            continue  # pragma: no cover

        num_lines = len(non_empty)

        # Compute log-scaled sample size and stride
        sample_size = _compute_log_sample_size(num_lines, fleximax_lines)
        stride = _compute_stride(num_lines, sample_size)
        _vlog(f"  Log-scaled: {num_lines} lines -> sample {sample_size}, stride {stride}")

        # Sample line indices at stride intervals
        sampled_indices: list[int] = []
        for i in range(0, num_lines, stride):
            sampled_indices.append(non_empty[i][0])  # Get original line index

        # Build context chunks for each sampled line
        chunks: list[tuple[int, str]] = []  # (center_idx, chunk_text)
        for center_idx in sampled_indices:
            chunk = _build_context_chunk(file_lines, center_idx, max_chunk_chars)
            if chunk:  # Skip empty chunks
                chunks.append((center_idx, chunk))

        if not chunks:  # pragma: no cover
            continue  # pragma: no cover

        # Embed chunks
        chunk_texts = [chunk for _, chunk in chunks]
        _t0 = _time.time()
        chunk_embeddings = model.encode(chunk_texts, convert_to_numpy=True)
        _vlog(f"  Encoded {len(chunk_texts)} chunks in {_time.time() - _t0:.1f}s")

        # Normalize chunks and compute similarity to all probes
        _t1 = _time.time()
        chunk_norms = np.linalg.norm(chunk_embeddings, axis=1, keepdims=True)
        normalized_chunks = chunk_embeddings / (chunk_norms + 1e-8)
        # Shape: (num_chunks, num_answer_patterns)
        answer_sim_matrix = np.dot(normalized_chunks, normalized_answer_patterns.T)
        # Shape: (num_chunks, num_question_patterns)
        question_sim_matrix = np.dot(normalized_chunks, normalized_question_patterns.T)
        # Combine into single matrix: (num_chunks, num_all_probes)
        combined_sim_matrix = np.concatenate(
            [answer_sim_matrix, question_sim_matrix], axis=1
        )
        # Top-k mean: require k probes to "agree" rather than one spurious match
        # This softens max-pooling sensitivity while preserving signal
        top_k = 3
        num_probes = combined_sim_matrix.shape[1]
        if num_probes >= top_k:
            # Partition to get top-k values (more efficient than full sort)
            top_k_values = np.partition(combined_sim_matrix, -top_k, axis=1)[:, -top_k:]
            similarities = np.mean(top_k_values, axis=1)
        else:
            # Fallback if fewer probes than k (shouldn't happen in practice)
            similarities = np.mean(combined_sim_matrix, axis=1)  # pragma: no cover

        # Apply penalty for LICENSE/COPYING files - their verbose content is
        # semantically similar to many probes but has low information density.
        # ANSWER_PATTERNS already captures compact 'license = "MIT"' declarations.
        source_lower = source.lower()
        if "license" in source_lower or "copying" in source_lower:
            license_penalty = 0.5  # Reduce similarity scores by 50%
            similarities = similarities * license_penalty
            _vlog(f"  Applied LICENSE penalty ({license_penalty}x) to {source}")

        _vlog(f"  Dot products/similarity in {(_time.time() - _t1)*1000:.1f}ms")

        # Collect chunks above threshold, sorted by similarity
        # Store center_idx, chunk_text, file_lines, AND embedding for diversity computation
        above_threshold = [
            (float(sim), center_idx, chunk_text, file_lines, normalized_chunks[i])
            for i, ((center_idx, chunk_text), sim) in enumerate(
                zip(chunks, similarities, strict=True)
            )
            if sim >= similarity_threshold
        ]
        above_threshold.sort(reverse=True, key=lambda x: x[0])

        if above_threshold:
            file_candidates[source] = above_threshold

        processed_files += 1

    if not file_candidates:
        return []  # pragma: no cover

    # === PASS 2: Fair allocation across files ===
    # Each file gets equal base allocation, then remainder distributed by quality
    base_per_file = max(5, max_lines_per_file // 2)  # Minimum 5 lines per file

    # Collect selected chunks with fair allocation
    # Structure: [(sim, source, center_idx, chunk_text), ...]
    selected_chunks: list[tuple[float, str, int, str]] = []

    # Track picks per file for diminishing returns AND selected embeddings for diversity
    picks_per_file: dict[str, int] = dict.fromkeys(file_candidates, 0)
    # selected_embeddings_per_file: {source: [embedding1, embedding2, ...]}
    selected_embeddings_per_file: dict[str, list[np.ndarray]] = {
        source: [] for source in file_candidates
    }

    # First: give each file its base allocation
    for source, candidates in file_candidates.items():
        for sim, center_idx, chunk_text, _file_lines, embedding in candidates[
            :base_per_file
        ]:
            selected_chunks.append((sim, source, center_idx, chunk_text))
            picks_per_file[source] += 1
            selected_embeddings_per_file[source].append(embedding)

    # Second: if budget remains, fill with diminishing returns + diversity selection
    remaining_budget = max_lines - len(selected_chunks)
    if remaining_budget > 0:
        # Parameters for diminishing returns and diversity
        diminishing_alpha = 0.5  # Same as symbol selection
        diversity_weight = 0.3  # How much to penalize similar chunks

        # Build priority queue with adjusted scores
        # Structure: [(-adjusted_score, sim, source, center_idx, chunk_text, embedding)]
        import heapq

        pq: list[tuple[float, float, str, int, str, np.ndarray]] = []

        for source, candidates in file_candidates.items():
            for sim, center_idx, chunk_text, _file_lines, embedding in candidates[
                base_per_file:
            ]:
                # Compute initial adjusted score
                picks = picks_per_file[source]
                marginal = sim / (1 + diminishing_alpha * picks)

                # Compute diversity penalty (max similarity to already-selected from same file)
                diversity_penalty = 0.0
                if selected_embeddings_per_file[source]:
                    selected_embs = np.array(selected_embeddings_per_file[source])
                    # embedding is already normalized, selected_embs are normalized
                    chunk_sims = np.dot(selected_embs, embedding)
                    diversity_penalty = float(np.max(chunk_sims))

                # Adjusted score: diminishing returns * diversity discount
                adjusted = marginal * (1 - diversity_weight * diversity_penalty)
                heapq.heappush(
                    pq, (-adjusted, sim, source, center_idx, chunk_text, embedding)
                )

        # Greedy selection with recomputation after each pick
        while len(selected_chunks) < max_lines and pq:
            neg_adj, sim, source, center_idx, chunk_text, embedding = heapq.heappop(pq)

            # Add to selected
            selected_chunks.append((sim, source, center_idx, chunk_text))
            picks_per_file[source] += 1
            selected_embeddings_per_file[source].append(embedding)

            # Recompute scores for remaining candidates from the SAME file
            # (their diversity penalty has changed)
            new_pq: list[tuple[float, float, str, int, str, np.ndarray]] = []
            while pq:
                neg_adj2, sim2, source2, center_idx2, chunk_text2, emb2 = heapq.heappop(
                    pq
                )
                if source2 == source:
                    # Recompute adjusted score for this candidate
                    picks = picks_per_file[source2]
                    marginal = sim2 / (1 + diminishing_alpha * picks)
                    selected_embs = np.array(selected_embeddings_per_file[source2])
                    chunk_sims = np.dot(selected_embs, emb2)
                    diversity_penalty = float(np.max(chunk_sims))
                    adjusted = marginal * (1 - diversity_weight * diversity_penalty)
                    new_pq.append(
                        (-adjusted, sim2, source2, center_idx2, chunk_text2, emb2)
                    )
                else:
                    # Keep original score (unchanged)
                    new_pq.append(
                        (neg_adj2, sim2, source2, center_idx2, chunk_text2, emb2)
                    )
            # Rebuild heap
            heapq.heapify(new_pq)
            pq = new_pq

    # === PASS 3: Format output, grouping by file ===
    from collections import defaultdict
    by_source: dict[str, list[tuple[float, int, str]]] = defaultdict(list)
    for sim, source, center_idx, chunk_text in selected_chunks:
        by_source[source].append((sim, center_idx, chunk_text))

    # Sort each file's chunks by center line index for coherent output
    for source in by_source:
        by_source[source].sort(key=lambda x: x[1])

    # Build output - all files get representation
    result_lines: list[str] = []

    for source in sorted(by_source.keys()):
        file_selected = by_source[source]
        if not file_selected:  # pragma: no cover
            continue  # pragma: no cover

        # Add file header
        if result_lines:
            result_lines.append("")
        result_lines.append(f"[{source}]")

        # Output chunks (context already included, may have ellipsis for subsampled)
        seen_chunks: set[int] = set()
        for _sim, center_idx, chunk_text in file_selected:
            # Deduplicate overlapping chunks by center index
            if center_idx in seen_chunks:  # pragma: no cover
                continue
            seen_chunks.add(center_idx)

            # Format chunk - indent and mark with ~ if it contains ellipsis (was subsampled)
            if " ... " in chunk_text:  # pragma: no cover - tested in unit test
                result_lines.append(f"  ~ {chunk_text}")
            else:
                result_lines.append(f"  > {chunk_text}")

    return result_lines


def _extract_config_hybrid(
    repo_root: Path,
    max_chars: int = 1500,
    max_config_files: int = 15,
    fleximax_lines: int = 100,
    max_chunk_chars: int = 800,
) -> list[str]:
    """Extract config using hybrid approach: heuristics first, then embeddings.

    This combines the best of both approaches:
    1. First, extract known fields using fast heuristic patterns
    2. Then, use embedding-based selection to fill remaining budget
       with semantically relevant content not captured by heuristics

    Args:
        repo_root: Path to repository root.
        max_chars: Maximum characters for output.
        max_config_files: Maximum config files to process (embedding mode).
        fleximax_lines: Base sample size for log-scaled line sampling.
        max_chunk_chars: Maximum characters per chunk for embedding.

    Returns:
        List of extracted metadata lines.
    """
    # Step 1: Get heuristic extraction (fast, reliable for known fields)
    heuristic_lines = _extract_config_heuristic(repo_root)
    heuristic_text = "\n".join(heuristic_lines)

    # If heuristics already fill the budget, we're done
    if len(heuristic_text) >= max_chars * 0.8:
        return heuristic_lines  # pragma: no cover - edge case, very large configs

    # Step 2: Compute remaining budget for embedding-based extraction
    remaining_chars = max_chars - len(heuristic_text) - 50  # Buffer
    if remaining_chars < 100:
        return heuristic_lines  # pragma: no cover - edge case, budget nearly filled

    # Estimate lines we can add
    remaining_lines = max(5, remaining_chars // 50)

    # Step 3: Get embedding-based extraction
    try:
        embedding_lines = _extract_config_embedding(
            repo_root,
            max_lines=remaining_lines,
            max_config_files=max_config_files,
            fleximax_lines=fleximax_lines,
            max_chunk_chars=max_chunk_chars,
        )
    except Exception:  # pragma: no cover
        # If embedding fails, just return heuristic results
        return heuristic_lines

    # Step 4: Merge, avoiding duplicates
    # Extract key terms from heuristic lines to avoid redundancy
    heuristic_terms = set()
    for line in heuristic_lines:
        # Extract significant words
        for word in line.lower().split():
            if len(word) > 3:
                heuristic_terms.add(word.strip(":;,"))

    # Add embedding lines that provide new information
    combined = heuristic_lines.copy()
    if embedding_lines:
        combined.append("")  # Separator
        combined.append("# Additional context (semantic)")
        for line in embedding_lines:
            # Skip if line content is already covered by heuristics
            line_lower = line.lower()
            is_redundant = sum(1 for term in heuristic_terms if term in line_lower) > 2
            if not is_redundant:
                combined.append(line)

    return combined


def _extract_config_info(
    repo_root: Path,
    max_chars: int = 1500,
    mode: ConfigExtractionMode = ConfigExtractionMode.HEURISTIC,
    max_config_files: int = 15,
    fleximax_lines: int = 100,
    max_chunk_chars: int = 800,
) -> str:
    """Extract key metadata from config files via extractive summarization.

    Supports three extraction modes:
    - HEURISTIC: Fast pattern-based extraction of known fields (default)
    - EMBEDDING: Semantic selection using UnixCoder + question centroid
    - HYBRID: Heuristics first, then embeddings for remaining budget

    For long config files (e.g., package.json with hundreds of deps), only
    the relevant fields/lines are extracted, keeping output bounded.

    Args:
        repo_root: Path to repository root.
        max_chars: Maximum characters for config section output.
        mode: Extraction mode (heuristic, embedding, or hybrid).
        max_config_files: Maximum config files to process (embedding mode).
        fleximax_lines: Base sample size for log-scaled line sampling.
        max_chunk_chars: Maximum characters per chunk for embedding.

    Returns:
        Extracted config metadata as a formatted string, or empty string
        if no config files found.
    """
    # Select extraction strategy based on mode
    if mode == ConfigExtractionMode.EMBEDDING:
        max_lines = max(10, max_chars // 50)
        lines = _extract_config_embedding(
            repo_root,
            max_lines=max_lines,
            max_config_files=max_config_files,
            fleximax_lines=fleximax_lines,
            max_chunk_chars=max_chunk_chars,
        )
    elif mode == ConfigExtractionMode.HYBRID:
        lines = _extract_config_hybrid(
            repo_root,
            max_chars=max_chars,
            max_config_files=max_config_files,
            fleximax_lines=fleximax_lines,
            max_chunk_chars=max_chunk_chars,
        )
    else:  # HEURISTIC (default)
        lines = _extract_config_heuristic(repo_root)

    # Check if output uses [filename] headers (embedding/hybrid modes)
    # If not, just join and truncate (heuristic mode)
    has_file_headers = any(
        line.startswith("[") and line.endswith("]") and "/" not in line
        for line in lines
    )

    if not has_file_headers:
        result = "\n".join(lines)
        if len(result) > max_chars:  # pragma: no cover - defensive truncation
            result = result[:max_chars]
            last_newline = result.rfind("\n")
            if last_newline > max_chars // 2:
                result = result[:last_newline]
        return result

    # Fair character allocation: each file gets equal share
    # First pass: group lines by file (lines starting with "[" are file headers)
    # Also preserve any "preamble" lines that come before the first header (hybrid mode)
    # NOTE: This block only executes when embedding mode produces [filename] headers.
    # When sentence-transformers is unavailable, this code path is never reached.
    preamble_lines: list[str] = []  # pragma: no cover - embedding output only
    file_sections: list[tuple[str, list[str]]] = []  # pragma: no cover - embedding output only
    current_file = ""  # pragma: no cover - embedding output only
    current_lines: list[str] = []  # pragma: no cover - embedding output only

    for line in lines:  # pragma: no cover - embedding output only
        if line.startswith("[") and line.endswith("]") and "/" not in line:
            if current_file and current_lines:
                file_sections.append((current_file, current_lines))
            elif current_lines:
                # Lines before first header are preamble (e.g., heuristic in hybrid)
                preamble_lines = current_lines
            current_file = line
            current_lines = []
        else:
            current_lines.append(line)

    if current_file and current_lines:  # pragma: no cover - embedding output only
        file_sections.append((current_file, current_lines))

    if not file_sections and not preamble_lines:  # pragma: no cover
        return "\n".join(lines)[:max_chars]  # pragma: no cover

    # Second pass: allocate chars - preamble gets priority, rest shared among files
    preamble_text = "\n".join(preamble_lines) if preamble_lines else ""  # pragma: no cover - embedding output only
    remaining_chars = max_chars - len(preamble_text)  # pragma: no cover - embedding output only
    if preamble_text:  # pragma: no cover - embedding output only
        remaining_chars -= 2  # Account for separator newlines

    if not file_sections:  # pragma: no cover - defensive, no file headers
        # Only preamble, no file sections
        if len(preamble_text) > max_chars:
            preamble_text = preamble_text[:max_chars]
            last_newline = preamble_text.rfind("\n")
            if last_newline > max_chars // 2:
                preamble_text = preamble_text[:last_newline]
        return preamble_text

    num_files = len(file_sections)  # pragma: no cover - embedding output only
    chars_per_file = remaining_chars // num_files if num_files > 0 else remaining_chars  # pragma: no cover - embedding output only

    result_parts: list[str] = []  # pragma: no cover - embedding output only
    if preamble_text:  # pragma: no cover - embedding output only
        result_parts.append(preamble_text)

    for file_header, file_lines in file_sections:  # pragma: no cover - embedding output only
        # Build this file's content
        file_content = file_header + "\n" + "\n".join(file_lines)

        # Truncate to per-file budget
        if len(file_content) > chars_per_file:  # pragma: no cover - large file edge case
            file_content = file_content[:chars_per_file]
            # Try to cut at line boundary
            last_newline = file_content.rfind("\n")
            if last_newline > chars_per_file // 2:
                file_content = file_content[:last_newline]

        if result_parts:
            result_parts.append("")  # Separator
        result_parts.append(file_content)

    return "\n".join(result_parts)  # pragma: no cover - embedding output only


def _format_config_section(config_info: str) -> str:
    """Format config info as a Markdown section.

    Args:
        config_info: Extracted config information string.

    Returns:
        Markdown-formatted configuration section.
    """
    if not config_info:
        return ""

    lines = ["## Configuration", ""]
    lines.append("```")
    lines.append(config_info)
    lines.append("```")

    return "\n".join(lines)


def _format_language_stats(profile: RepoProfile) -> str:
    """Format language statistics as a summary line."""
    if not profile.languages:
        return "No source files detected"

    # Sort by LOC descending
    sorted_langs = sorted(
        profile.languages.items(),
        key=lambda x: x[1].loc,
        reverse=True,
    )

    # Calculate percentages
    total_loc = sum(lang.loc for lang in profile.languages.values())
    if total_loc == 0:
        return "No source code detected"

    parts = []
    for lang, stats in sorted_langs[:5]:  # Top 5 languages
        pct = (stats.loc / total_loc) * 100
        if pct >= 1:  # Only show languages with ≥1%
            parts.append(f"{lang.title()} ({pct:.0f}%)")

    total_files = sum(lang.files for lang in profile.languages.values())
    return f"{', '.join(parts)} · {total_files} files · ~{total_loc:,} LOC"


def _format_structure(
    repo_root: Path, extra_excludes: Optional[List[str]] = None
) -> str:
    """Format top-level directory structure.

    Filters out directories that match DEFAULT_EXCLUDES patterns
    (e.g., node_modules, __pycache__, .git) to show only meaningful
    project structure.
    """
    from fnmatch import fnmatch

    lines = ["## Structure", ""]

    # Combine default and extra excludes
    excludes = list(DEFAULT_EXCLUDES)
    if extra_excludes:
        excludes.extend(extra_excludes)

    # Get top-level directories, filtering out excluded ones
    dirs = []
    for d in repo_root.iterdir():
        if not d.is_dir():
            continue
        # Check if directory matches any exclude pattern
        excluded = any(fnmatch(d.name, pattern) for pattern in excludes)
        if not excluded:
            dirs.append(d.name)

    dirs = sorted(dirs)

    # Common source directories to highlight
    source_dirs = {"src", "lib", "app", "pkg", "cmd", "internal", "core"}
    test_dirs = {"test", "tests", "spec", "specs", "__tests__"}
    doc_dirs = {"docs", "doc", "documentation"}

    for d in dirs[:10]:  # Limit to 10 directories
        if d in source_dirs:
            lines.append(f"- `{d}/` — Source code")
        elif d in test_dirs:
            lines.append(f"- `{d}/` — Tests")
        elif d in doc_dirs:
            lines.append(f"- `{d}/` — Documentation")
        else:
            lines.append(f"- `{d}/`")

    if len(dirs) > 10:
        lines.append(f"- ... and {len(dirs) - 10} more directories")

    return "\n".join(lines)


def _format_frameworks(profile: RepoProfile) -> str:
    """Format detected frameworks."""
    if not profile.frameworks:
        return ""

    lines = ["## Frameworks", ""]
    for framework in sorted(profile.frameworks):
        lines.append(f"- {framework}")

    return "\n".join(lines)


def _get_repo_name(repo_root: Path) -> str:
    """Get repository name from path."""
    return repo_root.resolve().name


def _extract_readme_description(
    repo_root: Path, max_chars: int = 200
) -> Optional[str]:
    """Extract a description from the project README file.

    Looks for README.md, README.rst, README.txt, or README (in that order)
    and extracts the first descriptive paragraph after the title.

    Args:
        repo_root: Path to the repository root.
        max_chars: Maximum characters to extract (default 200).

    Returns:
        Extracted description string, or None if no README found.
    """
    import re

    # Try different README file names in priority order
    readme_names = ["README.md", "README.rst", "README.txt", "README"]
    readme_path = None
    for name in readme_names:
        candidate = repo_root / name
        if candidate.is_file():
            readme_path = candidate
            break

    if readme_path is None:
        return None

    try:
        content = readme_path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover
        return None

    # Find the markdown title and extract description
    lines = content.split("\n")
    start_idx = 0
    title_subtitle = None

    # Find the first markdown H1 title (# ...)
    for i, line in enumerate(lines):
        if line.startswith("# "):
            # Check if title has a subtitle (e.g., "# Project: Description here")
            title_text = line[2:].strip()
            if ":" in title_text:
                parts = title_text.split(":", 1)
                if len(parts[1].strip()) > 10:  # Meaningful subtitle
                    title_subtitle = parts[1].strip()
            start_idx = i + 1
            break
        # Skip lines before title that are badges/images/comments
        stripped = line.strip()
        if stripped.startswith("![") or stripped.startswith("<!--"):
            continue
        if stripped.startswith("<"):
            continue
        # If we hit a non-skip line before finding title, treat as RST format
        if stripped and not stripped.startswith("#"):
            # RST title: text followed by === or --- underline
            if i + 1 < len(lines) and re.match(r"^[=\-~^]+$", lines[i + 1].strip()):
                start_idx = i + 2
                break

    # Skip any empty lines after title
    while start_idx < len(lines) and not lines[start_idx].strip():
        start_idx += 1

    # Find the first non-empty paragraph (stop at next header or empty line)
    # Skip common non-description content: badges, images, HTML comments
    paragraph_lines = []
    for line in lines[start_idx:]:
        stripped = line.strip()
        # Stop at headers (markdown ## or RST underlines)
        if line.startswith("#") or re.match(r"^[=\-~^]+$", stripped):
            break
        # Stop at empty line (end of paragraph)
        if not stripped and paragraph_lines:
            break
        # Skip markdown images and badges
        if stripped.startswith("![") or stripped.startswith("[!["):
            continue
        # Skip HTML comments
        if stripped.startswith("<!--"):
            continue
        # Skip HTML tags (picture, source, img, etc.)
        if stripped.startswith("<") and not stripped.startswith("<http"):
            continue
        # Skip lines that are just links (often badge URLs)
        if re.match(r"^\[.*\]\(https?://.*\)$", stripped):
            continue
        if stripped:
            paragraph_lines.append(stripped)

    if not paragraph_lines:
        # Fall back to title subtitle if available
        if title_subtitle:
            return title_subtitle
        return None

    description = " ".join(paragraph_lines)

    # Truncate if too long, trying to break at word boundary
    if len(description) > max_chars:
        # Find last space before max_chars
        truncate_at = description.rfind(" ", 0, max_chars)
        if truncate_at > max_chars // 2:
            description = description[:truncate_at] + "…"
        else:
            description = description[: max_chars - 1] + "…"

    return description


def _extract_python_docstrings(
    repo_root: Path, symbols: list[Symbol], max_len: int = 80
) -> dict[str, str]:
    """Extract docstrings for Python symbols.

    Reads Python files and extracts the first line of docstrings for
    functions and classes. Returns a dict mapping symbol IDs to docstring
    summaries (truncated to max_len).

    Args:
        repo_root: Repository root path.
        symbols: List of symbols to extract docstrings for.
        max_len: Maximum length of docstring summary (default 80).

    Returns:
        Dict mapping symbol ID to first-line docstring summary.
    """
    import ast

    docstrings: dict[str, str] = {}

    # Group symbols by file for efficient reading
    symbols_by_file: dict[str, list[Symbol]] = {}
    for sym in symbols:
        if sym.language == "python" and sym.kind in ("function", "class", "method"):
            symbols_by_file.setdefault(sym.path, []).append(sym)

    for file_path, file_symbols in symbols_by_file.items():
        try:
            full_path = repo_root / file_path if not Path(file_path).is_absolute() else Path(file_path)
            if not full_path.exists():
                continue
            source = full_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (SyntaxError, OSError):
            continue

        # Build a map of (start_line, name) -> docstring
        node_docstrings: dict[tuple[int, str], str] = {}

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                docstring = ast.get_docstring(node)
                if docstring:
                    # Take first line only
                    first_line = docstring.split("\n")[0].strip()
                    if len(first_line) > max_len:
                        first_line = first_line[:max_len - 1] + "…"
                    node_docstrings[(node.lineno, node.name)] = first_line

        # Match symbols to docstrings
        for sym in file_symbols:
            key = (sym.span.start_line, sym.name)
            if key in node_docstrings:
                docstrings[sym.id] = node_docstrings[key]

    return docstrings


# Common programming terms to exclude from domain vocabulary
_COMMON_TERMS = frozenset({
    # English stopwords
    "the", "and", "for", "not", "with", "this", "that", "from", "have", "has",
    "are", "was", "were", "been", "being", "will", "would", "could", "should",
    "all", "any", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "than", "too", "very", "when", "where", "which", "while",
    "who", "why", "how", "what", "then", "also", "just", "only",
    # Generic programming terms
    "get", "set", "add", "remove", "delete", "update", "create", "read", "write",
    "init", "start", "stop", "open", "close", "run", "call", "return", "value",
    "name", "type", "data", "item", "items", "list", "array", "object",
    "key", "keys", "val", "var", "vars", "arg", "args", "param", "params",
    "result", "results", "output", "input", "index", "idx", "len", "length",
    "count", "num", "number", "str", "string", "int", "integer", "float", "bool",
    "true", "false", "null", "none", "void", "use", "using", "used",
    "new", "old", "first", "last", "next", "prev", "current", "default",
    "error", "errors", "log", "console", "print", "debug", "info", "warn",
    "text", "msg", "message", "callback", "handler", "listener", "event",
    "async", "await", "promise", "resolve", "reject", "load", "save", "fetch",
    "send", "receive", "process", "handle", "path", "file", "config", "option",
    "options", "state", "props", "ref", "self", "super", "base", "parent",
    "child", "node", "tree", "root", "body", "head", "main", "temp", "util",
    "helper", "wrapper", "manager", "service", "factory", "builder", "module",
    "component", "context", "scope", "global", "local", "instance", "static",
    "public", "private", "protected", "virtual", "abstract", "final", "const",
    # Testing-related terms
    "test", "tests", "expect", "mock", "stub", "spy", "fixture",
    "logger", "logging", "describe", "spec", "suite", "setup",
    "teardown", "before", "after", "given", "verify",
})

# Programming language keywords to exclude
_KEYWORDS = frozenset({
    "class", "function", "return", "import", "export", "const", "else", "elif",
    "while", "break", "continue", "finally", "catch", "throw", "extends",
    "implements", "interface", "static", "public", "private", "protected",
    "super", "switch", "case", "yield", "assert", "raise", "pass", "lambda",
    "struct", "enum", "impl", "match", "trait", "package", "include", "define",
    "ifdef", "ifndef", "endif", "extern", "typedef", "sizeof", "typeof",
})


def _extract_domain_vocabulary(
    repo_root: Path, profile: "RepoProfile", max_terms: int = 12
) -> list[str]:
    """Extract domain-specific vocabulary from source code.

    Analyzes identifiers in source files to find domain-specific terms.
    Filters out common programming terms and language keywords to highlight
    terms unique to this codebase's domain.

    Args:
        repo_root: Path to the repository root.
        profile: Repository profile with language info.
        max_terms: Maximum number of domain terms to return (default 12).

    Returns:
        List of domain-specific terms, ordered by frequency.
    """
    import re
    from collections import Counter

    word_counts: Counter[str] = Counter()

    # File extensions to analyze
    extensions = ["*.py", "*.js", "*.ts", "*.jsx", "*.tsx", "*.java", "*.c", "*.h",
                  "*.go", "*.rs", "*.rb", "*.php", "*.cpp", "*.cc", "*.hpp"]

    # Directories to exclude
    excludes = {"node_modules", "__pycache__", "dist", "build", ".venv", "vendor",
                ".git", "target", "coverage", "htmlcov", ".pytest_cache"}

    for ext in extensions:
        for f in repo_root.rglob(ext):
            # Skip excluded directories
            if any(excl in f.parts for excl in excludes):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                # Extract identifiers
                for match in re.finditer(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', text):
                    word = match.group()
                    if len(word) <= 3:
                        continue
                    if word.lower() in _KEYWORDS:
                        continue
                    # Split compound words (camelCase, PascalCase, snake_case)
                    # First try to find camelCase/PascalCase parts
                    parts = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)', word)
                    if parts:
                        for p in parts:
                            p_lower = p.lower()
                            if len(p_lower) > 3 and p_lower not in _COMMON_TERMS:
                                word_counts[p_lower] += 1
                    # Also split by underscore for snake_case (including UPPER_CASE)
                    for part in word.split('_'):
                        p_lower = part.lower()
                        if len(p_lower) > 3 and p_lower not in _COMMON_TERMS:
                            word_counts[p_lower] += 1
            except OSError:
                continue

    # Return top terms by frequency
    return [word for word, _ in word_counts.most_common(max_terms)]


def _format_vocabulary(terms: list[str]) -> str:
    """Format domain vocabulary as a Markdown section.

    Args:
        terms: List of domain-specific terms.

    Returns:
        Markdown-formatted vocabulary section.
    """
    if not terms:
        return ""

    lines = ["## Domain Vocabulary", ""]
    lines.append(f"*Key terms: {', '.join(terms)}*")

    return "\n".join(lines)


# Source file extensions by language
SOURCE_EXTENSIONS = {
    "python": ["*.py"],
    "javascript": ["*.js", "*.jsx", "*.mjs"],
    "typescript": ["*.ts", "*.tsx"],
    "go": ["*.go"],
    "rust": ["*.rs"],
    "java": ["*.java"],
    "c": ["*.c", "*.h"],
    "cpp": ["*.cpp", "*.cc", "*.hpp", "*.hh"],
    "ruby": ["*.rb"],
    "php": ["*.php"],
}

# Common source directories
SOURCE_DIRS = {"src", "lib", "app", "pkg", "cmd", "internal", "core", "source"}


def _collect_source_files(repo_root: Path, profile: RepoProfile) -> list[Path]:
    """Collect source files, prioritizing source directories."""
    files: list[Path] = []
    seen: set[Path] = set()

    # Get patterns for detected languages
    patterns: list[str] = []
    for lang in profile.languages:
        if lang in SOURCE_EXTENSIONS:
            patterns.extend(SOURCE_EXTENSIONS[lang])

    if not patterns:
        # Fallback to common patterns
        patterns = ["*.py", "*.js", "*.ts", "*.go", "*.rs", "*.java"]

    # First, collect files from source directories (sorted for determinism)
    for source_dir in sorted(SOURCE_DIRS):
        src_path = repo_root / source_dir
        if src_path.is_dir():
            for f in find_files(src_path, patterns):
                if f not in seen:
                    files.append(f)
                    seen.add(f)

    # Then collect remaining files from root
    for f in find_files(repo_root, patterns):
        if f not in seen:
            files.append(f)
            seen.add(f)

    return files


def _format_source_files(
    repo_root: Path,
    files: list[Path],
    max_files: int = 50,
) -> str:
    """Format source files as a Markdown section."""
    if not files:
        return ""

    lines = ["## Source Files", ""]

    for f in files[:max_files]:
        rel_path = f.relative_to(repo_root)
        lines.append(f"- `{rel_path}`")

    if len(files) > max_files:
        lines.append(f"- ... and {len(files) - max_files} more files")

    return "\n".join(lines)


def _format_all_files(
    repo_root: Path,
    max_files: int = 200,
) -> str:
    """Format all files (non-excluded) as a Markdown section."""
    # Collect all non-excluded files
    files: list[Path] = []
    for f in repo_root.rglob("*"):
        if f.is_file():
            # Check exclusions
            excluded = False
            for part in f.relative_to(repo_root).parts:
                for pattern in DEFAULT_EXCLUDES:
                    if part == pattern or (
                        "*" in pattern and part.endswith(pattern.lstrip("*"))
                    ):
                        excluded = True
                        break
                if excluded:
                    break
            if not excluded and not any(p.startswith(".") for p in f.parts):
                files.append(f)

    if not files:
        return ""

    # Sort by path
    files.sort(key=lambda p: str(p.relative_to(repo_root)))

    lines = ["## All Files", ""]

    for f in files[:max_files]:
        rel_path = f.relative_to(repo_root)
        lines.append(f"- `{rel_path}`")

    if len(files) > max_files:
        lines.append(f"- ... and {len(files) - max_files} more files")

    return "\n".join(lines)


# Test file patterns by language/framework
TEST_FILE_PATTERNS = [
    # Python
    "test_*.py",
    "*_test.py",
    "tests.py",
    # JavaScript/TypeScript
    "*.test.js",
    "*.test.ts",
    "*.test.jsx",
    "*.test.tsx",
    "*.spec.js",
    "*.spec.ts",
    "*.spec.jsx",
    "*.spec.tsx",
    "__tests__/*.js",
    "__tests__/*.ts",
    # Go
    "*_test.go",
    # Rust
    # Rust tests are in src files with #[test], harder to detect statically
    # Java
    "*Test.java",
    "*Tests.java",
    # Ruby
    "*_spec.rb",
    "test_*.rb",
    # Shell
    "*.bats",
]

# Test framework detection: (import/require pattern, framework name)
TEST_FRAMEWORK_PATTERNS = [
    # Python
    (r"import pytest|from pytest", "pytest"),
    (r"import unittest|from unittest", "unittest"),
    (r"from hypothesis import", "hypothesis"),
    # JavaScript
    (r"from ['\"]jest['\"]|require\(['\"]jest['\"]", "jest"),
    (r"from ['\"]vitest['\"]|import.*vitest", "vitest"),
    (r"from ['\"]mocha['\"]|require\(['\"]mocha['\"]", "mocha"),
    (r"import.*@testing-library", "testing-library"),
    # Go (built-in testing package)
    (r'import.*"testing"', "go test"),
    # Ruby
    (r"require ['\"]rspec['\"]|RSpec\.describe", "rspec"),
    (r"require ['\"]minitest['\"]", "minitest"),
    # Rust
    (r"#\[cfg\(test\)\]|#\[test\]", "cargo test"),
    # Java
    (r"import org\.junit", "junit"),
    (r"import org\.testng", "testng"),
]


def _detect_test_summary(repo_root: Path) -> tuple[Optional[str], set[str]]:
    """Detect test files and frameworks, return a summary string and frameworks.

    This is a static analysis - it detects test files by naming conventions
    and test frameworks by import patterns. It does NOT measure coverage
    (which requires execution).

    Args:
        repo_root: Path to the repository root.

    Returns:
        Tuple of (summary_string, frameworks_set) where:
        - summary_string: Like "103 test files · pytest, hypothesis" or None if no tests
        - frameworks_set: Set of detected framework names
    """
    import re

    test_files: list[Path] = []
    frameworks_found: set[str] = set()

    # Find test files by pattern
    for pattern in TEST_FILE_PATTERNS:
        for f in repo_root.rglob(pattern):
            if f.is_file() and not is_excluded(f, repo_root):
                test_files.append(f)

    # Deduplicate (same file might match multiple patterns)
    test_files = list(set(test_files))

    if not test_files:
        return None, set()

    # Sample test files to detect frameworks (don't read all of them)
    sample_size = min(20, len(test_files))
    sample_files = test_files[:sample_size]

    for test_file in sample_files:
        try:
            content = test_file.read_text(encoding="utf-8", errors="replace")[:5000]
            for pattern, framework in TEST_FRAMEWORK_PATTERNS:
                if re.search(pattern, content):
                    frameworks_found.add(framework)
        except OSError:  # pragma: no cover
            continue

    # Build summary
    file_count = len(test_files)
    file_word = "file" if file_count == 1 else "files"

    if frameworks_found:
        framework_str = ", ".join(sorted(frameworks_found))
        return f"{file_count} test {file_word} · {framework_str}", frameworks_found
    else:
        return f"{file_count} test {file_word}", frameworks_found


# Coverage command hints for different test frameworks
COVERAGE_HINTS: dict[str, str] = {
    # Python
    "pytest": "pytest --cov",
    "unittest": "coverage run -m unittest",
    "hypothesis": "pytest --cov",  # Usually used with pytest
    # JavaScript/TypeScript
    "jest": "jest --coverage",
    "vitest": "vitest run --coverage",
    "mocha": "nyc mocha",
    "testing-library": "jest --coverage",  # Usually used with jest
    # Go
    "go test": "go test -cover",
    # Java/Kotlin
    "junit": "mvn test jacoco:report",
    "testng": "mvn test jacoco:report",
    # Ruby
    "rspec": "rspec --format documentation",
    # Rust
    "cargo test": "cargo tarpaulin",
    # Elixir
    "exunit": "mix test --cover",
    # Shell
    "bats": "bats tests/",
}


def _get_coverage_hint(frameworks: set[str]) -> str:
    """Get the best coverage command hint for the detected frameworks.

    Prioritizes common frameworks and returns the first match.
    Falls back to generic hint if no match found.
    """
    # Priority order for common frameworks
    priority = [
        "pytest", "jest", "vitest", "go test", "junit",
        "rspec", "cargo test", "exunit", "mocha", "bats",
        "unittest", "testing-library", "hypothesis", "testng",
    ]

    for fw in priority:
        if fw in frameworks:
            return COVERAGE_HINTS[fw]

    # If no specific match, try to infer from any framework
    # (This is a defensive fallback for any future frameworks not in priority list)
    for fw in frameworks:  # pragma: no cover
        if fw in COVERAGE_HINTS:
            return COVERAGE_HINTS[fw]

    # Default fallback
    return "your test runner's coverage tool"


def _format_test_summary(repo_root: Path) -> str:
    """Format test summary as a Markdown section.

    Args:
        repo_root: Path to the repository root.

    Returns:
        Markdown section string, or empty string if no tests.
    """
    summary, frameworks = _detect_test_summary(repo_root)
    if not summary:
        return ""

    coverage_hint = _get_coverage_hint(frameworks)
    return f"## Tests\n\n{summary}\n\n*Coverage requires execution; see {coverage_hint}*"


def _run_analysis(
    repo_root: Path, profile: RepoProfile, exclude_tests: bool = False
) -> tuple[list[Symbol], list]:
    """Run static analysis to get symbols and edges.

    Only runs analysis for detected languages to avoid unnecessary work.
    Applies supply chain classification to all symbols.

    Args:
        repo_root: Path to the repository root.
        profile: Detected repository profile with language info.
        exclude_tests: If True, filter out symbols from test files after analysis.

    Returns:
        (symbols, edges) tuple.
    """
    from .supply_chain import classify_file, detect_package_roots

    all_symbols: list[Symbol] = []
    all_edges: list = []

    # Only import and run analyzers if we have the relevant languages
    if "python" in profile.languages:
        try:
            from .analyze.py import analyze_python
            result = analyze_python(repo_root)
            all_symbols.extend(result.symbols)
            all_edges.extend(result.edges)
        except Exception:  # pragma: no cover
            pass  # Analysis failed, continue without Python symbols

    if "javascript" in profile.languages or "typescript" in profile.languages:
        try:  # pragma: no cover
            from .analyze.js_ts import analyze_javascript  # pragma: no cover
            result = analyze_javascript(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # JS/TS analysis failed or tree-sitter not available

    if "c" in profile.languages:
        try:  # pragma: no cover
            from .analyze.c import analyze_c  # pragma: no cover
            result = analyze_c(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # C analysis failed or tree-sitter not available

    if "rust" in profile.languages:
        try:  # pragma: no cover
            from .analyze.rust import analyze_rust  # pragma: no cover
            result = analyze_rust(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Rust analysis failed or tree-sitter not available

    if "php" in profile.languages:
        try:  # pragma: no cover
            from .analyze.php import analyze_php  # pragma: no cover
            result = analyze_php(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # PHP analysis failed or tree-sitter not available

    if "java" in profile.languages:
        try:  # pragma: no cover
            from .analyze.java import analyze_java  # pragma: no cover
            result = analyze_java(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Java analysis failed or tree-sitter not available

    if "go" in profile.languages:
        try:  # pragma: no cover
            from .analyze.go import analyze_go  # pragma: no cover
            result = analyze_go(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Go analysis failed or tree-sitter not available

    if "ruby" in profile.languages:
        try:  # pragma: no cover
            from .analyze.ruby import analyze_ruby  # pragma: no cover
            result = analyze_ruby(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Ruby analysis failed or tree-sitter not available

    if "kotlin" in profile.languages:
        try:  # pragma: no cover
            from .analyze.kotlin import analyze_kotlin  # pragma: no cover
            result = analyze_kotlin(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Kotlin analysis failed or tree-sitter not available

    if "swift" in profile.languages:
        try:  # pragma: no cover
            from .analyze.swift import analyze_swift  # pragma: no cover
            result = analyze_swift(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Swift analysis failed or tree-sitter not available

    if "scala" in profile.languages:
        try:  # pragma: no cover
            from .analyze.scala import analyze_scala  # pragma: no cover
            result = analyze_scala(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Scala analysis failed or tree-sitter not available

    if "lua" in profile.languages:
        try:  # pragma: no cover
            from .analyze.lua import analyze_lua  # pragma: no cover
            result = analyze_lua(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Lua analysis failed or tree-sitter not available

    if "haskell" in profile.languages:
        try:  # pragma: no cover
            from .analyze.haskell import analyze_haskell  # pragma: no cover
            result = analyze_haskell(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Haskell analysis failed or tree-sitter not available

    if "agda" in profile.languages:
        try:  # pragma: no cover
            from .analyze.agda import analyze_agda  # pragma: no cover
            result = analyze_agda(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Agda analysis failed or tree-sitter not available

    if "lean" in profile.languages:
        try:  # pragma: no cover
            from .analyze.lean import analyze_lean  # pragma: no cover
            result = analyze_lean(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Lean analysis failed or tree-sitter not available

    if "wolfram" in profile.languages:
        try:  # pragma: no cover
            from .analyze.wolfram import analyze_wolfram  # pragma: no cover
            result = analyze_wolfram(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Wolfram analysis failed or tree-sitter not available

    if "ocaml" in profile.languages:
        try:  # pragma: no cover
            from .analyze.ocaml import analyze_ocaml  # pragma: no cover
            result = analyze_ocaml(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # OCaml analysis failed or tree-sitter not available

    if "solidity" in profile.languages:
        try:  # pragma: no cover
            from .analyze.solidity import analyze_solidity  # pragma: no cover
            result = analyze_solidity(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Solidity analysis failed or tree-sitter not available

    if "csharp" in profile.languages:
        try:  # pragma: no cover
            from .analyze.csharp import analyze_csharp  # pragma: no cover
            result = analyze_csharp(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # C# analysis failed or tree-sitter not available

    if "cpp" in profile.languages:
        try:  # pragma: no cover
            from .analyze.cpp import analyze_cpp  # pragma: no cover
            result = analyze_cpp(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # C++ analysis failed or tree-sitter not available

    if "zig" in profile.languages:
        try:  # pragma: no cover
            from .analyze.zig import analyze_zig  # pragma: no cover
            result = analyze_zig(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Zig analysis failed or tree-sitter not available

    if "nix" in profile.languages:
        try:  # pragma: no cover
            from .analyze.nix import analyze_nix_files  # pragma: no cover
            result = analyze_nix_files(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Nix analysis failed or tree-sitter-nix not available

    if "elixir" in profile.languages:
        try:  # pragma: no cover
            from .analyze.elixir import analyze_elixir  # pragma: no cover
            result = analyze_elixir(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Elixir analysis failed or tree-sitter not available

    if "erlang" in profile.languages:
        try:  # pragma: no cover
            from .analyze.erlang import analyze_erlang  # pragma: no cover
            result = analyze_erlang(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Erlang analysis failed or tree-sitter not available

    if "elm" in profile.languages:
        try:  # pragma: no cover
            from .analyze.elm import analyze_elm  # pragma: no cover
            result = analyze_elm(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Elm analysis failed or tree-sitter not available

    if "fsharp" in profile.languages:
        try:  # pragma: no cover
            from .analyze.fsharp import analyze_fsharp  # pragma: no cover
            result = analyze_fsharp(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # F# analysis failed or tree-sitter not available

    if "fortran" in profile.languages:
        try:  # pragma: no cover
            from .analyze.fortran import analyze_fortran  # pragma: no cover
            result = analyze_fortran(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Fortran analysis failed or tree-sitter not available

    if "groovy" in profile.languages:
        try:  # pragma: no cover
            from .analyze.groovy import analyze_groovy  # pragma: no cover
            result = analyze_groovy(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Groovy analysis failed or tree-sitter not available

    if "julia" in profile.languages:
        try:  # pragma: no cover
            from .analyze.julia import analyze_julia  # pragma: no cover
            result = analyze_julia(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Julia analysis failed or tree-sitter not available

    if "objective-c" in profile.languages or "objc" in profile.languages:
        try:  # pragma: no cover
            from .analyze.objc import analyze_objc  # pragma: no cover
            result = analyze_objc(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Objective-C analysis failed or tree-sitter not available

    if "perl" in profile.languages:
        try:  # pragma: no cover
            from .analyze.perl import analyze_perl  # pragma: no cover
            result = analyze_perl(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Perl analysis failed or tree-sitter not available

    if "proto" in profile.languages:
        try:  # pragma: no cover
            from .analyze.proto import analyze_proto  # pragma: no cover
            result = analyze_proto(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Proto analysis failed or tree-sitter not available

    if "thrift" in profile.languages:
        try:  # pragma: no cover
            from .analyze.thrift import analyze_thrift  # pragma: no cover
            result = analyze_thrift(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Thrift analysis failed or tree-sitter not available

    if "capnp" in profile.languages:
        try:  # pragma: no cover
            from .analyze.capnp import analyze_capnp  # pragma: no cover
            result = analyze_capnp(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Cap'n Proto analysis failed or tree-sitter not available

    if "powershell" in profile.languages:
        try:  # pragma: no cover
            from .analyze.powershell import analyze_powershell  # pragma: no cover
            result = analyze_powershell(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # PowerShell analysis failed or tree-sitter not available

    if "gdscript" in profile.languages:
        try:  # pragma: no cover
            from .analyze.gdscript import analyze_gdscript  # pragma: no cover
            result = analyze_gdscript(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # GDScript analysis failed or tree-sitter not available

    if "starlark" in profile.languages:
        try:  # pragma: no cover
            from .analyze.starlark import analyze_starlark  # pragma: no cover
            result = analyze_starlark(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Starlark analysis failed or tree-sitter not available

    if "fish" in profile.languages:
        try:  # pragma: no cover
            from .analyze.fish import analyze_fish  # pragma: no cover
            result = analyze_fish(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Fish analysis failed or tree-sitter not available

    if "hlsl" in profile.languages:
        try:  # pragma: no cover
            from .analyze.hlsl import analyze_hlsl  # pragma: no cover
            result = analyze_hlsl(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # HLSL analysis failed or tree-sitter not available

    if "ada" in profile.languages:
        try:  # pragma: no cover
            from .analyze.ada import analyze_ada  # pragma: no cover
            result = analyze_ada(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Ada analysis failed or tree-sitter not available

    if "d" in profile.languages:
        try:  # pragma: no cover
            from .analyze.d_lang import analyze_d  # pragma: no cover
            result = analyze_d(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # D analysis failed or tree-sitter not available

    if "nim" in profile.languages:
        try:  # pragma: no cover
            from .analyze.nim import analyze_nim  # pragma: no cover
            result = analyze_nim(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Nim analysis failed or tree-sitter not available

    if "r" in profile.languages:
        try:  # pragma: no cover
            from .analyze.r_lang import analyze_r  # pragma: no cover
            result = analyze_r(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # R analysis failed or tree-sitter not available

    if "bash" in profile.languages or "shell" in profile.languages:
        try:  # pragma: no cover
            from .analyze.bash import analyze_bash  # pragma: no cover
            result = analyze_bash(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Bash analysis failed or tree-sitter not available

    if "sql" in profile.languages:
        try:  # pragma: no cover
            from .analyze.sql import analyze_sql  # pragma: no cover
            result = analyze_sql(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # SQL analysis failed or tree-sitter not available

    if "dockerfile" in profile.languages:
        try:  # pragma: no cover
            from .analyze.dockerfile import analyze_dockerfile  # pragma: no cover
            result = analyze_dockerfile(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Dockerfile analysis failed or tree-sitter not available

    if "hcl" in profile.languages or "terraform" in profile.languages:
        try:  # pragma: no cover
            from .analyze.hcl import analyze_hcl  # pragma: no cover
            result = analyze_hcl(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # HCL/Terraform analysis failed or tree-sitter not available

    if "vhdl" in profile.languages:
        try:  # pragma: no cover
            from .analyze.vhdl import analyze_vhdl  # pragma: no cover
            result = analyze_vhdl(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # VHDL analysis failed or tree-sitter not available

    if "verilog" in profile.languages:
        try:  # pragma: no cover
            from .analyze.verilog import analyze_verilog  # pragma: no cover
            result = analyze_verilog(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Verilog analysis failed or tree-sitter not available

    if "clojure" in profile.languages:
        try:  # pragma: no cover
            from .analyze.clojure import analyze_clojure  # pragma: no cover
            result = analyze_clojure(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Clojure analysis failed or tree-sitter not available

    if "dart" in profile.languages:
        try:  # pragma: no cover
            from .analyze.dart import analyze_dart  # pragma: no cover
            result = analyze_dart(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # Dart analysis failed or tree-sitter not available

    if "cobol" in profile.languages:
        try:  # pragma: no cover
            from .analyze.cobol import analyze_cobol  # pragma: no cover
            result = analyze_cobol(repo_root)  # pragma: no cover
            all_symbols.extend(result.symbols)  # pragma: no cover
            all_edges.extend(result.edges)  # pragma: no cover
        except Exception:  # pragma: no cover
            pass  # COBOL analysis failed or tree-sitter not available

    # Filter out test files if requested (significant speedup for large codebases)
    if exclude_tests:
        # Filter symbols from test files
        filtered_symbols = [s for s in all_symbols if not _is_test_path(s.path)]
        # Get IDs of remaining symbols for edge filtering
        remaining_ids = {s.id for s in filtered_symbols}
        # Filter edges to only include those between remaining symbols
        filtered_edges = [
            e for e in all_edges
            if getattr(e, "src", None) in remaining_ids
            and getattr(e, "dst", None) in remaining_ids
        ]
        all_symbols = filtered_symbols
        all_edges = filtered_edges

    # Apply supply chain classification to all symbols
    package_roots = detect_package_roots(repo_root)
    for symbol in all_symbols:
        file_path = repo_root / symbol.path
        classification = classify_file(file_path, repo_root, package_roots)
        symbol.supply_chain_tier = classification.tier.value
        symbol.supply_chain_reason = classification.reason

    return all_symbols, all_edges


def _format_entrypoints(
    entrypoints: list[Entrypoint],
    symbols: list[Symbol],
    repo_root: Path,
    max_entries: int = 20,
) -> str:
    """Format detected entry points as a Markdown section."""
    if not entrypoints:
        return ""

    # Build symbol lookup for path info
    symbol_by_id = {s.id: s for s in symbols}

    # Sort by confidence (highest first)
    sorted_eps = sorted(entrypoints, key=lambda e: -e.confidence)

    lines = ["## Entry Points", ""]

    for ep in sorted_eps[:max_entries]:
        sym = symbol_by_id.get(ep.symbol_id)
        if sym:
            rel_path = sym.path
            if rel_path.startswith(str(repo_root)):
                rel_path = rel_path[len(str(repo_root)) + 1:]
            lines.append(f"- `{sym.name}` ({ep.label}) — `{rel_path}`")
        else:
            lines.append(f"- `{ep.symbol_id}` ({ep.label})")

    if len(entrypoints) > max_entries:
        lines.append(f"- ... and {len(entrypoints) - max_entries} more entry points")

    return "\n".join(lines)


def _select_symbols_two_phase(
    by_file: dict[str, list[Symbol]],
    centrality: dict[str, float],
    file_scores: dict[str, float],
    max_symbols: int,
    entrypoint_files: set[str],
    max_files: int = 20,
    coverage_fraction: float = 0.33,
    diminishing_alpha: float = 0.7,
    language_proportional: bool = True,
) -> list[tuple[str, Symbol]]:
    """Select symbols using two-phase policy for breadth + depth.

    Phase 1 (coverage-first): Pick the best symbol from each eligible file
    in rounds, ensuring representation across subsystems.

    When language_proportional=True, Phase 1 uses language-stratified selection:
    symbols are allocated proportionally by language based on symbol counts,
    with a minimum guarantee of 1 slot per language.

    Phase 2 (diminishing-returns greedy): Fill remaining slots using marginal
    utility that penalizes repeated picks from the same file.

    Args:
        by_file: Symbols grouped by file path, sorted by centrality within each file.
        centrality: Centrality scores for each symbol ID.
        file_scores: File importance scores (sum of top-K).
        max_symbols: Total symbol budget.
        entrypoint_files: Set of file paths containing entrypoints (always included).
        max_files: Maximum number of files to consider.
        coverage_fraction: Fraction of budget for phase 1 (coverage).
        diminishing_alpha: Penalty factor for repeated file picks in phase 2.
        language_proportional: If True, use language-stratified selection in Phase 1.

    Returns:
        List of (file_path, symbol) tuples in selection order.
    """
    import heapq

    # Gate eligible files: top N by file_score, plus entrypoint files
    sorted_files = sorted(file_scores.keys(), key=lambda f: -file_scores.get(f, 0))
    eligible_files = set(sorted_files[:max_files]) | entrypoint_files

    # Filter by_file to eligible files only
    eligible_by_file = {f: syms for f, syms in by_file.items() if f in eligible_files}

    if not eligible_by_file:  # pragma: no cover
        return []

    # Track per-file state: next symbol index and pick count
    file_state: dict[str, dict] = {
        f: {"next_idx": 0, "picks": 0, "symbols": syms}
        for f, syms in eligible_by_file.items()
    }

    selected: list[tuple[str, Symbol]] = []

    # Phase 1: Coverage-first - pick best symbol from each file in rounds
    coverage_budget = int(max_symbols * coverage_fraction)
    coverage_budget = min(coverage_budget, len(eligible_by_file))  # Cap at file count

    if language_proportional:
        # Language-stratified Phase 1: allocate slots by language proportion
        lang_groups = _group_files_by_language(eligible_by_file)
        lang_budgets = _allocate_language_budget(lang_groups, coverage_budget)

        # Order languages by budget (largest first) for fair distribution
        sorted_langs = sorted(lang_budgets.keys(), key=lambda lang: -lang_budgets[lang])

        for lang in sorted_langs:
            lang_budget = lang_budgets[lang]
            if lang not in lang_groups:  # pragma: no cover
                continue

            # Order files within this language by file_score
            lang_files = sorted(
                lang_groups[lang].keys(),
                key=lambda f: -file_scores.get(f, 0)
            )

            # Pick symbols from this language's files
            lang_selected = 0
            for file_path in lang_files:
                if lang_selected >= lang_budget:
                    break
                if len(selected) >= coverage_budget:  # pragma: no cover
                    break
                state = file_state[file_path]
                if state["next_idx"] < len(state["symbols"]):
                    sym = state["symbols"][state["next_idx"]]
                    selected.append((file_path, sym))
                    state["next_idx"] += 1
                    state["picks"] += 1
                    lang_selected += 1
    else:
        # Original behavior: order files by file_score for round-robin
        phase1_files = sorted(
            eligible_by_file.keys(),
            key=lambda f: -file_scores.get(f, 0)
        )

        for file_path in phase1_files:
            if len(selected) >= coverage_budget:
                break
            state = file_state[file_path]
            if state["next_idx"] < len(state["symbols"]):
                sym = state["symbols"][state["next_idx"]]
                selected.append((file_path, sym))
                state["next_idx"] += 1
                state["picks"] += 1

    # Phase 2: Diminishing-returns greedy fill
    remaining_budget = max_symbols - len(selected)

    if remaining_budget > 0:
        # Build priority queue with marginal utility
        # marginal = score / (1 + alpha * picks_from_file)
        pq: list[tuple[float, str, int]] = []  # (-marginal, file_path, sym_idx)

        for file_path, state in file_state.items():
            idx = state["next_idx"]
            if idx < len(state["symbols"]):
                sym = state["symbols"][idx]
                score = centrality.get(sym.id, 0)
                picks = state["picks"]
                marginal = score / (1 + diminishing_alpha * picks)
                heapq.heappush(pq, (-marginal, file_path, idx))

        while len(selected) < max_symbols and pq:
            neg_marginal, file_path, sym_idx = heapq.heappop(pq)
            state = file_state[file_path]

            # Check if this entry is stale (index already advanced)
            if sym_idx != state["next_idx"]:  # pragma: no cover
                continue

            sym = state["symbols"][sym_idx]
            selected.append((file_path, sym))
            state["next_idx"] += 1
            state["picks"] += 1

            # Push next symbol from this file if available
            next_idx = state["next_idx"]
            if next_idx < len(state["symbols"]):
                next_sym = state["symbols"][next_idx]
                score = centrality.get(next_sym.id, 0)
                picks = state["picks"]
                marginal = score / (1 + diminishing_alpha * picks)
                heapq.heappush(pq, (-marginal, file_path, next_idx))

    return selected


def _format_symbols(
    symbols: list[Symbol],
    edges: list,
    repo_root: Path,
    max_symbols: int = 100,
    first_party_priority: bool = True,
    entrypoint_files: set[str] | None = None,
    max_symbols_per_file: int = 5,
    docstrings: dict[str, str] | None = None,
    signatures: dict[str, str] | None = None,
    language_proportional: bool = True,
) -> str:
    """Format key symbols (functions, classes) as a Markdown section.

    Uses a two-phase selection policy for balanced coverage:
    1. Coverage-first: Pick best symbol from each top file
    2. Diminishing-returns: Fill remaining slots with marginal utility

    When language_proportional=True, Phase 1 uses language-stratified selection
    to ensure multi-language projects have proportional representation.

    File ordering uses sum-of-top-K centrality scores (density metric)
    rather than single-max, for more stable and intuitive ranking.

    Per-file rendering is capped to avoid visual monopoly, with a
    summary line for additional selected symbols.

    Args:
        symbols: List of symbols from analysis.
        edges: List of edges from analysis.
        repo_root: Repository root path.
        max_symbols: Maximum symbols to include.
        first_party_priority: If True (default), boost first-party symbols.
        entrypoint_files: Set of file paths containing entrypoints (preserved).
        max_symbols_per_file: Max symbols to render per file (compression).
        docstrings: Optional dict mapping symbol IDs to docstring summaries.
        signatures: Optional dict mapping symbol IDs to function signatures.
        language_proportional: If True, use language-stratified selection.
    """
    if docstrings is None:
        docstrings = {}
    if signatures is None:
        signatures = {}
    if not symbols:
        return ""

    if entrypoint_files is None:
        entrypoint_files = set()

    # Filter to meaningful symbol kinds, exclude test files and derived artifacts
    # Include OOP kinds (function, class, method) plus language-specific equivalents:
    # - Nix: binding, derivation, input (core abstractions)
    # - Terraform/HCL: resource, data, module, variable, output, provider, local
    # - Elixir/Erlang: module, macro, record, type
    # - Elm/F#: module, type, port, record, union, value
    # - SQL: table, view, procedure, trigger
    # - Dockerfile: stage
    # - Lean: theorem, structure, inductive, instance
    # - Agda: data (algebraic data types)
    # - Fortran/COBOL: program, subroutine
    # - VHDL: entity, architecture, component
    # - Other: struct, enum, trait, interface, protocol, object
    KEY_SYMBOL_KINDS = frozenset({
        # OOP languages
        "function", "class", "method", "constructor",
        # Structs and data types
        "struct", "structure", "enum", "type", "record", "union", "abstract",
        # Interfaces and traits
        "interface", "trait", "protocol",
        # Modules and namespaces
        "module", "object", "namespace", "instance",
        # Nix
        "binding", "derivation", "input",
        # Terraform/HCL
        "resource", "data", "variable", "output", "provider", "local",
        # Elixir/Erlang
        "macro",
        # Elm
        "port",
        # SQL
        "table", "view", "procedure", "trigger",
        # Dockerfile
        "stage",
        # F#
        "value",
        # Lean (theorem prover)
        "theorem", "inductive",
        # Fortran/COBOL
        "program", "subroutine",
        # VHDL (hardware design)
        "entity", "architecture", "component",
    })
    key_symbols = [
        s for s in symbols
        if s.kind in KEY_SYMBOL_KINDS
        and not _is_test_path(s.path)
        and "test_" not in s.name  # Exclude test functions
        and s.supply_chain_tier != 4  # Exclude derived artifacts (bundles, etc.)
    ]

    # Build lookup: symbol ID -> path (for filtering edges by source)
    symbol_path_by_id = {s.id: s.path for s in symbols}

    # Filter edges: exclude edges originating from test files
    production_edges = [
        e for e in edges
        if not _is_test_path(symbol_path_by_id.get(getattr(e, 'src', ''), ''))
    ]

    if not key_symbols:
        return ""

    # Compute centrality scores using only production edges
    raw_centrality = compute_centrality(key_symbols, production_edges)

    # Apply tier-based weighting (first-party symbols boosted) if enabled
    if first_party_priority:
        centrality = apply_tier_weights(raw_centrality, key_symbols)
    else:
        centrality = raw_centrality

    # Sort by weighted centrality (most called first), then by name for stability
    key_symbols.sort(key=lambda s: (-centrality.get(s.id, 0), s.name))

    # Group by file, preserving centrality order within files
    by_file: dict[str, list[Symbol]] = {}
    for s in key_symbols:
        rel_path = s.path
        if rel_path.startswith(str(repo_root)):
            rel_path = rel_path[len(str(repo_root)) + 1:]
        by_file.setdefault(rel_path, []).append(s)

    # Compute file scores using sum-of-top-K (B3: density metric)
    file_scores = compute_file_scores(by_file, centrality, top_k=3)

    # Normalize entrypoint file paths
    normalized_ep_files: set[str] = set()
    repo_root_str = str(repo_root)
    for ep_path in entrypoint_files:
        if ep_path.startswith(repo_root_str):
            normalized_ep_files.add(ep_path[len(repo_root_str) + 1:])
        else:  # pragma: no cover
            normalized_ep_files.add(ep_path)

    # Two-phase selection (B1)
    selected = _select_symbols_two_phase(
        by_file=by_file,
        centrality=centrality,
        file_scores=file_scores,
        max_symbols=max_symbols,
        entrypoint_files=normalized_ep_files,
        language_proportional=language_proportional,
    )

    if not selected:  # pragma: no cover
        return ""

    # Group selected symbols by file for rendering
    selected_by_file: dict[str, list[Symbol]] = {}
    for file_path, sym in selected:
        selected_by_file.setdefault(file_path, []).append(sym)

    # Order files by file_score (B3), then alphabetically for tie-breaking
    sorted_files = sorted(
        selected_by_file.keys(),
        key=lambda f: (-file_scores.get(f, 0), f)
    )

    # Find max centrality for star threshold
    max_centrality = max(centrality.values()) if centrality else 1.0
    star_threshold = max_centrality * 0.5

    lines = ["## Key Symbols", ""]
    lines.append("*★ = centrality ≥ 50% of max*")
    lines.append("")

    # Track function names already rendered for deduplication
    # Functions like _node_text() appear in many files - show only first occurrence
    rendered_function_names: set[str] = set()

    # Track duplicate counts for summary at end
    # Maps function name -> number of times it appeared across files
    function_occurrence_count: dict[str, int] = {}

    total_rendered = 0
    for file_path in sorted_files:
        file_symbols = selected_by_file[file_path]

        lines.append(f"### `{file_path}`")

        # Render up to max_symbols_per_file (B2: compression)
        rendered_count = 0
        deduped_count = 0  # Track skipped duplicates
        for sym in file_symbols[:max_symbols_per_file]:
            # Deduplicate: skip functions with same name already shown in other files
            # This reduces noise from utility functions like _node_text() that appear
            # in many analyzer files with identical implementations
            if sym.kind in ("function", "method") and sym.name in rendered_function_names:
                deduped_count += 1
                # Track occurrence for summary
                function_occurrence_count[sym.name] = function_occurrence_count.get(sym.name, 1) + 1
                continue

            kind_label = sym.kind
            score = centrality.get(sym.id, 0)
            star = " ★" if score >= star_threshold else ""
            docstring = docstrings.get(sym.id)
            signature = signatures.get(sym.id)
            # Build symbol display name (with signature for functions)
            if signature and sym.kind in ("function", "method"):
                display_name = f"{sym.name}{signature}"
            else:
                display_name = sym.name
            if docstring:
                lines.append(f"- `{display_name}` ({kind_label}){star} — {docstring}")
            else:
                lines.append(f"- `{display_name}` ({kind_label}){star}")

            # Track rendered function names for deduplication
            if sym.kind in ("function", "method"):
                rendered_function_names.add(sym.name)

            rendered_count += 1
            total_rendered += 1

        # If all symbols in this file were deduplicated, remove the empty header
        if rendered_count == 0:  # pragma: no cover
            # Remove the "### `file_path`" line we added
            lines.pop()
            continue

        # Summary line for remaining symbols in this file (B2)
        # Don't count deduped symbols as "remaining" - they're intentionally hidden
        remaining_in_file = len(file_symbols) - rendered_count - deduped_count
        if remaining_in_file > 0:
            # Show stats for compressed symbols (excluding deduped ones)
            remaining_syms = [
                s for s in file_symbols[max_symbols_per_file:]
                if not (s.kind in ("function", "method") and s.name in rendered_function_names)
            ]
            remaining_scores = [centrality.get(s.id, 0) for s in remaining_syms]
            if remaining_scores:
                top_score = max(remaining_scores)
                lines.append(f"  (... +{remaining_in_file} more, top score: {top_score:.2f})")

        lines.append("")  # Blank line between files

    # Global summary of unselected symbols
    total_selected = len(selected)
    total_candidates = len(key_symbols)
    unselected = total_candidates - total_selected
    if unselected > 0:
        lines.append(f"(... and {unselected} more symbols across {len(by_file) - len(selected_by_file)} other files)")

    # Summary of deduplicated utility functions (show top duplicates)
    if function_occurrence_count:
        # Sort by occurrence count descending, show top 5
        sorted_dupes = sorted(
            function_occurrence_count.items(),
            key=lambda x: -x[1]
        )[:5]
        if sorted_dupes:
            lines.append("")
            lines.append("The following symbols, for brevity shown only once above, would have appeared multiple times:")
            for i, (name, count) in enumerate(sorted_dupes):
                omitted = count - 1  # count includes the one shown
                if i == 0:
                    # First: full format
                    lines.append(f"- `{name}` - we omitted {omitted} appearances of `{name}`")
                elif i == 1:
                    # Second: medium format
                    lines.append(f"- `{name}` - we omitted {omitted} appearances")
                else:
                    # Third+: short format
                    lines.append(f"- `{name}` - {omitted} omitted")

    return "\n".join(lines)


def generate_sketch(
    repo_root: Path,
    max_tokens: Optional[int] = None,
    exclude_tests: bool = False,
    first_party_priority: bool = True,
    extra_excludes: Optional[List[str]] = None,
    config_extraction_mode: ConfigExtractionMode = ConfigExtractionMode.HEURISTIC,
    verbose: bool = False,
    max_config_files: int = 15,
    fleximax_lines: int = 100,
    max_chunk_chars: int = 800,
    language_proportional: bool = True,
) -> str:
    """Generate a token-budgeted Markdown sketch of the repository.

    The sketch progressively includes content to fill the token budget:
    1. Header with language breakdown and LOC (always included)
    2. Directory structure
    3. Detected frameworks
    4. Configuration metadata (extracted from package.json, go.mod, etc.)
    5. Domain vocabulary
    6. Source files (for medium budgets)
    7. Entry points from static analysis (for larger budgets)
    8. Key symbols from static analysis (for large budgets)
    9. All files (for very large budgets)

    Args:
        repo_root: Path to the repository root.
        max_tokens: Target tokens for output. If None, returns minimal sketch.
        exclude_tests: If True, skip analyzing test files for faster performance.
        first_party_priority: If True (default), boost first-party symbols in
            ranking. Set False to use raw centrality scores.
        extra_excludes: Additional exclude patterns beyond DEFAULT_EXCLUDES.
            Useful for excluding project-specific files (e.g., "*.json", "vendor").
        config_extraction_mode: Mode for extracting config file metadata.
            - HEURISTIC (default): Fast pattern-based extraction
            - EMBEDDING: Semantic selection using UnixCoder + question centroid
            - HYBRID: Heuristics first, then embeddings for remaining budget
        verbose: If True, print progress messages to stderr.
        max_config_files: Maximum config files to process (embedding mode).
        fleximax_lines: Base sample size for log-scaled line sampling.
        max_chunk_chars: Maximum characters per chunk for embedding.
        language_proportional: If True, use language-stratified symbol selection
            to ensure multi-language projects have proportional representation.

    Returns:
        Markdown-formatted sketch string.
    """
    import sys
    import time

    def _log(msg: str) -> None:
        if verbose:  # pragma: no cover
            print(f"[sketch] {msg}", file=sys.stderr)

    t0 = time.time()
    _log("Starting sketch generation...")

    repo_root = Path(repo_root).resolve()
    _log(f"Detecting profile for {repo_root.name}...")
    profile = detect_profile(repo_root, extra_excludes=extra_excludes)
    _log(f"Profile detected in {time.time() - t0:.1f}s")
    repo_name = _get_repo_name(repo_root)

    # Build base sections (always included)
    sections = []

    # Section 1: Header (always included, highest priority)
    # Include project description from README if available
    readme_desc = _extract_readme_description(repo_root)
    if readme_desc:
        header = (
            f"# {repo_name}\n\n"
            f"{readme_desc}\n\n"
            f"## Overview\n{_format_language_stats(profile)}"
        )
    else:
        header = f"# {repo_name}\n\n## Overview\n{_format_language_stats(profile)}"
    sections.append(header)

    # Section 2: Structure
    structure = _format_structure(repo_root, extra_excludes=extra_excludes)
    if structure:
        sections.append(structure)

    # Section 3: Frameworks
    frameworks = _format_frameworks(profile)
    if frameworks:
        sections.append(frameworks)

    # Section 3.25: Tests (static summary - count and frameworks)
    test_summary_section = _format_test_summary(repo_root)
    if test_summary_section:
        sections.append(test_summary_section)

    # Section 3.5: Configuration (extracted metadata from config files)
    # This section is high value for answering project metadata questions
    # (e.g., "what version of TypeScript?", "what license?", "what database?")
    t_config = time.time()
    _log(f"Extracting config ({config_extraction_mode.value})...")
    config_info = _extract_config_info(
        repo_root,
        mode=config_extraction_mode,
        max_config_files=max_config_files,
        fleximax_lines=fleximax_lines,
        max_chunk_chars=max_chunk_chars,
    )
    _log(f"Config extracted in {time.time() - t_config:.1f}s")
    config_section = _format_config_section(config_info)
    if config_section:
        sections.append(config_section)

    # Section 3.75: Domain Vocabulary (only for medium+ budgets)
    if max_tokens is None or max_tokens >= 500:
        vocab_terms = _extract_domain_vocabulary(repo_root, profile)
        vocabulary = _format_vocabulary(vocab_terms)
        if vocabulary:
            sections.append(vocabulary)

    # Combine base sections
    base_sketch = "\n\n".join(sections)
    base_tokens = estimate_tokens(base_sketch)

    # If no budget or budget is small, return base sketch (possibly truncated)
    if max_tokens is None:
        return base_sketch

    if max_tokens <= base_tokens:
        return truncate_to_tokens(base_sketch, max_tokens)

    # We have room to expand - calculate remaining budget
    remaining_tokens = max_tokens - base_tokens

    # Collect source files for expansion
    source_files = _collect_source_files(repo_root, profile)

    # Estimate tokens per file item
    # Typical line: "- `path/to/long/filename.py`" is ~50 chars = ~12 tokens
    tokens_per_file = 12

    # Estimate tokens per entry point or symbol item with docstring/signature
    # Typical line: "- `func(x: int, y: List[str]) -> Dict[str, Any]` (method) — Does X."
    # is ~100-150 chars = ~25-38 tokens. Use realistic estimate based on qwix data.
    tokens_per_symbol = 35

    # Section 4: Source files (if we have budget >= 50 tokens remaining)
    if remaining_tokens > 50 and source_files:
        # Use up to half of remaining budget for source files at small budgets
        # Scale down the fraction as budget grows (files are less important)
        # Reserve space for Entry Points and Key Symbols sections
        if remaining_tokens < 300:
            budget_for_files = (remaining_tokens * 2) // 3  # 66% at small budgets
        else:
            # At larger budgets, limit files to 25% to leave room for analysis
            budget_for_files = remaining_tokens // 4  # 25% at larger budgets
        max_source_files = max(5, budget_for_files // tokens_per_file)

        source_section = _format_source_files(
            repo_root, source_files, max_files=max_source_files
        )
        if source_section:
            sections.append(source_section)

        # Recalculate remaining budget
        current_sketch = "\n\n".join(sections)
        current_tokens = estimate_tokens(current_sketch)
        remaining_tokens = max_tokens - current_tokens

    # For larger budgets, run static analysis
    symbols: list[Symbol] = []
    edges: list = []
    if remaining_tokens > 100:
        symbols, edges = _run_analysis(repo_root, profile, exclude_tests=exclude_tests)

    # Section 5: Entry points (if we have analysis results and budget)
    # Track entrypoint files for B4: preserve in Key Symbols
    entrypoint_files: set[str] = set()
    entrypoints: list[Entrypoint] = []

    if remaining_tokens > 50 and symbols:
        entrypoints = detect_entrypoints(symbols, edges)
        if entrypoints:
            # Build symbol lookup for extracting file paths
            symbol_by_id = {s.id: s for s in symbols}

            # Extract file paths from entrypoints (B4)
            for ep in entrypoints:
                sym = symbol_by_id.get(ep.symbol_id)
                if sym:
                    entrypoint_files.add(sym.path)

            # Entry points are high value, give them space
            budget_for_eps = remaining_tokens // 3
            max_eps = max(5, budget_for_eps // tokens_per_symbol)

            ep_section = _format_entrypoints(
                entrypoints, symbols, repo_root, max_entries=max_eps
            )
            if ep_section:
                sections.append(ep_section)

            # Recalculate remaining budget
            current_sketch = "\n\n".join(sections)
            current_tokens = estimate_tokens(current_sketch)
            remaining_tokens = max_tokens - current_tokens

    # Section 6: Key symbols
    # IMPORTANT: Minimum Key Symbols guarantee
    # Always include at least MIN_KEY_SYMBOLS symbols when analysis produces results.
    # This addresses the issue where some projects (qwix, marlin, guacamole-client)
    # had 0 Key Symbols at 1k budget because budget was exhausted earlier.
    # Key Symbols is the most valuable section for code understanding, so we
    # guarantee its presence even if it means slight budget overage.
    MIN_KEY_SYMBOLS = 5

    if symbols:
        # Calculate symbol budget based on remaining tokens
        if remaining_tokens > 200:
            # Normal case: use most of remaining budget for symbols
            budget_for_symbols = (remaining_tokens * 4) // 5  # 80% of remaining
            max_symbols = max(10, budget_for_symbols // tokens_per_symbol)
        else:
            # Budget-constrained case: guarantee minimum symbols anyway
            # This ensures Key Symbols section appears for every analyzable project
            max_symbols = MIN_KEY_SYMBOLS

        # Extract docstrings for Python symbols
        docstrings = _extract_python_docstrings(repo_root, symbols)
        # Get signatures from Symbol.signature field (now includes all languages)
        signatures = {s.id: s.signature for s in symbols if s.signature}

        symbols_section = _format_symbols(
            symbols,
            edges,
            repo_root,
            max_symbols=max_symbols,
            first_party_priority=first_party_priority,
            entrypoint_files=entrypoint_files,  # B4: preserve entrypoint files
            docstrings=docstrings,
            signatures=signatures,
            language_proportional=language_proportional,
        )
        if symbols_section:
            sections.append(symbols_section)

            # Recalculate remaining budget
            current_sketch = "\n\n".join(sections)
            current_tokens = estimate_tokens(current_sketch)
            remaining_tokens = max_tokens - current_tokens

    # Section 7: All files (if we still have budget after everything else)
    if remaining_tokens > 50:
        budget_for_files = remaining_tokens - 10
        max_all_files = max(1, budget_for_files // tokens_per_file)

        all_files_section = _format_all_files(repo_root, max_files=max_all_files)
        if all_files_section:
            sections.append(all_files_section)

    # Combine all sections
    full_sketch = "\n\n".join(sections)

    # Final truncation to ensure we don't exceed budget
    return truncate_to_tokens(full_sketch, max_tokens)
