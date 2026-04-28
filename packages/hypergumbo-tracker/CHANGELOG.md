<!-- SPDX-License-Identifier: MPL-2.0 -->
# Changelog — hypergumbo-tracker

All notable changes to the `hypergumbo-tracker` package are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

This package is independently versioned from the main hypergumbo tool and licensed under MPL-2.0.

## [Unreleased]

### Changed

- **Auto-sync line threshold doubled from 40 to 80**: `_maybe_auto_sync` was firing too frequently in dense work sessions, creating a tracker-sync PR every ~5 mutations and adding queue churn. Lifting the threshold reduces the syncs-per-session count without delaying push-to-origin meaningfully (every `auto-pr` run flushes the queue regardless). AGENTS.md and `tracker test_cli` updated to match.

### Added

- **`tracker tags` enumeration / lifecycle subcommand (WI-lifal)**: catalog-backed tag management. `tracker tags` lists every tag in use (alphabetical), `tracker tags --count` shows `tag<TAB>count<TAB>status` rows sorted by count desc then alpha, `tracker tags --json` emits the full per-tag record (count, status, description, created_on, last_modified, last_used, deprecated, in_favor_of). Three editorial verbs: `tags rename OLD NEW` rewrites every item's tags list (idempotent, de-duplicates when both names coexist), `tags describe TAG [TEXT]` round-trips a single-line description, `tags deprecate TAG [--in-favor-of NEW]` flips the deprecation flag and records the canonical replacement. Three external statuses (`active` / `inactive` / `deprecated`) computed at read time from `(count, deprecated_flag)` — derived rather than stored, so `add` / `update` / `rename` paths never have to keep a status field in sync. `tracker add --tag <deprecated>` and `update --add-tag <deprecated>` emit a non-blocking stderr warning that mentions `in_favor_of` (mirrors the existing `deprecated_statuses` precedent for kind statuses; see the `holding` example in `AGENTS.md`). Catalog file is `.agent/tracker/tag_catalog.yaml` (sibling of `config.yaml`), populated lazily on first `tracker tags` invocation by walking the op log to backfill `created_on` / `last_used` for every tag currently in use — migration cost paid once. Maintenance hooks in `_cmd_add` and `_cmd_update` keep `last_used` current on every tag-touching mutation.
- **Forensic race log** for transient `.ops` read failures: new `race_log` module captures every `PermissionError` / `FileNotFoundError` (timestamp, pid, euid, stat snapshot, attempt number) to `~/.cache/hypergumbo/tracker/<root-fingerprint>/race_log.jsonl` (honors `XDG_CACHE_HOME`). Path surfaced in `tui_preferences.json` as `race_log_path`. Wired by `TrackerSet.__init__`.

### Fixed

#### Sync gate self-recovery (WI-nutin)

- **`.git/TRACKER_SYNC_PENDING` is now an OS-managed flock, not a marker file**: previously the file's existence meant "sync in progress"; cleanup ran only in `do_sync`'s `finally` block, which a SIGKILL bypasses, so the marker leaked across crashes and silently blocked every subsequent `auto-pr` until manual `rm`. Replaced with `SyncGate` — opens the file, takes `fcntl.flock(LOCK_EX | LOCK_NB)`, writes `pid=…\nstarted=…\npr=…` into the body for diagnostics. The OS releases the flock unconditionally on process exit (SIGKILL, segfault, power loss), so the lock can never leak and the next acquirer simply succeeds over the stale body.
- **`check_sync_gate_held` for non-destructive callers** (preflight, `_maybe_auto_sync`, bash `auto-pr`): tries `LOCK_SH | LOCK_NB`; on success, the file is stale (no exclusive holder) and is silently auto-removed; on `BlockingIOError`, the file body is parsed and a friendly diagnostic is returned.
- **Friendly, actionable holder messages** replace the prior `Error: tracker sync in progress. Wait for htrac sync to complete.` one-liner. New shape: `tracker sync gate locked by PID 12345 (alive, started 2m ago) working on PR #3392. Your operation will be retried automatically when the sync completes.` (or, if PID is dead: `… (DEAD, started 1h17m ago). Stale lock — the OS will release it on the next acquire attempt; no manual cleanup needed.`).
- **`scripts/auto-pr`'s three sync-gate checks** (`flush_queue`, `do_pr` preflight, post-`PR_PENDING` re-check) now route through a shared bash helper `_autopr_sync_gate_held` that uses `flock --shared --nonblock` to inspect the lock state without disturbing the holder, auto-cleans stale files, and renders the same diagnostic shape as the Python side.

