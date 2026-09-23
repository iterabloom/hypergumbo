# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical registry of I/O-primitive kinds — the io-primitive-kind axis (ADR-0059).

THE AXIOM (INV-zikab, owner-ratified 2026-09-23):

    A row's kind says how the primitive is reached from the row's own
    ``module``: ``method`` iff it is called on an INSTANCE of ``module``;
    ``function`` iff it is called on ``module`` itself -- a namespace, package,
    type, companion object or named global -- or has no owner; ``attribute``
    iff it is read on ``module``, not called.

The decision procedure a catalogue author applies, from the language's own
documentation and without running any analyzer: if ``module.name(...)`` in
the language's own qualifier syntax (``module(...)`` for a constructor) is a
valid call as written, the row is a function; if a value of type ``module``
must precede ``.name``, it is a method; if it is read rather than called, it is
an attribute. If none of the three holds, the ``module`` string is wrong.

WHY THIS MODULE EXISTS. The value comes only from the YAML section a row sits
under (``functions:`` / ``methods:`` / ``attributes:``), and until this axis
nothing said what those sections assert. The catalogues' own notes stated
three different rules -- what the callee IS (rust.yaml, INV-pimir), whether a
value supplies its owner (cpp, scala, python, WI-komun), and what the
language's ANALYZER stamps at the call site (java.yaml, kotlin.yaml) -- while
every consumer that branches on the value needs the second. The 2026-09-23
concept audit measured the cost on 21 surveys: JS ``process`` reported
"structurally invisible" on 6 of 9 JS-bearing repos on calls the classifier had
matched, the fourth recurrence of INV-fugus's contradiction (INV-dihun).

A PROPERTY OF THE PRIMITIVE, NEVER OF AN ANALYZER. The rule does not mention
what any analyzer emits, and that is deliberate: ``_CATALOG_PARENTS`` gives
kotlin and scala java's rows and typescript loads the javascript YAML, so a row
keyed to one analyzer's stamp is wrong for every analyzer that inherits it. The
axiom is also stated against the ROW's ``module``, not the call site, for the
reason ADR-0051 §1 gives for the sibling field: the module key "is not a
property of the call site".

THE CONSUMERS read the value through the three predicates below and never
through a string literal (:func:`find_kind_literal_drift` enforces it on the
consumer modules). Named for what the axiom says, the call sites now read as
what they assume -- which is the point: ``lookup_with_module``'s INV-nizom arm
now visibly reads "drop rows called on a named owner when the stamp says
method", the construct-as-receiver-evidence assumption INV-pimir records.

THE LEDGER. :data:`KNOWN_NONCONFORMING_ROWS` lists the shipped rows the axiom
rejects and that are NOT re-kinded yet, each naming the tracker item that
blocks it. It is SHRINK-ONLY: the property test fails on an entry whose row has
been fixed or removed (delete the entry) and pins the ledger's size, so a new
exception is a visible diff. It records non-conformance; it does not approve
it. Why each is blocked rather than simply fixed was MEASURED (INV-zikab):
re-kinding the java statics loses 41 classifications through the INV-nizom arm
until it stops reading a ``method`` stamp as receiver evidence; the swift
constructor rows carry mostly WRONG boundaries that their unreachable kind has
hidden (INV-gujoh), so re-kinding them first would mint false detections.

WHAT IS AND IS NOT CHECKED MECHANICALLY, stated so a green test is not read as
more than it is. Python: every method row on an importable class or object
owner (``test_associated_fn_kinds.py``, via ``inspect.getattr_static``). Swift:
a method row whose NAME is UpperCamelCase is a constructor and must be in the
ledger. Every other language is checked only through the ledger -- a new
non-conforming java or objc row would pass the tests. No toolchain for those
languages is available to the test suite.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable


KIND_FUNCTION: Final[str] = "function"
KIND_METHOD: Final[str] = "method"
KIND_ATTRIBUTE: Final[str] = "attribute"


@dataclass(frozen=True)
class IoPrimitiveKindSpec:
    """One kind: its YAML section and what a row under it asserts."""

    name: str
    yaml_section: str
    called: bool
    description: str


