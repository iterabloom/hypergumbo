# SPDX-License-Identifier: AGPL-3.0-or-later
"""The call-resolution fixture: one program, written once per mainstream language (WI-tazuk).

This is NOT a test module (the leading underscore keeps pytest from collecting
it); ``test_call_resolution_matrix.py`` imports it. Run it as a script to print
the matrix with confidences::

    python packages/hypergumbo-lang-mainstream/tests/_call_matrix_fixture.py

THE PROGRAM. Two classes R and D both declare ``execute()``; a base B declares
``ping()``, ``Child`` inherits B, and an unrelated S declares ``ping()`` too;
``get_db()`` returns an R; a second file declares ``helper()`` and a decoy file
declares another. Nine callers, one call shape each:

==============  =====================================  =============================
caller          the call                               what it reaches
==============  =====================================  =============================
via_var         ``db = get_db(); db.execute()``        R.execute
via_chain       ``get_db().execute()``                 R.execute
via_ctor        ``c = R(); c.execute()``               R.execute
via_inherited   ``ch = Child(); ch.ping()``            B.ping
via_amb_ext     ``x = extlib.make(); x.execute()``     nothing in the repository
via_amb_param   ``(x) x.execute()``, x untyped         nothing in the repository
via_import      ``helper()``, imported from util       util's helper
via_scope_own   ``h = R(); h.execute()``               R.execute
via_scope_ext   ``h = extlib.make(); h.execute()``     nothing in the repository
==============  =====================================  =============================

THE SAME NAME IN ANOTHER FUNCTION (INV-zabud). The last two callers each bind a
local ``h``, and two more functions bind an ``h`` of the DECOY class:
``scope_decoy_local`` (``h = D()``) and, where a parameter can carry a type,
``scope_decoy_param`` (``h: D``). ``decoy_last`` writes the two decoys after the
measured callers, otherwise before them. A variable-type table kept per file
shows here and nowhere else in the program: ``via_scope_own`` reaches the decoy
(the first ``h`` in the file typed every ``h``), or ``via_scope_ext`` is bound
at all (an ``h`` nothing types kept the type another function gave its own).

``via_amb_param`` exists only where the language allows an untyped parameter.
Statically typed languages bind ``db`` with their inference keyword (``var``,
``auto``, ``:=``, ``let``, ``val``); Objective-C has none and writes the type.

WHY A DECOY, AND FOUR ARMS. With one class declaring ``execute()`` every
analyzer reaches it, typed or not, so a fixture without a decoy cannot tell a
typed resolution from a pick by name. The decoy makes a pick visible, and the
arms show what the pick depends on: ``decoy_last`` puts the decoy class (and
the decoy file, by path order) after or before the right one, and ``swap``
exchanges the class names (Conn/Other, Base/Stray), which rules out a pick by
spelling.

HOW A DESTINATION IS JUDGED. Every right declaration returns 1 and every decoy
returns 2. A destination symbol is "right" or "decoy" by the digit in its own
source span, so the verdict does not depend on how an analyzer spells a
qualified name, and a renamed class cannot fool it.

CELLS. One arm gives ``right``, ``decoy``, ``unresolved`` (an edge to no
symbol of the repository), ``no_edge`` (no call edge names the method at all)
or ``right+unresolved`` (both edges). For the two ``amb`` callers nothing in
the repository is the callee, so an edge to either class is ``picked``. Four
arms collapse to one cell: the common verdict when they agree,
``order:<decoy last>/<decoy first>`` when only the declaration order changes
it, ``mixed:...`` otherwise. An edge a linker superseded
(``meta["superseded_by"]``) is not counted: the linker's edge is the answer.

The whole pipeline runs (``run_behavior_map``), not one analyzer, because the
inherited-call and receiver-type linkers are part of how a call is resolved.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

from hypergumbo_core.cli import run_behavior_map

def order(decoy_last: bool, right: str, decoy: str) -> str:
    return (right + "\n" + decoy) if decoy_last else (decoy + "\n" + right)


#: The same-named-variable callers, per language: the function's first line
#: (``@n`` its name, ``@p`` its parameters), the line binding ``h`` to a new
#: ``@c``, the line binding ``h`` to an external value, the call, the closing
#: line, and a parameter ``h`` typed ``@c`` (``None`` where a parameter cannot
#: carry a type).
_SCOPE: dict[str, tuple[str, str, str, str, str, "str | None"]] = {
    "python": ("def @n(@p):", "    h = @c()", "    h = extlib.make()", "    h.execute()", "", "h: @c"),
    "javascript": ("function @n(@p) {", "  const h = new @c();", "  const h = extlib.make();",
                   "  h.execute();", "}", None),
    "typescript": ("function @n(@p): void {", "  const h = new @c();", "  const h = extlib.make();",
                   "  h.execute();", "}", "h: @c"),
    "java": ("    static void @n(@p) {", "        var h = new @c();", "        var h = Lib.make();",
             "        h.execute();", "    }", "@c h"),
    "kotlin": ("fun @n(@p) {", "    val h = @c()", "    val h = make()", "    h.execute()", "}", "h: @c"),
    "scala": ("  def @n(@p): Unit = {", "    val h = new @c()", "    val h = Lib.make()", "    h.execute()",
              "  }", "h: @c"),
    "csharp": ("        static void @n(@p)\n        {", "            var h = new @c();",
               "            var h = Factory.make();", "            h.execute();", "        }", "@c h"),
    "go": ("func @n(@p) {", "\th := &@c{}", "\th := extlib.Make()", "\th.execute()", "}", "h *@c"),
    "rust": ("pub fn @n(@p) {", "    let h = @c::new();", "    let h = extlib::make();", "    h.execute();",
             "}", "h: @c"),
    "swift": ("func @n(@p) {", "    let h = @c()", "    let h = ExtLib.make()", "    h.execute()", "}", "h: @c"),
    "cpp": ("void @n(@p) {", "    @c h;", "    auto h = extlib::make();", "    h.execute();", "}", "@c& h"),
    "objc": ("+ (void)@n@p {", "    @c *h = [[@c alloc] init];", "    id h = [ExtLib make];", "    [h execute];",
             "}", ":(@c *)h"),
    "ruby": ("def @n@p", "  h = @c.new", "  h = ExtLib.make", "  h.execute", "end", None),
    "php": ("function @n(@p)\n{", "    $h = new @c();", "    $h = Factory::make();", "    $h->execute();", "}",
            "@c $h"),
    "groovy": ("    static void @n(@p) {", "        def h = new @c()", "        def h = Lib.make()",
               "        h.execute()", "    }", "@c h"),
    "lua": ("local function @n(@p)", "  local h = @c.new()", "  local h = extlib.make()", "  h:execute()", "end",
            None),
    "perl": ("sub @n@p {", "    my $h = @c->new();", "    my $h = ExtLib->make();", "    $h->execute();", "}",
             None),
}


def scope_callers(lang: str, R: str, D: str, decoy_last: bool) -> str:
    """The four functions that each bind a local named ``h`` (see the module docstring)."""
    head, own, ext, call, tail, param = _SCOPE[lang]

    def function(name: str, bind: str, cls: str = "", params: str = "") -> str:
        lines = [head.replace("@n", name).replace("@p", params)]
        lines += [bind.replace("@c", cls)] if bind else []
        return "\n".join([*lines, call, *([tail] if tail else [])]) + "\n"

    decoys = [function("scope_decoy_local", own, D)]
    if param is not None:
        decoys.insert(0, function("scope_decoy_param", "", params=param.replace("@c", D)))
    measured = [function("via_scope_ext", ext), function("via_scope_own", own, R)]
    return "\n".join(measured[::-1] + decoys[::-1] if decoy_last else decoys + measured)


# ---------------------------------------------------------------- python
def py(R, D, B, S, dl, split=False, third=False):
    def cls(n, m, v):
        return f"class {n}:\n    def {m}(self):\n        return {v}\n\n"
    spare = "class Spare:\n    def execute(self):\n        return 2\n\n    def ping(self):\n        return 2\n\n"
    side = "z" if dl else "a"
    files = {"util.py": "def helper():\n    return 1\n", f"{side}_decoy.py": "def helper():\n    return 2\n"}
    if third:
        files[f"{side}_spare_util.py"] = "def helper():\n    return 2\n"
    if split:
        # One class per file. The decoys' files sort after (``z_``) or before
        # (``a_``) every right file. main imports R and Child, and D too:
        # the same-variable callers below name it. S is never imported.
        files |= {"store.py": cls(R, "execute", 1), f"{side}_store.py": cls(D, "execute", 2),
                  "base.py": cls(B, "ping", 1), "child.py": f"from base import {B}\n\n\nclass Child({B}):\n    pass\n",
                  f"{side}_stray.py": cls(S, "ping", 2)}
        if third:
            files[f"{side}_spare.py"] = spare
        classes = f"from store import {R}\nfrom {side}_store import {D}\nfrom child import Child\n"
    else:
        ex = order(dl, cls(R, "execute", 1), cls(D, "execute", 2) + (spare if third else ""))
        pg = order(dl, cls(B, "ping", 1) + f"class Child({B}):\n    pass\n\n", cls(S, "ping", 2))
        classes = f"\n{ex}\n{pg}"
    files["main.py"] = f"""import extlib
