<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# hypergumbo

[![PyPI](https://img.shields.io/pypi/v/hypergumbo.svg)](https://pypi.org/project/hypergumbo/)
[![License](https://img.shields.io/pypi/l/hypergumbo.svg)](https://pypi.org/project/hypergumbo/)

hypergumbo is a local-first CLI that generates surveys and sketches from source code. The goal of this project is to efficiently help developers and LLMs understand a codebase.

```bash
pip install hypergumbo
```

> Requires Python 3.10+. For optional extras (embeddings, gitleaks, grammars), run `hypergumbo add-extras` after installing.

> Installing on a personal machine? Read the [Security](https://github.com/iterabloom/hypergumbo/blob/dev/README.md#security-as-of-2026-11) section first: what hypergumbo reads, writes and downloads, which commands run other code, and how its claims are checked.

> Intel Mac users: Some tree-sitter packages lack x86_64 wheels. See [docs/INTEL_MAC.md](https://github.com/iterabloom/hypergumbo/blob/dev/docs/INTEL_MAC.md) for a Docker-based workaround.

```bash
git clone https://github.com/iterabloom/hypergumbo
hypergumbo hypergumbo/
```

Output:

```bash
# hypergumbo

hypergumbo is a local-first CLI that generates surveys and sketches from source code. The goal of this project is to efficiently help developers and LLMs understand a codebase. > Requires Python 3.10+.

## Overview
Python (89%), Markdown (5%), Yaml (3%), Bash (2%)
2,379 files    (  944 non-test + 1,435 test)
~928,098 LOC (~379,495 non-test + ~548,603 test)

Vendored third-party code analysed as tier 3, not counted in the language breakdown: packages/hypergumbo-core/src/hypergumbo_core/vendor/ (c, cpp, json; 13 files). To skip: --exclude <dir>.

## Structure

` ` `
     hypergumbo/
  8  ├── .ci
  8  │   └── .gitignore
  3k ├── .githooks
304  │   ├── brand-scrub.sh
  2k │   └── [and 9 other items]
813k ├── packages
433k │   ├── hypergumbo-core
168k │   │   ├── src
168k │   │   │   └── hypergumbo_core
  4  │   │   │       ├── __main__.py
 13k │   │   │       ├── cli.py
  2k │   │   │       ├── ir.py
153k │   │   │       └── [and 126 other items]
265k │   │   ├── tests
 18k │   │   │   ├── test_framework_patterns.py
247k │   │   │   └── [and 562 other items]
131  │   │   └── [and 2 other items]
 71k │   ├── hypergumbo-tracker
 23k │   │   ├── src
 23k │   │   │   └── hypergumbo_tracker
  4k │   │   │       ├── cli.py
 20k │   │   │       └── [and 37 other items]
 48k │   │   └── [and 4 other items]
309k │   └── [and 6 other items]
 26k ├── scripts
  3k │   ├── lib
  2k │   │   ├── forgejo-api.sh
994  │   │   └── [and 4 other items]
 23k │   └── [and 69 other items]
 38k ├── tests
894  │   ├── test_agent_supervisor.py
 37k │   └── [and 142 other items]
107  ├── .gitignore
 75  ├── .gitleaks.toml
116  ├── conftest.py
147  ├── pyproject.toml
 12  ├── setup.py
 51k └── [and 12 other items]
` ` `
Counts and LOC cover only files with at least one detected symbol; files with none, such as JSON data or extensionless scripts, are left out.

## Frameworks

- protobuf
- pytest
- starlette

## Tests

1435 test files · cargo test, pytest, unittest

*4700 of 7446 functions (63%) are reached from tests in the static call graph; tests that reach code through dynamic dispatch, reflection, a macro or another process are not seen, so more may be tested than counted. `hypergumbo test-coverage` lists the functions not reached.*

## Configuration
[...]
```

**[See full example output](https://github.com/iterabloom/hypergumbo/blob/dev/docs/example-output.md)**

Use `-t` to control the token budget:
```bash
hypergumbo . -t 1000   # brief overview (structure only)
hypergumbo . -t 4000   # good balance for most LLMs
hypergumbo . -t 8000   # detailed with many symbols
```

## Security (as of 2026-11)

Please report vulnerabilities as described in [SECURITY.md](https://github.com/iterabloom/hypergumbo/blob/dev/SECURITY.md#reporting-a-vulnerability).

Hypergumbo is about 335,000 lines of Python, almost entirely generated over 11 months of full-time agentic coding steered by one human (me, josh-iterabloom, aka jgstern). There are also about 550,000 lines of agent-generated automated tests, for 100% line coverage.[^1] Is it safe to run hypergumbo (v10.0) even though its human author understands much less of it than would a typical human who had hypothetically written 335k lines of 100%-passing Python code in, say, 2016, before LLMs?

I think so, and as of November [JOSH WILL INSERT DATE HERE]th 2026, I run it on my daily driver Mac Mini, which has all of my sensitive and important personal documents (eg tax returns, medical records) on it. (And I have iCloud enabled and I also back up the boot disk using Time Machine, but that's just common sense.)

Caveats. First of all, this is open source software (AGPL-3.0-or-later). Like all OSS, I make no guarantee this will not cause the sun to explode (or accidentally delete all your files, etc). That's a standard disclaimer that every open source license makes. I have of course done my best to make hypergumbo not do those things, but just like everyone else, I make no guarantee.

The second caveat is that hypergumbo is *deliberately* overambitious; it supports static analysis of over 100 programming languages (at least nominally). For some of the earlier months, the agent instructions included ["turn it up to 11"](https://www.youtube.com/watch?v=QJzJCjzfEEk) as the engineering philosophy. (I cut that one because iirc it was a spaghetti-inducing instruction.)[^2]

**How I check what it touches.** Three separate checks:

1. **Every risky call, found mechanically.** A scan that doesn't use hypergumbo at all (`scripts/check-io-allowlist`, standard library and PyYAML only) finds every place in the source that writes or deletes a file, starts a process, opens a network connection or a server, imports code dynamically, or changes the environment: about 300 places today. Each one must be listed in a reviewed allowlist with its purpose and the commands that reach it. An unlisted place fails CI, and so does a command list that disagrees with a call graph of the code.
2. **hypergumbo checks itself.** Its own dataflow analysis (inspired by [Joern](https://joern.io/)) checks 23 claims about its own source, such as "analysis commands never touch the network". All 23 currently pass *with caveats*: the analysis can't see inside the programs it launches, and it couldn't work out the receiver type of 74% of the method calls in its own code. With the optional scip-python backend that falls to 7%, but the self-check can't use it yet, because it then reaches library types the catalogue doesn't yet vouch for.
3. **Watching it run.** At each release, the installed tool runs over six real repositories under `strace`, which watches the process itself. So it also sees what dependencies and compiled extensions do, but only on the runs it watched.

Finding the risky calls is mechanical. Deciding which kinds of call count as risky, and whether each listed place is benign, rests on judgement. Those judgements were made by me and an LLM, from research on the Python standard library, and LLM-assisted reviews have found bugs in places that were already listed. That's how I've been thinking about security: hypergumbo is a black box, or perhaps a grey box, and I don't know everything going on inside it. But if we know every place it writes, connects or executes, and what each of those does, that's most of what matters. That is a big "if", but it's the sort of thing that can keep improving through small contributions from many contributors.

**Before you point it at code you don't trust:**

- `--backend rust-analyzer` **runs the analysed repository's `build.rs` and proc macros as you.** It is off unless you turn it on, and nothing inside the repository can turn it on. Exporting `HYPERGUMBO_RUST_ANALYZER=1` turns it on for every repository.
- The analysed repository's own `.hypergumbo.toml` *can* switch on the scip-python backend, which analyses statically and executes nothing from the repository.
- hypergumbo has no sandbox of its own: a file crafted to exploit a parser bug would run with your permissions.

**On any repository, your own included:** every `sketch` hands gitleaks the sketch's text, which quotes your source. `install-gitleaks` installs gitleaks 8.30.0 only if the download matches the SHA-256 gitleaks published for it; gitleaks signs nothing, and a gitleaks you installed another way is used as it is.

[More details: what a default run does, commands that download, compile or run other programs, the tracker (`htrac`), how these statements are checked, analysing a repository you don't trust, and running it confined.](https://github.com/iterabloom/hypergumbo/blob/dev/SECURITY.md#using-hypergumbo-safely)

[^1]: CI requires 100% test coverage (although tests only test what the test writer thought to test). I tell the agent to follow test-driven development (red -> green -> refactor) and trunk-based development. Anecdotally, agents in this repo follow the TDD instruction more often than not (but I don't have more precision than that -- I have seen them "forget"), and as for trunk-based development, I have never seen an agent not do it, except for when I have explicitly requested parallel worktrees with subagents on a couple of rare occasions. More detail in AGENTS.md.

[^2]: Part of hypergumbo's thesis is that it is possible to use LLMs and agentic coding to write extremely ambitious and complex software that is *not* spaghetti-code. And that although I have not read the overwhelming majority of hypergumbo, if I (or another human Python programmer) chose to, I would understand what I was reading. And that the way to create such artifacts is (a) to have a bunch of careful git hooks, linting, CI, thoughtful agent instructions, a Design Specification, Architecture Decision Records, unit tests, an issue tracker, a lab notebook (presently outside the repo), and other such infrastructure, and (b) use hypergumbo itself. I am less sure of (b) than I am of (a), because so far I have only used hypergumbo heavily during development of... hypergumbo, which is almost completely Python. A friend of mine has also used hypergumbo with agentic development of an Elixir codebase.

## Two Outputs

**Sketch** (`hypergumbo .`) — Token-budgeted Markdown sized for LLM context windows. Ranks symbols by graph centrality (★ = most connected).

**Survey** (`hypergumbo survey`) — Full JSON with all symbols, edges, and provenance tracking. Use this for programmatic analysis.

## CLI Commands

```bash
hypergumbo [path]              # Markdown sketch (default)
hypergumbo survey [path]       # Full JSON survey
hypergumbo slice --entry X     # Subgraph from entry point
hypergumbo io-boundaries       # Find all I/O (filesystem, network, subprocess, env, IPC, browser storage)
hypergumbo verify-claims ...   # Verify security claims against analysis
hypergumbo routes [path]       # List HTTP routes
hypergumbo search <query>      # Search symbols
hypergumbo symbols [path]      # Browse symbols with connectivity
hypergumbo explain <symbol>    # Detailed symbol info
hypergumbo test-coverage       # Analyze test coverage (transitive)
hypergumbo catalog             # List analysis passes
```

Useful flags:
```bash
hypergumbo . -x                                # exclude test files (cleaner output)
hypergumbo . --no-source                       # omit source code (included by default)
hypergumbo . --no-progress                     # hide progress indicator (sketch/run only; on by default)
HG_HEARTBEAT=10 hypergumbo .                   # stack heartbeat every 10s (default 30; 0 disables)
hypergumbo io-boundaries --show-external-potential   # opt into the (large) external-potential bucket (text output)
hypergumbo --help --all                        # comprehensive help for all commands
```

### Catalogues: built-in, community, yours, in-repo

Every analysis runs on catalogue rows — which calls are I/O, which values are tainted, what makes them safe. Each row is one of four tiers, declared on its file's `provenance:` line: **built-in** (standard-library rows hypergumbo vouches for), **community** (third-party rows it ships without maintaining — they can add a finding but never make a verdict cleaner), **yours** (files under `$XDG_CONFIG_HOME/hypergumbo/` or named on the command line), and **in-repo** (a repository's own `.hypergumbo.toml` catalogue keys, loaded only when you opt in with `--in-repo-catalogues` or `hypergumbo trust-catalogues`). [docs/CATALOGUES.md](https://github.com/iterabloom/hypergumbo/blob/dev/docs/CATALOGUES.md) says where each family's files live and how to add a row.

Your persistent taint model lives in `$XDG_CONFIG_HOME/hypergumbo/taint_sources.d/`, `taint_sinks.d/` and `taint_sanitizers.d/` (`hypergumbo init-catalogs` creates them). For one run, name files on the command line:

```bash
hypergumbo verify-claims --claims claims.yaml \
    --taint-sources    myrepo/taint/sources.yaml \
    --taint-sinks      myrepo/taint/sinks/ \
    --taint-sanitizers myrepo/taint/sanitizers.yaml
```

Each flag accepts a YAML file or a directory (globbed as `*.yaml`), and is repeatable. The same paths can be declared inside the claims YAML under `extra_catalogs: {io_primitives, sources, sinks, sanitizers}` — relative paths resolve against the claims-file directory. Precedence, lowest first: your channel directories, the claims file, the flags; an entry whose `(module, name, kind)` triple matches a built-in replaces it, and sanitizers concatenate.

Results are automatically cached in `~/.cache/hypergumbo/`. Just run:
```bash
hypergumbo .    # auto-runs analysis if no cache exists, then generates sketch
```

The cache auto-invalidates when source files change. See [docs/CACHE.md](https://github.com/iterabloom/hypergumbo/blob/dev/docs/CACHE.md) for details.

See `hypergumbo --help` for all options.

## What It Understands

- **Language analyzers**: Python, JS/TS, Java, Rust, Go, C/C++, and [many more](https://github.com/iterabloom/hypergumbo/blob/dev/docs/LANGUAGES.md)
- **Linkers**: Tier 2 edge-recovery passes across four subcategories — Protocol (HTTP, WebSocket, message queues, SQL), Bridge (JNI, wasm_bindgen, Tauri IPC, language-pair FFI), Framework (gRPC, GraphQL, React components, DI resolution, ORM), Infrastructure (containment, inheritance, module imports). [Full catalogue](https://github.com/iterabloom/hypergumbo/blob/dev/docs/LINKERS.md).
- **Framework patterns**: FastAPI, Django, Rails, Spring Boot, Phoenix, Express, and [many more](https://github.com/iterabloom/hypergumbo/blob/dev/docs/FRAMEWORKS.md)
- **I/O boundary detection**: Maps every call chain that reaches the filesystem, network, subprocess, environment, IPC, or browser-local storage — across FFI boundaries
- **Taint-flow analysis**: Traces data from sensitive sources (environment variables, received network input, crypto outputs, key material) to sinks in ten built-in trust zones (`host_fs`, `network`, `subprocess`, `host_env`, `ipc`, `browser_storage`, `database`, `logging`, `code_execution`, `dom_injection`), with sanitizer awareness; a project can declare its own zones
- **Supply chain tiers**: Classifies code as first-party, internal, external, or derived for dependency-aware analysis

## How It Works

1. **Profile**: Scan the repo for languages, file counts, LOC, and frameworks
2. **Analyze**: Run language-specific analyzers to extract symbols and edges
3. **Enrich**: Tag symbols with framework concepts (routes, models, tests) via YAML patterns
4. **Link**: Connect symbols across files and language boundaries (JS fetch → Python route)
5. **Classify**: Assign supply chain tiers (first-party, internal, external, derived)
6. **Rank**: Score symbols by centrality
7. **Output**: Generate Markdown sketch or JSON survey

Taint-flow analysis is separate: `hypergumbo verify-claims` runs it against a claims file.

### The Internal Representation

All analyzers produce the same IR types:

- **Symbol**: A code element (function, class, method) with name, location, and stable ID
- **Edge**: A relationship between symbols (calls, imports, extends, implements)
- **Span**: Source location within a file (start/end line and column)

This uniform IR is what allows all language analyzers and linkers (Protocol / Bridge / Framework / Infrastructure — see [ADR-3bbb](https://github.com/iterabloom/hypergumbo/blob/dev/docs/adr/3bbb-linker-subcategory-restoration.md)) to work together coherently.

## Architecture

```
packages/
├── hypergumbo-core/           # CLI, IR, slice, sketch, linkers
│   └── src/hypergumbo_core/
│       ├── cli.py             # Entry point
│       ├── ir.py              # Symbol, Edge, Span
│       ├── sketch.py          # Token-budgeted Markdown
│       ├── slice.py           # Subgraph extraction
│       ├── linkers/           # Tier 2 edge-recovery passes (Protocol/Bridge/Framework/Infrastructure)
│       └── frameworks/        # Framework detection (YAML patterns)
├── hypergumbo-lang-mainstream/  # Python, JS, Java, Go, Rust, etc.
├── hypergumbo-lang-common/      # Haskell, Elixir, GraphQL, etc.
├── hypergumbo-lang-extended1/   # Zig, Solidity, Agda, etc.
├── hypergumbo-lang-rust-analyzer/ # SCIP-backed Rust backend (extra: hypergumbo[rust-analyzer])
├── hypergumbo-lang-scip-python/   # SCIP-backed Python backend (extra: hypergumbo[scip-python])
├── hypergumbo-tracker/           # Structured work tracker for agent governance (MPL-2.0)
└── hypergumbo/                  # Meta-package (installs core + the three language packs; not the tracker or the SCIP backends)
```

Key design choices:
- **Registry pattern**: Analyzers and linkers self-register via decorators
- **Two-pass analysis**: First collect symbols, then resolve edges (enables cross-file references)
- **Provenance tracking**: Every edge records which analyzer/linker created it
- **YAML-driven patterns**: Framework detection is declarative, not hardcoded

## Development

```bash
git clone https://github.com/iterabloom/hypergumbo.git
cd hypergumbo
python3 -m venv .venv && source .venv/bin/activate
./scripts/dev-install
source .venv/bin/activate  # reload to enable pytest alias
pytest                      # runs smart-test (affected tests only)
```

`dev-install` installs all packages, git hooks, and the pytest/smart-test wrapper. 100% test coverage required.

See [CONTRIBUTING.md](CONTRIBUTING.md) for PR workflow (including fork-based workflow for external contributors), smart test selection setup, and coverage requirements. Agent instructions live in [AGENTS.md](https://github.com/iterabloom/hypergumbo/blob/dev/AGENTS.md).

## Links

- [docs/USE-CASES.md](https://github.com/iterabloom/hypergumbo/blob/dev/docs/USE-CASES.md) — Practical workflows and examples
- [docs/VERIFY-CLAIMS-SCOPE.md](https://github.com/iterabloom/hypergumbo/blob/dev/docs/VERIFY-CLAIMS-SCOPE.md) — What `verify-claims` can and cannot see: the verdict ladder and exit-code contract, the caveat vocabulary, and what a clean verdict does **not** mean. Read this before gating CI on it.
- [docs/RELEASE-NOTES-9.X.md](https://github.com/iterabloom/hypergumbo/blob/dev/docs/RELEASE-NOTES-9.X.md) — User-facing release notes for the current (9.x) line; earlier lines are [8.x](https://github.com/iterabloom/hypergumbo/blob/dev/docs/RELEASE-NOTES-8.X.md), [7.x](https://github.com/iterabloom/hypergumbo/blob/dev/docs/RELEASE-NOTES-7.X.md), [6.x](https://github.com/iterabloom/hypergumbo/blob/dev/docs/RELEASE-NOTES-6.X.md), [5.x](https://github.com/iterabloom/hypergumbo/blob/dev/docs/RELEASE-NOTES-5.X.md)
- [CHANGELOG.md](https://github.com/iterabloom/hypergumbo/blob/dev/CHANGELOG.md) — Implementation history
- [docs/LANGUAGES.md](https://github.com/iterabloom/hypergumbo/blob/dev/docs/LANGUAGES.md) — Supported languages
- [docs/LINKERS.md](https://github.com/iterabloom/hypergumbo/blob/dev/docs/LINKERS.md) — Linkers catalogue (Protocol / Bridge / Framework / Infrastructure)
- [docs/FRAMEWORKS.md](https://github.com/iterabloom/hypergumbo/blob/dev/docs/FRAMEWORKS.md) — Framework patterns
- [docs/hypergumbo-spec.md](https://github.com/iterabloom/hypergumbo/blob/dev/docs/hypergumbo-spec.md) — Detailed specification
- [docs/CITATIONS.md](https://github.com/iterabloom/hypergumbo/blob/dev/docs/CITATIONS.md) — Paper citations for embedding models
- [docs/CACHE.md](https://github.com/iterabloom/hypergumbo/blob/dev/docs/CACHE.md) — Caching architecture
- [docs/agent-supervisor.md](https://github.com/iterabloom/hypergumbo/blob/dev/docs/agent-supervisor.md) — Operator guide for `scripts/agent-supervisor` (the tmux-session watchdog for autonomous agents)
- [SECURITY.md](https://github.com/iterabloom/hypergumbo/blob/dev/SECURITY.md) — Vulnerability reporting, and the audited I/O surface with its self-checked claims
- [hypergumbo-tracker README](packages/hypergumbo-tracker/README.md) — Standalone tracker for AI agent governance

## License

[AGPL-3.0-or-later](https://github.com/iterabloom/hypergumbo/blob/dev/LICENSE)

![Hypergumbo logo](https://raw.githubusercontent.com/iterabloom/hypergumbo/dev/docs/hypergumbo%20FINAL%20halfres.jpg)

