# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-bivab, nim: an overloaded call is chosen among visible declarations by ARITY.

Nim resolves an overloaded call by its arguments. The scoped lookup (WI-giloh)
found the declarations a call can SEE, then guessed among them by name; and a
declaration in the caller's own file hid every imported one, whatever either
could take. A UFCS call ``x.f(a)`` is ``f(x, a)``: two arguments.

Each fixture puts the declaration the old lookup would take (the first one
registered, the nearest file) at the WRONG arity, so the pre-fix code fails.
Each asserts REACH (an edge at the call line) before its target.
"""
from __future__ import annotations

import math
from pathlib import Path

from hypergumbo_lang_extended1.nim import analyze_nim

UNRESOLVED = "UNRESOLVED"


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def _calls(root: Path, caller_file: str) -> dict[int, tuple[str, object, float]]:
    """``{line: (target "path:line" or UNRESOLVED, resolution_quality, conf)}``."""
    result = analyze_nim(root)
    by_id = {s.id: s for s in result.symbols}
    out: dict[int, tuple[str, object, float]] = {}
    for e in result.edges:
        if e.edge_type != "calls" or by_id[e.src].path != caller_file:
            continue
        tgt = by_id.get(e.dst)
        where = f"{tgt.path}:{tgt.span.start_line}" if tgt else UNRESOLVED
        out[e.line] = (where, (e.meta or {}).get("resolution_quality"), e.confidence)
    return out


def test_an_overload_of_the_call_arity_is_chosen(tmp_path: Path) -> None:
    _write(tmp_path, {"a.nim": (
        "proc f(a: int) = discard\n"
        "proc f(a, b: int) = discard\n"
        "proc run() =\n"
        "  f(1, 2)\n"
        "  f(1)\n"
    )})
    calls = _calls(tmp_path, "a.nim")
    assert calls[4][:2] == ("a.nim:2", None), calls
    assert calls[5][:2] == ("a.nim:1", None), calls


def test_a_ufcs_receiver_is_the_first_argument(tmp_path: Path) -> None:
    """nitter's ``add*(timeline: var seq[Tweets]; tweet: Tweet)``: a call that
    passes the receiver plus TWO arguments cannot be it, so it is system's."""
    _write(tmp_path, {
        "types.nim": "proc add*(timeline: var seq[int]; tweet: int) = discard\n",
        "p.nim": (
            "import types\n"
            "proc run(xs: var seq[int]) =\n"
            "  xs.add(1)\n"
            "  xs.add(1, 2)\n"
            "  xs.inner.add(1)\n"
        ),
    })
    calls = _calls(tmp_path, "p.nim")
    assert calls[3][0] == "types.nim:1", calls
    assert calls[4][0] == UNRESOLVED, calls
    # A dotted receiver the name reader cannot spell is still a receiver.
    assert calls[5][0] == "types.nim:1", calls


def test_a_nearer_overload_that_cannot_bind_does_not_hide_an_imported_one(
    tmp_path: Path,
) -> None:
    _write(tmp_path, {
        "fmt.nim": "proc show*(a, b: int): string = $a\n",
        "p.nim": (
            "import fmt\n"
            "proc show(a: int): string = $a\n"
            "proc run() =\n"
            "  discard show(1, 2)\n"
            "  discard show(1)\n"
        ),
    })
    calls = _calls(tmp_path, "p.nim")
    assert calls[4][0] == "fmt.nim:1", calls
    assert calls[5][0] == "p.nim:2", calls


def test_same_arity_overloads_are_an_ambiguous_record(tmp_path: Path) -> None:
    _write(tmp_path, {"a.nim": (
        "proc put(a: int) = discard\n"
        "proc put(a: string) = discard\n"
        "proc run() =\n"
        "  put(1)\n"
    )})
    where, quality, conf = _calls(tmp_path, "a.nim")[4]
    assert where == "a.nim:1" and quality == "ambiguous"
    assert math.isclose(conf, 0.85 / math.sqrt(2))


