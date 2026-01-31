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
import warnings
from enum import Enum
from pathlib import Path
from typing import List, Optional

from .discovery import find_files, DEFAULT_EXCLUDES
from .profile import detect_profile, RepoProfile
from .ir import Symbol, Edge
from .entrypoints import detect_entrypoints, Entrypoint
from .datamodels import detect_datamodels, DataModel
from .ranking import (
    compute_centrality,
    apply_tier_weights,
    compute_file_scores,
    _is_test_path,
    compute_transitive_test_coverage,
    compute_raw_in_degree,
    compute_symbol_importance_density,
    compute_symbol_mention_centrality_batch,
)
from .selection.language_proportional import (
    allocate_language_budget as _allocate_language_budget,
    group_files_by_language as _group_files_by_language,
)
from .selection.token_budget import (
    estimate_tokens,
    truncate_to_tokens,
)
from .taxonomy import SOURCE_EXTENSIONS, is_additional_file_candidate
from dataclasses import dataclass


@dataclass
class SketchStats:
    """Statistics about sketch representativeness.

    Tracks what fraction of the codebase's "importance" is captured in each
    sketch section, using two metrics:
    - Symbol mass: (summed in-degree for symbols) / (total repo in-degree) * 100
    - Confidence mass: (summed confidence) / (total confidence) * 100 (for framework concepts)
    """

    # Token budget used
    token_budget: int = 0

    # Total repo metrics (denominators)
    total_in_degree: int = 0
    total_entrypoint_confidence: float = 0.0
    total_datamodel_confidence: float = 0.0

    # Section metrics (numerators) - in-degree sums
    key_symbols_in_degree: int = 0
    source_files_in_degree: int = 0
    additional_files_in_degree: int = 0
    source_files_content_in_degree: int = 0
    additional_files_content_in_degree: int = 0

    # Section metrics (numerators) - confidence sums
    entrypoints_confidence: float = 0.0
    datamodels_confidence: float = 0.0

    # Flags for which sections are present
    has_key_symbols: bool = False
    has_source_files: bool = False
    has_additional_files: bool = False
    has_source_files_content: bool = False
    has_additional_files_content: bool = False
    has_entrypoints: bool = False
    has_datamodels: bool = False

    def symbol_mass(self, in_degree_sum: int) -> float:
        """Compute symbol mass percentage."""
        if self.total_in_degree == 0:
            return 0.0
        return (in_degree_sum / self.total_in_degree) * 100

    def confidence_mass(self, confidence_sum: float, total: float) -> float:
        """Compute confidence mass percentage."""
        if total == 0:
            return 0.0
        return (confidence_sum / total) * 100


