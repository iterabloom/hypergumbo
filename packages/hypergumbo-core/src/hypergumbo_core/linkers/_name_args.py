# SPDX-License-Identifier: AGPL-3.0-or-later
"""Infrastructure linker helper: resolve a pub/sub NAME argument to its value.

The message-queue, IPC and event-sourcing linkers all join a sending site to a
receiving site on a NAME -- a topic, a channel, an event. At the call site that
name is written either as a string literal (``producer.send("orders", m)``) or
as an identifier (``producer.send(ORDERS_TOPIC, m)``). Before WI-misod each
linker stored the identifier's TEXT in the slot that holds the name and joined
on it, so ``ORDERS_TOPIC`` was treated as if it were the topic. Two failures
followed from one mechanism:

* a publisher whose topic is ``ORDERS_TOPIC = "orders"`` never joined the
  subscriber that wrote ``"orders"`` (a missed edge), and
* an identifier whose text happened to equal another site's string joined it
  (event-sourcing folds case, so ``emit(CREATED)`` with
  ``CREATED = "user:created"`` was joined to ``on("created")`` and not to
  ``on("user:created")`` -- a wrong edge in place of the right one).

How it works
------------
:class:`ConstantResolver` resolves an identifier to the string a SAME-FILE,
MODULE-SCOPE constant binds it to (Python / JS / TS top-level assignment, Java
``static final String`` field, Go ``const``). A name resolves only when its one
module-scope definition binds a plain string literal AND the file binds the
name nowhere else -- no second assignment, parameter, loop target, import,
``global`` declaration, local shadow. Any of those makes the reference at the
call site ambiguous, so the name stays unresolved. The checks are regexes over
the masked source, not a scope analysis: they over-approximate binding sites on
purpose, so the failure direction is "left unresolved", never "resolved to the
wrong string". Dotted references (``config.topic``, ``Topics.ORDERS``) and
names bound in another file (``from settings import ORDERS_TOPIC``) are NOT
resolved: they stay ``unresolved``.

:func:`name_arg` turns a regex match's literal / identifier groups into a
:class:`NameArg` whose ``kind`` says how the name is known:

``literal``     written as a string at the site; ``value`` is that string.
``constant``    written as an identifier that resolved; ``value`` is the bound
                string and ``identifier`` the name written at the site.
``unresolved``  written as an identifier that did not resolve; ``value`` is
                ``None`` -- the name is NOT known -- and ``identifier`` keeps the
                text written at the site.
``variable``    (event-sourcing only) the identifier IS the channel's identity
                -- a Django signal object, a Go channel variable -- so no string
                value exists to resolve. ``value`` is ``None``.

:func:`pair_by_name` joins senders to receivers. Sites whose value is known
join on the VALUE. Sites join on the IDENTIFIER only when at least one side's
value is unknown (``unresolved`` / ``variable``) -- the long-standing
"both sides reference the same name" heuristic, kept so a constant imported
from another file still reaches its counterpart, but kept in its own namespace
so an identifier's text is never compared with another site's string. Each
pair carries a :class:`NameJoin` whose ``kind`` labels the JOIN (``literal``,
``constant``, ``unresolved``, ``variable``); a site's own ``kind`` is a
separate, per-site fact.

Why one module
--------------
The three linkers carried three copies of the same ``(literal, identifier)``
extraction and the same raw-string join. Fixing one copy would have left the
other two storing identifiers; the shared resolver and join make the three
agree by construction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Hashable, Iterable, Iterator, TypeVar

#: Site kinds (see the module docstring).
KIND_LITERAL = "literal"
KIND_CONSTANT = "constant"
KIND_UNRESOLVED = "unresolved"
KIND_VARIABLE = "variable"

#: Kinds whose ``value`` is a known string -- the ones that join on VALUE.
KNOWN_VALUE_CLASSES = frozenset({KIND_LITERAL, KIND_CONSTANT})

#: An identifier: a bare name, or a dotted attribute chain (``config.topic``).
IDENTIFIER_RE = r"[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*"

#: A name argument: a quoted string (group 1) OR an identifier (group 2).
NAME_ARG_RE = rf"(?:['\"]([^'\"]+)['\"]|({IDENTIFIER_RE}))"

_BARE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class NameArg:
    """How a site names its topic / channel / event."""

    label: str  # value when known, else the identifier written at the site
    kind: str  # literal | constant | unresolved | variable
    identifier: str | None = None  # the identifier written at the site, if any

    @property
    def value(self) -> str | None:
        """The name's string value, or ``None`` when it is not known."""
        return self.label if self.kind in KNOWN_VALUE_CLASSES else None


