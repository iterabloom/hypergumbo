# SPDX-License-Identifier: AGPL-3.0-or-later
"""Record which allowlist site lines a process executes (``sys.monitoring``, Python 3.12+).

Used by ``scripts/check-allowlist-commands --trace``. PY_START is watched
globally: a code object outside the site files answers DISABLE and is never
reported again; one inside gets LINE events, and every line that is not a site
answers DISABLE too, so the steady-state cost is near zero. Each first hit is
appended AT ONCE to ``$HG_TRACE_DIR/<pid>.jsonl``: multiprocessing workers
leave through ``os._exit``, so nothing may wait for atexit.

``subprocess.run`` is wrapped so a stub (``fake_run``, set by the runner for
the install commands) replaces the launch without hiding the line that made it.
"""
import json
import os
import sys

TOOL = 4
_seen = set()
_out = None
fake_run = None


def _write(rec):
    global _out
    if _out is None:
        _out = open(os.path.join(os.environ["HG_TRACE_DIR"], f"{os.getpid()}.jsonl"), "a")
    _out.write(json.dumps(rec) + "\n")
    _out.flush()


def install():
    mod = sys.modules[__name__]
    if getattr(mod, "_installed", False):
        return
    mod._installed = True
    with open(os.environ["HG_TRACE_SITES"]) as fh:
        sites = {f: {int(k): v for k, v in d.items()} for f, d in json.load(fh).items()}
    mon = sys.monitoring
    try:
        mon.use_tool_id(TOOL, "hg-allowlist-trace")
    except ValueError:  # pragma: no cover - another tool holds the id
        return
    events = mon.events

    def on_start(code, offset):
        if code.co_filename not in sites:
            return mon.DISABLE
        mon.set_local_events(TOOL, code, events.LINE)
        return None

    def on_line(code, line):
        site = sites.get(code.co_filename, {}).get(line)
        if site is not None and site not in _seen:
            _seen.add(site)
            _write({"site": site, "pid": os.getpid()})
        return mon.DISABLE

    mon.register_callback(TOOL, events.PY_START, on_start)
    mon.register_callback(TOOL, events.LINE, on_line)
    mon.set_events(TOOL, events.PY_START)

    import subprocess  # nosec B404 - wraps run; launches nothing itself
    real_run = subprocess.run

    def run(*a, **k):
        return (mod.fake_run or real_run)(*a, **k)

    subprocess.run = run
