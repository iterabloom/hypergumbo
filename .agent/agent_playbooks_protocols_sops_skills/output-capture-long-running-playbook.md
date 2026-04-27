<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Long-Running Output Capture Playbook

## The canonical pattern

For any command that takes more than a handful of seconds, **capture full output to a file, then read it back with `Read` or `Grep`**:

```bash
some-long-command > /tmp/cmd-output.log 2>&1
# then: Read /tmp/cmd-output.log     (or Grep for a specific pattern)
```

This is the shape every long-running invocation in this repo should take. The full transcript lives on disk; you can search it freely; you never have to re-run the command to recover output you already produced.

## Which commands this applies to

Commands that routinely run for many seconds to many minutes:

- `pytest` / `smart-test`
- `./scripts/auto-pr`
- `./scripts/merge-pr`
- `./scripts/release-check`
- `./scripts/bakeoff-broad` and `./scripts/bakeoff-deep` (all subcommands)
- `./scripts/ci-debug`
- anything that polls CI, drives the tracker, or contacts the network

When in doubt, capture. The disk is cheap; the context window is not.

## Anti-pattern: piping through `tail` / `head`

The shape to avoid is `<long-running-command> | tail -N` (or `| head -N`) as the *primary* capture method. The pipe buffers, the truncation destroys whatever the failure mode left earlier in stdout, and re-running the command to recover the lost lines is pure waste. Use the canonical pattern above instead.

(Note: `| tail -N` on a *cheap* command like `git log --oneline | tail -5` is fine — the rule is about long-running commands where re-running is expensive.)

## Background + Monitor for very long commands

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

The `|Recovery:` alternation is load-bearing: `scripts/auto-pr` emits `Recovery: ...` hints when the final merge did not actually complete but the script exited 0 anyway. Without that pattern in the Monitor alternation, the script appears to succeed silently.

## Reading the captured log

Use the `Read` tool on the file, or `Grep` for the pattern you care about. The log already has everything — re-running the command to "see what happened" produces nothing the file doesn't already contain.

## Special Hazards

### auto-pr backs up and restores tracker .ops edits during rebase

When `auto-pr` detects that the feature branch is behind base, it copies
`.agent/tracker-workspace/.ops` and `.agent/tracker/.ops` to a temp directory,
rebases, then restores the temp copy. This is a plain file copy — *not* a `git stash`.
Any `tracker discuss` / `tracker add` / `tracker update` operations performed
*during* the auto-pr run are at risk of being silently overwritten by the restore
step. Symptom: a comment you just posted disappears from the TUI mid-session.

**Mitigation:** do not perform tracker `discuss` / `add` / `update` operations while an `auto-pr` run is in flight. Wait for the merge to complete first.

**Recovery if it happens to you:**

A separate, unrelated mechanism — the post-merge `git checkout dev` step — may have *also* created a `git stash` entry containing your lost edits. This is independent of the temp-directory backup and is a possible second line of defense, but it is not guaranteed.

1. `git stash list` — look for a recent `WIP on dev:` or `WIP on <branch>:` entry.
2. **Verify before popping.** `stash@{0}` is "most recent across all of git", which may include unrelated stashes from other work. Confirm the stash actually contains your lost tracker edits:
   ```bash
   git stash show stash@{0} --name-only
   ```
   The output should list files under `.agent/tracker-workspace/.ops/` or `.agent/tracker/.ops/`. If it lists something else, **do not pop it** — you would be unstashing somebody else's WIP. Skip to step 4.
3. Reset `.ci/affected-tests.txt` first (smart-test regenerates it on every run, so the stash pop will conflict on it), then pop:
   ```bash
   git checkout -- "$(git rev-parse --show-toplevel)/.ci/affected-tests.txt"
   git stash pop stash@{0}
   ```
   The next tracker mutation will trigger an auto-sync that pushes the restored ops.
4. **If no usable stash exists:** re-issue the lost `tracker` operations by hand. State-changing flags (`--status`, `--add-tag`, `--remove-tag`) are idempotent — re-running them yields the same compiled state. Discussion entries are not idempotent: they need to be re-added with `tracker discuss` and will carry fresh timestamps.

## Quick self-check before running a long command

- Will the output fit on one screen? If no, **redirect to a file**.
- Will the output be useful if only the last 30 lines survive? If no, **redirect to a file**.
- Is this a command listed above under "Which commands this applies to"? If yes, **redirect to a file**.
- Do I plan to keep working while it runs? If yes, **run in background + Monitor on the file**.

When in doubt, redirect.
