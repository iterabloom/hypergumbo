<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0046: Two-Axis Taint Precision — Correctness and Usefulness

- Status: **Accepted**
- Date: 2026-08-27
- Supersedes: —
- Superseded by: —
- Related: ADR-0017 (Taint-Zone Dataflow Analysis — the analysis this measures). Measurement records [0001](../measurements/0001-taint-flow-precision.md) (the rubric this amends), [0005](../measurements/0005-taint-precision-after-vocabulary-split.md), [0006](../measurements/0006-taint-precision-under-the-ratified-frame.md). Tracker items: WI-gohok (the ruling), INV-duvup (the series this unblocks), INV-nular (the defect class held separate below).

**Decision provenance.** This ADR records a **fresh human ruling**, made
2026-08-27, on a question put to the owner as three options. The owner chose
option B and accepted the stated amendment. It is not an engineering artifact
derived from prior rulings; it changes what a published precision number means.

## Context

### The rubric, and the line it deliberately did not draw

Measurement 0001 fixed the adjudication rubric and every measurement since has
reproduced it verbatim. A TRUE POSITIVE is a value that reaches a sink argument
by data flow, citable line by line. And explicitly:

> **Exploitability is not the question.**

That was a deliberate choice with a good reason: it keeps adjudication
objective. A verifier traces dataflow and cites lines; they do not argue about
whether an attacker could reach the code. Independent panels agreed 94.6% on
0006 under this rubric, which is the payoff of that objectivity.

### What 0005 surfaced

`caddy run --config -` reads its configuration from stdin. That config names a
log file. The bytes flow:

```
cmd/main.go:164  io.ReadAll(os.Stdin)
  -> caddy.Load -> changeConfig -> unsyncedDecodeAndRun
  -> StrictUnmarshalJSON            (deserialization into a declared schema)
  -> BaseLog.WriterRaw -> ctx.LoadModule(cl, "WriterRaw")
  -> FileWriter.Filename            (json:"filename")
  -> filewriter.go:239  os.Chmod(fw.Filename, configuredMode)
```

Every hop is citable in caddy's own source. Under the rubric this is a TRUE
POSITIVE, and an independent adjudication commissioned for this exact question
agreed — and accepted the consequence in terms: under this rubric **every**
filesystem, network and subprocess operation whose argument comes from a
configured value is a true positive from the config source, for any
config-driven server.

The finding is **true and uninformative at the same time**. Caddy's config *is*
a program for the filesystem; "untrusted input reached `os.Chmod`" is a correct
sentence that tells a reader nothing they did not already know. The class is
not small — it is every config field of every module in a plugin-registry
architecture.

### Why this had to be settled before a baseline series

Two facts make it gating rather than interesting.

**0006 already concedes the point in writing**: it calls 33.9% *"an upper bound
on useful precision, not a measure of it."*

**And the standing decision band is denominated in usefulness** — *"≤25% useful
⇒ recall work stays stopped"* — while the instrument computes only correctness.
The project has a program-level decision band expressed in a quantity nothing
can compute. A baseline series would faithfully track correctness precision for
months without ever answering the question the band exists to answer.

INV-duvup requires the rubric be fixed *in advance* of a comparable series, so
changing it mid-series restarts the series. Hence: before the first datum.

## Decision

**Both numbers are published. A configured-action flow remains a TRUE POSITIVE.**

1. **Correctness precision** is unchanged: `TP / adjudicated`. ADR-0001's
   "exploitability is not the question" is **not** relitigated and not weakened.
   Comparability of this figure with 0001–0006 is preserved by construction.

2. **Useful precision** is published alongside it:
   `(TP − VACUOUS) / adjudicated`, over the **same denominator** — the deduction
   is from the numerator only, so the two figures are directly comparable and
   `useful ≤ correctness` always.

3. `VACUOUS` has exactly two member classes, and a record **must report their
   counts separately** because they have opposite lifetimes:
   - **CONFIGURED-ACTION** — a permanent limit of the model (§ below).
   - **KIND-MISDECLARED** — a *defect* (INV-nular), which will disappear when
     fixed. It is deducted while it exists and must never be laundered into a
     rubric exclusion.

### The CONFIGURED-ACTION test — structural, not a severity judgement

This is the amendment the owner accepted with the ruling. Keeping adjudication
objective was the entire point of the 0001 rubric, and an "is this
interesting?" test would surrender it. A finding is labelled CONFIGURED-ACTION
only when **all three** hold, **each citable**:

