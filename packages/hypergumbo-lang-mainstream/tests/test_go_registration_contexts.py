# SPDX-License-Identifier: AGPL-3.0-or-later
"""A local Go function handed to a library is a usage context a row can name (WI-movur).

Go hands a framework its user code as a function VALUE, in two shapes:

- a keyed FIELD of a composite literal of the library's type,
  ``&cobra.Command{RunE: runServe}``, ``&cli.App{Action: run}`` (also the
  element whose type is elided, ``[]*cli.Command{{Action: serve}}``, and the
  assignment ``app.Action = run``), and
- an ARGUMENT of a call to library code, ``r.Use(auth)``,
  ``c.AddFunc("@daily", sweep)``, ``cron.FuncJob(cleanup)``.

go.py wrote a ``calls`` edge for each, but no usage context, so a framework
row could not tell a cobra handler or a gin middleware from any other
function: the deleted ``decorator:`` rows of cli-go.yaml and go-web.yaml
never fired. The analyzer now records one context per such value, named
``<package>.<Type>.<Field>`` (the package by the identifier its import path
gives, so an aliased import names the same package) or the callee as written
(``r.Use``; a package-qualified callee by its package identifier). It records
one only when a shipped ``usage:`` row could match that name
(``usage_row_names_call``): the rows are the list of names, not go.py.

Controls: a field of a LOCAL type, a call to a LOCAL function or method, a
value that is no local function (a library value, a call, a closure), and a
library name no row has (``cobra.Command.PersistentPreRun``, ``sort.Slice``)
record nothing.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/go.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_core.ir import Symbol, UsageContext
from hypergumbo_lang_mainstream.go import analyze_go

_GO_MOD = "module example.com/m\n\ngo 1.22\n"


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text(_GO_MOD)
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return repo


def _contexts(repo: Path) -> tuple[list[UsageContext], dict[str, Symbol]]:
    result = analyze_go(repo)
    return list(result.usage_contexts), {s.id: s for s in result.symbols}


def _found(repo: Path) -> set[tuple[str, str, str]]:
    """(context name, position, name of the symbol referred to) per context."""
    contexts, by_id = _contexts(repo)
    return {
        (c.context_name, c.position, by_id[c.symbol_ref].name)
        for c in contexts if c.symbol_ref in by_id
    }


def _names(repo: Path) -> set[str]:
    contexts, _ = _contexts(repo)
    return {c.context_name for c in contexts}


_COBRA_ROOT = """package cmd

import "github.com/spf13/cobra"

var rootCmd = &cobra.Command{
	Use:              "fx",
	Run:              rootRun,
	PersistentPreRun: setupLogging,
}

var serveCmd = &cobra.Command{
	Use:  "serve",
	RunE: runServe,
}

var versionCmd = &cobra.Command{
	Use: "version",
	Run: func(cmd *cobra.Command, args []string) { versionHelper() },
}

type migrateOptions struct{ cmd *cobra.Command }

func (o *migrateOptions) run(cmd *cobra.Command, args []string) error { return nil }

func newMigrateCmd() *migrateOptions {
	o := &migrateOptions{}
	o.cmd = &cobra.Command{Use: "migrate", RunE: o.run}
	return o
}

type job struct {
	Name string
	Run  func()
}

var jobs = []job{{Name: "x", Run: notACommand}}
"""

_COBRA_HANDLERS = """package cmd

import "github.com/spf13/cobra"

