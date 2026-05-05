<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0003: Symbol.kind Cluster A — Canonical Language Constructs

- Date: 2026-05-05
- Status: All rows RESOLVED
- Closes: WI-kozis-fabik-tuhug-rogup-lobar-zamij-zurid-vakij (Cluster A audit-findings: canonical language constructs, ADR-0027 Phase 3)
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the audit-findings format defined in [`docs/audits/README.md`](README.md). First audit-findings doc on the `Symbol.kind` axis declared by [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md).

## Context

[ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) declares `Symbol.kind` as the source-language syntactic construct axis, and clusters the 192 emitted-in-production values into eight groups. Cluster A is the canonical seed: ~50 values that each name a language-level construct (function, class, method, struct, interface, enum, module, variable, property, constructor, namespace, attribute, field, trait, type, type_alias, alias, macro, fn, var, proc, etc.).

ADR-0027 §"Detailed analysis: per-cluster fold targets" assigns Cluster A the canonical verdict at axis declaration time. This audit-findings document records the per-value verdicts so the Cluster A baseline is enumerated under the audit-findings format — the same as ADR-0023's Cluster A members are enumerated in this directory's other docs.

This is the lowest-risk Cluster A doc on the roadmap: no producer migration is required, and every row carries the same shape (CANONICAL on the `language_construct` axis, RESOLVED at filing time because Phase 1 already shipped the registry with these values on the canonical axis).

## Methodology

The CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy and the four-leakage-test diagnostic procedure are defined in [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). This document applies that methodology to the Cluster A subset of `Symbol.kind` values.

The `diagnostic_test` field for each row is a one-line Python invocation that asserts the value is present in the live `SYMBOL_KINDS` registry on the `language_construct` axis. The `expect: exit_code:0` shape lets a future audit runner execute the test and assert success. The structural shape is what `packages/hypergumbo-core/tests/test_audit_findings.py` validates today.

## Verdicts

```yaml
kind: audit_verdicts
axis: Symbol.kind
verdicts:
  - value: function
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"function\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Top-level function definition."
  - value: method
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"method\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Method on a class / struct / interface."
  - value: class
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"class\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Class declaration."
  - value: interface
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"interface\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Interface declaration."
  - value: struct
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"struct\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Struct / record-type declaration."
  - value: enum
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"enum\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Enum declaration."
  - value: union
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"union\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Union / sum-type declaration."
  - value: trait
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"trait\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Trait declaration (Rust / Scala / Groovy)."
  - value: module
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"module\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Module declaration (the source-level construct)."
  - value: namespace
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"namespace\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Namespace declaration (C++ / TypeScript / C#)."
  - value: variable
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"variable\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Variable / let / mutable binding."
  - value: constant
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"constant\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Constant / final / let-immutable binding."
  - value: const
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"const\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Const declaration (C / C++ / Rust / JS const)."
  - value: property
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"property\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Property declaration (Kotlin / Swift / C#)."
  - value: attribute
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"attribute\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Attribute declaration (Python class attribute, etc.)."
  - value: field
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"field\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Field declaration on a struct / class / record."
  - value: constructor
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"constructor\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Constructor / __init__ / init method."
  - value: getter
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"getter\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Property getter accessor."
  - value: setter
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"setter\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Property setter accessor."
  - value: type
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"type\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Type declaration (TypeScript type, Haskell type, etc.)."
  - value: type_alias
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"type_alias\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Type alias declaration."
  - value: typedef
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"typedef\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: C/C++ typedef declaration."
  - value: alias
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"alias\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Generic alias declaration."
  - value: simple_type
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"simple_type\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Simple-type declaration (XSD-shape)."
  - value: defined_type
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"defined_type\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Defined / nominal type declaration (Puppet / Coq)."
  - value: macro
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"macro\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Macro definition (Rust / C / Scheme)."
  - value: mixin
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"mixin\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Mixin declaration (Ruby / Sass)."
  - value: record
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"record\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Record declaration (Java 14+, Erlang, Haskell)."
  - value: abstract
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"abstract\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Abstract class / member declaration."
  - value: instance
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"instance\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Typeclass / interface instance declaration."
  - value: subroutine
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"subroutine\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Subroutine / sub declaration (Fortran / Perl)."
  - value: procedure
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"procedure\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Procedure declaration (Pascal / Ada / SQL)."
  # `proc` / `fn` / `var` were seeded here as Cluster A canonical at 0003's
  # filing time because the registry placed them on `language_construct`.
  # WI-rusit (audit-findings 0009) reclassifies them as Cluster C apex/peer
  # overloads (peers of `procedure` / `function` / `variable`); the rows live
  # in 0009 with FOLD verdicts at PRELIM_RESOLVED. They no longer appear here.
  - value: arrow_function
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"arrow_function\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Arrow-function expression (JS / TS)."
  - value: object
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"object\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Object / singleton declaration (Scala / Kotlin)."
  - value: prop
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"prop\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Component prop declaration (Vue / React)."
  - value: slot
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"slot\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Component slot declaration (Vue / Svelte)."
  - value: template
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"template\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Template declaration (C++ / Vue / Handlebars)."
  - value: directive
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"directive\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Directive declaration (Vue / Angular / GraphQL)."
  - value: declaration
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"declaration\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Generic declaration (catch-all for non-categorized syntactic forms)."
  - value: keyword
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"keyword\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Keyword-shaped construct (configuration languages)."
  - value: export
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"export\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Export declaration (JS / TS / TOML / Rust)."
  - value: import
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"import\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Import declaration as a syntactic-form symbol."
  - value: include
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"include\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Include declaration (Ruby include, C #include, Make include)."
  - value: inherit
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"inherit\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Inherit clause as a syntactic form (BitBake, OOP DSLs)."
  - value: extends
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"extends\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Extends clause as a syntactic form (Java, Solidity)."
  - value: component
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"component\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: Component declaration (Vue / Svelte / Astro / React)."
  - value: view
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"view\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Cluster A language construct: View declaration (MVC / template languages)."
```

## Related

- [ADR-0027: Symbol.kind Names the Source-Language Syntactic Construct](../adr/0027-symbol-kind-language-construct-only.md) — the originating axis declaration. Cluster A is the canonical seed defined in §"Detailed analysis: per-cluster fold targets".
- [ADR-0024: Axis Declaration Template](../adr/0024-axis-declaration-template.md) — §"Family-audit verdict methodology" defines the CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy applied here.
- [`docs/audits/README.md`](README.md) — format spec.
- [Audit-findings 0001](0001-dispatch-publish-family.md) and [0002](0002-ipc-family.md) — sibling audits on the `Edge.edge_type` axis. Same methodology, different axis.
- WI-runod (cross-axis Phase 3 sequencing schedule) — this document is Wave 1 in the schedule.