# Declaration ORDER is load-bearing: ``_from_dict`` and taint's source/sink
# loaders emit rows section by section in this order, and several sites
# resolve a doubly-declared primitive by first-declared-wins.
IO_PRIMITIVE_KINDS: Final[tuple[IoPrimitiveKindSpec, ...]] = (
    IoPrimitiveKindSpec(
        name=KIND_FUNCTION,
        yaml_section="functions",
        called=True,
        description=(
            "Called on the row's module ITSELF -- a namespace, package, type, "
            "companion object or named global -- or with no owner. Covers "
            "free functions, statics, classmethods, associated functions, "
            "constructors, and members of named objects (`process.on`, "
            "`ctypes.cdll.LoadLibrary`)."
        ),
    ),
    IoPrimitiveKindSpec(
        name=KIND_METHOD,
        yaml_section="methods",
        called=True,
        description=(
            "Called on an INSTANCE of the row's module: the owner is a value "
            "whose type has to be inferred at the call site (`f.write(...)` "
            "with `f` a file object)."
        ),
    ),
    IoPrimitiveKindSpec(
        name=KIND_ATTRIBUTE,
        yaml_section="attributes",
        called=False,
        description=(
            "Read on the row's module, not called (`os.environ`, "
            "`System.out`, `process.env`). Pairs with `module_attr_ref` edges."
        ),
    ),
)

_BY_NAME: Final[dict[str, IoPrimitiveKindSpec]] = {s.name: s for s in IO_PRIMITIVE_KINDS}
_BY_SECTION: Final[dict[str, IoPrimitiveKindSpec]] = {
    s.yaml_section: s for s in IO_PRIMITIVE_KINDS
}

#: The YAML section keys a catalogue row may carry names under, in emission order.
YAML_SECTIONS: Final[tuple[str, ...]] = tuple(s.yaml_section for s in IO_PRIMITIVE_KINDS)


def all_io_primitive_kind_names() -> frozenset[str]:
    """Every legal ``IoPrimitive.kind`` (the ``_known_axes`` resolver)."""
    return frozenset(_BY_NAME)


def kind_for_yaml_section(section: str) -> str:
    """The kind a row under YAML ``section`` carries. Raises on an unknown key,
    so a new section cannot mint an unregistered kind silently."""
    return _BY_SECTION[section].name


def reached_through_an_instance(kind: str) -> bool:
    """True iff the primitive is called on an INSTANCE of its module, so a call
    site reaches it only through a value whose type must be inferred."""
    return kind == KIND_METHOD


def called_on_a_named_owner(kind: str) -> bool:
    """True iff the primitive is called on its module itself or has no owner, so
    naming the owner at the call site is enough to reach it."""
    return kind == KIND_FUNCTION


def read_not_called(kind: str) -> bool:
    """True iff the primitive is read, not called."""
    return kind == KIND_ATTRIBUTE


# ---------------------------------------------------------------------------
# The shrink-only ledger of rows the axiom rejects and that are not fixed yet.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NonconformingRow:
    """A shipped row that breaks the axiom, and what blocks fixing it.

    ``language`` is the catalogue the row is LOADED for (``load_catalog``,
    shipped overlays included); a java row inherited by kotlin and scala is
    listed once, under java.
    """

    language: str
    module: str
    name: str
    blocked_by: tuple[str, ...]


_JVM_STATICS_BLOCKERS: Final[tuple[str, ...]] = ("INV-pimir", "WI-kilap")


def _rows(language: str, module: str, names: Iterable[str],
          blocked_by: tuple[str, ...]) -> tuple[NonconformingRow, ...]:
    return tuple(NonconformingRow(language, module, n, blocked_by) for n in names)


