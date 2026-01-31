"""Tests for HLSL (DirectX shader) analysis pass.

Tests verify that the HLSL analyzer correctly extracts:
- Function definitions (vertex, pixel, compute shaders)
- Struct definitions (input/output structures)
- Constant buffer declarations
- Resource declarations (Texture, Sampler, Buffer)
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_common import hlsl as hlsl_module
from hypergumbo_lang_common.hlsl import (
    analyze_hlsl,
    find_hlsl_files,
    is_hlsl_tree_sitter_available,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository for testing."""
    return tmp_path


class TestFindHLSLFiles:
    """Tests for find_hlsl_files function."""

    def test_finds_hlsl_files(self, temp_repo: Path) -> None:
        """Finds .hlsl extension files."""
        (temp_repo / "vertex.hlsl").write_text("float4 main() { return 0; }")
        (temp_repo / "pixel.hlsl").write_text("float4 main() { return 0; }")
        (temp_repo / "README.md").write_text("# Docs")

        files = list(find_hlsl_files(temp_repo))
        filenames = {f.name for f in files}

        assert "vertex.hlsl" in filenames
        assert "pixel.hlsl" in filenames
        assert "README.md" not in filenames

    def test_finds_hlsli_files(self, temp_repo: Path) -> None:
        """Finds .hlsli include files."""
        (temp_repo / "common.hlsli").write_text("struct Input {};")

        files = list(find_hlsl_files(temp_repo))
        filenames = {f.name for f in files}

        assert "common.hlsli" in filenames

    def test_finds_fx_files(self, temp_repo: Path) -> None:
        """Finds .fx effect files."""
        (temp_repo / "effect.fx").write_text("technique Pass1 {}")

        files = list(find_hlsl_files(temp_repo))
        filenames = {f.name for f in files}

        assert "effect.fx" in filenames


class TestHLSLTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_availability_check_runs(self) -> None:
        """Availability check returns a boolean."""
        result = is_hlsl_tree_sitter_available()
        assert isinstance(result, bool)


class TestHLSLAnalysis:
    """Tests for HLSL analysis with tree-sitter."""

    def test_analyzes_function(self, temp_repo: Path) -> None:
        """Detects function definitions."""
        (temp_repo / "shaders.hlsl").write_text('''
float4 VS_Main(float4 pos : POSITION) : SV_POSITION {
    return pos;
}

float4 PS_Main(float4 pos : SV_POSITION) : SV_TARGET {
    return float4(1, 0, 0, 1);
}
''')

        result = analyze_hlsl(temp_repo)

        assert not result.skipped
        func_names = {s.name for s in result.symbols if s.kind == "function"}
        assert "VS_Main" in func_names
        assert "PS_Main" in func_names

    def test_function_signature(self, temp_repo: Path) -> None:
        """Function signatures include parameters."""
        (temp_repo / "shader.hlsl").write_text('''
float4 ComputeLight(float3 normal, float3 lightDir, float4 color) {
    return color * max(dot(normal, lightDir), 0);
}
''')

        result = analyze_hlsl(temp_repo)

        func = next(s for s in result.symbols if s.name == "ComputeLight")
        assert func.signature is not None
        assert "normal" in func.signature
        assert "lightDir" in func.signature

    def test_analyzes_struct(self, temp_repo: Path) -> None:
        """Detects struct definitions."""
        (temp_repo / "structs.hlsl").write_text('''
struct VS_INPUT {
    float4 position : POSITION;
    float2 texcoord : TEXCOORD0;
};

struct VS_OUTPUT {
    float4 position : SV_POSITION;
    float2 texcoord : TEXCOORD0;
};
''')

        result = analyze_hlsl(temp_repo)

        struct_names = {s.name for s in result.symbols if s.kind == "struct"}
        assert "VS_INPUT" in struct_names
        assert "VS_OUTPUT" in struct_names

    def test_analyzes_cbuffer(self, temp_repo: Path) -> None:
        """Detects constant buffer declarations."""
        (temp_repo / "constants.hlsl").write_text('''
cbuffer PerFrame : register(b0) {
    float4x4 viewProj;
    float3 cameraPos;
}

cbuffer PerObject : register(b1) {
    float4x4 world;
}
''')

        result = analyze_hlsl(temp_repo)

        # cbuffers detected as variables
        var_names = {s.name for s in result.symbols if s.kind == "variable"}
        assert "PerFrame" in var_names or "viewProj" in var_names

    def test_analyzes_resources(self, temp_repo: Path) -> None:
        """Detects texture and sampler declarations."""
        (temp_repo / "resources.hlsl").write_text('''
Texture2D diffuseTexture : register(t0);
Texture2D normalTexture : register(t1);
SamplerState linearSampler : register(s0);
''')

        result = analyze_hlsl(temp_repo)

        var_names = {s.name for s in result.symbols if s.kind == "variable"}
        assert "diffuseTexture" in var_names
        assert "normalTexture" in var_names
        assert "linearSampler" in var_names


