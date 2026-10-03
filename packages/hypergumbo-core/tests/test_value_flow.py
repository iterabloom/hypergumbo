# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for :mod:`hypergumbo_core.value_flow`.

Each case is a tiny module with ONE ``sink(<expr>)`` call; the test resolves
``<expr>`` and compares with the expected set, or ``None`` for "cannot say".
Every rule is pinned from both sides: a shape the walk follows, and the
neighbouring shape it must refuse because following it would be unsound
(the refusals are what let a registry gate trust a non-``None`` answer).
"""
from __future__ import annotations

import ast
import textwrap
from typing import Callable, Optional

import pytest

from hypergumbo_core.value_flow import ModuleValueFlow, flow_for, resolve_strings


def _sink_arg(tree: ast.Module) -> ast.expr:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "sink"
        ):
            return node.args[0]
    raise AssertionError("fixture has no sink(...) call")


def _resolve(source: str) -> Optional[frozenset[str]]:
    tree = ast.parse(textwrap.dedent(source))
    return resolve_strings(_sink_arg(tree), tree)


F = frozenset

CASES: dict[str, tuple[str, Optional[frozenset[str]]]] = {
    # -- constants, operators -------------------------------------------
    "literal": ('sink("a")', F({"a"})),
    "none_contributes_nothing": ("sink(None)", F()),
    "string_iterated_is_refused": (
        'X = "ab"\nfor c in X:\n    sink(c)', None,
    ),
    "ternary": ('sink("a" if c else "b")', F({"a", "b"})),
    "boolop": ('sink(x or "b")\nx = "a"', F({"a", "b"})),
    "walrus": ('sink((y := "a"))', F({"a"})),
    "walrus_binding_read_later": ('if (y := "a"):\n    sink(y)', F({"a"})),
    "walrus_in_comprehension_binds_outside": (
        'def _f():\n    [(z := "a") for _ in range(2)]\n    sink(z)', F({"a"}),
    ),
    "unsupported_expression": ("sink(1 + 2)", None),
    "starred_argument_is_its_first_element": ('X = ("a",)\nsink(*X)', F({"a"})),
    "unbound_name": ("sink(nowhere)", None),
    # -- f-strings --------------------------------------------------------
    "fstring_expands": (
        'p = "a" if c else "b"\nsink(f"x_{p}")', F({"x_a", "x_b"}),
    ),
    "fstring_format_spec_refused": ('p = "a"\nsink(f"{p:>3}")', None),
    "fstring_conversion_refused": ('p = "a"\nsink(f"{p!r}")', None),
    "fstring_non_string_segment_refused": ("p = None\nsink(f\"{p}\")", None),
    "fstring_over_cap_refused": (
        "a = " + " if c else ".join(f'"{i}"' for i in range(6)) + "\n"
        'sink(f"{a}{a}")',
        None,
    ),
    "fstring_iterated_refused": ('for c in f"ab":\n    sink(c)', None),
    # -- narrowing ----------------------------------------------------------
    "narrow_eq_else_arm": (
        'def _f(t):\n    sink("d" if t == "dev" else t)\n'
        '_f("dev")\n_f("main")',
        F({"d", "main"}),
    ),
    "narrow_eq_reversed": (
        'def _f(t):\n    sink("d" if "dev" == t else t)\n_f("dev")\n_f("x")',
        F({"d", "x"}),
    ),
    "narrow_eq_then_arm": (
        'def _f(t):\n    sink(t if t == "dev" else "z")\n_f("dev")\n_f("x")',
        F({"dev", "z"}),
    ),
    "narrow_noteq": (
        'def _f(t):\n    sink(t if t != "dev" else "z")\n_f("dev")\n_f("x")',
        F({"x", "z"}),
    ),
    "narrow_in": (
        'def _f(t):\n    sink(t if t in ("a", "b") else "z")\n_f("a")\n_f("q")',
        F({"a", "z"}),
    ),
    "narrow_notin": (
        'def _f(t):\n    sink(t if t not in ["a"] else "z")\n_f("a")\n_f("q")',
        F({"q", "z"}),
    ),
    "narrow_not": (
        'def _f(t):\n    sink(t if not t == "a" else "z")\n_f("a")\n_f("q")',
        F({"q", "z"}),
    ),
    "narrow_other_name_untouched": (
        'def _f(t, u):\n    sink(t if u == "a" else "z")\n_f("a", "a")',
        F({"a", "z"}),
    ),
    "narrow_in_string_is_substring_not_set": (
        'def _f(t):\n    sink(t if t in "abc" else "z")\n_f("ab")',
        F({"ab", "z"}),
    ),
    "narrow_comprehension_filter": (
        'L = ["a", "b"]\nfor x in [k for k in L if k != "a"]:\n    sink(x)',
        F({"b"}),
    ),
    "narrow_comprehension_filters_compose": (
        'L = ["a", "b", "c"]\n'
        'for x in [k for k in L if k != "a" if k not in ("b",)]:\n    sink(x)',
        F({"c"}),
    ),
    "narrow_comprehension_dict_key": (
        'D = {"type": 1, "x": 2}\n'
        'for x in {k: v for k, v in D.items() if k != "type"}:\n    sink(x)',
        F({"x"}),
    ),
    "narrow_comprehension_opaque_filter_untouched": (
        'L = ["a", "b"]\nfor x in [k for k in L if f(k)]:\n    sink(x)',
        F({"a", "b"}),
    ),
    "narrow_comprehension_filter_on_another_name_untouched": (
        'L = ["a", "b"]\nfor x in [k for k in L if q != "a"]:\n    sink(x)',
        F({"a", "b"}),
    ),
    "narrow_comprehension_filter_on_another_target_untouched": (
        'L = ["a", "b"]\n'
        'for x in [j for k in L if k != "a" for j in L]:\n    sink(x)',
        F({"a", "b"}),
    ),
    "narrow_comprehension_element_not_an_iteration_variable_untouched": (
        'L = ["a", "b"]\nc = "a"\nfor x in [c for k in L if c != "a"]:\n    sink(x)',
        F({"a"}),
    ),
    "narrow_less_than_untouched": (
        'def _f(t):\n    sink(t if t < "m" else "z")\n_f("a")', F({"a", "z"}),
    ),
    "narrow_chained_compare_untouched": (
        'def _f(t):\n    sink(t if "a" < t < "c" else "z")\n_f("b")', F({"b", "z"}),
    ),
    "narrow_non_compare_test_untouched": (
        'def _f(t):\n    sink(t if flag else "z")\n_f("b")', F({"b", "z"}),
    ),
    "narrow_only_applies_to_whole_value": (
        'P = ("a", "b")\nsink((P if c == "x" else P)[0])', F({"a"}),
    ),
    # -- displays -----------------------------------------------------------
    "tuple_position": ('P = ("a", "b")\nsink(P[1])', F({"b"})),
    "negative_index_is_any_position": ('P = ("a", "b")\nsink(P[-1])', F({"a", "b"})),
    "tuple_index_out_of_range": ('P = ("a",)\nsink(P[5])', F()),
    "tuple_starred_display": (
        'A = ("a",)\nP = (*A, "b")\nsink(P[0])', F({"a", "b"}),
    ),
    "tuple_whole_is_not_a_string": ('P = ("a",)\nsink(P)', None),
    "tuple_attr_refused": ('P = ("a",)\nsink(P.x)', None),
    "list_any_index": ('L = ["a", "b"]\nsink(L[i])', F({"a", "b"})),
    "set_elements": ('for s in {"a", "b"}:\n    sink(s)', F({"a", "b"})),
    "dict_values": ('D = {"k": "v"}\nsink(D[k])', F({"v"})),
    "dict_keys_iterated": ('D = {"k": "v"}\nfor k in D:\n    sink(k)', F({"k"})),
    "dict_spread": (
        'B = {"j": "w"}\nD = {"k": "v", **B}\nsink(D[x])', F({"v", "w"}),
    ),
    "dict_spread_keys": (
        'B = {"j": "w"}\nD = {**B}\nfor k in D:\n    sink(k)', F({"j"}),
    ),
    "dict_whole_refused": ('D = {"k": "v"}\nsink(D)', None),
    "dict_attr_refused": ('D = {"k": "v"}\nsink(D.k)', None),
    # -- comprehensions -------------------------------------------------------
    "listcomp_element": ('for x in [n for n in ("a", "b")]:\n    sink(x)', F({"a", "b"})),
    "dictcomp_value": ('D = {k: "v" for k in ks}\nsink(D[q])', F({"v"})),
    "dictcomp_key": ('D = {k: 1 for k in ("a",)}\nfor k in D:\n    sink(k)', F({"a"})),
    "comprehension_whole_refused": ('sink([n for n in ("a",)])', None),
    "comprehension_attr_refused": ('G = (n for n in ("a",))\nsink(G.x)', None),
    "comprehension_first_iter_uses_outer_name": (
        'x = ("a", "b")\nfor y in [x for x in x]:\n    sink(y)', F({"a", "b"}),
    ),
    "comprehension_second_iter_uses_inner_name": (
        'R = [("a", "b")]\nfor y in [c for row in R for c in row]:\n    sink(y)',
        None,
    ),
    # -- names and bindings ---------------------------------------------------
    "module_constant": ('X = "a"\ndef _f():\n    sink(X)', F({"a"})),
    "function_local_rebinding": ('X = "a"\ndef _f():\n    X = "b"\n    sink(X)', F({"b"})),
    "annotated_assignment": ('X: str = "a"\nsink(X)', F({"a"})),
    "tuple_target_position": ('a, b = "x", "y"\nsink(b)', F({"y"})),
    "nested_tuple_target": ('a, (b, c) = "x", ("y", "z")\nsink(c)', F({"z"})),
    "starred_target_before_name_refused": ('*a, b = ("x", "y")\nsink(b)', None),
    "starred_target_after_name_ok": ('b, *a = ("x", "y")\nsink(b)', F({"x"})),
    "for_loop_over_pairs": (
        'PAIRS = [("f", "a"), ("g", "b")]\nfor flag, t in PAIRS:\n    sink(t)',
        F({"a", "b"}),
    ),
    "with_binding_refused": ('with open(p) as x:\n    sink(x)', None),
    "import_binding_refused": ("import x\nsink(x)", None),
    "except_binding_refused": (
        'try:\n    pass\nexcept E as x:\n    sink(x)', None,
    ),
    "def_used_as_value_refused": ("def x():\n    pass\nsink(x)", None),
    "del_binding_refused": ('x = "a"\ndel x\nsink(x)', None),
    "match_capture_refused": (
        'match v:\n    case [x]:\n        sink(x)', None,
    ),
    "match_star_refused": (
        'match v:\n    case [*x]:\n        sink(x[0])', None,
    ),
    "match_rest_refused": (
        'match v:\n    case {**x}:\n        sink(x["k"])', None,
    ),
    "global_declaration_reads_module": (
        'X = "a"\ndef _f():\n    global X\n    sink(X)', F({"a"}),
    ),
    "global_rebinding_from_function_refused": (
        'X = "a"\ndef _g():\n    global X\n    X = "b"\nsink(X)', None,
    ),
    "global_declaration_of_unbound_name": (
        'def _f():\n    global Q\n    sink(Q)', None,
    ),
    "nonlocal_declaration_reads_enclosing": (
        'def _f():\n    x = "a"\n    def _g():\n        nonlocal x\n'
        '        sink(x)\n    _g()',
        F({"a"}),
    ),
    "nonlocal_rebinding_refused": (
        'def _f():\n    x = "a"\n    def _g():\n        nonlocal x\n'
        '        x = "b"\n    _g()\n    sink(x)',
        None,
    ),
    "class_body_name_not_seen_from_method": (
        'X = "m"\nclass C:\n    X = "c"\n    def f(self):\n        sink(X)', F({"m"}),
    ),
    "class_body_name_seen_in_class_body": (
        'class C:\n    X = "c"\n    sink(X)', F({"c"}),
    ),
    "augassign_collection": (
        'L = ["a"]\nL += ["b"]\nfor x in L:\n    sink(x)', F({"a", "b"}),
    ),
    "augassign_string_refused": ('X = "a"\nX += "b"\nsink(X)', None),
    # -- collection mutations -----------------------------------------------
    "append": (
        'def _f():\n    out = []\n    out.append(("n", "a"))\n'
        '    return out\nfor n, t in _f():\n    sink(t)',
        F({"a"}),
    ),
    "append_in_closure": (
        'def _f():\n    out = []\n    def add(t):\n        out.append(t)\n'
        '    add("a")\n    add("b")\n    return out\nfor t in _f():\n    sink(t)',
        F({"a", "b"}),
    ),
    "add_insert_extend": (
        'S = set()\nS.add("a")\nL = []\nL.insert(0, "b")\nL.extend(["c"])\n'
        'L.extend(S)\nfor x in L:\n    sink(x)',
        F({"b", "c", "a"}),
    ),
    "set_update": ('S = {"a"}\nS.update(["b"])\nfor x in S:\n    sink(x)', F({"a", "b"})),
    "dict_update_values": (
        'D = {}\nD.update({"k": "v"})\nsink(D[k])', F({"v"}),
    ),
    "dict_update_keys": (
        'D = dict()\nD.update({"k": "v"})\nfor k in D:\n    sink(k)', F({"k"}),
    ),
    "dict_setdefault": (
        'D = {}\nD.setdefault("k", "v")\nsink(D[q])', F({"v"}),
    ),
    "dict_setdefault_keys": (
        'D = {}\nD.setdefault("k", "v")\nfor k in D.keys():\n    sink(k)', F({"k"}),
    ),
    "dict_subscript_store": (
        'D: dict = {}\nD["k"] = "v"\nfor k, v in D.items():\n    sink(v)', F({"v"}),
    ),
    "dict_subscript_store_keys": (
        'D = {}\nD["k"] = "v"\nfor k, v in D.items():\n    sink(k)', F({"k"}),
    ),
    "list_subscript_store": ('L = ["a"]\nL[0] = "b"\nfor x in L:\n    sink(x)', F({"a", "b"})),
    "subscript_augassign_refused": ('D = {}\nD["k"] += "v"\nsink(D["k"])', None),
    "subscript_delete_harmless": ('D = {"k": "v"}\ndel D["k"]\nsink(D[q])', F({"v"})),
    "escape_into_unknown_call_refused": (
        'L = ["a"]\nfill(L)\nfor x in L:\n    sink(x)', None,
    ),
    "escape_via_keyword_refused": (
        'L = ["a"]\nfill(items=L)\nfor x in L:\n    sink(x)', None,
    ),
    "escape_via_starred_refused": (
        'L = ["a"]\nfill(*L)\nfor x in L:\n    sink(x)', None,
    ),
    "alias_refused": ('L = ["a"]\nM = L\nfor x in L:\n    sink(x)', None),
    "attribute_read_refused": ('L = ["a"]\nL.foo\nfor x in L:\n    sink(x)', None),
    "unknown_method_refused": ('L = ["a"]\nL.frob()\nfor x in L:\n    sink(x)', None),
    "harmless_reads": (
        'L = ["a"]\nlen(L)\nsorted(L, key=len)\nif L: pass\nwhile not L: pass\n'
        'assert L\nq = "x" in L\nf"{L}"\nr = 1 if L else 2\nif L and q: pass\n'
        'D = {}\nD[L] = 1\nother.extend(L)\nL.count("a")\n'
        'def _g():\n    return L\n'
        'def _h():\n    yield L\n'
        'for x in L:\n    sink(x)',
        F({"a"}),
    ),
    "harmless_starred_into_builtin": (
        'L = ["a"]\nprint(*L)\nfor x in L:\n    sink(x)', F({"a"}),
    ),
    "boolop_outside_a_test_refused": (
        'L = ["a"]\nM = L or []\nfor x in L:\n    sink(x)', None,
    ),
    "ifexp_branch_refused": ('L = ["a"]\nM = L if c else []\nfor x in L:\n    sink(x)', None),
    "shadowed_builtin_is_not_safe": (
        'def len(x):\n    x.append("z")\nL = ["a"]\nlen(L)\nfor x in L:\n    sink(x)',
        None,
    ),
    "element_mutated_in_place_refused": (
        'D = {"k": ("a",)}\nD["k"].append("b")\nsink(D["k"][0])', None,
    ),
    "setdefault_then_append_refused": (
        'D = {}\nD.setdefault("k", []).append(("x", "a"))\nsink(D["k"][0][1])',
        None,
    ),
    "element_subscript_store_refused": (
        'D = {"k": ("a",)}\nD.get("k")[0] = "b"\nsink(D["k"][0])', None,
    ),
    "nested_collection_iteration_refused": (
        'T = [["a"]]\nfor row in T:\n    for c in row:\n        sink(c)', None,
    ),
    "mutable_element_by_position_refused": (
        'R = [["a", "b"]]\nfor a, b in R:\n    sink(b)', None,
    ),
    "mutable_element_name_refused": (
        'row = ["a", "b"]\nR = [row]\nfor a, b in R:\n    sink(b)', None,
    ),
    "mutable_element_factory_refused": (
        'R = [list()]\nfor a, b in R:\n    sink(b)', None,
    ),
    "mutable_element_name_from_factory_refused": (
        'row = dict()\nR = [row]\nfor a, b in R:\n    sink(b)', None,
    ),
    "append_starred_refused": ('L = []\nL.append(*x)\nfor y in L:\n    sink(y)', None),
    "append_keyword_refused": ('L = []\nL.append(x=1)\nfor y in L:\n    sink(y)', None),
    "append_on_dict_refused": ('D = {}\nD.append("a")\nsink(D[k])', None),
    "insert_wrong_arity_refused": ('L = []\nL.insert("a")\nfor y in L:\n    sink(y)', None),
    "extend_on_dict_refused": ('D = {}\nD.extend("a")\nsink(D[k])', None),
    "setdefault_on_list_refused": ('L = []\nL.setdefault("a")\nfor y in L:\n    sink(y)', None),
    "update_two_args_refused": ('S = set()\nS.update("a", "b")\nfor y in S:\n    sink(y)', None),
    "tuples_skip_the_mutation_scan": (
        'def _g(a, b):\n    sink(b)\nP = ("x", "a")\n_g(*P)', F({"a"}),
    ),
    # -- parameters ----------------------------------------------------------
    "private_function_param": (
        'def _f(t):\n    sink(t)\n_f("a")\n_f(t="b")', F({"a", "b"}),
    ),
    "nested_function_param": (
        'def outer():\n    def inner(t):\n        sink(t)\n    inner("a")', F({"a"}),
    ),
    "public_module_function_param_refused": ('def f(t):\n    sink(t)\nf("a")', None),
    "method_param_refused": (
        'class C:\n    def _m(self, t):\n        sink(t)\nC()._m("a")', None,
    ),
    "lambda_param_refused": ('g = lambda t: sink(t)\ng("a")', None),
    "decorator_of_subscript_refused": (
        '@REGISTRY["x"]\ndef _f(t):\n    sink(t)\n_f("a")', None,
    ),
    "unrelated_starred_assignment_is_skipped": (
        '*rest, last = parts\nX = "a"\nsink(X)', F({"a"}),
    ),
    "lambda_defaults_belong_to_the_enclosing_scope": (
        'def _f(t):\n    g = lambda u=t: u\n    sink(t)\n_f("a")', F({"a"}),
    ),
    "unbound_element_refused": (
        'R = [nowhere]\nfor a, b in R:\n    sink(b)', None,
    ),
    "collection_stored_as_dict_value_refused": (
        'L = ["a"]\nD = {"k": L}\nfor x in L:\n    sink(x)', None,
    ),
    "collection_used_as_dict_key_refused": (
        'L = ["a"]\nD = {L: 1}\nfor x in L:\n    sink(x)', None,
    ),
    "dict_spread_copy_is_harmless": (
        'B = {"k": "v"}\nD = {**B}\nsink(B[q])', F({"v"}),
    ),
    "list_starred_copy_is_harmless": (
        'L = ["a"]\nM = [*L]\nfor x in L:\n    sink(x)', F({"a"}),
    ),
    "decorated_function_refused": (
        '@trace\ndef _f(t):\n    sink(t)\n_f("a")', None,
    ),
    "lru_cache_is_transparent": (
        '@lru_cache(maxsize=None)\ndef _f(t):\n    sink(t)\n_f("a")', F({"a"}),
    ),
    "vararg_refused": ('def _f(*t):\n    sink(t[0])\n_f("a")', None),
    "kwarg_refused": ('def _f(**t):\n    sink(t["k"])\n_f(k="a")', None),
    "default_when_omitted": (
        'def _f(t="d"):\n    sink(t)\n_f()\n_f("a")', F({"a", "d"}),
    ),
    "kwonly_default": (
        'def _f(*, t="d"):\n    sink(t)\n_f()\n_f(t="a")', F({"a", "d"}),
    ),
    "missing_without_default_refused": ('def _f(t):\n    sink(t)\n_f()', None),
    "double_star_call_refused": ('def _f(t):\n    sink(t)\n_f(**kw)', None),
    "function_used_as_callback_refused": (
        'def _f(t):\n    sink(t)\n_f("a")\nmap(_f, xs)', None,
    ),
    "function_never_called_refused": ('def _f(t):\n    sink(t)', None),
    "function_bound_twice_refused": (
        'def _f(t):\n    sink(t)\ndef _f(t):\n    pass\n_f("a")', None,
    ),
    "splat_with_known_arity": (
        'def _pick(c):\n    if c:\n        return c, "extends"\n'
        '    return None\n'
        'def _emit(s, c, t):\n    sink(t)\n'
        'p = _pick(x)\n_emit(1, *p)',
        F({"extends"}),
    ),
    "splat_before_the_parameter": (
        'def _emit(a, b, t):\n    sink(t)\nP = ("x", "y")\n_emit(*P, "z")', F({"z"}),
    ),
    "splat_with_unknown_arity_refused": (
        'def _emit(a, t):\n    sink(t)\n_emit(*xs)', None,
    ),
    "splat_with_mixed_arity_refused": (
        'def _p(c):\n    if c:\n        return ("a",)\n    return ("a", "b")\n'
        'def _emit(a, t):\n    sink(t)\n_emit(*_p(c))',
        None,
    ),
    "splat_of_starred_tuple_refused": (
        'A = ("a",)\nP = (*A, "b")\ndef _emit(a, t):\n    sink(t)\n_emit(*P)', None,
    ),
    "splat_of_name_from_loop_refused": (
        'def _emit(a, t):\n    sink(t)\nfor p in PS:\n    _emit(*p)', None,
    ),
    "splat_of_unbound_name_refused": (
        'def _emit(a, t):\n    sink(t)\n_emit(*nowhere)', None,
    ),
    "splat_of_opaque_call_refused": (
        'def _emit(a, t):\n    sink(t)\n_emit(*other())', None,
    ),
    "splat_of_call_returning_unknown_refused": (
        'def _p():\n    return xs\ndef _emit(a, t):\n    sink(t)\n_emit(*_p())', None,
    ),
    "splat_of_attribute_refused": (
        'def _emit(a, t):\n    sink(t)\n_emit(*o.pair)', None,
    ),
    "recursive_arity_depth_cap": (
        "def _emit(a, t):\n    sink(t)\n"
        + "".join(f"p{i} = p{i + 1}\n" for i in range(12))
        + 'p12 = ("a", "b")\n_emit(*p0)',
        None,
    ),
    # -- calls --------------------------------------------------------------
    "local_function_returns": (
        'def _k(x):\n    return "calls" if x else "references"\nsink(_k(1))',
        F({"calls", "references"}),
    ),
    "function_without_return_contributes_nothing": (
        "def _k():\n    pass\nsink(_k())", F(),
    ),
    "bare_return_contributes_nothing": (
        'def _k(x):\n    if x:\n        return\n    return "a"\nsink(_k(1))', F({"a"}),
    ),
    "generator_yields": (
        'def _g():\n    yield "a"\n    yield from ("b",)\n    yield\n'
        'for x in _g():\n    sink(x)',
        F({"a", "b"}),
    ),
    "generator_as_string_refused": ('def _g():\n    yield "a"\nsink(_g())', None),
    "decorated_function_call_refused": (
        '@trace\ndef _k():\n    return "a"\nsink(_k())', None,
    ),
    "opaque_name_call_refused": ("from m import k\nsink(k())", None),
    "call_of_subscript_refused": ('sink(FNS[0]())', None),
    "recursion_closes_on_itself": (
        'def _k(n):\n    return "a" if n == 0 else _k(n - 1)\nsink(_k(3))', F({"a"}),
    ),
    "builtin_passthrough": (
        'for x in sorted(list(("b", "a")), key=len):\n    sink(x)', F({"a", "b"}),
    ),
    "builtin_enumerate_value": (
        'for i, x in enumerate(("a",)):\n    sink(x)', F({"a"}),
    ),
    "builtin_enumerate_index": ('for i, x in enumerate(("a",)):\n    sink(i)', F()),
    "builtin_zip": ('for a, b in zip(("x",), ("y",)):\n    sink(b)', F({"y"})),
    "builtin_zip_out_of_range_refused": ('for t in zip(("x",)):\n    sink(t[3])', None),
    "builtin_other_refused": ('for x in map(f, ("a",)):\n    sink(x)', None),
    "builtin_starred_refused": ('for x in list(*xs):\n    sink(x)', None),
    "builtin_keyword_refused": ('for x in list(("a",), k=1):\n    sink(x)', None),
    "builtin_as_string_refused": ('sink(str("a"))', None),
    "builtin_enumerate_without_position_refused": (
        'for p in enumerate(("a",)):\n    sink(p)', None,
    ),
    # -- method calls -------------------------------------------------------
    "items_value": ('D = {"k": "v"}\nfor k, v in D.items():\n    sink(v)', F({"v"})),
    "items_without_unpack_refused": ('D = {"k": "v"}\nfor p in D.items():\n    sink(p)', None),
    "values": ('D = {"k": "v"}\nfor v in D.values():\n    sink(v)', F({"v"})),
    "values_as_string_refused": ('D = {"k": "v"}\nsink(D.values())', None),
    "get_with_default": ('D = {"k": "v"}\nsink(D.get(q, "d"))', F({"v", "d"})),
    "get_without_default": ('D = {"k": "v"}\nsink(D.get(q))', F({"v"})),
    "pop": ('L = ["a"]\nsink(L.pop())', F({"a"})),
    "copy": ('L = ["a"]\nfor x in L.copy():\n    sink(x)', F({"a"})),
    "unknown_method_call_refused": ('sink("a".upper())', None),
    "self_method_refused": (
        'class C:\n    def f(self):\n        sink(self.g())', None,
    ),
    "method_with_keywords_refused": ('D = {"k": "v"}\nsink(D.get(q, default="d"))', None),
    "method_with_starred_refused": ('D = {"k": "v"}\nsink(D.get(*q))', None),
    # -- record classes ------------------------------------------------------
    "dataclass_field_positional": (
        '@dataclass(frozen=True)\nclass R:\n    src: object\n    edge_type: str\n'
        'refs = [R(1, "extends"), R(2, edge_type="implements")]\n'
        'def _emit(rs: list[R]) -> None:\n    for r in rs:\n        sink(r.edge_type)\n'
        '_emit(refs)',
        F({"extends", "implements"}),
    ),
    "dataclass_field_default": (
        '@dataclasses.dataclass\nclass R:\n    a: int\n    t: str = "d"\n'
        '    u: str = field(default="e")\n    v: list = field(default_factory=list)\n'
        '    K: ClassVar[str] = "k"\n    def m(self):\n        pass\n'
        'x = R(1)\nisinstance(x, (R, int))\nsink(x.t)',
        F({"d"}),
    ),
    "dataclass_field_factory_default_refused": (
        '@dataclass\nclass R:\n    v: str = field(default_factory=str)\nsink(R().v)', None,
    ),
    "dataclass_field_unwrapped_default": (
        '@dataclass\nclass R:\n    u: str = field(default="e")\nsink(R().u)', F({"e"}),
    ),
    "dataclass_missing_required_refused": (
        '@dataclass\nclass R:\n    t: str\nsink(R().t)', None,
    ),
    "namedtuple_by_position": (
        'class P(NamedTuple):\n    a: str\n    b: str\nx = P("x", "y")\nsink(x[1])',
        F({"y"}),
    ),
    "namedtuple_typing_base": (
        'class P(typing.NamedTuple):\n    a: str\nsink(P("x").a)', F({"x"}),
    ),
    "dataclass_by_position_refused": (
        '@dataclass\nclass P:\n    a: str\nsink(P("x")[0])', None,
    ),
    "record_constructed_whole_refused": ('@dataclass\nclass P:\n    a: str\nsink(P("x"))', None),
    "record_replace_included": (
        '@dataclass\nclass P:\n    a: str\np = P("x")\nq = replace(p, a="y")\n'
        'r = p._replace(b="z")\nsink(q.a)',
        F({"x", "y"}),
    ),
    "record_replace_double_star_refused": (
        '@dataclass\nclass P:\n    a: str\np = P("x")\nreplace(p, **kw)\nsink(p.a)', None,
    ),
    "record_field_store_refused": (
        '@dataclass\nclass P:\n    a: str\np = P("x")\np.a = "y"\nsink(p.a)', None,
    ),
    "record_constructed_with_starred_refused": (
        '@dataclass\nclass P:\n    a: str\nsink(P(*xs).a)', None,
    ),
    "record_constructed_with_double_star_refused": (
        '@dataclass\nclass P:\n    a: str\nsink(P(**kw).a)', None,
    ),
    "record_passed_as_factory_refused": (
        '@dataclass\nclass P:\n    a: str\nmap(P, xs)\nsink(P("x").a)', None,
    ),
    "plain_class_attribute_refused": ('class P:\n    a = "x"\nsink(P().a)', None),
    "attribute_with_no_record_refused": ("sink(edge.edge_type)", None),
    "self_attribute_refused": (
        'class C:\n    def f(self):\n        sink(self.t)', None,
    ),
    "record_unknown_field_in_construction_refused": (
        '@dataclass\nclass P:\n    a: str\nsink(P("x").b)', None,
    ),
}


@pytest.mark.parametrize("source,expected", list(CASES.values()), ids=list(CASES))
def test_resolution(source: str, expected: Optional[frozenset[str]]) -> None:
    assert _resolve(source) == expected


class TestTheMotivatingShapes:
    """The producer shapes INV-dahuh, WI-pubin and WI-nakur were filed on,
    reduced to their flow and resolved end to end."""

    def test_loop_over_a_list_of_tuples(self) -> None:
        """scip/edges.py's ``_RELATION_EDGE_TYPES`` (INV-dahuh)."""
        assert _resolve(
            '_RELATION_EDGE_TYPES: "list[tuple[str, str]]" = [\n'
            '    ("is_implementation", "implements"),\n'
            '    ("is_type_definition", "has_type"),\n]\n'
            "def scip_index_to_edges(index):\n"
            "    for rel in index:\n"
            "        for flag_name, edge_type in _RELATION_EDGE_TYPES:\n"
            "            sink(edge_type)\n",
        ) == F({"implements", "has_type"})

    def test_tuples_appended_by_a_helper(self) -> None:
        """linkers/lua_ffi.py's ``_scan_lua_file_for_ffi_calls`` (WI-pubin)."""
        assert _resolve(
            "def _scan(path):\n"
            "    try:\n        content = read(path)\n"
            "    except OSError:\n        return []\n"
            "    results: list[tuple[str, str, int]] = []\n"
            "    for n, line in enumerate(content):\n"
            '        results.append((line, "luajit_ffi_c", n))\n'
            '        results.append((line, "luajit_ffi_load", n))\n'
            "    return results\n"
            "def link(paths):\n"
            "    for p in paths:\n"
            "        calls = _scan(p)\n"
            "        everything.extend(calls)\n"
            "        for name, evidence_type, n in calls:\n"
            "            sink(evidence_type)\n",
        ) == F({"luajit_ffi_c", "luajit_ffi_load"})

    def test_tuples_stored_in_a_dict_then_unpacked(self) -> None:
        """linkers/napi.py's ``export_map`` (WI-pubin)."""
        assert _resolve(
            "def _scan(path):\n"
            "    results = []\n"
            '    results.append(("js", "c", "napi_create_function"))\n'
            '    results.append(("js", "c", "napi_addon_api"))\n'
            "    return results\n"
            "def link(paths, calls):\n"
            "    export_map: dict = {}\n"
            "    for p in paths:\n"
            "        for js, c, evidence_type in _scan(p):\n"
            "            export_map[js] = (c, evidence_type, False)\n"
            "    for call in calls:\n"
            "        if call not in export_map:\n            continue\n"
            "        sym, evidence_type, fallback = export_map[call]\n"
            "        sink(evidence_type)\n",
        ) == F({"napi_create_function", "napi_addon_api"})

    def test_label_carried_through_a_dataclass(self) -> None:
        """dart.py's ``_HeritageRef`` (WI-nakur): a closure appends
        ``(edge_type, base, line)`` tuples, a generator expression wraps them
        in records, and the emitter reads ``ref.edge_type``."""
        assert _resolve(
            "@dataclass(frozen=True)\n"
            "class _Ref:\n    src: object\n    base: str\n    edge_type: str\n"
            "def _clauses(decl):\n"
            "    out: list[tuple[str, str]] = []\n"
            "    def add(edge_type, nodes):\n"
            "        out.extend((edge_type, n) for n in nodes)\n"
            "    for child in decl:\n"
            '        add("extends", child)\n'
            '        add("implements", child)\n'
            "    return out\n"
            "def _collect(tree):\n"
            "    refs: list[_Ref] = []\n"
            "    for node in tree:\n"
            "        refs.extend(_Ref(node, base, edge_type)\n"
            "                    for edge_type, base in _clauses(node))\n"
            "    return refs\n"
            "def _edges(refs: list[_Ref]) -> list:\n"
            "    return [sink(ref.edge_type) for ref in refs]\n",
        ) == F({"extends", "implements"})


