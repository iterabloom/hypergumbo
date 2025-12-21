#!/bin/bash
set -e

echo "Validating Agent Policy..."

# 1. Canary Check
if ! grep -q "<!-- CANARY: agents-policy-v" AGENTS.md; then
    echo "FAIL: AGENTS.md missing canary."
    exit 1
fi

# 2. Adapter Check Function
check_adapter() {
    local file="$1"
    local pattern="$2"
    if [ -f "$file" ]; then
        # Remove whitespace/newlines for checking
        content=$(cat "$file" | tr -d '[:space:]')
        if [[ ! "$content" =~ $pattern ]]; then
            echo "FAIL: $file contains unauthorized rules."
            exit 1
        else
            echo "OK: $file"
        fi
    fi
}

# 3. Validate Adapters
# Claude: Only allows "@AGENTS.md"
check_adapter "CLAUDE.md" "^@AGENTS\.md$"

# Gemini: Only allows "@./AGENTS.md"
check_adapter "GEMINI.md" "^@\./AGENTS\.md$"

# Cursor: Allows frontmatter + import
# Regex allows "---...---SeeAGENTS.mdforallguidance.@AGENTS.md"
check_adapter ".cursor/rules/00-canonical.mdc" "@AGENTS\.md$"

echo "Agent policy validation passed."