def display_representativeness_table(
    stats: SketchStats,
    stats_4x: SketchStats,
    stats_16x: SketchStats,
    console: Optional["Console"] = None,  # noqa: F821 - Console imported lazily
) -> None:
    """Display a Rich table showing sketch representativeness.

    Shows what fraction of the codebase's "importance" is captured in each
    sketch section, comparing the requested budget vs. 4x and 16x budgets.
    Using 4x/16x (instead of 2x) reveals when large files start fitting.

    Args:
        stats: Stats from sketch at requested token budget.
        stats_4x: Stats from sketch at 4x the token budget.
        stats_16x: Stats from sketch at 16x the token budget.
        console: Optional Rich console to use. If None, creates one.
    """
    from rich.console import Console
    from rich.table import Table

    if console is None:
        console = Console(stderr=True)

    # Create the table
    table = Table(
        title="How Representative Is This Sketch?",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Section", style="cyan")
    table.add_column(f"{stats.token_budget:,}t", justify="right")
    table.add_column(f"{stats_4x.token_budget:,}t", justify="right", style="dim")
    table.add_column(f"{stats_16x.token_budget:,}t", justify="right", style="dim")
    table.add_column("Metric", style="dim")

    def fmt_pct(val: float) -> str:
        """Format percentage with appropriate precision."""
        if val == 0:
            return "-"
        if val >= 10:
            return f"{val:.0f}%"
        return f"{val:.1f}%"

    def any_has(*flags: bool) -> bool:
        """Check if any stats object has this section."""
        return any(flags)

    # Entry Points (confidence mass)
    if any_has(stats.has_entrypoints, stats_4x.has_entrypoints, stats_16x.has_entrypoints):
        pct1 = stats.confidence_mass(stats.entrypoints_confidence, stats.total_entrypoint_confidence)
        pct4 = stats_4x.confidence_mass(stats_4x.entrypoints_confidence, stats_4x.total_entrypoint_confidence)
        pct16 = stats_16x.confidence_mass(stats_16x.entrypoints_confidence, stats_16x.total_entrypoint_confidence)
        table.add_row("Entry Points", fmt_pct(pct1), fmt_pct(pct4), fmt_pct(pct16), "confidence mass")

    # Data Models (confidence mass)
    if any_has(stats.has_datamodels, stats_4x.has_datamodels, stats_16x.has_datamodels):
        pct1 = stats.confidence_mass(stats.datamodels_confidence, stats.total_datamodel_confidence)
        pct4 = stats_4x.confidence_mass(stats_4x.datamodels_confidence, stats_4x.total_datamodel_confidence)
        pct16 = stats_16x.confidence_mass(stats_16x.datamodels_confidence, stats_16x.total_datamodel_confidence)
        table.add_row("Data Models", fmt_pct(pct1), fmt_pct(pct4), fmt_pct(pct16), "confidence mass")

    # Source Files (symbol mass)
    if any_has(stats.has_source_files, stats_4x.has_source_files, stats_16x.has_source_files):
        pct1 = stats.symbol_mass(stats.source_files_in_degree)
        pct4 = stats_4x.symbol_mass(stats_4x.source_files_in_degree)
        pct16 = stats_16x.symbol_mass(stats_16x.source_files_in_degree)
        table.add_row("Source Files", fmt_pct(pct1), fmt_pct(pct4), fmt_pct(pct16), "symbol mass")

    # Key Symbols (symbol mass)
    if any_has(stats.has_key_symbols, stats_4x.has_key_symbols, stats_16x.has_key_symbols):
        pct1 = stats.symbol_mass(stats.key_symbols_in_degree)
        pct4 = stats_4x.symbol_mass(stats_4x.key_symbols_in_degree)
        pct16 = stats_16x.symbol_mass(stats_16x.key_symbols_in_degree)
        table.add_row("Key Symbols", fmt_pct(pct1), fmt_pct(pct4), fmt_pct(pct16), "symbol mass")

    # Additional Files (symbol mass)
    if any_has(stats.has_additional_files, stats_4x.has_additional_files, stats_16x.has_additional_files):
        pct1 = stats.symbol_mass(stats.additional_files_in_degree)
        pct4 = stats_4x.symbol_mass(stats_4x.additional_files_in_degree)
        pct16 = stats_16x.symbol_mass(stats_16x.additional_files_in_degree)
        table.add_row("Additional Files", fmt_pct(pct1), fmt_pct(pct4), fmt_pct(pct16), "symbol mass")

    # Source Files Content (symbol mass) - only if --with-source was used
    if any_has(stats.has_source_files_content, stats_4x.has_source_files_content, stats_16x.has_source_files_content):
        pct1 = stats.symbol_mass(stats.source_files_content_in_degree)
        pct4 = stats_4x.symbol_mass(stats_4x.source_files_content_in_degree)
        pct16 = stats_16x.symbol_mass(stats_16x.source_files_content_in_degree)
        table.add_row("Source Files Content", fmt_pct(pct1), fmt_pct(pct4), fmt_pct(pct16), "symbol mass")

    # Additional Files Content (symbol mass) - only if --with-source was used
    if any_has(stats.has_additional_files_content, stats_4x.has_additional_files_content, stats_16x.has_additional_files_content):
        pct1 = stats.symbol_mass(stats.additional_files_content_in_degree)
        pct4 = stats_4x.symbol_mass(stats_4x.additional_files_content_in_degree)
        pct16 = stats_16x.symbol_mass(stats_16x.additional_files_content_in_degree)
        table.add_row("Additional Files Content", fmt_pct(pct1), fmt_pct(pct4), fmt_pct(pct16), "symbol mass")

    # Only display if there's at least one row
    if table.row_count > 0:
        console.print(table)


# Boilerplate patterns to exclude from Additional Files section (ADR-0004 Phase 4)
# Binary files are now filtered by is_additional_file_candidate() which excludes
# files without CONFIG or DOCUMENTATION role. This list catches low-value boilerplate
# that would otherwise pass the role check.
ADDITIONAL_FILES_EXCLUDES = [
    # License/legal boilerplate (has DOCUMENTATION role but low value)
    "LICENSE",
    "LICENSE.*",
    "LICENCE",
    "LICENCE.*",
    "COPYING",
    "COPYING.*",
    "COPYRIGHT",
    "COPYRIGHT.*",
    "PATENTS",
    "NOTICE",
    "NOTICE.*",
    # Hypergumbo output artifacts
    "hypergumbo.results.json",
    "hypergumbo.results.*.json",
    ".hypergumbo_cache",
    # Git/repo metadata (has CONFIG role but low value)
    ".gitignore",
    ".gitattributes",
    ".gitmodules",
    ".mailmap",
    "CODEOWNERS",
    ".editorconfig",
    # IDE/editor config (has CONFIG role but low value)
    ".vscode",
    ".idea",
    "*.sublime-*",
    # Minified files (bloat, unreadable)
    "*.min.js",
    "*.min.css",
    "*.bundle.js",
    "*.bundle.css",
    # Source maps (generated, not useful context)
    "*.map",
    # Generated documentation cruft
    ".nojekyll",
    ".buildinfo",
    # Coverage reports
    "coverage-report.txt",
    ".coverage",
    "coverage.xml",
]


class ConfigExtractionMode(Enum):
    """Mode for extracting config file content.

    - HEURISTIC: Extract known fields using pattern matching (fast, no model)
    - EMBEDDING: Use semantic similarity to prototype questions (requires model)
    - HYBRID: Extract known fields first, then use embeddings for remaining budget
    """

    HEURISTIC = "heuristic"
    EMBEDDING = "embedding"
    HYBRID = "hybrid"


class SketchProgress:  # pragma: no cover
    """Progress reporter for sketch generation with ETA calculation.

    Tracks progress through phases and estimates time remaining based on
    historical timing of completed phases.

    This class is marked no cover because it's UI-only output code.
    """

    PHASES = [  # noqa: RUF012
        ("profile", "Detecting profile", 0.05),
        ("readme", "Extracting README", 0.10),
        ("structure", "Building structure", 0.15),
        ("frameworks", "Detecting frameworks", 0.20),
        ("tests", "Analyzing tests", 0.25),
        ("config", "Extracting config", 0.45),  # Config is often slowest
        ("analysis", "Running static analysis", 0.70),
        ("symbols", "Ranking symbols", 0.80),
        ("embedding", "Embedding additional files", 0.90),
        ("centrality", "Computing symbol centrality", 0.95),
        ("format", "Formatting output", 1.0),
    ]

    # Display text for cached phases (overrides PHASES display text)
    CACHED_DISPLAY = {  # noqa: RUF012
        "profile": "Loading cached profile",
        "readme": "Loading cached README",
        "config": "Loading cached config",
        "analysis": "Loading cached analysis",
        "symbols": "Using cached symbols",
        "embedding": "Using cached embeddings",
        "centrality": "Using cached centrality",
    }

    def __init__(self, output_stream=None):
        """Initialize progress reporter.

        Args:
            output_stream: Stream to write progress to (default: sys.stderr).
        """
        import sys
        import time
        self._stream = output_stream or sys.stderr
        self._start_time = time.time()
        self._phase_times: dict[str, float] = {}
        self._current_phase_idx = 0
        self._enabled = True

    def disable(self) -> None:
        """Disable progress output."""
        self._enabled = False

    def enable(self) -> None:
        """Enable progress output."""
        self._enabled = True

    def start_phase(self, phase_name: str, cached: bool = False) -> None:
        """Mark the start of a phase.

        Args:
            phase_name: Name of the phase (e.g., "profile", "config").
            cached: If True, show "loading cached" message instead of "computing".
        """
        if not self._enabled:
            return

        import time

        # Find phase index and display info
        phase_info = None
        for idx, (name, display, progress) in enumerate(self.PHASES):
            if name == phase_name:
                phase_info = (idx, display, progress)
                self._current_phase_idx = idx
                break

        if phase_info is None:
            return

        idx, display, progress = phase_info

        # Use cached display text if available and cached=True
        if cached and phase_name in self.CACHED_DISPLAY:
            display = self.CACHED_DISPLAY[phase_name]

        pct = int(progress * 100)

        # Calculate ETA based on elapsed time and progress
        elapsed = time.time() - self._start_time
        if progress > 0:
            estimated_total = elapsed / progress
            remaining = estimated_total - elapsed
            if remaining > 0:
                eta_str = f" ETA {remaining:.0f}s"
            else:
                eta_str = ""
        else:
            eta_str = ""

        # Write progress line (carriage return to overwrite)
        self._stream.write(f"\r[{pct:3d}%] {display}...{eta_str}    ")
        self._stream.flush()

    def complete_phase(self, phase_name: str) -> None:
        """Mark a phase as complete and record timing.

        Args:
            phase_name: Name of the phase that completed.
        """
        if not self._enabled:
            return

        import time
        self._phase_times[phase_name] = time.time()

    def update_item_progress(self, label: str, current: int, total: int) -> None:
        """Update progress for item-level operations within a phase.

        Args:
            label: Description of items being processed (e.g., "Embedding files").
            current: Current item number (1-indexed).
            total: Total number of items.
        """
        if not self._enabled:
            return

        import time

        # Calculate overall progress based on current phase
        phase_progress = 0.0
        next_progress = 1.0
        for idx, (_name, _display, progress) in enumerate(self.PHASES):
            if idx == self._current_phase_idx:
                phase_progress = progress
                if idx + 1 < len(self.PHASES):
                    next_progress = self.PHASES[idx + 1][2]
                break

        # Interpolate within the phase
        item_fraction = current / total if total > 0 else 1.0
        phase_span = next_progress - phase_progress
        overall_progress = phase_progress + (item_fraction * phase_span * 0.9)
        pct = int(overall_progress * 100)

        # Calculate ETA
        elapsed = time.time() - self._start_time
        if overall_progress > 0:
            estimated_total = elapsed / overall_progress
            remaining = estimated_total - elapsed
            if remaining > 0:
                eta_str = f" ETA {remaining:.0f}s"
            else:
                eta_str = ""
        else:
            eta_str = ""

        # Write progress line
        self._stream.write(f"\r[{pct:3d}%] {label}: {current}/{total}{eta_str}    ")
        self._stream.flush()

    def finish(self) -> None:
        """Mark progress as complete and show final status."""
        if not self._enabled:
            return

        import time
        elapsed = time.time() - self._start_time
        self._stream.write(f"\r[100%] Complete in {elapsed:.1f}s           \n")
        self._stream.flush()


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


def _section_header(title: str, exclude_tests: bool = False) -> str:
    """Generate a section header with optional [IGNORING TESTS] marker.

    Args:
        title: The section title (e.g., "Overview", "Structure").
        exclude_tests: If True, append [IGNORING TESTS] marker.

    Returns:
        Formatted section header like "## Overview" or "## Overview [IGNORING TESTS]"
    """
    if exclude_tests:
        return f"## {title} [IGNORING TESTS]"
    return f"## {title}"


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
            # Skip non-dict package.json files
            if not isinstance(data, dict):
                return result
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
        from .sketch_embeddings import _load_embedding_model
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
    model = _load_embedding_model()  # pragma: no cover

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
    min_chunk_chars: int = 0,
) -> str:
    """Build a 3-line chunk with context, subsampling words if too long.

    Takes lines [center_idx-1, center_idx, center_idx+1] and joins them.
    If the result exceeds max_chunk_chars, applies word-level subsampling
    with ellipsis to indicate elision.

    If min_chunk_chars > 0 and the initial chunk is too small, expands
    forward by including additional lines until the minimum is reached
    or no more content is available. This prevents undersized chunks
    (e.g., heading-only fragments) from being indexed.

    Args:
        lines: All lines in the file.
        center_idx: Index of the center line to build chunk around.
        max_chunk_chars: Maximum characters for the chunk.
        fleximax_words: Base sample size for word-level subsampling.
        min_chunk_chars: Minimum characters for the chunk (0 = no minimum).

    Returns:
        Chunk string, possibly with ellipsis if words were subsampled.
    """
    import math

    # Get context lines (before, center, after)
    start_idx = max(0, center_idx - 1)
    end_idx = min(len(lines), center_idx + 2)
    context_lines = [lines[i] for i in range(start_idx, end_idx) if lines[i]]

    chunk = " ".join(context_lines)

    # If chunk is undersized and we have a minimum, expand forward
    if min_chunk_chars > 0 and len(chunk) < min_chunk_chars:
        current_end = end_idx
        while len(chunk) < min_chunk_chars and current_end < len(lines):
            if lines[current_end]:
                context_lines.append(lines[current_end])
                chunk = " ".join(context_lines)
            current_end += 1

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
    progress_callback: "callable | None" = None,
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
        from .sketch_embeddings import _load_embedding_model
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
    model = _load_embedding_model()
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

    # === PASS 1a: Collect chunks from all files (no embedding yet) ===
    # Structure: list of (source, center_idx, chunk_text, file_lines)
    all_chunks: list[tuple[str, int, str, list[str]]] = []
    file_chunk_ranges: dict[str, tuple[int, int]] = {}  # source -> (start_idx, end_idx)
    processed_files = 0
    total_files = min(len(config_content), max_config_files)

    for source, content in config_content:
        if processed_files >= max_config_files:  # pragma: no cover
            break

        file_lines = [ln.strip() for ln in content.split("\n")]
        _vlog(f"Collecting chunks from {source} ({len(file_lines)} lines)...")

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
        # For license/copying files, enforce minimum chunk size to avoid
        # undersized chunks (e.g., heading-only fragments like "Preamble")
        source_lower = source.lower()
        is_license_file = any(
            lic.lower() in source_lower for lic in LICENSE_FILES
        )
        min_chars = 80 if is_license_file else 0

        start_idx = len(all_chunks)
        for center_idx in sampled_indices:
            chunk = _build_context_chunk(
                file_lines, center_idx, max_chunk_chars, min_chunk_chars=min_chars
            )
            if chunk:  # Skip empty chunks
                all_chunks.append((source, center_idx, chunk, file_lines))

        end_idx = len(all_chunks)
        if end_idx > start_idx:
            file_chunk_ranges[source] = (start_idx, end_idx)
            processed_files += 1

    if not all_chunks:
        return []  # pragma: no cover

    # === PASS 1b: Batch encode ALL chunks at once ===
    if progress_callback:  # pragma: no cover - progress callback optional
        progress_callback(0, total_files)  # Signal embedding start

    chunk_texts = [chunk for _, _, chunk, _ in all_chunks]
    _t0 = _time.time()
    all_embeddings = model.encode(chunk_texts, convert_to_numpy=True)
    _vlog(f"Batch encoded {len(chunk_texts)} chunks in {_time.time() - _t0:.1f}s")

    # Normalize all embeddings
    _t1 = _time.time()
    all_norms = np.linalg.norm(all_embeddings, axis=1, keepdims=True)
    all_normalized = all_embeddings / (all_norms + 1e-8)

    # Compute similarity to all probes for all chunks at once
    # Shape: (num_all_chunks, num_answer_patterns)
    answer_sim_matrix = np.dot(all_normalized, normalized_answer_patterns.T)
    # Shape: (num_all_chunks, num_question_patterns)
    question_sim_matrix = np.dot(all_normalized, normalized_question_patterns.T)
    # Combine into single matrix: (num_all_chunks, num_all_probes)
    combined_sim_matrix = np.concatenate(
        [answer_sim_matrix, question_sim_matrix], axis=1
    )

    # Top-k mean: require k probes to "agree" rather than one spurious match
    top_k = 3
    num_probes = combined_sim_matrix.shape[1]
    if num_probes >= top_k:
        top_k_values = np.partition(combined_sim_matrix, -top_k, axis=1)[:, -top_k:]
        all_similarities = np.mean(top_k_values, axis=1)
    else:
        all_similarities = np.mean(combined_sim_matrix, axis=1)  # pragma: no cover
    _vlog(f"Similarity computation in {(_time.time() - _t1)*1000:.1f}ms")

    if progress_callback:  # pragma: no cover - progress callback optional
        progress_callback(total_files, total_files)  # Signal embedding complete

    # === PASS 1c: Build candidates per file ===
    file_candidates: dict[str, list[tuple[float, int, str, list[str], np.ndarray]]] = {}

    for source, (start_idx, end_idx) in file_chunk_ranges.items():
        # Get this file's similarities
        file_similarities = all_similarities[start_idx:end_idx].copy()

        # Apply penalty for LICENSE/COPYING files
        source_lower = source.lower()
        if "license" in source_lower or "copying" in source_lower:
            license_penalty = 0.5
            file_similarities = file_similarities * license_penalty
            _vlog(f"  Applied LICENSE penalty ({license_penalty}x) to {source}")

        # Collect chunks above threshold
        above_threshold = []
        for i, global_idx in enumerate(range(start_idx, end_idx)):
            sim = float(file_similarities[i])
            if sim >= similarity_threshold:
                _, center_idx, chunk_text, file_lines = all_chunks[global_idx]
                above_threshold.append(
                    (sim, center_idx, chunk_text, file_lines, all_normalized[global_idx])
                )

        above_threshold.sort(reverse=True, key=lambda x: x[0])

        if above_threshold:
            file_candidates[source] = above_threshold

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
    progress_callback: "callable | None" = None,
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
        progress_callback: Optional callback for progress updates (current, total).

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
            progress_callback=progress_callback,
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
        combined.append("--- Additional context (semantic) ---")
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
    progress_callback: "callable | None" = None,
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
        progress_callback: Optional callback for progress updates (current, total).

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
            progress_callback=progress_callback,
        )
    elif mode == ConfigExtractionMode.HYBRID:
        lines = _extract_config_hybrid(
            repo_root,
            max_chars=max_chars,
            max_config_files=max_config_files,
            fleximax_lines=fleximax_lines,
            max_chunk_chars=max_chunk_chars,
            progress_callback=progress_callback,
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


def _format_config_section(config_info: str, exclude_tests: bool = False) -> str:
    """Format config info as a Markdown section.

    Args:
        config_info: Extracted config information string.
        exclude_tests: If True, add [IGNORING TESTS] marker to header.

    Returns:
        Markdown-formatted configuration section.
    """
    if not config_info:
        return ""

    lines = [_section_header("Configuration", exclude_tests), ""]
    lines.append("```")
    lines.append(config_info)
    lines.append("```")

    return "\n".join(lines)


def _format_file_content_block(rel_path: str, content: str) -> list[str]:
    """Format file content with visible START/END markers.

    Creates clear visual delineation of file boundaries for easier parsing.

    Args:
        rel_path: Relative path to the file.
        content: The file content.

    Returns:
        List of lines including START marker, code block, and END marker.
    """
    # Build visually distinctive markers
    # Pad to ~60 chars total for visual balance
    start_marker = f"------------------- START of {rel_path} "
    start_marker += "-" * max(0, 60 - len(start_marker))
    end_marker = f"------------------- END of {rel_path} "
    end_marker += "-" * max(0, 60 - len(end_marker))

    return [
        start_marker,
        "```",
        content.rstrip(),
        "```",
        end_marker,
        "",  # Blank line for separation
    ]


def _estimate_file_block_tokens(rel_path: str, content: str) -> int:
    """Estimate tokens for a complete file content block including markers.

    This provides accurate token estimation for budget calculations,
    accounting for START/END markers and code fences, not just content.

    Args:
        rel_path: Relative path to the file.
        content: The file content.

    Returns:
        Estimated token count for the full formatted block.
    """
    # Markers are ~60 chars each, plus code fences (~6 chars), plus newlines
    # Total overhead: ~130 chars + path length * 2
    marker_overhead = 130 + len(rel_path) * 2
    content_chars = len(content.rstrip())
    total_chars = marker_overhead + content_chars
    # Use same estimation as estimate_tokens (4 chars per token)
    return total_chars // 4


def _count_test_loc(
    repo_root: Path,
    profile: RepoProfile,
    extra_excludes: Optional[List[str]] = None,
) -> tuple[int, int]:
    """Count LOC in test files.

    Only counts source code files (SOURCE_EXTENSIONS), not config/build files.
    This ensures consistency with the Tests section which also uses SOURCE_EXTENSIONS.

    Args:
        repo_root: Repository root path.
        profile: Repository profile with detected languages.
        extra_excludes: Additional exclude patterns.

    Returns:
        (test_loc, test_files) tuple.
    """
    from .discovery import find_files, DEFAULT_EXCLUDES

    # Combine default and extra excludes (same as detect_profile)
    excludes = list(DEFAULT_EXCLUDES)
    if extra_excludes:  # pragma: no cover
        excludes.extend(extra_excludes)

    test_loc = 0
    test_files = 0

    # Use SOURCE_EXTENSIONS (source code only) instead of LANGUAGE_EXTENSIONS
    # This excludes config/build files like Makefile from test counts
    all_patterns: set[str] = set()
    for pattern_list in SOURCE_EXTENSIONS.values():
        all_patterns.update(pattern_list)
    patterns = list(all_patterns)

    for f in find_files(repo_root, patterns, excludes=excludes):
        rel_path = str(f.relative_to(repo_root))
        if _is_test_path(rel_path):
            test_files += 1
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                test_loc += sum(1 for line in content.splitlines() if line.strip())
            except Exception:  # pragma: no cover
                pass  # Skip unreadable files

    return test_loc, test_files


def _format_language_stats(
    profile: RepoProfile,
    repo_root: Optional[Path] = None,
    extra_excludes: Optional[List[str]] = None,
    exclude_tests: bool = False,
) -> str:
    """Format language statistics as a multi-line summary.

    Args:
        profile: Repository profile with language statistics.
        repo_root: If provided, compute and show test LOC separately.
        extra_excludes: Additional exclude patterns for test LOC counting.
        exclude_tests: If True, add [IGNORING TESTS] marker to output.

    Returns:
        Formatted statistics (multi-line if test files detected).
    """
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

    lang_line = ", ".join(parts)
    total_files = sum(lang.files for lang in profile.languages.values())
    ignore_marker = " [IGNORING TESTS]" if exclude_tests else ""

    # Compute test LOC if repo_root provided
    if repo_root is not None:
        # Always use _count_test_loc to get accurate test counts from the profile
        # (profile.languages includes all file types: code, markdown, JSON, etc.)
        test_loc, test_files = _count_test_loc(repo_root, profile, extra_excludes)

        # Always show breakdown format for consistency (with or without -x flag)
        non_test_loc = total_loc - test_loc
        non_test_files = total_files - test_files

        # When exclude_tests=True, show non-test as total and 0 for tests
        # This matches the [IGNORING TESTS] marker semantics
        if exclude_tests:
            display_total_files = non_test_files
            display_total_loc = non_test_loc
            display_test_files = 0
            display_test_loc = 0
        else:
            display_total_files = total_files
            display_total_loc = total_loc
            display_test_files = test_files
            display_test_loc = test_loc

        # Format with aligned columns for easy comparison
        # Determine widths for alignment
        files_width = max(len(f"{non_test_files:,}"), len(f"{display_test_files:,}"))
        loc_width = max(len(f"~{non_test_loc:,}"), len(f"~{display_test_loc:,}"))

        files_line = (
            f"{display_total_files:,} files    "
            f"({non_test_files:>{files_width},} non-test + "
            f"{display_test_files:>{files_width},} test){ignore_marker}"
        )
        loc_line = (
            f"~{display_total_loc:,} LOC "
            f"(~{non_test_loc:>{loc_width - 1},} non-test + "
            f"~{display_test_loc:>{loc_width - 1},} test){ignore_marker}"
        )

        return f"{lang_line}\n{files_line}\n{loc_line}"

    return f"{lang_line} · {total_files} files · ~{total_loc:,} LOC{ignore_marker}"  # pragma: no cover


def _count_root_items(repo_root: Path, excludes: list[str]) -> int:
    """Count all non-excluded items (files and directories) at root level.

    This shared helper ensures consistent item counts between
    _format_structure_tree and _format_structure_tree_fallback.

    Args:
        repo_root: Repository root path.
        excludes: Patterns to exclude.

    Returns:
        Number of non-excluded items at root level.
    """
    from fnmatch import fnmatch

    count = 0
    for item in repo_root.iterdir():
        if any(fnmatch(item.name, pat) for pat in excludes):
            continue
        count += 1
    return count


def _format_structure_tree_fallback(
    repo_root: Path,
    excludes: list[str],
    exclude_tests: bool = False,
) -> str:
    """Format top-level directory structure in tree format.

    Used when there are no important files to show (e.g., all source files
    are tests and -x was used). Shows just the top-level directories with
    item counts, using the tree format for consistency.

    Args:
        repo_root: Path to the repository root.
        excludes: Patterns to exclude from directory listing.
        exclude_tests: Whether tests are being excluded.

    Returns:
        Markdown formatted tree structure.
    """
    from fnmatch import fnmatch

    lines = [_section_header("Structure", exclude_tests), "", "```"]
    lines.append(f"{repo_root.name}/")

    # Get top-level directories, filtering out excluded ones
    dirs = []
    for d in repo_root.iterdir():
        if not d.is_dir():
            continue
        excluded = any(fnmatch(d.name, pattern) for pattern in excludes)
        if not excluded:
            dirs.append(d.name)

    # Also get root-level files (source and config/doc files)
    # Build set of all source file patterns from SOURCE_EXTENSIONS
    source_patterns: set[str] = set()
    for patterns in SOURCE_EXTENSIONS.values():
        source_patterns.update(patterns)

    def is_source_file(filename: str) -> bool:
        """Check if filename matches any source file pattern."""
        return any(fnmatch(filename, pat) for pat in source_patterns)

    root_files = []
    for f in repo_root.iterdir():
        if not f.is_file():
            continue
        if any(fnmatch(f.name, pattern) for pattern in excludes):
            continue
        # Include source files and additional file candidates (CONFIG/DOCUMENTATION)
        if is_source_file(f.name) or is_additional_file_candidate(f):
            # When excluding tests, skip test files
            if exclude_tests and _is_test_path(f.name):
                continue
            root_files.append(f.name)

    root_files = sorted(root_files)
    dirs = sorted(dirs)

    if not dirs and not root_files:
        lines.append("└── (empty)")
        lines.append("```")
        return "\n".join(lines)

    # Count items in each directory
    def count_items(dir_path: Path) -> int:
        count = 0
        try:
            for item in dir_path.iterdir():
                # Skip excluded items
                if any(fnmatch(item.name, p) for p in excludes):
                    continue
                # When excluding tests, skip test files but keep config/doc files
                if exclude_tests:
                    rel_path = str(item.relative_to(repo_root))
                    if _is_test_path(rel_path):
                        if item.is_file():
                            # Keep config/documentation files (Additional Files candidates)
                            if not is_additional_file_candidate(item):
                                continue  # Skip test source file
                        else:
                            # Skip test directories
                            continue
                count += 1
        except PermissionError:  # pragma: no cover
            pass
        return count

    # Combine dirs and files, showing dirs first (max 10 total items)
    all_items = [(d, True) for d in dirs] + [(f, False) for f in root_files]
    shown_items = all_items[:10]
    # Calculate hidden count based on TOTAL non-excluded items (not just source/config files)
    # This ensures consistency with _format_structure_tree which counts all items
    total_root_items = _count_root_items(repo_root, excludes)
    hidden_count = total_root_items - len(shown_items)

    # Directory type annotations for common directory names
    source_dirs = {"src", "lib", "source", "app", "pkg"}
    test_dirs = {"test", "tests", "spec", "specs", "__tests__"}
    doc_dirs = {"docs", "doc", "documentation"}

    def get_dir_label(dirname: str) -> str:
        """Get descriptive label for a directory based on its name."""
        lower_name = dirname.lower()
        if lower_name in source_dirs:
            return " — Source code"
        elif lower_name in test_dirs:
            return " — Tests"
        elif lower_name in doc_dirs:
            return " — Documentation"
        return ""

    for i, (name, is_dir) in enumerate(shown_items):
        is_last = (i == len(shown_items) - 1) and hidden_count == 0
        prefix = "└── " if is_last else "├── "
        if is_dir:
            dir_path = repo_root / name
            item_count = count_items(dir_path)
            label = get_dir_label(name)
            if item_count > 0:
                lines.append(f"{prefix}{name}/ ({item_count} items){label}")
            else:
                lines.append(f"{prefix}{name}/{label}")
        else:
            lines.append(f"{prefix}{name}")

    if hidden_count > 0:
        lines.append(f"└── [and {hidden_count} other items]")

    lines.append("```")
    return "\n".join(lines)


def _format_structure_tree(
    repo_root: Path,
    important_files: list[str],
    max_root_dirs: int = 10,
    extra_excludes: Optional[List[str]] = None,
    exclude_tests: bool = False,
) -> str:
    """Format directory structure as a tree built from important files.

    ADR-0005 specifies that the Structure section should show a tree-like
    visualization with paths to important files, revealing directory
    organization along the way.

    Args:
        repo_root: Path to the repository root.
        important_files: List of important file paths (relative to repo_root).
        max_root_dirs: Maximum number of root-level directories to show.
        extra_excludes: Additional patterns to exclude from item counts.

    Returns:
        Markdown formatted tree structure.

    Example output:
        ## Structure
        ```
        myproject/
        ├── config.yaml
        ├── src
        │   ├── main.py
        │   └── [and 42 other items]
        └── tests
            └── test_main.py
        ```
    """
    from fnmatch import fnmatch

    # Combine default and extra excludes for counting
    excludes = list(DEFAULT_EXCLUDES)
    if extra_excludes:
        excludes.extend(extra_excludes)

    # If no important files, show top-level directories in tree format
    # (Don't fall back to deprecated bullet-list format)
    if not important_files:
        return _format_structure_tree_fallback(repo_root, excludes, exclude_tests)

    # Build a tree from paths
    # Tree node: {"name": str, "children": dict, "is_file": bool, "shown": bool}
    def make_node(name: str, is_file: bool = False) -> dict:
        return {"name": name, "children": {}, "is_file": is_file, "shown": False}

    root = make_node(repo_root.name)

    # Track which root directories we've seen
    seen_root_dirs: set[str] = set()

    # Add important files to the tree, respecting max_root_dirs
    for file_path in important_files:
        parts = Path(file_path).parts
        if not parts:
            continue

        # Check if this file adds a new root-level directory
        first_part = parts[0]
        if len(seen_root_dirs) >= max_root_dirs and first_part not in seen_root_dirs:
            continue  # Skip files that would add more root dirs than allowed

        seen_root_dirs.add(first_part)

        # Add path to tree
        node = root
        for i, part in enumerate(parts):
            is_file = i == len(parts) - 1
            if part not in node["children"]:
                node["children"][part] = make_node(part, is_file)
            node["children"][part]["shown"] = True
            node = node["children"][part]

    def count_items(path: Path) -> int:
        """Count items in a directory, excluding patterns."""
        if not path.is_dir():  # pragma: no cover
            return 0
        count = 0
        try:
            for item in path.iterdir():
                if any(fnmatch(item.name, pat) for pat in excludes):
                    continue
                count += 1
        except OSError:  # pragma: no cover
            pass
        return count

    def render_tree(node: dict, path: Path, prefix: str = "") -> list[str]:
        """Render tree node and its children."""
        lines: list[str] = []

        # Get children sorted: directories first, then files
        children = list(node["children"].values())
        shown_children = [c for c in children if c["shown"]]

        # Sort: directories first, then alphabetically
        shown_children.sort(key=lambda c: (c["is_file"], c["name"]))

        # Count hidden items at THIS level (siblings of shown_children)
        total_at_level = count_items(path) if path.is_dir() else 0
        hidden_at_level = max(0, total_at_level - len(shown_children))

        for i, child in enumerate(shown_children):
            child_path = path / child["name"]
            is_last_shown = i == len(shown_children) - 1

            # Child uses └── only if it's last shown AND no hidden siblings follow
            is_truly_last = is_last_shown and hidden_at_level == 0
            connector = "└── " if is_truly_last else "├── "

            # Prefix for this child's subtree: use │ if more items follow at this level
            has_more_siblings = not is_truly_last
            child_prefix = prefix + ("│   " if has_more_siblings else "    ")

            lines.append(f"{prefix}{connector}{child['name']}")

            # If directory with shown children, recurse
            if not child["is_file"] and child["children"]:
                lines.extend(render_tree(child, child_path, child_prefix))

                # Count hidden items inside this child directory
                child_total = count_items(child_path) if child_path.is_dir() else 0
                child_shown = len([c for c in child["children"].values() if c["shown"]])
                child_hidden = max(0, child_total - child_shown)

                if child_hidden > 0:
                    # "and N other items" is always the last item in this directory
                    lines.append(f"{child_prefix}└── [and {child_hidden} other items]")

        return lines

    # Render the tree
    tree_lines = render_tree(root, repo_root)

    # Count hidden root-level directories
    total_root_items = count_items(repo_root)
    hidden_root = total_root_items - len(seen_root_dirs)

    if hidden_root > 0:
        tree_lines.append(f"└── [and {hidden_root} other items]")

    # Build the output
    lines = [_section_header("Structure", exclude_tests), "", "```", f"{repo_root.name}/"]
    lines.extend(tree_lines)
    lines.append("```")

    return "\n".join(lines)


def _collect_important_files(
    repo_root: Path,
    source_files: list[str],
    entrypoints: "list[Entrypoint]",
    datamodels: "list[DataModel]",
    symbols: list[Symbol],
    centrality: dict[str, float],
    max_root_dirs: int = 10,
    max_root_files: int = 5,
    max_additional_files: int = 5,
) -> list[str]:
    """Collect important files for the Structure tree per ADR-0005.

    Samples files from various sources in priority order until we have
    enough root-level directories represented:

    1. Configuration files (2+ files)
    2. Test files (1+ file, highest LOC)
    3. Entry point files (1+ file, highest confidence)
    4. Source files (by centrality, one per new root directory)
    5. Data model files (top 3)
    6. Additional files (CONFIG/DOCUMENTATION, one per new root directory)

    Uses separate limits for root-level files vs directories to prevent
    many root-level config files from crowding out directory representation.

    Args:
        repo_root: Repository root path.
        source_files: List of source file paths (relative).
        entrypoints: Detected entry points.
        datamodels: Detected data models.
        symbols: All symbols for lookup.
        centrality: Symbol centrality scores.
        max_root_dirs: Max root-level directories to show (default 10).
        max_root_files: Max root-level files to show (default 5).
        max_additional_files: Max additional files to add in step 6 (default 5).

    Returns:
        List of relative file paths to show in the tree.
    """
    important_files: list[str] = []
    seen_root_dirs: set[str] = set()  # Directories only
    seen_root_files: set[str] = set()  # Root-level files only
    resolved_root = repo_root.resolve()

    def to_relative(path: str) -> str:
        """Convert a path to relative, handling both absolute and relative inputs."""
        p = Path(path)
        if p.is_absolute():
            try:
                return str(p.relative_to(resolved_root))
            except ValueError:
                # Path is not under repo_root, skip it
                return ""
        return path

    def get_root_dir(path: str) -> str:
        """Get the root-level directory or filename."""
        parts = Path(path).parts
        return parts[0] if parts else ""

    def is_root_level_file(path: str) -> bool:
        """Check if path is a root-level file (not in a subdirectory)."""
        return len(Path(path).parts) == 1

    def add_file(path: str) -> bool:
        """Add a file if it contributes a new root item within limits."""
        path = to_relative(path)
        if not path:
            return False  # Path not under repo_root or empty
        if path in important_files:
            return False  # Already added

        root = get_root_dir(path)
        if not root:  # pragma: no cover
            return False  # Empty paths filtered by callers

        # Apply separate limits for root-level files vs directories
        if is_root_level_file(path):
            # Root-level file: check against max_root_files
            if root in seen_root_files:  # pragma: no cover
                return False  # Already have this file (duplicate prevention)
            if len(seen_root_files) >= max_root_files:  # pragma: no cover
                return False  # Would exceed max root files
            important_files.append(path)
            seen_root_files.add(root)
            return True
        else:
            # File in subdirectory: check against max_root_dirs
            if root not in seen_root_dirs and len(seen_root_dirs) >= max_root_dirs:
                return False  # Would exceed max root dirs  # pragma: no cover
            important_files.append(path)
            seen_root_dirs.add(root)
            return True

    # 1. Configuration files (look for common config file patterns)
    config_patterns = [
        "pyproject.toml", "package.json", "Cargo.toml", "go.mod",
        "pom.xml", "build.gradle", "composer.json", "Gemfile",
        "requirements.txt", "setup.py", "setup.cfg",
        "tsconfig.json", "webpack.config.js", "vite.config.ts",
        ".env.example", "docker-compose.yml", "Dockerfile",
    ]
    for pattern in config_patterns:
        config_path = repo_root / pattern
        if config_path.exists():
            add_file(pattern)
        if len(important_files) >= 2:  # 2+ config files
            break

    # 2. Test files (find highest-LOC test file)
    test_files = []
    for f in source_files:
        f_str = str(f)
        f_name = Path(f).name.lower()
        if "/test" in f_str.lower() or "test_" in f_name or "_test." in f_name or "/spec" in f_str.lower():
            test_files.append(f_str)
    if test_files:
        # Sort by file size as proxy for LOC
        def get_file_size(f: str) -> int:
            try:
                return (repo_root / f).stat().st_size
            except OSError:  # pragma: no cover
                return 0
        test_files.sort(key=get_file_size, reverse=True)
        add_file(test_files[0])

    # 3. Entry point files (highest confidence)
    symbol_by_id = {s.id: s for s in symbols}
    if entrypoints:
        sorted_eps = sorted(entrypoints, key=lambda e: -e.confidence)
        for ep in sorted_eps[:3]:  # Top 3 entry points
            sym = symbol_by_id.get(ep.symbol_id)
            if sym and sym.path:
                add_file(sym.path)  # add_file handles path conversion

    # 4. Source files (maximize root directory coverage)
    # Get files with highest average symbol centrality
    file_centrality: dict[str, float] = {}
    file_counts: dict[str, int] = {}
    for sym in symbols:
        if sym.path:
            rel_path = to_relative(sym.path)
            if not rel_path:
                continue  # Skip paths not under repo_root
            score = centrality.get(sym.id, 0.0)
            file_centrality[rel_path] = file_centrality.get(rel_path, 0.0) + score
            file_counts[rel_path] = file_counts.get(rel_path, 0) + 1

    # Sort by total centrality (density * count approximation)
    sorted_files = sorted(
        file_centrality.items(),
        key=lambda x: x[1],
        reverse=True
    )
    # Add files only if they introduce a new root directory.
    # This ensures directory diversity: each root dir gets its highest-centrality
    # file as representative, and we cover up to max_root_dirs directories.
    for path, _ in sorted_files:
        root = get_root_dir(path)
        if root and root not in seen_root_dirs:
            add_file(path)
        if len(seen_root_dirs) >= max_root_dirs:
            break

    # 5. Data model files
    if datamodels:
        for dm in datamodels[:3]:  # Top 3 data models
            sym = symbol_by_id.get(dm.symbol_id)
            if sym and sym.path:
                add_file(sym.path)  # add_file handles path conversion

    # 5b. Root-level source files (even without symbols)
    # For flat repos (all files at root), collect source files directly.
    # This ensures repos like qemu-sgabios show all root-level .c/.h/.S files,
    # not just those with symbols detected.
    if len(seen_root_files) < max_root_files:
        from fnmatch import fnmatch

        # Build set of all source patterns from SOURCE_EXTENSIONS
        source_patterns: set[str] = set()
        for patterns in SOURCE_EXTENSIONS.values():
            source_patterns.update(patterns)

        for item in sorted(repo_root.iterdir(), key=lambda x: x.name):
            if not item.is_file():
                continue
            # Check if it matches a source file pattern
            if any(fnmatch(item.name, pat) for pat in source_patterns):
                add_file(item.name)
            if len(seen_root_files) >= max_root_files:
                break

    # 6. Additional files (CONFIG and DOCUMENTATION files)
    # Scan for files that would appear in Additional Files section,
    # adding only those that introduce new root directories.
    if len(seen_root_dirs) < max_root_dirs:
        from fnmatch import fnmatch
        excludes = list(DEFAULT_EXCLUDES)

        def walk_additional_files() -> list[str]:
            """Walk repo and collect additional file candidates."""
            candidates: list[str] = []
            for item in repo_root.rglob("*"):
                if not item.is_file():
                    continue
                # Skip excluded directories
                if any(fnmatch(p, pat) for p in item.parts for pat in excludes):
                    continue
                if is_additional_file_candidate(item):
                    try:
                        rel_path = str(item.relative_to(resolved_root))
                        candidates.append(rel_path)
                    except ValueError:  # pragma: no cover
                        continue  # Not under repo_root (defensive)
            return candidates

        additional_candidates = walk_additional_files()
        additional_added = 0
        for path in additional_candidates:
            # Skip root-level files - they're handled by config patterns in step 1
            parts = Path(path).parts
            if len(parts) < 2:
                continue
            root = get_root_dir(path)
            if root and root not in seen_root_dirs:
                if add_file(path):
                    additional_added += 1  # pragma: no cover (rare: new dir from additional files)
            # Stop when we hit either limit
            if additional_added >= max_additional_files:
                break  # pragma: no cover
            if len(seen_root_dirs) >= max_root_dirs:
                break  # pragma: no cover

    return important_files


def _format_frameworks(profile: RepoProfile, exclude_tests: bool = False) -> str:
    """Format detected frameworks."""
    if not profile.frameworks:
        return ""

    lines = [_section_header("Frameworks", exclude_tests), ""]
    for framework in sorted(profile.frameworks):
        lines.append(f"- {framework}")

    return "\n".join(lines)


def _get_repo_name(repo_root: Path) -> str:
    """Get repository name from path."""
    return repo_root.resolve().name


def _find_readme_path(repo_root: Path) -> Optional[Path]:
    """Find the README file in a repository.

    Uses case-insensitive matching for README files with common extensions.
    Prioritizes .md > .rst > .txt > .markdown > no extension.

    Args:
        repo_root: Path to the repository root.

    Returns:
        Path to the README file, or None if not found.
    """
    # Priority order for extensions
    # Supports: Markdown, RST, Org-mode, AsciiDoc, plain text, no extension
    extension_priority = [".md", ".rst", ".org", ".adoc", ".asc", ".txt", ".markdown", ""]

    # Collect all files that start with "readme" (case-insensitive)
    try:
        candidates = [
            f for f in repo_root.iterdir()
            if f.is_file() and f.name.lower().startswith("readme")
        ]
    except OSError:  # pragma: no cover
        return None

    if not candidates:
        return None

    # Sort by extension priority, then by name (prefer README over Readme)
    def sort_key(path: Path) -> tuple[int, str]:
        ext = path.suffix.lower()
        try:
            ext_rank = extension_priority.index(ext)
        except ValueError:  # pragma: no cover
            ext_rank = len(extension_priority)  # Unknown extension goes last
        # Prefer all-caps README over mixed case
        name_rank = path.name  # Alphabetically, README < Readme < readme
        return (ext_rank, name_rank)

    candidates.sort(key=sort_key)
    return candidates[0]


def _extract_readme_description_heuristic(
    readme_path: Path, max_chars: int = 200
) -> Optional[str]:
    """Extract description from README using heuristic parsing.

    Finds the first descriptive paragraph after the title, skipping
    badges, images, and HTML content.

    Args:
        readme_path: Path to the README file.
        max_chars: Maximum characters to extract.

    Returns:
        Extracted description string, or None if extraction fails.
    """
    import re

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

    result = " ".join(paragraph_lines)

    # If the sentence seems incomplete (no period at end) and we stopped at an
    # empty line, check if the next non-empty line continues the sentence.
    # This helps complete sentences that span paragraph breaks.
    if not result.rstrip().endswith((".", "!", "?", ":")):
        # Find where we stopped in the original lines
        end_idx = start_idx + len(paragraph_lines)
        # Skip empty lines to find the next content line
        next_line_idx = end_idx
        while next_line_idx < len(lines) and not lines[next_line_idx].strip():
            next_line_idx += 1

        if next_line_idx < len(lines):
            next_line = lines[next_line_idx].strip()
            # Strip HTML tags from the line
            next_line = re.sub(r"<[^>]+>", "", next_line)
            # Only continue if it's a regular text line (not a header/image/etc)
            if next_line and not next_line.startswith(("#", "!", "[", "<", "-")):
                # Append words until we hit a period or end of line
                words = next_line.split()
                for word in words:
                    result += " " + word
                    if word.rstrip().endswith((".", "!", "?", ":")):
                        break

    return result


def _truncate_description(description: str, max_chars: int) -> str:
    """Truncate description at sentence boundary, falling back to word boundary.

    Prefers to truncate at sentence-ending punctuation (. ! ? :) to avoid
    cutting off mid-sentence. Falls back to word boundary if no sentence
    boundary is found within a reasonable range.

    Args:
        description: The description to truncate.
        max_chars: Maximum characters.

    Returns:
        Truncated description with ellipsis if needed.
    """
    if len(description) <= max_chars:
        return description

    # First, try to find a sentence boundary before max_chars
    # Look for sentence-ending punctuation followed by space or end
    search_range = description[:max_chars]
    best_sentence_end = -1

    for i in range(len(search_range) - 1, -1, -1):
        char = search_range[i]
        if char in ".!?:":
            # Check if this looks like a sentence end (not part of URL, number, etc.)
            # A sentence end is followed by space, newline, or is at end of string
            if i == len(search_range) - 1:
                best_sentence_end = i + 1
                break
            next_char = search_range[i + 1]
            if next_char in " \n\t":
                best_sentence_end = i + 1
                break

    # Use sentence boundary if found and it's not unreasonably short
    # Minimum 10 chars to avoid single-word sentences like "Hi."
    if best_sentence_end >= 10:
        return description[:best_sentence_end].rstrip()

    # Fall back to word boundary
    truncate_at = description.rfind(" ", 0, max_chars)
    if truncate_at > max_chars // 2:
        return description[:truncate_at] + "…"
    return description[: max_chars - 1] + "…"


def _extract_readme_description(
    repo_root: Path, max_chars: int = 300
) -> Optional[str]:
    """Extract a description from the project README file.

    Uses embedding-based extraction when sentence-transformers is available,
    falling back to heuristic parsing otherwise. The embedding approach uses
    probe patterns from well-known project mission statements to semantically
    identify descriptive content.

    Args:
        repo_root: Path to the repository root.
        max_chars: Maximum characters to extract (default 300).

    Returns:
        Extracted description string, or None if no README found.
    """
    readme_path = _find_readme_path(repo_root)
    if readme_path is None:
        return None

    # Try embedding-based extraction first (more accurate)
    try:
        from .sketch_embeddings import extract_readme_description_embedding  # pragma: no cover
        description = extract_readme_description_embedding(readme_path)  # pragma: no cover
        if description:  # pragma: no cover
            return _truncate_description(description, max_chars)  # pragma: no cover
    except Exception:
        pass  # Fall back to heuristic - embeddings unavailable

    # Fall back to heuristic extraction
    description = _extract_readme_description_heuristic(readme_path, max_chars)
    if description:
        return _truncate_description(description, max_chars)

    return None


# ============ README Link Extraction ============
# These functions extract internal links from README files in various formats.
# Used for README-first ordering in Additional Files section.


def _extract_markdown_links(content: str) -> list[tuple[str, str]]:
    """Extract all markdown links from content, in document order.

    Handles:
    - Inline links: [text](url)
    - Reference links: [text][ref] or [text][] with [ref]: url definitions

    Args:
        content: Markdown file content.

    Returns:
        List of (link_text, url) tuples in document order.
    """
    import re

    results = []

    # First, build a map of reference definitions: [ref]: url
    ref_pattern = r"^\s*\[([^\]]+)\]:\s*(\S+)"
    ref_map = {}
    for match in re.finditer(ref_pattern, content, re.MULTILINE):
        ref_name = match.group(1).lower()
        ref_url = match.group(2)
        ref_map[ref_name] = ref_url

    # Extract inline links: [text](url) - but not images ![...](...)
    inline_pattern = r"(?<!\!)\[([^\]]*)\]\(([^)]+)\)"
    for match in re.finditer(inline_pattern, content):
        results.append((match.group(1), match.group(2)))

    # Extract reference-style links: [text][ref] or [text][]
    ref_link_pattern = r"(?<!\!)\[([^\]]+)\](?:\[([^\]]*)\])?(?!\()"
    for match in re.finditer(ref_link_pattern, content):
        text = match.group(1)
        ref = match.group(2)

        # Skip if this is a reference definition (has : immediately after)
        # Check if the next character after match is ':'
        if match.end() < len(content) and content[match.end()] == ":":
            continue

        # Determine which reference to look up
        if ref is None or ref == "":
            lookup = text.lower()
        else:
            lookup = ref.lower()

        if lookup in ref_map:
            results.append((text, ref_map[lookup]))

    return results


def _extract_org_links(content: str) -> list[tuple[str, str]]:
    """Extract all Org-mode links from content, in document order.

    Handles:
    - [[url][text]] - link with description
    - [[url]] - link without description

    Args:
        content: Org-mode file content.

    Returns:
        List of (link_text, url) tuples in document order.
    """
    import re

    results = []
    org_pattern = r"\[\[([^\]]+)\](?:\[([^\]]*)\])?\]"
    for match in re.finditer(org_pattern, content):
        url = match.group(1)
        text = match.group(2) or url
        results.append((text, url))

    return results


def _extract_rst_links(content: str) -> list[tuple[str, str]]:
    """Extract all reStructuredText links from content, in document order.

    Handles:
    - `text <url>`_ or `text <url>`__ (inline links)
    - `text`_ with .. _text: url defined elsewhere (reference links)

    Args:
        content: RST file content.

    Returns:
        List of (link_text, url) tuples in document order.
    """
    import re

    results = []

    # Build map of reference definitions: .. _name: url
    ref_pattern = r"^\.\.\s+_([^:]+):\s*(\S+)"
    ref_map = {}
    for match in re.finditer(ref_pattern, content, re.MULTILINE):
        ref_name = match.group(1).lower().strip()
        ref_url = match.group(2)
        ref_map[ref_name] = ref_url

    # Inline links: `text <url>`_ or `text <url>`__
    inline_pattern = r"`([^<`]+)\s+<([^>]+)>`_{1,2}"
    for match in re.finditer(inline_pattern, content):
        text = match.group(1).strip()
        url = match.group(2)
        results.append((text, url))

    # Reference links: `text`_ (look up in ref_map)
    ref_link_pattern = r"`([^`]+)`_(?!_)"
    for match in re.finditer(ref_link_pattern, content):
        text = match.group(1).strip()
        lookup = text.lower()
        if lookup in ref_map:
            results.append((text, ref_map[lookup]))

    return results


def _extract_asciidoc_links(content: str) -> list[tuple[str, str]]:
    """Extract all AsciiDoc links from content, in document order.

    Handles:
    - https://url[text] or http://url[text] - URL with bracket text
    - link:url[text] - explicit link macro
    - {attr}[text] with :attr: url - attribute reference links

    Args:
        content: AsciiDoc file content.

    Returns:
        List of (link_text, url) tuples in document order.
    """
    import re

    results = []

    # Build attribute map for {attribute} references
    attr_pattern = r"^:([^:]+):\s*(.+)$"
    attr_map = {}
    for match in re.finditer(attr_pattern, content, re.MULTILINE):
        attr_name = match.group(1).strip()
        attr_value = match.group(2).strip()
        attr_map[attr_name] = attr_value

    # URL with bracket text: https://url[text] or http://url[text]
    url_pattern = r"(https?://[^\s\[]+)\[([^\]]*)\]"
    for match in re.finditer(url_pattern, content):
        url = match.group(1)
        text = match.group(2) or url
        results.append((text, url))

    # link: macro: link:url[text]
    link_macro_pattern = r"link:([^\s\[]+)\[([^\]]*)\]"
    for match in re.finditer(link_macro_pattern, content):
        url = match.group(1)
        text = match.group(2) or url
        # Expand attribute references in URL
        for attr, val in attr_map.items():
            url = url.replace("{" + attr + "}", val)
        results.append((text, url))

    # Attribute reference links: {attr}[text] or {attr}/path[text]
    attr_link_pattern = r"\{([^}]+)\}(/[^\s\[]+)?\[([^\]]*)\]"
    for match in re.finditer(attr_link_pattern, content):
        attr = match.group(1)
        path_suffix = match.group(2) or ""
        text = match.group(3)
        if attr in attr_map:
            url = attr_map[attr] + path_suffix
            results.append((text, url))

    return results


def _resolve_readme_link(
    link: str,
    readme_dir: Path,
    repo_root: Path,
    repo_name: str,
) -> Optional[Path]:
    """Resolve a link from README to a file path within the repo.

    Handles:
    - Relative paths
    - Absolute paths (treated as repo-relative)
    - Forge URLs (github.com/org/repo/blob/...)
    - GitHub/GitLab Pages URLs

    Args:
        link: The URL or path from the README.
        readme_dir: Directory containing the README.
        repo_root: Repository root path.
        repo_name: Repository name (for forge URL matching).

    Returns:
        Resolved Path if the file exists within repo, None otherwise.
    """
    from urllib.parse import urlparse, unquote

    # Skip anchor-only links
    if link.startswith("#"):
        return None

    # Skip non-file protocols
    if link.startswith(("mailto:", "javascript:", "tel:", "data:", "ftp:", "irc:")):
        return None

    # Handle Org-mode file: scheme (strip prefix and treat as relative path)
    if link.startswith("file:"):
        link = link[5:]  # Remove "file:" prefix
        if not link:
            return None

    # Handle URLs
    if link.startswith(("http://", "https://", "//")):
        parsed = urlparse(link)

        # Check for forge domains
        forge_domains = [
            "github.com",
            "gitlab.com",
            "codeberg.org",
            "bitbucket.org",
            "raw.githubusercontent.com",
            "raw.github.com",
        ]
        is_forge = parsed.netloc.lower() in forge_domains
        is_ghpages = parsed.netloc.lower().endswith(".github.io")
        is_glpages = parsed.netloc.lower().endswith(".gitlab.io")

        if not is_forge and not is_ghpages and not is_glpages:
            return None  # External domain

        # Try GitHub/GitLab Pages URL mapping
        if is_ghpages or is_glpages:
            target = _resolve_pages_url(link, repo_name, repo_root)
            if target is not None:
                return target

        # Try forge URL extraction
        path_str = _extract_path_from_forge_url(link, repo_name)
        if path_str is None:
            return None
        target = repo_root / unquote(path_str)
    else:
        # Relative or absolute path
        link_path = link.split("#")[0]  # Remove anchor
        link_path = link_path.split("?")[0]  # Remove query string
        if not link_path:  # pragma: no cover - defensive for ?query-only links
            return None

        # Handle absolute paths (starting with /) as repo-relative
        if link_path.startswith("/"):
            stripped = link_path.lstrip("/")
            parts = stripped.split("/")

            # Check for relative forge URL pattern: /repo/tree/branch/path
            if len(parts) >= 4 and parts[1] in ("tree", "blob", "raw"):
                # Skip repo, tree/blob/raw, and branch name
                file_path = "/".join(parts[3:])
                target = repo_root / unquote(file_path)
            elif len(parts) >= 5 and parts[1] == "-" and parts[2] in (
                "tree",
                "blob",
                "raw",
            ):
                # GitLab-style: /repo/-/tree/branch/path
                file_path = "/".join(parts[4:])
                target = repo_root / unquote(file_path)
            else:
                target = repo_root / unquote(stripped)
        else:
            target = (readme_dir / unquote(link_path)).resolve()

    # Verify file exists and is within repo
    try:
        target = target.resolve()
        target.relative_to(repo_root)
        if target.is_file():
            return target
        elif target.is_dir():
            # Check for index file in directory
            for index_name in ["README.md", "index.md", "README.rst", "index.html"]:
                index_path = target / index_name
                if index_path.is_file():
                    return index_path
    except (ValueError, OSError):
        pass

    return None


def _resolve_pages_url(url: str, repo_name: str, repo_root: Path) -> Optional[Path]:
    """Try to find a repo file that corresponds to a GitHub/GitLab Pages URL.

    Args:
        url: The Pages URL.
        repo_name: Repository name.
        repo_root: Repository root path.

    Returns:
        Path to source file if found, None otherwise.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    path_parts = parsed.path.strip("/").split("/")

    if not path_parts or path_parts == [""]:
        return None

    # First part might be the repo name, skip it if so
    if path_parts[0].lower() == repo_name.lower():
        path_parts = path_parts[1:]

    if not path_parts or path_parts == [""]:  # pragma: no cover - defensive
        return None

    url_path = "/".join(path_parts)

    # Common source locations to try
    search_paths = [
        f"{url_path}.md",
        url_path,
        f"website/{url_path}.md",
        f"website/{url_path}/index.md",
        f"docs/{url_path}.md",
        f"docs/{url_path}/index.md",
        f"doc/{url_path}.md",
        f"site/{url_path}.md",
        f"documentation/{url_path}.md",
    ]

    # If URL path starts with 'docs/', also try without that prefix
    if url_path.startswith("docs/"):
        subpath = url_path[5:]
        search_paths.extend(
            [
                f"website/docs/{subpath}.md",
                f"website/docs/{subpath}/index.md",
                f"{subpath}.md",
            ]
        )

    for search_path in search_paths:
        candidate = repo_root / search_path
        if candidate.is_file():
            return candidate

    return None


def _extract_path_from_forge_url(url: str, repo_name: str) -> Optional[str]:
    """Extract file path from a forge URL if it points to the same repo.

    Args:
        url: The forge URL.
        repo_name: Repository name to match.

    Returns:
        File path string if extracted, None otherwise.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    path_parts = parsed.path.strip("/").split("/")

    if len(path_parts) < 3:
        return None

    # Check if this URL is for our repo (case-insensitive)
    url_repo = path_parts[1].lower()
    if url_repo != repo_name.lower():
        return None

    # GitHub/GitLab: /org/repo/blob/branch/path or /org/repo/tree/branch/path
    if len(path_parts) >= 4 and path_parts[2] in ("blob", "tree", "raw", "-"):
        if path_parts[2] == "-":
            # GitLab uses /-/blob/branch/path
            if len(path_parts) >= 5 and path_parts[3] in ("blob", "tree", "raw"):
                return "/".join(path_parts[5:]) if len(path_parts) > 5 else None
        return "/".join(path_parts[4:]) if len(path_parts) > 4 else None

    # raw.githubusercontent.com: /org/repo/branch/path
    if "raw.githubusercontent.com" in parsed.netloc:
        if len(path_parts) >= 4:
            return "/".join(path_parts[3:])

    return None  # pragma: no cover - fallback for unrecognized forge URL patterns


def _extract_readme_internal_links(
    readme_path: Path, repo_root: Path
) -> list[Path]:
    """Extract all internal file links from README, in document order.

    Uses format-specific parsers based on README extension.

    Args:
        readme_path: Path to the README file.
        repo_root: Repository root path.

    Returns:
        List of resolved Paths to files that exist in the repo.
    """
    try:
        content = readme_path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover
        return []

    # Choose extractor based on file type
    ext = readme_path.suffix.lower()
    if ext == ".org":
        links = _extract_org_links(content)
    elif ext == ".rst":
        links = _extract_rst_links(content)
    elif ext in (".adoc", ".asc", ".asciidoc"):
        links = _extract_asciidoc_links(content)
    else:
        # .md, .txt, .markdown, or no extension
        links = _extract_markdown_links(content)

    # Resolve links to file paths
    readme_dir = readme_path.parent
    repo_name = repo_root.name

    resolved: list[Path] = []
    seen: set[Path] = set()

    for _, url in links:
        target = _resolve_readme_link(url, readme_dir, repo_root, repo_name)
        if target is not None and target not in seen:
            seen.add(target)
            resolved.append(target)

    return resolved


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
            # Suppress SyntaxWarning from invalid escape sequences in analyzed code
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=SyntaxWarning)
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


