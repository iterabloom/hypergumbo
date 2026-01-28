# 8. Autonomous Governance and Vendor-Agnostic Hook System

Date: 2026-01-24
Updated: 2026-01-27
Status: Accepted

## Context

### The Problem: Workarounds Instead of Structural Fixes

Hypergumbo's bakeoff infrastructure successfully surfaces real bugs (CRITICAL/HIGH signals). However, analysis of recent "fixes" reveals a pattern: **we repeatedly ship workarounds that bypass problematic code paths rather than fixing the root causes**.

Three case studies illustrate this (documented in full in `/tmp/analysis1.md`):

| Case | Symptom | Root Cause | What We Did | Result |
|------|---------|------------|-------------|--------|
| Rails routes | No routes detected | `symbol_ref` gate skips string-based handlers | Created Symbol objects directly, bypassing UsageContext flow | Workaround |
| JS/TS anonymous functions | 0 call edges | `_get_enclosing_function()` returns None for arrow functions | Added position-based lookup to populate `symbol_ref` | Partial fix (JS/TS only) |
| Library exports | No entrypoints for libraries | `symbol_ref` gate skips anonymous exports | Set `symbol_ref` when name resolves | Partial fix (named exports only) |

**Common thread:** All three work around the same gate at `framework_patterns.py:992-993`:

```python
for ctx in usage_contexts:
    if not ctx.symbol_ref:
        continue  # THE GATE
```

The methodology to prevent this exists in `AGENTS.md` ("assume structural until proven otherwise"), but it wasn't followed. The tests passed, PRs merged, and the gate remains — waiting to break the next framework with string-based handlers.

### The Need for Enforcement

Voluntary adherence to methodology isn't sufficient. When the workaround is obvious and the structural fix is harder, the workaround wins. We need **automated enforcement** that forces reflection before shipping.

### The Vendor Lock-In Problem

Currently, governance relies on Claude Code's conventions. Contributors using Gemini CLI, Cursor, Codex CLI, or other tools can't participate in the governance loop. ADR-0001 established that `AGENTS.md` is the canonical source for agent instructions, but there's no equivalent for governance hooks.

### Existing Infrastructure

| Component | Status | Description |
|-----------|--------|-------------|
| `AUTONOMOUS_MODE.txt` | ✅ Exists | File-based gate: "TRUE" continues, "FALSE" stops |
| `AGENTS.md` | ✅ Exists | Methodology documentation (voluntary) |
| `scripts/bakeoff loop` | ✅ Exists | Automated cycling with convergence detection |
| `scripts/bakeoff-reflect` | ✅ Exists | Qualitative analysis |
| Hook enforcement | ❌ Missing | Automated reflection injection |
| Invariant ledger | ❌ Missing | Structured tracking of discovered invariants |

## Decision

### 1. Create Vendor-Agnostic Hook Adapters

Each AI coding tool has different hook mechanisms. We will create adapter scripts that provide a consistent interface:

```
.agent/
├── hooks/
│   ├── claude-code/
│   │   └── stop.sh           # Claude Code Stop hook
│   ├── gemini-cli/
│   │   └── after-agent.sh    # Gemini CLI AfterAgent hook
│   ├── cursor/
│   │   └── hooks.json        # Cursor hooks config
│   └── codex-cli/
│       └── notify.sh         # Codex CLI notification (limited)
├── stop_reflect.md           # The reflection prompt (shared)
├── LOOP                      # Sentinel file (exists = continue)
└── invariant-ledger.md       # Structured invariant tracking
```

### 2. Implement Hook Adapters

#### Claude Code (`Stop` hook)

Claude Code's Stop hook receives JSON via stdin and returns JSON via stdout.
When `decision: "block"` is returned, the `reason` field is sent to Claude as guidance.

**Input schema:**
```json
{
  "session_id": "string",
  "transcript_path": "string",
  "cwd": "string",
  "hook_event_name": "Stop",
  "stop_hook_active": boolean
}
```

