# SPDX-License-Identifier: AGPL-3.0-or-later
"""File taxonomy classification (ADR-0004).

This module provides the two-dimensional file classification system:
- Tier (provenance): Where does the file come from? (defined in supply_chain.py)
- Role (purpose): What is the file for? (defined here)

How It Works
------------
Every file has both a Tier and a Role:
- Tier answers "where from?" (first-party, internal dep, external dep, derived)
- Role answers "what for?" (analyzable, config, documentation, data)

These dimensions compose for analysis decisions:
- LOC counting: Tiers 1-2, CODE roles (analyzable + config + documentation)
- Symbol extraction: analysis_tiers, ANALYZABLE role only
- Additional Files: Tiers 1-2, CONFIG + DOCUMENTATION roles

Why This Design
---------------
The previous scattered constants (LANGUAGE_EXTENSIONS, SOURCE_EXTENSIONS,
CONFIG_FILES_BY_LANG, ADDITIONAL_FILES_EXCLUDES) are unified here into a
single source of truth. This eliminates duplication and makes the taxonomy
explicit.

Key insight: JSON files are ambiguous by extension alone. We need filename-level
disambiguation to tell config (package.json) from data (prices_data.json).

See docs/adr/0004-file-taxonomy.md for the full design rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Flag, auto
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional


class FileRole(Flag):
    """Purpose/content type of a file.

    Files may have multiple roles (e.g., JSON can be CONFIG or DATA depending
    on the specific file). Use bitwise operations to combine roles.
    """

    ANALYZABLE = auto()     # Has symbols to extract (functions, classes, etc.)
    CONFIG = auto()         # Parameterizes behavior (package.json, YAML configs)
    DOCUMENTATION = auto()  # Human-readable instructions/explanations
    DATA = auto()           # Raw information, not instructions


# What counts as "code" for LOC purposes
CODE_ROLES = FileRole.ANALYZABLE | FileRole.CONFIG | FileRole.DOCUMENTATION


@dataclass
class LanguageSpec:
    """Specification for a language/file type.

    Provides a single source of truth for how to handle files of this type.

    Attributes:
        name: Language identifier (e.g., "python", "json")
        extensions: Glob patterns for file extensions (e.g., ["*.py", "*.pyi"])
        roles: What role(s) files of this type can have
        config_files: Specific filenames that are CONFIG (for ambiguous types)
        data_patterns: Glob patterns for files that are DATA (for ambiguous types)
    """

    name: str
    extensions: list[str]
    roles: FileRole
    config_files: list[str] | None = None
    data_patterns: list[str] | None = None


# Size threshold for large files (bytes) - used for JSON disambiguation
LARGE_FILE_THRESHOLD = 100_000  # 100KB


# =============================================================================
# LANGUAGES REGISTRY - Single source of truth for all file types
# =============================================================================

LANGUAGES: dict[str, LanguageSpec] = {
    # -------------------------------------------------------------------------
    # Analyzable languages (have tree-sitter parsers, extract symbols)
    # -------------------------------------------------------------------------
    "python": LanguageSpec(
        name="python",
        extensions=["*.py", "*.pyi"],
        roles=FileRole.ANALYZABLE,
    ),
    "javascript": LanguageSpec(
        name="javascript",
        extensions=["*.js", "*.mjs", "*.cjs", "*.jsx"],
        roles=FileRole.ANALYZABLE,
    ),
    "typescript": LanguageSpec(
        name="typescript",
        extensions=["*.ts", "*.tsx", "*.d.ts"],
        roles=FileRole.ANALYZABLE,
    ),
    "go": LanguageSpec(
        name="go",
        extensions=["*.go"],
        roles=FileRole.ANALYZABLE,
    ),
    "rust": LanguageSpec(
        name="rust",
        extensions=["*.rs"],
        roles=FileRole.ANALYZABLE,
    ),
    "java": LanguageSpec(
        name="java",
        extensions=["*.java"],
        roles=FileRole.ANALYZABLE,
    ),
    "kotlin": LanguageSpec(
        name="kotlin",
        extensions=["*.kt", "*.kts"],
        roles=FileRole.ANALYZABLE,
    ),
    "scala": LanguageSpec(
        name="scala",
        extensions=["*.scala", "*.sc"],
        roles=FileRole.ANALYZABLE,
    ),
    "c": LanguageSpec(
        name="c",
        extensions=["*.c", "*.h"],
        roles=FileRole.ANALYZABLE,
    ),
    "cpp": LanguageSpec(
        name="cpp",
        extensions=["*.cpp", "*.cc", "*.cxx", "*.hpp", "*.hh", "*.hxx"],
        roles=FileRole.ANALYZABLE,
    ),
    "csharp": LanguageSpec(
        name="csharp",
        extensions=["*.cs"],
        roles=FileRole.ANALYZABLE,
    ),
    "fsharp": LanguageSpec(
        name="fsharp",
        extensions=["*.fs", "*.fsi", "*.fsx"],
        roles=FileRole.ANALYZABLE,
    ),
    "ruby": LanguageSpec(
        name="ruby",
        extensions=["*.rb", "*.rake"],
        roles=FileRole.ANALYZABLE,
    ),
    "php": LanguageSpec(
        name="php",
        extensions=["*.php"],
        roles=FileRole.ANALYZABLE,
    ),
    "swift": LanguageSpec(
        name="swift",
        extensions=["*.swift"],
        roles=FileRole.ANALYZABLE,
    ),
    "objc": LanguageSpec(
        name="objc",
        extensions=["*.m", "*.mm"],
        roles=FileRole.ANALYZABLE,
    ),
    "elixir": LanguageSpec(
        name="elixir",
        extensions=["*.ex", "*.exs"],
        roles=FileRole.ANALYZABLE,
    ),
    "erlang": LanguageSpec(
        name="erlang",
        extensions=["*.erl", "*.hrl"],
        roles=FileRole.ANALYZABLE,
    ),
    "haskell": LanguageSpec(
        name="haskell",
        extensions=["*.hs", "*.lhs"],
        roles=FileRole.ANALYZABLE,
    ),
    "ocaml": LanguageSpec(
        name="ocaml",
        extensions=["*.ml", "*.mli"],
        roles=FileRole.ANALYZABLE,
    ),
    "clojure": LanguageSpec(
        name="clojure",
        extensions=["*.clj", "*.cljs", "*.cljc", "*.edn"],
        roles=FileRole.ANALYZABLE,
    ),
    "lua": LanguageSpec(
        name="lua",
        extensions=["*.lua"],
        roles=FileRole.ANALYZABLE,
    ),
    "perl": LanguageSpec(
        name="perl",
        extensions=["*.pl", "*.pm", "*.t"],
        roles=FileRole.ANALYZABLE,
    ),
    "r": LanguageSpec(
        name="r",
        extensions=["*.r", "*.R"],
        roles=FileRole.ANALYZABLE,
    ),
    "julia": LanguageSpec(
        name="julia",
        extensions=["*.jl"],
        roles=FileRole.ANALYZABLE,
    ),
    "dart": LanguageSpec(
        name="dart",
        extensions=["*.dart"],
        roles=FileRole.ANALYZABLE,
    ),
    "zig": LanguageSpec(
        name="zig",
        extensions=["*.zig"],
        roles=FileRole.ANALYZABLE,
    ),
    "nim": LanguageSpec(
        name="nim",
        extensions=["*.nim", "*.nims", "*.nimble"],
        roles=FileRole.ANALYZABLE,
    ),
    "d": LanguageSpec(
        name="d",
        extensions=["*.d", "*.di"],
        roles=FileRole.ANALYZABLE,
    ),
    "ada": LanguageSpec(
        name="ada",
        extensions=["*.adb", "*.ads", "*.ada"],
        roles=FileRole.ANALYZABLE,
    ),
    "fortran": LanguageSpec(
        name="fortran",
        extensions=["*.f", "*.f90", "*.f95", "*.f03", "*.f08", "*.for", "*.F", "*.F90"],
        roles=FileRole.ANALYZABLE,
    ),
    "cobol": LanguageSpec(
        name="cobol",
        extensions=["*.cob", "*.cbl", "*.cobol", "*.cpy"],
        roles=FileRole.ANALYZABLE,
    ),
    "groovy": LanguageSpec(
        name="groovy",
        extensions=["*.groovy", "*.gvy", "*.gradle"],
        roles=FileRole.ANALYZABLE,
    ),
    "powershell": LanguageSpec(
        name="powershell",
        extensions=["*.ps1", "*.psm1", "*.psd1"],
        roles=FileRole.ANALYZABLE,
    ),
    "bash": LanguageSpec(
        name="bash",
        extensions=["*.sh", "*.bash", "*.zsh"],
        roles=FileRole.ANALYZABLE,
    ),
    "fish": LanguageSpec(
        name="fish",
        extensions=["*.fish"],
        roles=FileRole.ANALYZABLE,
    ),
    "sql": LanguageSpec(
        name="sql",
        extensions=["*.sql"],
        roles=FileRole.ANALYZABLE,
    ),
    "graphql": LanguageSpec(
        name="graphql",
        extensions=["*.graphql", "*.gql"],
        roles=FileRole.ANALYZABLE,
    ),
    "proto": LanguageSpec(
        name="proto",
        extensions=["*.proto"],
        roles=FileRole.ANALYZABLE,
    ),
    "thrift": LanguageSpec(
        name="thrift",
        extensions=["*.thrift"],
        roles=FileRole.ANALYZABLE,
    ),
    "vue": LanguageSpec(
        name="vue",
        extensions=["*.vue"],
        roles=FileRole.ANALYZABLE,
    ),
    "svelte": LanguageSpec(
        name="svelte",
        extensions=["*.svelte"],
        roles=FileRole.ANALYZABLE,
    ),
    "elm": LanguageSpec(
        name="elm",
        extensions=["*.elm"],
        roles=FileRole.ANALYZABLE,
    ),
    "purescript": LanguageSpec(
        name="purescript",
        extensions=["*.purs"],
        roles=FileRole.ANALYZABLE,
    ),
    "solidity": LanguageSpec(
        name="solidity",
        extensions=["*.sol"],
        roles=FileRole.ANALYZABLE,
    ),
    "circom": LanguageSpec(
        name="circom",
        extensions=["*.circom"],
        roles=FileRole.ANALYZABLE,
    ),
    "verilog": LanguageSpec(
        name="verilog",
        extensions=["*.v", "*.sv", "*.svh"],
        roles=FileRole.ANALYZABLE,
    ),
    "vhdl": LanguageSpec(
        name="vhdl",
        extensions=["*.vhd", "*.vhdl"],
        roles=FileRole.ANALYZABLE,
    ),
    # Additional analyzable languages (from profile.py)
    "commonlisp": LanguageSpec(
        name="commonlisp",
        extensions=["*.lisp", "*.lsp", "*.cl", "*.asd"],
        roles=FileRole.ANALYZABLE,
    ),
    "agda": LanguageSpec(
        name="agda",
        extensions=["*.agda", "*.lagda", "*.lagda.md"],
        roles=FileRole.ANALYZABLE,
    ),
    "lean": LanguageSpec(
        name="lean",
        extensions=["*.lean"],
        roles=FileRole.ANALYZABLE,
    ),
    "wolfram": LanguageSpec(
        name="wolfram",
        extensions=["*.wl", "*.wls", "*.nb"],
        roles=FileRole.ANALYZABLE,
    ),
    "llvm_ir": LanguageSpec(
        name="llvm_ir",
        extensions=["*.ll"],
        roles=FileRole.ANALYZABLE,
    ),
    "asm": LanguageSpec(
        name="asm",
        extensions=["*.s", "*.asm", "*.S"],
        roles=FileRole.ANALYZABLE,
    ),
    "glsl": LanguageSpec(
        name="glsl",
        extensions=["*.glsl", "*.vert", "*.frag", "*.geom", "*.comp", "*.tesc", "*.tese"],
        roles=FileRole.ANALYZABLE,
    ),
    "nix": LanguageSpec(
        name="nix",
        extensions=["*.nix"],
        roles=FileRole.ANALYZABLE,
    ),
    "cuda": LanguageSpec(
        name="cuda",
        extensions=["*.cu", "*.cuh"],
        roles=FileRole.ANALYZABLE,
    ),
    "gdscript": LanguageSpec(
        name="gdscript",
        extensions=["*.gd"],
        roles=FileRole.ANALYZABLE,
    ),
    "hlsl": LanguageSpec(
        name="hlsl",
        extensions=["*.hlsl", "*.hlsli", "*.fx"],
        roles=FileRole.ANALYZABLE,
    ),
    "wgsl": LanguageSpec(
        name="wgsl",
        extensions=["*.wgsl"],
        roles=FileRole.ANALYZABLE,
    ),
    "capnp": LanguageSpec(
        name="capnp",
        extensions=["*.capnp"],
        roles=FileRole.ANALYZABLE,
    ),
    "jupyter": LanguageSpec(
        name="jupyter",
        extensions=["*.ipynb"],
        roles=FileRole.ANALYZABLE,
    ),
    "blade": LanguageSpec(
        name="blade",
        extensions=["*.blade.php"],
        roles=FileRole.ANALYZABLE,
    ),
    "gnuplot": LanguageSpec(
        name="gnuplot",
        extensions=["*.gnuplot", "*.gp", "*.plt"],
        roles=FileRole.ANALYZABLE,
    ),
    "handlebars": LanguageSpec(
        name="handlebars",
        extensions=["*.hbs", "*.handlebars"],
        roles=FileRole.ANALYZABLE,
    ),
    "tlaplus": LanguageSpec(
        name="tlaplus",
        extensions=["*.tla"],
        roles=FileRole.ANALYZABLE,
    ),
    "just": LanguageSpec(
        name="just",
        extensions=["justfile", "Justfile", ".justfile", "*.just"],
        roles=FileRole.CONFIG,
    ),
    "mermaid": LanguageSpec(
        name="mermaid",
        extensions=["*.mmd", "*.mermaid"],
        roles=FileRole.DOCUMENTATION,
    ),
    "qml": LanguageSpec(
        name="qml",
        extensions=["*.qml"],
        roles=FileRole.ANALYZABLE,
    ),
    "latex": LanguageSpec(
        name="latex",
        extensions=["*.tex", "*.sty", "*.cls"],
        roles=FileRole.DOCUMENTATION,  # LaTeX is typically documentation
    ),

    # -------------------------------------------------------------------------
    # Documentation languages
    # -------------------------------------------------------------------------
    "markdown": LanguageSpec(
        name="markdown",
        extensions=["*.md", "*.markdown"],
        roles=FileRole.DOCUMENTATION,
    ),
    "rst": LanguageSpec(
        name="rst",
        extensions=["*.rst"],
        roles=FileRole.DOCUMENTATION,
    ),
    "asciidoc": LanguageSpec(
        name="asciidoc",
        extensions=["*.adoc", "*.asciidoc"],
        roles=FileRole.DOCUMENTATION,
    ),

    # -------------------------------------------------------------------------
    # Config languages (pure config, no symbol extraction)
    # -------------------------------------------------------------------------
    "yaml": LanguageSpec(
        name="yaml",
        extensions=["*.yaml", "*.yml"],
        roles=FileRole.CONFIG,
    ),
    "toml": LanguageSpec(
        name="toml",
        extensions=["*.toml"],
        roles=FileRole.CONFIG,
    ),
    "ini": LanguageSpec(
        name="ini",
        extensions=["*.ini", "*.cfg"],
        roles=FileRole.CONFIG,
    ),
    "xml": LanguageSpec(
        name="xml",
        extensions=["*.xml"],
        roles=FileRole.CONFIG,
    ),
    "html": LanguageSpec(
        name="html",
        extensions=["*.html", "*.htm"],
        roles=FileRole.CONFIG,  # HTML is structural config, not really "code"
    ),
    # WI-novob: Rails view templates (erb/haml/slim). CONFIG, like html — they
    # are structural HTML markup with embedded logic, not prose (DOCUMENTATION)
    # and not analyzable source (no dedicated analyzer). The view-template linker
    # stamps Symbol.language from these extensions (.html.erb → erb); without a
    # LanguageSpec they were absent from the WI-kunut language-axis union and
    # tripped axis_conformance. Not folded to ruby: an .html.erb is an HTML
    # template that embeds Ruby, not Ruby source.
    "erb": LanguageSpec(
        name="erb",
        extensions=["*.erb"],
        roles=FileRole.CONFIG,
    ),
    "haml": LanguageSpec(
        name="haml",
        extensions=["*.haml"],
        roles=FileRole.CONFIG,
    ),
    "slim": LanguageSpec(
        name="slim",
        extensions=["*.slim"],
        roles=FileRole.CONFIG,
    ),
    "css": LanguageSpec(
        name="css",
        extensions=["*.css", "*.scss", "*.sass", "*.less"],
        roles=FileRole.CONFIG,  # Styling config
    ),
    "dockerfile": LanguageSpec(
        name="dockerfile",
        extensions=["Dockerfile", "Dockerfile.*", "*.dockerfile"],
        roles=FileRole.CONFIG,
    ),
    "makefile": LanguageSpec(
        name="makefile",
        extensions=["Makefile", "*.mk"],
        roles=FileRole.CONFIG,
    ),
    "hcl": LanguageSpec(
        name="hcl",
        extensions=["*.hcl", "*.tf", "*.tfvars"],
        roles=FileRole.CONFIG,
    ),
    "cmake": LanguageSpec(
        name="cmake",
        extensions=["CMakeLists.txt", "*.cmake"],
        roles=FileRole.CONFIG,
    ),
    "starlark": LanguageSpec(
        name="starlark",
        extensions=["BUILD", "BUILD.bazel", "BUCK", "*.bzl"],
        roles=FileRole.CONFIG,
    ),
    # WI-gijot: jsonnet has a tree-sitter grammar and a working analyzer
    # in hypergumbo-lang-extended1/jsonnet.py, but was missing from this
    # taxonomy until 2026-04-26. Used widely for Grafana dashboards
    # (Grafonnet) and Tanka manifests; observed as the source-language
    # of legitimate boundary nodes in alertmanager + prometheus.
    "jsonnet": LanguageSpec(
        name="jsonnet",
        extensions=["*.jsonnet", "*.libsonnet"],
        roles=FileRole.CONFIG,
    ),

    # -------------------------------------------------------------------------
    # Ambiguous - needs filename-level disambiguation
    # -------------------------------------------------------------------------
    "json": LanguageSpec(
        name="json",
        extensions=["*.json"],
        roles=FileRole.CONFIG | FileRole.DATA,
        config_files=[
            # JavaScript/TypeScript ecosystem
            "package.json",
            "tsconfig.json",
            "tsconfig.base.json",
            "jsconfig.json",
            ".eslintrc.json",
            ".prettierrc.json",
            ".babelrc.json",
            # Editor/IDE
            ".vscode/settings.json",
            ".vscode/launch.json",
            ".vscode/tasks.json",
            # Other
            "composer.json",
            "appsettings.json",
            "manifest.json",
        ],
        data_patterns=[
            # Explicit data patterns
            "*_data.json",
            "*_dataset.json",
            "*-data.json",
            "*-dataset.json",
            # Common data directories
            "**/fixtures/*.json",
            "**/fixtures/**/*.json",
            "**/data/*.json",
            "**/data/**/*.json",
            "**/seed/*.json",
            "**/mock/*.json",
            "**/mocks/*.json",
            # Test data
            "**/test_data/*.json",
            "**/testdata/*.json",
            # Specific known data files (can be extended)
            "model_prices*.json",
        ],
    ),
}


# Build extension-to-language lookup for efficient matching
_EXTENSION_MAP: dict[str, str] = {}
for _lang_name, _spec in LANGUAGES.items():
    for _ext in _spec.extensions:
        # Handle both "*.py" and "Dockerfile" patterns
        if _ext.startswith("*."):
            _EXTENSION_MAP[_ext[1:].lower()] = _lang_name  # ".py" -> "python"
        else:
            _EXTENSION_MAP[_ext.lower()] = _lang_name  # "Dockerfile" -> "dockerfile"


def get_language(path: Path) -> Optional[str]:
    """Get the language name for a file based on its extension.

    Args:
        path: Path to the file.

    Returns:
        Language name (e.g., "python", "json") or None if unknown.
    """
    # Try exact filename match first (for Dockerfile, Makefile, etc.)
    if path.name.lower() in _EXTENSION_MAP:
        return _EXTENSION_MAP[path.name.lower()]

    # Try extension match
    suffix = path.suffix.lower()
    if suffix in _EXTENSION_MAP:
        return _EXTENSION_MAP[suffix]

    return None


def _matches_patterns(path: Path, patterns: list[str]) -> bool:
    """Check if path matches any of the glob patterns.

    Handles both filename patterns (e.g., "*_data.json") and
    path patterns (e.g., "**/fixtures/*.json").
    """
    path_str = str(path)
    name = path.name

    for pattern in patterns:
        # For patterns with path separators, match against full path
        if "/" in pattern or "\\" in pattern:
            if fnmatch(path_str, pattern):
                return True
            # Also try with forward slashes normalized (Windows paths)
            if fnmatch(path_str.replace("\\", "/"), pattern):  # pragma: no cover
                return True  # pragma: no cover
        else:
            # Simple filename pattern
            if fnmatch(name, pattern):
                return True

    return False


def get_role(path: Path) -> Optional[FileRole]:
    """Get the role for a file based on its type and name.

    For ambiguous types (like JSON), applies disambiguation rules:
    1. Check explicit config_files list
    2. Check data_patterns
    3. Check file size (large files likely data)
    4. Default to primary role

    Args:
        path: Path to the file.

    Returns:
        FileRole for the file, or None if unknown file type.
    """
    lang = get_language(path)
    if lang is None:
        return None

    spec = LANGUAGES[lang]

    # If unambiguous (single role), return it directly
    if spec.roles in (FileRole.ANALYZABLE, FileRole.CONFIG, FileRole.DOCUMENTATION, FileRole.DATA):
        return spec.roles

    # Ambiguous type - need disambiguation
    # Check explicit config files first
    if spec.config_files and path.name in spec.config_files:
        return FileRole.CONFIG

    # Check data patterns
    if spec.data_patterns and _matches_patterns(path, spec.data_patterns):
        return FileRole.DATA

    # Size heuristic for JSON - large files are likely data
    if lang == "json":
        try:
            if path.stat().st_size > LARGE_FILE_THRESHOLD:
                return FileRole.DATA
        except OSError:  # pragma: no cover
            pass  # pragma: no cover

    # Default: first role in the combined flags (CONFIG for JSON)
    # This is conservative - treat unknown JSON as config rather than data
    if FileRole.CONFIG in spec.roles:
        return FileRole.CONFIG

    # Fallback for ambiguous types without CONFIG role (defensive)
    return FileRole.DATA  # pragma: no cover


def is_analyzable(path: Path) -> bool:
    """Check if a file should be analyzed for symbols.

    Only ANALYZABLE files have symbols (functions, classes, etc.) to extract.

    Args:
        path: Path to the file.

    Returns:
        True if the file should be analyzed for symbols.
    """
    role = get_role(path)
    return role == FileRole.ANALYZABLE


def is_code(path: Path) -> bool:
    """Check if a file counts as "code" for LOC purposes.

    Code = ANALYZABLE + CONFIG + DOCUMENTATION
    Data files do not count as code.

    Args:
        path: Path to the file.

    Returns:
        True if the file should be counted in LOC statistics.
    """
    role = get_role(path)
    if role is None:
        return False
    return bool(role & CODE_ROLES)


def is_additional_file_candidate(path: Path) -> bool:
    """Check if a file is a candidate for Additional Files section.

    Additional Files should be CONFIG or DOCUMENTATION files that provide
    useful context for understanding the project. DATA files and unknown
    file types (binary files, etc.) are excluded.

    This is the role-based filter from ADR-0004 Phase 4. Note that callers
    may apply additional pattern-based exclusions for boilerplate files
    like LICENSE, CODEOWNERS, etc.

    Args:
        path: Path to the file.

    Returns:
        True if the file has CONFIG or DOCUMENTATION role.
    """
    role = get_role(path)
    if role is None:
        return False
    return role in (FileRole.CONFIG, FileRole.DOCUMENTATION)


def additional_file_candidates(
    repo_root: Path,
    all_files: "list[Path]",
    content_source_paths: "set[str]",
    exclude_patterns: "list[str]",
) -> "list[Path]":
    """The non-source CONFIG/DOC files eligible for the Additional-Files surface.

    Single source of truth shared by two callers (file-anchor:F1 + F4):

    * the orchestrator's file-anchor synthesis (``all_analyzers``), which mints a
      ``kind="file"`` anchor per candidate so the centrality keys are real node
      paths (the WI-rajod subset invariant); and
    * the ``additional_file_centrality_scores`` producer (``cli``), which scores
      these same candidates.

    A candidate must: NOT be a content-source path (``content_source_paths`` is
    the set of paths that already have a non-file-kind node — file-anchor:F4
    keys on *content*, not bare anchors, so the anchors this list drives don't
    re-exclude themselves); not be hidden; pass :func:`is_additional_file_candidate`
    (CONFIG/DOCUMENTATION role); not match an ``exclude_patterns`` glob; AND have
    a resolvable language (:func:`get_language` not ``None``) so it can be
    anchored with a valid ``make_file_id`` / ``Symbol.language`` and the subset
    invariant holds for every key.

    Args:
        repo_root: Repository root (candidate paths are returned absolute,
            relativized by the caller).
        all_files: Discovered files (e.g. ``FileIndex.all_files()``).
        content_source_paths: Repo-relative paths that already carry a
            non-file-kind node.
        exclude_patterns: Boilerplate globs (e.g. ``DEFAULT_EXCLUDES`` +
            ``ADDITIONAL_FILES_EXCLUDES``), matched against the file name and
            each path part.

    Returns:
        Candidate file paths (absolute), each with a resolvable language.
    """
    from fnmatch import fnmatch

    candidates: list[Path] = []
    for f in all_files:
        rel_path = f.relative_to(repo_root)
        rel_str = str(rel_path)
        if rel_str in content_source_paths:
            continue
        if any(p.startswith(".") for p in rel_path.parts):
            continue
        if not is_additional_file_candidate(f):
            continue
        # Defensive: every name/extension that resolves to a CONFIG/DOC role in
        # the taxonomy registry today also resolves a language, so this guard is
        # unreachable for current entries. It protects the subset invariant from
        # a future registry entry that adds a CONFIG/DOC role without a language
        # (which would mint a language=None anchor and break make_file_id).
        if get_language(f) is None:  # pragma: no cover
            continue
        # A discovered path can be a broken symlink (Path.is_file() follows the
        # link and is False when the target is absent). Such a file is not
        # readable, so neither anchoring it (file-anchor:F1) nor scoring it
        # (F4 centrality) is meaningful — the html analyzer skips it too.
        if not f.is_file():
            continue
        excluded = False
        for pattern in exclude_patterns:
            if fnmatch(f.name, pattern) or any(
                fnmatch(part, pattern) for part in rel_path.parts
            ):
                excluded = True
                break
        if excluded:
            continue
        candidates.append(f)
    return candidates


# =============================================================================
# LANGUAGE_EXTENSIONS derivation (for backward compatibility with profile.py)
# =============================================================================

# Language name aliases. Callers that ingest a non-canonical name (e.g.,
# legacy profiles that still emit "shell") resolve it via
# ``LANGUAGE_ALIASES.get(name, name)``. The dict is NOT injected as
# duplicate keys into LANGUAGE_EXTENSIONS / SOURCE_EXTENSIONS — see
# get_language_extensions docstring (INV-tosum).
LANGUAGE_ALIASES: dict[str, str] = {
    "shell": "bash",
}


def get_language_extensions() -> dict[str, list[str]]:
    """Derive LANGUAGE_EXTENSIONS dict from LANGUAGES registry.

    Returns one entry per canonical language. Aliases are *not* injected
    as duplicate keys — historically they were (INV-tosum), which caused
    profile.py to enumerate the same .sh files twice ('bash' + 'shell').
    Callers that need alias resolution use LANGUAGE_ALIASES directly.

    Returns:
        Dict mapping canonical language names to lists of extension patterns.
    """
    return {name: list(spec.extensions) for name, spec in LANGUAGES.items()}


def get_analyzable_extensions() -> dict[str, list[str]]:
    """Get extensions for ANALYZABLE languages only.

    Returns one entry per canonical analyzable language. Aliases are not
    injected as duplicate keys — see ``get_language_extensions`` (INV-tosum).

    Returns:
        Dict mapping canonical language names to lists of extension patterns
        for ANALYZABLE languages only.
    """
    return {
        name: list(spec.extensions)
        for name, spec in LANGUAGES.items()
        if spec.roles == FileRole.ANALYZABLE
    }


# Pre-computed for efficiency (module-level singletons)
LANGUAGE_EXTENSIONS = get_language_extensions()
SOURCE_EXTENSIONS = get_analyzable_extensions()
