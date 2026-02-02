"""Runtime warnings for partial installations (ADR-0010 Item 8).

This module provides diagnostic warnings when hypergumbo detects that:
1. Files exist for a language but no analyzer is installed for it
2. A linker has partial requirements met (e.g., Java native methods but no C JNI functions)

These warnings help developers diagnose missing functionality and understand
which packages to install for complete analysis.

How It Works
------------
After profile detection identifies languages in the repo, we compare against
registered analyzers (discovered via entry_points). Languages with files but
no analyzer trigger "unanalyzed files" warnings.

After linkers run, we check their requirements using the LinkerRequirement
system. Linkers with partial requirements (some met, some not) trigger
"partial linker" warnings, suggesting which packages might be missing.

Why This Design
---------------
- Non-intrusive: Warnings go to stderr, don't affect analysis output
- Actionable: Include specific package names and pip commands
- Future-proof: Package mappings align with ADR-0010 modular structure
- Testable: Functions return warning messages for assertion in tests

Usage
-----
In cli.py after analysis:

    from .partial_install_warnings import check_partial_install_warnings
    warnings = check_partial_install_warnings(profile, linker_ctx)
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)
"""

from __future__ import annotations

import warnings as python_warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .linkers.registry import LinkerContext
    from .profile import RepoProfile

# =============================================================================
# Language -> Package Mapping (ADR-0010)
# =============================================================================

# Maps language names to the package that provides their analyzer.
# This aligns with the ADR-0010 modular package structure.
# Note: Currently all analyzers are in hypergumbo (monorepo), but this
# mapping enables future modularization.

LANGUAGE_PACKAGES: dict[str, str] = {
    # hypergumbo-lang-mainstream (ADR-0010)
    "python": "hypergumbo-lang-mainstream",
    "javascript": "hypergumbo-lang-mainstream",
    "typescript": "hypergumbo-lang-mainstream",
    "java": "hypergumbo-lang-mainstream",
    "c": "hypergumbo-lang-mainstream",
    "cpp": "hypergumbo-lang-mainstream",
    "csharp": "hypergumbo-lang-mainstream",
    "go": "hypergumbo-lang-mainstream",
    "rust": "hypergumbo-lang-mainstream",
    "ruby": "hypergumbo-lang-mainstream",
    "php": "hypergumbo-lang-mainstream",
    "swift": "hypergumbo-lang-mainstream",
    "kotlin": "hypergumbo-lang-mainstream",
    # hypergumbo-lang-common (ADR-0010)
    "scala": "hypergumbo-lang-common",
    "bash": "hypergumbo-lang-common",
    "sql": "hypergumbo-lang-common",
    "html": "hypergumbo-lang-common",
    "css": "hypergumbo-lang-common",
    "dockerfile": "hypergumbo-lang-common",
    "lua": "hypergumbo-lang-common",
    "perl": "hypergumbo-lang-common",
    "haskell": "hypergumbo-lang-common",
    "ocaml": "hypergumbo-lang-common",
    "elixir": "hypergumbo-lang-common",
    "erlang": "hypergumbo-lang-common",
    "clojure": "hypergumbo-lang-common",
    "fsharp": "hypergumbo-lang-common",
    "groovy": "hypergumbo-lang-common",
    "dart": "hypergumbo-lang-common",
    "julia": "hypergumbo-lang-common",
    "r": "hypergumbo-lang-common",
    "elm": "hypergumbo-lang-common",
    # hypergumbo-lang-extended1 (ADR-0010)
    "zig": "hypergumbo-lang-extended1",
    "nim": "hypergumbo-lang-extended1",
    "agda": "hypergumbo-lang-extended1",
    "lean": "hypergumbo-lang-extended1",
    "cobol": "hypergumbo-lang-extended1",
    "solidity": "hypergumbo-lang-extended1",
    "verilog": "hypergumbo-lang-extended1",
    "vhdl": "hypergumbo-lang-extended1",
    "fortran": "hypergumbo-lang-extended1",
    "ada": "hypergumbo-lang-extended1",
    "d": "hypergumbo-lang-extended1",
    "wolfram": "hypergumbo-lang-extended1",
    "cuda": "hypergumbo-lang-extended1",
    "glsl": "hypergumbo-lang-extended1",
    "hlsl": "hypergumbo-lang-extended1",
    "wgsl": "hypergumbo-lang-extended1",
    "gdscript": "hypergumbo-lang-extended1",
    "nix": "hypergumbo-lang-extended1",
    "proto": "hypergumbo-lang-extended1",
    "graphql": "hypergumbo-lang-extended1",
    "thrift": "hypergumbo-lang-extended1",
    "capnp": "hypergumbo-lang-extended1",
    "powershell": "hypergumbo-lang-extended1",
    "fish": "hypergumbo-lang-extended1",
    "objc": "hypergumbo-lang-extended1",
    "vue": "hypergumbo-lang-extended1",
    "svelte": "hypergumbo-lang-extended1",
}

