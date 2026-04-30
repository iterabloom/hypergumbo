<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# What If We Don't Know What The Fuck We Are Talking About? Audit (aka Fundamental Concept Audit)

A procedure for systematically checking whether a fundamental concept in
the codebase is internally confused — i.e., whether a single field, type,
category, or vocabulary is silently smuggling unrelated information
through the same name. Run this when you suspect, but cannot yet prove,
that the codebase has a structural conceptual leak.

The discipline this audit teaches: distinguish "we are saying different
things with the same word" from "we are saying the same thing with
different words." Both are confusions; they need different fixes.

## When to run

Six signals, any of which is sufficient:

1. **You just identified ONE conceptual leak.** Most leaks are not
   alone. The cognitive habits and pattern-matching that produced one
   conflation produced others. Audit the surrounding territory before
   the same agent (you, me, anyone) ships another.
2. **A new component reveals "yet another flavor" of an existing thing.**
   When the third or fourth `<existing_thing>_<framework_qualifier>`
   variant ships, the typing axis is probably wrong.
3. **Consumers maintain hardcoded sets that keep going out of date.**
   A "staff picks" page hardcoded with
   `FEATURED_GENRES = {"fantasy", "mystery", "thriller"}` that misses
   `young_adult_fantasy` and so silently excludes that whole subsection
   is the canonical shape. If a set has had a patch in the last
   quarter to add a new variant, the set probably should have been a
   query against properties instead.
4. **Reviewing for a refactor reveals N similar-but-different
   somethings where you expected 1.** That gap between expected and
   observed cardinality is data; investigate before refactoring on
   top of it.
5. **You're about to ship a new "thing" that feels like it's relabeling
   existing stuff.** Stop and audit. Shipping the relabel cements the
   confusion at one greater scale.
6. **The cadence hook fired.** A session-start check at
   `.agent/hooks/_shared/check_audit_cadence.py` emits a "time for the
   audit" reminder when more than the configured threshold of
   development commits have accumulated since the last recorded audit.
   The hook is a calibration nudge, not a directive — when it fires,
   ask the user whether to run an audit now and which suspect domain
   to pick (or which to defer to the next cycle). See "Cadence
   mechanism" below.

## The audit, step by step

### Step 1 — Name the suspect domain

Write down, in plain English, what you think the confusion is. Examples:

- "I think the `genre` column is encoding properties that already
  live on the `audience` column."
- "I think `book_type` mixes literary form (novel, novella, short
  story) with literary mode (tragedy, comedy, satire)."
- "I think `series_name` and `imprint` overlap somewhere."
- "I think `subtitle` and `series_volume` have an undocumented
  boundary."

If you can't write the suspicion in one sentence, the audit isn't ready;
go look at examples until you can.

### Step 2 — Inventory the values

Get every value the suspect field/category takes across the entire
codebase:

```sql
-- When the suspect field is stored in a database — say, `genre` on
-- a books catalog:
SELECT genre, COUNT(*) AS n
FROM   books
GROUP  BY genre
ORDER  BY n DESC;
```

```
# When the suspect field is a literal value in source code — say, a
# `category=` argument passed at constructor sites — extract every
# distinct value across the codebase, count by frequency, and sort.
# The exact shell or query incantation depends on your stack. The
# output is the goal: a frequency-sorted list of every value the
# field has ever taken.
```

The output is the **suspect taxonomy**. Read the full list end-to-end
— the pattern is in the long tail.

### Step 3 — Apply the four leakage tests

For each pair of suspect-confused values (A, B), ask the four
questions below. The tests are not orthogonal — in particular,
Tests 1 and 4 can both fire on the same pair with different
verdicts. Test 1 asks whether A and B should be collapsed; Test 4
asks whether either belongs in this field at all. A pair can pass
one and fail the other.

The four tests address axis-correctness — whether each value
*belongs* in the field. They do not address enumeration-completeness:
a clean axis can still have schema-vs-runtime drift in either
direction (missing values that runtime emits, phantom values that
runtime never emits — see Step 4). Pair the tests with a value-set
property test where the suspect domain is itself a value-set in code.

1. **Property derivability.** Is the distinction between A and B
   derivable from properties of the things they connect or describe?
   - `young_adult_fantasy` vs `fantasy` — derivable from the book's
     `audience` column. Leakage.
   - `paperback` vs `audiobook` — not derivable from the book's
     audience or topic; these describe genuinely different artifacts
     of the same work. Not leakage. (Though see Test 4 for a different
     confusion these two might share.)

