# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0047 rulings 3 and 4: the user's catalogue data gets a findable home,
and it is created by an EXPLICIT subcommand.

WHY A HOME AT ALL. Nobody edits files inside their site-packages. A catalogue
family that the registry declares user-extensible (ruling 7, ``user_channel``)
has to have a directory a user can find without being told where pip put the
wheel. Ruling 3 puts it at ``$XDG_CONFIG_HOME/hypergumbo/<family>.d/``, with the
repo tier at ``<repo>/.hypergumbo/``.

WHY NOT ON FIRST RUN. Ruling 4: materialization is an explicit subcommand.
ADR-0045's precedent is a HUMAN-OWNED config file the tool may read and must not
write, and silently creating files in someone's config directory on first
invocation is exactly the surprise that precedent exists to avoid. Default-on
loading does not need materialization either — the shipped overlays load from
the wheel; the subcommand exists so a user can EDIT them.

SEED, NEVER COPY (ruling 2), which is the property most likely to be broken by a
well-meaning change. Base catalogues stay in the wheel and are NEVER
materialized; only DELTAS — the overlays — land in the user's directory. A full
copy means the next release's rows never reach that user, silently, and the tool
degrades for exactly the people who engaged with it enough to run the command.
``test_the_base_catalogues_are_never_materialized`` is the gate on that.

THE DIRECTORY LIST IS DERIVED, NOT WRITTEN DOWN TWICE. It comes from
``YAML_CATALOGS`` — the same registry ``yaml-catalog-index --check`` gates — so a
family that gains a channel gains a directory with no edit here, and a family
with ``no_channel_reason`` cannot acquire one by being forgotten about. One
fact, one home.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import ClassVar

import pytest

from hypergumbo_core.catalogue_home import (
    channel_directories,
    materialize_catalogue_home,
    repo_catalogue_home,
    user_catalogue_home,
)
from hypergumbo_core.yaml_catalogs import YAML_CATALOGS


# ----------------------------------------------------------------------
# Ruling 3 — where the home is
# ----------------------------------------------------------------------

def test_user_home_honours_xdg_config_home() -> None:
    got = user_catalogue_home(environ={"XDG_CONFIG_HOME": "/x/cfg"},
                              home=Path("/home/nobody"))
    assert got == Path("/x/cfg/hypergumbo")


def test_user_home_falls_back_to_dot_config_under_home() -> None:
    """The XDG default, and it must not read the real process environment —
    the injected ``home`` is what decides."""
    got = user_catalogue_home(environ={}, home=Path("/home/nobody"))
    assert got == Path("/home/nobody/.config/hypergumbo")


def test_user_home_is_the_config_files_own_directory() -> None:
    """ONE HOME, NOT TWO. ``config.toml`` (ADR-0045) and the catalogue channels
    are the same directory; if these ever diverge a user has to learn two
    locations and the ADR-0045 setting stops describing its own neighbours."""
    from hypergumbo_core.user_config import user_config_path
    env, home = {"XDG_CONFIG_HOME": "/x/cfg"}, Path("/home/nobody")
    assert user_config_path(env, home).parent == user_catalogue_home(env, home)


def test_repo_tier_is_a_directory_in_the_repo() -> None:
    assert repo_catalogue_home(Path("/srv/proj")) == Path("/srv/proj/.hypergumbo")


def test_repo_tier_is_not_the_project_config_file() -> None:
    """``.hypergumbo.toml`` (a file, ADR-0045) and ``.hypergumbo/`` (a
    directory, this ADR) are different things that differ by one character.
    Pinned so a later 'tidy-up' cannot merge them."""
    from hypergumbo_core.user_config import project_config_path
    root = Path("/srv/proj")
    assert repo_catalogue_home(root) != project_config_path(root)


# ----------------------------------------------------------------------
# The channel list is DERIVED from the registry
# ----------------------------------------------------------------------

def test_channel_directories_come_from_the_registry() -> None:
    expected = {s.user_channel for s in YAML_CATALOGS if s.user_channel}
    assert set(channel_directories()) == expected
    assert expected, "registry declares no user channels — the fixture is wrong"


