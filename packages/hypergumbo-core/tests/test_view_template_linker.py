# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for view template linker.

The view template linker creates renders edges from Rails controller action
methods to their convention-based view template files. It probes the file
system for templates matching the controller/action naming convention.
"""

from pathlib import Path

import pytest

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.view_template import (
    PASS_ID,
    _camel_to_snake,
    _controller_to_view_dir,
    _count_controller_methods,
    _is_action_method,
    _probe_template_files,
    link_view_template,
    link_view_templates,
)
from hypergumbo_core.linkers.registry import LinkerContext


def _make_controller_class(name: str, base: str = "ApplicationController") -> Symbol:
    """Helper to create a controller class symbol."""
    return Symbol(
        id=f"ruby:app/controllers/{name.lower()}.rb:1-50:{name}:class",
        name=name,
        kind="class",
        language="ruby",
        path=f"app/controllers/{name.lower()}.rb",
        span=Span(start_line=1, end_line=50, start_col=0, end_col=3),
        meta={"base_classes": [base]},
        origin="ruby-v1",
    )


def _make_method(class_name: str, method_name: str, start: int = 5) -> Symbol:
    """Helper to create a controller method symbol."""
    return Symbol(
        id=f"ruby:app/controllers/{class_name.lower()}.rb:{start}-{start+5}:{class_name}#{method_name}:method",
        name=f"{class_name}#{method_name}",
        kind="method",
        language="ruby",
        path=f"app/controllers/{class_name.lower()}.rb",
        span=Span(start_line=start, end_line=start + 5, start_col=2, end_col=5),
        origin="ruby-v1",
    )


class TestControllerToViewDir:
    """Tests for _controller_to_view_dir."""

    def test_simple_controller(self) -> None:
        assert _controller_to_view_dir("UsersController") == "users"

    def test_namespaced_controller(self) -> None:
        assert _controller_to_view_dir("Admin::UsersController") == "admin/users"

    def test_deeply_namespaced_controller(self) -> None:
        assert _controller_to_view_dir("Api::V1::AccountsController") == "api/v1/accounts"

    def test_multi_word_controller(self) -> None:
        assert _controller_to_view_dir("UserSessionsController") == "user_sessions"


class TestCamelToSnake:
    """Tests for _camel_to_snake."""

    def test_simple(self) -> None:
        assert _camel_to_snake("Users") == "users"

    def test_multi_word(self) -> None:
        assert _camel_to_snake("UserSessions") == "user_sessions"

    def test_acronym(self) -> None:
        assert _camel_to_snake("IPPoolRules") == "ip_pool_rules"

    def test_single_char(self) -> None:
        assert _camel_to_snake("A") == "a"

    def test_lowercase(self) -> None:
        assert _camel_to_snake("admin") == "admin"


class TestIsActionMethod:
    """Tests for _is_action_method."""

    def test_regular_action(self) -> None:
        assert _is_action_method("index") is True

    def test_initialize_skipped(self) -> None:
        assert _is_action_method("initialize") is False

    def test_private_helper_skipped(self) -> None:
        assert _is_action_method("_set_user") is False


class TestProbeTemplateFiles:
    """Tests for _probe_template_files."""

    def test_finds_erb_template(self, tmp_path: Path) -> None:
        template_dir = tmp_path / "app" / "views" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "index.html.erb").write_text("<h1>Users</h1>")

        result = _probe_template_files(tmp_path, "users", "index")

        assert len(result) == 1
        assert str(result[0]) == "app/views/users/index.html.erb"

    def test_finds_haml_template(self, tmp_path: Path) -> None:
        template_dir = tmp_path / "app" / "views" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "show.html.haml").write_text("%h1 User")

        result = _probe_template_files(tmp_path, "users", "show")

        assert len(result) == 1
        assert str(result[0]) == "app/views/users/show.html.haml"

    def test_finds_multiple_formats(self, tmp_path: Path) -> None:
        """Both .html.erb and .text.erb templates for same action."""
        template_dir = tmp_path / "app" / "views" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "show.html.erb").write_text("<h1>User</h1>")
        (template_dir / "show.text.erb").write_text("User")

        result = _probe_template_files(tmp_path, "users", "show")

        assert len(result) == 2
        paths = {str(p) for p in result}
        assert "app/views/users/show.html.erb" in paths
        assert "app/views/users/show.text.erb" in paths

    def test_no_template_returns_empty(self, tmp_path: Path) -> None:
        result = _probe_template_files(tmp_path, "users", "missing")

        assert result == []

    def test_namespaced_directory(self, tmp_path: Path) -> None:
        template_dir = tmp_path / "app" / "views" / "admin" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "index.html.erb").write_text("<h1>Admin Users</h1>")

        result = _probe_template_files(tmp_path, "admin/users", "index")

        assert len(result) == 1
        assert str(result[0]) == "app/views/admin/users/index.html.erb"

    def test_jbuilder_template(self, tmp_path: Path) -> None:
        template_dir = tmp_path / "app" / "views" / "api" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "index.json.jbuilder").write_text("json.array! @users")

        result = _probe_template_files(tmp_path, "api/users", "index")

        assert len(result) == 1
        assert str(result[0]) == "app/views/api/users/index.json.jbuilder"

    def test_slim_template(self, tmp_path: Path) -> None:
        template_dir = tmp_path / "app" / "views" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "new.html.slim").write_text("h1 New User")

        result = _probe_template_files(tmp_path, "users", "new")

        assert len(result) == 1
        assert str(result[0]) == "app/views/users/new.html.slim"


class TestLinkViewTemplates:
    """Tests for the main link_view_templates function."""

    def test_convention_render_creates_edge(self, tmp_path: Path) -> None:
        """Controller method → matching template file creates renders edge."""
        # Set up template on disk
        template_dir = tmp_path / "app" / "views" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "index.html.erb").write_text("<h1>Users</h1>")

        controller = _make_controller_class("UsersController")
        method = _make_method("UsersController", "index")

        result = link_view_templates(tmp_path, [controller, method], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == method.id
        assert edge.dst == "erb:app/views/users/index.html.erb:1-1:index.html.erb:template"
        assert edge.edge_type == "renders"
        assert edge.evidence_type == "implicit_convention"
        assert edge.confidence == 0.85

    def test_namespaced_controller(self, tmp_path: Path) -> None:
        """Admin::UsersController#index → admin/users/index.html.erb."""
        template_dir = tmp_path / "app" / "views" / "admin" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "index.html.erb").write_text("<h1>Admin Users</h1>")

        controller = _make_controller_class("Admin::UsersController")
        method = Symbol(
            id="ruby:app/controllers/admin/users_controller.rb:5-10:Admin::UsersController#index:method",
            name="Admin::UsersController#index",
            kind="method",
            language="ruby",
            path="app/controllers/admin/users_controller.rb",
            span=Span(start_line=5, end_line=10, start_col=2, end_col=5),
            origin="ruby-v1",
        )

        result = link_view_templates(tmp_path, [controller, method], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "renders"
        assert edge.dst == "erb:app/views/admin/users/index.html.erb:1-1:index.html.erb:template"

    def test_haml_template_detected(self, tmp_path: Path) -> None:
        """Controller method matches .html.haml template."""
        template_dir = tmp_path / "app" / "views" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "show.html.haml").write_text("%h1 User")

        controller = _make_controller_class("UsersController")
        method = _make_method("UsersController", "show")

        result = link_view_templates(tmp_path, [controller, method], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.dst == "haml:app/views/users/show.html.haml:1-1:show.html.haml:template"

    def test_no_template_no_edge(self, tmp_path: Path) -> None:
        """Missing template file → no edge created."""
        controller = _make_controller_class("UsersController")
        method = _make_method("UsersController", "index")

        result = link_view_templates(tmp_path, [controller, method], [])

        assert len(result.edges) == 0
        assert len(result.symbols) == 0

    def test_non_controller_class_skipped(self, tmp_path: Path) -> None:
        """Model methods with # naming are skipped when class is not a controller."""
        template_dir = tmp_path / "app" / "views" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "index.html.erb").write_text("<h1>Users</h1>")

        # A real controller exists (so controller_classes is non-empty)
        controller = _make_controller_class("UsersController")
        controller_method = _make_method("UsersController", "index")

        # User model inherits from ApplicationRecord, not ApplicationController
        model = Symbol(
            id="ruby:app/models/user.rb:1-20:User:class",
            name="User",
            kind="class",
            language="ruby",
            path="app/models/user.rb",
            span=Span(start_line=1, end_line=20, start_col=0, end_col=3),
            meta={"base_classes": ["ApplicationRecord"]},
            origin="ruby-v1",
        )
        # Method on the model with # naming — should not trigger linking
        model_method = Symbol(
            id="ruby:app/models/user.rb:5-10:User#save_record:method",
            name="User#save_record",
            kind="method",
            language="ruby",
            path="app/models/user.rb",
            span=Span(start_line=5, end_line=10, start_col=2, end_col=5),
            origin="ruby-v1",
        )

        result = link_view_templates(
            tmp_path, [controller, controller_method, model, model_method], []
        )

        # Only the controller method gets an edge, not the model method
        assert len(result.edges) == 1
        assert result.edges[0].src == controller_method.id

    def test_class_method_skipped(self, tmp_path: Path) -> None:
        """UsersController.find (class method with .) is not an action."""
        template_dir = tmp_path / "app" / "views" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "find.html.erb").write_text("<h1>Find</h1>")

        controller = _make_controller_class("UsersController")
        # Class method uses . separator, not #
        class_method = Symbol(
            id="ruby:app/controllers/users_controller.rb:5-10:UsersController.find:method",
            name="UsersController.find",
            kind="method",
            language="ruby",
            path="app/controllers/users_controller.rb",
            span=Span(start_line=5, end_line=10, start_col=2, end_col=5),
            origin="ruby-v1",
        )

        result = link_view_templates(tmp_path, [controller, class_method], [])

        assert len(result.edges) == 0

    def test_template_symbol_created(self, tmp_path: Path) -> None:
        """Linker creates template file symbols."""
        template_dir = tmp_path / "app" / "views" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "index.html.erb").write_text("<h1>Users</h1>")

        controller = _make_controller_class("UsersController")
        method = _make_method("UsersController", "index")

        result = link_view_templates(tmp_path, [controller, method], [])

        assert len(result.symbols) == 1
        template_sym = result.symbols[0]
        assert template_sym.kind == "template"
        assert template_sym.language == "erb"
        assert template_sym.name == "index.html.erb"
        assert template_sym.path == "app/views/users/index.html.erb"
        assert template_sym.origin == PASS_ID

    def test_multiple_actions_same_controller(self, tmp_path: Path) -> None:
        """Multiple actions in one controller each get their own edge."""
        template_dir = tmp_path / "app" / "views" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "index.html.erb").write_text("<h1>Index</h1>")
        (template_dir / "show.html.erb").write_text("<h1>Show</h1>")

        controller = _make_controller_class("UsersController")
        index_method = _make_method("UsersController", "index", start=5)
        show_method = _make_method("UsersController", "show", start=15)

        result = link_view_templates(
            tmp_path, [controller, index_method, show_method], []
        )

        assert len(result.edges) == 2
        assert len(result.symbols) == 2

        edge_dsts = {e.dst for e in result.edges}
        assert "erb:app/views/users/index.html.erb:1-1:index.html.erb:template" in edge_dsts
        assert "erb:app/views/users/show.html.erb:1-1:show.html.erb:template" in edge_dsts

    def test_private_method_skipped(self, tmp_path: Path) -> None:
        """Methods starting with _ are not actions."""
        template_dir = tmp_path / "app" / "views" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "_set_user.html.erb").write_text("partial")

        controller = _make_controller_class("UsersController")
        method = _make_method("UsersController", "_set_user")

        result = link_view_templates(tmp_path, [controller, method], [])

        assert len(result.edges) == 0

    def test_initialize_skipped(self, tmp_path: Path) -> None:
        """initialize method is not a controller action."""
        template_dir = tmp_path / "app" / "views" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "initialize.html.erb").write_text("init")

        controller = _make_controller_class("UsersController")
        method = _make_method("UsersController", "initialize")

        result = link_view_templates(tmp_path, [controller, method], [])

        assert len(result.edges) == 0

    def test_action_controller_base(self, tmp_path: Path) -> None:
        """Controllers inheriting from ActionController::Base are detected."""
        template_dir = tmp_path / "app" / "views" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "index.html.erb").write_text("<h1>Users</h1>")

        controller = _make_controller_class("UsersController", base="ActionController::Base")
        method = _make_method("UsersController", "index")

        result = link_view_templates(tmp_path, [controller, method], [])

        assert len(result.edges) == 1

    def test_action_controller_api(self, tmp_path: Path) -> None:
        """Controllers inheriting from ActionController::API are detected."""
        template_dir = tmp_path / "app" / "views" / "api" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "index.json.jbuilder").write_text("json.array!")

        controller = _make_controller_class(
            "Api::UsersController", base="ActionController::API"
        )
        method = Symbol(
            id="ruby:app/controllers/api/users_controller.rb:5-10:Api::UsersController#index:method",
            name="Api::UsersController#index",
            kind="method",
            language="ruby",
            path="app/controllers/api/users_controller.rb",
            span=Span(start_line=5, end_line=10, start_col=2, end_col=5),
            origin="ruby-v1",
        )

        result = link_view_templates(tmp_path, [controller, method], [])

        assert len(result.edges) == 1
        assert result.edges[0].dst.startswith("ruby:")

    def test_no_controllers_returns_empty(self, tmp_path: Path) -> None:
        """No controller classes → empty result with run metadata."""
        method = _make_method("UsersController", "index")

        result = link_view_templates(tmp_path, [method], [])

        assert len(result.edges) == 0
        assert len(result.symbols) == 0
        assert result.run is not None

    def test_dedup_template_symbols(self, tmp_path: Path) -> None:
        """Same template file referenced by two methods creates only one symbol."""
        template_dir = tmp_path / "app" / "views" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "show.html.erb").write_text("<h1>Show</h1>")
        (template_dir / "show.text.erb").write_text("Show")

        controller = _make_controller_class("UsersController")
        method = _make_method("UsersController", "show")

        result = link_view_templates(tmp_path, [controller, method], [])

        # Two edges (one for each format), but distinct template symbols
        assert len(result.edges) == 2
        assert len(result.symbols) == 2  # .html.erb and .text.erb are different files

        # Verify no duplicate symbol IDs
        sym_ids = [s.id for s in result.symbols]
        assert len(sym_ids) == len(set(sym_ids))

    def test_run_metadata(self, tmp_path: Path) -> None:
        """Result includes AnalysisRun metadata."""
        controller = _make_controller_class("UsersController")
        method = _make_method("UsersController", "index")

        result = link_view_templates(tmp_path, [controller, method], [])

        assert result.run is not None
        assert result.run.pass_id == PASS_ID


class TestCountControllerMethods:
    """Tests for _count_controller_methods requirement check."""

    def test_counts_controller_methods(self) -> None:
        controller = _make_controller_class("UsersController")
        method = _make_method("UsersController", "index")

        ctx = LinkerContext(
            repo_root=Path("/tmp/test"),
            symbols=[controller, method],
        )

        assert _count_controller_methods(ctx) == 1

    def test_zero_without_controllers(self) -> None:
        method = _make_method("UsersController", "index")

        ctx = LinkerContext(
            repo_root=Path("/tmp/test"),
            symbols=[method],
        )

        assert _count_controller_methods(ctx) == 0


class TestRegistryEntryPoint:
    """Tests for the registered linker entry point."""

    def test_link_view_template_entry_point(self, tmp_path: Path) -> None:
        """Registry entry point delegates to link_view_templates."""
        template_dir = tmp_path / "app" / "views" / "users"
        template_dir.mkdir(parents=True)
        (template_dir / "index.html.erb").write_text("<h1>Users</h1>")

        controller = _make_controller_class("UsersController")
        method = _make_method("UsersController", "index")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[controller, method],
        )

        result = link_view_template(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "renders"
