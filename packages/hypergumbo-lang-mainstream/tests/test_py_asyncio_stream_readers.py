# SPDX-License-Identifier: AGPL-3.0-or-later
"""asyncio's stream factories type the readers they return, and say what medium (WI-himim).

``reader, writer = await asyncio.open_connection(host, 80)`` hands back a
``StreamReader`` and a ``StreamWriter``; ``proc = await
asyncio.create_subprocess_exec(...)`` a ``Process`` whose ``stdout`` is a
``StreamReader``. py.py typed none of them -- an AWAITED call was never typed,
and a TUPLE target never was -- so ``await reader.read(100)`` emitted
``python:external:0-0:read`` and reached no catalogue row: a TCP read through
``open_connection`` was not a ``net_recv`` crossing at all.

The types come from the library-signature catalogue: ``tuple_returns`` (a
tuple-returning producer, one type per position) and ``member_types`` (a
member attribute of a typed object), beside the existing ``signatures``. The
MEDIUM comes from which factory made the stream: ``open_connection`` is a
``net_stream``, ``open_unix_connection`` and the subprocess pipes are a
``pipe`` -- stamped as ``io_target_kind`` on every call through that receiver,
the stamp WI-foloh's ``net_recv`` / ``ipc_recv`` dual row selects on.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def _calls(tmp_path: Path, source: str) -> dict[str, list[tuple[str, str | None]]]:
    """{method name: [(dst module slot, io_target_kind)]} for each call in app.py."""
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    (root / "app.py").write_text(source)
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    found: dict[str, list[tuple[str, str | None]]] = {}
    for e in data["edges"]:
        if e["type"] == "calls" and e["src"].startswith("python:app.py:"):
            module, _, rest = e["dst"].split(":", 1)[1].partition(":0-0:")
            found.setdefault(rest.split(":")[0], []).append(
                (module, (e.get("meta") or {}).get("io_target_kind")),
            )
    return found


def test_open_connection_types_both_ends_as_a_network_stream(tmp_path: Path) -> None:
    calls = _calls(tmp_path, (
        "import asyncio\n\n\nasync def f(host):\n"
        "    reader, writer = await asyncio.open_connection(host, 80)\n"
        "    writer.write(b'GET /')\n"
        "    return await reader.readuntil(b'\\n')\n"
    ))
    assert ("asyncio.StreamReader", "net_stream") in calls["readuntil"], calls
    assert ("asyncio.StreamWriter", "net_stream") in calls["write"], calls


def test_open_unix_connection_is_a_pipe(tmp_path: Path) -> None:
    calls = _calls(tmp_path, (
        "from asyncio import open_unix_connection\n\n\nasync def f(path):\n"
        "    reader, writer = await open_unix_connection(path)\n"
        "    return await reader.read(100)\n"
    ))
    assert ("asyncio.StreamReader", "pipe") in calls["read"], calls


def test_a_subprocess_s_pipes_are_its_members(tmp_path: Path) -> None:
    calls = _calls(tmp_path, (
        "import asyncio\n\n\nasync def f(cmd):\n"
        "    proc = await asyncio.create_subprocess_exec(cmd, stdout=asyncio.subprocess.PIPE)\n"
        "    proc.stdin.write(b'x')\n"
        "    return await proc.stdout.read()\n"
    ))
    assert ("asyncio.StreamReader", "pipe") in calls["read"], calls
    assert ("asyncio.StreamWriter", "pipe") in calls["write"], calls


def test_a_rebound_name_loses_the_medium(tmp_path: Path) -> None:
    """``reader = other`` drops the medium the stream carried. (Its TYPE
    outlives an untyped rebinding -- py.py's typed-assignment arms purge only
    when the new value is typed, for every external type, not this item's.)"""
    calls = _calls(tmp_path, (
        "import asyncio\n\n\nasync def f(host, other):\n"
        "    reader, writer = await asyncio.open_connection(host, 80)\n"
        "    reader = other\n"
        "    return await reader.read(1)\n"
    ))
    assert all(kind is None for _, kind in calls["read"]), calls


def test_an_annotated_reader_is_typed_without_a_medium(tmp_path: Path) -> None:
    """Control: a parameter annotated StreamReader came from anywhere."""
    calls = _calls(tmp_path, (
        "import asyncio\n\n\nasync def f(reader: asyncio.StreamReader):\n"
        "    return await reader.read(1)\n"
    ))
    assert calls["read"] == [("asyncio.StreamReader", None)], calls


def test_an_unknown_awaited_factory_types_nothing(tmp_path: Path) -> None:
    calls = _calls(tmp_path, (
        "import mylib\n\n\nasync def f():\n"
        "    a, b = await mylib.connect()\n"
        "    return await a.read(1)\n"
    ))
    assert not any(m.startswith("asyncio") or k for m, k in calls["read"]), calls


def test_a_starred_or_mismatched_target_is_not_typed_by_position(tmp_path: Path) -> None:
    for target in ("reader, *rest", "reader, w, extra"):
        calls = _calls(tmp_path / target.replace(",", "").replace("*", "s").replace(" ", "_"), (
            "import asyncio\n\n\nasync def f(host):\n"
            f"    {target} = await asyncio.open_connection(host, 80)\n"
            "    return await reader.read(1)\n"
        ))
        assert not any(m == "asyncio.StreamReader" for m, _ in calls["read"]), (target, calls)


def test_a_shadowed_root_names_no_factory(tmp_path: Path) -> None:
    """A parameter named ``asyncio`` is not the module."""
    calls = _calls(tmp_path, (
        "import asyncio\n\n\nasync def f(asyncio, host):\n"
        "    reader, writer = await asyncio.open_connection(host, 80)\n"
        "    return await reader.read(1)\n"
    ))
    assert not any(m == "asyncio.StreamReader" for m, _ in calls["read"]), calls


def test_a_from_imported_root_is_its_owner_path(tmp_path: Path) -> None:
    """``from asyncio import streams; streams.open_connection(...)`` names
    ``asyncio.streams.open_connection``, which no row types -- the qualified
    name is the owner path, as the constructor resolver reads it."""
    calls = _calls(tmp_path, (
        "from asyncio import streams\n\n\nasync def f(host):\n"
        "    reader, writer = await streams.open_connection(host, 80)\n"
        "    return await reader.read(1)\n"
    ))
    assert not any(m == "asyncio.StreamReader" for m, _ in calls["read"]), calls


def test_a_called_call_names_no_factory(tmp_path: Path) -> None:
    calls = _calls(tmp_path, (
        "import asyncio\n\n\ndef pick():\n    return asyncio.open_connection\n\n\n"
        "async def f(host):\n"
        "    reader, writer = await pick()(host, 80)\n"
        "    return await reader.read(1)\n"
    ))
    assert not any(m == "asyncio.StreamReader" for m, _ in calls["read"]), calls