import util
from util import helper
{classes}
def get_db() -> {R}:
    return {R}()


def via_var():
    db = get_db()
    db.execute()


def via_chain():
    get_db().execute()


def via_ctor():
    c = {R}()
    c.execute()


def via_inherited():
    ch = Child()
    ch.ping()


def via_amb_ext():
    x = extlib.make()
    x.execute()


def via_amb_param(x):
    x.execute()


{scope_callers("python", R, D, dl)}
def via_import():
    helper()


def via_qual():
    util.helper()
"""
    return files


# ---------------------------------------------------------------- javascript / typescript
def _jsts(R, D, B, S, dl, ts, split=False, third=False):
    t = (lambda s: s) if ts else (lambda s: "")
    def cls(n, m, v, ext=""):
        return f"class {n}{ext} {{\n  {m}(){t(': number')} {{ return {v}; }}\n}}\n"
    def helper(v):
        return f"export function helper(){t(': number')} {{ return {v}; }}\n"
    spare = f"class Spare {{\n  execute(){t(': number')} {{ return 2; }}\n\n  ping(){t(': number')} {{ return 2; }}\n}}\n"
    e = "ts" if ts else "js"
    x = "" if ts else ".js"
    side = "z" if dl else "a"
    files = {f"util.{e}": helper(1), f"{side}_decoy.{e}": helper(2)}
    if third:
        files[f"{side}_spare_util.{e}"] = helper(2)
    if split:
        # One class per file, named by role; the decoys' files sort after
        # (``z_``) or before (``a_``) every right file. main imports R, Child and
        # D (the scope decoys name D); S and Spare stay unreferenced.
        files |= {f"store.{e}": "export " + cls(R, "execute", 1), f"{side}_store.{e}": "export " + cls(D, "execute", 2),
                  f"base.{e}": "export " + cls(B, "ping", 1),
                  f"child.{e}": f"import {{ {B} }} from './base{x}';\n\nexport class Child extends {B} {{}}\n",
                  f"{side}_stray.{e}": "export " + cls(S, "ping", 2)}
        if third:
            files[f"{side}_spare.{e}"] = "export " + spare
        classes = (f"import {{ {R} }} from './store{x}';\nimport {{ {D} }} from './{side}_store{x}';\n"
                   f"import {{ Child }} from './child{x}';\n")
    else:
        ex = order(dl, cls(R, "execute", 1), cls(D, "execute", 2) + (spare if third else ""))
        pg = order(dl, cls(B, "ping", 1) + f"class Child extends {B} {{}}\n", cls(S, "ping", 2))
        classes = f"\n{ex}\n{pg}"
    files[f"main.{e}"] = f"""import * as extlib from 'extlib';
import {{ helper }} from './util{x}';
import * as util from './util{x}';
{classes}
function get_db(){t(': ' + R)} {{
  return new {R}();
}}

function via_var(){t(': void')} {{
  const db = get_db();
  db.execute();
}}

function via_chain(){t(': void')} {{
  get_db().execute();
}}

function via_ctor(){t(': void')} {{
  const c = new {R}();
  c.execute();
}}

function via_inherited(){t(': void')} {{
  const ch = new Child();
  ch.ping();
}}

function via_amb_ext(){t(': void')} {{
  const x = extlib.make();
  x.execute();
}}

function via_amb_param(x{t(': any')}){t(': void')} {{
  x.execute();
}}

{scope_callers("typescript" if ts else "javascript", R, D, dl)}
function via_import(){t(': void')} {{
  helper();
}}

