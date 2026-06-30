# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hypergumbo mainstream language analyzers.

This package provides analyzers for the most widely-used programming
languages in industry — Python, JavaScript/TypeScript, Java, Go, Rust,
C, C++, C#, Ruby, PHP, Swift, Kotlin, Scala — plus the scripting and
shell languages (Bash, Lua, Perl, PowerShell, Groovy), Objective-C,
SQL, and the web markup / styling family (HTML, CSS, Markdown).

It also bundles the high-volume *non-language* config-file analyzers
that share the same tree-sitter machinery and IR — JSON, YAML/Ansible,
XML, TOML, INI, properties, gitignore, requirements — plus build-system
descriptors (Dockerfile, Make, CMake), cross-language manifest target
extraction, Jupyter notebooks, and the Play framework's routes DSL.

See `docs/LANGUAGES.md` for the authoritative inventory.
"""

__version__ = "6.1.0"

# Module paths for analyzer discovery via entry-points (ADR-0012 Step 1).
# Importing each module triggers the @register_analyzer decorator within it.
ANALYZER_MODULES = [
    # Core languages (most popular)
    "hypergumbo_lang_mainstream.py",
    "hypergumbo_lang_mainstream.html",
    "hypergumbo_lang_mainstream.js_ts",
    "hypergumbo_lang_mainstream.java",
    "hypergumbo_lang_mainstream.c",
    "hypergumbo_lang_mainstream.cpp",
    "hypergumbo_lang_mainstream.csharp",
    "hypergumbo_lang_mainstream.go",
    "hypergumbo_lang_mainstream.rust",
    "hypergumbo_lang_mainstream.ruby",
    "hypergumbo_lang_mainstream.php",
    "hypergumbo_lang_mainstream.swift",
    "hypergumbo_lang_mainstream.kotlin",
    "hypergumbo_lang_mainstream.scala",

    # Scripting and shell
    "hypergumbo_lang_mainstream.bash",
    "hypergumbo_lang_mainstream.lua",
    "hypergumbo_lang_mainstream.perl",
    "hypergumbo_lang_mainstream.powershell",
    "hypergumbo_lang_mainstream.groovy",

    # Apple platforms (Objective-C)
    "hypergumbo_lang_mainstream.objc",

    # Web and markup
    "hypergumbo_lang_mainstream.css",
    "hypergumbo_lang_mainstream.markdown",

    # Database and query
    "hypergumbo_lang_mainstream.sql",

    # Framework-specific config
    "hypergumbo_lang_mainstream.play_routes",

    # Config files
    "hypergumbo_lang_mainstream.json_config",
    "hypergumbo_lang_mainstream.yaml_ansible",
    "hypergumbo_lang_mainstream.yaml",
    "hypergumbo_lang_mainstream.xml_config",
    "hypergumbo_lang_mainstream.toml_config",
    "hypergumbo_lang_mainstream.ini",
    "hypergumbo_lang_mainstream.properties",
    "hypergumbo_lang_mainstream.gitignore",
    "hypergumbo_lang_mainstream.requirements",

    # Build systems
    "hypergumbo_lang_mainstream.dockerfile",
    "hypergumbo_lang_mainstream.make",
    "hypergumbo_lang_mainstream.cmake",

    # Cross-language manifest build targets
    "hypergumbo_lang_mainstream.manifest_targets",

    # Notebook formats
    "hypergumbo_lang_mainstream.jupyter",
]

__all__ = ["ANALYZER_MODULES", "__version__"]
