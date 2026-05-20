# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Spring MVC view-template linker (WI-hogik).

Spring controllers return a view name as a string literal; Spring's view
resolver maps it to ``src/main/resources/templates/<view>.<ext>``
(Thymeleaf / FreeMarker / Velocity) or
``src/main/webapp/WEB-INF/views/<view>.jsp`` (JSP).

* ``@Controller`` (not ``@RestController``) class methods with a mapping
  annotation (``@RequestMapping`` family or any of ``@GetMapping``,
  ``@PostMapping``, ``@PutMapping``, ``@DeleteMapping``, ``@PatchMapping``)
  return a view name as a string literal: ``return "users/show";``.
* ``new ModelAndView("users/show", model)`` first-argument string also
  counts as a view name.
* ``return "redirect:/foo";`` is a redirect, not a view; skipped.
* ``@RestController`` classes return JSON; skipped entirely.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.registry import LinkerContext
from hypergumbo_core.linkers.view_template_spring import (
    SpringStrategy,
    link_view_template_spring,
)


def _java_class(
    fqn: str,
    *,
    path: str,
    decorators: list[dict] | None = None,
    start: int = 1,
    end: int = 50,
) -> Symbol:
    meta: dict[str, object] = {}
    if decorators is not None:
        meta["decorators"] = decorators
    return Symbol(
        id=f"java:{path}:{start}-{end}:{fqn}:class",
        name=fqn,
        kind="class",
        language="java",
        path=path,
        span=Span(start_line=start, end_line=end, start_col=0, end_col=0),
        meta=meta or None,
        origin="java",
    )


def _java_method(
    fqn: str,
    *,
    path: str,
    decorators: list[dict] | None = None,
    start: int = 5,
    end: int = 10,
) -> Symbol:
    meta: dict[str, object] = {}
    if decorators is not None:
        meta["decorators"] = decorators
    return Symbol(
        id=f"java:{path}:{start}-{end}:{fqn}:method",
        name=fqn,
        kind="method",
        language="java",
        path=path,
        span=Span(start_line=start, end_line=end, start_col=2, end_col=2),
        meta=meta or None,
        origin="java",
    )


def _write(tmp_path: Path, rel: str, body: str = "tpl") -> None:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


_CONTROLLER_DECORATOR = [{"name": "Controller", "args": [], "kwargs": {}}]
_REST_CONTROLLER_DECORATOR = [{"name": "RestController", "args": [], "kwargs": {}}]
_GET_MAPPING_USERS = [{"name": "GetMapping", "args": ["/users/{id}"], "kwargs": {}}]
_REQUEST_MAPPING_USERS = [
    {"name": "RequestMapping", "args": ["/users/{id}"], "kwargs": {}}
]
_POST_MAPPING = [{"name": "PostMapping", "args": ["/save"], "kwargs": {}}]


