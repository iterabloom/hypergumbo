# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0047 rulings 3 and 4 — a findable home for the user's catalogue data,
created only when the user asks for it.

WHY THIS MODULE EXISTS. The registry (ADR-0047 ruling 7) already records which
catalogue families are user-extensible and what each one's overlay directory is
called. What it could not say is *where on disk that directory lives*, because
until now there was nowhere: the shipped catalogues sit inside the installed
wheel, and nobody edits files inside their site-packages. This module turns the
registry's ``user_channel`` answers into real directories under
``$XDG_CONFIG_HOME/hypergumbo/``, with the repo tier at ``<repo>/.hypergumbo/``.

TWO PROPERTIES DO THE ACTUAL WORK, and both are about what does NOT happen.

**Seed, never copy (ruling 2).** Base catalogues stay in the wheel and are never
materialized. Only *deltas* — the community overlays — are written into the
user's directory, because the loader already speaks deltas and a full copy has a
silent failure mode: the next release's rows never reach that user, so the tool
quietly degrades for exactly the people who engaged with it enough to run the
command. :func:`materialize_catalogue_home` therefore seeds from the shipped
OVERLAY directory and never from the catalogue directory, and a test pins the
two sets disjoint rather than trusting this paragraph.

**Explicit, never implicit (ruling 4).** Nothing here is called during
analysis. ADR-0045's precedent is a human-owned config file the tool may read
and must not write, and silently creating files in someone's config directory on
first invocation is the surprise that precedent exists to avoid. Default-on
loading does not need materialization either: the shipped overlays load from the
wheel, and this exists so a user can *edit* them.