**Output schema:**
```json
{
  "decision": "block" | "approve",
  "reason": "string (guidance sent to Claude when blocking)"
}
```

```json
// .claude/hooks.json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR\"/.agent/hooks/claude-code/stop.sh"
          }
        ]
      }
    ]
  }
}
```

```bash
#!/bin/bash
# .agent/hooks/claude-code/stop.sh

# Check autonomous mode
if [[ "$(cat AUTONOMOUS_MODE.txt 2>/dev/null)" != "TRUE" ]]; then
  echo '{"decision": "approve", "reason": "Autonomous mode disabled"}'
  exit 0
fi

# Check if loop sentinel exists
if [[ ! -f .agent/LOOP ]]; then
  echo '{"decision": "approve", "reason": "Loop sentinel removed"}'
  exit 0
fi

# Inject reflection prompt via the "reason" field
REFLECTION_PROMPT=$(cat .agent/stop_reflect.md | jq -Rs .)
cat <<EOF
{
  "decision": "block",
  "reason": $REFLECTION_PROMPT
}
EOF
```

#### Gemini CLI (`AfterAgent` hook)

Gemini CLI's hooks are experimental but support `type: "command"`.
**Important:** Hooks must output JSON only—plain text output causes failures.
Use `decision: "deny"` with `reason` to inject a continuation prompt.

**Input schema:**
```json
{
  "session_id": "string",
  "transcript_path": "string",
  "cwd": "string",
  "hook_event_name": "AfterAgent",
  "prompt": "string",
  "prompt_response": "string",
  "stop_hook_active": boolean
}
```

**Output schema:**
```json
{
  "decision": "allow" | "deny",
  "reason": "string (sent as new prompt when denying)"
}
```

```yaml
# .gemini/config.yaml
hooks:
  AfterAgent:
    - type: command
      command: .agent/hooks/gemini-cli/after-agent.sh
```

```bash
#!/bin/bash
# .agent/hooks/gemini-cli/after-agent.sh

INPUT=$(cat)  # Read JSON from stdin

if [[ "$(cat AUTONOMOUS_MODE.txt 2>/dev/null)" != "TRUE" ]]; then
  echo '{"decision": "allow"}'
  exit 0
fi

if [[ ! -f .agent/LOOP ]]; then
  echo '{"decision": "allow"}'
  exit 0
fi

# Check stop_hook_active to prevent infinite loops
STOP_HOOK_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
if [[ "$STOP_HOOK_ACTIVE" == "true" ]]; then
  echo '{"decision": "allow"}'
  exit 0
fi

# Inject reflection prompt via decision: "deny" with reason
REFLECTION_PROMPT=$(cat .agent/stop_reflect.md | jq -Rs .)
cat <<EOF
{
  "decision": "deny",
  "reason": $REFLECTION_PROMPT
}
EOF
```

#### Cursor (`stop` hook)

Cursor's hooks receive JSON via stdin and return JSON via stdout.
Return `followup_message` to automatically continue the agent loop.
Default loop limit is 5 iterations to prevent infinite loops.

**Input schema:**
```json
{
  "conversation_id": "string",
  "status": "completed" | "aborted" | "error",
  "loop_count": number
}
```

**Output schema:**
```json
{
  "followup_message": "string (optional, auto-submitted as next user message)"
}
```

```json
// .cursor/hooks.json
{
  "version": 1,
  "hooks": {
    "stop": [
      {
        "command": ".agent/hooks/cursor/stop.sh",
        "loop_limit": 10
      }
    ]
  }
}
```

```bash
#!/bin/bash
# .agent/hooks/cursor/stop.sh

INPUT=$(cat)  # Read JSON from stdin

if [[ "$(cat AUTONOMOUS_MODE.txt 2>/dev/null)" != "TRUE" ]]; then
  echo '{}'
  exit 0
fi

if [[ ! -f .agent/LOOP ]]; then
  echo '{}'
  exit 0
fi

# Check loop_count to prevent infinite loops
LOOP_COUNT=$(echo "$INPUT" | jq -r '.loop_count // 0')
if [[ "$LOOP_COUNT" -ge 5 ]]; then
  echo '{}'
  exit 0
fi

# Return followup_message to continue the agent loop
REFLECTION_PROMPT=$(cat .agent/stop_reflect.md | jq -Rs .)
cat <<EOF
{
  "followup_message": $REFLECTION_PROMPT
}
EOF
```

