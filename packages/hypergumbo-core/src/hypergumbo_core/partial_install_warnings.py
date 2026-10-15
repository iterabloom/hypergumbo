# SPDX-License-Identifier: AGPL-3.0-or-later
"""Runtime warnings for partial installations (ADR-0010 Item 8).

This module provides diagnostic warnings when hypergumbo detects that:
1. Files exist for a language but no analyzer is installed for it
2. A linker has partial requirements met (e.g., Java native methods but no C JNI functions)
3. The repo contains Rust, so the opt-in rust-analyzer (SCIP) backend is
   relevant — a disclosure that indexing runs the project's build scripts
   and proc macros (``check_rust_analyzer_disclosure``, category
   ``rust_analyzer_optin``)

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
``check_partial_linker_requirements`` suppresses three unactionable cases:
a linker whose activation would not fire on this tree (WI-zamoz), one whose
only met requirements are ``*_files`` presence counts (WI-vasir), and one
whose unmet requirements can be satisfied only by languages absent from the
tree (WI-ruman). Each ``LinkerRequirement`` declares those languages, and
they also pick the package to suggest; a package already installed is not
suggested. The requirement counts see only
the project's own code (WI-dipag, ADR-0004 point 3a): symbols of supply-chain
tier 1-2, and edges whose source is not a tier-3+ symbol. Committed vendored
code is still linked; it just does not make the project look like a partial
install. Measured on hypergumbo itself: 53 C functions in the vendored
tree-sitter grammar sources (``hypergumbo_core/vendor/``) fired
"CGO-LINKER linker found 53 C/C++ function implementations but 0 Go cgo
calls" and the matching RUBY-FFI warning. The CLI therefore calls the check
after supply-chain classification, which is what stamps the tiers. Until
WI-tonuh the languages came from a table keyed ``jni`` / ``swift_objc`` /
``dependency`` while diagnostics carry registered names (``jni-linker``),
so the lookup never hit and the third gate and the suggestions never ran;
declaring them on the requirement leaves no second key to drift.

The Rust disclosure is emitted whether or not the binary is installed. It
is skipped when the backend is off and the user has already recorded a
decision for this repository (``backend_trust.read_decision``); when the
backend is on, it states that build scripts are running now.

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
    check_partial_install_warnings(
        profile, linker_ctx, emit_warnings=True, repo_root=repo_root,
    )

The default ``emit_warnings=True`` writes each finding to stderr as plain
text (not ``warnings.warn``, whose file:line prefix and echoed source line
were noise; WI-vadom). Pass ``emit_warnings=False`` to suppress emission and
iterate over the returned list instead.
"""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from typing import TYPE_CHECKING

from .taxonomy import LANGUAGE_ALIASES

if TYPE_CHECKING:
    from .linkers.registry import LinkerContext
from .profile import RepoProfile
from .rust_analyzer_install import is_rust_analyzer_available

# =============================================================================
# Language -> Package Mapping (ADR-0010)
# =============================================================================

# Maps each language to the ``hypergumbo-lang-*`` package whose analyzer
# covers it, for the "install X" suggestions. It cannot be read from the
# packages at runtime because the point is to name one that is NOT installed,
# so it is a table, and
# ``test_language_packages_name_the_package_that_ships_the_analyzer`` checks
# it against every ``@register_analyzer`` in the source tree (WI-tonuh: 22 of
# 56 entries had drifted, e.g. sql/lua/html/css named -common, not
# -mainstream). Config-only languages are left out; they never warn.

