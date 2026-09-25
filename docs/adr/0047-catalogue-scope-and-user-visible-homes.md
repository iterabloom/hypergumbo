<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0047: Catalogue Scope, and Where a User's Catalogue Data Lives

- Status: **Accepted**
- Date: 2026-08-27
- Supersedes: —
- Superseded by: —
- Related: ADR-0016 (I/O Boundary Analysis — §27 catalogue scope and §35 project-local overlays, both amended here), ADR-0045 (User Configuration and Backend Trust — the two config tiers and the `io_primitives` setting this builds on), ADR-0017 (Taint-Zone Dataflow — the `taint_*` catalogues), ADR-3aaa (the `frameworks/` catalogue). Tracker items: WI-sutuk (the project-wide filing), WI-bahid (the Go case and its measurement), INV-safig, INV-fotav (where the standing ruling was given), INV-zosun (verdict-level catalogue disclosure), INV-lufib (per-language overlay scoping), WI-vafit (the user-facing inventory).

**Decision provenance.** This ADR records a **fresh human ruling** given
2026-08-27, and it **changes a standing owner ruling** rather than restating
one. The prior ruling — *"io_primitives stays STDLIB-SCOPED (ADR-0016 §27);
third-party is USER-SUPPLIED via overlays"* — was given 2026-08-11 on INV-safig
and reaffirmed in person on INV-fotav. It is not reversed lightly and §Context
below records what it was protecting.

The question put to the owner was: *"Can hypergumbo ship third-party rows it
doesn't vouch for, loaded by default? That reverses your stdlib-only ruling."*
The answer was **"yes as long as it's loud about it"**, and the conditional is
load-bearing rather than decorative — it is why ruling 6 requires the
disclosure in DEFAULT HUMAN OUTPUT and not only in a JSON field. Ruling 9's two
audiences were separated by the owner in the same exchange. Rulings 2–5, 7, 8
and 10 are engineering consequences of those two answers.

## Context

### The standing ruling, and that it was enforced once at higher cost

ADR-0016 scopes the shipped catalogues to *"a curated list of stdlib
functions, not an unbounded set of library APIs"*, bounded by *"what hypergumbo
can be responsible for"*, and sends third-party rows to *"a project-local
overlay, not more built-in rows"*.

This is not aspirational. Commit `864f55ed02` (2026-04-24, "strict-stdlib cull
cross-language") culled six languages and added structural tests, on the
reasoning that *"once you allow popular wrappers, the maintenance treadmill is
unbounded — every language has dozens of HTTP clients, ORMs, logging façades."*
`tokio::fs` / `tokio::net` went out at a **91.7% name-for-name mirror rate**
against still-shipping `std::` rows — a *higher* overlap than any row now under
discussion. The "everyone uses this spelling" argument was heard and rejected.

### Practice diverged, in the open, and the mechanism is visible

| catalogue | third-party rows | share |
|---|---|---|
| elixir | 155 | 29.4% |
| haskell | 68 | 31.5% |
| swift | 38 | 36.5% |
| go | 33 | 17.4% |
| python | 4 ungoverned + 29 `django.db.models` | — |

Three catalogue headers **declare** third-party scope deliberately — swift names
"SwiftNIO, AsyncHTTPClient, NIOSSL"; haskell says "and common libraries"; elixir
goes furthest, naming "a galaxy of HTTP clients (HTTPoison, Tesla, Req, Finch,
Mint)" and justifying it with a UAT bug report: **a real Phoenix/Ecto repository
returned ZERO boundaries.**

Openly-declared drift is still drift, not a competing policy. But the mechanism
is worth naming: **scope gates exist for 6 of 14 languages**, and the eight with
none (go, elixir, erlang, haskell, swift, objc, c, cpp) are exactly where the
rows accumulated. Java's `javax.*` / `jakarta.*` allowance is *not* a carve-out
and must not be cited as precedent — the test defines Java's stdlib as "the JDK
(`java.*`) plus the historically-bundled `javax.*` and the standardized
`jakarta.*`", which is a statement about the platform, not an exception to it.

### The overlays that do exist ship to nobody

`docs/io-primitives-overlays/` holds three overlays. `pyproject.toml` declares
`packages = ["src/hypergumbo_core"]`, so **`docs/` is not in the wheel**: an
installed hypergumbo cannot load them at all. The extension point ADR-0016 §35
granted has, in practice, no artifact a user can reach.

