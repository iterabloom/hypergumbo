"""Tests for the Puppet manifest analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_common import puppet as puppet_module
from hypergumbo_lang_common.puppet import (
    PuppetAnalysisResult,
    analyze_puppet,
    find_puppet_files,
    is_puppet_tree_sitter_available,
)


def make_puppet_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Puppet file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindPuppetFiles:
    """Tests for find_puppet_files function."""

    def test_finds_pp_files(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", "class base {}")
        make_puppet_file(tmp_path, "modules/nginx/manifests/init.pp", "class nginx {}")
        files = find_puppet_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"init.pp"}

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_puppet_files(tmp_path)
        assert files == []


class TestIsPuppetTreeSitterAvailable:
    """Tests for is_puppet_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_puppet_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(puppet_module, "is_puppet_tree_sitter_available", return_value=False):
            assert puppet_module.is_puppet_tree_sitter_available() is False


class TestAnalyzePuppet:
    """Tests for analyze_puppet function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", "class base {}")
        with patch.object(puppet_module, "is_puppet_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Puppet analysis skipped"):
                result = puppet_module.analyze_puppet(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_puppet(tmp_path)
        assert result.symbols == []
        assert result.run is None

    def test_extracts_class(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", """class nginx {
  package { 'nginx':
    ensure => installed,
  }
}""")
        result = analyze_puppet(tmp_path)
        assert not result.skipped
        cls = next((s for s in result.symbols if s.kind == "class"), None)
        assert cls is not None
        assert cls.name == "nginx"
        assert "class nginx" in cls.signature

    def test_extracts_class_with_params(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", """class nginx (
  String $server_name = 'localhost',
  Integer $port = 80,
) {
}""")
        result = analyze_puppet(tmp_path)
        cls = next((s for s in result.symbols if s.kind == "class"), None)
        assert cls is not None
        assert cls.meta.get("param_count") == 2
        assert "server_name" in cls.meta.get("params", [])
        assert "port" in cls.meta.get("params", [])

    def test_extracts_defined_type(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "vhost.pp", """define nginx::vhost (
  String $server_name,
) {
}""")
        result = analyze_puppet(tmp_path)
        defined = next((s for s in result.symbols if s.kind == "defined_type"), None)
        assert defined is not None
        assert defined.name == "nginx::vhost"
        assert "define nginx::vhost" in defined.signature

    def test_extracts_resource_package(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", """class nginx {
  package { 'nginx':
    ensure => installed,
  }
}""")
        result = analyze_puppet(tmp_path)
        resource = next((s for s in result.symbols if s.kind == "resource"), None)
        assert resource is not None
        assert "package" in resource.name
        assert resource.meta.get("resource_type") == "package"
        assert resource.meta.get("title") == "nginx"
        assert resource.meta.get("ensure") == "installed"

    def test_extracts_resource_service(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", """class nginx {
  service { 'nginx':
    ensure => running,
    enable => true,
  }
}""")
        result = analyze_puppet(tmp_path)
        resource = next((s for s in result.symbols if s.kind == "resource"), None)
        assert resource is not None
        assert resource.meta.get("resource_type") == "service"
        assert resource.meta.get("ensure") == "running"

    def test_extracts_resource_file(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", """class nginx {
  file { '/etc/nginx/nginx.conf':
    ensure => file,
  }
}""")
        result = analyze_puppet(tmp_path)
        resource = next((s for s in result.symbols if s.kind == "resource"), None)
        assert resource is not None
        assert resource.meta.get("resource_type") == "file"
        assert resource.meta.get("title") == "/etc/nginx/nginx.conf"

    def test_extracts_node(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "site.pp", """node 'webserver.example.com' {
  include nginx
}""")
        result = analyze_puppet(tmp_path)
        node = next((s for s in result.symbols if s.kind == "node"), None)
        assert node is not None
        assert node.name == "webserver.example.com"
        assert "node 'webserver.example.com'" in node.signature

    def test_extracts_include(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "site.pp", """node 'server' {
  include nginx
}""")
        result = analyze_puppet(tmp_path)
        include = next((s for s in result.symbols if s.kind == "include"), None)
        assert include is not None
        assert include.name == "include nginx"
        assert include.meta.get("class_name") == "nginx"

    def test_creates_require_edge(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", """class nginx {
  package { 'nginx':
    ensure => installed,
  }
  service { 'nginx':
    ensure => running,
    require => Package['nginx'],
  }
}""")
        result = analyze_puppet(tmp_path)
        edge = next((e for e in result.edges if e.edge_type == "requires_resource"), None)
        assert edge is not None

    def test_creates_notify_edge(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", """class nginx {
  file { '/etc/nginx/nginx.conf':
    ensure => file,
    notify => Service['nginx'],
  }
}""")
        result = analyze_puppet(tmp_path)
        edge = next((e for e in result.edges if e.edge_type == "notifies_resource"), None)
        assert edge is not None

    def test_creates_includes_class_edge(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", """class nginx {
}