1. **Declared-configuration source.** The tainted value enters the program as
   its own configuration — a config file, a config-designated CLI flag or stdin
   under one, or a documented config environment variable. Cite the read.
2. **Schema deserialization.** It passes through a deserialization call into a
   type whose fields are *declared* as a configuration schema — struct tags, a
   schema class, or an equivalent declaration. Cite the call and the type.
3. **Field-parameterized sink.** The value reaching the sink argument is read
   from a field of that deserialized object, and the sink operation is one the
   field exists to parameterize. Cite the field declaration and the sink call.

**If any of the three cannot be cited, the finding is not CONFIGURED-ACTION**
and counts as useful. The test defaults to counting a finding as useful, which
is the conservative direction: it can only *understate* the damage this class
does to the headline.

### What this does NOT license

- It does not make configured-action flows false positives. They are true.
- It does not introduce an exploitability, severity, or actionability judgement
  anywhere in adjudication. The three clauses are all citations.
- It does not excuse the INV-nular class. Filing a Haskell `IORef` under
  `untrusted-input-no-database` is a **bug**; it gets fixed, not excluded.
  Both classes yield "true and vacuous" findings and only one is a defect;
  conflating them would launder a bug through a rubric change.

## Consequences

- **The band becomes evaluable for the first time.** "≤25% useful" can be
  computed rather than bounded.
- **The number gets worse, and useful precision is lower than correctness on
  the same population. Measured (WI-gibom): 33.9% → 24.1%.** Every point of
  that fall is KIND-MISDECLARED; **CONFIGURED-ACTION contributed zero to
  0006's population**, structurally rather than by luck — 0006's claim set
  admits only `env_read` and `host_info_read` sources, so clause 2 (schema
  deserialization) has no candidate to match. The class is real and is
  concentrated in config-file-driven servers like caddy, which 0006 did not
  sample. **A cohort meant to exercise this class must contain one.**
- **Adjudication cost rises** by one labelled bit per TP, gated behind three
  citations. Only TPs need the label; FPs and UNADJUDICABLEs are unaffected.
- **Two numbers can move in opposite directions.** Fixing INV-nular raises
  useful precision without touching correctness. A record must therefore state
  which class moved.

### A provenance gap this ADR does not paper over

The measurements index publishes 0006's useful precision as **≤25.0%**. That
figure is **not derivable from the record**. 0006's body names exactly **five**
vacuous TPs (all of shellcheck's, Haskell in-process refs filed under
`untrusted-input-no-database`), while ≤25.0% of a 38-TP-of-112 population
requires **ten** removals. The other five are not identified anywhere in the
record.

The figure may well be right — section A's table lists kind-misdeclarations in
five languages, so more than shellcheck's five TPs plausibly qualify — but
`docs/measurements/README.md` states that what belongs in a record is "the
evidence a reader would need to disbelieve the number", and that evidence is
absent for the one figure a program-level decision band is written against.

**Therefore: ≤25.0% must be re-derived under this ADR's two named classes, or
restated, before it anchors the baseline series.** It is not carried forward on
authority. Tracked separately; naming it here rather than asserting it away is
the point, because a band anchored to an underived number is the shape of
defect this ADR exists to correct.

**CLOSED by WI-gibom.** Re-derived at **24.1%** (27/112): 11 KIND-MISDECLARED,
0 CONFIGURED-ACTION. The derivation, the eleven citations, and the one standing
sensitivity (21.4%) are in
[0006 § "Useful precision, re-derived"](../measurements/0006-taint-precision-under-the-ratified-frame.md).
The ≤25.0% held as a bound and failed as a derivation — its stated basis
supports 29.5% — and the index now carries the derived figure instead.

Two of that pass's three declared sensitivities were subsequently **withdrawn**:
`argv` and application config under `env_read` were published as awaiting a
vocabulary ruling that **already existed**, in the `env_read` definition
INV-tutar's resolution added to the spec — *"an ambient CONFIGURATION read
(environment variables, system properties, argv — values that may carry a
credential)"*. The residue is not a vocabulary question but the gap between a
boundary meaning *may carry a credential* and a label saying `host_secret`,
which is the gap **this ADR exists to measure rather than close**. Deducting
those rows would be doing by catalogue membership what the decision above does
by measurement.
