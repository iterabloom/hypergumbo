# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Play Framework routes file parser.

Verifies that conf/routes files are correctly parsed into route symbols
with HTTP method, URL path, and controller action metadata. Tests cover
basic routes, path parameters, query parameters, module includes,
comments, and edge cases.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.play_routes import (
    analyze_play_routes,
    find_play_routes_files,
    parse_play_routes,
)


class TestParsePlayRoutes:
    """Tests for the line-by-line routes parser."""

    def test_basic_get_route(self) -> None:
        content = "GET  /  controllers.Application.index"
        syms, edges = parse_play_routes(content, "conf/routes", "run-1")
        assert len(syms) == 1
        assert syms[0].kind == "route"
        assert syms[0].name == "GET /"
        assert syms[0].meta["http_method"] == "GET"
        assert syms[0].meta["route_path"] == "/"
        assert syms[0].meta["controller_action"] == "controllers.Application.index"

    def test_post_route_with_path_param(self) -> None:
        content = "POST  /users/:userId  controllers.Users.update(userId: Int)"
        syms, _ = parse_play_routes(content, "conf/routes", "run-1")
        assert len(syms) == 1
        assert syms[0].meta["http_method"] == "POST"
        assert syms[0].meta["route_path"] == "/users/:userId"
        assert syms[0].meta["controller_action"] == "controllers.Users.update"

    def test_multiple_routes(self) -> None:
        content = """\
GET   /                controllers.Lobby.home
GET   /lobby/seeks      controllers.Lobby.seeks
POST  /timeline/unsub/:channel  controllers.Timeline.unsub(channel)
"""
        syms, _ = parse_play_routes(content, "conf/routes", "run-1")
        assert len(syms) == 3
        methods = [s.meta["http_method"] for s in syms]
        assert methods == ["GET", "GET", "POST"]
        paths = [s.meta["route_path"] for s in syms]
        assert paths == ["/", "/lobby/seeks", "/timeline/unsub/:channel"]

    def test_comments_and_blank_lines_ignored(self) -> None:
        content = """\
# This is a comment
GET  /home  controllers.Home.index

# Another comment

GET  /about  controllers.About.index
"""
        syms, _ = parse_play_routes(content, "conf/routes", "run-1")
        assert len(syms) == 2

    def test_regex_path_parameter(self) -> None:
        content = r"GET  /files/$id<\d+>  controllers.Files.show(id: Int)"
        syms, _ = parse_play_routes(content, "conf/routes", "run-1")
        assert len(syms) == 1
        assert r"/files/$id<\d+>" in syms[0].meta["route_path"]

    def test_delete_route(self) -> None:
        content = "DELETE  /api/data/:id  controllers.Api.deleteData(id: Int)"
        syms, _ = parse_play_routes(content, "conf/routes", "run-1")
        assert len(syms) == 1
        assert syms[0].meta["http_method"] == "DELETE"

    def test_put_and_patch_routes(self) -> None:
        content = """\
PUT    /api/data/:id  controllers.Api.updateData(id: Int)
PATCH  /api/data/:id  controllers.Api.patchData(id: Int)
"""
        syms, _ = parse_play_routes(content, "conf/routes", "run-1")
        assert len(syms) == 2
        assert syms[0].meta["http_method"] == "PUT"
        assert syms[1].meta["http_method"] == "PATCH"

    def test_instance_injection_at_prefix(self) -> None:
        content = "GET  /page  @controllers.MyController.index"
        syms, _ = parse_play_routes(content, "conf/routes", "run-1")
        assert len(syms) == 1
        # @ prefix should be stripped
        assert syms[0].meta["controller_action"] == "controllers.MyController.index"

    def test_module_include(self) -> None:
        content = "->  /api  api.Routes"
        syms, _ = parse_play_routes(content, "conf/routes", "run-1")
        assert len(syms) == 1
        assert syms[0].kind == "route_include"
        assert syms[0].meta["route_prefix"] == "/api"
        assert syms[0].meta["module_ref"] == "api.Routes"

    def test_query_params_stripped_from_action(self) -> None:
        content = "GET  /search  controllers.Search.index(q: String ?= \"default\")"
        syms, _ = parse_play_routes(content, "conf/routes", "run-1")
        assert len(syms) == 1
        assert syms[0].meta["controller_action"] == "controllers.Search.index"

    def test_route_symbols_have_scala_language(self) -> None:
        content = "GET  /test  controllers.Test.index"
        syms, _ = parse_play_routes(content, "conf/routes", "run-1")
        assert syms[0].language == "scala"

    def test_route_symbols_have_correct_span(self) -> None:
        content = """\
# Comment
GET  /first  controllers.First.index
GET  /second  controllers.Second.index
"""
        syms, _ = parse_play_routes(content, "conf/routes", "run-1")
        assert syms[0].span.start_line == 2
        assert syms[1].span.start_line == 3

    def test_empty_content_returns_no_symbols(self) -> None:
        syms, _ = parse_play_routes("", "conf/routes", "run-1")
        assert len(syms) == 0

    def test_comment_only_returns_no_symbols(self) -> None:
        syms, _ = parse_play_routes("# Just a comment\n# Another", "conf/routes", "run-1")
        assert len(syms) == 0

    def test_head_and_options_methods(self) -> None:
        content = """\
HEAD     /ping  controllers.Health.ping
OPTIONS  /api   controllers.Api.options
"""
        syms, _ = parse_play_routes(content, "conf/routes", "run-1")
        assert len(syms) == 2
        assert syms[0].meta["http_method"] == "HEAD"
        assert syms[1].meta["http_method"] == "OPTIONS"


