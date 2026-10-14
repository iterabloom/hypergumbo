# SPDX-License-Identifier: AGPL-3.0-or-later
"""A binding's initialiser is typed by what the expression EVALUATES to (WI-hojib).

WI-silos made a declaration the binding pass cannot type SHADOW: it answers
"unknown" instead of falling through to an outer name, the file level or a
same-named field. Measured on four repositories, most of the stamps that removed
had been right only by coincidence -- a local named like a field usually has the
field's type -- and every one of them sat behind an initialiser shape the
expression walker did not type. Each shape is now typed by its value, never by
the name it is bound to:

* an implicit-``self`` call to an INHERITED method (``stored(Session())`` in a
  subclass of the test base that declares ``stored(_:) -> Session``; XCTest's
  ``expectation(description:)``) -- the return type is looked up through the
  enclosing type's bases, not only on the enclosing type itself;
* a literal -- its default type (``String``, ``Int``, ``Double``, ``Bool``);
* a member chain on a value (``config.fileManager``) -- the head's type, then
  the member's declared type on THAT type, through its bases;
* ``a ?? b`` and ``c ? a : b`` -- the type every operand agrees on;
* a constructor whose type name starts with ``_`` (``_URLEncodedFormDecoder()``);
* ``self.init(...)`` / ``T.init(...)`` -- the type constructed; a ``self.m()``
  call -- what the enclosing type's ``m`` returns, as for the bare ``m()``.

Every test pairs the typed use with a control that must stay untyped, so a
change that stamps everything cannot pass.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.swift import analyze_swift


def _edges(root: Path, src: str, extra: dict[str, str] | None = None) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.swift").write_text(src)
    for name, text in (extra or {}).items():
        (root / name).write_text(text)
    return analyze_swift(root).edges


def _calls_to(edges: list[Edge], method: str) -> list[Edge]:
    return [
        e for e in edges
        if e.edge_type == "calls"
        and (e.dst.endswith(f":{method}:unresolved") or f".{method}:method" in e.dst)
    ]


def _one(edges: list[Edge], method: str) -> Edge:
    hits = _calls_to(edges, method)
    assert len(hits) == 1, [e.dst for e in edges if method in e.dst]
    return hits[0]


def _hint(edges: list[Edge], method: str) -> object:
    return (_one(edges, method).meta or {}).get("receiver_type_hint")


class TestAnInheritedMethodTypesItsCallResult:
    BASE = (
        "import Foundation\n"
        "import XCTest\n"
        "final class Session {\n"
        "    func request(_ u: Int) -> DataRequest { return DataRequest() }\n"
        "}\n"
        "final class DataRequest {\n"
        "    func validate() -> DataRequest { return self }\n"
        "}\n"
        "class BaseTestCase: XCTestCase {\n"
        "    var session: Session?\n"
        "    func stored(_ session: Session) -> Session {\n"
        "        self.session = session\n"
        "        return session\n"
        "    }\n"
        "}\n"
    )

    def test_a_subclass_types_its_local_through_the_base_method(self, tmp_path: Path) -> None:
        """Alamofire Tests' ``let session = stored(Session())`` (61 sites + the
        cascade): ``stored`` is declared on the BASE class, so the key
        ``<enclosing>.stored`` missed. The call result is Session, and the
        cascade follows: ``session.request`` resolves to ``Session.request`` and
        the request's own type comes from the registry."""
        edges = _edges(tmp_path / "s", self.BASE, {"t.swift": (
            "final class ConcurrencyTests: BaseTestCase {\n"
            "    func testIt() {\n"
            "        let session = stored(Session())\n"
            "        let request = session.request(1)\n"
            "        request.validate()\n"
            "    }\n"
            "}\n"
        )})
        assert any(e.dst.endswith(":Session.request:method") for e in _calls_to(edges, "request"))
        assert any(e.dst.endswith(":DataRequest.validate:method") for e in _calls_to(edges, "validate"))

    def test_a_type_outside_the_hierarchy_does_not_reach_the_base_method(
        self, tmp_path: Path,
    ) -> None:
        """The control: an unrelated type calling a bare ``stored`` has no such
        method, so its local stays untyped."""
        edges = _edges(tmp_path / "u", self.BASE, {"t.swift": (
            "final class Unrelated {\n"
            "    func testIt() {\n"
            "        let session = stored(Session())\n"
            "        session.request(1)\n"
            "    }\n"
            "}\n"
        )})
        [e] = _calls_to(edges, "request")
        assert e.dst.startswith("swift:external:"), e.dst
        assert (e.meta or {}).get("receiver_type_hint") is None

    def test_an_explicit_receiver_reaches_a_method_its_base_declares(
        self, tmp_path: Path,
    ) -> None:
        """The same walk for a NAMED receiver: ``s.make()`` with ``s: Sub`` and
        ``make`` declared on ``Base``."""
        edges = _edges(tmp_path / "e", (
            "import Foundation\n"
            "class Base { func make() -> FileManager { return FileManager.default } }\n"
            "class Sub: Base { }\n"
            "class Other { func make() -> URLSession { return URLSession.shared } }\n"
            "func go(s: Sub, o: Other) {\n"
            "    let m = s.make()\n"
            "    m.zap()\n"
            "    let n = o.make()\n"
            "    n.zop()\n"
            "}\n"
        ))
        assert _hint(edges, "zap") == "FileManager"
        assert _hint(edges, "zop") == "URLSession"

    def test_xctest_expectation_is_an_XCTestExpectation(self, tmp_path: Path) -> None:
        """``let exp = expectation(description:)`` in an XCTestCase subclass (21
        sites): a library method on the external base, typed by its row."""
        edges = _edges(tmp_path / "x", (
            "import XCTest\n"
            "final class DownloaderTests: XCTestCase {\n"
            "    func testIt() {\n"
            "        let exp = expectation(description: #function)\n"
            "        exp.fulfill()\n"
            "    }\n"
            "}\n"
            "final class NotATest {\n"
            "    func run() {\n"
            "        let exp = expectation(description: \"x\")\n"
            "        exp.wait()\n"
            "    }\n"
            "}\n"
        ))
        e = _one(edges, "fulfill")
        assert e.dst.startswith("swift:XCTestExpectation:"), e.dst
        assert _hint(edges, "wait") is None