class TestBudgets:
    def test_a_chain_deeper_than_the_budget_is_refused(self) -> None:
        chain = "".join(f"x{i} = x{i + 1}\n" for i in range(60))
        assert _resolve(chain + 'x60 = "a"\nsink(x0)') is None

    def test_a_chain_within_the_budget_resolves(self) -> None:
        chain = "".join(f"x{i} = x{i + 1}\n" for i in range(10))
        assert _resolve(chain + 'x10 = "a"\nsink(x0)') == F({"a"})


class TestMemoAndCache:
    def test_a_repeated_question_is_answered_the_same_way(self) -> None:
        tree = ast.parse('X = "a"\nY = nowhere\nsink(X)\nsink(Y)')
        sinks = [
            n.args[0] for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "sink"
        ]
        flow = ModuleValueFlow(tree)
        assert flow.resolve(sinks[0]) == F({"a"})
        assert flow.resolve(sinks[0]) == F({"a"})
        assert flow.resolve(sinks[1]) is None
        assert flow.resolve(sinks[1]) is None

    def test_flow_for_reuses_one_tree_and_replaces_on_another(self) -> None:
        first = ast.parse('sink("a")')
        second = ast.parse('sink("b")')
        assert flow_for(first) is flow_for(first)
        replaced = flow_for(second)
        assert replaced is flow_for(second)
        assert replaced is not flow_for(first)


