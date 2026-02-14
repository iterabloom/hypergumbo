# hypergumbo-tracker

YAML-backed structured tracker for agent governance.

Replaces fragile grep-based markdown governance files (`.agent/invariant-ledger.md`
and `work_items.md`) with append-only op-log storage that is git-merge-safe,
causally ordered, and supports field-level access control.

## License

This package is licensed under the [Mozilla Public License 2.0](LICENSE).
Other hypergumbo packages are licensed under AGPL-3.0-or-later.
See the repository root LICENSE and CONTRIBUTING.md for details.

## Status

PR 1a: Data model, store (YAML I/O, compile, CRUD, Lamport clock), and serialization.
CLI, TUI, migration, cache, and TrackerSet come in later PRs.
