# SPDX-License-Identifier: MPL-2.0
"""Assemble the display content for the tracker TUI item-nav modal.

The item-nav modal (WI-sulij) shows three regions for a currently-
focused tracker item: a one-line nav-state header, a Description block,
and an Activity block. All three are Rich-markup strings the modal
feeds into ``Static`` widgets.

This module is the "what does the modal show" contract. It takes the
raw Description/Activity text the caller has already formatted (via the
existing ``_format_detail_lines`` / ``_format_activity_lines`` helpers
in ``tui.py``), wraps each detected item-ID with a clickable span via
:func:`hypergumbo_tracker.hotspot_markup.render_hotspots`, and derives
the header from the navigation history using
:func:`hypergumbo_tracker.nav_history.format_nav_indicator`.

Keeping this logic in a pure function, separate from the Textual
``ModalScreen`` subclass that mounts it, means:

- The hotspot-wrapping behaviour (which panes get hotspots, what click
  action name is used, whether IDs resolve) can be unit tested without
  spinning up an ``App``.
- A future slice can reuse the same transform for the non-modal
  Description/Activity panes, so hotspot rendering is identical
  everywhere and only configured in one place.
- Extending the modal — adding per-ID badges, unread markers, etc. —
  happens in one function that has no event-loop entanglements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .hotspot_markup import render_hotspots
from .nav_history import NavigationHistory, format_nav_indicator


@dataclass(frozen=True)
class NavModalContent:
    """Pre-rendered markup strings for the three item-nav modal regions."""

    header: str
    detail: str
    activity: str


def build_nav_modal_content(
    *,
    history: NavigationHistory,
    detail_text: str,
    activity_text: str,
    pattern: re.Pattern[str],
    resolver: Callable[[str], bool],
    action: str = "jump_to_item",
    style: str = "underline",
) -> NavModalContent:
    """Package the modal's three display strings in one call.

    ``detail_text`` and ``activity_text`` are the already-formatted Rich
    markup for the Description and Activity panes (typically produced by
    ``"\\n".join(_format_detail_lines(...))`` and the corresponding
    activity helper). Both strings are run through
    :func:`render_hotspots` with the same ``resolver``, ``action``, and
    ``style`` so clickable IDs behave uniformly between panes.

    The ``header`` field carries the navigation indicator from
    :func:`format_nav_indicator` — useful for showing "[2/4] WI-foo"
    above the modal body.
    """
    header = format_nav_indicator(history)
    detail = render_hotspots(
        detail_text,
        pattern,
        resolver=resolver,
        action=action,
        style=style,
    )
    activity = render_hotspots(
        activity_text,
        pattern,
        resolver=resolver,
        action=action,
        style=style,
    )
    return NavModalContent(header=header, detail=detail, activity=activity)