function via_qual(){t(': void')} {{
  util.helper();
}}
"""
    return files


def js(R, D, B, S, dl, split=False, third=False):
    return _jsts(R, D, B, S, dl, False, split, third)


def ts(R, D, B, S, dl, split=False, third=False):
    return _jsts(R, D, B, S, dl, True, split, third)


# ---------------------------------------------------------------- java
def java(R, D, B, S, dl, split=False, third=False):
    def cls(n, m, v, ext="", pub=""):
        return f"{pub}class {n}{ext} {{\n    {pub}int {m}() {{ return {v}; }}\n}}\n"
    def spare(pub=""):
        return f"{pub}class Spare {{\n    {pub}int execute() {{ return 2; }}\n\n    {pub}int ping() {{ return 2; }}\n}}\n"
    def util(pkg, v):
        return f"package {pkg};\n\npublic class Util {{\n    public static int helper() {{ return {v}; }}\n}}\n"
    dec = ("zdecoy" if dl else "adecoy")
    files = {"util/Util.java": util("util", 1), f"{dec}/Util.java": util(dec, 2)}
    if third:
        files[f"{dec}/spare/Util.java"] = util(f"{dec}.spare", 2)
    if split:
        # A file's name is its public class's, so the PACKAGE carries the side:
        # the right classes in ``model``, the decoys in the decoy package, whose
        # directory sorts after (``zdecoy``) or before (``adecoy``) every right
        # one. Main imports R, Child and D (the scope decoys name D); S and Spare
        # stay unreferenced.
        def unit(pkg, text):
            return f"package {pkg};\n\n{text}"
        files |= {f"model/{R}.java": unit("model", cls(R, "execute", 1, pub="public ")),
                  f"{dec}/{D}.java": unit(dec, cls(D, "execute", 2, pub="public ")),
                  f"model/{B}.java": unit("model", cls(B, "ping", 1, pub="public ")),
                  "model/Child.java": unit("model", f"public class Child extends {B} {{}}\n"),
                  f"{dec}/{S}.java": unit(dec, cls(S, "ping", 2, pub="public "))}
        if third:
            files[f"{dec}/Spare.java"] = unit(dec, spare("public "))
        classes = f"import model.{R};\nimport {dec}.{D};\nimport model.Child;\n"
    else:
        ex = order(dl, cls(R, "execute", 1), cls(D, "execute", 2) + (spare() if third else ""))
        pg = order(dl, cls(B, "ping", 1) + f"class Child extends {B} {{}}\n", cls(S, "ping", 2))
        classes = f"\n{ex}\n{pg}"
    files["app/Main.java"] = f"""package app;

import ext.Lib;
import util.Util;
{classes}
public class Main {{
    static {R} get_db() {{
        return new {R}();
    }}

    static void via_var() {{
        var db = get_db();
        db.execute();
    }}

    static void via_chain() {{
        get_db().execute();
    }}

    static void via_ctor() {{
        var c = new {R}();
        c.execute();
    }}

    static void via_inherited() {{
        var ch = new Child();
        ch.ping();
    }}

    static void via_amb_ext() {{
        var x = Lib.make();
        x.execute();
    }}

{scope_callers("java", R, D, dl)}
    static void via_import() {{
        Util.helper();
    }}

    static void via_qual() {{
        util.Util.helper();
    }}
}}
"""
    return files


# ---------------------------------------------------------------- kotlin
def kotlin(R, D, B, S, dl, split=False, third=False):
    def cls(n, m, v, op=""):
        return f"{op}class {n} {{\n    fun {m}(): Int = {v}\n}}\n"
    spare = "class Spare {\n    fun execute(): Int = 2\n\n    fun ping(): Int = 2\n}\n"
    dec = ("zdecoy" if dl else "adecoy")
    files = {"util/Util.kt": "package util\n\nfun helper(): Int = 1\n",
             f"{dec}/Util.kt": f"package {dec}\n\nfun helper(): Int = 2\n"}
    if third:
        files[f"{dec}/spare/Util.kt"] = f"package {dec}.spare\n\nfun helper(): Int = 2\n"
    if split:
        # One class per file, named by role. The right classes are in package
        # ``model``, the decoys in the decoy package, whose directory sorts after
        # (``zdecoy``) or before (``adecoy``) every right one. Main imports R, Child
        # and D (the scope decoys name D); S and Spare stay unreferenced.
        files |= {"model/Store.kt": "package model\n\n" + cls(R, "execute", 1),
                  f"{dec}/Store.kt": f"package {dec}\n\n" + cls(D, "execute", 2),
                  "model/Base.kt": "package model\n\n" + cls(B, "ping", 1, "open "),
                  "model/Child.kt": f"package model\n\nclass Child : {B}()\n",
                  f"{dec}/Stray.kt": f"package {dec}\n\n" + cls(S, "ping", 2)}
        if third:
            files[f"{dec}/Spare.kt"] = f"package {dec}\n\n" + spare
        classes = f"import model.{R}\nimport {dec}.{D}\nimport model.Child\n"
    else:
        ex = order(dl, cls(R, "execute", 1), cls(D, "execute", 2) + (spare if third else ""))
        pg = order(dl, cls(B, "ping", 1, "open ") + f"class Child : {B}()\n", cls(S, "ping", 2))
        classes = f"\n{ex}\n{pg}"
    files["app/Main.kt"] = f"""package app

import ext.make
import util.helper
{classes}
fun get_db(): {R} {{
    return {R}()
}}

fun via_var() {{
    val db = get_db()
    db.execute()
}}

fun via_chain() {{
    get_db().execute()
}}

fun via_ctor() {{
    val c = {R}()
    c.execute()
}}

fun via_inherited() {{
    val ch = Child()
    ch.ping()
}}

fun via_amb_ext() {{
    val x = make()
    x.execute()
}}

{scope_callers("kotlin", R, D, dl)}
fun via_import() {{
    helper()
}}