#### Codex CLI (limited)

Codex CLI only supports `notify = [...]` in config.toml — **cannot block or inject prompts**.
The notify script receives a JSON argument (not stdin) with event details.
Currently only `agent-turn-complete` events are supported.

**Input schema (command-line argument):**
```json
{
  "type": "agent-turn-complete",
  "thread-id": "string",
  "turn-id": "string",
  "cwd": "string",
  "input-messages": [...],
  "last-assistant-message": "string"
}
```

**Output:** Stderr is displayed to user. Cannot affect agent behavior.

```toml
# ~/.codex/config.toml
notify = [".agent/hooks/codex-cli/notify.sh"]
```

```bash
#!/bin/bash
# .agent/hooks/codex-cli/notify.sh
# LIMITATION: Can only notify, cannot block or inject prompt

JSON_ARG="${1:-"{}"}"  # Note: quotes required to avoid bash brace expansion
EVENT_TYPE=$(echo "$JSON_ARG" | jq -r '.type // "unknown"')

if [[ "$EVENT_TYPE" != "agent-turn-complete" ]]; then
  exit 0
fi

if [[ "$(cat AUTONOMOUS_MODE.txt 2>/dev/null)" == "TRUE" ]]; then
  if [[ -f .agent/LOOP ]]; then
    # Output full reflection prompt to stderr - gets it into context window
    cat >&2 <<'EOF'
════════════════════════════════════════════════════════════════════
  AUTONOMOUS MODE ACTIVE - REFLECTION REQUIRED BEFORE STOPPING
  (If Codex CLI does not auto-continue, review and manually proceed)
════════════════════════════════════════════════════════════════════

EOF
    cat .agent/stop_reflect.md >&2
  fi
fi
```

### 3. Define the Reflection Prompt

```markdown
<!-- .agent/stop_reflect.md -->
# Stop Reflection Protocol

Before stopping, complete this checklist:

## 1. Current State
- [ ] What CRITICAL/HIGH signals remain from last bakeoff run?
- [ ] What was the last change made?

## 2. Invariant Check
For each remaining signal, state the violated invariant:
> "In this system, X must always be true because Y depends on it."

## 3. Structural vs Workaround
For the last change made:
- [ ] Does it bypass a problematic code path, or fix/remove that path?
- [ ] If bypass: what is the root cause, and when will it be fixed?

## 4. Scope Expansion
- [ ] Same language, different construct?
- [ ] Different language, same pattern?
- [ ] Different pipeline stage?

## 5. Decision
- [ ] If root cause unfixed and analogous issues exist: **DO NOT STOP** — fix the root cause
- [ ] If root cause fixed or truly isolated: document in invariant ledger, then stop

## Current Root Causes (Known Unfixed)
- `symbol_ref` gate at `framework_patterns.py:992-993`
```

### 4. Create the Invariant Ledger

```markdown
<!-- .agent/invariant-ledger.md -->
# Invariant Ledger

## INV-001: Call Attribution Completeness
- **Statement:** Every emitted `calls` edge has a non-null caller symbol
- **Status:** ⚠️ PARTIALLY ADDRESSED
- **Root cause:** JS/TS arrow function special-case early-return
- **Fix:** Position-based lookup in `_get_enclosing_function()`
- **Limitation:** JS/TS only; Kotlin/Scala lambdas still vulnerable
- **Regression tests:** `test_js_ts.py::TestCallbackCallAttribution`

## INV-002: Usage-to-Concept Flow
- **Statement:** Usage patterns extracted by analyzers become concepts on nodes
- **Status:** ❌ UNFIXED
- **Root cause:** `symbol_ref` gate at `framework_patterns.py:992-993`
- **Workarounds:**
  - Rails: Direct Symbol creation (bypasses UsageContext flow)
  - Library exports: Set `symbol_ref` when name resolves
- **Affected frameworks:** Rails, Django string views, any string-based handler reference
- **Regression tests:** `test_ruby.py::test_rails_routes` (tests workaround, not fix)

## INV-003: [Template for new invariants]
- **Statement:** [What must always be true]
- **Status:** [UNFIXED | PARTIALLY ADDRESSED | FIXED]
- **Root cause:** [File:line or description]
- **Fix:** [What was done]
- **Limitation:** [What's still broken]
- **Regression tests:** [Test names]
```

