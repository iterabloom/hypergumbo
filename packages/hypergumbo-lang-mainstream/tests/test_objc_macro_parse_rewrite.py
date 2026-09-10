# SPDX-License-Identifier: AGPL-3.0-or-later
"""An ObjC file the grammar cannot parse is retried through a byte-length-preserving
rewrite of the two Apple SDK macros tree-sitter-objc 3.0.2 chokes on (WI-lafom).

WI-lafom filed the symptom -- 74 of 387 real ObjC files carry a parse ERROR and the
grammar silently falls back to C, so ``@property`` never becomes a node -- and named
``NS_ASSUME_NONNULL_BEGIN``, ``nullable`` and generics together as candidate triggers.
Bisecting the constructs one at a time REFUTES most of that: ``nullable``, generics,
``NS_DESIGNATED_INITIALIZER``, ``NS_SWIFT_NAME``, block properties and
``NS_ASSUME_NONNULL_BEGIN`` **alone** all parse with zero ERROR nodes. What breaks the
parse is ``NS_ASSUME_NONNULL_BEGIN`` in the WRAPPING position, and ``NS_ENUM`` /
``NS_OPTIONS``, which the item did not name at all.

There is no grammar to bump to: 3.0.2 is the newest tree-sitter-objc published, and it
is what is installed. So this takes INV-bisok's route -- a rewrite behind
``TreeSitterAnalyzer.parse_source``, guarded twice: it runs ONLY on a file that already
fails to parse, and its result is kept ONLY when it strictly reduces ERROR nodes, so a
file the grammar handles is byte-identical through the hook.

The rewrite CONSUMES a leading ``typedef``. Rewriting ``typedef NS_ENUM(NSInteger, S)``
to ``typedef enum S`` -- the first draft -- is a typedef with no declarator, which
yields a MISSING ``type_identifier``: a new parse failure inside the fix for parse
failures. ``enum S`` plus padding is what parses.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.objc import (
    ObjCAnalyzer,
    _objc_parse_source,
    _objc_rewrite_unparseable,
    analyze_objc,
)

WRAPPED = (
    "NS_ASSUME_NONNULL_BEGIN\n"
    "\n"
    "@interface AFURLSessionManager : NSObject\n"
    "@property (readonly, nonatomic, strong) NSURLSession *session;\n"
    "- (void)go;\n"
    "@end\n"
    "\n"
    "NS_ASSUME_NONNULL_END\n"
)

ENUM = (
    "typedef NS_ENUM(NSInteger, AFNetworkReachabilityStatus) {\n"
    "    AFNetworkReachabilityStatusUnknown = -1,\n"
    "    AFNetworkReachabilityStatusNotReachable = 0,\n"
    "};\n"
)


def _analyze(tmp_path: Path, files: dict[str, str]):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    for name, src in files.items():
        (root / name).write_text(src)
    return analyze_objc(root)


class TestTheRewriteItself:
    def test_it_preserves_byte_length(self) -> None:
        """Every span the analyzer reports must still point at the real file."""
        for src in (WRAPPED, ENUM,
                    "typedef NS_OPTIONS(NSUInteger, AFOpt) {\n    AFOptA = 1,\n};\n"):
            raw = src.encode()
            assert len(_objc_rewrite_unparseable(raw)) == len(raw)

    def test_the_nonnull_markers_are_blanked(self) -> None:
        out = _objc_rewrite_unparseable(b"NS_ASSUME_NONNULL_BEGIN\n@class A;\n")
        assert out == b"                       \n@class A;\n"

    def test_ns_enum_becomes_a_named_enum_and_the_typedef_goes(self) -> None:
        """``typedef enum S {...};`` has NO declarator and yields a MISSING node."""
        out = _objc_rewrite_unparseable(b"typedef NS_ENUM(NSInteger, S) {\n};\n")
        assert out.startswith(b"enum S")
        assert b"typedef" not in out
        assert out == b"enum S                        {\n};\n"

    def test_ns_options_and_ns_closed_enum_take_the_same_route(self) -> None:
        for macro in (b"NS_OPTIONS", b"NS_CLOSED_ENUM"):
            src = b"typedef " + macro + b"(NSUInteger, S) {\n};\n"
            assert _objc_rewrite_unparseable(src).startswith(b"enum S")

    def test_ns_enum_without_a_leading_typedef_still_rewrites(self) -> None:
        out = _objc_rewrite_unparseable(b"NS_ENUM(NSInteger, S) x;\n")
        assert out == b"enum S                x;\n"

    def test_a_file_with_neither_macro_is_returned_unchanged(self) -> None:
        src = b"@interface A : NSObject\n@property (nonatomic) int n;\n@end\n"
        assert _objc_rewrite_unparseable(src) == src


class TestThroughTheAnalyzer:
    def test_a_wrapped_interface_yields_its_class_and_its_property(
        self, tmp_path: Path,
    ) -> None:
        """WI-garar's headline shape: the ``session`` property must become a symbol."""
        result = _analyze(tmp_path, {"AFURLSessionManager.h": WRAPPED})
        names = {s.name for s in result.symbols}
        assert "AFURLSessionManager" in names
        assert any(n.endswith("session") for n in names), sorted(names)

    def test_an_ns_enum_file_does_not_swallow_the_declarations_after_it(
        self, tmp_path: Path,
    ) -> None:
        result = _analyze(tmp_path, {
            "AFNetworkReachabilityManager.h": ENUM
            + "@interface AFNetworkReachabilityManager : NSObject\n"
              "@property (nonatomic, strong) NSURLSession *session;\n"
              "@end\n",
        })
        names = {s.name for s in result.symbols}
        assert "AFNetworkReachabilityManager" in names

    def test_a_clean_file_is_analysed_identically_with_the_hook_in_place(
        self, tmp_path: Path,
    ) -> None:
        """Guard 1: the rewrite never runs on a file that already parses."""
        clean = (
            "@interface Clean : NSObject\n"
            "@property (nonatomic, copy) NSString *NS_ASSUME_NONNULL_BEGIN_lookalike;\n"
            "@end\n"
        )
        result = _analyze(tmp_path, {"Clean.h": clean})
        assert "Clean" in {s.name for s in result.symbols}


