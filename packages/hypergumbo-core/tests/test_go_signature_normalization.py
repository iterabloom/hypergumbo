# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py
"""The Go normalizer hashes every type; its deliberate rewrites are kept.

``normalize_signature_go`` took a parameter's type to be its LAST whitespace
token, so ``chan int`` hashed as ``int``, ``func(int) error`` as ``error``
and ``struct{ a int }`` as ``}``; and ``strip_fqn_prefix`` cut a type with
no ``[`` at its last dot, so ``chan pkg.T`` hashed as ``T`` in a result too.
Two signatures that differ only there shared one hashed text.

The deliberate rewrites are kept, and pinned below: parameter names dropped,
one leading ``*`` stripped (``*T`` hashes as ``T``), a package qualifier at
the head of a type stripped (``*http.Request`` -> ``Request``). A signature
whose old text lost nothing hashes exactly as before.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.analyze.base import normalize_signature_go

# Signature -> its hashed text now. Each lost a type before (the old text is
# in the comment).
FILLED = {
    "(c chan int)": "(chan int)",  # (int)
    "(c <-chan int)": "(<-chan int)",  # (int)
    "(c chan<- int)": "(chan<- int)",  # (int)
    "(c <-chan int, d string)": "(<-chan int,string)",  # (string): one part
    "(cb func(int) error)": "(func(int) error)",  # (error)
    "(func(int) error)": "(func(int) error)",  # (error): unnamed
    "(func(pkg.T))": "(func(pkg.T))",  # (T))
    "(x struct{ a int })": "(struct{ a int })",  # (})
    "(x struct{ a, b int })": "(struct{ a, b int })",  # (a,})
    '(x struct{ A int "json:\\"a,b\\"" })': '(struct{ A int "json:\\"a,b\\"" })',
    "(x interface{ A() })": "(interface{ A() })",  # (})
    "(x map[string] int)": "(map[string] int)",  # (int)
    "(x []chan int)": "([]chan int)",  # (int)
    "(x *struct{ a int })": "(struct{ a int })",  # (}): one * off
    "(x pkg.List[int, string])": "(List[int, string])",  # (string])
    "(x **pkg.T)": "(*pkg.T)",  # (T): only ONE * is deliberate
    "(xs ...int)": "(...int)",  # (int)
    "(xs ...*pkg.T)": "(...T)",  # (T)
    "(...pkg.List[int, string])": "(...List[int, string])",
    # A function type's parameter names are not hashed (stable-id v17).
    "(cb func(\n\ta int,\n) error)": "(func( int, ) error)",  # (error)
    "() chan pkg.T": "()chan pkg.T",  # ()T
    "() func(x pkg.A) error": "()func(pkg.A) error",  # ()A) error
    "() interface{ M() pkg.T }": "()interface{ M() pkg.T }",  # ()T }
    "() **pkg.T": "()*pkg.T",  # ()T
    "() (chan pkg.T, error)": "()(chan pkg.T,error)",  # ()(T,error)
    "() (ch chan pkg.T, err error)": "()(ch chan pkg.T,err error)",  # ()(T,err error)
    "() (fn func(a.B), err error)": "()(fn func(a.B),err error)",  # ()(B),err error)
}

# Signatures whose old text lost nothing: the hashed text is unchanged,
# including the deliberate and historical rewrites.
UNCHANGED = {
    "(name string, age int) error": "(string,int)error",
    "(x *T)": "(T)",  # one leading * stripped
    "(x **T)": "(*T)",
    "(*Request)": "(Request)",
    "(r *http.Request) error": "(Request)error",  # qualifier stripped
    "(x pkg.List[int])": "(List[int])",
    "(x []*pkg.T)": "([]*pkg.T)",  # an inner qualifier is kept
    "(m map[string]*S)": "(map[string]*S)",
    "(x [4]byte, f func(int))": "([4]byte,func(int))",
    "(x interface{}, y struct{})": "(interface{},struct{})",
    "(a, b int)": "(a,int)",  # grouped: the first name is hashed
    "(int, string)": "(int,string)",
    "(x int,,y string) error": "(int,string)error",
    "() *Response": "()Response",
    "() *http.Response": "()Response",
    "() chan int": "()chan int",
    "() <-chan pkg.T": "()<-chan pkg.T",
    "() func(int) error": "()func(int) error",
    "() []pkg.T": "()[]pkg.T",
    "() pkg.List[int]": "()List[int]",
    "() (int, error)": "()(int,error)",
    "() (n int, err error)": "()(n int,err error)",  # tuple names kept
    "() (r *http.Response, err error)": "()(Response,err error)",
    "() (**T, error)": "()(T,error)",  # every leading * of an element
    "() (x, y int)": "()(x,y int)",
    "() (struct{ a, b int }, error)": "()(struct{ a,b int },error)",
    "() (<-chan int, error)": "()(<-chan int, error)",
}


@pytest.mark.parametrize(("signature", "hashed"), FILLED.items())
def test_every_type_is_hashed(signature: str, hashed: str) -> None:
    assert normalize_signature_go(signature) == hashed


@pytest.mark.parametrize(("signature", "hashed"), UNCHANGED.items())
def test_a_signature_with_no_hole_hashes_as_before(signature: str, hashed: str) -> None:
    assert normalize_signature_go(signature) == hashed


@pytest.mark.parametrize(("left", "right"), [
    ("(c chan int)", "(c int)"),
    ("(c <-chan int)", "(c chan<- int)"),
    ("(cb func(int) error)", "(cb func(string) error)"),
    ("(x struct{ a int })", "(x struct{ b string })"),
    ("(x interface{ A() })", "(x interface{ B() })"),
    ("(xs ...int)", "(x int)"),
    ("() chan pkg.T", "() pkg.T"),
])
def test_two_signatures_that_differ_inside_a_hole_hash_apart(left: str, right: str) -> None:
    assert normalize_signature_go(left) != normalize_signature_go(right)


def test_type_params_still_rename_by_position() -> None:
    assert normalize_signature_go("(x T, c chan T) T", ["T"]) == "($0,chan $0)$0"
    assert normalize_signature_go("(xs ...T) (T, error)", ["T"]) == "(...$0)($0,error)"
    assert normalize_signature_go("() (c chan T, err error)", ["T"]) == "()(c chan $0,err error)"
    # ``a.T`` is package a's type T, not the type parameter: not renamed.
    assert normalize_signature_go("() (fn func(a.T), err error)", ["T"]) == "()(fn func(a.T),err error)"
