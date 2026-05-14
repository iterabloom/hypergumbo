# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical registry of Symbol.kind values in hypergumbo's behavior map.

Per ADR-0027, every value in the canonical registry should have
``axis="language_construct"`` — ``Symbol.kind`` names the source-language
syntactic construct that the symbol represents, with framework
participation queried from ``Symbol.meta`` and edge-shaped relationships
queried from the ``Edge`` rather than smuggled into the kind label.

This module is the single source of truth: ``scripts/generate-schema``
imports ``SYMBOL_KINDS`` to emit both the JSON Schema enum and per-value
axis annotations (under the ``x-axis-of-values`` extension keyword).
Consumers that need a subset of kinds (for example, a "callable kinds"
filter) should call ``symbol_kinds_on_axis(...)`` rather than maintain
their own hardcoded set; the property test in
``tests/test_symbol_kinds.py`` enforces that every hardcoded set in the
codebase whose name contains ``KIND`` is a subset of this registry.

Axis taxonomy (per ADR-0027 §1):

- ``language_construct`` — ADR-0027 compliant. The value names the
  source-language syntactic construct the symbol represents (Cluster A
  in the WI-dumiz audit, plus the Cluster B/G/H promotions per the
  per-cluster audit-findings docs).
- ``pending_classification`` — deferred for the residual Cluster B / G
  values still on the registry; per-value verdicts arrive with each
  cluster's audit-findings doc.

The ``endpoint_shape`` axis was retired in PR #3633 (Phase 4b enum
closure / WI-butol). All 71 deprecated values that occupied that axis
during the Phase 4a deprecation window are now removed from the
registry; their fold targets live as ``Symbol.kind`` canonical values
plus ``Symbol.meta`` keys (see :mod:`hypergumbo_core.axis_meta_keys`).
Consult the per-cluster audit-findings docs (0009 / 0010 / 0011 / 0013
/ 0005-0008) for the per-value canonical / fold-target map.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final


AXIS_LANGUAGE_CONSTRUCT: Final[str] = "language_construct"
AXIS_PENDING: Final[str] = "pending_classification"

# Retired axis name kept as a public constant for audit-findings
# validation (``hypergumbo_core.audit_findings._REGISTRIES``) and for
# downstream readers comparing schema versions across the Phase 4a
# deprecation window. Not in :data:`VALID_AXES` — no live spec may
# carry this axis; the property test in
# ``tests/test_symbol_kinds.py`` enforces the empty-axis invariant.
AXIS_ENDPOINT_SHAPE: Final[str] = "endpoint_shape"

VALID_AXES: Final[frozenset[str]] = frozenset({
    AXIS_LANGUAGE_CONSTRUCT,
    AXIS_PENDING,
})


@dataclass(frozen=True)
class SymbolKindSpec:
    """A single Symbol.kind value and its axis classification."""

    name: str
    axis: str
    description: str


