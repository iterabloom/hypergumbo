# SPDX-License-Identifier: AGPL-3.0-or-later
"""The io-primitive-kind axis (ADR-0059): registry, producers, consumers, ledger.

What each class pins, and why it is not the same check twice:

* ``TestTheRegistry`` -- the three kinds, their YAML sections and their order
  (rows are emitted section by section and first-declared-wins is load-bearing).
* ``TestEveryShippedRowIsOnTheAxis`` -- the RUNTIME half of ADR-0024's
  enforcement: every row every shipped catalogue and overlay loads carries a
  registered kind, and no row hides its names under an unrecognised key (a
  typo'd ``method:`` would drop them without a word, since the loader reads
  only the registered sections).
* ``TestTheLiteralScanner`` -- the STATIC half: the live tree has no kind
  literal in the consumer modules, and the scanner demonstrably fires on each
  offence shape, so a clean exit cannot be a broken scanner.
* ``TestTheLedger`` -- the shrink-only record of rows the axiom rejects. It
  fails on an entry whose row is fixed or gone, and pins the size.
* ``TestSwiftConstructorsAreFunctions`` -- the one mechanical conformance check
  available for a non-python language: an UpperCamelCase name under
  ``methods:`` is a constructor, called on the type itself, so none may remain;
  and the constructors that do stay rowed carry the boundary INV-gujoh's
  adjudication gave them.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.io_primitive_kinds import (
    IO_PRIMITIVE_KINDS,
    KIND_ATTRIBUTE,
    KIND_FUNCTION,
    KIND_METHOD,
    KNOWN_NONCONFORMING_ROWS,
    YAML_SECTIONS,
    all_io_primitive_kind_names,
    called_on_a_named_owner,
    find_kind_literal_drift,
    find_kind_literal_drift_in_source,
    kind_for_yaml_section,
    reached_through_an_instance,
    read_not_called,
)

_PKG = Path(__file__).resolve().parents[1] / "src" / "hypergumbo_core"
_REPO = Path(__file__).resolve().parents[3]
_CATALOGUES = sorted((_PKG / "io_primitives").glob("*.yaml"))
_OVERLAYS = sorted((_PKG / "io_primitives_overlays").glob("*.yaml"))
_LANGUAGES = [p.stem for p in _CATALOGUES]


class TestTheRegistry:
    def test_the_three_kinds(self) -> None:
        assert all_io_primitive_kind_names() == {KIND_FUNCTION, KIND_METHOD, KIND_ATTRIBUTE}

    def test_sections_in_emission_order(self) -> None:
        assert YAML_SECTIONS == ("functions", "methods", "attributes")
        assert [kind_for_yaml_section(s) for s in YAML_SECTIONS] == [
            s.name for s in IO_PRIMITIVE_KINDS
        ]

    def test_an_unknown_section_raises(self) -> None:
        with pytest.raises(KeyError):
            kind_for_yaml_section("method")

    @pytest.mark.parametrize("kind", sorted(all_io_primitive_kind_names()))
    def test_the_predicates_partition_the_kinds(self, kind: str) -> None:
        hits = [reached_through_an_instance(kind), called_on_a_named_owner(kind),
                read_not_called(kind)]
        assert hits.count(True) == 1, (kind, hits)

    def test_an_unregistered_value_satisfies_no_predicate(self) -> None:
        for bogus in ("", "constructor", "Method"):
            assert not (reached_through_an_instance(bogus) or called_on_a_named_owner(bogus)
                        or read_not_called(bogus))


class TestEveryShippedRowIsOnTheAxis:
    @pytest.mark.parametrize("language", _LANGUAGES)
    def test_every_loaded_row_has_a_registered_kind(self, language: str) -> None:
        catalog = load_catalog(language)
        assert catalog.primitives, f"{language}: reach -- the catalogue must load rows"
        bad = {(p.module, p.name, p.kind) for p in catalog.primitives
               if p.kind not in all_io_primitive_kind_names()}
        assert not bad, bad

    @pytest.mark.parametrize("path", _CATALOGUES + _OVERLAYS, ids=lambda p: p.name)
    def test_no_name_list_hides_under_an_unregistered_key(self, path: Path) -> None:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        offenders = []
        for section, rows in data.items():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for key, value in row.items():
                    is_name_list = isinstance(value, list) and value and all(
                        isinstance(v, (str, bool)) for v in value)
                    if is_name_list and key not in YAML_SECTIONS:
                        offenders.append((section, row.get("module"), key))
        assert not offenders, offenders

    def test_the_taint_copies_are_on_the_axis(self) -> None:
        from hypergumbo_core.taint import _derive_auto_imports_from_io_primitives

        sources, sinks, _ambiguous = _derive_auto_imports_from_io_primitives(
            _PKG / "io_primitives")
        kinds = {e.kind for by_lang in (sources, sinks) for entries in by_lang.values()
                 for e in entries}
        assert kinds, "reach: auto-derived sources/sinks must exist"
        assert kinds <= all_io_primitive_kind_names(), kinds


class TestTheLiteralScanner:
    def test_the_live_tree_is_clean(self) -> None:
        assert find_kind_literal_drift(_REPO) == []

    @pytest.mark.parametrize("snippet", [
        'x = [p for p in hits if p.kind != "method"]',
        'y = getattr(p, "kind", None) == "method"',
        'z = {m for m, k in kinds.items() if "method" in k}',
        'w = [p for p in rows if p.kind in ("function", "attribute")]',
        'v = IoPrimitive(boundary="fs_read", module="os", name="x", kind="function")',
        'u = TaintSink(module="os", name="x", kind="method", zone="z")',
    ])
    def test_it_fires_on_each_offence_shape(self, snippet: str) -> None:
        assert find_kind_literal_drift_in_source(snippet, "t.py"), snippet

    @pytest.mark.parametrize("snippet", [
        'x = [s for s in syms if s.kind == "class"]',
        'y = reached_through_an_instance(p.kind)',
        'z = Symbol(kind="method", name="m")',
        'w = meta.get("call_construct") == "method"',
    ])
    def test_it_ignores_what_is_not_a_primitive_kind_literal(self, snippet: str) -> None:
        """CONTROLS. A symbol kind that is not one of the three, a predicate
        call, a Symbol constructor and a call_construct comparison are not
        offences. Stated limit: ``kind == "method"`` on a bare NAME is not
        caught -- the scanner keys on an attribute or getattr access."""
        assert find_kind_literal_drift_in_source(snippet, "t.py") == [], snippet


class TestTheLedger:
    #: Shrink-only. Lower it when a blocked row is fixed and its entry deleted;
    #: raising it is a new exception to the axiom and must be argued in review.
    LEDGER_SIZE = 29

    def test_the_size_is_pinned(self) -> None:
        assert len(KNOWN_NONCONFORMING_ROWS) == self.LEDGER_SIZE

    def test_no_duplicates(self) -> None:
        keys = [(r.language, r.module, r.name) for r in KNOWN_NONCONFORMING_ROWS]
        assert len(keys) == len(set(keys))

    def test_every_entry_names_a_blocker(self) -> None:
        pattern = re.compile(r"^(INV|WI)-[a-z]{5}$")
        for row in KNOWN_NONCONFORMING_ROWS:
            assert row.blocked_by and all(pattern.match(b) for b in row.blocked_by), row

    @pytest.mark.parametrize("language", sorted({r.language for r in KNOWN_NONCONFORMING_ROWS}))
    def test_every_entry_is_still_a_method_row(self, language: str) -> None:
        """An entry whose row was re-kinded or removed must be DELETED, so the
        ledger can only shrink toward conformance."""
        loaded = {(p.module, p.name) for p in load_catalog(language).primitives
                  if p.kind == KIND_METHOD}
        stale = [r for r in KNOWN_NONCONFORMING_ROWS
                 if r.language == language and (r.module, r.name) not in loaded]
        assert not stale, f"row re-kinded or removed; drop the entry: {stale}"


class TestSwiftConstructorsAreFunctions:
    def test_no_upper_camel_method_row_remains(self) -> None:
        catalog = load_catalog("swift")
        assert any(p.kind == KIND_METHOD for p in catalog.primitives), (
            "reach: the swift catalogue must still carry method rows")
        ctors = sorted((p.module, p.name) for p in catalog.primitives
                       if p.kind == KIND_METHOD and p.name[:1].isupper())
        assert not ctors, ctors

    def test_the_adjudicated_constructors(self) -> None:
        """INV-gujoh, 2026-09-23. Pinned by (module, name) -> boundary so a
        re-added row, or one moved back onto a transfer boundary, is a visible
        diff. ADR-0049: SETUP / REGISTER / LAZY rows are disclosed
        (net_listen, db_compose); HANDLE rows keep the transfer boundary."""
        rows = {(p.module, p.name): (p.boundary, p.kind)
                for p in load_catalog("swift").primitives if p.name[:1].isupper()
                and p.module == p.name}
        assert rows == {
            ("NWConnection", "NWConnection"): ("net_send", KIND_FUNCTION),
            ("NWListener", "NWListener"): ("net_listen", KIND_FUNCTION),
            ("ServerBootstrap", "ServerBootstrap"): ("net_listen", KIND_FUNCTION),
            ("NIOWebSocketServerUpgrader", "NIOWebSocketServerUpgrader"):
                ("net_listen", KIND_FUNCTION),
            ("NIOAsyncChannel", "NIOAsyncChannel"): ("net_recv", KIND_FUNCTION),
            ("NSFetchRequest", "NSFetchRequest"): ("db_compose", KIND_FUNCTION),
            ("ModelContext", "ModelContext"): ("db_read", KIND_FUNCTION),
        }

    @pytest.mark.parametrize("name", [
        "URLRequest", "HTTPClientRequest", "ClientBootstrap",
        "MultiThreadedEventLoopGroup", "Logger", "NIOSSLContext",
        "NIOSSLCertificate", "NIOSSLPrivateKey", "CommandLine",
    ])
    def test_a_constructor_that_crosses_nothing_is_not_rowed(self, name: str) -> None:
        assert not [p for p in load_catalog("swift").primitives if p.name == name]

    def test_command_line_is_an_attribute_read(self) -> None:
        rows = [(p.boundary, p.kind) for p in load_catalog("swift").primitives
                if p.module == "CommandLine"]
        assert rows == [("env_read", KIND_ATTRIBUTE)]


class TestJvmStaticsAreFunctions:
    """INV-zikab step 6 (ADR-0059): a JDK static is called on its class."""

    @pytest.mark.parametrize("module,name", [
        ("java.nio.file.Files", "readAllBytes"), ("java.nio.file.Files", "walk"),
        ("java.nio.file.Files", "deleteIfExists"), ("java.lang.System", "getenv"),
        ("java.lang.System", "currentTimeMillis"), ("java.time.Instant", "now"),
        ("java.time.Clock", "systemUTC"),
    ])
    def test_a_static_is_function_kind_in_every_jvm_catalogue(self, module: str, name: str) -> None:
        for language in ("java", "kotlin", "scala"):
            kinds = {p.kind for p in load_catalog(language).primitives
                     if (p.module, p.name) == (module, name)}
            assert kinds == {KIND_FUNCTION}, (language, module, name, kinds)

    def test_scala_properties_members_are_function_kind(self) -> None:
        kinds = {p.kind for p in load_catalog("scala").primitives
                 if p.module == "scala.util.Properties"}
        assert kinds == {KIND_FUNCTION}

    def test_every_java_function_row_needs_module_context(self) -> None:
        """Java has no free functions: every function row is called on its
        class, and a bare call with no module context can reach one only by a
        static import, which java.py slots. So the no-context path must not
        match one by name. Arm B of the re-kind did exactly that without this
        rule: sbt's own ``now()`` became ``Instant.now``, gatling's own
        ``readString()`` became ``Files.readString``."""
        catalog = load_catalog("java")
        functions = {p.name for p in catalog.primitives if p.kind == KIND_FUNCTION}
        assert functions, "reach: java must carry function rows"
        assert functions <= catalog.ambiguous_names, sorted(functions - catalog.ambiguous_names)

    def test_scala_properties_members_need_module_context(self) -> None:
        catalog = load_catalog("scala")
        names = {p.name for p in catalog.primitives if p.module == "scala.util.Properties"}
        assert names and names <= catalog.ambiguous_names, sorted(names - catalog.ambiguous_names)

    @pytest.mark.parametrize("language,name", [("scala", "now"), ("scala", "readString"),
                                               ("kotlin", "getenv"), ("java", "walk")])
    def test_a_bare_no_context_call_does_not_match(self, language: str, name: str) -> None:
        """The two false positives arm B found, plus one per other language."""
        assert load_catalog(language).lookup_with_module(name, "external") is None

    @pytest.mark.parametrize("language", ["java", "kotlin", "scala"])
    def test_a_call_whose_slot_names_the_class_still_matches(self, language: str) -> None:
        """REACH: the definite slot java writes and WI-kilap made kotlin and
        scala write reaches the row regardless of the stamp."""
        hit = load_catalog(language).lookup_with_module(
            "readAllBytes", "java.nio.file.Files", call_construct="method")
        assert hit is not None and hit.kind == KIND_FUNCTION


class TestScalaProcessLaunchIsReachable:
    """WI-narij (ADR-0059). ``Process(cmd)`` is companion-apply sugar for
    ``Process.apply(cmd)``, called on the object, so both rows are function-kind.
    The analyzer emits the sugar under the name ``Process``, so the row must
    carry that name too: the constructor-named-row convention the Swift rows
    already use."""

    _MODULE = "scala.sys.process.Process"

    def test_the_launch_rows_are_function_kind(self) -> None:
        kinds = {(p.name, p.boundary): p.kind for p in load_catalog("scala").primitives
                 if p.module == self._MODULE}
        for name in ("apply", "Process"):
            for boundary in ("fs_write", "subprocess"):
                assert kinds.get((name, boundary)) == KIND_FUNCTION, (name, boundary, kinds)

    def test_a_slotted_launch_crosses_both_boundaries(self) -> None:
        catalog = load_catalog("scala")
        hit = catalog.lookup_with_module("Process", self._MODULE)
        assert hit is not None and hit.name == "Process"
        assert catalog.simultaneous_boundaries_for(f"{self._MODULE}.Process") == {"fs_write", "subprocess"}

    def test_a_bare_apply_needs_module_context(self) -> None:
        """``apply`` is the most common method name in scala; a no-context
        ``apply`` is anything's companion. sbt alone has 11 unstamped or
        function-stamped ``external:apply`` edges that would all read as
        process launches."""
        catalog = load_catalog("scala")
        assert "apply" in catalog.ambiguous_names
        assert catalog.lookup_with_module("apply", "external") is None

    def test_a_bare_process_still_matches(self) -> None:
        """Measured, not assumed. ``import scala.sys.process._`` (and scala 3's
        ``.*``) leaves the slot at ``external``, so a bare ``Process`` is how the
        wildcard spelling arrives. On 11 scala repos (sbt, tapir, lila,
        playframework, pekko, spark, scala3, gatling, http4s, cats, zio), every
        bare ``external:Process`` edge this row matched was a genuine
        scala.sys.process launch: 8 of 8, 0 lost and 0 changed. A project type
        named ``Process`` resolves in-repo and never reaches this path. If a
        false positive is ever found, list the name in ``ambiguous_names``: that
        was arm B1, which gave up those 8."""
        hit = load_catalog("scala").lookup_with_module("Process", "external")
        assert hit is not None and hit.module == self._MODULE
