<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Use Cases

## Start Here

```bash
git clone https://github.com/tiangolo/fastapi
cd fastapi
hypergumbo .
```

That's it. Paste the output into ChatGPT, Claude, or any LLM. You get:
- Language breakdown and structure
- Detected frameworks
- Key symbols ranked by importance
- Entry points (routes, CLI commands, main functions)
- Source code for important files

First run takes 10-60 seconds (analyzing). Subsequent runs are instant (cached).

---

## Adjust the Detail Level

```bash
hypergumbo . -t 1000    # Brief (fits small context windows)
hypergumbo . -t 4000    # Balanced (default)
hypergumbo . -t 8000    # Detailed
hypergumbo . -t 16000   # Comprehensive
```

Exclude test files for cleaner output:

```bash
hypergumbo . -x
```

Omit source code (just the summary):

```bash
hypergumbo . --no-source
```

---

## Query Commands

Once you've run `hypergumbo .` once (analysis is cached), you can query:

```bash
# List all HTTP routes
hypergumbo routes

# Search for symbols
hypergumbo search "User"
hypergumbo search "handle" --kind function

# Understand a symbol (what calls it, what it calls)
hypergumbo explain "processPayment"
hypergumbo explain "UserService" --no-source  # Omit source code

# Browse symbols by connectivity
hypergumbo symbols
hypergumbo symbols -x --limit 50
```

---

## Slicing: Extract Relevant Code

Find what a function depends on:

```bash
hypergumbo slice --entry "main"
```

Find what calls a function (reverse):

```bash
hypergumbo slice --entry "processPayment" --reverse
```

List entry points detected in the codebase:

```bash
hypergumbo slice --list-entries
```

---

## Real Workflows

### Code Review

```bash
# What calls the function being changed?
hypergumbo slice --entry "processPayment" --reverse

# What does it call?
hypergumbo slice --entry "processPayment"
```

Paste both outputs into your LLM with the diff: "What could break?"

### Onboarding

```bash
hypergumbo . -t 4000
```

Paste into LLM: "I'm new to this codebase. Where should I start?"

### Debug a Route

```bash
# List all routes
hypergumbo routes

# Get details on one (includes source by default)
hypergumbo explain "get_user"
```

### Find Untested Code

```bash
hypergumbo test-coverage
hypergumbo test-coverage --max-tests 0  # Only untested
```

### Prepare Context for AI Coding

```bash
hypergumbo . -t 8000 > context.md
```

Then paste `context.md` into Claude Code, Cursor, or Copilot. Source code is included by default.

---

## Large Codebases

For repos with vendored dependencies or lots of test files:

```bash
# Skip test files
hypergumbo . -x

# First-party code only (skip node_modules, vendor, etc.)
hypergumbo run . --first-party-only
hypergumbo .  # Uses the filtered results
```

---

## How Caching Works

Results are cached in `~/.cache/hypergumbo/`. The cache auto-invalidates when files change.

```bash
hypergumbo .           # First run: analyzes (10-60s), then generates sketch
hypergumbo . -t 8000   # Instant: uses cache, different token budget
hypergumbo . -x        # Instant: uses cache, excludes tests in output
```

Force a fresh analysis:

```bash
hypergumbo run .
```

---

## Quick Reference

| Goal | Command |
|------|---------|
| Overview for LLM | `hypergumbo .` |
| More detail | `hypergumbo . -t 8000` |
| Skip tests | `hypergumbo . -x` |
| Without source code | `hypergumbo . --no-source` |
| List routes | `hypergumbo routes` |
| Search symbols | `hypergumbo search "X"` |
| Explain a symbol | `hypergumbo explain "X"` |
| What calls X? | `hypergumbo slice --entry "X" --reverse` |
| What does X call? | `hypergumbo slice --entry "X"` |
| Browse symbols | `hypergumbo symbols` |
| Test coverage | `hypergumbo test-coverage` |
