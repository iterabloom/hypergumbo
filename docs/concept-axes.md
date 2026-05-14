<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- AUTO-GENERATED — do not edit manually.
     Regenerate with: ./scripts/generate-concept-axes
     Sources of truth:
       packages/hypergumbo-core/src/hypergumbo_core/edge_types.py
       packages/hypergumbo-core/src/hypergumbo_core/symbol_kinds.py
       packages/hypergumbo-core/src/hypergumbo_core/evidence_types.py -->

# Concept Axes

Typing axes maintained in hypergumbo's behavior map. Each axis names a
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
- **`depends_on`** — Generic dependency relationship.
- **`depends_on_manifest`** — Dependency declared in a package or build manifest.
- **`dispatches_to`** — Caller dispatches to callee via runtime indirection (virtual method, function pointer, DI resolution, etc.).
- **`event_publishes`** — Producer publishes an event/message that the consumer receives via an async channel (event bus, queue, CRDT, etc.).
- **`extends`** — Class extends a superclass.
- **`implements`** — Class implements an interface.
- **`imports`** — Module imports another module or symbol.
- **`includes`** — File includes / sources / mixes-in another file's content (LaTeX \include, RST .. include::, Meson subdir, etc.).
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

- **`abi_call`** — Solidity contract ABI call (cross-contract method invocation); likely fold to 'calls' + meta['protocol']='abi'.
- **`association`** — Ruby ActiveRecord association declaration (has_many, belongs_to, etc.); likely fold to 'references' + meta['construct']='association'.
- **`base_image`** — Dockerfile ``FROM`` base image reference.
- **`build_tag_alternative_of`** — Go build-tag-conditional alternative implementation of a symbol; likely fold to 'references' + meta['construct']='build_tag_alternative'.
- **`caller_invokes`** — Tauri-style cross-language invoke (caller → bound command); likely fold to 'calls' + meta['protocol']='ipc' (parallel to ipc_calls per audit-findings 0002).
- **`contains_routes`** — Controller / module containing route handlers; likely fold to 'contains' (already canonical) — pure dst-kind leakage.
- **`crypto_flow`** — Crypto-related dataflow (key/secret reaches sink); likely fold to 'data_flows_to' + meta['construct']='crypto'.
- **`depends`** — Package depends on another (Bitbake, requirements.txt); likely fold to 'depends_on' (already canonical) or 'depends_on_manifest' depending on declaration site.
- **`extends_template`** — Twig/Jinja template extends a parent template; likely fold to 'extends' + meta['construct']='template' or stay as canonical if templates' extension semantics differ enough.
- **`graphql_calls`** — GraphQL call (use 'calls' + protocol meta).
- **`grpc_calls`** — gRPC call (use 'calls' + protocol meta).
- **`http_calls`** — HTTP call (use 'calls' + protocol meta).
- **`includes_class`** — Puppet manifest includes a class declaration; likely fold to 'includes' (now canonical) + meta['construct']='puppet_class'.
- **`includes_template`** — Twig/Jinja template includes a partial; likely fold to 'includes' (now canonical) + meta['construct']='template'.
- **`invokes_callback`** — Erlang/Elixir/Ruby callback invocation (gen_server callback, framework lifecycle hook); likely fold to 'dispatches_to' or 'calls' + meta['mechanism']='callback'.
- **`kernel_launch`** — GPU kernel invocation.
- **`links_to`** — Markdown link from one document to another; likely fold to 'references' + meta['construct']='markdown_link'.
- **`notifies_resource`** — Puppet/Chef resource notify directive (trigger another resource on change); likely fold to 'event_publishes' + meta['channel_kind']='puppet_notify' (configuration-management pub-sub shape).
- **`renders`** — Controller renders a view template; likely fold to 'references' + meta['construct']='view_render' (parallel to renders_component for JSX).
- **`requires_resource`** — Puppet/Chef resource require directive (this resource depends on another); likely fold to 'depends_on' + meta['construct']='puppet_require'.
- **`script_src`** — HTML ``<script src=...>`` reference.
- **`signal_receiver`** — Django signal receiver registration; likely fold to 'event_publishes' + meta['channel_kind']='django_signal' (signals are pub-sub via Django's dispatch module).
- **`template_calls`** — Vue / template-engine method call from template into component logic; likely fold to 'calls' + meta['mechanism']='template'.
- **`uses_mixin`** — Sass/SCSS @include of a mixin; likely fold to 'references' + meta['construct']='sass_mixin'.
- **`uses_vocabulary`** — SPARQL/RDF query references a vocabulary/ontology; likely fold to 'references' + meta['construct']='rdf_vocabulary'.

### `pending_classification` — per-family audit pending per ADR-0023 §5

Values deferred to per-family audit. Some may be genuinely distinct relationships; others are protocol-conditional duplicates of a more general relationship. Verdicts arrive with each family's audit.

- **`implements_rpc`** — RPC implementation binding — pending per-family audit.
- **`openapi_implements`** — OpenAPI handler pattern — pending per-family audit.
- **`resolver_for_type`** — GraphQL resolver-type binding — pending per-family audit.
- **`resolver_implements`** — GraphQL resolver pattern — pending per-family audit.


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
- **`external_symbol`** — IR-pipeline boundary pseudo-symbol — emitted by ``create_boundary_nodes`` (``ir.py:959``) for every edge endpoint that doesn't resolve to a real Symbol (stdlib calls, npm imports, third-party constructors). CANONICAL per audit-findings 0007 §"Diagnostic findings #3" (Wave 6 PR 6 reclassification): structurally a top-level construct in the IR pipeline's own DSL, parallel to other Cluster H domain-DSL constructs (``playbook``, ``participant``, …). Consumers query boundary status via ``is_external_boundary(sym)`` (meta-key based), so this kind is a label not a discriminator — promotion does not change consumer behavior.
- **`field`** — Field declaration on a struct / class / record.
- **`file`** — File-shape symbol — top-level file declaration in build / source DSLs. CANONICAL per audit-findings 0005.
- **`font_face`** — CSS @font-face symbol. CANONICAL per audit-findings 0007.
- **`for_loop`** — For-loop symbol (control-flow). CANONICAL per audit-findings 0007.
- **`fragment`** — Fragment symbol (GraphQL / template). CANONICAL per audit-findings 0007.
- **`function`** — Top-level function definition.
- **`getter`** — Property getter accessor.
- **`id`** — Id symbol (k8s / DSL). CANONICAL per audit-findings 0007.
- **`id_selector`** — CSS id selector symbol. CANONICAL per audit-findings 0007.
- **`import`** — Top-level wasm-bindgen FFI import declaration. Reclassified DEPRECATE-NO-FOLD → CANONICAL on 2026-05-07 by the indirection-aware re-audit: the original Cluster E sub-case (b) drop verdict in audit-findings 0010 was correct for the css.py / jsonnet.py / astro.py / r_lang.py producers it inventoried, but Wave 6 PR 3 (wasm_import → kind="import" + meta["compilation_target"]="wasm") added wasm_bindgen.py:266 as a new producer for a different purpose — a synthetic boundary node the slicer BFS needs for continuity. The wasm-bindgen `import` is a real top-level construct in its source DSL, not a relabel of the imports Edge.
- **`index`** — Index symbol (SQL / DSL). CANONICAL per audit-findings 0007.
- **`inductive`** — Lean ``inductive`` type declaration (``lean.py:247``). CANONICAL per audit-findings 0007 (reclassified Wave 6 PR 4 — the original DEPRECATE-NO-FOLD verdict was a literal-grep blind-spot miss; ``lean.py`` emits via ``add_symbol(..., 'inductive')`` indirection).
- **`input`** — Input symbol (Terraform / shader). CANONICAL per audit-findings 0007.
- **`instance`** — Typeclass / interface instance declaration.
- **`interface`** — Interface declaration.
- **`keyframes`** — CSS @keyframes symbol. CANONICAL per audit-findings 0007.
- **`keyword`** — Keyword-shaped construct (configuration languages).
- **`label`** — Label symbol (assembly / k8s). CANONICAL per audit-findings 0007.
- **`library`** — Library declaration (CMake `add_library`, Meson `library`, Cargo `[lib]`, etc.). CANONICAL per audit-findings 0005.
- **`link`** — Link symbol (markdown / yaml-anchor). CANONICAL per audit-findings 0007.
- **`local`** — Local symbol (Terraform local). CANONICAL per audit-findings 0007.
- **`macro`** — Macro definition (Rust / C / Scheme).
- **`media`** — CSS @media symbol. CANONICAL per audit-findings 0007.
- **`message`** — Protobuf ``message`` declaration (``proto.py:260``). CANONICAL per audit-findings 0007 (reclassified Wave 6 PR 4 — the original DEPRECATE-NO-FOLD verdict was a literal-grep blind-spot miss; ``proto.py`` emits via ``_make_proto_symbol(..., 'message', ...)`` indirection).
- **`method`** — Method on a class / struct / interface.
- **`mixin`** — Mixin declaration (Ruby / Sass).
- **`module`** — Module declaration (the source-level construct).
- **`namespace`** — Namespace declaration (C++ / TypeScript / C#).
- **`node`** — Node symbol (k8s / DSL). CANONICAL per audit-findings 0007.
- **`object`** — Object / singleton declaration (Scala / Kotlin).
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
- **`yield`** — Yield-statement symbol. CANONICAL per audit-findings 0007.

### `pending_classification` — per-cluster audit pending per ADR-0027 §"Migration"

Values deferred to per-cluster audit-findings docs at `docs/audits/<NN>-<topic>.md`. Includes the file-shape entities (Cluster B), apex/peer overloads (Cluster C), build/config-shape entities (Cluster G), and the long-tail domain vocabulary (Cluster H). Each cluster's audit decides between fold-to-Cluster-A vs separate-axis declaration vs producer-side drop.

- **`benchmark`** — Cargo `[[bench]]` target kind. Pending cluster-G audit.
- **`bin`** — Binary executable symbol. Pending cluster-B audit.
- **`binary`** — Cargo `[[bin]]` target kind. Pending cluster-G audit.
- **`example`** — Cargo `[[example]]` target kind. Pending cluster-G audit.
- **`handler`** — Ansible playbook handler. Pending cluster-G audit.
- **`helper`** — Handlebars block helper (non-builtin). Pending cluster-H audit.
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

- **`ast_annotation`** — Edge inferred from a type/decorator annotation in source AST.
- **`ast_attribute`** — Edge inferred from an attribute access in source AST.
- **`ast_call`** — Edge inferred from a generic call expression in source AST.
- **`ast_call_direct`** — Edge inferred from a direct (non-method) call site.
- **`ast_call_extension`** — Edge inferred from an extension-method call (Kotlin / Swift / C#).
- **`ast_call_inherited`** — Edge inferred from a call on an inherited member.
- **`ast_call_inherited_field`** — Edge inferred from access to an inherited field.
- **`ast_call_inherited_method`** — Edge inferred from a call on an inherited method.
- **`ast_call_static`** — Edge inferred from a static (class-level) method call.
- **`ast_call_this`** — Edge inferred from a `this`/`self` receiver call.
- **`ast_call_this_property`** — Edge inferred from a `this.property` / `self.attr` resolved access.
- **`ast_call_type_inferred`** — Edge inferred from a call site where the receiver type was inferred.
- **`ast_cite`** — Edge inferred from a citation/cross-reference link in source.
- **`ast_decorator`** — Edge inferred from a decorator/annotation node in source AST.
- **`ast_extends`** — Edge inferred from an `extends` clause in source AST.
- **`ast_implements`** — Edge inferred from an `implements` clause in source AST.
- **`ast_import`** — Edge inferred from an import statement in source AST.
- **`ast_include`** — Edge inferred from an include directive in source AST (C/C++).
- **`ast_method_inferred`** — Edge inferred from a method call where dispatch was inferred.
- **`ast_method_this`** — Edge inferred from a `this`/`self` method call.
- **`ast_method_this_property`** — Edge inferred from a `this.prop` / `self.attr` reference.
- **`ast_method_type_inferred`** — Edge inferred from a method call with type-inferred receiver.
- **`ast_new`** — Edge inferred from a `new` constructor expression.
- **`ast_package`** — Edge inferred from a package declaration.
- **`ast_perform`** — Edge inferred from a `perform`/effect-handler invocation (OCaml/Eff).
- **`ast_ref`** — Edge inferred from a generic name reference in source AST.
- **`ast_static_call`** — Edge inferred from a static method call (qualifier-resolved).
- **`ast_type_ref`** — Edge inferred from a type reference (annotation, generic, etc.).
- **`async_spawn`** — Edge inferred from an async spawn / task-creation construct.
- **`behaviour`** — Edge inferred from an Erlang `-behaviour(...)` attribute.
- **`behaviour_callback`** — Edge inferred from an Erlang behaviour callback definition.
- **`bridging_header_import`** — Edge inferred from an Objective-C bridging-header import.
- **`build_dependency`** — Edge inferred from a build-system dependency declaration.
- **`build_target_main`** — Edge inferred from a build target's main entry point.
- **`callable_reference`** — Edge inferred from a callable reference (Kotlin `::fn`, etc.).
- **`callback_argument_reference`** — Edge inferred from a callback function passed as an argument.
- **`canonical_name`** — Edge inferred from canonical-name resolution.
- **`cgo_call`** — Edge inferred from a Go cgo C-function call.
- **`closure_wrapper`** — Edge inferred from a closure/lambda wrapper construct.
- **`cmake_target_link`** — Edge inferred from a CMake `target_link_libraries` call.
- **`constructor_reference`** — Edge inferred from a constructor reference (Java `::new`, etc.).
- **`designated_init_fptr`** — Edge inferred from a designated-initializer function pointer (C99).
- **`dispatch_pattern`** — Edge inferred from a generic dispatch-pattern recognition.
- **`dispatch_table_initializer`** — Edge inferred from a dispatch-table initializer entry.
- **`dispatch_table_reference`** — Edge inferred from a reference into a dispatch table.
- **`dockerfile_copy_from`** — Edge inferred from a Dockerfile `COPY --from=...` directive.
- **`dockerfile_from`** — Edge inferred from a Dockerfile `FROM` directive.
- **`enclosing_scope`** — Edge inferred from an enclosing-scope relationship.
- **`eta_expansion`** — Edge inferred from an eta-expansion (point-free → pointed).
- **`extends`** — Edge inferred from a generic extends/inheritance relationship.
- **`function_pointer`** — Edge inferred from a function-pointer assignment or use.
- **`function_pointer_arg`** — Edge inferred from a function pointer passed as an argument.
- **`function_reference`** — Edge inferred from a function reference (not a call).
- **`function_reference_arg`** — Edge inferred from a function reference passed as an argument.
- **`grpc_stub_resolution`** — Edge inferred from a gRPC stub-method resolution lookup. Cluster B canonical for `grpc_unresolved_resolution` (ADR-0028 §Phase 3 Cluster B / WI-nunal).
- **`hash_field_reference`** — Edge inferred from a hash/dict field reference.
- **`hg_annotation`** — Edge inferred from a hypergumbo-emitted analyzer annotation.
- **`import`** — Edge inferred from a generic import construct.
- **`import_declaration`** — Edge inferred from an import declaration node.
- **`import_directive`** — Edge inferred from an import directive (C# `using`, etc.).
- **`import_statement`** — Edge inferred from an import statement node.
- **`import_static`** — Edge inferred from a Java `import static` declaration.
- **`import_to_manifest`** — Edge inferred from a manifest-driven import resolution.
- **`include`** — Edge inferred from a generic include construct.
- **`include_directive`** — Edge inferred from a `#include` directive (C / C++).
- **`instance`** — Edge inferred from a typeclass / trait instance declaration.
- **`interface_dispatch`** — Edge inferred from interface-method dispatch resolution.
- **`jsx_element`** — Edge inferred from a JSX element reference.
- **`link`** — Edge inferred from an OTP link/monitor relationship.
- **`luajit_ffi_lookup`** — Edge inferred from a LuaJIT FFI symbol lookup. Cluster B canonical for `luajit_ffi_unresolved` (ADR-0028 §Phase 3 Cluster B / WI-nunal).
- **`make_prerequisite`** — Edge inferred from a Make/CMake prerequisite declaration.
- **`message_send`** — Edge inferred from a message-send construct (Erlang `!`, Smalltalk).
- **`method_reference`** — Edge inferred from a method reference (Java `::method`, etc.).
- **`module_attribute_reference`** — Edge inferred from a module-level attribute reference.
- **`module_export_heuristic`** — Edge inferred from a module-export heuristic recognition.
- **`module_identifier_reference`** — Edge inferred from a module-qualified identifier reference.
- **`module_source`** — Edge inferred from a module's source-file relationship.
- **`naming_convention`** — Edge inferred from a language-level naming convention.
- **`notify`** — Edge inferred from a notification/signal construct.
- **`object_field_reference`** — Edge inferred from an object-field reference.
- **`open`** — Edge inferred from an `open` directive (OCaml / F#).
- **`open_import`** — Edge inferred from a Go open-import (qualified-but-unbound).
- **`recipe_dependency`** — Edge inferred from a Bazel/Buck recipe-dependency declaration.
- **`reference`** — Edge inferred from a generic name-reference.
- **`require`** — Edge inferred from a `require` construct (Ruby / Node).
- **`require_alias_call`** — Edge inferred from a `require(...)` aliased to a local name.
- **`require_dynamic`** — Edge inferred from a dynamic `require(...)` call.
- **`require_statement`** — Edge inferred from a top-level `require` statement.
- **`require_static`** — Edge inferred from a static `require(...)` invocation.
- **`schema_relation`** — Edge inferred from a schema-declared relation.
- **`scip_occurrence_ref`** — Edge inferred from a SCIP occurrence cross-reference.
- **`scip_relationship`** — Edge inferred from a SCIP-emitted symbol-relationship record.
- **`signal_constraint`** — Edge inferred from an HDL signal-constraint declaration.
- **`source_statement`** — Edge inferred from a generic source-level statement.
- **`span_overlap`** — Edge inferred from text-span overlap between symbols.
- **`sql_foreign_key`** — Edge inferred from a SQL `FOREIGN KEY` constraint.
- **`stack_construction`** — Edge inferred from a stack-frame construction site.
- **`static`** — Edge inferred from a static-linkage declaration.
- **`struct_field_reference`** — Edge inferred from a struct-field reference.
- **`subdir_include`** — Edge inferred from a subdirectory-include in a build file.
- **`trait_impl`** — Edge inferred from a Rust `impl Trait for Type` block.
- **`tree_sitter`** — Edge inferred from a tree-sitter query match.
- **`type_hierarchy`** — Edge inferred from a type-hierarchy traversal.
- **`typeclass_instance`** — Edge inferred from a typeclass-instance declaration (Haskell, Scala).
- **`use`** — Edge inferred from a `use` directive (Rust, PHP).
- **`use-package`** — Edge inferred from a Common Lisp `use-package` form (hyphenated identifier per CL convention).
- **`use_declaration`** — Edge inferred from a `use` declaration node.
- **`use_directive`** — Edge inferred from a `use` directive (qualifier-bound).
- **`using_directive`** — Edge inferred from a `using` directive (C# / C++).
- **`variable_match`** — Edge inferred from a variable-name match across sites.
- **`verilog_instantiation`** — Edge inferred from a Verilog module instantiation.
- **`vhdl_architecture`** — Edge inferred from a VHDL architecture declaration.

### `pending_classification` — per-cluster audit pending per ADR-0028

Values deferred to per-cluster audit-findings docs at `docs/audits/<NN>-<topic>.md`. Each cluster's audit decides between fold-to-Cluster-A vs separate-axis declaration vs producer-side drop.

- **`alias_resolution`** — JS module-resolution pathway via path alias (linkers/js_module.py). Pending cluster-A audit (could promote to AXIS_INFERENCE_PATHWAY canonical).
- **`ast_call_method`** — Python AST method-call inference (py.py). At-risk Cluster D peer of `ast_call_direct`: fold candidate to `ast_call_direct` + `meta['call_construct']='method'`. Pending cluster-D audit.
- **`ast_call_namespace`** — JS/TS namespace-import call inference (hypergumbo-lang-mainstream/js_ts.py:3858; ``import * as obj; obj.method()``). Sibling of the canonical `ast_call_direct` / `ast_new` peers; emitted from the inline ternary ``'ast_new' if is_class else 'ast_call_namespace'``. At-risk Cluster D call-construct: fold candidate to `ast_call_direct` + `meta['call_construct']='namespace'`. Pending cluster-D audit.
- **`cffi_call`** — Python cffi FFI call (linkers/pyffi.py). At-risk Cluster C: fold candidate to canonical inference + `meta['ffi_mechanism']='cffi'`. Pending cluster-C audit.
- **`cffi_stdlib_call`** — Python cffi FFI call against the stdlib variant (linkers/pyffi.py; same dict-subscript-target leak shape as `ctypes_stdlib_call`). At-risk Cluster C peer: fold candidate to `cffi_call` + `meta['ffi_scope']='stdlib'`. Pending cluster-C audit.
- **`ctypes_call`** — Python ctypes FFI call (linkers/pyffi.py). At-risk Cluster C: fold candidate to canonical inference + `meta['ffi_mechanism']='ctypes'`. Pending cluster-C audit.
- **`ctypes_stdlib_call`** — Python ctypes FFI call against the stdlib variant (linkers/pyffi.py; `lib_vars[var_name] = "ctypes_stdlib_call"` then for-loop unpack into Edge.create). Distinguishes stdlib-loader scope from the repo-local-loaded `ctypes_call`. At-risk Cluster C peer: fold candidate to `ctypes_call` + `meta['ffi_scope']='stdlib'`. Pending cluster-C audit (see WI-nubuv tracker discussion 2026-05-06).
- **`import_resolution`** — JS module-resolution pathway via direct import (linkers/js_module.py). Pending cluster-A audit (could promote to AXIS_INFERENCE_PATHWAY canonical).
- **`ipc_channel_match`** — Electron IPC channel-name matching inference (linkers/ipc.py:546). Emitted in the canonical-`event_publishes` fold for the Electron renderer→main exchange when the publisher's channel name matches the subscriber's pattern. Sibling of `variable_match` (already canonical). At-risk Cluster A: candidate for promotion to AXIS_INFERENCE_PATHWAY or fold to `naming_convention` + `meta['detection_pattern']='ipc_channel'`. Pending cluster-A/C audit.
- **`qualified_call`** — R qualified function call via `pkg::fn` (hypergumbo-lang-common/r_lang.py:385). Sibling of the canonical `static` inference label; emitted from the inline ternary ``'static' if not path_hint else 'qualified_call'`` that the pre-WI-nubuv classifier silently skipped. At-risk Cluster D call-construct: fold candidate to `static` + `meta['call_construct']='qualified'`. Pending cluster-D audit.
- **`topic_match`** — Message-queue topic-name matching inference (linkers/message_queue.py:516). Emitted in the canonical-`event_publishes` fold for MQ publisher→subscriber via topic when the publisher's topic matches the subscriber's pattern. Sibling of `variable_match`. At-risk Cluster A: candidate for promotion or fold to `naming_convention` + `meta['detection_pattern']='mq_topic'`. Pending cluster-A/C audit.
