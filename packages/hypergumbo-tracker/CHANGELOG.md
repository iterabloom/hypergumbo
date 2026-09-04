<!-- SPDX-License-Identifier: MPL-2.0 -->
# Changelog — hypergumbo-tracker

All notable changes to the `hypergumbo-tracker` package are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

This package is independently versioned from the main hypergumbo tool and licensed under MPL-2.0.

## [Unreleased]

### Added

- **`tracker reconcile` runs the three steps a failed post-merge pull leaves behind, and `tracker recover` is deprecated in its favour.** When `auto-pr` merges and its post-merge pull then aborts because pending `.ops` collide with the same paths in the incoming commit, the way out was `tracker sync` -> a raw fast-forward -> `tracker recover`: two tracker commands with a plain VCS command between them, none of which reports whether the next is still needed. `reconcile` runs all three, each skipped when it is not needed, and reports what every step did or why it did nothing — a silent step is indistinguishable from an unnecessary one, which is what sends the next reader back to doing it by hand. It refuses outright while `.git/PR_PENDING` exists (an in-flight `auto-pr` moves the ops dirs around its own rebase, so the two would fight over the same files), stops rather than fast-forwarding when the flush fails (advancing on top of ops that never reached the remote is the collision it exists to prevent), and is a clean no-op on a healthy repo so it is safe to run reflexively. The flush goes through `do_sync`, which takes no threshold argument — the line-count gate lives in its caller — so no synthetic mutation is minted to trip a counter and the append-only log stays free of entries that mean nothing. Unlike `do_sync`, it deliberately does NOT set the `tracker-recover-disabled` marker around its fast-forward: the self-healing hooks should be live, because restoring ops the pull disturbs is precisely their job. `recover` remains as the primitive `reconcile` calls and as the hooks' entry point (both invoke it with stderr discarded, so the deprecation notice surfaces only for manual use), but on its own it restores the journal and leaves the repo behind the upstream — which is how the other two steps get skipped.

- **`tracker sync --prune` closes superseded `tracker-sync/*` branches, and a successful auto-sync does so automatically.** A branch is superseded only when `git merge-tree --write-tree` against dev yields dev's own tree — its ops already landed — never merely because the forge reports a conflict, since a branch holding unique ops conflicts for exactly the same reason as one holding none. Every undecidable case (git older than 2.38, a missing base ref, a blank base tree) yields an empty list rather than a guess. `--prune` runs before preflight, so it works precisely when the ops have already landed, and combines with `--dry-run`; a failing prune can neither damage a sync that already merged nor be counted as a sync failure.

### Fixed

- **Nine `tracker-sync/*` PRs sat unmergeable, the oldest since August 24, and every one of them was empty.** `.gitattributes` declares `merge=union` on `.ops`, but that is a client-side driver: the forge's server-side merge does a plain three-way merge, so two syncs appending to the same journal from a common base conflict there. Nothing was lost — `do_sync` gates its cleanup on merge success, so each later sync carried the stranded ops forward — and both a per-file set difference and `git merge-tree` confirmed all nine contributed zero lines to dev. The prune above is the remedy.
- **The modal test harness gave up silently, and one tracker TUI test then masked three unrelated CI gates for a day and a half.** `_wait_for_modal` polled up to 30 event-loop ticks and then returned as though it had succeeded, so callers drove a DOM that was not ready and failed later on a result assertion naming neither the widget nor the wait; because Woodpecker collapses a workflow's steps into one aggregate commit status, the red 2026-08-20 full-suite run made `self-claims-gate`, `self-tree-validation` and `test-agent-infra` unreadable alongside it. The helper now raises, naming what it waited for and for how long, and waits for layout rather than DOM presence — `pilot.click` synthesises a mouse event at the widget's region, so an un-laid-out widget's click lands nowhere.

## [0.8.0] - 2026-08-20

### Added

- **`tracker textconv <file>` is a real subcommand.** The `.gitattributes` diff driver had named a `textconv` subcommand that was never built — only the `hypergumbo-tracker-textconv` console script existed — so the configured driver aborted every patch-producing git walk. Both surfaces now share one `_render_textconv` body. See the Fixed entry below for the defect this closes.

### Removed

- **`setup.py`'s `git config --global` fallback for the diff driver.** A per-repo textconv driver was being leaked into the user's machine-wide git config whenever the local config was not writable. Setup now emits a warning naming the exact local command to run instead.

### Fixed

