"""Branch coverage tests for hlsl.py analyzer.

Tests specific branch paths in the HLSL analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Function definition extraction
- Struct definition extraction
- Variable/resource declaration extraction
- Function call edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.hlsl import (
    _make_edge_id,
    analyze_hlsl,
    find_hlsl_files,
)


def make_hlsl_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an HLSL file with given content."""
    (tmp_path / name).write_text(content)


class TestHLSLHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_edge_id_deterministic(self) -> None:
        """Test edge ID is deterministic."""
        edge_id_1 = _make_edge_id("src1", "dst1", "calls")
        edge_id_2 = _make_edge_id("src1", "dst1", "calls")
        assert edge_id_1 == edge_id_2
        assert edge_id_1.startswith("edge:sha256:")


class TestFunctionExtraction:
    """Branch coverage for function definition extraction."""

    def test_vertex_shader_function(self, tmp_path: Path) -> None:
        """Test vertex shader function extraction."""
        make_hlsl_file(tmp_path, "shader.hlsl", """
struct VS_INPUT {
    float3 pos : POSITION;
};

struct VS_OUTPUT {
    float4 pos : SV_POSITION;
};

VS_OUTPUT VSMain(VS_INPUT input) {
    VS_OUTPUT output;
    output.pos = float4(input.pos, 1.0);
    return output;
}
""")
        result = analyze_hlsl(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) >= 1
        assert any(f.name == "VSMain" for f in funcs)

    def test_multiple_functions(self, tmp_path: Path) -> None:
        """Test multiple function definitions."""
        make_hlsl_file(tmp_path, "utils.hlsl", """
float square(float x) { return x * x; }
float3 helper(float3 v) { return normalize(v); }
""")
        result = analyze_hlsl(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "square" in names
        assert "helper" in names


class TestStructExtraction:
    """Branch coverage for struct definition extraction."""

    def test_struct_definition(self, tmp_path: Path) -> None:
        """Test struct definition extraction."""
        make_hlsl_file(tmp_path, "types.hlsl", """
struct VertexInput {
    float3 position : POSITION;
    float2 texcoord : TEXCOORD0;
};

struct PixelOutput {
    float4 color : SV_TARGET;
};
""")
        result = analyze_hlsl(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        names = [s.name for s in structs]
        assert "VertexInput" in names
        assert "PixelOutput" in names


class TestVariableExtraction:
    """Branch coverage for variable/resource extraction."""

    def test_texture_declaration(self, tmp_path: Path) -> None:
        """Test texture resource declaration extraction."""
        make_hlsl_file(tmp_path, "resources.hlsl", """
Texture2D diffuseTexture : register(t0);
SamplerState linearSampler : register(s0);
""")
        result = analyze_hlsl(tmp_path)
        vars = [s for s in result.symbols if s.kind == "variable"]
        names = [v.name for v in vars]
        assert "diffuseTexture" in names
        assert "linearSampler" in names


class TestFunctionCallEdges:
    """Branch coverage for function call edge extraction."""

    def test_internal_call(self, tmp_path: Path) -> None:
        """Test call to function in same file."""
        make_hlsl_file(tmp_path, "shader.hlsl", """
float helper(float x) { return x * 2.0; }

float4 main() : SV_TARGET {
    return helper(1.0);
}
""")
        result = analyze_hlsl(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_calls = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_calls) >= 1

    def test_external_call(self, tmp_path: Path) -> None:
        """Test call to external/builtin function."""
        make_hlsl_file(tmp_path, "shader.hlsl", """
float4 main() : SV_TARGET {
    return normalize(float4(1, 0, 0, 0));
}
""")
        result = analyze_hlsl(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        external_calls = [e for e in call_edges if "external" in e.dst]
        assert len(external_calls) >= 1


class TestFindHLSLFiles:
    """Branch coverage for file discovery."""

    def test_finds_hlsl_files(self, tmp_path: Path) -> None:
        """Test .hlsl files are discovered."""
        (tmp_path / "shader.hlsl").write_text("float4 main() : SV_TARGET { return 0; }")

        files = list(find_hlsl_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".hlsl"

    def test_finds_hlsli_files(self, tmp_path: Path) -> None:
        """Test .hlsli include files are discovered."""
        (tmp_path / "common.hlsli").write_text("float helper() { return 1.0; }")

        files = list(find_hlsl_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".hlsli"

    def test_finds_fx_files(self, tmp_path: Path) -> None:
        """Test .fx effect files are discovered."""
        (tmp_path / "effect.fx").write_text("float4 main() : SV_TARGET { return 0; }")

        files = list(find_hlsl_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".fx"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        shaders = tmp_path / "Content" / "Shaders"
        shaders.mkdir(parents=True)
        (shaders / "main.hlsl").write_text("float4 main() : SV_TARGET { return 0; }")

        files = list(find_hlsl_files(tmp_path))
        assert len(files) == 1


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_minimal_shader(self, tmp_path: Path) -> None:
        """Test minimal HLSL shader."""
        make_hlsl_file(tmp_path, "min.hlsl", """
float4 main() : SV_TARGET { return 0; }
""")
        result = analyze_hlsl(tmp_path)
        assert not result.skipped

    def test_no_hlsl_files(self, tmp_path: Path) -> None:
        """Test directory with no HLSL files."""
        result = analyze_hlsl(tmp_path)
        assert not result.skipped
        assert len(result.symbols) == 0


class TestFunctionSignatures:
    """Branch coverage for function signature extraction."""

    def test_function_with_params(self, tmp_path: Path) -> None:
        """Test function signature extraction."""
        make_hlsl_file(tmp_path, "math.hlsl", """
float3 lerp_custom(float3 a, float3 b, float t) {
    return a + (b - a) * t;
}
""")
        result = analyze_hlsl(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "lerp_custom"]
        assert len(funcs) == 1
        assert funcs[0].signature is not None


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_hlsl_file(tmp_path, "shader.hlsl", """
float4 main() : SV_TARGET { return 0; }
""")
        result = analyze_hlsl(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1
