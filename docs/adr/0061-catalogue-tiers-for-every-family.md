<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0061: Four Catalogue Tiers, for Every Family

Status: Accepted — implementation in progress
Related: [ADR-0016](0016-io-boundary-analysis.md), [ADR-0017](0017-taint-zone-dataflow.md), [ADR-0045](0045-user-config-and-backend-trust.md), [ADR-0047](0047-catalogue-scope-and-user-visible-homes.md), [ADR-0060](0060-non-boundary-taint-sinks.md)

## Context

hypergumbo's analyses are driven by catalogue data: I/O primitives, taint
sources, sinks and sanitizers, function summaries, library signatures,
framework patterns, and more (`hypergumbo_core.yaml_catalogs.YAML_CATALOGS` is
the registry). Rows come from four places: the standard-library rows
hypergumbo maintains; rows for third-party libraries it ships without
maintaining them; rows the operator adds; and files inside the repository being
analysed.

These sources deserve different trust. A row can do more than add a finding.
It can also make a verdict cleaner. A sanitizer clears a flow, a summary can
end a taint branch, and an I/O row makes a call count as examined. A clean
verdict is only as trustworthy as the least-trusted row it rests on, so every
row needs a tier, whatever family it belongs to, and the verdict has to say
which tiers it rested on.

## Decision

**1. Four tiers, named by who vouches, applying to every catalogue family.**

| tier | contents | vouched for by | lives in |
|---|---|---|---|
| **built-in** | rows for a language's standard library and platform | hypergumbo | the installed package |
| **community** | rows for third-party libraries, shipped but not maintained | nobody | the installed package |
| **yours** | rows the operator supplies | the operator | `$XDG_CONFIG_HOME/hypergumbo/` (`config.toml`, one `<family>.d/` per extensible family), or a file the operator names on the command line |
| **in-repo** | catalogue data inside the analysed repository that the operator did not name | nobody, until the operator opts in (ruling 4) | `<repo>/.hypergumbo.toml`'s catalogue keys, `<repo>/.hypergumbo/<family>.d/` |

- **The built-in line is the standard-library line** of ADR-0016 §27. It is now
  drawn for every family, not only the I/O catalogue.
- **`cfg_nodes` and `url_folding` have only the built-in tier.** Their rows
  describe grammar node types and hypergumbo's own folding engines, not anyone's
  libraries (ADR-0047 ruling 10).

**2. Community and un-opted in-repo rows may add findings, but never make a
verdict cleaner.** A verdict is "cleaner" whenever it moves toward `confirmed`.
The test is what a row can do, not which family it belongs to.

| a row that… | examples | from community or un-opted in-repo |
|---|---|---|
| adds a finding or structure | a taint source or sink, a framework pattern, a propagating summary, an I/O row that matches a call | takes effect, disclosed |
| removes a finding or an uncertainty | a sanitizer, a summary that ends a branch, a `module_completeness` grant, an I/O row that makes a call count as examined, a library signature that types a receiver | only the adding half takes effect; the removing half is withheld, and the verdict names the withheld rows |

- **Built-in rows and your rows take full effect.** When your row removes a
  finding, the verdict still carries the existing user-supplied-sanitizer caveat
  (exit 3).
- **Community `library_signatures` rows are disclosed, not withheld.** Typing a
  receiver is in the second column because it can shrink the untyped-receiver
  disclosures, but measured on the Django application the shipped community
  signatures were written for, they added flows and made no verdict or caveat
  set cleaner.
- **`frameworks` and `dataflow_patterns` rows are disclosed, not withheld.**
  Measured with every community file in both families removed, on five
  repositories using the frameworks they describe: no verdict, flow or caveat
  changed, while the rows added up to 1,127 concept-bearing symbols, 380 edges
  and 24 entrypoints. They add structure and removed nothing there. A family
  later shown to remove a finding gets the withholding.
- **Consequence: third-party crypto sanitizers stop clearing flows.** The
  `cryptography`, `aes_gcm` and `ring` sanitizers become community rows. A
  program that decrypts, re-encrypts and writes will read `violated` on a
  plaintext→`host_fs` claim until the operator vouches for those rows (ruling 3).
  The I/O catalogue already made this trade for HTTP client libraries: an
  honest `inconclusive` or `violated` beats a clean verdict resting on
  unmaintained rows.

**3. Provenance travels with the row, and vouching is an explicit act.**

- **Every shipped catalogue file declares its tier.** Its header says
  `provenance: builtin` or `provenance: community`, and a gate refuses a shipped
  file that says neither. A community file also carries a dated `retrieved:`.
