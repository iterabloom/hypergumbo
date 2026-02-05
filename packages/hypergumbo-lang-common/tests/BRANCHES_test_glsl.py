"""Branch coverage tests for glsl.py analyzer.

Tests specific branch paths in the GLSL analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Function definition extraction
- Struct definition extraction
- Uniform variable extraction
- Input/output variable extraction
- Function call edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.glsl import (
    _make_edge_id,
    _make_symbol_id,
    analyze_glsl_files,
    find_glsl_files,
)


def make_glsl_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a GLSL file with given content."""
    (tmp_path / name).write_text(content)


class TestGLSLHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id("shaders/vert.glsl", 1, 10, "main", "function")
        assert symbol_id == "glsl:shaders/vert.glsl:1-10:main:function"

    def test_make_edge_id_deterministic(self) -> None:
        """Test edge ID is deterministic."""
        edge_id_1 = _make_edge_id("src1", "dst1", "calls")
        edge_id_2 = _make_edge_id("src1", "dst1", "calls")
        assert edge_id_1 == edge_id_2
        assert edge_id_1.startswith("edge:sha256:")


class TestFunctionExtraction:
    """Branch coverage for function definition extraction."""

    def test_main_function(self, tmp_path: Path) -> None:
        """Test main function extraction."""
        make_glsl_file(tmp_path, "shader.frag", """
#version 330 core
void main() {
    gl_FragColor = vec4(1.0);
}
""")
        result = analyze_glsl_files(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) >= 1
        assert any(f.name == "main" for f in funcs)

    def test_multiple_functions(self, tmp_path: Path) -> None:
        """Test multiple function definitions."""
        make_glsl_file(tmp_path, "utils.glsl", """
#version 330 core
float square(float x) { return x * x; }
float cube(float x) { return x * x * x; }
vec3 normalize_safe(vec3 v) { return length(v) > 0.0 ? normalize(v) : vec3(0.0); }
""")
        result = analyze_glsl_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "square" in names
        assert "cube" in names
        assert "normalize_safe" in names


class TestStructExtraction:
    """Branch coverage for struct definition extraction."""

    def test_struct_definition(self, tmp_path: Path) -> None:
        """Test struct definition extraction."""
        make_glsl_file(tmp_path, "types.glsl", """
#version 330 core
struct Light {
    vec3 position;
    vec3 color;
    float intensity;
};
""")
        result = analyze_glsl_files(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert len(structs) >= 1
        assert any(s.name == "Light" for s in structs)


class TestUniformExtraction:
    """Branch coverage for uniform variable extraction."""

    def test_uniform_declaration(self, tmp_path: Path) -> None:
        """Test uniform variable extraction."""
        make_glsl_file(tmp_path, "shader.vert", """
#version 330 core
uniform mat4 modelMatrix;
uniform mat4 viewMatrix;
uniform mat4 projectionMatrix;
void main() { }
""")
        result = analyze_glsl_files(tmp_path)
        uniforms = [s for s in result.symbols if s.kind == "uniform"]
        names = [u.name for u in uniforms]
        assert "modelMatrix" in names
        assert "viewMatrix" in names
        assert "projectionMatrix" in names


class TestInputOutputExtraction:
    """Branch coverage for in/out variable extraction."""

    def test_input_variable(self, tmp_path: Path) -> None:
        """Test input variable extraction."""
        make_glsl_file(tmp_path, "shader.vert", """
#version 330 core
in vec3 aPosition;
in vec2 aTexCoord;
void main() { }
""")
        result = analyze_glsl_files(tmp_path)
        inputs = [s for s in result.symbols if s.kind == "input"]
        names = [i.name for i in inputs]
        assert "aPosition" in names
        assert "aTexCoord" in names

    def test_output_variable(self, tmp_path: Path) -> None:
        """Test output variable extraction."""
        make_glsl_file(tmp_path, "shader.frag", """
#version 330 core
out vec4 fragColor;
void main() { fragColor = vec4(1.0); }
""")
        result = analyze_glsl_files(tmp_path)
        outputs = [s for s in result.symbols if s.kind == "output"]
        assert len(outputs) >= 1
        assert any(o.name == "fragColor" for o in outputs)


class TestFunctionCallEdges:
    """Branch coverage for function call edge extraction."""

    def test_internal_call(self, tmp_path: Path) -> None:
        """Test call to function in same file."""
        make_glsl_file(tmp_path, "shader.frag", """
#version 330 core
float helper(float x) { return x * 2.0; }
void main() {
    float val = helper(1.0);
}
""")
        result = analyze_glsl_files(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_calls = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_calls) >= 1

    def test_builtin_call(self, tmp_path: Path) -> None:
        """Test call to builtin function creates edge."""
        make_glsl_file(tmp_path, "shader.frag", """
#version 330 core
void main() {
    vec3 v = normalize(vec3(1.0));
}
""")
        result = analyze_glsl_files(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have edge to builtin
        builtin_calls = [e for e in call_edges if "builtin" in e.dst]
        assert len(builtin_calls) >= 1


class TestFindGLSLFiles:
    """Branch coverage for file discovery."""

    def test_finds_vert_files(self, tmp_path: Path) -> None:
        """Test .vert files are discovered."""
        (tmp_path / "shader.vert").write_text("#version 330\nvoid main() {}")

        files = list(find_glsl_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".vert"

    def test_finds_frag_files(self, tmp_path: Path) -> None:
        """Test .frag files are discovered."""
        (tmp_path / "shader.frag").write_text("#version 330\nvoid main() {}")

        files = list(find_glsl_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".frag"

    def test_finds_glsl_files(self, tmp_path: Path) -> None:
        """Test .glsl files are discovered."""
        (tmp_path / "utils.glsl").write_text("#version 330\nfloat f() { return 1.0; }")

        files = list(find_glsl_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".glsl"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        shaders = tmp_path / "assets" / "shaders"
        shaders.mkdir(parents=True)
        (shaders / "main.frag").write_text("#version 330\nvoid main() {}")

        files = list(find_glsl_files(tmp_path))
        assert len(files) == 1


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_minimal_shader(self, tmp_path: Path) -> None:
        """Test minimal GLSL shader."""
        make_glsl_file(tmp_path, "min.frag", """
#version 330 core
void main() { }
""")
        result = analyze_glsl_files(tmp_path)
        assert not result.skipped

    def test_no_glsl_files(self, tmp_path: Path) -> None:
        """Test directory with no GLSL files."""
        result = analyze_glsl_files(tmp_path)
        assert not result.skipped
        assert len(result.symbols) == 0


class TestFunctionSignatures:
    """Branch coverage for function signature extraction."""

    def test_function_with_params(self, tmp_path: Path) -> None:
        """Test function signature extraction."""
        make_glsl_file(tmp_path, "math.glsl", """
#version 330 core
vec3 lerp(vec3 a, vec3 b, float t) {
    return mix(a, b, t);
}
""")
        result = analyze_glsl_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "lerp"]
        assert len(funcs) == 1
        assert funcs[0].signature is not None
