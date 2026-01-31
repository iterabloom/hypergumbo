"""Tests for the Java properties file analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_mainstream import properties as properties_module
from hypergumbo_lang_mainstream.properties import (
    PropertiesAnalysisResult,
    analyze_properties,
    find_properties_files,
    is_properties_tree_sitter_available,
)


def make_properties_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a properties file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindPropertiesFiles:
    """Tests for find_properties_files function."""

    def test_finds_properties_files(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "app.properties", "key=value\n")
        make_properties_file(tmp_path, "config/db.properties", "host=localhost\n")
        files = find_properties_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"app.properties", "db.properties"}

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_properties_files(tmp_path)
        assert files == []


class TestIsPropertiesTreeSitterAvailable:
    """Tests for is_properties_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_properties_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(properties_module, "is_properties_tree_sitter_available", return_value=False):
            assert properties_module.is_properties_tree_sitter_available() is False


class TestAnalyzeProperties:
    """Tests for analyze_properties function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "key=value\n")
        with patch.object(properties_module, "is_properties_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Properties analysis skipped"):
                result = properties_module.analyze_properties(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_simple_property(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "app.name=MyApp\n")
        result = analyze_properties(tmp_path)
        assert not result.skipped
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.name == "app.name"
        assert prop.language == "properties"
        assert prop.meta.get("value") == "MyApp"

    def test_extracts_property_without_value(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "empty.property=\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("value") == ""

    def test_extracts_multiple_properties(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", """
database.host=localhost
database.port=5432
database.name=mydb
""")
        result = analyze_properties(tmp_path)
        props = [s for s in result.symbols if s.kind == "property"]
        assert len(props) == 3
        names = {p.name for p in props}
        assert "database.host" in names
        assert "database.port" in names
        assert "database.name" in names

    def test_extracts_prefix(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "database.host=localhost\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("prefix") == "database"

    def test_categorizes_database_prefix(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "database.url=jdbc:mysql://localhost\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("category") == "database"

    def test_categorizes_db_prefix(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "db.host=localhost\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("category") == "database"

    def test_categorizes_spring_prefix(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "spring.datasource.url=jdbc:h2:mem:test\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("category") == "framework"

    def test_categorizes_logging_prefix(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "logging.level.root=INFO\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("category") == "logging"

    def test_categorizes_security_prefix(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "security.oauth2.enabled=true\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("category") == "security"

    def test_masks_password_property(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "database.password=secret123\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("is_sensitive") is True
        assert prop.meta.get("value") == "***"
        assert "***" in prop.signature
        assert "secret123" not in prop.signature

    def test_masks_secret_property(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "api.secret=abc123xyz\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("is_sensitive") is True

    def test_masks_token_property(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "auth.token=eyJhbGc\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("is_sensitive") is True

    def test_masks_key_property(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "encryption.key=AES256KEY\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("is_sensitive") is True

    def test_masks_credential_property(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "service.credential=mypass\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("is_sensitive") is True

    def test_non_sensitive_property_shows_value(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "app.name=MyApplication\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("is_sensitive") is False
        assert prop.meta.get("value") == "MyApplication"
        assert "MyApplication" in prop.signature

    def test_truncates_long_value_in_signature(self, tmp_path: Path) -> None:
        long_value = "x" * 100
        make_properties_file(tmp_path, "test.properties", f"app.description={long_value}\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert len(prop.signature) < len(long_value)
        assert "..." in prop.signature

    def test_pass_id(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "key=value\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.origin == "properties.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "key=value\n")
        result = analyze_properties(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "properties.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_properties(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "key=value\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.id == prop.stable_id
        assert "properties:" in prop.id
        assert "test.properties" in prop.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "key=value\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.span is not None
        assert prop.span.start_line >= 1

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "app.properties", "app.name=MyApp\n")
        make_properties_file(tmp_path, "db.properties", "database.host=localhost\n")
        result = analyze_properties(tmp_path)
        props = [s for s in result.symbols if s.kind == "property"]
        assert len(props) == 2
        names = {p.name for p in props}
        assert "app.name" in names
        assert "database.host" in names

    def test_run_files_analyzed(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "a.properties", "a=1\n")
        make_properties_file(tmp_path, "b.properties", "b=2\n")
        make_properties_file(tmp_path, "c.properties", "c=3\n")
        result = analyze_properties(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 3

    def test_complete_properties_file(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "application.properties", """
# Application settings
app.name=MyApplication
app.version=1.0.0

# Database configuration
database.host=localhost
database.port=5432
database.name=mydb
database.password=secret

# Logging
logging.level.root=INFO
logging.level.com.myapp=DEBUG

# Server
server.port=8080
server.ssl.enabled=true
""")
        result = analyze_properties(tmp_path)
        props = [s for s in result.symbols if s.kind == "property"]
        assert len(props) >= 9

        # Check categories
        categories = {p.meta.get("category") for p in props}
        assert "database" in categories
        assert "logging" in categories
        assert "server" in categories
        assert "application" in categories

        # Check sensitive masking
        password_prop = next((p for p in props if "password" in p.name), None)
        assert password_prop is not None
        assert password_prop.meta.get("is_sensitive") is True

    def test_handles_comments(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", """
# This is a comment
app.name=MyApp
! Another comment style
app.version=1.0
""")
        result = analyze_properties(tmp_path)
        props = [s for s in result.symbols if s.kind == "property"]
        assert len(props) == 2
        names = {p.name for p in props}
        assert "app.name" in names
        assert "app.version" in names

    def test_property_without_prefix(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "standalone=value\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("prefix") == ""
        assert prop.meta.get("category") == ""

    def test_property_with_spaces(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "app.name = My Application\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("value") == "My Application"

    def test_kafka_category(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "kafka.bootstrap.servers=localhost:9092\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("category") == "messaging"

    def test_aws_category(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "aws.region=us-east-1\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("category") == "cloud"

    def test_cache_category(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "cache.ttl=3600\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("category") == "cache"

    def test_hibernate_category(self, tmp_path: Path) -> None:
        make_properties_file(tmp_path, "test.properties", "hibernate.dialect=org.hibernate.dialect.PostgreSQLDialect\n")
        result = analyze_properties(tmp_path)
        prop = next((s for s in result.symbols if s.kind == "property"), None)
        assert prop is not None
        assert prop.meta.get("category") == "persistence"
