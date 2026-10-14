<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0047: Catalogue Scope, and Where a User's Catalogue Data Lives

- Status: Accepted
- Date: 2026-08-27
- Related: ADR-0016 (I/O Boundary Analysis — catalogue scope and overlays), ADR-0045 (User Configuration and Backend Trust — the config tiers), ADR-0017 (Taint-Zone Dataflow — the `taint_*` catalogues), ADR-0061 (the four catalogue tiers), ADR-3aaa (the `frameworks/` catalogue).

## Context

hypergumbo analyses the repository in front of it, not the repository's
installed dependencies. To see I/O or taint that happens inside a library, it
needs a row saying what that library call does. The shipped catalogues hold rows
hypergumbo can stand behind: the standard library of each language (ADR-0016).
Every language also has dozens of popular third-party libraries, and a real
application that uses only those would otherwise report no boundaries at all.
So hypergumbo also ships rows it does not maintain, and users need to add their
own.

That raises three design questions:

- how shipped-but-unmaintained rows are kept apart from vouched ones;
- where a user's own catalogue data lives;
- which catalogue families a user may extend at all.

The catalogue registry (`hypergumbo_core.yaml_catalogs.YAML_CATALOGS`) is where
the answers are declared and checked.

## Decision

**1. Tiers.** Every catalogue row is **built-in**, **community**, **yours** or
**in-repo**, whatever family it belongs to. A community row may add a finding
but never make a verdict cleaner. ADR-0061 defines the tiers.

**2. Seed, never copy.** Built-in catalogues live in the installed package and
are **never copied** into a user directory. Only a user's *deltas* live in their
config directory. A full copy would silently stop the next release's rows
reaching exactly the users who engaged enough to customise.

**3. A user's catalogue data lives under `$XDG_CONFIG_HOME/hypergumbo/`.**

- **Layout.** `config.toml` (ADR-0045) sits beside one `<family>.d/` directory
  for each family whose registry entry declares a user channel (ruling 7).
- **The list is derived from the registry,** not written here.
- **Nobody edits files inside their site-packages.** An extensible family must
  have a home a user can find without knowing where Python installed the
  package.
- **Catalogue data inside an analysed repository** lives and loads as ADR-0061
  ruling 4 says.

**4. The home is created by an explicit subcommand, never on first run.**

- **`hypergumbo init-catalogs`** creates the home and seeds the community
  overlays into it, so the user can edit them. Silently creating files in
  someone's config directory is the surprise ADR-0045's human-owned config
  exists to avoid.
- **Loading community rows doesn't depend on this command.** They load from the
  package by default.
- **Each seeded file is stamped `seeded_from:`** with the hypergumbo version it
  came from, so its staleness against the shipped source is checkable.

**5. Provenance.** Every shipped catalogue file declares
`provenance: builtin` or `provenance: community`, and a community file carries
a dated `retrieved:`. The loader reads a row's tier from that declaration
(ADR-0061 ruling 3).

**6. Disclosure.**

- **Every run that loads community rows says so in its default human output,**
  not only in JSON.
- **The `verify-claims` envelope names every loaded catalogue file by tier**
  (ADR-0061 ruling 5).

**7. The registry answers extensibility.** Each family's registry entry either
declares a user channel or gives a reason it has none, and
`scripts/yaml-catalog-index --check` refuses an entry that does neither. A new
catalogue family cannot land without someone answering the question.

**8. Every language has a scope gate.** `test_catalogue_scope_gates.py` refuses
a built-in row outside that language's standard-library line.

**9. hypergumbo never writes into an analysed repository.** Catalogue data
inside one loads only when the operator opts in (ADR-0061 ruling 4).

**Two audiences, two mechanisms.**

- **The normal user has `$XDG_CONFIG_HOME/hypergumbo/`, and it is created by a
  subcommand, not an offer.** Ruling 4's command creates it and seeds the
  community overlays, which are the user's working configuration rather than
  examples.
- **The hypergumbo developer has `~/hypergumbo`, and that is where the offer
  belongs.** A literal `hypergumbo` directory in the home directory is a
  repository checkout. Nobody creates one by accident, so its presence is a
  deliberate signal. When hypergumbo sees it, it may **offer once** to place
  example in-repo overlay files there, showing what a repository's
  `.hypergumbo/` would contain without writing one into any repository.

