# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for YAML/Ansible analyzer."""
from pathlib import Path

import pytest
from hypergumbo_core.analyze.base import find_child_by_type
from unittest.mock import patch, MagicMock

from hypergumbo_lang_mainstream import yaml_ansible as yaml_module

class TestYAMLHelpers:
    """Tests for YAML analyzer helper functions."""

    def test_find_child_by_type_returns_none(self) -> None:
        """Returns None when no matching child type is found."""

        mock_node = MagicMock()
        mock_child = MagicMock()
        mock_child.type = "different_type"
        mock_node.children = [mock_child]

        result = find_child_by_type(mock_node, "block_mapping")
        assert result is None

class TestFindAnsibleFiles:
    """Tests for Ansible file discovery."""

    def test_finds_ansible_playbooks(self, tmp_path: Path) -> None:
        """Finds Ansible playbook files."""
        from hypergumbo_lang_mainstream.yaml_ansible import find_ansible_files

        (tmp_path / "playbook.yml").write_text("- hosts: all")
        (tmp_path / "site.yml").write_text("- hosts: webservers")
        (tmp_path / "other.txt").write_text("not ansible")

        files = list(find_ansible_files(tmp_path))

        assert len(files) == 2
        assert all(f.suffix in (".yml", ".yaml") for f in files)

    def test_finds_ansible_roles_tasks(self, tmp_path: Path) -> None:
        """Finds Ansible role task files."""
        from hypergumbo_lang_mainstream.yaml_ansible import find_ansible_files

        # Create role structure
        tasks_dir = tmp_path / "roles" / "webserver" / "tasks"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "main.yml").write_text("- name: Install nginx\n  apt: name=nginx")

        files = list(find_ansible_files(tmp_path))

        assert len(files) == 1
        assert "main.yml" in files[0].name

class TestYAMLTreeSitterAvailability:
    """Tests for tree-sitter-yaml availability checking."""

    def test_is_yaml_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-yaml is available."""
        from hypergumbo_lang_mainstream.yaml_ansible import is_yaml_tree_sitter_available

        result = is_yaml_tree_sitter_available()
        assert result is True

    def test_is_yaml_tree_sitter_available_false(self) -> None:
        """Returns False when grammar is not available."""
        from hypergumbo_lang_mainstream.yaml_ansible import is_yaml_tree_sitter_available

        with patch.object(yaml_module._analyzer, "_check_grammar_available", return_value=False):
            assert is_yaml_tree_sitter_available() is False

class TestAnalyzeYAMLFallback:
    """Tests for fallback behavior when tree-sitter-yaml unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-yaml unavailable."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        (tmp_path / "playbook.yml").write_text("- hosts: all")

        with patch.object(yaml_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="yaml_ansible analysis skipped"):
                result = analyze_ansible(tmp_path)

        assert result.skipped is True
        assert "not available" in result.skip_reason

class TestAnsiblePlaybookExtraction:
    """Tests for extracting Ansible playbooks."""

    def test_extracts_playbook_with_name(self, tmp_path: Path) -> None:
        """Extracts named playbooks."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        playbook = tmp_path / "deploy.yml"
        playbook.write_text('''
---
- name: Deploy application
  hosts: webservers
  tasks:
    - name: Copy files
      copy:
        src: app/
        dest: /opt/app/
''')

        result = analyze_ansible(tmp_path)

        playbooks = [s for s in result.symbols if s.kind == "playbook"]
        assert len(playbooks) >= 1

class TestAnsibleTaskExtraction:
    """Tests for extracting Ansible tasks."""

    def test_extracts_tasks_with_names(self, tmp_path: Path) -> None:
        """Extracts named tasks."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        playbook = tmp_path / "playbook.yml"
        playbook.write_text('''
- hosts: all
  tasks:
    - name: Install packages
      apt:
        name: nginx

    - name: Start service
      service:
        name: nginx
        state: started
''')

        result = analyze_ansible(tmp_path)

        tasks = [s for s in result.symbols if s.kind == "task"]
        task_names = [s.name for s in tasks]
        assert "Install packages" in task_names or len(tasks) >= 1

