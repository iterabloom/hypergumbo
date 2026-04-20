<!-- SPDX-License-Identifier: MPL-2.0 -->
# Changelog — hypergumbo-tracker

All notable changes to the `hypergumbo-tracker` package are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

This package is independently versioned from the main hypergumbo tool and licensed under MPL-2.0.

## [Unreleased]

### Added

- **TUI integration of the WI-sulij item-nav modal (WI-sulij, slice D):** the Description and Activity panes now detect embedded item IDs and render them as `[@click=jump_to_item(...)]` hotspots; clicking (or invoking the action directly) pushes `ItemNavModal` onto the screen stack with the target item's detail + activity content. `TrackerApp` now compiles the ID pattern at construction via `build_item_id_pattern(tracker_set.config)`, exposes `_item_exists` as the resolver (treats `ItemNotFoundError` / `AmbiguousPrefixError` as absent so dead or ambiguous IDs render as plain text, never as dead hotspots), and wires `_apply_nav_hotspots` into `_show_detail`, `_show_std_detail`, and `_show_activity`. The modal is constructed with `exists` / `content_for` / `id_pattern` injection so the existing dict-backed unit tests still work unchanged. `action_jump_to_item` is defensive against stale IDs from items deleted between render and click (silent no-op). 10 new tests cover the resolver (known / unknown / ambiguous prefix), the hotspot wrap (resolvable wrapped, unresolvable left plain, empty no-op), the content-for pair, and Pilot end-to-end checks that the live detail pane markup contains a click span for a cross-referencing item, that `action_jump_to_item` pushes a real `ItemNavModal`, and that invoking the action on an unknown ID leaves the screen stack unchanged. 5 snapshot SVGs regenerated to reflect the item-own-ID underline now shown in detail view.

- **`format_nav_indicator` helper for the TUI item-nav modal header (WI-sulij, slice B polish):** adds `hypergumbo_tracker.nav_history.format_nav_indicator(history, *, empty_label="(empty)")` — a thin pure-text formatter that returns an `"[N/M] item-id"` indicator (1-indexed cursor, 1-indexed total) for the nav modal's header. Returns *empty_label* (configurable) when the history has no entries so the header never renders an ambiguous blank string. 5 new tests cover empty default, empty custom label, single item, mid-stack (cursor below tip), and tip of stack. No Textual dependency — still pure data + string formatting.

- **`nav_history` module: browser-style back/forward stack for the TUI item-nav modal (WI-sulij, slice B):** new pure-Python `hypergumbo_tracker.nav_history.NavigationHistory` dataclass providing browser-like semantics for the forthcoming clickable-ID modal's navigation controls. API: `current()` returns the displayed ID or None; `push(item_id)` advances the cursor, truncating any forward history (so a new jump from a mid-stack position drops everything ahead, matching browser behaviour), treats pushing the same ID as a no-op double-click, and raises `ValueError` on empty input; `back()` / `forward()` move the cursor one step and return the new `current()`, no-ops at the boundaries; `can_go_back` / `can_go_forward` are boolean properties for button-disabled state; `depth()` and `position()` expose inspection fields for display/telemetry. Deliberately not persistent — closing the modal discards the stack (WI-sulij design constraint 5) — and unbounded, since realistic cross-reference chains top out at a handful of jumps. No Textual imports: the class is a pure data structure so slice C (the ModalScreen subclass) can mount it without coupling history logic to the event loop. 19 tests cover empty-state semantics (current=None, can't traverse, empty-push raises), push semantics (first push, second advances, same-ID no-op, truncation after back, return value), back/forward (single-step, boundary no-ops, can_go_* boundaries), and browser-like traversal (full round-trip A→B→C→back→back→forward, truncation drops forward chain, depth independent of cursor).

