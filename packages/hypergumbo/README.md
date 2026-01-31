# hypergumbo

Local-first repo behavior map generator for AI coding agents.

## Installation

```bash
pip install hypergumbo
```

This installs all hypergumbo components:
- `hypergumbo-core`: CLI, IR, slice, sketch, linkers
- `hypergumbo-lang-mainstream`: Python, JS, Java, Go, Rust, etc.
- `hypergumbo-lang-common`: Haskell, Elixir, GraphQL, etc.
- `hypergumbo-lang-extended1`: Zig, Solidity, Agda, etc.

## Usage

```bash
# Generate behavior map
hypergumbo run .

# Generate token-budgeted overview
hypergumbo sketch .

# Forward slice from a symbol
hypergumbo slice --symbol MyClass.method

# Reverse slice to find callers
hypergumbo slice --reverse --symbol MyClass.method
```

## Documentation

See https://codeberg.org/iterabloom/hypergumbo for full documentation.
