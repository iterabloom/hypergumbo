# SPDX-License-Identifier: MPL-2.0
"""A Textual ``Pilot.click`` that waits until the click can land (WI-mumos).

``Pilot.click(selector)`` reads the target's region, posts mouse events at
``region.offset + offset``, and returns whether the widget under that point
was the target. It does not wait for anything first, and the tracker TUI
tests ignored its return value. A modal's buttons can be in the DOM, sized,
and reporting a region while the compositor still places something else at
that point: when ``_wait_for_modal`` returns on a starved run (Textual
7.5.0's ``wait_for_idle`` returning at once, WI-ruhul's load model), a
LockScreen's buttons report row 16 while hit-testing there finds the
``#modal-dialog`` container, and the settled layout puts them on row 13. The
click lands on the container, no ``Button.Pressed`` fires, and a test then
waits 30 s for a dismissal that cannot come. That is the nightly 979a6c11
failure (``test_it_returns_the_result_under_the_one_tick_model[#submit]``).

:func:`click` waits on the CONDITION the click needs: the target is the
widget the compositor finds at the exact point Pilot will click. The layout
settles on wall-clock time (about 0.05 s in the starved probe, roughly 150
starved pauses), not on a tick count, so the wait sleeps real time with
``asyncio.sleep`` rather than ``Pilot.pause``: a test that patches ``pause``
down to one tick must not also starve this wait. Then it clicks and fails,
naming the target and what was under the point, if the click did not land.
The bound is generous because it only bounds a real defect, a target that
never becomes clickable; it never returns as though it had succeeded.
"""

from __future__ import annotations

import asyncio
from typing import Any

from textual.css.query import NoMatches
from textual.errors import NoWidget

#: Wall-clock seconds between hit-test checks.
_POLL = 0.01


def _under(pilot: Any, widget: Any, offset: tuple[int, int]) -> tuple[Any, Any, Any]:
    """``(target, region, widget under the click point)``; ``None`` where unknown."""
    app = pilot.app
    try:
        target = app.screen.query_one(widget) if isinstance(widget, str) else widget
    except NoMatches:
        return None, None, None
    region = target.region
    if not (region.width and region.height):
        return target, region, None
    try:
        under, _ = app.get_widget_at(region.x + offset[0], region.y + offset[1])
    except NoWidget:
        under = None
    return target, region, under


async def click(
    pilot: Any, widget: Any, offset: tuple[int, int] = (0, 0), timeout: float = 10.0,
    *, shift: bool = False,
) -> None:
    """Click *widget* at *offset* once it is what the compositor finds there.

    *shift* holds Shift during the click (the TUI's multi-select gesture).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    target, region, under = _under(pilot, widget, offset)
    while target is None or under is not target:
        if loop.time() > deadline:
            raise AssertionError(
                f"{widget!r} was not clickable at offset {offset} within {timeout}s: "
                f"region={region}, widget under the point={under!r}, "
                f"screen={type(pilot.app.screen).__name__}."
            )
        await asyncio.sleep(_POLL)
        target, region, under = _under(pilot, widget, offset)
    landed = await pilot.click(widget, offset=offset, shift=shift)
    if not landed:
        _, region, under = _under(pilot, widget, offset)
        raise AssertionError(
            f"click on {widget!r} at offset {offset} did not land: region={region}, "
            f"widget under the point={under!r}."
        )
