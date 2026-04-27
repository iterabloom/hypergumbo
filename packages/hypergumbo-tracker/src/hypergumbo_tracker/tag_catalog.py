# SPDX-License-Identifier: MPL-2.0
"""Tag catalog: persistent metadata for tracker tags (WI-lifal).

Why this module exists
----------------------
Item tags are first-class filter values (`tracker list --tag <name>`), but
the tracker had no way to *enumerate* the tags currently in use, no place to
hang descriptions on them, and no lifecycle vocabulary for retiring drifted
near-duplicates (e.g. ``dx`` vs ``developer_experience``). Without a catalog,
the agent's spot-check step in
``structural-fix-scope-expansion-protocol.md §"When NOT to file a new tracker item"``
relies on guessing tag names from memory. This module is the storage layer
that backs the ``tracker tags`` subcommand and gives every catalogued tag a
description, four timestamps, and an explicit deprecation flag.

The three-state status model
----------------------------
A tag has an external ``status`` of exactly one of ``active`` / ``inactive``
/ ``deprecated``, computed at read time from two underlying values:

    +----------------------+----------+--------------+
    | stored: deprecated   | count    | status       |
    +======================+==========+==============+
    | False                | > 0      | active       |
    | False                | == 0     | inactive     |
    | True                 | (any)    | deprecated   |
    +----------------------+----------+--------------+

``active`` means the tag has current uses and no opinion is recorded.
``inactive`` means no current uses and no explicit retirement decision —
re-using the tag is fine, no warning fires.
``deprecated`` means an explicit retirement decision was made; new uses
should warn (and surface ``in_favor_of`` when set), independent of count, so
a tag deprecated *while* still affixed to live items keeps firing the
warning on new uses while existing usages stay untouched until cleanup.

Why ``status`` is derived rather than stored: the alternative — storing
``status`` as a single field on the catalog entry — requires every code
path that mutates a tag's count (``add``, ``update`` with ``--add-tag`` /
``--remove-tag``, ``rename``) to also flip the status field, and the bug
surface scales with the number of such paths. Deriving from
``(count, deprecated)`` collapses that surface to zero: the only stored
flag is the editorial one (``deprecated``), and the observable count comes
from the same compiled-tier index that every other read path already uses.

Why this name (``deprecated``) and not a new word: the tracker's
``config.yaml`` already uses ``deprecated_statuses`` for per-kind status
retirement (see ``validation.py`` and the AGENTS.md note "Do NOT use
``holding`` — it is deprecated"). Reusing the same vocabulary is consistent
with existing terminology rather than introducing a new word; the
``holding`` precedent also established that deprecated values stay
*readable* and writes during a migration window emit warnings rather than
hard failures. Tag deprecation follows the same rule.

Schema invariants (enforced at load and save time)
--------------------------------------------------
* Every key in ``tags:`` matches ``^[a-z_][a-z0-9_]*$`` (the existing tag
  name shape; rejecting other shapes prevents the catalog from drifting
  into a state where it documents a tag that ``tracker add --tag`` will
  reject).
* ``created_on``, ``last_modified``, ``last_used`` are RFC3339-Z UTC
  strings (``YYYY-MM-DDTHH:MM:SSZ``) or ``None``. ``last_used`` may be
  ``None`` only when the tag has never been added to or removed from any
  item — an edge case that arises when ``tags describe`` or
  ``tags deprecate`` is invoked before the tag is ever applied.
* ``in_favor_of`` is only meaningful when ``deprecated`` is true; it is
  ignored on read when ``deprecated`` is false (and not written back in
  that case to avoid stale-redirect entries).
* ``description`` is a single-line string; multi-line descriptions are
  rejected at save time so the per-line ``tracker tags --count`` output
  never gets clipped by an embedded newline.

Op-log backfill
---------------
The first ``tracker tags`` invocation after this module lands walks every
item's op log and populates ``created_on`` and ``last_used`` for every tag
currently in use. Once the catalog file exists, subsequent invocations
skip the backfill — migration cost is paid once. The backfill is also
trigger-able explicitly via ``ensure_catalog(..., force_backfill=True)``
for tests.

Failure mode this guards against
--------------------------------
If the maintenance hooks in ``add`` / ``update`` paths *don't* update
``last_used`` whenever a tag is added to or removed from an item, the
catalog drifts from reality silently — every catalogued tag's
``last_used`` slowly ages out and the inactive-vs-active classification
becomes meaningless. The hooks live in the CLI layer
(``cli._cmd_add`` / ``cli._cmd_update``) where the tag-touching surface is
already concentrated; they call ``touch_tags(...)`` from this module on
every successful mutation that adds or removes any tag.

References
----------
- WI-lifal-tadah-nabup-soriv-sofan-tomiz-sagor-jimaz (locked design
  2026-04-27).
- ``packages/hypergumbo-tracker/src/hypergumbo_tracker/cli.py`` —
  ``_cmd_tags`` and the ``add`` / ``update`` hook sites.
- ``.agent/tracker/tag_catalog.yaml`` — production catalog file
  (sibling of ``config.yaml``).
"""
from __future__ import annotations