#### Cross-tier reference resolution (WI-sohot)

- **`tracker validate` now matches CI's view by default** (WI-sohot): the cross-file reference checks in `_check_ref_resolution` (renamed from `_check_dangling_parents`) split into two index scopes by writer tier. Canonical and workspace items must resolve refs in canonical ∪ workspace (the CI-visible set, since `tracker-workspace/stealth/` is gitignored); stealth items resolve in the full canonical ∪ workspace ∪ stealth index. Reproduces the PR #3365 CI failure locally — workspace items pointing at stealth-tier ids no longer pass `tracker validate` only to fail in CI.
- **All four ref-typed fields are now checked for dangling/cross-tier issues**: `parent`, `isbefore`, `duplicate_of`, `not_duplicate_of`. Previously only `parent` was checked for dangling references; `isbefore` was checked only for cycles, and `duplicate_of` / `not_duplicate_of` weren't validated at all. Two error classes are emitted: `dangling <field> reference` (target absent in every tier) and `cross-tier <field> reference … from <tier> to stealth` (target exists but in a tier the writer cannot legally reference).
- **CLI write-time guard** for `tracker add` / `tracker update`: `_resolve_ref` now accepts a `writer_tier` argument and refuses to resolve a reference whose target is in stealth when the writer is canonical or workspace. Threaded through `--parent`, `--isbefore`, `--add-isbefore`, `--add-duplicate-of`, `--add-not-duplicate-of`, and `--add-blocked-by` (where the blocker is the writer of the new `isbefore` link). Removal flags (`--remove-isbefore`, etc.) skip the check — narrowing a ref set can never create a cross-tier link.
- **Three pre-existing latent dangling refs cleaned up** as a side-effect of tightening `_check_ref_resolution`: `WI-dogir.isbefore` referenced ambiguous short id `WI-duzul` (resolved to `WI-duzul-kugag-…`, the Phase 3 stable_id item per the description); `WI-nugiv.duplicate_of` and `WI-vibat.duplicate_of` referenced short id `WI-hugir-balik` (resolved to `WI-hugir-balik-tajub-…`). These predated the WI-sohot fix but were invisible because the fields weren't checked.

#### Transient `.ops` read-race hardening

- **`_parse_ops_file` retries transient read races** (observed 2026-04-22): a long-running `htrac tui` crashed with `PermissionError [Errno 13]` on a mode-0o664 ops file owned by the reader — narrow window where `stat()` saw the old inode but `open()` landed on a new one written by a concurrent atomic-rename writer. Retries on `PermissionError` / `FileNotFoundError` with capped exponential backoff (`_OPS_READ_BASE_BACKOFF_S * 2**(attempt-1)`, clamped by `_OPS_READ_BACKOFF_CAP_S`); every retry and final re-raise logged to the race log. Budget bumped 2026-04-26 from 3 attempts × ~0.06 s linear → 6 attempts × ~0.55 s exponential after a `git checkout` during auto-sync outlasted the original window and re-crashed the TUI.
- **`_compile_all` / `_compile_all_cached` widen except to `(CorruptFileError, OSError)`**: a single unreadable ops file no longer poisons the entire compile — the bad file is skipped and the rest of the tier still renders.
- **TUI `_check_external_writes` catches `OSError`**: the periodic background refresh in `htrac tui` no longer lets a transient FS hiccup kill the Textual event loop. Persistent errors surface on the next user action; the race log retains the detail.
- **`TrackerSet.exists(item_id)`**: lightweight stat-only existence check that bypasses the `.ops`-content read path entirely. Used by `TrackerApp._item_exists` (the per-cursor-move hotspot resolver), which previously called `.get()` and parsed the full ops file on every cursor move. The new path can't race an atomic-rename writer's brief 600-mode tmpfile window because it only stats `item_path`. Swallows `(ItemNotFoundError, AmbiguousPrefixError, OSError)` — UI helpers want a boolean, not a crash.

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
- **Self-contained `check-messages` output** (WI-radab): now includes item description, prior discussion entries (last 3), and `pr_ref`. Eliminates follow-up `tracker show` calls.
- **`--add-field` and `--remove-field` for partial updates** (WI-lorip): merge into or delete from existing fields without erasing other keys. Fixes data-loss footgun where `--field key=value` silently erased all other fields.
- **`--description-file` and `--description-stdin`** (WI-pudan): read description text from a file or stdin, avoiding shell quoting issues.
- **`list --status` repeatable + `--open` shortcut** (WI-lukop): `--status` accepts multiple values. `--open` expands to the five open statuses.
- **`discuss <id>` view mode** (WI-kidip): calling `discuss` with no message prints the thread chronologically. `--json` for structured output.
- **`--ack-thread` gate for `discuss`** (WI-zufuj): when the last entry is from a human, `discuss` exits non-zero unless `--ack-thread` is provided. Prevents drive-by agent replies.

