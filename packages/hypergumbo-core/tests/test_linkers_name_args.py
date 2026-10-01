# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the shared pub/sub name-argument resolver (WI-misod)."""
from __future__ import annotations

import re
from dataclasses import dataclass

import pytest

from hypergumbo_core.linkers._name_args import (
    KIND_CONSTANT,
    KIND_LITERAL,
    KIND_UNRESOLVED,
    KIND_VARIABLE,
    NAME_ARG_RE,
    ConstantResolver,
    NameArg,
    NameJoin,
    name_arg,
    name_arg_from_match,
    pair_by_name,
)


# (language, source, identifier, expected value or None)
RESOLVE_CASES = [
    # Python: module-scope string constants resolve.
    ("python", 'EVENTS_TOPIC = "orders"\nproducer.produce(EVENTS_TOPIC, m)\n',
     "EVENTS_TOPIC", "orders"),
    ("python", 'T: str = "o"  # comment\n', "T", "o"),
    ("python", 'T = "o"\nproducer.produce(T, value=x)\n', "T", "o"),
    # Python: any other binding of the name leaves it unresolved.
    ("python", 'T = "o"\ndef f():\n    T = "p"\n', "T", None),
    ("python", 'T = "o"\ndef f(T):\n    send(T)\n', "T", None),
    ("python", 'from cfg import T\nT = "o"\n', "T", None),
    ("python", 'T = "o"\ndef f():\n    global T\n', "T", None),
    ("python", 'T = "o"\nfor T in xs:\n    pass\n', "T", None),
    ("python", 'T = "o"\nwith open(p) as T:\n    pass\n', "T", None),
    ("python", 'T = "o"\nif (T := g()):\n    pass\n', "T", None),
    ("python", 'T = "o"\nh = lambda T: T\n', "T", None),
    ("python", 'T = "o"\nT = "p"\n', "T", None),
    ("python", 'T = os.environ["X"]\n', "T", None),
    ("python", 'T = f"o{x}"\n', "T", None),
    ("python", 'def f():\n    T = "o"\n', "T", None),
    # JavaScript / TypeScript.
    ("javascript", "const CH = 'open-file';\nipcRenderer.send(CH, {});\n", "CH", "open-file"),
    ("typescript", 'export const CH: string = "a";\n', "CH", "a"),
    ("javascript", "const CH = `a`;\n", "CH", "a"),
    ("javascript", "const CH = 'a';\nif (x === CH) { y() }\nipcMain.on(CH, (e) => {});\n",
     "CH", "a"),
    ("javascript", "let CH = 'a';\nCH = 'b';\n", "CH", None),
    ("javascript", "const CH = 'a';\nfunction f(CH) { send(CH) }\n", "CH", None),
    ("javascript", "const CH = 'a';\nconst g = (CH) => send(CH);\n", "CH", None),
    ("javascript", "const CH = 'a';\nconst g = CH => send(CH);\n", "CH", None),
    ("javascript", "const CH = 'a';\nclass K { send(CH, d) { x(CH) } }\n", "CH", None),
    ("javascript", "const CH = 'a';\ntry { f() } catch (CH) { g() }\n", "CH", None),
    ("javascript", "const CH = 'a';\nimport { CH as X } from './c';\n", "CH", None),
    ("javascript", "const CH = 'a';\nconst { CH: y } = obj;\n", "CH", None),
    ("javascript", "const CH = `a-${b}`;\n", "CH", None),
    ("javascript", "function f() {\n  const CH = 'a';\n}\n", "CH", None),
    # Java: static final String fields.
    ("java", 'class P {\n  private static final String TOPIC = "events";\n'
             '  void s() { kafkaTemplate.send(TOPIC, m); }\n'
             '  String g() { return TOPIC; }\n}\n', "TOPIC", "events"),
    ("java", 'class P {\n  static final String TOPIC = "events";\n'
             '  void s(String TOPIC) {}\n}\n', "TOPIC", None),
    ("java", 'class P {\n  String TOPIC = "events";\n}\n', "TOPIC", None),
    # Go: top-level const and const blocks (typed, raw strings).
    ("go", 'package x\nconst UserCreated = "user.created"\n', "UserCreated", "user.created"),
    ("go", 'package x\nconst (\n\tA Event = "a"\n\tB = `b`\n)\n', "A", "a"),
    ("go", 'package x\nconst (\n\tA Event = "a"\n\tB = `b`\n)\n', "B", "b"),
    ("go", 'package x\nconst A = "a"\nfunc f(A string) {}\n', "A", None),
    ("go", 'package x\nconst A = "a"\nfunc f() { x, A := g() }\n', "A", None),
    # Never resolved: dotted references, unknown languages.
    ("python", 'cfg = "x"\n', "cfg.topic", None),
    ("ruby", 'T = "x"\n', "T", None),
]


@pytest.mark.parametrize(("language", "source", "identifier", "expected"), RESOLVE_CASES)
def test_constant_resolver(language: str, source: str, identifier: str, expected: str | None):
    assert ConstantResolver(source, language).resolve(identifier) == expected


