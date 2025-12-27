"""Tests for XML (Maven/Android) configuration analyzer using tree-sitter-xml.

Tests verify that the analyzer correctly extracts:
- Maven pom.xml: dependencies, project metadata, modules
- Android Manifest: activities, services, permissions, intent-filters
"""

from hypergumbo.analyze.xml_config import (
    PASS_ID,
    PASS_VERSION,
    XMLAnalysisResult,
    analyze_xml_files,
    find_xml_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "xml-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


def test_analyze_maven_pom(tmp_path):
    """Test parsing Maven pom.xml with dependencies."""
    pom_file = tmp_path / "pom.xml"
    pom_file.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <groupId>com.example</groupId>
    <artifactId>my-app</artifactId>
    <version>1.0.0</version>

    <dependencies>
        <dependency>
            <groupId>junit</groupId>
            <artifactId>junit</artifactId>
            <version>4.13.2</version>
            <scope>test</scope>
        </dependency>
        <dependency>
            <groupId>org.springframework</groupId>
            <artifactId>spring-core</artifactId>
            <version>5.3.0</version>
        </dependency>
    </dependencies>
</project>
""")
    result = analyze_xml_files(tmp_path)

    assert not result.skipped

    # Find project module
    modules = [s for s in result.symbols if s.kind == "module"]
    assert len(modules) >= 1
    project = modules[0]
    assert project.name == "my-app"
    assert project.meta is not None
    assert project.meta.get("groupId") == "com.example"
    assert project.meta.get("version") == "1.0.0"

    # Find dependencies
    deps = [s for s in result.symbols if s.kind == "dependency"]
    assert len(deps) >= 2

    junit_dep = next((d for d in deps if d.name == "junit"), None)
    assert junit_dep is not None
    assert junit_dep.meta.get("groupId") == "junit"
    assert junit_dep.meta.get("version") == "4.13.2"
    assert junit_dep.meta.get("scope") == "test"

    spring_dep = next((d for d in deps if d.name == "spring-core"), None)
    assert spring_dep is not None
    assert spring_dep.meta.get("groupId") == "org.springframework"


def test_analyze_maven_pom_without_group(tmp_path):
    """Test Maven pom.xml without groupId (inherits from parent)."""
    pom_file = tmp_path / "pom.xml"
    pom_file.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <artifactId>child-module</artifactId>
    <version>1.0.0</version>
</project>
""")
    result = analyze_xml_files(tmp_path)

    modules = [s for s in result.symbols if s.kind == "module"]
    assert len(modules) >= 1
    assert modules[0].name == "child-module"


def test_analyze_maven_dependency_edges(tmp_path):
    """Test that dependency edges are created."""
    pom_file = tmp_path / "pom.xml"
    pom_file.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <groupId>com.example</groupId>
    <artifactId>my-app</artifactId>
    <version>1.0.0</version>

    <dependencies>
        <dependency>
            <groupId>junit</groupId>
            <artifactId>junit</artifactId>
            <version>4.13.2</version>
        </dependency>
    </dependencies>
</project>
""")
    result = analyze_xml_files(tmp_path)

    # Should have depends_on edge from project to dependency
    edges = [e for e in result.edges if e.edge_type == "depends_on"]
    assert len(edges) >= 1


def test_analyze_android_manifest_activities(tmp_path):
    """Test parsing Android manifest with activities."""
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text("""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.app">

    <application
        android:name=".MyApplication"
        android:label="@string/app_name">

        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>

        <activity
            android:name=".SettingsActivity"
            android:exported="false" />
    </application>
</manifest>
""")
    result = analyze_xml_files(tmp_path)

    assert not result.skipped

    activities = [s for s in result.symbols if s.kind == "activity"]
    assert len(activities) >= 2

    main_activity = next((a for a in activities if a.name == "MainActivity"), None)
    assert main_activity is not None
    assert main_activity.meta is not None
    assert main_activity.meta.get("full_name") == "com.example.app.MainActivity"
    assert main_activity.meta.get("exported") is True
    assert "android.intent.action.MAIN" in main_activity.meta.get("intent_actions", [])
    assert "android.intent.category.LAUNCHER" in main_activity.meta.get("intent_categories", [])

    settings_activity = next((a for a in activities if a.name == "SettingsActivity"), None)
    assert settings_activity is not None
    assert settings_activity.meta.get("exported") is False


def test_analyze_android_manifest_services(tmp_path):
    """Test parsing Android manifest with services."""
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text("""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.app">

    <application>
        <service
            android:name=".BackgroundService"
            android:exported="false" />
    </application>