fun via_qual() {{
    util.helper()
}}
"""
    return files


# ---------------------------------------------------------------- scala
def scala(R, D, B, S, dl, split=False, third=False):
    def cls(n, m, v):
        return f"class {n} {{\n  def {m}(): Int = {v}\n}}\n"
    def util(pkg, v):
        return f"package {pkg}\n\nobject Util {{\n  def helper(): Int = {v}\n}}\n"
    spare = "class Spare {\n  def execute(): Int = 2\n\n  def ping(): Int = 2\n}\n"
    dec = ("zdecoy" if dl else "adecoy")
    files = {"util/Util.scala": util("util", 1), f"{dec}/Util.scala": util(dec, 2)}
    if third:
        files[f"{dec}/spare/Util.scala"] = util(f"{dec}.spare", 2)
    if split:
        # One class per file, named by role. The right classes are in package
        # ``model``, the decoys in the decoy package, whose directory sorts after
        # (``zdecoy``) or before (``adecoy``) every right one. Main imports R, Child
        # and D (the scope decoys name D); S and Spare stay unreferenced.
        files |= {"model/Store.scala": "package model\n\n" + cls(R, "execute", 1),
                  f"{dec}/Store.scala": f"package {dec}\n\n" + cls(D, "execute", 2),
                  "model/Base.scala": "package model\n\n" + cls(B, "ping", 1),
                  "model/Child.scala": f"package model\n\nclass Child extends {B}\n",
                  f"{dec}/Stray.scala": f"package {dec}\n\n" + cls(S, "ping", 2)}
        if third:
            files[f"{dec}/Spare.scala"] = f"package {dec}\n\n" + spare
        classes = f"import model.{R}\nimport {dec}.{D}\nimport model.Child\n"
    else:
        ex = order(dl, cls(R, "execute", 1), cls(D, "execute", 2) + (spare if third else ""))
        pg = order(dl, cls(B, "ping", 1) + f"class Child extends {B}\n", cls(S, "ping", 2))
        classes = f"\n{ex}\n{pg}"
    files["app/Main.scala"] = f"""package app

import ext.Lib
import util.Util.helper
import util.Util
{classes}
object Main {{
  def get_db(): {R} = new {R}()

  def via_var(): Unit = {{
    val db = get_db()
    db.execute()
  }}

  def via_chain(): Unit = {{
    get_db().execute()
  }}

  def via_ctor(): Unit = {{
    val c = new {R}()
    c.execute()
  }}

  def via_inherited(): Unit = {{
    val ch = new Child()
    ch.ping()
  }}

  def via_amb_ext(): Unit = {{
    val x = Lib.make()
    x.execute()
  }}

{scope_callers("scala", R, D, dl)}
  def via_import(): Unit = {{
    helper()
  }}

  def via_qual(): Unit = {{
    Util.helper()
  }}
}}
"""
    return files


# ---------------------------------------------------------------- c#
def csharp(R, D, B, S, dl, split=False, third=False):
    def cls(n, m, v, ext=""):
        return f"    public class {n}{ext}\n    {{\n        public int {m}() {{ return {v}; }}\n    }}\n"
    child = f"    public class Child : {B}\n    {{\n    }}\n"
    spare = ("    public class Spare\n    {\n        public int execute() { return 2; }\n\n"
             "        public int ping() { return 2; }\n    }\n")
    dec = ("ZDecoy" if dl else "ADecoy")
    files = {"UtilNs/Util.cs": "namespace UtilNs\n{\n    public static class Util\n    {\n        public static int helper() { return 1; }\n    }\n}\n",
             f"{dec}/Util.cs": f"namespace {dec}\n{{\n    public static class Util\n    {{\n        public static int helper() {{ return 2; }}\n    }}\n}}\n"}
    if third:
        files[f"{dec}/SpareUtil.cs"] = (f"namespace {dec}.Spare\n{{\n    public static class Util\n    {{\n"
                                        "        public static int helper() { return 2; }\n    }\n}\n")
    if split:
        # One class per file, all in namespace App, which makes them visible to
        # Program with no using directive. A namespace is not tied to a folder in
        # C#, so the decoy classes live in the decoy folder (ADecoy sorts before
        # App, ZDecoy after UtilNs) and keep namespace App.
        def unit(text):
            return f"namespace App\n{{\n{text}}}\n"
        files |= {"App/Store.cs": unit(cls(R, "execute", 1)), f"{dec}/Store.cs": unit(cls(D, "execute", 2)),
                  "App/Base.cs": unit(cls(B, "ping", 1)), "App/Child.cs": unit(child),
                  f"{dec}/Stray.cs": unit(cls(S, "ping", 2))}
        if third:
            files[f"{dec}/Spare.cs"] = unit(spare)
        classes = ""
    else:
        ex = order(dl, cls(R, "execute", 1), cls(D, "execute", 2) + (spare if third else ""))
        pg = order(dl, cls(B, "ping", 1) + child, cls(S, "ping", 2))
        classes = f"{ex}\n{pg}\n"
    files["App/Program.cs"] = f"""using ExtLib;
using UtilNs;

namespace App
{{
{classes}    public static class Program
    {{
        static {R} get_db()
        {{
            return new {R}();
        }}

        static void via_var()
        {{
            var db = get_db();
            db.execute();
        }}

        static void via_chain()
        {{
            get_db().execute();
        }}

        static void via_ctor()
        {{
            var c = new {R}();
            c.execute();
        }}

        static void via_inherited()
        {{
            var ch = new Child();
            ch.ping();
        }}

        static void via_amb_ext()
        {{
            var x = Factory.make();
            x.execute();
        }}

{scope_callers("csharp", R, D, dl)}
        static void via_import()
        {{
            Util.helper();
        }}

        static void via_qual()
        {{
            UtilNs.Util.helper();
        }}
    }}
}}
"""
    return files


# ---------------------------------------------------------------- go
def go(R, D, B, S, dl, split=False, third=False):
    # No ``via_qual``: ``via_import`` is already the qualified form (``util.Helper()``,
    # through the module's own package ``example.com/fx/util``), and Go has no
    # second one. An alias (``u.Helper()``) is the same package qualifier renamed.
    def cls(n, m, v):
        return f"type {n} struct{{}}\n\nfunc (r *{n}) {m}() int {{ return {v} }}\n"
    child = f"\ntype Child struct {{\n\t{B}\n}}\n"
    spare = "\ntype Spare struct{}\n\nfunc (r *Spare) execute() int { return 2 }\n\nfunc (r *Spare) ping() int { return 2 }\n"
    side = "z" if dl else "a"
    files = {"go.mod": "module example.com/fx\n\ngo 1.21\n",
             "util/util.go": "package util\n\nfunc Helper() int { return 1 }\n",
             f"{side}decoy/util.go": "package util\n\nfunc Helper() int { return 2 }\n"}
    if third:
        files[f"{side}spare/util.go"] = "package util\n\nfunc Helper() int { return 2 }\n"
    if split:
        # One type per file of package main: every file of a package sees the
        # others with no import. The decoys' files sort after (``z_``) or before
        # (``a_``) every other file.
        def unit(text):
            return "package main\n\n" + text.lstrip("\n")
        files |= {"store.go": unit(cls(R, "execute", 1)), f"{side}_store.go": unit(cls(D, "execute", 2)),
                  "base.go": unit(cls(B, "ping", 1)), "child.go": unit(child),
                  f"{side}_stray.go": unit(cls(S, "ping", 2))}
        if third:
            files[f"{side}_spare.go"] = unit(spare)
        classes = ""
    else:
        ex = order(dl, cls(R, "execute", 1), cls(D, "execute", 2) + (spare if third else ""))
        pg = order(dl, cls(B, "ping", 1) + child, cls(S, "ping", 2))
        classes = f"{ex}\n{pg}\n"
    files["main.go"] = f"""package main

