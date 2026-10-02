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

class TestAnsibleContentDiscriminator:
    """WI-jifog: a YAML file is claimed as Ansible only on Ansible evidence.

    The path arms (root-level, or under an Ansible-named directory) only
    nominate candidates. A candidate is claimed when it is itself a playbook
    (a top-level list with a play keyword), or when it sits under an Ansible
    tree root evidenced by ``ansible.cfg``, a playbook, or a role entry
    point (``tasks/main.yml`` holding a task list).
    """

    @staticmethod
    def _rels(root: Path) -> set[str]:
        from hypergumbo_lang_mainstream.yaml_ansible import find_ansible_files

        return {p.relative_to(root).as_posix() for p in find_ansible_files(root)}

    def test_root_yamllint_config_not_claimed(self, tmp_path: Path) -> None:
        """The filed instance: a root-level yamllint config is not Ansible."""
        (tmp_path / ".yamllint.yaml").write_text(
            "extends: default\nrules:\n  line-length:\n    max: 120\n"
        )
        assert self._rels(tmp_path) == set()

    def test_root_list_of_mappings_without_play_key_not_claimed(
        self, tmp_path: Path
    ) -> None:
        """A root list of mappings (pre-commit hook manifest) is not a playbook."""
        (tmp_path / ".pre-commit-hooks.yaml").write_text(
            "- id: lint\n  name: lint\n  entry: lint\n  language: python\n"
        )
        assert self._rels(tmp_path) == set()

    def test_root_playbook_claimed(self, tmp_path: Path) -> None:
        (tmp_path / "site.yml").write_text(
            "---\n- name: Web\n  hosts: web\n  tasks: []\n"
        )
        (tmp_path / "imports.yml").write_text(
            "- import_playbook: site.yml\n"
            "- ansible.builtin.import_playbook: other.yml\n"
        )
        assert self._rels(tmp_path) == {"site.yml", "imports.yml"}

    def test_plain_config_in_vars_dir_not_claimed(self, tmp_path: Path) -> None:
        """The directory arm's measured misfire: web/vars/app.yaml."""
        (tmp_path / "web" / "vars").mkdir(parents=True)
        (tmp_path / "web" / "vars" / "app.yaml").write_text("port: 8080\nhost: x\n")
        (tmp_path / "src" / "tasks").mkdir(parents=True)
        (tmp_path / "src" / "tasks" / "jobs.yaml").write_text(
            "- name: nightly\n  cron: '0 0 * * *'\n"
        )
        assert self._rels(tmp_path) == set()

    def test_ansible_cfg_marks_vars_files(self, tmp_path: Path) -> None:
        (tmp_path / "ansible.cfg").write_text("[defaults]\n")
        (tmp_path / "group_vars").mkdir()
        (tmp_path / "group_vars" / "all.yml").write_text("ntp_server: x\n")
        (tmp_path / "host_vars").mkdir()
        (tmp_path / "host_vars" / "web1.yml").write_text("$ANSIBLE_VAULT;1.1;AES256\n0123\n")
        assert self._rels(tmp_path) == {"group_vars/all.yml", "host_vars/web1.yml"}

    def test_role_entry_point_marks_role_tree(self, tmp_path: Path) -> None:
        role = tmp_path / "roles" / "web"
        for sub in ("tasks", "defaults", "vars", "handlers"):
            (role / sub).mkdir(parents=True)
        (role / "tasks" / "main.yml").write_text("- name: install\n  package: nginx\n")
        (role / "defaults" / "main.yml").write_text("web_port: 80\n")
        (role / "vars" / "main.yml").write_text("---\n")
        (role / "handlers" / "main.yml").write_text("- name: restart\n  service: x\n")
        # A second role without its own entry point is in the same tree.
        (tmp_path / "roles" / "db" / "defaults").mkdir(parents=True)
        (tmp_path / "roles" / "db" / "defaults" / "main.yml").write_text("db_port: 5432\n")
        assert self._rels(tmp_path) == {
            "roles/web/tasks/main.yml",
            "roles/web/defaults/main.yml",
            "roles/web/vars/main.yml",
            "roles/web/handlers/main.yml",
            "roles/db/defaults/main.yml",
        }

    def test_tasks_main_that_is_not_a_task_list_is_not_evidence(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "tasks").mkdir()
        (tmp_path / "tasks" / "main.yaml").write_text("schedule: daily\n")
        (tmp_path / "vars").mkdir()
        (tmp_path / "vars" / "app.yaml").write_text("port: 1\n")
        assert self._rels(tmp_path) == set()

    def test_playbook_marks_its_tree(self, tmp_path: Path) -> None:
        """A playbook under playbooks/ roots the tree at the playbooks/ parent."""
        (tmp_path / "playbooks").mkdir()
        (tmp_path / "playbooks" / "site.yml").write_text("- hosts: all\n")
        (tmp_path / "group_vars").mkdir()
        (tmp_path / "group_vars" / "web.yml").write_text("x: 1\n")
        assert self._rels(tmp_path) == {"playbooks/site.yml", "group_vars/web.yml"}

    def test_playbook_beside_ansible_dirs_marks_its_tree(self, tmp_path: Path) -> None:
        """containerd's contrib/ansible layout: the play sits beside tasks/ and vars/.

        The playbook is neither at the root nor under an Ansible-named
        directory; it is found because it sits directly in a directory with
        an Ansible-named subdirectory, and it roots that tree.
        """
        base = tmp_path / "contrib" / "ansible"
        (base / "tasks").mkdir(parents=True)
        (base / "vars").mkdir()
        (base / "cri-containerd.yaml").write_text(
            "---\n- hosts: all\n  become: true\n  tasks:\n"
            "    - include_tasks: tasks/k8s.yaml\n"
        )
        (base / "tasks" / "k8s.yaml").write_text("- name: key\n  apt_key: {}\n")
        (base / "vars" / "vars.yaml").write_text("version: 1\n")
        # Control: a non-playbook beside an Ansible-named directory is not
        # claimed and does not root a tree.
        other = tmp_path / "contrib" / "other"
        (other / "tasks").mkdir(parents=True)
        (other / "config.yaml").write_text("- id: x\n  run: y\n")
        (other / "tasks" / "jobs.yaml").write_text("- name: j\n  cron: z\n")
        assert self._rels(tmp_path) == {
            "contrib/ansible/cri-containerd.yaml",
            "contrib/ansible/tasks/k8s.yaml",
            "contrib/ansible/vars/vars.yaml",
        }

    def test_evidence_is_scoped_to_its_tree(self, tmp_path: Path) -> None:
        """Ansible under deploy/ does not make app/vars/*.yaml Ansible."""
        (tmp_path / "deploy" / "group_vars").mkdir(parents=True)
        (tmp_path / "deploy" / "ansible.cfg").write_text("[defaults]\n")
        (tmp_path / "deploy" / "group_vars" / "all.yml").write_text("a: 1\n")
        (tmp_path / "app" / "vars").mkdir(parents=True)
        (tmp_path / "app" / "vars" / "config.yaml").write_text("b: 2\n")
        assert self._rels(tmp_path) == {"deploy/group_vars/all.yml"}

    def test_ansible_named_ancestor_of_repo_root_is_ignored(
        self, tmp_path: Path
    ) -> None:
        """Only repo-relative directories count: a checkout under .../vars/."""
        root = tmp_path / "vars" / "repo"
        (root / "conf").mkdir(parents=True)
        (root / "conf" / "app.yaml").write_text("k: v\n")
        assert self._rels(root) == set()

    def test_unparseable_root_yaml_not_claimed(self, tmp_path: Path) -> None:
        (tmp_path / "broken.yml").write_text("- hosts: [unterminated\n")
        (tmp_path / "latin.yml").write_bytes(b"\xff\xfe\x00- hosts: all\n")
        assert self._rels(tmp_path) == set()

    def test_oversized_root_yaml_not_parsed(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.yaml_ansible import _MAX_SHAPE_BYTES

        big = "- hosts: all\n" + "#" * (_MAX_SHAPE_BYTES + 1) + "\n"
        (tmp_path / "huge.yml").write_text(big)
        assert self._rels(tmp_path) == set()

    def test_behavior_map_has_no_ansible_language_for_yamllint_only_repo(
        self, tmp_path: Path
    ) -> None:
        """Live repro of the filed defect through the production entry point."""
        import json

        from hypergumbo_core.cli import run_behavior_map

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".yamllint.yaml").write_text(
            "extends: default\nrules:\n  line-length:\n    max: 120\n"
        )
        (repo / "main.py").write_text("def f():\n    return 1\n")
        out = tmp_path / "map.json"
        run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)

        data = json.loads(out.read_text())
        assert "yaml" in data["metrics"]["languages"]
        assert "ansible" not in data["metrics"]["languages"]
        langs = {n["path"].rsplit("/", 1)[-1]: n["language"] for n in data["nodes"]
                 if n["kind"] == "file" and n["path"].endswith(".yamllint.yaml")}
        assert langs == {".yamllint.yaml": "yaml"}


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

    def test_jinja_include_fans_out_to_role_tasks_dir(self, tmp_path: Path) -> None:
        """Jinja-templated include_tasks fans out to matching files in same role tasks/.

        Pattern from real-world ansible playbooks:
            - include_tasks: "{{ ansible_os_family }}.yml"
        in roles/foo/tasks/main.yml should produce edges to every sibling
        .yml file (Debian.yml, RedHat.yml), each at reduced confidence (0.30).
        """
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        tasks = tmp_path / "roles" / "foo" / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "main.yml").write_text(
            '- include_tasks: "{{ ansible_os_family }}.yml"\n'
        )
        (tasks / "Debian.yml").write_text("- name: Debian\n  debug: msg=d\n")
        (tasks / "RedHat.yml").write_text("- name: RedHat\n  debug: msg=r\n")

        result = analyze_ansible(tmp_path)
        node_ids = {s.id for s in result.symbols}
        fanout_edges = [
            e for e in result.edges
            if e.edge_type == "imports" and e.evidence_type == "include_tasks"
            and "roles/foo/tasks/main.yml" in e.src
        ]
        # Two candidates (Debian.yml, RedHat.yml); main.yml itself excluded.
        assert len(fanout_edges) == 2, [e.dst for e in fanout_edges]
        for edge in fanout_edges:
            assert edge.dst in node_ids
            assert abs(edge.confidence - 0.30) < 1e-6
        resolved_basenames = {e.dst.split(":")[1].rsplit("/", 1)[-1] for e in fanout_edges}
        assert resolved_basenames == {"Debian.yml", "RedHat.yml"}

    def test_jinja_include_with_literal_suffix_matches(self, tmp_path: Path) -> None:
        """Jinja pattern with literal suffix matches only files ending in that suffix."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        tasks = tmp_path / "roles" / "bar" / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "main.yml").write_text(
            '- include_tasks: "configure_{{ stage }}.yml"\n'
        )
        (tasks / "configure_dev.yml").write_text("- debug: msg=dev\n")
        (tasks / "configure_prod.yml").write_text("- debug: msg=prod\n")
        (tasks / "unrelated.yml").write_text("- debug: msg=other\n")
        # Distractor in a *different* role's tasks/ that matches the regex
        # but must not be picked up (fan-out is scoped to same directory).
        other = tmp_path / "roles" / "baz" / "tasks"
        other.mkdir(parents=True)
        (other / "configure_other.yml").write_text("- debug: msg=baz\n")

        result = analyze_ansible(tmp_path)
        fanout_edges = [
            e for e in result.edges
            if e.edge_type == "imports" and e.evidence_type == "include_tasks"
            and "roles/bar/tasks/main.yml" in e.src
        ]
        # Only the two configure_*.yml files; unrelated.yml must not match.
        resolved_basenames = {e.dst.split(":")[1].rsplit("/", 1)[-1] for e in fanout_edges}
        assert resolved_basenames == {"configure_dev.yml", "configure_prod.yml"}, resolved_basenames
        for edge in fanout_edges:
            assert abs(edge.confidence - 0.30) < 1e-6

    def test_jinja_path_prefix_fans_out_by_basename(self, tmp_path: Path) -> None:
        """Path-prefix Jinja with literal basename fans out across the repo.

        Pattern from real playbooks:
            - import_tasks: "{{ tasks_path }}/yumrepos.yml"
        The basename is literal but the directory is templated. Fan out to
        every yumrepos.yml in the repo. With one match, produce one edge
        at fan-out confidence (best-guess given the templated prefix).
        """
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        playbook_dir = tmp_path / "playbooks" / "groups"
        playbook_dir.mkdir(parents=True)
        # A real play (hosts:): it is what marks this tree as Ansible
        # (WI-jifog); a bare task list under playbooks/ is not evidence.
        (playbook_dir / "backup-server.yml").write_text(
            '- hosts: backup\n'
            '  tasks:\n'
            '    - import_tasks: "{{ tasks_path }}/yumrepos.yml"\n'
        )
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "yumrepos.yml").write_text("- debug: msg=yum\n")

        result = analyze_ansible(tmp_path)
        node_ids = {s.id for s in result.symbols}
        edges = [
            e for e in result.edges
            if e.edge_type == "imports" and e.evidence_type == "import_tasks"
            and "backup-server.yml" in e.src
        ]
        assert len(edges) == 1
        assert edges[0].dst in node_ids
        assert "tasks/yumrepos.yml" in edges[0].dst
        assert abs(edges[0].confidence - 0.30) < 1e-6

    def test_jinja_path_prefix_fans_out_to_multiple_basename_matches(
        self, tmp_path: Path
    ) -> None:
        """Path-prefix Jinja with multiple basename matches fans out to all."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        playbook_dir = tmp_path / "playbooks"
        playbook_dir.mkdir()
        (playbook_dir / "site.yml").write_text(
            '- hosts: all\n'
            '  tasks:\n'
            '    - import_tasks: "{{ tasks_path }}/setup.yml"\n'
        )
        # Two setup.yml in different directories.
        a = tmp_path / "roles" / "a" / "tasks"
        a.mkdir(parents=True)
        b = tmp_path / "roles" / "b" / "tasks"
        b.mkdir(parents=True)
        (a / "setup.yml").write_text("- debug: msg=a\n")
        (b / "setup.yml").write_text("- debug: msg=b\n")

        result = analyze_ansible(tmp_path)
        edges = [
            e for e in result.edges
            if e.edge_type == "imports" and e.evidence_type == "import_tasks"
            and "playbooks/site.yml" in e.src
        ]
        # Two basename matches → two fan-out edges, each at 0.30.
        assert len(edges) == 2
        for edge in edges:
            assert abs(edge.confidence - 0.30) < 1e-6
        dst_paths = {e.dst.split(":")[1] for e in edges}
        assert any("roles/a/tasks/setup.yml" in p for p in dst_paths)
        assert any("roles/b/tasks/setup.yml" in p for p in dst_paths)

    def test_jinja_include_no_candidates_falls_back_to_unresolvable(self, tmp_path: Path) -> None:
        """Jinja pattern with no matching siblings keeps the unresolved edge at 0.50."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        tasks = tmp_path / "roles" / "lonely" / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "main.yml").write_text(
            '- include_tasks: "{{ missing_var }}.yml"\n'
        )
        # No sibling .yml files exist.

        result = analyze_ansible(tmp_path)
        import_edges = [
            e for e in result.edges
            if e.edge_type == "imports" and e.evidence_type == "include_tasks"
            and "roles/lonely/tasks/main.yml" in e.src
        ]
        assert len(import_edges) == 1
        assert abs(import_edges[0].confidence - 0.50) < 1e-6

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
            assert symbol.origin == ["yaml_ansible"]

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

        # Under an evidenced tree, so the empty file is claimed and reaches
        # extraction (an empty root-level file is not a playbook: WI-jifog).
        (tmp_path / "ansible.cfg").write_text("[defaults]\n")
        (tmp_path / "vars").mkdir()
        playbook = tmp_path / "vars" / "empty.yml"
        playbook.write_text("")

        result = analyze_ansible(tmp_path)

        assert result.run is not None
        assert [s.name for s in result.symbols if s.kind == "file"] == ["empty.yml"]

    def test_handles_comment_only_file(self, tmp_path: Path) -> None:
        """Handles files with only comments."""
        from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

        (tmp_path / "ansible.cfg").write_text("[defaults]\n")
        (tmp_path / "vars").mkdir()
        playbook = tmp_path / "vars" / "comments.yml"
        playbook.write_text("""# This is a comment
# Another comment
""")

        result = analyze_ansible(tmp_path)

        assert result.run is not None
        assert [s.name for s in result.symbols if s.kind == "file"] == ["comments.yml"]

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


def test_root_list_with_a_non_mapping_item_is_not_a_playbook(tmp_path: Path) -> None:
    """A top-level YAML list whose items are not ALL mappings is no play or task
    list (WI-jifog): ``- hosts: all`` beside a bare scalar item is refused, so a
    root-level file of that shape is not claimed as Ansible. Covers the
    not-every-item-is-a-mapping refusal in ``_top_level_shape``."""
    from hypergumbo_lang_mainstream.yaml_ansible import (
        _NO_SHAPE,
        _top_level_shape,
        find_ansible_files,
    )

    mixed = tmp_path / "site.yml"
    mixed.write_text("- hosts: all\n- just-a-string\n")
    assert _top_level_shape(mixed) == _NO_SHAPE
    assert find_ansible_files(tmp_path) == []