class TestFindPlayRoutesFiles:
    """Tests for routes file discovery."""

    def test_finds_main_routes_file(self, tmp_path: Path) -> None:
        conf = tmp_path / "conf"
        conf.mkdir()
        routes = conf / "routes"
        routes.write_text("GET  /  controllers.Home.index")
        found = list(find_play_routes_files(tmp_path))
        assert len(found) >= 1
        assert routes in found

    def test_finds_partial_routes_files(self, tmp_path: Path) -> None:
        conf = tmp_path / "conf"
        conf.mkdir()
        (conf / "api.routes").write_text("GET  /api  controllers.Api.index")
        found = list(find_play_routes_files(tmp_path))
        assert any(f.name == "api.routes" for f in found)

    def test_finds_subproject_routes(self, tmp_path: Path) -> None:
        # Sub-project routes in module/conf/routes
        sub_conf = tmp_path / "module" / "conf"
        sub_conf.mkdir(parents=True)
        sub_routes = sub_conf / "routes"
        sub_routes.write_text("GET  /sub  controllers.Sub.index")
        found = list(find_play_routes_files(tmp_path))
        assert sub_routes in found

    def test_subproject_dedup_with_main(self, tmp_path: Path) -> None:
        # When conf/routes exists, */conf/routes should not duplicate it
        conf = tmp_path / "conf"
        conf.mkdir()
        main_routes = conf / "routes"
        main_routes.write_text("GET  /  controllers.Home.index")
        found = list(find_play_routes_files(tmp_path))
        # main_routes should appear exactly once (not duplicated by */conf/routes glob)
        assert found.count(main_routes) == 1

    def test_no_routes_returns_empty(self, tmp_path: Path) -> None:
        found = list(find_play_routes_files(tmp_path))
        assert len(found) == 0


class TestAnalyzePlayRoutes:
    """Tests for the full analyzer integration."""

    def test_analyze_creates_route_symbols(self, tmp_path: Path) -> None:
        conf = tmp_path / "conf"
        conf.mkdir()
        (conf / "routes").write_text("""\
# Lobby
GET   /                controllers.Lobby.home
GET   /lobby/seeks      controllers.Lobby.seeks
POST  /api/data         controllers.Api.create
""")
        result = analyze_play_routes(tmp_path)
        assert not result.skipped
        assert len(result.symbols) == 3
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 3
        paths = {s.meta["route_path"] for s in routes}
        assert "/" in paths
        assert "/lobby/seeks" in paths
        assert "/api/data" in paths

    def test_analyze_empty_repo_not_skipped(self, tmp_path: Path) -> None:
        result = analyze_play_routes(tmp_path)
        assert not result.skipped
        assert len(result.symbols) == 0

    def test_analyze_with_module_includes(self, tmp_path: Path) -> None:
        conf = tmp_path / "conf"
        conf.mkdir()
        (conf / "routes").write_text("""\
GET  /  controllers.Home.index
->   /api  api.Routes
""")
        result = analyze_play_routes(tmp_path)
        assert len(result.symbols) == 2
        routes = [s for s in result.symbols if s.kind == "route"]
        includes = [s for s in result.symbols if s.kind == "route_include"]
        assert len(routes) == 1
        assert len(includes) == 1

    def test_lila_style_routes(self, tmp_path: Path) -> None:
        """Test routes in the style of lichess/lila's conf/routes."""
        conf = tmp_path / "conf"
        conf.mkdir()
        (conf / "routes").write_text("""\
# Run ./lila.sh playRoutes after modifying this file

# Lobby
GET   /                                controllers.Lobby.home
GET   /lobby/seeks                     controllers.Lobby.seeks

# 2-letter routes
GET   /$lang<\\w\\w>                     controllers.Lobby.homeLang(lang: Language)

# Account
GET   /account/info                    controllers.Account.info

# Game export
POST  /games/export/_ids               controllers.Game.exportByIds
GET   /games/export/:username          controllers.Game.exportByUser(username: UserStr)

# Bookmark
POST  /bookmark/$gameId<\\w{8}>         controllers.Game.bookmark(gameId: GameId)
""")
        result = analyze_play_routes(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 7
        # Check specific routes
        actions = {s.meta["controller_action"] for s in routes}
        assert "controllers.Lobby.home" in actions
        assert "controllers.Account.info" in actions
        assert "controllers.Game.exportByIds" in actions
        assert "controllers.Game.bookmark" in actions
