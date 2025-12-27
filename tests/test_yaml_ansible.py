"""Tests for YAML/Ansible analyzer."""
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestYAMLHelpers:
    """Tests for YAML analyzer helper functions."""

    def test_find_child_by_type_returns_none(self) -> None:
        """Returns None when no matching child type is found."""
        from hypergumbo.analyze.yaml_ansible import _find_child_by_type

        mock_node = MagicMock()
        mock_child = MagicMock()
        mock_child.type = "different_type"
        mock_node.children = [mock_child]

        result = _find_child_by_type(mock_node, "block_mapping")
        assert result is None


class TestFindAnsibleFiles:
    """Tests for Ansible file discovery."""

    def test_finds_ansible_playbooks(self, tmp_path: Path) -> None:
        """Finds Ansible playbook files."""
        from hypergumbo.analyze.yaml_ansible import find_ansible_files

        (tmp_path / "playbook.yml").write_text("- hosts: all")
        (tmp_path / "site.yml").write_text("- hosts: webservers")
        (tmp_path / "other.txt").write_text("not ansible")

        files = list(find_ansible_files(tmp_path))

        assert len(files) == 2
        assert all(f.suffix in (".yml", ".yaml") for f in files)

    def test_finds_ansible_roles_tasks(self, tmp_path: Path) -> None:
        """Finds Ansible role task files."""
        from hypergumbo.analyze.yaml_ansible import find_ansible_files

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
        from hypergumbo.analyze.yaml_ansible import is_yaml_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()
            assert is_yaml_tree_sitter_available() is True

    def test_is_yaml_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo.analyze.yaml_ansible import is_yaml_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_yaml_tree_sitter_available() is False


class TestAnalyzeYAMLFallback:
    """Tests for fallback behavior when tree-sitter-yaml unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-yaml unavailable."""
        from hypergumbo.analyze.yaml_ansible import analyze_ansible

        (tmp_path / "playbook.yml").write_text("- hosts: all")

        with patch("hypergumbo.analyze.yaml_ansible.is_yaml_tree_sitter_available", return_value=False):
            result = analyze_ansible(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-yaml" in result.skip_reason


class TestAnsiblePlaybookExtraction:
    """Tests for extracting Ansible playbooks."""

    def test_extracts_playbook_with_name(self, tmp_path: Path) -> None:
        """Extracts named playbooks."""
        from hypergumbo.analyze.yaml_ansible import analyze_ansible

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
        from hypergumbo.analyze.yaml_ansible import analyze_ansible

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
        from hypergumbo.analyze.yaml_ansible import analyze_ansible

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
        from hypergumbo.analyze.yaml_ansible import analyze_ansible

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


class TestAnsibleVariableExtraction:
    """Tests for extracting Ansible variables."""

    def test_extracts_vars_section(self, tmp_path: Path) -> None:
        """Extracts variables from vars section."""
        from hypergumbo.analyze.yaml_ansible import analyze_ansible

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
        from hypergumbo.analyze.yaml_ansible import analyze_ansible

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
            assert symbol.origin == "ansible-v1"


class TestAnsibleEdgeProperties:
    """Tests for edge property correctness."""

    def test_edges_have_confidence(self, tmp_path: Path) -> None:
        """Edges have confidence values."""
        from hypergumbo.analyze.yaml_ansible import analyze_ansible

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
        from hypergumbo.analyze.yaml_ansible import analyze_ansible

        playbook = tmp_path / "empty.yml"
        playbook.write_text("")

        result = analyze_ansible(tmp_path)


        assert result.run is not None

    def test_handles_comment_only_file(self, tmp_path: Path) -> None:
        """Handles files with only comments."""
        from hypergumbo.analyze.yaml_ansible import analyze_ansible

        playbook = tmp_path / "comments.yml"
        playbook.write_text("""# This is a comment
# Another comment
""")

        result = analyze_ansible(tmp_path)


        assert result.run is not None


class TestAnsibleParserFailure:
    """Tests for parser failure handling."""

    def test_handles_parser_load_failure(self, tmp_path: Path) -> None:
        """Handles failure to load YAML parser."""
        from hypergumbo.analyze.yaml_ansible import analyze_ansible

        playbook = tmp_path / "test.yml"
        playbook.write_text("- hosts: all")

        with patch("hypergumbo.analyze.yaml_ansible.is_yaml_tree_sitter_available", return_value=True):
            with patch("tree_sitter_yaml.language", side_effect=Exception("Parser error")):
                result = analyze_ansible(tmp_path)

        assert result.skipped is True
        assert "Parser error" in result.skip_reason or "Failed to load" in result.skip_reason
