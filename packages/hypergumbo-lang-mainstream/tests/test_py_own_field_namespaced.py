# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-bufud / INV-hovoj: an own field's type answers for ``self.<field>`` only.

A method's typed own fields were merged into its call-resolution type map under
their BARE names (``param_types["conn"] = Conn``), so a field and a local of the
same name were one key. Measured before the fix on dev 02507e31eb:

    self.conn = Conn()        # __init__
    conn = Other()            # a local in a method
    self.conn.ping()          # -> Other.ping @0.85  (WRONG: the field is a Conn)

    [self.conn.ping() for conn in items]   # -> python:external @0.4  (LOST)
    lambda conn: self.conn.ping()          # -> python:external @0.4  (LOST)

The first is a local's assignment overwriting the field's key; the other two are
the comprehension/lambda shadow pruning (``_prune_shadowed``) deleting it. A field
and a local of the same name are different variables. The external-type map
already keys fields as ``self.<field>`` for exactly this reason (INV-fibis); the
in-repo map now does too, so the collision cannot be expressed.

The bare key also typed a BARE name the method never bound: ``conn.ping()`` with
no local ``conn`` resolved to the field's class, which is not what Python does
(that name is a global or a NameError). Pinned below as well.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.py import extract_nodes

_CLASSES = '''\
class Conn:
    def ping(self):
        return 1


class Other:
    def ping(self):
        return 2


'''


def _ping_targets(tmp_path: Path, body: str) -> list[str]:
    """The ``dst`` of every calls edge to a ``ping`` from ``Svc.run``."""
    src = _CLASSES + (
        "class Svc:\n"
        "    def __init__(self):\n"
        "        self.conn = Conn()\n"
        "\n"
        "    def run(self, items):\n"
        + "".join(f"        {line}\n" for line in body.splitlines())
    )
    f = tmp_path / "m.py"
    f.write_text(src)
    res = extract_nodes(f)
    return [
        e.dst for e in res.edges
        if e.edge_type == "calls" and e.src.endswith(":Svc.run:method")
        and e.dst.split(":")[-2].endswith("ping")
    ]


def _is(dst: str, cls: str) -> bool:
    return dst.endswith(f":{cls}.ping:method")


def test_reach_the_plain_field_call_resolves_to_the_field_type(tmp_path: Path) -> None:
    (dst,) = _ping_targets(tmp_path, "self.conn.ping()")
    assert _is(dst, "Conn"), dst


def test_a_same_named_local_does_not_retype_the_field(tmp_path: Path) -> None:
    targets = _ping_targets(tmp_path, "conn = Other()\nself.conn.ping()\nconn.ping()")
    assert len(targets) == 2, targets
    field_call, local_call = targets
    assert _is(field_call, "Conn"), targets
    assert _is(local_call, "Other"), targets


def test_a_comprehension_variable_does_not_erase_the_field(tmp_path: Path) -> None:
    (dst,) = _ping_targets(tmp_path, "return [self.conn.ping() for conn in items]")
    assert _is(dst, "Conn"), dst


def test_a_lambda_parameter_does_not_erase_the_field(tmp_path: Path) -> None:
    (dst,) = _ping_targets(tmp_path, "f = lambda conn: self.conn.ping()\nreturn f")
    assert _is(dst, "Conn"), dst


def test_a_bare_name_is_not_the_field(tmp_path: Path) -> None:
    """``conn`` with no local binding is not ``self.conn``."""
    targets = _ping_targets(tmp_path, "conn.ping()")
    assert not any(_is(t, "Conn") for t in targets), targets