class TestALiteralHasItsDefaultType:
    def test_string_literals_are_String(self, tmp_path: Path) -> None:
        """vapor RFC1123's ``var rfc1123 = ""`` (16 sites), an interpolated
        literal (Kingfisher's processor identifiers) and a multi-line one."""
        edges = _edges(tmp_path / "s", (
            "func go(alpha: Int) {\n"
            "    var rfc1123 = \"\"\n"
            "    rfc1123.append(\" \")\n"
            "    var identifier = \"com.x.Blend(\\(alpha))\"\n"
            "    identifier.appendTwo(\"y\")\n"
            "    let block = \"\"\"\n"
            "    text\n"
            "    \"\"\"\n"
            "    block.appendThree(\"z\")\n"
            "    var list = [\"a\"]\n"
            "    list.appendFour(\"b\")\n"
            "}\n"
        ))
        assert _hint(edges, "append") == "String"
        assert _hint(edges, "appendTwo") == "String"
        assert _hint(edges, "appendThree") == "String"
        # The control: a collection literal is not a receiver type.
        assert _hint(edges, "appendFour") is None

    def test_number_and_boolean_literals(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "n", (
            "func go() {\n"
            "    let n = 3\n"
            "    n.advanced(by: 1)\n"
            "    let d = 2.5\n"
            "    d.rounded()\n"
            "    let b = true\n"
            "    b.toggleCopy()\n"
            "}\n"
        ))
        assert _hint(edges, "advanced") == "Int"
        assert _hint(edges, "rounded") == "Double"
        assert _hint(edges, "toggleCopy") == "Bool"

    def test_a_literal_receiver_is_typed(self, tmp_path: Path) -> None:
        """The walker is shared: the literal as the RECEIVER gets the same answer
        as the literal bound to a name."""
        edges = _edges(tmp_path / "r", (
            "import Foundation\n"
            "func go(p: String) {\n"
            "    \"text\".write(toFile: p, atomically: true, encoding: .utf8)\n"
            "}\n"
        ))
        e = _one(edges, "write")
        assert e.dst.startswith("swift:String:"), e.dst