LANGUAGE_PACKAGES: dict[str, str] = {
    # hypergumbo-lang-mainstream
    "bash": "hypergumbo-lang-mainstream",
    "c": "hypergumbo-lang-mainstream",
    "cpp": "hypergumbo-lang-mainstream",
    "csharp": "hypergumbo-lang-mainstream",
    "css": "hypergumbo-lang-mainstream",
    "dockerfile": "hypergumbo-lang-mainstream",
    "go": "hypergumbo-lang-mainstream",
    "groovy": "hypergumbo-lang-mainstream",
    "html": "hypergumbo-lang-mainstream",
    "java": "hypergumbo-lang-mainstream",
    "javascript": "hypergumbo-lang-mainstream",
    "jupyter": "hypergumbo-lang-mainstream",
    "kotlin": "hypergumbo-lang-mainstream",
    "lua": "hypergumbo-lang-mainstream",
    "objc": "hypergumbo-lang-mainstream",
    "perl": "hypergumbo-lang-mainstream",
    "php": "hypergumbo-lang-mainstream",
    "powershell": "hypergumbo-lang-mainstream",
    "python": "hypergumbo-lang-mainstream",
    "ruby": "hypergumbo-lang-mainstream",
    "rust": "hypergumbo-lang-mainstream",
    "scala": "hypergumbo-lang-mainstream",
    "sql": "hypergumbo-lang-mainstream",
    "swift": "hypergumbo-lang-mainstream",
    "typescript": "hypergumbo-lang-mainstream",
    # hypergumbo-lang-common
    "clojure": "hypergumbo-lang-common",
    "commonlisp": "hypergumbo-lang-common",
    "cuda": "hypergumbo-lang-common",
    "dart": "hypergumbo-lang-common",
    "elixir": "hypergumbo-lang-common",
    "elm": "hypergumbo-lang-common",
    "erlang": "hypergumbo-lang-common",
    "fortran": "hypergumbo-lang-common",
    "fsharp": "hypergumbo-lang-common",
    "glsl": "hypergumbo-lang-common",
    "graphql": "hypergumbo-lang-common",
    "handlebars": "hypergumbo-lang-common",
    "haskell": "hypergumbo-lang-common",
    "hlsl": "hypergumbo-lang-common",
    "julia": "hypergumbo-lang-common",
    "just": "hypergumbo-lang-common",
    "latex": "hypergumbo-lang-common",
    "nix": "hypergumbo-lang-common",
    "ocaml": "hypergumbo-lang-common",
    "proto": "hypergumbo-lang-common",
    "purescript": "hypergumbo-lang-common",
    "qml": "hypergumbo-lang-common",
    "r": "hypergumbo-lang-common",
    "svelte": "hypergumbo-lang-common",
    "thrift": "hypergumbo-lang-common",
    "vue": "hypergumbo-lang-common",
    "wgsl": "hypergumbo-lang-common",
    # hypergumbo-lang-extended1
    "ada": "hypergumbo-lang-extended1",
    "agda": "hypergumbo-lang-extended1",
    "asm": "hypergumbo-lang-extended1",
    "blade": "hypergumbo-lang-extended1",
    "capnp": "hypergumbo-lang-extended1",
    "circom": "hypergumbo-lang-extended1",
    "cobol": "hypergumbo-lang-extended1",
    "d": "hypergumbo-lang-extended1",
    "fish": "hypergumbo-lang-extended1",
    "gdscript": "hypergumbo-lang-extended1",
    "gnuplot": "hypergumbo-lang-extended1",
    "jsonnet": "hypergumbo-lang-extended1",
    "lean": "hypergumbo-lang-extended1",
    "llvm_ir": "hypergumbo-lang-extended1",
    "mermaid": "hypergumbo-lang-extended1",
    "nim": "hypergumbo-lang-extended1",
    "solidity": "hypergumbo-lang-extended1",
    "tlaplus": "hypergumbo-lang-extended1",
    "verilog": "hypergumbo-lang-extended1",
    "vhdl": "hypergumbo-lang-extended1",
    "wolfram": "hypergumbo-lang-extended1",
    "zig": "hypergumbo-lang-extended1",
}

# Config/markup languages never produce an unanalyzed-files warning. Several
# have analyzers (json, yaml, toml in -mainstream), but a missing one leaves
# none of the project's code unanalyzed.
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
        if name in ("javascript_ts", "js_ts", "js", "javascript"):
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
        # Normalize aliases (e.g., "shell" -> "bash") so we match the
        # canonical analyzer name used in the registry.
        canonical = LANGUAGE_ALIASES.get(lang, lang)

        # Skip config-only languages (no analyzer expected)
        if canonical in CONFIG_ONLY_LANGUAGES:
            continue

        # Skip if analyzer is registered
        if canonical in registered_languages:
            continue

        # Get package suggestion (try canonical first, then raw name)
        package = LANGUAGE_PACKAGES.get(canonical) or LANGUAGE_PACKAGES.get(lang)

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