import (
	"example.com/fx/util"
	"github.com/ext/extlib"
)

{classes}func get_db() *{R} {{
	return &{R}{{}}
}}

func via_var() {{
	db := get_db()
	db.execute()
}}

func via_chain() {{
	get_db().execute()
}}

func via_ctor() {{
	c := &{R}{{}}
	c.execute()
}}

func via_inherited() {{
	ch := &Child{{}}
	ch.ping()
}}

func via_amb_ext() {{
	x := extlib.Make()
	x.execute()
}}

{scope_callers("go", R, D, dl)}
func via_import() {{
	util.Helper()
}}

func main() {{}}
"""
    return files


# ---------------------------------------------------------------- rust
def rust(R, D, B, S, dl):
    def cls(n, m, v):
        return (f"pub struct {n};\n\nimpl {n} {{\n    pub fn new() -> Self {{ {n} }}\n"
                f"    pub fn {m}(&self) -> i32 {{ {v} }}\n}}\n")
    ex = order(dl, cls(R, "execute", 1), cls(D, "execute", 2))
    base = (f"pub trait {B} {{\n    fn ping(&self) -> i32 {{ 1 }}\n}}\n\npub struct Child;\n\n"
            f"impl Child {{\n    pub fn new() -> Self {{ Child }}\n}}\n\nimpl {B} for Child {{}}\n")
    pg = order(dl, base, cls(S, "ping", 2))
    dec = ("zdecoy" if dl else "adecoy")
    main = f"""mod {dec};
mod util;

use util::helper;

{ex}
{pg}
pub fn get_db() -> {R} {{
    {R}::new()
}}

pub fn via_var() {{
    let db = get_db();
    db.execute();
}}

pub fn via_chain() {{
    get_db().execute();
}}

pub fn via_ctor() {{
    let c = {R}::new();
    c.execute();
}}

pub fn via_inherited() {{
    let ch = Child::new();
    ch.ping();
}}

pub fn via_amb_ext() {{
    let x = extlib::make();
    x.execute();
}}

{scope_callers("rust", R, D, dl)}
pub fn via_import() {{
    helper();
}}
"""
    return {"Cargo.toml": '[package]\nname = "fx"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\nextlib = "1"\n',
            "src/lib.rs": main,
            "src/util.rs": "pub fn helper() -> i32 { 1 }\n",
            f"src/{dec}.rs": "pub fn helper() -> i32 { 2 }\n"}


# ---------------------------------------------------------------- swift
def swift(R, D, B, S, dl):
    def cls(n, m, v, ext=""):
        return f"class {n}{ext} {{\n    func {m}() -> Int {{ return {v} }}\n}}\n"
    ex = order(dl, cls(R, "execute", 1), cls(D, "execute", 2))
    pg = order(dl, cls(B, "ping", 1) + f"class Child: {B} {{}}\n", cls(S, "ping", 2))
    main = f"""import ExtLib
import Util

{ex}
{pg}
func get_db() -> {R} {{
    return {R}()
}}

func via_var() {{
    let db = get_db()
    db.execute()
}}

func via_chain() {{
    get_db().execute()
}}

func via_ctor() {{
    let c = {R}()
    c.execute()
}}

func via_inherited() {{
    let ch = Child()
    ch.ping()
}}

func via_amb_ext() {{
    let x = ExtLib.make()
    x.execute()
}}

{scope_callers("swift", R, D, dl)}
func via_import() {{
    helper()
}}
"""
    dec = ("ZDecoy" if dl else "ADecoy")
    return {"Sources/App/main.swift": main,
            "Sources/Util/util.swift": "public func helper() -> Int { return 1 }\n",
            f"Sources/{dec}/decoy.swift": "public func helper() -> Int { return 2 }\n"}


# ---------------------------------------------------------------- c++
def cpp(R, D, B, S, dl):
    def cls(n, m, v, ext=""):
        return f"class {n}{ext} {{\npublic:\n    int {m}() {{ return {v}; }}\n}};\n"
    ex = order(dl, cls(R, "execute", 1), cls(D, "execute", 2))
    pg = order(dl, cls(B, "ping", 1) + f"class Child : public {B} {{}};\n", cls(S, "ping", 2))
    main = f"""#include <extlib.h>
#include "util.h"

{ex}
{pg}
{R} get_db() {{
    return {R}();
}}

void via_var() {{
    auto db = get_db();
    db.execute();
}}

void via_chain() {{
    get_db().execute();
}}

void via_ctor() {{
    {R} c;
    c.execute();
}}

void via_inherited() {{
    Child ch;
    ch.ping();
}}

void via_amb_ext() {{
    auto x = extlib::make();
    x.execute();
}}

{scope_callers("cpp", R, D, dl)}
void via_import() {{
    helper();
}}
"""
    dec = ("z" if dl else "a") + "_decoy.cpp"
    return {"main.cpp": main, "util.h": "#pragma once\nint helper();\n",
            "util.cpp": '#include "util.h"\n\nint helper() { return 1; }\n',
            dec: "static int helper() { return 2; }\n\nint decoy_user() { return helper(); }\n"}


# ---------------------------------------------------------------- objective-c
def objc(R, D, B, S, dl):
    def cls(n, m, v, sup="NSObject"):
        return (f"@interface {n} : {sup}\n- (int){m};\n@end\n\n@implementation {n}\n"
                f"- (int){m} {{ return {v}; }}\n@end\n")
    ex = order(dl, cls(R, "execute", 1), cls(D, "execute", 2))
    child = f"\n@interface Child : {B}\n@end\n\n@implementation Child\n@end\n"
    pg = order(dl, cls(B, "ping", 1) + child, cls(S, "ping", 2))
    main = f"""#import <Foundation/Foundation.h>
#import <ExtLib/ExtLib.h>
#import "Util.h"

{ex}
{pg}
@interface App : NSObject
@end

@implementation App
+ ({R} *)get_db {{
    return [[{R} alloc] init];
}}

+ (void)via_var {{
    {R} *db = [App get_db];
    [db execute];
}}

+ (void)via_chain {{
    [[App get_db] execute];
}}