def _keys(
    source: str,
    readers: frozenset[str] = frozenset(),
    known_empty: Optional[Callable[[ast.expr], bool]] = None,
) -> Optional[frozenset[str]]:
    """The KEYS of the dict passed as ``sink(meta=...)`` (WI-lijaz)."""
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "sink"
        ):
            value = next(kw.value for kw in node.keywords if kw.arg == "meta")
            return flow_for(tree).resolve(
                value, (("key",),), readers=readers, known_empty=known_empty,
            )
    raise AssertionError("fixture has no sink(meta=...) call")


class TestTheReadBeingAnsweredIsNotAnEscape:
    """WI-lijaz: a named dict handed to the sink is the value being asked
    about, so that one use cannot be what makes it unresolvable.

    Before the fix every ``meta=edge_meta`` resolved to ``None``: the walk
    over ``edge_meta``'s uses met ``Edge.create(meta=edge_meta)`` itself,
    an opaque call, and refused the dict as escaped. Every OTHER use is
    still checked, which is what the last two tests pin.
    """

    def test_a_named_dict_at_the_sink(self) -> None:
        assert _keys(
            'D = {"a": 1}\nif c:\n    D["b"] = 2\nsink(meta=D)',
        ) == F({"a", "b"})

    def test_a_named_dict_or_none_at_the_sink(self) -> None:
        """``analyze/base.py``'s ``meta=hint_meta or None``."""
        assert _keys(
            'def make(x):\n    hint = {"callee_name": x}\n'
            '    if x:\n        hint["enclosing_class"] = x\n'
            '    return sink(meta=hint or None)',
        ) == F({"callee_name", "enclosing_class"})

    def test_a_dict_forwarded_through_a_private_helper(self) -> None:
        """The helper's own writes to its parameter are still collected."""
        assert _keys(
            'def _mk(meta):\n    meta["b"] = 1\n    return sink(meta=meta)\n'
            'D = {"a": 1}\n_mk(D)',
        ) == F({"a", "b"})

    def test_another_opaque_use_still_escapes(self) -> None:
        assert _keys('D = {"a": 1}\nfill(D)\nsink(meta=D)') is None

    def test_a_dict_a_public_helper_receives_is_still_unresolved(self) -> None:
        assert _keys(
            'def mk(meta):\n    return sink(meta=meta)\nD = {"a": 1}\nmk(D)',
        ) is None