# Linker -> Required Languages mapping
# Used to suggest which packages are needed for linker requirements
# Keys must match the LinkerRequirement.name values from each linker
LINKER_LANGUAGE_REQUIREMENTS: dict[str, dict[str, list[str]]] = {
    "jni": {
        "java_native_methods": ["java"],
        "c_cpp_jni_functions": ["c"],
    },
    "swift_objc": {
        "swift_functions": ["swift"],
        "objc_functions": ["objc"],
    },
}

# Languages that don't have analyzers (config/markup only)
# These are detected in profile but shouldn't trigger warnings
CONFIG_ONLY_LANGUAGES: set[str] = {
    "json", "yaml", "toml", "xml", "ini", "markdown", "rst", "asciidoc",
    "makefile", "cmake", "starlark", "hcl",
}


@dataclass
class PartialInstallWarning:
    """A warning about partial installation.

    Attributes:
        category: Type of warning ("unanalyzed_files" or "partial_linker")
        message: Human-readable warning message
        language: For unanalyzed_files, the language missing an analyzer
        linker: For partial_linker, the linker name
        suggested_package: Package to install to resolve the issue
    """

    category: str
    message: str
    language: str | None = None
    linker: str | None = None
    suggested_package: str | None = None


def _get_registered_analyzer_languages() -> set[str]:
    """Get the set of languages that have registered analyzers.

    Discovers analyzers via entry_points and extracts their language names.
    This uses a heuristic: analyzer names often match language names.
    """
    from .analyze.all_analyzers import get_analyzers

    registered_languages: set[str] = set()

    for spec in get_analyzers():
        # Analyzer names are often language names or language-related
        # e.g., "python", "go", "rust", "javascript_ts", "c", "cpp"
        name = spec.name.lower()

        # Map common analyzer name patterns to language names
        if name in ("javascript_ts", "js_ts", "js"):
            registered_languages.add("javascript")
            registered_languages.add("typescript")
        elif name in ("c_cpp", "cpp"):
            registered_languages.add("cpp")
            # C analyzer is often separate
        elif name == "objc":
            registered_languages.add("objc")
        elif "_" in name:
            # Handle names like "swift_objc" - both are supported
            for part in name.split("_"):
                if part in LANGUAGE_PACKAGES:
                    registered_languages.add(part)
        else:
            registered_languages.add(name)

    return registered_languages


