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

Works with Python, Go, Rust, TypeScript, Java, C/C++, Ruby, and [60+ more languages](LANGUAGES.md) — detected automatically.

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

# See which language analyzers are active for this repo
hypergumbo catalog
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

Only follow actual data dependencies (tighter than structural slicing):

```bash
hypergumbo slice --entry "processPayment" --dataflow
```

`--dataflow` follows only write-to-read edges — useful when you want to trace where a value actually flows, not everything structurally reachable.

---

## I/O Boundaries

Find every place your code touches the outside world — filesystem, network, subprocesses, environment variables:

```bash
hypergumbo io-boundaries
```

Group results by source file instead of boundary type:

```bash
hypergumbo io-boundaries --by-file
```

Filter to a specific boundary type:

```bash
hypergumbo io-boundaries --boundary net_send
```

Exclude test files:

```bash
hypergumbo io-boundaries -x
```

For polyglot repos, I/O is traced across language boundaries (Python→Rust, Go→C, etc.) automatically. Use `--json` for scripting or CI integration.

---

## Verify Security Claims

Codify what your code should and shouldn't do, then check automatically.

Create a `security-claims.yaml` file:

```yaml
claims:
  - id: SC-001
    text: "No subprocess calls"
    constraint:
      boundary: subprocess
      must_not_exist: true

  - id: TF-001
    text: "Plaintext must not reach the filesystem"
    constraint:
      taint_flow:
        source_taint: plaintext
        prohibited_sink_zone: host_fs
```

Then verify:

```bash
hypergumbo verify-claims --claims security-claims.yaml
```

Exit code 1 means violations were found. Use `--json` for CI pipelines.

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

### Security Audit

```bash
# What touches the network?
hypergumbo io-boundaries --boundary net_send

# Enforce it in CI
hypergumbo verify-claims --claims security-claims.yaml --json
echo $?  # 0 = all claims confirmed, 1 = violations found
```

### Polyglot Repo

```bash
# Analyze a mixed Go/C or Python/Rust repo — no configuration needed
hypergumbo .

# See which analyzers are active
hypergumbo catalog

# I/O that crosses FFI boundaries is traced automatically
hypergumbo io-boundaries
```

---

## Large Codebases

For repos with vendored dependencies or lots of test files:

```bash
# Skip test files
hypergumbo . -x

# First-party code only (skip node_modules, vendor, etc.)
hypergumbo run . --first-party-only
hypergumbo .  # Uses the filtered results

# Skip specific directories
hypergumbo . --exclude "generated/**" --exclude "vendor/**"
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
| Find all I/O operations | `hypergumbo io-boundaries` |
| Filter I/O by type | `hypergumbo io-boundaries --boundary net_send` |
| Verify security claims | `hypergumbo verify-claims --claims claims.yaml` |
| Data-dependency slice | `hypergumbo slice --entry "X" --dataflow` |
| Skip specific paths | `hypergumbo . --exclude "vendor/**"` |
| See active analyzers | `hypergumbo catalog` |