KNOWN_NONCONFORMING_ROWS: Final[tuple[NonconformingRow, ...]] = (
    # JVM statics, called on the class (`Files.readAllBytes(p)`). Keyed methods
    # because java's analyzer stamps `method` on a qualified static and the
    # INV-nizom arm drops function rows under that stamp (-41 classifications
    # measured); the kotlin/scala qualifier drop (WI-kilap) is the other half.
    *_rows("java", "java.nio.file.Files", (
        "copy", "createDirectories", "createDirectory", "createFile",
        "createTempDirectory", "createTempFile", "delete", "deleteIfExists",
        "exists", "find", "getLastModifiedTime", "isDirectory",
        "isRegularFile", "list", "move", "readAllBytes", "readAllLines",
        "readString", "size", "walk", "write", "writeString",
    ), _JVM_STATICS_BLOCKERS),
    *_rows("java", "java.lang.System", (
        "currentTimeMillis", "getProperties", "getProperty", "getenv", "nanoTime",
    ), _JVM_STATICS_BLOCKERS),
    *(NonconformingRow("java", f"java.time.{c}", "now", _JVM_STATICS_BLOCKERS)
      for c in ("Instant", "LocalDate", "LocalDateTime", "LocalTime",
                "OffsetDateTime", "ZonedDateTime")),
    NonconformingRow("java", "java.time.Clock", "systemUTC", _JVM_STATICS_BLOCKERS),
    # scala `object` members, called on the object; the scala analyzer drops
    # the qualifier (WI-kilap), so today the method kind is what discloses them.
    *_rows("scala", "scala.util.Properties", (
        "envOrElse", "envOrNone", "javaHome", "propIsSet", "propOrElse",
        "propOrNone", "scalaHome", "tmpDir", "userDir", "userHome", "userName",
    ), ("WI-kilap",)),
    # Companion apply; the analyzer emits `Process(c)` under the name `Process`,
    # so the row is unreachable whatever its kind (WI-narij).
    NonconformingRow("scala", "scala.sys.process.Process", "apply", ("WI-narij",)),
    # swift constructors keyed methods: unreachable today, and most of their
    # boundaries are wrong (a constructor that builds a value is not I/O), so
    # the boundaries are adjudicated before any re-kind (INV-gujoh).
    *(NonconformingRow("swift", m, n, ("INV-gujoh",)) for m, n in (
        ("AsyncHTTPClient", "HTTPClientRequest"), ("ClientBootstrap", "ClientBootstrap"),
        ("CommandLine", "CommandLine"), ("EventLoopGroup", "MultiThreadedEventLoopGroup"),
        ("Logger", "Logger"), ("ModelContext", "ModelContext"),
        ("NIOAsyncChannel", "NIOAsyncChannel"), ("NIOSSL", "NIOSSLCertificate"),
        ("NIOSSL", "NIOSSLContext"), ("NIOSSL", "NIOSSLPrivateKey"),
        ("NIOWebSocketServerUpgrader", "NIOWebSocketServerUpgrader"),
        ("NSFetchRequest", "NSFetchRequest"), ("NWConnection", "NWConnection"),
        ("NWListener", "NWListener"), ("ServerBootstrap", "ServerBootstrap"),
        ("URLRequest", "URLRequest"),
    )),
    # No measured blocker; queued (WI-ziviv).
    *(NonconformingRow("swift", m, n, ("WI-ziviv",)) for m, n in (
        ("Date", "now"), ("DispatchTime", "now"), ("ProcessInfo", "processInfo"),
    )),
    *(NonconformingRow("objc", m, n, ("WI-ziviv",)) for m, n in (
        ("NSData", "dataWithContentsOfFile:"), ("NSData", "dataWithContentsOfFile:options:error:"),
        ("NSData", "dataWithContentsOfURL:"), ("NSData", "dataWithContentsOfURL:options:error:"),
        ("NSString", "stringWithContentsOfFile:encoding:error:"),
        ("NSString", "stringWithContentsOfURL:encoding:error:"),
        ("NSFileHandle", "fileHandleForReadingAtPath:"),
        ("NSFileHandle", "fileHandleForReadingFromURL:error:"),
        ("NSFileHandle", "fileHandleForUpdatingAtPath:"),
        ("NSFileHandle", "fileHandleForUpdatingURL:error:"),
        ("NSFileHandle", "fileHandleForWritingAtPath:"),
        ("NSFileHandle", "fileHandleForWritingToURL:error:"),
        ("NSInputStream", "inputStreamWithFileAtPath:"), ("NSInputStream", "inputStreamWithURL:"),
        ("NSOutputStream", "outputStreamToFileAtPath:append:"),
        ("NSMutableURLRequest", "requestWithURL:"),
        ("NSURLConnection", "connectionWithRequest:delegate:"),
        ("NSURLConnection", "sendAsynchronousRequest:queue:completionHandler:"),
        ("NSURLConnection", "sendSynchronousRequest:returningResponse:error:"),
        ("NSFetchRequest", "fetchRequestWithEntityName:"), ("NSDate", "date"),
    )),
    # Neither kind fits: no Kotlin value has type FilesKt, and
    # `FilesKt.readText(f)` is not valid Kotlin -- the module string is wrong.
    *_rows("kotlin", "kotlin.io.FilesKt", (
        "forEachLine", "readBytes", "readLines", "readText", "useLines",
    ), ("WI-ziviv",)),
)


