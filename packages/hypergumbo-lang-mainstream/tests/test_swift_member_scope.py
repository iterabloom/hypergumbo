# SPDX-License-Identifier: AGPL-3.0-or-later
"""A type's property belongs to that type, and is looked up before a global (WI-tagir).

The binding pass kept ONE name-keyed map, ``var_types``, for every declaration
outside a callable body: true globals AND the stored properties of every type
declared in the file. ``_type_of`` read it BEFORE the enclosing type's own
fields, and writes were last-writer-wins, so

* a property of type A typed a same-named bare receiver inside type B, which has
  no such member (live repro on origin/dev 385ec63be1: ``store.zap()`` in B
  stamped FileManager from A's ``store``);
* when a global and a property shared a name, whichever was declared LATER in
  the file won, in every type.

A property is now recorded under its OWNING type (``_file_members``), only a true
global goes to the file level, and a bare name resolves the way Swift's
unqualified lookup does: locals, then the members of the enclosing type and its
bases, then the file's globals.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.swift import analyze_swift


def _edges(root: Path, src: str) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.swift").write_text(src)
    return analyze_swift(root).edges


def _hint(edges: list[Edge], method: str) -> object:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.dst.endswith(f":{method}:unresolved")
    ]
    assert len(hits) == 1, [e.dst for e in edges if method in e.dst]
    return (hits[0].meta or {}).get("receiver_type_hint")


def test_the_items_repro_another_types_property_names_nothing(tmp_path: Path) -> None:
    """The filed fixture, with its control: A's own method still sees ``store``."""
    edges = _edges(tmp_path, (
        "import Foundation\n"
        "class A {\n"
        "    var store: FileManager = FileManager.default\n"
        "    func mine() { store.zip() }\n"
        "}\n"
        "class B { func go() { store.zap() } }\n"
    ))
    assert _hint(edges, "zip") == "FileManager"
    assert _hint(edges, "zap") is None


def test_a_member_beats_a_global_wherever_either_is_declared(tmp_path: Path) -> None:
    """Swift's lookup order, not the file's declaration order: inside A the member
    answers, in a type without the member and in a free function the global does.
    The global is declared AFTER the class, where last-writer-wins let it
    overwrite A's property."""
    edges = _edges(tmp_path, (
        "import Foundation\n"
        "class A {\n"
        "    var store: FileManager = FileManager.default\n"
        "    func mine() { store.zip() }\n"
        "}\n"
        "class B { func go() { store.zap() } }\n"
        "func free() { store.zop() }\n"
        "let store = URLSession.shared\n"
    ))
    assert _hint(edges, "zip") == "FileManager"
    assert _hint(edges, "zap") == "URLSession"
    assert _hint(edges, "zop") == "URLSession"


def test_an_untyped_member_shadows_the_global(tmp_path: Path) -> None:
    """A member the pass cannot type is still A's member: it answers "unknown",
    never the global's type (WI-silos' rule, now possible at the type level
    because a member no longer lives in a map every type shares)."""
    edges = _edges(tmp_path, (
        "import Foundation\n"
        "let store = URLSession.shared\n"
        "class A {\n"
        "    var store = makeStore()\n"
        "    func mine() { store.zip() }\n"
        "}\n"
        "func free() { store.zop() }\n"
    ))
    assert _hint(edges, "zip") is None
    assert _hint(edges, "zop") == "URLSession"


def test_a_member_typed_by_its_call_result_stays_with_its_type(tmp_path: Path) -> None:
    """The binding pass types a property from its initialiser's call result,
    which Pass 1's field registry cannot. That type must still serve the owner
    and only the owner."""
    edges = _edges(tmp_path, (
        "import Foundation\n"
        "class Factory { func session() -> URLSession { return URLSession.shared } }\n"
        "class A {\n"
        "    let s = Factory().session()\n"
        "    func mine() { s.dataTask(with: URL(fileURLWithPath: \"/\")) }\n"
        "}\n"
        "class B { func go() { s.resume() } }\n"
    ))
    assert _hint(edges, "dataTask") == "URLSession"
    assert _hint(edges, "resume") is None


def test_a_base_class_member_in_the_same_file_is_inherited(tmp_path: Path) -> None:
    edges = _edges(tmp_path, (
        "import Foundation\n"
        "class Base { let s = Factory().session() }\n"
        "class Factory { func session() -> URLSession { return URLSession.shared } }\n"
        "class Sub: Base { func go() { s.resume() } }\n"
        "class Unrelated { func go() { s.cancel() } }\n"
    ))
    assert _hint(edges, "resume") == "URLSession"
    assert _hint(edges, "cancel") is None


