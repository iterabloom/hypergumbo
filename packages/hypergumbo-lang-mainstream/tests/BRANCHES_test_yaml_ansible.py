# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for yaml_ansible.py analyzer.

Tests specific branch paths in the YAML/Ansible analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Playbook extraction
- Task and handler extraction
- Variable extraction
- Include/import edges
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_symbol_id
from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible, find_ansible_files

def make_ansible_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an Ansible YAML file with given content."""
    (tmp_path / name).write_text(content)

class TestAnsibleHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("ansible", "playbook.yml", 1, 10, "Deploy App", "playbook")
        assert symbol_id == "ansible:playbook.yml:1-10:Deploy App:playbook"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = make_file_id("ansible", "playbook.yml")
        assert file_id == "ansible:playbook.yml:1-1:file:file"

class TestPlaybookExtraction:
    """Branch coverage for playbook extraction."""

    def test_simple_playbook(self, tmp_path: Path) -> None:
        """Test simple playbook extraction."""
        make_ansible_file(tmp_path, "playbook.yml", """
- name: Deploy Application
  hosts: webservers
  tasks:
    - name: Install packages
      apt:
        name: nginx
""")
        result = analyze_ansible(tmp_path)
        assert not result.skipped

        playbooks = [s for s in result.symbols if s.kind == "playbook"]
        assert len(playbooks) >= 1
        assert any(p.name == "Deploy Application" for p in playbooks)

    def test_multiple_plays(self, tmp_path: Path) -> None:
        """Test multiple plays in a playbook."""
        make_ansible_file(tmp_path, "playbook.yml", """
- name: Setup Database
  hosts: dbservers
  tasks:
    - name: Install PostgreSQL
      apt:
        name: postgresql

- name: Setup Web
  hosts: webservers
  tasks:
    - name: Install Nginx
      apt:
        name: nginx
""")
        result = analyze_ansible(tmp_path)
        playbooks = [s for s in result.symbols if s.kind == "playbook"]

        assert len(playbooks) >= 2

class TestTaskExtraction:
    """Branch coverage for task extraction."""

    def test_task_in_playbook(self, tmp_path: Path) -> None:
        """Test task extraction from playbook."""
        make_ansible_file(tmp_path, "playbook.yml", """
- name: My Playbook
  hosts: all
  tasks:
    - name: First Task
      debug:
        msg: Hello

    - name: Second Task
      debug:
        msg: World
""")
        result = analyze_ansible(tmp_path)
        tasks = [s for s in result.symbols if s.kind == "task"]

        assert len(tasks) >= 2
        assert any(t.name == "First Task" for t in tasks)
        assert any(t.name == "Second Task" for t in tasks)

class TestHandlerExtraction:
    """Branch coverage for handler extraction."""

    def test_handler_in_playbook(self, tmp_path: Path) -> None:
        """Test handler extraction from playbook."""
        make_ansible_file(tmp_path, "playbook.yml", """
- name: My Playbook
  hosts: all
  tasks:
    - name: Install Nginx
      apt:
        name: nginx
      notify: Restart Nginx

  handlers:
    - name: Restart Nginx
      service:
        name: nginx
        state: restarted
""")
        result = analyze_ansible(tmp_path)
        handlers = [s for s in result.symbols if s.kind == "handler"]

        assert len(handlers) >= 1
        assert any(h.name == "Restart Nginx" for h in handlers)

class TestVariableExtraction:
    """Branch coverage for variable extraction."""

    def test_vars_in_playbook(self, tmp_path: Path) -> None:
        """Test vars section extraction."""
        make_ansible_file(tmp_path, "playbook.yml", """
- name: My Playbook
  hosts: all
  vars:
    app_name: myapp
    app_port: 8080
  tasks:
    - name: Debug
      debug:
        msg: "{{ app_name }}"