- **The git textconv driver actually works: `git log -p` / `git diff` / `git blame` no longer exit 128 on `.ops` paths (ADR-0013).** Two `.gitattributes` families declared *different* diff drivers for the same files, and git resolves attributes most-specific-first, so the winning declaration named a `textconv` subcommand that had never been built — only the `hypergumbo-tracker-textconv` console script existed. Because git aborts any diff whose textconv driver writes to stderr, argparse's "invalid choice" usage dump turned every patch-producing walk into exit 128, silently truncating history-walking tools: a full-history secret scan stopped after 1 commit / 3.66 KB and still reported success. `textconv` is now a genuine subcommand sharing one `_render_textconv` body with the console script, all three declaration sites use the single name `diff=tracker`, and `tracker setup` **executes** the configured driver against a real op log rather than only checking that the config key was set.
- **An un-compilable op log degrades to a stdout marker instead of breaking history for the whole repo.** An op log whose `create` op lives in the other tier's file — normal history after an item is promoted or demoted, not corruption — was treated as fatal and written to stderr, so a single such blob anywhere in history aborted `git log -p` repo-wide. Un-compilable content now renders a `# <id>: op log not compilable at this revision — <reason>` marker on **stdout** at exit 0; only a genuinely missing file stays an error. A full-history scan now completes **6,241 commits / 66.18 MB** where it previously stopped at 1. Known cost, not addressed: textconv spawns an interpreter per blob, so a whole-history `git log -p` takes ~15 minutes.
- **Textconv tests no longer mock away the only thing that could fail.** Every `_check_textconv` test mocked the subprocess while asserting the *broken* invocation was the correct configured value, encoding the defect as expected behavior at 100% coverage. The suite now splits the concerns: `test_textconv_git_integration.py` drives real `git`, with a positive control that fails on a stderr-writing driver, and `test_textconv_wiring.py` covers the same branches in-process, since subprocess tests contribute no coverage.
- **The near-duplicate warning suggested two flags that do not exist, sending every agent who followed it to `unrecognized arguments`.** On `add`, a SimHash match recommended `update <ID> --duplicate-of <ID>` and `--not-duplicate-of`; `update` accepts `--add-duplicate-of` and `--add-not-duplicate-of`. This is the *only* place most agents learn how to resolve a near-duplicate, so the warning was self-defeating — and it had been hit twice and worked around rather than fixed. The existing test matched the message against `"similar"`, which cannot catch a wrong flag name; the new guard extracts every `--flag` token from the emitted warning and asserts each is a real option of the live `update` subparser. ADR-0013 carried the same two non-existent flags in its illustrative warning and command table; both are corrected to match what is actually emitted.
- **ADR-0013's SimHash thresholds no longer contradict the code.** The ADR stated the store warns at 13 bits while validation uses 8. Both are 8 and are pinned equal by `test_simhash_threshold_is_unified_with_validation`; the 13-bit create-time value was tightened to cut false positives on unrelated items sharing common tracker vocabulary.

## [0.7.0] - 2026-07-27

### Added

- **Out-of-repo write-ahead journal makes pending ops durable; new `tracker recover` command.** Every tracker op is mirror-appended to an out-of-repo journal git cannot see, and `tracker recover` union-restores journalled ops back into the worktree — closing the op-durability class structurally instead of per-path whack-a-mole.

### Changed

- **Single canonical op-block `.ops` restore primitive shared by `recover` and `do_sync`.** Both now share one op-block-granularity union primitive (`journal._union_op_blocks`), deleting `do_sync`'s duplicate private `_union_lines`; deduping whole ops rather than lines so one implementation serves both restorers.

#### Codeberg → GitHub migration

The tracker's auto-sync gained a GitHub write path (dormant dual-mode), now live after the cutover — auto-sync targets GitHub, with the Forgejo/AGit path byte-identical for rollback.

- **`_poll_ci` is forge-backend-aware.** A `backend` parameter (defaulting to `forgejo`) makes `_poll_ci` read a status element's state under GitHub's `state` key or Forgejo's `status` key and treat GitHub's single combined commit-status — which stays `pending` for the whole build — as "started" rather than firing the Forgejo multi-job stale-pending recovery.
- **`sync.py` resolves the forge backend and threads it through preflight → `_poll_ci`.** `_detect_api_base` gains a `github.com` arm (`https://api.github.com/repos/...`), `PreflightResult` carries a `backend` field (forced `forgejo` under CI failover), and `_api_call` adds GitHub's `Accept` + pinned `X-GitHub-Api-Version` headers, so the single-status fix activates automatically when `origin` re-points to GitHub.
- **`do_sync`'s write path is GitHub-aware.** On the github backend `do_sync` pushes a normal branch (no AGit push options), opens the PR via `POST /pulls` (idempotent on a 422-already-exists), merges via `PUT /pulls/{n}/merge` with `{merge_method: rebase}`, and deletes the server-side head branch. The local post-sync cleanup is unchanged and backend-agnostic — local `dev` never leaves `base`, so `origin/dev` is always a fast-forward even after GitHub's rebase-merge rewrites the SHA. The Forgejo/AGit path is byte-identical.

### Fixed

