# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical registry of YAML catalog directories shipped under hypergumbo-core.

Hypergumbo's analysis pipeline reads several categories of YAML data alongside
the Python source: framework patterns for symbol enrichment, per-language I/O
primitive catalogs, dataflow classification rules, CFG node mappings, taint
sources / sanitizers, and per-language function summaries. Each lives in its
own subdirectory of this package, has its own loader, and is governed by its
own ADR. This module is the single index over all of them.

Why this exists. The catalog set grew organically — ``frameworks/`` landed in
the initial pattern-system work, ``io_primitives/`` landed with ADR-0016, the
``dataflow_patterns/`` + ``cfg_nodes/`` + ``taint_*`` + ``function_summaries/``
family landed across ADR-0015 / ADR-0017. With seven directories holding ~150
YAML files there is no longer a single place to enumerate them. Without a
registry, new categories drift in and out of documentation, generate-architecture
counted only ``frameworks/``, and there was no drift-detection equivalent to the
canonical-registry / drift-linter pattern used for ``Edge.edge_type`` /
``Symbol.kind`` / ``Edge.evidence_type`` (ADR-0023 / 0027 / 0028).

How it works. ``YAML_CATALOGS`` is the source of truth — one ``CatalogSpec``
per directory, naming the directory, the loader module that consumes it, the
governing ADR, and a one-line purpose. ``enumerate_catalogs()`` walks the
package root and pairs each registered spec with its actual ``*.yaml`` file
count. ``validate_registry()`` returns drift findings (registered directories
that are absent, on-disk YAML directories not yet registered); the companion
``scripts/yaml-catalog-index --check`` mode exits non-zero on any finding.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class CatalogSpec:
    """One YAML catalog directory under ``hypergumbo_core/``.

    The last two fields are REQUIRED and carry no default, which is the whole
    mechanism behind ADR-0047 ruling 7: a new catalogue family cannot be
    constructed — and therefore cannot land — without someone deciding whether
    users may extend it. The registry answers extensibility instead of the
    answer being scattered across loaders, docs and habit.

    The test ruling 10 applies is "does the family describe the USER'S world or
    the LANGUAGE'S". ``cfg_nodes`` rows are tree-sitter node types, so a user
    cannot know better than the grammar; ``url_folding`` rows name Python
    functions inside the shipped package, so a user file could only reference
    engines already present and the channel would be inert.

    Attributes:
        directory: The catalog directory name under the package root.
        purpose: One-line description of what the catalog holds.
        loader: Dotted module path of the code that consumes it.
        adr: Governing ADR, when the family has one.
        user_channel: Per-family overlay directory under
            ``$XDG_CONFIG_HOME/hypergumbo/`` (repo tier: ``<repo>/.hypergumbo/``),
            or ``None`` when the family is internal. Always ``f"{directory}.d"``
            when set — derived, never independently chosen, so the two names
            cannot drift apart.
        no_channel_reason: Why an internal family has no channel. Required
            exactly when ``user_channel`` is ``None``; supplying both is a
            contradiction and the registry gate refuses it.
        channel_scope: The YAML section the channel is limited to, when a
            family is MIXED. ``dataflow_patterns`` is the live case: its
            grammar rows are internal by the ``cfg_nodes`` reasoning while its
            ``library_patterns`` rows are regexes over call syntax that a user
            with an in-house collection type has a legitimate row to add.
            Granting the file would hand over the grammar rules too.
        channel_gated: The caveat a user-supplied entry must ride, when
            accepting one changes what the tool will CLAIM rather than only
            what it sees. ``function_summaries`` is the live case: a
            terminating user summary IS a sanitizer declaration.
    """

    directory: str
    purpose: str
    loader: str
    adr: Optional[str]
    user_channel: Optional[str]
    no_channel_reason: Optional[str]
    channel_scope: Optional[str] = None
    channel_gated: Optional[str] = None


