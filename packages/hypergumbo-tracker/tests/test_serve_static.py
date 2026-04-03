# SPDX-License-Identifier: MPL-2.0
"""Tests for htrac serve static file serving (BlockSuite frontend)."""
from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient


class TestStaticFiles:
    """Tests for static file serving."""

    def test_serves_index_html(self, tmp_path: Path) -> None:
        """Static dir with index.html is served at /."""
        from hypergumbo_tracker.serve import create_app

        static_dir = tmp_path / "frontend"
        static_dir.mkdir()
        (static_dir / "index.html").write_text("<html><body>htrac</body></html>")

        app = create_app(static_dir=static_dir)
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "htrac" in resp.text

    def test_serves_js_files(self, tmp_path: Path) -> None:
        """Static dir serves JavaScript files."""
        from hypergumbo_tracker.serve import create_app

        static_dir = tmp_path / "frontend"
        static_dir.mkdir()
        (static_dir / "index.html").write_text("<html></html>")
        (static_dir / "app.js").write_text("console.log('hello');")

        app = create_app(static_dir=static_dir)
        client = TestClient(app)
        resp = client.get("/app.js")
        assert resp.status_code == 200
        assert "hello" in resp.text

    def test_api_routes_take_precedence(self, tmp_path: Path) -> None:
        """API routes are matched before static files."""
        from hypergumbo_tracker.serve import create_app

        static_dir = tmp_path / "frontend"
        static_dir.mkdir()
        (static_dir / "index.html").write_text("<html></html>")

        app = create_app(static_dir=static_dir)
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_no_static_dir_still_works(self) -> None:
        """App works without static_dir."""
        from hypergumbo_tracker.serve import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_nonexistent_static_dir_ignored(self, tmp_path: Path) -> None:
        """Non-existent static_dir is silently ignored."""
        from hypergumbo_tracker.serve import create_app

        app = create_app(static_dir=tmp_path / "nonexistent")
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
