# SPDX-License-Identifier: AGPL-3.0-or-later
"""docs-prose:F1 / G7 — the docs-vs-argparse gate.

The structural fix for the INV-rotup CLI-help/README-drift umbrella. The
docs-prose:F2 sweep corrected stale CLI documentation by hand (a removed
ripgrep reference in ``--debug`` help, a README flag with the wrong
subcommand scope, …); that drift could accumulate only because *nothing*
diffed the documentation against the argparse parser. This gate is that
missing diff. Three standing checks, all reading the live ``build_parser()``:

1. **Removed-feature denylist.** Names of features/flags that were removed
   must never reappear in the ``--help --all`` output. This is the exact
   class that produced INV-bugiz (``--debug`` help still promised
   "ripgrep vs Python fallback decisions" for a code path that was deleted).

2. **README invocation surface.** Every ``hypergumbo …`` example in a fenced
   README code block must reference only subcommands and flags that exist in
   ``build_parser()`` (honoring the ``main()`` default-``sketch`` argv
   injection and the handful of flags ``main()`` handles outside argparse).
   This is a *membership* check on subcommand + flag tokens, not a full
   ``parse_args`` — it catches a renamed/removed flag or subcommand named in
   the README, but not invalid choice *values*, arity, or required positionals.

3. **Flag-availability matrix.** The per-subcommand option set is locked to a
   committed *serialized snapshot* (``.ci/cli-flag-matrix.json``) — unlike
   checks 1-2 this is a baseline, not pure live introspection. Any flag
   add/remove trips the gate until the maintainer regenerates the baseline, so
   a CLI surface change can't merge without a visible, reviewable matrix diff
   that prompts a docs check. Regenerate with::

       HYPERGUMBO_UPDATE_CLI_MATRIX=1 pytest \
         packages/hypergumbo-core/tests/test_cli_docs_prose_gate.py -k flag_matrix

Liveness floors (the G2 lesson — a silent zero-case run must be a loud red,
not a vacuous green): ``test_readme_has_invocations`` guards check 2 against
an extraction that breaks to ``[]``, and ``test_removed_feature_absent_from_help``
asserts the help text is substantive before searching it.
"""
from __future__ import annotations

import inspect
import io
import json
import os
import re
import shlex
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from hypergumbo_core import cli

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "README.md"
BASELINE = REPO_ROOT / ".ci" / "cli-flag-matrix.json"

# Smallest README invocation count that proves the extractor is live. 22 are
# parsed today; a floor of 10 survives routine doc edits while catching a
# regex/fence break that would otherwise zero out the surface check.
_MIN_README_INVOCATIONS = 10

# Features/flags removed from the CLI. Their names must never reappear in help
# output — a resurrected reference is exactly the INV-bugiz drift class.
REMOVED_FEATURES: dict[str, str] = {
    "ripgrep": (
        "the ripgrep-vs-Python ranking fallback was removed; help/docs must "
        "not promise it (INV-bugiz)"
    ),
}

# Flags that ``main()`` handles OUTSIDE argparse, so they are legitimate in the
# docs but never appear in build_parser()'s option strings. ``--all`` is only
# intercepted in the help path (``hypergumbo --help --all``); see _allowed().
ARGPARSE_EXTERNAL_FLAGS: frozenset[str] = frozenset({"--all"})


# ──────────────────────────── introspection ────────────────────────────────
def _subparsers_action(parser):
    return next(
        a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"
    )


def _subcommands(parser) -> dict[str, object]:
    return dict(_subparsers_action(parser).choices)


def _option_strings(action_holder) -> set[str]:
    return {
        opt
        for action in action_holder._actions
        for opt in action.option_strings
    }


def _global_flags(parser) -> set[str]:
    return _option_strings(parser)


def _current_matrix(parser) -> dict[str, list[str]]:
    """{"__global__": [...], "<subcommand>": [...], ...} of sorted flags."""
    matrix = {"__global__": sorted(_global_flags(parser))}
    for name, sub in sorted(_subcommands(parser).items()):
        matrix[name] = sorted(_option_strings(sub))
    return matrix


def _main_injection_subcommands() -> set[str]:
    """The hardcoded subcommand-name set ``main()`` keys its default-``sketch``
    argv injection off of (cli.py). This literal is the rule the *real* CLI
    enforces; the gate asserts it agrees with ``build_parser()`` so it can't
    silently diverge (a build_parser subcommand absent from this literal would
    mis-inject ``sketch`` at runtime while this gate kept passing)."""
    src = inspect.getsource(cli.main)
    match = re.search(r"subcommands\s*=\s*\{([^}]+)\}", src)
    assert match, "could not locate the main() injection subcommand literal"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _help_all_text() -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.print_all_help(cli.build_parser())
    return buf.getvalue()