class TestReaders:
    """WI-lijaz: callees the ASKER declares read-only for this question.

    ``lua_ffi`` binds ``edge_meta`` twice in one function and hands each to
    its own ``Edge.create``; ``route_handler`` passes ``handler_meta`` in both
    arms of a ternary. Each use is the other's opaque escape, so neither
    resolves -- unless the gate, which scans ``edge.meta[...]`` writes as
    sites of their own, says that ``Edge.create`` adds no key to what it is
    given.
    """

    TWO_SINKS = 'D = {"a": 1}\nsink(meta=D)\nreader(meta=D)\n'

    def test_a_second_use_at_an_undeclared_callee_escapes(self) -> None:
        assert _keys(self.TWO_SINKS) is None

    def test_a_second_use_at_a_declared_reader_does_not(self) -> None:
        assert _keys(self.TWO_SINKS, F({"reader"})) == F({"a"})

    def test_a_dotted_reader(self) -> None:
        assert _keys(
            'D = {"a": 1}\nsink(meta=D)\nEdge.create(meta=D)\n',
            F({"Edge.create"}),
        ) == F({"a"})

    def test_a_positional_argument_to_a_reader(self) -> None:
        assert _keys(
            'D = {"a": 1}\nsink(meta=D)\nreader(D)\n', F({"reader"}),
        ) == F({"a"})

    def test_both_arms_of_a_ternary_at_the_reader(self) -> None:
        source = (
            'D = {"a": 1}\nD["b"] = 2\n'
            'sink(meta={**D, "c": True} if f else D)\n'
        )
        assert _keys(source) is None
        assert _keys(source, F({"sink"})) == F({"a", "b", "c"})

    def test_an_or_at_the_reader(self) -> None:
        assert _keys(
            'D = {"a": 1}\nsink(meta=D)\nreader(meta=D or None)\n',
            F({"reader"}),
        ) == F({"a"})

    def test_a_reader_that_is_an_attribute_of_an_expression_is_not_matched(
        self,
    ) -> None:
        assert _keys(
            'D = {"a": 1}\nsink(meta=D)\nmake().create(meta=D)\n',
            F({"Edge.create"}),
        ) is None

    def test_a_class_keyword_is_not_a_reader_argument(self) -> None:
        assert _keys(
            'D = {"a": 1}\nsink(meta=D)\nclass C(Base, reader=D):\n    pass\n',
            F({"reader"}),
        ) is None

    def test_a_class_base_is_not_a_reader_argument(self) -> None:
        assert _keys(
            'D = {"a": 1}\nsink(meta=D)\nclass C(D):\n    pass\n',
            F({"reader"}),
        ) is None

    def test_answers_are_kept_apart_per_reader_set(self) -> None:
        tree = ast.parse(self.TWO_SINKS)
        value = next(
            kw.value for n in ast.walk(tree) if isinstance(n, ast.Call)
            for kw in n.keywords if getattr(n.func, "id", "") == "sink"
        )
        flow = ModuleValueFlow(tree)
        key = (("key",),)
        assert flow.resolve(value, key) is None
        assert flow.resolve(value, key, readers=F({"reader"})) == F({"a"})
        assert flow.resolve(value, key) is None


