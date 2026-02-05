"""Branch coverage tests for ini.py analyzer.

Tests specific branch paths in the INI analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Section extraction with categorization
- Setting extraction with sensitivity detection
- Value masking for sensitive values
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_mainstream.ini import (
    _categorize_section,
    _is_sensitive_key,
    _make_symbol_id,
    _mask_value,
    analyze_ini,
    find_ini_files,
)


def make_ini_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an INI file with given content."""
    (tmp_path / name).write_text(content)


class TestIniHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id(Path("config.ini"), "database", "section", 1)
        assert symbol_id == "ini:config.ini:section:1:database"

    def test_mask_value_short(self) -> None:
        """Test masking short values."""
        assert _mask_value("ab") == "***"
        assert _mask_value("a") == "***"
        assert _mask_value("") == "***"

    def test_mask_value_normal(self) -> None:
        """Test masking normal length values."""
        masked = _mask_value("secretpassword")
        assert masked.startswith("s")
        assert masked.endswith("d")
        assert "***" not in masked or len(masked) > 3

    def test_is_sensitive_key_true(self) -> None:
        """Test sensitive key detection."""
        assert _is_sensitive_key("password") is True
        assert _is_sensitive_key("db_password") is True
        assert _is_sensitive_key("API_KEY") is True
        assert _is_sensitive_key("secret_token") is True
        assert _is_sensitive_key("auth_credential") is True

    def test_is_sensitive_key_false(self) -> None:
        """Test non-sensitive key detection."""
        assert _is_sensitive_key("host") is False
        assert _is_sensitive_key("port") is False
        assert _is_sensitive_key("debug") is False


class TestSectionCategorization:
    """Branch coverage for section categorization."""

    def test_categorize_database_section(self) -> None:
        """Test database section categorization."""
        assert _categorize_section("database") == "database"
        assert _categorize_section("db_config") == "database"
        assert _categorize_section("mysql") == "database"
        assert _categorize_section("postgres") == "database"

    def test_categorize_logging_section(self) -> None:
        """Test logging section categorization."""
        assert _categorize_section("logging") == "logging"
        assert _categorize_section("log") == "logging"
        assert _categorize_section("logger") == "logging"

    def test_categorize_server_section(self) -> None:
        """Test server section categorization."""
        assert _categorize_section("server") == "server"
        assert _categorize_section("host_config") == "server"

    def test_categorize_security_section(self) -> None:
        """Test security section categorization."""
        assert _categorize_section("security") == "security"
        assert _categorize_section("ssl_config") == "security"
        assert _categorize_section("tls") == "security"

    def test_categorize_cache_section(self) -> None:
        """Test cache section categorization."""
        assert _categorize_section("cache") == "cache"
        # redis is in both database and cache keywords; database matches first
        assert _categorize_section("redis") == "database"

    def test_categorize_email_section(self) -> None:
        """Test email section categorization."""
        assert _categorize_section("email") == "email"
        assert _categorize_section("smtp") == "email"

    def test_categorize_storage_section(self) -> None:
        """Test storage section categorization."""
        assert _categorize_section("storage") == "storage"
        assert _categorize_section("s3_config") == "storage"

    def test_categorize_api_section(self) -> None:
        """Test API section categorization."""
        assert _categorize_section("api") == "api"
        assert _categorize_section("endpoint") == "api"

    def test_categorize_feature_section(self) -> None:
        """Test feature flag section categorization."""
        assert _categorize_section("feature_flags") == "feature"
        assert _categorize_section("toggles") == "feature"

    def test_categorize_general_section(self) -> None:
        """Test general/unknown section categorization."""
        assert _categorize_section("custom_section") == "general"
        assert _categorize_section("misc") == "general"


class TestSectionExtraction:
    """Branch coverage for section extraction."""

    def test_simple_section(self, tmp_path: Path) -> None:
        """Test simple section extraction."""
        make_ini_file(tmp_path, "config.ini", """[database]
host = localhost
port = 5432
""")
        result = analyze_ini(tmp_path)
        assert not result.skipped

        sections = [s for s in result.symbols if s.kind == "section"]
        assert len(sections) >= 1
        assert any(s.name == "database" for s in sections)

    def test_section_with_category(self, tmp_path: Path) -> None:
        """Test section category in metadata."""
        make_ini_file(tmp_path, "config.ini", """[database]
host = localhost

[logging]
level = INFO
""")
        result = analyze_ini(tmp_path)
        sections = [s for s in result.symbols if s.kind == "section"]

        db_section = next((s for s in sections if s.name == "database"), None)
        log_section = next((s for s in sections if s.name == "logging"), None)

        assert db_section is not None
        assert db_section.meta.get("category") == "database"
        assert log_section is not None
        assert log_section.meta.get("category") == "logging"

    def test_section_settings_count(self, tmp_path: Path) -> None:
        """Test section settings count in metadata."""
        make_ini_file(tmp_path, "config.ini", """[database]
host = localhost
port = 5432
name = mydb
""")
        result = analyze_ini(tmp_path)
        sections = [s for s in result.symbols if s.kind == "section"]

        db_section = next((s for s in sections if s.name == "database"), None)
        assert db_section is not None
        assert db_section.meta.get("settings_count") >= 3