- **`sync.py` preflight resolves the GitHub credential for the github backend (dormant).** The dormant github write path authenticated with `preflight.forgejo_token` (populated only from `FORGEJO_TOKEN`), so after cutover it would have presented the Codeberg token to `github.com` and 401'd on the first sync. `preflight_check` now also resolves `HG_GITHUB_TOKEN` and selects the credential by backend (github → `HG_GITHUB_TOKEN`, forgejo → `FORGEJO_TOKEN`), failing preflight with a clear message when a github origin has no PAT.

- **Bump `starlette` floor to `>=1.3.1` (CVE-2026-54282, CVE-2026-54283).** The `>=1.0` constraint resolved to a vulnerable 1.2.1; 1.3.1 patches both starlette advisories. starlette is the tracker's remote/federation transport dependency (ADR-0019/ADR-0021).
- **`recover` serializes its `.ops` rewrite with concurrent Store appends via a shared lock.** `recover`'s per-file rewrite now takes the same `LOCK_EX` as Store appends around read/union/rewrite and rewrites in place, fixing a race where a concurrent append landing mid-recover was clobbered (or left a torn `.ops`); exposure rose after the hook made recover fire on every committed ref transaction.
- **`scripts/tracker`'s `python -m cli` fallback now dispatches instead of silently no-opping.** `cli.py` lacked an `__main__` guard, so `python -m hypergumbo_tracker.cli` exited 0 without dispatching, silently breaking every tracker command (including the recovery hook) in environments using that fallback path.
- **`do_sync` suppresses the recovery hook during its own git operations.** The auto-firing `reference-transaction` hook restored journalled-uncommitted ops as untracked files mid-sync, aborting the very `merge --ff-only` that reconciles them so local dev perpetually lagged the remote; `do_sync` now sets a `tracker-recover-disabled` marker around all its git ops (user-initiated reset/checkout still self-heal).
- **Tracker sync stages ops additively, never committing deletion of op-logs absent from a stale tree.** A plain `git add` reconciled the index to the working tree, staging a deletion for any op-log present in the seeded base but missing from a stale/incomplete tree, so a sync while local dev was behind silently committed item removals; both staging sites now route through a shared helper passing `--ignore-removal`.
- **Auto-sync cleanup no longer destroys ops mutations written during the sync window.** `do_sync`'s post-merge cleanup reset tracked ops to HEAD and unlinked untracked ops before the ff-merge, dropping any mutation written during the push+CI+merge window; the fix snapshots each ops file's content before the destructive steps and union-restores it after the ff-only merge.
- **Op-compilation tolerates explicit-null `set`/`add`/`remove` dicts instead of crashing.** `compile_ops` and the lock-violation scan used `dict.get(k, {})`, which returns None (not the default) for an explicit null, so an op with `remove: null` crashed whole-corpus reads with AttributeError; both now use `.get(k) or {}`, treating null as no changes.

## [0.6.0] - 2026-06-10

### Added

- **Per-message edit-mode for tracker discussions.** Three new ops (`delete-msg`, `undelete-msg`, `edit-msg-text`) tombstone, restore, or rewrite individual discussion entries by their 4-char nonce. Authorization is window-based: a human-only `edit-mode-on` op opens a window (default 30m, max 60m, capped at 500 mutation ops); ops outside any valid window are filtered at compile time, not removed from disk. A defense-in-depth OS-permission gate backs the window: the edit-mode log lives in a dedicated human-owned subdirectory, and agent-owned log files have every op filtered (preserved on disk for forensics). CLI: `tracker edit-mode {on|off|status}` (human only for on/off), `delete-msg`, `undelete-msg`, `edit-msg-text`. `tracker show` suppresses tombstones by default with a `(not shown: N deleted messages)` footer; `--include-deleted` and `--include-history` reveal deleted entries and prior text revisions. The TUI gains a `Ctrl-E` toggle and a live countdown banner.

- **Optional `dogfood_anon_id` field on the invariant schema.** New text field in the tracker config template's invariant `fields_schema`, acting as a join key linking a tracker item back to its entry in a self-analysis-dogfooding blind-reassessment corpus. Optional (no `required: true`); existing items validate unchanged. Items carrying the field now render cleanly in `tracker show` / TUI instead of producing an "unknown field" warning.

### Changed

- **Tracker auto-sync PR titles are now distinct and descriptive.** The previous `tracker: sync N file(s)` template made consecutive syncs near-identical, which a forge-side title-similarity check could flag as duplicate submissions — rejecting the PR and opening the auto-sync circuit breaker. Titles now read e.g. `tracker: 6 files (142 ops) — <top item IDs>` (most-touched IDs capped at 3, then `+N more`). The branch-ref-based orphan-sync-PR detection is unaffected — it matches on `head.ref`, not the title.

- **TUI activity-pane message-prefix styling.** Each discussion entry's `<ts> [by]:` prefix is now rendered bold-and-underlined so the boundary between adjacent messages is visually unmistakable. Body text is unstyled as before; no other format change.

### Fixed

