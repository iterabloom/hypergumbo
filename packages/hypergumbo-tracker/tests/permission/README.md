# Two-User Permission Test Playbook

These four scripts exercise the tracker's two-user permission boundary
(jgstern=human, jgstern_agent=agent). They must be run in sequence,
alternating between OS users.

## Prerequisites

1. Both users exist: `jgstern`, `jgstern_agent`
2. Shared group exists: `project-dev`
3. Both users are members: `groups jgstern_agent` and `groups jgstern` should both show `project-dev`
4. `hypergumbo-tracker` is on PATH for both users (e.g. via the project virtualenv)

If the group doesn't exist:
```bash
sudo groupadd project-dev
sudo usermod -aG project-dev jgstern
sudo usermod -aG project-dev jgstern_agent
# Users must log out and back in for group membership to take effect
```

## Execution sequence

```
Step  Who             Command
────  ──────────────  ──────────────────────────────────────────────────
 1    jgstern_agent   ./1_agent_setup.sh
 2    jgstern         ./2_human_governance.sh
 3    jgstern_agent   ./3_agent_constrained.sh
 4    jgstern         ./4_human_cleanup.sh
```

Each script reads `state.json` from the temp directory created by script 1
and writes its results back. Script 4 prints the combined report and
cleans up.

### Running as the other user

From the human's shell:
```bash
# Run script 1 as agent
sudo -u jgstern_agent ./1_agent_setup.sh

# Run script 2 as yourself (human)
./2_human_governance.sh

# Run script 3 as agent
sudo -u jgstern_agent ./3_agent_constrained.sh

# Run script 4 as yourself (human)
./4_human_cleanup.sh
```

Or from the agent's shell:
```bash
# Run script 1 as yourself (agent)
./1_agent_setup.sh

# Ask the human to run scripts 2 and 4, or use sudo if available:
sudo -u jgstern ./2_human_governance.sh
./3_agent_constrained.sh
sudo -u jgstern ./4_human_cleanup.sh
```

## What each script tests

### 1_agent_setup.sh (jgstern_agent)

Creates the temp repo and tracker structure, then tests:
- Agent can: create work_item, create invariant, discuss
- Agent cannot: lock, unlock, discuss --clear, stealth, unstealth, delete

### 2_human_governance.sh (jgstern)

Exercises human-authority operations:
- Human can: lock status, discuss, discuss --clear, lock/unlock discussion,
  stealth/unstealth, delete
- Verifies deleted item excluded from `ready`
- Locks invariant status (sets up constraint for script 3)
- Fixes config.yaml ownership to human-only

### 3_agent_constrained.sh (jgstern_agent)

Verifies that agent attempts to bypass human constraints fail:
- Agent update of locked status field fails with LockedFieldError
- Agent can still update non-locked fields (priority) and discuss
- Filesystem checks: config.yaml not writable by agent, .ops dirs have
  project-dev group, setgid bit set

### 4_human_cleanup.sh (jgstern)

Final verification and teardown:
- Human unlocks invariant, sets it to done
- Verifies count-todos = 0
- Prints combined pass/fail report across all 4 scripts
- Removes temp directory and git safe.directory entry

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "project-dev group does not exist" | Group not created | `sudo groupadd project-dev` |
| Permission denied on .ops files | Missing group membership | `sudo usermod -aG project-dev <user>`, re-login |
| "dubious ownership" git error | Cross-user repo access | Script 2 adds safe.directory automatically |
| Script 2/4 can't find state.json | Script 1 didn't run or failed | Check /tmp/tracker-permission-test-* exists |
| setgid check fails | OS doesn't support setgid on dirs | Expected on some filesystems; non-fatal |

## Environment variables

- `TRACKER_CMD`: Override the tracker command (default: `hypergumbo-tracker`)