@dataclass(frozen=True)
class NameJoin:
    """Why a sender was joined to a receiver."""

    kind: str  # literal | constant (joined on value); unresolved | variable (on identifier)
    value: str | None  # the shared value; None for an identifier join
    identifier: str | None  # the shared identifier; None for a value join

    @property
    def on_value(self) -> bool:
        """True when the join compared values, False when it compared identifiers."""
        return self.value is not None

    @property
    def channel(self) -> str | None:
        """The channel name an edge may assert.

        A value join asserts its value. An identifier join asserts the
        identifier only when it is a channel's identity on both sides
        (``variable``); when either side is ``unresolved`` the value is
        unknown, so nothing is asserted.
        """
        if self.value is not None:
            return self.value
        return self.identifier if self.kind == KIND_VARIABLE else None


# ---------------------------------------------------------------------------
# Same-file constant resolution
# ---------------------------------------------------------------------------

_PY_STR = r"""[uU]?(?P<q>['"])(?P<v>[^'"\\\n]*)(?P=q)"""
_JS_STR = r"""(?P<q>['"`])(?P<v>[^'"`\\\n$]*)(?P=q)"""
_DQ_STR = r'''"(?P<v>[^"\\\n]*)"'''
_GO_STR = r'''(?:"(?P<v>[^"\\\n]*)"|`(?P<r>[^`\n]*)`)'''


def _py_definition(n: str) -> str:
    return rf"^{n}\s*(?::[^=\n]+)?=\s*{_PY_STR}\s*(?:#[^\n]*)?$"


def _js_definition(n: str) -> str:
    return (
        rf"^(?:export\s+)?(?:const|let|var)\s+{n}\s*(?::[^=\n]+)?="
        rf"\s*{_JS_STR}\s*;?\s*(?://[^\n]*)?$"
    )


def _java_definition(n: str) -> str:
    return (
        rf"(?:\b(?:public|private|protected)\s+)?"
        rf"\b(?:static\s+final|final\s+static)\s+String\s+{n}\s*=\s*{_DQ_STR}\s*;"
    )


def _go_definition(n: str) -> str:
    return rf"^const\s+{n}(?:\s+[\w.]+)?\s*=\s*{_GO_STR}"


def _go_block_definition(n: str) -> str:
    return rf"^[ \t]+{n}(?:\s+[\w.]+)?\s*=\s*{_GO_STR}"