def test_a_family_with_no_channel_gets_no_directory() -> None:
    """``cfg_nodes``, ``url_folding`` and ``io_primitives_overlays`` declare
    ``no_channel_reason``. A directory for them would invite edits the loader
    would ignore — worse than no directory at all."""
    silent = {s.directory for s in YAML_CATALOGS if not s.user_channel}
    assert silent, "fixture wrong: registry has no channel-less families"
    for name in channel_directories():
        assert name.removesuffix(".d") not in silent


def test_every_channel_directory_is_named_for_its_family() -> None:
    """``yaml-catalog-index --check`` already refuses a channel that is not
    ``<directory>.d``; asserted here too because THIS is the code that turns the
    name into a real directory on someone's disk."""
    for spec in YAML_CATALOGS:
        if spec.user_channel:
            assert spec.user_channel == f"{spec.directory}.d"


# ----------------------------------------------------------------------
# Ruling 4 — the subcommand, and what it writes
# ----------------------------------------------------------------------

@pytest.fixture()
def home(tmp_path: Path) -> Path:
    return tmp_path / "cfg" / "hypergumbo"


def test_materialize_creates_every_channel_directory(home: Path) -> None:
    result = materialize_catalogue_home(home, version="9.9.9")
    for name in channel_directories():
        assert (home / name).is_dir(), name
    assert result.home == home


def test_materialize_writes_a_readme_that_names_the_layout(home: Path) -> None:
    result = materialize_catalogue_home(home, version="9.9.9")
    text = result.readme.read_text()
    assert result.readme == home / "README.md"
    for name in channel_directories():
        assert name in text, f"README does not mention {name}"
    assert "seed" in text.lower()


def test_materialize_seeds_the_community_overlays(home: Path) -> None:
    """Ruling 9: the subcommand populates the home with the community overlays.
    They are DELTAS, so seeding them is consistent with ruling 2."""
    result = materialize_catalogue_home(home, version="9.9.9")
    seeded = {p.name for p in result.seeded}
    assert seeded, "nothing seeded"
    for p in result.seeded:
        assert p.parent == home / "io_primitives.d"
        assert p.is_file()


def test_a_seeded_overlay_records_what_it_was_seeded_from(home: Path) -> None:
    """Ruling 5: ``seeded_from:`` names the hypergumbo version, so staleness
    against the shipped source is a fact a reader can check rather than a
    sentence in a header."""
    result = materialize_catalogue_home(home, version="9.9.9")
    for path in result.seeded:
        import yaml
        doc = yaml.safe_load(path.read_text())
        assert doc.get("seeded_from") == "9.9.9", path.name
        assert doc.get("provenance") == "community", path.name
        assert doc.get("retrieved"), path.name


def test_the_base_catalogues_are_never_materialized(home: Path) -> None:
    """SEED, NEVER COPY — ruling 2, and the property whose violation is silent.

    A full copy means the next release's rows never reach this user. So no
    seeded filename may be one of the shipped BASE catalogue files: those live
    in the wheel and stay there."""
    from hypergumbo_core.io_boundary import _CATALOG_DIR  # type: ignore[attr-defined]
    base = {p.name for p in Path(_CATALOG_DIR).glob("*.yaml")}
    result = materialize_catalogue_home(home, version="9.9.9")
    assert base, "fixture wrong: no base catalogues found"
    assert not {p.name for p in result.seeded} & base


def test_materialize_is_idempotent_and_never_clobbers_an_edit(home: Path) -> None:
    """The command a user runs twice must not eat what they wrote in between.
    A tool that overwrites an edited overlay teaches people not to run it."""
    first = materialize_catalogue_home(home, version="9.9.9")
    edited = first.seeded[0]
    edited.write_text(edited.read_text() + "\n# my own note\n")
    second = materialize_catalogue_home(home, version="9.9.9")
    assert "# my own note" in edited.read_text()
    assert edited in second.skipped
    assert edited not in second.seeded


def test_materialize_reports_what_it_skipped_rather_than_staying_silent(
    home: Path,
) -> None:
    materialize_catalogue_home(home, version="9.9.9")
    second = materialize_catalogue_home(home, version="9.9.9")
    assert second.seeded == ()
    assert len(second.skipped) > 0


