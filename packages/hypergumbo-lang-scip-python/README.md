<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# hypergumbo-lang-scip-python

SCIP-backed Python analyzer for hypergumbo, driven by
[scip-python](https://github.com/sourcegraph/scip-python) (pyright).

This optional package is the second opt-in fidelity backend (after
`hypergumbo-lang-rust-analyzer`) and the first that **executes nothing** from
the analysed repository: pyright performs static inference only. It runs
*beside* the ast-based `python` analyzer, never instead of it — the two
producers' records for one declaration are folded into one node by the
ADR-0057 merge pass, and every disagreement stays visible in the provenance
slot.

## What it adds, measured

On hypergumbo-core's own 179 source files (2026-09-19, scip-python 0.6.6):
the syntax arm left 4,133 method-call receivers untyped (`obj.method()` with
`obj` of unknown module); scip-python typed **3,973 of them (96.1%)** — 3,840
to the stdlib (`builtins.str`, `dict`, `list`, `pathlib.Path`, `re.Pattern`, …),
96 in-repo, 37 third-party. That module is what `hypergumbo verify-claims`'s
I/O-boundary and taint verdicts key on. The Rust backend measured zero
receivers typed.

## Enabling it

```
npm install -g @sourcegraph/scip-python@0.6.6     # the producer (node + npm)
pipx install 'hypergumbo[scip-python]'            # this wrapper
hypergumbo survey . --backend scip-python         # one run
```

Durably, as a preference in either ADR-0045 config tier (allowed because the
backend executes nothing):

```toml
# $XDG_CONFIG_HOME/hypergumbo/config.toml  or  <repo>/.hypergumbo.toml
[backends]
scip_python = true
```

`HYPERGUMBO_SCIP_PYTHON=1` is the environment spelling; `--backend tree-sitter`
turns every opt-in backend off for one run. Precedence: flag > environment >
project config > user config.

When enabled but the binary or this package is missing, the CLI exits 2 with
the install line rather than silently falling through.

## What the translation does with scip-python's output

- Global definitions become `Symbol`s through the shared importer
  (`hypergumbo_core.scip`); scip-python declares no `Document.language` and
  no `SymbolInformation.kind`, so the importer reads the file extension and
  the SCIP descriptor grammar instead.
- Parameter symbols and the per-module `__init__:` declaration are dropped:
  the syntax arm models neither, and a node nothing can pair with is noise.
- References to in-repo definitions become `calls` / `references` edges.
- References to **external** callables (stdlib, third-party) become external
  call edges carrying the receiver's module — `python:pathlib.Path:0-0:glob:unresolved`
  with a structured `dst_ref` — the same shape the syntax arm mints when it
  knows the module, so the two arms are comparable at every call site.

## Recorded fixture

`recorded/sample_project/` is a three-file package and the `index.scip`
scip-python 0.6.6 emitted for it (`metadata.project_root` normalised). Every
cross-backend test reads that recording, never anything derived from the
syntax arm's own output (ADR-0057 §12).