def rust_analyzer_backend_enabled(
    environ: Mapping[str, str] | None = None,
    repo_root: "Path | None" = None,
) -> bool:
    """True when this run has the SCIP backend switched on.

    ``--backend rust-analyzer`` is normalised into ``HYPERGUMBO_RUST_ANALYZER=1``
    during argv pre-parse (``cli.py``), so the env var carries both flag routes.

    IT IS NO LONGER THE ONLY SIGNAL, and reading it as such was briefly a real
    defect: once a per-repository TRUST GRANT could enable the backend
    (ADR-0045 ruling 7), a granted run had no env var set, so this returned
    False, the caller took the "not enabled" branch, and the recorded decision
    then suppressed the nudge entirely — a run that WAS executing the
    repository's build scripts printed nothing at all about it. The
    present-tense disclosure is the one message that must never be lost, so
    this asks the resolver that owns the whole precedence chain instead.
    """
    from .backend_selection import resolve_rust_analyzer_optin

    return resolve_rust_analyzer_optin(
        environ=environ, repo_root=repo_root,
    ) is True


def check_rust_analyzer_disclosure(
    profile: "RepoProfile",
    available: bool,
    enabled: bool = False,
    repo_root: "Path | None" = None,
) -> list[PartialInstallWarning]:
    """Tell the user the Rust backend exists — and what enabling it costs.

    WHY THIS IS A DISCLOSURE AND NOT AN ADVERT. ``rust-analyzer scip
    <workspace>`` **executes that workspace's ``build.rs`` and expands its
    proc macros**, as the invoking user. That is inherent to Cargo, not a
    hypergumbo defect: it is the same trust you extend by opening the
    project in an editor. But hypergumbo is routinely pointed at repositories
    the user has not read, so the capability cannot be advertised without the
    consequence attached.

    MEASURED, because an earlier attempt to make it safe FAILED and was
    reverted: a canary crate's build script fired under the bare invocation,
    and also under ``--config-path`` with ``cargo.buildScripts.enable=false``
    in both key spellings — accepted, no config errors reported, script ran
    anyway. The first reading that it worked was Cargo caching the previous
    run's build-script output; on three fresh never-built crates it does not
    hold. There is no known way to index a Cargo project without running its
    code, which is why the backend stays opt-in and why this text says so.

    The opt-in gate's own docstring justifies itself on indexing cost (~10x
    slower than tree-sitter, WI-zakub §4). That reason is real but it is no
    longer the only one, and it is the weaker one.

    Emitted whether or not the binary is present: someone deciding whether to
    install it deserves the caveat BEFORE they do, not after.
    """
    if not any(
        LANGUAGE_ALIASES.get(lang, lang) == "rust" for lang in profile.languages
    ):
        return []

    # ADR-0045 ruling 8. A user who has ANSWERED — either way — for this
    # repository is not asked again. Before the trust store there was no way
    # to answer durably, so the nudge fired on every run against every Rust
    # repo with the binary installed, and a person who had deliberately
    # chosen tree-sitter had no way to say so. That matters more here than
    # for an ordinary nag because the text carries a SECURITY disclosure, and
    # this module already records why (see TestRustAnalyzerDisclosure
    # RespectsTheGate): a prompt that fires when it is moot trains people to
    # skim past the one sentence that must land.
    #
    # The ENABLED arm is deliberately still emitted below even with a grant
    # on file: that message is not a nudge, it is a statement that build
    # scripts ARE RUNNING right now, and a standing grant does not make the
    # present tense less true.
    if not enabled and repo_root is not None:
        from .backend_trust import read_decision

        if read_decision(repo_root, "rust_analyzer") is not None:
            return []

    caveat = (
        "  Indexing runs this project's build scripts (build.rs) and proc "
        "macros as you — the same trust you extend by opening it in an "
        "editor. Enable it only for repositories you trust."
    )
    # WI-vadom: the nudge repeats until a decision is recorded (ruling 8
    # above), so it says how to record one. It fired on every run of
    # hypergumbo's own tree, whose only Rust is recorded test crates.
    target = f" {shlex.quote(str(repo_root))}" if repo_root is not None else ""
    decline = (
        f"  Not wanted here: hypergumbo trust-backend rust_analyzer{target} "
        "--revoke  (records the answer; this notice stops)"
    )
    if enabled:
        # WI-dojud: the backend is already running for THIS invocation, so the
        # "here is how to turn it on" arm would be false. The trust caveat is
        # still owed — it describes what is happening right now, not what would
        # happen if the reader opted in — so this arm states it in the present
        # tense rather than staying silent.
        message = (
            "rust-analyzer backend ACTIVE for this run: Rust types and call "
            "targets are resolved via SCIP rather than tree-sitter.\n"
            "  Disable per run: --backend tree-sitter\n"
            f"{caveat.replace('Indexing runs', 'Indexing IS RUNNING')}"
        )
    elif available:
        message = (
            "rust-analyzer is installed but NOT enabled. It resolves Rust "
            "types and call targets far more precisely than the built-in "
            "tree-sitter analyzer.\n"
            "  Enable per run:  --backend rust-analyzer\n"
            "  Or by env:       HYPERGUMBO_RUST_ANALYZER=1\n"
            f"{caveat}\n{decline}"
        )
    else:
        message = (
            "rust-analyzer is not installed. Installing it gives hypergumbo "
            "far more precise Rust type and call-target resolution.\n"
            "  Install:  rustup component add rust-analyzer\n"
            "  Then enable per run with --backend rust-analyzer.\n"
            f"{caveat}\n{decline}"
        )

    return [PartialInstallWarning(
        category="rust_analyzer_optin",
        message=message,
        language="rust",
    )]