+ (void)via_ctor {{
    {R} *c = [[{R} alloc] init];
    [c execute];
}}

+ (void)via_inherited {{
    Child *ch = [[Child alloc] init];
    [ch ping];
}}

+ (void)via_amb_ext {{
    id x = [ExtLib make];
    [x execute];
}}

+ (void)via_amb_param:(id)x {{
    [x execute];
}}

{scope_callers("objc", R, D, dl)}
+ (void)via_import {{
    helper();
}}
@end
"""
    dec = ("z" if dl else "a") + "_decoy.m"
    return {"main.m": main, "Util.h": "int helper(void);\n",
            "Util.m": '#import "Util.h"\n\nint helper(void) { return 1; }\n',
            dec: "static int helper(void) { return 2; }\n\nint decoy_user(void) { return helper(); }\n"}


# ---------------------------------------------------------------- ruby
def ruby(R, D, B, S, dl):
    def cls(n, m, v, sup=""):
        return f"class {n}{sup}\n  def {m}\n    {v}\n  end\nend\n"
    ex = order(dl, cls(R, "execute", 1), cls(D, "execute", 2))
    pg = order(dl, cls(B, "ping", 1) + f"\nclass Child < {B}\nend\n", cls(S, "ping", 2))
    main = f"""require 'extlib'
require_relative 'util'

{ex}
{pg}
def get_db
  {R}.new
end

def via_var
  db = get_db
  db.execute
end

def via_chain
  get_db.execute
end

def via_ctor
  c = {R}.new
  c.execute
end

def via_inherited
  ch = Child.new
  ch.ping
end

def via_amb_ext
  x = ExtLib.make
  x.execute
end

def via_amb_param(x)
  x.execute
end

{scope_callers("ruby", R, D, dl)}
def via_import
  helper()
end
"""
    dec = ("z" if dl else "a") + "_decoy.rb"
    return {"main.rb": main, "util.rb": "def helper\n  1\nend\n", dec: "def helper\n  2\nend\n"}


# ---------------------------------------------------------------- php
def php(R, D, B, S, dl):
    def cls(n, m, v, ext=""):
        return f"class {n}{ext}\n{{\n    public function {m}()\n    {{\n        return {v};\n    }}\n}}\n"
    ex = order(dl, cls(R, "execute", 1), cls(D, "execute", 2))
    pg = order(dl, cls(B, "ping", 1) + f"\nclass Child extends {B}\n{{\n}}\n", cls(S, "ping", 2))
    main = f"""<?php

namespace App;

use ExtLib\\Factory;
use function Util\\helper;

{ex}
{pg}
function get_db(): {R}
{{
    return new {R}();
}}

function via_var()
{{
    $db = get_db();
    $db->execute();
}}

function via_chain()
{{
    get_db()->execute();
}}

function via_ctor()
{{
    $c = new {R}();
    $c->execute();
}}

function via_inherited()
{{
    $ch = new Child();
    $ch->ping();
}}

function via_amb_ext()
{{
    $x = Factory::make();
    $x->execute();
}}

function via_amb_param($x)
{{
    $x->execute();
}}

{scope_callers("php", R, D, dl)}
function via_import()
{{
    helper();
}}
"""
    dec = ("ZDecoy" if dl else "ADecoy")
    return {"main.php": main,
            "util.php": "<?php\n\nnamespace Util;\n\nfunction helper()\n{\n    return 1;\n}\n",
            f"{dec.lower()}.php": f"<?php\n\nnamespace {dec};\n\nfunction helper()\n{{\n    return 2;\n}}\n"}


# ---------------------------------------------------------------- groovy
def groovy(R, D, B, S, dl, split=False, third=False):
    def cls(n, m, v, ext=""):
        return f"class {n}{ext} {{\n    int {m}() {{ return {v} }}\n}}\n"
    def util(pkg, v):
        return f"package {pkg}\n\nclass Util {{\n    static int helper() {{ return {v} }}\n}}\n"
    spare = "class Spare {\n    int execute() { return 2 }\n\n    int ping() { return 2 }\n}\n"
    dec = ("zdecoy" if dl else "adecoy")
    files = {"util/Util.groovy": util("util", 1), f"{dec}/Util.groovy": util(dec, 2)}
    if third:
        files[f"{dec}/spare/Util.groovy"] = util(f"{dec}.spare", 2)
    if split:
        # One class per file, named by role. The right classes are in package
        # ``model``, the decoys in the decoy package, whose directory sorts after
        # (``zdecoy``) or before (``adecoy``) every right one. Main imports R, Child
        # and D (the scope decoys name D); S and Spare stay unreferenced.
        files |= {"model/Store.groovy": "package model\n\n" + cls(R, "execute", 1),
                  f"{dec}/Store.groovy": f"package {dec}\n\n" + cls(D, "execute", 2),
                  "model/Base.groovy": "package model\n\n" + cls(B, "ping", 1),
                  "model/Child.groovy": f"package model\n\nclass Child extends {B} {{}}\n",
                  f"{dec}/Stray.groovy": f"package {dec}\n\n" + cls(S, "ping", 2)}
        if third:
            files[f"{dec}/Spare.groovy"] = f"package {dec}\n\n" + spare
        classes = f"import model.{R}\nimport {dec}.{D}\nimport model.Child\n"
    else:
        ex = order(dl, cls(R, "execute", 1), cls(D, "execute", 2) + (spare if third else ""))
        pg = order(dl, cls(B, "ping", 1) + f"class Child extends {B} {{}}\n", cls(S, "ping", 2))
        classes = f"\n{ex}\n{pg}"
    files["app/Main.groovy"] = f"""package app

import ext.Lib
import util.Util
{classes}
class Main {{
    static {R} get_db() {{
        return new {R}()
    }}

    static void via_var() {{
        def db = get_db()
        db.execute()
    }}

    static void via_chain() {{
        get_db().execute()
    }}

    static void via_ctor() {{
        def c = new {R}()
        c.execute()
    }}

    static void via_inherited() {{
        def ch = new Child()
        ch.ping()
    }}

    static void via_amb_ext() {{
        def x = Lib.make()
        x.execute()
    }}

    static void via_amb_param(x) {{
        x.execute()
    }}

{scope_callers("groovy", R, D, dl)}
    static void via_import() {{
        Util.helper()
    }}

