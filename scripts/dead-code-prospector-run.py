#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lightweight one-shot dead-code-maybe prospecting run.

Runs `hypergumbo dead-code-maybe --format json --exclude-annotated` on a
user-specified set of repos and aggregates the results by linker gap
category.  Produces a single JSON artifact ranking candidates globally.

Per WI-tubot (created from WI-duroz human directive 2026-04-10): this
is an explicit ONE-SHOT variant, not a recurring bakeoff cohort.  The
goal is to surface which linker gaps appear most frequently across a
20-30 repo polyglot subset, prioritizing linker investment.

Usage:
    python scripts/dead-code-prospector-run.py \
        --pool ~/ALL_REPOS/whole_bunch_of_repos \
        --repos repo1,repo2,repo3 \
        --output ~/hypergumbo_lab_notebook/prospector_runs/run-YYYYMMDD/

If --repos is omitted, uses a built-in default selection of 20
polyglot repos covering Go, Java, Python, JS/TS, and Rust.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Default polyglot subset — spans multiple languages to maximize
# cross-language linker gap detection.  User may override with --repos.
_DEFAULT_REPOS = [
    # Go
    "alertmanager", "prometheus", "kafka",
    "containerd", "buildkit",
    # Java
    "spring-boot", "trino",
    # Python
    "airflow", "django", "superset",
    # JS/TS
    "vscode", "apollo-server",
    # Rust
    "wasmtime", "arti",
    # Polyglot / bindings
    "envoy", "cilium",
    # Smaller for variety
    "cowboy", "vapor",
]


# WI-zafab filter 1: polyglot-only detection. Mapping of file extension
# to a language label for the harness-level polyglot check. Limited to
# the languages the prospector cares about (the eight covered by the
# default cohort plus a few common adjacents). Build files, shell
# scripts, and config files are intentionally excluded — they don't
# count toward "polyglot" because every repo has them.
_LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".scala": "scala", ".sc": "scala",
    ".rs": "rust",
    ".rb": "ruby",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript",
    ".mjs": "javascript", ".cjs": "javascript",
    ".cs": "csharp",
    ".swift": "swift",
    ".m": "objc", ".mm": "objc",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".c": "c", ".h": "c",
    ".ex": "elixir", ".exs": "elixir",
    ".erl": "erlang",
    ".clj": "clojure", ".cljs": "clojure",
    ".elm": "elm",
    ".dart": "dart",
    ".php": "php",
}


def _count_languages_by_extension(repo_path: Path) -> dict[str, int]:
    """Walk the repo and count source files by language extension.

    Skips common ignore directories (vendor/, node_modules/, .git/, etc.)
    so vendored dependencies don't inflate the count and accidentally
    promote a monoglot repo to "polyglot".

    Returns a ``{language: file_count}`` dict.
    """
    _IGNORE_DIRS = {
        ".git", "node_modules", "vendor", "third_party", "third-party",
        ".venv", "venv", "env", ".env", "build", "dist", "target",
        ".gradle", ".idea", ".vscode", "__pycache__",
    }
    counts: dict[str, int] = {}
    for entry in repo_path.rglob("*"):
        if not entry.is_file():
            continue
        # Skip if any path component is an ignored directory.
        try:
            rel_parts = entry.relative_to(repo_path).parts
        except ValueError:  # pragma: no cover - rglob always yields relative
            continue
        if any(part in _IGNORE_DIRS for part in rel_parts[:-1]):
            continue
        lang = _LANG_BY_EXT.get(entry.suffix.lower())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return counts


def _is_polyglot_repo(
    lang_counts: dict[str, int],
    threshold: int = 10,
) -> bool:
    """Return True if the repo has at least two languages above the threshold.

    The threshold filters out incidental files (e.g., a Go project's two
    test JS files for a UI fixture) so the check identifies repos with
    meaningful production code in multiple languages — exactly the
    cohort where cross-language linker gaps drive dead-code-maybe noise.

    WI-zafab filter 1: monoglot repos are skipped because a dead Python
    function in a Python-only repo is almost never a missed cross-language
    linker — it's either real dead code or a missing language-internal
    framework hook (different fix class).
    """
    above_threshold = sum(1 for count in lang_counts.values() if count >= threshold)
    return above_threshold >= 2


