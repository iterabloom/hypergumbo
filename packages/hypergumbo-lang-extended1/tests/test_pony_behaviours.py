# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-rokus: a Pony behaviour (``be``) is a symbol, and its calls are its own.

The member walk handled ``constructor`` and ``method`` nodes only. The grammar
parses ``be`` as a ``behavior`` node, which was skipped, so an actor's
asynchronous message handlers were not symbols and a call inside one was
credited to the actor.

A behaviour is emitted as ``kind="method"`` (the registry has no behaviour
kind, and a behaviour is a method an actor runs asynchronously) with
``meta["is_behaviour"] = True`` and a ``be name(params)`` signature.

The enclosing callable was also looked up by NAME in the repository-wide
registry, which keeps one symbol per name. Every Pony program has an
``actor Main`` with ``new create``, so a second program's ``Main.create``
calls were credited to the first one's. Found by the declaration's POSITION
in its own file now (INV-midag).
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_extended1.pony import analyze_pony

_ACTORS = """\
actor Main
  new create(env: Env) =>
    let w = Worker
    w.ping()
    ping()

  be ping() =>
    helper()

  be tell(n: U32, msg: String) =>
    helper()

  fun helper() => None

actor Worker
  be ping() =>
    work()

  fun work() => None
"""


def _edges(result) -> list[tuple[str, str, str, str, int]]:
    """(src kind, src name, src path, dst name or id, line) per calls edge."""
    by_id = {s.id: s for s in result.symbols}
    out = []
    for e in result.edges:
        if e.edge_type != "calls":
            continue
        src = by_id[e.src]
        dst = by_id[e.dst].name if e.dst in by_id else e.dst
        out.append((src.kind, src.name, src.path, dst, e.line))
    return out


def test_a_behaviour_is_a_method_symbol(tmp_path: Path) -> None:
    (tmp_path / "main.pony").write_text(_ACTORS)
    result = analyze_pony(tmp_path)
    by_name = {s.name: s for s in result.symbols}
    ping = by_name["Main.ping"]
    assert ping.kind == "method"
    assert ping.meta["is_behaviour"] is True
    assert ping.signature == "be ping()"
    assert (ping.span.start_line, ping.span.end_line) == (7, 8)
    assert ping.cyclomatic_complexity == 1 and ping.line_span == 2
    tell = by_name["Main.tell"]
    assert tell.signature == "be tell(n, msg)"
    assert tell.meta["params"] == ["n", "msg"]
    assert by_name["Worker.ping"].meta["is_behaviour"] is True
    # A ``fun`` is not a behaviour.
    assert "is_behaviour" not in by_name["Main.helper"].meta
    # The type counts its behaviours apart from its methods.
    assert by_name["Main"].meta["behaviour_count"] == 2
    assert by_name["Main"].meta["method_count"] == 1


def test_a_call_in_a_behaviour_is_credited_to_it(tmp_path: Path) -> None:
    (tmp_path / "main.pony").write_text(_ACTORS)
    edges = _edges(analyze_pony(tmp_path))
    assert ("method", "Main.ping", "main.pony", "Main.helper", 8) in edges, edges
    assert ("method", "Main.tell", "main.pony", "Main.helper", 11) in edges, edges
    assert ("method", "Worker.ping", "main.pony", "Worker.work", 17) in edges, edges
    assert not [e for e in edges if e[0] == "actor"], edges


def test_a_bare_call_resolves_to_its_actors_behaviour(tmp_path: Path) -> None:
    (tmp_path / "main.pony").write_text(_ACTORS)
    edges = _edges(analyze_pony(tmp_path))
    assert ("constructor", "Main.create", "main.pony", "Main.ping", 5) in edges, edges


def test_same_named_types_in_two_files_keep_their_own_calls(tmp_path: Path) -> None:
    """INV-midag. Both files declare ``actor Main`` with ``new create`` and
    ``fun helper``; the name registry kept the last file's symbols, and the
    first file's calls were credited to (and resolved into) the other file."""
    (tmp_path / "a.pony").write_text(
        "actor Main\n  new create(env: Env) =>\n    helper()\n\n  fun helper() => None\n")
    (tmp_path / "b.pony").write_text(
        "actor Main\n  new create(env: Env) =>\n    None\n\n  be run() =>\n    helper()\n"
        "\n  fun helper() => None\n")
    result = analyze_pony(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    calls = [e for e in result.edges if e.edge_type == "calls"]
    assert len(calls) == 2, calls  # reach
    for e in calls:
        src, dst = by_id[e.src], by_id[e.dst]
        assert src.kind in ("constructor", "method"), src.kind
        assert src.span.start_line <= e.line <= src.span.end_line, (src.name, e.line)
        # The caller and the callee are both the call's own file's.
        assert dst.path == src.path, (src.path, dst.path)
    assert sorted((by_id[e.src].path, by_id[e.src].name, e.line) for e in calls) == [
        ("a.pony", "Main.create", 3), ("b.pony", "Main.run", 6)]


def test_a_bare_call_never_resolves_into_another_files_same_named_type(tmp_path: Path) -> None:
    """``loop()`` in b.pony's ``Main`` names no member of that ``Main``. The
    registry's ``Main.loop`` is a.pony's (another program's) ``Main``: a Pony
    type is declared in one file, so the call stays unresolved."""
    (tmp_path / "a.pony").write_text(
        "actor Main\n  new create(env: Env) =>\n    None\n\n  fun loop() => None\n")
    (tmp_path / "b.pony").write_text(
        "actor Main\n  new create(env: Env) =>\n    loop()\n")
    edges = _edges(analyze_pony(tmp_path))
    assert [(e[1], e[2], e[3]) for e in edges] == [
        ("Main.create", "b.pony", "pony:external:0-0:loop:unresolved")], edges


def test_this_files_field_is_found_as_the_registry_finds_it(tmp_path: Path) -> None:
    """The same-file table holds every symbol the registry holds, fields too:
    ``table(i)`` (``table.apply(i)``) bound to the field before, and still does,
    now to this file's own ``Updater.table`` rather than whichever file's the
    registry kept."""
    src = "actor Updater\n  let table: Array[U64] = Array[U64]\n\n  be run() =>\n    table(0)\n"
    (tmp_path / "a.pony").write_text(src)
    (tmp_path / "b.pony").write_text(src)
    result = analyze_pony(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    calls = [e for e in result.edges if e.edge_type == "calls"]
    assert len(calls) == 2, calls  # reach
    for e in calls:
        src_sym, dst = by_id[e.src], by_id[e.dst]
        assert (src_sym.name, dst.name, dst.kind) == ("Updater.run", "Updater.table", "field")
        assert dst.path == src_sym.path
