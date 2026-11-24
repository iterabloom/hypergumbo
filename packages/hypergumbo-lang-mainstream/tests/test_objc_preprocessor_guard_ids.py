# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/objc.py
"""Objective-C runs the C preprocessor, and its stable ids carry the
preprocessor branches a declaration sits in, as C, C++, CUDA, HLSL and GLSL
do (WI-sifar, ``c_family_open_guards``).

Two whole ``@implementation Foo`` blocks (or ``@interface``, ``@protocol``)
under ``#ifdef A`` / ``#else`` hashed one text per class, method and property
and tied until scheme v19, told apart only by occurrence order. Checked on the
real analyzer: the alternatives get distinct ids; a declaration outside every
branch hashes what it hashes in a file with no directive; a header with a
classic include guard, and one with ``#import`` and no guard, move no id.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.objc import analyze_objc

_BODY = (
    "@protocol P\n- (void)m;\n@end\n"
    "@interface Foo : NSObject\n@property int x;\n- (int)run;\n@end\n"
)
_IMPL_A = "@implementation Foo\n- (int)run { return 1; }\n@end\n"
_IMPL_B = "@implementation Foo\n- (int)run { return 2; }\n@end\n"


def _ids(root: Path, rel: str, text: str) -> list[tuple[str, str, int, str | None]]:
    root.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)
    return sorted(
        (s.kind, s.name, s.span.start_line, s.stable_id)
        for s in analyze_objc(root).symbols if s.kind != "file"
    )


def _by_name(found: list[tuple[str, str, int, str | None]]) -> dict[tuple[str, str], list[str | None]]:
    out: dict[tuple[str, str], list[str | None]] = {}
    for kind, name, _line, sid in found:
        out.setdefault((kind, name), []).append(sid)
    return out


def test_implementation_alternatives_get_distinct_ids(tmp_path: Path) -> None:
    found = _by_name(_ids(tmp_path, "a.m", f"#ifdef A\n{_IMPL_A}#else\n{_IMPL_B}#endif\n"))
    assert len(found[("class", "Foo")]) == 2 and len(found[("method", "Foo.run")]) == 2  # reach
    for ids in found.values():
        assert None not in ids and len(set(ids)) == len(ids), found


def test_interface_protocol_and_property_alternatives_get_distinct_ids(tmp_path: Path) -> None:
    found = _by_name(_ids(tmp_path, "a.h", f"#if TARGET_OS_IPHONE\n{_BODY}#else\n{_BODY}#endif\n"))
    for key in (("protocol", "P"), ("class", "Foo"), ("property", "Foo.x"), ("method", "Foo.run")):
        assert len(found[key]) == 2, key  # reach
        assert found[key][0] != found[key][1], key


def test_unguarded_twin_keeps_its_id(tmp_path: Path) -> None:
    plain = _by_name(_ids(tmp_path / "p", "a.m", f"\n\n\n{_IMPL_B}"))
    twin = _by_name(_ids(tmp_path / "t", "a.m", f"#if 0\n{_IMPL_A}#endif\n{_IMPL_B}"))
    for key in (("class", "Foo"), ("method", "Foo.run")):
        assert len(plain[key]) == 1 and len(twin[key]) == 2  # reach
        assert plain[key][0] in twin[key]
        assert len(set(twin[key])) == 2


def test_headers_move_no_id(tmp_path: Path) -> None:
    """A classic include guard (renamed or dropped) and an ``#import`` header
    hash what the bare declarations hash; a guard that does not wrap the whole
    file is a branch."""
    def sids(found: list[tuple[str, str, int, str | None]]) -> list[tuple[str, str, str | None]]:
        return sorted((k, n, s) for k, n, _l, s in found)

    bare = sids(_ids(tmp_path / "b", "a.h", _BODY))
    guarded = sids(_ids(tmp_path / "g", "a.h", f"#ifndef FOO_H\n#define FOO_H\n{_BODY}#endif\n"))
    renamed = sids(_ids(tmp_path / "r", "a.h", f"#ifndef BAR_H\n#define BAR_H\n{_BODY}#endif\n"))
    imported = sids(_ids(tmp_path / "i", "a.h", f"#import <Foundation/Foundation.h>\n{_BODY}"))
    assert len(bare) == 5  # reach: protocol, method, class, property, method
    assert guarded == renamed == imported == bare
    partial = sids(_ids(tmp_path / "x", "a.h",
                        f"#ifndef FOO_H\n#define FOO_H\n{_BODY}#endif\n@protocol Q\n@end\n"))
    assert not set(bare) & set(partial)