### Fixed

- **`cache-rebuild` writes to XDG cache dir, not legacy path**: `_cmd_cache_rebuild` hardcoded the pre-ADR-0013 location. Fix: reuse `_get_cache_dir()` instances.
- **TUI displayed `before` field with inverted direction**: `_build_dep_index` swapped blockers and dependents, so every detail panel, chain summary, and dep pill rendered arrows upside down. Fix: populate under CLI semantics — `item.isbefore` renders as "Blocks:", not "Blocked by:". (Field was later renamed from `before` to `isbefore`.)
- **`_maybe_auto_sync` guarded against `tracker_root` outside `repo_root`**: auto-sync derived `repo_root` from cwd instead of the parameter, causing test hangs when cwd had pending ops and `PR_PENDING`. Fix: verify containment and return early if not.
- **Fold-induced nonce injection in YAML serializer** (WI-pusif-bukor): `_serialize_op` now sets `ry.width = sys.maxsize` to prevent ruamel.yaml from folding long scalars, which embedded nonce comments inside field values on read-back.
- **TUI crash on bracketed text in user content**: Rich's markup parser interpreted literal `[` in titles/tags/discussions as style tags. Fix: `_escape_user` helper escapes `[` to `\[` in user-controlled text.

## [0.2.0] - 2026-04-04

### Added

#### TUI screenshot annotation mode (ADR-0020)

- **Annotation overlay**: Press `S` to capture a screenshot, then annotate with rectangles (`R` + drag), arrows (`A` + drag), and numbered text labels (`L` + click). Arrow key nudge (±1 cell) mitigates SSH mouse coordinate drift. Annotations injected as SVG elements; label text XML-sanitized.
- **Inline SVG preview**: Discussion entries referencing `.svg` files show placeholders. SVG→PNG→ANSI pipeline via optional `cairosvg` + `chafa` with graceful degradation.
- **Label UX redesign** (WI-gikut): Replaced raw keystroke capture with Textual `Input` widget for full editing support. Numbered markers provide visual feedback before text entry.

#### htrac serve and web frontend (ADR-0019 Part A)

- **`htrac serve` command**: Starlette/uvicorn server bound to 127.0.0.1:7380. REST API (`/api/items`, `/api/ready`, create/update/discuss endpoints) and WebSocket (`/ws`) protocol for real-time state sync. Same TrackerSet engine as CLI/TUI. PID file management for `--background`/`--stop`/`--status`.
- **Auth stack**: WebAuthn/FIDO2 hardware key auth (ES256, RS256). Bcrypt password verification with timing-safe dual-check (real + duress). Per-credential rate limiting with exponential backoff. In-memory session store with configurable TTL. `DuressHandler` protocol for user-defined duress behavior. `AuthConfig` from `config.yaml`.
- **Filesystem watcher**: `watchfiles` detects CLI/TUI ops file changes and auto-broadcasts state to all WebSocket clients. Sub-second latency, no IPC.
- **Web frontend** (WI-kimuj): Vite + TypeScript + BlockSuite in `packages/htrac-frontend/`. Lit web components for tracker item list and detail views (WI-gojov, WI-nopuj) with responsive two-panel layout. Service worker for cache-first Tor-friendly loading. WebSocket client with exponential backoff reconnect.

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
- Gate timing race (INV-rahib): `PR_PENDING` gate now created before push (was after), closing a window where sync could advance dev mid-flight.
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
