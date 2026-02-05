"""Branch coverage tests for xml_config.py analyzer.

Tests specific branch paths in the XML config analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Maven pom.xml processing (project, dependencies)
- Android AndroidManifest.xml processing (activities, permissions)
- XML type detection
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_mainstream.xml_config import (
    _make_symbol_id,
    _make_edge_id,
    analyze_xml_files,
    find_xml_files,
)


def make_xml_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an XML file with given content."""
    (tmp_path / name).write_text(content)


class TestXMLHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id("pom.xml", 1, 10, "myapp", "module")
        assert symbol_id == "xml:pom.xml:1-10:myapp:module"

    def test_make_edge_id_deterministic(self) -> None:
        """Test edge ID is deterministic."""
        edge_id1 = _make_edge_id("src1", "dst1", "depends_on")
        edge_id2 = _make_edge_id("src1", "dst1", "depends_on")
        assert edge_id1 == edge_id2
        assert edge_id1.startswith("edge:sha256:")


class TestMavenPomExtraction:
    """Branch coverage for Maven pom.xml extraction."""

    def test_project_identity(self, tmp_path: Path) -> None:
        """Test Maven project identity extraction."""
        make_xml_file(tmp_path, "pom.xml", """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <groupId>com.example</groupId>
    <artifactId>myapp</artifactId>
    <version>1.0.0</version>
</project>
""")
        result = analyze_xml_files(tmp_path)
        assert not result.skipped

        modules = [s for s in result.symbols if s.kind == "module"]
        assert len(modules) >= 1
        module = modules[0]
        assert module.name == "myapp"
        assert module.meta is not None
        assert module.meta.get("groupId") == "com.example"
        assert module.meta.get("version") == "1.0.0"

    def test_dependencies_extraction(self, tmp_path: Path) -> None:
        """Test Maven dependencies extraction."""
        make_xml_file(tmp_path, "pom.xml", """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <groupId>com.example</groupId>
    <artifactId>myapp</artifactId>
    <dependencies>
        <dependency>
            <groupId>org.springframework</groupId>
            <artifactId>spring-core</artifactId>
            <version>5.3.0</version>
        </dependency>
        <dependency>
            <groupId>junit</groupId>
            <artifactId>junit</artifactId>
            <version>4.13</version>
            <scope>test</scope>
        </dependency>
    </dependencies>
</project>
""")
        result = analyze_xml_files(tmp_path)
        deps = [s for s in result.symbols if s.kind == "dependency"]

        assert len(deps) >= 2
        spring_dep = next((d for d in deps if d.name == "spring-core"), None)
        assert spring_dep is not None
        assert spring_dep.meta is not None
        assert spring_dep.meta.get("groupId") == "org.springframework"

        junit_dep = next((d for d in deps if d.name == "junit"), None)
        assert junit_dep is not None
        assert junit_dep.meta.get("scope") == "test"

    def test_dependency_edges(self, tmp_path: Path) -> None:
        """Test Maven dependency edges are created."""
        make_xml_file(tmp_path, "pom.xml", """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <groupId>com.example</groupId>
    <artifactId>myapp</artifactId>
    <dependencies>
        <dependency>
            <groupId>org.apache</groupId>
            <artifactId>commons-lang</artifactId>
        </dependency>
    </dependencies>
</project>
""")
        result = analyze_xml_files(tmp_path)
        dep_edges = [e for e in result.edges if e.edge_type == "depends_on"]

        assert len(dep_edges) >= 1


class TestAndroidManifestExtraction:
    """Branch coverage for Android AndroidManifest.xml extraction."""

    def test_permission_extraction(self, tmp_path: Path) -> None:
        """Test Android permission extraction."""
        make_xml_file(tmp_path, "AndroidManifest.xml", """<?xml version="1.0"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.myapp">
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.CAMERA" />
</manifest>
""")
        result = analyze_xml_files(tmp_path)
        permissions = [s for s in result.symbols if s.kind == "permission"]

        assert len(permissions) >= 2
        assert any(p.name == "INTERNET" for p in permissions)
        assert any(p.name == "CAMERA" for p in permissions)

    def test_activity_extraction(self, tmp_path: Path) -> None:
        """Test Android activity extraction."""
        make_xml_file(tmp_path, "AndroidManifest.xml", """<?xml version="1.0"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.myapp">
    <application>
        <activity android:name=".MainActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
""")
        result = analyze_xml_files(tmp_path)
        activities = [s for s in result.symbols if s.kind == "activity"]

        assert len(activities) >= 1
        activity = activities[0]
        assert activity.name == "MainActivity"
        assert activity.meta is not None
        assert activity.meta.get("exported") is True
        assert "android.intent.action.MAIN" in activity.meta.get("intent_actions", [])

    def test_service_extraction(self, tmp_path: Path) -> None:
        """Test Android service extraction."""
        make_xml_file(tmp_path, "AndroidManifest.xml", """<?xml version="1.0"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.myapp">
    <application>
        <service android:name=".MyService" android:exported="false" />
    </application>
</manifest>
""")
        result = analyze_xml_files(tmp_path)
        services = [s for s in result.symbols if s.kind == "service"]

        assert len(services) >= 1

    def test_receiver_extraction(self, tmp_path: Path) -> None:
        """Test Android broadcast receiver extraction."""
        make_xml_file(tmp_path, "AndroidManifest.xml", """<?xml version="1.0"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.myapp">
    <application>
        <receiver android:name=".MyReceiver" />
    </application>
</manifest>
""")
        result = analyze_xml_files(tmp_path)
        receivers = [s for s in result.symbols if s.kind == "receiver"]

        assert len(receivers) >= 1

    def test_provider_extraction(self, tmp_path: Path) -> None:
        """Test Android content provider extraction."""
        make_xml_file(tmp_path, "AndroidManifest.xml", """<?xml version="1.0"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.myapp">
    <application>
        <provider android:name=".MyProvider" android:authorities="com.example.provider" />
    </application>
</manifest>
""")
        result = analyze_xml_files(tmp_path)
        providers = [s for s in result.symbols if s.kind == "provider"]

        assert len(providers) >= 1


class TestXMLTypeDetection:
    """Branch coverage for XML type detection."""

    def test_maven_namespace_detection(self, tmp_path: Path) -> None:
        """Test Maven file detection by namespace."""
        make_xml_file(tmp_path, "build.xml", """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <groupId>com.example</groupId>
    <artifactId>myapp</artifactId>
</project>
""")
        result = analyze_xml_files(tmp_path)
        modules = [s for s in result.symbols if s.kind == "module"]

        assert len(modules) >= 1


class TestFindXMLFiles:
    """Branch coverage for file discovery."""

    def test_finds_xml_files(self, tmp_path: Path) -> None:
        """Test .xml files are discovered."""
        (tmp_path / "config.xml").write_text("<?xml version='1.0'?><root/>")

        files = list(find_xml_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".xml" for f in files)


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_xml_files(self, tmp_path: Path) -> None:
        """Test directory with no XML files."""
        result = analyze_xml_files(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_xml(self, tmp_path: Path) -> None:
        """Test minimal XML file (generic, no extraction)."""
        make_xml_file(tmp_path, "config.xml", """<?xml version="1.0"?>
<config>
    <setting>value</setting>
</config>
""")
        result = analyze_xml_files(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_xml_file(tmp_path, "pom.xml", """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <artifactId>myapp</artifactId>
</project>
""")
        result = analyze_xml_files(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1