# ---------------------------------------------------------------------------
# Categorization rules (WI-vupin v5)
# ---------------------------------------------------------------------------
#
# Each entry is (predicate, category_name). The first matching rule wins,
# so order is important — specific patterns precede broader ones. The
# predicate receives (tail, name, path, lower_name, lower_path, lang)
# and returns bool. Using a table rather than a nested if-chain keeps the
# rules testable in isolation and the order explicit.
#
# Success criterion from WI-tubot: the rule set must leave <50% of
# candidates `uncategorized` on the 2026-04-11 prospector corpus. v5
# lands at ~43.5% (baseline was 94%). Each iteration improves by less
# than 3 percentage points now, so additional narrow rules have hit
# diminishing returns on heuristic-only categorization. See WI-vupin
# discussion for the analytic reflection on why further heuristic
# expansion overfits the corpus.


_RE_YAML_JSON = re.compile(r"unmarshal|marshaljson|unmarshalyaml")
_RE_STAGE_EXEC = re.compile(r"\.exec.*stage|stage.*\.exec")
_RE_ZZ_GEN = re.compile(r"zz_generated|\.pb\.go|_generated\.go")
_RE_DEEPCOPY = re.compile(r"DeepCopy(Into)?$|\.DeepCopy$")
_RE_MIGRATION_PATH = re.compile(r"/migrations/|/alembic/|/db/migrate/")
_RE_MIGRATION_FUNC = re.compile(
    r"^(upgrade|downgrade|forwards|backwards|state_forwards|state_backwards|rename_forward|rename_backward)$",
    re.IGNORECASE,
)
_RE_STORYBOOK = re.compile(r"\.stories\.(t|j)sx?$")
_RE_TEST_PATH = re.compile(
    r"(^|/)(tests?|testing|fixtures?|conftest|spec|e2e|__tests__)(/|$)",
)
_RE_AIRFLOW_OP = re.compile(
    r"(Operator|Sensor|Hook|Transfer|Trigger)\."
    r"(execute|execute_complete|poke|on_kill|pre_execute|post_execute"
    r"|_prepare|_execute|_build|_hook|_get_hook)$",
)
_RE_DJANGO_CMD = re.compile(r"Command\.(handle|execute|add_arguments)$")
_RE_CROSS_LANG_PATH = re.compile(
    r"(?:^|/)(api|rpc|proto|ffi|native|bindings|bridge|grpc)(?:$|/)",
    re.IGNORECASE,
)
_RE_CROSS_LANG_NAME = re.compile(
    r"(^(rpc|grpc|api)_|_(rpc|grpc|api)$)", re.IGNORECASE,
)
_RE_AIRFLOW_ENTRY = frozenset({
    "get_provider_info", "get_base_airflow_version_tuple",
    "provider_user_agent", "get_cli_commands",
})
_RE_OPENLINEAGE = re.compile(
    r"get_openlineage_facets_(on_start|on_complete|on_failure|on_running)",
)
_RE_RUST_TRAIT = re.compile(
    r"::(drop|fmt|clone|eq|ne|hash|partial_cmp|cmp|from|into|try_from"
    r"|try_into|deref|deref_mut|as_ref|as_mut|default|next|size_hint|load"
    r"|store|lower|lower_branch|patch|define|emit|kind|io|generate)$",
    re.IGNORECASE,
)
_RE_RUST_VISITOR = re.compile(r"^visit_|::visit_|\.visit_[A-Za-z_]+$")
_RE_RUST_INST_DESCR = re.compile(
    r"^(Inst|EmitState|LabelUse|Signature|MachLabel|FuncEnvironment"
    r"|IsleContext|ControlStackFrame|VCodeBuilder|Interpreter"
    r"|CallThreadState|ByteCountOutOfBoundsKind|Compiler|CodeGen"
    r"|CodeGenerator|MachInst)::",
)
_RE_WASI_VIEW = re.compile(
    r"^(WasiSocketsCtxView|WasiHttpImpl|WasiNnView|WasiCryptoView"
    r"|WasiView|WasiCtx|Ctx)::",
)
_RE_RUST_FFI = re.compile(r"^[a-z][a-z0-9_]+$")
_RE_PY_DUNDER = re.compile(r"^__\w+__$")
_PY_ORM_TAILS = frozenset({
    "process_rhs", "iter_references", "get_group_by_cols",
    "set_source_expressions", "get_source_expressions", "get_prep_lookup",
    "value_from_datadict", "get_context_data", "get_link", "as_sql",
    "as_oracle", "as_mysql", "as_postgresql", "as_sqlite",
    "resolve_expression", "deconstruct", "to_python", "to_orm", "eval",
    "from_db_value", "get_queryset", "get_form", "full_clean",
    "get_absolute_url", "_remake_table", "_get_field", "render_content",
})
_RE_PY_ORM_CLASS = re.compile(
    r"(DatabaseOperations|QuerySet|Query|BaseDatabaseSchemaEditor"
    r"|DatabaseSchemaEditor|Field|ModelAdmin|SessionBase|SessionStore"
    r"|Serializer|Validator|Paginator|HttpResponseBase|SchemaEditor"
    r"|Compiler|CursorWrapper|CursorDebugWrapper|Node|Expression|Lookup"
    r"|Transform|BaseCache|Model|DatabaseCreation|DatabaseWrapper"
    r"|DatabaseIntrospection|BaseDatabaseWrapper|BaseDatabaseCreation"
    r"|BaseDatabaseFeatures|BaseStorage|BaseSessionManager)\.",
)
_PY_AIRFLOW_TAILS = frozenset({
    "get_conn", "test_connection", "_validate_inputs", "hook", "serialize",
    "deserialize", "render_template", "on_finish_action", "get_uri",
    "get_context", "get_spark_web_ui_address", "next_dagrun_info",
    "init_app",
})
_RE_PY_AIRFLOW_CLASS = re.compile(
    r"(AirflowConfigParser|SupersetSecurityManager"
    r"|FabAirflowSecurityManagerOverride|Paginator|Serializer|Hook"
    r"|Trigger|Operator|Sensor)\.",
)
_PY_SERVICE_TAILS = frozenset({
    "run", "validate", "render", "apply", "close", "check", "execute",
    "process", "handle", "start", "stop", "setup", "teardown", "terminate",
    "end", "notify", "async_notify", "clone", "copy", "to_dict", "encode",
    "decode", "resolve", "setup_loader", "get", "add", "set", "clear",
    "save", "delete", "write", "read",
})
_RE_REACT_LIFECYCLE = re.compile(
    r"\.(componentDidMount|componentDidUpdate|componentWillUnmount"
    r"|componentWillMount|shouldComponentUpdate|componentDidCatch"
    r"|getDerivedStateFromProps|render|constructor|getInitialState"
    r"|componentWillReceiveProps)$",
)
_REDUX_MAPPERS = frozenset({
    "mapStateToProps", "mapDispatchToProps", "transformProps", "mergeProps",
})
_APOLLO_LIFECYCLE = frozenset({
    "requestDidStart", "willSendResponse", "didResolveOperation",
    "didEncounterErrors", "serverWillStart", "formatError",
})
_SUPERSET_CHART = frozenset({
    "transformProps", "controlPanel", "buildQuery", "thumbnail",
    "getCrossFilterDataMask", "getPoints", "defaultTooltipGenerator",
    "formatValue",
})
_TS_UI_CONFIG = frozenset({
    "columns", "options", "filters", "schemes", "data", "charts",
    "dataMask", "actions", "list", "values", "ids", "index", "visibility",
    "dashboardId", "nativeFilters", "checkedKeys", "filteredColumns",
    "clearField", "shouldEmptyQueryResults", "updateMeta", "htmlContent",
    "saveSliceFailed", "sendRequest", "internalOnError", "coercedValue",
    "getChosenOptionsValue", "wfsVersionOptions", "currentUser",
    "chartsByCategory", "breakPoints", "groupby", "isAPIEnvelope",
    "isMetricOrPercentMetric", "format", "traverse", "resolve", "get",
    "has", "stop", "hide", "clear",
})
_TS_EVENT_HANDLERS = frozenset({
    "togglePopover", "openModal", "closeModal",
})
_RE_JS_EVENT = re.compile(
    r"(^|\.)(on|handle|dispatch|toggle|open|close|show|hide)[A-Z_]"
    r"|(handler|listener|callback)$",
    re.IGNORECASE,
)
_RE_KAFKA_CLASS = re.compile(
    r"(KStream|KTable|StreamsMetrics|GroupMetadataManager|KafkaRaftClient"
    r"|SharePartition|UnifiedLog|TaskManager|QuorumState|ProcessorContext"
    r"|InMemoryWindowStore|AbstractMembershipManager"
    r"|StreamsMembershipManager|KafkaConsumer|ClassicKafkaConsumer"
    r"|KafkaProducer|KafkaStreams|Worker|ConfigDef|AbstractConfig|Utils"
    r"|Admin|LeaderEpochFileCache|FileQuorumStateStore"
    r"|RecordsSnapshotReader)\.",
)
_RE_JAVA_BEAN = re.compile(
    r"^(get|set|is|has)[A-Z]|\.(get|set|is|has)[A-Z]|Bean$|Dto$|Dao$",
)
_RE_SPRING_BEAN = re.compile(
    r"(Autoconfiguration|Configuration|Properties|Bean|Configurer"
    r"|Customizer)$",
)
_RE_JAVA_BUILDER = re.compile(r"(Builder|Assembler|Factory)(\.[a-zA-Z_]+)?$")
_JAVA_IFACE_TAILS = frozenset({
    "parse", "close", "put", "add", "remove", "get", "process", "toString",
    "write", "replay", "customize", "validate", "apply", "size", "name",
    "value", "all", "type", "stop", "start", "run", "create", "error",
    "update", "load", "register", "state", "read", "initialize", "clear",
    "empty", "poll", "append", "reset", "send", "contains", "metrics",
    "flush", "convert", "resolve", "writeTo", "complete", "shutdown",
    "record", "key", "timestamp", "partition", "partitions", "matches",
    "serialize", "id",
})
_RE_GO_STRINGER = re.compile(r"\.(String|Error|GoString|Format)$")
_RE_GO_SORT = re.compile(r"\.(Len|Less|Swap)$")
_RE_GO_SORT_SUFFIX = re.compile(r"(Sort|ByName|ByTime|ByKey)$")
_RE_GO_WATCHER = re.compile(r"(Watcher|Informer|Controller|Reconciler)\.")
_RE_GO_METRICS = re.compile(r"(Metrics|Collector|Registry)\.")
_RE_GO_LIFECYCLE = re.compile(
    r"\.(Close|Start|Stop|Run|Flags|Dump|Refresh|refresh)$",
)
_RE_GO_BYTEORDER = re.compile(
    r"\.(ToHost|ToNetwork|HostToNetwork|NetworkToHost)$",
)
_RE_GO_TABLE = re.compile(r"\.(TableRow|TableHeader)$")
_RE_GO_EVENT = re.compile(r"\.OnBuild[A-Z]")
_GO_GENERIC_TAILS = frozenset({
    "Type", "Name", "Key", "Labels", "Next", "Add", "Get", "Delete",
    "Update", "Equal", "Value", "Size", "Count", "Status", "Has", "Clone",
    "Equals", "Err", "Read", "Write", "List", "Push", "Pop", "Append",
    "Release", "Lookup", "Decode", "Merge", "Encode", "Match", "DeepEqual",
    "Select", "Reset", "Upsert", "Pretty", "Commit",
})
_GO_PROM_TAILS = frozenset({
    "AppendHistogram", "LabelValues", "LabelNames", "GetFlags",
    "PositionRange", "PromQLExpr", "SetEnabled", "SetOptions",
})
_RE_CILIUM_BPF = re.compile(
    r"(BPFOps|BPFLBMaps|LBIPAM|IPCache|DNSCache|ObjectMeta"
    r"|ConnectivityTest|mapState|NoopRemoteIDCache|BGPService"
    r"|KubeProxyReplacement|EndpointUpdater|GRPCClient|WireguardAgent)\.",
)
_RE_GO_SERVER = re.compile(
    r"(Server|Listener|Handler|Agent|Manager|Client|Endpoint|Map|Node"
    r"|Config|Writer|Allocator)\.",
)
_RE_FACTORY = re.compile(
    r"^(new|create|build|make|from|of|with)[A-Z_]|\.of$|\.from$|\.build$",
)
_RE_PREDICATE = re.compile(
    r"^(is|has|can|should|was|will|are)[A-Z_]|[A-Z]is[A-Z]",
)
_RE_EVENT_HANDLER = re.compile(
    r"^(on|handle|dispatch)[A-Z_]|(handler|listener|callback)$",
    re.IGNORECASE,
)
_RE_JAVA_DTO = re.compile(
    r"(Name|Id|Type|Value|Config|Key|Data|State|Status|Metadata|Info"
    r"|Count|Size|Offset|Time|Timeout|Version|Path|Url|Host|Port)$",
)
_RE_TSX_EXT = re.compile(r"\.tsx?$")
_RE_TSX_COMPONENT = re.compile(r"^[A-Z][A-Za-z0-9]*$")
_RE_REDUX_PATH = re.compile(r"/(actions|reducers|slices)/", re.IGNORECASE)
_RE_REACT_HOOK = re.compile(r"^use[A-Z]")


