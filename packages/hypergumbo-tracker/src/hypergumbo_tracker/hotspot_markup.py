# SPDX-License-Identifier: MPL-2.0
"""Wrap detected item IDs in Textual ``[@click=...]`` action markup.

The tracker TUI's Description and Activity panes are Rich-markup strings
fed into ``Static.update``. To make item-ID substrings clickable
(WI-sulij), the renderer pre-pass replaces each detected ID with a
click-action span. Textual's ``[@click=action_name('arg')]text[/]``
markup fires the action method when the wrapped span is clicked,
matching the design constraint that hotspots open the item-nav modal.

This module is a pure text transform:

- It uses :func:`~hypergumbo_tracker.id_matching.find_item_ids` to locate
  the IDs (so the word-boundary and CVCVC rules live in one place).
- A caller-supplied ``resolver(item_id) -> bool`` decides whether the
  match is a real item; an unresolved ID renders as plain text so the
  user does not see dead hotspots (key constraint 2 in WI-sulij).
- A ``style`` kwarg (default ``"underline"``) applies an optional visual
  hint so clickable IDs are visually distinct from surrounding prose.
  Set ``style=""`` to suppress styling when the surrounding context
  already provides one.
- A ``skip_ranges`` kwarg is forwarded to ``find_item_ids`` so regions
  that should not become hotspots (existing link markup, code blocks)
  can be excluded.

No Textual imports — the output is a plain string, so the transform can
be unit tested without spinning up an App instance.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable

from .id_matching import find_item_ids


def render_hotspots(
    text: str,
    pattern: re.Pattern[str],
    *,
    resolver: Callable[[str], bool],
    action: str = "jump_to_item",
    style: str = "underline",
    skip_ranges: Iterable[tuple[int, int]] = (),
) -> str:
    """Return *text* with each resolvable item ID wrapped in a click span.

    The output is a Rich-markup string suitable for feeding into a
    Textual ``Static`` widget. Each detected ID for which
    ``resolver(item_id)`` returns ``True`` is replaced with::

        [@click=<action>('<item_id>')][<style>]<item_id>[/<style>][/]

    The inner ``[<style>]...[/<style>]`` wrapper is omitted when
    ``style`` is an empty string. IDs for which the resolver returns
    ``False`` are left as plain text — a missing target should not
    render as a dead hotspot.

    ``skip_ranges`` is passed straight through to
    :func:`find_item_ids` so the caller can exclude regions that are
    already linked or rendered differently.
    """
    matches = find_item_ids(text, pattern, skip_ranges=skip_ranges)
    if not matches:
        return text
    parts: list[str] = []
    cursor = 0
    for m in matches:
        parts.append(text[cursor : m.start])
        if resolver(m.item_id):
            inner = (
                f"[{style}]{m.item_id}[/{style}]" if style else m.item_id
            )
            parts.append(f"[@click={action}('{m.item_id}')]{inner}[/]")
        else:
            parts.append(m.item_id)
        cursor = m.end
    parts.append(text[cursor:])
    return "".join(parts)
