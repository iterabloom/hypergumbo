"""Hypergumbo mainstream language analyzers.

This package provides analyzers for the most widely-used programming languages
in industry, including Python, JavaScript, Java, Go, Rust, and more.

These are languages that most developers will encounter regularly, representing
the core of modern software development stacks.
"""
from hypergumbo_core.analyze.all_analyzers import AnalyzerSpec

__version__ = "2.0.0"

# Analyzer specifications for mainstream languages
# These are registered via entry_points in pyproject.toml
ANALYZER_SPECS = [
    # Core languages (most popular)
    AnalyzerSpec("python", "hypergumbo_lang_mainstream.py", "analyze_python", supports_max_files=True),
    AnalyzerSpec("html", "hypergumbo_lang_mainstream.html", "analyze_html", supports_max_files=True),
    AnalyzerSpec("javascript", "hypergumbo_lang_mainstream.js_ts", "analyze_javascript", supports_max_files=True),
    AnalyzerSpec("java", "hypergumbo_lang_mainstream.java", "analyze_java", capture_symbols_as="java"),
    AnalyzerSpec("c", "hypergumbo_lang_mainstream.c", "analyze_c", capture_symbols_as="c"),
    AnalyzerSpec("cpp", "hypergumbo_lang_mainstream.cpp", "analyze_cpp"),
    AnalyzerSpec("csharp", "hypergumbo_lang_mainstream.csharp", "analyze_csharp"),
    AnalyzerSpec("go", "hypergumbo_lang_mainstream.go", "analyze_go"),
    AnalyzerSpec("rust", "hypergumbo_lang_mainstream.rust", "analyze_rust"),
    AnalyzerSpec("ruby", "hypergumbo_lang_mainstream.ruby", "analyze_ruby"),
    AnalyzerSpec("php", "hypergumbo_lang_mainstream.php", "analyze_php"),
    AnalyzerSpec("swift", "hypergumbo_lang_mainstream.swift", "analyze_swift"),
    AnalyzerSpec("kotlin", "hypergumbo_lang_mainstream.kotlin", "analyze_kotlin"),
    AnalyzerSpec("scala", "hypergumbo_lang_mainstream.scala", "analyze_scala"),

    # Scripting and shell
    AnalyzerSpec("bash", "hypergumbo_lang_mainstream.bash", "analyze_bash"),
    AnalyzerSpec("lua", "hypergumbo_lang_mainstream.lua", "analyze_lua"),
    AnalyzerSpec("perl", "hypergumbo_lang_mainstream.perl", "analyze_perl"),
    AnalyzerSpec("powershell", "hypergumbo_lang_mainstream.powershell", "analyze_powershell"),
    AnalyzerSpec("groovy", "hypergumbo_lang_mainstream.groovy", "analyze_groovy"),

    # JVM ecosystem
    AnalyzerSpec("objc", "hypergumbo_lang_mainstream.objc", "analyze_objc"),

    # Web and markup
    AnalyzerSpec("css", "hypergumbo_lang_mainstream.css", "analyze_css_files"),
    AnalyzerSpec("markdown", "hypergumbo_lang_mainstream.markdown", "analyze_markdown"),

    # Database and query
    AnalyzerSpec("sql", "hypergumbo_lang_mainstream.sql", "analyze_sql_files"),

    # Config files
    AnalyzerSpec("json", "hypergumbo_lang_mainstream.json_config", "analyze_json_files"),
    AnalyzerSpec("yaml_ansible", "hypergumbo_lang_mainstream.yaml_ansible", "analyze_ansible"),
    AnalyzerSpec("xml", "hypergumbo_lang_mainstream.xml_config", "analyze_xml_files"),
    AnalyzerSpec("toml", "hypergumbo_lang_mainstream.toml_config", "analyze_toml_files"),
    AnalyzerSpec("ini", "hypergumbo_lang_mainstream.ini", "analyze_ini"),
    AnalyzerSpec("properties", "hypergumbo_lang_mainstream.properties", "analyze_properties"),
    AnalyzerSpec("gitignore", "hypergumbo_lang_mainstream.gitignore", "analyze_gitignore"),
    AnalyzerSpec("requirements", "hypergumbo_lang_mainstream.requirements", "analyze_requirements"),

    # Build systems
    AnalyzerSpec("dockerfile", "hypergumbo_lang_mainstream.dockerfile", "analyze_dockerfiles"),
    AnalyzerSpec("make", "hypergumbo_lang_mainstream.make", "analyze_make_files"),
    AnalyzerSpec("cmake", "hypergumbo_lang_mainstream.cmake", "analyze_cmake_files"),
]

__all__ = ["ANALYZER_SPECS", "__version__"]
