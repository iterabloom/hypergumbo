"""Tests for GLSL shader analyzer using tree-sitter-glsl.

Tests verify that the analyzer correctly extracts:
- Shader functions (main, custom functions)
- Struct definitions
- Uniform/in/out variable declarations
- Function calls
"""

from hypergumbo.analyze.glsl import (
    PASS_ID,
    PASS_VERSION,
    GLSLAnalysisResult,
    analyze_glsl_files,
    find_glsl_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "glsl-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


def test_analyze_function(tmp_path):
    """Test detection of shader functions."""
    glsl_file = tmp_path / "shader.vert"
    glsl_file.write_text("""
#version 330 core

void main() {
    gl_Position = vec4(0.0, 0.0, 0.0, 1.0);
}
""")
    result = analyze_glsl_files(tmp_path)

    assert not result.skipped
    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 1
    assert functions[0].name == "main"
    assert functions[0].language == "glsl"


def test_analyze_struct(tmp_path):
    """Test detection of struct definitions."""
    glsl_file = tmp_path / "shader.frag"
    glsl_file.write_text("""
#version 330 core

struct Material {
    sampler2D diffuse;
    float shininess;
};

void main() {
    gl_FragColor = vec4(1.0);
}
""")
    result = analyze_glsl_files(tmp_path)

    structs = [s for s in result.symbols if s.kind == "struct"]
    assert len(structs) >= 1
    assert structs[0].name == "Material"


def test_analyze_uniform(tmp_path):
    """Test detection of uniform declarations."""
    glsl_file = tmp_path / "shader.vert"
    glsl_file.write_text("""
#version 330 core

uniform mat4 u_projection;
uniform vec3 u_lightPos;

void main() {
    gl_Position = u_projection * vec4(0.0, 0.0, 0.0, 1.0);
}
""")
    result = analyze_glsl_files(tmp_path)

    uniforms = [s for s in result.symbols if s.kind == "uniform"]
    assert len(uniforms) >= 2
    names = [u.name for u in uniforms]
    assert "u_projection" in names
    assert "u_lightPos" in names


def test_analyze_in_out(tmp_path):
    """Test detection of in/out variable declarations."""
    glsl_file = tmp_path / "shader.vert"
    glsl_file.write_text("""
#version 330 core

in vec3 a_position;
in vec2 a_texCoord;

out vec2 v_texCoord;

void main() {
    v_texCoord = a_texCoord;
}
""")
    result = analyze_glsl_files(tmp_path)

    inputs = [s for s in result.symbols if s.kind == "input"]
    outputs = [s for s in result.symbols if s.kind == "output"]
    assert len(inputs) >= 2
    assert len(outputs) >= 1


def test_analyze_function_calls(tmp_path):
    """Test detection of function calls."""
    glsl_file = tmp_path / "shader.frag"
    glsl_file.write_text("""
#version 330 core

uniform sampler2D u_texture;
in vec2 v_texCoord;
out vec4 FragColor;

void main() {
    vec4 texColor = texture(u_texture, v_texCoord);
    FragColor = texColor;
}
""")
    result = analyze_glsl_files(tmp_path)

    calls = [e for e in result.edges if e.edge_type == "calls"]
    assert len(calls) >= 1


def test_find_glsl_files(tmp_path):
    """Test that GLSL files are discovered correctly."""
    (tmp_path / "shader.vert").write_text("void main() {}")
    (tmp_path / "shader.frag").write_text("void main() {}")
    (tmp_path / "shader.glsl").write_text("void main() {}")
    (tmp_path / "not_glsl.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "sub.vert").write_text("void main() {}")

    files = list(find_glsl_files(tmp_path))
    # Should find .vert, .frag, .glsl files
    assert len(files) >= 4


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no GLSL files."""
    result = analyze_glsl_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    glsl_file = tmp_path / "shader.vert"
    glsl_file.write_text("void main() {}")

    result = analyze_glsl_files(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    glsl_file = tmp_path / "broken.vert"
    glsl_file.write_text("void main( {{{{")

    # Should not raise an exception
    result = analyze_glsl_files(tmp_path)

    # Result should still be valid
    assert isinstance(result, GLSLAnalysisResult)


def test_span_information(tmp_path):
    """Test that span information is correct."""
    glsl_file = tmp_path / "shader.vert"
    glsl_file.write_text("""void main() {
    gl_Position = vec4(0.0);
}
""")
    result = analyze_glsl_files(tmp_path)

    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 1

    # Check span
    assert functions[0].span.start_line >= 1
    assert functions[0].span.end_line >= functions[0].span.start_line


def test_tree_sitter_not_available():
    """Test graceful degradation when tree-sitter is not available."""
    from hypergumbo.analyze.glsl import is_glsl_tree_sitter_available

    # The function should return a boolean
    result = is_glsl_tree_sitter_available()
    assert isinstance(result, bool)


def test_multiple_glsl_files(tmp_path):
    """Test analysis across multiple GLSL files."""
    (tmp_path / "vertex.vert").write_text("""
#version 330 core
uniform mat4 u_mvp;
in vec3 a_pos;
void main() {
    gl_Position = u_mvp * vec4(a_pos, 1.0);
}
""")
    (tmp_path / "fragment.frag").write_text("""
#version 330 core
out vec4 FragColor;
void main() {
    FragColor = vec4(1.0, 0.0, 0.0, 1.0);
}
""")

    result = analyze_glsl_files(tmp_path)

    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 2


def test_complete_shader(tmp_path):
    """Test a complete shader structure."""
    glsl_file = tmp_path / "shader.vert"
    glsl_file.write_text("""
#version 330 core

struct Light {
    vec3 position;
    vec3 color;
    float intensity;
};

uniform mat4 u_projection;
uniform mat4 u_view;
uniform mat4 u_model;
uniform Light u_light;

in vec3 a_position;
in vec3 a_normal;
in vec2 a_texCoord;

out vec3 v_normal;
out vec2 v_texCoord;
out vec3 v_fragPos;

void main() {
    vec4 worldPos = u_model * vec4(a_position, 1.0);
    v_fragPos = worldPos.xyz;
    v_normal = mat3(transpose(inverse(u_model))) * a_normal;
    v_texCoord = a_texCoord;
    gl_Position = u_projection * u_view * worldPos;
}
""")
    result = analyze_glsl_files(tmp_path)

    # Check for expected symbol kinds
    kinds = {s.kind for s in result.symbols}
    assert "struct" in kinds
    assert "uniform" in kinds
    assert "input" in kinds
    assert "output" in kinds
    assert "function" in kinds


def test_custom_functions(tmp_path):
    """Test detection of custom shader functions."""
    glsl_file = tmp_path / "shader.frag"
    glsl_file.write_text("""
#version 330 core

float calculateAttenuation(float distance) {
    return 1.0 / (1.0 + 0.1 * distance);
}

vec3 calculateLighting(vec3 normal, vec3 lightDir) {
    float diff = max(dot(normal, lightDir), 0.0);
    return vec3(diff);
}

void main() {
    float atten = calculateAttenuation(10.0);
    gl_FragColor = vec4(1.0);
}
""")
    result = analyze_glsl_files(tmp_path)

    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 3
    names = [f.name for f in functions]
    assert "calculateAttenuation" in names
    assert "calculateLighting" in names
    assert "main" in names


class TestGLSLSignatureExtraction:
    """Tests for GLSL function signature extraction."""

    def test_function_with_params(self, tmp_path):
        """Extract signature for function with parameters."""
        glsl_file = tmp_path / "shader.frag"
        glsl_file.write_text("""
#version 330 core

float calculate(float x, float y) {
    return x + y;
}

void main() {}
""")
        result = analyze_glsl_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "calculate"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(float x, float y) float"

    def test_void_main(self, tmp_path):
        """Extract signature for void main()."""
        glsl_file = tmp_path / "shader.vert"
        glsl_file.write_text("""
#version 330 core

void main() {
    gl_Position = vec4(0.0);
}
""")
        result = analyze_glsl_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "main"]
        assert len(funcs) == 1
        # void return type is omitted
        assert funcs[0].signature == "()"

    def test_vec_return_type(self, tmp_path):
        """Extract signature for function returning vec type."""
        glsl_file = tmp_path / "shader.frag"
        glsl_file.write_text("""
#version 330 core

vec3 computeNormal(vec3 p1, vec3 p2, vec3 p3) {
    return normalize(cross(p2 - p1, p3 - p1));
}

void main() {}
""")
        result = analyze_glsl_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "computeNormal"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(vec3 p1, vec3 p2, vec3 p3) vec3"