2. **Apex/peer overloading.** Is one of A/B used in some places as
   "the generic top type" and in other places as "a specific subtype"?
   This test is the most diagnostically powerful of the four — a hit
   here alone is sufficient grounds to deprecate, since the same value
   playing two roles is a structural break, not an interpretive
   judgment.
   - `fiction` used by the catalog import script as the top of the
     genre tree (everything is either fiction or non-fiction) AND as
     a peer of `literary_fiction` and `genre_fiction` in the
     recommendation engine. The same string is both apex and peer.
     Definitional confusion.

3. **Construct vs. relationship.** Is one a syntactic-construct label
   and the other a semantic-relationship label?
   - `novel` (form: long-form prose) vs `tragedy` (mode: thematic
     shape). When the same `genre` field accepts both as values, you
     have axis confusion — they answer different questions about the
     work and shouldn't compete for the same slot.

4. **Mechanism vs. category.** Is the distinction "how it's done" vs.
   "what it is"? Mechanism almost always belongs in metadata, not in
   the type.
   - `audiobook` vs `ebook` vs `hardcover` mixed into the same
     `book_type` field — these are delivery formats, not what the
     work *is*. Should be a separate `format` field on each edition,
     with one `book_type` shared across editions of the same work.
     Leakage.
   - `library.lookup_by_title()` and `library.lookup_from_cache()` as
     peer methods — the first names the query parameter (a category);
     the second names the storage mechanism (a how). Different shape
     of suspect — method names rather than column values — but the
     same axis confusion. Leakage.

A pair that passes all four tests is genuinely distinct and should
stay. A pair that fails any test is a confusion candidate.

### Step 4 — Find the silent bugs

Once you've identified leakage, find the consumers that have been
papering over it. Symptoms:

- **Hardcoded sets enumerating variants.** Grep for the suspect
  variants together: any place that lists three of them but not the
  fourth is a silent miscategorization.
- **Long if/elif chains over the suspect field.** These are queries-
  in-disguise; each branch is asking a question that should have been
  answered from the endpoint properties.
- **Reconciliation code with TODOs.** "We need a downstream
  specialization pass to handle these uniformly" — that pass usually
  doesn't exist; the comment is the bug.
- **Phantom values.** A set that contains values nothing actually
  emits. Two shapes: (a) the value belongs to a *different field* on
  the same type and was smuggled into a membership test against the
  wrong field (e.g., an ``evidence_type`` placed in a set checked
  against ``edge_type``); (b) the value once existed but was renamed,
  and the set didn't follow. Both fail silently because the membership
  test never fires the way the author expected. This is the mirror
  image of the "missing values" shape above.

Each silent bug becomes a concrete file:line reference for the audit
write-up. Without these, the audit is just opinion.

**Pair manual audit with automated detection where possible.** When
the suspect domain is itself a value-set in code, an AST-walk
property test asserting "every named ``*_TYPES``-shaped set is a
subset of the canonical registry" catches shapes the manual sweep
misses — almost always more than the manual count. The first run
surfaces existing offenders; subsequent runs prevent regression at
CI time. The 2026-04-29 ``Edge.edge_type`` audit illustrates this:
the manual sweep found 2 silent bugs, the property test added
afterwards surfaced 3 more, one of which exposed the phantom-value
shape.

### Step 5 — Adjacent concept sweep

Once you've found one confusion, expand outward. The same cognitive
habit that produced it produced others. For each suspect domain, name
the adjacent fields and audit them too:

- Suspect `genre` → also audit `subject`, `audience`, `format`,
  `series_name`. The fields that share a logical column with the
  suspect almost always share its design history.
- Suspect `audience` → also audit `reading_level`, `age_range`,
  `content_rating`, `language`. Each could be a hidden third
  dimension being smuggled through `audience`.
- Suspect `format` → also audit `medium`, `binding`,
  `physical_dimensions`, `delivery_channel`. Format-adjacent fields
  are a common site for "is this physical, virtual, or both?"
  confusion.