- **TUI activity + detail panes now suppress tombstoned discussion entries.** The CLI `tracker show` rendering filtered tombstones since phase 1, but the two TUI rendering sites (`_format_activity_lines` for the wide-mode activity pane, `_format_detail_lines` for compact/standard detail panes) kept printing the deleted text — so after a successful `delete-msg`, the TUI looked unchanged. Both renderers now skip `is_tombstoned` entries, recompute the visible count for the header, and append `(not shown: N deleted messages)` when N > 0. Special case: when every entry is tombstoned, the activity pane shows only the footer instead of `"No recent activity"`.

- **Edit-mode log relocated out of the sync zone.** The original log path lived inside `.ops/`, which auto-sync sweeps — so the human-owned log got committed to dev by one sync, and the next sync's cleanup failed with EACCES trying to unlink a file the OS-permission gate (correctly) makes agent-undeletable. The log now lives as a sibling of `.ops/`, outside every sync code path, and both the new and legacy locations are gitignored; the two accidentally-committed files are `git rm --cached`'d. Users with an active edit-mode window from the old path will need to `tracker edit-mode on` once to seed the new location.

- **Tag-name validation now applies uniformly across every tag-write path.** The CLI validated tag names against the `^[a-z_][a-z0-9_]*$` regex, but the TUI and `serve.py` bypassed validation entirely. Tags with hyphens could be persisted via the TUI even though the catalog would refuse them. Validation is now centralized in `trackerset.add()` and `trackerset.update()`. `remove_fields["tags"]` is exempt so historically-bad tags remain cleanable.

- **`tracker delete` now sweeps dangling `isbefore` references.** A deleted item's `isbefore` list and any inbound holders kept the now-dangling ID. `tracker show` / `tracker deps` treated the deleted ID as a real dependency. Delete now clears outbound and inbound `isbefore` references in the same op batch.

- **`tracker tags rename` no longer rejects renames FROM legacy-format tags.** Renaming a hyphenated tag (e.g., `for-deep-bakeoff` → `for_deep_bakeoff`) succeeded per-item but left a regex-violating key in the catalog, causing the save to fail. Legacy-format source tags are now dropped from the catalog cleanly.

- **TUI `tui_preferences.json` writes now atomic; `human_read_state` no longer wiped on `_move_selected`.** A 2026-05-31 incident silently lost ~800 `human_read_state` entries. Two related causes: `_move_selected` took a legacy v1-schema save path that omits `human_read_state`, and saves used non-atomic `write_text()`, which could leave a truncated file under concurrent activity. Saves now pass full v2 kwargs and write via tempfile + `fsync` + `os.replace`. Data lost in the original incident is not recoverable.

## [0.5.1] - 2026-05-08

### Fixed

- **Auto-sync no longer wipes local ops files when sync fails:** the cleanup in `do_sync()`'s `finally` block (`git checkout HEAD -- .ops/` reset and `unlink()` of untracked ops files) was running unconditionally on every sync attempt, regardless of whether the PR had actually been merged. When push, CI, or merge failed, the cleanup silently dropped every mutation in the in-flight batch — a regression that caused 4 of 17 boundary-adjacent ops to vanish during a Codeberg outage on 2026-05-03 (3 UPDATE ops + 1 silent ADD-loss). Cleanup is now gated on a `merge_succeeded` flag set only after a successful merge, so failed syncs leave the working tree intact for the next attempt.

## [0.5.0] - 2026-04-29

### Added

- **`tracker tags` enumeration and lifecycle subcommand**: catalog-backed tag management. `tracker tags` lists every tag in use; `--count` shows `tag<TAB>count<TAB>status` rows sorted by count desc then alpha; `--json` emits per-tag records (count, status, description, created_on, last_modified, last_used, deprecated, in_favor_of). Three editorial verbs: `tags rename OLD NEW` rewrites every item's tags list (idempotent, de-duplicates when both names coexist); `tags describe TAG [TEXT]` round-trips a single-line description; `tags deprecate TAG [--in-favor-of NEW]` flips the deprecation flag and records the canonical replacement. External statuses (`active` / `inactive` / `deprecated`) are computed at read time from `(count, deprecated_flag)`, so write paths never have to keep a status field in sync. `tracker add --tag <deprecated>` / `update --add-tag <deprecated>` emit a non-blocking stderr warning that mentions `in_favor_of`. Catalog file is `.agent/tracker/tag_catalog.yaml`, populated lazily on first invocation by walking the op log to backfill `created_on` / `last_used` for every tag currently in use.
- **Forensic race log** for transient `.ops` read failures: a new `race_log` module captures every `PermissionError` / `FileNotFoundError` (timestamp, pid, euid, stat snapshot, attempt number) to `~/.cache/hypergumbo/tracker/<root-fingerprint>/race_log.jsonl` (honors `XDG_CACHE_HOME`). Path surfaced in `tui_preferences.json` as `race_log_path`.