class TestAMemberChainIsTypedThroughTheHeadType:
    KF = (
        "import Foundation\n"
        "enum DiskStorage {\n"
        "    struct Config {\n"
        "        var fileManager: FileManager\n"
        "        var name: String\n"
        "    }\n"
        "    final class Backend {\n"
        "        private var _config: Config\n"
        "        var config: Config {\n"
        "            get { _config }\n"
        "        }\n"
        "        init(config: Config) { _config = config }\n"
        "        func prepareDirectory() {\n"
        "            let fileManager = config.fileManager\n"
        "            fileManager.fileExists(atPath: \"p\")\n"
        "            config.fileManager.createDirectory(atPath: \"p\")\n"
        "            let other = config.missing\n"
        "            other.fileExistsTwo(atPath: \"p\")\n"
        "        }\n"
        "    }\n"
        "}\n"
        "struct Unrelated { var fileManager: URLSession }\n"
    )

    def test_kingfisher_diskstorage_config_fileManager(self, tmp_path: Path) -> None:
        """Kingfisher DiskStorage.swift:122/250/397: the three catalogued
        FileManager chains. ``config`` is Backend's property of type Config,
        and ``fileManager`` is Config's property -- not Unrelated's."""
        edges = _edges(tmp_path / "k", self.KF)
        assert _one(edges, "fileExists").dst.startswith("swift:FileManager:")
        assert _one(edges, "createDirectory").dst.startswith("swift:FileManager:")
        # The control: a member the head's type does not declare names nothing.
        assert _hint(edges, "fileExistsTwo") is None

    def test_a_deeper_self_chain_is_typed_member_by_member(self, tmp_path: Path) -> None:
        """``self.inner.session``: Outer's ``inner`` is Inner, and Inner's
        ``session`` is URLSession. The step-3a hazard WI-sizas named was a
        GUESS; this is the declared member type, and a member Inner does not
        declare stays silent."""
        edges = _edges(tmp_path / "d", (
            "import Foundation\n"
            "class Inner { let session: URLSession = URLSession.shared }\n"
            "class Outer {\n"
            "    let inner: Inner = Inner()\n"
            "    func go() {\n"
            "        self.inner.session.invalidateAndCancel()\n"
            "        self.inner.nothing.invalidateTwo()\n"
            "    }\n"
            "}\n"
        ))
        assert _one(edges, "invalidateAndCancel").dst.startswith("swift:URLSession:")
        assert _hint(edges, "invalidateTwo") is None

    def test_an_inherited_member_and_an_optional_chain(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "i", (
            "import Foundation\n"
            "class Base { var fm: FileManager = FileManager.default }\n"
            "class Sub: Base { }\n"
            "class Holder { var sub: Sub? }\n"
            "func go(s: Sub, h: Holder) {\n"
            "    s.fm.removeItem(atPath: \"x\")\n"
            "    let m = h.sub?.fm\n"
            "    m?.copyItem(atPath: \"x\", toPath: \"y\")\n"
            "}\n"
        ))
        assert _hint(edges, "removeItem") == "FileManager"
        assert _hint(edges, "copyItem") == "FileManager"


    def test_a_generic_parameter_member_names_no_type(self, tmp_path: Path) -> None:
        """Kingfisher's ``ciContext.value`` with ``class SendableBox<T> { var
        value: T }``: ``T`` is whatever the use binds, not a type called ``T``."""
        edges = _edges(tmp_path / "g", (
            "import Foundation\n"
            "final class Box<T> {\n"
            "    var value: T\n"
            "    var fm: FileManager = FileManager()\n"
            "    init(value: T) { self.value = value }\n"
            "}\n"
            "func go(box: Box<FileManager>) {\n"
            "    box.value.zap()\n"
            "    box.fm.zop()\n"
            "}\n"
        ))
        assert _hint(edges, "zap") is None
        assert _hint(edges, "zop") == "FileManager"


class TestCoalescingAndConditionalExpressions:
    def test_both_operands_agree(self, tmp_path: Path) -> None:
        """Kingfisher's ``options.downloader ?? self.downloader`` (and vapor
        URI's ``cond ? path : "/\\(path)"``): every operand has one type."""
        edges = _edges(tmp_path / "a", (
            "final class Downloader { func download() { } }\n"
            "struct Options { var downloader: Downloader? }\n"
            "final class Manager {\n"
            "    let downloader: Downloader = Downloader()\n"
            "    func go(options: Options, path: String) {\n"
            "        let downloader = options.downloader ?? self.downloader\n"
            "        downloader.download()\n"
            "        let p = path.isEmpty ? path : \"/\\(path)\"\n"
            "        p.dropSlash()\n"
            "    }\n"
            "}\n"
        ))
        assert any(e.dst.endswith(":Downloader.download:method") for e in _calls_to(edges, "download"))
        assert _hint(edges, "dropSlash") == "String"

    def test_disagreeing_or_unknown_operands_name_nothing(self, tmp_path: Path) -> None:
        """A literal's type is contextual (``""`` may be a Substring), so one
        known operand does not decide the other: the controls stay silent."""
        edges = _edges(tmp_path / "b", (
            "import Foundation\n"
            "func go(a: FileManager?, b: URLSession, c: Int) {\n"
            "    let x = a ?? b\n"
            "    x.mixed()\n"
            "    let y = c.unknownThing ?? \"\"\n"
            "    y.half()\n"
            "    let z = a ?? FileManager()\n"
            "    z.agreed()\n"
            "}\n"
        ))
        assert _hint(edges, "mixed") is None
        assert _hint(edges, "half") is None
        assert _hint(edges, "agreed") == "FileManager"


