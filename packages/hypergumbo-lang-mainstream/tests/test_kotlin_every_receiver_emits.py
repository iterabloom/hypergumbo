# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every Kotlin method call emits a calls edge, whatever its receiver (INV-dupol).

kotlin.py's navigation-receiver loop kept a receiver only when it was ``this``,
a navigation expression, a call or an identifier, and then handled a
navigation receiver only when it was rooted at ``this``. The unresolved-edge
fallback sat inside the branch those shapes never reached, so each of these
emitted NOTHING:

* ``java.nio.file.Files.readAllBytes(p)`` written inline (the filed instance)
* ``h.file.readText()`` and ``this.file.readText()``, chains through a field
* ``System.out.println("x")``
* ``"x".trim()``, ``(f).readText()``, ``fs[0].readText()``
* ``super.go()``

A call with no edge is invisible to io-boundaries and to the coverage gate. The
filed instance returned ``confirmed_with_caveats`` on a must_not_exist fs_read
claim whose only caveat named an unrelated ``trim()``.

The edges now follow java's spelling of the same shapes: a fully-qualified
inline call names its owner in the module slot, ``this.field`` names the field's
declared type, a value receiver the analyzer cannot type keeps the
``external`` placeholder (so the untyped-receiver disclosure names it), and
``super.m()`` resolves through the enclosing class's declared base. A chain
through ANOTHER object's field is such a receiver: the file-wide name table
cannot type it without risking a wrong type in the slot.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin


_SRC = '''package app

import java.io.File

open class Base {
    open fun go(): Int = 1
}

class Kid : Base() {
    override fun go(): Int = super.go()
}

class Holder(val file: File, val name: String) {
    fun own(): String = this.file.readText()
}

fun inline(p: String): ByteArray = java.nio.file.Files.readAllBytes(java.nio.file.Paths.get(p))
fun env(): String? = java.lang.System.getenv("K")
fun chain(h: Holder): String = h.file.readText()
fun out(): Unit = System.out.println("x")
fun lit(): String = "x".trim()
fun par(f: File): String = (f).readText()
fun idx(fs: List<File>): String = fs[0].readText()
fun ctl(f: File): String = f.readText()
'''


@pytest.fixture(scope="module")
def calls(tmp_path_factory: pytest.TempPathFactory) -> dict[str, set[str]]:
    repo = tmp_path_factory.mktemp("dupol")
    (repo / "App.kt").write_text(_SRC)
    by_caller: dict[str, set[str]] = {}
    for edge in analyze_kotlin(repo).edges:
        if edge.edge_type != "calls":
            continue
        caller = edge.src.split(":")[-2].rsplit(".", 1)[-1]
        by_caller.setdefault(caller, set()).add(":".join(edge.dst.split(":")[1:4]))
    return by_caller


def test_an_inline_qualified_call_names_its_owner(calls: dict[str, set[str]]) -> None:
    assert "java.nio.file.Files:0-0:readAllBytes" in calls["inline"]
    assert "java.nio.file.Paths:0-0:get" in calls["inline"]
    assert "java.lang.System:0-0:getenv" in calls["env"]


def test_a_chain_through_this_names_the_field_type(
    calls: dict[str, set[str]],
) -> None:
    assert "java.io.File:0-0:readText" in calls["own"]


@pytest.mark.parametrize("caller,method", [
    ("chain", "readText"), ("out", "println"), ("lit", "trim"), ("par", "readText"), ("idx", "readText"),
])
def test_every_other_receiver_shape_emits(
    calls: dict[str, set[str]], caller: str, method: str,
) -> None:
    assert any(d.endswith(f":0-0:{method}") for d in calls.get(caller, set())), calls.get(caller)


def test_super_resolves_to_the_base_method(calls: dict[str, set[str]]) -> None:
    assert any(d.endswith(":Base.go") for d in calls.get("go", set())), calls.get("go")


def test_the_control_is_unchanged(calls: dict[str, set[str]]) -> None:
    assert calls["ctl"] == {"java.io.File:0-0:readText"}


def test_the_filed_claim_is_violated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The finding the item was filed on, with another call in the repo so the
    zero-edges guard cannot be what withholds the verdict."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "App.kt").write_text(
        "package app\n\nfun helper(s: String): String = s.trim()\n\n"
        "fun readInline(p: String): ByteArray =\n"
        "    java.nio.file.Files.readAllBytes(java.nio.file.Paths.get(helper(p)))\n"
    )
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n  - id: C\n    text: t\n    constraint:\n"
        "      boundary: fs_read\n      must_not_exist: true\n"
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    assert verdict["verdict"] == "violated", verdict["details"]