Adjacent fields don't have to all be confused — but they almost always
share design history with the confused one, so they're the cheapest
places to find the next instance. When a sweep surfaces a field that
warrants a full axis declaration (rather than a single-pair verdict),
ADR-0024 specifies the four-part declaration template (axis name,
axiom, consumer pattern, enforcement) and the seven-step workflow for
landing the artifacts.

### Step 6 — Decide: deprecate, document, or keep

Apply this decision per-pair — each suspect pair from Step 3 gets its
own verdict, and a single audit can produce a mix of all three
outcomes below:

- **Deprecate** — the distinction is leakage; open an ADR with a typing
  principle and a migration plan. ADR-0023 is the worked example;
  ADR-0024 is the abstract template (axis name, axiom, consumer
  pattern, enforcement) for declaring the new axis the deprecation
  reorganizes around.
- **Document** — the distinction is genuine but undocumented. The
  artifact ranges from a short ADR addendum or module docstring (when
  the suspect domain is conceptual) up to a structured registry module
  with per-value metadata and a property test enforcing the boundary
  going forward (when the suspect domain is a value-set in code — see
  ADR-0024 for the template, ADR-0023 / `edge_types.py` for the worked
  example). Either way, the artifact must state WHY the variants are
  distinct and what query each is meant to answer.
- **Keep without action** — explicitly accept the apparent overlap
  as "not actually a problem at the scale we care about". This
  outcome is fine but must be **written down with rationale and a
  re-evaluation trigger**: why the overlap is tolerable now, and
  what specific threshold of harm (volume of misclassifications, a
  new consumer arriving, a metric crossing a line) would force the
  audit to be re-run. A bare note saying "we decided this was fine"
  decays into folklore in two quarters; a written trigger gives the
  next auditor concrete grounds to re-open.

### Step 7 — Record findings

Write up the audit. Whether or not the suspicion was confirmed. Format
suggestion (use whatever survives in your repo):

```
## Audit: <one-line description of suspect>

Date: <YYYY-MM-DD>
Trigger: <why this audit was run>
Outcome: confirmed | partially confirmed | rejected

Findings:
- <each candidate confusion>: <verdict>, file:line evidence

Adjacent audited:
- <adjacent field>: <verdict>

Action:
- ADR-XXXX (if deprecate) | docstring update (if document) | none (if keep)
```

A null result is still a result. Writing "looked at <X>, no leak"
prevents the next agent from redoing the same investigation.

## Anti-patterns

- **Doing the audit in your head and not writing anything down.** The
  whole point is making the structure visible to future readers. A
  silent audit is no audit.
- **Treating the audit as code review.** Code review asks "is this
  line correct?" Conceptual audit asks "is this CATEGORY correct?".
  Different question, different mode.
- **Coupling the audit to a deadline.** Mid-feature, mid-release-prep,
  mid-incident — any moment when you're racing toward a deliverable —
  you will rationalize away the smell to keep moving. Audit triggers
  should couple to *slack*, not to deadlines. The cadence hook below
  is calendar-orthogonal precisely so it's more likely to fire on a
  normal day when there's bandwidth to act on whatever it surfaces.
  Mid-feature is one slack-anti-correlation; release prep is another;
  triage during an active incident is a third. None of them are good
  audit moments.
- **N=1 audits.** "I found one example; the field is fine." One
  example is a fluke. Run the inventory step. The pattern is in the
  long tail, not the head.
- **Auditing without naming a hypothesis first.** Without a written
  one-sentence suspicion (Step 1), the audit becomes a fishing trip
  and produces noise rather than signal.
- **Refusing to consider that the audit might find nothing.** A
  rejection is just as valuable as a confirmation; either way you've
  shrunk the future search space. Do not bias toward finding a problem
  just because you started looking.

## Self-test before stopping

Before you stop the audit, confirm:

- [ ] Did I write down the one-sentence suspicion?
- [ ] Was my hypothesis falsifiable — could the inventory and tests
      have come back showing no leakage? If "no" or "I'm not sure",
      the audit was confirmation theater.
- [ ] Did I inventory all values of the suspect field?
- [ ] Did I apply each of the four leakage tests to candidate pairs?
- [ ] Did I find at least one silent bug or near-bug — or write a
      sentence stating "no silent bugs found, here's why I'm
      confident"?
- [ ] Did I sweep at least two adjacent concepts?
- [ ] Did I record findings somewhere durable (ADR, lab notebook,
      tracker discussion thread)?
