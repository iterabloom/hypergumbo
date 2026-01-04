"""Tests for WGSL (WebGPU) shader analyzer using tree-sitter-wgsl.

Tests verify that the analyzer correctly extracts:
- Shader functions (entry points with @vertex, @fragment, @compute)
- Struct definitions
- Uniform/storage buffer declarations
- Function calls
- Binding attributes (@group/@binding)
"""

from hypergumbo.analyze.wgsl import (
    PASS_ID,
    PASS_VERSION,
    WGSLAnalysisResult,
    analyze_wgsl_files,
    find_wgsl_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "wgsl-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


def test_analyze_vertex_shader(tmp_path):
    """Test detection of @vertex entry point."""
    wgsl_file = tmp_path / "shader.wgsl"
    wgsl_file.write_text("""
@vertex
fn vs_main(@location(0) position: vec4<f32>) -> @builtin(position) vec4<f32> {
    return position;
}
""")
    result = analyze_wgsl_files(tmp_path)

    assert not result.skipped
    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 1
    vs_main = next((f for f in functions if f.name == "vs_main"), None)
    assert vs_main is not None
    assert vs_main.language == "wgsl"
    assert vs_main.stable_id == "vertex"
    assert vs_main.meta is not None
    assert vs_main.meta.get("entry_point") == "vertex"


def test_analyze_fragment_shader(tmp_path):
    """Test detection of @fragment entry point."""
    wgsl_file = tmp_path / "shader.wgsl"
    wgsl_file.write_text("""
@fragment
fn fs_main() -> @location(0) vec4<f32> {
    return vec4<f32>(1.0, 0.0, 0.0, 1.0);
}
""")
    result = analyze_wgsl_files(tmp_path)

    functions = [s for s in result.symbols if s.kind == "function"]
    fs_main = next((f for f in functions if f.name == "fs_main"), None)
    assert fs_main is not None
    assert fs_main.stable_id == "fragment"
    assert fs_main.meta.get("entry_point") == "fragment"


def test_analyze_compute_shader(tmp_path):
    """Test detection of @compute entry point."""
    wgsl_file = tmp_path / "compute.wgsl"
    wgsl_file.write_text("""
@compute @workgroup_size(64)
fn cs_main(@builtin(global_invocation_id) id: vec3<u32>) {
    // Compute work
}
""")
    result = analyze_wgsl_files(tmp_path)

    functions = [s for s in result.symbols if s.kind == "function"]
    cs_main = next((f for f in functions if f.name == "cs_main"), None)
    assert cs_main is not None
    assert cs_main.stable_id == "compute"
    assert cs_main.meta.get("entry_point") == "compute"


def test_analyze_struct(tmp_path):
    """Test detection of struct definitions."""
    wgsl_file = tmp_path / "shader.wgsl"
    wgsl_file.write_text("""
struct Uniforms {
    mvp: mat4x4<f32>,
    color: vec4<f32>,
};

@vertex
fn main() -> @builtin(position) vec4<f32> {
    return vec4<f32>(0.0);
}
""")
    result = analyze_wgsl_files(tmp_path)

    structs = [s for s in result.symbols if s.kind == "struct"]
    assert len(structs) >= 1
    uniforms_struct = next((s for s in structs if s.name == "Uniforms"), None)
    assert uniforms_struct is not None


def test_analyze_uniform_binding(tmp_path):
    """Test detection of uniform bindings with @group/@binding."""
    wgsl_file = tmp_path / "shader.wgsl"
    wgsl_file.write_text("""
struct Uniforms {
    mvp: mat4x4<f32>,
};

@group(0) @binding(0)
var<uniform> uniforms: Uniforms;

@vertex
fn main() -> @builtin(position) vec4<f32> {
    return uniforms.mvp * vec4<f32>(0.0);
}
""")
    result = analyze_wgsl_files(tmp_path)

    uniforms = [s for s in result.symbols if s.kind == "uniform"]
    assert len(uniforms) >= 1
    uniform = uniforms[0]
    assert uniform.name == "uniforms"
    assert uniform.meta is not None
    assert uniform.meta.get("group") == 0
    assert uniform.meta.get("binding") == 0


def test_analyze_storage_buffer(tmp_path):
    """Test detection of storage buffers."""
    wgsl_file = tmp_path / "compute.wgsl"
    wgsl_file.write_text("""
struct Data {
    values: array<f32>,
};

@group(0) @binding(1)
var<storage, read_write> data: Data;

@compute @workgroup_size(64)
fn main(@builtin(global_invocation_id) id: vec3<u32>) {
    data.values[id.x] = f32(id.x);
}
""")
    result = analyze_wgsl_files(tmp_path)

    storage = [s for s in result.symbols if s.kind == "storage"]
    assert len(storage) >= 1
    data_buffer = storage[0]
    assert data_buffer.name == "data"
    assert data_buffer.meta.get("group") == 0
    assert data_buffer.meta.get("binding") == 1


def test_analyze_function_calls(tmp_path):
    """Test detection of function calls."""
    wgsl_file = tmp_path / "shader.wgsl"
    wgsl_file.write_text("""
fn helper(x: f32) -> f32 {
    return x * 2.0;
}

@fragment
fn main() -> @location(0) vec4<f32> {
    let result = helper(1.0);
    return vec4<f32>(result, 0.0, 0.0, 1.0);
}
""")
    result = analyze_wgsl_files(tmp_path)

    calls = [e for e in result.edges if e.edge_type == "calls"]
    assert len(calls) >= 1


def test_find_wgsl_files(tmp_path):
    """Test that WGSL files are discovered correctly."""
    (tmp_path / "shader.wgsl").write_text("@vertex fn main() {}")
    (tmp_path / "compute.wgsl").write_text("@compute fn main() {}")
    (tmp_path / "not_wgsl.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "sub.wgsl").write_text("@fragment fn main() {}")

    files = list(find_wgsl_files(tmp_path))
    # Should find only .wgsl files
    assert len(files) == 3


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no WGSL files."""
    result = analyze_wgsl_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    wgsl_file = tmp_path / "shader.wgsl"
    wgsl_file.write_text("@vertex fn main() {}")

    result = analyze_wgsl_files(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    wgsl_file = tmp_path / "broken.wgsl"
    wgsl_file.write_text("@vertex fn main( {{{{")

    # Should not raise an exception
    result = analyze_wgsl_files(tmp_path)

    # Result should still be valid
    assert isinstance(result, WGSLAnalysisResult)


def test_span_information(tmp_path):
    """Test that span information is correct."""
    wgsl_file = tmp_path / "shader.wgsl"
    wgsl_file.write_text("""@vertex
fn main() -> @builtin(position) vec4<f32> {
    return vec4<f32>(0.0);
}
""")
    result = analyze_wgsl_files(tmp_path)

    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 1
    main_fn = functions[0]
    assert main_fn.span is not None
    assert main_fn.span.start_line >= 1


def test_non_entry_point_function(tmp_path):
    """Test that non-entry-point functions are detected without stable_id."""
    wgsl_file = tmp_path / "shader.wgsl"
    wgsl_file.write_text("""
fn helper(x: f32) -> f32 {
    return x * 2.0;
}

@vertex
fn main() -> @builtin(position) vec4<f32> {
    return vec4<f32>(helper(1.0));
}
""")
    result = analyze_wgsl_files(tmp_path)

    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 2

    helper_fn = next((f for f in functions if f.name == "helper"), None)
    assert helper_fn is not None
    assert helper_fn.stable_id is None  # No @vertex/@fragment/@compute

    main_fn = next((f for f in functions if f.name == "main"), None)
    assert main_fn is not None
    assert main_fn.stable_id == "vertex"  # Has @vertex attribute


class TestWGSLSignatureExtraction:
    """Tests for WGSL function signature extraction."""

    def test_function_with_params(self, tmp_path):
        """Extract signature for function with parameters."""
        wgsl_file = tmp_path / "shader.wgsl"
        wgsl_file.write_text("""
fn calculate(x: f32, y: f32) -> f32 {
    return x + y;
}
""")
        result = analyze_wgsl_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "calculate"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x: f32, y: f32) -> f32"

    def test_no_return_type(self, tmp_path):
        """Extract signature for function with no return type."""
        wgsl_file = tmp_path / "shader.wgsl"
        wgsl_file.write_text("""
fn doSomething(value: i32) {
    var x = value;
}
""")
        result = analyze_wgsl_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "doSomething"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(value: i32)"

    def test_vec_types(self, tmp_path):
        """Extract signature with vector types."""
        wgsl_file = tmp_path / "shader.wgsl"
        wgsl_file.write_text("""
fn computeNormal(p1: vec3<f32>, p2: vec3<f32>) -> vec3<f32> {
    return normalize(p2 - p1);
}
""")
        result = analyze_wgsl_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "computeNormal"]
        assert len(funcs) == 1
        assert "vec3<f32>" in funcs[0].signature
