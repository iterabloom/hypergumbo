"""Tests for CUDA analyzer using tree-sitter-cuda.

Tests verify that the analyzer correctly extracts:
- Kernel functions (__global__)
- Device functions (__device__)
- Host/device functions (__host__ __device__)
- Kernel launches (<<<grid, block>>>)
- CUDA API calls
"""

from hypergumbo.analyze.cuda import (
    PASS_ID,
    PASS_VERSION,
    CudaAnalysisResult,
    analyze_cuda_files,
    find_cuda_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "cuda-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


def test_analyze_kernel_function(tmp_path):
    """Test detection of __global__ kernel function."""
    cuda_file = tmp_path / "kernel.cu"
    cuda_file.write_text("""
__global__ void vectorAdd(float* A, float* B, float* C, int N) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < N) {
        C[i] = A[i] + B[i];
    }
}
""")
    result = analyze_cuda_files(tmp_path)

    assert not result.skipped
    kernels = [s for s in result.symbols if s.kind == "kernel"]
    assert len(kernels) >= 1
    assert kernels[0].name == "vectorAdd"
    assert kernels[0].language == "cuda"


def test_analyze_device_function(tmp_path):
    """Test detection of __device__ function."""
    cuda_file = tmp_path / "device.cu"
    cuda_file.write_text("""
__device__ float square(float x) {
    return x * x;
}
""")
    result = analyze_cuda_files(tmp_path)

    device_funcs = [s for s in result.symbols if s.kind == "device_function"]
    assert len(device_funcs) >= 1
    assert device_funcs[0].name == "square"


def test_analyze_host_device_function(tmp_path):
    """Test detection of __host__ __device__ function."""
    cuda_file = tmp_path / "hostdevice.cu"
    cuda_file.write_text("""
__host__ __device__ float add(float a, float b) {
    return a + b;
}
""")
    result = analyze_cuda_files(tmp_path)

    funcs = [s for s in result.symbols if s.kind == "host_device_function"]
    assert len(funcs) >= 1
    assert funcs[0].name == "add"


def test_analyze_kernel_launch(tmp_path):
    """Test detection of kernel launch <<<grid, block>>>."""
    cuda_file = tmp_path / "launch.cu"
    cuda_file.write_text("""
__global__ void myKernel(int* data) {
    data[threadIdx.x] = threadIdx.x;
}

int main() {
    dim3 grid(16);
    dim3 block(256);
    myKernel<<<grid, block>>>(d_data);
    return 0;
}
""")
    result = analyze_cuda_files(tmp_path)

    # Should have kernel and main function
    kernels = [s for s in result.symbols if s.kind == "kernel"]
    assert len(kernels) >= 1
    assert kernels[0].name == "myKernel"

    # Should have kernel_launch edge
    launch_edges = [e for e in result.edges if e.edge_type == "kernel_launch"]
    assert len(launch_edges) >= 1


def test_analyze_cuda_api_call(tmp_path):
    """Test detection of CUDA API calls."""
    cuda_file = tmp_path / "api.cu"
    cuda_file.write_text("""
int main() {
    float* d_data;
    cudaMalloc(&d_data, size);
    cudaMemcpy(d_data, h_data, size, cudaMemcpyHostToDevice);
    cudaFree(d_data);
    return 0;
}
""")
    result = analyze_cuda_files(tmp_path)

    # Should detect cuda API calls as edges
    api_edges = [e for e in result.edges if e.edge_type == "calls" and "cuda" in e.dst.lower()]
    # We don't necessarily create symbols for cuda API, just call edges
    assert len(result.symbols) >= 1  # At least main function


def test_find_cuda_files(tmp_path):
    """Test that CUDA files are discovered correctly."""
    (tmp_path / "kernel.cu").write_text("__global__ void k() {}")
    (tmp_path / "utils.cuh").write_text("__device__ void u();")
    (tmp_path / "not_cuda.cpp").write_text("int main() {}")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "more.cu").write_text("__global__ void m() {}")

    files = list(find_cuda_files(tmp_path))
    # Should find .cu and .cuh files
    assert len(files) >= 3


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no CUDA files."""
    result = analyze_cuda_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    cuda_file = tmp_path / "test.cu"
    cuda_file.write_text("__global__ void test() {}")

    result = analyze_cuda_files(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    cuda_file = tmp_path / "broken.cu"
    cuda_file.write_text("__global__ broken syntax {{{")

    # Should not raise an exception
    result = analyze_cuda_files(tmp_path)

    # Result should still be valid
    assert isinstance(result, CudaAnalysisResult)


def test_span_information(tmp_path):
    """Test that span information is correct."""
    cuda_file = tmp_path / "span.cu"
    cuda_file.write_text("""__global__ void testKernel() {
    // kernel body
}
""")
    result = analyze_cuda_files(tmp_path)

    kernels = [s for s in result.symbols if s.kind == "kernel"]
    assert len(kernels) >= 1

    # Check span
    assert kernels[0].span.start_line >= 1
    assert kernels[0].span.end_line >= kernels[0].span.start_line


def test_tree_sitter_not_available():
    """Test graceful degradation when tree-sitter is not available."""
    from hypergumbo.analyze.cuda import is_cuda_tree_sitter_available

    # The function should return a boolean
    result = is_cuda_tree_sitter_available()
    assert isinstance(result, bool)


def test_multiple_cuda_files(tmp_path):
    """Test analysis across multiple CUDA files."""
    (tmp_path / "kernels.cu").write_text("""