class TestConstructorSpellings:
    def test_an_underscored_type_is_a_constructor(self, tmp_path: Path) -> None:
        """hummingbird's ``let decoder = _URLEncodedFormDecoder(options:)``: the
        capitalisation test read ``_`` and refused the constructor."""
        edges = _edges(tmp_path / "u", (
            "final class _URLEncodedFormDecoder {\n"
            "    init(options: Int) { }\n"
            "    func unbox(_ x: Int) -> Int { return x }\n"
            "}\n"
            "func go() {\n"
            "    let decoder = _URLEncodedFormDecoder(options: 1)\n"
            "    decoder.unbox(1)\n"
            "    let annotated: _URLEncodedFormDecoder? = nil\n"
            "    annotated?.unbox(2)\n"
            "}\n"
        ))
        hits = _calls_to(edges, "unbox")
        assert len(hits) == 2 and all(
            e.dst.endswith(":_URLEncodedFormDecoder.unbox:method") for e in hits
        ), [e.dst for e in hits]

    def test_an_underscored_value_head_is_a_value_chain(self, tmp_path: Path) -> None:
        """The sibling test: ``_storage.p.m()`` is a VALUE chain (WI-sulas), so
        the head ``_storage`` does not name the receiver's type."""
        edges = _edges(tmp_path / "v", (
            "import Foundation\n"
            "final class Inner { var queue: DispatchQueue = DispatchQueue(label: \"q\") }\n"
            "final class Store {\n"
            "    private var _storage: Inner = Inner()\n"
            "    func go() { _storage.queue.async { } }\n"
            "}\n"
        ))
        e = _one(edges, "async")
        assert e.dst.startswith("swift:DispatchQueue:"), e.dst

    def test_self_init_and_type_init_construct_the_type(self, tmp_path: Path) -> None:
        """hummingbird ResponseGenerator's ``var headers = self.init()`` in a
        static func of an extension of HTTPFields."""
        edges = _edges(tmp_path / "i", (
            "import HTTPTypes\n"
            "extension HTTPFields {\n"
            "    static func make() -> HTTPFields {\n"
            "        var headers = self.init()\n"
            "        headers.reserveCapacity(4)\n"
            "        return headers\n"
            "    }\n"
            "}\n"
            "func go() {\n"
            "    let f = HTTPFields.init()\n"
            "    f.removeAll()\n"
            "}\n"
        ))
        assert _hint(edges, "reserveCapacity") == "HTTPFields"
        assert _hint(edges, "removeAll") == "HTTPFields"

    def test_Self_constructs_the_enclosing_type(self, tmp_path: Path) -> None:
        """``Self(...)`` names the enclosing type, as a ``-> Self`` return type
        does; it is never a type called ``Self``. Both readers: the declaration
        reader (a bare constructor) and the walker (under ``try``)."""
        edges = _edges(tmp_path / "S", (
            "import Foundation\n"
            "extension FileManager {\n"
            "    static func make() throws {\n"
            "        let a = Self()\n"
            "        a.zap()\n"
            "        let b = try Self()\n"
            "        b.zop()\n"
            "    }\n"
            "}\n"
        ))
        assert _hint(edges, "zap") == "FileManager"
        assert _hint(edges, "zop") == "FileManager"

    def test_Self_init_receiver_is_the_enclosing_type(self, tmp_path: Path) -> None:
        """vapor OTP.swift's ``Self.init(...).generate(...)``: the owner
        ``Self`` is the enclosing type, never a module called ``Self``."""
        edges = _edges(tmp_path / "Si", (
            "import Foundation\n"
            "extension FileManager {\n"
            "    static func make() { Self.init().zap() }\n"
            "}\n"
        ))
        assert _hint(edges, "zap") == "FileManager"

    def test_a_tuple_element_names_no_member(self, tmp_path: Path) -> None:
        """``pair.0`` has no member NAME, so the member lookup has nothing to
        look up and the walker stays silent."""
        edges = _edges(tmp_path / "t", (
            "import Foundation\n"
            "func go(pair: (FileManager, Int)) {\n"
            "    let first = pair.0\n"
            "    first.zap()\n"
            "}\n"
        ))
        assert _hint(edges, "zap") is None

    def test_a_self_method_call_result_is_typed_like_the_bare_call(
        self, tmp_path: Path,
    ) -> None:
        """``self.make()`` and ``make()`` are the same call. Typing the RESULT
        does not stamp ``self`` itself (WI-sizas's bare-self half, pinned in
        test_swift_self_property_receiver)."""
        edges = _edges(tmp_path / "m", (
            "import Foundation\n"
            "final class Client {\n"
            "    func make() -> FileManager { return FileManager.default }\n"
            "    func go() {\n"
            "        let a = self.make()\n"
            "        a.zap()\n"
            "        let b = make()\n"
            "        b.zop()\n"
            "    }\n"
            "}\n"
        ))
        assert _hint(edges, "zap") == "FileManager"
        assert _hint(edges, "zop") == "FileManager"