- **`id_matching` module: detect tracker item IDs in free text (WI-sulij, slice A):** new pure-Python module `hypergumbo_tracker.id_matching` with two exported functions: `build_item_id_pattern(config)` compiles a regex from the kind prefixes in a `TrackerConfig` (sorted, `re.escape`-ed, alternation-joined), and `find_item_ids(text, pattern, skip_ranges=...)` enumerates non-overlapping matches in document order and drops any match whose span intersects a caller-supplied skip range. Pattern shape is `<prefix>-<proquint>(-<proquint>)+` where each proquint is CVCVC (consonants `bcdfghjklmnpqrstvz`, vowels `aeiou`) — requires at least two syllables so the detector cannot collide with English word fragments. Word-boundary handling uses `(?<![A-Za-z0-9])` / `(?![A-Za-z0-9])` so IDs embedded between hyphens (`see-WI-lusab-baril-now`) still extract cleanly while alphanumeric-attached forms (`aWI-lusab-baril`, `WI-lusab-barilX`) are rejected. Raises `ValueError` when the config has zero configured kinds. No TUI wiring yet — that's slice B once the Textual modal scaffolding lands; this slice delivers the foundation in a standalone, 100%-covered package module so the modal can consume it without reinventing the regex. 20 tests cover prefix enumeration (default kinds, unconfigured prefix rejection, custom prefix, empty-kinds raises, regex-metacharacter escaping), CVCVC rules (one-syllable rejection, two-syllable minimum, full eight-syllable ID, non-proquint rejection), word-boundary handling (leading alphanum reject, trailing alphanum reject, hyphen-adjacency allowed), and scan semantics (document order, empty input, empty-text result, namedtuple shape, skip-range interior / partial-overlap / non-overlap cases, pre-compiled-pattern pass-through).

- **Stop-hook `[[preface]]` escalation + LLM precedence gate** (WI-mofaz, Phase 2 of WI-ripuz): the REPLY-FIRST CYCLE document now recognises a `[[tag]]` preface at char 0 of an unread human message (e.g. `[[IMPORTANT]]`, `[[do-right-fucking-now]]`, `[[Mclovin]]`) and, when other unreads also exist, consults an LLM via OpenRouter — "Does the tag [[X]] imply this message takes precedence over the other N unreads?" — to decide whether to lift the item into a top-of-document "Escalated" section. The preface regex is strict (anchored at char 0, no internal whitespace, no nested brackets, must close with `]]`) but semantically opaque — the gate decides meaning, not the regex. Fail-soft: missing `OPENROUTER_API_KEY`, network errors, malformed responses, or any non-affirmative answer all fall back to the Phase 1 equal-priority listing. The gate skips entirely when there's only one unread (nothing to take precedence over). OpenRouter is already covered by `ALLOWED_WEBSITES.md` and used by `.agent/hooks/_shared/on_transcript_change.py`.

### Changed

