# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/frameworks/flask-restful.yaml
"""Framework hooks a repository writes by subclassing, asked of the analyzers.

Three hooks a framework calls on a subclass the repository defines, with
nothing in the repository calling them:

- flask-restful: ``class JsonErrorApi(Api): def handle_error(self, e)`` - the
  Api calls it when a resource raises (``error_handler``).
- Slim: ``class HttpErrorHandler extends ErrorHandler`` - handed to
  ``setDefaultErrorHandler``; Slim calls it on an uncaught exception
  (``error_handler`` on the class: the PHP analyzer records no parent bases on
  a method, so the row reads the class's ``base_classes``).
- argparse: ``class UpperAction(argparse.Action): def __call__(...)`` - argparse
  calls it when the option is parsed (``event_handler``: no entry kind names a
  parse callback; the action is a callback on the "option seen" event). The
  row is in cli.yaml, which loads only where a CLI framework is detected.

Each test runs the analyzer on source, gives its symbols to the shipped rows,
asserts the symbol exists first, and has a control: the same method name or
base name where the row must not fire.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.php import analyze_php
from hypergumbo_lang_mainstream.py import analyze_python


def _enriched(
    tmp_path: Path, files: dict[str, str],
    analyze: Callable[[Path], AnalysisResult], *frameworks: str,
) -> dict[str, Symbol]:
    for relative, source in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    symbols = list(analyze(tmp_path).symbols)
    for symbol in symbols:
        if Path(symbol.path).is_absolute():
            symbol.path = Path(symbol.path).relative_to(tmp_path).as_posix()
    clear_pattern_cache()
    enrich_symbols(symbols, set(frameworks))
    return {s.name: s for s in symbols if s.kind in ("class", "method", "function")}


def _concepts(symbol: Symbol) -> list[tuple[str, str]]:
    return [
        (c["concept"], c.get("framework", ""))
        for c in (symbol.meta or {}).get("concepts", [])
    ]


def test_flask_restful_handle_error_override(tmp_path: Path) -> None:
    source = (
        "from flask_restful import Api\n\n\n"
        "class JsonErrorApi(Api):\n"
        "    def handle_error(self, e):\n        return {}, 500\n\n\n"
        "class Worker:\n"
        "    def handle_error(self, e):\n        return None\n"
    )
    by_name = _enriched(tmp_path, {"app.py": source}, analyze_python, "flask-restful")
    assert ("error_handler", "flask-restful") in _concepts(by_name["JsonErrorApi.handle_error"])
    assert _concepts(by_name["Worker.handle_error"]) == []


def test_slim_error_handler_subclass(tmp_path: Path) -> None:
    source = (
        "<?php\nnamespace App;\n\n"
        "use Slim\\Handlers\\ErrorHandler;\n\n"
        "class HttpErrorHandler extends ErrorHandler\n{\n"
        "    protected function respond(): void {}\n}\n\n"
        "class ErrorHandlerFactory\n{\n"
        "    public function make(): void {}\n}\n\n"
        "class LogRenderer extends ErrorHandlerBase\n{\n"
        "    public function render(): void {}\n}\n"
    )
    by_name = _enriched(tmp_path, {"src/Errors.php": source}, analyze_php, "slim")
    assert ("error_handler", "slim") in _concepts(by_name["HttpErrorHandler"])
    assert _concepts(by_name["ErrorHandlerFactory"]) == []
    assert _concepts(by_name["LogRenderer"]) == []


def test_argparse_action_subclass(tmp_path: Path) -> None:
    source = (
        "import argparse\n\n\n"
        "class UpperAction(argparse.Action):\n"
        "    def __call__(self, parser, namespace, values, option_string=None):\n"
        "        setattr(namespace, self.dest, values.upper())\n\n"
        "    def format(self):\n        return ''\n\n\n"
        "class Callable:\n"
        "    def __call__(self):\n        return 1\n"
    )
    by_name = _enriched(tmp_path, {"cli.py": source}, analyze_python, "cli")
    assert ("event_handler", "cli") in _concepts(by_name["UpperAction.__call__"])
    assert _concepts(by_name["UpperAction.format"]) == []
    assert _concepts(by_name["Callable.__call__"]) == []
