<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- AUTO-GENERATED — do not edit manually.
     Regenerate with: ./scripts/generate-concept-axes
     Sources of truth:
       packages/hypergumbo-core/src/hypergumbo_core/edge_types.py
       packages/hypergumbo-core/src/hypergumbo_core/symbol_kinds.py
       packages/hypergumbo-core/src/hypergumbo_core/evidence_types.py -->

# Concept Axes

Typing axes maintained in hypergumbo's survey. Each axis names a
dimension along which a multi-value field's values must be classified.
New axes are introduced via ADR following ADR-0024's four-part
declaration template (axis name, axiom, consumer pattern, enforcement);
ADR-0023 (Edge.type) is the worked example, and ADR-0027 (Symbol.kind)
is the second instantiation.

The current axes apply to `Edge.type`, `Symbol.kind`, and
`Edge.evidence_type`. Other multi-value fields (`supply_chain.tier`,
etc.) will be added here as their axes are formally declared.

## Why this doc exists

`docs/schema.json` carries the same axis information under the
`x-axis-of-values` extension keyword, but reading the schema by eye
doesn't surface the axis structure — values appear in registry order,
not grouped by axis. This doc is the human-readable view; the schema
remains the machine-readable source of truth for downstream consumers.

The Fundamental Concept Audit playbook
(`.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md`)
nominates an axis from this list each release cycle for a deliberate
re-audit, complementing the commit-count cadence hook.

## Downstream consumers

If you consume hypergumbo's behavior-map JSON output and your code
filters or weights edges by `edge_type`, see
[`docs/migrating-edge-types.md`](migrating-edge-types.md) for the
rename table, the meta-key vocabulary, and migration patterns.

The values currently in the `endpoint_shape` section below (under
`Edge.type` axes and `Symbol.kind` axes both) are deprecation
candidates whose producers still emit them. Each is scheduled for
migration to a canonical relationship-axis (or language_construct-axis)
name plus an `edge.meta` (or `Symbol.meta["framework_role"]`) payload
— see the per-ADR migration guides for the per-value fold targets.
Each value's producer-side migration ships as its own per-family
subset, with bakeoff validation, then a `SCHEMA_VERSION` minor bump
removes the value from the registry. Values already removed at
`SCHEMA_VERSION` 0.4.0 do NOT appear here; this section is the
remaining backlog.

## `Edge.type` axes

### `relationship` — ADR-0023 compliant

Values that name the relationship the edge expresses between src and dst. Per ADR-0023, this is the only axis a new `edge_type` value should occupy.