# Binding sites OTHER than the definition. Each is searched in the file with
# the definition's own span blanked; one hit leaves the name unresolved.
_PY_BINDERS: tuple[str, ...] = (
    r"^[ \t]*{n}\s*(?::[^=\n]+)?(?:[-+*/%|&^@]|//|\*\*|<<|>>)?=(?!=)",
    r"(?<![\w.]){n}\s*:=",
    r"\bdef\s+\w+\s*\([^)]*\b{n}\b",
    r"\blambda\b[^:\n]*\b{n}\b",
    r"\bfor\s+[^\n:]*\b{n}\b[^\n:]*\bin\b",
    r"\bas\s+{n}\b",
    r"\bimport\b[^\n]*\b{n}\b",
    r"\b(?:global|nonlocal)\b[^\n]*\b{n}\b",
)
_JS_BINDERS: tuple[str, ...] = (
    r"\b(?:const|let|var)\s+{n}\b",
    r"\b(?:const|let|var)\s*[{{\[][^=;]*\b{n}\b",
    r"(?<![\w$.]){n}\s*(?:[-+*/%|&^]|\*\*|<<|>>>?|\?\?|&&|\|\|)?=(?![=>])",
    r"\bfunction\b[^(]*\([^)]*\b{n}\b",
    r"\([^()]*\b{n}\b[^()]*\)\s*(?::[^=;{{]*)?=>",
    r"(?<![\w$.]){n}\s*=>",
    r"(?<![\w$.])(?!(?:if|for|while|switch|with|return)\b)[A-Za-z_$][\w$]*"
    r"\s*\([^()]*\b{n}\b[^()]*\)\s*(?::[^{{;]*)?\{{",
    r"\bcatch\s*\(\s*{n}\b",
    r"\bimport\b[^;\n]*\b{n}\b",
)
_JAVA_BINDERS: tuple[str, ...] = (
    r"(?<![\w.]){n}\s*(?:[-+*/%|&^]|<<|>>>?)?=(?!=)",
    r"(?<![\w.])(?!(?:return|new|throw|case|else|instanceof)\b)"
    r"[A-Za-z_][\w.]*(?:<[^<>;]*>)?(?:\[\])*\s+{n}\s*[,)=;:]",
)
_GO_BINDERS: tuple[str, ...] = (
    r"(?<![\w.]){n}\s*(?:,[\w\s,]*)?:?=(?!=)",
    r"(?<![\w.]){n}\s+[\w.*\[\]]+\s*[,)]",
    r"(?<![\w.])\w+\s*,\s*{n}\s*:?=(?!=)",
)


@dataclass(frozen=True)
class _LanguageRules:
    definitions: tuple[Callable[[str], str], ...]
    binders: tuple[str, ...]
    go_const_blocks: bool = False


_RULES: dict[str, _LanguageRules] = {
    "python": _LanguageRules((_py_definition,), _PY_BINDERS),
    "javascript": _LanguageRules((_js_definition,), _JS_BINDERS),
    "typescript": _LanguageRules((_js_definition,), _JS_BINDERS),
    "java": _LanguageRules((_java_definition,), _JAVA_BINDERS),
    "go": _LanguageRules((_go_definition,), _GO_BINDERS, go_const_blocks=True),
}

_GO_CONST_BLOCK = re.compile(r"^const\s*\((?P<body>.*?)^\)", re.MULTILINE | re.DOTALL)


def _string_value(m: re.Match[str]) -> str:
    """The string a definition match binds (Go raw strings use group ``r``)."""
    value = m.group("v")
    return value if value is not None else m.group("r")


class ConstantResolver:
    """Resolve identifiers to same-file module-scope string constants.

    One instance per file; results are cached per name. A language with no
    rules (or a dotted identifier) resolves nothing.
    """

    def __init__(self, content: str, language: str) -> None:
        self._content = content
        self._rules = _RULES.get(language)
        self._cache: dict[str, str | None] = {}

    def resolve(self, identifier: str) -> str | None:
        """Return the string *identifier* is bound to, or ``None``."""
        if identifier not in self._cache:
            self._cache[identifier] = self._resolve(identifier)
        return self._cache[identifier]

    def _definitions(self, n: str) -> list[tuple[int, int, str]]:
        assert self._rules is not None  # guarded by _resolve
        found: list[tuple[int, int, str]] = []
        for make in self._rules.definitions:
            for m in re.finditer(make(n), self._content, re.MULTILINE):
                found.append((m.start(), m.end(), _string_value(m)))
        if self._rules.go_const_blocks:
            for block in _GO_CONST_BLOCK.finditer(self._content):
                body_start = block.start("body")
                for m in re.finditer(_go_block_definition(n), block.group("body"), re.MULTILINE):
                    found.append((body_start + m.start(), body_start + m.end(), _string_value(m)))
        return found

    def _resolve(self, identifier: str) -> str | None:
        if self._rules is None or not _BARE_NAME.fullmatch(identifier):
            return None
        n = re.escape(identifier)
        definitions = self._definitions(n)
        if len(definitions) != 1:
            return None
        start, end, value = definitions[0]
        rest = self._content[:start] + " " * (end - start) + self._content[end:]
        for binder in self._rules.binders:
            if re.search(binder.format(n=n), rest, re.MULTILINE):
                return None
        return value


