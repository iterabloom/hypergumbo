# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/tcl.py
"""A Tcl call resolves the way Tcl resolves a command name.

The analyzer looked a callee up by the word as written in a registry keyed
by each proc's own word, so ``util::helper 1`` missed the proc declared as
``proc helper`` inside ``namespace eval util`` (its key was ``helper``) and
ended on a stand-in; only a proc DECLARED as ``proc util::other`` was found
under its qualified name. A bare word went to whichever proc of that word
was registered last, whatever namespace the call was in.

Tcl reads ``::a::b`` from the global namespace, and any other name first in
the namespace of the proc that runs it and then in the global one; a name
``namespace import`` brought into that namespace is found there too.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_extended1.tcl import analyze_tcl

_SOURCE = """\
namespace eval util {
    proc helper {x} {
        return $x
    }
    proc inner {} {
        helper 2
    }
    namespace export helper
}
proc util::other {y} {
    return $y
}
proc helper {z} {
    return $z
}
proc main {} {
    util::helper 1
    ::util::helper 3
    util::other 4
    missing::thing 5
    helper 6
}
namespace eval client {
    namespace import ::util::helper
    proc useit {} {
        helper 7
    }
}
namespace eval plain {
    proc useit {} {
        helper 8
    }
}
namespace eval app {
    namespace eval sub {
        proc go {} {}
    }
    proc run {} {
        sub::go
    }
}
namespace eval ::glob {
    proc g {} {}
}
proc callg {} {
    glob::g
}
namespace eval w {
    namespace import -force ::util::*
    proc usew {} {
        helper 9
    }
}
namespace eval outer {
    namespace eval ::abs {
        proc p {} {}
    }
}
proc callp {} {
    abs::p
}
"""


def _calls(tmp_path: Path) -> tuple[dict[str, str], dict[int, str]]:
    """``({qualified proc name: id}, {call line: dst})``."""
    (tmp_path / "main.tcl").write_text(_SOURCE)
    result = analyze_tcl(tmp_path)
    procs = {}
    for s in result.symbols:
        if s.kind == "function":
            ns = (s.meta or {}).get("namespace")
            procs[f"{ns}::{s.name}" if ns else s.name] = s.id
    dst_by_line = {e.line: e.dst for e in result.edges if e.edge_type == "calls"}
    return procs, dst_by_line


def test_a_namespace_qualified_call_reaches_the_proc_declared_in_the_namespace(
    tmp_path: Path,
) -> None:
    procs, dst = _calls(tmp_path)
    assert "util::helper" in procs and "helper" in procs  # reach
    assert dst[17] == procs["util::helper"]   # util::helper 1
    assert dst[18] == procs["util::helper"]   # ::util::helper 3
    # Control: a proc declared by its qualified name was already found.
    assert dst[19] == procs["util::other"]
    # Control: a namespace the tree does not declare stays a stand-in.
    assert dst[20].endswith(":0-0:thing:unresolved")


def test_a_bare_call_is_read_in_the_callers_namespace_then_the_global_one(
    tmp_path: Path,
) -> None:
    procs, dst = _calls(tmp_path)
    assert dst[6] == procs["util::helper"]    # inside util: util::helper
    assert dst[21] == procs["helper"]         # from a global proc: ::helper
    assert dst[31] == procs["helper"]         # plain imports nothing: ::helper
    # A relative qualified name is read from the caller's namespace first.
    assert dst[39] == procs["app::sub::go"]
    # ``namespace eval ::glob`` is the global namespace's child ``glob``.
    assert dst[46] == procs["::glob::g"]
    # An absolute ``namespace eval`` inside another is not its child.
    assert dst[60] == procs["outer::::abs::p"]


def test_namespace_import_brings_the_proc_into_the_importing_namespace(
    tmp_path: Path,
) -> None:
    procs, dst = _calls(tmp_path)
    assert dst[26] == procs["util::helper"]   # client imported ::util::helper
    # A pattern import (with an option before it) beats the global proc too.
    assert dst[51] == procs["util::helper"]