# ---------------------------------------------------------------------------
# Consumer drift: no string literal may stand in for a kind.
# ---------------------------------------------------------------------------

#: The modules that branch on or produce ``IoPrimitive.kind`` (and its taint
#: copies). A literal there bypasses the predicates above -- the shape that let
#: three rules coexist unnoticed.
CONSUMER_FILES: Final[tuple[str, ...]] = (
    "packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py",
    "packages/hypergumbo-core/src/hypergumbo_core/verify_claims.py",
    "packages/hypergumbo-core/src/hypergumbo_core/analyzer_disclosure.py",
    "packages/hypergumbo-core/src/hypergumbo_core/taint.py",
)

#: Constructors whose ``kind=`` keyword is a primitive kind.
_PRODUCER_CALLS: Final[frozenset[str]] = frozenset({"IoPrimitive", "TaintSource", "TaintSink"})


def _is_kind_access(node: ast.expr) -> bool:
    """``x.kind`` or ``getattr(x, "kind", ...)``."""
    if isinstance(node, ast.Attribute) and node.attr == "kind":
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant) and node.args[1].value == "kind"
    )


def _literal_kinds(node: ast.expr) -> list[str]:
    """Kind names a node spells as a string literal (bare, or in a tuple/set/list)."""
    names = all_io_primitive_kind_names()
    if isinstance(node, ast.Constant) and node.value in names:
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.Set, ast.List)):
        return [e.value for e in node.elts
                if isinstance(e, ast.Constant) and e.value in names]
    return []


def find_kind_literal_drift_in_source(source: str, label: str) -> list[str]:
    """Offences in one module's source: a kind literal compared with a ``kind``
    access, a kind literal tested for membership in anything, or a producer
    call passing ``kind=`` a literal."""
    tree = ast.parse(source)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            literal = [k for o in operands for k in _literal_kinds(o)]
            if not literal:
                continue
            membership = any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
            if any(_is_kind_access(o) for o in operands) or (
                membership and isinstance(node.left, ast.Constant)
            ):
                out.append(f"{label}:{node.lineno}: kind literal {literal} -- use "
                           "the io_primitive_kinds predicates")
        elif isinstance(node, ast.Call):
            func = node.func
            fname = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else "")
            if fname not in _PRODUCER_CALLS:
                continue
            for kw in node.keywords:
                literal = _literal_kinds(kw.value) if kw.arg == "kind" else []
                if literal:
                    out.append(f"{label}:{node.lineno}: {fname}(kind={literal[0]!r}) -- "
                               "use kind_for_yaml_section / the KIND_* constants")
    return out


def find_kind_literal_drift(repo_root: Path) -> list[str]:
    """Every offence across :data:`CONSUMER_FILES` (empty list = clean)."""
    out: list[str] = []
    for rel in CONSUMER_FILES:
        path = repo_root / rel
        out.extend(find_kind_literal_drift_in_source(path.read_text(encoding="utf-8"), rel))
    return out
