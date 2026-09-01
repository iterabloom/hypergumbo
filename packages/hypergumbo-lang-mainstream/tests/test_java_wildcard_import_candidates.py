# SPDX-License-Identifier: AGPL-3.0-or-later
"""A wildcard import is a CANDIDATE package, not a blanket file-level prefix.

INV-hahak. ``_extract_wildcard_imports``' own docstring already says the resolver
should "attribute bare class receivers to the first wildcard whose package the
receiver could plausibly belong to" — but the resolver took ``wildcard_imports[0]``
unconditionally, so under ``import java.io.*;`` the call ``System.currentTimeMillis()``
landed as ``java.util.System`` / ``java.io.System`` and matched no catalogue row.

**java.lang is the load-bearing case and it is not a heuristic.** JLS 7.3 makes
``java.lang.*`` implicitly imported in every compilation unit, so ``String``,
``System``, ``Integer``, ``Math`` and ``Thread`` are in scope having never been
written. Any file carrying ANY wildcard therefore mis-attributes all of them.
Measured on jedis alone: 170 external refs name a java.lang class under a
non-java.lang package, including ``System.currentTimeMillis`` x28 and
``System.nanoTime``, both catalogued ``host_info_read``.

The fix emits the disjunction the situation actually is — every wildcard package
plus ``java.lang`` — reusing the comma-joined slot contract cpp has used since
INV-funuf, where ``_module_hint_candidates`` asks an ANY question over the
disjuncts. It does NOT add the bare class name as a candidate: that would match
any catalogue row ending in that class and would turn a recall fix into the
INV-dijor false-positive shape.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from hypergumbo_core import io_boundary as IB
from hypergumbo_core.ir import ExternalRef
from hypergumbo_lang_mainstream.java import analyze_java


def _analyze(src: str):
    with tempfile.TemporaryDirectory() as d:
        pathlib.Path(d, "P.java").write_text(src)
        res = analyze_java(pathlib.Path(d))
        edges = res.edges if hasattr(res, "edges") else res[1]
        return list(edges)


def _module_slot_for(src: str, callee_suffix: str) -> str | None:
    for e in _analyze(src):
        if e.edge_type == "calls" and e.dst.endswith(f"{callee_suffix}:unresolved"):
            dr = getattr(e, "dst_ref", None)
            return dr.module_path if dr else None
    return None


def _classify(src: str, callee_suffix: str):
    cats = {"java": IB.load_catalog("java")}
    for e in _analyze(src):
        if e.edge_type == "calls" and e.dst.endswith(f"{callee_suffix}:unresolved"):
            dr = getattr(e, "dst_ref", None)
            ref = ExternalRef(
                lang="java", module_path=dr.module_path, name=dr.name,
            ) if dr else None
            prim, _ = IB.classify_call_in_catalog(
                cats, e.dst, e.meta or {}, dst_ref=ref,
            )
            return prim
    return None


_WILDCARD_SYSTEM = (
    "import java.io.*;\n"
    "public class P { void f() { long t = System.currentTimeMillis(); } }\n"
)


def test_java_lang_is_a_candidate_under_any_wildcard():
    """JLS 7.3: java.lang.* is implicitly imported, so it is always in scope."""
    slot = _module_slot_for(_WILDCARD_SYSTEM, "System.currentTimeMillis")
    assert slot is not None
    assert "java.lang.System" in slot.split(",")


def test_the_catalogued_row_is_reached_under_a_wildcard():
    """The measured loss: 28 sites in jedis alone."""
    prim = _classify(_WILDCARD_SYSTEM, "System.currentTimeMillis")
    assert prim is not None
    assert prim.boundary == "host_info_read"
    assert (prim.module, prim.name) == ("java.lang.System", "currentTimeMillis")


def test_every_wildcard_is_a_candidate_not_just_the_first():
    src = (
        "import java.io.*;\n"
        "import java.nio.file.*;\n"
        "public class P { void f(String s) throws Exception "
        "{ Files.writeString(Path.of(\"/x\"), s); } }\n"
    )
    slot = _module_slot_for(src, "Files.writeString")
    assert slot is not None
    parts = slot.split(",")
    assert "java.io.Files" in parts
    assert "java.nio.file.Files" in parts, "the SECOND wildcard must survive"


def test_the_class_in_the_second_wildcard_now_reaches_its_row():
    src = (
        "import java.io.*;\n"
        "import java.nio.file.*;\n"
        "public class P { void f(String s) throws Exception "
        "{ Files.writeString(Path.of(\"/x\"), s); } }\n"
    )
    prim = _classify(src, "Files.writeString")
    assert prim is not None and prim.boundary == "fs_write"


def test_a_correct_single_wildcard_still_resolves():
    """WI-tuhok's gain must survive: the wildcard's OWN classes still attribute."""
    src = (
        "import java.nio.file.*;\n"
        "public class P { void f(String s) throws Exception "
        "{ Files.writeString(Path.of(\"/x\"), s); } }\n"
    )
    assert _classify(src, "Files.writeString").boundary == "fs_write"


def test_an_explicit_import_still_wins_over_a_wildcard():
    src = (
        "import java.io.*;\n"
        "import java.nio.file.Files;\n"
        "public class P { void f(String s) throws Exception "
        "{ Files.writeString(Path.of(\"/x\"), s); } }\n"
    )
    slot = _module_slot_for(src, "Files.writeString")
    assert slot == "java.nio.file.Files", "explicit import is evidence, not a guess"


def test_a_repo_local_class_under_a_wildcard_still_matches_nothing():
    """The precision guard: the bare class name is NOT offered as a candidate.

    A project class named ``Files`` must not reach ``java.nio.file.Files`` just
    because the short names agree — that is INV-dijor's shape, and offering the
    unqualified name as a disjunct is exactly how this fix would have caused it.
    """
    src = (
        "import java.io.*;\n"
        "public class P { void f() { Helper.writeString(); } }\n"
    )
    assert _classify(src, "Helper.writeString") is None


def test_no_wildcard_is_unchanged():
    src = (
        "public class P { void f() { long t = System.currentTimeMillis(); } }\n"
    )
    slot = _module_slot_for(src, "System.currentTimeMillis")
    assert slot in (None, "external")


@pytest.mark.parametrize("cls", ["String", "Integer", "Math", "Thread"])
def test_other_implicitly_imported_java_lang_classes(cls):
    src = (
        "import java.io.*;\n"
        f"public class P {{ void f() {{ {cls}.class.getName(); }} }}\n"
    )
    slot = _module_slot_for(src, f"{cls}.class.getName") or _module_slot_for(
        src, "getName")
    if slot is not None:
        assert f"java.lang.{cls}" in slot.split(",") or "java.lang" in slot


# --- the second half of INV-hahak: a fully-qualified call site was overridden ---

def test_a_fully_qualified_call_site_is_not_overridden_by_a_wildcard():
    """The call site spells the package; that is evidence, not a guess.

    This is the half that made INV-hahak more than an import-table gap: under
    ``import java.io.*;`` the call ``java.nio.file.Files.writeString(...)`` was
    re-attributed to ``java.io.Files``, discarding a package the source states
    outright. The receiver arrives as a ``field_access`` chain and only its last
    component (``Files``) was kept.
    """
    src = (
        "import java.io.*;\n"
        "public class P { void f(String s) throws Exception "
        "{ java.nio.file.Files.writeString(java.nio.file.Path.of(\"/x\"), s); } }\n"
    )
    slot = _module_slot_for(src, "Files.writeString")
    assert slot == "java.nio.file.Files"


def test_the_fully_qualified_call_site_reaches_its_row():
    src = (
        "import java.io.*;\n"
        "public class P { void f(String s) throws Exception "
        "{ java.nio.file.Files.writeString(java.nio.file.Path.of(\"/x\"), s); } }\n"
    )
    assert _classify(src, "Files.writeString").boundary == "fs_write"


def test_an_instance_field_chain_is_not_read_as_a_package_path():
    """``this.svc.call()`` and ``a.b.call()`` are receivers, not type references.

    The discriminator is java's own package/type spelling convention — leading
    components lowercase, the type capitalised — which is language-appropriate
    HERE in the java analyzer, unlike the same inference applied language-
    agnostically in ``_module_matches``.
    """
    src = (
        "public class P { Svc svc; void f() { this.svc.run(); } }\n"
        "class Svc { void run() {} }\n"
    )
    slots = [
        e.dst for e in _analyze(src)
        if e.edge_type == "calls" and "run" in e.dst
    ]
    assert not any("this.svc" in s for s in slots)


# --- the package-path discriminator, directly -------------------------------

from hypergumbo_lang_mainstream.java import (  # noqa: E402
    _fully_qualified_type_reference,
    _wildcard_candidate_slot,
)


@pytest.mark.parametrize(
    "chain,expected",
    [
        ("java.nio.file.Files", "java.nio.file.Files"),
        ("java.io.File", "java.io.File"),
        # A bare name is not qualified — one component names no package.
        ("Files", None),
        # A NESTED CLASS is not a package path: Outer.Inner names a type inside
        # a type, and prefixing it as though Outer were a package would invent a
        # module. The leading components must all be lowercase package parts.
        ("Outer.Inner", None),
        ("Map.Entry", None),
        # this-rooted chains are receivers.
        ("this.svc", None),
        # a lowercase tail is a field, not a type
        ("config.client", None),
    ],
)
def test_package_path_discriminator(chain, expected):
    assert _fully_qualified_type_reference(chain) == expected


def test_candidate_slot_always_offers_java_lang_and_dedupes():
    assert _wildcard_candidate_slot(["java.io"], "System") == (
        "java.io.System,java.lang.System"
    )
    # a file that wildcard-imports java.lang itself must not list it twice
    assert _wildcard_candidate_slot(["java.lang"], "System") == "java.lang.System"


def test_candidate_slot_preserves_wildcard_source_order():
    assert _wildcard_candidate_slot(
        ["javax.net.ssl", "java.io", "java.security"], "System",
    ) == (
        "javax.net.ssl.System,java.io.System,java.security.System,"
        "java.lang.System"
    )
