# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/dead-code-prospector-run.py.

Coverage focus: WI-zafab filter 1 (polyglot-only check at the harness
level). The full prospecting flow is integration-tested via real
hypergumbo invocations during the bakeoff; here we exercise the
language-counting and polyglot-detection helpers in isolation so
regressions are caught without depending on external repos.
"""
from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path


def _load_prospector():
    """Import scripts/dead-code-prospector-run.py as a module despite no .py extension on the canonical name.

    The script DOES have a .py extension, but it lives outside any package
    and importlib.import_module won't find it without help.
    """
    script_path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "dead-code-prospector-run.py"
    )
    loader = importlib.machinery.SourceFileLoader(
        "dead_code_prospector", str(script_path),
    )
    spec = importlib.util.spec_from_loader("dead_code_prospector", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


prospector = _load_prospector()


# ---------------------------------------------------------------------------
# Tests: _count_languages_by_extension
# ---------------------------------------------------------------------------


class TestCountLanguagesByExtension:
    """Verify file-extension language counting."""

    def test_counts_python_files(self, tmp_path: Path) -> None:
        """Python files are counted under the python language label."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("def f(): pass")
        (tmp_path / "src" / "b.py").write_text("def g(): pass")
        counts = prospector._count_languages_by_extension(tmp_path)
        assert counts == {"python": 2}

    def test_counts_multiple_languages(self, tmp_path: Path) -> None:
        """A polyglot repo aggregates files by language."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.go").write_text("package main")
        (tmp_path / "src" / "main.py").write_text("def f(): pass")
        (tmp_path / "src" / "client.ts").write_text("export const x = 1")
        counts = prospector._count_languages_by_extension(tmp_path)
        assert counts == {"go": 1, "python": 1, "typescript": 1}

    def test_skips_node_modules(self, tmp_path: Path) -> None:
        """Vendored dependencies under ``node_modules/`` do NOT count.

        Without the ignore filter, a Python repo with a single test fixture
        ``node_modules/`` would be falsely promoted to polyglot by counting
        thousands of vendored .js files.
        """
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("def f(): pass")
        (tmp_path / "node_modules" / "lib").mkdir(parents=True)
        for i in range(20):
            (tmp_path / "node_modules" / "lib" / f"vendor{i}.js").write_text(
                "export const x = 1",
            )
        counts = prospector._count_languages_by_extension(tmp_path)
        assert counts == {"python": 1}, (
            f"node_modules vendored files must be ignored; got {counts}"
        )

    def test_skips_vendor_directory(self, tmp_path: Path) -> None:
        """Go's vendor/ directory is also ignored."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.go").write_text("package main")
        (tmp_path / "vendor" / "github.com" / "foo").mkdir(parents=True)
        for i in range(20):
            (tmp_path / "vendor" / "github.com" / "foo" / f"v{i}.go").write_text(
                "package foo",
            )
        counts = prospector._count_languages_by_extension(tmp_path)
        assert counts == {"go": 1}

    def test_unknown_extensions_ignored(self, tmp_path: Path) -> None:
        """Files with extensions not in the language map are silently dropped."""
        (tmp_path / "Makefile").write_text("all:")
        (tmp_path / "README.md").write_text("# repo")
        (tmp_path / "config.toml").write_text("[section]")
        (tmp_path / "main.py").write_text("def f(): pass")
        counts = prospector._count_languages_by_extension(tmp_path)
        assert counts == {"python": 1}


# ---------------------------------------------------------------------------
# Tests: _is_polyglot_repo
# ---------------------------------------------------------------------------


class TestIsPolyglotRepo:
    """Verify the polyglot threshold logic."""

    def test_monoglot_python_returns_false(self) -> None:
        """A pure Python repo is monoglot."""
        assert prospector._is_polyglot_repo({"python": 100}) is False

    def test_two_above_threshold_is_polyglot(self) -> None:
        """Two languages above the default threshold (10) → polyglot."""
        assert prospector._is_polyglot_repo({"python": 50, "go": 30}) is True

    def test_one_below_threshold_is_monoglot(self) -> None:
        """One language above the threshold and another below → monoglot.

        A Go repo with 5 stray test fixtures in JS should still be considered
        monoglot Go for prospecting purposes.
        """
        assert prospector._is_polyglot_repo({"go": 200, "javascript": 5}) is False

    def test_custom_threshold_lower(self) -> None:
        """Threshold can be lowered for tests/small fixtures."""
        # With threshold=2, two languages with 3+ files each is polyglot.
        assert prospector._is_polyglot_repo(
            {"go": 3, "python": 3}, threshold=2,
        ) is True
        # With default threshold=10, the same counts are monoglot.
        assert prospector._is_polyglot_repo({"go": 3, "python": 3}) is False

    def test_empty_returns_false(self) -> None:
        """A repo with no detected source files is not polyglot."""
        assert prospector._is_polyglot_repo({}) is False


