<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# hypergumbo-lang-common

Domain-specific language analyzers for hypergumbo.

## Supported Languages

See [docs/LANGUAGES.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/LANGUAGES.md) for the full list.

## Installation

```bash
# With core
pip install hypergumbo-core hypergumbo-lang-common

# Full installation (recommended)
pip install hypergumbo
```

## Usage

```python
from hypergumbo_lang_common.haskell import analyze_haskell
from hypergumbo_lang_common.elixir import analyze_elixir
from hypergumbo_lang_common.graphql import analyze_graphql
```

## Documentation

See https://codeberg.org/iterabloom/hypergumbo for full documentation.
