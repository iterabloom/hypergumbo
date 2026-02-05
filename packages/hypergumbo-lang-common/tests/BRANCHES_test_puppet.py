"""Branch coverage tests for puppet.py analyzer.

Tests specific branch paths in the Puppet analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Class definition extraction
- Defined type extraction
- Resource declaration extraction
- Node definition extraction
- Include statement extraction
- Require/notify edges
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.puppet import (
    _make_symbol_id,
    analyze_puppet,
    find_puppet_files,
)


def make_puppet_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Puppet manifest file with given content."""
    (tmp_path / name).write_text(content)


class TestPuppetHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        from pathlib import Path
        symbol_id = _make_symbol_id(Path("manifests/init.pp"), "myclass", "class", 1)
        assert symbol_id == "puppet:manifests/init.pp:class:1:myclass"


class TestClassExtraction:
    """Branch coverage for class definition extraction."""

    def test_simple_class(self, tmp_path: Path) -> None:
        """Test simple class extraction."""
        make_puppet_file(tmp_path, "init.pp", """
class nginx {
  package { 'nginx':
    ensure => installed,
  }
}
""")
        result = analyze_puppet(tmp_path)
        assert not result.skipped

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) >= 1
        assert any(c.name == "nginx" for c in classes)

    def test_class_with_params(self, tmp_path: Path) -> None:
        """Test class with parameters."""
        make_puppet_file(tmp_path, "init.pp", """
class nginx (
  $port = 80,
  $root = '/var/www',
) {
  service { 'nginx':
    ensure => running,
  }
}
""")
        result = analyze_puppet(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class" and s.name == "nginx"]
        assert len(classes) == 1
        assert classes[0].meta is not None
        assert classes[0].meta.get("param_count") >= 2


class TestDefinedTypeExtraction:
    """Branch coverage for defined type extraction."""

    def test_defined_type(self, tmp_path: Path) -> None:
        """Test defined type extraction."""
        make_puppet_file(tmp_path, "vhost.pp", """
define apache::vhost (
  $port,
  $docroot,
) {
  file { "/etc/apache2/sites-enabled/${name}.conf":
    ensure  => file,
    content => template('apache/vhost.conf.erb'),
  }
}
""")
        result = analyze_puppet(tmp_path)
        defined_types = [s for s in result.symbols if s.kind == "defined_type"]
        assert len(defined_types) >= 1


class TestResourceExtraction:
    """Branch coverage for resource declaration extraction."""

    def test_package_resource(self, tmp_path: Path) -> None:
        """Test package resource extraction."""
        make_puppet_file(tmp_path, "init.pp", """
package { 'nginx':
  ensure => installed,
}
""")
        result = analyze_puppet(tmp_path)
        resources = [s for s in result.symbols if s.kind == "resource"]
        assert len(resources) >= 1

    def test_service_resource(self, tmp_path: Path) -> None:
        """Test service resource extraction."""
        make_puppet_file(tmp_path, "init.pp", """
service { 'nginx':
  ensure => running,
  enable => true,
}
""")
        result = analyze_puppet(tmp_path)
        resources = [s for s in result.symbols if s.kind == "resource"]
        assert len(resources) >= 1

    def test_file_resource(self, tmp_path: Path) -> None:
        """Test file resource extraction."""
        make_puppet_file(tmp_path, "init.pp", """
file { '/etc/nginx/nginx.conf':
  ensure  => file,
  content => template('nginx/nginx.conf.erb'),
}
""")
        result = analyze_puppet(tmp_path)
        resources = [s for s in result.symbols if s.kind == "resource"]
        assert len(resources) >= 1

    def test_resource_with_require(self, tmp_path: Path) -> None:
        """Test resource with require creates edge."""
        make_puppet_file(tmp_path, "init.pp", """
package { 'nginx':
  ensure => installed,
}

service { 'nginx':
  ensure  => running,
  require => Package['nginx'],
}
""")
        result = analyze_puppet(tmp_path)
        requires = [e for e in result.edges if e.edge_type == "requires_resource"]
        assert len(requires) >= 1

    def test_resource_with_notify(self, tmp_path: Path) -> None:
        """Test resource with notify creates edge."""
        make_puppet_file(tmp_path, "init.pp", """
file { '/etc/nginx/nginx.conf':
  ensure => file,
  notify => Service['nginx'],
}

service { 'nginx':
  ensure => running,
}
""")
        result = analyze_puppet(tmp_path)
        notifies = [e for e in result.edges if e.edge_type == "notifies_resource"]
        assert len(notifies) >= 1


class TestNodeExtraction:
    """Branch coverage for node definition extraction."""

    def test_node_definition(self, tmp_path: Path) -> None:
        """Test node definition extraction."""
        make_puppet_file(tmp_path, "site.pp", """
node 'web.example.com' {
  include nginx
}
""")
        result = analyze_puppet(tmp_path)
        nodes = [s for s in result.symbols if s.kind == "node"]
        assert len(nodes) >= 1
        assert any(n.name == "web.example.com" for n in nodes)

    def test_default_node(self, tmp_path: Path) -> None:
        """Test default node definition."""
        make_puppet_file(tmp_path, "site.pp", """
node 'default' {
  include base
}
""")
        result = analyze_puppet(tmp_path)
        nodes = [s for s in result.symbols if s.kind == "node"]
        assert len(nodes) >= 1


class TestIncludeExtraction:
    """Branch coverage for include statement extraction."""

    def test_include_statement(self, tmp_path: Path) -> None:
        """Test include statement extraction."""
        make_puppet_file(tmp_path, "site.pp", """
node 'web.example.com' {
  include nginx
}
""")
        result = analyze_puppet(tmp_path)
        includes = [s for s in result.symbols if s.kind == "include"]
        assert len(includes) >= 1

    def test_include_creates_edge(self, tmp_path: Path) -> None:
        """Test include creates edge when class is defined."""
        make_puppet_file(tmp_path, "init.pp", """
class nginx {
  package { 'nginx':
    ensure => installed,
  }
}

include nginx
""")
        result = analyze_puppet(tmp_path)
        includes_edges = [e for e in result.edges if e.edge_type == "includes_class"]
        assert len(includes_edges) >= 1


class TestFindPuppetFiles:
    """Branch coverage for file discovery."""

    def test_finds_pp_files(self, tmp_path: Path) -> None:
        """Test .pp files are discovered."""
        (tmp_path / "init.pp").write_text("class test { }")

        files = list(find_puppet_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".pp"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        manifests = tmp_path / "manifests"
        manifests.mkdir()
        (manifests / "init.pp").write_text("class test { }")

        files = list(find_puppet_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "init.pp"


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_puppet_files(self, tmp_path: Path) -> None:
        """Test directory with no Puppet files."""
        result = analyze_puppet(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_class(self, tmp_path: Path) -> None:
        """Test minimal Puppet class."""
        make_puppet_file(tmp_path, "init.pp", """
class minimal { }
""")
        result = analyze_puppet(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_puppet_file(tmp_path, "init.pp", """
class nginx {
  package { 'nginx': ensure => installed }
}
""")
        result = analyze_puppet(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1


class TestClassSignatures:
    """Branch coverage for class signature extraction."""

    def test_class_signature_with_params(self, tmp_path: Path) -> None:
        """Test class signature includes parameters."""
        make_puppet_file(tmp_path, "init.pp", """
class nginx (
  $port,
  $enabled,
) {
}
""")
        result = analyze_puppet(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) >= 1
        assert classes[0].signature is not None
        assert "port" in classes[0].signature
