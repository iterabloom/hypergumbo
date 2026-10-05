# SPDX-License-Identifier: MPL-2.0
"""Mean priority of the open items, day by day, replayed from the op logs.

Compiled state answers only "what is true now", so the history is rebuilt from
the raw op logs. For each item, every UTC calendar day on which it has an op is a
candidate change point. The item's state at the end of that day is the ops whose
``at`` falls on or before the day, folded by ``compile_ops``. That is the same
Lamport-ordered LWW fold every other command uses, so the replay cannot disagree
with ``show`` about what an item was. Two consequences follow:

* **Wall-clock cut, clock-ordered fold.** An op belongs to the day of its ``at``
  timestamp; within that prefix, ops are ordered by Lamport clock as usual. An
  op whose ``at`` is missing or unreadable cannot be placed in time, so it is
  applied from the item's first day. An item none of whose ops has a readable
  ``at`` has no history and is left out.
* **No create, no item.** Across branches an update's timestamp can precede
  every create op of its item. A prefix without a create op does not compile,
  and the item does not exist yet on that day.

An item is *open* on a day when its status at the end of that day is not one
of the caller's resolved statuses (the CLI passes the config's
``resolved_statuses``, the same set ``ready`` and ``clusters`` use). Every
item starts out not open, so an item created already resolved contributes
nothing until it is reopened. A later ``deleted`` status removes an item from
the open set on that day only. On the days before, while it was open, it
counted, because it was an open ticket then.

The series has one point for every calendar day from the first change point to
the last one (or the ``since`` / ``until`` window). Quiet days carry the state
forward, so the x axis is uniform time rather than "days something happened".
Replaying compiles each item once per active day, which is quadratic in one
item's op count. On this repository's tracker (2663 items, 14691 ops, at most
91 per item) the whole replay takes 0.7 s.

The series is also available as JSON or CSV (``render_csv``); both report the
mean to four decimals (``rounded_mean``).

The plot is a hand-written SVG. The tracker prefers the standard library and
has no plotting dependency, and a scatter with axes needs only circles, lines
and text. Each point carries a ``<title>`` that shows its date, mean and
open-item count on hover.
"""

from __future__ import annotations

import csv
import datetime
import html
import io
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hypergumbo_tracker.store import CorruptFileError, compile_ops

DEFAULT_OUT = "tracker-priority-trend.svg"
DEFAULT_TITLE = "Mean priority of open tracker items"


@dataclass(frozen=True)
class TrendPoint:
    """The open items at the end of one UTC day: how many, and their mean priority.

    ``mean_priority`` is None when nothing was open that day.
    """

    day: datetime.date
    open_count: int
    mean_priority: float | None


def rounded_mean(point: TrendPoint) -> float | None:
    """The point's mean priority to four decimals, as the JSON and CSV outputs report it."""
    return None if point.mean_priority is None else round(point.mean_priority, 4)


def render_csv(points: Sequence[TrendPoint]) -> str:
    """The series as CSV: ``day,open,mean_priority``; the mean is empty when nothing was open."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["day", "open", "mean_priority"])
    for p in points:
        mean = rounded_mean(p)
        writer.writerow([p.day.isoformat(), p.open_count, "" if mean is None else mean])
    return buf.getvalue()


def _op_day(op: dict[str, Any]) -> datetime.date | None:
    """The UTC calendar day of an op's ``at``, or None when it has no readable one."""
    at = op.get("at")
    if not at:
        return None
    try:
        return datetime.date.fromisoformat(str(at)[:10])
    except ValueError:
        return None


def item_timeline(
    ops: Sequence[dict[str, Any]], item_id: str, resolved_statuses: Iterable[str],
) -> list[tuple[datetime.date, int | None]]:
    """One item's change points: ``(day, priority)`` while open, ``(day, None)`` once not.

    Only days on which the end-of-day state differs from the previous one are
    listed; the item starts out not open.
    """
    resolved = frozenset(resolved_statuses)
    dated = [(_op_day(op), op) for op in ops]
    days = sorted({d for d, _ in dated if d is not None})
    out: list[tuple[datetime.date, int | None]] = []
    previous: int | None = None
    for day in days:
        prefix = [op for d, op in dated if d is None or d <= day]
        state: int | None
        try:
            item = compile_ops(prefix, item_id)
        except CorruptFileError:
            state = None
        else:
            state = None if item.status in resolved else item.priority
        if state != previous:
            out.append((day, state))
            previous = state
    return out


