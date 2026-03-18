<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0016: I/O Boundary Analysis and Security Claim Verification

Date: 2026-03-18
Status: Proposed

## Context

### Origin: the trust question

Hypergumbo's dataflow system (ADR-0015) classifies edges as read/write/mutate/delete and traces data dependencies between symbols through named channels. A different question arose from the practical situation of recommending hypergumbo to others: *what would it take to feel confident running a program on a machine with sensitive data?*

The answer reduces to four buckets:

1. **Which code paths read from the filesystem, and what do they read?**
2. **Which code paths write to the filesystem, and what do they write?**
3. **Which code paths receive data from the network, and from where?**
4. **Which code paths send data to the network, and to where?**

If you can answer these four questions exhaustively, you can reason about whether a program's actual behavior matches your trust requirements.

### Why static analysis can answer this (mostly)

In managed-runtime languages (Python, Ruby, JavaScript, Java, C#, Go, etc.), **all I/O must ultimately flow through the language's own primitives**. `requests.get()` eventually calls `socket.send()`. `pandas.read_csv()` eventually calls `open()`. There is no way to conjure a syscall from pure Python without going through the standard library.

The I/O primitive catalog for any given language is therefore **finite and stable** — a curated list of stdlib functions, not an unbounded set of library APIs.

The exception is **native code** (C extensions, FFI, JNI, N-API, etc.). Compiled native code can call OS primitives directly, bypassing the language's stdlib. However:

- Many native extensions receive data from the managed language and return results — the managed code does the I/O.
- When native source *is* available (Rust crates ship source by default; Python sdists include C source), hypergumbo can analyze both sides and link them through existing FFI linkers (pyffi, cgo, jni, napi, wasm_bindgen, etc.).

This yields three **transparency tiers** for any code path:

| Tier | Condition | Analysis capability |
|------|-----------|-------------------|
| **Transparent** | Source present, analyzer + linker available | Full I/O boundary tracing |
| **Opaque** | Compiled binary only, no source | Flag the boundary, cannot trace past it |
| **Partial** | Source present, but no analyzer or linker for the FFI mechanism | Flag as gap, manual review needed |

These are distinct from hypergumbo's existing supply-chain tiers (first-party vs. dependency code).

### Security claim verification

For projects with stated security properties — "relays never see plaintext," "decrypted content never hits the host disk" — the I/O boundary map enables **verification against actual code**:

> **Claim: "Decrypted content is never written to the host filesystem"**
> - 14 code paths reach fs-write primitives
> - 12 involve only encrypted blobs (ciphertext confirmed via crypto-flow trace)
> - 2 involve plaintext: `cache.rs:47` writes to tmpfs (guest-only), `debug.rs:203` writes a log when `--verbose` is set (**violates claim in debug mode**)

This moves security properties from documentation-level assertions to evidence-backed findings.

## Decision

### 1. I/O boundary classification vocabulary

Define a controlled vocabulary for system boundary types:

| Boundary | Direction | Meaning | Examples |
|----------|-----------|---------|---------|
| `fs_read` | inbound | Read data from local filesystem | `open(f).read()`, `Path.read_text()`, `os.walk()`, `std::fs::read()` |
| `fs_write` | outbound | Write data to local filesystem | `open(f,'w').write()`, `shutil.copy()`, `std::fs::write()` |
| `net_recv` | inbound | Receive data from network | `socket.recv()`, `TcpStream::read()`, HTTP response body |
| `net_send` | outbound | Send data to network | `socket.send()`, `TcpStream::write()`, HTTP request body |
| `ipc_recv` | inbound | Receive data from another process | `stdin.read()`, pipe read, shared memory read |
| `ipc_send` | outbound | Send data to another process | `stdout.write()`, pipe write, shared memory write |
| `env_read` | inbound | Read from process environment | `os.environ`, `std::env::var()`, command-line args |
| `subprocess` | outbound | Launch or communicate with child process | `subprocess.run()`, `Command::new()` |

The first four are the primary trust-question buckets. The latter four capture additional system boundary interactions relevant to security reasoning.

### 2. I/O primitive catalogs (per-language, YAML-driven)

For each supported language, maintain a catalog of stdlib/runtime functions classified by boundary type. These are YAML files following the pattern established by ADR-0015's dataflow patterns:

```yaml
# io_primitives/python.yaml
language: python

fs_read:
  - module: builtins
    functions: [open]  # when mode is 'r' or default
    notes: "Mode detection: 'r'/'rb' = fs_read, 'w'/'wb'/'a' = fs_write"
  - module: pathlib.Path
    methods: [read_text, read_bytes, stat, exists, is_file, is_dir, iterdir, glob, rglob]
  - module: os
    functions: [listdir, scandir, stat, access, readlink, walk]
  - module: shutil
    functions: [which]

fs_write:
  - module: builtins
    functions: [open]  # when mode is 'w', 'a', 'x'
  - module: pathlib.Path
    methods: [write_text, write_bytes, mkdir, touch, unlink, rename, replace, symlink_to]
  - module: os
    functions: [mkdir, makedirs, remove, unlink, rename, replace, symlink, link]
  - module: shutil
    functions: [copy, copy2, copytree, move, rmtree]
  - module: tempfile
    functions: [mkstemp, mkdtemp, NamedTemporaryFile, TemporaryDirectory]

net_send:
  - module: socket.socket
    methods: [send, sendto, sendall, sendmsg, connect]
  - module: http.client.HTTPConnection
    methods: [request, putrequest]
  - module: urllib.request
    functions: [urlopen, Request]

net_recv:
  - module: socket.socket
    methods: [recv, recvfrom, recv_into, recvmsg]

subprocess:
  - module: subprocess
    functions: [run, Popen, call, check_call, check_output]
  - module: os
    functions: [system, popen, exec, execvp, fork, spawn]

env_read:
  - module: os
    attributes: [environ]
    functions: [getenv]
  - module: sys
    attributes: [argv, stdin]
```

Similar catalogs needed for: Rust (`std::fs`, `std::net`, `std::process`), JavaScript (`fs`, `net`, `http`, `child_process`), Go (`os`, `net`, `io`, `exec`), Java (`java.io`, `java.net`, `java.nio`, `ProcessBuilder`), C (`fopen`, `socket`, `fork`, `exec`).

The catalogs are finite and stable. Python's stdlib I/O surface hasn't fundamentally changed across 3.x. Rust's `std` is similarly stable.

### 3. Analysis pipeline

The I/O boundary analysis runs as a post-processing pass over hypergumbo's existing graph:

**Phase 1: Identify boundary calls.** Walk all symbols and edges. For each call edge whose target matches an I/O primitive catalog entry, tag the edge with the boundary classification:

```python
edge.meta["io_boundary"] = "fs_read"
edge.meta["io_primitive"] = "pathlib.Path.read_text"
```

**Phase 2: Reverse trace.** For each identified boundary call, compute a reverse slice: which application-level entry points can reach this I/O call? This produces a set of **I/O chains**:

```
cli.main() -> profile.run_analysis() -> discovery.walk_tree() -> os.walk() [fs_read]
```

**Phase 3: Classify transparency.** For each I/O chain, check whether any link in the chain crosses into opaque territory:

- Call into a native extension without source -> **opaque boundary**, flag it
- Call through an FFI linker with source on both sides -> **transparent**, continue tracing
- Call into a dependency without analyzer coverage -> **partial**, flag the gap

**Phase 4: Aggregate into boundary map.** Produce a structured report grouping chains by boundary type:

```json
{
  "fs_read": {
    "chains": ["..."],
    "entry_points": ["cli.main", "profile.run_analysis"],
    "primitives_used": ["os.walk", "pathlib.Path.read_text", "open"],
    "opaque_boundaries": [],
    "transparency": "full"
  },
  "net_send": {
    "chains": [],
    "entry_points": [],
    "primitives_used": [],
    "opaque_boundaries": ["tree_sitter (C extension, no source analyzed)"],
    "transparency": "n/a - no net_send paths found"
  }
}
```

### 4. Security claim verification (optional layer)

A separate, optional input: security claims expressed as constraints on the I/O boundary map.

```yaml
# security-claims.yaml
claims:
  - id: SC-001
    text: "Decrypted content is never written to the host filesystem"
    constraint:
      boundary: fs_write
      must_not_contain:
        data_classification: plaintext
      exceptions:
        - path_pattern: "guest_fuse_daemon/**"
          reason: "Guest-local tmpfs, not host filesystem"

  - id: SC-002
    text: "Relays never see plaintext document content"
    constraint:
      boundary: net_send
      at_symbols: ["relay_*", "RelaySyncManager.*"]
      must_not_contain:
        data_classification: plaintext

  - id: SC-003
    text: "No network I/O occurs without user-initiated action"
    constraint:
      boundary: [net_send, net_recv]
      must_have:
        reachable_only_from: ["cli.main", "cli.analyze"]
        not_reachable_from: ["import_time_side_effects"]
```

The verifier checks each claim against the boundary map and produces a verdict:

- **Confirmed**: all I/O chains consistent with claim, full transparency
- **Confirmed with caveats**: consistent, but opaque boundaries exist that could not be verified
- **Violated**: specific I/O chain contradicts the claim (with evidence)
- **Inconclusive**: insufficient analysis coverage to determine

### 5. Integration with existing infrastructure

The I/O boundary analysis builds on existing hypergumbo infrastructure:

- **Reuses** the YAML pattern machinery (ADR-0015) for I/O primitive catalogs
- **Reuses** the `meta` dict on edges for boundary classification
- **Reuses** `slice --dataflow` reverse traversal for chain computation
- **Extends** the crypto-flow linker's output: if a `crypto_flow` edge shows data is encrypted before reaching a `net_send` boundary, the security claim verifier can use this as evidence
- **Fits** the multi-pass architecture (ADR-0012): I/O boundary tagging is a post-processing pass

### 6. CLI interface

```bash
# Produce I/O boundary map
hypergumbo io-boundaries repo/

# Produce boundary map as JSON
hypergumbo io-boundaries --format json repo/

# Verify security claims
hypergumbo verify-claims --claims security-claims.yaml repo/

# Show all code paths that reach network sends
hypergumbo slice --io-boundary net_send --reverse repo/
```

### 7. Resolved design questions

**`open()` dual classification.** Python's `open()` is both `fs_read` and `fs_write` depending on mode. Decision: **conservatively classify as both** by default. If the analyzer can extract the mode argument (literal string), narrow to the correct boundary. If the mode is a variable, keep both classifications. This is consistent with the "honest about limitations" principle.

**Transitive dependencies.** Decision: **trace everything, group by supply chain tier.** The boundary map includes all code paths (first-party and dependency), but the output groups chains by tier so users can focus on first-party code or drill into dependency paths.

**Runtime I/O.** Decision: **include but tag as `runtime_io`.** Python's import system reads `.py`/`.pyc` files — this is real I/O but universal. Tag it distinctly so it can be filtered from reports that focus on application-level I/O.

**Dynamic dispatch.** Decision: **flag as unresolvable.** When a call target is computed at runtime (`getattr(obj, name)()`), the I/O boundary status is unknown. Flag the call site with `io_boundary: "unknown_dynamic"` so the boundary map is explicit about what it cannot determine.

**Naming.** Decision: **"I/O boundary analysis"** for the feature, **"boundary map"** for the output artifact, **"claim verification"** for the security layer. These are descriptive and unambiguous.

### 8. Scope and limitations

**What this can do:**
- Exhaustively enumerate I/O boundary calls in pure managed-language code
- Trace call chains from application entry points to I/O primitives
- Flag opaque native code boundaries
- Cross FFI boundaries when source + linker are available
- Integrate with crypto-flow tracing to distinguish encrypted vs. plaintext I/O
- Verify stated security claims against the actual code graph

**What this cannot do:**
- See inside compiled native code without source (strace/dtrace needed for runtime verification)
- Determine conditions under which an I/O path executes (requires control-flow sensitivity beyond the current graph)
- Guarantee that analyzed source matches the binary actually running (reproducible builds problem)
- Detect I/O performed by the language runtime itself (import machinery, GC finalizers, etc.) — tagged as `runtime_io` but not exhaustively enumerable
- Replace a full security audit — this is evidence gathering, not a proof system

**Future work that would strengthen the analysis:**
- Control-flow sensitivity: "this `fs_write` only executes when `--output-file` is passed"
- Data classification propagation: track whether data flowing to an I/O boundary is "user content," "metadata," or "ciphertext"
- Runtime correlation: compare static boundary map against strace/dtrace output to find divergences
- Supply-chain depth integration: distinguish "my code does `net_send`" from "my dependency's dependency does `net_send`"

## Consequences

### Positive

1. **Enables informed trust decisions**: users can see exactly what a program does to their machine before running it outside a sandbox
2. **Security claims become testable**: project maintainers can write claims and CI can verify them on every commit
3. **Builds on existing infrastructure**: no new graph model needed — this is a composition of existing edge metadata, YAML catalogs, and slice traversal
4. **Polyglot by design**: works across hypergumbo's full language coverage, which matters for projects like PlazaFlow (Rust + TypeScript + C)
5. **Honest about limitations**: the transparency tier model makes it clear what the analysis can and cannot see

### Negative

1. **I/O primitive catalogs require curation**: each language needs a manually maintained list of stdlib I/O functions. This is finite and stable work, but it is work.
2. **Opaque boundaries reduce confidence**: for projects heavy on native code (ML/data science Python, game engines), many paths will be flagged as opaque rather than fully traced
3. **No control-flow sensitivity**: "this code *can* reach `fs_write`" is weaker than "this code *will* reach `fs_write` under conditions X." Users must understand this limitation.
4. **Security claim YAML format needs iteration**: the constraint language for expressing claims will need real-world usage to converge

### Neutral

- Does not conflict with existing dataflow analysis — the two systems share infrastructure but answer different questions
- The I/O primitive catalogs could eventually be community-maintained (similar to security advisory databases)

## Implementation phases

### Phase 1: Python I/O boundary map (dogfood on hypergumbo itself)

1. Write `io_primitives/python.yaml` catalog
2. Add boundary-tagging pass in analysis pipeline
3. Add `hypergumbo io-boundaries` CLI command
4. Run on hypergumbo's own codebase and verify results manually
5. Publish boundary map as documentation

This is the minimum useful increment: hypergumbo analyzing itself, answering the original trust question.

### Phase 2: Multi-language catalogs and FFI tracing

1. Add catalogs for Rust, JavaScript/TypeScript, C, Go, Java
2. Integrate with FFI linkers so chains cross language boundaries
3. Test on a polyglot project (PlazaFlow is the natural candidate)

### Phase 3: Security claim verification

1. Design the claim YAML format based on real claims from real projects
2. Implement the verifier
3. Add `hypergumbo verify-claims` CLI command
4. Integrate with crypto-flow linker for encrypted-vs-plaintext reasoning

### Phase 4: CI integration and ecosystem

1. GitHub Action / CI step that runs claim verification on every PR
2. Standard claim templates for common security properties
3. Community-maintained I/O primitive catalogs for additional languages

## Relationship to other ADRs

- **ADR-0015 (Dataflow Access Modes)**: provides the edge metadata model and YAML pattern infrastructure reused here
- **ADR-0012 (Pass Unification)**: I/O boundary tagging is a post-processing pass, fitting the existing multi-pass architecture
- **ADR-0008 (Autonomous Governance)**: security claim verification could extend the structural fix protocol — a violated security claim is an invariant violation