node 'server' {
  include nginx
}""")
        result = analyze_puppet(tmp_path)
        edge = next((e for e in result.edges if e.edge_type == "includes_class"), None)
        assert edge is not None

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", "class nginx {}")
        result = analyze_puppet(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "puppet.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0
        assert result.run.files_analyzed == 1

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", "class base {}")
        make_puppet_file(tmp_path, "nginx.pp", "class nginx {}")
        result = analyze_puppet(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 2

    def test_pass_id(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", "class nginx {}")
        result = analyze_puppet(tmp_path)
        cls = next((s for s in result.symbols if s.kind == "class"), None)
        assert cls is not None
        assert cls.origin == "puppet.tree_sitter"

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", "class nginx {}")
        result = analyze_puppet(tmp_path)
        cls = next((s for s in result.symbols if s.kind == "class"), None)
        assert cls is not None
        assert cls.id == cls.stable_id
        assert "puppet:" in cls.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", "class nginx {}")
        result = analyze_puppet(tmp_path)
        cls = next((s for s in result.symbols if s.kind == "class"), None)
        assert cls is not None
        assert cls.span is not None
        assert cls.span.start_line >= 1

    def test_complete_manifest(self, tmp_path: Path) -> None:
        """Test a complete Puppet manifest."""
        make_puppet_file(tmp_path, "init.pp", """class nginx (
  String $server_name = 'localhost',
  Integer $port = 80,
) {
  package { 'nginx':
    ensure => installed,
  }

  service { 'nginx':
    ensure => running,
    enable => true,
    require => Package['nginx'],
  }

  file { '/etc/nginx/nginx.conf':
    ensure  => file,
    notify  => Service['nginx'],
  }
}

define nginx::vhost (
  String $server_name,
) {
  file { "/etc/nginx/sites-enabled/${name}":
    ensure => file,
    notify => Service['nginx'],
  }
}

node 'webserver.example.com' {
  include nginx
  nginx::vhost { 'mysite':
    server_name => 'mysite.example.com',
  }
}""")
        result = analyze_puppet(tmp_path)

        # Check class
        cls = next((s for s in result.symbols if s.kind == "class"), None)
        assert cls is not None
        assert cls.name == "nginx"
        assert cls.meta.get("param_count") == 2

        # Check defined type
        defined = next((s for s in result.symbols if s.kind == "defined_type"), None)
        assert defined is not None
        assert defined.name == "nginx::vhost"

        # Check resources
        resources = [s for s in result.symbols if s.kind == "resource"]
        assert len(resources) >= 3  # package, service, file, vhost resource

        # Check node
        node = next((s for s in result.symbols if s.kind == "node"), None)
        assert node is not None
        assert node.name == "webserver.example.com"

        # Check include
        include = next((s for s in result.symbols if s.kind == "include"), None)
        assert include is not None

        # Check edges
        require_edges = [e for e in result.edges if e.edge_type == "requires_resource"]
        assert len(require_edges) >= 1
        notify_edges = [e for e in result.edges if e.edge_type == "notifies_resource"]
        assert len(notify_edges) >= 2
        include_edges = [e for e in result.edges if e.edge_type == "includes_class"]
        assert len(include_edges) >= 1
