# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-vafit — the user's inventory of what this installation knows.

WHY THIS IS NOT A PORT OF ``scripts/yaml-catalog-index``. That script answers
the OPERATOR's question ("is the registry in sync with the tree") and ships to
nobody — ``pyproject.toml`` packages ``src/hypergumbo_core``, so ``scripts/`` is
not in the wheel and an installed hypergumbo cannot run it. The user's questions
are different, and the tests below are organised by them: what is loaded, what
may I extend and where, does my language have a catalogue and is it finished,
and why should I care.

THE TWO PROPERTIES MOST LIKELY TO ROT are gated rather than described. The
family list and every extensibility answer are DERIVED from ``YAML_CATALOGS``,
so a new family cannot appear here with a blank answer; and ``read_now`` is
pinned BEHAVIOURALLY, so wiring a second channel (WI-sofov) fails these tests
until the inventory is told, instead of the inventory quietly advertising a
capability the tool does not have.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from hypergumbo_core.catalogue_inventory import (
    FAMILY_CONSEQUENCE,
    build_inventory,
)
from hypergumbo_core.yaml_catalogs import YAML_CATALOGS


@pytest.fixture()
def inv(tmp_path: Path):
    return build_inventory("9.9.9", environ={"XDG_CONFIG_HOME": str(tmp_path)})


# ---------------------------------------------------------------- derived ---

def test_every_registry_family_is_reported(inv) -> None:
    assert {f.directory for f in inv.families} == {
        s.directory for s in YAML_CATALOGS}


def test_every_family_carries_a_reason_to_care(inv) -> None:
    """Question 4. A row count is not an answer, and a family that arrives
    without a sentence here is a family the inventory would describe as a
    number only."""
    assert set(FAMILY_CONSEQUENCE) == {s.directory for s in YAML_CATALOGS}
    for family in inv.families:
        assert family.consequence.strip()


def test_extensibility_is_the_registry_s_answer_not_a_second_one(inv) -> None:
    declared = {s.directory: s.user_channel for s in YAML_CATALOGS}
    for family in inv.families:
        assert family.extensible == (declared[family.directory] is not None)
        assert family.channel == declared[family.directory]


def test_a_non_extensible_family_says_why(inv) -> None:
    """The registry requires a reason exactly when there is no channel
    (ADR-0047 ruling 7); the inventory must surface it, because "no" without a
    reason reads as an omission."""
    for family in inv.families:
        if not family.extensible:
            assert family.no_channel_reason
            assert family.channel_path is None


# ------------------------------------------------------- where my file goes --

def test_the_channel_path_is_inside_the_users_own_home(inv, tmp_path) -> None:
    """Question 2 is "where does MY file go" — an answer that names a
    directory inside the wheel would be worse than no answer."""
    for family in inv.families:
        if family.channel_path is not None:
            assert family.channel_path.parent == tmp_path / "hypergumbo"
            assert family.channel_path.name == family.channel


def test_your_files_counts_what_the_user_actually_put_there(tmp_path) -> None:
    channel = tmp_path / "hypergumbo" / "io_primitives.d"
    channel.mkdir(parents=True)
    (channel / "mine.yaml").write_text("language: python\n")
    inv = build_inventory("9.9.9", environ={"XDG_CONFIG_HOME": str(tmp_path)})
    io_row = next(f for f in inv.families if f.directory == "io_primitives")
    assert io_row.your_files == 1


# ------------------------------------------------------------ wired != declared

def test_declared_extensible_is_not_reported_as_readable(inv) -> None:
    """THE DISTINCTION THIS VIEW EXISTS TO MAKE. Five of the six declared
    channels are read by nothing today: the registry says the family is
    extensible and no loader has been taught to look (WI-sofov). Reporting
    them as usable would send a user to write a file that does nothing."""
    extensible = [f for f in inv.families if f.extensible]
    assert len(extensible) > 1, "fixture wrong: only one extensible family"
    read_now = {f.directory for f in extensible if f.read_now}
    assert read_now == {"io_primitives", "frameworks", "dataflow_patterns",
                        "function_summaries"}, (
        "a channel changed readability without the inventory being told — "
        "update _WIRED_CHANNELS so users are neither sent to an inert "
        "directory nor told a working one is dead"
    )
    still_inert = {f.directory for f in extensible if not f.read_now}
    assert still_inert == {"taint_sources", "taint_sanitizers"}, (
        "the remaining unwired channels changed — a user must not be sent to "
        "a directory nothing reads, nor told a working one is dead"
    )


