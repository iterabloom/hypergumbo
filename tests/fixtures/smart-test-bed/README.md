<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# smart-test bed

A small repo for scoring smart-test's selectors. `scripts/smart-test-bed` copies
`tree/` into a temporary git repo, applies each edit in `edits.yaml`, and
compares what smart-test selects with what the edit can break. The harness
docstring explains the oracle and the scoring.

```
scripts/smart-test-bed run              # every selector, every edit (~1 min)
scripts/smart-test-bed run narrow --edit body-edit-c
scripts/smart-test-bed materialize /tmp/bed
```

## Rules for adding to it

- Files in `tree/` carry a `.in` suffix, which the harness strips. This keeps
  pytest, ruff, mypy and hypergumbo's own self-analysis from treating them as
  this repo's code.
- A bed test must not catch `Exception` broadly. The oracle makes a changed
  block raise `RuntimeError`, and a broad `except` would hide that.
- A test that reads a file's text, not just its behaviour, goes in
  `readers.yaml`. No import graph or coverage trace can find that edge, so it
  has to be declared.
- `test_citations` cites `bedcore/chain_c.py:4`. An edit that moves that line
  breaks the citation on purpose. An edit that doesn't mean to should put its
  change below the line.
- In `edits.yaml`, a block scalar loses its first line's indentation. A
  one-line `old`/`new` pair therefore matches text in the middle of a line.
  When `new` has several lines, start both `old` and `new` at a line whose
  indentation all of their lines share.
- An edit's `should_not` lists tests that have no path to the change. Only
  list a test you can name a reason for.

`tests/test_smart_test_bed.py` checks that every path these files name exists,
and runs three edits end to end.
