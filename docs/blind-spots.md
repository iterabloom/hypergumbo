<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Blind spots — question-shapes empirically missed in this codebase

This is a catalog of *question-shapes* — meta-patterns of questions
that recurrently go unasked and produce buried-for-years bugs when
they finally get asked. The Fundamental Concept Audit playbook is the
diagnostic tool ("given a one-sentence suspicion, here's how to
investigate"); this doc is the generative complement ("here are the
shapes of suspicion worth forming when nothing is obviously wrong").

The cadence hook
(`.agent/hooks/_shared/check_audit_cadence.py`) tells you *when* it's
time to audit. This doc tells you *what to look at* when picking a
suspect domain.

## How to use this doc

- **Skim it at the natural inflection points** — post-incident
  retrospective, pre-refactor frame-locking, domain-shift onboarding.
  A list of bake-ins lives in the playbooks under
  `.agent/agent_playbooks_protocols_sops_skills/` (each prompts a
  skim of this file at the relevant moment).
- **Pick one shape that resonates** with the work in front of you and
  ask the question for real — don't just read the entry, run the
  question against the code.
- **5–10 minutes is the budget**, not an hour. The point is to
  notice, not to investigate exhaustively. If a shape genuinely fires,
  promote to the Fundamental Concept Audit playbook for the deep dive.

## How to add an entry

Add a shape when a retrospective, incident, or audit reveals an
unasked question worth keeping in the catalog. Each entry has three
parts:

- **Shape** — one-sentence statement of the meta-pattern. The
  question, not the answer. Phrase it so that it can be asked of
  domains we haven't audited yet.
- **Example** — a past instance where the shape actually fired.
  Reference the PR / commit / ADR / tracker item so future readers can
  trace the empirical grounding.
- **Trigger** — the situation that should remind you of this shape.
  Use plain language; the goal is recognition, not a checklist.

Prune entries when a shape is consistently caught upstream by
automation (the existence of a property test or pre-commit lint that
fires on the shape means the shape doesn't need carrying around in
human attention any more).

---

## Shape 1: Did we check the typing axis, not just the values?

**Shape** — When a multi-value field accumulates a long list of
values, check whether all the values name the *same kind of thing*.
Different kinds of things masquerading as values along a single
"type" axis is the classic conceptual leak (ADR-0023's case).

**Example** — `Edge.edge_type` accreted relationship-shaped values
(`calls`, `imports`, `extends`), endpoint-shape values (`imports_module`,
`napi_bridge`), and dispatch-mechanism values (`routes_to`,
`message_dispatch`) under one field. The leak existed for years before
ADR-0023 named it. PRs #3459 / #3462 / #3463 / #3468 plus ADR-0023 /
ADR-0024 / ADR-0025 / ADR-0026 plus WI-vomoj-suhaz Phase 4b removed
the leak.

**Trigger** — Whenever you're about to add a new value to a
multi-value enum field. Whenever the third or fourth
`<existing_thing>_<framework_qualifier>` variant gets proposed. The
"yet another flavor" feeling is the signal.

---

## Shape 2: What did we assume the pipeline never sees?

**Shape** — Implicit input-domain assumptions that erode silently as
the surface broadens. The first language analyzer assumed Python; the
first framework assumed Flask; the first repo type assumed monorepo
layout. Each later addition narrows the original assumption a notch
without anyone updating the boundary. The eventual failure surfaces
far from the assumption.

**Example** — The `_detect_src_layout` function in the Python
analyzer assumed a single `src/<module>/` shape; monorepo
`packages/<pkg>/src/<mod>/` layouts were silently misclassified. WI-davan
catalogued the symptoms; the structural fix landed only after the
underlying assumption was named.

**Trigger** — Whenever you're adding a new analyzer / framework /
repo type. Whenever a code path branches on "if it looks like X, do
Y" without an explicit fallback for "doesn't look like X." Whenever a
configuration file's *absence* is treated as a default rather than a
question.

---

## Shape 3: What failure mode are we silently relying on not happening?

**Shape** — Graceful-degradation paths that turn into load-bearing
assumptions. "If the parser fails, return empty" sounds defensive but
becomes a silent correctness regression once the failure rate
exceeds zero. The bug isn't in the degradation — it's in the absence
of a metric that fires when the degradation rate spikes.

**Example** — Tree-sitter analyzer fallback paths that returned
empty symbol lists on parse failure produced silently-empty behavior
maps when grammars drifted. The cohort-level diagnostic ("repo X went
from 5000 nodes to 10") surfaced the regression weeks after it landed.
Documented under WI-vavur-tonid and the tree-sitter test playbook.

**Trigger** — Whenever you're about to write a `try / except: pass`
or a `return []` on a failure path. Whenever you find yourself
thinking "this code-path almost never fires." Whenever you ship a
fallback without a counter that fires when the fallback rate exceeds
some threshold.

---

## Shape 4: What did we let the absence of evidence stand in for?

**Shape** — Null results read as confirmation. When a search returns
zero hits, that can mean (a) no instances exist, OR (b) the search is
wrong and instances are everywhere unrecognized. Without a positive
control — a known instance the search must hit — null results are
ambiguous.

**Example** — The pre-Phase-4b consumer-side enumeration audit for
ADR-0023 §6 grep'd for deprecated `edge_type` values in production
code; the absence of hits was initially read as "Phase 3 migration is
complete," but the broader-scoped grep (post WI-zisit-hagud) caught
phantom values in `bakeoff-deep`'s `_FFI_EDGE_TYPES` (`jni_bridge`,
`pyffi_bridge`) that no analyzer ever emitted. The narrow grep's
"clean" result was misleading.

**Trigger** — Whenever you grep for "the bad pattern" and find none.
Whenever a bakeoff cohort produces zero `no_move` verdicts. Whenever
"we didn't find anything" is about to become the conclusion. Ask:
"would I find a known instance if I planted one as a positive
control?" If you wouldn't, the search isn't evidence.

---

## Related infrastructure

- **Diagnostic mode** — `.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md`. Run when you have a one-sentence suspicion.
- **Cadence trigger** — `.agent/hooks/_shared/check_audit_cadence.py`. Fires every ~72 development commits.
- **Static drift linter** — `scripts/check-edge-type-drift` (with `--strict` mode for axis-principle enforcement). Catches Shape 1 regressions on `Edge.edge_type` specifically.
- **Runtime coherence** — `scripts/check-edge-type-runtime-coherence`. Partitions emitted edges by `(src.kind, src.language, dst.kind, dst.language)` to surface producer-side endpoint-shape leakage.
- **Bake-in playbooks** — see `agentic-session-retrospective.md` (post-incident) and `structural-fix-scope-expansion-protocol.md` (pre-refactor) for the trigger moments where this doc gets skimmed.
