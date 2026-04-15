# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for I/O boundary analysis (ADR-0016).

Covers the Python I/O primitive catalog loading, edge matching,
and boundary map generation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import (
    HIGH_RISK_PRIMITIVES,
    BoundaryMap,
    BoundaryMapEntry,
    IoBoundaryCatalog,
    IoChain,
    IoPrimitive,
    _extract_callee_name,
    _extract_module_hint,
    _module_matches,
    compute_boundary_map,
    is_high_risk,
    load_catalog,
    match_edge_to_primitive,
    tag_io_boundaries,
)


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

    def test_rust_catalog_has_tokio_framework_entries(self) -> None:
        """Rust catalog includes Tokio/Hyper/Reqwest framework entries."""
        catalog = load_catalog("rust")
        qualified_names = {p.qualified_name for p in catalog.primitives}
        assert "tokio::net::TcpStream.connect" in qualified_names
        assert "tokio::net::TcpListener.bind" in qualified_names
        assert "tokio::fs.read" in qualified_names
        assert "reqwest::Client.get" in qualified_names
        assert "hyper::Client.get" in qualified_names
        assert "axum::Router.route" in qualified_names

    def test_javascript_catalog_has_all_boundary_types(self) -> None:
        catalog = load_catalog("javascript")
        boundaries = {p.boundary for p in catalog.primitives}
        expected = {"fs_read", "fs_write", "net_send", "net_recv", "subprocess", "env_read"}
        assert expected.issubset(boundaries)

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

        # Data is ambiguous without context (gin.Context.Data vs template.Data)
        assert catalog.lookup_with_module("Data", None) is None
        # But with gin context, it matches
        hit = catalog.lookup_with_module("Data", "gin")
        assert hit is not None
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

    def test_java_catalog_has_netty_framework_entries(self) -> None:
        """Java catalog includes Netty framework IO methods."""
        catalog = load_catalog("java")
        qualified_names = {p.qualified_name for p in catalog.primitives}
        assert "io.netty.channel.Channel.write" in qualified_names
        assert "io.netty.channel.Channel.read" in qualified_names
        assert "io.netty.buffer.ByteBuf.writeBytes" in qualified_names
        assert "io.netty.buffer.ByteBuf.readBytes" in qualified_names
        assert "io.netty.bootstrap.ServerBootstrap.bind" in qualified_names

    def test_java_catalog_netty_channel_write_is_net_send(self) -> None:
        """Netty Channel.write is classified as net_send."""
        catalog = load_catalog("java")
        match = catalog.lookup("io.netty.channel.Channel.write")
        assert match is not None
        assert match.boundary == "net_send"

    def test_java_catalog_covers_jdbc_and_jpa(self) -> None:
        """WI-sakan: JDBC, JPA, Hibernate, Spring Data covered under db_read / db_write."""
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
        # Spring Data / JdbcTemplate
        assert qnames["org.springframework.jdbc.core.JdbcTemplate.query"].boundary == "db_read"
        assert qnames["org.springframework.jdbc.core.JdbcTemplate.update"].boundary == "db_write"
        assert qnames["org.springframework.data.repository.CrudRepository.findById"].boundary == "db_read"
        assert qnames["org.springframework.data.repository.CrudRepository.save"].boundary == "db_write"
        # Hibernate Session
        assert qnames["org.hibernate.Session.get"].boundary == "db_read"
        assert qnames["org.hibernate.Session.save"].boundary == "db_write"

    def test_java_catalog_covers_logging_facades(self) -> None:
        """WI-sakan: SLF4J, Log4j, java.util.logging, Logback covered under logging."""
        catalog = load_catalog("java")
        qnames = {p.qualified_name: p for p in catalog.primitives}

        assert qnames["org.slf4j.Logger.info"].boundary == "logging"
        assert qnames["org.slf4j.Logger.error"].boundary == "logging"
        assert qnames["org.apache.logging.log4j.Logger.info"].boundary == "logging"
        assert qnames["org.apache.log4j.Logger.info"].boundary == "logging"
        assert qnames["java.util.logging.Logger.info"].boundary == "logging"
        assert qnames["ch.qos.logback.classic.Logger.info"].boundary == "logging"

    def test_java_catalog_covers_http_clients(self) -> None:
        """WI-sakan: Apache HttpClient 4.x/5.x, WebClient, Unirest, Retrofit."""
        catalog = load_catalog("java")
        qnames = {p.qualified_name: p for p in catalog.primitives}

        assert qnames["org.apache.http.client.HttpClient.execute"].boundary == "net_send"
        assert qnames["org.apache.hc.client5.http.classic.HttpClient.execute"].boundary == "net_send"
        assert qnames["org.springframework.web.reactive.function.client.WebClient.get"].boundary == "net_send"
        assert qnames["kong.unirest.Unirest.post"].boundary == "net_send"
        assert qnames["retrofit2.Call.execute"].boundary == "net_send"

    def test_java_catalog_covers_commons_io(self) -> None:
        """WI-sakan: Apache Commons IO file helpers covered under fs_read / fs_write."""
        catalog = load_catalog("java")
        qnames = {p.qualified_name: p for p in catalog.primitives}

        assert qnames["org.apache.commons.io.FileUtils.readFileToString"].boundary == "fs_read"
        assert qnames["org.apache.commons.io.FileUtils.writeStringToFile"].boundary == "fs_write"
        assert qnames["org.apache.commons.io.IOUtils.toString"].boundary == "fs_read"

    def test_kotlin_loads_own_catalog_with_java_parent(self) -> None:
        """WI-rujos: Kotlin has its own catalog merged with Java parent.

        Kotlin idiom favors extension functions on java.io.File (readText,
        writeText, forEachLine) and top-level println/print that have no
        Java analog. Plus ktor, kotlin-logging, Android Log, Exposed ORM.
        The Java parent fills in the raw java.io/java.net/JDBC entries so
        code using the underlying Java APIs directly is still matched.
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
        # ktor client
        assert catalog.lookup("io.ktor.client.HttpClient.get") is not None
        # Android Log (very common in Kotlin Android codebases)
        assert catalog.lookup("android.util.Log.d") is not None

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

    def test_objective_c_alias_loads_objc_catalog(self) -> None:
        """The 'objective-c' alias resolves to the 'objc' catalog."""
        catalog = load_catalog("objective-c")
        assert len(catalog.primitives) > 0
        hit = catalog.lookup("removeItemAtPath:error:")
        assert hit is not None


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

    def test_catalog_ignores_malformed_yaml_entries(self, tmp_path: Path) -> None:
        """Non-list boundary values and non-dict entries are skipped."""
        yaml_content = """\
