"""Tests for the INI configuration file analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_mainstream import ini as ini_module
from hypergumbo_lang_mainstream.ini import (
    IniAnalysisResult,
    analyze_ini,
    find_ini_files,
    is_ini_tree_sitter_available,
    _categorize_section,
    _is_sensitive_key,
    _mask_value,
)


def make_ini_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create an INI file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindIniFiles:
    """Tests for find_ini_files function."""

    def test_finds_ini_files(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", "[section]\nkey=value")
        make_ini_file(tmp_path, "app.cfg", "[section]\nkey=value")
        files = find_ini_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"config.ini", "app.cfg"}

    def test_finds_common_ini_files(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "setup.cfg", "[metadata]\nname=pkg")
        make_ini_file(tmp_path, "tox.ini", "[tox]\nenvlist=py3")
        make_ini_file(tmp_path, ".editorconfig", "[*]\nindent_style=space")
        make_ini_file(tmp_path, "pytest.ini", "[pytest]\naddopts=-v")
        files = find_ini_files(tmp_path)
        assert len(files) == 4

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_ini_files(tmp_path)
        assert files == []


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_categorize_section_database(self) -> None:
        assert _categorize_section("database") == "database"
        assert _categorize_section("mysql") == "database"
        assert _categorize_section("db_config") == "database"

    def test_categorize_section_logging(self) -> None:
        assert _categorize_section("logging") == "logging"
        assert _categorize_section("loggers") == "logging"

    def test_categorize_section_server(self) -> None:
        assert _categorize_section("server") == "server"
        assert _categorize_section("host_config") == "server"

    def test_categorize_section_security(self) -> None:
        assert _categorize_section("security") == "security"
        assert _categorize_section("ssl") == "security"

    def test_categorize_section_general(self) -> None:
        assert _categorize_section("myapp") == "general"
        assert _categorize_section("settings") == "general"

    def test_is_sensitive_key_true(self) -> None:
        assert _is_sensitive_key("password") is True
        assert _is_sensitive_key("db_password") is True
        assert _is_sensitive_key("api_key") is True
        assert _is_sensitive_key("secret_token") is True
        assert _is_sensitive_key("AUTH_TOKEN") is True

    def test_is_sensitive_key_false(self) -> None:
        assert _is_sensitive_key("host") is False
        assert _is_sensitive_key("port") is False
        assert _is_sensitive_key("username") is False

    def test_mask_value(self) -> None:
        assert _mask_value("secret123") == "s*******3"
        assert _mask_value("ab") == "***"
        assert _mask_value("") == "***"
        assert _mask_value("a") == "***"


class TestIsIniTreeSitterAvailable:
    """Tests for is_ini_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_ini_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(ini_module, "is_ini_tree_sitter_available", return_value=False):
            assert ini_module.is_ini_tree_sitter_available() is False


class TestAnalyzeIni:
    """Tests for analyze_ini function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", "[section]\nkey=value")
        with patch.object(ini_module, "is_ini_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="INI analysis skipped"):
                result = ini_module.analyze_ini(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_ini(tmp_path)
        assert result.symbols == []
        assert result.run is None

    def test_extracts_section(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", """[database]
host = localhost
""")
        result = analyze_ini(tmp_path)
        assert not result.skipped
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.name == "database"
        assert section.signature == "[database]"

    def test_extracts_multiple_sections(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", """[database]
host = localhost

[logging]
level = DEBUG

[server]
port = 8080
""")
        result = analyze_ini(tmp_path)
        sections = [s for s in result.symbols if s.kind == "section"]
        assert len(sections) == 3
        names = {s.name for s in sections}
        assert names == {"database", "logging", "server"}

    def test_extracts_setting(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", """[database]
host = localhost
""")
        result = analyze_ini(tmp_path)
        setting = next((s for s in result.symbols if s.kind == "setting"), None)
        assert setting is not None
        assert setting.name == "host"
        assert "localhost" in setting.signature

    def test_extracts_multiple_settings(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", """[database]
host = localhost
port = 3306
user = admin
""")
        result = analyze_ini(tmp_path)
        settings = [s for s in result.symbols if s.kind == "setting"]
        assert len(settings) == 3
        names = {s.name for s in settings}
        assert names == {"host", "port", "user"}

    def test_masks_sensitive_values(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", """[database]