def _categorize_candidate(name: str, path: str, language: str = "") -> str:
    """Group a dead-code candidate by likely linker gap category.

    Uses path + name heuristics to assign candidates to gap kinds. See
    the ``_RE_*`` / ``_*_TAILS`` tables above for rule composition. The
    language argument comes from the hypergumbo ``dead-code-maybe``
    JSON ``language`` field and is used to gate language-specific rules
    (Rust trait impls, Java JavaBean accessors, Go receiver methods,
    etc.). When missing (legacy callers), language-specific rules are
    skipped and only cross-language rules apply.
    """
    lp = path.lower()
    ln = name.lower()
    tail = name.rsplit(".", 1)[-1]

    # Cross-language / pre-established structural categories
    if _RE_YAML_JSON.search(ln):
        return "yaml_json_marshal"
    if "cli/" in lp and ("cmd" in lp or "cmd" in ln):
        return "cobra_cli_dispatch"
    if "maintenance" in ln or ".gc" in ln:
        return "goroutine_lifecycle"
    if "restapi/" in lp or "configure" in ln:
        return "swagger_generated"
    if "cluster/" in lp or "memberlist" in lp or "tlstransport" in ln:
        return "memberlist_callbacks"
    if _RE_STAGE_EXEC.search(ln):
        return "pipeline_stage_dispatch"
    if _RE_ZZ_GEN.search(lp) or _RE_DEEPCOPY.search(name):
        return "k8s_deepcopy_generated"
    if _RE_MIGRATION_PATH.search(lp) or _RE_MIGRATION_FUNC.match(name):
        return "migration_script"
    if _RE_STORYBOOK.search(lp):
        return "storybook_story"
    if _RE_TEST_PATH.search(lp):
        return "test_fixture_or_helper"
    if _RE_AIRFLOW_OP.search(name):
        return "airflow_operator_hook"
    if _RE_DJANGO_CMD.search(name):
        return "django_management_command"
    if _RE_CROSS_LANG_PATH.search(lp) or _RE_CROSS_LANG_NAME.search(name):
        return "cross_language_api"
    if tail in _RE_AIRFLOW_ENTRY:
        return "airflow_provider_entry"
    if _RE_OPENLINEAGE.search(name):
        return "openlineage_facet"

    # Rust-specific
    if language == "rust":
        if _RE_RUST_TRAIT.search(name):
            return "rust_trait_impl"
        if _RE_RUST_VISITOR.search(name):
            return "visitor_pattern"
        if tail.startswith("_assert_"):
            return "rust_auto_trait_assert"
        if _RE_RUST_INST_DESCR.match(name):
            return "rust_instruction_descriptor"
        if _RE_WASI_VIEW.match(name):
            return "wasi_view_binding"
        if _RE_RUST_FFI.match(tail) and len(tail) > 6 and "_" in tail:
            return "rust_ffi_or_internal"

    # Python dunder — applies to any language that has dotted class.__x__
    if _RE_PY_DUNDER.search(tail):
        return "python_dunder_method"

    # Python-specific
    if language == "python":
        if tail in _PY_ORM_TAILS or _RE_PY_ORM_CLASS.search(name):
            return "python_orm_dispatch"
        if tail in _PY_AIRFLOW_TAILS or _RE_PY_AIRFLOW_CLASS.search(name):
            return "python_airflow_framework"
        if tail in _PY_SERVICE_TAILS:
            return "python_service_dispatch"

    # TypeScript / JavaScript
    if language in ("typescript", "javascript"):
        if _RE_REACT_LIFECYCLE.search(name):
            return "react_lifecycle_method"
        if tail in _REDUX_MAPPERS:
            return "redux_mapper"
        if tail in _APOLLO_LIFECYCLE:
            return "apollo_plugin_lifecycle"
        if tail in _SUPERSET_CHART:
            return "superset_chart_plugin"
        if _RE_REACT_HOOK.match(tail):
            return "react_hook"
        if _RE_JS_EVENT.search(name) or tail in _TS_EVENT_HANDLERS:
            return "ui_event_handler"
        if _RE_REDUX_PATH.search(lp):
            return "redux_action_reducer"
        if _RE_TSX_EXT.search(lp) and _RE_TSX_COMPONENT.match(tail):
            return "tsx_component_export"
        if tail in _TS_UI_CONFIG:
            return "ts_ui_config_field"

    # Java / Kotlin / Scala
    if language in ("java", "kotlin", "scala"):
        if _RE_KAFKA_CLASS.search(name):
            return "kafka_streams_internal"
        if _RE_JAVA_BEAN.match(tail):
            return "java_bean_accessor"
        if _RE_SPRING_BEAN.search(name):
            return "spring_bean_config"
        if _RE_JAVA_BUILDER.search(name):
            return "java_builder_method"
        if tail in _JAVA_IFACE_TAILS:
            return "java_interface_impl"

    # Go
    if language == "go":
        if name == "init":
            return "go_init_function"
        if _RE_GO_STRINGER.search(name):
            return "go_stringer_error_interface"
        if _RE_GO_SORT.search(name) or _RE_GO_SORT_SUFFIX.search(name):
            return "go_sort_interface"
        if _RE_GO_WATCHER.search(name):
            return "go_kubernetes_watcher"
        if _RE_GO_METRICS.search(name):
            return "go_metrics_registration"
        if _RE_GO_LIFECYCLE.search(name):
            return "go_lifecycle_method"
        if _RE_GO_BYTEORDER.search(name):
            return "go_byte_order_conversion"
        if _RE_GO_TABLE.search(name):
            return "go_table_printer_interface"
        if _RE_GO_EVENT.search(name):
            return "go_event_callback"
        if tail in _GO_GENERIC_TAILS:
            return "go_generic_accessor"
        if tail in _GO_PROM_TAILS:
            return "go_prometheus_interface"
        if _RE_CILIUM_BPF.search(name):
            return "cilium_bpf_dispatch"
        if _RE_GO_SERVER.search(name):
            return "go_server_manager_method"

    # Generic fallbacks (any language)
    if _RE_FACTORY.match(tail) or tail in ("of", "from", "build", "new"):
        return "factory_constructor"
    if _RE_PREDICATE.match(tail):
        return "predicate_validator"
    if _RE_EVENT_HANDLER.search(name):
        return "event_handler_callback"
    if "handler" in ln or "_request" in ln or "_response" in ln:
        return "handler_or_dto"
    if language in ("java", "kotlin", "scala") and _RE_JAVA_DTO.search(tail):
        return "java_dto_field"
    return "uncategorized"