def _reads_meta(expr: ast.expr) -> bool:
    return isinstance(expr, ast.Attribute) and expr.attr == "meta"


class TestKnownEmpty:
    """WI-lijaz: expressions the ASKER vouches add nothing to the answer.

    ``meta = dict(edge.meta or {}); meta["call_arg_shape"] = s; edge.meta =
    meta`` writes one key. The copy holds only keys some other site already
    wrote, which the meta-key gate checks there; ``value_flow`` cannot know
    that, so the gate says it.
    """

    COPY = 'D = dict(x.meta or {})\nD["k"] = 1\nsink(meta=D)\n'

    def test_unvouched_the_copy_is_unresolvable(self) -> None:
        assert _keys(self.COPY) is None

    def test_vouched_the_copy_adds_nothing(self) -> None:
        assert _keys(self.COPY, known_empty=_reads_meta) == F({"k"})

    def test_a_copy_extended_by_update(self) -> None:
        assert _keys(
            'D = dict(a.meta or {})\nD.update(b.meta or {})\nsink(meta=D or None)\n',
            known_empty=_reads_meta,
        ) == F()

    def test_a_spread_and_a_copy_method(self) -> None:
        assert _keys(
            'sink(meta={**(x.meta or {}), "k": 1, **x.meta.copy()})\n',
            known_empty=_reads_meta,
        ) == F({"k"})

    def test_answers_are_kept_apart_per_vouch(self) -> None:
        tree = ast.parse(self.COPY)
        value = next(
            kw.value for n in ast.walk(tree) if isinstance(n, ast.Call)
            for kw in n.keywords if getattr(n.func, "id", "") == "sink"
        )
        flow = ModuleValueFlow(tree)
        key = (("key",),)
        assert flow.resolve(value, key) is None
        assert flow.resolve(value, key, known_empty=_reads_meta) == F({"k"})
        assert flow.resolve(value, key) is None


class TestDictOfAMapping:
    """``dict(m)`` has ``m``'s keys and values (WI-lijaz)."""

    def test_keys(self) -> None:
        assert _keys('M = {"a": 1}\nsink(meta=dict(M))\n') == F({"a"})

    def test_values(self) -> None:
        assert _resolve('M = {"k": "v"}\nsink(dict(M)["k"])') == F({"v"})

    def test_two_arguments_refused(self) -> None:
        assert _keys('M = {"a": 1}\nsink(meta=dict(M, N))\n') is None