### 5. Add Governance to Pre-Commit Checklist

Update `AGENTS.md` to include governance check:

```markdown
## Pre-Commit Checklist
Run these checks before every commit:
```bash
# ... existing checks ...

# 5. Governance check (if fixing a bakeoff signal)
cat .agent/invariant-ledger.md | grep -A5 "Status: ❌"
# If your change relates to an UNFIXED invariant, fix the root cause first
```

## Consequences

### Positive

1. **Enforcement over documentation:** Hooks actively block premature stopping
2. **Vendor portability:** Contributors using any supported tool participate in governance
3. **Institutional memory:** Invariant ledger persists knowledge across sessions
4. **Reduced whack-a-mole:** Root causes get fixed instead of worked around

### Negative

1. **Per-tool maintenance:** Each AI tool needs its own adapter script
2. **Hook API instability:** Tool hook APIs are young and may change
3. **Codex CLI gap:** Limited hook support means weaker enforcement for Codex users
4. **Friction:** Legitimate stops may require extra steps

### Neutral

1. **Backward compatible:** Tools without hooks continue to work (just without enforcement)
2. **Opt-in:** `AUTONOMOUS_MODE.txt` must be "TRUE" for hooks to engage

## Implementation Checklist

### Phase 1: Infrastructure

- [x] Create `.agent/` directory structure
- [x] Write `stop_reflect.md` reflection prompt
- [x] Initialize `invariant-ledger.md` with INV-001, INV-002
- [x] Create `.agent/LOOP` sentinel mechanism

### Phase 2: Hook Adapters

- [x] **Claude Code:** Implement `stop.sh` with correct JSON schema (2026-01-27)
- [x] **Gemini CLI:** Implement `after-agent.sh` with correct JSON schema (2026-01-27)
- [x] **Cursor:** Implement `stop.sh` with `followup_message` output (2026-01-27)
- [x] **Codex CLI:** Implement `notify.sh` (limited enforcement) (2026-01-27)

### Phase 3: Integration

- [x] Update `AGENTS.md` with governance checklist
- [x] Add `.agent/` to repository
- [ ] Document hook setup in `docs/contributing.md`
- [ ] Test full loop: bakeoff signal → fix → reflection → verify

### Phase 4: Fix the Root Cause

- [ ] **Address `symbol_ref` gate:** Either remove the gate or ensure all analyzers populate `symbol_ref`
- [ ] **Generalize position-based lookup:** Extract from JS/TS into shared helper
- [ ] **Remove workarounds:** Once gate is fixed, undo Rails direct-symbol-creation hack
- [ ] **Verify:** Run bakeoff on frameworks with string-based handlers (Rails, Django, Phoenix)

## References

- `docs/governance-case-critiques.md`: Full technical analysis with case critiques
- ADR-0001: Portable Agent Instructions (establishes `AGENTS.md` as canonical)
- `framework_patterns.py:992-993`: The `symbol_ref` gate
- [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks.md)
- [Gemini CLI Hooks Reference](https://geminicli.com/docs/hooks/reference/)
- [Cursor Hooks Docs](https://cursor.com/docs/agent/hooks)
- [Codex CLI Config Reference](https://developers.openai.com/codex/config-reference/)
- [Codex CLI Hooks Discussion](https://github.com/openai/codex/discussions/2150)