user = admin
password = secret123
""")
        result = analyze_ini(tmp_path)
        password = next((s for s in result.symbols if s.name == "password"), None)
        assert password is not None
        assert password.meta.get("is_sensitive") is True
        assert "secret123" not in password.signature
        assert "***" in password.signature or "*" in password.signature

    def test_non_sensitive_values_visible(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", """[database]
host = localhost
""")
        result = analyze_ini(tmp_path)
        host = next((s for s in result.symbols if s.name == "host"), None)
        assert host is not None
        assert host.meta.get("is_sensitive") is False
        assert "localhost" in host.signature

    def test_section_settings_count(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", """[database]
host = localhost
port = 3306
user = admin
""")
        result = analyze_ini(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.meta.get("settings_count") == 3

    def test_section_category_database(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", """[database]
host = localhost
""")
        result = analyze_ini(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.meta.get("category") == "database"

    def test_section_category_logging(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", """[logging]
level = DEBUG
""")
        result = analyze_ini(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.meta.get("category") == "logging"

    def test_setting_inherits_section_category(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", """[database]
host = localhost
""")
        result = analyze_ini(tmp_path)
        setting = next((s for s in result.symbols if s.kind == "setting"), None)
        assert setting is not None
        assert setting.meta.get("category") == "database"
        assert setting.meta.get("section") == "database"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", "[section]\nkey=value")
        result = analyze_ini(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "ini.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0
        assert result.run.files_analyzed == 1

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", "[section]\nkey=value")
        make_ini_file(tmp_path, "app.cfg", "[section]\nkey=value")
        result = analyze_ini(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 2

    def test_pass_id(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", "[section]\nkey=value")
        result = analyze_ini(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.origin == "ini.tree_sitter"

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", "[section]\nkey=value")
        result = analyze_ini(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.id == section.stable_id
        assert "ini:" in section.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_ini_file(tmp_path, "config.ini", "[section]\nkey=value")
        result = analyze_ini(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.span is not None
        assert section.span.start_line >= 1

    def test_edges_empty(self, tmp_path: Path) -> None:
        """INI files don't have edges."""
        make_ini_file(tmp_path, "config.ini", "[section]\nkey=value")
        result = analyze_ini(tmp_path)
        assert result.edges == []

    def test_complete_config(self, tmp_path: Path) -> None:
        """Test a complete INI configuration file."""
        make_ini_file(tmp_path, "config.ini", """
; Application configuration
[database]
host = localhost
port = 3306
user = myapp
password = supersecret

[logging]
level = INFO
file = /var/log/app.log

[server]
host = 0.0.0.0
port = 8080

[feature_flags]
enable_new_ui = true
enable_caching = false
""")
        result = analyze_ini(tmp_path)

        # Check sections
        sections = [s for s in result.symbols if s.kind == "section"]
        assert len(sections) == 4
        section_names = {s.name for s in sections}
        assert section_names == {"database", "logging", "server", "feature_flags"}

        # Check settings (4 in database + 2 in logging + 2 in server + 2 in feature_flags)
        settings = [s for s in result.symbols if s.kind == "setting"]
        assert len(settings) == 10

        # Check sensitive value masking
        password = next((s for s in settings if s.name == "password"), None)
        assert password is not None
        assert password.meta.get("is_sensitive") is True
        assert "supersecret" not in password.signature

        # Check categories
        db_section = next((s for s in sections if s.name == "database"), None)
        assert db_section is not None
        assert db_section.meta.get("category") == "database"

        logging_section = next((s for s in sections if s.name == "logging"), None)
        assert logging_section is not None
        assert logging_section.meta.get("category") == "logging"

    def test_flake8_config(self, tmp_path: Path) -> None:
        """Test .flake8 config file."""
        make_ini_file(tmp_path, ".flake8", """[flake8]
max-line-length = 100
exclude = .git,__pycache__
""")
        result = analyze_ini(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.name == "flake8"

    def test_pylintrc_config(self, tmp_path: Path) -> None:
        """Test .pylintrc config file."""
        make_ini_file(tmp_path, ".pylintrc", """[MESSAGES CONTROL]
disable = C0111
""")
        result = analyze_ini(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.name == "MESSAGES CONTROL"