def test_materialize_never_writes_outside_the_home(home: Path,
                                                   tmp_path: Path) -> None:
    """Ruling 9's hard constraint, asserted as containment rather than as a
    promise: every path the call reports must be under the home it was given."""
    result = materialize_catalogue_home(home, version="9.9.9")
    for path in (*result.seeded, *result.skipped, *result.created_dirs,
                 result.readme):
        assert home in path.parents or path == home


def test_nothing_is_created_implicitly_by_loading_a_catalogue(
    tmp_path: Path,
) -> None:
    """RULING 4 IS ABOUT WHAT DOES **NOT** HAPPEN, so it needs a test that
    fails if the write ever becomes implicit. Loading catalogues — the ordinary
    path every analysis takes — must leave the home absent."""
    from hypergumbo_core.io_boundary import load_catalog
    absent = tmp_path / "cfg" / "hypergumbo"
    load_catalog("python")
    load_catalog("haskell")
    assert not absent.exists()


def test_the_result_is_a_frozen_record(home: Path) -> None:
    """The caller (the CLI) renders this; a mutable result invites a caller to
    'fix up' the report instead of the behaviour."""
    result = materialize_catalogue_home(home, version="9.9.9")
    assert dataclasses.is_dataclass(result)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.home = Path("/elsewhere")  # type: ignore[misc]


# ----------------------------------------------------------------------
# The property that makes the directory worth creating
# ----------------------------------------------------------------------

def test_a_materialized_overlay_still_loads_as_a_user_overlay(home: Path) -> None:
    """THE POINT OF THE WHOLE FEATURE, and the one failure that would make it
    worse than doing nothing: shipping a directory whose contents the loader
    rejects. The ``seeded_from:`` stamp is a new top-level key, so this is not
    hypothetical — it asserts the stamped file is still valid input.

    Also checks the rows arrive VOUCHED. A shipped default is stamped
    ``unvouched`` at merge and therefore does not EXAMINE (#598/ADR-0047); a
    file the user has materialized into their own config home is theirs, and
    the distinction is the whole reason the third state exists."""
    from hypergumbo_core.io_boundary import load_catalog

    result = materialize_catalogue_home(home, version="9.9.9")
    seeded = [p for p in result.seeded if p.name.startswith("python-")]
    assert seeded, "fixture wrong: no python overlay was seeded"

    catalog = load_catalog("python", overlay_paths=seeded, include_defaults=False)
    from_user = {p.qualified_name for p in catalog.primitives}
    baseline = {p.qualified_name for p in
                load_catalog("python", include_defaults=False).primitives}
    assert from_user - baseline, "the seeded overlay contributed no rows"
    assert not any(p.unvouched for p in catalog.primitives), (
        "a row the user materialized into their own config home must not be "
        "stamped unvouched — that state is for rows hypergumbo ships"
    )


def test_the_stamp_survives_a_yaml_round_trip_for_every_shipped_overlay(
    home: Path,
) -> None:
    """Every seeded file, not just one — the stamp is appended textually, and
    an overlay whose last block ended differently could in principle break."""
    import yaml
    result = materialize_catalogue_home(home, version="9.9.9")
    for path in result.seeded:
        doc = yaml.safe_load(path.read_text())
        assert isinstance(doc, dict), path.name
        assert doc["seeded_from"] == "9.9.9", path.name


def test_seeding_preserves_the_overlays_explanatory_comments(home: Path) -> None:
    """Those headers carry the REASON the rows are unvouched. A YAML
    round-trip would silently delete them, which is why the stamp is appended
    as text; this fails if someone 'tidies' that into a dumper."""
    result = materialize_catalogue_home(home, version="9.9.9")
    for path in result.seeded:
        source = (Path(__file__).parent.parent / "src" / "hypergumbo_core"
                  / "io_primitives_overlays" / path.name)
        original_comments = [ln for ln in source.read_text().splitlines()
                             if ln.startswith("#")]
        seeded_comments = path.read_text().splitlines()
        for line in original_comments:
            assert line in seeded_comments, f"{path.name}: lost {line!r}"