    static void via_qual() {{
        util.Util.helper()
    }}
}}
"""
    return files


# ---------------------------------------------------------------- lua
def lua(R, D, B, S, dl):
    def cls(n, m, v):
        return (f"local {n} = {{}}\n{n}.__index = {n}\n\nfunction {n}.new()\n  return setmetatable({{}}, {n})\nend\n\n"
                f"function {n}:{m}()\n  return {v}\nend\n")
    ex = order(dl, cls(R, "execute", 1), cls(D, "execute", 2))
    child = (f"\nlocal Child = setmetatable({{}}, {{ __index = {B} }})\nChild.__index = Child\n\n"
             "function Child.new()\n  return setmetatable({}, Child)\nend\n")
    pg = order(dl, cls(B, "ping", 1) + child, cls(S, "ping", 2))
    main = f"""local extlib = require("extlib")
local util = require("util")

{ex}
{pg}
local function get_db()
  return {R}.new()
end

local function via_var()
  local db = get_db()
  db:execute()
end

local function via_chain()
  get_db():execute()
end

local function via_ctor()
  local c = {R}.new()
  c:execute()
end

local function via_inherited()
  local ch = Child.new()
  ch:ping()
end

local function via_amb_ext()
  local x = extlib.make()
  x:execute()
end

local function via_amb_param(x)
  x:execute()
end

{scope_callers("lua", R, D, dl)}
local function via_import()
  util.helper()
end

return {{ via_var = via_var, via_chain = via_chain, via_ctor = via_ctor, via_inherited = via_inherited,
         via_amb_ext = via_amb_ext, via_amb_param = via_amb_param, via_import = via_import }}
"""
    dec = ("z" if dl else "a") + "_decoy.lua"
    mod = "local M = {{}}\n\nfunction M.helper()\n  return {v}\nend\n\nreturn M\n"
    return {"main.lua": main, "util.lua": mod.format(v=1), dec: mod.format(v=2)}


# ---------------------------------------------------------------- perl
def perl(R, D, B, S, dl):
    def cls(n, m, v, parent=""):
        return (f"package {n};\n{parent}\nsub new {{\n    my $class = shift;\n    return bless {{}}, $class;\n}}\n\n"
                f"sub {m} {{\n    return {v};\n}}\n")
    ex = order(dl, cls(R, "execute", 1), cls(D, "execute", 2))
    child = (f"\npackage Child;\nuse parent -norequire, '{B}';\n")
    pg = order(dl, cls(B, "ping", 1) + child, cls(S, "ping", 2))
    main = f"""use strict;
use warnings;

{ex}
{pg}
package main;

use ExtLib;
use Util qw(helper);

sub get_db {{
    return {R}->new();
}}

sub via_var {{
    my $db = get_db();
    $db->execute();
}}

sub via_chain {{
    get_db()->execute();
}}

sub via_ctor {{
    my $c = {R}->new();
    $c->execute();
}}

sub via_inherited {{
    my $ch = Child->new();
    $ch->ping();
}}

sub via_amb_ext {{
    my $x = ExtLib->make();
    $x->execute();
}}

sub via_amb_param {{
    my ($x) = @_;
    $x->execute();
}}

{scope_callers("perl", R, D, dl)}
sub via_import {{
    helper();
}}

