"""Branch coverage tests for properties.py analyzer.

Tests specific branch paths in the Java properties analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Property extraction with prefixes
- Sensitive key detection and masking
- Category assignment
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_mainstream.properties import (
    _make_symbol_id,
    analyze_properties,
    find_properties_files,
    KNOWN_PREFIXES,
)


def make_properties_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a properties file with given content."""
    (tmp_path / name).write_text(content)


class TestPropertiesHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id(Path("config.properties"), "db.url", "property")
        assert symbol_id == "properties:config.properties:property:db.url"


class TestPropertyExtraction:
    """Branch coverage for property extraction."""

    def test_simple_property(self, tmp_path: Path) -> None:
        """Test simple property extraction."""
        make_properties_file(tmp_path, "app.properties", """
app.name=MyApp
app.version=1.0.0
""")
        result = analyze_properties(tmp_path)
        assert not result.skipped

        props = [s for s in result.symbols if s.kind == "property"]
        assert len(props) >= 2
        assert any(p.name == "app.name" for p in props)
        assert any(p.name == "app.version" for p in props)

    def test_property_with_prefix(self, tmp_path: Path) -> None:
        """Test property with recognized prefix gets category."""
        make_properties_file(tmp_path, "app.properties", """
database.url=jdbc:mysql://localhost:3306/mydb
database.username=admin
""")
        result = analyze_properties(tmp_path)
        props = [s for s in result.symbols if s.kind == "property"]

        db_prop = next((p for p in props if p.name == "database.url"), None)
        assert db_prop is not None
        assert db_prop.meta is not None
        assert db_prop.meta.get("prefix") == "database"
        assert db_prop.meta.get("category") == "database"

    def test_sensitive_property_masking(self, tmp_path: Path) -> None:
        """Test sensitive properties are masked."""
        make_properties_file(tmp_path, "app.properties", """
db.password=supersecret123
api.token=abcd1234
""")
        result = analyze_properties(tmp_path)
        props = [s for s in result.symbols if s.kind == "property"]

        password_prop = next((p for p in props if p.name == "db.password"), None)
        assert password_prop is not None
        assert password_prop.meta is not None
        assert password_prop.meta.get("is_sensitive") is True
        assert password_prop.meta.get("value") == "***"
        assert "***" in password_prop.signature

    def test_property_without_prefix(self, tmp_path: Path) -> None:
        """Test property without dot has empty prefix."""
        make_properties_file(tmp_path, "app.properties", """
standalone=value
""")
        result = analyze_properties(tmp_path)
        props = [s for s in result.symbols if s.kind == "property"]

        prop = next((p for p in props if p.name == "standalone"), None)
        assert prop is not None
        assert prop.meta is not None
        assert prop.meta.get("prefix") == ""

    def test_long_value_truncation(self, tmp_path: Path) -> None:
        """Test long values are truncated in signature."""
        long_value = "x" * 100
        make_properties_file(tmp_path, "app.properties", f"""
long.property={long_value}
""")
        result = analyze_properties(tmp_path)
        props = [s for s in result.symbols if s.kind == "property"]

        prop = next((p for p in props if p.name == "long.property"), None)
        assert prop is not None
        assert "..." in prop.signature
        assert len(prop.signature) < len(f"long.property={long_value}")


class TestKnownPrefixes:
    """Branch coverage for known prefix categories."""

    def test_known_prefixes_coverage(self, tmp_path: Path) -> None:
        """Test various known prefix categories."""
        # Create properties with different prefixes
        make_properties_file(tmp_path, "app.properties", """
spring.application.name=test
server.port=8080
logging.level=DEBUG
mail.host=smtp.example.com
security.enabled=true
cache.ttl=3600
jpa.showSql=true
kafka.broker=localhost:9092
aws.region=us-east-1
""")
        result = analyze_properties(tmp_path)
        props = [s for s in result.symbols if s.kind == "property"]

        # Verify categories assigned
        spring_prop = next((p for p in props if p.name.startswith("spring.")), None)
        assert spring_prop is not None
        assert spring_prop.meta.get("category") == "framework"


class TestFindPropertiesFiles:
    """Branch coverage for file discovery."""

    def test_finds_properties_files(self, tmp_path: Path) -> None:
        """Test .properties files are discovered."""
        (tmp_path / "config.properties").write_text("key=value")

        files = find_properties_files(tmp_path)
        assert len(files) >= 1
        assert any(f.suffix == ".properties" for f in files)


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_properties_files(self, tmp_path: Path) -> None:
        """Test directory with no properties files."""
        result = analyze_properties(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_properties(self, tmp_path: Path) -> None:
        """Test minimal properties file."""
        make_properties_file(tmp_path, "app.properties", """
key=value
""")
        result = analyze_properties(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_properties_file(tmp_path, "app.properties", """
app.name=Test
""")
        result = analyze_properties(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1