### The wider shape: 89% of the catalogue data has no user channel

`hypergumbo_core.yaml_catalogs` registers **8 catalogue families, 156 YAML
files**, with a drift check already gating the registry against the tree.

| family | files | user channel today |
|---|---|---|
| `frameworks` | 107 | **none** |
| `dataflow_patterns` | 20 | **none** |
| `io_primitives` | 15 | `--io-primitives`, ADR-0045 config |
| `cfg_nodes` | 5 | **none** |
| `function_summaries` | 4 | **none** |
| `taint_sources` / `taint_sanitizers` | 3 | `--taint-*`, `extra_catalogs:` |
| `url_folding` | 2 | **none** (and no governing ADR) |

**138 of 156 files — 89% — cannot be extended by a user at all**, and the
largest family is `frameworks/`, which is precisely what someone with an
in-house framework needs to reach.

### What already exists and should be reused

- **ADR-0045 landed two config tiers** — `$XDG_CONFIG_HOME/hypergumbo/config.toml`
  and `<repo>/.hypergumbo.toml` — with **`io_primitives` overlay paths as its
  first real setting**. The "user points at overlays" half is built.
- **`load_catalog(language, overlay_paths=...)` is already a delta layer**, in
  ascending precedence, and deliberately drops `stdlib_modules` from an overlay
  so a `requests` overlay cannot relabel a PyPI package as stdlib.
- **Verdict-level catalogue disclosure exists** (INV-zosun): the `verify-claims`
  envelope carries `catalog_provenance.layers.io_primitives.{claims_file, cli}`
  and a `user_supplied` flag, built because *"a 5-line project-local overlay
  re-opens INV-buzab's false confirm."*
- **Per-language overlay scoping is a solved hazard** (INV-lufib): a claims-file
  overlay was once applied to every language, hard-failing any repo that also
  contained JavaScript.

## Decision

**1. Scope is about what hypergumbo VOUCHES for, not about what it ships.**
The shipped *catalogues* stay stdlib-only — ADR-0016 §27 is unchanged for them.
hypergumbo may additionally **ship community overlays it does not vouch for**,
which are loaded by default and disclosed as unvouched. The third-party rows
now in five catalogues move into those overlays, `django.db.models` included.
This is the amendment: the previous ruling said third-party is *user-supplied*;
it is now *shipped, unvouched, and disclosed*. The maintenance burden ADR-0016
declined is not eliminated by this — it is **bounded by disclosure instead of by
exclusion**, and §Consequences says so plainly rather than claiming otherwise.

**2. Seed, never copy.** Base catalogues live in the wheel and are **never
materialized** into a user directory. Only *deltas* live in the user's config
dir. A full copy means the next release's rows never reach that user, silently,
and the tool quietly degrades for exactly the people who engaged with it enough
to run the command. Deltas do not have that failure mode, and the loader already
speaks them.

**3. A user's catalogue data lives under `$XDG_CONFIG_HOME/hypergumbo/`**, per
family, with the repo tier at `<repo>/.hypergumbo/`:

```
$XDG_CONFIG_HOME/hypergumbo/
  config.toml          # ADR-0045
  io_primitives.d/     # per-family overlay directory
  taint_sources.d/
  frameworks.d/
  README.md
```

Nobody edits files inside their site-packages. A catalogue family that is
declared user-extensible must have a home a user can find without being told
where Python installed the wheel.

**4. Materialization is an explicit subcommand, never an implicit first-run
write.** ADR-0045's own precedent is a *human-owned* config file the tool may
read but not write; silently creating files in someone's config directory on
first invocation is the surprise that precedent exists to avoid. Default-on
loading does **not** require materialization — the shipped overlays load from
the wheel; the subcommand exists so a user can *edit* them.

**5. An unvouched row declares its provenance, and staleness is checkable.**
Every shipped-but-unvouched overlay carries `provenance: community`, a dated
`retrieved:`, and — once materialized — `seeded_from:` naming the hypergumbo
version it was copied from. "hypergumbo does not maintain these rows" is then a
fact a reader can check rather than a sentence in a header.

**6. Disclosure has three states, not two — and it is LOUD.** The owner's
"yes" to ruling 1 was conditioned on this, so it is a requirement and not a
nicety.

`catalog_provenance` gains layer keys for `shipped_default` and `user_config`.
The existing boolean `user_supplied` cannot express *"hypergumbo shipped it and
does not vouch for it"*, which is the state this ADR creates; conflating it
with either neighbour would re-open exactly the INV-zosun gap.

