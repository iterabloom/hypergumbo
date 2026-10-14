# SPDX-License-Identifier: AGPL-3.0-or-later
"""``datetime.date`` is enumerated, so a pure date conversion is not blindness.

``datetime.date.today`` is the class's one clock read and has a row in
``io_primitives/python.yaml``. Nothing declared the rest of the class, so the
first call into another member made ``datetime.date`` "a module the I/O
catalog could not classify", and that one module withheld every self-claim
verdict: the tracker's ``datetime.date.fromisoformat`` (priority_trend.py,
cli.py) moved all 18 from ``confirmed_with_caveats`` to ``inconclusive``.
The same class did the same thing through ``date.today()`` (INV-fugus).

``datetime.datetime`` was already declared complete; ``datetime.date`` now is
too. The member check below fails when CPython adds a classmethod, so the new
constructor is audited for a clock or I/O read before the declaration
vouches for it.
"""
from __future__ import annotations

import datetime
import inspect

from hypergumbo_core.io_boundary import load_catalog

#: Every classmethod ``datetime.date`` defines on Python 3.10-3.13. ``today``
#: reads the clock and has its own row; the rest convert a supplied value.
_DATE_CLASSMETHODS = frozenset({
    "today", "fromtimestamp", "fromordinal", "fromisoformat", "fromisocalendar",
})


def test_datetime_date_io_is_enumerated() -> None:
    assert load_catalog("python").module_io_is_enumerated("datetime.date")


def test_date_classmethods_are_the_audited_set() -> None:
    found = {
        name for name in dir(datetime.date)
        if not name.startswith("_")
        # The C type exposes classmethod_descriptor, the pure-Python fallback
        # (_pydatetime) a plain classmethod.
        and type(inspect.getattr_static(datetime.date, name)).__name__
        in ("classmethod", "classmethod_descriptor")
    }
    assert found == _DATE_CLASSMETHODS
