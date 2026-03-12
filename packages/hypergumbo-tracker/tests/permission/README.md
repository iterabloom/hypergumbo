<!-- SPDX-License-Identifier: MPL-2.0 -->
# Two-User Permission Test Playbook

These four scripts exercise the tracker's two-user permission boundary
(jgstern=human, jgstern_agent=agent). They must be run in sequence,
alternating between OS users.

## Prerequisites

1. Both users exist: `jgstern`, `jgstern_agent`
2. Shared group exists: `project-dev`
3. Both users are members: `groups jgstern_agent` and `groups jgstern` should both show `project-dev`
4. Agent's home directory is traversable: `jgstern` must be able to traverse
   into `/home/jgstern_agent/` (this is also required for the real tracker)
5. `hypergumbo-tracker` is on PATH for both users (e.g. via the project virtualenv)

If the group doesn't exist:
```bash
sudo groupadd project-dev
sudo usermod -aG project-dev jgstern
sudo usermod -aG project-dev jgstern_agent
# Users must log out and back in for group membership to take effect
```

If the agent's home directory is not traversable (mode 700):
```bash
# The human needs execute permission to traverse into the agent's home dir.
# This is required for the real two-user tracker model, not just the test.
sudo chmod o+x /home/jgstern_agent
```

## Execution sequence

```
Step  Who             Command
────  ──────────────  ──────────────────────────────────────────────────
 1    jgstern_agent   ./1_agent_setup.sh [--workdir DIR]
 2    jgstern         ./2_human_governance.sh <state.json>
 3    jgstern_agent   ./3_agent_constrained.sh <state.json>
 4    jgstern         ./4_human_cleanup.sh <state.json>
```

Script 1 creates the test directory and prints the `state.json` path.
Scripts 2-4 require that path as a positional argument. Script 4 prints
the combined report and cleans up.

### Running

From the **agent's** shell:
```bash
# Run script 1 as the agent.
# --workdir places test files on the same filesystem as real tracker data
# (default is /tmp, which may be tmpfs with different permission semantics).
./1_agent_setup.sh --workdir ~/tracker-permission-test
# Note the state.json path printed at the end, e.g.:
#   /home/jgstern_agent/tracker-permission-test/tracker-permission-test-EkFb/state.json
```

From the **human's** shell:
```bash
./2_human_governance.sh /home/jgstern_agent/tracker-permission-test/tracker-permission-test-EkFb/state.json
```

From the **agent's** shell:
```bash
./3_agent_constrained.sh /home/jgstern_agent/tracker-permission-test/tracker-permission-test-EkFb/state.json
```

From the **human's** shell:
```bash
./4_human_cleanup.sh /home/jgstern_agent/tracker-permission-test/tracker-permission-test-EkFb/state.json
```

## What each script tests

### 1_agent_setup.sh (jgstern_agent)

Creates the temp repo and tracker structure, then tests:
- Agent can: create work_item, create invariant, discuss, create freeze target
- Agent cannot: lock, unlock, discuss --clear, stealth, unstealth, delete, freeze, unfreeze

### 2_human_governance.sh (jgstern)

Exercises human-authority operations (14 tests):
- Human can: lock status, discuss, discuss --clear, lock/unlock discussion,
  stealth/unstealth, delete
- Verifies deleted item excluded from `ready`
- Locks invariant status (sets up constraint for script 3)
- Locks invariant title with mixed case (case-insensitive normalization)
- Validates that locking a nonexistent field fails
- Freezes invariant (sets up freeze constraint for script 3)
- Fixes config.yaml ownership to human-only

### 3_agent_constrained.sh (jgstern_agent)

Verifies that agent attempts to bypass human constraints fail (12 tests):
- Agent update of locked status field fails with LockedFieldError
- Agent can still update non-locked fields (priority) and discuss
- Agent update of locked title (case-insensitive) fails with LockedFieldError
- Agent update of frozen item fails with FrozenItemError
- Agent discuss on frozen item fails with FrozenItemError
- Filesystem checks: config.yaml not writable by agent, .ops dirs have
  project-dev group, setgid bit set

### 4_human_cleanup.sh (jgstern)

Final verification and teardown (5 tests):
- Human unfreezes invariant (was frozen in script 2)
- Human unlocks title (was locked with mixed case)
- Human unlocks invariant status, sets it to done
- Verifies count-todos = 0
- Prints combined pass/fail report across all 4 scripts
- Removes temp directory and git safe.directory entry

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "project-dev group does not exist" | Group not created | `sudo groupadd project-dev` |
| Permission denied on .ops files | Missing group membership | `sudo usermod -aG project-dev <user>`, re-login |
| "dubious ownership" git error | Cross-user repo access | Script 2 adds safe.directory automatically |
| "state.json not found" | Wrong path or script 1 didn't run | Pass the exact path printed by script 1 |
| Permission denied accessing workdir | Home dir is mode 700 | `sudo chmod o+x /home/jgstern_agent` (required for real tracker too) |
| setgid check fails | OS doesn't support setgid on dirs | Expected on some filesystems; non-fatal |
| Permissions pass in /tmp but fail elsewhere | /tmp is often tmpfs; different mount options | Use `--workdir /var/tmp/...` to test on the real filesystem |

## Options and environment variables

- `--workdir DIR` (script 1 only): Parent directory for test files. Defaults to
  `/tmp`. Use `~/tracker-permission-test` to test on the same filesystem as real
  tracker data — `/tmp` is often tmpfs, which may have different mount options
  (e.g. `nosuid` silently ignores setgid). The agent's home directory must be
  traversable by the human user (`chmod o+x`); script 1 checks this and fails
  early if not.
- `TRACKER_CMD`: Override the tracker command (default: `hypergumbo-tracker`)
