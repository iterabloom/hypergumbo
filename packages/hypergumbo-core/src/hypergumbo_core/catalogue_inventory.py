# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-vafit — the inventory a USER needs of what this installation knows.

``scripts/yaml-catalog-index`` already answers a question about these 156 YAML
files, but it answers the OPERATOR's: *is the registry in sync with the tree?*
It also ships to nobody — ``pyproject.toml`` packages ``src/hypergumbo_core``,
so ``scripts/`` is not in the wheel, and a user who installed hypergumbo cannot
run it at all.

THE USER'S QUESTIONS ARE DIFFERENT, and they are the reason this is a new view
rather than a port:

1. **What is loaded right now**, including the community overlays layered on
   top and anything of the user's own — not what the repository contains.
2. **Which families may I extend, and where does my file go?** The registry
   knows (ADR-0047 ruling 7); nothing surfaced it.
3. **Per language, does this installation have a catalogue at all, and what is
   its status?** Today that is disclosed only as a one-line stderr warning
   during an analysis run, which is the wrong moment and the wrong place to
   learn that a language is `in_progress`.
4. **Why should I care?** A row count is not an answer. Each family carries a
   sentence naming what goes wrong without it, because "io_primitives: 15
   files" tells a user nothing about whether to believe a verdict.

EVERYTHING IS DERIVED FROM THE REGISTRY, deliberately. The family list, the
extensibility answers, and the channel directories all come from
``YAML_CATALOGS`` — the same source ``yaml-catalog-index --check`` gates — so a
new family appears here with no edit, and cannot appear with a blank answer:
:data:`FAMILY_CONSEQUENCE` is gated by a test asserting it covers the registry
exactly.

THE LANGUAGE LIST COMES FROM THE TREE, NOT FROM ``CATALOG_LANGUAGES``. That
constant names fourteen languages while the package ships fifteen, and the
discrepancy is not hypothetical: ``bash.yaml`` sat outside it, which is how it
also sat outside the WI-sugav subprocess guard (found by WI-surun). An
inventory that inherited the same list would under-report the very installation
it is describing.

WIRED IS NOT THE SAME AS EXTENSIBLE, and saying so is the point. Five of the
six declared channels are read by nothing today: the registry declares the
family extensible, and the loader has not been taught to look (WI-sofov). An
inventory that printed "extensible: yes" for all six would be describing an
intention as though it were a capability, and a user would put a file in
``frameworks.d/`` and watch it do nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from .catalogue_home import user_catalogue_home, user_overlay_paths
from .io_boundary import default_overlays, load_catalog
from .yaml_catalogs import enumerate_catalogs

__all__ = [
    "FAMILY_CONSEQUENCE",
    "FamilyRow",
    "Inventory",
    "LanguageRow",
    "build_inventory",
]

#: Channels a loader actually consults today. NOT derived — there is no fact in
#: the registry that distinguishes "declared extensible" from "wired", and
#: inventing one would be a registry change riding in on an inventory feature.
#: A behavioural test pins the wired one and the count, so wiring another
#: (WI-sofov) fails here until this is updated rather than silently reporting a
#: capability the tool does not have.
_WIRED_CHANNELS = frozenset({"io_primitives.d"})

#: Question 4: what goes wrong without this family. A row count is not an
#: answer to "why should I care", and this is the sentence that is.
FAMILY_CONSEQUENCE: "dict[str, str]" = {
    "frameworks": (
        "Without these, a symbol is just a function: decorators, annotations "
        "and naming conventions stop being read as routes, handlers or "
        "entrypoints, so reachability starts from less than it should."
    ),
    "dataflow_patterns": (
        "Decides whether a call READS or MUTATES its receiver. Without them "
        "the taint walk cannot tell `x.append(secret)` from `len(x)`."
    ),
    "io_primitives": (
        "The rows that make a call count as I/O at all. Without them no "
        "boundary is recognised, every boundary claim goes inconclusive, and "
        "a repository looks clean because nothing was examined."
    ),
    "io_primitives_overlays": (
        "Third-party I/O that hypergumbo ships but does NOT vouch for. "
        "Without them a call to `requests.post` is invisible, because "
        "hypergumbo analyses your repository and not site-packages."
    ),
    "cfg_nodes": (
        "Maps tree-sitter node types to control flow. Without them the CFG "
        "for that language is empty, and an empty CFG silently means an "
        "empty taint walk."
    ),
    "taint_sources": (
        "Declares which values arrive untrusted. Without them a flow has no "
        "beginning and nothing is reported, however far the value travels."
    ),
    "taint_sanitizers": (
        "Declares what makes a tainted value safe. Without them real flows "
        "stay reported after they have been made harmless — noise, not risk."
    ),
    "function_summaries": (
        "Describes callees whose source is not analysed. A wrong "
        "'terminates' verdict closes a branch that is really open and DELETES "
        "a real finding, which is why a user-supplied one rides a caveat."
    ),
    "url_folding": (
        "Folds a built URL back to a route pattern. Without them an "
        "interpolated URL never matches the route it calls."
    ),
}


@dataclass(frozen=True)
class FamilyRow:
    """One catalogue family, answering questions 1, 2 and 4."""

    directory: str
    purpose: str
    adr: Optional[str]
    files: int
    extensible: bool
    channel: Optional[str]
    channel_path: Optional[Path]
    channel_scope: Optional[str]
    channel_gated: Optional[str]
    no_channel_reason: Optional[str]
    read_now: bool
    your_files: int
    consequence: str


@dataclass(frozen=True)
class LanguageRow:
    """One language's I/O primitive catalogue, answering question 3."""

    language: str
    status: str
    rows: int
    shipped_overlays: int
    unvouched_rows: int


@dataclass(frozen=True)
class Inventory:
    version: str
    home: Path
    families: "tuple[FamilyRow, ...]"
    languages: "tuple[LanguageRow, ...]"


def _catalogue_languages() -> "tuple[str, ...]":
    """Languages with a shipped I/O catalogue, FROM THE TREE.

    See the module docstring: ``CATALOG_LANGUAGES`` names fourteen and the
    package ships fifteen.
    """
    from .io_boundary import _CATALOG_DIR

    return tuple(sorted(p.stem for p in Path(_CATALOG_DIR).glob("*.yaml")))


def build_inventory(
    version: str,
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Inventory:
    """Everything the four questions need, computed against THIS installation."""
    config_home = user_catalogue_home(environ, home)
    user_paths = user_overlay_paths(environ, home)

    families: "list[FamilyRow]" = []
    for spec, count in enumerate_catalogs():
        channel = spec.user_channel
        families.append(FamilyRow(
            directory=spec.directory,
            purpose=spec.purpose,
            adr=spec.adr,
            files=count,
            extensible=channel is not None,
            channel=channel,
            channel_path=(config_home / channel) if channel else None,
            channel_scope=spec.channel_scope,
            channel_gated=spec.channel_gated,
            no_channel_reason=spec.no_channel_reason,
            read_now=channel in _WIRED_CHANNELS,
            your_files=len(user_paths) if channel == "io_primitives.d" else 0,
            consequence=FAMILY_CONSEQUENCE[spec.directory],
        ))

    languages: "list[LanguageRow]" = []
    for language in _catalogue_languages():
        catalog = load_catalog(language)
        languages.append(LanguageRow(
            language=language,
            status=catalog.status,
            rows=len(catalog.primitives),
            shipped_overlays=len(default_overlays(language)),
            unvouched_rows=sum(1 for p in catalog.primitives if p.unvouched),
        ))

    return Inventory(
        version=version,
        home=config_home,
        families=tuple(families),
        languages=tuple(languages),
    )