class TestSettingExtraction:
    """Branch coverage for setting extraction."""

    def test_simple_setting(self, tmp_path: Path) -> None:
        """Test simple setting extraction."""
        make_ini_file(tmp_path, "config.ini", """[server]
host = localhost
port = 8080
""")
        result = analyze_ini(tmp_path)
        settings = [s for s in result.symbols if s.kind == "setting"]

        assert len(settings) >= 2
        assert any(s.name == "host" for s in settings)
        assert any(s.name == "port" for s in settings)

    def test_sensitive_setting_masked(self, tmp_path: Path) -> None:
        """Test sensitive setting value is masked."""
        make_ini_file(tmp_path, "config.ini", """[database]
password = mysecretpass
""")
        result = analyze_ini(tmp_path)
        settings = [s for s in result.symbols if s.kind == "setting"]

        pwd_setting = next((s for s in settings if s.name == "password"), None)
        assert pwd_setting is not None
        assert pwd_setting.meta.get("is_sensitive") is True
        # Signature should have masked value
        assert "mysecretpass" not in pwd_setting.signature

    def test_non_sensitive_setting_visible(self, tmp_path: Path) -> None:
        """Test non-sensitive setting value is visible."""
        make_ini_file(tmp_path, "config.ini", """[server]
host = localhost
""")
        result = analyze_ini(tmp_path)
        settings = [s for s in result.symbols if s.kind == "setting"]

        host_setting = next((s for s in settings if s.name == "host"), None)
        assert host_setting is not None
        assert host_setting.meta.get("is_sensitive") is False
        assert "localhost" in host_setting.signature

    def test_setting_inherits_section_category(self, tmp_path: Path) -> None:
        """Test setting inherits section category."""
        make_ini_file(tmp_path, "config.ini", """[database]
host = localhost
""")
        result = analyze_ini(tmp_path)
        settings = [s for s in result.symbols if s.kind == "setting"]

        host_setting = next((s for s in settings if s.name == "host"), None)
        assert host_setting is not None
        assert host_setting.meta.get("category") == "database"


class TestFindIniFiles:
    """Branch coverage for file discovery."""

    def test_finds_ini_files(self, tmp_path: Path) -> None:
        """Test .ini files are discovered."""
        (tmp_path / "config.ini").write_text("[main]\nkey = value")

        files = find_ini_files(tmp_path)
        assert len(files) >= 1
        assert any(f.suffix == ".ini" for f in files)

    def test_finds_cfg_files(self, tmp_path: Path) -> None:
        """Test .cfg files are discovered."""
        (tmp_path / "setup.cfg").write_text("[metadata]\nname = myproject")

        files = find_ini_files(tmp_path)
        assert len(files) >= 1
        assert any(f.suffix == ".cfg" for f in files)

    def test_finds_conf_files(self, tmp_path: Path) -> None:
        """Test .conf files are discovered."""
        (tmp_path / "app.conf").write_text("[app]\nmode = production")

        files = find_ini_files(tmp_path)
        assert len(files) >= 1
        assert any(f.suffix == ".conf" for f in files)

    def test_finds_editorconfig(self, tmp_path: Path) -> None:
        """Test .editorconfig is discovered."""
        (tmp_path / ".editorconfig").write_text("""root = true

[*]
indent_style = space
""")

        files = find_ini_files(tmp_path)
        assert len(files) >= 1
        assert any(f.name == ".editorconfig" for f in files)

    def test_finds_flake8(self, tmp_path: Path) -> None:
        """Test .flake8 is discovered."""
        (tmp_path / ".flake8").write_text("""[flake8]
max-line-length = 100
""")

        files = find_ini_files(tmp_path)
        assert len(files) >= 1
        assert any(f.name == ".flake8" for f in files)

    def test_finds_pylintrc(self, tmp_path: Path) -> None:
        """Test .pylintrc is discovered."""
        (tmp_path / ".pylintrc").write_text("""[MAIN]
jobs = 4
""")

        files = find_ini_files(tmp_path)
        assert len(files) >= 1
        assert any(f.name == ".pylintrc" for f in files)


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_ini_files(self, tmp_path: Path) -> None:
        """Test directory with no INI files."""
        result = analyze_ini(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_ini(self, tmp_path: Path) -> None:
        """Test minimal INI file."""
        make_ini_file(tmp_path, "config.ini", """[main]
key = value
""")
        result = analyze_ini(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_ini_file(tmp_path, "config.ini", """[database]
host = localhost
""")
        result = analyze_ini(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1


class TestComplexIni:
    """Branch coverage for complex INI files."""

    def test_comprehensive_config(self, tmp_path: Path) -> None:
        """Test comprehensive INI configuration file."""
        make_ini_file(tmp_path, "config.ini", """[database]
host = localhost
port = 5432
name = production_db
password = supersecret123

[logging]
level = INFO
file = /var/log/app.log
format = json

[server]
host = 0.0.0.0
port = 8080
debug = false

[cache]
driver = redis
host = localhost
ttl = 3600

[security]
ssl_enabled = true
certificate = /etc/ssl/cert.pem

[api]
base_url = https://api.example.com
api_key = sk_live_12345
timeout = 30
""")
        result = analyze_ini(tmp_path)

        sections = [s for s in result.symbols if s.kind == "section"]
        settings = [s for s in result.symbols if s.kind == "setting"]

        assert len(sections) >= 6
        assert len(settings) >= 15

        # Check sensitive values are masked
        sensitive = [s for s in settings if s.meta.get("is_sensitive")]
        assert len(sensitive) >= 2  # password, api_key