def test_a_protocol_extension_does_not_see_a_conformer_property(tmp_path: Path) -> None:
    """hummingbird Application.swift:99-117's shape: the protocol extension's
    bare ``responder`` was typed from ``struct Application``'s property."""
    edges = _edges(tmp_path, (
        "protocol ApplicationProtocol { }\n"
        "extension ApplicationProtocol {\n"
        "    func run() { responder.respond() }\n"
        "}\n"
        "struct Application: ApplicationProtocol {\n"
        "    public let responder: Responder\n"
        "    func own() { responder.respondTwo() }\n"
        "}\n"
    ))
    assert _hint(edges, "respond") is None
    assert _hint(edges, "respondTwo") == "Responder"


def test_same_named_nested_types_keep_their_own_members(tmp_path: Path) -> None:
    """Alamofire Combine.swift: three publishers each nest an ``Inner`` class
    with its own ``request`` property, of three different types. A map keyed by
    the owner's NAME gives every ``Inner`` the first one's; the lookup from
    inside a declaration reads that declaration's own members first."""
    edges = _edges(tmp_path, (
        "import Foundation\n"
        "struct DataPublisher {\n"
        "    final class Inner {\n"
        "        let request: FileManager = FileManager()\n"
        "        func one() { request.zip() }\n"
        "    }\n"
        "}\n"
        "struct DownloadPublisher {\n"
        "    final class Inner {\n"
        "        let request: URLSession = URLSession.shared\n"
        "        func two() { request.zap() }\n"
        "    }\n"
        "}\n"
    ))
    assert _hint(edges, "zip") == "FileManager"
    assert _hint(edges, "zap") == "URLSession"


def test_an_extension_of_a_nested_type_sees_its_members(tmp_path: Path) -> None:
    """Alamofire OfflineRetrier.swift: ``extension OfflineRetrier.State`` names
    the nested ``State`` by its dotted path; its members are declared under
    ``State``."""
    edges = _edges(tmp_path, (
        "import Foundation\n"
        "final class OfflineRetrier {\n"
        "    struct State {\n"
        "        var timeoutWorkItem: DispatchWorkItem?\n"
        "    }\n"
        "}\n"
        "extension OfflineRetrier.State {\n"
        "    mutating func stop() { timeoutWorkItem?.cancel() }\n"
        "}\n"
    ))
    assert _hint(edges, "cancel") == "DispatchWorkItem"


def test_a_protocol_requirement_is_a_member_of_the_protocol_and_its_conformers(
    tmp_path: Path,
) -> None:
    """Kingfisher ImageProcessor.swift: a protocol extension's bare
    ``identifier`` is the protocol's own ``var identifier: String { get }``,
    and a conformer that does not redeclare it inherits it through the base
    walk. A type that does not conform does not see it."""
    edges = _edges(tmp_path, (
        "protocol ImageProcessor { var identifier: String { get } }\n"
        "extension ImageProcessor {\n"
        "    func append() { identifier.appendingOne(\"x\") }\n"
        "}\n"
        "struct Blend: ImageProcessor {\n"
        "    func other() { identifier.appendingTwo(\"y\") }\n"
        "}\n"
        "struct Unrelated {\n"
        "    func other() { identifier.appendingThree(\"z\") }\n"
        "}\n"
    ))
    assert _hint(edges, "appendingOne") == "String"
    assert _hint(edges, "appendingTwo") == "String"
    assert _hint(edges, "appendingThree") is None


def test_a_local_of_an_initialiser_is_not_a_field(tmp_path: Path) -> None:
    """Pass 1's field registry stopped only at ``func``, so a ``let`` inside an
    ``init`` (or a closure, or an accessor) registered as a FIELD of the type
    and typed a same-named receiver in every method."""
    edges = _edges(tmp_path, (
        "import Foundation\n"
        "final class Store {\n"
        "    let real: URLSession = URLSession.shared\n"
        "    init() {\n"
        "        let fm = FileManager()\n"
        "        fm.zip()\n"
        "    }\n"
        "    func go() {\n"
        "        fm.zap()\n"
        "        real.zop()\n"
        "    }\n"
        "}\n"
    ))
    assert _hint(edges, "zip") == "FileManager"
    assert _hint(edges, "zap") is None
    assert _hint(edges, "zop") == "URLSession"


def test_a_requirement_of_an_associated_type_names_no_type(tmp_path: Path) -> None:
    """hummingbird Application.swift: ``associatedtype Responder: HTTPResponder``
    then ``var responder: Responder { get async throws }``. ``Responder`` is
    whatever a conformer binds, a generic parameter of the protocol, not a type
    called ``Responder``; the concrete requirement beside it is typed."""
    edges = _edges(tmp_path, (
        "import Foundation\n"
        "protocol ApplicationProtocol {\n"
        "    associatedtype Responder: HTTPResponder\n"
        "    var responder: Responder { get }\n"
        "    var files: FileManager { get }\n"
        "}\n"
        "extension ApplicationProtocol {\n"
        "    func run() {\n"
        "        responder.respond()\n"
        "        files.fileExists(atPath: \"x\")\n"
        "    }\n"
        "}\n"
    ))
    assert _hint(edges, "respond") is None
    assert _hint(edges, "fileExists") == "FileManager"
