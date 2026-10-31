# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Puppet manifest analyzer."""

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_lang_common import puppet as puppet_module
from hypergumbo_lang_common.puppet import analyze_puppet, find_puppet_files, is_puppet_tree_sitter_available

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
        with patch.object(puppet_module._analyzer, "_check_grammar_available", return_value=False):
            assert puppet_module.is_puppet_tree_sitter_available() is False

class TestAnalyzePuppet:
    """Tests for analyze_puppet function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", "class base {}")
        with patch.object(puppet_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="puppet analysis skipped"):
                result = puppet_module.analyze_puppet(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_empty_repo(self, tmp_path: Path) -> None:
        # The base pipeline reports a run that read no file (the
        # orchestrator's no_candidate_files), not a missing run.
        result = analyze_puppet(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is not None
        assert result.run.files_analyzed == 0

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
        # Cluster E sub-case (b) per audit-findings 0010: include Symbol was
        # dropped; the includes_class Edge carries the relationship.
        edge = next(
            (e for e in result.edges if e.edge_type == "includes" and (e.meta or {}).get("ref_construct") == "puppet_class"
             and (e.meta or {}).get("class_name") == "nginx"),
            None,
        )
        assert edge is not None

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
        edge = next((e for e in result.edges if e.edge_type == "depends_on" and (e.meta or {}).get("ref_construct") == "puppet_require"), None)
        assert edge is not None

    def test_creates_notify_edge(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", """class nginx {
  file { '/etc/nginx/nginx.conf':
    ensure => file,
    notify => Service['nginx'],
  }
}""")
        result = analyze_puppet(tmp_path)
        edge = next((e for e in result.edges if e.edge_type == "depends_on" and (e.meta or {}).get("ref_construct") == "puppet_notify"), None)
        assert edge is not None

    def test_creates_includes_class_edge(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", """class nginx {
}

