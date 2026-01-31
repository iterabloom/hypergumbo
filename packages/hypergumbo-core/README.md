# hypergumbo-core

Core infrastructure for hypergumbo repo behavior map generator.

## What's Included

- **CLI**: Command-line interface (`hypergumbo run`, `hypergumbo sketch`, etc.)
- **IR**: Data structures (Symbol, Edge, Span, AnalysisRun)
- **Analysis Framework**: Base classes and registry for language analyzers
- **Linkers**: Cross-language relationship detection (gRPC, HTTP, IPC, etc.)
- **Framework Patterns**: Route and handler detection for 150+ frameworks
- **Slice**: Forward and reverse dependency analysis
- **Sketch**: Token-budgeted codebase overview generation

## Installation

```bash
# Core only (no language analyzers)
pip install hypergumbo-core

# Full installation (recommended)
pip install hypergumbo
```

## Usage

```python
from hypergumbo_core.ir import Symbol, Edge, Span
from hypergumbo_core.sketch import generate_sketch
from hypergumbo_core.slice import forward_slice, reverse_slice
```

## Documentation

See https://codeberg.org/iterabloom/hypergumbo for full documentation.