class TestStringReturnViewName:
    """``return "users/show"`` produces a renders edge to the matching template."""

    def test_thymeleaf_html(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/main/resources/templates/users/show.html")
        java_source = (
            "@Controller\n"
            "public class UserController {\n"
            "    @GetMapping(\"/users/{id}\")\n"
            "    public String show(@PathVariable Long id) {\n"
            "        return \"users/show\";\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "src/main/java/com/ex/UserController.java", java_source)

        klass = _java_class(
            "com.ex.UserController",
            path="src/main/java/com/ex/UserController.java",
            decorators=_CONTROLLER_DECORATOR,
            end=8,
        )
        method = _java_method(
            "com.ex.UserController.show",
            path="src/main/java/com/ex/UserController.java",
            decorators=_GET_MAPPING_USERS,
            start=4,
            end=6,
        )

        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == method.id
        assert edge.edge_type == "renders"
        assert (edge.meta or {}).get("detection_pattern") == "return_string"
        assert edge.dst.endswith(
            "src/main/resources/templates/users/show.html:1-1:show.html:template"
        )

    def test_jsp_under_webapp(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/main/webapp/WEB-INF/views/users/show.jsp")
        java_source = (
            "@Controller\n"
            "public class UserController {\n"
            "    @RequestMapping(\"/users/{id}\")\n"
            "    public String show() {\n"
            "        return \"users/show\";\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "src/main/java/com/ex/UserController.java", java_source)

        klass = _java_class(
            "com.ex.UserController",
            path="src/main/java/com/ex/UserController.java",
            decorators=_CONTROLLER_DECORATOR,
            end=8,
        )
        method = _java_method(
            "com.ex.UserController.show",
            path="src/main/java/com/ex/UserController.java",
            decorators=_REQUEST_MAPPING_USERS,
            start=4,
            end=6,
        )

        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst.endswith("show.jsp:1-1:show.jsp:template")
        assert result.edges[0].dst.startswith("jsp:")

    def test_freemarker_ftl(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/main/resources/templates/users/show.ftl")
        java_source = (
            "@Controller\n"
            "public class UserController {\n"
            "    @GetMapping(\"/users\")\n"
            "    public String show() {\n"
            "        return \"users/show\";\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "src/main/java/com/ex/UserController.java", java_source)

        klass = _java_class(
            "com.ex.UserController",
            path="src/main/java/com/ex/UserController.java",
            decorators=_CONTROLLER_DECORATOR,
            end=8,
        )
        method = _java_method(
            "com.ex.UserController.show",
            path="src/main/java/com/ex/UserController.java",
            decorators=_GET_MAPPING_USERS,
            start=4,
            end=6,
        )

        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst.startswith("ftl:")


class TestRedirectSkipped:
    """``return "redirect:..."`` is a redirect, not a view name."""

    def test_redirect_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/main/resources/templates/redirect:/foo.html")
        java_source = (
            "@Controller\n"
            "public class UserController {\n"
            "    @GetMapping(\"/save\")\n"
            "    public String save() {\n"
            "        return \"redirect:/foo\";\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "src/main/java/com/ex/UserController.java", java_source)

        klass = _java_class(
            "com.ex.UserController",
            path="src/main/java/com/ex/UserController.java",
            decorators=_CONTROLLER_DECORATOR,
            end=8,
        )
        method = _java_method(
            "com.ex.UserController.save",
            path="src/main/java/com/ex/UserController.java",
            decorators=_GET_MAPPING_USERS,
            start=4,
            end=6,
        )

        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)

        assert result.edges == []

    def test_forward_skipped(self, tmp_path: Path) -> None:
        """``forward:`` is also a non-template directive."""
        java_source = (
            "@Controller\n"
            "public class UserController {\n"
            "    @GetMapping(\"/x\")\n"
            "    public String save() {\n"
            "        return \"forward:/foo\";\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "src/main/java/com/ex/UserController.java", java_source)

        klass = _java_class(
            "com.ex.UserController",
            path="src/main/java/com/ex/UserController.java",
            decorators=_CONTROLLER_DECORATOR,
            end=8,
        )
        method = _java_method(
            "com.ex.UserController.save",
            path="src/main/java/com/ex/UserController.java",
            decorators=_GET_MAPPING_USERS,
            start=4,
            end=6,
        )

        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)

        assert result.edges == []


class TestRestControllerSkipped:
    """``@RestController`` classes return JSON — no view binding."""

    def test_rest_controller_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/main/resources/templates/users/show.html")
        java_source = (
            "@RestController\n"
            "public class UserApi {\n"
            "    @GetMapping(\"/api/users\")\n"
            "    public String list() {\n"
            "        return \"users/show\";\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "src/main/java/com/ex/UserApi.java", java_source)

        klass = _java_class(
            "com.ex.UserApi",
            path="src/main/java/com/ex/UserApi.java",
            decorators=_REST_CONTROLLER_DECORATOR,
            end=8,
        )
        method = _java_method(
            "com.ex.UserApi.list",
            path="src/main/java/com/ex/UserApi.java",
            decorators=_GET_MAPPING_USERS,
            start=4,
            end=6,
        )

        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)

        assert result.edges == []


class TestModelAndView:
    """``new ModelAndView("users/show", model)`` first-arg is the view name."""

    def test_model_and_view_first_arg(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/main/resources/templates/users/show.html")
        java_source = (
            "@Controller\n"
            "public class UserController {\n"
            "    @GetMapping(\"/users/{id}\")\n"
            "    public ModelAndView show() {\n"
            "        return new ModelAndView(\"users/show\", new ModelMap());\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "src/main/java/com/ex/UserController.java", java_source)

        klass = _java_class(
            "com.ex.UserController",
            path="src/main/java/com/ex/UserController.java",
            decorators=_CONTROLLER_DECORATOR,
            end=8,
        )
        method = _java_method(
            "com.ex.UserController.show",
            path="src/main/java/com/ex/UserController.java",
            decorators=_GET_MAPPING_USERS,
            start=4,
            end=6,
        )

        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert (edge.meta or {}).get("detection_pattern") == "model_and_view_first_arg"


class TestActionDetection:
    """Methods are gated on (a) enclosing class has @Controller (b) method has a mapping annotation."""

    def test_method_without_mapping_skipped(self, tmp_path: Path) -> None:
        """``@Controller`` class method without ``@*Mapping`` is not an action."""
        java_source = (
            "@Controller\n"
            "public class UserController {\n"
            "    public String show() {\n"
            "        return \"users/show\";\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "src/main/java/com/ex/UserController.java", java_source)
        _write(tmp_path, "src/main/resources/templates/users/show.html")

        klass = _java_class(
            "com.ex.UserController",
            path="src/main/java/com/ex/UserController.java",
            decorators=_CONTROLLER_DECORATOR,
            end=6,
        )
        method = _java_method(
            "com.ex.UserController.show",
            path="src/main/java/com/ex/UserController.java",
            decorators=None,  # no mapping annotation
            start=3,
            end=5,
        )

        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)

        assert result.edges == []

    def test_mixed_controller_and_helper_in_same_file(self, tmp_path: Path) -> None:
        """A method in a non-controller class is skipped even when a sibling
        ``@Controller`` class is present (covers ``enclosing not in
        controller_classes`` early-continue)."""
        _write(tmp_path, "src/main/resources/templates/users/show.html")
        java_source = (
            "@Controller\n"
            "public class UserController {\n"
            "    @GetMapping(\"/users\")\n"
            "    public String show() {\n"
            "        return \"users/show\";\n"
            "    }\n"
            "}\n"
            "class HelperClass {\n"
            "    @GetMapping(\"/helper\")\n"
            "    public String help() {\n"
            "        return \"helper/show\";\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "src/main/java/com/ex/UserController.java", java_source)

        controller_klass = _java_class(
            "com.ex.UserController",
            path="src/main/java/com/ex/UserController.java",
            decorators=_CONTROLLER_DECORATOR,
            end=7,
        )
        controller_method = _java_method(
            "com.ex.UserController.show",
            path="src/main/java/com/ex/UserController.java",
            decorators=_GET_MAPPING_USERS,
            start=3,
            end=5,
        )
        helper_klass = _java_class(
            "com.ex.HelperClass",
            path="src/main/java/com/ex/UserController.java",
            decorators=None,
            start=8,
            end=13,
        )
        # ``HelperClass`` exists but isn't a controller; its method should
        # be skipped at the controller-membership check.
        helper_method = _java_method(
            "com.ex.HelperClass.help",
            path="src/main/java/com/ex/UserController.java",
            decorators=_GET_MAPPING_USERS,
            start=10,
            end=12,
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[
                controller_klass,
                controller_method,
                helper_klass,
                helper_method,
            ],
        )
        result = link_view_template_spring(ctx)
        # Only the controller-bound show() gets an edge.
        assert len(result.edges) == 1
        assert result.edges[0].src == controller_method.id

    def test_no_controller_class_skipped(self, tmp_path: Path) -> None:
        """Method with ``@GetMapping`` but enclosing class has no ``@Controller``."""
        java_source = (
            "public class Service {\n"
            "    @GetMapping(\"/users\")\n"
            "    public String list() {\n"
            "        return \"users/list\";\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "src/main/java/com/ex/Service.java", java_source)
        _write(tmp_path, "src/main/resources/templates/users/list.html")

        klass = _java_class(
            "com.ex.Service",
            path="src/main/java/com/ex/Service.java",
            decorators=None,
            end=6,
        )
        method = _java_method(
            "com.ex.Service.list",
            path="src/main/java/com/ex/Service.java",
            decorators=_GET_MAPPING_USERS,
            start=2,
            end=5,
        )

        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)

        assert result.edges == []

    def test_all_mapping_variants_recognized(self, tmp_path: Path) -> None:
        """``@PostMapping`` (and friends) also count as action annotations."""
        _write(tmp_path, "src/main/resources/templates/users/save.html")
        java_source = (
            "@Controller\n"
            "public class UserController {\n"
            "    @PostMapping(\"/save\")\n"
            "    public String save() {\n"
            "        return \"users/save\";\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "src/main/java/com/ex/UserController.java", java_source)

        klass = _java_class(
            "com.ex.UserController",
            path="src/main/java/com/ex/UserController.java",
            decorators=_CONTROLLER_DECORATOR,
            end=6,
        )
        method = _java_method(
            "com.ex.UserController.save",
            path="src/main/java/com/ex/UserController.java",
            decorators=_POST_MAPPING,
            start=3,
            end=5,
        )

        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)

        assert len(result.edges) == 1


class TestEdgeCases:
    """Defensive paths."""

    def test_no_template_no_edge(self, tmp_path: Path) -> None:
        java_source = (
            "@Controller\n"
            "public class UserController {\n"
            "    @GetMapping(\"/users\")\n"
            "    public String show() {\n"
            "        return \"users/missing\";\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "src/main/java/com/ex/UserController.java", java_source)

        klass = _java_class(
            "com.ex.UserController",
            path="src/main/java/com/ex/UserController.java",
            decorators=_CONTROLLER_DECORATOR,
            end=6,
        )
        method = _java_method(
            "com.ex.UserController.show",
            path="src/main/java/com/ex/UserController.java",
            decorators=_GET_MAPPING_USERS,
            start=3,
            end=5,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)
        assert result.edges == []

    def test_non_modelandview_object_creation_skipped(
        self, tmp_path: Path
    ) -> None:
        """``return new HashMap();`` is not a ModelAndView; can't resolve."""
        java_source = (
            "@Controller\n"
            "public class UserController {\n"
            "    @GetMapping(\"/users\")\n"
            "    public Object show() {\n"
            "        return new HashMap();\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "src/main/java/com/ex/UserController.java", java_source)

        klass = _java_class(
            "com.ex.UserController",
            path="src/main/java/com/ex/UserController.java",
            decorators=_CONTROLLER_DECORATOR,
            end=6,
        )
        method = _java_method(
            "com.ex.UserController.show",
            path="src/main/java/com/ex/UserController.java",
            decorators=_GET_MAPPING_USERS,
            start=3,
            end=5,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)
        assert result.edges == []

    def test_model_and_view_with_non_literal_first_arg_skipped(
        self, tmp_path: Path
    ) -> None:
        """``new ModelAndView(computeName(), model)`` cannot be resolved."""
        java_source = (
            "@Controller\n"
            "public class UserController {\n"
            "    @GetMapping(\"/users\")\n"
            "    public ModelAndView show() {\n"
            "        return new ModelAndView(computeName(), new ModelMap());\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "src/main/java/com/ex/UserController.java", java_source)

        klass = _java_class(
            "com.ex.UserController",
            path="src/main/java/com/ex/UserController.java",
            decorators=_CONTROLLER_DECORATOR,
            end=6,
        )
        method = _java_method(
            "com.ex.UserController.show",
            path="src/main/java/com/ex/UserController.java",
            decorators=_GET_MAPPING_USERS,
            start=3,
            end=5,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)
        assert result.edges == []

    def test_bare_return_skipped(self, tmp_path: Path) -> None:
        """``return;`` with no expression."""
        java_source = (
            "@Controller\n"
            "public class UserController {\n"
            "    @GetMapping(\"/users\")\n"
            "    public void show() {\n"
            "        return;\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "src/main/java/com/ex/UserController.java", java_source)

        klass = _java_class(
            "com.ex.UserController",
            path="src/main/java/com/ex/UserController.java",
            decorators=_CONTROLLER_DECORATOR,
            end=6,
        )
        method = _java_method(
            "com.ex.UserController.show",
            path="src/main/java/com/ex/UserController.java",
            decorators=_GET_MAPPING_USERS,
            start=3,
            end=5,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)
        assert result.edges == []

    def test_non_string_return_skipped(self, tmp_path: Path) -> None:
        """``return helper.getView();`` cannot be statically resolved."""
        java_source = (
            "@Controller\n"
            "public class UserController {\n"
            "    @GetMapping(\"/users\")\n"
            "    public String show() {\n"
            "        return computeView();\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "src/main/java/com/ex/UserController.java", java_source)

        klass = _java_class(
            "com.ex.UserController",
            path="src/main/java/com/ex/UserController.java",
            decorators=_CONTROLLER_DECORATOR,
            end=6,
        )
        method = _java_method(
            "com.ex.UserController.show",
            path="src/main/java/com/ex/UserController.java",
            decorators=_GET_MAPPING_USERS,
            start=3,
            end=5,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)
        assert result.edges == []

    def test_no_symbols_returns_empty(self, tmp_path: Path) -> None:
        ctx = LinkerContext(repo_root=tmp_path, symbols=[])
        result = link_view_template_spring(ctx)
        assert result.edges == []
        assert result.symbols == []
        assert result.run is not None

    def test_unparseable_source_tolerated(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/main/java/com/ex/Broken.java",
            "@Controller class Broken {\n",  # truncated
        )

        klass = _java_class(
            "com.ex.Broken",
            path="src/main/java/com/ex/Broken.java",
            decorators=_CONTROLLER_DECORATOR,
        )
        method = _java_method(
            "com.ex.Broken.show",
            path="src/main/java/com/ex/Broken.java",
            decorators=_GET_MAPPING_USERS,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)
        # Tolerated; may produce 0 edges depending on partial-parse behavior.
        assert isinstance(result.edges, list)

    def test_missing_source_file_skipped(self, tmp_path: Path) -> None:
        """Symbol references a file that doesn't exist — tolerate."""
        klass = _java_class(
            "com.ex.UserController",
            path="src/main/java/com/ex/UserController.java",
            decorators=_CONTROLLER_DECORATOR,
        )
        method = _java_method(
            "com.ex.UserController.show",
            path="src/main/java/com/ex/UserController.java",
            decorators=_GET_MAPPING_USERS,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_spring(ctx)
        assert result.edges == []

    def test_non_java_symbol_skipped(self, tmp_path: Path) -> None:
        """Non-Java symbols in context are ignored."""
        ruby_sym = Symbol(
            id="ruby:app/controllers/users.rb:1-10:UsersController:class",
            name="UsersController",
            kind="class",
            language="ruby",
            path="app/controllers/users.rb",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            meta={"decorators": [{"name": "Controller", "args": [], "kwargs": {}}]},
            origin="ruby",
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[ruby_sym])
        result = link_view_template_spring(ctx)
        assert result.edges == []


class TestStrategyExposure:
    def test_spring_strategy_class_exposed(self) -> None:
        from hypergumbo_core.linkers._view_template_core import (
            ExplicitStringStrategy,
            TemplateStrategy,
        )

        assert issubclass(SpringStrategy, ExplicitStringStrategy)
        assert issubclass(SpringStrategy, TemplateStrategy)