**A JSON field alone does not satisfy this.** A reader who never opens the
envelope must still learn that unvouched rows were loaded, so a run that loads
them emits a one-line stderr notice naming the overlays and their `retrieved:`
dates — the shape already used for an `in_progress` catalogue, which warns per
queried language on every run. Silence is the failure mode this whole ADR
exists to remove: the tool asserting stdlib-only while shipping 300
third-party rows was silent, and that is what made it wrong rather than merely
generous.

**7. The registry answers extensibility.** `CatalogSpec` gains the fields
naming whether a family is user-extensible and where the user's file goes, so
`scripts/yaml-catalog-index --check` — already a gate — refuses a family that
declares a channel it does not have. A new catalogue family cannot land without
someone answering the question.

**8. Every language gets a scope gate.** The eight ungated languages get the
same gate shape as the six that have one. The asymmetry is the mechanical cause
of the drift and leaving it in place guarantees a repeat.

**9. The repo tier does not load by default, and hypergumbo never writes into
an analysed repository.** A repository that ships an overlay silencing its own
boundaries is the shape INV-zosun was filed about, and it arrives on a machine
whose owner never opted into it — so the repo tier stays opt-in per invocation
or per user config, not automatic.

**Two audiences, two mechanisms — and they are not the same offer.** Collapsing
them was the error this ruling corrects.

**The normal user has `$XDG_CONFIG_HOME/hypergumbo/`, and that is a
SUBCOMMAND, not an offer.** They run the ruling-4 command; it creates their
config home and populates it with the community overlays. Nothing is proposed
to them unprompted, and the contents are not "examples" — they are the user's
actual working configuration, some of which happens to be community-sourced
third-party rows.

**The hypergumbo developer has `~/hypergumbo`, and THAT is where the offer
belongs.** A literal `hypergumbo` directory in the home directory is a
repository checkout; nobody creates one by accident, which makes its presence a
deliberate signal in a way that a config directory is not. When hypergumbo sees
it, it may **offer once** to place *examples of repo-tier overlay files* there
— the material a developer needs to see what a `.hypergumbo/` in an analysed
repository would contain, without one being written into any repository.

Three constraints keep the offer an offer:

- **Nothing is ever written into an analysed repository.** Writing a file into
  someone's working tree is how a tool gets its output committed by accident,
  and a repo-tier overlay is exactly the file whose presence should be a
  deliberate act by that repository's owner.
- **A decline is recorded as a decision**, reusing ADR-0045 ruling 8 verbatim:
  *"The store records declines as well as grants… The nudge goes quiet for any
  path with a recorded decision."* An offer that cannot be answered permanently
  is a nag, and the project has already written down why that is corrosive:
  *"A nudge that fires when it is already moot trains people to skim past the
  one sentence that must land."*
- **It is never raised in a non-interactive context.** No prompt when stdin is
  not a TTY — the shape `cli.py` already applies to progress output. An offer
  that blocks a CI run or an agent invocation is a defect, not a courtesy.

**10. A family gets a user channel when it describes the USER'S world, not the
LANGUAGE'S.** That is the whole test, and it decides all eight by inspection:

| family | describes | channel |
|---|---|---|
| `io_primitives` | libraries and their I/O | **yes** (exists) |
| `taint_sources` / `taint_sanitizers` | the user's trust model | **yes** (exists) |
| `frameworks` | conventions, including in-house ones | **yes** (new) |
| `function_summaries` | dependency behaviour | **yes, gated** (see below) |
| `dataflow_patterns` → `library_patterns` | library and idiom mutation | **yes** (section-scoped) |
| `dataflow_patterns` → grammar rules | tree-sitter node types | no |
| `cfg_nodes` | tree-sitter node types | no |
| `url_folding` | wiring to shipped engines | no |

**`cfg_nodes` is internal beyond argument.** Its rows are grammar node types and
field names (`if_statement`, `field:condition`) against a named grammar version.
A user cannot know better than the grammar, and a wrong row silently breaks the
CFG, which silently breaks the taint walk.

**`url_folding` is internal for a mechanical reason:** its rows name
`engine: fold_array_join`, a Python function inside `url_folding/__init__.py`.
A user-supplied file could only reference engines the package already contains,
so the channel would be inert without also accepting user code. **This also
answers OQ2** — it has no governing ADR because it is a dispatch table wiring
languages to shipped engines, not a decision surface. That is worth documenting;
it is not an ADR-shaped gap.

