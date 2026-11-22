# SPDX-License-Identifier: AGPL-3.0-or-later
"""A framework row keyed on ``decorator:`` that feeds an entry point names a decorator.

The matcher (``framework_patterns.Pattern.matches_all``) reads a symbol's
``meta["decorators"]``. Six analyzers write that key -- py, js_ts, java,
kotlin, scala, groovy -- and each writes it only from decorator or
annotation syntax on a declaration. A ``decorator:`` row whose name is a
CALL (``argparse.ArgumentParser``, ``kong.Parse``, Express's ``app.get``,
Electron's ``ipcMain.handle``, akka-http's ``path``) therefore never fires,
and a row in a YAML whose language has none of those analyzers never fires
whatever it names. Audit 0027 found six such rows claiming the
``entrypoint`` concept, documented as detected; the sweep (WI-jilik) found
164 call-keyed rows feeding an entry kind and deleted them. Rows feeding
concepts no entry reads were left in place and listed as residuals.

Two checks:

* the general rule: a ``decorator:`` row feeding an entry concept is in a
  YAML (or carries a ``language:`` filter) that reaches a decorator-writing
  analyzer, and its name has no call-only syntax (``new ``, ``->``, ``::``,
  ``use ``, ``(``, a ``!`` macro);
* the deleted rows of decorator-writing languages, by name: a call that
  looks like a decorator name (``app.get``) is caught only by naming it.

The entry concepts are read from ``entrypoints._detect_from_concepts``
itself (every string it compares the concept to), so a new arm extends the
check without an edit here.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from hypergumbo_core.framework_patterns import get_frameworks_dir
from hypergumbo_core.yaml_catalogs import read_catalogue_yaml

_PACKAGES = Path(__file__).resolve().parents[2]
_ENTRYPOINTS = (_PACKAGES / "hypergumbo-core" / "src" / "hypergumbo_core"
                / "entrypoints.py")

#: Languages whose analyzer writes ``meta["decorators"]`` (from decorator or
#: annotation syntax). Pinned against the analyzer sources below.
DECORATOR_WRITERS = frozenset({
    "python", "javascript", "typescript", "java", "kotlin", "scala", "groovy"})
_WRITER_FILES = {"py.py", "js_ts.py", "java.py", "kotlin.py", "scala.py",
                 "groovy.py"}

#: Syntax that never appears in a decorator or annotation name.
_CALL_ONLY = re.compile(r"new |->|::|\buse |\\\(|!\$?$|^\^go\\s")


def _entry_concepts() -> frozenset[str]:
    tree = ast.parse(_ENTRYPOINTS.read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_detect_from_concepts")
    out: set[str] = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                and node.left.id == "concept_type"):
            for comp in node.comparators:
                for const in ast.walk(comp):
                    if isinstance(const, ast.Constant) and isinstance(const.value, str):
                        out.add(const.value)
    return frozenset(out)


def _decorator_rows() -> list[tuple[str, str, str, frozenset[str]]]:
    """(file, concept, decorator regex, languages the row reaches)."""
    rows = []
    for path in sorted(get_frameworks_dir().glob("*.yaml")):
        data = read_catalogue_yaml(path)
        lang = data.get("language")
        file_langs = {lang} if isinstance(lang, str) else set(lang or [])
        for pat in data.get("patterns") or []:
            if "decorator" not in pat:
                continue
            filt = pat.get("language")
            if filt:
                reach = frozenset(w for w in DECORATOR_WRITERS if re.search(filt, w))
            elif file_langs == {"multi"}:
                reach = DECORATOR_WRITERS
            else:
                reach = frozenset(file_langs & DECORATOR_WRITERS)
            rows.append((path.name, pat.get("concept"), pat["decorator"], reach))
    return rows


def test_the_writer_list_matches_the_analyzers() -> None:
    """The six analyzers that write meta["decorators"] are the ones listed."""
    writers = set()
    for src in _PACKAGES.glob("*/src/*/*.py"):
        text = src.read_text()
        if re.search(r"""meta\w*\[["']decorators["']\]\s*=|\{["']decorators["']:""", text):
            writers.add(src.name)
    assert writers == _WRITER_FILES


def test_the_entry_concepts_are_read_from_the_detector() -> None:
    concepts = _entry_concepts()
    assert {"route", "middleware", "command", "entrypoint", "ipc_handler",
            "application"} <= concepts


#: Entry rows that cannot fire for ANOTHER reason, left in place: attribute-
#: shaped names in a language whose analyzer records no attribute (PHP, F#,
#: D), and Plug's ``conn``, a variable. The rows whose analyzer records the
#: attribute in another field (C# and Rust write ``annotations``) were
#: re-keyed and left this list.
_KEYED_ON_THE_WRONG_FIELD = frozenset({
    # Eleven of the fifteen were re-keyed on 2026-11-22 (Rust, C#, WGSL,
    # Laravel: test_wrong_field_rows.py). These remain, marked CANNOT FIRE in
    # their YAML: the analyzer records nothing a row could read.
    ("plug.yaml", "^conn$"),
    ("test-frameworks.yaml", "^test$"),               # PHP @test / #[Test]; D @("test")
    ("test-frameworks.yaml", "^(Test|Fact|Theory)$"),  # F# attributes
})


def test_an_entry_row_keyed_on_decorator_can_reach_a_decorator() -> None:
    entry = _entry_concepts()
    bad = [(f, c, d) for f, c, d, reach in _decorator_rows()
           if c in entry and (not reach or _CALL_ONLY.search(d))
           and (f, d) not in _KEYED_ON_THE_WRONG_FIELD]
    assert bad == []


def test_the_wrong_field_list_names_live_rows_only() -> None:
    """Each allowance names a row that exists, so the list shrinks when one
    is fixed or deleted."""
    rows = {(f, d) for f, _c, d, _reach in _decorator_rows()}
    for key in _KEYED_ON_THE_WRONG_FIELD:
        assert key in rows, key


#: The call-keyed rows deleted from decorator-writing languages' YAMLs
#: (each feeds an entry kind). Rows of languages without a decorator writer
#: are caught by the general rule above.
DELETED_CALL_ROWS: dict[str, tuple[str, ...]] = {
    "aiohttp.yaml": (
        '^(app\\.router|router)\\.(add_get|add_post|add_put|add_delete|add_patch|add_route|add_view|add_static)$',
        '^cleanup_ctx$',
        '^setup$',
    ),
    "akka-http.yaml": (
        '^(get|post|put|delete|patch|head|options)$',
        '^(path|pathPrefix|pathEnd|pathSuffix|pathSuffixTest)$',
        '^(parameter|parameters|formField|formFields)$',
        '^(entity|handleWith)$',
        '^handleWebSocketMessages$',
        '^Http\\(\\)\\.newServerAt$',
        '^concat$',
        '^cors$',
        '^(handleExceptions|handleRejections)$',
        '^(withRequestTimeout|withRequestTimeoutResponse)$',
        '^(as|toStrictEntity)$',
    ),
    "bottle.yaml": (
        '^(app|bottle)\\.install$',
    ),
    "cli-js.yaml": (
        '^(program|commander)\\.command$',
        '^(program|commander)\\.action$',
        '^(program|commander)\\.version$',
        '^yargs\\.command$',
        '^yargs\\.commandDir$',
        '^(cli|cac)\\.command$',
        '^inquirer\\.prompt$',
        '^vorpal\\.command$',
        '^vorpal\\.action$',
        '^toolbox\\.(print|prompt|filesystem)$',
    ),
    "cli.yaml": (
        '^typer\\.Typer$',
        '^fire\\.Fire$',
        '^argparse\\.ArgumentParser$',
    ),
    "electron.yaml": (
        '^ipcMain\\.(on|handle|handleOnce)$',
        '^webContents\\.(on|send)$',
    ),
    "express.yaml": (
        '^(app|router|express)\\.(get|post|put|delete|patch|options|head|all)$',
        '^(app|router)\\.route$',
        '^(app|router)\\.use$',
        '^(app|router)\\.param$',
        '^session$',
        '^morgan$',
        '^helmet$',
        '^cors$',
        '^(bodyParser|json|urlencoded)$',
        '^multer$',
        '^(body|query|param|check|validationResult)$',
        '^(socket|io)\\.on$',
    ),
    "fastify.yaml": (
        '^(fastify|app|server)\\.(get|post|put|delete|patch|options|head|all)$',
        '^(fastify|app|server)\\.route$',
        '^(fastify|app|server)\\.register$',
        '^(fastify|app|server)\\.(decorate|decorateRequest|decorateReply)$',
        '^(fastify|app|server)\\.(addHook|onRequest|onResponse|onSend|onError|preParsing|preValidation|preHandler|preSerialization)$',
        '^(fastify|app|server)\\.setErrorHandler$',
        '^(fastify|app|server)\\.setNotFoundHandler$',
        '^(fastify|app|server)\\.addContentTypeParser$',
        '^(fastify|app|server)\\.websocket$',
    ),
    "flask-restful.yaml": (
        '^abort$',
    ),
    "graphql-python.yaml": (
        '^make_executable_schema$',
    ),
    "graphql.yaml": (
        '^(fastify|app)\\.graphql$',
    ),
    "hapi.yaml": (
        '^server\\.route$',
        '^server\\.ext$',
        '^server\\.events\\.on$',
        '^Boom\\.(badRequest|unauthorized|forbidden|notFound|internal|boomify)$',
    ),
    "koa.yaml": (
        '^router\\.(get|post|put|delete|patch|head|options|all)$',
        '^router\\.prefix$',
        '^router\\.use$',
        '^router\\.param$',
        '^router\\.allowedMethods$',
        '^(app|koa)\\.use$',
        '^(koaBody|bodyParser)$',
        '^session$',
        '^helmet$',
        '^cors$',
        '^(serve|send|static)$',
        '^logger$',
        '^compress$',
        '^mount$',
    ),
    "ktor.yaml": (
        '^(get|post|put|delete|patch|head|options)$',
        '^Application\\.(routing|install|intercept)$',
        '^Route\\.(route|get|post|put|delete|patch|head|options)$',
        '^sessions$',
        '^webSocket$',
        '^(statusPages|exception)$',
        '^(contentNegotiation|json|xml)$',
        '^CORS$',
        '^callLogging$',
    ),
    "play.yaml": (
        '^Action$',
        '^Action\\.async$',
        '^(UserAction|AuthAction|SecuredAction|AdminAction)$',
        '^Action\\(parse\\.(json|tolerantJson|xml|text|raw)\\)$',
        '^WebSocket\\.(accept|acceptOrResult)$',
        '^Form$',
        '^(CSRFAddToken|CSRFCheck)$',
        '^Messages$',
    ),
    "solid.yaml": (
        '^(Route|Router|Routes|useNavigate|useParams|useSearchParams|useLocation)$',
    ),
}


@pytest.mark.parametrize("yaml_file", sorted(DELETED_CALL_ROWS))
def test_a_deleted_call_row_stays_deleted(yaml_file: str) -> None:
    present = {d for f, _c, d, _r in _decorator_rows() if f == yaml_file}
    assert present.isdisjoint(DELETED_CALL_ROWS[yaml_file])
