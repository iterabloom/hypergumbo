# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/frameworks/play.yaml
"""A Play CSRF error handler is code Play calls: an error-handler entry.

``CSRFCheck(action, errorHandler)`` and the CSRF filter take an
implementation of ``CSRF.ErrorHandler`` (Scala API) or ``CSRFErrorHandler``
(the Java interface), whose ``handle`` Play calls when a request fails the
token check. Nothing in the repository calls it. The scala analyzer records
the type's parents in ``base_classes`` as written, so a play.yaml row reads
them: the type becomes an ``error_handler`` (entry kind ``error_handler``).

The row is anchored on the qualified spelling: an ``ErrorHandler`` of
another library, written bare or under another qualifier, is not Play's.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.entrypoints import EntrypointKind, detect_entrypoints
from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.scala import analyze_scala

_SOURCE = """\
package controllers

import play.api.mvc._
import play.filters.csrf._

object NoopErrorHandler extends CSRF.ErrorHandler {
  def handle(req: RequestHeader, msg: String) = scala.concurrent.Future.successful(Results.Forbidden(msg))
}

class QualifiedHandler extends play.filters.csrf.CSRF.ErrorHandler {
  def handle(req: RequestHeader, msg: String) = scala.concurrent.Future.successful(Results.Forbidden(msg))
}

class JavaStyleHandler extends CSRFErrorHandler {
  def handle(req: play.mvc.Http.RequestHeader, msg: String) = null
}

// controls: another library's ErrorHandler, bare and qualified
class LoggingHandler extends ErrorHandler
class OtherHandler extends mylib.ErrorHandler
"""


def _enriched(tmp_path: Path, frameworks: set[str]) -> dict[str, Symbol]:
    (tmp_path / "app" / "controllers").mkdir(parents=True)
    (tmp_path / "app" / "controllers" / "Handlers.scala").write_text(_SOURCE)
    symbols = list(analyze_scala(tmp_path).symbols)
    clear_pattern_cache()
    enrich_symbols(symbols, frameworks)
    return {s.name: s for s in symbols}


def _concepts(symbol: Symbol) -> set[str]:
    return {c["concept"] for c in (symbol.meta or {}).get("concepts", [])}


def test_csrf_error_handlers_are_error_handler_entries(tmp_path: Path) -> None:
    symbols = _enriched(tmp_path, {"play"})
    assert symbols["NoopErrorHandler"].meta["base_classes"] == ["CSRF.ErrorHandler"]
    assert symbols["JavaStyleHandler"].meta["base_classes"] == ["CSRFErrorHandler"]
    for name in ("NoopErrorHandler", "QualifiedHandler", "JavaStyleHandler"):
        assert "error_handler" in _concepts(symbols[name]), name
    entries = {
        e.symbol_id for e in detect_entrypoints(list(symbols.values()), [])
        if e.kind == EntrypointKind.ERROR_HANDLER
    }
    assert symbols["NoopErrorHandler"].id in entries


def test_another_librarys_error_handler_is_not(tmp_path: Path) -> None:
    symbols = _enriched(tmp_path, {"play"})
    assert symbols["LoggingHandler"].meta["base_classes"] == ["ErrorHandler"]
    assert symbols["OtherHandler"].meta["base_classes"] == ["mylib.ErrorHandler"]
    assert "error_handler" not in _concepts(symbols["LoggingHandler"])
    assert "error_handler" not in _concepts(symbols["OtherHandler"])


def test_not_without_play(tmp_path: Path) -> None:
    symbols = _enriched(tmp_path, set())
    assert "error_handler" not in _concepts(symbols["NoopErrorHandler"])
