# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-losij: a request handler reached through a base class is a request-ingress entrypoint.

Python's ``http.server`` and the Java Servlet API reach a handler through a
BASE CLASS, not a registration a pattern can see: ``HTTPServer`` calls the
``do_<VERB>`` method of a ``BaseHTTPRequestHandler`` subclass, and a servlet
container calls ``doGet`` / ``doPost`` / ``service`` on an ``HttpServlet``
subclass. Neither was an entrypoint, so WI-fuzik's unseeded-ingress withhold
could not fire on them: the one-file probes read ``confirmed_with_caveats``
over ``open(self.path)`` once the stdlib call was declared complete, and had
no ingress reason to withhold over ``new FileWriter(req.getParameter("p"))``.

Three pieces are pinned here: the always-loaded ``python-http-server.yaml``
rule (``do_[A-Z]+`` on a ``(Base|Simple|CGI)HTTPRequestHandler`` subclass,
Python, never in test code); the detection-gated ``servlet.yaml`` rule
(``doGet`` / ... / ``service`` on an ``HttpServlet`` subclass); and the
``route_handler`` concept's mapping to ``EntrypointKind.HTTP_ROUTE``, a
request-ingress kind. Three YAMLs (falcon, flask-restful, nex) had emitted
that concept and ``entrypoints.py`` never read it. Both rules read the
method's ``parent_base_classes``, which the Python analyzer stamps since
WI-sorop (``test_py_parent_base_classes.py`` in hypergumbo-lang-mainstream)
and the Java analyzer has always written.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.entrypoints import (
    REQUEST_INGRESS_KINDS,
    EntrypointKind,
    detect_entrypoints,
)
from hypergumbo_core.framework_patterns import (
    _CONVENTION_PATTERN_IDS,
    clear_pattern_cache,
    enrich_symbols,
    load_framework_patterns,
    strip_test_file_only_concepts,
)
from hypergumbo_core.ir import Span, Symbol


def _method(
    name: str, bases: list[str] | None, *, language: str = "python",
    kind: str = "method", path: str = "srv/handler.py",
) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:1-9:{name}:{kind}",
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=Span(1, 9, 0, 0),
        meta={} if bases is None else {"parent_base_classes": bases},
    )


def _java(name: str, bases: list[str] | None, *, kind: str = "method") -> Symbol:
    return _method(name, bases, language="java", kind=kind, path="src/main/java/app/Saver.java")


def _concepts(symbol: Symbol, frameworks: set[str] | None = None) -> list[str]:
    enrich_symbols([symbol], set(frameworks or ()))
    return [c.get("concept") for c in (symbol.meta or {}).get("concepts", [])]


def _http_routes(symbol: Symbol) -> list:
    return [e for e in detect_entrypoints([symbol], [])
            if e.kind == EntrypointKind.HTTP_ROUTE]


# --------------------------------------------------------- python http.server


def test_python_http_server_rules_are_always_loaded() -> None:
    assert "python-http-server" in _CONVENTION_PATTERN_IDS
    clear_pattern_cache()
    fw = load_framework_patterns("python-http-server")
    assert fw is not None
    assert {p.concept for p in fw.patterns} == {"route_handler"}


@pytest.mark.parametrize("name,bases", [
    ("Saver.do_GET", ["BaseHTTPRequestHandler"]),
    ("Saver.do_POST", ["http.server.BaseHTTPRequestHandler"]),
    ("Files.do_HEAD", ["SimpleHTTPRequestHandler"]),
    ("Mixed.do_PUT", ["LoggingMixin", "BaseHTTPRequestHandler"]),
])
def test_a_do_verb_method_of_a_stdlib_handler_subclass_is_a_handler(
    name: str, bases: list[str],
) -> None:
    assert "route_handler" in _concepts(_method(name, bases))


@pytest.mark.parametrize("symbol", [
    _method("Saver.do_work", ["BaseHTTPRequestHandler"]),
    _method("Saver.do_get", ["BaseHTTPRequestHandler"]),
    _method("Saver.log_message", ["BaseHTTPRequestHandler"]),
    _method("Plain.do_GET", None),
    _method("Worker.do_GET", ["Thread"]),
    _method("Saver.do_GET", ["BaseHTTPRequestHandler"], language="ruby", path="srv/h.rb"),
], ids=["helper", "lower-case-verb", "other-method", "no-bases", "other-base", "ruby"])
def test_a_python_lookalike_is_not_a_handler(symbol: Symbol) -> None:
    assert "route_handler" not in _concepts(symbol)


