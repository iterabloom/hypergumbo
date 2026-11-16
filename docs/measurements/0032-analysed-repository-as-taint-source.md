<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0032: The analysed repository as a taint source — what a repo-content claim can say, and the three sites where a repo-chosen value sits in a path or argv position

**Status:** Complete
**Date:** 2026-11-04
**Instrument:** `~/hypergumbo_lab_notebook/pakag_11042026/` — `PREREGISTER.md` (final before any run), `repo_content_sources_v1.yaml` / `_v2.yaml` (the two source anchors), `claims_v{1,2}{,b}_{prohibited,allowed}.yaml` (`b` = with the barrier transform of §2; absolute catalogue paths so the lab files run against the shipped self-catalogue), `zone_barrier_repo_content.yaml`, `analyze_run.py` (per-run table and the subset check against the in-run controls), `fixtures/f1_name_in_path`, `fixtures/f2_content_in_content`, one `<run>.json` / `.err` / `_rc.txt` / `_start.txt` per run, `RESULT.md`
**Claims:** the lab variants above; the shipped form is the five `repo-content-no-*` claims this record's PR adds to [`docs/hypergumbo.claims.yaml`](../hypergumbo.claims.yaml)
**Tracker:** `WI-pakag` (this measurement), `WI-bulag` / `WI-gajir` / `WI-mubak` (the sink-side resource-naming role this record runs into from the other side), `WI-gohok` / `WI-jivih` (the source-side value-shape decision), `INV-hotib` (the withheld-for-the-wrong-reason this record's §2 mirrors)

## Frame

Machine-readable per ADR-0048 §A3.

- unit: FOUR units, never pooled. (a) VERDICTS per claim. (b) FLOWS: `evidence`
  rows (capped at 100 per claim by `_MAX_EVIDENCE_ROWS`) with their
  `collapsed_flow_count` ("spanning N source→sink pairs"); a row is a call-graph
  reachability pair, not a value flow (see [What a row counts](README.md#what-a-row-counts)).
  (c) SINK SITES: distinct `(caller function, sink symbol)` pairs across a
  claim's evidence. (d) READ SITES: wrapper call sites read in source and
  adjudicated by the POSITION of the repo-derived value (path / argv / content).
- allocation: CENSUS of one tree, dev `94b03ded17`, shipped-artifact scope, on
  the warm tree-sitter map (`verify-claims .`'s own survey; the first run built it,
  03:08–03:16, every later run reused it, ~2 min each).
- seed: none.
- cohort: hypergumbo's own shipped artifact; the two fixtures.
- arms: **V1** names: a caller-seeded source (`start_at: caller`,
  `return_tainted: true`) on `hypergumbo_core.discovery.find_files` /
  `find_non_test_files` and the `FileIndex` methods. **V2** = V1 plus `pathlib`
  methods `read_bytes` / `read_text`. Each over the PROHIBITED zones (host_fs,
  network, subprocess, install_artifact, dev_zone; the five `runtime-cli-no-*`
  claims run in the same file as controls) and over the PERMITTED zones
  (user_cache, user_out, tmp_artifact, repo_inspection, analyser_backend,
  user_state). Suffix `b` = with a `_safety_zone_barrier` transform for the new
  label (§2).
- pre-registered: prohibited zones `confirmed_with_caveats` as a SUBSET of the
  controls' flows; permitted zones `violated`, many rows; fixture F1 and F2 both
  `violated` with indistinguishable evidence; V2 under-covers. Of these, the
  first prediction FAILED on the first run and the reason is §2.
- claim_set: the lab files `claims_v{1,2}{,b}_{prohibited,allowed}.yaml` — five
  `repo-content-no-*` claims over host_fs / network / subprocess /
  install_artifact / dev_zone (prohibited runs, with the five shipped
  `runtime-cli-no-*` claims verbatim as in-run controls) and six over user_cache
  / user_out / tmp_artifact / repo_inspection / analyser_backend / user_state
  (permitted runs); `fixtures/*/fixture_claims.yaml` (one host_fs claim); and the
  shipped file after this PR (23 claims, `final_gate_actual.json`).
- rubric: measurement 0001's, cited verbatim (TRUE POSITIVE = a real value flow
  into a sink argument; exploitability is not the question), applied to the
  permitted-zone rows of §4 with one addition fixed BEFORE the reading: each row
  is labelled by the POSITION the repo-derived value occupies at the sink call —
  PATH component, ARGV element, STDIN, CONTENT, or NONE (call-graph reachability
  only) — read from the wrapper call in source, since the instrument does not
  carry it. Prohibited-zone rows were not adjudicated: 0 evidence once the
  barrier transform existed, and before it every row was a wrapper internal
  (§2), labelled as such by the sink symbol alone.
- analyzer_sha: dev `94b03ded17`, the code and the tree for every run; the
  tree-sitter map in the main results cache (built 03:08–03:16 by the first
  run); fixtures analysed fresh under their own `XDG_CACHE_HOME`.
- language_scope: python only — the self-catalogue declares sources for no other
  language and the DDG arm walks python here. `c` and `cpp` appear in the
  shipped scope's edge set (vendored grammar sources) and are EXCLUDED, not
  measured at zero: no source of this label is in them and they have no taint
  catalogue, so no claim here can read them. Every other language in the
  repository is outside the shipped-artifact scope; the fixtures are python.

## Question

The 18 self-claims take a CLI handler as the taint source. For a tool whose job
is to read arbitrary repositories, the threat that matters is the repository
choosing a write path outside the zones, a launch argument, or a network
destination. Can that be a verify-claims claim with repo content as the source,
what verdict does the shipped tree get, and what does the attempt force?

## 1. Expressibility: yes, as a declared caller-seeded source

`fs_read` deliberately mints no label (`taint.py` ~296), so the repository
enters as a self-catalogue source. The fixture pair, each with its own declared
source on the fixture's `list_repo_files`, both read **violated**, DDG arm,
confidence `precise`, with byte-identical evidence: `sink_primitive
pathlib.Path.write_text`, `sink_call_sites [(main, write_text)]`,
`resource_naming_flows 0`. F1 is the true positive the claim wants (a repo-chosen
NAME is the write PATH); F2 is the false positive the matcher cannot avoid (repo
CONTENT is the write CONTENT at a literal path). The verdict records the sink
primitive and the call site, never the argument.

## 2. First finding: the zone barrier is enumerated per label, and a new label falls through it

`zone_barrier_sanitizers.yaml` declares `_safety_zone_barrier` once per ENTRY
label (six transforms); the sanitizer schema has no wildcard (`taint.py` ~1751
matches `h.input_taint == input_taint`). The seventh label walked through every
wrapper into its stdlib call:

| run | claim | verdict | evidence (rows / pairs) | where every sink sat |
|---|---|---|---|---|
| v1_prohibited | repo-content-no-host-fs | violated | 5 / 24 | inside wrappers: `cache_write`→`Path.write_text`, `cache_save_npy`→`numpy.save`, `user_out_write`→`Path.write_text`, `user_out_open_json_dump`→`open`/`json.dump`, `cache_mkdir`→`Path.mkdir`, `tmp_artifact_rmtree`→`shutil.rmtree` |
| v1_prohibited | repo-content-no-subprocess | violated | 100 (cap) / 107 | inside `repo_inspect_git` → `subprocess.run` |
| v1_prohibited | runtime-cli-no-host-fs (control) | confirmed_with_caveats | 0, sanitized 20 | the same internals, stopped by the barrier |
| v1_prohibited | runtime-cli-no-subprocess (control) | confirmed_with_caveats | 0, sanitized 13 | same |
| v1b_prohibited | repo-content-no-host-fs | confirmed_with_caveats | 0, **sanitized 5** | — |
| v1b_prohibited | repo-content-no-subprocess | confirmed_with_caveats | 0, **sanitized 100** | — |

Every "violation" was a wrapper internal the controls sanitize: a
violated-for-the-wrong-reason verdict. The PR adds the transform and a gate
(`test_self_catalog_barrier_coverage.py`: every `taint_label` in
`*_sources.yaml` has a barrier transform, and no transform names a label nothing
declares; red against the pre-edit barrier file, green after).

## 3. Prohibited zones (V1, barrier extended): confirmed, and a subset of the entry claims

| claim | verdict | evidence | sanitized | caveat kinds |
|---|---|---|---|---|
| repo-content-no-host-fs | confirmed_with_caveats | 0 | 5 | opaque_boundary, unknown_receiver_scope, untyped_receiver, user_supplied_sanitizer |
| repo-content-no-network | confirmed_with_caveats | 0 | 0 | opaque_boundary, unknown_receiver_scope, untyped_receiver |
| repo-content-no-subprocess | confirmed_with_caveats | 0 | 100 | opaque_boundary, unknown_receiver_scope, untyped_receiver, user_supplied_sanitizer |
| repo-content-no-install-artifact | confirmed_with_caveats | 0 | 0 | opaque_boundary, unknown_receiver_scope |
| repo-content-no-dev-zone | confirmed_with_caveats | 0 | 0 | opaque_boundary, unknown_receiver_scope |

The subset relation holds by construction and by measurement: every seed
function sits inside a runtime `cmd_*` closure, and every sanitized site of the
repo-content claims is one of the controls' sanitized sites (11 distinct wrapper
internals for host_fs, 2 for subprocess; the controls list 0 evidence sites and
20 / 13 sanitized flows). These five claims therefore add no new reachability
fact; they NAME the threat in the published claims file with the same caveats,
which is what the owner's audit sentence needs enumerated.

## 4. Permitted zones (V1, the flood), every flow read at its sink

| claim | verdict | evidence (rows / pairs) | sinks |
|---|---|---|---|
| repo-content-no-user-cache | violated | 6 / 20 | `cache_mkdir` ×3, `cache_write` ×3 |
| repo-content-no-user-out | violated | 2 / 5 | `user_out_mkdir`, `user_out_open_json_dump` |
| repo-content-no-tmp-artifact | violated | 1 / 1 | `tmp_artifact_rmtree` |
| repo-content-no-repo-inspection | violated | 100 (cap) / 117 | `repo_inspect_git` (4 distinct sites: `_ls_tree_head` 100, `_run_git_command` 4, `_run_git` 3, `scan_content` 1) |
| repo-content-no-analyser-backend | confirmed_with_caveats | 0 | — |
| repo-content-no-user-state | confirmed_with_caveats | 0 | — |

Adjudication of each flow by reading its sink call (the instrument says the
wrapper was reached; the position is read from source):

| caller → wrapper | arm | repo-derived value's position | what contains it |
|---|---|---|---|
| `cmd_sketch` → `cache_write(cache_dir / _generate_sketch_filename(flags), sketch)` | precise/ddg | CONTENT (the sketch) | path = fingerprint dir (below) + a flag-derived name |
| `_get_or_generate_comparison_sketch` → `cache_write(sketch_path, sketch_text)`, `(stats_path, json)` | precise/ddg | CONTENT | same path shape |
| `run_survey` → `user_out_open_json_dump(out_path, behavior_map)` | precise/ddg | CONTENT (the map) | `out_path` is the user's `--out` or the results cache path |
| `_get_cache_dir` / `_get_results_cache_dir` → `cache_mkdir(cache_base / fingerprint / …)` | approximate/ddg_mixed | PATH component: `fingerprint = sha256(remote.origin.url ":" root-commit)[:16]`, `state_hash` | hex digests of fixed length; no separator can survive; `cache_mkdir` resolves and raises on escape |
| `_save_cached_embedding` → `cache_save_npy(cache_dir / f"embed_{file_hash}.npy")` | approximate | PATH component: content hash | hex digest |
| `scan_content_cached` → `cache_write(cache_dir / "secret_scan_cache.json", json)` | approximate | CONTENT (hash-keyed JSON) | fixed file name |
| `_emit_handler_slices` → `user_out_open_json_dump(out_dir / _handler_slice_filename(sym, route))` | approximate (the instrument attributed `user_out_mkdir(out_dir)`, which takes the user's directory) | PATH component: the route METHOD and PATH, `_sanitize_filename_part`: `[^a-zA-Z0-9_.-]` → `_`, collapsed, stripped, ≤ 50 chars | no separator survives (`../../x` → `.._.._x`); `user_out_*` resolves and raises on escape |
| `cmd_sketch` → `tmp_artifact_rmtree(_legacy_compare_dir)` | approximate | none (fixed legacy path; reachability only) | — |
| `_ls_tree_head` → `repo_inspect_git([git, "ls-tree", *opts, "HEAD", "--", rel])` | approximate, 100 rows from 88 distinct discovery callers | ARGV: `rel`, a directory the repository chose (the submodule check) | fixed binary via `shutil.which`, fixed subcommand, after `--` (a pathspec, never an option), cwd = superproject, `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE`/`GIT_OBJECT_DIRECTORY`/`GIT_COMMON_DIR` scrubbed, `GIT_NO_LAZY_FETCH=1`, 30 s timeout, read-only |
| `_run_git_command`, `_run_git` → `repo_inspect_git` | approximate | none in argv (`config --get remote.origin.url`, `rev-list --max-parents=0 HEAD`, `rev-parse HEAD`); the OUTPUT (remote URL, SHA) is repo content that feeds the fingerprint above | hashed before it names anything |
| `scan_content` → `repo_inspect_scan([gitleaks, "stdin", …])` | approximate | STDIN: repo content as the scan's input, by design | fixed argv |

## 5. The enumeration the sentence needs: where a repo-chosen value is a path or argv component

By the instrument plus a grep of every caller of the five subprocess wrappers
(`repo_inspect_git` ×4, `repo_inspect_scan` ×1, `backend_index_*` ×2 through the
invoke modules' `runner` seam), on this tree:

1. `discovery._ls_tree_head`: a repo-chosen directory name as a `git ls-tree`
   pathspec after `--` (§4, the only argv row the instrument found).
2. `cli._handler_slice_filename`: a route's method and path, sanitized to
   `[a-zA-Z0-9_.-]`, as a file-name component under the user's output
   directory.
3. `sketch_embeddings._get_repo_fingerprint` / `_get_results_cache_dir`: git
   remote URL and root commit, hashed, as cache directory names.
4. `in_repo_catalogues.git_state`: `git ls-files --error-unmatch -- <rel>` and
   `git status --porcelain -- <rel>` where `rel` is an in-repo catalogue file a
   claims file's `extra_catalogs:` names — only when the user named that claims
   file or opted in (ADR-0061); NOT in the instrument's evidence because the
   path comes from the claims file, not discovery.
5. The analyser backends: `scip-python index --cwd <repo> --project-name
   <repo_root.name>` and `rust-analyzer scip <workspace>` take the user's path
   and its directory NAME; repo content steers them by CONTROL (an analysed
   repo's `.hypergumbo.toml` `[backends] scip_python = true` turns the static
   indexer on, ADR-0045 ruling 5), which no data-flow claim sees. The instrument
   reads 0 flows to `analyser_backend`, consistent with the argv reading.

Items 1–4 are each contained by a code fact in the function named; none is
contained by a claim.

## 6. V2 (the `pathlib` content anchor) is rejected

| run | claim | verdict | evidence | what the extra flows were |
|---|---|---|---|---|
| v2_prohibited | repo-content-no-network | violated | 7 | `hypergumbo_tracker.embeddings.download_model` → `urlretrieve`, `hypergumbo_tracker.sync._api_call` → `urlopen`, seeded by the tracker's own `read_text` of its config |
| v2_prohibited | repo-content-no-subprocess | violated | 161 (100 shown) | `_ls_tree_head` plus the tracker's `_maybe_auto_sync` and `sync._git` → `subprocess.run` |
| v2_prohibited | repo-content-no-dev-zone | violated | 3 | tracker `preflight_check` / `_maybe_auto_sync` |
| v2b_prohibited | the same three | violated | 7 / 10 / 3 | unchanged by the barrier: these are not wrapper internals |
| v2b_allowed | repo-content-no-user-state | violated | 2 | `repo_tier_offer.record_offer_decision` reading the offer's own answer file |

`pathlib.Path.read_text` labels every read in the shipped scope. The tracker
package is in that scope (`packages/hypergumbo-tracker/src`), has its own CLI,
and reads its own ops and config; none of those bytes is the analysed
repository. The names anchor (V1) is the one that means what the label says;
contents follow the names only where the def-use chain follows them, and the
`untyped_receiver` caveat discloses the method rows the receiver typing missed.

## What the numbers do not support

- The five shipped claims do not say repo content never chose a PATH inside a
  permitted zone. §4–§5 say that by reading, on this tree, with containment
  resting on `_sanitize_filename_part`, the hex digests, the `--` separator and
  the wrappers' resolve-and-raise — code facts a later edit can change without
  any claim moving.
- Every permitted-zone row is `ddg_mixed` (approximate) except five `precise`
  DDG rows, all of which are CONTENT positions. The instrument did not attribute
  the handler-slice filename row; it was found by reading `_emit_handler_slices`.
- The evidence cap (100 rows) hides 17 of the 117 `repo_inspection` pairs; the
  distinct sink sites (4) are complete.
- Closed-world over typed receivers (74.1% of method call sites untyped on this
  map; measurement 0031 for what typing them does).

## 7. The design decision this forces

"The analysed repository never chooses a host_fs path, a subprocess argument or
a network destination" splits in two. The ZONE half is expressible and shipped
(§3). The POSITION half is not expressible, because no part of the pipeline
carries which argument a tainted value reached: the def-use records are
`(variable, line)`, `TaintFlowFinding` records the sink primitive and symbol,
and the one argument-aware mechanism, the T9 `resource_naming` annotation
(WI-bulag), lives on `io_primitives` rows — not on the self-catalogue's
zone-sink rows, whose schema admits `module` / `functions` / `methods` / `notes` —
and was built for the inverse question (suppress a content flow that merely
names a resource), with the explicit soundness guard that a MIXED sink (name
and content, every write wrapper) can never be attributed.

The route that answers the position half: argument-position attribution in the
DDG arm — the per-language def-use extractors record the call-argument index a
use sits in; the finding carries `sink_arg_index`; zone-sink rows accept the
T9 vocabulary (`resource_naming: {names: [0], content: [1]}`); a claim gains
`prohibited_argument_role: resource`. Cost: an extractor change per DDG language
(python, javascript, typescript, go), one finding field, one schema extension,
and the annotation of ~30 wrappers. The alternative — splitting each wrapper
into a path-choosing and a content API — does not exist for a write, which takes
both. Filed as its own item; until it lands, §5 is the enumeration and it is by
reading.