### Changed

- **Auto-sync line threshold doubled from 40 to 80**: `_maybe_auto_sync` was firing too frequently in dense work sessions, creating a tracker-sync PR every ~5 mutations. Lifting the threshold reduces syncs-per-session without delaying push-to-origin meaningfully — every `auto-pr` run flushes the queue regardless.

### Fixed

#### Sync gate self-recovery

- **`.git/TRACKER_SYNC_PENDING` is now an OS-managed flock, not a marker file**: previously cleanup ran only in `do_sync`'s `finally` block, which a SIGKILL bypasses, so the marker leaked across crashes and silently blocked every subsequent `auto-pr` until manual `rm`. Replaced with `SyncGate` — opens the file, takes `fcntl.flock(LOCK_EX | LOCK_NB)`, writes `pid` / `started` / `pr` into the body for diagnostics. The OS releases the flock unconditionally on process exit (SIGKILL, segfault, power loss), so the lock can never leak.
- **Non-destructive callers (preflight, auto-sync, bash `auto-pr`) inspect via `LOCK_SH | LOCK_NB`**: on success the file is stale and is silently auto-removed; on `BlockingIOError` the body is parsed and a friendly diagnostic is returned. The `scripts/auto-pr` queue-flush, PR preflight, and post-`PR_PENDING` re-check all route through one shared bash helper.
- **Friendly, actionable holder messages** replace the prior `Error: tracker sync in progress. Wait for htrac sync to complete.` one-liner. New shape: `tracker sync gate locked by PID 12345 (alive, started 2m ago) working on PR #3392. Your operation will be retried automatically when the sync completes.` (or, if the PID is dead: `… (DEAD, started 1h17m ago). Stale lock — the OS will release it on the next acquire attempt.`).

#### Cross-tier reference resolution

- **`tracker validate` now matches CI's view by default**: the cross-file reference checks split into two index scopes by writer tier. Canonical and workspace items must resolve refs in canonical ∪ workspace (the CI-visible set, since `tracker-workspace/stealth/` is gitignored); stealth items resolve in the full canonical ∪ workspace ∪ stealth index. Reproduces locally a CI failure pattern where workspace items pointing at stealth-tier ids passed `tracker validate` only to fail in CI.
- **All four ref-typed fields are now checked** for dangling/cross-tier issues: `parent`, `isbefore`, `duplicate_of`, `not_duplicate_of`. Previously only `parent` was checked for dangling references; `isbefore` was checked only for cycles; the two duplicate fields weren't validated at all. Two error classes: `dangling <field> reference` (target absent in every tier) and `cross-tier <field> reference … from <tier> to stealth`.
- **CLI write-time guard** for `tracker add` / `tracker update`: `_resolve_ref` accepts a `writer_tier` argument and refuses to resolve a reference whose target is in stealth when the writer is canonical or workspace. Threaded through every flag that creates a ref. Removal flags skip the check — narrowing a ref set can never create a cross-tier link.
- **Three pre-existing latent dangling refs cleaned up** as a side-effect of tightening ref resolution.

#### Transient `.ops` read-race hardening

- **`_parse_ops_file` retries transient read races**: a long-running TUI crashed with `PermissionError [Errno 13]` on a mode-0o664 ops file owned by the reader — narrow window where `stat()` saw the old inode but `open()` landed on a new one written by a concurrent atomic-rename writer. Capped exponential backoff up to 6 attempts × ~0.55 s; every retry and final re-raise logged to the race log. Budget was bumped from 3 attempts × ~0.06 s after a `git checkout` during auto-sync outlasted the original window and re-crashed the TUI.
- **`_compile_all` widens its except to `(CorruptFileError, OSError)`**: a single unreadable ops file no longer poisons the entire compile — the bad file is skipped and the rest of the tier still renders.
- **TUI's periodic `_check_external_writes` catches `OSError`**: a transient FS hiccup no longer kills the Textual event loop. Persistent errors surface on the next user action; the race log retains the detail.
- **`TrackerSet.exists(item_id)`**: lightweight stat-only existence check used by the per-cursor-move hotspot resolver in the TUI, which previously parsed the full ops file on every cursor move. Cannot race an atomic-rename writer's brief 600-mode tmpfile window because it only stats `item_path`.

## [0.4.0] - 2026-04-21

### Added

