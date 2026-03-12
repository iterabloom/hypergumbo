# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for wgsl.py analyzer.

Tests specific branch paths in the WGSL analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Function extraction (including entry points)
- Struct extraction
- Uniform/storage buffer extraction
- Binding attribute detection
- Function call edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_symbol_id
from hypergumbo_lang_common.wgsl import _make_edge_id, analyze_wgsl_files, find_wgsl_files

def make_wgsl_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a WGSL shader file with given content."""
    (tmp_path / name).write_text(content)

class TestWgslHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("wgsl", "shaders/main.wgsl", 1, 5, "vertexMain", "function")
        assert symbol_id == "wgsl:shaders/main.wgsl:1-5:vertexMain:function"

    def test_make_edge_id_format(self) -> None:
        """Test edge ID is deterministic."""
        edge_id1 = _make_edge_id("src1", "dst1", "calls")
        edge_id2 = _make_edge_id("src1", "dst1", "calls")
        assert edge_id1 == edge_id2
        assert edge_id1.startswith("edge:sha256:")

class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_simple_function(self, tmp_path: Path) -> None:
        """Test simple function extraction."""
        make_wgsl_file(tmp_path, "shader.wgsl", """
fn add(a: f32, b: f32) -> f32 {
    return a + b;
}
""")
        result = analyze_wgsl_files(tmp_path)
        assert not result.skipped

        functions = [s for s in result.symbols if s.kind == "function"]
        assert len(functions) >= 1
        assert any(f.name == "add" for f in functions)

    def test_function_signature(self, tmp_path: Path) -> None:
        """Test function signature extraction."""
        make_wgsl_file(tmp_path, "shader.wgsl", """
fn multiply(x: f32, y: f32) -> f32 {
    return x * y;
}
""")
        result = analyze_wgsl_files(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function" and s.name == "multiply"]
        assert len(functions) >= 1
        assert functions[0].signature is not None
        assert "f32" in functions[0].signature

class TestEntryPointExtraction:
    """Branch coverage for shader entry point extraction."""

    def test_vertex_entry_point(self, tmp_path: Path) -> None:
        """Test @vertex entry point detection."""
        make_wgsl_file(tmp_path, "shader.wgsl", """
@vertex
fn vertexMain(@location(0) pos: vec3<f32>) -> @builtin(position) vec4<f32> {
    return vec4<f32>(pos, 1.0);
}
""")
        result = analyze_wgsl_files(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function" and s.name == "vertexMain"]
        assert len(functions) >= 1
        assert functions[0].meta is not None
        assert functions[0].meta.get("entry_point") == "vertex"

    def test_fragment_entry_point(self, tmp_path: Path) -> None:
        """Test @fragment entry point detection."""
        make_wgsl_file(tmp_path, "shader.wgsl", """
@fragment
fn fragmentMain() -> @location(0) vec4<f32> {
    return vec4<f32>(1.0, 0.0, 0.0, 1.0);
}
""")
        result = analyze_wgsl_files(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function" and s.name == "fragmentMain"]
        assert len(functions) >= 1
        assert functions[0].meta is not None
        assert functions[0].meta.get("entry_point") == "fragment"

    def test_compute_entry_point(self, tmp_path: Path) -> None:
        """Test @compute entry point detection."""
        make_wgsl_file(tmp_path, "shader.wgsl", """
@compute @workgroup_size(64)
fn computeMain(@builtin(global_invocation_id) id: vec3<u32>) {
    // compute work
}
""")
        result = analyze_wgsl_files(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function" and s.name == "computeMain"]
        assert len(functions) >= 1
        assert functions[0].meta is not None
        assert functions[0].meta.get("entry_point") == "compute"

class TestStructExtraction:
    """Branch coverage for struct extraction."""

    def test_simple_struct(self, tmp_path: Path) -> None:
        """Test simple struct extraction."""
        make_wgsl_file(tmp_path, "shader.wgsl", """
struct VertexInput {
    @location(0) position: vec3<f32>,
    @location(1) color: vec3<f32>,
}
""")
        result = analyze_wgsl_files(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert len(structs) >= 1
        assert any(s.name == "VertexInput" for s in structs)

    def test_multiple_structs(self, tmp_path: Path) -> None:
        """Test multiple struct extraction."""
        make_wgsl_file(tmp_path, "shader.wgsl", """
struct VertexInput {
    @location(0) position: vec3<f32>,
}

struct VertexOutput {
    @builtin(position) position: vec4<f32>,
}
""")
        result = analyze_wgsl_files(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert len(structs) >= 2

class TestUniformExtraction:
    """Branch coverage for uniform buffer extraction."""

    def test_uniform_buffer(self, tmp_path: Path) -> None:
        """Test uniform buffer extraction."""
        make_wgsl_file(tmp_path, "shader.wgsl", """
struct Uniforms {
    modelViewProjection: mat4x4<f32>,
}

