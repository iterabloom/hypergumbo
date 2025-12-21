# 1. Portable Agent Instructions

Date: 2025-12-20
Status: Accepted

## Context
We use LLM-assisted development tools (Claude, Gemini, Cursor, etc.). Each tool typically encourages a vendor-specific configuration file (e.g., `CLAUDE.md`, `.cursor/rules`) to define coding standards and workflows.

Allowing these files to evolve independently leads to **instruction drift**: the rules for Claude diverge from the rules for Cursor. Furthermore, relying on vendor-specific formats locks the repository workflow to specific tools, reducing portability for contributors who may prefer different agents.

## Decision
We will maintain a **canonical, tool-agnostic source of truth** for agent instructions in `AGENTS.md` at the repository root.

Vendor-specific files (`CLAUDE.md`, `GEMINI.md`, etc.) are permitted only as **thin adapters** that import the canonical source. They must not contain independent rules or guidance.

### Policy Rules
1. **Canonical Source:** `AGENTS.md` is the authority.
2. **Adapters:** Vendor files must only contain import directives (e.g., `@AGENTS.md`).
3. **Guardrails:** Vendor-specific config is allowed only for mechanical enforcement (permissions, file access) where no portable equivalent exists.
4. **CI Enforcement:** We validate via script that adapters remain thin imports.
5. **Workflow:** We follow Trunk-Based Development. Agents should favor small, frequent integrations over long-lived feature branches.

## Consequences

### Positive
*   **Single Source of Truth:** Workflow changes only need to happen in one place.
*   **Portability:** New agents can be onboarded simply by adding a one-line adapter.
*   **Drift Prevention:** It is mechanically impossible for one agent to have different coding standards than another.

### Negative
*   **Context Window Usage:** We force the loading of the full `AGENTS.md` into every session context, which consumes tokens.
*   **Feature Loss:** We cannot leverage unique prompt-engineering features of specific tools (e.g., Cursor's strict "alwaysApply" granular scopes) if they don't have a generic Markdown equivalent.

## References
*   Implementation details can be found in `scripts/validate-agents.sh` and `AGENTS.md`.
*   Tool compatibility is tracked in `docs/agents/tool-compatibility.md`.