**`dataflow_patterns` is MIXED, and that is the finding.** It is not one kind of
data. The grammar section is `node_type: assignment / write: left` — internal by
the same reasoning as `cfg_nodes`. But **9 of its 20 files carry a
`library_patterns` section** whose rows are regex matches over call syntax
(`'\.append\('` → `access_mode: mutate`), matched *"by method name regardless
of receiver type"*. That is a statement about libraries and idioms, and a user
with an in-house collection type has a legitimate row to add. **The channel is
scoped to that section**, not to the file: a family can be half-internal, and
granting the whole file would hand a user the grammar rules as well.

**`function_summaries` gets the channel it most obviously needs and the gate it
least obviously needs.** Its entries describe callees *"whose source is not
analyzed"* — dependencies, which is exactly where a user knows something
hypergumbo cannot. But its own header states the risk: *"A wrong `terminates`
verdict lets the walk close a branch that is really open, which deletes a real
security finding; a wrong `propagates` verdict only leaves an unknown unknown."*
A user-supplied terminating summary **is** a sanitizer declaration by another
name — it removes a flow the tool would otherwise report — and today
`verify_claims` discloses nothing about function summaries at all, while
`CAVEAT_USER_SUPPLIED_SANITIZER` already exists for the structurally identical
case. So: a user summary that TERMINATES a branch rides the existing
`user_supplied_sanitizer` caveat and its exit-3 contract. A propagating one does
not, because it cannot silence anything. Granting this channel without that gate
would re-open INV-buzab's shape on a fresh surface.

## Consequences

### Positive

- The ~15 user-facing strings asserting stdlib-only become **true again**, which
  no other option achieves: today they describe a state the catalogues do not
  satisfy.
- Recall is preserved for the Phoenix/Ecto and SwiftNIO cases that motivated the
  drift, without the tool claiming to vouch for those rows.
- A user can finally see and edit what their installation knows.
- The 89% no-channel finding gets a gate, so it stops being invisible.

### Negative

- **hypergumbo still ships and must maintain ~300 third-party rows.** This ADR
  changes who is *responsible* for their correctness, not who carries the files.
  The unbounded-treadmill objection in ADR-0016 and in commit `864f55ed02` is
  answered only partially, and a future ADR may have to revisit it if the
  overlay set grows the way that commit predicted.
- A materialized overlay can go stale against its shipped source; ruling 5
  makes that visible but does not prevent it.
- Default-on loading means a default run's results now depend on files the
  project does not vouch for — mitigated by ruling 6, not eliminated.

### Neutral

- The `HIGH_RISK_PRIMITIVES` drift guard (WI-sugav / WI-gitad) must widen its
  corpus from built-in catalogues to built-ins **plus shipped default
  overlays**. Its intent — no entry that resolves to nothing anywhere — is
  preserved exactly, and this incidentally removes the one genuine cost WI-bahid
  identified for culling Go's `execabs` rows.

## Alternatives Considered

**A1 — Enforce the standing ruling strictly: cull, ship nothing.** The literal
reading, and the cheapest to state. Rejected because the Phoenix/Ecto UAT
already showed what it produces: a real application, analysed, reporting zero
boundaries. Correct by the letter and useless to the person running it.

**A2 — Materialize full copies of the base catalogues.** The most obvious
reading of "put it where the user can edit it". Rejected: it guarantees that
upgrades never reach the users who engaged most. See ruling 2.

**A3 — Materialize implicitly on first run.** Better ergonomics than a
subcommand, and rejected for ADR-0045's reason: `$XDG_CONFIG_HOME` is the
user's, and a tool that writes there uninvited has made a decision that was not
its to make.

**A4 — Widen the built-in catalogues officially and drop the stdlib claim.**
Honest about what ships today, and the option this ADR is closest to. Rejected
because it discards the distinction that makes the disclosure meaningful: with
no vouched/unvouched line, a user cannot tell an audited `os.write` row from a
community `HTTPoison.post` row, and the strings would have to say the tool
vouches for both.

## Open Questions

- **OQ1 and OQ2 — RULED, see ruling 10.** Both were settled by reading the
  files rather than by deferring them.
- **OQ3 — RULED, see ruling 9.** The repo tier does not load by default.
