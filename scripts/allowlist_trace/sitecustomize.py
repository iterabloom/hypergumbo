# SPDX-License-Identifier: AGPL-3.0-or-later
"""Install the allowlist-site tracer in every Python process that inherits PYTHONPATH.

It shadows any other ``sitecustomize`` on the path (a distribution ships one),
so that one is found and run first.
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
for _d in sys.path:
    _cand = os.path.join(_d or ".", "sitecustomize.py")
    if os.path.abspath(_d or ".") != _here and os.path.isfile(_cand):
        with open(_cand) as _fh:
            # Runs the sitecustomize this file shadows, as the interpreter would have.
            exec(compile(_fh.read(), _cand, "exec"),  # noqa: S102  # nosec B102
                 {"__name__": "_chained_sitecustomize", "__file__": _cand})
        break
if os.environ.get("HG_TRACE_DIR"):
    import hgtrace

    hgtrace.install()
