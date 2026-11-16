# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/cli.py
"""The help says one thing one way.

Seen by a reader given only ``hypergumbo --help --all``: ``--input``
described two ways for one behaviour (five commands said "default:
survey.json"; all ten find the cached survey or run the analysis), the
default sketch budget stated as 8000 tokens and as "auto", and one noun
spelled two ways in the names a user types (``catalog`` / ``catalogue``,
``rust-analyzer`` / ``rust_analyzer``).

The checks walk the parser as built, so a subcommand, option or choice added
later is covered without editing this file.
"""

from __future__ import annotations

import argparse
import io
import itertools
import re
from contextlib import redirect_stdout

import pytest

from hypergumbo_core import cli


def _parsers() -> dict[str, argparse.ArgumentParser]:
    """Every parser by the name a user types it under ("" is the top level;
    an alias is its own entry)."""
    parser = cli.build_parser()
    subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return {"": parser, **subparsers.choices}


def _help_all() -> str:
    buffer = io.StringIO()
    parser = cli.build_parser()
    with redirect_stdout(buffer):
        parser.print_help()
        cli.print_all_help(parser)
    return buffer.getvalue()


def test_input_is_described_once() -> None:
    """Ten commands resolve ``--input`` through one discover-or-analyse step
    and five of them said "default: survey.json". Every parser whose
    ``--input`` is optional now takes its help from one definition."""
    optional = {name: action.help for name, parser in _parsers().items()
                for action in parser._actions
                if "--input" in action.option_strings and not action.required}
    assert len(optional) >= 11
    assert set(optional.values()) == {cli.INPUT_HELP}
    assert "default: survey.json" not in _help_all()


def test_the_default_sketch_budget_is_stated_from_the_code() -> None:
    text = _help_all()
    assert f"default: {cli.DEFAULT_SKETCH_TOKENS}" in " ".join(text.split())
    assert "auto token budget" not in text
    args = cli.build_parser().parse_args(["sketch", "."])
    assert args.tokens is None  # no -t: cmd_sketch falls back to the constant


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _fold(word: str) -> str:
    """Spelling variants of one noun fold together: plural, and a trailing
    ``-ue`` (catalogue / catalog, dialogue / dialog, analogue / analog)."""
    word = _singular(word)
    return word[:-2] if word.endswith("ue") else word


def _typed_names(parser: argparse.ArgumentParser) -> set[str]:
    """Names a user types at this parser: its long options, their choices,
    and (for the top level) the subcommands."""
    names: set[str] = set()
    for action in parser._actions:
        names.update(o.lstrip("-") for o in action.option_strings if o.startswith("--"))
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices)
        elif action.choices:
            names.update(str(c) for c in action.choices)
    return names


def _respell(name: str, word: str, other: str) -> str:
    return "-".join(other + ("s" if part.endswith("s") and _singular(part) == word else "")
                    if _singular(part) == word else part
                    for part in name.split("-"))


def test_a_noun_spelled_two_ways_is_accepted_both_ways() -> None:
    """Across everything a user types, find the nouns that occur in two
    spellings. Each name holding one must be accepted in the other spelling
    in the same place, so nobody has to remember which command took which.
    Which spelling the help leads with is a separate, owner's, decision."""
    per_parser = {name: _typed_names(parser) for name, parser in _parsers().items()}
    words = {_singular(part) for names in per_parser.values()
             for typed in names for part in re.split(r"[-_]", typed)}
    variants = [(a, b) for a, b in itertools.permutations(sorted(words), 2)
                if _fold(a) == _fold(b)]
    assert ("catalog", "catalogue") in variants  # reach: the filed pair
    missing = []
    for parser_name, names in per_parser.items():
        for typed in names:
            for word, other in variants:
                if word in {_singular(p) for p in typed.split("-")}:
                    wanted = _respell(typed, word, other)
                    if wanted not in names:
                        missing.append(f"{parser_name or 'hypergumbo'}: {typed} without {wanted}")
    assert missing == []


def test_no_two_names_differ_only_by_separator() -> None:
    """``rust-analyzer`` in one place and ``rust_analyzer`` in another is one
    backend with two spellings a user must keep apart."""
    names = sorted({typed for parser in _parsers().values() for typed in _typed_names(parser)})
    clashes = [(a, b) for a, b in itertools.combinations(names, 2)
               if a != b and a.replace("_", "-") == b.replace("_", "-")]
    assert clashes == []


@pytest.mark.parametrize("spelling", ["rust-analyzer", "rust_analyzer"])
def test_trust_backend_takes_either_spelling(spelling: str) -> None:
    args = cli.build_parser().parse_args(["trust-backend", spelling, "--show"])
    assert args.backend_name == "rust_analyzer"


@pytest.mark.parametrize(("typed", "canonical"), [
    ("catalogue", "catalog"), ("init-catalogues", "init-catalogs"),
    ("catalogue-inventory", "catalog-inventory"), ("trust-catalogs", "trust-catalogues"),
])
def test_an_alias_runs_the_same_command(typed: str, canonical: str) -> None:
    parsers = _parsers()
    assert parsers[typed] is parsers[canonical]
    assert typed in cli._registered_subcommands(cli.build_parser())


@pytest.mark.parametrize("command", ["io-boundaries", "verify-claims"])
def test_in_repo_catalogues_flag_takes_either_spelling(command: str) -> None:
    extra = ["--claims", "c.yaml"] if command == "verify-claims" else []
    for flag in ("--in-repo-catalogues", "--in-repo-catalogs"):
        args = cli.build_parser().parse_args([command, ".", flag, *extra])
        assert args.in_repo_catalogues is True
