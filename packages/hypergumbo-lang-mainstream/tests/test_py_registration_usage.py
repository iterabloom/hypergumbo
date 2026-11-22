# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py.py
"""A call that hands a library a callable of the repository is recorded.

``app.on_startup.append(open_pool)``, ``fire.Fire(Calculator)``,
``app.install(stopwatch)``, ``app.register_error_handler(404, on_404)``:
the library calls the function or class later, and nothing in the repository
does, so without a record the handler has only a ``references`` edge and
looks dead. The Python analyzer records each such call as a ``UsageContext``
(kind ``call``, the callee as written, the argument's position, the local
callable's id) when a shipped ``usage:`` row could match the callee's name
(``framework_patterns.usage_row_names_call``): the rows are the only list of
call names.

Every test runs the analyzer on source text. Each class has its control: the
same shape the rule must not record (a callee no row names, a callee of the
repository, a library object passed instead of local code).
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_core.ir import Symbol, UsageContext
from hypergumbo_lang_mainstream.py import analyze_python


def _analyze(tmp_path: Path, files: dict[str, str]):
    for relative, source in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    clear_pattern_cache()
    return analyze_python(tmp_path)


def _registrations(result, name: str) -> list[UsageContext]:
    return [u for u in result.usage_contexts if u.context_name == name]


def _symbol(result, name: str) -> Symbol:
    found = [s for s in result.symbols if s.name == name and s.kind != "variable"]
    assert len(found) == 1, f"{name}: {[(s.name, s.kind) for s in found]}"
    return found[0]


def _concepts(symbol: Symbol) -> list[tuple[str, str]]:
    return [
        (c["concept"], c.get("framework", ""))
        for c in (symbol.meta or {}).get("concepts", [])
    ]


def _enriched(tmp_path: Path, files: dict[str, str], *frameworks: str):
    result = _analyze(tmp_path, files)
    symbols = list(result.symbols)
    for symbol in symbols:
        if Path(symbol.path).is_absolute():
            symbol.path = Path(symbol.path).relative_to(tmp_path).as_posix()
    clear_pattern_cache()
    enrich_symbols(symbols, set(frameworks), list(result.usage_contexts))
    return result, {s.name: s for s in symbols if s.kind != "variable"}


AIOHTTP_APP = '''\
import aiohttp_jinja2
from aiohttp import web


async def open_pool(app):
    app["pool"] = 1


async def close_pool(app):
    app["pool"] = None


async def db_ctx(app):
    yield


async def user_processor(request):
    return {}


def index(request):
    return web.Response(text="ok")


def make_app(callback):
    app = web.Application()
    app.on_startup.append(open_pool)
    app.on_cleanup.append(close_pool)
    app.cleanup_ctx.append(db_ctx)
    aiohttp_jinja2.setup(app, context_processors=[user_processor])
    app.on_shutdown.append(callback)
    items = []
    items.append(index)
    app.router.add_get("/users", index, expect_handler=check_expect)
    return app


async def check_expect(request):
    return None
'''


class TestRecorded:
    def test_a_signal_append_hands_over_the_local_function(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"app.py": AIOHTTP_APP})
        [ctx] = _registrations(result, "app.on_startup.append")
        assert ctx.kind == "call"
        assert ctx.position == "args[0]"
        assert ctx.symbol_ref == _symbol(result, "open_pool").id
        assert ctx.metadata["receiver"] == "app.on_startup"
        assert ctx.metadata["method"] == "append"
        [cleanup] = _registrations(result, "app.cleanup_ctx.append")
        assert cleanup.symbol_ref == _symbol(result, "db_ctx").id

    def test_a_list_element_has_its_index(self, tmp_path: Path) -> None:
        source = (
            "import fire\n\n"
            "def add(x, y):\n    return x + y\n\n"
            "def run():\n    fire.Fire([add])\n"
        )
        result = _analyze(tmp_path, {"cli.py": source})
        [ctx] = _registrations(result, "fire.Fire")
        assert ctx.position == "args[0][0]"
        assert ctx.symbol_ref == _symbol(result, "add").id

    def test_string_arguments_are_kept_for_a_row_to_read(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"app.py": AIOHTTP_APP})
        [ctx] = _registrations(result, "app.router.add_get")
        assert ctx.position == "args[1]"
        assert ctx.metadata["args"] == ["/users"]

    def test_a_class_and_an_instance_of_a_local_class(self, tmp_path: Path) -> None:
        source = (
            "import bottle\nimport fire\n\n"
            "class Calculator:\n    def double(self, n):\n        return 2 * n\n\n"
            "class AuthPlugin:\n    def apply(self, callback, route):\n        return callback\n\n"
            "app = bottle.Bottle()\n"
            "app.install(AuthPlugin())\n"
            "fire.Fire(Calculator)\n"
        )
        result = _analyze(tmp_path, {"cli.py": source})
        [fire_ctx] = _registrations(result, "fire.Fire")
        assert fire_ctx.symbol_ref == _symbol(result, "Calculator").id
        [install] = _registrations(result, "app.install")
        assert install.symbol_ref == _symbol(result, "AuthPlugin").id

    def test_a_dict_value_and_a_curried_registration(self, tmp_path: Path) -> None:
        source = (
            "import fire\nimport typer\n\n"
            "def multiply(x, y):\n    return x * y\n\n"
            "def download(name):\n    return name\n\n"
            "app = typer.Typer()\n"
            "app.command()(download)\n"
            "fire.Fire({'multiply': multiply})\n"
        )
        result = _analyze(tmp_path, {"cli.py": source})
        [fire_ctx] = _registrations(result, "fire.Fire")
        assert fire_ctx.position == "args[0][multiply]"
        assert fire_ctx.symbol_ref == _symbol(result, "multiply").id
        [curried] = _registrations(result, "app.command()")
        assert curried.position == "args[0]"
        assert curried.symbol_ref == _symbol(result, "download").id

    def test_a_handler_of_another_file_is_resolved_through_the_import(
        self, tmp_path: Path,
    ) -> None:
        files = {
            "pkg/__init__.py": "",
            "pkg/cmds.py": "def add(x, y):\n    return x + y\n",
            "pkg/main.py": "import fire\nfrom pkg.cmds import add\n\nfire.Fire(add)\n",
        }
        result = _analyze(tmp_path, files)
        [ctx] = _registrations(result, "fire.Fire")
        assert ctx.symbol_ref == _symbol(result, "add").id


class TestNotRecorded:
    def test_a_name_no_row_has_is_not_recorded(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"app.py": AIOHTTP_APP})
        # reach: the same file records the named shapes
        assert _registrations(result, "app.on_startup.append")
        assert _registrations(result, "items.append") == []

    def test_a_parameter_or_a_library_object_is_not_local_code(
        self, tmp_path: Path,
    ) -> None:
        source = (
            "import bottle\nfrom bottle_sqlite import SQLitePlugin\n\n"
            "def stopwatch(callback):\n    return callback\n\n"
            "app = bottle.Bottle()\n"
            "plugin = SQLitePlugin(dbfile='x')\n"
            "app.install(stopwatch)\n"
            "app.install(SQLitePlugin(dbfile='y'))\n"
            "app.install(plugin)\n"
        )
        result = _analyze(tmp_path, {"app.py": source})
        installs = _registrations(result, "app.install")
        assert [u.symbol_ref for u in installs] == [_symbol(result, "stopwatch").id]
        on_shutdown = [
            u for u in _analyze(tmp_path / "b", {"app.py": AIOHTTP_APP}).usage_contexts
            if u.context_name == "app.on_shutdown.append"
        ]
        assert on_shutdown == []

    def test_a_computed_callee_and_a_local_factory_call(self, tmp_path: Path) -> None:
        source = (
            "import bottle\n\n"
            "def stopwatch(callback):\n    return callback\n\n"
            "def make_plugin():\n    return stopwatch\n\n"
            "apps = [bottle.Bottle()]\n"
            "apps[0].install(stopwatch)\n"
            "app = bottle.Bottle()\n"
            "app.install(make_plugin())\n"
            "app.install(stopwatch)\n"
        )
        result = _analyze(tmp_path, {"app.py": source})
        recorded = [
            (u.context_name, u.symbol_ref) for u in result.usage_contexts
            if u.metadata.get("method") == "install"
        ]
        # reach: the plain form on the same file is recorded
        assert recorded == [("app.install", _symbol(result, "stopwatch").id)]

    def test_a_callee_of_the_repository_is_an_ordinary_call(self, tmp_path: Path) -> None:
        files = {
            "pkg/__init__.py": "",
            "pkg/reg.py": "def install(fn):\n    return fn\n",
            "pkg/main.py": (
                "from pkg.reg import install\n\n"
                "def plugin(cb):\n    return cb\n\n"
                "install(plugin)\n"
            ),
            "pkg/same_file.py": (
                "def install(fn):\n    return fn\n\n"
                "def other(cb):\n    return cb\n\n"
                "install(other)\n"
            ),
            "pkg/library.py": (
                "from bottle import install\n\n"
                "def timer(cb):\n    return cb\n\n"
                "install(timer)\n"
            ),
        }
        result = _analyze(tmp_path, files)
        recorded = _registrations(result, "install")
        # reach: the bottle-imported install of the same name IS recorded
        assert [u.symbol_ref for u in recorded] == [_symbol(result, "timer").id]

    def test_only_a_position_a_row_names_is_recorded(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"app.py": AIOHTTP_APP})
        # reach: the same call's handler, at the position the row names
        assert [u.position for u in _registrations(result, "app.router.add_get")] == [
            "args[1]",
        ]
        assert _symbol(result, "check_expect").id not in {
            u.symbol_ref for u in result.usage_contexts
        }
        # a call whose row was removed: aiohttp_jinja2 context processors
        assert _registrations(result, "aiohttp_jinja2.setup") == []

    def test_a_call_a_route_extractor_owns_is_left_to_it(self, tmp_path: Path) -> None:
        source = (
            "from django.urls import path\n\n"
            "def index(request):\n    return None\n\n"
            "urlpatterns = [path('home/', index)]\n\n"
            "def build(prefix):\n    return path(prefix, index)\n"
        )
        result = _analyze(tmp_path, {"urls.py": source})
        # the Django extractor's context only: the second call has no path
        # it can read, and nothing else records it
        [ctx] = _registrations(result, "path")
        assert ctx.metadata["route_path"] == "/home/"


class TestShippedRows:
    def test_aiohttp_rows(self, tmp_path: Path) -> None:
        source = AIOHTTP_APP + (
            "\n\n@web.middleware\nasync def timing(request, handler):\n"
            "    return await handler(request)\n"
        )
        _, by_name = _enriched(tmp_path, {"app.py": source}, "aiohttp")
        assert ("event_handler", "aiohttp") in _concepts(by_name["open_pool"])
        assert ("event_handler", "aiohttp") in _concepts(by_name["close_pool"])
        assert ("event_handler", "aiohttp") in _concepts(by_name["db_ctx"])
        assert _concepts(by_name["user_processor"]) == []
        assert ("middleware", "aiohttp") in _concepts(by_name["timing"])
        [route] = [
            c for c in (by_name["index"].meta or {}).get("concepts", [])
            if c["concept"] == "route"
        ]
        assert route["path"] == "/users"
        assert route["method"] == "GET"
        assert _concepts(by_name["make_app"]) == []

    def test_bottle_row(self, tmp_path: Path) -> None:
        source = (
            "import bottle\nfrom bottle import install\n\n"
            "def stopwatch(callback):\n    return callback\n\n"
            "def timer(callback):\n    return callback\n\n"
            "def unused(callback):\n    return callback\n\n"
            "app = bottle.Bottle()\napp.install(stopwatch)\ninstall(timer)\n"
        )
        _, by_name = _enriched(tmp_path, {"app.py": source}, "bottle")
        assert ("middleware", "bottle") in _concepts(by_name["stopwatch"])
        assert ("middleware", "bottle") in _concepts(by_name["timer"])
        assert _concepts(by_name["unused"]) == []

    def test_cli_rows(self, tmp_path: Path) -> None:
        source = (
            "import fire\nimport typer\n\n"
            "def add(x, y):\n    return x + y\n\n"
            "def greet(name):\n    return name\n\n"
            "def download(name):\n    return name\n\n"
            "class Calculator:\n    def double(self, n):\n        return n\n\n"
            "app = typer.Typer()\napp.command()(download)\n"
            "def run():\n    typer.run(greet)\n    fire.Fire(add)\n    fire.Fire(Calculator)\n"
        )
        _, by_name = _enriched(tmp_path, {"cli.py": source}, "cli")
        for name in ("add", "greet", "download", "Calculator"):
            assert ("command", "cli") in _concepts(by_name[name]), name
        assert _concepts(by_name["run"]) == []

    def test_flask_row(self, tmp_path: Path) -> None:
        source = (
            "from flask import Flask\n\n"
            "def on_not_found(e):\n    return 'nf', 404\n\n"
            "def helper(e):\n    return e\n\n"
            "app = Flask(__name__)\napp.register_error_handler(404, on_not_found)\n"
        )
        _, by_name = _enriched(tmp_path, {"app.py": source}, "flask")
        assert ("error_handler", "flask") in _concepts(by_name["on_not_found"])
        assert _concepts(by_name["helper"]) == []

    def test_django_path_outside_urlpatterns_gets_no_pathless_route(
        self, tmp_path: Path,
    ) -> None:
        source = (
            "from django.urls import path\n\n"
            "def index(request):\n    return None\n\n"
            "def build(prefix):\n    return path(prefix, index)\n"
        )
        result, by_name = _enriched(tmp_path, {"urls.py": source}, "django")
        # reach: the call is there; no context, so no route without a path
        assert _registrations(result, "path") == []
        assert _concepts(by_name["index"]) == []

    def test_tornado_rows_read_the_path_from_the_first_argument(
        self, tmp_path: Path,
    ) -> None:
        source = (
            "from tornado.web import Application, RequestHandler, URLSpec, url\n\n"
            "class MainHandler(RequestHandler):\n    def get(self):\n        pass\n\n"
            "class ItemHandler(RequestHandler):\n    def get(self):\n        pass\n\n"
            "app = Application([url(r'/x', MainHandler), URLSpec(r'/y', ItemHandler)])\n"
        )
        _, by_name = _enriched(tmp_path, {"app.py": source}, "tornado")

        def paths(name: str) -> list[str]:
            return [
                c.get("path") for c in (by_name[name].meta or {}).get("concepts", [])
                if c["concept"] == "route"
            ]

        assert paths("MainHandler") == ["/x"]
        assert paths("ItemHandler") == ["/y"]

    def test_graphql_python_row(self, tmp_path: Path) -> None:
        source = (
            "from ariadne import QueryType\n\n"
            "def resolve_version(_, info):\n    return '1'\n\n"
            "query = QueryType()\nquery.set_field('version', resolve_version)\n"
        )
        _, by_name = _enriched(tmp_path, {"schema.py": source}, "graphql-python")
        assert ("graphql_resolver", "graphql-python") in _concepts(
            by_name["resolve_version"],
        )