class TestHLSLAnalysisUnavailable:
    """Tests for handling unavailable tree-sitter."""

    def test_skipped_when_unavailable(self, temp_repo: Path) -> None:
        """Returns skipped result when tree-sitter unavailable."""
        (temp_repo / "shader.hlsl").write_text("float4 main() { return 0; }")

        with patch.object(hlsl_module, "is_hlsl_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="HLSL analysis skipped"):
                result = hlsl_module.analyze_hlsl(temp_repo)

        assert result.skipped is True


class TestHLSLAnalysisRun:
    """Tests for HLSL analysis run metadata."""

    def test_analysis_run_created(self, temp_repo: Path) -> None:
        """Analysis run is created with correct metadata."""
        (temp_repo / "shader.hlsl").write_text('''
float4 main() : SV_TARGET {
    return float4(1, 1, 1, 1);
}
''')

        result = analyze_hlsl(temp_repo)

        assert result.run is not None
        assert result.run.pass_id == "hlsl-v1"
        assert result.run.files_analyzed >= 1


class TestHLSLCallResolution:
    """Tests for HLSL call resolution."""

    def test_function_call_edge(self, temp_repo: Path) -> None:
        """Creates call edges when functions call other functions."""
        (temp_repo / "shaders.hlsl").write_text('''
float4 helper(float4 color) {
    return color * 0.5;
}

float4 main(float4 pos : SV_POSITION) : SV_TARGET {
    return helper(float4(1, 0, 0, 1));
}
''')

        result = analyze_hlsl(temp_repo)

        # Should have call edges from main to helper
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1
        main_calls = [e for e in call_edges if "main" in e.src]
        assert len(main_calls) >= 1
        assert any("helper" in e.dst for e in main_calls)

    def test_external_function_call(self, temp_repo: Path) -> None:
        """Creates call edges for external function calls with lower confidence."""
        (temp_repo / "shader.hlsl").write_text('''
float4 main(float4 pos : SV_POSITION) : SV_TARGET {
    float4 color = saturate(float4(1, 2, 3, 1));
    return color;
}
''')

        result = analyze_hlsl(temp_repo)

        # Should have call edge to external saturate (built-in)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        external_calls = [e for e in call_edges if "external" in e.dst]
        assert len(external_calls) >= 1
        assert any("saturate" in e.dst for e in external_calls)
        # External calls have lower confidence
        for e in external_calls:
            assert e.confidence == 0.70

    def test_resolved_call_confidence(self, temp_repo: Path) -> None:
        """Resolved calls have higher confidence than external calls."""
        (temp_repo / "shader.hlsl").write_text('''
float internal_func() {
    return 1.0;
}

float4 caller(float4 pos : SV_POSITION) : SV_TARGET {
    float val = internal_func();
    return float4(val, val, val, 1);
}
''')

        result = analyze_hlsl(temp_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Find the resolved edge from caller to internal_func
        resolved_call = next((e for e in call_edges if "internal_func" in e.dst and "external" not in e.dst), None)
        assert resolved_call is not None
        # Resolved calls have confidence 0.85 * lookup confidence
        assert resolved_call.confidence > 0.70

    def test_cross_file_call_resolution(self, temp_repo: Path) -> None:
        """Resolves calls across multiple files."""
        (temp_repo / "common.hlsli").write_text('''
float4 shared_helper(float intensity) {
    return float4(intensity, intensity, intensity, 1);
}
''')
        (temp_repo / "main.hlsl").write_text('''
float4 main(float4 pos : SV_POSITION) : SV_TARGET {
    return shared_helper(0.5);
}
''')

        result = analyze_hlsl(temp_repo)

        # Should resolve the cross-file call
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        resolved_calls = [e for e in call_edges if "shared_helper" in e.dst and "external" not in e.dst]
        assert len(resolved_calls) >= 1

    def test_method_style_call(self, temp_repo: Path) -> None:
        """Handles method-style calls like texture.Sample()."""
        (temp_repo / "shader.hlsl").write_text('''
Texture2D myTexture;
SamplerState mySampler;

float4 main(float2 uv : TEXCOORD) : SV_TARGET {
    return myTexture.Sample(mySampler, uv);
}
''')

        result = analyze_hlsl(temp_repo)

        # Should have at least the main function
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) >= 1