class TestTheTwoGuards:
    """The guards are what make the hook safe rather than a licence to edit source."""

    @staticmethod
    def _parse(src: bytes):
        analyzer = ObjCAnalyzer()
        return _objc_parse_source(analyzer._create_parser(), src)

    def test_a_clean_file_is_returned_byte_identical_and_never_rewritten(self) -> None:
        """Guard 1. The lookalike would be blanked if the rewrite ever ran here."""
        src = (b"@interface A : NSObject\n"
               b"@property (nonatomic) int NS_ASSUME_NONNULL_BEGIN;\n@end\n")
        out, tree = self._parse(src)
        assert out == src
        assert not tree.root_node.has_error

    def test_a_file_that_fails_for_another_reason_is_left_alone(self) -> None:
        """Guard 1 passes, but there is nothing to rewrite: Quick's test macros."""
        src = b'QuickSpecBegin(S)\nit(@"x", ^{ });\nQuickSpecEnd\n'
        out, tree = self._parse(src)
        assert out == src
        assert tree.root_node.has_error

    def test_a_rewrite_that_does_not_help_is_discarded(self) -> None:
        """Guard 2, and the reason a macro name in a COMMENT is safe.

        The rewrite fires here -- ``NS_ENUM(...)`` inside a ``//`` comment matches
        the pattern, because a regex over bytes has no idea what a comment is. The
        bytes are still thrown away, because the retry does not parse better.
        """
        src = b'// NS_ENUM(NSInteger, S)\nQuickSpecBegin(S)\nit(@"x", ^{ });\n'
        assert _objc_rewrite_unparseable(src) != src
        out, _ = self._parse(src)
        assert out == src

    def test_the_base_class_hook_and_the_pass_helper_are_one_fact(self) -> None:
        src = b"NS_ASSUME_NONNULL_BEGIN\n@interface A : NSObject\n@end\n"
        analyzer = ObjCAnalyzer()
        parser = analyzer._create_parser()
        assert analyzer.parse_source(parser, src)[0] == _objc_parse_source(parser, src)[0]
        assert not analyzer.parse_source(parser, src)[1].root_node.has_error