func rootRun(cmd *cobra.Command, args []string)            {}
func runServe(cmd *cobra.Command, args []string) error      { return nil }
func setupLogging(cmd *cobra.Command, args []string)       {}
func versionHelper()                                       {}
func notACommand()                                         {}
"""


class TestLiteralField:
    """A keyed field of a library type's composite literal."""

    def _repo(self, tmp_path: Path) -> Path:
        return _repo(tmp_path, {
            "cmd/root.go": _COBRA_ROOT, "cmd/handlers.go": _COBRA_HANDLERS,
        })

    def test_a_handler_in_another_file_of_the_package_is_referred_to(
        self, tmp_path: Path,
    ) -> None:
        found = _found(self._repo(tmp_path))
        assert ("cobra.Command.Run", "Run", "rootRun") in found
        assert ("cobra.Command.RunE", "RunE", "runServe") in found

    def test_a_method_value_is_referred_to(self, tmp_path: Path) -> None:
        found = _found(self._repo(tmp_path))
        assert ("cobra.Command.RunE", "RunE", "migrateOptions.run") in found

    def test_a_field_no_row_names_is_not_recorded(self, tmp_path: Path) -> None:
        # reach: the same literal's Run field IS recorded (first test)
        names = _names(self._repo(tmp_path))
        assert "cobra.Command.Run" in names
        assert "cobra.Command.PersistentPreRun" not in names

    def test_a_local_type_and_a_closure_are_not_recorded(
        self, tmp_path: Path,
    ) -> None:
        contexts, by_id = _contexts(self._repo(tmp_path))
        refs = {by_id[c.symbol_ref].name for c in contexts if c.symbol_ref in by_id}
        assert "rootRun" in refs  # reach
        assert "notACommand" not in refs  # job{Run: ...}: job is local
        assert "versionHelper" not in refs  # called inside a closure
        assert all(c.symbol_ref for c in contexts)

    def test_elided_element_type_and_an_aliased_import(
        self, tmp_path: Path,
    ) -> None:
        repo = _repo(tmp_path, {"main.go": """package main

import ucli "github.com/urfave/cli/v2"

func appAction(c *ucli.Context) error   { return nil }
func serveAction(c *ucli.Context) error { return nil }
func listAction(c *ucli.Context) error  { return nil }

var listCommand = &ucli.Command{Name: "list", Action: listAction}

func main() {
	app := &ucli.App{
		Action: appAction,
		Commands: []*ucli.Command{
			{Name: "serve", Action: serveAction},
			listCommand,
		},
	}
	_ = app
}
"""})
        found = _found(repo)
        assert ("cli.App.Action", "Action", "appAction") in found
        assert ("cli.Command.Action", "Action", "serveAction") in found
        assert ("cli.Command.Action", "Action", "listAction") in found

    def test_a_map_value_and_an_in_module_or_local_named_type(
        self, tmp_path: Path,
    ) -> None:
        repo = _repo(tmp_path, {
            "internal/cobra/cobra.go": (
                "package cobra\n\ntype Command struct{ Run func() }\n"
            ),
            "own.go": (
                "package main\n\n"
                'import own "example.com/m/internal/cobra"\n\n'
                "func inModule() {}\n\n"
                "var mine = &own.Command{Run: inModule}\n"
            ),
            "main.go": """package main

import (
	"github.com/spf13/cobra"
)

func mapped(cmd *cobra.Command, args []string) {}
func named(cmd *cobra.Command, args []string)  {}

type commands []*cobra.Command

var byName = map[string]*cobra.Command{"m": {Run: mapped}}
var list = commands{{Run: named}}
""",
        })
        found = _found(repo)
        assert ("cobra.Command.Run", "Run", "mapped") in found
        refs = {ref for _, _, ref in found}
        # a package of this module, though named like the library
        assert "inModule" not in refs
        assert "named" not in refs  # a local slice type's element is not read

    def test_the_assignment_form(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"main.go": """package main

import "github.com/urfave/cli"

func legacyAction(c *cli.Context) error { return nil }
func other(c *cli.Context) error        { return nil }

type local struct{ Action func(*cli.Context) error }

func main() {
	app := cli.NewApp()
	app.Action = legacyAction
	l := &local{}
	l.Action = other
}
"""})
        found = _found(repo)
        assert ("cli.App.Action", "Action", "legacyAction") in found
        assert not any(ref == "other" for _, _, ref in found)