SYMBOL_KINDS: Final[tuple[SymbolKindSpec, ...]] = (
    # ----------------------------------------------------------------
    # Cluster A — Pure language constructs (AXIS_LANGUAGE_CONSTRUCT).
    # Each value names a source-language syntactic construct directly.
    # ----------------------------------------------------------------
    SymbolKindSpec("function", AXIS_LANGUAGE_CONSTRUCT,
                   "Top-level function definition."),
    SymbolKindSpec("method", AXIS_LANGUAGE_CONSTRUCT,
                   "Method on a class / struct / interface."),
    SymbolKindSpec("class", AXIS_LANGUAGE_CONSTRUCT,
                   "Class declaration."),
    SymbolKindSpec("interface", AXIS_LANGUAGE_CONSTRUCT,
                   "Interface declaration."),
    SymbolKindSpec("contract", AXIS_LANGUAGE_CONSTRUCT,
                   "Smart-contract declaration (Solidity / Vyper / Move). "
                   "Sibling to `class` / `interface` / `struct` — names the "
                   "source-language top-level construct directly. Producers: "
                   "`solidity.py:265` emits `add_symbol(name, \"contract\", ...)` "
                   "for `contract_declaration` AST nodes. Consumed by "
                   "`library-exports.yaml`'s `symbol_kind: ^contract$` rule "
                   "that surfaces deployable Solidity contracts as library "
                   "exports."),
    SymbolKindSpec("struct", AXIS_LANGUAGE_CONSTRUCT,
                   "Struct / record-type declaration."),
    SymbolKindSpec("enum", AXIS_LANGUAGE_CONSTRUCT,
                   "Enum declaration."),
    SymbolKindSpec("union", AXIS_LANGUAGE_CONSTRUCT,
                   "Union / sum-type declaration."),
    SymbolKindSpec("error_set", AXIS_LANGUAGE_CONSTRUCT,
                   "Zig error-set declaration. Surfaced by WI-nubuv's "
                   "inline-IfExp / non-string-Constant classifier fixes "
                   "from the function-local kind union in "
                   "hypergumbo-lang-extended1/zig.py:259. Zig errors are "
                   "a first-class language construct sibling to "
                   "`struct` / `enum` / `union`."),
    SymbolKindSpec("trait", AXIS_LANGUAGE_CONSTRUCT,
                   "Trait declaration (Rust / Scala / Groovy)."),
    SymbolKindSpec("module", AXIS_LANGUAGE_CONSTRUCT,
                   "Module declaration (the source-level construct)."),
    SymbolKindSpec("namespace", AXIS_LANGUAGE_CONSTRUCT,
                   "Namespace declaration (C++ / TypeScript / C#)."),
    SymbolKindSpec("variable", AXIS_LANGUAGE_CONSTRUCT,
                   "Variable / let / mutable binding."),
    SymbolKindSpec("constant", AXIS_LANGUAGE_CONSTRUCT,
                   "Constant / final / let-immutable binding."),
    SymbolKindSpec("const", AXIS_LANGUAGE_CONSTRUCT,
                   "Const declaration (C / C++ / Rust / JS const)."),
    SymbolKindSpec("property", AXIS_LANGUAGE_CONSTRUCT,
                   "Property declaration (Kotlin / Swift / C#)."),
    SymbolKindSpec("attribute", AXIS_LANGUAGE_CONSTRUCT,
                   "Attribute declaration (Python class attribute, etc.)."),
    SymbolKindSpec("field", AXIS_LANGUAGE_CONSTRUCT,
                   "Field declaration on a struct / class / record."),
    SymbolKindSpec("constructor", AXIS_LANGUAGE_CONSTRUCT,
                   "Constructor / __init__ / init method."),
    SymbolKindSpec("getter", AXIS_LANGUAGE_CONSTRUCT,
                   "Property getter accessor."),
    SymbolKindSpec("setter", AXIS_LANGUAGE_CONSTRUCT,
                   "Property setter accessor."),
    SymbolKindSpec("type", AXIS_LANGUAGE_CONSTRUCT,
                   "Type declaration (TypeScript type, Haskell type, etc.)."),
    SymbolKindSpec("type_alias", AXIS_LANGUAGE_CONSTRUCT,
                   "Type alias declaration."),
    SymbolKindSpec("typedef", AXIS_LANGUAGE_CONSTRUCT,
                   "C/C++ typedef declaration."),
    SymbolKindSpec("alias", AXIS_LANGUAGE_CONSTRUCT,
                   "Generic alias declaration."),
    SymbolKindSpec("simple_type", AXIS_LANGUAGE_CONSTRUCT,
                   "Simple-type declaration (XSD-shape)."),
    SymbolKindSpec("defined_type", AXIS_LANGUAGE_CONSTRUCT,
                   "Defined / nominal type declaration (Puppet / Coq)."),
    SymbolKindSpec("macro", AXIS_LANGUAGE_CONSTRUCT,
                   "Macro definition (Rust / C / Scheme)."),
    SymbolKindSpec("mixin", AXIS_LANGUAGE_CONSTRUCT,
                   "Mixin declaration (Ruby / Sass)."),
    SymbolKindSpec("record", AXIS_LANGUAGE_CONSTRUCT,
                   "Record declaration (Java 14+, Erlang, Haskell)."),
    SymbolKindSpec("abstract", AXIS_LANGUAGE_CONSTRUCT,
                   "Abstract class / member declaration."),
    SymbolKindSpec("instance", AXIS_LANGUAGE_CONSTRUCT,
                   "Typeclass / interface instance declaration."),
    SymbolKindSpec("subroutine", AXIS_LANGUAGE_CONSTRUCT,
                   "Subroutine / sub declaration (Fortran / Perl)."),
    SymbolKindSpec("procedure", AXIS_LANGUAGE_CONSTRUCT,
                   "Procedure declaration (Pascal / Ada / SQL)."),
    SymbolKindSpec("call_site", AXIS_LANGUAGE_CONSTRUCT,
                   "Call-expression site as a syntactic construct. Cluster E sub-case (a) "
                   "fold target per audit-findings 0010: the call expression is an AST "
                   "node worth representing as a Symbol, distinct from the relationship "
                   "captured by an Edge of edge_type='calls'. Producers that previously "
                   "emitted kind='function_call' / 'subprocess_call' / 'db_query' / "
                   "'abi_call' now emit kind='call_site' with the prior specialisation "
                   "moved to meta['call_kind']."),
    SymbolKindSpec("arrow_function", AXIS_LANGUAGE_CONSTRUCT,
                   "Arrow-function expression (JS / TS)."),
    SymbolKindSpec("object", AXIS_LANGUAGE_CONSTRUCT,
                   "Object / singleton declaration (Scala / Kotlin)."),
    SymbolKindSpec("prop", AXIS_LANGUAGE_CONSTRUCT,
                   "Component prop declaration (Vue / React)."),
    SymbolKindSpec("slot", AXIS_LANGUAGE_CONSTRUCT,
                   "Component slot declaration (Vue / Svelte)."),
    SymbolKindSpec("template", AXIS_LANGUAGE_CONSTRUCT,
                   "Template declaration (C++ / Vue / Handlebars)."),
    SymbolKindSpec("directive", AXIS_LANGUAGE_CONSTRUCT,
                   "Directive declaration (Vue / Angular / GraphQL)."),
    SymbolKindSpec("declaration", AXIS_LANGUAGE_CONSTRUCT,
                   "Generic declaration (catch-all for non-categorized syntactic forms)."),
    SymbolKindSpec("keyword", AXIS_LANGUAGE_CONSTRUCT,
                   "Keyword-shaped construct (configuration languages)."),
    SymbolKindSpec("export", AXIS_LANGUAGE_CONSTRUCT,
                   "Export declaration (JS / TS / TOML / Rust)."),
    SymbolKindSpec("import", AXIS_LANGUAGE_CONSTRUCT,
                   "Top-level wasm-bindgen FFI import declaration. Reclassified "
                   "DEPRECATE-NO-FOLD → CANONICAL on 2026-05-07 by the indirection-"
                   "aware re-audit: the original Cluster E sub-case (b) drop verdict "
                   "in audit-findings 0010 was correct for the css.py / jsonnet.py / "
                   "astro.py / r_lang.py producers it inventoried, but Wave 6 PR 3 "
                   "(wasm_import → kind=\"import\" + meta[\"compilation_target\"]=\"wasm\") "
                   "added wasm_bindgen.py:266 as a new producer for a different "
                   "purpose — a synthetic boundary node the slicer BFS needs for "
                   "continuity. The wasm-bindgen `import` is a real top-level "
                   "construct in its source DSL, not a relabel of the imports Edge."),

    # ----------------------------------------------------------------
    # Cluster D (framework roles) — Phase 4b removal complete.
    # Producer migration folded all 29 values to canonical kind +
    # ``meta['framework_role']=<role>`` per audit-findings 0013 /
    # WI-habut Wave 5; deprecated registry entries removed in
    # PR #3633 (WI-butol). Consult audit-findings 0013 for the
    # per-value canonical / fold-target map.
    # ----------------------------------------------------------------

    # ----------------------------------------------------------------
    # Cluster E — Edge labels masquerading as Symbol kinds.
    # Phase 4b removal complete: ``call`` / ``inherit`` / ``include`` /
    # ``extends`` / ``read`` / ``write`` shipped as DEPRECATE-NO-FOLD
    # (relationship lives on the Edge), ``function_call`` /
    # ``subprocess_call`` / ``db_query`` / ``abi_call`` folded to
    # ``call_site`` + ``meta['call_kind']`` per audit-findings 0010 /
    # WI-zarov Wave 4. ``reference`` remains canonical below as the
    # ObjC selector-ref / generic-use-site Symbol kind.
    # ----------------------------------------------------------------
    SymbolKindSpec("reference", AXIS_LANGUAGE_CONSTRUCT,
                   "Use-site reference (Objective-C selector_ref shape; possibly "
                   "other _ref folds). Reclassified DEPRECATE-NO-FOLD → CANONICAL "
                   "on 2026-05-07 by the indirection-aware re-audit: the original "
                   "Cluster E sub-case (b) drop verdict in audit-findings 0010 "
                   "predated Wave 5's framework-role fold, which moved selector_ref "
                   "to canonical kind=\"reference\" + meta[\"framework_role\"]=\"selector_ref\" "
                   "at swift_objc.py:167 per audit-findings 0011's _ref shape "
                   "disposition. The (separate, defunct) json_config.py shape-2 "
                   "redesign that the original verdict referenced was for the "
                   "tsconfig case and has been resolved by Wave 6 PR 3."),

    # ----------------------------------------------------------------
    # Cluster F — Component / UI references.
    # ``component_ref`` shipped as DEPRECATE-NO-FOLD per audit-findings
    # 0011 / WI-mihiz Wave 4 (the relationship is captured by the
    # imports Edge with the file-shape Symbol as src; no replacement
    # Symbol kind). ``component`` remains canonical below.
    # ----------------------------------------------------------------
    SymbolKindSpec("component", AXIS_LANGUAGE_CONSTRUCT,
                   "Component declaration (Vue / Svelte / Astro / React)."),
    SymbolKindSpec("view", AXIS_LANGUAGE_CONSTRUCT,
                   "View declaration (MVC / template languages)."),

    # ----------------------------------------------------------------
    # Cluster B — File-shape entities.
    # CANONICAL promotions per audit-findings 0005 (WI-runod Wave 6 PR 1):
    # build-DSL / source-language top-level constructs (CMake, Meson,
    # COBOL, Pascal, Fortran, VHDL declarations) are language-level
    # constructs in their own tongue. Test 4 (mechanism vs. category)
    # confirms each names a *category* of source-language construct,
    # not a *mechanism* qualifier. Test 1 (property derivability) does
    # not fire — none is derivable from another field. The remaining
    # AXIS_PENDING entries below (module_file, component_file,
    # npm_package, composer_package, main_entry, bin, library_export,
    # export_entry, wasm_module, wasm_import, tsconfig) ship FOLD or
    # DEPRECATE-NO-FOLD verdicts; their producer migrations are
    # subsequent Wave-6 PRs.
    # ----------------------------------------------------------------
    SymbolKindSpec("file", AXIS_LANGUAGE_CONSTRUCT,
                   "File-shape symbol — top-level file declaration in build / "
                   "source DSLs. CANONICAL per audit-findings 0005."),
    SymbolKindSpec("library", AXIS_LANGUAGE_CONSTRUCT,
                   "Library declaration (CMake `add_library`, Meson `library`, "
                   "Cargo `[lib]`, etc.). CANONICAL per audit-findings 0005."),
    SymbolKindSpec("package", AXIS_LANGUAGE_CONSTRUCT,
                   "Package declaration (CMake `find_package`, VHDL `package`, "
                   "Go `package`, JS `package.json` synthesis, etc.). "
                   "CANONICAL per audit-findings 0005."),
    SymbolKindSpec("executable", AXIS_LANGUAGE_CONSTRUCT,
                   "Executable declaration (CMake `add_executable`, Meson "
                   "`executable`). CANONICAL per audit-findings 0005."),
    SymbolKindSpec("program", AXIS_LANGUAGE_CONSTRUCT,
                   "Program declaration (Fortran `PROGRAM`, COBOL `PROGRAM-ID`, "
                   "Pascal `program`). CANONICAL per audit-findings 0005."),
    SymbolKindSpec("project", AXIS_LANGUAGE_CONSTRUCT,
                   "Project declaration (Meson `project()`, .csproj root, "
                   "etc.). CANONICAL per audit-findings 0005."),
    # Wave 6 PR 3 FOLDs per audit-findings 0005 — producer migration
    # complete, deprecated entries (``module_file``, ``component_file``,
    # ``npm_package``, ``composer_package``, ``main_entry``,
    # ``library_export``, ``export_entry``, ``wasm_module``,
    # ``wasm_import``, ``tsconfig``, ``script``) removed in PR #3633.
    # Each fold target is the canonical Cluster A construct above; the
    # framework / ecosystem qualifier moves to ``Symbol.meta`` under
    # the named axis key (``module_system``, ``component_framework``,
    # ``package_ecosystem``, ``entry_role``, etc.).
    SymbolKindSpec("bin", AXIS_PENDING,
                   "Binary executable symbol. Pending cluster-B audit."),

    # ----------------------------------------------------------------
    # Cluster G — Build / config-shape.
    # AXIS_PENDING: separate-axis vs canonical decision per cluster-G
    # audit-findings doc.
    # ----------------------------------------------------------------
    SymbolKindSpec("test", AXIS_LANGUAGE_CONSTRUCT,
                   "Test-case symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("target", AXIS_LANGUAGE_CONSTRUCT,
                   "Build-target symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("special_target", AXIS_LANGUAGE_CONSTRUCT,
                   "Make special-target symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("recipe", AXIS_LANGUAGE_CONSTRUCT,
                   "Make recipe symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("env_var", AXIS_LANGUAGE_CONSTRUCT,
                   "Environment variable symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("build_arg", AXIS_LANGUAGE_CONSTRUCT,
                   "Build argument symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("exposed_port", AXIS_LANGUAGE_CONSTRUCT,
                   "Container exposed-port symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("stage", AXIS_LANGUAGE_CONSTRUCT,
                   "Build / pipeline stage. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("requirement", AXIS_LANGUAGE_CONSTRUCT,
                   "Requirement / pip requirement. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("setting", AXIS_LANGUAGE_CONSTRUCT,
                   "Setting / option symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("derivation", AXIS_LANGUAGE_CONSTRUCT,
                   "Nix derivation symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("dependency", AXIS_LANGUAGE_CONSTRUCT,
                   "Dependency entry. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("addtask", AXIS_LANGUAGE_CONSTRUCT,
                   "BitBake addtask symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("task", AXIS_LANGUAGE_CONSTRUCT,
                   "Generic task symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("trigger", AXIS_LANGUAGE_CONSTRUCT,
                   "Pipeline / DB trigger symbol. CANONICAL per audit-findings 0006."),

    # ----------------------------------------------------------------
    # Cluster H — Domain-specific long tail.
    # AXIS_PENDING: per-value audit pending in cluster-H audit-findings.
    # ----------------------------------------------------------------
    SymbolKindSpec("section", AXIS_LANGUAGE_CONSTRUCT,
                   "Section symbol (markdown / config). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("paragraph", AXIS_LANGUAGE_CONSTRUCT,
                   "Paragraph symbol (markdown / docs). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("code_block", AXIS_LANGUAGE_CONSTRUCT,
                   "Code-block symbol (markdown). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("diagram", AXIS_LANGUAGE_CONSTRUCT,
                   "Diagram symbol (mermaid / graphviz). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("plot", AXIS_LANGUAGE_CONSTRUCT,
                   "Plot symbol (notebook / R). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("yield", AXIS_LANGUAGE_CONSTRUCT,
                   "Yield-statement symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("for_loop", AXIS_LANGUAGE_CONSTRUCT,
                   "For-loop symbol (control-flow). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("conditional", AXIS_LANGUAGE_CONSTRUCT,
                   "Conditional-statement symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("block", AXIS_LANGUAGE_CONSTRUCT,
                   "Block symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("prefix", AXIS_LANGUAGE_CONSTRUCT,
                   "Prefix symbol (URI / namespace). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("base", AXIS_LANGUAGE_CONSTRUCT,
                   "Base symbol (XML / OWL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("query", AXIS_LANGUAGE_CONSTRUCT,
                   "Query symbol (GraphQL / SQL operation). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("entry", AXIS_LANGUAGE_CONSTRUCT,
                   "Entry symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("entity", AXIS_LANGUAGE_CONSTRUCT,
                   "Entity symbol (DSL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("architecture", AXIS_LANGUAGE_CONSTRUCT,
                   "Architecture symbol (VHDL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("participant", AXIS_LANGUAGE_CONSTRUCT,
                   "Participant symbol (mermaid). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("state", AXIS_LANGUAGE_CONSTRUCT,
                   "State symbol (state-machine DSL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("fragment", AXIS_LANGUAGE_CONSTRUCT,
                   "Fragment symbol (GraphQL / template). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("partial", AXIS_LANGUAGE_CONSTRUCT,
                   "Partial symbol (template). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("provider", AXIS_LANGUAGE_CONSTRUCT,
                   "Provider symbol (Terraform / DI). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("local", AXIS_LANGUAGE_CONSTRUCT,
                   "Local symbol (Terraform local). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("style_block", AXIS_LANGUAGE_CONSTRUCT,
                   "Style-block symbol (Vue / scoped CSS). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("permission", AXIS_LANGUAGE_CONSTRUCT,
                   "Permission symbol (k8s / Solidity). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("keyframes", AXIS_LANGUAGE_CONSTRUCT,
                   "CSS @keyframes symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("media", AXIS_LANGUAGE_CONSTRUCT,
                   "CSS @media symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("font_face", AXIS_LANGUAGE_CONSTRUCT,
                   "CSS @font-face symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("class_selector", AXIS_LANGUAGE_CONSTRUCT,
                   "CSS class selector symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("id_selector", AXIS_LANGUAGE_CONSTRUCT,
                   "CSS id selector symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("rule_set", AXIS_LANGUAGE_CONSTRUCT,
                   "CSS / shader rule-set symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("subdirectory", AXIS_LANGUAGE_CONSTRUCT,
                   "Subdirectory pseudo-symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("table", AXIS_LANGUAGE_CONSTRUCT,
                   "Table symbol (SQL / TOML / Markdown). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("table_array", AXIS_LANGUAGE_CONSTRUCT,
                   "TOML table-array symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("link", AXIS_LANGUAGE_CONSTRUCT,
                   "Link symbol (markdown / yaml-anchor). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("label", AXIS_LANGUAGE_CONSTRUCT,
                   "Label symbol (assembly / k8s). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("command", AXIS_LANGUAGE_CONSTRUCT,
                   "Command symbol (shell / Cobra). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("environment", AXIS_LANGUAGE_CONSTRUCT,
                   "Environment symbol (LaTeX / shell). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("binding", AXIS_LANGUAGE_CONSTRUCT,
                   "Binding symbol (DSL / DI). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("id", AXIS_LANGUAGE_CONSTRUCT,
                   "Id symbol (k8s / DSL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("source", AXIS_LANGUAGE_CONSTRUCT,
                   "Source symbol (data-flow / shell). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("port", AXIS_LANGUAGE_CONSTRUCT,
                   "Port symbol (k8s / VHDL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("output", AXIS_LANGUAGE_CONSTRUCT,
                   "Output symbol (Terraform / shader). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("input", AXIS_LANGUAGE_CONSTRUCT,
                   "Input symbol (Terraform / shader). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("value", AXIS_LANGUAGE_CONSTRUCT,
                   "Value symbol (key-value DSLs). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("pattern", AXIS_LANGUAGE_CONSTRUCT,
                   "Pattern symbol (DSL / regex). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("subscript", AXIS_LANGUAGE_CONSTRUCT,
                   "Subscript symbol (Swift / Python __getitem__). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("signal", AXIS_LANGUAGE_CONSTRUCT,
                   "Signal symbol (VHDL / Verilog / Qt). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("message", AXIS_LANGUAGE_CONSTRUCT,
                   "Protobuf ``message`` declaration (``proto.py:260``). "
                   "CANONICAL per audit-findings 0007 (reclassified Wave 6 "
                   "PR 4 — the original DEPRECATE-NO-FOLD verdict was a "
                   "literal-grep blind-spot miss; ``proto.py`` emits via "
                   "``_make_proto_symbol(..., 'message', ...)`` indirection)."),
    SymbolKindSpec("data", AXIS_LANGUAGE_CONSTRUCT,
                   "Data symbol (Terraform data block). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("resource", AXIS_LANGUAGE_CONSTRUCT,
                   "Resource symbol (Terraform / k8s). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("event", AXIS_LANGUAGE_CONSTRUCT,
                   "Event symbol (DSL / Solidity). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("protocol", AXIS_LANGUAGE_CONSTRUCT,
                   "Protocol symbol (Swift / Solidity / DSL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("index", AXIS_LANGUAGE_CONSTRUCT,
                   "Index symbol (SQL / DSL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("node", AXIS_LANGUAGE_CONSTRUCT,
                   "Node symbol (k8s / DSL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("inductive", AXIS_LANGUAGE_CONSTRUCT,
                   "Lean ``inductive`` type declaration (``lean.py:247``). "
                   "CANONICAL per audit-findings 0007 (reclassified Wave 6 "
                   "PR 4 — the original DEPRECATE-NO-FOLD verdict was a "
                   "literal-grep blind-spot miss; ``lean.py`` emits via "
                   "``add_symbol(..., 'inductive')`` indirection)."),
    SymbolKindSpec("theorem", AXIS_LANGUAGE_CONSTRUCT,
                   "Theorem-prover top-level construct (Lean theorems and "
                   "lemmas at ``lean.py:222,231``; TLA+ theorems at "
                   "``tlaplus.py:207``). CANONICAL per audit-findings 0007 "
                   "(reclassified Wave 6 PR 4 — the original DEPRECATE-NO-FOLD "
                   "verdict was a literal-grep blind-spot miss; both producers "
                   "emit via ``add_symbol(..., 'theorem')`` indirection)."),
    SymbolKindSpec("playbook", AXIS_LANGUAGE_CONSTRUCT,
                   "Ansible playbook symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("external_symbol", AXIS_LANGUAGE_CONSTRUCT,
                   "IR-pipeline boundary pseudo-symbol — emitted by "
                   "``create_boundary_nodes`` (``ir.py:959``) for every "
                   "edge endpoint that doesn't resolve to a real Symbol "
                   "(stdlib calls, npm imports, third-party constructors). "
                   "CANONICAL per audit-findings 0007 §\"Diagnostic findings "
                   "#3\" (Wave 6 PR 6 reclassification): structurally a "
                   "top-level construct in the IR pipeline's own DSL, "
                   "parallel to other Cluster H domain-DSL constructs "
                   "(``playbook``, ``participant``, …). Consumers query "
                   "boundary status via ``is_external_boundary(sym)`` "
                   "(meta-key based), so this kind is a label not a "
                   "discriminator — promotion does not change consumer "
                   "behavior."),

    # ----------------------------------------------------------------
    # WI-nubuv ext A discoveries — assignment-form producer leaks
    # ----------------------------------------------------------------
    # Surfaced by the L3 producer-coherence linter when extension A
    # (function-local assignment trace) landed and walked back
    # ``kind = "<literal>"`` shapes that were previously invisible to
    # the literal-kwarg-only matcher. All registered AXIS_PENDING for
    # follow-on Cluster-G / Cluster-H audit; the values themselves
    # are real language-level kinds, just never previously surfaced.
    SymbolKindSpec("binary", AXIS_PENDING,
                   "Cargo `[[bin]]` target kind. "
                   "Pending cluster-G audit."),
    SymbolKindSpec("benchmark", AXIS_PENDING,
                   "Cargo `[[bench]]` target kind. "
                   "Pending cluster-G audit."),
    SymbolKindSpec("example", AXIS_PENDING,
                   "Cargo `[[example]]` target kind. "
                   "Pending cluster-G audit."),
    SymbolKindSpec("workspace", AXIS_PENDING,
                   "Cargo `[workspace]` table kind. "
                   "Pending cluster-G audit."),
    SymbolKindSpec("handler", AXIS_PENDING,
                   "Ansible playbook handler. Pending cluster-G audit."),
    SymbolKindSpec("pattern_rule", AXIS_PENDING,
                   "Make pattern-rule target. Pending cluster-G audit."),
    SymbolKindSpec("helper", AXIS_PENDING,
                   "Handlebars block helper (non-builtin). "
                   "Pending cluster-H audit."),
    SymbolKindSpec("uniform", AXIS_PENDING,
                   "Shader uniform binding (GLSL / WGSL). "
                   "Pending cluster-H audit."),
    SymbolKindSpec("varying", AXIS_PENDING,
                   "GLSL varying qualifier (legacy interpolation). "
                   "Pending cluster-H audit."),
    SymbolKindSpec("private", AXIS_PENDING,
                   "WGSL `var<private>` address space. "
                   "Pending cluster-H audit."),
    SymbolKindSpec("storage", AXIS_PENDING,
                   "WGSL `var<storage>` address space. "
                   "Pending cluster-H audit."),
    SymbolKindSpec("workgroup", AXIS_PENDING,
                   "WGSL `var<workgroup>` address space. "
                   "Pending cluster-H audit."),
)