YAML_CATALOGS: tuple[CatalogSpec, ...] = (
    CatalogSpec(
        directory="frameworks",
        purpose="Framework + convention patterns for symbol enrichment "
        "(decorators, annotations, naming conventions).",
        loader="hypergumbo_core.framework_patterns",
        adr="ADR-3aaa",
        # Conventions, including in-house ones — the user's world.
        user_channel="frameworks.d",
        no_channel_reason=None,
    ),
    CatalogSpec(
        directory="dataflow_patterns",
        purpose="Per-language dataflow access-mode classification rules.",
        loader="hypergumbo_core.dataflow",
        adr="ADR-0015",
        # MIXED: grammar rows are internal, library_patterns rows are
        # regexes over call syntax that an in-house collection type needs.
        user_channel="dataflow_patterns.d",
        no_channel_reason=None,
        channel_scope="library_patterns",
    ),
    CatalogSpec(
        directory="io_primitives",
        purpose="Per-language I/O primitive catalog (filesystem, network, "
        "subprocess, env, IPC, browser storage).",
        loader="hypergumbo_core.io_boundary",
        adr="ADR-0016",
        # Libraries and their I/O. The channel already exists.
        user_channel="io_primitives.d",
        no_channel_reason=None,
    ),
    CatalogSpec(
        directory="io_primitives_overlays",
        purpose="Community I/O primitive overlays that ship in the wheel and "
        "load by default, disclosed as unvouched (ADR-0047).",
        loader="hypergumbo_core.io_boundary",
        adr="ADR-0047",
        user_channel=None,
        no_channel_reason="These rows describe the user's world, but the "
        "user's extension point for them is the io_primitives channel, not a "
        "second one: a user entry in io_primitives.d displaces a community "
        "row on a qualified-name collision. Giving this family its own "
        "channel would create two homes for one kind of edit.",
    ),
    CatalogSpec(
        directory="cfg_nodes",
        purpose="Per-language tree-sitter node mappings for the CFG builder.",
        loader="hypergumbo_core.cfg",
        adr="ADR-0017",
        user_channel=None,
        no_channel_reason="Rows are tree-sitter node types and field names "
        "against a named grammar version. A user cannot know better than the "
        "grammar, and a wrong row silently breaks the CFG, which silently "
        "breaks the taint walk.",
    ),
    CatalogSpec(
        directory="taint_sources",
        purpose="Trust-zone source declarations for taint-flow analysis.",
        loader="hypergumbo_core.taint",
        adr="ADR-0017",
        # The user's trust model. The channel already exists.
        user_channel="taint_sources.d",
        no_channel_reason=None,
    ),
    CatalogSpec(
        directory="taint_sanitizers",
        purpose="Sanitizer declarations for taint-flow analysis.",
        loader="hypergumbo_core.taint",
        adr="ADR-0017",
        user_channel="taint_sanitizers.d",
        no_channel_reason=None,
    ),
    CatalogSpec(
        directory="function_summaries",
        purpose="Per-language function summaries (return-type and "
        "side-effect annotations consumed by language-config).",
        loader="hypergumbo_core.function_summaries",
        adr="ADR-0017",
        # Dependency behaviour — where a user knows what the tool cannot see.
        # GATED: a terminating user summary IS a sanitizer declaration, so it
        # changes what the tool CLAIMS, not only what it sees.
        user_channel="function_summaries.d",
        no_channel_reason=None,
        channel_gated="CAVEAT_USER_SUPPLIED_SANITIZER",
    ),
    CatalogSpec(
        directory="url_folding",
        purpose="Per-idiom URL-folding declarations (string interpolation, "
        "array join, ...) wiring active route-detector languages to engine "
        "functions in hypergumbo_core.url_folding.",
        loader="hypergumbo_core.url_folding",
        adr=None,
        user_channel=None,
        no_channel_reason="Rows name an engine function inside "
        "url_folding/__init__.py, so a user file could only reference engines "
        "the package already contains — the channel would be inert without "
        "also accepting user code.",
    ),
    CatalogSpec(
        directory="library_signatures",
        purpose="Per-language library signatures: the type a producing function "
        "returns, so a receiver bound to a LIBRARY call can be typed at all.",
        loader="hypergumbo_core.library_signatures",
        adr="ADR-0006",
        # ADR-0047 ruling 10's test is "does the family describe the USER'S world
        # or the LANGUAGE'S". These rows describe LIBRARIES, and an in-house
        # factory returning an in-house type is exactly the row a user has and
        # the shipped catalogue cannot.
        user_channel="library_signatures.d",
        no_channel_reason=None,
    ),
)