- **The loader reads the tier from that line, never from the directory.** A
  community file copied into your config home stays community.
- **Deleting the `provenance: community` line is how you vouch for a file.** A
  file under your config home with no provenance line is yours.

**4. In-repo catalogue data is off unless the operator opts in.**

- **Preferences in `.hypergumbo.toml` still load by default.** These are the
  `[merge]` and non-executing `[backends]` tables of ADR-0045 and ADR-0057.
- **Catalogue data inside the repository loads only on opt-in.** That covers
  `.hypergumbo.toml`'s catalogue keys (today `io_primitives`) and
  `<repo>/.hypergumbo/<family>.d/`.
- **There are two ways to opt in:**
  - for one run, a command-line flag;
  - for one repository, a grant recorded under `$XDG_STATE_HOME/hypergumbo/`.
    It is keyed by resolved path, as backend trust is (ADR-0045 ruling 7). A
    decline is recorded as a decision (ADR-0045 ruling 8).
- **There is no `config.toml` key for this.** A global setting would be a
  standing grant to every repository the operator ever clones.
- **Naming a file on the command line opts that file in.** So
  `--claims path/in/repo.yaml` loads that claims file's `extra_catalogs:`
  exactly as before. Nothing inside a repository loads merely because it exists.
- **Tier names follow location, not authorship.** hypergumbo cannot know who
  wrote a file in a working tree, so the names describe where a file lives.
  What it can check, and reports for every in-repo file it loads, is git state:
  - `committed`: tracked and unchanged since `HEAD`;
  - `modified`: tracked and changed locally;
  - `untracked`.

  A file named on the command line that lives inside the repository is reported
  the same way.

**5. Every loaded file is disclosed by tier.**

- **The `verify-claims` JSON lists every catalogue file the run loaded.**
  `catalog_provenance` groups them under `builtin`, `community`, `yours` and
  `in_repo`, with each file's path, and git state for `in_repo`.
- **The verdict says when its outcome depended on a withheld row** (ruling 2).
- **The one-line stderr notice** that community rows were loaded covers every
  family.
- **`--no-default-overlays` omits community rows from every family, taint
  included.**

**6. One vocabulary, and one page that uses it.**

- **User-facing text uses only four names for the tiers:** built-in,
  community, yours and in-repo. "Project-local" is retired.
- **One user-facing page, `docs/CATALOGUES.md`, covers:**
  - the four tiers;
  - where each family's files live;
  - how to add a row to each family;
  - what each tier can do to a verdict.
- **The page's registry facts are generated from `YAML_CATALOGS`,** so they
  cannot drift: the families, which ones accept your rows, and which are read.

**7. Your taint model gets one persistent home.** The taint families get
`yours` channels: `taint_sources.d/`, `taint_sanitizers.d/` and
`taint_sinks.d/`, all of them read. The claims file travels with the
repository, and flags last one run, so neither can be that home.

## Consequences

### Positive

- **"Who stands behind this row?" has one answer for every family,** readable
  in the JSON and in `catalog-inventory`.
- **A clean verdict rests only on rows hypergumbo or the operator vouches for.**
- **No existing invocation changes meaning** (ruling 4, naming a file).

### Negative

- **More `violated` and `inconclusive` verdicts on third-party code.** This
  follows from ruling 2 for repos that use third-party crypto, test or logging
  libraries. It is measured on the corpus before those rows move, and the
  change is reported with its size and direction.
- **A repository's `.hypergumbo.toml` overlays need an opt-in to load.**

### Neutral

- **Where community files sit on disk is an implementation choice,** because
  the file's own declaration decides its tier (ruling 3).

## Alternatives Considered

- **A1 — Draw the vouched line for the I/O catalogue only.** Rejected: a
  community sanitizer could clear a finding with no trace on the verdict.
- **A2 — Name the fourth tier "repository-supplied".** Rejected: it asserts an
  authorship hypergumbo cannot check.
- **A3 — Decide a row's tier by the directory it sits in.** Rejected: copying a
  file then silently changes its tier.
- **A4 — Opt into in-repo catalogues with a `config.toml` key.** Rejected: that
  is a standing grant to every future clone (ruling 4).

## Open Questions

- **Whether a framework row can lift a coverage gate.** A framework-derived
  edge could make a language count as having produced call edges, which would
  make a verdict cleaner. The measurement behind ruling 2 did not meet that
  case: every language in it already produced call edges.
- **Whether any third-party library earns built-in status.** The built-in line
  is the standard-library line; widening it needs a new owner ruling.