def test_defaults_varargs_and_a_block_argument_count(tmp_path: Path) -> None:
    _write(tmp_path, {"a.nim": (
        "proc g() = discard\n"
        "proc g(a: int; b = 2) = discard\n"
        "proc h(a, b: string; c: bool) = discard\n"
        "proc h(xs: varargs[int]) = discard\n"
        "proc w(a: int) = discard\n"
        "proc w(a: int; body: proc ()) = discard\n"
        "proc run() =\n"
        "  g(1)\n"
        "  h(1, 2, 3, 4)\n"
        "  w(1):\n"
        "    discard\n"
    )})
    calls = _calls(tmp_path, "a.nim")
    assert calls[8][:2] == ("a.nim:2", None), calls
    assert calls[9][:2] == ("a.nim:4", None), calls
    assert calls[10][:2] == ("a.nim:6", None), calls


def test_procs_carry_their_parameters(tmp_path: Path) -> None:
    _write(tmp_path, {"a.nim": (
        "proc tick = discard\n"
        "proc f*(a, b: int; c = 3; d: varargs[string]) = discard\n"
        "method m(self: Obj) {.base.} = discard\n"
    )})
    params = {
        s.name: (s.meta or {}).get("parameters")
        for s in analyze_nim(tmp_path).symbols if s.kind in ("function", "method")
    }
    assert params["tick"] == []
    assert params["f"] == [
        {"name": "a", "type": "int", "default": False},
        {"name": "b", "type": "int", "default": False},
        {"name": "c", "type": None, "default": True},
        {"name": "d", "type": "varargs[string]", "default": False, "variadic": True},
    ]
    assert params["m"] == [{"name": "self", "type": "Obj", "default": False}]


# ---------------------------------------------------------------------------
# First-argument types: an overload whose first parameter the call's first
# argument provably cannot bind to is not a candidate (WI-bivab, ask 1).
# ---------------------------------------------------------------------------

_TYPES_NIM = (
    "type\n"
    "  Tweet* = object\n"
    "  Photo* = object\n"
    "  Tweets* = seq[Tweet]\n"
    "proc add*(timeline: var seq[Tweets]; tweet: Tweet) = discard\n"
    "proc push*[T](s: var seq[T]; x: T) = discard\n"
)


def test_a_typed_receiver_that_cannot_bind_leaves_the_call_to_the_library(
    tmp_path: Path,
) -> None:
    """nitter: ``result.add photo`` in a proc returning ``seq[Photo]`` is
    system's ``add``; ``types.add*`` takes a ``seq[Tweets]``."""
    _write(tmp_path, {
        "types.nim": _TYPES_NIM,
        "p.nim": (
            "import types\n"
            "proc gallery(photo: Photo): seq[Photo] =\n"
            "  result.add photo\n"
            "proc text(s: var string) =\n"
            "  s.add \"x\"\n"
            "proc nums() =\n"
            "  var xs: seq[int]\n"
            "  xs.add 1\n"
            "  add(xs, 2)\n"
        ),
    })
    calls = _calls(tmp_path, "p.nim")
    assert {line: calls[line][0] for line in (3, 5, 8, 9)} == {
        3: UNRESOLVED, 5: UNRESOLVED, 8: UNRESOLVED, 9: UNRESOLVED,
    }, calls


def test_a_receiver_that_can_bind_still_binds(tmp_path: Path) -> None:
    """The check only EXCLUDES on proof: the same type, an alias of it, a
    generic parameter, and an untyped receiver all keep the edge."""
    _write(tmp_path, {
        "types.nim": _TYPES_NIM + "type Timeline* = seq[Tweets]\n",
        "p.nim": (
            "import types\n"
            "proc a(t: Tweet) =\n"
            "  var tl: seq[Tweets]\n"
            "  tl.add t\n"
            "  var al: Timeline\n"
            "  al.add t\n"
            "  var ints: seq[int]\n"
            "  ints.push 1\n"
            "  var guess = newSeq[Tweets]()\n"
            "  guess.add t\n"
        ),
    })
    calls = _calls(tmp_path, "p.nim")
    assert calls[4][0] == "types.nim:5", calls
    assert calls[6][0] == "types.nim:5", calls
    assert calls[8][0] == "types.nim:6", calls
    assert calls[10][0] == "types.nim:5", calls