1;
"""
    dec = ("ZDecoy" if dl else "ADecoy")
    return {"main.pl": main,
            "Util.pm": "package Util;\nuse strict;\nuse warnings;\nuse Exporter 'import';\nour @EXPORT_OK = qw(helper);\n\nsub helper {\n    return 1;\n}\n\n1;\n",
            f"{dec}.pm": f"package {dec};\nuse strict;\nuse warnings;\n\nsub helper {{\n    return 2;\n}}\n\n1;\n"}


LANGS: dict[str, Callable[..., dict[str, str]]] = {
    "cpp": cpp, "csharp": csharp, "go": go, "groovy": groovy, "java": java, "javascript": js,
    "kotlin": kotlin, "lua": lua, "objc": objc, "perl": perl, "php": php, "python": py,
    "ruby": ruby, "rust": rust, "scala": scala, "swift": swift, "typescript": ts,
}
#: (decoy_last, swap)
ARMS = [(True, False), (True, True), (False, False), (False, True)]
#: The three-candidate arms: ``decoy_last`` only. ``swap`` is not crossed in:
#: over the four arms above it changed no cell in any language.
THIRD_ARMS = [True, False]
#: (cell name, caller, method the call names)
SHAPES = [("var", "via_var", "execute"), ("chain", "via_chain", "execute"), ("ctor", "via_ctor", "execute"),
          ("inherited", "via_inherited", "ping"), ("amb_ext", "via_amb_ext", "execute"),
          ("amb_param", "via_amb_param", "execute"), ("import", "via_import", "helper"),
          ("scope_own", "via_scope_own", "execute"), ("scope_ext", "via_scope_ext", "execute"),
          ("qual", "via_qual", "helper")]
AMBIGUOUS = {"amb_ext", "amb_param", "scope_ext"}


def class_names(swap: bool) -> tuple[str, str, str, str]:
    """(R, D, B, S) for one arm."""
    return ("Other", "Conn", "Stray", "Base") if swap else ("Conn", "Other", "Base", "Stray")


def write_arm(root: Path, lang: str, decoy_last: bool, swap: bool, *, split: bool = False,
              third: bool = False) -> Path:
    """Write one arm of one language under ``root`` and return its directory."""
    directory = root / f"{lang}__dl{int(decoy_last)}_sw{int(swap)}_sp{int(split)}_th{int(third)}"
    program = LANGS[lang](*class_names(swap), decoy_last, split=split, third=third)
    for rel, text in program.items():
        path = directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return directory


def survey(directory: Path) -> dict[str, Any]:
    """The behavior map of ``directory``, as the CLI would write it."""
    with tempfile.TemporaryDirectory() as out:
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            run_behavior_map(directory, out_path=Path(out) / "map.json", progress=False,
                             enable_handler_slices=False, no_sketch_fan_out=True,
                             include_sketch_precomputed=False)
        return json.loads((Path(out) / "map.json").read_text())


def _names(text: str, target: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z_]){target}(?![A-Za-z_])", text, re.IGNORECASE))


def _side(directory: Path, node: dict[str, Any]) -> str:
    """"right" / "decoy" by the digit the declaration returns; "?" when the span shows neither."""
    span = node.get("span") or {}
    try:
        lines = (directory / node["path"]).read_text().splitlines()
    except OSError:
        return "?"
    text = "\n".join(lines[span.get("start_line", 1) - 1: span.get("end_line", 0)])
    one = bool(re.search(r"(?<![\w.])1(?![\w.])", text))
    two = bool(re.search(r"(?<![\w.])2(?![\w.])", text))
    if one != two:
        return "right" if one else "decoy"
    return "?"


def _declared(node: dict[str, Any]) -> bool:
    return bool(node.get("path")) and node["kind"] in ("function", "method")


def callers(behavior_map: dict[str, Any], caller: str) -> set[str]:
    """Ids of the symbols named ``caller`` (``Main.via_var``, ``via_amb_param:``, ...)."""
    return {n["id"] for n in behavior_map["nodes"] if _declared(n)
            and re.split(r"\.|::", n["name"])[-1].rstrip(":") == caller}


def arm_cell(behavior_map: dict[str, Any], directory: Path, shape: str, caller: str,
             target: str) -> tuple[str, list[float], set[str]]:
    """One arm's verdict for one caller, the confidences of the edges behind it,
    and the resolution marks (``meta["resolved_by"]``) of the resolved ones."""
    sources = callers(behavior_map, caller)
    if not sources:
        return "n/a", [], set()
    nodes = {n["id"]: n for n in behavior_map["nodes"]}
    sides: set[str] = set()
    confidences: list[float] = []
    marks: set[str] = set()
    for edge in behavior_map["edges"]:
        if edge["src"] not in sources or edge["type"] != "calls":
            continue
        if (edge.get("meta") or {}).get("superseded_by"):
            continue
        dst = nodes.get(edge["dst"])
        if dst is not None and _names(dst["name"], target):
            sides.add(_side(directory, dst) if _declared(dst) else "unresolved")
        elif dst is None and _names(edge["dst"], target):
            sides.add("unresolved")
        else:
            continue
        confidences.append(edge.get("confidence"))
        meta = edge.get("meta") or {}
        marks |= {meta["resolved_by"]} if "resolved_by" in meta else set(meta.get("resolved_by_values", ()))
    if not sides:
        return "no_edge", [], set()
    if shape in AMBIGUOUS and sides & {"right", "decoy"}:
        return "picked", confidences, marks
    return "+".join(sorted(sides)), confidences, marks


def missing_declarations(behavior_map: dict[str, Any], directory: Path) -> set[str]:
    """Declarations of the program that have no symbol: ``"<method>:<right|decoy>"``."""
    missing = set()
    for target in ("execute", "ping", "helper"):
        got = {_side(directory, n) for n in behavior_map["nodes"] if _declared(n) and _names(n["name"], target)}
        missing |= {f"{target}:{want}" for want in ("right", "decoy") if want not in got}
    return missing


def _collapse(verdicts: list[str]) -> str:
    """Four arms (decoy last / first, each plain and swapped) as one cell."""
    last_plain, last_swapped, first_plain, first_swapped = verdicts
    if len(set(verdicts)) == 1:
        return verdicts[0]
    if last_plain == last_swapped and first_plain == first_swapped:
        return f"order:{last_plain}/{first_plain}"
    return "mixed:" + ",".join(verdicts)


class Measured:
    """What one language's arms gave, in one layout.

    ``cells`` is the collapsed cell per shape over the four arms, ``marks`` the
    resolution marks seen on the resolved edges behind it (sorted, joined with
    ``|``; ``-`` when no edge was resolved), ``third`` the same cell measured
    with a third same-named candidate (two arms: decoy last / first),
    ``confidences`` the confidences seen, ``missing`` the declarations with no
    symbol in any arm and ``absent_callers`` the callers with none.
    """

    def __init__(self) -> None:
        self.cells: dict[str, str] = {}
        self.marks: dict[str, str] = {}
        self.third: dict[str, str] = {}
        self.confidences: dict[str, list[float]] = {shape: [] for shape, _, _ in SHAPES}
        self.missing: set[str] = set()
        self.absent_callers: set[str] = set()


def measure(root: Path, lang: str, *, split: bool = False) -> Measured:
    """Run the arms of ``lang`` under ``root`` in one layout."""
    out = Measured()
    per_arm: dict[str, list[str]] = {shape: [] for shape, _, _ in SHAPES}
    third: dict[str, list[str]] = {shape: [] for shape, _, _ in SHAPES}
    marks: dict[str, set[str]] = {shape: set() for shape, _, _ in SHAPES}
    for decoy_last, swap in ARMS:
        directory = write_arm(root, lang, decoy_last, swap, split=split)
        behavior_map = survey(directory)
        out.missing |= missing_declarations(behavior_map, directory)
        out.absent_callers |= {caller for _, caller, _ in SHAPES if not callers(behavior_map, caller)}
        for shape, caller, target in SHAPES:
            verdict, seen, by = arm_cell(behavior_map, directory, shape, caller, target)
            per_arm[shape].append(verdict)
            out.confidences[shape] += seen
            marks[shape] |= by
    for decoy_last in THIRD_ARMS:
        directory = write_arm(root, lang, decoy_last, False, split=split, third=True)
        behavior_map = survey(directory)
        for shape, caller, target in SHAPES:
            third[shape].append(arm_cell(behavior_map, directory, shape, caller, target)[0])
    for shape, verdicts in per_arm.items():
        out.cells[shape] = _collapse(verdicts)
        out.marks[shape] = "|".join(sorted(marks[shape])) or "-"
        last, first = third[shape]
        out.third[shape] = last if last == first else f"order:{last}/{first}"
    return out


def main() -> None:  # pragma: no cover - a printing aid, run by hand
    """Print both layouts. ``python _call_matrix_fixture.py [language ...]``."""
    import sys
    shapes = [shape for shape, _, _ in SHAPES]
    for split in (False, True):
        print(f"\n## {'one class per file' if split else 'one file'}\n")
        print("| language | " + " | ".join(shapes) + " | no symbol for | no caller |")
        print("|---|" + "---|" * (len(shapes) + 2))
        with tempfile.TemporaryDirectory() as root:
            for lang in sys.argv[1:] or LANGS:
                got = measure(Path(root), lang, split=split)
                row = []
                for shape in shapes:
                    seen = sorted({f"{c:.2f}".rstrip("0").rstrip(".") for c in got.confidences[shape]
                                   if c is not None})
                    cell = got.cells[shape] + (" (" + ", ".join(seen) + ")" if seen else "")
                    cell += f" [{got.marks[shape]}]" if got.marks[shape] != "-" else ""
                    cell += f" {{3: {got.third[shape]}}}" if got.third[shape] != got.cells[shape] else ""
                    row.append(cell)
                print(f"| {lang} | " + " | ".join(row) + " | " + (", ".join(sorted(got.missing)) or "-")
                      + " | " + (", ".join(sorted(got.absent_callers)) or "-") + " |")


if __name__ == "__main__":
    main()
