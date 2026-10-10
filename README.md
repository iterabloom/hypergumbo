<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# hypergumbo

[![PyPI](https://img.shields.io/pypi/v/hypergumbo.svg)](https://pypi.org/project/hypergumbo/)
[![License](https://img.shields.io/pypi/l/hypergumbo.svg)](https://pypi.org/project/hypergumbo/)

hypergumbo is a local-first CLI that generates surveys and sketches from source code. The goal of this project is to efficiently help developers and LLMs understand a codebase.

```bash
pip install hypergumbo
```

> Requires Python 3.10+. For optional extras (embeddings, gitleaks, grammars), run `hypergumbo add-extras` after installing.

> Installing on a personal machine? Read [Security and trust](https://github.com/iterabloom/hypergumbo/blob/dev/README.md#security-and-trust-as-of-2026-10) first: what hypergumbo reads, writes and downloads, which commands run other code, and how its claims are checked.

> Intel Mac users: Some tree-sitter packages lack x86_64 wheels. See [docs/INTEL_MAC.md](https://github.com/iterabloom/hypergumbo/blob/dev/docs/INTEL_MAC.md) for a Docker-based workaround.

```bash
git clone https://github.com/iterabloom/hypergumbo
hypergumbo hypergumbo/
```

Output:

```bash
# hypergumbo

hypergumbo is a local-first CLI that generates surveys and sketches from source code. The goal of this project is to efficiently help developers and LLMs understand a codebase. > Requires Python 3.10+. For optional extras (embeddings, gitleaks, grammars), run `hypergumbo add-extras` after installing. > Intel Mac users:

> **Audited IO surface.** hypergumbo's own filesystem / network / subprocess activity is enumerated per CLI subcommand category in [SECURITY.md](SECURITY.md), with machine-verifiable claims authored at `docs/hypergumbo.claims.yaml`. Re-run the audit locally via `hypergumbo verify-claims --claims docs/hypergumbo.claims.yaml`. The wrapper-function discipline used to make path-bounded claims structurally verifiable is documented as the recommended pattern for any hypergumbo user (any language) wanting the same precision.

## Overview
Python (91%), Markdown (4%), Yaml (3%)
728 files    (383 non-test + 345 test)
~320,798 LOC (~129,172 non-test + ~191,626 test)

## Structure

` ` `
hypergumbo/
├── .agent
│   └── [and 6 other items]
├── .gitea
│   ├── SQUASH_TEMPLATE.md
│   └── [and 1 other items]
├── .githooks
│   ├── commit-msg
│   └── [and 9 other items]
├── docs
│   ├── CACHE.md
│   └── [and 22 other items]
├── packages
│   ├── hypergumbo-core
│   │   ├── src
│   │   │   └── hypergumbo_core
│   │   │       ├── analyze
│   │   │       │   ├── base.py
│   │   │       │   └── [and 3 other items]
│   │   │       ├── __main__.py
│   │   │       ├── cli.py
│   │   │       ├── ir.py
│   │   │       └── [and 26 other items]
│   │   ├── tests
│   │   │   ├── test_framework_patterns.py
│   │   │   └── [and 94 other items]
│   │   └── [and 2 other items]
│   ├── hypergumbo-tracker
│   │   ├── src
│   │   │   └── hypergumbo_tracker
│   │   │       ├── cli.py
│   │   │       └── [and 13 other items]
│   │   └── [and 5 other items]
│   └── [and 4 other items]
├── scripts
│   ├── lib
│   │   └── forgejo-api.sh
│   └── [and 33 other items]
├── tests
│   ├── test_bakeoff_deep_reflect.py
│   └── [and 2 other items]
├── conftest.py
├── pyproject.toml
├── setup.py
└── [and 21 other items]
` ` `

## Frameworks

- pytest
- pytorch
- transformers

## Tests

345 test files · cargo test, pytest, unittest

*~95% estimated coverage (2693/2847 functions called by tests)*

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

## Security and trust (as of 2026-10)

Read this before installing hypergumbo on a machine you care about. Each statement below was checked against the code on 2026-10-10.

**Who wrote it.** AI coding agents wrote most of hypergumbo, directed by its owner: 5,410 of the repository's 5,483 commits (not counting tracker-data commits) come from the agent account. No human has reviewed the code line by line. It has about 287,000 lines of Python in `packages/*/src` and about 600,000 lines of tests (`wc -l` over git-tracked `.py` files). CI requires 100% test coverage, but tests only check what someone thought to test.

**What a default run does** (`hypergumbo .`, the same as `hypergumbo sketch .`):

- Reads the files of the repository you point it at.
- Writes its results under `~/.cache/hypergumbo/` (`$XDG_CACHE_HOME/hypergumbo/` if set), plus short-lived files in the system temp directory. It does not write into the repository it analyses ([ADR-0047](https://github.com/iterabloom/hypergumbo/blob/dev/docs/adr/0047-catalogue-scope-and-user-visible-homes.md) ruling 9). The exceptions are an `-o` path you choose inside it, and the case in the last bullet of this list.
- Runs `git` read-only (`rev-parse HEAD`, `rev-list --max-parents=0 HEAD`, `config --get remote.origin.url`). If they are installed, it also runs `gitleaks` to scan for secrets and `rust-analyzer --version`; the version check does not index anything.
- Makes no network connection, unless you installed embeddings support (`install-embeddings`, `add-extras`, or `pip install 'hypergumbo[embeddings]'`). In that case the first sketch downloads two models (`microsoft/unixcoder-base` and `nomic-ai/modernbert-embed-base`) from huggingface.co into `~/.cache/huggingface/` (or `$HF_HOME`). Once both models are cached, later runs work offline. To stop the download, set `HF_HUB_OFFLINE=1`; the sketch then runs without embeddings. SECURITY.md's network claim still says downloads happen only through `install-embeddings`. That claim is wrong, and INV-gujus tracks it.
- If a directory `~/hypergumbo` exists (for example, a clone of this repository), an interactive run asks once whether to write example config files into `~/hypergumbo/.hypergumbo-examples/`. It records your answer in `~/.local/state/hypergumbo/offers.json`. If you answer yes while analysing `~/hypergumbo` itself, that is a write into the analysed repository (INV-kuroj).

**Commands that download, compile or run other programs.** None of these run unless you invoke them:

| Command | What it does |
|---|---|
| `install-gitleaks` | Downloads the latest gitleaks release from github.com into `~/.local/bin/`. It checks no checksum or signature. |
| `install-embeddings` | Runs `pip install sentence-transformers`, which pulls in PyTorch (about 2 GB). It does not fetch the models; the first sketch does. |
| `install-rust-analyzer` | Runs `rustup component add rust-analyzer`. |
| `build-grammars` | Compiles three tree-sitter grammars (Lean, Wolfram, Circom) with your compiler, from C source that ships inside `hypergumbo-core`, through `pip install`, which can download build tools from PyPI. |
| `add-extras` | Runs all four of the above. Skip any of them with `--skip`. |
| `--backend rust-analyzer` | Runs `rust-analyzer scip`, which **executes the analysed repository's `build.rs` and proc macros as you**. It is off unless you pass the flag, set `HYPERGUMBO_RUST_ANALYZER=1`, or record a per-repository grant with `hypergumbo trust-backend rust_analyzer`. No config file can switch it on, including one inside the analysed repository ([ADR-0045](https://github.com/iterabloom/hypergumbo/blob/dev/docs/adr/0045-user-config-and-backend-trust.md)). Do not use it on code you don't trust. Do not export the variable from your shell profile, because that turns it on for every Rust repository. |
| `--backend scip-python` | Runs `scip-python index`, a static analyser that does not execute the project. Besides the flag, `HYPERGUMBO_SCIP_PYTHON`, your `config.toml`, **or the analysed repository's own `.hypergumbo.toml`** can switch it on, provided the `hypergumbo[scip-python]` extra and the `scip-python` npm tool are installed. |

**The tracker (`htrac`) is a separate package.** `pip install hypergumbo` does not install it. It was built for this repository's agent workflow. If you install `hypergumbo-tracker`, note the following:

- `htrac sync` commits and pushes with `git` and calls the GitHub or Forgejo API with a token. So does any tracker command that changes data, once enough unsynced changes have piled up (auto-sync).
- `htrac serve` starts a web server on 127.0.0.1:7380. Its write API has no authentication yet (WI-hopip). Today that API is not connected to any tracker data and returns errors, but do not expose the port.
- `htrac guidance` sends tracker item titles to openrouter.ai when `OPENROUTER_API_KEY` is set and an unread message starts with a `[[tag]]` (WI-puban).
- `htrac setup` adds the repository to your global git `safe.directory` list when git reports "dubious ownership".
- The self-check described below does not cover the tracker yet (WI-sunan). The tracker was designed to stop an agent from accidentally undermining it, not to resist a determined attacker ([ADR-0013](https://github.com/iterabloom/hypergumbo/blob/dev/docs/adr/0013-structured-tracker.md), Security Model).

**How these statements are checked.** Two independent gates run in CI:

1. **Self-claims.** [SECURITY.md](https://github.com/iterabloom/hypergumbo/blob/dev/SECURITY.md) lists 18 claims about what each group of commands may touch, and hypergumbo checks them by analysing its own source (`scripts/check-self-claims`). All 18 currently read `confirmed_with_caveats`. That means the analysis found no contradiction where it could see, and it names what it could not see: programs it launches, statements the repository made about itself, and the 70.6% of method calls whose receiver type it could not work out. In auditing terms this is a qualified opinion, not proof ([ADR-0016](https://github.com/iterabloom/hypergumbo/blob/dev/docs/adr/0016-io-boundary-analysis.md)). It covers the `hypergumbo` command only.
2. **I/O allowlist.** `scripts/check-io-allowlist` is a separate scan that uses only the standard library. It finds every place in `packages/*/src`, tracker included, that writes or deletes a file, starts a process, opens a connection or a server, or loads a model. Each one must be listed with its purpose in [`docs/hypergumbo.io-allowlist.yaml`](https://github.com/iterabloom/hypergumbo/blob/dev/docs/hypergumbo.io-allowlist.yaml), and an unlisted one fails CI. The gate proves that every such place was reviewed. It does not prove that any of them is safe.

The allowlist gate currently records these open findings:

- **INV-gujus:** the first sketch after installing embeddings downloads models (see above), although the network claim says it does not.
- **INV-tusos:** the rust-analyzer and scip-python backends start their programs outside the wrappers that the subprocess claim lists, and the claim does not name them.
- **INV-kuroj:** writes to `~/.local/state/hypergumbo/` (trust grants, offer answers), `~/.config/hypergumbo/` (`init-catalogs`) and `~/hypergumbo/.hypergumbo-examples/` bypass the audited write wrappers, so no claim covers them. Each one follows a command you ran or a question you answered.
- **WI-puban:** the tracker's OpenRouter call, described above.

**Analysing a repository you don't trust.**

- Unless you turn on the rust-analyzer backend, hypergumbo parses the repository's code and never runs it.
- Catalogue data inside the repository (the catalogue keys in `.hypergumbo.toml` and the files in `.hypergumbo/<family>.d/`) is ignored until you opt in with `--in-repo-catalogues` or `hypergumbo trust-catalogues` ([ADR-0061](https://github.com/iterabloom/hypergumbo/blob/dev/docs/adr/0061-catalogue-tiers-for-every-family.md)). Its preferences do load by default, and that is how it can switch on scip-python.
- It cannot turn on the rust-analyzer backend, and it cannot make hypergumbo write into it.
- If gitleaks is installed, the repository's own `.gitleaks.toml` and `.gitleaksignore` can hide secret-scan findings (INV-nihab).
- It does control everything the parsers read. hypergumbo has no sandbox of its own, so a file crafted to exploit a parser bug would run with your permissions.

**Running it confined.**

- **Container:** install hypergumbo in an image, then run it with networking off and the repository mounted read-only (`docker run --network none -v "$PWD":/repo:ro …`). hypergumbo runs without `git` and writes only its cache.
- **Watch it once on Linux:** the commands below log every file opened for writing, created, renamed or deleted, every network connection and every program started.

  ```bash
  strace -f -e trace=%file,%network,execve -o /tmp/hg.trace hypergumbo .
  grep -E 'O_CREAT|O_WRONLY|O_RDWR|mkdir|rename|unlink|connect\(|execve\(' /tmp/hg.trace | grep -v ENOENT
  ```

- **macOS:** block network access for the Python that runs hypergumbo with LuLu or Little Snitch, and watch file activity once with `sudo fs_usage`. We have not tested exact commands for these on macOS.

Report vulnerabilities as described in [SECURITY.md](https://github.com/iterabloom/hypergumbo/blob/dev/SECURITY.md#reporting-a-vulnerability).

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

1. **Profile**: Scan the repo for languages, file counts, LOC
2. **Analyze**: Run language-specific analyzers to extract symbols and edges
3. **Link**: Connect symbols across language boundaries (JS fetch → Python route)
4. **Enrich**: Detect frameworks via YAML pattern matching
5. **Classify**: Assign supply chain tiers (first-party, internal, external, derived)
6. **Trace I/O**: Map call chains to I/O boundaries; run taint-flow analysis
7. **Output**: Generate Markdown sketch or JSON survey

### The Internal Representation

All analyzers produce the same IR types:

- **Symbol**: A code element (function, class, method) with name, location, and stable ID
- **Edge**: A relationship between symbols (calls, imports, extends, implements)
- **Span**: Source location (file, line, column)

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
├── hypergumbo-tracker/           # Structured work tracker for agent governance (MPL-2.0)
└── hypergumbo/                  # Meta-package (installs core + the three language packs; not the tracker)
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
- [docs/RELEASE-NOTES-8.X.md](https://github.com/iterabloom/hypergumbo/blob/dev/docs/RELEASE-NOTES-8.X.md) — User-facing release notes for the current (8.x) line; earlier lines are [7.x](https://github.com/iterabloom/hypergumbo/blob/dev/docs/RELEASE-NOTES-7.X.md), [6.x](https://github.com/iterabloom/hypergumbo/blob/dev/docs/RELEASE-NOTES-6.X.md), [5.x](https://github.com/iterabloom/hypergumbo/blob/dev/docs/RELEASE-NOTES-5.X.md)
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