def test_constant_resolver_caches_per_name():
    resolver = ConstantResolver('T = "o"\n', "python")
    assert resolver.resolve("T") == "o"
    assert resolver.resolve("T") == "o"
    assert resolver.resolve("U") is None


def test_name_arg_kinds():
    resolver = ConstantResolver('T = "orders"\n', "python")
    assert name_arg("orders", None, resolver) == NameArg("orders", KIND_LITERAL)
    assert name_arg(None, "T", resolver) == NameArg("orders", KIND_CONSTANT, "T")
    assert name_arg(None, "cfg.topic", resolver) == NameArg(
        "cfg.topic", KIND_UNRESOLVED, "cfg.topic",
    )
    assert name_arg(None, "T", None) == NameArg("T", KIND_UNRESOLVED, "T")


def test_unresolved_value_is_none_not_the_identifier():
    """THE item: an unresolved identifier is never reported as the value."""
    arg = name_arg(None, "EVENTS_TOPIC", ConstantResolver("", "python"))
    assert arg.value is None
    assert arg.identifier == "EVENTS_TOPIC"
    assert NameArg("ch", KIND_VARIABLE, "ch").value is None
    assert NameArg("x", KIND_LITERAL).value == "x"


def test_name_arg_from_match_reads_both_groups():
    pattern = re.compile(rf"send\(\s*{NAME_ARG_RE}")
    resolver = ConstantResolver('T = "o"\n', "python")
    lit = pattern.search("send('a')")
    ident = pattern.search("send(T)")
    assert lit is not None and ident is not None
    assert name_arg_from_match(lit, 1, 2, resolver) == NameArg("a", KIND_LITERAL)
    assert name_arg_from_match(ident, 1, 2, resolver) == NameArg("o", KIND_CONSTANT, "T")


@dataclass
class _Site:
    arg: NameArg
    scope: str = "s"


def _pairs(senders, receivers, **kw):
    return [
        (s, r, j) for s, r, j in pair_by_name(
            senders, receivers, lambda x: x.arg, lambda x: x.arg, **kw,
        )
    ]


def test_pair_value_join_literal_and_constant():
    lit = _Site(NameArg("orders", KIND_LITERAL))
    const = _Site(NameArg("orders", KIND_CONSTANT, "T"))
    [(_, _, join)] = _pairs([const], [lit])
    assert join == NameJoin(KIND_CONSTANT, "orders", None)
    assert join.on_value and join.channel == "orders"
    [(_, _, join)] = _pairs([lit], [_Site(NameArg("orders", KIND_LITERAL))])
    assert join.kind == KIND_LITERAL


def test_pair_identifier_text_never_joins_a_string():
    """An unresolved identifier `orders` does not join the literal 'orders'."""
    unresolved = _Site(NameArg("orders", KIND_UNRESOLVED, "orders"))
    lit = _Site(NameArg("orders", KIND_LITERAL))
    assert _pairs([unresolved], [lit]) == []
    assert _pairs([lit], [unresolved]) == []


def test_pair_identifier_join_needs_an_unknown_side():
    a = _Site(NameArg("T", KIND_UNRESOLVED, "T"))
    b = _Site(NameArg("T", KIND_UNRESOLVED, "T"))
    [(_, _, join)] = _pairs([a], [b])
    assert join == NameJoin(KIND_UNRESOLVED, None, "T")
    assert not join.on_value and join.channel is None
    # A resolved constant still reaches an unresolved use of the same name
    # (the constant imported elsewhere) -- on the identifier, value unasserted.
    const = _Site(NameArg("orders", KIND_CONSTANT, "T"))
    [(_, _, join)] = _pairs([const], [b])
    assert join.kind == KIND_UNRESOLVED and join.channel is None
    # Two constants with the same name but different values do not join.
    other = _Site(NameArg("payments", KIND_CONSTANT, "T"))
    assert _pairs([const], [other]) == []
    # Two constants with the same name and value join once, on the value.
    same = _Site(NameArg("orders", KIND_CONSTANT, "T"))
    [(_, _, join)] = _pairs([const], [same])
    assert join.on_value


def test_pair_variable_identity_join_asserts_the_identifier():
    a = _Site(NameArg("post_save", KIND_VARIABLE, "post_save"))
    b = _Site(NameArg("post_save", KIND_VARIABLE, "post_save"))
    [(_, _, join)] = _pairs([a], [b])
    assert join == NameJoin(KIND_VARIABLE, None, "post_save")
    assert join.channel == "post_save"


def test_pair_scope_and_casefold():
    a = _Site(NameArg("Orders", KIND_LITERAL), scope="kafka")
    b = _Site(NameArg("orders", KIND_LITERAL), scope="kafka")
    c = _Site(NameArg("Orders", KIND_LITERAL), scope="redis")
    assert _pairs([a], [b, c], scope=lambda x: x.scope) == []
    joined = _pairs([a], [b, c], scope=lambda x: x.scope, casefold=True)
    assert [r for _, r, _ in joined] == [b]
    assert len(_pairs([a], [b, c])) == 1
    assert len(_pairs([a], [b, c], casefold=True)) == 2