- **Stop-hook REPLY-FIRST CYCLE branch when reply debt exists** (WI-ripuz, Phase 1): `stop_hook.py::generate_guidance` now branches when any unread human messages are present (blocking or non-blocking) and emits a REPLY-FIRST CYCLE guidance document instead of the default TODO listing. Hard/violated/soft sections are suppressed, replaced with a 4-step protocol (`tracker show` → classify → plan investigation → `tracker discuss`) and a per-item list of blocking and non-blocking unreads. Companion change in `.agent/hooks/*/stop.sh` (claude-code, cursor, codex-cli, gemini-cli) reads a new `UNREAD_COUNT` export from `stop_logic.sh` and switches the one-line reason text from "AUTONOMOUS MODE: N TODOs block stopping…" to "REPLY-FIRST CYCLE: N unread human message(s)…" whenever `UNREAD_COUNT > 0`. This hides the TODO-count forward-march framing entirely while reply debt exists — the behavioural lever from the WI-ripuz design rationale (the only proposal that changes the agent's incentive structure rather than adding more text). The `[[…]]` preface + LLM-gate refinement is tracked separately as a follow-up item.

### Fixed

- **`_config_chmod_fallback` uses same-directory tempfile + atomic `os.rename`**: the cross-user `config.yaml` lock/unlock fallback in `setup.py` had the exact same bug as `_take_ownership_via_tmp` — `tempfile.mkstemp()` without a `dir=` kwarg (defaults to `/tmp`), followed by `shutil.copy2` / `unlink` / `shutil.move` / `chmod`. Because `/tmp` is typically on a separate tmpfs, `shutil.move` fell into its cross-filesystem `copy2 + unlink` fallback, exposing a window where `config.yaml` was briefly absent or held umask-respecting mode. Every `scripts/tracker` CLI invocation and every TUI startup reads `config.yaml`, so the potential reader pool was larger than the ops-file case; the race was just rarer because `config_lock`/`config_unlock` fires less often than `_append_op`. Fix mirrors the ops-file fix: pass `dir=str(path.parent)` to `mkstemp`, replace `unlink + shutil.move` with a single `os.rename(tmp, path)`, adjust the tempfile prefix to `.htrac_config_` (dotfile hygiene if cleanup fails). New `test_fallback_uses_same_directory_tmp` pins the invariant by spying on `mkstemp`; `test_fallback_cleans_tmp_on_error` updated to look for leftover tempfiles in the target's own directory (pattern `.htrac_config_*.yaml`) rather than relying on /tmp side effects.

- **`_take_ownership_via_tmp` uses same-directory tempfile + atomic `os.rename`**: the cross-user ownership-transfer path for ops files used `tempfile.mkstemp()` (default `dir=/tmp`) followed by `shutil.move`. Because `/tmp` is typically a separate tmpfs on Linux, `shutil.move` degraded to cross-filesystem `copy2 + unlink` — and within that sequence the target dentry was briefly absent / held umask-respecting (not 0o664) mode during the copy, before `copystat` normalized it. Live-update readers (notably the tracker TUI's inotify watcher) raced that window. First confirmed occurrence: 2026-04-19 10:24:05Z — the supervised agent appended an op to `.INV-rikis-…ops` at 10:24:01Z for commit `5f6531c94` (fix for INV-rikis), and 4 seconds later the TUI crashed in `store.py::_parse_ops_file` with `PermissionError [Errno 13]` on `open(filepath)` despite the same file being mode 0o664 at rest. Fix: pass `dir=str(filepath.parent)` to `mkstemp` and replace `unlink + shutil.move` with a single `os.rename(tmp, filepath)`. Rename is a single-syscall atomic replace on a single filesystem — readers see either the old inode or the new inode, never an intermediate. Cleanup semantics, error branches, and the informative `PermissionError` raised when the `.ops/` directory lacks group-write permission are preserved. 1 new test (`test_take_ownership_uses_same_directory_tmp`) pins the invariant by spying on `mkstemp` and asserting `dir=filepath.parent`; 2 existing cleanup tests updated to look for the tempfile in the target's own directory (pattern `.htrac_*.ops`) instead of `/tmp/htrac_*.ops`, and to patch `os.rename` instead of `shutil.move`.

- **SimHash similarity-warning threshold unified at 8 bits** (WI-giroz): `store._SIMHASH_THRESHOLD` was 13 (create-time warning) while `validation._SIMHASH_THRESHOLD` was 8 (validation-pass check) — accidental drift between two code paths checking the same property. The looser create-time threshold fired 5+ false-positive warnings per new item against unrelated tracker items that shared common domain vocabulary (e.g. the words "tracker", "stop", "hook"). Unified by importing the constant from `store` into `validation`, and tightened store's value to 8 to match. Net effect: fewer spurious near-duplicate warnings when filing new items; identical-by-paraphrase items (19/20 shared tokens) still warn as they did before.

- **`discuss` gate-message shows the working `--ack-thread` arg order** (WI-foril): the re-run hint printed when the gate fires now reads `tracker discuss <id> "<your reply>" --ack-thread` instead of the previous form `--ack-thread "<your reply>"`. Argparse's subparser parsing rejects the flag-before-positional form (an `nargs="?"` positional with a following optional flag won't accept the message after the flag — it's parsed as extra args). The fix is cosmetic but high-leverage: agents and humans alike were following the broken example shown by the gate itself. Also adds a regression check that the broken order really does still fail.

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