def test_a_subtype_binds_a_base_parameter(tmp_path: Path) -> None:
    """``object of Base`` passes as a ``Base`` at the top level."""
    _write(tmp_path, {"a.nim": (
        "type\n"
        "  Base = ref object of RootObj\n"
        "  Derived = ref object of Base\n"
        "proc show(b: Base) = discard\n"
        "proc run(d: Derived) =\n"
        "  d.show()\n"
    )})
    assert _calls(tmp_path, "a.nim")[6][0] == "a.nim:4"


def test_type_incompatibility_is_proved_or_refused() -> None:
    from hypergumbo_lang_extended1.nim import _NimTypes, _split_nim_type

    assert _split_nim_type("var seq[Table[string, int]]") == (
        "seq", ["Table[string, int]"])
    assert _split_nim_type("(string, int)") == ("tuple", ["string", "int"])
    assert _split_nim_type("seq[]") == ("seq", [])
    types = _NimTypes()
    types.nominal.update({"A", "B"})
    types.aliases["L"] = "seq[A]"
    inc = types.incompatible
    assert inc("string", "int") and not inc("int8", "int")  # scalar families
    assert inc("seq[int]", "Table[int, int]")               # container heads
    assert inc("seq[A]", "seq[B]")                          # invariant args
    assert not inc("A", "B")                                # subtype possible
    assert inc("A", "string") and inc("seq[int]", "A")      # kinds differ
    assert not inc("L", "seq[A]") and inc("L", "seq[B]")    # alias expanded
    assert not inc("array[3, int]", "array[4, int]")        # size is no type
    assert not inc("seq[int]", "seq[T]")                    # generic parameter
    assert not inc("seq[int]", "openArray[int]")            # a type class
    assert not inc("Table[int, int]", "Table[int]")         # arity mismatch


def test_a_name_declared_two_ways_is_unknown(tmp_path: Path) -> None:
    """A name one module declares as an alias and another as an object -- or
    as two different aliases -- has no known meaning, so it excludes nothing.
    The control: declared once, the same receiver IS excluded."""
    put = "type Box* = seq[int]\nproc put*(b: var Box; x: int) = discard\n"
    caller = "import a\nproc run(s: var seq[string]) =\n  s.put 1\n"
    cases = {
        "once": {},
        "alias_and_object": {"b.nim": "type Box* = object\n"},
        "two_aliases": {"b.nim": "type Box* = seq[float]\n"},
    }
    got = {}
    for name, extra in cases.items():
        root = tmp_path / name
        _write(root, {"a.nim": put, "p.nim": caller, **extra})
        got[name] = _calls(root, "p.nim")[3][0]
    assert got == {
        "once": UNRESOLVED, "alias_and_object": "a.nim:2", "two_aliases": "a.nim:2",
    }, got


def test_what_the_check_cannot_prove_it_does_not_exclude(tmp_path: Path) -> None:
    """Four shapes measured as false exclusions on nimble, each kept:

    - the callee's generic parameter is named like a repository type
      (``proc assertHasKey[K, V](t: Table[K, V], ..)`` beside a ``type V``);
    - one generic type applied twice (``Term[string, R]`` to ``Term[P, VS]``);
    - an untyped ``let x = ..`` shadowing a typed parameter ``x``;
    - the CALLER's generic parameter in the receiver's type.
    """
    _write(tmp_path, {
        "lib.nim": (
            "import std/tables\n"
            "type\n"
            "  V* = object\n"
            "  R* = object\n"
            "  Term*[P, VS] = object\n"
            "proc assertHasKey*[K, V](t: Table[K, V], k: K) = discard\n"
            "proc merge*[P, VS](terms: seq[Term[P, VS]]) = discard\n"
            "proc wrap*(x: V) = discard\n"
        ),
        "p.nim": (
            "import lib, std/tables\n"
            "proc a(t: Table[string, int]) =\n"
            "  assertHasKey(t, \"k\")\n"
            "proc b(terms: seq[Term[string, R]]) =\n"
            "  merge(terms)\n"
            "proc c(x: seq[int]) =\n"
            "  let x = V()\n"
            "  x.wrap()\n"
            "proc d[T](x: T) =\n"
            "  x.wrap()\n"
        ),
    })
    calls = _calls(tmp_path, "p.nim")
    assert calls[3][0] == "lib.nim:6", calls
    assert calls[5][0] == "lib.nim:7", calls
    assert calls[8][0] == "lib.nim:8", calls
    assert calls[10][0] == "lib.nim:8", calls
