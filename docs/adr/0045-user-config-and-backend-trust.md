<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0045: User Configuration and Backend Trust Are Separate Stores

- Status: **Accepted**
- Date: 2026-08-23 (adopted by the owner the same day, with implementation authorised)
- Supersedes: —
- Superseded by: —
- Related: ADR-0012 (Pass Unification and Multi-Fidelity — the backends this configures), ADR-0013 (Structured Tracker — the `$XDG_CACHE_HOME` out-of-tree-state and human-owned-config-file patterns reused here), ADR-0016 §35 / ADR-0017 §370 (project-local overlay precedence, which this extends for catalogs and deliberately *inverts* for trust), ADR-0022 (Per-Language Configuration Surface — a different sense of "configuration"; see §Naming below); tracker items WI-sobig (the trigger), WI-jivim, WI-nanom (the next backend this must serve), WI-gojum.

## Context

### hypergumbo has no user configuration of any kind

Read in code 2026-08-23. There is no `$XDG_CONFIG_HOME` reader anywhere in `packages/*/src/`, no `hypergumbo.toml`, no per-repo config loader. (`.hypergumbo` in `discovery.py` is an *output-artifact* name in an ignore list, not config.) Every preference is expressed per-invocation or through the environment.

This has been survivable because the CLI's options were per-run choices. It stops being survivable at the point where an option encodes a decision the user wants to make **once**, and in particular where that decision is a security decision.

### The trigger: the only durable opt-in has the wrong scope

The SCIP-backed Rust backend is gated behind `should_use_rust_analyzer_backend`, which accepts exactly two inputs — the `HYPERGUMBO_RUST_ANALYZER` environment variable, and `--backend rust-analyzer`, which `cli.py` **normalises into that same environment variable** (`cli.py:11311`, `11319`). `install-rust-analyzer` installs the binary via rustup and enables nothing.

The gate exists because indexing **executes the analysed workspace's `build.rs` and proc macros as the invoking user**. The tool says so in its own warning text, and AGENTS.md carries the standing rule: *never on untrusted repos*.

So the only way to make the choice durable today is exporting `HYPERGUMBO_RUST_ANALYZER=1` from a shell profile. That is **global**: it opts in every Rust repository the user ever analyses, including ones cloned specifically to audit or triage. **The only persistence hypergumbo offers has exactly the wrong scope for the property its gate exists to enforce**, which is presumably why the tool never suggests it.

### Two smaller pressures, independent of trust

- **Overlay paths are retyped every run.** ADR-0016 §35 granted the boundary arm a project-local overlay channel (`--io-primitives`, repeatable, later outranks earlier, all outrank built-in), extending the taint-arm pattern from ADR-0017 §370. A project that has authored overlays must name them on every invocation.
- **The opt-in nudge cannot be dismissed.** `check_rust_analyzer_disclosure` fires the "installed but NOT enabled" arm on every run against every Rust repo whenever the binary is on `PATH`. A user who has deliberately chosen tree-sitter has no way to say so. The project has already written down why this matters, in `TestRustAnalyzerDisclosureRespectsTheGate`: *"A nudge that fires when it is already moot trains people to skim past the one sentence that must land."* That reasoning was applied to the backend-*active* case and fixed there; it applies equally to a user who has answered and keeps being asked.

### Naming: this is not ADR-0022's "configuration surface"

ADR-0022 is titled *Per-Language Configuration Surface* and concerns **package-internal YAML catalogs** — which rules ship *with hypergumbo* for language X (`io_primitives/`, `frameworks/`, `dataflow_patterns/`, …). That is data the tool carries. This ADR concerns **user preferences and user trust decisions** — data the user carries. The two are disjoint and must not be conflated in code or docs; where ambiguity is possible, say "catalog surface" (0022) and "user config" (this ADR).

### What already exists and should be reused

ADR-0013 supplies two directly applicable patterns:

1. **Out-of-tree state under `$XDG_*`**, keyed so that multiple checkouts behave sensibly (`$XDG_CACHE_HOME/hypergumbo-tracker/<repo-fingerprint>/`).
2. **A human-owned config file the agent may read but not write** — `config.yaml` is gitignored, `chown <human_user>`, `chmod 644`, with the OS enforcing the boundary: *"The agent can read config (needs to, for validation) but cannot write it."*

The second is load-bearing here and is generalised by ruling 6.

## Decision

**Introduce user-level configuration, and keep backend trust out of it.** Preferences and trust grants are different kinds of fact with different portability requirements, and they get different stores.