def _format_vocabulary(terms: list[str], exclude_tests: bool = False) -> str:
    """Format domain vocabulary as a Markdown section.

    Args:
        terms: List of domain-specific terms.
        exclude_tests: If True, add [IGNORING TESTS] marker to header.

    Returns:
        Markdown-formatted vocabulary section.
    """
    if not terms:
        return ""

    lines = [_section_header("Domain Vocabulary", exclude_tests), ""]
    lines.append(f"*Key terms: {', '.join(terms)}*")

    return "\n".join(lines)


# SOURCE_EXTENSIONS is imported from taxonomy module (ADR-0004 Phase 3)

# Common source directories
SOURCE_DIRS = {"src", "lib", "app", "pkg", "cmd", "internal", "core", "source"}


def _collect_source_files(
    repo_root: Path,
    profile: RepoProfile,
    exclude_tests: bool = False,
) -> list[Path]:
    """Collect source files, prioritizing source directories.

    Args:
        repo_root: Repository root path.
        profile: Detected repository profile.
        exclude_tests: If True, exclude test files from the collection.

    Returns:
        List of source file paths.
    """
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
                    rel_path = str(f.relative_to(repo_root))
                    if exclude_tests and _is_test_path(rel_path):
                        continue
                    files.append(f)
                    seen.add(f)

    # Then collect remaining files from root
    for f in find_files(repo_root, patterns):
        if f not in seen:
            rel_path = str(f.relative_to(repo_root))
            if exclude_tests and _is_test_path(rel_path):
                continue
            files.append(f)
            seen.add(f)

    return files