node 'server' {
  include nginx
}""")
        result = analyze_puppet(tmp_path)
        edge = next((e for e in result.edges if e.edge_type == "includes" and (e.meta or {}).get("ref_construct") == "puppet_class"), None)
        assert edge is not None

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", "class nginx {}")
        result = analyze_puppet(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "puppet"
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
        assert cls.origin == ["puppet"]

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", "class nginx {}")
        result = analyze_puppet(tmp_path)
        cls = next((s for s in result.symbols if s.kind == "class"), None)
        assert cls is not None
        # INV-dulah: node.id and stable_id are minted together by
        # make_doc_symbol_ids; node.id is the canonical ADR-0036
        # "{lang}:{path}:{start}-{end}:{name}:{kind}" (was the doc-family
        # kind-third/name-last order, which put the kind word in the span slot).
        # Parsed RIGHT-anchored, the way the canonical parser does
        # (span, name, kind = parts[-3:]), so a colon in the path cannot shift it.
        _head, _span, _name, _kind = cls.id.rsplit(":", 3)
        assert _head.startswith("puppet:"), cls.id
        assert re.match(r"^\d+-\d+$", _span), cls.id
        assert _kind == cls.kind, cls.id
        assert cls.stable_id != cls.id
        assert cls.stable_id.startswith("sha256:")

    def test_all_symbols_have_canonical_stable_id(self, tmp_path: Path) -> None:
        # Reuse the complete-manifest fixture, which a passing test
        # (test_complete_manifest) already exercises to yield class,
        # defined_type, multiple resources, and a node symbol.
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
        assert not result.skipped
        assert len(result.symbols) >= 1
        canonical = re.compile(r"^sha256:[0-9a-f]{16}$")
        for sym in result.symbols:
            assert canonical.match(sym.stable_id), (
                f"non-canonical stable_id for {sym.kind} {sym.name}: {sym.stable_id}"
            )

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

        # Check edges — Cluster E sub-case (b) per audit-findings 0010:
        # include Symbol dropped; includes_class Edge carries the relation.
        require_edges = [e for e in result.edges if e.edge_type == "depends_on" and (e.meta or {}).get("ref_construct") == "puppet_require"]
        assert len(require_edges) >= 1
        notify_edges = [e for e in result.edges if e.edge_type == "depends_on" and (e.meta or {}).get("ref_construct") == "puppet_notify"]
        assert len(notify_edges) >= 2
        include_edges = [e for e in result.edges if e.edge_type == "includes" and (e.meta or {}).get("ref_construct") == "puppet_class"]
        assert len(include_edges) >= 1


def _class_includes(result: AnalysisResult) -> list:
    return [
        e for e in result.edges
        if e.edge_type == "includes" and (e.meta or {}).get("ref_construct") == "puppet_class"
    ]


class TestIncludeResolutionIsOrderIndependent:
    """WI-vahun: an include resolves against every class in the repository,
    wherever it is declared. Pass 1 collects the definitions of every file;
    Pass 2 resolves, so neither the position of a class in its file nor the
    sort order of the files decides whether an include binds."""

    def test_class_defined_lower_in_the_same_file(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "a.pp", "class alpha {\n  include beta\n}\nclass beta {\n}\n")
        result = analyze_puppet(tmp_path)
        beta = next(s for s in result.symbols if s.kind == "class" and s.name == "beta")
        edges = [e for e in _class_includes(result) if e.meta["class_name"] == "beta"]
        assert len(edges) == 1  # reach: the include site is seen
        assert edges[0].dst == beta.id
        assert edges[0].is_resolved is True

    def test_class_defined_in_a_later_sorted_file(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "a.pp", "class alpha {\n  include zeta\n}\n")
        make_puppet_file(tmp_path, "z.pp", "class zeta {\n}\n")
        result = analyze_puppet(tmp_path)
        zeta = next(s for s in result.symbols if s.kind == "class" and s.name == "zeta")
        edges = [e for e in _class_includes(result) if e.meta["class_name"] == "zeta"]
        assert len(edges) == 1
        assert edges[0].dst == zeta.id
        assert edges[0].is_resolved is True

    def test_every_include_of_an_existing_class_binds_to_it(self, tmp_path: Path) -> None:
        """The item's repro: both targets exist, so no include dangles."""
        make_puppet_file(
            tmp_path, "a.pp",
            "class alpha {\n  include beta\n  include zeta\n}\nclass beta {\n}\n",
        )
        make_puppet_file(tmp_path, "z.pp", "class zeta {\n}\n")
        result = analyze_puppet(tmp_path)
        class_ids = {s.id for s in result.symbols if s.kind == "class"}
        edges = _class_includes(result)
        assert {e.meta["class_name"] for e in edges} == {"beta", "zeta"}
        for e in edges:
            assert e.is_resolved is True
            assert e.dst in class_ids

    def test_resolution_does_not_depend_on_file_order(self, tmp_path: Path) -> None:
        """The same manifests under swapped file names bind identically."""
        include = "class web {\n  include db\n}\n"
        define = "class db {\n}\n"
        first = tmp_path / "first"
        second = tmp_path / "second"
        make_puppet_file(first, "a.pp", include)
        make_puppet_file(first, "b.pp", define)
        make_puppet_file(second, "a.pp", define)
        make_puppet_file(second, "b.pp", include)
        outcomes = []
        for root in (first, second):
            result = analyze_puppet(root)
            db = next(s for s in result.symbols if s.name == "db")
            (edge,) = _class_includes(result)
            outcomes.append((edge.is_resolved, edge.dst == db.id))
        assert outcomes == [(True, True), (True, True)]

    def test_include_of_an_absent_class_stays_unresolved(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "a.pp", "class alpha {\n  include missing\n}\n")
        result = analyze_puppet(tmp_path)
        (edge,) = _class_includes(result)
        assert edge.is_resolved is False
        assert edge.dst not in {s.id for s in result.symbols}

    def test_a_same_named_non_class_is_not_an_include_target(self, tmp_path: Path) -> None:
        """Only a class is a class include's target: a node or defined type
        sharing the name is not bound."""
        make_puppet_file(
            tmp_path, "a.pp",
            "node 'web' {\n  include web\n  include site::thing\n}\n"
            "define site::thing {\n}\n",
        )
        result = analyze_puppet(tmp_path)
        edges = _class_includes(result)
        assert {e.meta["class_name"] for e in edges} == {"web", "site::thing"}
        assert all(e.is_resolved is False for e in edges)


class TestRelationshipEdgeSources:
    """require/notify edges leave the resource that declares them."""

    def test_each_relationship_edge_leaves_its_own_resource(self, tmp_path: Path) -> None:
        make_puppet_file(tmp_path, "init.pp", """class nginx {
  service { 'nginx':
    require => Package['nginx'],
  }
  file { '/etc/nginx.conf':
    notify => Service['nginx'],
  }
}""")
        result = analyze_puppet(tmp_path)
        by_name = {s.name: s for s in result.symbols if s.kind == "resource"}
        rel = {
            (e.meta or {}).get("ref_construct"): e for e in result.edges if e.edge_type == "depends_on"
        }
        assert rel["puppet_require"].src == by_name["service[nginx]"].id
        assert rel["puppet_notify"].src == by_name["file[/etc/nginx.conf]"].id
        assert rel["puppet_require"].line == by_name["service[nginx]"].span.start_line
