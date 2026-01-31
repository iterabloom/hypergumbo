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


# =============================================================================
# LANGUAGE_EXTENSIONS derivation (for backward compatibility with profile.py)
# =============================================================================

# Language name aliases for backward compatibility
# profile.py uses "shell" but taxonomy uses "bash"
_LANGUAGE_ALIASES: dict[str, str] = {
    "shell": "bash",
}


def get_language_extensions() -> dict[str, list[str]]:
    """Derive LANGUAGE_EXTENSIONS dict from LANGUAGES registry.

    This provides backward compatibility with profile.py which expects
    a dict mapping language names to extension patterns.

    Returns:
        Dict mapping language names to lists of extension patterns.
    """
    result: dict[str, list[str]] = {}
    for name, spec in LANGUAGES.items():
        result[name] = list(spec.extensions)

    # Add aliases for backward compatibility
    for alias, canonical in _LANGUAGE_ALIASES.items():
        if canonical in LANGUAGES:
            result[alias] = list(LANGUAGES[canonical].extensions)

    return result


def get_analyzable_extensions() -> dict[str, list[str]]:
    """Get extensions for ANALYZABLE languages only.

    This replaces SOURCE_EXTENSIONS in sketch.py, providing only the
    extensions for languages that have symbols to extract.

    Returns:
        Dict mapping language names to lists of extension patterns
        for ANALYZABLE languages only.
    """
    result: dict[str, list[str]] = {}
    for name, spec in LANGUAGES.items():
        if spec.roles == FileRole.ANALYZABLE:
            result[name] = list(spec.extensions)

    # Add aliases for backward compatibility
    for alias, canonical in _LANGUAGE_ALIASES.items():
        if canonical in LANGUAGES and LANGUAGES[canonical].roles == FileRole.ANALYZABLE:
            result[alias] = list(LANGUAGES[canonical].extensions)

    return result


# Pre-computed for efficiency (module-level singletons)
LANGUAGE_EXTENSIONS = get_language_extensions()
SOURCE_EXTENSIONS = get_analyzable_extensions()