# ---------------------------------------------------------------------------
# Tests: run_prospecting integration with the polyglot filter
# ---------------------------------------------------------------------------


class TestRunProspectingPolyglotFilter:
    """Verify that monoglot repos are skipped by default and surfaced
    in the summary, and that --include-monoglot overrides the skip.
    """

    def test_monoglot_repo_skipped_by_default(self, tmp_path: Path) -> None:
        """A pure Python repo is skipped from the prospecting run."""
        pool = tmp_path / "pool"
        pool.mkdir()
        repo = pool / "monorepo"
        repo.mkdir()
        # 50 Python files, no other languages
        for i in range(50):
            (repo / f"mod{i}.py").write_text("def f(): pass")
        output_dir = tmp_path / "out"

        summary = prospector.run_prospecting(
            pool, ["monorepo"], output_dir, include_monoglot=False,
        )

        assert summary["repos_analyzed"] == [], (
            f"Monoglot repo must be skipped, got {summary['repos_analyzed']}"
        )
        assert len(summary["repos_skipped_monoglot"]) == 1
        skipped = summary["repos_skipped_monoglot"][0]
        assert skipped["repo"] == "monorepo"
        assert skipped["languages"] == {"python": 50}

    def test_monoglot_repo_analyzed_with_include_flag(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """``include_monoglot=True`` overrides the skip and runs hypergumbo.

        We monkey-patch ``_run_hypergumbo`` to return a fake successful result
        so the test does not depend on the actual hypergumbo CLI being
        installed in the test environment.
        """
        pool = tmp_path / "pool"
        pool.mkdir()
        repo = pool / "monorepo"
        repo.mkdir()
        for i in range(50):
            (repo / f"mod{i}.py").write_text("def f(): pass")
        output_dir = tmp_path / "out"

        fake_result = {
            "summary": {"total_candidates": 0},
            "dead_candidates": [],
        }
        monkeypatch.setattr(
            prospector, "_run_hypergumbo", lambda repo_path: fake_result,
        )

        summary = prospector.run_prospecting(
            pool, ["monorepo"], output_dir, include_monoglot=True,
        )

        assert summary["repos_analyzed"] == ["monorepo"], (
            f"--include-monoglot should bypass the filter; "
            f"got analyzed={summary['repos_analyzed']}, "
            f"skipped={summary['repos_skipped_monoglot']}"
        )
        assert summary["repos_skipped_monoglot"] == []

    def test_polyglot_repo_analyzed_by_default(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """A genuinely polyglot repo (Go + Python) is analyzed without the override."""
        pool = tmp_path / "pool"
        pool.mkdir()
        repo = pool / "polyrepo"
        repo.mkdir()
        for i in range(15):
            (repo / f"go{i}.go").write_text("package main")
        for i in range(15):
            (repo / f"py{i}.py").write_text("def f(): pass")
        output_dir = tmp_path / "out"

        fake_result = {
            "summary": {"total_candidates": 0},
            "dead_candidates": [],
        }
        monkeypatch.setattr(
            prospector, "_run_hypergumbo", lambda repo_path: fake_result,
        )

        summary = prospector.run_prospecting(
            pool, ["polyrepo"], output_dir, include_monoglot=False,
        )

        assert summary["repos_analyzed"] == ["polyrepo"]
        assert summary["repos_skipped_monoglot"] == []


# ---------------------------------------------------------------------------
# Tests: _categorize_candidate (WI-vupin v5)
# ---------------------------------------------------------------------------
#
# The rule table is order-sensitive: specific patterns precede general
# ones. Each test picks a name/path/language triple that exercises one
# rule end-to-end. Tests for later rules also implicitly verify earlier
# rules didn't preempt them — if they had, the category would be wrong.


class TestCategorizerPreservedRules:
    """Rules carried forward from the original (pre-v5) categorizer."""

    def test_unmarshal_caught_before_language_rules(self) -> None:
        assert prospector._categorize_candidate(
            "Config.UnmarshalYAML", "x.go", "go",
        ) == "yaml_json_marshal"

    def test_cobra_cli_dispatch(self) -> None:
        assert prospector._categorize_candidate(
            "rootCmd.Run", "cli/cmd/root.go", "go",
        ) == "cobra_cli_dispatch"

    def test_goroutine_lifecycle_on_gc_method(self) -> None:
        assert prospector._categorize_candidate(
            "Worker.GC", "pkg/worker.go", "go",
        ) == "goroutine_lifecycle"

    def test_swagger_restapi_path(self) -> None:
        assert prospector._categorize_candidate(
            "Server.Handler", "api/v2/restapi/server.go", "go",
        ) == "swagger_generated"

    def test_memberlist_callbacks_path(self) -> None:
        assert prospector._categorize_candidate(
            "NodeMeta.Delegate", "pkg/cluster/delegate.go", "go",
        ) == "memberlist_callbacks"

    def test_pipeline_stage_exec(self) -> None:
        assert prospector._categorize_candidate(
            "stage.Exec", "pipeline.py", "python",
        ) == "pipeline_stage_dispatch"

    def test_cross_language_api_path(self) -> None:
        assert prospector._categorize_candidate(
            "client.send", "proto/client.py", "python",
        ) == "cross_language_api"

    def test_handler_or_dto_fallback(self) -> None:
        # Name contains "_request" but does NOT match the event-handler
        # prefix regex (no `on`/`handle`/`toggle` prefix) and does not end
        # in `handler|listener|callback`. Those properties ensure the
        # event_handler_callback rule above does not preempt us.
        assert prospector._categorize_candidate(
            "process_request_body", "lib/auth.py", "python",
        ) == "handler_or_dto"


class TestCategorizerStructuralRules:
    """Rules that identify structurally-generated or framework code."""

    def test_k8s_deepcopy_zz_generated(self) -> None:
        assert prospector._categorize_candidate(
            "Endpoint.DeepCopyInto",
            "pkg/apis/v1/zz_generated.deepcopy.go", "go",
        ) == "k8s_deepcopy_generated"

    def test_migration_script_path(self) -> None:
        assert prospector._categorize_candidate(
            "upgrade", "superset/migrations/versions/abc.py", "python",
        ) == "migration_script"

    def test_migration_script_name(self) -> None:
        assert prospector._categorize_candidate(
            "state_forwards", "myapp/schema.py", "python",
        ) == "migration_script"

    def test_storybook_story_tsx(self) -> None:
        assert prospector._categorize_candidate(
            "Example", "src/Button.stories.tsx", "typescript",
        ) == "storybook_story"

    def test_test_fixture_path(self) -> None:
        assert prospector._categorize_candidate(
            "MyTest.helper", "src/tests/test_foo.py", "python",
        ) == "test_fixture_or_helper"

    def test_airflow_operator_execute(self) -> None:
        assert prospector._categorize_candidate(
            "BashOperator.execute",
            "providers/bash/operators/bash.py", "python",
        ) == "airflow_operator_hook"

    def test_django_management_command(self) -> None:
        assert prospector._categorize_candidate(
            "Command.handle",
            "myapp/management/commands/mycmd.py", "python",
        ) == "django_management_command"

    def test_airflow_provider_entry_tail(self) -> None:
        assert prospector._categorize_candidate(
            "get_provider_info", "providers/ydb/__init__.py", "python",
        ) == "airflow_provider_entry"

    def test_openlineage_facet_tail(self) -> None:
        assert prospector._categorize_candidate(
            "MyHook.get_openlineage_facets_on_complete",
            "providers/common/hooks.py", "python",
        ) == "openlineage_facet"


class TestCategorizerRust:
    """Language-gated Rust rules."""

    def test_rust_trait_impl_drop(self) -> None:
        assert prospector._categorize_candidate(
            "Transaction::drop", "crates/wasi/src/p1.rs", "rust",
        ) == "rust_trait_impl"

    def test_visitor_pattern(self) -> None:
        assert prospector._categorize_candidate(
            "CodeGen::visit_i64_load", "winch/codegen/src/visitor.rs", "rust",
        ) == "visitor_pattern"

    def test_rust_auto_trait_assert(self) -> None:
        assert prospector._categorize_candidate(
            "_assert_send_sync", "crates/foo/src/lib.rs", "rust",
        ) == "rust_auto_trait_assert"

    def test_rust_instruction_descriptor(self) -> None:
        # Use a method name that is NOT in the trait-impl set (drop/fmt/
        # clone/emit/...). `is_move` is a wasmtime-specific Inst predicate
        # so it hits the instruction-descriptor rule cleanly.
        assert prospector._categorize_candidate(
            "Inst::is_move", "cranelift/codegen/src/isa/x64/inst/mod.rs",
            "rust",
        ) == "rust_instruction_descriptor"

    def test_wasi_view_binding(self) -> None:
        assert prospector._categorize_candidate(
            "WasiSocketsCtxView::subscribe",
            "crates/wasi/src/sockets.rs", "rust",
        ) == "wasi_view_binding"

    def test_rust_ffi_or_internal_snake_case(self) -> None:
        assert prospector._categorize_candidate(
            "wasmtime_fiber_init", "crates/fiber/src/unix.rs", "rust",
        ) == "rust_ffi_or_internal"


class TestCategorizerPython:
    """Language-gated Python rules."""

    def test_python_dunder_method(self) -> None:
        # Path does not contain /migrations/ so the migration_script rule
        # (which runs earlier) does not preempt.
        assert prospector._categorize_candidate(
            "Node.__repr__", "django/utils/graph.py", "python",
        ) == "python_dunder_method"

    def test_python_orm_tail(self) -> None:
        assert prospector._categorize_candidate(
            "SQLCompiler.as_sql", "django/db/models/sql/compiler.py",
            "python",
        ) == "python_orm_dispatch"

    def test_python_orm_class(self) -> None:
        assert prospector._categorize_candidate(
            "QuerySet.filter_chain", "django/db/models/query.py", "python",
        ) == "python_orm_dispatch"

    def test_python_airflow_tail(self) -> None:
        assert prospector._categorize_candidate(
            "GCSHook.get_conn", "providers/google/hooks/gcs.py", "python",
        ) == "python_airflow_framework"

    def test_python_airflow_class(self) -> None:
        assert prospector._categorize_candidate(
            "AirflowConfigParser.read_dict",
            "airflow-core/configuration.py", "python",
        ) == "python_airflow_framework"

    def test_python_service_dispatch(self) -> None:
        assert prospector._categorize_candidate(
            "MyService.start", "services/foo.py", "python",
        ) == "python_service_dispatch"


class TestCategorizerTypeScript:
    """Language-gated TS/JS rules."""

    def test_react_lifecycle_componentDidMount(self) -> None:
        assert prospector._categorize_candidate(
            "MyComp.componentDidMount",
            "src/MyComp.tsx", "typescript",
        ) == "react_lifecycle_method"

    def test_redux_mapper(self) -> None:
        assert prospector._categorize_candidate(
            "Page.mapStateToProps", "src/Page.tsx", "typescript",
        ) == "redux_mapper"

    def test_apollo_plugin_lifecycle(self) -> None:
        assert prospector._categorize_candidate(
            "MyPlugin.requestDidStart",
            "packages/server/src/plugin.ts", "typescript",
        ) == "apollo_plugin_lifecycle"

    def test_superset_chart_plugin(self) -> None:
        # `transformProps` is also in the redux-mapper set (which runs
        # earlier), so use a non-overlapping tail like `controlPanel`
        # that is uniquely claimed by the superset_chart_plugin rule.
        assert prospector._categorize_candidate(
            "controlPanel",
            "plugins/chart-foo/src/plugin/controlPanel.ts", "typescript",
        ) == "superset_chart_plugin"

    def test_react_hook(self) -> None:
        assert prospector._categorize_candidate(
            "useFoo", "src/hooks/useFoo.ts", "typescript",
        ) == "react_hook"

    def test_ui_event_handler(self) -> None:
        assert prospector._categorize_candidate(
            "Button.onClick", "src/Button.tsx", "typescript",
        ) == "ui_event_handler"

    def test_ui_event_handler_togglePopover(self) -> None:
        assert prospector._categorize_candidate(
            "togglePopover", "src/Popover.tsx", "typescript",
        ) == "ui_event_handler"

    def test_redux_action_reducer_path(self) -> None:
        assert prospector._categorize_candidate(
            "saveSomething",
            "src/dashboard/actions/dashboard.ts", "typescript",
        ) == "redux_action_reducer"

    def test_tsx_component_export(self) -> None:
        assert prospector._categorize_candidate(
            "Button", "src/components/Button.tsx", "typescript",
        ) == "tsx_component_export"

    def test_ts_ui_config_field(self) -> None:
        assert prospector._categorize_candidate(
            "columns", "src/explore/controls.ts", "typescript",
        ) == "ts_ui_config_field"


class TestCategorizerJava:
    """Language-gated Java/Kotlin/Scala rules."""

    def test_kafka_streams_internal(self) -> None:
        assert prospector._categorize_candidate(
            "KStream.filter",
            "streams/src/main/java/org/apache/kafka/streams/kstream/KStream.java",
            "java",
        ) == "kafka_streams_internal"

    def test_java_bean_accessor_getter(self) -> None:
        assert prospector._categorize_candidate(
            "User.getName", "src/main/java/com/example/User.java", "java",
        ) == "java_bean_accessor"

    def test_spring_bean_config_suffix(self) -> None:
        assert prospector._categorize_candidate(
            "MyAutoconfiguration",
            "src/main/java/com/example/MyAutoconfiguration.java", "java",
        ) == "spring_bean_config"

    def test_java_builder_method(self) -> None:
        assert prospector._categorize_candidate(
            "RequestBuilder.build", "src/main/java/Request.java", "java",
        ) == "java_builder_method"

    def test_java_interface_impl_tail(self) -> None:
        assert prospector._categorize_candidate(
            "ParserImpl.parse", "src/main/java/Parser.java", "java",
        ) == "java_interface_impl"


class TestCategorizerGo:
    """Language-gated Go rules."""

    def test_go_init_function(self) -> None:
        assert prospector._categorize_candidate(
            "init", "cmd/app/main.go", "go",
        ) == "go_init_function"

    def test_go_stringer_error_interface(self) -> None:
        assert prospector._categorize_candidate(
            "MyErr.Error", "pkg/err.go", "go",
        ) == "go_stringer_error_interface"

    def test_go_sort_interface_len(self) -> None:
        assert prospector._categorize_candidate(
            "ByAge.Len", "pkg/sort.go", "go",
        ) == "go_sort_interface"

    def test_go_sort_by_name_suffix(self) -> None:
        assert prospector._categorize_candidate(
            "sortByName", "pkg/util.go", "go",
        ) == "go_sort_interface"

    def test_go_kubernetes_watcher(self) -> None:
        assert prospector._categorize_candidate(
            "PodWatcher.handle", "pkg/controller/pod.go", "go",
        ) == "go_kubernetes_watcher"

    def test_go_metrics_registration(self) -> None:
        assert prospector._categorize_candidate(
            "Metrics.Inc", "pkg/metrics/metrics.go", "go",
        ) == "go_metrics_registration"

    def test_go_lifecycle_close(self) -> None:
        assert prospector._categorize_candidate(
            "Connection.Close", "pkg/net/conn.go", "go",
        ) == "go_lifecycle_method"

    def test_go_byte_order_to_host(self) -> None:
        assert prospector._categorize_candidate(
            "Packet.ToHost", "pkg/net/packet.go", "go",
        ) == "go_byte_order_conversion"

    def test_go_table_row(self) -> None:
        assert prospector._categorize_candidate(
            "Stats.TableRow", "pkg/output/table.go", "go",
        ) == "go_table_printer_interface"

    def test_go_event_callback(self) -> None:
        assert prospector._categorize_candidate(
            "Filter.OnBuildFilter", "pkg/filter.go", "go",
        ) == "go_event_callback"

    def test_go_generic_accessor_tail(self) -> None:
        assert prospector._categorize_candidate(
            "Node.Name", "pkg/graph/node.go", "go",
        ) == "go_generic_accessor"

    def test_go_prometheus_interface(self) -> None:
        assert prospector._categorize_candidate(
            "Metric.LabelValues", "promql/metrics.go", "go",
        ) == "go_prometheus_interface"

    def test_cilium_bpf_dispatch(self) -> None:
        assert prospector._categorize_candidate(
            "BPFOps.Install", "pkg/bpf/ops.go", "go",
        ) == "cilium_bpf_dispatch"

    def test_go_server_manager_method(self) -> None:
        # Avoid /rpc/ in the path — that matches the earlier
        # cross_language_api rule and would preempt this one.
        assert prospector._categorize_candidate(
            "Server.serve", "pkg/net/server.go", "go",
        ) == "go_server_manager_method"


class TestCategorizerGenericFallbacks:
    """Cross-language fallback rules that apply when no language-specific
    rule matched.
    """

    def test_factory_constructor_prefix(self) -> None:
        assert prospector._categorize_candidate(
            "newClient", "lib/client.go", "unknown",
        ) == "factory_constructor"

    def test_factory_of_suffix(self) -> None:
        assert prospector._categorize_candidate(
            "Foo.of", "lib/foo.py", "unknown",
        ) == "factory_constructor"

    def test_predicate_validator_is(self) -> None:
        assert prospector._categorize_candidate(
            "isReady", "lib/state.js", "unknown",
        ) == "predicate_validator"

    def test_event_handler_callback_on_prefix(self) -> None:
        assert prospector._categorize_candidate(
            "onClose", "lib/socket.c", "unknown",
        ) == "event_handler_callback"

    def test_java_dto_field_suffix(self) -> None:
        assert prospector._categorize_candidate(
            "Config.serverName", "src/main/java/Config.java", "java",
        ) == "java_dto_field"

    def test_uncategorized_when_no_rule_matches(self) -> None:
        assert prospector._categorize_candidate(
            "arbitraryThing", "weird/path.xyz", "unknown",
        ) == "uncategorized"

    def test_language_argument_defaults_to_empty_string(self) -> None:
        """Legacy callers that omit ``language`` get the cross-language
        subset of rules only; language-gated rules are skipped.
        """
        # "init" without go_init_function rule → factory_constructor
        # fallback does not match (no capital after 'new'), and "init" is
        # not in any generic tail set, so it lands in uncategorized.
        assert prospector._categorize_candidate(
            "init", "pkg/foo.go",
        ) == "uncategorized"


class TestCategorizerIntegration:
    """Verify the categorizer is actually invoked from the aggregator."""

    def test_run_prospecting_uses_language_field(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """``run_prospecting`` must pass the JSON ``language`` field into
        the categorizer so language-gated rules fire.
        """
        pool = tmp_path / "pool"
        pool.mkdir()
        repo = pool / "polyrepo"
        repo.mkdir()
        # Polyglot repo so the filter lets it through
        for i in range(15):
            (repo / f"go{i}.go").write_text("package main")
        for i in range(15):
            (repo / f"py{i}.py").write_text("def f(): pass")
        output_dir = tmp_path / "out"

        # The fake candidate has a name that only matches when language=='go'
        # (go_stringer_error_interface). If language is dropped, the
        # candidate falls through to 'uncategorized'.
        fake_result = {
            "summary": {"total_candidates": 1},
            "dead_candidates": [
                {
                    "name": "MyErr.Error",
                    "path": "pkg/err.go",
                    "language": "go",
                    "lines_of_code": 3,
                    "cross_language_hits": 0,
                    "path_shape_boost": 0,
                },
            ],
        }
        monkeypatch.setattr(
            prospector, "_run_hypergumbo", lambda repo_path: fake_result,
        )

        summary = prospector.run_prospecting(
            pool, ["polyrepo"], output_dir, include_monoglot=False,
        )
        # The candidate should be routed to go_stringer_error_interface,
        # NOT uncategorized.
        assert "go_stringer_error_interface" in summary["category_counts"]
        assert summary["category_counts"]["go_stringer_error_interface"] == 1
        assert summary["category_counts"].get("uncategorized", 0) == 0