class TestAnsibleHandlerExtraction:
    """Tests for extracting Ansible handlers."""

    def test_extracts_handlers(self, tmp_path: Path) -> None:
        """Extracts handler definitions."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        playbook = tmp_path / "playbook.yml"
        playbook.write_text('''
- hosts: all
  tasks:
    - name: Update config
      template:
        src: nginx.conf.j2
        dest: /etc/nginx/nginx.conf
      notify: restart nginx

  handlers:
    - name: restart nginx
      service:
        name: nginx
        state: restarted
''')

        result = analyze_ansible(tmp_path)

        handlers = [s for s in result.symbols if s.kind == "handler"]
        assert len(handlers) >= 1

class TestAnsibleIncludeEdges:
    """Tests for extracting include/import edges."""

    def test_extracts_include_tasks(self, tmp_path: Path) -> None:
        """Extracts include_tasks references."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        playbook = tmp_path / "playbook.yml"
        playbook.write_text('''
- hosts: all
  tasks:
    - include_tasks: common.yml
    - import_tasks: setup.yml
''')

        result = analyze_ansible(tmp_path)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 2

class TestAnsibleFileNodes:
    """Tests for file-level node creation and edge resolution."""

    def test_file_level_nodes_created(self, tmp_path: Path) -> None:
        """Each processed Ansible file gets a file-level node."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "main.yml").write_text("""
- name: Install package
  yum: name=nginx
""")

        result = analyze_ansible(tmp_path)

        file_nodes = [s for s in result.symbols if s.kind == "file"]
        assert len(file_nodes) >= 1
        assert any("main.yml" in s.path for s in file_nodes)

    def test_edge_dst_resolves_to_file_node(self, tmp_path: Path) -> None:
        """include_tasks edge dst resolves to the target file's node ID."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "main.yml").write_text("""
- include_tasks: common.yml
- import_tasks: setup.yml
""")
        (tasks_dir / "common.yml").write_text("""
- name: Common task
  debug: msg="common"
""")
        (tasks_dir / "setup.yml").write_text("""
- name: Setup task
  debug: msg="setup"
""")

        result = analyze_ansible(tmp_path)

        # File nodes should exist for all 3 files
        file_nodes = [s for s in result.symbols if s.kind == "file"]
        assert len(file_nodes) >= 3

        # Edge dst should be a valid node ID (not raw filename)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 2

        node_ids = {s.id for s in result.symbols}
        for edge in import_edges:
            assert edge.src in node_ids, f"Edge src {edge.src} not in node set"
            assert edge.dst in node_ids, f"Edge dst {edge.dst} not in node set"

    def test_unresolvable_edge_dst_kept_with_lower_confidence(self, tmp_path: Path) -> None:
        """Edges to non-existent files are kept but with lower confidence."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "main.yml").write_text("""
- include_tasks: "{{ dynamic_path }}/tasks.yml"
- include_tasks: nonexistent.yml
""")

        result = analyze_ansible(tmp_path)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1
        # Unresolvable edges get lower confidence
        for edge in import_edges:
            assert edge.confidence < 0.95

    def test_include_role_resolves_to_role_main(self, tmp_path: Path) -> None:
        """include_role with name= resolves to roles/<name>/tasks/main.yml."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "main.yml").write_text("""
- include_role: name=basessh
""")
        # Create the target role structure
        role_dir = tmp_path / "roles" / "basessh" / "tasks"
        role_dir.mkdir(parents=True)
        (role_dir / "main.yml").write_text("""
- name: Base SSH setup
  debug: msg="ssh"
""")

        result = analyze_ansible(tmp_path)

        import_edges = [e for e in result.edges if e.evidence_type == "include_role"]
        assert len(import_edges) == 1
        # Should resolve to the role's main.yml node
        node_ids = {s.id for s in result.symbols}
        assert import_edges[0].dst in node_ids

    def test_multiple_files_same_basename_prefers_same_dir(self, tmp_path: Path) -> None:
        """When multiple files have the same name, prefer same-directory match."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        # Create two common.yml in different dirs
        tasks_a = tmp_path / "roles" / "a" / "tasks"
        tasks_a.mkdir(parents=True)
        tasks_b = tmp_path / "roles" / "b" / "tasks"
        tasks_b.mkdir(parents=True)

        (tasks_a / "main.yml").write_text("""
- include_tasks: common.yml
""")
        (tasks_a / "common.yml").write_text("""
- name: Common A
  debug: msg="a"
""")
        (tasks_b / "common.yml").write_text("""
- name: Common B
  debug: msg="b"
""")

        result = analyze_ansible(tmp_path)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        # Should resolve to the common.yml in the same directory (roles/a/tasks/)
        assert len(import_edges) >= 1
        resolved_edge = import_edges[0]
        node_ids = {s.id for s in result.symbols}
        assert resolved_edge.dst in node_ids
        # The dst should point to the common.yml in roles/a/tasks/
        assert "roles/a/tasks/common.yml" in resolved_edge.dst

    def test_include_role_missing_role_unresolvable(self, tmp_path: Path) -> None:
        """include_role with name= for missing role gets lower confidence."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "main.yml").write_text("""