#: Highest supply-chain tier that counts as the project's own code
#: (FIRST_PARTY = 1, INTERNAL_DEP = 2; ADR-0004 point 3a).
_FIRST_PARTY_MAX_TIER = 2


def first_party_view(linker_ctx: "LinkerContext") -> "LinkerContext":
    """Return a copy of ``linker_ctx`` restricted to the project's own code.

    Keeps symbols of supply-chain tier 1-2 and drops edges whose source is a
    tier-3+ symbol (an edge whose source is not a known symbol is kept). The
    linker-requirement counters read ``symbols`` and ``edges`` only, so this
    makes every counter first-party without touching the counters.
    """
    from dataclasses import replace

    vendored_ids = {
        sym.id for sym in linker_ctx.symbols
        if sym.supply_chain_tier > _FIRST_PARTY_MAX_TIER
    }
    if not vendored_ids:
        return linker_ctx
    return replace(
        linker_ctx,
        symbols=[s for s in linker_ctx.symbols if s.id not in vendored_ids],
        edges=[e for e in linker_ctx.edges if e.src not in vendored_ids],
    )


def _distribution_installed(name: str) -> bool:
    """True when the distribution ``name`` is installed in this environment."""
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        distribution(name)
    except PackageNotFoundError:
        return False
    return True


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
    from .linkers.registry import check_linker_requirements, get_all_linkers

    warnings: list[PartialInstallWarning] = []
    diagnostics = check_linker_requirements(first_party_view(linker_ctx))

    # WI-zamoz: skip linkers that wouldn't have activated on this tree.
    # check_linker_requirements iterates ALL registered linkers, so e.g.
    # the cgo linker (language_pairs=[("go", "c"), ("go", "cpp")]) still
    # produces a "151 C/C++ implementations / 0 Go cgo calls" diagnostic
    # on a Rust + Python repo. Without the gate, that becomes an
    # unactionable partial-install warning. Build the lookup only when
    # the caller passed real detection info — empty defaults (test
    # fixtures, headless contexts) bypass the gate to preserve existing
    # crafted-diagnostic test behavior.
    has_detection = bool(
        linker_ctx.detected_languages or linker_ctx.detected_frameworks
    )
    linker_specs_by_name = (
        {linker.name: linker for linker in get_all_linkers()}
        if has_detection
        else {}
    )

    for diag in diagnostics:
        # Skip if all requirements met or none met
        # We only warn about partial requirements (indicates partial install)
        met_count = sum(1 for r in diag.requirements if r.met)
        if met_count == 0 or met_count == len(diag.requirements):
            continue

        # WI-zamoz: skip if the linker's activation conditions wouldn't
        # have triggered it on this tree.
        spec = linker_specs_by_name.get(diag.linker_name)
        if spec is not None and not spec.activation.should_run(
            linker_ctx.detected_frameworks, linker_ctx.detected_languages
        ):
            continue

        # Build warning message
        met_reqs = [r for r in diag.requirements if r.met]
        unmet_reqs = [r for r in diag.requirements if not r.met]

        # WI-vasir: cross-language linkers define two requirements —
        # one counts language-file presence (name ends with ``_files``)
        # and one counts the actual pattern (JNI funcs, .proto services,
        # napi_create_function, etc.). If the only met requirement(s)
        # are file-presence ones, the "partial install" inference is
        # unreliable: on any polyglot repo (alertmanager has Go+JS;
        # the UAT observed warnings on 1-7 linkers per repo) the JS/TS
        # file count alone triggers NAPI/TAURI_IPC/SOLIDITY_ABI/
        # WASM_BINDGEN/IPC/LUA_FFI/RUBY_FFI/PYFFI noise. Real signal
        # requires at least one pattern-level match; without it,
        # suppress the warning.
        if all(r.name.endswith("_files") for r in met_reqs):
            continue

        # The languages that could satisfy the unmet requirements, as each
        # requirement declares them (WI-tonuh), feed both the relevance gate
        # and the package suggestion. A package already installed is not
        # suggested: "is X installed?" has an answer, and it is yes.
        unmet_relevant_languages = {
            lang for unmet in unmet_reqs for lang in unmet.languages
        }
        suggested_packages = {
            LANGUAGE_PACKAGES[lang] for lang in unmet_relevant_languages
            if lang in LANGUAGE_PACKAGES
            and not _distribution_installed(LANGUAGE_PACKAGES[lang])
        }

        # WI-ruman: relevance gate. If the tree contains none of the
        # languages that could satisfy the unmet requirements, the
        # warning is unactionable (installing the suggested package
        # wouldn't help — there's nothing in the tree for the analyzer
        # to find). Skip the diagnostic. Only fires when (a) we have
        # detection data (preserves test-fixture behavior), (b) the
        # unmet requirements declare languages, and (c) none of those
        # languages was detected. It catches what the WI-zamoz
        # activation gate cannot: always-on linkers, and
        # framework-activated ones (a Tauri app with a Rust frontend).
        if (
            has_detection
            and unmet_relevant_languages
            and not (unmet_relevant_languages & linker_ctx.detected_languages)
        ):
            continue

        met_str = ", ".join(
            f"{r.count} {r.description}" for r in met_reqs
        )
        unmet_str = ", ".join(r.description for r in unmet_reqs)
        # Registered names end in "-linker"; "JNI-LINKER linker" said it twice.
        label = diag.linker_name.removesuffix("-linker").upper()

        if suggested_packages:
            package_list = ", ".join(sorted(suggested_packages))
            message = (
                f"{label} linker found {met_str} but 0 {unmet_str}.\n"
                f"  Is {package_list} installed?"
            )
            suggested = sorted(suggested_packages)[0]  # Primary suggestion
        else:
            message = (
                f"{label} linker found {met_str} but 0 {unmet_str}."
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
    repo_root: "Path | None" = None,
) -> list[PartialInstallWarning]:
    """Check for all partial installation issues and optionally emit warnings.

    This is the main entry point for partial install diagnostics.

    Args:
        profile: The detected repository profile
        linker_ctx: The linker context after analysis
        emit_warnings: If True, write each warning to stderr

    Returns:
        List of all partial install warnings detected.
    """
    all_warnings: list[PartialInstallWarning] = []

    # Check for unanalyzed files
    all_warnings.extend(check_unanalyzed_files(profile))

    # Check for partial linker requirements
    all_warnings.extend(check_partial_linker_requirements(linker_ctx))

    # Disclose the opt-in Rust backend and what enabling it means. Availability
    # is resolved here rather than inside the check so the check stays pure and
    # testable in both states without shelling out.
    all_warnings.extend(
        check_rust_analyzer_disclosure(
            profile,
            available=is_rust_analyzer_available(),
            enabled=rust_analyzer_backend_enabled(repo_root=repo_root),
            repo_root=repo_root,
        ),
    )

    # Plain stderr lines, like the discovery disclosures. Through
    # ``warnings.warn`` each one was prefixed "<path>/cli.py:12852:
    # UserWarning:" and followed by the echoed source line, two lines of
    # noise per message that told the reader nothing (WI-vadom).
    if emit_warnings:
        for warning in all_warnings:
            sys.stderr.write(warning.message + "\n")

    return all_warnings
