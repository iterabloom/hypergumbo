# SPDX-License-Identifier: AGPL-3.0-or-later
"""Consumer-side helper for reading hypergumbo behavior maps (WI-mokim).

Symmetric counterpart to ``safety_zones.user_out_open_json_dump_gzip``,
which is the producer for ``hypergumbo run --gzip``. The producer
writes ``.json`` or ``.json.gz`` purely on path-suffix routing; this
loader uses the same path-suffix rule rather than magic-byte sniffing
so behavior is identical to ``json.loads(path.read_text())`` for plain
files and only diverges when the caller explicitly hands over a
``.gz`` path.

Centralizes the read path so every CLI subcommand and bakeoff/analysis
script that consumes ``--input`` honors ``--gzip`` outputs without each
caller rolling its own suffix check. Prior to WI-mokim only
``scripts/bakeoff-deep`` was wired up (via its own
``_find_hg_json``/``_open_hg_json`` helpers), and feeding a ``.gz``
behavior map into any other consumer dumped gzip bytes into
``json.loads`` and exploded.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Optional, Union


def load_behavior_map(path: Union[Path, str]) -> Any:
    """Read a hypergumbo behavior map from ``path``, decompressing on demand.

    Routes on the lowercase ``.gz`` suffix to match the producer side
    (``cli.cmd_run --gzip``), which only ever writes lowercase ``.gz``.
    Returns the parsed JSON content (typically a ``dict`` for the main
    behavior map, but the type signature stays open so the helper can
    also load auxiliary JSON-shaped artifacts like compact outputs that
    share the same on-disk convention).

    Raises ``FileNotFoundError`` (from the underlying open) if the path
    is missing; the caller is responsible for the user-facing message.
    """
    p = Path(path)
    if str(p).endswith(".gz"):
        with gzip.open(p, "rt") as f:
            return json.load(f)
    with open(p) as f:
        return json.load(f)


def find_behavior_map(directory: Union[Path, str], basename: str = "hg.json") -> Optional[Path]:
    """Locate a behavior map inside ``directory`` by basename.

    Returns the path to ``<directory>/<basename>`` if it exists, falling
    back to the ``.gz`` variant. Plain JSON wins ties (mirrors the
    bakeoff-deep ``_find_hg_json`` convention and ADR-equivalent
    expectation that an uncompressed file present alongside a compressed
    one is the freshly-written copy). Returns ``None`` if neither
    exists.

    Used by bakeoff scripts and ``analyze-artifacts`` to discover
    hypergumbo outputs without each caller hardcoding the ``hg.json``
    basename twice (once plain, once ``.gz``).
    """
    d = Path(directory)
    plain = d / basename
    if plain.exists():
        return plain
    gz = d / (basename + ".gz")
    if gz.exists():
        return gz
    return None