1. **Two stores.**
   - `$XDG_CONFIG_HOME/hypergumbo/config.toml` — preferences. Hand-edited, portable, safe to commit to a dotfiles repo.
   - `$XDG_STATE_HOME/hypergumbo/trust.d/` — backend trust grants. Machine-written, not meant to travel.

   `~/.config` is *designed* to be synced across machines; that is correct for preferences and disqualifying for a grant of code execution. A `backend = "rust-analyzer"` line in a dotfiles repo reproduces the global-environment-variable footgun with nicer syntax and follows the user to every machine they clone to.

2. **A trust grant is never a config key.** No config file, at any tier, may enable a backend that executes analysed-repo code. The loader **rejects** such a key with a named error rather than ignoring it — a silently-dropped key trains the user to believe it took effect.

3. **Project-local config may not set security-gated keys.** ADR-0016 §35 / ADR-0017 §370 establish that project-local overlays *outrank* built-ins; that is right for catalogs and exactly inverted for trust. A repository that ships its own "trust me" marker is precisely the attack the gate exists to prevent. Enforced as a deny-list at load time, with the error naming both the key and the file.

4. **Precedence, and stop laundering the flag through the environment.** The order is: **CLI flag > environment variable > project config > user config > built-in default.** `cli.py` currently normalises `--backend rust-analyzer` into `HYPERGUMBO_RUST_ANALYZER=1`, which erases the distinction between the two highest tiers. Untangle that *before* adding a third source, not after.

5. **Backend opt-in is two concepts, and gets two homes.** A backend that executes analysed-repo code (rust-analyzer) is a **trust grant**. A backend that does not (pyright, tsserver, gopls — pyright performs pure static inference and executes nothing) is an ordinary **preference**. They must not share one key. Each backend declares `executes_analysed_code: bool`, and which store its opt-in lands in follows from that bit rather than from a hardcoded list — so the next backend cannot be added into the wrong store by omission.

6. **The trust store is human-owned, agent-readable, agent-unwritable.** ADR-0013's config-ownership pattern (`chown <human>`, `chmod 644`) applies with more force here than it does to the tracker: in a repository whose agents invoke hypergumbo autonomously, **a trust store the agent can write is not a trust store**. The command that writes it is human-invoked.

7. **Grants are keyed by absolute resolved path; a changed `build.rs` revokes, a changed `Cargo.toml` does not.** *(Amended 2026-08-23, resolving OQ1 — the original wording made the whole hash advisory.)* Keying is not by ADR-0013's repo-fingerprint: that key deliberately *shares across checkouts*, which is right for a cache and wrong for trust — two clones of one remote can have different working trees. The split is the substance: `build.rs` is the file that actually **executes** and it changes rarely, so a prompt about it carries information, while `Cargo.toml` churns with routine dependency bumps and re-prompting on that is how a person learns to click through the prompt that matters. **What this does not buy, and this was on the table when it was decided:** neither form protects against a *dependency's* build script, because only the top-level manifests are hashed. It catches tampering with this project's own build script, and nothing else.

8. **A decision, either way, silences the nudge.** The store records declines as well as grants, so "I have chosen tree-sitter for this repo" is expressible. The nudge goes quiet for any path with a recorded decision and keeps its security disclosure for paths without one.

## Consequences

### Positive

- The durable opt-in acquires the granularity the trust decision actually has: per repository, not per machine.
- The security disclosure stops firing at people who have already read it, so it retains the attention it needs on first encounter.
- Overlay paths, output defaults, and every future backend get a home, so this is not one-key infrastructure.
- Ruling 5 means WI-nanom's pyright backend needs no trust machinery at all — its opt-in is a plain preference — while the machinery exists for any future backend that does execute code.

### Negative

- **This is hypergumbo's first user-config mechanism**, so it becomes a compatibility surface: schema, versioning, and precedence are now things that can break. That cost is the reason this is an ADR and not a ticket.
- Two stores are more to explain than one. The alternative is one store that is wrong for half its contents.
- Ruling 4 requires a small refactor of a working code path before any of the new value lands.

### Neutral

- No behaviour changes for users who set nothing: the built-in default is unchanged and the gate's existing two inputs keep working at their existing precedence.
- The trust store is additive to the gate, consulted only after flag and environment.

## Alternatives Considered

**A1 — One config file with a `backends.rust_analyzer = true` key.** Simplest, and the shape the trigger question naturally suggests. Rejected: it reproduces the global-environment-variable hazard with better ergonomics, and `~/.config` is *more* likely to be synced across machines than a shell profile, not less. The convenience and the hazard are the same feature.

**A2 — Hash-strict revocation on ANY manifest change.** Rejected 2026-08-23 in favour of the `build.rs`-only split now in ruling 7. Full strictness re-prompts on every ordinary dependency bump, and per `TestRustAnalyzerDisclosureRespectsTheGate` an alarm that fires when it is moot trains people to skim the one that isn't — so it buys the same protection as the split while spending the attention that makes any prompt work.

