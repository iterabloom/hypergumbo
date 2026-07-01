# SPDX-License-Identifier: AGPL-3.0-or-later
"""Catalog of available analysis passes (registry-derived).

The catalog provides a discoverable view of all analysis passes available
in hypergumbo. Every catalog entry corresponds to one registered analyzer
or linker; the catalog is built from ``_ANALYZER_REGISTRY`` +
``_LINKER_REGISTRY``, not a hand-written list.

How It Works
------------
1. :func:`build_catalog_from_registries` triggers entry-point discovery
   (``ensure_discovered()``) so all language packages have a chance to
   register their analyzers/linkers.
2. For each registered pass it constructs a :class:`Pass` whose ``id`` is
   the registration name (no ``-v1`` / ``-ts-v1`` suffix — INV-morag PR 2).
3. Catalog metadata (description, languages, availability, requires,
   backend) is taken from the registry entry when the analyzer/linker
   provides it via decorator kwargs; otherwise it falls back to
   ``_PASS_METADATA`` below for entries that haven't migrated yet.

Why This Design
---------------
- **Single source of truth.** Pass IDs come from the same registries that
  runtime code uses, so catalog and runtime can no longer drift.
- **Self-describing modules.** When an analyzer module specifies metadata
  on its ``@register_analyzer`` call, the data lives next to the function
  it describes — the natural location.
- **Transitional fallback.** ``_PASS_METADATA`` lets PR 2 ship the rename
  + invariant without requiring a simultaneous edit of every analyzer
  package; follow-up PRs push entries into individual decorator sites.

INV-morag PR 2 invariant
------------------------
For every registered analyzer/linker ``name``,
``make_pass_id(name) == <some Pass.id in get_default_catalog()>``.
Asserted at CI by :file:`scripts/check-pass-id-agreement`.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Pass:
    """An analysis pass that can be applied to source code.

    Attributes:
        id: Unique identifier (e.g., 'python', 'javascript', 'websocket-linker')
        description: Human-readable description
        availability: 'core' (always available) or 'extra' (requires deps)
        requires: Optional package requirement for extras
        languages: Languages this pass handles (for suggestions)
        backend: Parsing backend tag (INV-morag PR 2). Optional for older
            catalog entries; empty string when unknown.
        pass_label: Human-friendly display name. Falls back to ``id`` when
            empty.
        depends_on: Pass-id dependencies expressed in Conjunctive Normal Form
            (INV-hujog / WI-dilab; WI-hupaz shipped the flat-list precursor).
            Outer list = AND-conjunction; each inner list = OR-disjunction.
            Example: JNI bridges Java native methods to C/C++/Rust impls —
            ``[["java"], ["c", "cpp", "rust"]]`` reads as "java required, AND
            at least one of c/cpp/rust required." Empty outer list = no
            declared dependencies (honest declaration for language-agnostic
            Infrastructure linkers like containment). Distinct from
            ``requires`` (package-availability label).

            Two validators consume this field:

            - :func:`validate_pass_name_resolution` is the static check:
              every literal in every clause must resolve to a registered
              pass-id. Catches typos at CI time, independent of any runtime
              active-set decision.
            - :func:`validate_pass_dependencies` is the forward-looking
              runtime check: given an active subset of passes, every outer
              AND-conjunct must contain at least one literal in the active
              set. Not yet wired into ``run_behavior_map``; deferred to a
              Phase 2 WI that adds config-time pass-filtering plumbing.
    """

    id: str
    description: str
    availability: str  # 'core' or 'extra'
    requires: Optional[str] = None
    languages: List[str] = field(default_factory=list)
    backend: str = ""
    pass_label: str = ""
    depends_on: List[List[str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        d: Dict[str, Any] = {
            "id": self.id,
            "description": self.description,
            "availability": self.availability,
        }
        if self.requires:
            d["requires"] = self.requires
        if self.backend:
            d["backend"] = self.backend
        if self.pass_label and self.pass_label != self.id:
            d["pass_label"] = self.pass_label
        return d


@dataclass
class Catalog:
    """Registry of available passes.

    Attributes:
        passes: List of available analysis passes
    """

    passes: List[Pass] = field(default_factory=list)

    def get_core_passes(self) -> List[Pass]:
        """Return only core passes (always available)."""
        return [p for p in self.passes if p.availability == "core"]

    def get_extra_passes(self) -> List[Pass]:
        """Return only extra passes (require optional deps)."""
        return [p for p in self.passes if p.availability == "extra"]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "passes": [p.to_dict() for p in self.passes],
        }


def is_available(p: Pass) -> bool:
    """Check if a pass is available in the current environment.

    Core passes are always available. Extra passes require their
    dependency to be importable (tree-sitter language pack).
    """
    if p.availability == "core":
        return True

    # Check for tree-sitter dependency based on the requires field
    if p.requires and "tree-sitter" in p.requires:
        return importlib.util.find_spec("tree_sitter") is not None

    return False


def validate_pass_name_resolution(passes: List[Pass]) -> None:
    """Static check: every literal in every ``depends_on`` clause names a known pass.

    ``Pass.depends_on`` is CNF (outer-AND of inner-OR clauses), per the WI-dilab
    schema. This validator iterates every literal across every clause and
    requires it to match the ``id`` of a registered pass. Independent of which
    passes are runtime-active — purely a typo / drift check that runs at
    catalog-build time and in CI.

    Distinct from :func:`validate_pass_dependencies` (which checks the OR-arm
    runtime semantics: every outer-AND-conjunct must contain at least one
    literal in the active-pass subset).

    Raises:
        ValueError: At least one literal in some clause names a pass that is
            not in ``passes``. The error message groups misspellings by
            dependent pass-id, listing every (pass_id, [unknown_literals])
            pair so callers see the full surface of the typo set, not just
            the first offending name.
    """
    known_ids = {p.id for p in passes}
    unknown_by_pass: List[tuple[str, List[str]]] = []
    for p in passes:
        unknown: List[str] = []
        for clause in p.depends_on:
            for literal in clause:
                if literal not in known_ids and literal not in unknown:
                    unknown.append(literal)
        if unknown:
            unknown_by_pass.append((p.id, unknown))
    if unknown_by_pass:
        lines = [
            f"Pass {pass_id!r} depends_on names unknown passes: {unknown}"
            for pass_id, unknown in unknown_by_pass
        ]
        raise ValueError(
            "Pass dependency name-resolution failed:\n  " + "\n  ".join(lines)
        )


def validate_pass_dependencies(active_passes: List[Pass]) -> None:
    """Runtime CNF check: every AND-conjunct contains at least one active literal.

    Forward-looking validator. Once :data:`hypergumbo_core.cli.run_behavior_map`
    grows config-time pass-filtering (a Phase 2 WI), this validator will fire
    on the filtered active-pass set to surface "linker X needs at least one
    of [a, b, c] but none of them are active" before pipeline execution
    produces silently-degraded output.

    CNF semantics:

    - ``depends_on=[]`` — no declared dependencies (vacuously satisfied).
      Honest for Infrastructure linkers like containment that operate on any
      symbol set.
    - ``depends_on=[["x"]]`` — single conjunct, single literal. Equivalent to
      plain "x must be active." Used for Framework linkers scoped to one
      language.
    - ``depends_on=[["x", "y", "z"]]`` — single conjunct, three-literal OR.
      "At least one of x/y/z must be active." Used for Protocol linkers that
      scan multiple language analyzers' output.
    - ``depends_on=[["x"], ["a", "b"]]`` — two conjuncts. "x AND (a OR b)
      must both hold." Used for Bridge linkers that bridge one anchor
      language to multiple impl languages.

    Pathological-but-defined: a pass listing itself in some clause is trivially
    satisfied when that pass is in the active set. Cycle detection is a
    separate scheduling concern, deferred.

    Raises:
        ValueError: At least one pass has an outer AND-conjunct with no
            literal in the active-pass set. The error message groups by
            dependent pass, listing every unsatisfied clause as a list so
            callers see the full set of unmet "at least one of"
            requirements, not just the first.
    """
    active_ids = {p.id for p in active_passes}
    unsatisfied_by_pass: List[tuple[str, List[List[str]]]] = []
    for p in active_passes:
        unsatisfied_clauses: List[List[str]] = []
        for clause in p.depends_on:
            if not any(literal in active_ids for literal in clause):
                unsatisfied_clauses.append(list(clause))
        if unsatisfied_clauses:
            unsatisfied_by_pass.append((p.id, unsatisfied_clauses))
    if unsatisfied_by_pass:
        lines = [
            f"Pass {pass_id!r} unsatisfied depends_on clauses (need at least one literal in active set): {clauses}"
            for pass_id, clauses in unsatisfied_by_pass
        ]
        raise ValueError(
            "Pass dependency CNF validation failed:\n  " + "\n  ".join(lines)
        )


# Config/data formats that shouldn't trigger pass suggestions
CONFIG_LANGUAGES = {"json", "yaml", "toml", "xml", "css", "markdown", "just", "mermaid"}


# INV-morag PR 2 transitional metadata table.
#
# Keyed by registration ``name`` (the post-rename pass_id). When an analyzer
# or linker's @register_analyzer / @register_linker call doesn't specify
# description / languages / availability / requires / backend explicitly,
# the catalog falls back to the entries here.
#
# Follow-up PRs (tracked as a separate item) push each entry into the
# corresponding decorator call site, eventually emptying this dict.
_TS_REQUIRES = "tree-sitter-language-pack"


def _ts(description: str, languages: List[str], availability: str = "extra") -> Dict[str, Any]:
    """Helper: tree-sitter-based pass metadata (most common case)."""
    return {
        "description": description,
        "availability": availability,
        "requires": _TS_REQUIRES,
        "languages": languages,
        "backend": "tree-sitter",
    }


_PASS_METADATA: Dict[str, Dict[str, Any]] = {
    # ---- Core analyzers (no tree-sitter required) ----
    "python": {
        "description": "Python AST parser (classes, functions, imports)",
        "availability": "core",
        "languages": ["python"],
        "backend": "ast",
    },
    "jupyter": {
        "description": "Jupyter notebook code cell parser",
        "availability": "core",
        "languages": ["jupyter"],
        "backend": "ast",
    },
    "blade": {
        "description": "Laravel Blade template directives",
        "availability": "core",
        "languages": ["blade"],
        "backend": "pattern",
    },
    "gnuplot": {
        "description": "Gnuplot functions, variables, plot commands",
        "availability": "core",
        "languages": ["gnuplot"],
        "backend": "pattern",
    },
    "handlebars": {
        "description": "Handlebars partials and block helpers",
        "availability": "core",
        "languages": ["handlebars"],
        "backend": "pattern",
    },
    "just": {
        "description": "Justfile recipes and dependencies",
        "availability": "core",
        "languages": ["just"],
        "backend": "pattern",
    },
    "mermaid": {
        "description": "Mermaid diagram types and nodes",
        "availability": "core",
        "languages": ["mermaid"],
        "backend": "pattern",
    },
    "qml": {
        "description": "QML components, properties, signals",
        "availability": "core",
        "languages": ["qml"],
        "backend": "pattern",
    },
    "html": {
        "description": "HTML script tag parser",
        "availability": "core",
        "languages": ["html"],
        "backend": "pattern",
    },
    # ---- Tree-sitter analyzers ----
    "javascript": _ts("JS/TS/Svelte/Vue via tree-sitter", ["javascript", "typescript", "vue"]),
    "php": _ts("PHP via tree-sitter", ["php"]),
    "c": _ts("C via tree-sitter", ["c"]),
    "cpp": _ts("C++ via tree-sitter", ["cpp"]),
    "java": _ts("Java via tree-sitter", ["java"]),
    "elixir": _ts("Elixir via tree-sitter", ["elixir"]),
    "rust": _ts("Rust via tree-sitter", ["rust"]),
    "go": _ts("Go via tree-sitter", ["go"]),
    "ruby": _ts("Ruby via tree-sitter", ["ruby"]),
    "kotlin": _ts("Kotlin via tree-sitter", ["kotlin"]),
    "swift": _ts("Swift via tree-sitter", ["swift"]),
    "scala": _ts("Scala via tree-sitter", ["scala"]),
    "lua": _ts("Lua via tree-sitter", ["lua"]),
    "dart": _ts("Dart via tree-sitter", ["dart"]),
    "clojure": _ts("Clojure via tree-sitter", ["clojure"]),
    "elm": _ts("Elm via tree-sitter", ["elm"]),
    "erlang": _ts("Erlang via tree-sitter", ["erlang"]),
    "haskell": _ts("Haskell via tree-sitter", ["haskell"]),
    "agda": _ts("Agda proof assistant via tree-sitter", ["agda"]),
    "lean": _ts("Lean 4 theorem prover via tree-sitter (build from source)", ["lean"]),
    "wolfram": _ts("Wolfram Language via tree-sitter (build from source)", ["wolfram"]),
    "ocaml": _ts("OCaml via tree-sitter", ["ocaml"]),
    "solidity": _ts("Solidity smart contracts via tree-sitter", ["solidity"]),
    "csharp": _ts("C# via tree-sitter", ["csharp"]),
    "zig": _ts("Zig via tree-sitter", ["zig"]),
    "groovy": _ts("Groovy via tree-sitter", ["groovy"]),
    "julia": _ts("Julia via tree-sitter", ["julia"]),
    "objc": _ts("Objective-C via tree-sitter", ["objc"]),
    "hcl": _ts("HCL/Terraform via tree-sitter", ["hcl"]),
    "fsharp": _ts("F# via tree-sitter", ["fsharp"]),
    "perl": _ts("Perl via tree-sitter", ["perl"]),
    "r": _ts("R via tree-sitter", ["r"]),
    "bash": _ts("Bash/Shell via tree-sitter", ["bash"]),
    "sql": _ts("SQL schema analysis via tree-sitter", ["sql"]),
    "dockerfile": _ts("Dockerfile analysis via tree-sitter", ["dockerfile"]),
    "cmake": _ts("CMake build system via tree-sitter", ["cmake"]),
    "make": _ts("Makefile build system via tree-sitter", []),
    "graphql": _ts("GraphQL schema via tree-sitter", ["graphql"]),
    "nix": _ts("Nix expressions via tree-sitter", ["nix"]),
    "cuda": _ts("CUDA GPU kernels via tree-sitter", ["cuda"]),
    "verilog": _ts("Verilog/SystemVerilog via tree-sitter", ["verilog"]),
    "vhdl": _ts("VHDL hardware design via tree-sitter", ["vhdl"]),
    "glsl": _ts("GLSL shaders via tree-sitter", ["glsl"]),
    "hlsl": _ts("HLSL DirectX shaders via tree-sitter", ["hlsl"]),
    "wgsl": _ts("WGSL WebGPU shaders via tree-sitter", ["wgsl"]),
    "fortran": _ts("Fortran via tree-sitter", ["fortran"]),
    "cobol": _ts("COBOL via tree-sitter", ["cobol"]),
    "latex": _ts("LaTeX via tree-sitter", ["latex"]),
    "proto": _ts("Protocol Buffers via tree-sitter", ["proto"]),
    "thrift": _ts("Apache Thrift via tree-sitter", ["thrift"]),
    "capnp": _ts("Cap'n Proto via tree-sitter", ["capnp"]),
    "powershell": _ts("PowerShell via tree-sitter", ["powershell"]),
    "fish": _ts("Fish shell via tree-sitter", ["fish"]),
    "gdscript": _ts("GDScript (Godot) via tree-sitter", ["gdscript"]),
    "starlark": _ts("Starlark (Bazel/Buck) via tree-sitter", ["starlark"]),
    "ada": _ts("Ada via tree-sitter", ["ada"]),
    "d": _ts("D programming language via tree-sitter", ["d"]),
    "nim": _ts("Nim via tree-sitter", ["nim"]),
    "toml": _ts("TOML configuration files via tree-sitter", ["toml"]),
    "css": _ts("CSS stylesheets via tree-sitter", ["css"]),
    "json": _ts("JSON configuration files via tree-sitter", ["json"]),
    "yaml_ansible": _ts("YAML/Ansible via tree-sitter", ["yaml"]),
    "xml": _ts("XML configuration files via tree-sitter", ["xml"]),
    # ---- Linkers ----
    "websocket-linker": {
        "description": "WebSocket communication patterns",
        "availability": "core",
        "languages": [],
        "backend": "protocol",
    },
}


def _metadata_for(name: str) -> Dict[str, Any]:
    """Return fallback metadata for a registered analyzer/linker, or sensible defaults."""
    return _PASS_METADATA.get(name, {})


def build_catalog_from_registries() -> Catalog:
    """Build the catalog from ``_ANALYZER_REGISTRY`` + ``_LINKER_REGISTRY``.

    Triggers entry-point discovery so all language packages register before
    the catalog is materialized. Per-pass metadata (description, languages,
    availability, requires, backend) comes first from the registry entry
    when the decorator specified it; otherwise from :data:`_PASS_METADATA`
    as a transitional fallback.

    Returns:
        A :class:`Catalog` whose ``passes`` list mirrors the registries.
        Pass IDs are the registration names (no ``-v1`` suffix per
        INV-morag PR 2); ordering is analyzers-first (sorted by priority,
        then name) then linkers (sorted by priority, then name).
    """
    # Local imports to avoid circular deps (analyze.registry imports from
    # this package).
    from .analyze.registry import _ANALYZER_REGISTRY, ensure_discovered
    from .linkers.registry import _LINKER_REGISTRY

    ensure_discovered()

    passes: List[Pass] = []
    seen: set[str] = set()

    def _from_registered(name: str, reg_description: str, reg_languages: List[str],
                         reg_availability: str, reg_requires: Optional[str],
                         reg_backend: str, reg_pass_label: str,
                         reg_depends_on: List[List[str]]) -> Pass:
        # Registry default for availability is "core". For analyzers that
        # haven't yet moved their metadata into the decorator call, the
        # fallback dict knows better — most analyzers are actually "extra"
        # via tree-sitter. Prefer the dict when the registry value is the
        # default "core" sentinel AND the dict has an explicit entry.
        fallback = _metadata_for(name)
        if reg_availability == "core" and "availability" in fallback:
            availability = fallback["availability"]
        else:
            availability = reg_availability
        return Pass(
            id=name,
            description=reg_description or fallback.get("description", ""),
            availability=availability,
            requires=reg_requires if reg_requires is not None else fallback.get("requires"),
            languages=list(reg_languages) if reg_languages else list(fallback.get("languages", [])),
            backend=reg_backend or fallback.get("backend", ""),
            pass_label=reg_pass_label or name,
            depends_on=[list(clause) for clause in reg_depends_on],
        )

    # Analyzers first, sorted by priority then name for stable output.
    for reg in sorted(_ANALYZER_REGISTRY.values(), key=lambda r: (r.priority, r.name)):
        if reg.name in seen:  # pragma: no cover - registry uniqueness invariant
            continue
        passes.append(_from_registered(
            name=reg.name,
            reg_description=reg.description,
            reg_languages=reg.languages,
            reg_availability=reg.availability,
            reg_requires=reg.requires,
            reg_backend=reg.backend,
            reg_pass_label=reg.pass_label,
            reg_depends_on=reg.depends_on,
        ))
        seen.add(reg.name)

    for reg in sorted(_LINKER_REGISTRY.values(), key=lambda r: (r.priority, r.name)):
        if reg.name in seen:  # pragma: no cover - cross-registry name collision (shouldn't happen)
            continue
        passes.append(_from_registered(
            name=reg.name,
            reg_description=reg.description,
            reg_languages=reg.languages,
            reg_availability=reg.availability,
            reg_requires=reg.requires,
            reg_backend=reg.backend,
            reg_pass_label=reg.pass_label,
            reg_depends_on=reg.depends_on,
        ))
        seen.add(reg.name)

    return Catalog(passes=passes)


def get_default_catalog() -> Catalog:
    """Return the default catalog (registry-derived, INV-morag PR 2)."""
    return build_catalog_from_registries()


# Pipeline-level synthetic passes that don't go through
# ``@register_analyzer`` or ``@register_linker``. These are post-pass
# steps the orchestrator runs after the registered linkers; their
# pass_id values legitimately appear in ``AnalysisRun.pass_id``,
# ``Symbol.origin``, and ``Edge.origin`` but are NOT registry entries.
# Phase 6 PR5 (validator-driven cleanup tail) closes the corresponding
# 411 ``axis_conformance`` violations on ``Edge.origin`` by accepting
# these as legitimate pass-id values.
_BUILTIN_PIPELINE_PASS_IDS: frozenset[str] = frozenset({
    # registry.py:774 — synthetic post-pass that connects synthetic
    # linker-emitted nodes (grpc_stub, mq_publisher, etc.) to their
    # enclosing functions for slice traversal.
    "enclosure-linker",
})

# Synthetic-pass provenance IDs (ADR-0044). A few pipeline-level synthesis /
# import passes emit Symbols (and Edges) whose ``origin`` names the pass that
# synthesized them, but which are not ``@register_analyzer`` /
# ``@register_linker`` call sites. Per synthetic:F1 (WI-dizir/WI-mosil/WI-sijut)
# the two orchestrator-level synthesizers emit a real ``AnalysisRun`` whose
# ``pass_id`` is the value below, and their nodes' ``origin_run_id`` joins to
# it — so these ARE genuine pass IDs, not a separate "synthesis-mechanism"
# axis. ADR-0044 records the decision to treat them as legitimate synthetic
# pass IDs and WITHDRAW WI-kadop's proposed ``synthesis_mechanism`` field
# split: synthetic:F1 collapsed the mechanism/pass distinction (a synthesized
# Symbol's "how" IS "which pass synthesized it"), so the split would have
# duplicated these values into a second field rather than removed a leak. The
# stale ``inheritance`` value was dropped (zero producers — the
# inheritance-linker stamps ``make_pass_id("inheritance-linker")``, not the bare
# string). ``scip`` is the SCIP-index import pass (rust-analyzer); giving its
# Symbol side a real ``AnalysisRun`` join (like the two orchestrator passes) is
# the tracked follow-on (WI-zabus).
_SYNTHETIC_PASS_IDS: frozenset[str] = frozenset({
    "boundary_external_symbol_synthesis",
    "orchestrator_file_symbol_synthesis",
    "scip",
})


def all_known_pass_ids() -> frozenset[str]:
    """Return the union of pass IDs across registered analyzers and linkers.

    Single source of truth for "what passes does this codebase
    declare?" — the answer is "the names of every ``@register_analyzer``
    and ``@register_linker`` call site, plus the small set of
    pipeline-level synthetic passes documented in this module." Used by
    the WI-busij multi-value-field-axis linter to resolve the
    ``# axis: pass-id`` annotation (covers ``AnalysisRun.pass_id``,
    ``Symbol.origin``, ``Edge.origin``).

    Beyond the two registries, a few pipeline-level synthesis / import
    passes emit Symbols whose ``origin`` names them but which are not
    ``@register_*`` call sites — ``_SYNTHETIC_PASS_IDS`` (above) enumerates
    them (``orchestrator_file_symbol_synthesis`` /
    ``boundary_external_symbol_synthesis`` / ``scip``). Per ADR-0044 these
    are genuine synthetic pass IDs, not a separate synthesis-mechanism axis
    (WI-kadop's proposed field split was withdrawn — synthetic:F1 made the
    two orchestrator synthesizers emit real ``AnalysisRun``s whose pass_id
    is the origin value, collapsing the mechanism/pass distinction).
    """
    from .analyze.registry import _ANALYZER_REGISTRY, ensure_discovered
    from .linkers.registry import _LINKER_REGISTRY

    ensure_discovered()
    return (
        frozenset(_ANALYZER_REGISTRY)
        | frozenset(_LINKER_REGISTRY)
        | _BUILTIN_PIPELINE_PASS_IDS
        | _SYNTHETIC_PASS_IDS
    )


def all_known_languages() -> frozenset[str]:
    """Return the union of language tags across registered analyzers and linkers.

    Single source of truth for "what languages does this codebase know
    about?" — the answer is "the union of ``languages=[...]`` kwargs on
    every ``@register_analyzer`` / ``@register_linker`` call site." Used
    by the WI-busij multi-value-field-axis linter to resolve the
    ``# axis: language`` annotation; not a hand-maintained list.

    Behaves as a lightweight axis declaration per ADR-0024 §4:
    no separate registry module, no ``AXIS_*`` constants, no
    ``*Spec`` dataclass — just a function that returns the legal
    set, derived from data we already have.
    """
    from .analyze.registry import _ANALYZER_REGISTRY, ensure_discovered
    from .linkers.registry import _LINKER_REGISTRY

    ensure_discovered()
    langs: set[str] = set()
    for analyzer in _ANALYZER_REGISTRY.values():
        langs.update(analyzer.languages)
    for linker in _LINKER_REGISTRY.values():
        langs.update(linker.languages)
    return frozenset(langs)


def suggest_passes_for_languages(detected_languages: set[str]) -> List[Pass]:
    """Suggest passes based on detected languages.

    Takes a set of language names (from profile.languages) and returns
    passes that would be relevant. Config-only languages (JSON, YAML, etc.)
    are filtered out.

    Args:
        detected_languages: Set of language names (e.g., {"python", "javascript"}).

    Returns:
        List of Pass objects relevant to detected languages.
    """
    # Filter out config-only languages (they don't suggest passes by default)
    code_languages = detected_languages - CONFIG_LANGUAGES

    if not code_languages:
        return []

    # Find passes that handle detected languages
    catalog = get_default_catalog()
    suggested: List[Pass] = []
    seen_ids: set[str] = set()

    for p in catalog.passes:
        if p.id in seen_ids:  # pragma: no cover - defensive for duplicate IDs
            continue
        for lang in p.languages:
            if lang in code_languages:
                suggested.append(p)
                seen_ids.add(p.id)
                break

    return suggested