def test_the_wired_channel_really_is_wired(tmp_path, monkeypatch) -> None:
    """PINNED BEHAVIOURALLY, not by agreement between two constants. If the
    io_primitives scan is ever removed, this fails rather than the inventory
    continuing to advertise it."""
    from hypergumbo_core.cli import _resolve_io_overlays
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    channel = tmp_path / "hypergumbo" / "io_primitives.d"
    channel.mkdir(parents=True)
    (channel / "mine.yaml").write_text("language: python\n")

    class _A:
        io_primitives: ClassVar[list[str]] = []

    assert channel / "mine.yaml" in _resolve_io_overlays(_A())


# ------------------------------------------------------------------ languages

def test_languages_come_from_the_tree_not_from_catalog_languages(inv) -> None:
    """``CATALOG_LANGUAGES`` names fourteen; the package ships fifteen, and
    ``bash.yaml`` is the one outside it (WI-surun). An inventory built on that
    constant would under-report the installation it describes."""
    from hypergumbo_core.io_boundary import _CATALOG_DIR  # type: ignore[attr-defined]
    on_disk = {p.stem for p in Path(_CATALOG_DIR).glob("*.yaml")}
    assert {lang.language for lang in inv.languages} == on_disk
    assert "bash" in on_disk


def test_each_language_reports_a_status_and_a_row_count(inv) -> None:
    for lang in inv.languages:
        assert lang.rows > 0
        assert lang.status in {"provenance_declared", "in_progress"}


def test_unvouched_rows_are_counted_where_the_overlays_are(inv) -> None:
    """The count a reader needs to weigh a clean result: these rows can add a
    finding but never license an all-clear."""
    by_lang = {lang.language: lang for lang in inv.languages}
    assert by_lang["haskell"].unvouched_rows > 0
    assert by_lang["haskell"].shipped_overlays > 0
    assert by_lang["rust"].unvouched_rows == 0, "control: rust ships no overlay"


# ------------------------------------------------------------------- the CLI

class _Args:
    pass


def test_text_output_answers_all_four_questions(capsys, monkeypatch,
                                                tmp_path) -> None:
    from hypergumbo_core.cli import cmd_catalog_inventory
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    args = _Args()
    args.format = "text"
    assert cmd_catalog_inventory(args) == 0
    out = capsys.readouterr().out
    assert "catalogue inventory" in out          # what this is
    assert "io_primitives.d/" in out             # where my file goes
    assert "NOT read yet" in out                 # and where it would be inert
    assert "Not extensible:" in out              # and why not, for the rest
    assert "in_progress" in out                  # per-language status
    assert "unvouched" in out                    # how to weigh a clean result
    assert "Without them" in out or "Without these" in out   # why care


def test_json_output_carries_the_same_facts(capsys, monkeypatch,
                                            tmp_path) -> None:
    from hypergumbo_core.cli import cmd_catalog_inventory
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    args = _Args()
    args.format = "json"
    assert cmd_catalog_inventory(args) == 0
    doc = json.loads(capsys.readouterr().out)
    assert {f["directory"] for f in doc["families"]} == {
        s.directory for s in YAML_CATALOGS}
    assert len(doc["languages"]) == 15
    io_row = next(f for f in doc["families"] if f["directory"] == "io_primitives")
    assert io_row["read_now"] is True
    fw = next(f for f in doc["families"] if f["directory"] == "frameworks")
    assert fw["extensible"] is True and fw["read_now"] is True
    # Still-inert, and the one whose gate is not built yet.
    fs = next(f for f in doc["families"]
              if f["directory"] == "function_summaries")
    assert fs["extensible"] is True and fs["read_now"] is True
    # The gate has to survive to the reader: this is the one channel whose
    # entries can DELETE a finding, and the caveat is what discloses it.
    assert fs["channel_gated"] == "CAVEAT_USER_SUPPLIED_SANITIZER"
    ts = next(f for f in doc["families"] if f["directory"] == "taint_sources")
    assert ts["extensible"] is True and ts["read_now"] is False
    # Section-scoped: readable, but the scope has to survive to the reader.
    df = next(f for f in doc["families"]
              if f["directory"] == "dataflow_patterns")
    assert df["read_now"] is True and df["channel_scope"] == "library_patterns"


def test_the_subcommand_is_reachable_through_main(tmp_path, monkeypatch) -> None:
    from hypergumbo_core.cli import main
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["catalog-inventory", "--format", "json"]) == 0


def test_it_is_a_different_question_from_catalog() -> None:
    """`catalog` lists what hypergumbo can ANALYSE; this lists the DATA behind
    that. Both exist, and the help text has to distinguish them or the second
    verb is just confusing (the item's own naming concern)."""
    import argparse as _argparse
    from hypergumbo_core.cli import build_parser
    subs = {}
    for action in build_parser()._actions:
        if isinstance(action, _argparse._SubParsersAction):
            subs = action.choices
    assert "catalog" in subs and "catalog-inventory" in subs
    assert "analyze" in (subs["catalog"].format_help() or "").lower()
    assert "ANALYSE" in (subs["catalog-inventory"].epilog or "")