_GIN = """package main

import (
	"sort"

	"github.com/gin-gonic/gin"
	"github.com/robfig/cron/v3"
)

func requestID(c *gin.Context) {}
func apiOnly(c *gin.Context)   {}
func reportJob()               {}
func cleanupJob()              {}
func less(i, j int) bool       { return i < j }
func register(f func())        { f() }
func localCallback()           {}

type counter struct{}

func (k *counter) Use(f func(*gin.Context)) {}

func setup() {
	r := gin.New()
	r.Use(gin.Logger(), requestID)
	api := r.Group("/api")
	api.Use(apiOnly)
	k := &counter{}
	k.Use(requestID)
	register(localCallback)
	notDefinedHere(localCallback)
	xs := []int{2, 1}
	sort.Slice(xs, less)
	r.GET("/ping", requestID)
	c := cron.New()
	c.AddFunc("@every 1m", reportJob)
	c.Schedule(cron.Every(5), cron.FuncJob(cleanupJob))
}
"""


class TestCallArgument:
    """A local function passed as an argument of a library call."""

    def test_use_addfunc_and_a_wrapper_one_call_deep(self, tmp_path: Path) -> None:
        found = _found(_repo(tmp_path, {"main.go": _GIN}))
        assert ("r.Use", "args[1]", "requestID") in found
        assert ("api.Use", "args[0]", "apiOnly") in found
        assert ("c.AddFunc", "args[1]", "reportJob") in found
        assert ("cron.FuncJob", "args[0]", "cleanupJob") in found

    def test_local_callees_and_unnamed_library_calls_are_not_recorded(
        self, tmp_path: Path,
    ) -> None:
        contexts, by_id = _contexts(_repo(tmp_path, {"main.go": _GIN}))
        assert contexts  # reach
        names = {c.context_name for c in contexts}
        refs = {(c.context_name, by_id[c.symbol_ref].name) for c in contexts}
        assert "k.Use" not in names  # counter.Use is repository code
        # a local register, and a bare callee (own package or a dot import)
        assert not any(ref == "localCallback" for _, ref in refs)
        assert "sort.Slice" not in names  # no row names it
        # a route call keeps the route extractor's one context, no twin
        assert [c.position for c in contexts if c.context_name == "r.GET"] == ["args[last]"]
        # gin.Logger() is a call, not a local function: only args[1] is recorded
        assert {c.position for c in contexts if c.context_name == "r.Use"} == {"args[1]"}


def _enriched(
    tmp_path: Path, files: dict[str, str], *frameworks: str,
) -> dict[str, set[str]]:
    """Concepts by symbol name after the shipped rows ran over the analyzer's output."""
    repo = _repo(tmp_path, files)
    result = analyze_go(repo)
    symbols = list(result.symbols)
    for symbol in symbols:
        symbol.path = Path(symbol.path).relative_to(repo).as_posix()
    clear_pattern_cache()
    enrich_symbols(symbols, set(frameworks), list(result.usage_contexts))
    return {
        s.name: {c["concept"] for c in (s.meta or {}).get("concepts", [])}
        for s in symbols if s.kind in ("function", "method")
    }


class TestShippedRows:
    """The rows stamp the concept on what the analyzer records."""

    def test_cli_go_command_rows(self, tmp_path: Path) -> None:
        concepts = _enriched(tmp_path, {
            "cmd/root.go": _COBRA_ROOT, "cmd/handlers.go": _COBRA_HANDLERS,
        }, "cli-go")
        assert "command" in concepts["runServe"]
        assert "command" in concepts["rootRun"]
        assert "command" in concepts["migrateOptions.run"]
        assert "command" not in concepts["setupLogging"]  # a hook: no row
        assert "command" not in concepts["notACommand"]

    def test_go_web_middleware_row(self, tmp_path: Path) -> None:
        concepts = _enriched(tmp_path, {"main.go": _GIN}, "gin")
        assert "middleware" in concepts["requestID"]
        assert "middleware" in concepts["apiOnly"]
        assert "middleware" not in concepts["localCallback"]
        assert "middleware" not in concepts["less"]

    def test_go_cron_scheduled_task_rows(self, tmp_path: Path) -> None:
        concepts = _enriched(tmp_path, {"main.go": _GIN}, "go-cron")
        assert "scheduled_task" in concepts["reportJob"]
        assert "scheduled_task" in concepts["cleanupJob"]
        assert "scheduled_task" not in concepts["requestID"]