def name_arg(
    literal: str | None,
    identifier: str | None,
    resolver: ConstantResolver | None,
) -> NameArg:
    """Classify a site's name from its literal / identifier capture groups."""
    if literal:
        return NameArg(literal, KIND_LITERAL)
    if not identifier:  # pragma: no cover - every NAME_ARG_RE match fills one group
        return NameArg("", KIND_UNRESOLVED)
    value = resolver.resolve(identifier) if resolver is not None else None
    if value is not None:
        return NameArg(value, KIND_CONSTANT, identifier)
    return NameArg(identifier, KIND_UNRESOLVED, identifier)


def name_arg_from_match(
    match: re.Match[str],
    literal_group: int,
    identifier_group: int,
    resolver: ConstantResolver | None,
) -> NameArg:
    """:func:`name_arg` over a regex match's two capture groups."""
    return name_arg(match.group(literal_group), match.group(identifier_group), resolver)


# ---------------------------------------------------------------------------
# Joining senders to receivers
# ---------------------------------------------------------------------------

S = TypeVar("S")
R = TypeVar("R")


def _join_kind(a: NameArg, b: NameArg, on_value: bool) -> str:
    if on_value:
        if a.kind == KIND_LITERAL and b.kind == KIND_LITERAL:
            return KIND_LITERAL
        return KIND_CONSTANT
    if a.kind == KIND_VARIABLE and b.kind == KIND_VARIABLE:
        return KIND_VARIABLE
    return KIND_UNRESOLVED


def pair_by_name(
    senders: Iterable[S],
    receivers: Iterable[R],
    sender_arg: Callable[[S], NameArg],
    receiver_arg: Callable[[R], NameArg],
    scope: Callable[[S | R], Hashable] = lambda _site: None,
    casefold: bool = False,
) -> Iterator[tuple[S, R, NameJoin]]:
    """Yield ``(sender, receiver, join)`` for every pair that names the same channel.

    ``scope`` partitions sites that may never join (the message-queue family);
    ``casefold`` compares names case-insensitively (event-sourcing's rule).
    A pair joins on VALUE when both values are known and equal, else on
    IDENTIFIER when the identifiers are equal and at least one value is
    unknown. A pair is yielded at most once.
    """

    def norm(text: str) -> str:
        return text.lower() if casefold else text

    by_value: dict[tuple[Hashable, str], list[R]] = {}
    by_identifier: dict[tuple[Hashable, str], list[R]] = {}
    for receiver in receivers:
        arg = receiver_arg(receiver)
        key_scope = scope(receiver)
        if arg.value is not None:
            by_value.setdefault((key_scope, norm(arg.value)), []).append(receiver)
        if arg.identifier is not None:
            by_identifier.setdefault((key_scope, norm(arg.identifier)), []).append(receiver)

    for sender in senders:
        arg = sender_arg(sender)
        key_scope = scope(sender)
        if arg.value is not None:
            for receiver in by_value.get((key_scope, norm(arg.value)), []):
                other = receiver_arg(receiver)
                assert other.value is not None  # indexed by value above
                yield sender, receiver, NameJoin(
                    _join_kind(arg, other, True), arg.value, None,
                )
        if arg.identifier is not None:
            for receiver in by_identifier.get((key_scope, norm(arg.identifier)), []):
                other = receiver_arg(receiver)
                if arg.value is not None and other.value is not None:
                    continue  # both known: joined (or rightly not) on value above
                yield sender, receiver, NameJoin(
                    _join_kind(arg, other, False), None, arg.identifier,
                )