def test_an_overlay_dir_can_be_injected_for_testing(tmp_path: Path) -> None:
    """``overlay_dir`` exists so a test does not have to depend on how many
    overlays hypergumbo currently ships."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "only-one.yaml").write_text(
        "language: python\nstatus: overlay\nprovenance: community\n"
        "retrieved: 2026-01-01\nnet_send:\n  - module: x\n    functions: [y]\n"
    )
    result = materialize_catalogue_home(tmp_path / "home", version="1.2.3",
                                        overlay_dir=src)
    assert [p.name for p in result.seeded] == ["only-one.yaml"]


# ----------------------------------------------------------------------
# The subcommand (ruling 4's "explicit")
# ----------------------------------------------------------------------

class _Args:
    """Minimal namespace, matching the shape used across the CLI tests."""


def test_cmd_reports_what_it_created(tmp_path: Path, capsys) -> None:
    from hypergumbo_core.cli import cmd_init_catalogs
    args = _Args()
    args.home, args.format = str(tmp_path / "h"), "text"
    assert cmd_init_catalogs(args) == 0
    out = capsys.readouterr().out
    assert "Catalogue home:" in out
    assert "seeded" in out
    assert "DELTAS, not a copy" in out


def test_cmd_second_run_says_there_is_nothing_to_do(tmp_path: Path,
                                                    capsys) -> None:
    from hypergumbo_core.cli import cmd_init_catalogs
    args = _Args()
    args.home, args.format = str(tmp_path / "h"), "text"
    cmd_init_catalogs(args)
    capsys.readouterr()
    assert cmd_init_catalogs(args) == 0
    out = capsys.readouterr().out
    assert "already present" in out
    assert "nothing to do" in out


def test_cmd_json_names_the_version_it_seeded_from(tmp_path: Path,
                                                   capsys) -> None:
    import json as _json
    from hypergumbo_core import __version__
    from hypergumbo_core.cli import cmd_init_catalogs
    args = _Args()
    args.home, args.format = str(tmp_path / "h"), "json"
    assert cmd_init_catalogs(args) == 0
    doc = _json.loads(capsys.readouterr().out)
    assert doc["seeded_from"] == __version__
    assert doc["seeded"] and doc["created_dirs"]


def test_cmd_without_home_uses_the_xdg_location(tmp_path: Path,
                                                monkeypatch) -> None:
    """The default path is the XDG one, exercised without writing to the
    developer's real config directory."""
    from hypergumbo_core.cli import cmd_init_catalogs
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    args = _Args()
    args.home, args.format = None, "text"
    assert cmd_init_catalogs(args) == 0
    assert (tmp_path / "xdg" / "hypergumbo" / "io_primitives.d").is_dir()


def test_one_directory_is_reported_in_the_singular(tmp_path: Path,
                                                   capsys) -> None:
    """A cosmetic branch, but an uncovered branch in output code is how
    'created 1 directories' ships."""
    from hypergumbo_core.cli import cmd_init_catalogs
    home = tmp_path / "h"
    materialize_catalogue_home(home, version="9.9.9")
    # frameworks.d, not io_primitives.d: the latter holds the seeded
    # overlays, so rmdir would raise rather than set up the case.
    (home / "frameworks.d").rmdir()
    args = _Args()
    args.home, args.format = str(home), "text"
    cmd_init_catalogs(args)
    assert "created 1 directory" in capsys.readouterr().out


# ----------------------------------------------------------------------
# ONE FACT, ONE HOME — the did-you-mean guard's subcommand set
# ----------------------------------------------------------------------

def test_every_registered_subcommand_is_accepted_by_the_guard() -> None:
    """THE BUG THIS PINS, found on this very feature. The guard's set used to
    be a hand-written literal; a new subcommand parsed fine and was then
    rejected as "not a valid subcommand" with a suggestion to run a DIFFERENT
    one. Deriving the set from the parser makes that unrepresentable, and this
    fails if anyone reintroduces a literal."""
    import argparse as _argparse
    from hypergumbo_core.cli import _registered_subcommands, build_parser
    parser = build_parser()
    registered = set()
    for action in parser._actions:
        if isinstance(action, _argparse._SubParsersAction):
            registered = set(action.choices)
    assert registered
    assert _registered_subcommands(parser) == registered
    assert "init-catalogs" in registered


def test_the_new_subcommand_is_reachable_through_main(tmp_path: Path) -> None:
    """End to end through argv, which is the path that was broken."""
    from hypergumbo_core.cli import main
    rc = main(["init-catalogs", "--home", str(tmp_path / "h"), "--format", "json"])
    assert rc == 0
    assert (tmp_path / "h" / "README.md").is_file()