- **TUI integration of the item-nav modal:** Description and Activity panes detect embedded item IDs and render them as `[@click=jump_to_item(...)]` hotspots; clicking pushes `ItemNavModal` onto the screen stack. `TrackerApp` compiles the ID pattern at construction via `build_item_id_pattern(tracker_set.config)`, uses `_item_exists` as resolver (treats `ItemNotFoundError` / `AmbiguousPrefixError` as absent so dead/ambiguous IDs render as plain text), and wires `_apply_nav_hotspots` into `_show_detail`, `_show_std_detail`, `_show_activity`. Defensive against stale IDs from items deleted between render and click.
- **`format_nav_indicator` helper for the item-nav modal header:** `nav_history.format_nav_indicator(history, *, empty_label="(empty)")` returns `"[N/M] item-id"` (1-indexed cursor / total) for the modal header. Returns *empty_label* when history is empty.
- **`nav_history` module: browser-style back/forward stack for the item-nav modal:** pure-Python `NavigationHistory` dataclass with `current()`, `push(item_id)` (truncates forward history, treats same-ID as no-op double-click), `back()` / `forward()` (no-op at boundaries), `can_go_back` / `can_go_forward`, `depth()`, `position()`. Deliberately not persistent and unbounded. No Textual imports.
- **`id_matching` module: detect tracker item IDs in free text:** `build_item_id_pattern(config)` compiles a regex from a `TrackerConfig`'s kind prefixes; `find_item_ids(text, pattern, skip_ranges=...)` enumerates non-overlapping matches and drops any span intersecting a skip range. Pattern shape `<prefix>-<proquint>(-<proquint>)+` with CVCVC proquints (consonants `bcdfghjklmnpqrstvz`, vowels `aeiou`), requiring ≥ 2 syllables so the detector cannot collide with English word fragments. Word-boundary via `(?<![A-Za-z0-9])` / `(?![A-Za-z0-9])`.
- **Stop-hook `[[preface]]` escalation + LLM precedence gate**: REPLY-FIRST CYCLE now recognises a `[[tag]]` preface at char 0 of an unread human message and, when other unreads exist, consults an LLM via OpenRouter to decide whether the tagged message takes precedence. Preface regex is strict (anchored, no whitespace, no nesting); meaning is decided by the gate, not the regex. Fail-soft: missing `OPENROUTER_API_KEY`, network errors, malformed responses, or non-affirmative answers all fall back to equal-priority listing. Skipped entirely on single-unread (nothing to take precedence over).

### Changed

