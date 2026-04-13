<!-- SPDX-License-Identifier: MPL-2.0 -->
# Changelog — hypergumbo-tracker

All notable changes to the `hypergumbo-tracker` package are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

This package is independently versioned from the main hypergumbo tool and licensed under MPL-2.0.

## [Unreleased]

### Changed

- **Renamed `before` field to `isbefore`** (WI-tunag): the dependency-link field on tracker items is now `isbefore` (`X.isbefore = [Y]` → "X is before Y"). CLI flags renamed: `--isbefore`, `--add-isbefore`, `--remove-isbefore`. Old ops files using `before` are read transparently (backward compatible); new writes use `isbefore`. Prevents the directional confusion that caused the TUI inversion bug (PR #2981).

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