# ----------------------------------------------------------------------
# The scan — without which the whole home is decoration
# ----------------------------------------------------------------------

def test_overlay_paths_are_empty_when_the_home_does_not_exist(
    tmp_path: Path,
) -> None:
    from hypergumbo_core.catalogue_home import user_overlay_paths
    assert user_overlay_paths({"XDG_CONFIG_HOME": str(tmp_path / "nope")}) == []


def test_overlay_paths_are_sorted_and_yaml_only(tmp_path: Path) -> None:
    from hypergumbo_core.catalogue_home import user_overlay_paths
    env = {"XDG_CONFIG_HOME": str(tmp_path)}
    channel = tmp_path / "hypergumbo" / "io_primitives.d"
    channel.mkdir(parents=True)
    for name in ("b.yaml", "a.yaml", "notes.txt"):
        (channel / name).write_text("language: python\n")
    assert [p.name for p in user_overlay_paths(env)] == ["a.yaml", "b.yaml"]


def test_a_row_dropped_in_the_channel_reaches_a_run(tmp_path: Path,
                                                    monkeypatch) -> None:
    """THE INSTRUCTION HYPERGUMBO ALREADY PRINTS, MADE TRUE.

    Every run that loads a community overlay tells the reader to "override them
    in $XDG_CONFIG_HOME/hypergumbo/io_primitives.d/" (ADR-0047 ruling 6). Before
    WI-talaz nothing read that directory — the ADR-0045 ``io_primitives``
    setting is a list of paths a user NAMES, not a scan — so the sentence was
    false. This asserts the path the disclosure describes actually works."""
    from hypergumbo_core.cli import _resolve_io_overlays
    from hypergumbo_core.io_boundary import load_catalog

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    channel = tmp_path / "xdg" / "hypergumbo" / "io_primitives.d"
    channel.mkdir(parents=True)
    (channel / "mine.yaml").write_text(
        "language: python\nstatus: overlay\nprovenance: user\n"
        "net_send:\n  - module: wiretest_module\n    functions: [beam_it_out]\n"
    )

    class _A:
        io_primitives = None

    paths = _resolve_io_overlays(_A())
    assert channel / "mine.yaml" in paths

    catalog = load_catalog("python", overlay_paths=paths)
    assert any(p.qualified_name == "wiretest_module.beam_it_out"
               and p.boundary == "net_send" for p in catalog.primitives)


def test_the_scan_sits_below_an_explicitly_named_path(tmp_path: Path,
                                                      monkeypatch) -> None:
    """A path the user NAMED in config.toml is a more specific statement of
    intent than a file they dropped in a directory, so it must win on a
    qualified-name collision. Asserted as ORDER, because ``load_catalog``'s
    rule is that a later path wins."""
    from hypergumbo_core.cli import _resolve_io_overlays
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    channel = tmp_path / "xdg" / "hypergumbo" / "io_primitives.d"
    channel.mkdir(parents=True)
    (channel / "scanned.yaml").write_text("language: python\n")

    named = str(tmp_path / "named.yaml")

    class _A:
        io_primitives: ClassVar[list[str]] = [named]

    paths = _resolve_io_overlays(_A())
    assert paths.index(channel / "scanned.yaml") < paths.index(
        Path(str(tmp_path / "named.yaml")))


def test_a_foreign_language_overlay_in_the_channel_does_not_break_a_run(
    tmp_path: Path, monkeypatch,
) -> None:
    """The seeded directory holds overlays for FIVE languages at once, so the
    scan hands every one of them to every language's load. INV-lufib already
    ruled that shape — an overlay for another SHIPPED language is "not mine"
    and is skipped — so this asserts the scan leans on that rule instead of
    inventing a second filter."""
    from hypergumbo_core.catalogue_home import materialize_catalogue_home
    from hypergumbo_core.cli import _resolve_io_overlays
    from hypergumbo_core.io_boundary import load_catalog

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    materialize_catalogue_home(tmp_path / "xdg" / "hypergumbo", version="9.9.9")

    class _A:
        io_primitives = None

    paths = _resolve_io_overlays(_A())
    assert len({p.name for p in paths}) > 1, "fixture wrong: one language only"
    catalog = load_catalog("python", overlay_paths=paths)
    assert catalog.is_supported