def priority_trend(
    ops_by_item: Mapping[str, Sequence[dict[str, Any]]],
    resolved_statuses: Iterable[str],
    *,
    since: datetime.date | None = None,
    until: datetime.date | None = None,
) -> list[TrendPoint]:
    """One point per calendar day: the open items at the end of it and their mean priority.

    The window defaults to the first and last change point over all items;
    state from before ``since`` still carries into it.
    """
    resolved = frozenset(resolved_statuses)
    events = sorted(
        (
            (day, item_id, state)
            for item_id, ops in ops_by_item.items()
            for day, state in item_timeline(ops, item_id, resolved)
        ),
        key=lambda e: (e[0], e[1]),
    )
    if not events:
        return []
    day = since or events[0][0]
    last = until or events[-1][0]
    open_now: dict[str, int] = {}
    points: list[TrendPoint] = []
    i = 0
    while day <= last:
        while i < len(events) and events[i][0] <= day:
            _, item_id, state = events[i]
            if state is None:
                open_now.pop(item_id, None)
            else:
                open_now[item_id] = state
            i += 1
        n = len(open_now)
        points.append(TrendPoint(day, n, sum(open_now.values()) / n if n else None))
        day += datetime.timedelta(days=1)
    return points


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

_W, _H = 960, 440
_LEFT, _RIGHT, _TOP, _BOTTOM = 72, 48, 52, 64
_INK, _GRID, _DOT = "#222222", "#dddddd", "#2a6fdb"


def _y_range(values: Sequence[float]) -> tuple[float, float]:
    """The data range rounded out to quarter steps and kept inside priority's 0-4."""
    lo, hi = math.floor(min(values) * 4) / 4, math.ceil(max(values) * 4) / 4
    if lo == hi:
        lo, hi = lo - 0.25, hi + 0.25
    return max(lo, 0.0), min(hi, 4.0)


def _y_ticks(lo: float, hi: float) -> list[float]:
    """Gridline values: multiples of a round step (0.25, 0.5 or 1) between ``lo`` and ``hi``."""
    step = 0.25 if hi - lo <= 1.5 else 0.5 if hi - lo <= 3 else 1.0
    first = math.ceil(lo / step) * step
    return [first + k * step for k in range(round((hi - first) / step) + 1)]


def render_svg(points: Sequence[TrendPoint], *, title: str = DEFAULT_TITLE) -> str:
    """A scatterplot of ``points``: day on x, mean priority of the open items on y."""
    pw, ph = _W - _LEFT - _RIGHT, _H - _TOP - _BOTTOM
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{_H}" '
        f'viewBox="0 0 {_W} {_H}" font-family="sans-serif" font-size="12">',
        f'<rect width="{_W}" height="{_H}" fill="#ffffff"/>',
        f'<text x="{_W / 2}" y="26" text-anchor="middle" font-size="16" fill="{_INK}">{html.escape(title, quote=False)}</text>',
    ]
    plotted = [(p, m) for p in points if (m := p.mean_priority) is not None]
    if not plotted:
        parts.append(f'<text x="{_W / 2}" y="{_H / 2}" text-anchor="middle" fill="{_INK}">'
                     "no open items in this window</text>")
        parts.append("</svg>")
        return "\n".join(parts) + "\n"

    first, last = points[0].day, points[-1].day
    span = (last - first).days
    lo, hi = _y_range([m for _, m in plotted])

    def x(day: datetime.date) -> float:
        return _LEFT + ((day - first).days / span * pw if span else pw / 2)

    def y(value: float) -> float:
        return _TOP + ph - (value - lo) / (hi - lo) * ph

    for v in _y_ticks(lo, hi):
        parts.append(f'<line x1="{_LEFT}" y1="{y(v):.1f}" x2="{_LEFT + pw}" y2="{y(v):.1f}" stroke="{_GRID}"/>')
        parts.append(f'<text x="{_LEFT - 8}" y="{y(v) + 4:.1f}" text-anchor="end" fill="{_INK}">{v:.2f}</text>')
    n_ticks = min(6, span + 1)
    tick_days = sorted({first + datetime.timedelta(days=round(k * span / (n_ticks - 1))) for k in range(n_ticks)}
                       if n_ticks > 1 else {first})
    for d in tick_days:
        parts.append(f'<line x1="{x(d):.1f}" y1="{_TOP + ph}" x2="{x(d):.1f}" y2="{_TOP + ph + 5}" stroke="{_INK}"/>')
        parts.append(f'<text x="{x(d):.1f}" y="{_TOP + ph + 20}" text-anchor="middle" fill="{_INK}">{d.isoformat()}</text>')
    parts.append(f'<rect x="{_LEFT}" y="{_TOP}" width="{pw}" height="{ph}" fill="none" stroke="{_INK}"/>')
    parts.append(f'<text x="{_LEFT + pw / 2}" y="{_H - 14}" text-anchor="middle" fill="{_INK}">'
                 "day (UTC; state at the end of the day)</text>")
    parts.append(f'<text x="18" y="{_TOP + ph / 2}" text-anchor="middle" fill="{_INK}" '
                 f'transform="rotate(-90 18 {_TOP + ph / 2})">mean priority of open items (0 = most urgent)</text>')
    for p, m in plotted:
        parts.append(
            f'<circle cx="{x(p.day):.1f}" cy="{y(m):.1f}" r="2.5" fill="{_DOT}">'
            f"<title>{p.day.isoformat()}: mean {m:.2f} over {p.open_count} open</title></circle>"
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"
