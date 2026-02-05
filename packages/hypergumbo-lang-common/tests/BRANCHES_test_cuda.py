"""Branch coverage tests for cuda.py analyzer.

Tests specific branch paths in the CUDA analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Kernel function (__global__) extraction
- Device function (__device__) extraction
- Host/device function extraction
- Kernel launch edge extraction
- Regular function call edges
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.cuda import (
    _determine_function_kind,
    _make_edge_id,
    _make_symbol_id,
    analyze_cuda_files,
    find_cuda_files,
)


def make_cuda_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a CUDA file with given content."""
    (tmp_path / name).write_text(content)


class TestCudaHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id("src/kernel.cu", 10, 25, "myKernel", "kernel")
        assert symbol_id == "cuda:src/kernel.cu:10-25:myKernel:kernel"

    def test_make_edge_id_deterministic(self) -> None:
        """Test edge ID is deterministic."""
        edge_id_1 = _make_edge_id("src1", "dst1", "kernel_launch")
        edge_id_2 = _make_edge_id("src1", "dst1", "kernel_launch")
        assert edge_id_1 == edge_id_2
        assert edge_id_1.startswith("edge:sha256:")


class TestDetermineFunctionKind:
    """Branch coverage for function kind determination."""

    def test_global_kernel(self) -> None:
        """Test __global__ is identified as kernel."""
        kind = _determine_function_kind(is_global=True, is_device=False, is_host=False)
        assert kind == "kernel"

    def test_device_function(self) -> None:
        """Test __device__ is identified as device function."""
        kind = _determine_function_kind(is_global=False, is_device=True, is_host=False)
        assert kind == "device_function"

    def test_host_device_function(self) -> None:
        """Test __host__ __device__ is identified as host_device_function."""
        kind = _determine_function_kind(is_global=False, is_device=True, is_host=True)
        assert kind == "host_device_function"

    def test_regular_function(self) -> None:
        """Test no attributes is identified as regular function."""
        kind = _determine_function_kind(is_global=False, is_device=False, is_host=False)
        assert kind == "function"


class TestKernelExtraction:
    """Branch coverage for kernel function extraction."""

    def test_global_kernel(self, tmp_path: Path) -> None:
        """Test __global__ kernel extraction."""
        make_cuda_file(tmp_path, "kernel.cu", """
__global__ void vectorAdd(float *a, float *b, float *c, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) {
        c[i] = a[i] + b[i];
    }
}
""")
        result = analyze_cuda_files(tmp_path)
        assert not result.skipped

        kernels = [s for s in result.symbols if s.kind == "kernel"]
        assert len(kernels) == 1
        assert kernels[0].name == "vectorAdd"
        assert kernels[0].meta is not None
        assert kernels[0].meta.get("is_kernel") is True

    def test_multiple_kernels(self, tmp_path: Path) -> None:
        """Test multiple kernel definitions."""
        make_cuda_file(tmp_path, "kernels.cu", """
__global__ void kernel1(int *data) {
    data[threadIdx.x] = 1;
}

__global__ void kernel2(int *data) {
    data[threadIdx.x] = 2;
}
""")
        result = analyze_cuda_files(tmp_path)
        kernels = [s for s in result.symbols if s.kind == "kernel"]
        names = [k.name for k in kernels]
        assert "kernel1" in names
        assert "kernel2" in names


class TestDeviceFunctionExtraction:
    """Branch coverage for device function extraction."""

    def test_device_function(self, tmp_path: Path) -> None:
        """Test __device__ function extraction."""
        make_cuda_file(tmp_path, "device.cu", """
__device__ float helper(float x) {
    return x * 2.0f;
}
""")
        result = analyze_cuda_files(tmp_path)
        device_funcs = [s for s in result.symbols if s.kind == "device_function"]
        assert len(device_funcs) == 1
        assert device_funcs[0].name == "helper"

    def test_host_device_function(self, tmp_path: Path) -> None:
        """Test __host__ __device__ function extraction."""
        make_cuda_file(tmp_path, "hostdevice.cu", """
__host__ __device__ int shared_func(int x) {
    return x + 1;
}
""")
        result = analyze_cuda_files(tmp_path)
        hd_funcs = [s for s in result.symbols if s.kind == "host_device_function"]
        assert len(hd_funcs) == 1
        assert hd_funcs[0].name == "shared_func"