# ──────────────────────────── README parsing ───────────────────────────────
_FENCE = re.compile(r"```(?:bash|sh|shell|zsh|console|text)?\n(.*?)```", re.DOTALL)
_PATHLIKE = re.compile(r"^(\.|\.\.|/|\[|<)|/$|\.[a-z]+$")


def _looks_pathlike(token: str) -> bool:
    return token in (".", "..") or bool(_PATHLIKE.search(token))


def _readme_invocations() -> list[list[str]]:
    """Token-lists (argv minus the leading ``hypergumbo``) for every *real*
    ``hypergumbo …`` line in a fenced README code block. Filters prose that
    merely starts with "hypergumbo" (a real invocation's first token is a
    subcommand, a flag, or a path), joins ``\\``-continuations, strips trailing
    ``# comments``, and drops bare ``...`` ellipsis placeholders."""
    text = README.read_text(encoding="utf-8")
    subcommands = set(_subcommands(cli.build_parser()))
    invocations: list[list[str]] = []
    for block in _FENCE.findall(text):
        joined = re.sub(r"\\\n\s*", " ", block)
        for line in joined.splitlines():
            line = line.split("#", 1)[0].strip()
            if not (line == "hypergumbo" or line.startswith("hypergumbo ")):
                continue
            try:
                tokens = shlex.split(line)[1:]
            except ValueError:
                continue  # unbalanced quotes ⇒ not a real invocation
            tokens = [t for t in tokens if t != "..."]
            if not tokens:
                continue
            first = tokens[0]
            if first.startswith("-") or first in subcommands or _looks_pathlike(first):
                invocations.append(tokens)
    return invocations


def _resolve(tokens: list[str], subcommands: set[str]) -> tuple[str | None, list[str]]:
    """Apply the main() default-sketch injection: return (subcommand, args).
    subcommand is None for a global-flag-only invocation (``--help --all``)."""
    first = tokens[0]
    if first in subcommands:
        return first, tokens[1:]
    if first.startswith("-"):
        return None, tokens
    return "sketch", tokens


def _allowed_flags(parser, name: str | None, tokens: list[str]) -> set[str]:
    """Flags legitimately usable in this invocation: global flags, the
    subcommand's own flags, and ``--all`` *only* in the help path — mirroring
    main()'s real interception (``--all`` is rejected by argparse otherwise)."""
    allowed = set(_global_flags(parser))
    if name is not None:
        allowed |= _option_strings(_subcommands(parser)[name])
    if "--help" in tokens or "-h" in tokens:
        allowed |= ARGPARSE_EXTERNAL_FLAGS
    return allowed


# ──────────────────────────── matrix diff ──────────────────────────────────
def _diff_matrix(
    current: dict[str, list[str]], baseline: dict[str, list[str]]
) -> str | None:
    """Return a human-readable drift report, or None if the matrices match.
    Pure (no I/O) so the gate's failure path is unit-testable."""
    if current == baseline:
        return None
    added = sorted(k for k in current if k not in baseline)
    removed = sorted(k for k in baseline if k not in current)
    changed = {
        k: {"baseline": baseline[k], "current": current[k]}
        for k in current.keys() & baseline.keys()
        if current[k] != baseline[k]
    }
    return (
        "CLI flag matrix drifted from .ci/cli-flag-matrix.json. If this is an "
        "intentional CLI change, regenerate the matrix (HYPERGUMBO_UPDATE_CLI_"
        "MATRIX=1 pytest -k flag_matrix) AND check the README / help text that "
        f"documents these commands.\n  added subcommands: {added}\n  removed "
        f"subcommands: {removed}\n  changed flags: {json.dumps(changed, indent=2)}"
    )


# ──────────────────────────────── tests ────────────────────────────────────
@pytest.mark.parametrize("token", sorted(REMOVED_FEATURES))
def test_removed_feature_absent_from_help(token: str) -> None:
    """A removed feature's name must not reappear in ``--help --all`` output."""
    help_text = _help_all_text().lower()
    # Liveness: the help output is substantive, so the denylist search is not
    # vacuously satisfied by empty/truncated output.
    assert len(help_text) > 1000 and "sketch" in help_text and "slice" in help_text, (
        "`hypergumbo --help --all` output looks empty/truncated — the "
        "removed-feature denylist check would pass vacuously"
    )
    assert token not in help_text, (
        f"removed feature {token!r} reappeared in `hypergumbo --help --all` "
        f"output — {REMOVED_FEATURES[token]}"
    )


