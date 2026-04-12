<!-- SPDX-License-Identifier: MPL-2.0 -->
# Changelog — hypergumbo-tracker

All notable changes to the `hypergumbo-tracker` package are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

This package is independently versioned from the main hypergumbo tool and licensed under MPL-2.0.

## [Unreleased]

### Added

- **TUI background reload on external tracker writes**: `htrac tui` now notices when another process (the agent CLI in a sibling terminal, the auto-sync merger, etc.) appends, creates, or deletes ops files and refreshes its view automatically. The reload polls a cheap mtime signature over the three `.ops` directories every `HTRAC_RELOAD_INTERVAL` seconds (default `5.0`, overridable via environment variable) and reuses the existing `_reload_after_write` path. Ephemeral UX state is preserved across the reload: cursor position (restored by item ID via `_restore_selection`), scroll offset (captured and restored across the rebuild by new helpers deferred via `call_after_refresh`), filter text (`_filter_text` + the filter Input widget's `.value`), in-flight `#chat-input` TextArea contents, and focus. While any modal screen is pushed on the screen stack (item-edit, item-add, discuss composer, etc.), the background reload is deferred entirely to avoid racing with an in-progress edit against external state; the next tick after the modal closes catches up on all accumulated changes. New public method `TrackerSet.ops_mtime_signature()` encapsulates the per-tier signature computation and handles missing/empty ops directories gracefully.
- **Scroll offset now preserved across `_reload_after_write`**: fixes a latent UX bug where clicking Send in the TUI reset the main item list's scroll position to the top regardless of where the user had scrolled. The reload path now captures the active DataTable's `scroll_y` before `_load_items()` and restores it afterward via `call_after_refresh` (deferring until after the rebuild's internal `move_cursor(row=0)` has settled). Both the existing TUI-initiated write path and the new background-reload path benefit from this single change.
- **Self-contained `check-messages` output** (WI-radab): `check-messages` now includes the item description (truncated to 500 chars), prior discussion entries (last 3 before unread), and `pr_ref` field. JSON output adds `description`, `pr_ref`, and `prior_discussion` fields. Eliminates the need for a follow-up `tracker show` to understand message context.
- **`--add-field` and `--remove-field` for partial field updates** (WI-lorip): `update --add-field key=value` merges into existing fields without erasing other keys. `update --remove-field key` deletes a single key. Mutually exclusive with `--field` (full replacement). compile_ops treats `None`-valued field keys as deletions. Fixes a data-loss footgun where `--field key=value` silently erased all other fields.
- **`--description-file` and `--description-stdin` for add/update** (WI-pudan): new flags to read description text from a file or stdin, avoiding shell quoting issues with backticks, `$VAR`, and other special characters. Mutually exclusive with `--description`. Missing file produces a clear error.
- **`tracker list --status` repeatable + `--open` shortcut** (WI-lukop): `--status` now accepts multiple values (`--status todo_hard --status todo_soft`), returning the union. New `--open` flag expands to the five open statuses (todo_hard, todo_soft, violated, pending_validation, needs_human_review). `store.list_items()` accepts `str | list[str] | None` for the status parameter.
- **`tracker discuss <id>` view mode** (WI-kidip): calling `discuss` with no message argument now prints the discussion thread in chronological order instead of appending an empty entry. Shows entry number, timestamp, actor, and message text. `--json` returns the thread as a JSON list of records. Empty threads show "(no entries)". Existing `discuss <id> <message>` (append) behavior is unchanged.
- **`--ack-thread` gate for `tracker discuss`** (WI-zufuj): when the last discussion entry is from a human, `tracker discuss` now exits non-zero unless `--ack-thread` is provided. Prints the unread human message, the full prior transcript, and a hint to re-run with `--ack-thread`. Prevents drive-by agent replies that don't engage with the human's message. The `--note` shorthand on `tracker update` still warns without blocking.

### Fixed

- **TUI displayed `before` field with inverted direction**: `_build_dep_index` populated `blockers_of[item.id] = item.before` and `dependents_of[b] += [item.id]`, and `_format_detail_lines` rendered `item.before` as *"Blocked by:"*. That reads ``X.before = [Y]`` as "Y blocks X" — the opposite of the CLI semantic where ``_describe_changes`` logs *"X now blocks Y"* and ``_cmd_deps`` iterates ``item.before`` into the "blocks" list. Result: every TUI detail panel, chain summary bar, and dep pill rendered arrows and labels upside down. A user who set ``WI-tubot.before = [WI-botol]`` per the CLI help ("tubot runs before botol") saw the TUI report ``WI-tubot`` as "Blocked by: WI-botol". Fix: `_build_dep_index` now populates under CLI semantics (`dependents_of[item.id] = valid_before`, `blockers_of[b] += [item.id]`), and `_format_detail_lines` renders `item.before` as the *"Blocks:"* line and looks up `blockers_of[item.id]` for the *"Blocked by:"* line. The downstream BFS / chain-summary / dep-pill consumers already used the variable names correctly, so no logic changes were needed in `_compute_chain`, `_format_dep_pills`, or `_update_chain_summary` — flipping the population is enough to make them all correct. The `_format_detail_lines` parameter `dependents_of` is renamed to `blockers_of` to match the new semantic.
- **`_maybe_auto_sync` guarded against `tracker_root` outside `repo_root`**: the auto-sync helper accepted a `tracker_root` parameter but ignored it when deriving `repo_root`, instead calling `git rev-parse --show-toplevel` against cwd. Under pytest (cwd = real hypergumbo repo with ~50 pending ops, tracker_root = fixture tmpdir), `_maybe_auto_sync` counted pending ops against the real repo, tripped the 40-line threshold, saw `.git/PR_PENDING` from an in-flight governance PR, and slept up to 15 minutes waiting for the unrelated real-repo PR to clear — hanging `test_cli.py::TestWriteCommands` on every `main(["add", ...])` call. Fix: `_maybe_auto_sync` and `_print_sync_reminder` both verify that `tracker_root.resolve()` is contained within `repo_root.resolve()` and return early if not. The parameter is now a contract, not just documentation. Discovered during INV-jofaf facet 2 testing.
- **Fold-induced nonce injection in YAML serializer** (WI-pusif-bukor): `_serialize_op` now sets `ry.width = sys.maxsize` so ruamel.yaml never folds long double-quoted scalars across physical lines. The previous `width=4096` let any field over ~4080 UTF-8 bytes trigger fold-then-nonce-injection: the post-processor appended `  # <nonce>` to every physical line including continuation lines, and CSafeLoader treats `#` as literal inside double-quoted scalars on read-back, embedding the nonce comment inside the user's value. Nonce-on-every-line invariant preserved — each scalar now occupies one physical line.
- **TUI crash on items with bracketed text in user content**: opening WI-difij raised `MarkupError` because Rich's markup parser tried to interpret a bracketed shell snippet as a style tag. Same root cause for any user-controlled text containing literal `[` — titles, tags, custom field values, and discussion messages all had the latent crash. Fix: new `_escape_user` helper escapes literal `[` to `\[` before user values are interpolated into markup-formatted lines. Structural label markup is unaffected because it never embeds user content directly.

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