__global__ void kernel1() {}
__global__ void kernel2() {}
""")
    (tmp_path / "utils.cu").write_text("""
__device__ float helper() { return 1.0f; }
""")

    result = analyze_cuda_files(tmp_path)

    assert len(result.symbols) >= 3
    kinds = {s.kind for s in result.symbols}
    assert "kernel" in kinds
    assert "device_function" in kinds


def test_shared_memory_detection(tmp_path):
    """Test detection of __shared__ memory declarations."""
    cuda_file = tmp_path / "shared.cu"
    cuda_file.write_text("""
__global__ void sharedMem() {
    __shared__ float cache[256];
    cache[threadIdx.x] = 0.0f;
}
""")
    result = analyze_cuda_files(tmp_path)

    kernels = [s for s in result.symbols if s.kind == "kernel"]
    assert len(kernels) >= 1
    # Shared memory usage could be tracked in meta


class TestCudaSignatureExtraction:
    """Tests for CUDA function signature extraction."""

    def test_kernel_signature(self, tmp_path):
        """Extract signature from kernel function."""
        cuda_file = tmp_path / "kernel.cu"
        cuda_file.write_text("""
__global__ void addKernel(int *a, int *b, int *c) {
    int i = threadIdx.x;
    c[i] = a[i] + b[i];
}
""")
        result = analyze_cuda_files(tmp_path)
        kernels = [s for s in result.symbols if s.kind == "kernel" and s.name == "addKernel"]
        assert len(kernels) == 1
        assert kernels[0].signature is not None
        assert "int *a" in kernels[0].signature or "int * a" in kernels[0].signature

    def test_device_function_signature(self, tmp_path):
        """Extract signature from device function."""
        cuda_file = tmp_path / "device.cu"
        cuda_file.write_text("""
__device__ float square(float x) {
    return x * x;
}
""")
        result = analyze_cuda_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "device_function" and s.name == "square"]
        assert len(funcs) == 1
        assert funcs[0].signature is not None
        assert "float x" in funcs[0].signature
        assert "float" in funcs[0].signature  # Return type

    def test_function_no_params(self, tmp_path):
        """Extract signature from function with no params."""
        cuda_file = tmp_path / "empty.cu"
        cuda_file.write_text("""
__global__ void emptyKernel() {
}
""")
        result = analyze_cuda_files(tmp_path)
        kernels = [s for s in result.symbols if s.kind == "kernel" and s.name == "emptyKernel"]
        assert len(kernels) == 1
        assert kernels[0].signature == "()"
