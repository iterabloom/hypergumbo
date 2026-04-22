<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Long-Running Output Capture Playbook

## Rule

**NEVER** pipe the output of a long-running command through `| tail -N` or `| head -N` as the primary capture method. The pipe buffers; truncation destroys the lines you need; re-running the command wastes everyone's time.

The rule applies to every command that takes more than a handful of seconds:
- `pytest` / `smart-test`
- `./scripts/auto-pr`
- `./scripts/merge-pr`
- `./scripts/release-check`
- `./scripts/bakeoff-broad` and `./scripts/bakeoff-deep` (all subcommands)
- `./scripts/ci-debug`
- anything that polls CI, drives the tracker, or contacts the network

## Required Pattern

### Capture to a file, not a pipe

```bash
# Right — the full transcript is on disk, available to Read / Grep
some-long-command > /tmp/cmd-output.log 2>&1
```

```bash
# Wrong — the pipe buffers until the whole command completes, and
# truncation destroys anything the failure mode left earlier in stdout
some-long-command 2>&1 | tail -40
```

### Background + Monitor for very long commands

`./scripts/auto-pr` and bakeoff subcommands can run for many minutes. Start them in the background, then point a `Monitor` at the output file with an alternation that covers *every terminal state*, not just the happy path:

```bash
./scripts/auto-pr > /tmp/auto-pr.log 2>&1 &
# Wait for notifications; the Monitor will fire on success OR failure OR timeout signatures.
```

```
Monitor:
  tail -f /tmp/auto-pr.log | grep --line-buffered \
      -E "merged|Merged|FAIL|failed|Error|ERROR|SUCCESS|TIMEOUT|Cannot|blocked|Exit code|Recovery:"
```

Note the `|Recovery:` alternation — `scripts/auto-pr` emits `Recovery: ...` hints when the final merge did not actually complete but the script exited 0 anyway (tracked as WI-kujis). Without this alternation, the Monitor stays silent on that failure mode.

### Reading the captured log

Use the `Read` tool on the file, or `Grep` for the pattern you care about. **Do not** re-run the command to "see what happened" — the log already has it.

## Special Hazards

### auto-pr rebase stashes tracker .ops edits

When `auto-pr` detects that the feature branch is behind base, it backs up
`.agent/tracker-workspace/.ops` and `.agent/tracker/.ops` to a temp directory,
rebases, and restores the backup. Any `tracker discuss` / `tracker add` / `tracker update`
operations performed *during* the auto-pr run are at risk of being overwritten
by the restore step. Symptom: a comment you just posted disappears from the TUI
mid-session.

Recovery if it happens to you:

1. `git stash list` — the lost edits may be auto-stashed under a fresh `WIP on dev:` entry created by the post-merge `git checkout dev`.
2. Reset `affected-tests.txt` first, then `git stash pop stash@{0}`:
   ```bash
   git checkout -- "$(git rev-parse --show-toplevel)/.ci/affected-tests.txt"
   git stash pop stash@{0}
   ```
3. `./scripts/tracker sync` to push the restored `.ops` edits.

Mitigation until the underlying bug is fixed (tracked as WI-buhov): do not perform tracker `discuss` / `add` / `update` operations while an `auto-pr` run is in flight. Wait for the merge to complete first.

## Why

Re-running a 15-minute command because `| tail -30` missed the relevant lines is pure waste. Capturing to a file costs nothing and enables targeted searching after the fact. On 2026-04-17 the agent hit both failure modes in sequence — `tail`-pipe on `auto-pr` hid the "Recovery:" hint, then a second `tail`-pipe on `merge-pr` made the Monitor silent — and had to be told by the user that the rule already existed.

## Quick self-check before running a long command

- Will the output fit on one screen? If no, **redirect to a file**.
- Will the output be useful if only the last 30 lines survive? If no, **redirect to a file**.
- Is this a command I've seen listed under "Commands this applies to"? If yes, **redirect to a file**.
- Do I plan to keep working while it runs? If yes, **run in background + Monitor on the file**.

When in doubt, redirect. The disk is cheap; your context window is not.
