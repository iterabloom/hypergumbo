# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-babuj: the general YAML analyzer emits one file-anchor node per
generic (non-Ansible) YAML file.

Before this analyzer, only ``yaml_ansible`` ran — matching Ansible-shaped
YAML only — so generic YAML (CI workflows, framework catalogs, config) had
zero nodes in the behavior map. This analyzer emits a single ``kind="file"``
anchor per ``.yaml`` / ``.yml`` file, file-anchor-only (no per-key nodes),
and skips files the Ansible analyzer already claims so each physical file
gets exactly one anchor.
"""

from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id
from hypergumbo_core.catalog import all_known_languages
from hypergumbo_core.discovery import FileIndex, get_file_index, set_file_index
from hypergumbo_lang_mainstream.yaml import analyze_yaml, find_generic_yaml_files


def _file_nodes(result):
    return [s for s in result.symbols if s.kind == "file"]


class TestYamlFileAnchor:
    def test_generic_yaml_file_gets_one_anchor(self, tmp_path: Path) -> None:
        (tmp_path / "conf").mkdir()
        (tmp_path / "conf" / "app.yaml").write_text("key: value\n")
        result = analyze_yaml(tmp_path)
        files = _file_nodes(result)
        assert len(files) == 1
        node = files[0]
        assert node.language == "yaml"
        assert node.kind == "file"
        assert node.name == "app.yaml"
        # The analyzer emits str(path) (the orchestrator relativizes in
        # production, as it does for yaml_ansible); the id is derived from
        # whatever path it stamped.
        assert node.path.endswith("conf/app.yaml")
        assert node.id == make_file_id("yaml", node.path)
        assert node.id.startswith("yaml:") and node.id.endswith(":1-1:file:file")

    def test_yml_extension_also_anchored(self, tmp_path: Path) -> None:
        (tmp_path / "ci").mkdir()
        (tmp_path / "ci" / "build.yml").write_text("steps: []\n")
        files = _file_nodes(analyze_yaml(tmp_path))
        assert len(files) == 1
        assert files[0].name == "build.yml"

    def test_file_anchor_only_no_per_key_nodes(self, tmp_path: Path) -> None:
        (tmp_path / "conf").mkdir()
        (tmp_path / "conf" / "app.yaml").write_text(
            "database:\n  host: localhost\n  port: 5432\nfeatures:\n  - a\n  - b\n"
        )
        result = analyze_yaml(tmp_path)
        # Every emitted symbol is the file anchor — no key/section/document nodes.
        assert {s.kind for s in result.symbols} == {"file"}
        assert len(result.symbols) == 1

    def test_ansible_shaped_yaml_excluded(self, tmp_path: Path) -> None:
        # Files in an Ansible directory are claimed by yaml_ansible; this
        # analyzer must NOT emit a second (language="yaml") anchor for them.
        (tmp_path / "roles" / "web" / "tasks").mkdir(parents=True)
        (tmp_path / "roles" / "web" / "tasks" / "main.yml").write_text(
            "- name: install\n  package: nginx\n"
        )
        # A generic one alongside, to prove the analyzer still runs.
        (tmp_path / "conf").mkdir()
        (tmp_path / "conf" / "app.yaml").write_text("k: v\n")
        files = _file_nodes(analyze_yaml(tmp_path))
        paths = {f.path for f in files}
        assert any(p.endswith("conf/app.yaml") for p in paths)
        assert not any(p.endswith("roles/web/tasks/main.yml") for p in paths)
        assert len(files) == 1

    def test_root_yaml_excluded_as_ansible_claimed(self, tmp_path: Path) -> None:
        # find_ansible_files claims any root-level .yaml/.yml, so this
        # analyzer skips it (yaml_ansible owns it). Regression guard on the
        # subtraction.
        (tmp_path / "top.yaml").write_text("k: v\n")
        assert _file_nodes(analyze_yaml(tmp_path)) == []

    def test_non_yaml_files_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "conf").mkdir()
        (tmp_path / "conf" / "app.json").write_text("{}\n")
        (tmp_path / "conf" / "notes.md").write_text("# hi\n")
        assert _file_nodes(analyze_yaml(tmp_path)) == []

    def test_uses_file_index_when_available(self, tmp_path: Path) -> None:
        # Covers the FileIndex fast-path branch of find_generic_yaml_files.
        (tmp_path / "conf").mkdir()
        (tmp_path / "conf" / "app.yaml").write_text("k: v\n")
        set_file_index(FileIndex.build(tmp_path))
        try:
            assert get_file_index() is not None
            files = find_generic_yaml_files(tmp_path)
            assert any(p.name == "app.yaml" for p in files)
        finally:
            set_file_index(None)

    def test_yaml_is_a_known_language(self) -> None:
        # Registering @register_analyzer("yaml", languages=["yaml"])
        # auto-adds "yaml" to the catalog (derived from analyzer languages).
        assert "yaml" in all_known_languages()