**A3 — Key grants by repo-fingerprint (remote URL + first commit SHA), per ADR-0013.** Rejected for trust, endorsed for cache. Fingerprint keying exists so multiple checkouts *share*; trusting one clone must not trust another clone at a different path with a different working tree.

**A4 — In-repo `.hypergumbo/config.toml` only.** Allowed for non-gated keys (ruling 3 permits project config), forbidden for gated ones. In-repo config alone cannot express a trust decision for the same reason ruling 3 gives.

**A5 — Do nothing; document the environment variable.** Rejected. Documenting it would be documenting the footgun: the scope mismatch in §Context is a property of the mechanism, not of its documentation.

## Implementation status (2026-08-23)

- **Ruling 4 — landed** (`hypergumbo_core.backend_selection`). Fixing it uncovered a live defect rather than the tidy-up this ADR anticipated: `--backend tree-sitter` did not disable a backend enabled via the environment, so the opt-out the tool advertises in its own security warning was inert and the analysed repository's `build.rs` ran anyway. Filed and closed as INV-funam.
- **Rulings 1 (config half), 2, 3 — landed** (`hypergumbo_core.user_config`). Two tiers (`$XDG_CONFIG_HOME/hypergumbo/config.toml`, `<repo>/.hypergumbo.toml`), the trust-key deny-list, and `io_primitives` overlay paths as the first real setting.
- **Ruling 4's config tiers inside `resolve_optin` — DEFERRED, with a trigger.** Those tiers are reachable only by a backend whose opt-in is *allowed* in config, i.e. one that does not execute analysed-repo code (ruling 5). No such backend exists yet. Wiring them now would add a layer with no consumer and no way to test it except hypothetically — the same reasoning ADR-0022 used to defer its own runtime-aggregation half. **Trigger: the first non-executing backend** (pyright, WI-nanom). `resolve_optin` is shaped for it and its docstring names where the tiers go.
- **Rulings 1 (trust half), 5, 7, 8 — landed** (`hypergumbo_core.backend_trust`, `hypergumbo trust-backend`). Grants live under `$XDG_STATE_HOME/hypergumbo/trust.d`, keyed by resolved absolute path, `0600`, with a decline recorded as a decision so the nudge can stop asking. The trust store is the lowest tier of the opt-in chain (flag > env > grant), and `executes_analysed_code` now gates the store from *both* sides — config refuses an executing backend, the store refuses a non-executing one.
- **Ruling 6 — partially landed, and the gap is stated rather than papered over.** The store is `0600` and lives outside every synced directory. The stronger property the ruling asks for — a store the human owns and an automated agent cannot write — is enforceable only by the operating system, and only when the agent runs as a different user: hypergumbo cannot distinguish an agent from a person sharing a uid. An operator who wants it must `chown` the store to the human account, exactly as `tracker init` does for its config. `hypergumbo trust-backend` documents this in its own docstring.
- **A defect found while wiring ruling 8, worth recording because it inverted the disclosure.** `rust_analyzer_backend_enabled` read the environment variable alone. Once a *grant* could enable the backend, a granted run had no variable set, so it reported not-enabled, took the nudge branch, and was then suppressed by its own recorded decision — a run that **was** executing the repository's build scripts printed nothing about it. Fixed by resolving "enabled" through the whole precedence chain; pinned by `test_a_recorded_grant_still_discloses_that_scripts_are_running`.

## Open Questions

- **OQ1 — RESOLVED 2026-08-23 by the owner: revoke on `build.rs`, ignore `Cargo.toml`.** Ruling 7 is amended above. The original advisory-for-everything form traded a real safety property for friction avoidance on a judgement about human behaviour rather than a measurement; the split keeps most of the protection at close to none of the noise. The residual limit (transitive build scripts are not covered) is stated in ruling 7 rather than left as doubt.
- **OQ2 — TOML vs YAML.** The repo is YAML-heavy, but every existing YAML is *data the tool carries* (ADR-0022's catalog surface), not a hand-edited user preference file. TOML is recommended for the config file on those grounds. Not load-bearing; settle at implementation.
- **OQ3 — Does the project-config tier justify itself on day one**, or should the user tier ship alone with project config deferred until an overlay-carrying project asks? Rulings 3 and 4 are written to hold either way.
- **OQ4 — Schema versioning strategy is not settled here.** Whatever is chosen should be decided before the first release that reads the file, since the migration cost is asymmetric.
- **OQ5 — Not measured:** no user has reported this. The trigger came from an authored fixture (`~/hypergumbo_lab_notebook/rust_fidelity_probe_08232026/`) and a code read, not from a report. The reasoning stands on the scope mismatch being structural, but the *demand* is inferred.