def _run_hypergumbo(repo_path: Path) -> dict | None:
    """Run hypergumbo dead-code-maybe on a repo and return parsed JSON."""
    try:
        result = subprocess.run(
            [
                "hypergumbo", "dead-code-maybe",
                str(repo_path),
                "--format", "json",
                "--exclude-annotated",
                "--exclude-exports",
            ],
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        print(f"  [{repo_path.name}] TIMEOUT after 10 min", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"  [{repo_path.name}] FAILED (exit {result.returncode})", file=sys.stderr)
        print(f"    stderr: {result.stderr[:200]}", file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"  [{repo_path.name}] JSON parse error: {e}", file=sys.stderr)
        return None


def run_prospecting(
    pool: Path,
    repos: list[str],
    output_dir: Path,
    *,
    include_monoglot: bool = False,
) -> dict:
    """Run dead-code-maybe on each repo and aggregate by category.

    Returns a summary dict and writes per-repo + aggregate JSON to
    output_dir. WI-zafab filter 1: monoglot repos are skipped by default
    because they almost never have cross-language linker gaps; pass
    ``include_monoglot=True`` to override (the corresponding CLI flag is
    ``--include-monoglot``).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    per_repo: dict[str, dict] = {}
    aggregate_categories: dict[str, list] = {}
    failed: list[str] = []
    skipped_monoglot: list[dict] = []

    for repo_name in repos:
        repo_path = pool / repo_name
        if not repo_path.exists():
            print(f"  [{repo_name}] SKIP (not in pool)", file=sys.stderr)
            failed.append(repo_name)
            continue
        # WI-zafab filter 1: polyglot-only check at the harness level.
        # Skip monoglot repos because dead-code in a single-language
        # codebase is almost never a missed cross-language linker.
        if not include_monoglot:
            lang_counts = _count_languages_by_extension(repo_path)
            if not _is_polyglot_repo(lang_counts):
                top_lang = max(
                    lang_counts.items(), key=lambda kv: kv[1], default=("(none)", 0),
                )
                print(
                    f"  [{repo_name}] SKIP monoglot "
                    f"(top: {top_lang[0]}={top_lang[1]} files; "
                    f"--include-monoglot to override)",
                    file=sys.stderr,
                )
                skipped_monoglot.append({
                    "repo": repo_name,
                    "languages": lang_counts,
                })
                continue
        print(f"  [{repo_name}] analyzing...", file=sys.stderr)
        result = _run_hypergumbo(repo_path)
        if result is None:
            failed.append(repo_name)
            continue
        candidates = result.get("dead_candidates", [])
        summary = result.get("summary", {})
        per_repo[repo_name] = {
            "summary": summary,
            "candidate_count": len(candidates),
        }
        # Categorize candidates and aggregate
        for c in candidates:
            cat = _categorize_candidate(
                c.get("name", ""),
                c.get("path", ""),
                c.get("language", ""),
            )
            aggregate_categories.setdefault(cat, []).append({
                "repo": repo_name,
                "name": c.get("name", ""),
                "path": c.get("path", ""),
                "language": c.get("language", ""),
                "loc": c.get("lines_of_code", 0),
                "cross_language_hits": c.get("cross_language_hits", 0),
                "path_shape_boost": c.get("path_shape_boost", 0),
            })
        # Save per-repo output
        (output_dir / f"{repo_name}.json").write_text(
            json.dumps(result, indent=2),
        )

    # Sort each category by combined score
    for cat in aggregate_categories:
        aggregate_categories[cat].sort(
            key=lambda c: -(c["cross_language_hits"] + c["path_shape_boost"]),
        )

    # Build summary
    category_counts = {
        cat: len(items) for cat, items in aggregate_categories.items()
    }
    total_candidates = sum(category_counts.values())

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pool": str(pool),
        "repos_requested": repos,
        "repos_analyzed": list(per_repo.keys()),
        "repos_failed": failed,
        "repos_skipped_monoglot": skipped_monoglot,
        "total_candidates": total_candidates,
        "category_counts": dict(sorted(
            category_counts.items(), key=lambda x: -x[1],
        )),
        "per_repo": per_repo,
        "top_by_category": {
            cat: items[:10] for cat, items in aggregate_categories.items()
        },
    }
    (output_dir / "aggregate.json").write_text(json.dumps(summary, indent=2))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lightweight one-shot dead-code-maybe prospecting run",
    )
    parser.add_argument(
        "--pool", type=Path,
        default=Path.home() / "ALL_REPOS" / "whole_bunch_of_repos",
        help="Pool directory containing repos",
    )
    parser.add_argument(
        "--repos", type=str, default=None,
        help="Comma-separated list of repo names (default: built-in subset)",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output directory (default: ~/hypergumbo_lab_notebook/"
             "prospector_runs/run-<timestamp>/)",
    )
    parser.add_argument(
        "--include-monoglot", action="store_true",
        help="WI-zafab filter 1: by default, monoglot repos are skipped "
             "because they almost never have cross-language linker gaps. "
             "Pass this flag to analyze them anyway.",
    )
    args = parser.parse_args(argv)

    if args.repos:
        repos = [r.strip() for r in args.repos.split(",") if r.strip()]
    else:
        repos = _DEFAULT_REPOS

    if args.output:
        output_dir = args.output
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = (
            Path.home() / "hypergumbo_lab_notebook" / "prospector_runs"
            / f"run-{stamp}"
        )

    print(f"Pool:    {args.pool}")
    print(f"Repos:   {len(repos)} ({', '.join(repos[:3])}...)")
    print(f"Output:  {output_dir}")
    print()

    summary = run_prospecting(
        args.pool, repos, output_dir,
        include_monoglot=args.include_monoglot,
    )

    print()
    print("=" * 60)
    print("PROSPECTING SUMMARY")
    print("=" * 60)
    print(f"Repos analyzed: {len(summary['repos_analyzed'])}")
    print(f"Repos failed:   {len(summary['repos_failed'])}")
    print(f"Repos skipped (monoglot): {len(summary['repos_skipped_monoglot'])}")
    print(f"Total candidates: {summary['total_candidates']}")
    print()
    print("Top categories by candidate count:")
    for cat, count in list(summary["category_counts"].items())[:10]:
        print(f"  {cat:<30} {count:>6}")
    print()
    print(f"Full artifact: {output_dir / 'aggregate.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