def test_a_python_test_files_fake_handler_loses_the_concept() -> None:
    """A test's fake server is not the program's ingress (WI-bosab)."""
    symbol = _method("Fake.do_GET", ["BaseHTTPRequestHandler"], path="tests/test_srv.py")
    assert "route_handler" in _concepts(symbol)
    symbol.is_test_file = True
    strip_test_file_only_concepts([symbol])
    assert "route_handler" not in [
        c.get("concept") for c in symbol.meta.get("concepts", [])
    ]


@pytest.mark.parametrize("symbol,frameworks", [
    (_method("Foo.get", ["flask_restful.Resource"], path="tests/test_api.py"),
     {"flask-restful"}),
    (_method("FakeServlet.doGet", ["HttpServlet"], language="java",
             path="src/test/java/app/FakeServlet.java"), {"servlet"}),
], ids=["flask-restful", "servlet"])
def test_the_test_file_exclusion_is_by_concept_name_across_files(
    symbol: Symbol, frameworks: set[str],
) -> None:
    """``strip_test_file_only_concepts`` keys on the concept, not the rule: the
    always-loaded rule's ``is_test_file: false`` reaches a flask-restful
    fixture's ``get`` in ``tests/`` (measured: 41 on flask-restful) and a
    servlet in ``src/test``, whose own YAML declares no key because the set is
    built from convention files only. A comment on each producer says so."""
    assert "route_handler" in _concepts(symbol, frameworks)
    symbol.is_test_file = True
    strip_test_file_only_concepts([symbol])
    assert "route_handler" not in [
        c.get("concept") for c in symbol.meta.get("concepts", [])
    ]


# ----------------------------------------------------------------- java servlet


@pytest.mark.parametrize("name,bases", [
    ("Saver.doGet", ["HttpServlet"]),
    ("Saver.doPost", ["javax.servlet.http.HttpServlet"]),
    ("Saver.service", ["jakarta.servlet.http.HttpServlet"]),
    ("Saver.doDelete", ["HttpServlet", "Serializable"]),
])
def test_a_do_verb_method_of_an_http_servlet_subclass_is_a_handler(
    name: str, bases: list[str],
) -> None:
    assert "route_handler" in _concepts(_java(name, bases), {"servlet"})


@pytest.mark.parametrize("symbol", [
    _java("Saver.doSomething", ["HttpServlet"]),
    _java("Saver.doGet", ["GenericServlet"]),
    _java("Saver.doGet", None),
    _java("Saver.init", ["HttpServlet"]),
], ids=["other-name", "other-base", "no-bases", "lifecycle"])
def test_a_java_lookalike_is_not_a_handler(symbol: Symbol) -> None:
    assert "route_handler" not in _concepts(symbol, {"servlet"})


def test_the_servlet_rule_waits_for_detection() -> None:
    assert "route_handler" not in _concepts(_java("Saver.doGet", ["HttpServlet"]), set())


# --------------------------------------------------------------------- the kind


def test_route_handler_is_an_http_route_entrypoint() -> None:
    symbol = _method("Saver.do_GET", ["BaseHTTPRequestHandler"])
    symbol.meta = {"concepts": [
        {"concept": "route_handler", "framework": "python-http-server"},
    ]}
    eps = _http_routes(symbol)
    assert len(eps) == 1
    assert eps[0].symbol_id == symbol.id
    assert "Saver.do_GET" in eps[0].label
    assert EntrypointKind.HTTP_ROUTE.value in REQUEST_INGRESS_KINDS


@pytest.mark.parametrize("order", ["route-first", "handler-first"])
def test_a_handler_that_is_also_a_route_is_one_http_route_entrypoint(order: str) -> None:
    """Whichever concept comes first, the route's method and path label it."""
    route = {"concept": "route", "framework": "flask", "method": "GET", "path": "/todos"}
    handler = {"concept": "route_handler", "framework": "flask-restful"}
    symbol = _method("Todo.get", ["Resource"])
    symbol.meta = {"concepts": [route, handler] if order == "route-first" else [handler, route]}
    eps = _http_routes(symbol)
    assert len(eps) == 1
    assert eps[0].label == "HTTP GET /todos"


def test_a_handler_in_a_frontend_file_is_suppressed() -> None:
    """The ``route`` arm's frontend-file suppression (WI-ronik) applies here too."""
    symbol = _method("handler", None, language="typescript", kind="function",
                     path="src/components/Button.tsx")
    symbol.meta = {"concepts": [{"concept": "route_handler", "framework": "nex"}]}
    assert _http_routes(symbol) == []


def test_a_handler_carrying_the_concept_twice_is_one_entrypoint() -> None:
    symbol = _method("Saver.do_GET", ["BaseHTTPRequestHandler"])
    symbol.meta = {"concepts": [{"concept": "route_handler"}, {"concept": "route_handler"}]}
    assert len(_http_routes(symbol)) == 1