- **`calls`** — Caller invokes callee.
- **`constrains`** — pip ``-c`` / ``--constraint`` file reference — constrains version selection without forcing install. Peer of ``includes`` (which models ``-r`` / ``--requirement``). Surfaced by WI-nubuv ext A in linkers/requirements.py.
- **`contains`** — Container symbol holds member symbol.
- **`data_flows_to`** — Data flow edge per ADR-0015 — value computed at src reaches dst.
- **`decorated_by`** — Symbol is decorated/annotated by another (e.g., Python decorator, Java annotation, C# attribute, Rust derive).
- **`defines_target`** — Config file defines a build/run/deploy target (Makefile rule, package.json script, pyproject entry point, Compose service, etc.).
- **`depends_on`** — A package/build manifest DECLARES a dependency on another (package.json, Dockerfile, Makefile, meson, ...): a declaration edge from the manifest/project to its dependency. Distinct from depends_on_manifest (the import-resolution bridge) -- see WI-dinih.
- **`depends_on_manifest`** — An importing source file RESOLVED to a manifest-declared dependency (the dependency linker's import->declared-dep bridge; evidence_type=import_to_manifest): a resolution edge from the file to the dependency. Distinct from depends_on (the manifest's own declaration) -- see WI-dinih.
- **`dispatches_to`** — Caller dispatches to callee via runtime indirection (virtual method, function pointer, DI resolution, etc.).
- **`event_publishes`** — Producer publishes an event/message that the consumer receives via an async channel (event bus, queue, CRDT, etc.).
- **`extends`** — Class extends a superclass.
- **`implements`** — Class implements an interface.
- **`imports`** — Module imports another module or symbol.
- **`includes`** — File or class includes / sources / mixes-in another unit's content (LaTeX \include, RST .. include::, Meson subdir, Ruby `include`/`extend` mixin — WI-hatip).
- **`inherits`** — Class/contract inherits from a parent (used by languages where 'inherits' reads more naturally than 'extends').
- **`instantiates`** — Constructor or factory creates an instance.
- **`links`** — Generic linkage relationship.
- **`module_attr_ref`** — Reads an attribute on an imported module (e.g., os.environ).
- **`module_exports`** — Module exposes a symbol as part of its public surface (JS export, Python __all__, Rust pub, etc.).
- **`overrides`** — Method overrides a parent's same-named method (parallel to extends/implements; the override declaration itself).
- **`references`** — Symbol references another by name without invocation.
- **`sources`** — Sources another file (e.g., shell ``source``).
- **`subprocess_calls`** — Symbol invokes another symbol via a subprocess.
- **`uses`** — Generic symbol-usage relationship.
- **`wraps`** — Decorator or middleware wraps the target symbol.

### `endpoint_shape` — deprecation candidates per ADR-0023 §6

Values whose meaning is leaked into the type label even though it is captured by `src.kind` / `dst.kind` / language metadata. Migration plan in ADR-0023 §6 folds these back into relationship-shaped names with kind/language metadata on the endpoint nodes.

_(empty — no values currently classified on this axis)_

### `pending_classification` — per-family audit pending per ADR-0023 §5

Values deferred to per-family audit. Some may be genuinely distinct relationships; others are protocol-conditional duplicates of a more general relationship. Verdicts arrive with each family's audit.

_(empty — no values currently classified on this axis)_


## `Symbol.kind` axes

Per ADR-0027, `Symbol.kind` names the source-language syntactic
construct the symbol represents. Properties derivable from edges or
framework metadata are queried from those structures rather than
smuggled into the kind label. The audit at WI-dumiz-bikul classified
the 192 distinct `Symbol.kind` values currently in production into
eight clusters; this section lays out the three axes (Cluster A on
`language_construct`; Clusters D, E, plus `component_ref` from F on
`endpoint_shape` deprecation; Clusters B, C, G, H on
`pending_classification` until per-cluster audit-findings docs ship).

### `language_construct` — ADR-0027 compliant

Values that name the source-language syntactic construct the symbol represents. Per ADR-0027, this is the only axis a new `Symbol.kind` value should occupy.

- **`abstract`** — Abstract class / member declaration.
- **`addtask`** — BitBake addtask symbol. CANONICAL per audit-findings 0006.
- **`alias`** — Generic alias declaration.
- **`architecture`** — Architecture symbol (VHDL). CANONICAL per audit-findings 0007.
- **`arrow_function`** — Arrow-function expression (JS / TS).
- **`assumption`** — TLA+ `ASSUME` declaration — a named top-level assumption / axiom, sibling to `theorem` / `operator`. Producer: `tlaplus.py` `assumption` node via nested `add_symbol`. Registered per the WI-zipis drain / ADR-0027 verdict (audit-findings 0015).
- **`attribute`** — Attribute declaration (Python class attribute, etc.).
- **`base`** — Base symbol (XML / OWL). CANONICAL per audit-findings 0007.
- **`binding`** — Binding symbol (DSL / DI). CANONICAL per audit-findings 0007.
- **`block`** — Block symbol. CANONICAL per audit-findings 0007.
- **`build_arg`** — Build argument symbol. CANONICAL per audit-findings 0006.
- **`call_site`** — Call-expression site as a syntactic construct. Cluster E sub-case (a) fold target per audit-findings 0010: the call expression is an AST node worth representing as a Symbol, distinct from the relationship captured by an Edge of edge_type='calls'. Producers that previously emitted kind='function_call' / 'subprocess_call' / 'db_query' / 'abi_call' now emit kind='call_site' with the prior specialisation moved to meta['call_kind'].
- **`class`** — Class declaration.
- **`class_selector`** — CSS class selector symbol. CANONICAL per audit-findings 0007.
- **`code_block`** — Code-block symbol (markdown). CANONICAL per audit-findings 0007.
- **`command`** — Command symbol (shell / Cobra). CANONICAL per audit-findings 0007.
- **`component`** — Component declaration (Vue / Svelte / Astro / React).
- **`conditional`** — Conditional-statement symbol. CANONICAL per audit-findings 0007.
- **`const`** — Const declaration (C / C++ / Rust / JS const).
- **`constant`** — Constant / final / let-immutable binding.
- **`constructor`** — Constructor / __init__ / init method.
- **`contract`** — Smart-contract declaration (Solidity / Vyper / Move). Sibling to `class` / `interface` / `struct` — names the source-language top-level construct directly. Producers: `solidity.py:265` emits `add_symbol(name, "contract", ...)` for `contract_declaration` AST nodes. Consumed by `library-exports.yaml`'s `symbol_kind: ^contract$` rule that surfaces deployable Solidity contracts as library exports.
- **`data`** — Data symbol (Terraform data block). CANONICAL per audit-findings 0007.
- **`declaration`** — Generic declaration (catch-all for non-categorized syntactic forms).
- **`defined_type`** — Defined / nominal type declaration (Puppet / Coq).
- **`dependency`** — Dependency entry. CANONICAL per audit-findings 0006.
- **`derivation`** — Nix derivation symbol. CANONICAL per audit-findings 0006.
- **`diagram`** — Diagram symbol (mermaid / graphviz). CANONICAL per audit-findings 0007.
- **`directive`** — Directive declaration (Vue / Angular / GraphQL).
- **`entity`** — Entity symbol (DSL). CANONICAL per audit-findings 0007.
- **`entry`** — Entry symbol. CANONICAL per audit-findings 0007.
- **`enum`** — Enum declaration.
- **`env_var`** — Environment variable symbol. CANONICAL per audit-findings 0006.
- **`environment`** — Environment symbol (LaTeX / shell). CANONICAL per audit-findings 0007.
- **`error_set`** — Zig error-set declaration. Surfaced by WI-nubuv's inline-IfExp / non-string-Constant classifier fixes from the function-local kind union in hypergumbo-lang-extended1/zig.py:259. Zig errors are a first-class language construct sibling to `struct` / `enum` / `union`.
- **`event`** — Event symbol (DSL / Solidity). CANONICAL per audit-findings 0007.
- **`executable`** — Executable declaration (CMake `add_executable`, Meson `executable`). CANONICAL per audit-findings 0005.
- **`export`** — Export declaration (JS / TS / TOML / Rust).
- **`exposed_port`** — Container exposed-port symbol. CANONICAL per audit-findings 0006.
- **`extension`** — Dart `extension` declaration (`extension NumberParsing on String { ... }`) — a top-level named construct that adds members to an existing type without subclassing. A genuine peer to `class` / `mixin` / `enum` (own keyword, own `extension_declaration` AST node), NOT a `class` (defines no new type) nor a `mixin` (Dart's distinct `mixin_declaration` already maps to `mixin`). Producer: `dart.py` `extension_declaration`. Registered per the WI-zipis drain / ADR-0027 verdict (audit-findings 0015).
- **`external_symbol`** — IR-pipeline boundary pseudo-symbol — emitted by ``create_boundary_nodes`` (``ir.py:959``) for every edge endpoint that doesn't resolve to a real Symbol (stdlib calls, npm imports, third-party constructors). CANONICAL per audit-findings 0007 §"Diagnostic findings #3" (Wave 6 PR 6 reclassification): structurally a top-level construct in the IR pipeline's own DSL, parallel to other Cluster H domain-DSL constructs (``playbook``, ``participant``, …). Consumers query boundary status via ``is_external_boundary(sym)`` (meta-key based), so this kind is a label not a discriminator — promotion does not change consumer behavior.
- **`field`** — Field declaration on a struct / class / record.
- **`file`** — File-shape symbol — top-level file declaration in build / source DSLs. CANONICAL per audit-findings 0005.
- **`filter`** — PowerShell `filter` declaration — a named callable whose body is an implicit `process` block, run once per pipeline object. A distinct source keyword (own tree-sitter node), sibling to `function` / `workflow`; kept distinct like `subroutine` / `procedure` / `generic` rather than folded to `function`. Producer: `powershell.py` (`function_statement` child `filter`). Registered per the WI-zipis drain / ADR-0027 verdict (audit-findings 0015).
- **`font_face`** — CSS @font-face symbol. CANONICAL per audit-findings 0007.
- **`for_loop`** — For-loop symbol (control-flow). CANONICAL per audit-findings 0007.
- **`fragment`** — Fragment symbol (GraphQL / template). CANONICAL per audit-findings 0007.
- **`function`** — Top-level function definition.
- **`generic`** — Generic-function declaration (Common Lisp `defgeneric`). The dispatch-declaration construct, sibling to `method` (`defmethod`) and `function` (`defun`). Producer: `commonlisp.py` maps `defgeneric` -> kind="generic" via its `kind_map` dict. Surfaced (and registered) when the INV-loguk homoiconic-CC slice added the first registry-scanned `*_KINDS` set naming it — the original ADR-0027 Phase-1 seeding missed it because the producer emits via `kind_map` indirection, not a literal `kind="generic"` kwarg (same literal-grep blind-spot class as `message` / `inductive` / `theorem`).
- **`getter`** — Property getter accessor.
- **`id`** — Id symbol (k8s / DSL). CANONICAL per audit-findings 0007.
- **`id_selector`** — CSS id selector symbol. CANONICAL per audit-findings 0007.
- **`import`** — Top-level wasm-bindgen FFI import declaration. Reclassified DEPRECATE-NO-FOLD → CANONICAL on 2026-05-07 by the indirection-aware re-audit: the original Cluster E sub-case (b) drop verdict in audit-findings 0010 was correct for the css.py / jsonnet.py / astro.py / r_lang.py producers it inventoried, but Wave 6 PR 3 (wasm_import → kind="import" + meta["compilation_target"]="wasm") added wasm_bindgen.py:266 as a new producer for a different purpose — a synthetic boundary node the slicer BFS needs for continuity. The wasm-bindgen `import` is a real top-level construct in its source DSL, not a relabel of the imports Edge.
- **`index`** — Index symbol (SQL / DSL). CANONICAL per audit-findings 0007.
- **`inductive`** — Lean ``inductive`` type declaration (``lean.py:247``). CANONICAL per audit-findings 0007 (reclassified Wave 6 PR 4 — the original DEPRECATE-NO-FOLD verdict was a literal-grep blind-spot miss; ``lean.py`` emits via ``add_symbol(..., 'inductive')`` indirection).
- **`input`** — Input symbol (Terraform / shader). CANONICAL per audit-findings 0007.
- **`instance`** — Typeclass / interface instance declaration (Haskell / Lean / PureScript `instance`, Scala 3 `given`).
- **`interface`** — Interface declaration.
- **`keyframes`** — CSS @keyframes symbol. CANONICAL per audit-findings 0007.
- **`keyword`** — Keyword-shaped construct (configuration languages).
- **`label`** — Label symbol (assembly / k8s). CANONICAL per audit-findings 0007.
- **`library`** — Library declaration (CMake `add_library`, Meson `library`, Cargo `[lib]`, etc.). CANONICAL per audit-findings 0005.
- **`link`** — Link symbol (markdown / yaml-anchor). CANONICAL per audit-findings 0007.
- **`list`** — Smithy `list` shape — a named ordered-collection type declaration (`list Foo { member: Bar }`). A first-class aggregate-shape construct, sibling to the already-registered smithy shapes `union` / `enum` / `simple_type`; no generic collection kind exists to fold to (`type` is too broad, `struct`/`record` are product types). Producer: `smithy.py` `list_statement` -> `_extract_shape(..., "list", ...)`. Registered per the WI-zipis producer-sweep drain / ADR-0027 verdict (audit-findings 0015); its emit site is a nested-closure/positional helper the literal-grep diagnostics missed.
- **`local`** — Local symbol (Terraform local). CANONICAL per audit-findings 0007.
- **`macro`** — Macro definition (Rust / C / Scheme).
- **`map`** — Smithy `map` shape — a named key->value associative-type declaration (`map Foo { key: Bar, value: Baz }`). Sibling aggregate shape to `list` / `union` / `enum`; not a `struct` (associative, not fixed named members) and no generic map/dict kind exists to fold to. Producer: `smithy.py` `map_statement` -> `_extract_shape(..., "map", ...)`. Registered per the WI-zipis drain / ADR-0027 verdict (audit-findings 0015).
- **`media`** — CSS @media symbol. CANONICAL per audit-findings 0007.
- **`message`** — Protobuf ``message`` declaration (``proto.py:260``). CANONICAL per audit-findings 0007 (reclassified Wave 6 PR 4 — the original DEPRECATE-NO-FOLD verdict was a literal-grep blind-spot miss; ``proto.py`` emits via ``_make_proto_symbol(..., 'message', ...)`` indirection).
- **`method`** — Method on a class / struct / interface.
- **`mixin`** — Mixin declaration (Ruby / Sass).
- **`modifier`** — Solidity / Vyper modifier declaration. A function modifier is a reusable pre/post-condition block applied to contract functions (e.g., `onlyOwner`). Producer: `solidity.py:302` emits `add_symbol(mod_name, "modifier", ...)` for `modifier_definition` AST nodes.
- **`module`** — Module declaration (the source-level construct).
- **`mutation`** — Mutation symbol (GraphQL operation). Top-level construct, sibling to query/fragment (audit-findings 0007 omission; registered per id-format:F3).
- **`namespace`** — Namespace declaration (C++ / TypeScript / C#).
- **`node`** — Node symbol (k8s / DSL). CANONICAL per audit-findings 0007.
- **`object`** — Object / singleton declaration (Scala / Kotlin).
- **`operator`** — TLA+ operator definition (`Op(x) == ...`) — the primary TLA+ definitional construct, walked alongside `theorem` / `assumption`. Producer: `tlaplus.py` `operator_definition` via the nested `add_symbol(...)` helper (a literal-grep blind-spot, same class as `message` / `generic`). Registered per the WI-zipis drain / ADR-0027 verdict (audit-findings 0015).
- **`output`** — Output symbol (Terraform / shader). CANONICAL per audit-findings 0007.
- **`package`** — Package declaration (CMake `find_package`, VHDL `package`, Go `package`, JS `package.json` synthesis, etc.). CANONICAL per audit-findings 0005.
- **`paragraph`** — Paragraph symbol (markdown / docs). CANONICAL per audit-findings 0007.
- **`partial`** — Partial symbol (template). CANONICAL per audit-findings 0007.
- **`participant`** — Participant symbol (mermaid). CANONICAL per audit-findings 0007.
- **`pattern`** — Pattern symbol (DSL / regex). CANONICAL per audit-findings 0007.
- **`permission`** — Permission symbol (k8s / Solidity). CANONICAL per audit-findings 0007.
- **`playbook`** — Ansible playbook symbol. CANONICAL per audit-findings 0007.
- **`plot`** — Plot symbol (notebook / R). CANONICAL per audit-findings 0007.
- **`port`** — Port symbol (k8s / VHDL). CANONICAL per audit-findings 0007.
- **`prefix`** — Prefix symbol (URI / namespace). CANONICAL per audit-findings 0007.
- **`procedure`** — Procedure declaration (Pascal / Ada / SQL).
- **`program`** — Program declaration (Fortran `PROGRAM`, COBOL `PROGRAM-ID`, Pascal `program`). CANONICAL per audit-findings 0005.
- **`project`** — Project declaration (Meson `project()`, .csproj root, etc.). CANONICAL per audit-findings 0005.
- **`prop`** — Component prop declaration (Vue / React).
- **`property`** — Property declaration (Kotlin / Swift / C#).
- **`protocol`** — Protocol symbol (Swift / Solidity / DSL). CANONICAL per audit-findings 0007.
- **`provider`** — Provider symbol (Terraform / DI). CANONICAL per audit-findings 0007.
- **`query`** — Query symbol (GraphQL / SQL operation). CANONICAL per audit-findings 0007.
- **`recipe`** — Make recipe symbol. CANONICAL per audit-findings 0006.
- **`record`** — Record declaration (Java 14+, Erlang, Haskell).
- **`reference`** — Use-site reference (Objective-C selector_ref shape; possibly other _ref folds). Reclassified DEPRECATE-NO-FOLD → CANONICAL on 2026-05-07 by the indirection-aware re-audit: the original Cluster E sub-case (b) drop verdict in audit-findings 0010 predated Wave 5's framework-role fold, which moved selector_ref to canonical kind="reference" + meta["framework_role"]="selector_ref" at swift_objc.py:167 per audit-findings 0011's _ref shape disposition. The (separate, defunct) json_config.py shape-2 redesign that the original verdict referenced was for the tsconfig case and has been resolved by Wave 6 PR 3.
- **`requirement`** — Requirement / pip requirement. CANONICAL per audit-findings 0006.
- **`resource`** — Resource symbol (Terraform / k8s). CANONICAL per audit-findings 0007.
- **`rule_set`** — CSS / shader rule-set symbol. CANONICAL per audit-findings 0007.
- **`scalar`** — Scalar type-definition symbol (GraphQL `scalar Date`). Top-level type-system construct, sibling to type/input/interface/enum/union (audit-findings 0007 omission, surfaced by the WI-zigih dict-indirection gate).
- **`section`** — Section symbol (markdown / config). CANONICAL per audit-findings 0007.
- **`setter`** — Property setter accessor.
- **`setting`** — Setting / option symbol. CANONICAL per audit-findings 0006.
- **`signal`** — Signal symbol (VHDL / Verilog / Qt). CANONICAL per audit-findings 0007.
- **`simple_type`** — Simple-type declaration (XSD-shape).
- **`slot`** — Component slot declaration (Vue / Svelte).
- **`source`** — Source symbol (data-flow / shell). CANONICAL per audit-findings 0007.
- **`special_target`** — Make special-target symbol. CANONICAL per audit-findings 0006.
- **`stage`** — Build / pipeline stage. CANONICAL per audit-findings 0006.
- **`state`** — State symbol (state-machine DSL). CANONICAL per audit-findings 0007.
- **`struct`** — Struct / record-type declaration.
- **`style_block`** — Style-block symbol (Vue / scoped CSS). CANONICAL per audit-findings 0007.
- **`subdirectory`** — Subdirectory pseudo-symbol. CANONICAL per audit-findings 0007.
- **`subroutine`** — Subroutine / sub declaration (Fortran / Perl).
- **`subscript`** — Subscript symbol (Swift / Python __getitem__). CANONICAL per audit-findings 0007.
- **`subscription`** — Subscription symbol (GraphQL operation). Top-level construct, sibling to query/fragment (audit-findings 0007 omission; registered per id-format:F3).
- **`table`** — Table symbol (SQL / TOML / Markdown). CANONICAL per audit-findings 0007.
- **`table_array`** — TOML table-array symbol. CANONICAL per audit-findings 0007.
- **`target`** — Build-target symbol. CANONICAL per audit-findings 0006.
- **`task`** — Generic task symbol. CANONICAL per audit-findings 0006.
- **`template`** — Template declaration (C++ / Vue / Handlebars).
- **`test`** — Test-case symbol. CANONICAL per audit-findings 0006.
- **`theorem`** — Theorem-prover top-level construct (Lean theorems and lemmas at ``lean.py:222,231``; TLA+ theorems at ``tlaplus.py:207``). CANONICAL per audit-findings 0007 (reclassified Wave 6 PR 4 — the original DEPRECATE-NO-FOLD verdict was a literal-grep blind-spot miss; both producers emit via ``add_symbol(..., 'theorem')`` indirection).
- **`trait`** — Trait declaration (Rust / Scala / Groovy).
- **`trigger`** — Pipeline / DB trigger symbol. CANONICAL per audit-findings 0006.
- **`type`** — Type declaration (TypeScript type, Haskell type, etc.).
- **`type_alias`** — Type alias declaration.
- **`typedef`** — C/C++ typedef declaration.
- **`union`** — Union / sum-type declaration.
- **`value`** — Value symbol (key-value DSLs). CANONICAL per audit-findings 0007.
- **`variable`** — Variable / let / mutable binding.
- **`view`** — View declaration (MVC / template languages).
- **`workflow`** — PowerShell `workflow` declaration — a named callable compiled to Windows Workflow Foundation (checkpoint / suspend-resume / parallel semantics), a distinct keyword and AST node sibling to `function` / `filter`. Kept distinct rather than folded to `function` (same rationale as `filter` / `generic`). Producer: `powershell.py` (`function_statement` child `workflow`). Registered per the WI-zipis drain / ADR-0027 verdict (audit-findings 0015).
- **`yield`** — Yield-statement symbol. CANONICAL per audit-findings 0007.

### `pending_classification` — per-cluster audit pending per ADR-0027 §"Migration"

Values deferred to per-cluster audit-findings docs at `docs/audits/<NN>-<topic>.md`. Includes the file-shape entities (Cluster B), apex/peer overloads (Cluster C), build/config-shape entities (Cluster G), and the long-tail domain vocabulary (Cluster H). Each cluster's audit decides between fold-to-Cluster-A vs separate-axis declaration vs producer-side drop.

- **`benchmark`** — Cargo `[[bench]]` target kind. Pending cluster-G audit.
- **`bin`** — Binary executable symbol. Pending cluster-B audit.
- **`binary`** — Cargo `[[bin]]` target kind. Pending cluster-G audit.
- **`example`** — Cargo `[[example]]` target kind. Pending cluster-G audit.
- **`handler`** — Ansible playbook handler. Pending cluster-G audit.
- **`helper`** — Handlebars block helper (non-builtin). Pending cluster-H audit.
- **`operation`** — Anonymous GraphQL operation fallback (graphql.py op_type default when an operation_definition has no operation_type child). Semantically an anonymous query; pending the producer fold to `query` (id-changing, deferred to v6). Registered per id-format:F3.
- **`pattern_rule`** — Make pattern-rule target. Pending cluster-G audit.
- **`private`** — WGSL `var<private>` address space. Pending cluster-H audit.
- **`storage`** — WGSL `var<storage>` address space. Pending cluster-H audit.
- **`uniform`** — Shader uniform binding (GLSL / WGSL). Pending cluster-H audit.
- **`varying`** — GLSL varying qualifier (legacy interpolation). Pending cluster-H audit.
- **`workgroup`** — WGSL `var<workgroup>` address space. Pending cluster-H audit.
- **`workspace`** — Cargo `[workspace]` table kind. Pending cluster-G audit.


## `Edge.evidence_type` axes

Per ADR-0028, `Edge.evidence_type` names the inference pathway by which
the analyzer concluded this edge exists. Properties of the dst's
resolvedness move to the new sibling field `Edge.is_resolved`;
framework-specific dispatch conventions and call-construct surface
forms move to `Edge.meta`. The audit at WI-turin-pajuk classified the
~210 distinct `Edge.evidence_type` values currently in production into
four clusters; this section lays out the three axes (Cluster A on
`inference_pathway`; Clusters B / C / D on `endpoint_shape`
deprecation; long-tail or new clusters on `pending_classification`
until per-cluster audit-findings docs ship).

### `inference_pathway` — ADR-0028 compliant

Values that name the inference pathway by which the analyzer concluded this edge exists. Per ADR-0028, this is the only axis a new `Edge.evidence_type` value should occupy. Resolution status moves to the new sibling `Edge.is_resolved`; framework-specific dispatch conventions move to `Edge.meta`.

- **`ast_annotation`** — Edge inferred from a type/decorator annotation in source AST. _(derived confidence 0.50)_
- **`ast_attribute`** — Edge inferred from an attribute access in source AST. _(derived confidence 0.95)_
- **`ast_call`** — Edge inferred from a generic call expression in source AST. _(derived confidence 0.85; 0.40 when unresolved)_
- **`ast_call_direct`** — Edge inferred from a direct (non-method) call site. _(derived confidence 0.85; 0.50 when unresolved)_
- **`ast_call_extension`** — Edge inferred from an extension-method call (Kotlin / Swift / C#). _(derived confidence 0.80)_
- **`ast_call_inherited`** — Edge inferred from a call on an inherited member.
- **`ast_call_inherited_field`** — Edge inferred from access to an inherited field.
- **`ast_call_inherited_method`** — Edge inferred from a call on an inherited method.
- **`ast_call_static`** — Edge inferred from a static (class-level) method call.
- **`ast_call_this`** — Edge inferred from a `this`/`self` receiver call.
- **`ast_call_this_property`** — Edge inferred from a `this.property` / `self.attr` resolved access.
- **`ast_call_type_inferred`** — Edge inferred from a call site where the receiver type was inferred. _(derived confidence 0.85)_
- **`ast_call_ufcs`** — Edge inferred from a UFCS free-function call written with method syntax (x.foo() resolving to foo(x); D / Nim). _(derived confidence 0.80)_
- **`ast_cite`** — Edge inferred from a citation/cross-reference link in source.
- **`ast_decorator`** — Edge inferred from a decorator/annotation node in source AST. _(derived confidence 0.95)_
- **`ast_extends`** — Edge inferred from an `extends` clause in source AST. _(derived confidence 0.95)_
- **`ast_implements`** — Edge inferred from an `implements` clause in source AST. _(derived confidence 0.95)_
- **`ast_import`** — Edge inferred from an import statement in source AST. _(derived confidence 0.95)_
- **`ast_include`** — Edge inferred from an include directive in source AST (C/C++).
- **`ast_includes`** — Edge inferred from a runtime mixin declaration (Ruby `include`/`extend`, etc.) — WI-hatip.
- **`ast_method_inferred`** — Edge inferred from a method call where dispatch was inferred. _(derived confidence 0.70)_
- **`ast_method_this`** — Edge inferred from a `this`/`self` method call.
- **`ast_method_this_property`** — Edge inferred from a `this.prop` / `self.attr` reference. _(derived confidence 0.90)_
- **`ast_method_type_inferred`** — Edge inferred from a method call with type-inferred receiver. _(derived confidence 0.85)_
- **`ast_name_read`** — Edge inferred from a bare-name read of a module-level variable (WI-jagus). _(derived confidence 0.85)_
- **`ast_new`** — Edge inferred from a `new` constructor expression. _(derived confidence 0.95)_
- **`ast_package`** — Edge inferred from a package declaration.
- **`ast_perform`** — Edge inferred from a `perform`/effect-handler invocation (OCaml/Eff).
- **`ast_ref`** — Edge inferred from a generic name reference in source AST.
- **`ast_static_call`** — Edge inferred from a static method call (qualifier-resolved).
- **`ast_type_ref`** — Edge inferred from a type reference (annotation, generic, etc.). _(derived confidence 0.85)_
- **`async_spawn`** — Edge inferred from an async spawn / task-creation construct. _(derived confidence 0.85)_
- **`behaviour`** — Edge inferred from an Erlang `-behaviour(...)` attribute. _(derived confidence 0.95)_
- **`behaviour_callback`** — Edge inferred from an Erlang behaviour callback definition. _(derived confidence 0.90)_
- **`bridging_header_import`** — Edge inferred from an Objective-C bridging-header import. _(derived confidence 0.95)_
- **`build_dependency`** — Edge inferred from a build-system dependency declaration. _(derived confidence 0.95)_
- **`build_target_main`** — Edge inferred from a build target's main entry point. _(derived confidence 0.95)_
- **`callable_reference`** — Edge inferred from a callable reference (Kotlin `::fn`, etc.).
- **`callback_argument_reference`** — Edge inferred from a callback function passed as an argument. _(derived confidence 0.75)_
- **`canonical_name`** — Edge inferred from canonical-name resolution. _(derived confidence 0.95)_
- **`cgo_call`** — Edge inferred from a Go cgo C-function call.
- **`closure_wrapper`** — Edge inferred from a closure/lambda wrapper construct. _(derived confidence 0.85)_
- **`cmake_target_link`** — Edge inferred from a CMake `target_link_libraries` call.
- **`constructor_reference`** — Edge inferred from a constructor reference (Java `::new`, etc.).
- **`designated_init_fptr`** — Edge inferred from a designated-initializer function pointer (C99).
- **`dispatch_pattern`** — Edge inferred from a generic dispatch-pattern recognition. _(derived confidence 0.70)_
- **`dispatch_table_initializer`** — Edge inferred from a dispatch-table initializer entry.
- **`dispatch_table_reference`** — Edge inferred from a reference into a dispatch table. _(derived confidence 0.85)_
- **`dockerfile_copy_from`** — Edge inferred from a Dockerfile `COPY --from=...` directive. _(derived confidence 0.95)_
- **`dockerfile_from`** — Edge inferred from a Dockerfile `FROM` directive. _(derived confidence 0.95)_
- **`enclosing_scope`** — Edge inferred from an enclosing-scope relationship. _(derived confidence 0.90)_
- **`eta_expansion`** — Edge inferred from an eta-expansion (point-free → pointed). _(derived confidence 0.85)_
- **`extends`** — Edge inferred from a generic extends/inheritance relationship. _(derived confidence 0.95)_
- **`function_pointer`** — Edge inferred from a function-pointer assignment or use. _(derived confidence 0.85)_
- **`function_pointer_arg`** — Edge inferred from a function pointer passed as an argument.
- **`function_reference`** — Edge inferred from a function reference (not a call). _(derived confidence 0.80)_
- **`function_reference_arg`** — Edge inferred from a function reference passed as an argument. _(derived confidence 0.70)_
- **`grpc_stub_resolution`** — Edge inferred from a gRPC stub-method resolution lookup. Cluster B canonical for `grpc_unresolved_resolution` (ADR-0028 §Phase 3 Cluster B / WI-nunal). _(derived confidence 0.75)_
- **`hash_field_reference`** — Edge inferred from a hash/dict field reference. _(derived confidence 0.80)_
- **`hg_annotation`** — Edge inferred from a hypergumbo-emitted analyzer annotation. _(derived confidence 0.95)_
- **`import`** — Edge inferred from a generic import construct. _(derived confidence 0.95)_
- **`import_declaration`** — Edge inferred from an import declaration node. _(derived confidence 0.95)_
- **`import_directive`** — Edge inferred from an import directive (C# `using`, etc.). _(derived confidence 0.95)_
- **`import_statement`** — Edge inferred from an import statement node. _(derived confidence 0.95)_
- **`import_static`** — Edge inferred from a Java `import static` declaration. _(derived confidence 0.95)_
- **`import_to_manifest`** — Edge inferred from a manifest-driven import resolution. _(derived confidence 0.90)_
- **`include`** — Edge inferred from a generic include construct. _(derived confidence 0.95)_
- **`include_directive`** — Edge inferred from a `#include` directive (C / C++). _(derived confidence 0.95)_
- **`instance`** — Edge inferred from a typeclass / trait instance declaration. _(derived confidence 0.90)_
- **`interface_dispatch`** — Edge inferred from interface-method dispatch resolution.
- **`jsx_element`** — Edge inferred from a JSX element reference.
- **`link`** — Edge inferred from an OTP link/monitor relationship. _(derived confidence 0.95)_
- **`luajit_ffi_lookup`** — Edge inferred from a LuaJIT FFI symbol lookup. Cluster B canonical for `luajit_ffi_unresolved` (ADR-0028 §Phase 3 Cluster B / WI-nunal).
- **`make_prerequisite`** — Edge inferred from a Make/CMake prerequisite declaration.
- **`message_send`** — Edge inferred from a message-send construct (Erlang `!`, Smalltalk). _(derived confidence 0.90)_
- **`method_reference`** — Edge inferred from a method reference (Java `::method`, etc.). _(derived confidence 0.85)_
- **`module_attribute_reference`** — Edge inferred from a module-level attribute reference. _(derived confidence 0.85)_
- **`module_export_heuristic`** — Edge inferred from a module-export heuristic recognition. _(derived confidence 0.75)_
- **`module_identifier_reference`** — Edge inferred from a module-qualified identifier reference. _(derived confidence 0.85)_
- **`module_source`** — Edge inferred from a module's source-file relationship. _(derived confidence 0.95)_
- **`naming_convention`** — Edge inferred from a language-level naming convention. _(derived confidence 0.85)_
- **`notify`** — Edge inferred from a notification/signal construct. _(derived confidence 0.90)_
- **`object_field_reference`** — Edge inferred from an object-field reference. _(derived confidence 0.80)_
- **`open`** — Edge inferred from an `open` directive (OCaml / F#). _(derived confidence 0.95)_
- **`open_import`** — Edge inferred from a Go open-import (qualified-but-unbound). _(derived confidence 0.95)_
- **`recipe_dependency`** — Edge inferred from a Bazel/Buck recipe-dependency declaration.
- **`reference`** — Edge inferred from a generic name-reference. _(derived confidence 0.95)_
- **`require`** — Edge inferred from a `require` construct (Ruby / Node). _(derived confidence 0.95)_
- **`require_alias_call`** — Edge inferred from a `require(...)` aliased to a local name.
- **`require_dynamic`** — Edge inferred from a dynamic `require(...)` call. _(derived confidence 0.40)_
- **`require_statement`** — Edge inferred from a top-level `require` statement. _(derived confidence 0.95)_
- **`require_static`** — Edge inferred from a static `require(...)` invocation. _(derived confidence 0.90)_
- **`schema_relation`** — Edge inferred from a schema-declared relation.
- **`scip_occurrence_ref`** — Edge inferred from a SCIP occurrence cross-reference.
- **`scip_relationship`** — Edge inferred from a SCIP-emitted symbol-relationship record.
- **`signal_constraint`** — Edge inferred from an HDL signal-constraint declaration. _(derived confidence 0.85)_
- **`source_statement`** — Edge inferred from a generic source-level statement. _(derived confidence 0.95)_
- **`span_overlap`** — Edge inferred from text-span overlap between symbols. _(derived confidence 0.90)_
- **`sql_foreign_key`** — Edge inferred from a SQL `FOREIGN KEY` constraint.
- **`stack_construction`** — Edge inferred from a stack-frame construction site. _(derived confidence 0.85)_
- **`static`** — Edge inferred from a static-linkage declaration. _(derived confidence 0.95)_
- **`struct_field_reference`** — Edge inferred from a struct-field reference. _(derived confidence 0.70)_
- **`subdir_include`** — Edge inferred from a subdirectory-include in a build file. _(derived confidence 0.95)_
- **`trait_impl`** — Edge inferred from a Rust `impl Trait for Type` block. _(derived confidence 0.95)_
- **`tree_sitter`** — Edge inferred from a tree-sitter query match.
- **`type_hierarchy`** — Edge inferred from a type-hierarchy traversal. _(derived confidence 0.85)_
- **`typeclass_instance`** — Edge inferred from a typeclass-instance declaration (Haskell, Scala). _(derived confidence 0.90)_
- **`use`** — Edge inferred from a `use` directive (Rust, PHP). _(derived confidence 0.95)_
- **`use-package`** — Edge inferred from a Common Lisp `use-package` form (hyphenated identifier per CL convention). _(derived confidence 0.95)_
- **`use_declaration`** — Edge inferred from a `use` declaration node. _(derived confidence 0.95)_
- **`use_directive`** — Edge inferred from a `use` directive (qualifier-bound). _(derived confidence 0.95)_
- **`using_directive`** — Edge inferred from a `using` directive (C# / C++). _(derived confidence 0.95)_
- **`variable_match`** — Edge inferred from a variable-name match across sites.
- **`verilog_instantiation`** — Edge inferred from a Verilog module instantiation.
- **`vhdl_architecture`** — Edge inferred from a VHDL architecture declaration.

### `pending_classification` — per-cluster audit pending per ADR-0028

Values deferred to per-cluster audit-findings docs at `docs/audits/<NN>-<topic>.md`. Each cluster's audit decides between fold-to-Cluster-A vs separate-axis declaration vs producer-side drop.

- **`alias_resolution`** — JS module-resolution pathway via path alias (linkers/js_module.py). Pending cluster-A audit (could promote to AXIS_INFERENCE_PATHWAY canonical).
- **`ast_call_namespace`** — JS/TS namespace-import call inference (hypergumbo-lang-mainstream/js_ts.py:3858; ``import * as obj; obj.method()``). Sibling of the canonical `ast_call_direct` / `ast_new` peers; emitted from the inline ternary ``'ast_new' if is_class else 'ast_call_namespace'``. At-risk Cluster D call-construct: fold candidate to `ast_call_direct` + `meta['call_construct']='namespace'`. Pending cluster-D audit.
- **`cffi_call`** — Python cffi FFI call (linkers/pyffi.py). At-risk Cluster C: fold candidate to canonical inference + `meta['ffi_mechanism']='cffi'`. Pending cluster-C audit.
- **`cffi_stdlib_call`** — Python cffi FFI call against the stdlib variant (linkers/pyffi.py; same dict-subscript-target leak shape as `ctypes_stdlib_call`). At-risk Cluster C peer: fold candidate to `cffi_call` + `meta['ffi_scope']='stdlib'`. Pending cluster-C audit.
- **`ctypes_call`** — Python ctypes FFI call (linkers/pyffi.py). At-risk Cluster C: fold candidate to canonical inference + `meta['ffi_mechanism']='ctypes'`. Pending cluster-C audit.
- **`ctypes_stdlib_call`** — Python ctypes FFI call against the stdlib variant (linkers/pyffi.py; `lib_vars[var_name] = "ctypes_stdlib_call"` then for-loop unpack into Edge.create). Distinguishes stdlib-loader scope from the repo-local-loaded `ctypes_call`. At-risk Cluster C peer: fold candidate to `ctypes_call` + `meta['ffi_scope']='stdlib'`. Pending cluster-C audit (see WI-nubuv tracker discussion 2026-05-06).
- **`import_resolution`** — JS module-resolution pathway via direct import (linkers/js_module.py). Pending cluster-A audit (could promote to AXIS_INFERENCE_PATHWAY canonical).
- **`ipc_channel_match`** — Electron IPC channel-name matching inference (linkers/ipc.py:546). Emitted in the canonical-`event_publishes` fold for the Electron renderer→main exchange when the publisher's channel name matches the subscriber's pattern. Sibling of `variable_match` (already canonical). At-risk Cluster A: candidate for promotion to AXIS_INFERENCE_PATHWAY or fold to `naming_convention` + `meta['detection_pattern']='ipc_channel'`. Pending cluster-A/C audit.
- **`qualified_call`** — R qualified function call via `pkg::fn` (hypergumbo-lang-common/r_lang.py:385). Sibling of the canonical `static` inference label; emitted from the inline ternary ``'static' if not path_hint else 'qualified_call'`` that the pre-WI-nubuv classifier silently skipped. At-risk Cluster D call-construct: fold candidate to `static` + `meta['call_construct']='qualified'`. Pending cluster-D audit.
- **`topic_match`** — Message-queue topic-name matching inference (linkers/message_queue.py:516). Emitted in the canonical-`event_publishes` fold for MQ publisher→subscriber via topic when the publisher's topic matches the subscriber's pattern. Sibling of `variable_match`. At-risk Cluster A: candidate for promotion or fold to `naming_convention` + `meta['detection_pattern']='mq_topic'`. Pending cluster-A/C audit.

### Derived confidence — `base_confidence` projection (ADR-0039)

`Edge.create` derives `Edge.confidence` from the edge's `evidence_type` via `derive_confidence(evidence_type, is_resolved)` — detection *reliability*, not ranking prominence (that lives on `rank_score`). Values sit in the analyzer/linker band **0.30–0.95**; 1.0 is a reserved ceiling, since no detection method is certain. See [spec §12](hypergumbo-spec.md#12-confidence-scoring) for the model and [ADR-0039](adr/0039-confidence-separation.md) for the ruling.

**80 of 125 pathways are seeded.** Each base is the edge-weighted modal confidence that pathway's producers historically hardcoded, so the migration off literal `confidence=` sites preserved the dominant cohort and collapsed per-emitter outliers onto one canonical value.

| Derived confidence | Pathways |
|---|---|
| **0.95** | `ast_attribute`, `ast_decorator`, `ast_extends`, `ast_implements`, `ast_import`, `ast_new`, `behaviour`, `bridging_header_import`, `build_dependency`, `build_target_main`, `canonical_name`, `dockerfile_copy_from`, `dockerfile_from`, `extends`, `hg_annotation`, `import`, `import_declaration`, `import_directive`, `import_statement`, `import_static`, `include`, `include_directive`, `link`, `module_source`, `open`, `open_import`, `reference`, `require`, `require_statement`, `source_statement`, `static`, `subdir_include`, `trait_impl`, `use`, `use-package`, `use_declaration`, `use_directive`, `using_directive` |
| **0.90** | `ast_method_this_property`, `behaviour_callback`, `enclosing_scope`, `import_to_manifest`, `instance`, `message_send`, `notify`, `require_static`, `span_overlap`, `typeclass_instance` |
| **0.85** | `ast_call`, `ast_call_direct`, `ast_call_type_inferred`, `ast_method_type_inferred`, `ast_name_read`, `ast_type_ref`, `async_spawn`, `closure_wrapper`, `dispatch_table_reference`, `eta_expansion`, `function_pointer`, `method_reference`, `module_attribute_reference`, `module_identifier_reference`, `naming_convention`, `signal_constraint`, `stack_construction`, `type_hierarchy` |
| **0.80** | `ast_call_extension`, `ast_call_ufcs`, `function_reference`, `hash_field_reference`, `object_field_reference` |
| **0.75** | `callback_argument_reference`, `grpc_stub_resolution`, `module_export_heuristic` |
| **0.70** | `ast_method_inferred`, `dispatch_pattern`, `function_reference_arg`, `struct_field_reference` |
| **0.50** | `ast_annotation` |
| **0.40** | `require_dynamic` |

**`is_resolved`-conditioned pathways.** Two call pathways are multimodal — a name-resolved call is more reliable than an unresolved one, so they carry a second, lower base that `derive_confidence` selects when `is_resolved=False`:

| Pathway | Resolved | Unresolved |
|---|---|---|
| `ast_call` | 0.85 | 0.40 |
| `ast_call_direct` | 0.85 | 0.50 |

**Unseeded (45).** `derive_confidence` returns `None` for these, so the producer keeps whatever literal it emits and `confidence_source` stays `emitter_constant`. That is the honest state, not a gap to paper over: an unseeded pathway has no measured modal cohort to seed from. Seeding one is a producer-side migration (the ADR-0039 ruling-1 shape), not a documentation change.

<details><summary>Unseeded pathways</summary>

`alias_resolution`, `ast_call_inherited`, `ast_call_inherited_field`, `ast_call_inherited_method`, `ast_call_namespace`, `ast_call_static`, `ast_call_this`, `ast_call_this_property`, `ast_cite`, `ast_include`, `ast_includes`, `ast_method_this`, `ast_package`, `ast_perform`, `ast_ref`, `ast_static_call`, `callable_reference`, `cffi_call`, `cffi_stdlib_call`, `cgo_call`, `cmake_target_link`, `constructor_reference`, `ctypes_call`, `ctypes_stdlib_call`, `designated_init_fptr`, `dispatch_table_initializer`, `function_pointer_arg`, `import_resolution`, `interface_dispatch`, `ipc_channel_match`, `jsx_element`, `luajit_ffi_lookup`, `make_prerequisite`, `qualified_call`, `recipe_dependency`, `require_alias_call`, `schema_relation`, `scip_occurrence_ref`, `scip_relationship`, `sql_foreign_key`, `topic_match`, `tree_sitter`, `variable_match`, `verilog_instantiation`, `vhdl_architecture`

</details>