@group(0) @binding(0)
var<uniform> uniforms: Uniforms;
""")
        result = analyze_wgsl_files(tmp_path)
        uniforms = [s for s in result.symbols if s.kind == "uniform"]
        assert len(uniforms) >= 1
        assert any(u.name == "uniforms" for u in uniforms)

    def test_uniform_with_binding_info(self, tmp_path: Path) -> None:
        """Test uniform has binding metadata."""
        make_wgsl_file(tmp_path, "shader.wgsl", """
@group(1) @binding(2)
var<uniform> mvp: mat4x4<f32>;
""")
        result = analyze_wgsl_files(tmp_path)
        uniforms = [s for s in result.symbols if s.kind == "uniform" and s.name == "mvp"]
        assert len(uniforms) >= 1
        assert uniforms[0].meta is not None
        assert uniforms[0].meta.get("group") == 1
        assert uniforms[0].meta.get("binding") == 2

class TestStorageExtraction:
    """Branch coverage for storage buffer extraction."""

    def test_storage_buffer(self, tmp_path: Path) -> None:
        """Test storage buffer extraction."""
        make_wgsl_file(tmp_path, "shader.wgsl", """
@group(0) @binding(0)
var<storage, read> inputData: array<f32>;

@group(0) @binding(1)
var<storage, read_write> outputData: array<f32>;
""")
        result = analyze_wgsl_files(tmp_path)
        storage = [s for s in result.symbols if s.kind == "storage"]
        assert len(storage) >= 2

class TestCallEdges:
    """Branch coverage for function call edge extraction."""

    def test_function_call_creates_edge(self, tmp_path: Path) -> None:
        """Test function call creates calls edge."""
        make_wgsl_file(tmp_path, "shader.wgsl", """
fn helper() -> f32 {
    return 1.0;
}

fn main() -> f32 {
    return helper();
}
""")
        result = analyze_wgsl_files(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

    def test_multiple_calls(self, tmp_path: Path) -> None:
        """Test multiple function calls."""
        make_wgsl_file(tmp_path, "shader.wgsl", """
fn funcA() -> f32 {
    return 1.0;
}

fn funcB() -> f32 {
    return 2.0;
}

fn main() -> f32 {
    return funcA() + funcB();
}
""")
        result = analyze_wgsl_files(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 2

class TestFindWgslFiles:
    """Branch coverage for file discovery."""

    def test_finds_wgsl_files(self, tmp_path: Path) -> None:
        """Test .wgsl files are discovered."""
        (tmp_path / "shader.wgsl").write_text("fn main() {}")

        files = list(find_wgsl_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".wgsl"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        shaders = tmp_path / "assets" / "shaders"
        shaders.mkdir(parents=True)
        (shaders / "main.wgsl").write_text("fn main() {}")

        files = list(find_wgsl_files(tmp_path))
        assert len(files) == 1

class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_wgsl_files(self, tmp_path: Path) -> None:
        """Test directory with no WGSL files."""
        result = analyze_wgsl_files(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_shader(self, tmp_path: Path) -> None:
        """Test minimal WGSL shader."""
        make_wgsl_file(tmp_path, "min.wgsl", """
fn main() -> f32 {
    return 1.0;
}
""")
        result = analyze_wgsl_files(tmp_path)
        assert not result.skipped

class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_wgsl_file(tmp_path, "shader.wgsl", """
@vertex
fn main() -> @builtin(position) vec4<f32> {
    return vec4<f32>(0.0, 0.0, 0.0, 1.0);
}
""")
        result = analyze_wgsl_files(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1

class TestComplexShader:
    """Branch coverage for complex shader files."""

    def test_full_shader_pipeline(self, tmp_path: Path) -> None:
        """Test complete vertex + fragment shader."""
        make_wgsl_file(tmp_path, "full.wgsl", """
struct VertexInput {
    @location(0) position: vec3<f32>,
    @location(1) uv: vec2<f32>,
}

struct VertexOutput {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
}

struct Uniforms {
    mvp: mat4x4<f32>,
}

@group(0) @binding(0)
var<uniform> uniforms: Uniforms;

@vertex
fn vertexMain(input: VertexInput) -> VertexOutput {
    var output: VertexOutput;
    output.position = uniforms.mvp * vec4<f32>(input.position, 1.0);
    output.uv = input.uv;
    return output;
}

@fragment
fn fragmentMain(input: VertexOutput) -> @location(0) vec4<f32> {
    return vec4<f32>(input.uv, 0.0, 1.0);
}
""")
        result = analyze_wgsl_files(tmp_path)

        # Should have structs
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert len(structs) >= 2

        # Should have entry points
        functions = [s for s in result.symbols if s.kind == "function"]
        vertex_funcs = [f for f in functions if f.meta and f.meta.get("entry_point") == "vertex"]
        fragment_funcs = [f for f in functions if f.meta and f.meta.get("entry_point") == "fragment"]
        assert len(vertex_funcs) >= 1
        assert len(fragment_funcs) >= 1

        # Should have uniform
        uniforms = [s for s in result.symbols if s.kind == "uniform"]
        assert len(uniforms) >= 1