""")
        result = analyze_ansible(tmp_path)
        variables = [s for s in result.symbols if s.kind == "variable"]

        assert len(variables) >= 2
        assert any(v.name == "app_name" for v in variables)
        assert any(v.name == "app_port" for v in variables)

class TestIncludeImportEdges:
    """Branch coverage for include/import edge extraction."""

    def test_include_tasks_edge(self, tmp_path: Path) -> None:
        """Test include_tasks creates edge."""
        make_ansible_file(tmp_path, "playbook.yml", """
- name: My Playbook
  hosts: all
  tasks:
    - name: Include common tasks
      include_tasks: common.yml
""")
        result = analyze_ansible(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]

        assert len(import_edges) >= 1

    def test_import_tasks_edge(self, tmp_path: Path) -> None:
        """Test import_tasks creates edge."""
        make_ansible_file(tmp_path, "playbook.yml", """
- name: My Playbook
  hosts: all
  tasks:
    - name: Import setup tasks
      import_tasks: setup.yml
""")
        result = analyze_ansible(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]

        assert len(import_edges) >= 1

    def test_include_role_edge(self, tmp_path: Path) -> None:
        """Test include_role creates edge."""
        make_ansible_file(tmp_path, "playbook.yml", """
- name: My Playbook
  hosts: all
  tasks:
    - name: Include common role
      include_role: common
""")
        result = analyze_ansible(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]

        assert len(import_edges) >= 1

    def test_import_role_edge(self, tmp_path: Path) -> None:
        """Test import_role creates edge."""
        make_ansible_file(tmp_path, "playbook.yml", """
- name: My Playbook
  hosts: all
  tasks:
    - name: Import base role
      import_role: base
""")
        result = analyze_ansible(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]

        assert len(import_edges) >= 1

class TestFindAnsibleFiles:
    """Branch coverage for file discovery."""

    def test_finds_yml_files_in_root(self, tmp_path: Path) -> None:
        """Test .yml files in root are discovered."""
        (tmp_path / "playbook.yml").write_text("- hosts: all")

        files = find_ansible_files(tmp_path)
        assert len(files) >= 1
        assert any(f.suffix == ".yml" for f in files)

    def test_finds_yaml_files_in_root(self, tmp_path: Path) -> None:
        """Test .yaml files in root are discovered."""
        (tmp_path / "playbook.yaml").write_text("- hosts: all")

        files = find_ansible_files(tmp_path)
        assert len(files) >= 1
        assert any(f.suffix == ".yaml" for f in files)

    def test_finds_files_in_tasks_dir(self, tmp_path: Path) -> None:
        """Test files in tasks/ directory are discovered."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "main.yml").write_text("- name: Task")

        files = find_ansible_files(tmp_path)
        assert len(files) >= 1

    def test_finds_files_in_handlers_dir(self, tmp_path: Path) -> None:
        """Test files in handlers/ directory are discovered."""
        handlers_dir = tmp_path / "handlers"
        handlers_dir.mkdir()
        (handlers_dir / "main.yml").write_text("- name: Handler")

        files = find_ansible_files(tmp_path)
        assert len(files) >= 1

    def test_finds_files_in_vars_dir(self, tmp_path: Path) -> None:
        """Test files in vars/ directory are discovered."""
        vars_dir = tmp_path / "vars"
        vars_dir.mkdir()
        (vars_dir / "main.yml").write_text("app_name: test")

        files = find_ansible_files(tmp_path)
        assert len(files) >= 1

class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_minimal_playbook(self, tmp_path: Path) -> None:
        """Test minimal playbook file."""
        make_ansible_file(tmp_path, "playbook.yml", """
- hosts: all
""")
        result = analyze_ansible(tmp_path)
        assert not result.skipped

class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_ansible_file(tmp_path, "playbook.yml", """
- name: Test Playbook
  hosts: all
  tasks:
    - name: Debug
      debug:
        msg: Hello
""")
        result = analyze_ansible(tmp_path)
        assert result.run is not None
