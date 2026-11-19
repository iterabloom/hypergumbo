# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every spelling of one Rust receiver hashes one text.

A method's receiver can be written short (``&self``, ``&mut self``,
``self``) or typed (``self: &Self``, ``self: &mut Self``, ``self: Self``),
with an elided lifetime (``&'_ self``) or a named one (``&'a self`` /
``self: &'a Self``), and a by-value receiver may bind mutably (``mut self``).
Until WI-haras the typed long forms and ``mut self`` hashed differently from
the short forms: ``self: &Self`` hashed ``&Self`` while ``&self`` was
skipped, and ``mut self`` hashed ``mut self`` while ``self`` hashed ``Self``.

``mut`` on the binding is not part of the signature (it says whether the
body may reassign its own copy; a caller sees the same ``fn(Self)``), so
``mut self`` hashes as ``self``. An elided lifetime ``'_`` is no lifetime.
A named lifetime is kept: it ties the receiver to other parameters or the
result. Each class hashes the text its commonest spelling already hashed
(``&self`` nothing, ``&mut self`` ``&mut Self``, ``self`` ``Self``, ``&'a
self`` ``&'a self``), so only the other spellings move. A smart-pointer
receiver (``Box<Self>``, ``Rc<Self>``, ``Pin<&mut Self>``) keeps its type.
"""
from __future__ import annotations

import pytest

from hypergumbo_lang_mainstream.rust import normalize_rust_signature

_CLASSES = {
    "shared": ["(&self, x: i32)", "(self: &Self, x: i32)", "(&'_ self, x: i32)",
               "(self: &'_ Self, x: i32)", "(& self, x: i32)", "(self : & Self, x: i32)"],
    "mutable": ["(&mut self, x: i32)", "(self: &mut Self, x: i32)", "(&'_ mut self, x: i32)",
                "(self: &'_ mut Self, x: i32)", "(&  mut  self, x: i32)", "(self : & mut Self, x: i32)"],
    "by-value": ["(self, x: i32)", "(mut self, x: i32)", "(self: Self, x: i32)",
                 "(mut self: Self, x: i32)"],
    "named-shared": ["(&'a self, x: i32)", "(self: &'a Self, x: i32)", "(& 'a self, x: i32)"],
    "named-mutable": ["(&'a mut self, x: i32)", "(self: &'a mut Self, x: i32)"],
    "boxed": ["(self: Box<Self>, x: i32)", "(mut self: Box<Self>, x: i32)"],
}

#: The text each class hashes: the one its commonest spelling hashed before.
_HASHED = {
    "shared": "(i32)",
    "mutable": "(&mut Self,i32)",
    "by-value": "(Self,i32)",
    "named-shared": "(&'a self,i32)",
    "named-mutable": "(&'a mut self,i32)",
    "boxed": "(Box<Self>,i32)",
}


@pytest.mark.parametrize("cls", sorted(_CLASSES))
def test_every_spelling_of_a_receiver_hashes_one_text(cls: str) -> None:
    for spelling in _CLASSES[cls]:
        assert normalize_rust_signature(spelling) == _HASHED[cls], spelling


def test_the_classes_hash_apart() -> None:
    assert len(set(_HASHED.values())) == len(_HASHED)


@pytest.mark.parametrize("signature", [
    "(self: Rc<Self>)", "(self: std::rc::Rc<Self>)", "(self: Arc<Self>)",
    "(self: Pin<&mut Self>)", "(self: Pin<Box<Self>>)", "(self: &Box<Self>)",
])
def test_a_smart_pointer_receiver_keeps_its_type(signature: str) -> None:
    assert normalize_rust_signature(signature) == f"({signature[7:-1]})"


@pytest.mark.parametrize(("signature", "hashed"), [
    ("(x: i32, y: &str) -> bool", "(i32,&str)bool"),
    ("()", "()"),
    ("(selfish: i32)", "(i32)"),
    ("(self_: &Self)", "(&Self)"),
])
def test_a_signature_without_a_receiver_is_unchanged(signature: str, hashed: str) -> None:
    assert normalize_rust_signature(signature) == hashed