- [ ] If the outcome was "keep", did I write down explicitly why?

If any unchecked, the audit is not done.

## Examples (running list)

When this playbook is used, append the audit's outcome here so future
runs can find prior work:

- **2026-04-29 — `Edge.edge_type`.** Confirmed structural leak: ~80
  edge types, four leakage families (imports, references, FFI bridges,
  publish/dispatch). Manual audit identified 2 silent bugs
  (`ranking.py:1053`, `slice.py:640`); the property test added in
  PR #3459 surfaced 3 more (`taint.py:736`, `compact.py:108`,
  `io_boundary.py:704`), one of which exposed the phantom-value bug
  shape — a value from a different field smuggled into the membership
  check. Outcome: ADR-0023 (Draft); canonical registry +
  drift property test landed in PR #3459. Follow-on: axis-validation
  linter (PR (b)), by-axis view (PR (c)).

(Future audits append here.)

## Relationship to other playbooks

- **Structural Fix and Scope Expansion Protocol** — used when fixing a
  *single* identified violation across multiple language/construct/
  stage instances. This audit is run *upstream* of that protocol: it
  produces the violations the structural fix then handles.
- **Self-Analysis Dogfooding Playbook** — running hypergumbo on itself
  is a frequent trigger for this audit (the long tail of edge types or
  symbol kinds becomes visible in the self-analysis output).
- **Agentic Session Retrospective** — retrospectives sometimes surface
  "we kept reaching for a variant of X". When that observation appears
  in two retros, the trigger condition for this audit has been met.

## When NOT to run this audit

- **Active incident.** If something is broken in production-equivalent
  state (CI down, bakeoff stuck, agent looping), fix the incident
  first. The audit is upstream-of-future-bugs, not response-to-current-
  bugs.
- **Anytime you're under a deadline** — mid-feature, release prep,
  active incident. Audit produces real follow-on work (ADRs,
  migrations). Don't start something with a multi-week tail while
  you're racing toward a deliverable. Audit triggers couple to slack,
  not to deadlines.
- **You don't have a one-sentence suspicion.** "Just looking around"
  produces noise. Wait until you can name the suspected confusion;
  then the audit has a signal to chase.

## Cadence mechanism

Even when no specific signal triggers, conceptual leaks accumulate
silently over development. The audit being purely reactive — "wait
until you notice" — is itself a failure mode (the original failure
mode that produced ADR-0023). The repo ships a session-start cadence
check at `.agent/hooks/_shared/check_audit_cadence.py` that:

1. Reads `.agent/.last_concept_audit.json` for the SHA + timestamp of
   the most recent recorded audit.
2. Counts development commits between that SHA and `HEAD`, excluding
   tracker auto-syncs.
3. If the count exceeds the threshold (default 72, configurable under
   `concept_audit.commit_threshold` in `.agent/tracker/config.yaml`),
   prints a soft reminder. The session-start hook injects the reminder
   into the agent's context.
4. Softens the message to "defer until clean tree" when the working
   tree has uncommitted changes — running an audit while mid-feature
   is one face of the deadline-coupling anti-pattern above.

Threshold derivation (calibrate to your repo if it differs):

- Median ADR-to-ADR cadence on this repo: ≈ 4.5 calendar days.
- Audit-cadence target: ≈ 2× ADR cadence (≈ 3 calendar days).
- Empirical commits/calendar-day on this repo: ≈ 22.
- 3 days × 22 commits/day ≈ 66, rounded up to **72** for a small
  margin.

Tune the knob upward if the prompt fatigues; tune downward if
conceptual leaks slip through unaudited.

When you complete an audit, run::

    scripts/concept-audit-record <suspect-domain>

This updates the state file with the current `HEAD` SHA, the local-
zone timestamp, and the suspect name. Append the outcome prose to
the Examples section above by hand — each entry is a unique
narrative of what was found, classified, deprecated, kept, etc.,
and that prose belongs to the human who ran the audit, not to the
bookkeeping script.

The cadence mechanism is complementary to the static drift detection
described in Step 4: the property test catches drift in *known*
value-sets ("did the canonical list and consumer sets diverge?"); the
cadence hook catches *new* conceptual leaks we haven't named yet
("when did we last look across the codebase for this class of bug?").
Both belong; neither replaces the other.