class TestRegularFunctionExtraction:
    """Branch coverage for regular function extraction."""

    def test_host_function(self, tmp_path: Path) -> None:
        """Test regular host function extraction."""
        make_cuda_file(tmp_path, "host.cu", """
void initData(float *data, int n) {
    for (int i = 0; i < n; i++) {
        data[i] = 1.0f;
    }
}
""")
        result = analyze_cuda_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].name == "initData"


class TestKernelLaunchEdges:
    """Branch coverage for kernel launch edge extraction."""

    def test_kernel_launch(self, tmp_path: Path) -> None:
        """Test kernel launch creates kernel_launch edge."""
        make_cuda_file(tmp_path, "launch.cu", """
__global__ void myKernel(int *data) {
    data[threadIdx.x] = 1;
}

void launcher() {
    int *d_data;
    myKernel<<<1, 32>>>(d_data);
}
""")
        result = analyze_cuda_files(tmp_path)
        assert not result.skipped

        launch_edges = [e for e in result.edges if e.edge_type == "kernel_launch"]
        assert len(launch_edges) >= 1
        # Should be launcher calling myKernel
        kernel_launches = [e for e in launch_edges if "myKernel" in e.dst]
        assert len(kernel_launches) >= 1


class TestCallEdges:
    """Branch coverage for regular call edge extraction."""

    def test_device_to_device_call(self, tmp_path: Path) -> None:
        """Test device function calling another device function."""
        make_cuda_file(tmp_path, "calls.cu", """
__device__ float helper(float x) {
    return x * 2.0f;
}

__global__ void kernel(float *data) {
    data[threadIdx.x] = helper(data[threadIdx.x]);
}
""")
        result = analyze_cuda_files(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_calls = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_calls) >= 1


class TestFindCudaFiles:
    """Branch coverage for file discovery."""

    def test_finds_cu_files(self, tmp_path: Path) -> None:
        """Test .cu files are discovered."""
        (tmp_path / "test.cu").write_text("void f() {}")

        files = list(find_cuda_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".cu"

    def test_finds_cuh_files(self, tmp_path: Path) -> None:
        """Test .cuh files are discovered."""
        (tmp_path / "test.cuh").write_text("void f();")

        files = list(find_cuda_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".cuh"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        src = tmp_path / "src" / "kernels"
        src.mkdir(parents=True)
        (src / "vector.cu").write_text("__global__ void k() {}")

        files = list(find_cuda_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "vector.cu"


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_empty_file(self, tmp_path: Path) -> None:
        """Test empty CUDA file."""
        make_cuda_file(tmp_path, "empty.cu", "")
        result = analyze_cuda_files(tmp_path)
        # Should handle gracefully
        assert not result.skipped

    def test_minimal_function(self, tmp_path: Path) -> None:
        """Test minimal CUDA file with single function."""
        make_cuda_file(tmp_path, "min.cu", "void f() {}")
        result = analyze_cuda_files(tmp_path)
        assert not result.skipped
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1

    def test_no_cuda_files(self, tmp_path: Path) -> None:
        """Test directory with no CUDA files."""
        result = analyze_cuda_files(tmp_path)
        assert not result.skipped
        assert len(result.symbols) == 0


class TestCrossFileResolution:
    """Branch coverage for cross-file symbol resolution."""

    def test_cross_file_kernel_call(self, tmp_path: Path) -> None:
        """Test kernel resolution across files."""
        make_cuda_file(tmp_path, "kernels.cu", """
__global__ void sharedKernel(int *data) {
    data[threadIdx.x] = 1;
}
""")
        make_cuda_file(tmp_path, "main.cu", """
extern __global__ void sharedKernel(int *data);

void launchKernel() {
    int *d_data;
    sharedKernel<<<1, 32>>>(d_data);
}
""")
        result = analyze_cuda_files(tmp_path)
        assert not result.skipped

        # Should find both kernels and launch edges
        kernels = [s for s in result.symbols if s.kind == "kernel"]
        assert len(kernels) >= 1


class TestFunctionSignatures:
    """Branch coverage for function signature extraction."""

    def test_kernel_with_params(self, tmp_path: Path) -> None:
        """Test kernel signature extraction."""
        make_cuda_file(tmp_path, "sig.cu", """
__global__ void process(float* input, float* output, int size) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
}
""")
        result = analyze_cuda_files(tmp_path)
        kernels = [s for s in result.symbols if s.kind == "kernel"]
        assert len(kernels) == 1
        assert kernels[0].signature is not None
        # Signature should contain params
        assert "input" in kernels[0].signature or "float" in kernels[0].signature