def _format_source_files(
    repo_root: Path,
    files: list[Path],
    max_files: int = 50,
    density_scores: dict[str, float] | None = None,
    exclude_tests: bool = False,
) -> str:
    """Format source files as a Markdown section.

    When density_scores is provided, files are sorted by symbol importance
    density (sum of raw in-degrees of symbols / LOC) in descending order.
    Otherwise, files are displayed in their original order.
    """
    if not files:
        return ""

    # Sort by density if scores are provided
    if density_scores:
        files = sorted(
            files,
            key=lambda f: density_scores.get(str(f.relative_to(repo_root)), 0.0),
            reverse=True,
        )

    lines = [_section_header("Source Files", exclude_tests), ""]

    for f in files[:max_files]:
        rel_path = f.relative_to(repo_root)
        lines.append(f"- `{rel_path}`")

    if len(files) > max_files:
        lines.append(f"- ... and {len(files) - max_files} more files")

    return "\n".join(lines)


def _format_all_files(
    repo_root: Path,
    max_files: int = 200,
    exclude_tests: bool = False,
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

    lines = [_section_header("All Files", exclude_tests), ""]

    for f in files[:max_files]:
        rel_path = f.relative_to(repo_root)
        lines.append(f"- `{rel_path}`")

    if len(files) > max_files:
        lines.append(f"- ... and {len(files) - max_files} more files")

    return "\n".join(lines)


def _format_additional_files(
    repo_root: Path,
    source_files: list[Path],
    symbols: list[Symbol],
    in_degree: dict[str, int],
    max_files: int = 200,
    semantic_top_n: int = 10,
    progress_callback: "callable | None" = None,
    centrality_progress_callback: "callable | None" = None,
    cached_centrality_scores: dict[str, float] | None = None,
    exclude_tests: bool = False,
    token_budget: int | None = None,
    include_content: bool = False,
) -> tuple[str, list[Path], int]:
    """Format additional files (non-source) as a Markdown section.

    Uses a README-first hybrid ordering approach:
    1. README is always first (truncated if it exceeds budget)
    2. Files linked from README in document order
    3. Round-robin from similarity-ranked and centrality-ranked files

    When include_content=True, uses dynamic truncation based on median token
    count of already-selected files.

    Args:
        repo_root: Repository root path.
        source_files: List of source files (to be excluded from output).
        symbols: List of symbols from analysis.
        in_degree: Raw in-degree counts for symbols.
        max_files: Maximum files to show.
        semantic_top_n: Number of files to pick by semantic similarity.
        progress_callback: Optional callback for embedding progress updates.
            Called with (current, total) for each embedding computed.
        centrality_progress_callback: Optional callback for centrality progress.
            Called with (current, total) for each file scored.
        cached_centrality_scores: Optional pre-computed centrality scores from
            run_behavior_map(). Maps relative path strings to scores. When
            provided, uses these for RANKING (efficiency). Centrality is always
            recomputed to get per-file symbol data for accurate representativeness.
        exclude_tests: Whether tests are excluded (for section header).
        token_budget: Optional token budget for content. Required if include_content=True.
        include_content: If True, include file contents with dynamic truncation.

    Returns:
        Tuple of (Markdown formatted section, list of selected file paths,
        de-duplicated in-degree sum). The in-degree sum represents how much
        symbol connectivity is covered by the selected documentation files,
        counting each unique symbol only once across all selected files.
    """
    from fnmatch import fnmatch
    from statistics import median

    from .sketch_embeddings import (
        batch_embed_files,
        compute_5w1h_similarity,
        _get_cache_dir,
        _has_sentence_transformers,
    )

    # Combine exclusion patterns
    all_excludes = list(DEFAULT_EXCLUDES) + ADDITIONAL_FILES_EXCLUDES

    def _is_excluded(filepath: Path) -> bool:
        """Check if file should be excluded from Additional Files."""
        rel_path = filepath.relative_to(repo_root)
        filename = filepath.name

        if any(p.startswith(".") for p in rel_path.parts):
            return True

        if not is_additional_file_candidate(filepath):
            return True

        for pattern in all_excludes:
            if fnmatch(filename, pattern):
                return True
            for part in rel_path.parts:
                if fnmatch(part, pattern):
                    return True

        return False

    def _is_additional_candidate(filepath: Path) -> bool:
        """Check if file is a valid additional file candidate."""
        if _is_excluded(filepath):
            return False  # pragma: no cover - README links to excluded file
        rel_str = str(filepath.relative_to(repo_root))
        return rel_str not in source_set

    # Collect all non-excluded files
    all_files: list[Path] = []
    for f in repo_root.rglob("*"):
        if f.is_file() and not _is_excluded(f):
            all_files.append(f)

    if not all_files:
        return "", [], 0.0

    # Create set of source file paths for exclusion
    source_set = {str(f.relative_to(repo_root)) for f in source_files}

    # Exclude source files from candidates
    candidate_files = [
        f for f in all_files if str(f.relative_to(repo_root)) not in source_set
    ]

    if not candidate_files:
        return "", [], 0.0  # pragma: no cover - defensive

    # ========== README-First Hybrid Ordering ==========

    # Find README
    readme_path = _find_readme_path(repo_root)
    readme_links: list[Path] = []

    if readme_path is not None:
        # Extract internal links from README
        all_readme_links = _extract_readme_internal_links(readme_path, repo_root)
        # Filter to only additional file candidates
        readme_links = [f for f in all_readme_links if _is_additional_candidate(f)]

    # Build similarity rankings (only if semantic_top_n > 0)
    similarity_ranked: list[Path] = []
    if semantic_top_n > 0 and _has_sentence_transformers():
        cache_dir = _get_cache_dir(repo_root)
        embeddings = batch_embed_files(
            candidate_files,
            cache_dir=cache_dir,
            progress_callback=progress_callback,
        )

        file_scores: list[tuple[Path, float]] = []
        for f in candidate_files:
            embedding = embeddings.get(f)
            if embedding is not None:
                score = compute_5w1h_similarity(embedding)
                file_scores.append((f, score))
            else:  # pragma: no cover
                file_scores.append((f, 0.0))

        file_scores.sort(key=lambda x: x[1], reverse=True)
        similarity_ranked = [f for f, _ in file_scores]

    # Build centrality rankings
    # Compute centrality to get per-file symbol data for accurate representativeness
    centrality_result = compute_symbol_mention_centrality_batch(
        files=candidate_files,
        symbols=symbols,
        in_degree=in_degree,
        min_in_degree=2,
        max_file_size=100 * 1024,
        progress_callback=centrality_progress_callback,
    )
    symbols_per_file = centrality_result.symbols_per_file
    name_to_in_degree = centrality_result.name_to_in_degree

    # For RANKING: use cached scores if available (efficiency), else use computed scores
    if cached_centrality_scores is not None:
        centrality_scores: dict[Path, float] = {}
        for f in candidate_files:
            rel_str = str(f.relative_to(repo_root))
            centrality_scores[f] = cached_centrality_scores.get(rel_str, 0.0)
    else:
        centrality_scores = centrality_result.normalized_scores

    centrality_ranked = sorted(
        candidate_files,
        key=lambda f: centrality_scores.get(f, 0.0),
        reverse=True,
    )

    # ========== Round-Robin Selection ==========

    selected_files: list[Path] = []
    selected_set: set[Path] = set()

    # Iterators for round-robin sources
    readme_iter = iter(readme_links)
    similarity_iter = iter(similarity_ranked)
    centrality_iter = iter(centrality_ranked)

    def next_from_iter(it: "iter") -> Optional[Path]:
        """Get next unselected file from iterator."""
        while True:
            try:
                f = next(it)
                if f not in selected_set:
                    return f
            except StopIteration:
                return None

    # Add README first if it's a valid additional file candidate
    if readme_path is not None and _is_additional_candidate(readme_path):
        selected_files.append(readme_path)
        selected_set.add(readme_path)

    # Round-robin: readme_link, similarity, centrality, readme_link, ...
    sources_exhausted = [False, False, False]  # readme_links, similarity, centrality

    while len(selected_files) < max_files and not all(sources_exhausted):
        # README link
        if not sources_exhausted[0]:
            f = next_from_iter(readme_iter)
            if f is not None:
                selected_files.append(f)
                selected_set.add(f)
            else:
                sources_exhausted[0] = True

        if len(selected_files) >= max_files:  # pragma: no cover - defensive
            break

        # Similarity
        if not sources_exhausted[1]:
            f = next_from_iter(similarity_iter)
            if f is not None:
                selected_files.append(f)
                selected_set.add(f)
            else:
                sources_exhausted[1] = True

        if len(selected_files) >= max_files:  # pragma: no cover - defensive
            break

        # Centrality
        if not sources_exhausted[2]:
            f = next_from_iter(centrality_iter)
            if f is not None:
                selected_files.append(f)
                selected_set.add(f)
            else:
                sources_exhausted[2] = True

    # ========== Format Output ==========

    # Calculate accurate de-duplicated in-degree for representativeness:
    # 1. Collect unique symbols mentioned across ALL selected files
    # 2. Sum in-degrees for those unique symbols (no double-counting)
    unique_symbols_in_selected: set[str] = set()
    for f in selected_files:
        unique_symbols_in_selected.update(symbols_per_file.get(f, set()))
    selected_in_degree = sum(
        name_to_in_degree.get(sym, 0) for sym in unique_symbols_in_selected
    )

    if not include_content or token_budget is None:
        # Simple list format (backward compatible)
        lines = [_section_header("Additional Files", exclude_tests), ""]
        for f in selected_files:
            rel_path = f.relative_to(repo_root)
            lines.append(f"- `{rel_path}`")

        remaining = len(candidate_files) - len(selected_files)
        if remaining > 0:
            lines.append(f"- ... and {remaining} more files")

        return "\n".join(lines), selected_files, selected_in_degree

    # ========== Content Mode with Dynamic Truncation ==========

    lines = [_section_header("Additional Files", exclude_tests), ""]
    included_files: list[Path] = []
    token_counts: list[int] = []
    tokens_used = estimate_tokens("\n".join(lines))
    remaining_budget = token_budget - tokens_used

    # Minimum tokens for truncation floor
    MIN_TRUNCATION_TOKENS = 500

    for file_path in selected_files:
        if remaining_budget <= 50:  # pragma: no cover - budget exhausted
            break

        try:
            content = file_path.read_text(errors="replace")
            if not content.strip():  # pragma: no cover
                continue

            rel_path = file_path.relative_to(repo_root)
            file_tokens = _estimate_file_block_tokens(str(rel_path), content)

            # Check if file fits in remaining budget
            if file_tokens <= remaining_budget:
                # File fits completely
                lines.extend(_format_file_content_block(str(rel_path), content))
                included_files.append(file_path)
                token_counts.append(file_tokens)
                tokens_used += file_tokens
                remaining_budget -= file_tokens
            else:
                # File needs truncation
                # Calculate median of already-selected files (or use floor)
                if token_counts:
                    median_tokens = max(int(median(token_counts)), MIN_TRUNCATION_TOKENS)
                else:
                    median_tokens = MIN_TRUNCATION_TOKENS

                # Determine truncation target
                truncation_target = min(median_tokens, remaining_budget - 50)

                if truncation_target < MIN_TRUNCATION_TOKENS:
                    # Not enough budget for meaningful truncation, we're done
                    break

                # Truncate content to target tokens
                truncated_content = truncate_to_tokens(content, truncation_target)
                if truncated_content != content:
                    truncated_content += "\n\n[...truncated...]"

                truncated_tokens = _estimate_file_block_tokens(
                    str(rel_path), truncated_content
                )

                if truncated_tokens <= remaining_budget:
                    lines.extend(
                        _format_file_content_block(str(rel_path), truncated_content)
                    )
                    included_files.append(file_path)
                    token_counts.append(truncated_tokens)
                    tokens_used += truncated_tokens
                    remaining_budget -= truncated_tokens
                else:
                    # Even truncated content doesn't fit, we're done
                    break  # pragma: no cover - truncated still exceeds budget

        except (OSError, IOError):  # pragma: no cover
            continue

    # Recalculate de-duplicated in-degree for included files (may differ from selected due to budget)
    unique_symbols_in_included: set[str] = set()
    for f in included_files:
        unique_symbols_in_included.update(symbols_per_file.get(f, set()))
    included_in_degree = sum(
        name_to_in_degree.get(sym, 0) for sym in unique_symbols_in_included
    )
    return "\n".join(lines), included_files, included_in_degree


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

    This is a static analysis - it detects test files using the same path-based
    detection as the Overview section (_is_test_path), ensuring consistency.
    Framework detection uses import patterns. It does NOT measure coverage
    (which requires execution).

    Args:
        repo_root: Path to the repository root.

    Returns:
        Tuple of (summary_string, frameworks_set) where:
        - summary_string: Like "103 test files · pytest, hypothesis" or None if no tests
        - frameworks_set: Set of detected framework names
    """
    import re
    from .discovery import find_files, DEFAULT_EXCLUDES

    test_files: list[Path] = []
    frameworks_found: set[str] = set()

    # Find all source files using SOURCE_EXTENSIONS (already imported at module level)
    # SOURCE_EXTENSIONS is a dict of language -> list of extensions (patterns like "*.py")
    all_patterns: set[str] = set()
    for pattern_list in SOURCE_EXTENSIONS.values():
        all_patterns.update(pattern_list)

    # Also include test-only file extensions not in SOURCE_EXTENSIONS
    # These are files that are only used for tests, never for regular source code
    test_only_extensions = ["*.bats"]  # Bash Automated Testing System
    all_patterns.update(test_only_extensions)

    patterns = list(all_patterns)

    # Find test files using _is_test_path (same as Overview section)
    for f in find_files(repo_root, patterns, excludes=list(DEFAULT_EXCLUDES)):
        rel_path = str(f.relative_to(repo_root))
        if _is_test_path(rel_path):
            test_files.append(f)

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


def _detect_project_binary_names(repo_root: Path) -> list[str]:
    """Detect likely binary/executable names from build files.

    Checks common build systems for the project's binary name:
    - meson.build: executable('name', ...)
    - Makefile: TARGET, BINARY, PROGRAM variables
    - CMakeLists.txt: add_executable(name ...)
    - Cargo.toml: name in [package] or [[bin]]
    - go.mod: module name (last component)

    Falls back to the directory name if no build files found.

    Args:
        repo_root: Path to the repository root.

    Returns:
        List of likely binary names (may have multiple).
    """
    import re

    binary_names: set[str] = set()

    # meson.build: executable('name', ...)
    meson_build = repo_root / "meson.build"
    if meson_build.exists():
        try:
            content = meson_build.read_text(errors="replace")
            # Match executable('name' or executable("name"
            for match in re.finditer(r"executable\s*\(\s*['\"]([^'\"]+)['\"]", content):
                binary_names.add(match.group(1))
        except OSError:  # pragma: no cover
            pass

    # Makefile: TARGET, BINARY, PROGRAM, NAME variables
    for makefile_name in ["Makefile", "makefile", "GNUmakefile"]:
        makefile = repo_root / makefile_name
        if makefile.exists():
            try:
                content = makefile.read_text(errors="replace")
                # Match TARGET = name, BINARY := name, etc.
                for match in re.finditer(
                    r"^(?:TARGET|BINARY|PROGRAM|NAME|APP)\s*[:?]?=\s*(\S+)",
                    content,
                    re.MULTILINE,
                ):
                    name = match.group(1).strip()
                    # Skip variables like $(something)
                    if not name.startswith("$"):
                        binary_names.add(name)
            except OSError:  # pragma: no cover
                pass

    # CMakeLists.txt: add_executable(name ...)
    cmake_lists = repo_root / "CMakeLists.txt"
    if cmake_lists.exists():
        try:
            content = cmake_lists.read_text(errors="replace")
            # Match add_executable(name or add_executable( name
            for match in re.finditer(r"add_executable\s*\(\s*(\S+)", content):
                name = match.group(1)
                if not name.startswith("$"):
                    binary_names.add(name)
        except OSError:  # pragma: no cover
            pass

    # Cargo.toml: name in [package] section
    cargo_toml = repo_root / "Cargo.toml"
    if cargo_toml.exists():
        try:
            content = cargo_toml.read_text(errors="replace")
            # Simple pattern for name = "..." in package section
            for match in re.finditer(r'name\s*=\s*"([^"]+)"', content):
                binary_names.add(match.group(1))
        except OSError:  # pragma: no cover
            pass

    # go.mod: module path (use last component)
    go_mod = repo_root / "go.mod"
    if go_mod.exists():
        try:
            content = go_mod.read_text(errors="replace")
            match = re.search(r"^module\s+(\S+)", content, re.MULTILINE)
            if match:
                # Use last path component as binary name
                module_path = match.group(1)
                binary_names.add(module_path.split("/")[-1])
        except OSError:  # pragma: no cover
            pass

    # configure.ac: AC_INIT([name], ...)
    configure_ac = repo_root / "configure.ac"
    if configure_ac.exists():
        try:
            content = configure_ac.read_text(errors="replace")
            match = re.search(r"AC_INIT\s*\(\s*\[?([^\],\[]+)", content)
            if match:
                binary_names.add(match.group(1).strip())
        except OSError:  # pragma: no cover
            pass

    # Fall back to directory name only if we have compiled language source files
    # (no point detecting binaries for pure Python/JS projects)
    if not binary_names:
        compiled_extensions = {".c", ".cc", ".cpp", ".cxx", ".go", ".rs"}
        has_compiled_source = any(
            f.suffix in compiled_extensions
            for f in repo_root.iterdir()
            if f.is_file()
        )
        if has_compiled_source:
            binary_names.add(repo_root.name)

    return list(binary_names)


def _detect_shell_integration_tests(
    repo_root: Path, binary_names: list[str]
) -> list[Path]:
    """Find shell test scripts that likely invoke the project's binary.

    Searches for shell scripts in test directories that contain references
    to the project's binary name in a way that suggests invocation.

    Args:
        repo_root: Path to the repository root.
        binary_names: List of binary names to look for.

    Returns:
        List of shell script paths that likely invoke the binary.
    """
    from .discovery import find_files, DEFAULT_EXCLUDES

    shell_tests: list[Path] = []

    # Find shell scripts
    shell_patterns = ["*.sh", "*.bash"]
    for f in find_files(repo_root, shell_patterns, excludes=list(DEFAULT_EXCLUDES)):
        rel_path = str(f.relative_to(repo_root))

        # Only consider files in test-like directories or with test-like names
        is_test_location = any(
            part in rel_path.lower()
            for part in ["test", "tests", "spec", "specs", "check", "checks"]
        )
        is_test_name = any(
            pattern in f.name.lower()
            for pattern in ["test", "spec", "check"]
        )

        if not (is_test_location or is_test_name):
            continue

        # Check if the script references any binary name
        try:
            content = f.read_text(errors="replace")

            for binary in binary_names:
                # Patterns that suggest binary invocation
                invocation_patterns = [
                    f"./{binary}",           # ./slirp4netns
                    f"/{binary} ",           # /path/to/slirp4netns
                    f"/{binary}\n",          # end of line
                    f"/{binary}\"",          # quoted path end
                    f"/{binary}'",           # single quoted path end
                    f"${{{binary}",          # ${SLIRP4NETNS variable
                    f"$({binary}",           # $(slirp4netns ...) command sub
                    f" {binary} ",           # command with spaces
                    f"\n{binary} ",          # command at line start
                    f'"{binary}"',           # quoted command
                    f"'{binary}'",           # single quoted command
                ]

                if any(pattern in content for pattern in invocation_patterns):
                    shell_tests.append(f)
                    break  # Don't count same file multiple times

        except OSError:  # pragma: no cover
            continue

    return shell_tests


def _estimate_test_coverage(
    symbols: list[Symbol], edges: list
) -> tuple[int, int, float] | None:
    """Estimate test coverage from call graph using transitive BFS.

    Counts how many non-test functions/methods are reachable from test code
    via the call graph. This follows calls transitively: if test_foo() calls
    helper() which calls core(), both helper and core are counted as tested.

    This is a static approximation - actual coverage requires execution.

    Args:
        symbols: All symbols from analysis (including test symbols).
        edges: All edges from analysis (including test->production calls).

    Returns:
        (tested_count, total_count, percentage) or None if no targets.
    """
    # Identify test symbols (functions/methods in test files)
    test_symbol_ids: set[str] = set()
    for s in symbols:
        if _is_test_path(s.path) and s.kind in ("function", "method"):
            test_symbol_ids.add(s.id)

    # Identify non-test callable symbols (coverage targets)
    target_symbol_ids: set[str] = set()
    for s in symbols:
        if not _is_test_path(s.path) and s.kind in ("function", "method"):
            target_symbol_ids.add(s.id)

    if not target_symbol_ids:
        return None

    # Extract call edges
    call_edges = [
        (getattr(edge, "src", None), getattr(edge, "dst", None))
        for edge in edges
    ]

    # Use shared helper for transitive BFS
    tests_per_target = compute_transitive_test_coverage(
        test_ids=test_symbol_ids,
        target_ids=target_symbol_ids,
        call_edges=call_edges,
    )

    # Count how many targets have at least one test reaching them
    tested_count = sum(1 for tests in tests_per_target.values() if tests)
    total_count = len(target_symbol_ids)
    percentage = (tested_count / total_count * 100) if total_count > 0 else 0.0

    return (tested_count, total_count, percentage)


def _format_test_summary(
    repo_root: Path,
    coverage_stats: tuple[int, int, float] | None = None,
    exclude_tests: bool = False,
    shell_integration_count: int = 0,
) -> str:
    """Format test summary as a Markdown section.

    Args:
        repo_root: Path to the repository root.
        coverage_stats: Optional (tested, total, pct) from static analysis.
        exclude_tests: If True, show that tests are being ignored.
        shell_integration_count: Number of shell scripts that invoke the project binary.
            These are integration tests that can't be tracked via call graph analysis.

    Returns:
        Markdown section string (always returns a section, even if no tests detected).
    """
    # When exclude_tests=True, show that tests are excluded
    if exclude_tests:
        return f"{_section_header('Tests', exclude_tests)}\n\n0 tests (excluded via -x flag)"

    summary, frameworks = _detect_test_summary(repo_root)
    if not summary:
        # No tests detected - still show the section for consistency
        return f"{_section_header('Tests', exclude_tests)}\n\nNo test files detected"

    # Build coverage line with optional shell integration test info
    coverage_line = ""
    if coverage_stats is not None:
        tested, total, pct = coverage_stats

        # Format the shell integration test suffix
        shell_suffix = ""
        if shell_integration_count > 0:
            script_word = "script" if shell_integration_count == 1 else "scripts"
            shell_suffix = f" + {shell_integration_count} shell integration {script_word}"

        coverage_line = f"\n\n*~{pct:.0f}% estimated coverage ({tested}/{total} functions called by tests){shell_suffix}*"
    elif shell_integration_count > 0:
        # No call graph coverage but we have shell integration tests
        script_word = "script" if shell_integration_count == 1 else "scripts"
        coverage_line = f"\n\n*{shell_integration_count} shell integration {script_word} (invoke project binary)*"

    return f"{_section_header('Tests', exclude_tests)}\n\n{summary}{coverage_line}"


# Language analyzer registry: (languages_set, module_name, function_name, display_name)
# Each entry maps a set of detected language names to their analyzer module.
# The tuple format is: ({detected_languages}, "module", "function", "Display Name")
LANGUAGE_ANALYZERS: list[tuple[frozenset[str], str, str, str]] = [
    (frozenset({"python"}), "py", "analyze_python", "Python"),
    (frozenset({"javascript", "typescript"}), "js_ts", "analyze_javascript", "JS/TS"),
    (frozenset({"c"}), "c", "analyze_c", "C"),
    (frozenset({"rust"}), "rust", "analyze_rust", "Rust"),
    (frozenset({"php"}), "php", "analyze_php", "PHP"),
    (frozenset({"java"}), "java", "analyze_java", "Java"),
    (frozenset({"go"}), "go", "analyze_go", "Go"),
    (frozenset({"ruby"}), "ruby", "analyze_ruby", "Ruby"),
    (frozenset({"kotlin"}), "kotlin", "analyze_kotlin", "Kotlin"),
    (frozenset({"swift"}), "swift", "analyze_swift", "Swift"),
    (frozenset({"scala"}), "scala", "analyze_scala", "Scala"),
    (frozenset({"lua"}), "lua", "analyze_lua", "Lua"),
    (frozenset({"haskell"}), "haskell", "analyze_haskell", "Haskell"),
    (frozenset({"agda"}), "agda", "analyze_agda", "Agda"),
    (frozenset({"lean"}), "lean", "analyze_lean", "Lean"),
    (frozenset({"wolfram"}), "wolfram", "analyze_wolfram", "Wolfram"),
    (frozenset({"ocaml"}), "ocaml", "analyze_ocaml", "OCaml"),
    (frozenset({"solidity"}), "solidity", "analyze_solidity", "Solidity"),
    (frozenset({"csharp"}), "csharp", "analyze_csharp", "C#"),
    (frozenset({"cpp"}), "cpp", "analyze_cpp", "C++"),
    (frozenset({"zig"}), "zig", "analyze_zig", "Zig"),
    (frozenset({"nix"}), "nix", "analyze_nix_files", "Nix"),
    (frozenset({"elixir"}), "elixir", "analyze_elixir", "Elixir"),
    (frozenset({"erlang"}), "erlang", "analyze_erlang", "Erlang"),
    (frozenset({"elm"}), "elm", "analyze_elm", "Elm"),
    (frozenset({"fsharp"}), "fsharp", "analyze_fsharp", "F#"),
    (frozenset({"fortran"}), "fortran", "analyze_fortran", "Fortran"),
    (frozenset({"groovy"}), "groovy", "analyze_groovy", "Groovy"),
    (frozenset({"julia"}), "julia", "analyze_julia", "Julia"),
    (frozenset({"objective-c", "objc"}), "objc", "analyze_objc", "Obj-C"),
    (frozenset({"perl"}), "perl", "analyze_perl", "Perl"),
    (frozenset({"proto"}), "proto", "analyze_proto", "Proto"),
    (frozenset({"thrift"}), "thrift", "analyze_thrift", "Thrift"),
    (frozenset({"capnp"}), "capnp", "analyze_capnp", "Cap'n Proto"),
    (frozenset({"powershell"}), "powershell", "analyze_powershell", "PowerShell"),
    (frozenset({"gdscript"}), "gdscript", "analyze_gdscript", "GDScript"),
    (frozenset({"starlark"}), "starlark", "analyze_starlark", "Starlark"),
    (frozenset({"fish"}), "fish", "analyze_fish", "Fish"),
    (frozenset({"hlsl"}), "hlsl", "analyze_hlsl", "HLSL"),
    (frozenset({"ada"}), "ada", "analyze_ada", "Ada"),
    (frozenset({"d"}), "d_lang", "analyze_d", "D"),
    (frozenset({"nim"}), "nim", "analyze_nim", "Nim"),
    (frozenset({"r"}), "r_lang", "analyze_r", "R"),
    (frozenset({"bash", "shell"}), "bash", "analyze_bash", "Bash"),
    (frozenset({"sql"}), "sql", "analyze_sql", "SQL"),
    (frozenset({"dockerfile"}), "dockerfile", "analyze_dockerfile", "Dockerfile"),
    (frozenset({"hcl", "terraform"}), "hcl", "analyze_hcl", "HCL"),
    (frozenset({"vhdl"}), "vhdl", "analyze_vhdl", "VHDL"),
    (frozenset({"verilog"}), "verilog", "analyze_verilog", "Verilog"),
    (frozenset({"clojure"}), "clojure", "analyze_clojure", "Clojure"),
    (frozenset({"dart"}), "dart", "analyze_dart", "Dart"),
    (frozenset({"cobol"}), "cobol", "analyze_cobol", "COBOL"),
]


def _run_analysis(
    repo_root: Path,
    profile: RepoProfile,
    exclude_tests: bool = False,
    progress_callback: "callable | None" = None,
) -> tuple[list[Symbol], list, tuple[int, int, float] | None]:
    """Run static analysis to get symbols and edges.

    Only runs analysis for detected languages to avoid unnecessary work.
    Applies supply chain classification to all symbols.

    Args:
        repo_root: Path to the repository root.
        profile: Detected repository profile with language info.
        exclude_tests: If True, filter out symbols from test files after analysis.
        progress_callback: Optional callback(current, total, lang_name) for progress.

    Returns:
        (symbols, edges, coverage_stats) tuple. coverage_stats is (tested, total, pct)
        or None if no non-test functions exist. Coverage is computed BEFORE filtering.
    """
    import importlib
    from .supply_chain import classify_file, detect_package_roots

    all_symbols: list[Symbol] = []
    all_edges: list = []

    # Find which analyzers need to run based on detected languages
    detected_langs = set(profile.languages)
    analyzers_to_run = [
        (lang_set, mod, func, display)
        for lang_set, mod, func, display in LANGUAGE_ANALYZERS
        if lang_set & detected_langs
    ]
    total_analyzers = len(analyzers_to_run)

    # Run each analyzer with progress reporting
    for idx, (_lang_set, mod_name, func_name, display_name) in enumerate(analyzers_to_run):
        try:  # pragma: no cover (most analyzers require tree-sitter)
            # Try new package structure first (hypergumbo_lang_*), then fallback to core
            module = None
            for package in ("hypergumbo_lang_mainstream", "hypergumbo_lang_common", "hypergumbo_lang_extended1"):
                try:
                    module = importlib.import_module(f"{package}.{mod_name}")
                    break
                except ImportError:
                    continue
            if module is None:
                continue
            analyze_func = getattr(module, func_name)
            result = analyze_func(repo_root)
            all_symbols.extend(result.symbols)
            all_edges.extend(result.edges)
        except Exception:  # pragma: no cover
            pass  # Analysis failed or tree-sitter not available

        if progress_callback:  # pragma: no cover
            progress_callback(idx + 1, total_analyzers, display_name)

    # Compute coverage estimate BEFORE filtering (need test->production edges)
    coverage_stats = _estimate_test_coverage(all_symbols, all_edges)

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

    # Deduplicate edges by ID (some analyzers may produce duplicate edges)
    seen_edge_ids: set[str] = set()
    deduped_edges: list[Edge] = []
    for edge in all_edges:
        if edge.id not in seen_edge_ids:
            seen_edge_ids.add(edge.id)
            deduped_edges.append(edge)
    all_edges = deduped_edges

    return all_symbols, all_edges, coverage_stats


def _format_entrypoints(
    entrypoints: list[Entrypoint],
    symbols: list[Symbol],
    repo_root: Path,
    max_entries: int = 20,
    exclude_tests: bool = False,
) -> str:
    """Format detected entry points as a Markdown section."""
    if not entrypoints:
        return ""

    # Build symbol lookup for path info
    symbol_by_id = {s.id: s for s in symbols}

    # Sort by confidence (highest first)
    sorted_eps = sorted(entrypoints, key=lambda e: -e.confidence)

    lines = [_section_header("Entry Points", exclude_tests), ""]

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


def _format_datamodels(
    datamodels: "list[DataModel]",
    symbols: list[Symbol],
    repo_root: Path,
    max_entries: int = 30,
    exclude_tests: bool = False,
) -> str:
    """Format detected data models as a Markdown section.

    Args:
        datamodels: List of detected data models.
        symbols: All symbols for path lookup.
        repo_root: Repository root for relative paths.
        max_entries: Maximum number of models to show.
        exclude_tests: If True, add [IGNORING TESTS] marker to header.

    Returns:
        Markdown formatted section for data models.
    """
    if not datamodels:
        return ""

    # Build symbol lookup for path info
    symbol_by_id = {s.id: s for s in symbols}

    # Sort by confidence (highest first) - already sorted but ensure
    sorted_models = sorted(datamodels, key=lambda m: -m.confidence)

    lines = [_section_header("Data Models", exclude_tests), ""]

    for model in sorted_models[:max_entries]:
        sym = symbol_by_id.get(model.symbol_id)
        if sym:
            rel_path = sym.path
            if rel_path.startswith(str(repo_root)):
                rel_path = rel_path[len(str(repo_root)) + 1:]
            # Include framework if known
            if model.framework:
                lines.append(f"- `{sym.name}` ({model.framework} {model.label}) — `{rel_path}`")
            else:
                lines.append(f"- `{sym.name}` ({model.label}) — `{rel_path}`")
        else:
            lines.append(f"- `{model.symbol_id}` ({model.label})")

    if len(datamodels) > max_entries:
        lines.append(f"- ... and {len(datamodels) - max_entries} more data models")

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
    exclude_tests: bool = False,
    selected_symbols_out: list[Symbol] | None = None,
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
        selected_symbols_out: If provided, populated with the selected Symbol objects
            for stats tracking. The list is cleared and filled with symbols.
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
    for ep_path in entrypoint_files:  # pragma: no cover - requires integration with framework patterns
        if ep_path.startswith(repo_root_str):
            normalized_ep_files.add(ep_path[len(repo_root_str) + 1:])
        else:
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

    # Populate selected_symbols_out for stats tracking if provided
    if selected_symbols_out is not None:
        selected_symbols_out.clear()
        selected_symbols_out.extend(sym for _, sym in selected)

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

    lines = [_section_header("Key Symbols", exclude_tests), ""]
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
    progress: bool = False,
    cached_results: Optional[dict] = None,
    with_source: bool = False,
    stats_out: Optional[SketchStats] = None,
) -> str:
    """Generate a token-budgeted Markdown sketch of the repository.

    The sketch progressively includes content to fill the token budget
    (see ADR-0005 for full details):
    1. Header with title, description (always included)
    2. Overview: language breakdown, file counts, LOC (always included)
    3. Structure: top-level directory layout
    4. Frameworks: detected frameworks/libraries
    5. Tests: test file count, frameworks, coverage estimate
    6. Configuration: config file excerpts (heuristic + semantic)
    7. Entry Points: CLI commands, HTTP routes
    8. Data Models: ORM models, entities, core data structures
    9. Source Files: file listing by importance
    10. Key Symbols: functions, classes, types with centrality
    11. Additional Files: semantic + centrality ranked
    12. Source Files Content: actual code (--with-source only)
    13. Additional Files Content: code for semantic picks (--with-source only)

    Args:
        repo_root: Path to the repository root.
        max_tokens: Target tokens for output. Defaults to 4000 if None.
        exclude_tests: If True, skip analyzing test files for faster performance.
            When using cached_results, filters test symbols/edges at load time.
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
        progress: If True, show progress indicator with ETA to stderr.
        cached_results: If provided, use this behavior map instead of running
            analysis. Should be the parsed JSON from hypergumbo.results.json.
            Skips profile detection and analysis phases for faster generation.
            If None, auto-discovers cached results from ~/.cache/hypergumbo/.
        with_source: If True, append full source file contents after the sketch.
            Files are ordered by symbol importance density. Uses remaining
            token budget after other sections are filled.
        stats_out: If provided, a SketchStats object to populate with
            representativeness metrics. Tracks what fraction of the codebase's
            "importance" is captured in each section. Used for the "How
            Representative Is This Sketch?" table.

    Returns:
        Markdown-formatted sketch string.
    """
    import sys
    import time

    # Default to 4000 tokens if not specified (unified behavior)
    if max_tokens is None:
        max_tokens = 4000

    def _log(msg: str) -> None:
        if verbose:  # pragma: no cover
            print(f"[sketch] {msg}", file=sys.stderr)

    # Initialize progress reporter
    prog = SketchProgress()
    if not progress:
        prog.disable()

    t0 = time.time()
    _log("Starting sketch generation...")

    repo_root = Path(repo_root).resolve()

    # Auto-discover cached results from cache directory if not explicitly provided
    # If no cache exists, run analysis first to populate it
    if cached_results is None:
        from .sketch_embeddings import _get_results_cache_dir
        try:
            cache_dir = _get_results_cache_dir(repo_root)
            cached_path = cache_dir / "hypergumbo.results.json"
            if cached_path.exists():
                import json
                _log(f"Auto-discovered cached results: {cached_path}")
                cached_results = json.loads(cached_path.read_text())
            else:
                # No cache exists - run analysis to populate it
                _log("No cached results found, running analysis...")
                from .cli import run_behavior_map
                run_behavior_map(repo_root)
                # Now load the freshly generated cache
                if cached_path.exists():
                    import json
                    _log(f"Using freshly generated results: {cached_path}")
                    cached_results = json.loads(cached_path.read_text())
        except Exception:  # pragma: no cover - cache discovery errors shouldn't block sketch
            pass

    # Use cached profile if available, otherwise detect fresh
    using_cached_profile = cached_results is not None and "profile" in cached_results
    prog.start_phase("profile", cached=using_cached_profile)

    if using_cached_profile:
        _log("Using cached profile...")
        profile = RepoProfile.from_dict(cached_results["profile"])
    else:  # pragma: no cover - fallback when auto-discovery/auto-run fails
        _log(f"Detecting profile for {repo_root.name}...")
        profile = detect_profile(repo_root, extra_excludes=extra_excludes)

    _log(f"Profile ready in {time.time() - t0:.1f}s")
    prog.complete_phase("profile")
    repo_name = _get_repo_name(repo_root)

    # Collect source files early (needed for accurate LOC counts when exclude_tests=True)
    source_files = _collect_source_files(repo_root, profile, exclude_tests=exclude_tests)

    # Build base sections (always included)
    sections = []

    # Section 1: Header (always included, highest priority)
    # Include project description from README if available
    # Use cached README description if available (avoids loading embedding model)
    using_cached_readme = (
        cached_results is not None
        and "sketch_precomputed" in cached_results
        and cached_results["sketch_precomputed"].get("readme_description") is not None
    )
    prog.start_phase("readme", cached=using_cached_readme)
    if using_cached_readme:
        readme_desc = cached_results["sketch_precomputed"].get("readme_description")
    else:
        readme_desc = _extract_readme_description(repo_root)
    prog.complete_phase("readme")
    if readme_desc:
        header = (
            f"# {repo_name}\n\n"
            f"{readme_desc}\n\n"
            f"{_section_header('Overview', exclude_tests)}\n{_format_language_stats(profile, repo_root, extra_excludes, exclude_tests)}"
        )
    else:
        header = (
            f"# {repo_name}\n\n{_section_header('Overview', exclude_tests)}\n"
            f"{_format_language_stats(profile, repo_root, extra_excludes, exclude_tests)}"
        )
    sections.append(header)

    # Section 2: Structure (using tree format from the start)
    prog.start_phase("structure")
    excludes = list(DEFAULT_EXCLUDES)
    if extra_excludes:  # pragma: no cover
        excludes.extend(extra_excludes)
    structure = _format_structure_tree_fallback(repo_root, excludes, exclude_tests=exclude_tests)
    prog.complete_phase("structure")
    if structure:
        sections.append(structure)

    # Section 3: Frameworks
    prog.start_phase("frameworks")
    frameworks = _format_frameworks(profile, exclude_tests=exclude_tests)
    prog.complete_phase("frameworks")
    if frameworks:
        sections.append(frameworks)

    # Section 3.25: Tests (static summary - count and frameworks)
    prog.start_phase("tests")
    test_summary_section = _format_test_summary(repo_root, exclude_tests=exclude_tests)
    prog.complete_phase("tests")
    if test_summary_section:
        sections.append(test_summary_section)

    # Section 3.5: Configuration (extracted metadata from config files)
    # This section is high value for answering project metadata questions
    # (e.g., "what version of TypeScript?", "what license?", "what database?")
    t_config = time.time()

    # Use cached config info if available (avoids loading embedding model)
    using_cached_config = (
        cached_results is not None
        and "sketch_precomputed" in cached_results
        and cached_results["sketch_precomputed"].get("config_info") is not None
    )
    prog.start_phase("config", cached=using_cached_config)

    if using_cached_config:
        config_info = cached_results["sketch_precomputed"].get("config_info", "")
        _log("Using cached config info...")
    else:
        _log(f"Extracting config ({config_extraction_mode.value})...")

        # Create progress callback for config extraction telemetry
        def config_progress(current: int, total: int) -> None:  # pragma: no cover
            prog.update_item_progress("Embedding config files", current, total)

        config_info = _extract_config_info(
            repo_root,
            mode=config_extraction_mode,
            max_config_files=max_config_files,
            fleximax_lines=fleximax_lines,
            max_chunk_chars=max_chunk_chars,
            progress_callback=config_progress,
        )
    _log(f"Config extracted in {time.time() - t_config:.1f}s")
    prog.complete_phase("config")
    config_section = _format_config_section(config_info, exclude_tests=exclude_tests)
    if config_section:
        sections.append(config_section)

    # NOTE: Domain Vocabulary section removed per ADR-0005
    # TF-IDF terms were too generic to provide value

    # Combine base sections
    base_sketch = "\n\n".join(sections)
    base_tokens = estimate_tokens(base_sketch)

    # Detect shell integration tests (only if not excluding tests)
    # These are shell scripts that invoke the project's compiled binary
    shell_integration_count = 0
    if not exclude_tests:
        binary_names = _detect_project_binary_names(repo_root)
        if binary_names:
            shell_tests = _detect_shell_integration_tests(repo_root, binary_names)
            shell_integration_count = len(shell_tests)

    # Note: max_tokens is always set (defaults to 4000 in CLI)
    # If budget is very small, return truncated base sketch
    if max_tokens <= base_tokens:
        # Set token_budget before early return so Representativeness Table shows correct value
        if stats_out is not None:
            stats_out.token_budget = max_tokens
        prog.finish()
        return truncate_to_tokens(base_sketch, max_tokens)

    # We have room to expand - calculate remaining budget
    remaining_tokens = max_tokens - base_tokens

    # source_files already collected early (at start of function) for accurate LOC counts

    # Estimate tokens per file item
    # Typical line: "- `path/to/long/filename.py`" is ~50 chars = ~12 tokens
    tokens_per_file = 12

    # Estimate tokens per entry point or symbol item with docstring/signature
    # Typical line: "- `func(x: int, y: List[str]) -> Dict[str, Any]` (method) — Does X."
    # is ~100-150 chars = ~25-38 tokens. Use realistic estimate based on qwix data.
    tokens_per_symbol = 35

    # Run static analysis early to enable density-based source file ordering
    # (needed before the source files section)
    using_cached_analysis = (
        cached_results is not None
        and "nodes" in cached_results
        and remaining_tokens > 50
    )
    prog.start_phase("analysis", cached=using_cached_analysis)
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    coverage_stats: tuple[int, int, float] | None = None
    raw_in_degree: dict[str, int] = {}
    density_scores: dict[str, float] = {}

    def analysis_progress_with_budget(current: int, total: int, lang: str) -> None:  # pragma: no cover
        prog.update_item_progress(f"Analyzing {lang}", current, total)

    if remaining_tokens > 50:  # Run analysis if we have any room to expand
        if using_cached_analysis:
            # Use cached symbols and edges from behavior map
            _log("Using cached analysis results...")
            symbols = [Symbol.from_dict(n) for n in cached_results.get("nodes", [])]
            edges = [Edge.from_dict(e) for e in cached_results.get("edges", [])]

            # Compute coverage stats from cached symbols/edges BEFORE filtering
            # (coverage needs test symbols to compute transitively tested functions)
            coverage_stats = _estimate_test_coverage(symbols, edges)

            # Apply exclude_tests filter if requested
            if exclude_tests:
                # Filter out test symbols
                symbols = [s for s in symbols if not _is_test_path(s.path)]
                # Get IDs of remaining symbols for edge filtering
                remaining_ids = {s.id for s in symbols}
                # Filter edges to only include those between remaining symbols
                edges = [
                    e for e in edges
                    if e.src in remaining_ids and e.dst in remaining_ids
                ]
        else:  # pragma: no cover - fallback when auto-discovery/auto-run fails
            symbols, edges, coverage_stats = _run_analysis(
                repo_root, profile, exclude_tests=exclude_tests,
                progress_callback=analysis_progress_with_budget
            )

        # Compute raw in-degree and density scores for source file ordering
        raw_in_degree: dict[str, int] = {}  # Initialize for structure tree update
        if symbols and edges:
            raw_in_degree = compute_raw_in_degree(symbols, edges)
            by_file: dict[str, list[Symbol]] = {}
            for sym in symbols:
                if sym.path:
                    by_file.setdefault(sym.path, []).append(sym)
            density_scores = compute_symbol_importance_density(
                by_file, raw_in_degree, repo_root, min_loc=5
            )

    prog.complete_phase("analysis")

    # Update test summary with coverage stats if we got analysis results (only if not excluding tests)
    if coverage_stats is not None and not exclude_tests:
        # Find and replace the test summary section with coverage info
        updated_test_summary = _format_test_summary(
            repo_root, coverage_stats, shell_integration_count=shell_integration_count
        )
        for i, section in enumerate(sections):
            if section.startswith("## Tests"):
                sections[i] = updated_test_summary
                break
    elif shell_integration_count > 0 and not exclude_tests:  # pragma: no cover
        # No call graph coverage but we have shell integration tests
        # (rare: happens when analysis returns no countable symbols)
        updated_test_summary = _format_test_summary(
            repo_root, shell_integration_count=shell_integration_count
        )
        for i, section in enumerate(sections):
            if section.startswith("## Tests"):
                sections[i] = updated_test_summary
                break

    # Section 4: Entry points (if we have analysis results and budget)
    # ADR-0005: Entry Points come before Source Files as high-signal section
    # Track entrypoint files for B4: preserve in Key Symbols
    entrypoint_files: set[str] = set()
    entrypoints: list[Entrypoint] = []

    if remaining_tokens > 50 and symbols:
        entrypoints = detect_entrypoints(symbols, edges)
        if entrypoints:  # pragma: no cover - requires framework patterns to detect entrypoints
            # Build symbol lookup for extracting file paths
            symbol_by_id = {s.id: s for s in symbols}

            # Extract file paths from entrypoints (B4)
            for ep in entrypoints:
                sym = symbol_by_id.get(ep.symbol_id)
                if sym:
                    entrypoint_files.add(sym.path)

            # Entry points are high value, give them 33% of remaining (ADR-0005)
            budget_for_eps = remaining_tokens // 3
            max_eps = max(5, budget_for_eps // tokens_per_symbol)

            ep_section = _format_entrypoints(
                entrypoints, symbols, repo_root, max_entries=max_eps,
                exclude_tests=exclude_tests
            )
            if ep_section:
                sections.append(ep_section)
                # Track stats: compute confidence sum of selected entrypoints
                if stats_out is not None:
                    sorted_eps = sorted(entrypoints, key=lambda e: -e.confidence)
                    stats_out.entrypoints_confidence = sum(
                        ep.confidence for ep in sorted_eps[:max_eps]
                    )
                    stats_out.has_entrypoints = True

            # Recalculate remaining budget
            current_sketch = "\n\n".join(sections)
            current_tokens = estimate_tokens(current_sketch)
            remaining_tokens = max_tokens - current_tokens

    # Section 5: Data Models (if we have analysis results and budget)
    # ADR-0005: Data Models come after Entry Points, before Source Files
    datamodels: list = []  # Initialize for structure tree update
    if remaining_tokens > 50 and symbols:
        datamodels = detect_datamodels(symbols, edges)
        if datamodels:
            # Data Models get 20% of remaining budget (ADR-0005)
            budget_for_models = (remaining_tokens * 20) // 100
            max_models = max(3, budget_for_models // 20)  # ~20 tokens per model line

            dm_section = _format_datamodels(
                datamodels, symbols, repo_root, max_entries=max_models,
                exclude_tests=exclude_tests
            )
            if dm_section:
                sections.append(dm_section)
                # Track stats: compute confidence sum of selected datamodels
                if stats_out is not None:
                    sorted_models = sorted(datamodels, key=lambda m: -m.confidence)
                    stats_out.datamodels_confidence = sum(
                        dm.confidence for dm in sorted_models[:max_models]
                    )
                    stats_out.has_datamodels = True

            # Recalculate remaining budget
            current_sketch = "\n\n".join(sections)
            current_tokens = estimate_tokens(current_sketch)
            remaining_tokens = max_tokens - current_tokens

    # Compute stats totals if tracking is enabled
    # This is done after entrypoints and datamodels are detected so we have all data
    if stats_out is not None:
        stats_out.token_budget = max_tokens or 0
        # Total in-degree across all symbols
        stats_out.total_in_degree = sum(raw_in_degree.values())
        # Total confidence for framework concepts
        stats_out.total_entrypoint_confidence = sum(ep.confidence for ep in entrypoints)
        stats_out.total_datamodel_confidence = sum(dm.confidence for dm in datamodels)

    # Update Structure section with tree format (ADR-0005)
    # Now that we have analysis results, we can build a tree from important files
    # Always update to tree format for consistency (even if no important files)
    important_files: list[str] = []
    if symbols and raw_in_degree:
        important_files = _collect_important_files(
            repo_root=repo_root,
            source_files=source_files,
            entrypoints=entrypoints,
            datamodels=datamodels,
            symbols=symbols,
            centrality=raw_in_degree,  # Use in-degree as centrality proxy
        )

    # Always use tree format - _format_structure_tree handles empty important_files
    updated_structure = _format_structure_tree(
        repo_root, important_files, extra_excludes=extra_excludes,
        exclude_tests=exclude_tests
    )
    # Find and replace the Structure section
    for i, section in enumerate(sections):
        if section.startswith("## Structure"):
            sections[i] = updated_structure
            break

    # Section 6: Source files (if we have budget >= 50 tokens remaining)
    # Files are now ordered by symbol importance density when scores available
    if remaining_tokens > 50 and source_files:
        # ADR-0005: --with-source mode reduces file listing budget to prioritize code
        if with_source:
            # With source: shrink file listings to 15% to leave room for actual code
            budget_for_files = (remaining_tokens * 15) // 100  # 15% of remaining
        elif remaining_tokens < 300:
            # Default mode, small budgets: use 66% for file listings
            budget_for_files = (remaining_tokens * 2) // 3  # 66% at small budgets
        else:
            # Default mode, larger budgets: limit files to 25% for analysis
            budget_for_files = remaining_tokens // 4  # 25% at larger budgets
        max_source_files = max(5, budget_for_files // tokens_per_file)

        source_section = _format_source_files(
            repo_root,
            source_files,
            max_files=max_source_files,
            density_scores=density_scores if density_scores else None,
            exclude_tests=exclude_tests,
        )
        if source_section:
            sections.append(source_section)
            # Track stats: mark section as present, compute in-degree if available
            if stats_out is not None:
                stats_out.has_source_files = True
                if raw_in_degree:
                    # Replicate sorting logic to determine which files were selected
                    if density_scores:
                        sorted_files = sorted(
                            source_files,
                            key=lambda f: density_scores.get(str(f.relative_to(repo_root)), 0.0),
                            reverse=True,
                        )
                    else:  # pragma: no cover - defensive, density_scores usually exists
                        sorted_files = source_files
                    selected_files = {str(f) for f in sorted_files[:max_source_files]}
                    # Sum in-degree for symbols in selected files
                    stats_out.source_files_in_degree = sum(
                        raw_in_degree.get(s.id, 0)
                        for s in symbols if s.path and str(repo_root / s.path) in selected_files
                    )

        # Recalculate remaining budget
        current_sketch = "\n\n".join(sections)
        current_tokens = estimate_tokens(current_sketch)
        remaining_tokens = max_tokens - current_tokens

    # Section 7: Key symbols
    # IMPORTANT: Minimum Key Symbols guarantee
    # Always include at least MIN_KEY_SYMBOLS symbols when analysis produces results.
    # This addresses the issue where some projects (qwix, marlin, guacamole-client)
    # had 0 Key Symbols at 1k budget because budget was exhausted earlier.
    # Key Symbols is the most valuable section for code understanding, so we
    # guarantee its presence even if it means slight budget overage.
    prog.start_phase("symbols")
    MIN_KEY_SYMBOLS = 5

    if symbols:
        # Calculate symbol budget based on remaining tokens
        # ADR-0005: --with-source mode reduces Key Symbols budget to prioritize code
        if with_source and remaining_tokens > 200:
            # With source: shrink to 30% to leave room for actual code
            budget_for_symbols = (remaining_tokens * 30) // 100  # 30% of remaining
            max_symbols = max(MIN_KEY_SYMBOLS, budget_for_symbols // tokens_per_symbol)
        elif remaining_tokens > 200:
            # Default mode: use most of remaining budget for symbols
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

        # Track selected symbols for stats
        selected_key_symbols: list[Symbol] = [] if stats_out is not None else None  # type: ignore

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
            exclude_tests=exclude_tests,
            selected_symbols_out=selected_key_symbols,
        )
        if symbols_section:
            sections.append(symbols_section)
            # Track stats: mark section as present, compute in-degree if available
            if stats_out is not None:
                stats_out.has_key_symbols = True
                if selected_key_symbols and raw_in_degree:
                    stats_out.key_symbols_in_degree = sum(
                        raw_in_degree.get(s.id, 0) for s in selected_key_symbols
                    )

            # Recalculate remaining budget
            current_sketch = "\n\n".join(sections)
            current_tokens = estimate_tokens(current_sketch)
            remaining_tokens = max_tokens - current_tokens

    prog.complete_phase("symbols")

    # Section 8: Additional files (if we still have budget after everything else)
    # These are files NOT in source_files, ordered by hybrid semantic + centrality
    prog.start_phase("embedding")
    additional_files_selected: list[Path] = []  # Track for Additional Files Content section
    if remaining_tokens > 50:
        # ADR-0005: --with-source mode reduces Additional Files budget
        if with_source:
            # With source: shrink to 10% to leave room for actual code
            budget_for_files = (remaining_tokens * 10) // 100  # 10% of remaining
        else:
            # Default mode: use most of remaining budget minus small reserve
            budget_for_files = remaining_tokens - 10
        max_additional_files = max(1, budget_for_files // tokens_per_file)

        # Create progress callback for embedding telemetry
        def embedding_progress(current: int, total: int) -> None:  # pragma: no cover
            prog.update_item_progress("Embedding files", current, total)

        # Create progress callback for centrality computation
        def centrality_progress(current: int, total: int) -> None:  # pragma: no cover
            # Complete embedding phase when starting centrality
            if current == 0:
                prog.complete_phase("embedding")
                prog.start_phase("centrality")
            prog.update_item_progress("Computing centrality", current, total)

        # Use cached centrality scores if available (from run_behavior_map)
        cached_centrality = None
        if (
            cached_results is not None
            and "sketch_precomputed" in cached_results
            and cached_results["sketch_precomputed"].get("centrality_scores") is not None
        ):
            cached_centrality = cached_results["sketch_precomputed"].get("centrality_scores")

        additional_files_section, additional_files_selected, additional_centrality = _format_additional_files(
            repo_root,
            source_files=source_files,
            symbols=symbols,
            in_degree=raw_in_degree,
            max_files=max_additional_files,
            semantic_top_n=10,
            progress_callback=embedding_progress,
            centrality_progress_callback=centrality_progress,
            cached_centrality_scores=cached_centrality,
            exclude_tests=exclude_tests,
        )
        if additional_files_section:
            sections.append(additional_files_section)
            # Track stats: mark section as present, use mention centrality
            # Mention centrality = sum of in-degree-weighted symbol mentions in doc files
            if stats_out is not None:
                stats_out.has_additional_files = True
                # Use mention centrality (sum of in-degree for mentioned symbols)
                # This is more meaningful than symbol definitions for doc/config files
                stats_out.additional_files_in_degree = int(additional_centrality)

    prog.complete_phase("embedding")  # In case centrality was skipped
    prog.complete_phase("centrality")  # Complete centrality if it ran
    prog.start_phase("format")

    # Section 9: Source Files Content (if with_source is True and we have budget)
    # ADR-0005: Source Files Content gets 70% of remaining budget, all-or-nothing per file
    if with_source and source_files and max_tokens is not None:
        # Recalculate remaining budget
        current_sketch = "\n\n".join(sections)
        current_tokens = estimate_tokens(current_sketch)
        remaining_tokens = max_tokens - current_tokens

        if remaining_tokens > 100:  # Need meaningful space for source content
            source_content_lines = [_section_header("Source Files Content", exclude_tests), ""]

            # Order source files by density if available
            ordered_files = source_files
            if density_scores:
                # Sort by density descending
                ordered_files = sorted(
                    source_files,
                    key=lambda f: density_scores.get(str(f.relative_to(repo_root)), 0),
                    reverse=True,
                )

            source_tokens_used = 0
            # ADR-0005: allocate 70% of remaining for Source Files Content section
            source_budget = (remaining_tokens * 70) // 100

            # Track files with content shown for stats
            source_content_files_added: list[Path] = []

            for src_file in ordered_files:
                try:
                    content = src_file.read_text(errors="replace")

                    # Skip files with fewer than 5 lines of code
                    line_count = len(content.splitlines())
                    if line_count < 5:
                        continue

                    rel_path = src_file.relative_to(repo_root)
                    # Estimate full block size including markers, not just content
                    file_tokens = _estimate_file_block_tokens(str(rel_path), content)

                    # ADR-0005: All-or-nothing per file - skip if file doesn't fit
                    if source_tokens_used + file_tokens > source_budget:
                        continue

                    source_content_lines.extend(
                        _format_file_content_block(str(rel_path), content)
                    )
                    source_content_files_added.append(src_file)

                    source_tokens_used += file_tokens
                except (OSError, IOError):  # pragma: no cover - rare I/O errors
                    continue

            if len(source_content_lines) > 2:  # More than just header
                sections.append("\n".join(source_content_lines))
                # Track stats: mark section as present, compute in-degree if available
                if stats_out is not None:
                    stats_out.has_source_files_content = True
                    if raw_in_degree and source_content_files_added:
                        content_files = {str(f) for f in source_content_files_added}
                        stats_out.source_files_content_in_degree = sum(
                            raw_in_degree.get(s.id, 0)
                            for s in symbols if s.path and str(repo_root / s.path) in content_files
                        )

    # Section 10: Additional Files Content (if with_source and we have additional files)
    # Uses README-first hybrid ordering with round-robin selection from
    # README links, similarity-ranked, and centrality-ranked files.
    # Dynamic truncation based on median token count of already-selected files.
    if with_source and max_tokens is not None:
        # Recalculate remaining budget
        current_sketch = "\n\n".join(sections)
        current_tokens = estimate_tokens(current_sketch)
        remaining_tokens = max_tokens - current_tokens

        if remaining_tokens > 100:  # Need meaningful space for content
            # Use cached centrality scores if available
            cached_centrality = None
            if (
                cached_results is not None
                and "sketch_precomputed" in cached_results
                and cached_results["sketch_precomputed"].get("centrality_scores")
                is not None
            ):
                cached_centrality = cached_results["sketch_precomputed"].get(
                    "centrality_scores"
                )

            # Use the new README-first hybrid approach with content
            additional_content_section, additional_content_files_added, additional_content_centrality = (
                _format_additional_files(
                    repo_root,
                    source_files=source_files,
                    symbols=symbols,
                    in_degree=raw_in_degree,
                    max_files=max_additional_files,
                    semantic_top_n=10,
                    cached_centrality_scores=cached_centrality,
                    exclude_tests=exclude_tests,
                    token_budget=remaining_tokens - 50,  # Reserve 50 tokens
                    include_content=True,
                )
            )

            if additional_content_section:
                # Replace the header to be "Additional Files Content"
                header_to_replace = _section_header("Additional Files", exclude_tests)
                new_header = _section_header("Additional Files Content", exclude_tests)
                additional_content_section = additional_content_section.replace(
                    header_to_replace, new_header, 1
                )
                sections.append(additional_content_section)

                # Track stats using mention centrality
                if stats_out is not None:
                    stats_out.has_additional_files_content = True
                    # Use mention centrality for content files
                    stats_out.additional_files_content_in_degree = int(additional_content_centrality)

    # Combine all sections
    full_sketch = "\n\n".join(sections)

    # Final truncation to ensure we don't exceed budget
    prog.finish()
    result = truncate_to_tokens(full_sketch, max_tokens)

    # Ensure output ends with a newline (standard for text files)
    if not result.endswith("\n"):
        result += "\n"

    return result