import datetime
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

# RFC3339-Z UTC timestamp used everywhere in the tracker, matching the
# format Store.add / Store.update write.
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

# Same shape the tracker enforces for tags elsewhere (kebab/snake-style
# identifiers); rejected at save time so the catalog can't document a tag
# the rest of the tracker will reject.
_TAG_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

CATALOG_BASENAME = "tag_catalog.yaml"


def now_utc() -> str:
    """Return the current UTC time in RFC3339-Z form.

    Wrapped here so tests can monkeypatch a single symbol when they need
    deterministic timestamps; see ``test_tag_catalog.py``.
    """
    return datetime.datetime.now(datetime.timezone.utc).strftime(_TS_FMT)


@dataclass
class TagCatalogEntry:
    """One catalog row.

    All fields except ``description`` are catalog-controlled; ``description``
    is the only field set by the user via ``tracker tags describe``. The
    ``created_on`` / ``last_modified`` / ``last_used`` semantics are spelled
    out in the module docstring.
    """

    description: str = ""
    created_on: str | None = None
    last_modified: str | None = None
    last_used: str | None = None
    deprecated: bool = False
    in_favor_of: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for YAML write.

        Drops ``in_favor_of`` when ``deprecated`` is False, so an
        accidentally-set redirect on a non-deprecated tag never round-trips
        back into the catalog.
        """
        d: dict[str, Any] = {
            "description": self.description,
            "created_on": self.created_on,
            "last_modified": self.last_modified,
            "last_used": self.last_used,
            "deprecated": bool(self.deprecated),
        }
        if self.deprecated and self.in_favor_of:
            d["in_favor_of"] = self.in_favor_of
        else:
            # Always include the key for readability, but null when
            # not-deprecated to make catalog files diff-friendly.
            d["in_favor_of"] = None
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TagCatalogEntry:
        """Parse one entry from YAML.

        Permissive on read — unknown keys are ignored so future schema
        additions can be deployed in any order across machines without
        breaking older readers.
        """
        return cls(
            description=str(raw.get("description") or ""),
            created_on=raw.get("created_on") or None,
            last_modified=raw.get("last_modified") or None,
            last_used=raw.get("last_used") or None,
            deprecated=bool(raw.get("deprecated") or False),
            in_favor_of=raw.get("in_favor_of") or None,
        )


def tag_status(count: int, deprecated: bool) -> str:
    """Return the external status string for a (count, deprecated) pair.

    Implements the three-state model documented at module top. ``deprecated``
    wins over count, intentionally — a deprecated tag that's still affixed
    to items must surface as ``deprecated`` so the warning hook fires on
    further new uses.
    """
    if deprecated:
        return "deprecated"
    if count > 0:
        return "active"
    return "inactive"


# ---------------------------------------------------------------------------
# Catalog file IO
# ---------------------------------------------------------------------------


def catalog_path(tracker_root: Path) -> Path:
    """Resolve the catalog file path for a tracker root.

    The catalog lives alongside ``config.yaml`` under ``<tracker_root>/tracker/``.
    We treat the canonical tracker dir as the catalog's home so multi-tier
    deployments share one catalog, the same way they share one config.
    """
    return tracker_root / "tracker" / CATALOG_BASENAME


def load_catalog(catalog_file: Path) -> dict[str, TagCatalogEntry]:
    """Load the catalog from disk.

    Returns an empty dict when the file is missing or empty — callers
    distinguish "absent" from "empty" via :func:`catalog_exists` rather
    than via the return type.
    """
    if not catalog_file.exists():
        return {}
    try:
        with open(catalog_file) as f:
            raw = yaml.safe_load(f.read())
    except yaml.YAMLError as e:
        raise ValueError(f"{catalog_file}: invalid YAML: {e}") from e
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"{catalog_file}: expected mapping at top level, got "
            f"{type(raw).__name__}",
        )
    tags_raw = raw.get("tags") or {}
    if not isinstance(tags_raw, dict):
        raise ValueError(
            f"{catalog_file}: expected 'tags' to be a mapping, got "
            f"{type(tags_raw).__name__}",
        )
    out: dict[str, TagCatalogEntry] = {}
    for name, entry_raw in tags_raw.items():
        if not isinstance(name, str) or not _TAG_NAME_RE.match(name):
            raise ValueError(
                f"{catalog_file}: tag name {name!r} does not match "
                f"{_TAG_NAME_RE.pattern}",
            )
        if not isinstance(entry_raw, dict):
            raise ValueError(
                f"{catalog_file}: entry for tag '{name}' must be a mapping",
            )
        out[name] = TagCatalogEntry.from_dict(entry_raw)
    return out


def catalog_exists(tracker_root: Path) -> bool:
    """Return True if the catalog file is present on disk.

    Distinct from ``load_catalog`` returning ``{}``; an empty file is
    "present but empty" and skips the op-log backfill, while a missing
    file triggers the one-time backfill on first read.
    """
    return catalog_path(tracker_root).exists()


def save_catalog(
    catalog_file: Path,
    catalog: dict[str, TagCatalogEntry],
) -> None:
    """Atomically write the catalog file.

    Uses same-directory mkstemp + os.rename so concurrent readers see
    either the old contents or the new contents — never a partial write.
    Validates entries on the way out so a malformed catalog never lands
    on disk; this is the second line of defense after callers, since the
    catalog is hand-editable in principle.
    """
    catalog_file.parent.mkdir(parents=True, exist_ok=True)

    # Validate entries before serializing.
    for name, entry in catalog.items():
        if not _TAG_NAME_RE.match(name):
            raise ValueError(
                f"tag name {name!r} does not match {_TAG_NAME_RE.pattern}",
            )
        if "\n" in entry.description:
            raise ValueError(
                f"tag '{name}': description must be single-line "
                f"(got embedded newline)",
            )
        if entry.in_favor_of is not None and not _TAG_NAME_RE.match(entry.in_favor_of):
            raise ValueError(
                f"tag '{name}': in_favor_of {entry.in_favor_of!r} does not "
                f"match {_TAG_NAME_RE.pattern}",
            )

    payload = {
        "tags": {name: entry.to_dict() for name, entry in sorted(catalog.items())},
    }

    fd, tmp_str = tempfile.mkstemp(
        suffix=".yaml",
        prefix=".tag_catalog_",
        dir=str(catalog_file.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(payload, f, sort_keys=False, default_flow_style=False)
        os.rename(tmp_str, catalog_file)
    except Exception:
        Path(tmp_str).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Counting and status from a TrackerSet view
# ---------------------------------------------------------------------------


# Tiers enumerated by default (mirrors the design's "canonical union
# workspace" rule; stealth is excluded by default because stealth-only
# tags are an explicit design choice not to surface).
DEFAULT_ENUM_TIERS = ("canonical", "workspace")


def count_tags(items: Iterable[Any]) -> dict[str, int]:
    """Count tag uses across a list of compiled items.

    Pure helper; takes anything with a ``.tags`` attribute so tests can
    pass a list of stub objects without constructing real CompiledItems.
    """
    counts: dict[str, int] = {}
    for item in items:
        for t in getattr(item, "tags", []) or []:
            counts[t] = counts.get(t, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Op-log backfill
# ---------------------------------------------------------------------------


def _walk_op_files(tracker_root: Path) -> list[Path]:
    """Return op file paths from canonical and workspace tiers.

    Stealth is excluded — it matches the default enumeration tier set.
    """
    paths: list[Path] = []
    for sub in ("tracker/.ops", "tracker-workspace/.ops"):
        d = tracker_root / sub
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.name.startswith(".") and f.name.endswith(".ops") and f.is_file():
                paths.append(f)
    return paths


def _ts_min(a: str | None, b: str | None) -> str | None:
    """RFC3339-Z strings are lexicographically orderable; this is just min."""
    if a is None:
        return b
    if b is None:
        return a
    return a if a < b else b


def _ts_max(a: str | None, b: str | None) -> str | None:
    """RFC3339-Z strings are lexicographically orderable; this is just max."""
    if a is None:
        return b
    if b is None:
        return a
    return a if a > b else b


def backfill_from_op_log(tracker_root: Path) -> dict[str, dict[str, str]]:
    """Walk every item's op log and derive ``created_on`` / ``last_used`` per tag.

    The trigger condition for this pass is "the catalog file does not exist
    yet" (see :func:`ensure_catalog`). We're not reconstructing tag ops
    perfectly — that would require a separate normalized form on the op-log
    side — but we are extracting:

      * ``created_on``  — the earliest timestamp at which any item carried
        this tag, judged by scanning every ``create`` op's ``data.tags``
        list and every ``update`` op's ``add.tags`` list.
      * ``last_used``   — the latest timestamp at which any item gained
        OR lost this tag, judged by the same two op shapes plus
        ``update.remove.tags``.

    The op file is parsed directly (no compile pass) because we need the
    per-op timestamps, not the compiled net state. Failure to parse any
    one file is non-fatal — that file is skipped — because corrupt files
    are someone else's problem and the backfill has to converge.
    """
    from hypergumbo_tracker.store import _parse_ops_file

    # Per-tag aggregator: {created_on: min, last_used: max}
    per_tag: dict[str, dict[str, str | None]] = {}

    for op_file in _walk_op_files(tracker_root):
        try:
            ops = _parse_ops_file(op_file)
        except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
            # Corrupt-file robustness: parser's own tests cover the
            # parse-failure case; here we just want the backfill loop
            # to survive whichever way it manifests in the wild. We do
            # not log because the parser already raises CorruptFileError
            # to the caller of any non-backfill read path; the backfill
            # is best-effort by design.
            continue
        for op in ops:
            at = op.get("at")
            if not at:
                continue
            op_type = op.get("op")
            tags_touched: list[str] = []
            if op_type == "create":
                data = op.get("data") or {}
                for t in (data.get("tags") or []):
                    tags_touched.append(t)
                for t in tags_touched:
                    bucket = per_tag.setdefault(
                        t, {"created_on": None, "last_used": None},
                    )
                    bucket["created_on"] = _ts_min(bucket["created_on"], at)
                    bucket["last_used"] = _ts_max(bucket["last_used"], at)
            elif op_type == "update":
                add_tags = ((op.get("add") or {}).get("tags") or [])
                rem_tags = ((op.get("remove") or {}).get("tags") or [])
                # `set` on tags wholesale-replaces; treat each element as
                # if it were added at this op's timestamp for backfill
                # purposes (we can't recover what was there before without
                # the full compile pass).
                set_tags = ((op.get("set") or {}).get("tags") or [])
                for t in list(add_tags) + list(set_tags):
                    bucket = per_tag.setdefault(
                        t, {"created_on": None, "last_used": None},
                    )
                    bucket["created_on"] = _ts_min(bucket["created_on"], at)
                    bucket["last_used"] = _ts_max(bucket["last_used"], at)
                for t in rem_tags:
                    bucket = per_tag.setdefault(
                        t, {"created_on": None, "last_used": None},
                    )
                    # Removal counts as use (last_used) but never as
                    # creation.
                    bucket["last_used"] = _ts_max(bucket["last_used"], at)

    out: dict[str, dict[str, str]] = {}
    for name, bucket in per_tag.items():
        out[name] = {
            k: v for k, v in bucket.items() if v is not None  # type: ignore[misc]
        }
    return out


def ensure_catalog(
    tracker_root: Path,
    *,
    force_backfill: bool = False,
) -> dict[str, TagCatalogEntry]:
    """Load the catalog, performing one-time backfill if it doesn't exist.

    On first call after this module ships (or whenever the catalog file is
    deleted), walks the op log to populate ``created_on`` and ``last_used``
    for every tag currently in use. Writes the catalog file. Subsequent
    calls find the file present and skip the walk — migration cost paid
    once.

    ``force_backfill=True`` is a test affordance: it merges fresh op-log
    data into an existing catalog without losing user-provided fields like
    descriptions or deprecation flags.
    """
    cat_file = catalog_path(tracker_root)
    existing = load_catalog(cat_file)

    if cat_file.exists() and not force_backfill:
        return existing

    backfill = backfill_from_op_log(tracker_root)

    for name, ts_pair in backfill.items():
        if name in existing:
            # Preserve description, deprecated, in_favor_of, last_modified
            # — these are user editorial state. Update only the
            # observed-from-op-log fields, and only in the safe direction
            # (earliest created, latest last_used).
            entry = existing[name]
            entry.created_on = _ts_min(entry.created_on, ts_pair.get("created_on"))
            entry.last_used = _ts_max(entry.last_used, ts_pair.get("last_used"))
        else:
            existing[name] = TagCatalogEntry(
                created_on=ts_pair.get("created_on"),
                last_used=ts_pair.get("last_used"),
            )

    save_catalog(cat_file, existing)
    return existing


# ---------------------------------------------------------------------------
# Maintenance helpers used by the CLI add/update hooks
# ---------------------------------------------------------------------------


def touch_tags(
    tracker_root: Path,
    tags: Iterable[str],
    *,
    when: str | None = None,
) -> None:
    """Record tag use by updating ``last_used`` (and ``created_on`` on first sight).

    Called from the CLI ``add`` / ``update`` hook sites whenever an item
    operation adds or removes any tag. ``when`` defaults to "now" but
    accepts an override so tests can pin timestamps deterministically.

    This is the failure-mode-guarding hook documented in the module
    docstring: if it doesn't fire on every mutation that touches a tag
    list, the catalog drifts from reality and the inactive-vs-active
    classification becomes meaningless.
    """
    tags_list = [t for t in tags if t]
    if not tags_list:
        return
    when = when or now_utc()
    cat_file = catalog_path(tracker_root)
    catalog = load_catalog(cat_file)
    changed = False
    for t in tags_list:
        if t not in catalog:
            catalog[t] = TagCatalogEntry(created_on=when, last_used=when)
            changed = True
            continue
        entry = catalog[t]
        # `created_on` is set only on first observation; never overwrite a
        # backfilled (earlier) value with a forward-moving timestamp.
        if entry.created_on is None:
            entry.created_on = when
            changed = True
        new_last = _ts_max(entry.last_used, when)
        if new_last != entry.last_used:
            entry.last_used = new_last
            changed = True
    if changed:
        save_catalog(cat_file, catalog)


def bump_last_modified(
    tracker_root: Path,
    tags: Iterable[str],
    *,
    when: str | None = None,
) -> None:
    """Record an editorial change (rename / describe / deprecate) on a tag.

    Distinct from ``touch_tags``: this updates ``last_modified`` rather
    than ``last_used``, because editorial state changes are user actions
    on the tag itself, not item operations.
    """
    tags_list = [t for t in tags if t]
    if not tags_list:
        return
    when = when or now_utc()
    cat_file = catalog_path(tracker_root)
    catalog = load_catalog(cat_file)
    changed = False
    for t in tags_list:
        entry = catalog.setdefault(t, TagCatalogEntry(created_on=when))
        if entry.last_modified != when:
            entry.last_modified = when
            changed = True
    if changed:
        save_catalog(cat_file, catalog)


def get_entry(
    tracker_root: Path,
    name: str,
) -> TagCatalogEntry | None:
    """Return one catalog entry, or None if the tag has no catalog row.

    Read-only convenience for the CLI deprecation-warning hook so it
    doesn't need to load the whole catalog when checking one tag.
    """
    catalog = load_catalog(catalog_path(tracker_root))
    return catalog.get(name)


def upsert_entry(
    tracker_root: Path,
    name: str,
    entry: TagCatalogEntry,
) -> None:
    """Write one entry to the catalog, creating the file if needed.

    Used by ``tags describe`` and ``tags deprecate``. Atomic via
    ``save_catalog``.
    """
    if not _TAG_NAME_RE.match(name):
        raise ValueError(
            f"tag name {name!r} does not match {_TAG_NAME_RE.pattern}",
        )
    cat_file = catalog_path(tracker_root)
    catalog = load_catalog(cat_file)
    catalog[name] = entry
    save_catalog(cat_file, catalog)


# Re-export common shapes for callers (avoids repetitive imports)
__all__ = [
    "CATALOG_BASENAME",
    "DEFAULT_ENUM_TIERS",
    "TagCatalogEntry",
    "backfill_from_op_log",
    "bump_last_modified",
    "catalog_exists",
    "catalog_path",
    "count_tags",
    "ensure_catalog",
    "get_entry",
    "load_catalog",
    "now_utc",
    "save_catalog",
    "tag_status",
    "touch_tags",
    "upsert_entry",
]


# Coverage exclusion is unnecessary here — ``asdict`` and ``field`` are
# imported for downstream callers that may want to reflect on entries.
_ = asdict
_ = field
