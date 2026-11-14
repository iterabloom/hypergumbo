# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/cli.py
"""The help shows only names a user can use.

Seen by a reader given only ``hypergumbo --help --all``: a section headed
``DEFAULT`` (an internal fallback printed as a heading), ``--path
_PATH_FLAG`` on twenty-four lines (an argparse dest printed as a metavar),
and eleven tracker IDs nobody outside the project can look up.

Every check here walks the parser as built, or the help it renders, so a
subcommand or option added later is covered without editing this file.
"""

from __future__ import annotations

import argparse
import io
import re
from contextlib import redirect_stdout

from hypergumbo_core import cli

TRACKER_ID = re.compile(r"\b(?:WI|INV|META)-[a-z]{5}\b|\bdispatch:F\d+\b")
INTERNAL_NAME = re.compile(r"(?<![\w-])_[A-Za-z][A-Za-z0-9_]*")


def _subparsers_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:  # type: ignore[type-arg]
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))


def _parsers() -> dict[str, argparse.ArgumentParser]:
    """Every parser by the name a user types it under ("" is the top level;
    an alias is its own entry)."""
    parser = cli.build_parser()
    return {"": parser, **_subparsers_action(parser).choices}


def _help_all() -> str:
    buffer = io.StringIO()
    parser = cli.build_parser()
    with redirect_stdout(buffer):
        parser.print_help()
        cli.print_all_help(parser)
    return buffer.getvalue()


def test_reach() -> None:
    """The walks below see the whole command line, not an empty parser."""
    parsers = _parsers()
    assert len(parsers) > 30 and {"sketch", "survey", "run", "verify-claims"} <= set(parsers)
    assert sum(len(p._actions) for p in parsers.values()) > 200
    assert len(_help_all()) > 40_000


def test_every_subcommand_is_in_a_named_help_group() -> None:
    """A subcommand with no group was listed under a heading made from the
    fallback name. The heading now comes from ``HELP_GROUP_LABELS`` with no
    fallback, so an ungrouped subcommand fails here, not in the help."""
    actions = _subparsers_action(cli.build_parser())._choices_actions
    assert len(actions) > 25
    ungrouped = [a.dest for a in actions
                 if getattr(a, "group", None) not in cli.HELP_GROUP_LABELS]
    assert ungrouped == []
    headings = [line.strip() for line in _help_all().splitlines()
                if line.strip() in cli.HELP_GROUP_LABELS.values()]
    assert headings == list(cli.HELP_GROUP_LABELS.values())


def test_no_option_shows_an_internal_name() -> None:
    """What argparse prints for an option's value is its metavar, else its
    dest in capitals. A dest chosen for the code (``_path_flag``) needs a
    metavar chosen for the reader."""
    shown: list[str] = []
    for name, parser in _parsers().items():
        for action in parser._actions:
            metavar = action.metavar if action.metavar is not None else action.dest
            for part in (metavar if isinstance(metavar, tuple) else (metavar,)):
                if str(part).startswith("_"):
                    shown.append(f"{name or 'hypergumbo'} {'/'.join(action.option_strings)}: {part}")
    assert shown == []
    assert not INTERNAL_NAME.findall(_help_all())
    # Reach: the pattern sees the defect it guards against.
    assert INTERNAL_NAME.findall("  --path _PATH_FLAG     Path to repo root")


def test_the_help_names_no_tracker_item() -> None:
    """An item ID means something only inside this project's tracker. A
    design record (``ADR-0016``) names a file under docs/adr and may stay."""
    text = _help_all()
    assert TRACKER_ID.findall(text) == []
    assert "ADR-" in text
    assert TRACKER_ID.findall("slices (WI-sihok). dispatch:F7 (INV-fotav)") == [
        "WI-sihok", "dispatch:F7", "INV-fotav"]