</manifest>
""")
    result = analyze_xml_files(tmp_path)

    services = [s for s in result.symbols if s.kind == "service"]
    assert len(services) >= 1
    assert services[0].name == "BackgroundService"


def test_analyze_android_manifest_permissions(tmp_path):
    """Test parsing Android manifest with permissions."""
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text("""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.app">

    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.CAMERA" />

    <application>
        <activity android:name=".MainActivity" />
    </application>
</manifest>
""")
    result = analyze_xml_files(tmp_path)

    permissions = [s for s in result.symbols if s.kind == "permission"]
    assert len(permissions) >= 2

    internet_perm = next((p for p in permissions if p.name == "INTERNET"), None)
    assert internet_perm is not None
    assert internet_perm.meta.get("full_name") == "android.permission.INTERNET"


def test_analyze_android_receivers_and_providers(tmp_path):
    """Test parsing Android manifest with receivers and content providers."""
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text("""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.app">

    <application>
        <receiver android:name=".BootReceiver" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.BOOT_COMPLETED" />
            </intent-filter>
        </receiver>

        <provider
            android:name=".MyContentProvider"
            android:exported="false" />
    </application>
</manifest>
""")
    result = analyze_xml_files(tmp_path)

    receivers = [s for s in result.symbols if s.kind == "receiver"]
    assert len(receivers) >= 1
    assert receivers[0].name == "BootReceiver"
    assert "android.intent.action.BOOT_COMPLETED" in receivers[0].meta.get("intent_actions", [])

    providers = [s for s in result.symbols if s.kind == "provider"]
    assert len(providers) >= 1
    assert providers[0].name == "MyContentProvider"


def test_analyze_android_fully_qualified_name(tmp_path):
    """Test Android component with fully qualified class name."""
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text("""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.app">

    <application>
        <activity android:name="com.other.library.LibraryActivity" />
    </application>
</manifest>
""")
    result = analyze_xml_files(tmp_path)

    activities = [s for s in result.symbols if s.kind == "activity"]
    assert len(activities) >= 1
    # Should use the full name as-is (not prefixed with package)
    assert activities[0].meta.get("full_name") == "com.other.library.LibraryActivity"


def test_find_xml_files(tmp_path):
    """Test that XML files are discovered correctly."""
    (tmp_path / "pom.xml").write_text("<project />")
    (tmp_path / "AndroidManifest.xml").write_text("<manifest />")
    (tmp_path / "not_xml.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "config.xml").write_text("<config />")

    files = list(find_xml_files(tmp_path))
    # Should find only .xml files
    assert len(files) == 3


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no XML files."""
    result = analyze_xml_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    xml_file = tmp_path / "pom.xml"
    xml_file.write_text("<project />")

    result = analyze_xml_files(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_generic_xml_not_extracted(tmp_path):
    """Test that generic XML files don't produce symbols."""
    xml_file = tmp_path / "config.xml"
    xml_file.write_text("""<?xml version="1.0"?>
<configuration>
    <setting name="debug" value="true" />
</configuration>
""")
    result = analyze_xml_files(tmp_path)

    # Generic XML should be analyzed but not produce symbols
    assert result.run is not None
    assert result.run.files_analyzed >= 1
    assert len(result.symbols) == 0


def test_maven_namespace_detection(tmp_path):
    """Test Maven detection by namespace (not filename)."""
    # File not named pom.xml but has Maven namespace
    xml_file = tmp_path / "parent.xml"
    xml_file.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <artifactId>parent-project</artifactId>
</project>
""")
    result = analyze_xml_files(tmp_path)

    modules = [s for s in result.symbols if s.kind == "module"]
    assert len(modules) >= 1
    assert modules[0].name == "parent-project"


def test_span_information(tmp_path):
    """Test that span information is correct."""
    pom_file = tmp_path / "pom.xml"
    pom_file.write_text("""<?xml version="1.0"?>
<project>
    <artifactId>test</artifactId>
</project>
""")
    result = analyze_xml_files(tmp_path)

    modules = [s for s in result.symbols if s.kind == "module"]
    assert len(modules) >= 1
    assert modules[0].span is not None
    assert modules[0].span.start_line >= 1


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    xml_file = tmp_path / "broken.xml"
    xml_file.write_text("<<<<invalid xml>>>")

    # Should not raise an exception
    result = analyze_xml_files(tmp_path)

    # Result should still be valid
    assert isinstance(result, XMLAnalysisResult)
