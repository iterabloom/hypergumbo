# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py.py
"""A decorator's receiver, named by what made it (``meta["decorator_receivers"]``).

``@users_app.command("create")``, ``@cli.command()``, ``@user_type.field("x")``:
the decorator is a method of an object the file made, and the rows that knew
these frameworks keyed the object's VARIABLE NAME (``^app\\.command$``,
``^query\\.field$``), so a typer sub-app, a click group's subcommand or an
ariadne bindable under any other name was missed (mlflow: 1 of 67 click
subcommands marked). The Python analyzer now writes, on a decorated function,
method or class, what made each decorator's receiver, spelled through the
file's imports: ``typer.Typer.command`` for ``users_app = typer.Typer()``,
``click.group.command`` for a function decorated ``@click.group()``,
``click.group.group.command`` one level down, ``ariadne.ObjectType.field``
after ``from ariadne import ObjectType``. Space-separated, one entry per such
decorator. A row reads it with ``meta_match``.

Only a receiver a library made is written (its maker's root is bound by an
import): the controls are a receiver made by a class of the file and a
receiver no binding of the module makes.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.py import analyze_python


def _symbols(tmp_path: Path, source: str, *frameworks: str) -> dict[str, Symbol]:
    (tmp_path / "cli.py").write_text(source)
    symbols = list(analyze_python(tmp_path).symbols)
    for symbol in symbols:
        if Path(symbol.path).is_absolute():
            symbol.path = Path(symbol.path).relative_to(tmp_path).as_posix()
    if frameworks:
        clear_pattern_cache()
        enrich_symbols(symbols, set(frameworks))
    return {s.name: s for s in symbols if s.kind in ("function", "method", "class")}


def _receivers(symbol: Symbol) -> str | None:
    value = (symbol.meta or {}).get("decorator_receivers")
    return value if isinstance(value, str) else None


def _concepts(symbol: Symbol) -> list[tuple[str, str]]:
    return [
        (c["concept"], c.get("framework", ""))
        for c in (symbol.meta or {}).get("concepts", [])
    ]


TYPER = '''\
import typer as t

app = t.Typer()
users_app = t.Typer()
app.add_typer(users_app, name="users")


class Shell:
    def command(self, text=None):
        return lambda f: f


shell = Shell()


@users_app.command("create")
def create_user(username: str):
    return username


@app.command()
def hello(name: str):
    return name


@shell.command()
def not_a_cli(text):
    return text


@unknown.command()
def nobody_made_it():
    return None
'''

CLICK = '''\
import click


@click.group()
def cli():
    pass


@cli.command()
def sync():
    pass


@cli.group()
def remote():
    pass


@remote.command("add")
@click.option("--name")
def remote_add(name):
    pass
'''

ARIADNE = '''\
from ariadne import ObjectType, QueryType

query = QueryType()
user_type = ObjectType("User")


@user_type.field("fullName")
def resolve_full_name(obj, info):
    return obj["name"]


@query.field("hello")
def resolve_hello(_, info):
    return "hi"
'''


class TestRecorded:
    def test_typer_sub_app_through_an_aliased_import(self, tmp_path: Path) -> None:
        by_name = _symbols(tmp_path, TYPER)
        assert _receivers(by_name["create_user"]) == "typer.Typer.command"
        assert _receivers(by_name["hello"]) == "typer.Typer.command"

    def test_click_group_chain(self, tmp_path: Path) -> None:
        by_name = _symbols(tmp_path, CLICK)
        assert _receivers(by_name["sync"]) == "click.group.command"
        assert _receivers(by_name["remote"]) == "click.group.group"
        # one entry per decorator whose receiver a library made
        assert _receivers(by_name["remote_add"]) == "click.group.group.command"

    def test_ariadne_bindables(self, tmp_path: Path) -> None:
        by_name = _symbols(tmp_path, ARIADNE)
        assert _receivers(by_name["resolve_full_name"]) == "ariadne.ObjectType.field"
        assert _receivers(by_name["resolve_hello"]) == "ariadne.QueryType.field"


class TestMethods:
    def test_a_method_decorated_through_a_receiver(self, tmp_path: Path) -> None:
        source = (
            "import typer\n\nsub = typer.Typer()\n\n\n"
            "class Commands:\n    @sub.command()\n    def build(self):\n        return 1\n"
        )
        by_name = _symbols(tmp_path, source)
        assert _receivers(by_name["Commands.build"]) == "typer.Typer.command"


class TestNotRecorded:
    def test_a_relative_import_is_the_repository_not_a_library(
        self, tmp_path: Path,
    ) -> None:
        source = (
            "import typer\nfrom .factory import make_app\n\n"
            "app = make_app()\nsub = typer.Typer()\n"
            "factories = {'t': typer.Typer}\ncomputed = factories['t']()\n\n\n"
            "@app.command()\ndef local_made():\n    return 1\n\n\n"
            "@computed.command()\ndef computed_made():\n    return 3\n\n\n"
            "@sub.command()\ndef library_made():\n    return 2\n"
        )
        by_name = _symbols(tmp_path, source)
        # reach: the library-made receiver in the same file is recorded
        assert _receivers(by_name["library_made"]) == "typer.Typer.command"
        assert _receivers(by_name["local_made"]) is None
        assert _receivers(by_name["computed_made"]) is None

    def test_a_receiver_the_file_made_or_nobody_made(self, tmp_path: Path) -> None:
        by_name = _symbols(tmp_path, TYPER)
        # reach: the same file records the typer receivers
        assert _receivers(by_name["create_user"]) == "typer.Typer.command"
        assert _receivers(by_name["not_a_cli"]) is None
        assert _receivers(by_name["nobody_made_it"]) is None
        # the click group function itself is decorated by an import, not a receiver
        (tmp_path / "c").mkdir()
        click = _symbols(tmp_path / "c", CLICK)
        assert _receivers(click["cli"]) is None


class TestShippedRows:
    def test_cli_rows(self, tmp_path: Path) -> None:
        by_name = _symbols(tmp_path, TYPER, "cli")
        assert ("command", "cli") in _concepts(by_name["create_user"])
        assert ("command", "cli") in _concepts(by_name["hello"])
        assert _concepts(by_name["not_a_cli"]) == []
        assert _concepts(by_name["nobody_made_it"]) == []

    def test_click_rows(self, tmp_path: Path) -> None:
        by_name = _symbols(tmp_path, CLICK, "cli")
        for name in ("cli", "sync", "remote", "remote_add"):
            assert ("command", "cli") in _concepts(by_name[name]), name

    def test_graphql_python_rows(self, tmp_path: Path) -> None:
        by_name = _symbols(tmp_path, ARIADNE, "graphql-python")
        assert ("graphql_resolver", "graphql-python") in _concepts(
            by_name["resolve_full_name"],
        )
        assert ("graphql_resolver", "graphql-python") in _concepts(
            by_name["resolve_hello"],
        )