_PKG_ROOT = Path(__file__).parent


def _resolve_root(pkg_root: Optional[Path]) -> Path:
    return pkg_root if pkg_root is not None else _PKG_ROOT


def enumerate_catalogs(
    pkg_root: Optional[Path] = None,
) -> list[tuple[CatalogSpec, int]]:
    """Return ``(spec, file_count)`` for every registered catalog.

    ``file_count`` is the number of ``*.yaml`` files directly under the
    catalog directory. A registered directory that does not exist on disk
    is reported with ``file_count=0``; the drift is surfaced separately by
    :func:`validate_registry`.
    """
    root = _resolve_root(pkg_root)
    rows: list[tuple[CatalogSpec, int]] = []
    for spec in YAML_CATALOGS:
        directory = root / spec.directory
        count = (
            sum(1 for _ in directory.glob("*.yaml"))
            if directory.is_dir()
            else 0
        )
        rows.append((spec, count))
    return rows


def _validate_user_channels(
    catalogs: "tuple[CatalogSpec, ...]",
) -> list[str]:
    """Return findings where a family's extensibility answer is incoherent.

    ADR-0047 ruling 7: the gate "refuses a family that declares a channel it
    does not have". Being REQUIRED makes the fields answer the question; these
    checks make the answer mean something. Each is a shape that would otherwise
    ship a channel users cannot reach, or a claim of internality contradicted
    by the spec beside it.
    """
    findings: list[str] = []
    for spec in catalogs:
        d = spec.directory
        if spec.user_channel is not None and spec.no_channel_reason:
            findings.append(
                f"catalog '{d}' declares both a user channel and a "
                f"no-channel reason; exactly one is an answer"
            )
        if spec.user_channel is None and not spec.no_channel_reason:
            findings.append(
                f"catalog '{d}' declares no user channel and gives no reason "
                f"for not having one (ADR-0047 ruling 7)"
            )
        if spec.user_channel is not None and spec.user_channel != f"{d}.d":
            findings.append(
                f"catalog '{d}' user_channel is '{spec.user_channel}' but "
                f"must be '{d}.d' — the channel name is derived from the "
                f"directory so the two cannot drift"
            )
        if spec.user_channel is None:
            for field, value in (
                ("channel_scope", spec.channel_scope),
                ("channel_gated", spec.channel_gated),
            ):
                if value is not None:
                    findings.append(
                        f"catalog '{d}' sets {field}='{value}' but has no "
                        f"user channel for it to apply to"
                    )
    return findings


def validate_registry(
    pkg_root: Optional[Path] = None,
    catalogs: "Optional[tuple[CatalogSpec, ...]]" = None,
) -> list[str]:
    """Return drift findings between ``YAML_CATALOGS`` and the filesystem.

    Four failure modes are reported:

    1. A registered catalog whose directory does not exist on disk.
    2. A directory under the package root that contains one or more
       ``*.yaml`` files but is not in ``YAML_CATALOGS``.
    3. A family whose extensibility answer is missing or self-contradictory
       (ADR-0047 ruling 7) — see :func:`_validate_user_channels`.
    4. A user channel whose name does not derive from its directory.

    ``catalogs`` overrides the registry under test, so the ruling-7 checks can
    be exercised on synthetic specs without a fixture directory tree.

    Returning an empty list means the registry and filesystem agree.
    """
    root = _resolve_root(pkg_root)
    specs = catalogs if catalogs is not None else YAML_CATALOGS
    findings: list[str] = _validate_user_channels(specs)
    registered = {spec.directory for spec in specs}

    for spec in specs:
        if not (root / spec.directory).is_dir():
            findings.append(
                f"YAML_CATALOGS entry '{spec.directory}' points at a "
                f"non-existent directory"
            )

    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name in registered:
            continue
        yaml_files = list(child.glob("*.yaml"))
        if yaml_files:
            findings.append(
                f"YAML directory '{child.name}' contains "
                f"{len(yaml_files)} YAML file(s) but is not registered "
                f"in YAML_CATALOGS"
            )

    return findings