- include_role: name=nonexistent_role
""")

        result = analyze_ansible(tmp_path)

        import_edges = [e for e in result.edges if e.evidence_type == "include_role"]
        assert len(import_edges) == 1
        # Should be unresolvable → lower confidence
        assert import_edges[0].confidence == 0.50

    def test_multiple_files_same_basename_different_dir_fallback(self, tmp_path: Path) -> None:
        """When source is in a different dir from all candidates, fall back to first."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        # Source file in one dir, two candidates in other dirs
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "main.yml").write_text("""
- include_tasks: common.yml
""")

        # Both candidates in different dirs from source
        dir_x = tmp_path / "roles" / "x" / "tasks"
        dir_x.mkdir(parents=True)
        dir_y = tmp_path / "roles" / "y" / "tasks"
        dir_y.mkdir(parents=True)
        (dir_x / "common.yml").write_text("- name: X\n  debug: msg=x\n")
        (dir_y / "common.yml").write_text("- name: Y\n  debug: msg=y\n")

        result = analyze_ansible(tmp_path)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1
        # Should resolve to one of the candidates (fallback to first)
        node_ids = {s.id for s in result.symbols}
        assert import_edges[0].dst in node_ids

    def test_no_ansible_files_returns_empty(self, tmp_path: Path) -> None:
        """Empty directory with no Ansible files returns empty result."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        # Create a non-ansible file
        (tmp_path / "readme.md").write_text("# Hello")

        result = analyze_ansible(tmp_path)
        assert len(result.symbols) == 0
        assert len(result.edges) == 0


class TestAnsibleVariableExtraction:
    """Tests for extracting Ansible variables."""

    def test_extracts_vars_section(self, tmp_path: Path) -> None:
        """Extracts variables from vars section."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        playbook = tmp_path / "playbook.yml"
        playbook.write_text('''
- hosts: all
  vars:
    http_port: 80
    server_name: webserver
  tasks:
    - debug: msg="{{ server_name }}"
''')

        result = analyze_ansible(tmp_path)

        variables = [s for s in result.symbols if s.kind == "variable"]
        var_names = [s.name for s in variables]
        assert "http_port" in var_names or len(variables) >= 1

class TestAnsibleSymbolProperties:
    """Tests for symbol property correctness."""

    def test_symbol_has_correct_properties(self, tmp_path: Path) -> None:
        """Symbols have correct language and origin."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        playbook = tmp_path / "test.yml"
        playbook.write_text('''
- name: Test playbook
  hosts: all
  tasks:
    - name: Test task
      debug: msg="Hello"
''')

        result = analyze_ansible(tmp_path)

        for symbol in result.symbols:
            assert symbol.language == "ansible"
            assert symbol.origin == "yaml_ansible-v1"

class TestAnsibleEdgeProperties:
    """Tests for edge property correctness."""

    def test_edges_have_confidence(self, tmp_path: Path) -> None:
        """Edges have confidence values."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        playbook = tmp_path / "test.yml"
        playbook.write_text('''
- hosts: all
  tasks:
    - include_tasks: other.yml
''')

        result = analyze_ansible(tmp_path)

        for edge in result.edges:
            assert edge.confidence > 0
            assert edge.confidence <= 1.0

class TestAnsibleEmptyFile:
    """Tests for handling empty or minimal files."""

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        """Handles empty YAML files gracefully."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        playbook = tmp_path / "empty.yml"
        playbook.write_text("")

        result = analyze_ansible(tmp_path)

        assert result.run is not None

    def test_handles_comment_only_file(self, tmp_path: Path) -> None:
        """Handles files with only comments."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        playbook = tmp_path / "comments.yml"
        playbook.write_text("""# This is a comment
# Another comment
""")

        result = analyze_ansible(tmp_path)

        assert result.run is not None

class TestAnsibleParserFailure:
    """Tests for parser failure handling."""

    def test_handles_parser_load_failure(self, tmp_path: Path) -> None:
        """Handles failure to load YAML parser via _check_grammar_available."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        playbook = tmp_path / "test.yml"
        playbook.write_text("- hosts: all")

        # When grammar is not available, analyzer returns skipped
        with patch.object(yaml_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="yaml_ansible analysis skipped"):
                result = analyze_ansible(tmp_path)

        assert result.skipped is True
        assert "not available" in result.skip_reason