Three constraints keep the offer an offer:

- **Nothing is ever written into an analysed repository.** A file written into
  someone's working tree is how a tool's output gets committed by accident.
- **A decline is recorded as a decision** (ADR-0045 ruling 8), so the offer
  goes quiet. An offer that cannot be answered permanently is a nag.
- **It is never raised in a non-interactive context.** There is no prompt when
  stdin is not a TTY. An offer that blocks a CI run or an agent invocation is a
  defect.

**10. A family gets a user channel when it describes the USER'S world, not the
LANGUAGE'S.** That test decides each family:

| family | describes | channel |
|---|---|---|
| `io_primitives` | libraries and their I/O | **yes** |
| `taint_sources` / `taint_sanitizers` / `taint_sinks` | the user's trust model | **yes** |
| `frameworks` | conventions, including in-house ones | **yes** |
| `library_signatures` | the types library calls return | **yes** |
| `function_summaries` | dependency behaviour | **yes, gated** (see below) |
| `dataflow_patterns` → `library_patterns` | library and idiom mutation | **yes** (section-scoped) |
| `dataflow_patterns` → grammar rules | tree-sitter node types | no |
| `cfg_nodes` | tree-sitter node types | no |
| `url_folding` | wiring to shipped engines | no |

**`cfg_nodes` is internal.**

- Its rows are grammar node types and field names (`if_statement`,
  `field:condition`) against a named grammar version.
- A user cannot know better than the grammar.
- A wrong row silently breaks the control-flow graph, and with it the taint
  walk.

**`url_folding` is internal for a mechanical reason.**

- Its rows name `engine: fold_array_join`, a Python function inside
  `url_folding/__init__.py`.
- A user's file could only reference engines the package already contains, so
  the channel would be inert without also accepting user code.
- It is a dispatch table, not a decision surface.

**`dataflow_patterns` is mixed.**

- **The grammar section is internal,** for the `cfg_nodes` reason. Its rows
  look like `node_type: assignment / write: left`.
- **The `library_patterns` section describes libraries.** Its rows are regex
  matches over call syntax (`'\.append\('` → `access_mode: mutate`), and a user
  with an in-house collection type has a legitimate row to add.
- **So the channel covers that section only,** never the whole file.

**`function_summaries` gets a channel, and a gate.**

- **Why a channel.** Its entries describe callees whose source is not analysed,
  which is exactly where a user knows something hypergumbo cannot.
- **Why a gate.** A summary that *terminates* a branch removes a flow the tool
  would otherwise report, which makes it a sanitizer declaration by another
  name.
- **So:**
  - a user summary that terminates a branch carries the
    `user_supplied_sanitizer` caveat and its exit-3 contract;
  - a propagating one carries none, because it cannot silence anything.

## Consequences

### Positive

- **Statements that the built-in catalogues are standard-library-only are
  true.**
- **Recall on applications built on popular third-party libraries is kept,**
  without the tool claiming to vouch for those rows.
- **A user can see and edit what their installation knows.**
- **Every family either has a user channel or states why not.**

### Negative

- **hypergumbo ships third-party rows it does not maintain.** Disclosure bounds
  the cost; it does not remove the maintenance burden.
- **A seeded overlay can go stale against its shipped source.** `seeded_from:`
  makes that visible but does not prevent it.
- **A default run's results depend partly on community rows.** Disclosure, and
  the rule that such rows never make a verdict cleaner, mitigate this but don't
  eliminate it.

### Neutral

- **`HIGH_RISK_PRIMITIVES`' drift guard checks community overlays as well as
  built-in catalogues,** so no entry resolves to nothing anywhere.

## Alternatives Considered

**A1 — Ship only standard-library rows.** Rejected: an application built on
third-party libraries is analysed as having no boundaries at all. That is
correct by the letter and useless to the person running it.

**A2 — Copy the full built-in catalogues into the user's directory.** Rejected:
upgrades would never reach the users who engaged most. See ruling 2.

**A3 — Create the user's catalogue home on first run.** Rejected for ADR-0045's
reason: `$XDG_CONFIG_HOME` is the user's, and a tool that writes there uninvited
has made a decision that was not its to make.

**A4 — Treat third-party rows as built-in and drop the standard-library line.**
Rejected: a user could no longer tell an audited `os.write` row from an
unmaintained `HTTPoison.post` row.