def all_symbol_kind_names() -> frozenset[str]:
    """Return every canonical Symbol.kind name."""
    return frozenset(spec.name for spec in SYMBOL_KINDS)


def symbol_kinds_on_axis(axis: str) -> tuple[SymbolKindSpec, ...]:
    """Return all Symbol.kind specs whose axis equals *axis*.

    Use this in place of hardcoded sets like
    ``_CALLABLE_KINDS = {"function", "method", "fn", "proc"}``: query
    by axis (or by another property) instead of enumerating values, so
    new specs that match the axis are picked up automatically.
    """
    return tuple(spec for spec in SYMBOL_KINDS if spec.axis == axis)


def find_symbol_kind(name: str) -> SymbolKindSpec | None:
    """Look up a Symbol.kind spec by name; return None if not registered."""
    for spec in SYMBOL_KINDS:
        if spec.name == name:
            return spec
    return None


def find_axis_drift(repo_root: Path) -> list[str]:
    """Scan the repo for hardcoded ``*KIND*`` sets that drift from the registry.

    Wraps the field-agnostic AST walker in
    :mod:`hypergumbo_core.axis_drift` with the parameterization for
    ``Symbol.kind``: scans for module-level set / frozenset assignments
    whose target name contains ``KIND`` and returns a human-readable
    list of drift locations (file:line references plus the offending
    values).

    Per ADR-0027 §"Phase 1": the registry is seeded with every value
    currently emitted by producer code, so on a clean tree this
    function returns an empty list. Adding a NEW hardcoded value at a
    consumer site without first registering it in :data:`SYMBOL_KINDS`
    causes this scan to surface the addition; that's the structural
    enforcement the registry exists to provide.

    Used by the property test in ``tests/test_symbol_kinds.py`` and the
    pre-commit linter at ``scripts/check-symbol-kind-drift``.
    """
    from hypergumbo_core.axis_drift import find_drift
    return find_drift(
        repo_root,
        name_filter="KIND",
        registry_names=all_symbol_kind_names(),
        # ``PROTOCOL_KINDS`` and ``BRIDGE_KINDS`` in ``edge_types.py``
        # are vocabularies for ``Edge.meta['protocol']`` /
        # ``Edge.meta['bridge_kind']`` — not ``Symbol.kind`` sets.
        # They share the ``KIND`` substring but live on a different
        # axis; exclude them by name.
        excluded_target_names=("PROTOCOL_KINDS", "BRIDGE_KINDS"),
    )