def check_unanalyzed_files(
    profile: "RepoProfile",
    registered_languages: set[str] | None = None,
) -> list[PartialInstallWarning]:
    """Check for languages with files but no registered analyzer.

    Args:
        profile: The detected repository profile (languages, frameworks)
        registered_languages: Set of languages with registered analyzers.
            If None, discovers from entry_points.

    Returns:
        List of warnings for languages with unanalyzed files.
    """
    if registered_languages is None:
        registered_languages = _get_registered_analyzer_languages()

    warnings: list[PartialInstallWarning] = []

    for lang, stats in profile.languages.items():
        # Skip config-only languages (no analyzer expected)
        if lang in CONFIG_ONLY_LANGUAGES:
            continue

        # Skip if analyzer is registered
        if lang in registered_languages:
            continue

        # Get package suggestion
        package = LANGUAGE_PACKAGES.get(lang)

        # Build warning message
        file_count = stats.files
        if package:
            message = (
                f"Detected {file_count} {lang} file(s) but {lang.title()} analyzer "
                f"not installed.\n"
                f"  Install {package} for {lang.title()} support:\n"
                f"    pip install {package}"
            )
        else:
            message = (
                f"Detected {file_count} {lang} file(s) but no analyzer is available "
                f"for {lang.title()}."
            )

        warnings.append(PartialInstallWarning(
            category="unanalyzed_files",
            message=message,
            language=lang,
            suggested_package=package,
        ))

    return warnings


def check_partial_linker_requirements(
    linker_ctx: "LinkerContext",
) -> list[PartialInstallWarning]:
    """Check for linkers with partial requirements (some met, some not).

    This helps diagnose cases like:
    - JNI linker found Java native methods but no C JNI functions
    - Swift/ObjC linker found Swift code but no Objective-C

    Args:
        linker_ctx: The linker context after analysis

    Returns:
        List of warnings for linkers with partial requirements.
    """
    from .linkers.registry import check_linker_requirements

    warnings: list[PartialInstallWarning] = []
    diagnostics = check_linker_requirements(linker_ctx)

    for diag in diagnostics:
        # Skip if all requirements met or none met
        # We only warn about partial requirements (indicates partial install)
        met_count = sum(1 for r in diag.requirements if r.met)
        if met_count == 0 or met_count == len(diag.requirements):
            continue

        # Build warning message
        met_reqs = [r for r in diag.requirements if r.met]
        unmet_reqs = [r for r in diag.requirements if not r.met]

        met_str = ", ".join(
            f"{r.count} {r.description}" for r in met_reqs
        )
        unmet_str = ", ".join(r.description for r in unmet_reqs)

        # Find suggested packages for unmet requirements
        suggested_packages: set[str] = set()
        linker_reqs = LINKER_LANGUAGE_REQUIREMENTS.get(diag.linker_name, {})
        for unmet in unmet_reqs:
            if unmet.name in linker_reqs:
                for lang in linker_reqs[unmet.name]:
                    if lang in LANGUAGE_PACKAGES:
                        suggested_packages.add(LANGUAGE_PACKAGES[lang])

        if suggested_packages:
            package_list = ", ".join(sorted(suggested_packages))
            message = (
                f"{diag.linker_name.upper()} linker found {met_str} but 0 {unmet_str}.\n"
                f"  Is {package_list} installed?"
            )
            suggested = sorted(suggested_packages)[0]  # Primary suggestion
        else:
            message = (
                f"{diag.linker_name.upper()} linker found {met_str} but 0 {unmet_str}."
            )
            suggested = None

        warnings.append(PartialInstallWarning(
            category="partial_linker",
            message=message,
            linker=diag.linker_name,
            suggested_package=suggested,
        ))

    return warnings


def check_partial_install_warnings(
    profile: "RepoProfile",
    linker_ctx: "LinkerContext",
    emit_warnings: bool = True,
) -> list[PartialInstallWarning]:
    """Check for all partial installation issues and optionally emit warnings.

    This is the main entry point for partial install diagnostics.

    Args:
        profile: The detected repository profile
        linker_ctx: The linker context after analysis
        emit_warnings: If True, emit warnings via Python's warnings module

    Returns:
        List of all partial install warnings detected.
    """
    all_warnings: list[PartialInstallWarning] = []

    # Check for unanalyzed files
    all_warnings.extend(check_unanalyzed_files(profile))

    # Check for partial linker requirements
    all_warnings.extend(check_partial_linker_requirements(linker_ctx))

    # Emit warnings if requested
    if emit_warnings:
        for warning in all_warnings:
            python_warnings.warn(warning.message, UserWarning, stacklevel=2)

    return all_warnings