def test_readme_has_invocations() -> None:
    """Liveness floor for check 2: the README extractor is not silently empty.
    An empty parametrize set would make the per-invocation surface check skip
    (pass on nothing) if a doc edit broke the fence regex or extraction."""
    n = len(_readme_invocations())
    assert n >= _MIN_README_INVOCATIONS, (
        f"only {n} hypergumbo invocations extracted from README fenced blocks "
        f"(expected >= {_MIN_README_INVOCATIONS}); the fence regex or README "
        "structure likely changed and the surface check is now vacuous"
    )


def test_main_injection_subcommands_match_parser() -> None:
    """The hardcoded subcommand set main() uses for default-sketch injection
    must equal build_parser()'s subcommands — otherwise the gate validates
    README examples against a rule the real CLI does not enforce."""
    parser_subs = set(_subcommands(cli.build_parser()))
    main_subs = _main_injection_subcommands()
    assert main_subs == parser_subs, (
        "main() default-sketch injection set drifted from build_parser(): "
        f"only in main(): {sorted(main_subs - parser_subs)}; only in parser: "
        f"{sorted(parser_subs - main_subs)}. A build_parser subcommand missing "
        "from the injection literal mis-injects 'sketch' at runtime."
    )


def _readme_params():
    return [pytest.param(t, id=" ".join(t)[:60]) for t in _readme_invocations()]


@pytest.mark.parametrize("tokens", _readme_params())
def test_readme_invocation_references_known_surface(tokens: list[str]) -> None:
    """Every README ``hypergumbo …`` example references only subcommands and
    flags the parser exposes (the docs-vs-argparse membership diff)."""
    parser = cli.build_parser()
    subcommands = set(_subcommands(parser))
    name, args = _resolve(tokens, subcommands)
    assert name is None or name in subcommands, (
        f"README invocation references unknown subcommand {name!r}: "
        f"hypergumbo {' '.join(tokens)}"
    )
    allowed = _allowed_flags(parser, name, tokens)
    for tok in args:
        if tok.startswith("-") and tok != "-":
            flag = tok.split("=", 1)[0]
            assert flag in allowed, (
                f"README invocation `hypergumbo {' '.join(tokens)}` uses flag "
                f"{flag!r} which is not available on "
                f"{name or 'the top-level parser'} (known: {sorted(allowed)})"
            )


def test_flag_matrix_matches_committed_baseline() -> None:
    """The per-subcommand flag set is locked to ``.ci/cli-flag-matrix.json``.
    Set ``HYPERGUMBO_UPDATE_CLI_MATRIX=1`` to regenerate after a deliberate CLI
    surface change (and then review the docs that mention it)."""
    current = _current_matrix(cli.build_parser())
    if os.environ.get("HYPERGUMBO_UPDATE_CLI_MATRIX"):  # pragma: no cover
        BASELINE.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        pytest.skip("regenerated .ci/cli-flag-matrix.json")
    assert BASELINE.exists(), (
        f"{BASELINE} missing — regenerate with "
        "HYPERGUMBO_UPDATE_CLI_MATRIX=1 pytest -k flag_matrix"
    )
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    drift = _diff_matrix(current, baseline)
    assert drift is None, drift


def test_diff_matrix_reports_drift() -> None:
    """Exercise the matrix detector's failure path (added / removed / changed)
    so the gate's own drift-reporting can't silently break."""
    baseline = {"__global__": ["--help"], "routes": ["--language", "--path"]}
    current = {
        "__global__": ["--help", "--new-global"],  # changed
        "routes": ["--path"],                        # changed (dropped --language)
        "newsub": ["--x"],                           # added
    }
    msg = _diff_matrix(current, baseline)
    assert msg is not None
    assert "newsub" in msg              # added subcommand surfaced
    assert "--new-global" in msg        # changed flags surfaced
    assert _diff_matrix(baseline, baseline) is None  # equal ⇒ no drift


def test_config_help_discloses_it_takes_no_substrate_input() -> None:
    """`config` shows the bundled per-language config (it reads no repo or
    behavior-map substrate), so it accepts neither a path nor ``--input``. Its
    help must say so — INV-rotup pass-30 found ``config --input`` is rejected
    with no hint in the help text (a flag-availability discoverability gap).
    """
    config_parser = _subcommands(cli.build_parser())["config"]
    help_text = config_parser.format_help().lower()
    assert "--input" in help_text
    assert "does not" in help_text or "neither" in help_text