- **Stop-hook REPLY-FIRST CYCLE branch when reply debt exists**: `stop_hook.py::generate_guidance` branches on any unread human messages and emits a REPLY-FIRST CYCLE guidance document instead of the default TODO listing. Hard/violated/soft sections suppressed, replaced with a 4-step protocol (`tracker show` → classify → plan investigation → `tracker discuss`) and a per-item list of blocking + non-blocking unreads. Vendor stop hooks (`claude-code/stop.sh`, `cursor/stop.sh`, `codex-cli/notify.sh`, `gemini-cli/after-agent.sh`) read a new `UNREAD_COUNT` export from `stop_logic.sh` and switch the one-line reason text to "REPLY-FIRST CYCLE: N unread human message(s)…" when `UNREAD_COUNT > 0`. Changes the agent's incentive structure rather than adding more text.
- **Renamed `before` field to `isbefore`**: the dependency-link field is now `isbefore` (`X.isbefore = [Y]` → "X is before Y"). CLI flags renamed: `--isbefore`, `--add-isbefore`, `--remove-isbefore`. Old ops files using `before` are read transparently; new writes use `isbefore`. Prevents the directional confusion that caused the TUI inversion bug (PR #2981).

### Fixed

- **`_config_chmod_fallback` uses same-directory tempfile + atomic `os.rename`**: the cross-user `config.yaml` lock/unlock fallback in `setup.py` had the same bug as `_take_ownership_via_tmp` — `tempfile.mkstemp()` defaulted to `/tmp`, then `shutil.move` fell into its cross-filesystem `copy2 + unlink` fallback, exposing a window where `config.yaml` was briefly absent. Every `scripts/tracker` invocation and TUI startup reads `config.yaml`, so the race was just rarer. Fix: pass `dir=str(path.parent)` to `mkstemp` and replace `unlink + shutil.move` with a single `os.rename(tmp, path)`; tempfile prefix `.htrac_config_` for dotfile hygiene on cleanup failure.
- **`_take_ownership_via_tmp` uses same-directory tempfile + atomic `os.rename`**: the cross-user ownership-transfer path for ops files used `tempfile.mkstemp()` default `/tmp` followed by `shutil.move`. On Linux `/tmp` is typically a separate tmpfs, so `shutil.move` degraded to cross-filesystem `copy2 + unlink` — within that sequence the target dentry was briefly absent / held umask-respecting mode. Live-update readers (notably the TUI's inotify watcher) raced that window; first confirmed on 2026-04-19 10:24 when the TUI crashed with `PermissionError` on a file that was mode 0o664 at rest. Fix: pass `dir=str(filepath.parent)` to `mkstemp` and replace `unlink + shutil.move` with `os.rename(tmp, filepath)` (single-syscall atomic replace on a single filesystem).
- **SimHash similarity-warning threshold unified at 8 bits**: `store._SIMHASH_THRESHOLD` was 13 (create-time warning) while `validation._SIMHASH_THRESHOLD` was 8 (validation-pass check) — accidental drift. The looser create-time threshold fired 5+ false-positive warnings per new item against unrelated items sharing common domain vocabulary (e.g. "tracker", "stop", "hook"). Tightened store to 8; validation imports the constant from store.
- **`discuss` gate-message shows the working `--ack-thread` arg order**: the re-run hint now reads `tracker discuss <id> "<your reply>" --ack-thread` instead of `--ack-thread "<your reply>"`. Argparse's subparser parsing rejects the flag-before-positional form (an `nargs="?"` positional with a following optional flag won't accept the message after the flag). Cosmetic but high-leverage — agents and humans were following the broken example shown by the gate itself. Regression check added.

## [0.3.0] - 2026-04-12

### Added

- **TUI background reload on external writes**: `htrac tui` polls ops directory mtimes every `HTRAC_RELOAD_INTERVAL` seconds (default 5.0) and auto-refreshes when another process modifies tracker state. Preserves cursor position, scroll offset, filter text, and in-flight chat input across reloads. Deferred while modal screens are open. Also fixes a latent bug where scroll position reset to top after any TUI-initiated write.
- **Self-contained `check-messages` output**: now includes item description, prior discussion entries (last 3), and `pr_ref`. Eliminates follow-up `tracker show` calls.
- **`--add-field` and `--remove-field` for partial updates**: merge into or delete from existing fields without erasing other keys. Fixes data-loss footgun where `--field key=value` silently erased all other fields.
- **`--description-file` and `--description-stdin`**: read description text from a file or stdin, avoiding shell quoting issues.
- **`list --status` repeatable + `--open` shortcut**: `--status` accepts multiple values. `--open` expands to the five open statuses.
- **`discuss <id>` view mode**: calling `discuss` with no message prints the thread chronologically. `--json` for structured output.
- **`--ack-thread` gate for `discuss`**: when the last entry is from a human, `discuss` exits non-zero unless `--ack-thread` is provided. Prevents drive-by agent replies.

### Fixed

- **`cache-rebuild` writes to XDG cache dir, not legacy path**: `_cmd_cache_rebuild` hardcoded the pre-ADR-0013 location. Fix: reuse `_get_cache_dir()` instances.
- **TUI displayed `before` field with inverted direction**: `_build_dep_index` swapped blockers and dependents, so every detail panel, chain summary, and dep pill rendered arrows upside down. Fix: populate under CLI semantics — `item.isbefore` renders as "Blocks:", not "Blocked by:". (Field was later renamed from `before` to `isbefore`.)
- **`_maybe_auto_sync` guarded against `tracker_root` outside `repo_root`**: auto-sync derived `repo_root` from cwd instead of the parameter, causing test hangs when cwd had pending ops and `PR_PENDING`. Fix: verify containment and return early if not.
- **Fold-induced nonce injection in YAML serializer**: `_serialize_op` now sets `ry.width = sys.maxsize` to prevent ruamel.yaml from folding long scalars, which embedded nonce comments inside field values on read-back.
- **TUI crash on bracketed text in user content**: Rich's markup parser interpreted literal `[` in titles/tags/discussions as style tags. Fix: `_escape_user` helper escapes `[` to `\[` in user-controlled text.

## [0.2.0] - 2026-04-04

### Added

#### TUI screenshot annotation mode (ADR-0020)

- **Annotation overlay**: Press `S` to capture a screenshot, then annotate with rectangles (`R` + drag), arrows (`A` + drag), and numbered text labels (`L` + click). Arrow key nudge (±1 cell) mitigates SSH mouse coordinate drift. Annotations injected as SVG elements; label text XML-sanitized.
- **Inline SVG preview**: Discussion entries referencing `.svg` files show placeholders. SVG→PNG→ANSI pipeline via optional `cairosvg` + `chafa` with graceful degradation.
- **Label UX redesign**: Replaced raw keystroke capture with Textual `Input` widget for full editing support. Numbered markers provide visual feedback before text entry.

#### htrac serve and web frontend (ADR-0019 Part A)

- **`htrac serve` command**: Starlette/uvicorn server bound to 127.0.0.1:7380. REST API (`/api/items`, `/api/ready`, create/update/discuss endpoints) and WebSocket (`/ws`) protocol for real-time state sync. Same TrackerSet engine as CLI/TUI. PID file management for `--background`/`--stop`/`--status`.
- **Auth stack**: WebAuthn/FIDO2 hardware key auth (ES256, RS256). Bcrypt password verification with timing-safe dual-check (real + duress). Per-credential rate limiting with exponential backoff. In-memory session store with configurable TTL. `DuressHandler` protocol for user-defined duress behavior. `AuthConfig` from `config.yaml`.
- **Filesystem watcher**: `watchfiles` detects CLI/TUI ops file changes and auto-broadcasts state to all WebSocket clients. Sub-second latency, no IPC.
- **Web frontend**: Vite + TypeScript + BlockSuite in `packages/htrac-frontend/`. Lit web components for tracker item list and detail views with responsive two-panel layout. Service worker for cache-first Tor-friendly loading. WebSocket client with exponential backoff reconnect.

#### Governance

- **`deprecated_statuses`**: `KindConfig` now supports statuses accepted when reading historical ops but rejected on new create/update. Enables status renames without rewriting append-only ops.
- **Invariant statuses**: `satisfied` (confirmed with positive evidence) and `pending_validation` (fix deployed, not yet validated — blocks stopping). Replaces ambiguous `holding`.

#### CLI enhancements

- **Batch command** (`tracker batch`), **inverse blocking flags** (`--add-blocked-by`), **dependency graph view** (`tracker deps`).
- **Verbose update confirmations**, **setup wizard improvements**, **auto-create config template**.
- **`update --note`** shorthand for discuss, and unread message warning.
- **TUI startup diagnostics**: Prints item count, per-tier breakdown, and load time before and after the TUI (e.g., `htrac: 298 items (12 canonical, 286 workspace) loaded in 0.43s`).
- **Sync logging**: Always-on file logging in `.agent/.sync-logs/` with 30-day garbage collection.

#### Other

- `ORT_ENABLE_EXTENDED` fixes `SimplifiedLayerNormFusion` bug with quantized ModernBERT.
- Stale-pending detection in sync: 90-second timeout, close/wait/repush with up to 2 retries.
- Sync PR verification: Stop hook's stale-PR audit calls `verify-tracker-pr` to check safety before recommending close.
- Gate timing race: `PR_PENDING` gate now created before push (was after), closing a window where sync could advance dev mid-flight.
- `pending_sync_lines` failover: Now checks `.git/CI_FAILOVER_ACTIVE` and prefers `selfh/dev` as diff base.
- Release scripts now flush pending tracker ops before clean-tree check.
- Deprecated invariant ledger removed: Superseded by structured tracker (ADR-0013).

### Fixed

- Duplicate startup message on TUI exit suppressed. Auto-sync no longer fires on exit without mutations.
- Screenshot path resolved against tracker root instead of cwd.
- Per-user fallback directory (`/tmp/<uid>-htrac-screenshots/`) for screenshot permission errors.
- SVG cell geometry extracted from actual SVG instead of hardcoded 8.65×18px. Background rect (height=1707px) filtered.
- Screen-absolute coordinates for annotation placement (was widget-relative).
- Merged-render annotation canvas replays frozen `Strip` objects from compositor.
- Short prefix resolution: `--remove-before`, `--remove-duplicate-of`, `--remove-not-duplicate-of` now resolve short ID prefixes.
- Sync pending-line inflation: count grew by ~22 per cycle instead of resetting to 0. Fixed by fast-forwarding local dev after sync.
- Sync cleanup: `unlink()` OSError no longer halts cleanup; checkout/merge failures now logged.

### Documentation

- **ADR-0019** (Remote Access Transport): Part A/B split. Revised to native iOS/macOS app (Safari lacks secure context for `.onion` origins). TURN relay fallback for symmetric NAT.
- **ADR-0020** (TUI Screenshot Annotation): Proposed.
- **ADR-0021** (Tracker Federation): Proposed. "Node" terminology replaced with "machine" in prose.
- **Deployment guide** (`docs/DEPLOYMENT.md`): Architecture diagram, systemd setup, Tor onion service config, auth, CLI coexistence.
- **Torrc example** (`deploy/torrc.example`): v3 onion service with x25519 client authorization.

## [0.1.0] - 2026-03-01

### Added

- **Append-only op-log storage**: YAML-backed structured tracker with nonce-on-every-line for git merge=union safety.
- **Content-hash IDs**: Proquint-encoded SHA-256 IDs for natural deduplication.
- **Lamport clock**: Cross-branch causal ordering via git cat-file --batch peek.
- **SimHash near-duplicate detection**: Embedding-based near-duplicate detection on `add()`.
- **SQLite read cache**: Per-tier incremental invalidation, write-through on mutations, corruption recovery.
- **TrackerSet**: Multi-tier unified view merging canonical, workspace, and stealth Stores with write routing, tier movement, and reconciliation.
- **Positional alias persistence**: Short aliases for tracker items survive across sessions.
- **Auto-sync**: Automatic commit/push/poll/merge when pending ops exceed threshold (40 lines).
- **Lock enforcement**: Human-locked fields reject agent writes, enforced cross-branch via Lamport peek.
- **CLI**: `scripts/tracker` shell wrapper with add, update, discuss, show, list, ready, count-todos, check-messages subcommands.
- **TUI**: Textual 7.x interface with three responsive layouts, 8 write-mode dialogs, SVG snapshot tests, unread discussion indicator.
- **Quick-start guide**: `docs/tracker-quickstart.md`.
