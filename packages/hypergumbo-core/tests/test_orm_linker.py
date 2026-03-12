# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ORM query linker.

The ORM linker detects ORM query patterns (e.g., User.objects.filter()) in source
files and creates model_reference edges from the enclosing function to the Model
symbol. This increases the in-degree centrality of Model classes, improving their
ranking in behavior maps.

Supported ORM patterns:
- Django: ModelName.objects.<method>()
- Flask-SQLAlchemy: ModelName.query.<method>()
"""

from pathlib import Path
from textwrap import dedent

import pytest

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.linkers.orm import (
    _build_model_lookup,
    _build_orm_pattern,
    _scan_orm_references,
    link_orm_queries,
    PASS_ID,
)
from hypergumbo_core.linkers.registry import LinkerContext


def _make_symbol(
    name: str,
    kind: str = "class",
    path: str = "models.py",
    start_line: int = 1,
    end_line: int = 10,
    language: str = "python",
    concepts: list[dict] | None = None,
) -> Symbol:
    """Helper to create a Symbol with optional concept metadata."""
    meta = {}
    if concepts:
        meta["concepts"] = concepts
    return Symbol(
        id=f"python:{path}:{start_line}-{end_line}:{name}",
        name=name,
        kind=kind,
        path=path,
        span=Span(start_line=start_line, start_col=0, end_line=end_line, end_col=0),
        language=language,
        meta=meta if meta else None,
    )


class TestBuildModelLookup:
    """Tests for _build_model_lookup."""

    def test_finds_model_symbols(self) -> None:
        """Symbols with concept 'model' are included in lookup."""
        user = _make_symbol("User", concepts=[{"concept": "model", "framework": "django"}])
        post = _make_symbol("Post", concepts=[{"concept": "model", "framework": "django"}])
        view = _make_symbol("UserView", kind="function", concepts=[{"concept": "controller"}])

        lookup = _build_model_lookup([user, post, view])
        assert "User" in lookup
        assert "Post" in lookup
        assert "UserView" not in lookup

    def test_empty_when_no_models(self) -> None:
        """Returns empty dict when no model symbols exist."""
        func = _make_symbol("my_func", kind="function")
        lookup = _build_model_lookup([func])
        assert lookup == {}

    def test_ignores_symbols_without_concepts(self) -> None:
        """Symbols without concept metadata are skipped."""
        sym = _make_symbol("User", kind="class")
        sym.meta = None
        lookup = _build_model_lookup([sym])
        assert lookup == {}

    def test_handles_flask_sqlalchemy_model(self) -> None:
        """Flask-SQLAlchemy models (concept: model) are included."""
        model = _make_symbol(
            "User", concepts=[{"concept": "model", "framework": "flask"}]
        )
        lookup = _build_model_lookup([model])
        assert "User" in lookup

    def test_deduplicates_model_names(self) -> None:
        """When multiple symbols have the same model name, first one wins."""
        user1 = _make_symbol(
            "User", path="app/models.py",
            concepts=[{"concept": "model", "framework": "django"}],
        )
        user2 = _make_symbol(
            "User", path="other/models.py",
            concepts=[{"concept": "model", "framework": "django"}],
        )
        lookup = _build_model_lookup([user1, user2])
        assert "User" in lookup
        assert lookup["User"].path == "app/models.py"


class TestBuildOrmPattern:
    """Tests for _build_orm_pattern."""

    def test_builds_pattern_for_single_model(self) -> None:
        """Pattern matches single model name with .objects. accessor."""
        pattern = _build_orm_pattern(["User"])
        assert pattern is not None
        assert pattern.search("User.objects.filter(name='test')")
        assert pattern.search("User.query.filter_by(name='test')")

    def test_builds_pattern_for_multiple_models(self) -> None:
        """Pattern matches any of the provided model names."""
        pattern = _build_orm_pattern(["User", "Post", "Comment"])
        assert pattern is not None
        assert pattern.search("User.objects.all()")
        assert pattern.search("Post.objects.get(id=1)")
        assert pattern.search("Comment.query.first()")

    def test_returns_none_for_empty_list(self) -> None:
        """Returns None when no model names are provided."""
        pattern = _build_orm_pattern([])
        assert pattern is None

    def test_does_not_match_non_orm_attributes(self) -> None:
        """Pattern does not match arbitrary attribute access."""
        pattern = _build_orm_pattern(["User"])
        assert not pattern.search("User.name")
        assert not pattern.search("User.save()")
        assert not pattern.search("user.objects.filter()")  # lowercase

    def test_matches_word_boundaries(self) -> None:
        """Pattern respects word boundaries — no partial matches."""
        pattern = _build_orm_pattern(["User"])
        assert not pattern.search("SuperUser.objects.all()")
        assert pattern.search("User.objects.all()")


class TestScanOrmReferences:
    """Tests for _scan_orm_references (source file scanning)."""

    def test_detects_django_objects_filter(self, tmp_path: Path) -> None:
        """Detects User.objects.filter() in Python source."""
        source = dedent("""\
            from models import User

            def get_active_users():
                return User.objects.filter(is_active=True)
        """)
        f = tmp_path / "views.py"
        f.write_text(source)

        import re
        pattern = re.compile(r"\b(User)\.(objects|query)\.\w+")
        refs = _scan_orm_references(f, source, pattern)
        assert len(refs) == 1
        assert refs[0].model_name == "User"
        assert refs[0].line == 4
        assert refs[0].accessor == "objects"

    def test_detects_multiple_orm_calls(self, tmp_path: Path) -> None:
        """Detects multiple ORM calls in same file."""
        source = dedent("""\
            def view():
                users = User.objects.all()
                posts = Post.objects.filter(published=True)
                first_comment = Comment.query.first()
        """)
        f = tmp_path / "views.py"
        f.write_text(source)

        import re
        pattern = re.compile(r"\b(User|Post|Comment)\.(objects|query)\.\w+")
        refs = _scan_orm_references(f, source, pattern)
        assert len(refs) == 3
        model_names = {r.model_name for r in refs}
        assert model_names == {"User", "Post", "Comment"}

    def test_detects_flask_sqlalchemy_query(self, tmp_path: Path) -> None:
        """Detects User.query.filter_by() in Python source."""
        source = dedent("""\
            def get_user(user_id):
                return User.query.filter_by(id=user_id).first()
        """)
        f = tmp_path / "views.py"
        f.write_text(source)

        import re
        pattern = re.compile(r"\b(User)\.(objects|query)\.\w+")
        refs = _scan_orm_references(f, source, pattern)
        assert len(refs) == 1
        assert refs[0].accessor == "query"

    def test_ignores_non_python_files(self, tmp_path: Path) -> None:
        """Only scans .py files."""
        source = "User.objects.all()"
        f = tmp_path / "views.js"
        f.write_text(source)

        import re
        pattern = re.compile(r"\b(User)\.(objects|query)\.\w+")
        refs = _scan_orm_references(f, source, pattern)
        assert len(refs) == 0

    def test_deduplicates_same_line(self, tmp_path: Path) -> None:
        """Multiple ORM calls on same line to same model produce one reference."""
        # Use two distinct ORM accessor patterns on the same line
        source = "data = User.objects.filter(pk__in=User.objects.values('id'))\n"
        f = tmp_path / "views.py"
        f.write_text(source)

        import re
        pattern = re.compile(r"\b(User)\.(objects|query)\.\w+")
        refs = _scan_orm_references(f, source, pattern)
        # Should deduplicate same model on same line
        assert len(refs) == 1


class TestLinkOrmQueries:
    """Integration tests for link_orm_queries."""

    def test_creates_model_reference_edges(self, tmp_path: Path) -> None:
        """Creates edges from view function to model class."""
        # Create a model symbol
        user_model = _make_symbol(
            "User",
            kind="class",
            path=str(tmp_path / "models.py"),
            start_line=1,
            end_line=10,
            concepts=[{"concept": "model", "framework": "django"}],
        )
        # Create a view function symbol
        view_func = _make_symbol(
            "get_users",
            kind="function",
            path=str(tmp_path / "views.py"),
            start_line=3,
            end_line=5,
        )

        # Create the source file with ORM call
        views_py = tmp_path / "views.py"
        views_py.write_text(dedent("""\
            from models import User

            def get_users():
                return User.objects.all()
        """))

        result = link_orm_queries(
            root=tmp_path,
            symbols=[user_model, view_func],
        )

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == view_func.id
        assert edge.dst == user_model.id
        assert edge.edge_type == "model_reference"
        assert edge.confidence == 0.85
        assert edge.meta["accessor"] == "objects"
        assert edge.meta["model_name"] == "User"

    def test_no_edges_when_no_models(self, tmp_path: Path) -> None:
        """Produces no edges when no model symbols exist."""
        func = _make_symbol(
            "my_func",
            kind="function",
            path=str(tmp_path / "views.py"),
        )
        views_py = tmp_path / "views.py"
        views_py.write_text("def my_func(): pass\n")

        result = link_orm_queries(root=tmp_path, symbols=[func])
        assert len(result.edges) == 0
        assert result.run is not None

    def test_no_edges_when_no_orm_calls(self, tmp_path: Path) -> None:
        """Produces no edges when source has no ORM calls."""
        user_model = _make_symbol(
            "User",
            kind="class",
            path=str(tmp_path / "models.py"),
            concepts=[{"concept": "model", "framework": "django"}],
        )
        views_py = tmp_path / "views.py"
        views_py.write_text(dedent("""\
            def my_func():
                return "hello"
        """))

        result = link_orm_queries(root=tmp_path, symbols=[user_model])
        assert len(result.edges) == 0

    def test_multiple_models_multiple_files(self, tmp_path: Path) -> None:
        """Detects ORM calls across multiple files to multiple models."""
        user = _make_symbol(
            "User",
            kind="class",
            path=str(tmp_path / "models.py"),
            start_line=1, end_line=10,
            concepts=[{"concept": "model", "framework": "django"}],
        )
        post = _make_symbol(
            "Post",
            kind="class",
            path=str(tmp_path / "models.py"),
            start_line=12, end_line=20,
            concepts=[{"concept": "model", "framework": "django"}],
        )
        view1 = _make_symbol(
            "list_users",
            kind="function",
            path=str(tmp_path / "user_views.py"),
            start_line=1, end_line=3,
        )
        view2 = _make_symbol(
            "list_posts",
            kind="function",
            path=str(tmp_path / "post_views.py"),
            start_line=1, end_line=3,
        )

        (tmp_path / "user_views.py").write_text(dedent("""\
            def list_users():
                return User.objects.all()
        """))
        (tmp_path / "post_views.py").write_text(dedent("""\
            def list_posts():
                return Post.objects.filter(published=True)
        """))

        result = link_orm_queries(root=tmp_path, symbols=[user, post, view1, view2])
        assert len(result.edges) == 2
        edge_models = {e.meta["model_name"] for e in result.edges}
        assert edge_models == {"User", "Post"}

    def test_creates_analysis_run(self, tmp_path: Path) -> None:
        """Creates proper AnalysisRun metadata."""
        user = _make_symbol(
            "User",
            kind="class",
            path=str(tmp_path / "models.py"),
            concepts=[{"concept": "model", "framework": "django"}],
        )
        (tmp_path / "views.py").write_text("pass\n")

        result = link_orm_queries(root=tmp_path, symbols=[user])
        assert result.run is not None
        assert result.run.pass_id == PASS_ID


    def test_no_edge_when_enclosing_function_not_found(self, tmp_path: Path) -> None:
        """No edge when ORM call is outside any known function span."""
        user = _make_symbol(
            "User",
            kind="class",
            path=str(tmp_path / "models.py"),
            concepts=[{"concept": "model", "framework": "django"}],
        )
        # Create a view file with ORM call but NO function symbol covering line 2
        (tmp_path / "views.py").write_text(dedent("""\
            from models import User
            users = User.objects.all()
        """))

        result = link_orm_queries(root=tmp_path, symbols=[user])
        assert len(result.edges) == 0

    def test_deduplicates_same_function_same_model(self, tmp_path: Path) -> None:
        """Same function querying same model twice produces only one edge."""
        user = _make_symbol(
            "User",
            kind="class",
            path=str(tmp_path / "models.py"),
            concepts=[{"concept": "model", "framework": "django"}],
        )
        view = _make_symbol(
            "get_users",
            kind="function",
            path=str(tmp_path / "views.py"),
            start_line=1, end_line=5,
        )
        (tmp_path / "views.py").write_text(dedent("""\
            def get_users():
                active = User.objects.filter(is_active=True)
                admins = User.objects.filter(is_admin=True)
                return active, admins
        """))

        result = link_orm_queries(root=tmp_path, symbols=[user, view])
        # Two ORM calls in same function to same model → one edge
        assert len(result.edges) == 1


class TestLinkerRegistration:
    """Tests for linker registry integration."""

    def _ensure_registered(self) -> None:
        """Ensure ORM linker is registered (handles registry clearing by other tests)."""
        import importlib
        import hypergumbo_core.linkers.orm as orm_module
        importlib.reload(orm_module)

    def test_registered_in_registry(self) -> None:
        """ORM linker is registered with correct name."""
        from hypergumbo_core.linkers.registry import get_linker
        self._ensure_registered()

        linker = get_linker("orm")
        assert linker is not None
        assert linker.name == "orm"

    def test_linker_via_context(self, tmp_path: Path) -> None:
        """ORM linker works when invoked via registry context."""
        from hypergumbo_core.linkers.registry import run_linker
        self._ensure_registered()

        user = _make_symbol(
            "User",
            kind="class",
            path=str(tmp_path / "models.py"),
            concepts=[{"concept": "model", "framework": "django"}],
        )
        view = _make_symbol(
            "get_users",
            kind="function",
            path=str(tmp_path / "views.py"),
            start_line=1, end_line=3,
        )
        (tmp_path / "views.py").write_text(dedent("""\
            def get_users():
                return User.objects.all()
        """))

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[user, view],
        )
        result = run_linker("orm", ctx)
        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "model_reference"