IDEMPOTENCE IS A USABILITY PROPERTY, NOT A TIDINESS ONE. A command that
overwrites an edited overlay teaches people not to run it a second time, so an
existing file is never rewritten — it is reported as skipped, and the caller
says so. That also makes ``seeded_from:`` meaningful: it names the version the
file was seeded from and keeps naming it, so staleness against the shipped
source stays checkable (ruling 5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .user_config import user_config_path
from .yaml_catalogs import YAML_CATALOGS

__all__ = [
    "WIRED_CHANNELS",
    "MaterializeResult",
    "channel_directories",
    "materialize_catalogue_home",
    "repo_catalogue_home",
    "user_catalogue_home",
    "user_channel_files",
    "user_overlay_paths",
]

#: The repo tier (ruling 3). A DIRECTORY, and deliberately one character away
#: from ADR-0045's ``.hypergumbo.toml`` file — they are different things and a
#: test pins them distinct.
_REPO_DIRNAME = ".hypergumbo"

#: Where the community overlays that get seeded live inside the wheel.
_OVERLAY_PACKAGE_DIR = Path(__file__).parent / "io_primitives_overlays"

#: The family whose channel receives those overlays. Named once.
_OVERLAY_CHANNEL = "io_primitives.d"


#: Channels a loader actually consults today. NOT derived — there is no fact in
#: the registry that distinguishes "declared extensible" from "wired", and
#: inventing one would be a registry change riding in on an inventory feature.
#: A behavioural test pins the wired one and the count, so wiring another
#: (WI-sofov) fails here until this is updated rather than silently reporting a
#: capability the tool does not have.
WIRED_CHANNELS = frozenset({
    "io_primitives.d",
    "frameworks.d",          # WI-sofov
    "dataflow_patterns.d",   # WI-sofov, library_patterns section only
    "function_summaries.d",  # WI-sofov, gated by CAVEAT_USER_SUPPLIED_SANITIZER
    "library_signatures.d",  # WI-lalot
})


def user_catalogue_home(
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """``$XDG_CONFIG_HOME/hypergumbo/`` — the directory, not the config file.

    Derived from :func:`~hypergumbo_core.user_config.user_config_path` rather
    than re-deriving XDG resolution, so ``config.toml`` and the catalogue
    channels cannot drift into two different homes. One fact, one home; the
    second copy is the one that silently wins (LIVE.md).
    """
    return user_config_path(environ, home).parent


def repo_catalogue_home(repo_root: Path) -> Path:
    """``<repo>/.hypergumbo/`` — the repo tier's overlay directory.

    Note this is a DIRECTORY and ADR-0045's ``.hypergumbo.toml`` is a FILE.
    Ruling 9 keeps the repo tier opt-in and forbids hypergumbo from ever
    *writing* here; this function only names the location.
    """
    return Path(repo_root) / _REPO_DIRNAME


def channel_directories() -> "tuple[str, ...]":
    """The per-family overlay directory names, DERIVED FROM THE REGISTRY.

    Not a list maintained here. A family that gains a ``user_channel`` gains a
    directory with no edit in this module, and a family carrying
    ``no_channel_reason`` cannot acquire one by being forgotten — which is the
    failure mode a second hand-written list would have.
    """
    return tuple(sorted(s.user_channel for s in YAML_CATALOGS if s.user_channel))


def user_overlay_paths(
    environ: "Optional[Mapping[str, str]]" = None,
    home: "Optional[Path]" = None,
) -> "list[Path]":
    """Every YAML in the user's ``io_primitives.d/``, sorted, lowest first.

    WITHOUT THIS THE WHOLE HOME IS DECORATION. hypergumbo has been telling
    users to "override them in ``$XDG_CONFIG_HOME/hypergumbo/io_primitives.d/``"
    on every run that loads a community overlay (the ADR-0047 ruling 6
    disclosure) while nothing read that directory: the ADR-0045 ``io_primitives``
    setting is a list of paths a user names explicitly, not a scan. Measured
    before this was written — a row dropped in that directory changed no
    boundary — so the instruction hypergumbo prints was false.

    LANGUAGE MISMATCH IS THE LOADER'S PROBLEM AND IT ALREADY SOLVES IT. The
    seeded directory holds overlays for five languages at once, so an
    unfiltered list reaches ``load_catalog`` for every language. INV-lufib
    already ruled that shape: an overlay for another SHIPPED language is "not
    mine" and is skipped, while one naming a language no catalogue knows is a
    typo and still raises. So this returns everything and lets the one rule
    decide, rather than inventing a second filter here.

    Sorted for determinism, and returned lowest-precedence-first: a path the
    user NAMED in ``config.toml`` is more specific than a file they dropped in
    a directory, so the named one wins on a qualified-name collision.
    """
    return user_channel_files("io_primitives", environ, home)


def user_channel_files(
    family: str,
    environ: "Optional[Mapping[str, str]]" = None,
    home: "Optional[Path]" = None,
) -> "list[Path]":
    """Every YAML in the user's channel for ``family``, sorted, lowest first.

    THE ONE PLACE THAT TURNS A REGISTRY ANSWER INTO A DIRECTORY LISTING. Three
    loaders need it (io_primitives, frameworks, dataflow_patterns) and a fourth
    will; writing the join four times is how the four drift apart. The channel
    NAME is the registry's (``<family>.d``), not a string built here, so a
    family whose channel is renamed cannot leave one loader reading the old
    directory.

    Returns ``[]`` for a family with no channel, rather than raising: a caller
    asking "has the user extended this" about an internal family is asking a
    fair question with the answer "no".
    """
    channel = next((s.user_channel for s in YAML_CATALOGS
                    if s.directory == family and s.user_channel), None)
    if channel is None:
        return []
    directory = user_catalogue_home(environ, home) / channel
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.yaml"))


@dataclass(frozen=True)
class MaterializeResult:
    """What :func:`materialize_catalogue_home` did, for the caller to render.

    Frozen because the CLI renders this: a mutable result invites a caller to
    adjust the report instead of the behaviour.
    """

    home: Path
    created_dirs: "tuple[Path, ...]"
    seeded: "tuple[Path, ...]"
    skipped: "tuple[Path, ...]"
    readme: Path


def _channel_scope(channel: str) -> "Optional[str]":
    """The YAML section a channel is limited to, when it is limited.

    From the registry, because a scope stated in prose here and enforced in a
    loader there is two homes for one fact — and the half that would be wrong
    is the one a user reads before writing a file.
    """
    return next((s.channel_scope for s in YAML_CATALOGS
                 if s.user_channel == channel), None)


def _wired_listing(channels: "Sequence[str]") -> str:
    wired = [c for c in channels if c in WIRED_CHANNELS]
    lines = "\n".join(
        f"  {c}/" + (f"   (only its `{scope}` section is read)"
                     if (scope := _channel_scope(c)) else "")
        for c in wired)
    return (
        "These directories are SCANNED on every run — drop a YAML in one and\n"
        "its rows apply:\n"
        "\n"
        f"{lines}\n"
    )


def _inert_listing(channels: "Sequence[str]") -> str:
    inert = [c for c in channels if c not in WIRED_CHANNELS]
    if not inert:  # pragma: no cover - true only once every channel is wired
        return ""
    lines = "\n".join(f"  {c}/" for c in inert)
    return (
        "These are declared extensible and are NOT yet consulted by a run.\n"
        "They exist so the layout is stable, but until their loaders are\n"
        "wired a file you put in them has NO EFFECT. That is said here rather\n"
        "than left for you to discover through an overlay that silently does\n"
        "nothing:\n"
        "\n"
        f"{lines}\n"
    )


def _readme_text(channels: Sequence[str]) -> str:
    listing = "\n".join(f"  {name}/" for name in channels)
    return (
        "# hypergumbo — your catalogue data\n"
        "\n"
        "This directory is yours. hypergumbo reads it and does not rewrite it.\n"
        "\n"
        "## Layout\n"
        "\n"
        f"{listing}\n"
        "\n"
        "Each `<family>.d/` directory holds YAML overlays for one catalogue\n"
        "family. Only the families hypergumbo declares user-extensible get a\n"
        "directory here; the rest describe the language or the shipped engines\n"
        "rather than your world, so an overlay for them would be ignored.\n"
        "\n"
        "## What is read today\n"
        "\n"
        f"{_wired_listing(channels)}"
        "\n"
        f"{_inert_listing(channels)}"
        "\n"
        "## Seed, never copy\n"
        "\n"
        "The base catalogues are NOT here. They ship inside hypergumbo and are\n"
        "read from there on every run, so upgrades reach you. What lives here\n"
        "are DELTAS layered on top, at higher precedence.\n"
        "\n"
        "That is deliberate: if this directory held a full copy, the next\n"
        "release's rows would never reach you and nothing would say so.\n"
        "\n"
        "## seeded_from\n"
        "\n"
        "A file seeded here carries `seeded_from:`, naming the hypergumbo\n"
        "version it was copied from, alongside the `retrieved:` date its rows\n"
        "were last checked against upstream. Compare them against the shipped\n"
        "overlays to judge staleness. hypergumbo does not vouch for community\n"
        "rows — seeding them here does not change that, it just makes them\n"
        "yours to edit.\n"
        "\n"
        "Re-running the command never overwrites a file you already have.\n"
    )


def _seeded_text(source: Path, version: str) -> str:
    """The overlay's own bytes plus a ``seeded_from:`` stamp.

    APPENDED AS A TOP-LEVEL KEY rather than spliced beside ``retrieved:``.
    The version is rendered with :func:`json.dumps`, not ``!r`` — an early
    draft used ``!r`` plus a ``.replace("'", '\"')`` to get double quotes,
    and implicit string concatenation binds BEFORE the method call, so the
    replace ran over the whole file and turned every apostrophe in the
    source comments into a quote. It shipped mangled prose into a user's
    config home and a comment-preservation test is what caught it.
    These files carry substantial explanatory comment headers — the reason the
    rows are unvouched is written down in them — and round-tripping through a
    YAML dumper to insert one key would delete every one of those comments.
    Appending is a text operation, so the file a user opens is the file
    hypergumbo ships, plus one stamped line.
    """
    stamp = json.dumps(version)  # a JSON string IS a valid YAML double-quoted
    #                              scalar, and it escapes for us.
    body = source.read_text(encoding="utf-8").rstrip()
    return (
        f"{body}\n"
        "\n"
        "# ADR-0047 ruling 5: stamped when this overlay was materialized into\n"
        "# your config home. Compare with `retrieved:` above to judge staleness\n"
        "# against the version of hypergumbo that seeded it.\n"
        f"seeded_from: {stamp}\n"
    )


def materialize_catalogue_home(
    home: Path,
    *,
    version: str,
    overlay_dir: Optional[Path] = None,
) -> MaterializeResult:
    """Create the catalogue home and seed the community overlays into it.

    Never called during analysis (ruling 4) — only by the ``init-catalogs``
    subcommand. Idempotent: an existing file is reported in ``skipped`` and
    left exactly as the user left it.
    """
    home = Path(home)
    source_dir = _OVERLAY_PACKAGE_DIR if overlay_dir is None else Path(overlay_dir)

    created: "list[Path]" = []
    for name in ("",) + channel_directories():
        target = home / name if name else home
        if not target.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            created.append(target)

    readme = home / "README.md"
    if not readme.exists():
        readme.write_text(_readme_text(channel_directories()), encoding="utf-8")

    seeded: "list[Path]" = []
    skipped: "list[Path]" = []
    for source in sorted(source_dir.glob("*.yaml")):
        target = home / _OVERLAY_CHANNEL / source.name
        if target.exists():
            skipped.append(target)
            continue
        target.write_text(_seeded_text(source, version), encoding="utf-8")
        seeded.append(target)

    return MaterializeResult(
        home=home,
        created_dirs=tuple(created),
        seeded=tuple(seeded),
        skipped=tuple(skipped),
        readme=readme,
    )
