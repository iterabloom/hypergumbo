#!/bin/bash
# test-transcript-pipeline.sh — Dry-run the transcript sync pipeline.
#
# Runs each component in isolation with verbose output so you can see
# what's happening at each stage. Uses a real transcript file as input
# but doesn't modify any session state.
#
# Usage:
#   test-transcript-pipeline.sh                    # uses current session
#   test-transcript-pipeline.sh <path-to-jsonl>    # uses a specific file
#   test-transcript-pipeline.sh --live             # watches current session live
#
# Environment:
#   OPENROUTER_API_KEY  — set to make real LLM calls; unset for prompt-only dry run
#   TRANSCRIPT_THRESHOLD — confidence threshold (default: 7)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
FILTER_SCRIPT="$SCRIPT_DIR/filter-transcript.py"
HOOK_SCRIPT="$SCRIPT_DIR/on_transcript_change.py"
WORKDIR=$(mktemp -d /tmp/transcript-test-XXXXXX)

cleanup() {
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

echo "=== Transcript Sync Pipeline Test ==="
echo "Workdir: $WORKDIR"
echo ""

# --- Determine source transcript ---
MODE="static"
SRC=""

if [[ "${1:-}" == "--live" ]]; then
    MODE="live"
    # Find current session transcript
    SESSIONS_DIR="$HOME/.claude/sessions"
    if [[ -d "$SESSIONS_DIR" ]]; then
        for f in "$SESSIONS_DIR"/*.json; do
            PID=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('pid',''))" "$f" 2>/dev/null || true)
            if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
                SESSION_ID=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('sessionId',''))" "$f" 2>/dev/null || true)
                if [[ -n "$SESSION_ID" ]]; then
                    MANGLED=$(echo "$REPO_ROOT" | tr '/_' '-')
                    SRC="$HOME/.claude/projects/${MANGLED}/${SESSION_ID}.jsonl"
                    break
                fi
            fi
        done
    fi
    if [[ -z "$SRC" || ! -f "$SRC" ]]; then
        echo "ERROR: Could not find current session transcript."
        exit 1
    fi
    echo "Mode: LIVE (watching current session)"
elif [[ -n "${1:-}" && -f "${1:-}" ]]; then
    SRC="$1"
    echo "Mode: STATIC (one-shot analysis)"
else
    # Default: find the current session
    SESSIONS_DIR="$HOME/.claude/sessions"
    if [[ -d "$SESSIONS_DIR" ]]; then
        for f in "$SESSIONS_DIR"/*.json; do
            PID=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('pid',''))" "$f" 2>/dev/null || true)
            if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
                SESSION_ID=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('sessionId',''))" "$f" 2>/dev/null || true)
                if [[ -n "$SESSION_ID" ]]; then
                    MANGLED=$(echo "$REPO_ROOT" | tr '/_' '-')
                    SRC="$HOME/.claude/projects/${MANGLED}/${SESSION_ID}.jsonl"
                    break
                fi
            fi
        done
    fi
    if [[ -z "$SRC" || ! -f "$SRC" ]]; then
        echo "ERROR: No transcript found. Pass a JSONL path as argument."
        exit 1
    fi
    echo "Mode: STATIC (one-shot analysis)"
fi

echo "Source: $SRC"
SRC_LINES=$(wc -l < "$SRC")
SRC_SIZE=$(du -h "$SRC" | cut -f1)
echo "Source size: $SRC_LINES lines, $SRC_SIZE"
echo ""

# --- Stage 1: Filter ---
echo "=== Stage 1: Filter ==="
DEST="$WORKDIR/filtered.jsonl"
STATE="$WORKDIR/filter-state.json"

python3 "$FILTER_SCRIPT" "$SRC" "$DEST" "$STATE"

if [[ -f "$DEST" ]]; then
    DEST_LINES=$(wc -l < "$DEST")
    DEST_SIZE=$(du -h "$DEST" | cut -f1)
    REDUCTION=$(python3 -c "print(f'{(1 - $DEST_LINES/$SRC_LINES)*100:.1f}%')")
    echo "Filtered: $DEST_LINES lines, $DEST_SIZE ($REDUCTION reduction)"
else
    echo "Filtered: 0 lines (no output)"
fi
echo "State: $(cat "$STATE" 2>/dev/null)"
echo ""

# --- Stage 2: Entry selection ---
echo "=== Stage 2: Entry Selection (token budget) ==="
MAX_TOKENS="${TRANSCRIPT_MAX_TOKENS:-16000}"
python3 -c "
import os, sys

path = '$DEST'
max_chars = int($MAX_TOKENS * 4.4)

if not os.path.exists(path):
    print('No filtered transcript.')
    sys.exit(0)

with open(path, 'rb') as f:
    lines = f.readlines()

selected = []
total = 0
for line in reversed(lines):
    if total + len(line) > max_chars:
        break
    selected.append(line)
    total += len(line)

print(f'Budget: {$MAX_TOKENS} tokens (~{max_chars:,} chars)')
print(f'Selected: {len(selected)} of {len(lines)} lines')
print(f'Chars used: {total:,} (~{total/4.4:,.0f} tokens)')
if selected:
    import json
    first = json.loads(selected[-1])
    last = json.loads(selected[0])
    print(f'Time range: {first.get(\"timestamp\",\"?\")} to {last.get(\"timestamp\",\"?\")}')
"
echo ""

# --- Stage 3: LLM calls ---
echo "=== Stage 3: LLM Pipeline ==="
if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
    echo "OPENROUTER_API_KEY not set — showing prompts only (dry run)."
    echo ""
    echo "--- Step 1 prompt (first 500 chars) ---"
    python3 -c "
import os
path = '$DEST'
max_chars = int($MAX_TOKENS * 4.4)
with open(path, 'rb') as f:
    lines = f.readlines()
selected = []
total = 0
for line in reversed(lines):
    if total + len(line) > max_chars:
        break
    selected.append(line)
    total += len(line)
selected.reverse()
recent = b''.join(selected).decode('utf-8', errors='replace')
prompt = 'Below are the latest turns in an agentic coding session. Please distill what the agent\\'s present goals are.\n\n' + recent
print(prompt[:500])
print(f'... ({len(prompt):,} chars total)')
"
    echo ""
    echo "--- Step 2 prompt would include 21 playbook summaries ---"
    echo "(Set OPENROUTER_API_KEY to run the full pipeline)"
else
    echo "OPENROUTER_API_KEY is set — running full pipeline."
    echo ""
    THRESHOLD="${TRANSCRIPT_THRESHOLD:-7}"
    echo "Confidence threshold: $THRESHOLD/10"
    echo ""

    OUTPUT=$(TRANSCRIPT_THRESHOLD="$THRESHOLD" python3 "$HOOK_SCRIPT" "$DEST" 2>&1) || true

    if [[ -n "$OUTPUT" ]]; then
        echo "$OUTPUT"
    else
        echo "(No output — either no playbooks met threshold or API call failed)"
    fi
fi
echo ""

# --- Live mode: watch loop ---
if [[ "$MODE" == "live" ]]; then
    echo "=== Live Mode: Watching for changes ==="
    echo "(Ctrl-C to stop)"
    echo ""
    while true; do
        inotifywait -qq -e close_write "$SRC" 2>/dev/null || continue
        PREV_SIZE=$(stat -c%s "$DEST" 2>/dev/null || echo 0)
        python3 "$FILTER_SCRIPT" "$SRC" "$DEST" "$STATE"
        NEW_SIZE=$(stat -c%s "$DEST" 2>/dev/null || echo 0)
        if [[ "$NEW_SIZE" -gt "$PREV_SIZE" ]]; then
            ADDED=$((NEW_SIZE - PREV_SIZE))
            echo "[$(date +%H:%M:%S)] +${ADDED} bytes filtered → $DEST ($(wc -l < "$DEST") lines total)"
            if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
                OUTPUT=$(python3 "$HOOK_SCRIPT" "$DEST" 2>&1) || true
                if [[ -n "$OUTPUT" ]]; then
                    echo "$OUTPUT"
                    echo ""
                fi
            fi
        else
            echo "[$(date +%H:%M:%S)] (filtered — no new meaningful content)"
        fi
    done
fi

echo "=== Done ==="