language: broken

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

    def test_skips_unknown_language(self) -> None:
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="cobol:/app/main.cbl:1:MAIN:paragraph",
            dst="cobol:/stdlib/io.cbl:1:OPEN-FILE:paragraph",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 0

    def test_tags_subprocess_call(self) -> None:
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/deploy.py:50-52:deploy:function",
            dst="python:/stdlib/subprocess.py:200-210:subprocess.run:function",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "subprocess"

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
        """Entry-point trace crosses native_bridge edges (Java→C JNI)."""
        catalog = load_catalog("c")
        edges = [
            # Java side: main → nativeRead (native_bridge) → C_impl → fopen
            self._make_edge(src="java_main", dst="native_method"),
            self._make_edge(src="native_method", dst="c_jni_impl", edge_type="native_bridge"),
            self._make_edge(src="c_jni_impl", dst="c:external:0-0:fopen:unresolved"),
        ]
        entrypoint_ids = {"java_main"}

        bmap = compute_boundary_map(edges, {"c": catalog}, entrypoint_ids=entrypoint_ids)
        assert bmap.total_io_edges >= 1
        fs_entry = bmap.entries.get("fs_read")
        assert fs_entry is not None
        assert "java_main" in fs_entry.entry_points

    def test_cgo_bridge_edge_traced_to_entrypoint(self) -> None:
        """Entry-point trace crosses cgo_bridge edges (Go→C)."""
        catalog = load_catalog("c")
        edges = [
            self._make_edge(src="go_main", dst="go_wrapper"),
            self._make_edge(src="go_wrapper", dst="c_impl", edge_type="cgo_bridge"),
            self._make_edge(src="c_impl", dst="c:external:0-0:fopen:unresolved"),
        ]
        entrypoint_ids = {"go_main"}

        bmap = compute_boundary_map(edges, {"c": catalog}, entrypoint_ids=entrypoint_ids)
        assert bmap.total_io_edges >= 1
        fs_entry = bmap.entries.get("fs_read")
        assert fs_entry is not None
        assert "go_main" in fs_entry.entry_points

    def test_ffi_bridge_edge_traced_to_entrypoint(self) -> None:
        """Entry-point trace crosses ffi_bridge edges (Python→Rust)."""
        catalog = load_catalog("python")
        edges = [
            self._make_edge(src="py_main", dst="py_wrapper"),
            self._make_edge(src="py_wrapper", dst="rust_impl", edge_type="ffi_bridge"),
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


class TestHighRiskPrimitives:
    """Tests for the high-risk primitive classification."""

    def test_is_high_risk_subprocess(self) -> None:
        assert is_high_risk("subprocess.Popen") is True
        assert is_high_risk("subprocess.run") is True
        assert is_high_risk("os.execv") is True

    def test_is_high_risk_destructive_fs(self) -> None:
        assert is_high_risk("shutil.rmtree") is True
        assert is_high_risk("os.remove") is True

    def test_is_high_risk_network(self) -> None:
        assert is_high_risk("urllib.request.urlopen") is True
        assert is_high_risk("socket.socket.send") is True

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

    def test_scala_has_effect_system_entries(self) -> None:
        """Scala catalog covers cats-effect and ZIO I/O primitives."""
        catalog = load_catalog("scala")
        scala_modules = {p.module for p in catalog.primitives}
        # At least one effect system should be present
        has_cats_effect = any("cats.effect" in m for m in scala_modules)
        has_zio = any("zio" in m for m in scala_modules)
        assert has_cats_effect or has_zio, (
            f"Expected cats-effect or ZIO entries, got modules: "
            f"{sorted(m for m in scala_modules if 'scala' in m.lower() or 'cats' in m.lower() or 'zio' in m.lower())}"
        )

    def test_scala_has_http_client_entries(self) -> None:
        """Scala catalog covers Scala HTTP client libraries."""
        catalog = load_catalog("scala")
        scala_modules = {p.module for p in catalog.primitives}
        has_sttp = any("sttp" in m for m in scala_modules)
        has_http4s = any("http4s" in m for m in scala_modules)
        has_akka_http = any("akka.http" in m or "pekko.http" in m for m in scala_modules)
        assert has_sttp or has_http4s or has_akka_http, (
            "Scala catalog should include at least one Scala HTTP client library"
        )

    def test_scala_has_streaming_entries(self) -> None:
        """Scala catalog covers streaming I/O libraries (fs2, akka/pekko streams)."""
        catalog = load_catalog("scala")
        scala_modules = {p.module for p in catalog.primitives}
        has_fs2 = any("fs2" in m for m in scala_modules)
        has_akka_stream = any("akka.stream" in m or "pekko.stream" in m for m in scala_modules)
        assert has_fs2 or has_akka_stream, (
            "Scala catalog should include streaming I/O entries"
        )

    def test_fs2_file_ops_are_fs_not_net(self) -> None:
        """fs2.io.file.Files operations should be fs_read/fs_write, NOT net_recv."""
        catalog = load_catalog("scala")
        readAll = catalog.lookup("fs2.io.file.Files.readAll")
        assert readAll is not None
        assert readAll.boundary == "fs_read", (
            f"fs2.io.file.Files.readAll should be fs_read, got {readAll.boundary}"
        )
        writeAll = catalog.lookup("fs2.io.file.Files.writeAll")
        assert writeAll is not None
        assert writeAll.boundary == "fs_write", (
            f"fs2.io.file.Files.writeAll should be fs_write, got {writeAll.boundary}"
        )
        createDir = catalog.lookup("fs2.io.file.Files.createDirectory")
        assert createDir is not None
        assert createDir.boundary == "fs_write", (
            f"fs2.io.file.Files.createDirectory should be fs_write, got {createDir.boundary}"
        )

    def test_scala_has_db_entries(self) -> None:
        """Scala catalog covers database access libraries."""
        catalog = load_catalog("scala")
        boundaries = {p.boundary for p in catalog.primitives}
        assert "db_read" in boundaries or "db_write" in boundaries, (
            "Scala catalog should include database boundary entries"
        )

    def test_scala_catalog_all_boundary_types(self) -> None:
        """Scala catalog covers all major boundary types."""
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
        """Distinctive I/O names should still match even for unresolved externals."""
        catalog = load_catalog("scala")
        # readAllBytes is specific enough to not be ambiguous
        edge = self._make_edge(
            src="scala:IO.scala:10:readFile:method",
            dst="scala:external:0-0:readAllBytes:unresolved",
        )
        count = tag_io_boundaries([edge], {"scala": catalog})
        assert count == 1, "Specific name 'readAllBytes' should still match for unresolved externals"

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

    # --- Go ambiguous names ---

    def test_go_bare_run_not_matched(self) -> None:
        """Go: bare 'Run' should NOT match gin.Engine.Run without module context."""
        catalog = load_catalog("go")
        edge = self._make_edge(
            src="go:/main.go:10:TestFoo:function",
            dst="go:external:0-0:Run:unresolved",
        )
        count = tag_io_boundaries([edge], {"go": catalog})
        assert count == 0, "Bare 'Run' is ambiguous (testing.T.Run, cobra.Command.Run)"

    def test_go_bare_string_not_matched(self) -> None:
        """Go: bare 'String' should NOT match gin.Context.String without module context."""
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
        """Go: 'Run' with gin.Engine module context should still match."""
        catalog = load_catalog("go")
        edge = self._make_edge(
            src="go:/main.go:10:main:function",
            dst="go:gin.Engine:0-0:Run:unresolved",
        )
        count = tag_io_boundaries([edge], {"go": catalog})
        assert count == 1, "Qualified 'Run' on gin.Engine should match"

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
        """Distinctive I/O names should match even for unresolved externals."""
        catalog = load_catalog("swift")
        # fileExists is specific to FileManager — should match
        hit = catalog.lookup_with_module("fileExists", module_hint="external")
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
        edges = [
            MockEdge(
                src="swift:Sources/App/Network.swift:10:fetch:method",
                dst="swift:external:0-0:dataTask:unresolved",
            ),
            MockEdge(
                src="swift:Sources/App/Util.swift:5:log:method",
                dst="swift:external:0-0:print:unresolved",
            ),
            MockEdge(
                src="swift:Sources/App/IO.swift:20:check:method",
                dst="swift:external:0-0:fileExists:unresolved",
            ),
            # Generic 'write' should NOT be tagged (ambiguous)
            MockEdge(
                src="swift:Sources/App/Writer.swift:15:save:method",
                dst="swift:external:0-0:write:unresolved",
            ),
        ]
        count = tag_io_boundaries(edges, {"swift": catalog})
        assert count == 3, f"Expected 3 tagged edges, got {count}"
        assert edges[0].meta["io_boundary"] == "net_send"
        assert edges[1].meta["io_boundary"] == "logging"
        assert edges[2].meta["io_boundary"] == "fs_read"
        assert edges[3].meta is None  # 'write' should not be tagged

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
        edges = [
            MockEdge(
                src="swift:Sources/App/Server.swift:10:setup:method",
                dst="swift:external:0-0:MultiThreadedEventLoopGroup:unresolved",
            ),
            MockEdge(
                src="swift:Sources/App/Server.swift:15:teardown:method",
                dst="swift:external:0-0:syncShutdownGracefully:unresolved",
            ),
            MockEdge(
                src="swift:Sources/App/WS.swift:20:handle:method",
                dst="swift:external:0-0:onText:unresolved",
            ),
            MockEdge(
                src="swift:Sources/App/TLS.swift:5:configure:method",
                dst="swift:external:0-0:NIOSSLContext:unresolved",
            ),
            MockEdge(
                src="swift:Sources/App/Client.swift:8:fetch:method",
                dst="swift:external:0-0:HTTPClientRequest:unresolved",
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
