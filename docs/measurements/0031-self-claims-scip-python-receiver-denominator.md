<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0031: The self-claims on a scip-python map — the receiver denominator, and what typing the receivers uncovers

**Status:** Complete
**Date:** 2026-11-04
**Instrument:** `~/hypergumbo_lab_notebook/muzah_11042026/` — `PREREGISTER.md` (final before side B ran), `compare.py` (the per-claim table), `unknown_modules2.py` (the full unclassified-module set through `verify_claims._adjudicate_external_modules`, over the same shipped-scope edge set `cmd_verify_claims` walks), `unknown_classified.md`, `muzah_scip_claims.{json,err}` (side B, stdout and stderr separate), `b_map_path.txt` / `a_map_path.txt` (the two maps)
**Claims:** [`docs/hypergumbo.claims.yaml`](../hypergumbo.claims.yaml), verbatim, 18 claims, `analysis_scope: shipped_artifact`
**Tracker:** `WI-muzah` (this measurement), `INV-hotib` (the three slots that withheld every verdict on the tree-sitter map; fixed in PR #2042), `WI-jizam` (the per-PR guard, PR #2043)

## Frame

Machine-readable per ADR-0048 §A3.

- unit: THREE units, never pooled. (a) VERDICTS: the 18 self-claim verdicts and
  their caveat sets. (b) RECEIVER SITES: the `unknown_receiver_scope` pair
  "N untyped of M method call sites", computed by `verify_claims.unknown_receiver_scope`
  over the shipped-scope edge set — M counts call edges stamped
  `call_construct == "method"` whose destination language has an I/O catalogue,
  external callees included; N is the subset whose receiver no producer typed.
  (c) UNCLASSIFIED MODULE SLOTS: the set `_adjudicate_external_modules` returns
  as `unknown`, which is what withholds a verdict (`qualifying_only: false`).
- allocation: CENSUS of one tree, dev `94b03ded17`, both sides.
- seed: none. Both sides are deterministic given the installed scip-python.
- cohort: hypergumbo's own shipped artifact (8 packaging-declared roots).
- sides: **A** = the gate's own invocation,
  `python -m hypergumbo_core verify-claims . --claims docs/hypergumbo.claims.yaml --json --minimal`,
  tree-sitter backend, on tree `3aca2b5757`, byte-identical to `94b03ded17`
  (`git diff --stat` empty), REUSED from the WI-jizam closure run. **B** = the same
  invocation with `HYPERGUMBO_SCIP_PYTHON=1`, `NODE_OPTIONS=--max-old-space-size=9000`,
  scip-python 0.6.6 (the cron's pin, `scip_python_install.SCIP_PYTHON_PINNED_VERSION`)
  on a fresh `XDG_CACHE_HOME`.
- pre-registered expectation: unmeasured; the cron's "98% typed" figure may be a
  different population than the 14,027. Decision rule: an untyped share under 50%
  recommends the gate run on the scip map, sequenced after INV-hotib's cron
  validation; any verdict change is filed before any switch.
- claim_set: the 18 shipped self-claims of `docs/hypergumbo.claims.yaml` at dev
  `94b03ded17`, verbatim, `analysis_scope: shipped_artifact`, with the
  self-catalogue and `docs/io-primitives-overlays/hypergumbo-self.yaml` the file
  names; no lab variant.
- rubric: measurement 0001's, cited verbatim (TRUE POSITIVE = a real value flow
  into a sink argument; exploitability is not the question), and NOT APPLIED:
  both sides report 0 evidence on every claim, so no finding was adjudicated.
  The units adjudicated here are verdicts, receiver sites and module slots, each
  read off the tool's own output and re-derived from the map by the instrument.
- analyzer_sha: dev `94b03ded17` for the code and the tree on both sides (side A
  ran on `3aca2b5757`, whose tree is byte-identical: `git diff --stat` empty);
  scip-python `0.6.6`; side B's map `~/hg_wt/muzah_cache/.../survey.json`
  (`b_map_path.txt`), side A's the main results cache (`a_map_path.txt`).
- language_scope: python only — the only language with declared sources in the
  claims file, and the only one scip-python types. `c` and `cpp` appear in the
  shipped scope's edge set (vendored grammar sources) and are EXCLUDED, not
  measured at zero: no self-claim source is in them and they have no taint
  catalogue, so they reach no verdict input; the instrument filters to
  `python:`-sourced edges before the adjudication, as the per-PR guard does.
  Every other language in the repository is outside the shipped-artifact scope.

## Question

The 18 published self-claim verdicts are closed-world over the receivers the
analysis could type, and on the gate's map that is 26% of method call sites. The
full-suite cron builds a second map of the same tree with the scip-python backend
and reports 98% of Python method calls typed. Does the scip map materially shrink
the self-claims' untyped denominator, and does any verdict change? A "yes, and the
verdicts hold" would move `scripts/check-self-claims` onto the scip map.

## Results

### Per claim (18 rows; every row of a side is identical, which is itself the finding: the withholding is tree-wide, not per claim)

| claim | verdict A | verdict B | untyped sites A | untyped sites B | `untyped_receiver` method names A → B |
|---|---|---|---|---|---|
| runtime-cli-no-host-fs | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 147 → 10 |
| runtime-cli-no-network | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 1,216 → 82 |
| runtime-cli-no-subprocess | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 78 → 0 |
| runtime-cli-no-install-artifact | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 0 → 0 |
| runtime-cli-no-dev-zone | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 0 → 0 |
| install-gitleaks-no-host-fs | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 147 → 10 |
| install-gitleaks-no-dev-zone | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 0 → 0 |
| install-embeddings-no-host-fs | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 147 → 10 |
| install-embeddings-no-install-artifact | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 0 → 0 |
| install-embeddings-no-dev-zone | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 0 → 0 |
| build-grammars-no-host-fs | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 147 → 10 |
| build-grammars-no-install-artifact | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 0 → 0 |
| build-grammars-no-dev-zone | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 0 → 0 |
| install-rust-analyzer-no-host-fs | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 147 → 10 |
| install-rust-analyzer-no-install-artifact | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 0 → 0 |
| install-rust-analyzer-no-dev-zone | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 0 → 0 |
| add-extras-no-host-fs | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 147 → 10 |
| add-extras-no-dev-zone | confirmed_with_caveats | inconclusive | 10,396 of 14,027 | 1,227 of 17,197 | 0 → 0 |

Evidence count is 0 on every row of both sides: no flow reaches a prohibited zone
on either map. Caveat kinds: A carries `opaque_boundary` + `unknown_receiver_scope`
(+ `untyped_receiver`, `user_supplied_sanitizer` where applicable); B drops
`opaque_boundary` from the caveat list because the verdict is withheld before the
caveat stage. `analysis_fidelity.python`: A `inherited-calls-linker, method-call-recovery-linker, python`;
B `method-call-recovery-linker, python, scip` (the inherited-calls linker's absence
on B is observed, not examined).

### The receiver denominator

| side | untyped / method call sites | untyped share | edges (all) | python-src edges in the shipped scope | taint findings (ddg / ddg_mixed / structural) |
|---|---|---|---|---|---|
| A tree-sitter | 10,396 / 14,027 | 74.1% | 255,785 | 59,023 | 1,039 (144 / 842 / 53) |
| B scip-python | 1,227 / 17,197 | 7.1% | 366,009 | 84,264 | 1,327 (151 / 1,119 / 57) |

The denominator GROWS by 3,170 sites on B: calls the tree-sitter arm could not
attribute as method constructs are resolved by scip and enter the population.

### The two "typed" figures are different populations

On B's OWN map, `scripts/check-test-only-reachability --map <B map> --require-pass scip_python`
prints **9,587 of 9,772 (98.1%)**. `test_only_reachability.typed_method_call_share`
counts `calls` edges whose DESTINATION is an in-repo Python node of kind `method`,
over the whole map (tests included), and calls an edge typed when `scip` is among
its producers. The self-claims denominator (17,197 on the same map) counts
method-CONSTRUCT call sites in the shipped scope whatever the callee — external
methods such as `Path.write_text` included, which is exactly where the receiver
type decides which catalogue row applies. Same map, 9,772 against 17,197: the cron's
98% does not describe the population the verdicts are closed-world over. The
pre-registered suspicion holds.

### What typing the receivers uncovers: 86 module slots no catalogue row classifies

B is withheld (`qualifying_only: false`) by the external-module adjudication, not by
the receivers: on the shipped-scope edge set `_adjudicate_external_modules` returns
**86 unknown** (A: 0; A is withheld only by the same 3 opaque launch sites, which
qualify rather than block). scip resolves a receiver to its DEFINING CLASS, so the
slot is `io.TextIOWrapper` where the tree-sitter arm said `io` or nothing, and the
catalogue's module-level rows and the overlay's `module_completeness` grants are
asked about a name they do not hold. Full list and classification:
`unknown_classified.md` in the instrument directory.

| kind | slots | already covered one level up by an overlay grant | untraced dependency source |
|---|---|---|---|
| stdlib class / private submodule (`builtins.dict`, `io.TextIOWrapper`, `typing.Mapping`, `sqlite3.dbapi2`, `threading.Thread`, `tarfile.TarInfo`, …) | 51 | 0 | 0 |
| third-party class / submodule (`numpy.ndarray`, `coverage.control.Coverage`, `textual.app.App`, `tree_sitter.Node`, `uvicorn.main`, `starlette.requests.HTTPConnection`, …) | 35 | 25 | 35 |
| in-repo | 0 | | |

Of the 51 stdlib slots, most name containers and protocols that do no I/O
(`builtins.str`, `typing.Mapping`, `enum.Enum`), and a few name I/O carriers the
module-level catalogue reaches only through the module (`io.TextIOWrapper`,
`io.BufferedIOBase`, `sqlite3.dbapi2`, `tarfile.TarInfo`, `threading.Thread`). The
slot-exactness rule (a grant on `datetime` does not vouch for `datetime.timezone`,
INV-hotib) is what turns 25 already-granted modules into 25 withholding slots here.

### Cost of side B

| | |
|---|---|
| wall clock (`/usr/bin/time -v`, whole run incl. the 18-claim taint pass) | 11 min 02 s |
| maximum resident set (GNU time, the python process) | 8.2 GB |
| scip-python indexer, observed 90 s in | 4.8 GB RSS, separate `node` process |
| scip translate warning | 1,185 edge endpoints dropped, symbol recorded in several Documents (INV-sipod) |
| map size on disk | 596 MB (A: 322 MB) |

The cron's own note on the self-tree step (full-suite.yml): map 8m47s against 5m32s
plain, summed process-tree peak 10.3 GB, which is why that step waits for the two
other heavy steps on a 16 GB VM.

## What the numbers do not support

- No verdict moved from clean to violated: evidence_count is 0 on both sides for
  all 18. B's `inconclusive` is a coverage withholding, not a finding.
- The 7.1% is NOT "93% of the self-claims' receivers are typed on the cron's
  map": the cron's self-tree map is built by `run . --out`, the gate's by
  `verify-claims .`'s implicit survey; the two were not compared edge for edge.
- `typed_method_call_share` and `unknown_receiver_scope` were compared on ONE map;
  the cron log's 9,570 of 9,767 was produced on a different commit.
- The 86 slots are the shipped-scope set on this tree with this overlay; they move
  with both.

## Decision

**The gate does not move to the scip-python map now.** The pre-registered rule's
first half fires (7.1% < 50%) but its second half blocks: every verdict changes,
to `inconclusive`, on 86 class-qualified slots that need catalogue rows or
overlay grants before the scip map can carry a verdict at all. Granting 86 slots
one by one, 51 of them stdlib classes, is the wrong shape; the question the
switch forces is whether a completeness grant should vouch for the classes a
module DEFINES (a catalogue-tier decision touching INV-hotib's slot-exactness
rule), or whether the catalogue grows class-level rows for the I/O-bearing
classes. That decision is filed as its own item; this record is its measurement.
Until it is taken, the published verdicts stay closed-world over 26% of method
call sites, and SECURITY.md's `unknown_receiver_scope` caveat is the honest
statement of that.
