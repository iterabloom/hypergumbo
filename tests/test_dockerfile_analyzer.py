"""Tests for Dockerfile analyzer using tree-sitter-dockerfile.

Tests verify that the analyzer correctly extracts:
- Build stages (FROM ... AS)
- Base images
- Exposed ports
- Environment variables
- Multi-stage build dependencies (COPY --from)
"""

from hypergumbo.analyze.dockerfile import (
    PASS_ID,
    PASS_VERSION,
    DockerfileAnalysisResult,
    analyze_dockerfiles,
    find_dockerfiles,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "dockerfile-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


def test_analyze_simple_dockerfile(tmp_path):
    """Test detection of simple Dockerfile with FROM and CMD."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("""
FROM python:3.11-slim

WORKDIR /app

CMD ["python", "main.py"]
""")
    result = analyze_dockerfiles(tmp_path)

    assert not result.skipped
    assert len(result.symbols) >= 1

    # Find the stage symbol (from FROM)
    stages = [s for s in result.symbols if s.kind == "stage"]
    assert len(stages) >= 1
    # Default stage when no AS alias
    assert any("python:3.11-slim" in s.name or s.name == "0" for s in stages)


def test_analyze_multi_stage_build(tmp_path):
    """Test detection of multi-stage build with named stages."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("""
FROM node:18 AS builder
WORKDIR /build
RUN npm install

FROM node:18-slim AS runtime
COPY --from=builder /build/dist /app
CMD ["node", "app.js"]
""")
    result = analyze_dockerfiles(tmp_path)

    stages = [s for s in result.symbols if s.kind == "stage"]
    assert len(stages) >= 2

    # Check named stages exist
    stage_names = {s.name for s in stages}
    assert "builder" in stage_names
    assert "runtime" in stage_names

    # Check for depends_on edge from runtime to builder (via COPY --from)
    depends_edges = [e for e in result.edges if e.edge_type == "depends_on"]
    assert len(depends_edges) >= 1


def test_analyze_exposed_port(tmp_path):
    """Test detection of EXPOSE instruction."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("""
FROM nginx:latest

EXPOSE 80
EXPOSE 443
""")
    result = analyze_dockerfiles(tmp_path)

    ports = [s for s in result.symbols if s.kind == "exposed_port"]
    assert len(ports) >= 2
    port_values = {s.name for s in ports}
    assert "80" in port_values
    assert "443" in port_values


def test_analyze_env_variable(tmp_path):
    """Test detection of ENV instruction."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("""
FROM alpine

ENV APP_ENV=production
ENV DEBUG=false
""")
    result = analyze_dockerfiles(tmp_path)

    env_vars = [s for s in result.symbols if s.kind == "env_var"]
    assert len(env_vars) >= 2
    var_names = {s.name for s in env_vars}
    assert "APP_ENV" in var_names
    assert "DEBUG" in var_names


def test_analyze_base_image(tmp_path):
    """Test detection of base images."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("""
FROM ubuntu:22.04 AS base
RUN apt-get update

FROM base AS final
CMD ["/bin/bash"]
""")
    result = analyze_dockerfiles(tmp_path)

    # Check for base_image edge
    base_edges = [e for e in result.edges if e.edge_type == "base_image"]
    assert len(base_edges) >= 1


def test_analyze_copy_from(tmp_path):
    """Test detection of COPY --from dependencies."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("""
FROM golang:1.21 AS builder
WORKDIR /src
RUN go build -o app

FROM scratch
COPY --from=builder /src/app /app
ENTRYPOINT ["/app"]
""")
    result = analyze_dockerfiles(tmp_path)

    # Should create depends_on edge
    depends_edges = [e for e in result.edges if e.edge_type == "depends_on"]
    assert len(depends_edges) >= 1


def test_find_dockerfiles(tmp_path):
    """Test that Dockerfile files are discovered correctly."""
    (tmp_path / "Dockerfile").write_text("FROM alpine")
    (tmp_path / "Dockerfile.dev").write_text("FROM node")
    (tmp_path / "dockerfile").write_text("FROM python")  # lowercase
    (tmp_path / "not_docker.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "Dockerfile.prod").write_text("FROM nginx")

    files = list(find_dockerfiles(tmp_path))
    # Should find Dockerfile, Dockerfile.dev, dockerfile, Dockerfile.prod
    assert len(files) >= 3


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no Dockerfiles."""
    result = analyze_dockerfiles(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM alpine\nCMD echo hello")

    result = analyze_dockerfiles(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROMB broken syntax")

    # Should not raise an exception
    result = analyze_dockerfiles(tmp_path)

    # Result should still be valid
    assert isinstance(result, DockerfileAnalysisResult)


def test_span_information(tmp_path):
    """Test that span information is correct."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("""FROM python:3.11 AS app
CMD ["python"]
""")
    result = analyze_dockerfiles(tmp_path)

    stages = [s for s in result.symbols if s.kind == "stage"]
    assert len(stages) >= 1

    # Check span
    assert stages[0].span.start_line >= 1
    assert stages[0].span.end_line >= stages[0].span.start_line


def test_tree_sitter_not_available():
    """Test graceful degradation when tree-sitter is not available."""
    from hypergumbo.analyze.dockerfile import is_dockerfile_tree_sitter_available

    # The function should return a boolean
    result = is_dockerfile_tree_sitter_available()
    assert isinstance(result, bool)


def test_multiple_dockerfiles(tmp_path):
    """Test analysis across multiple Dockerfiles."""
    (tmp_path / "Dockerfile").write_text("""
FROM python:3.11 AS app
EXPOSE 8000
""")
    (tmp_path / "Dockerfile.test").write_text("""
FROM python:3.11 AS test
ENV TEST_MODE=1
""")

    result = analyze_dockerfiles(tmp_path)

    assert len(result.symbols) >= 2
    kinds = {s.kind for s in result.symbols}
    assert "stage" in kinds


def test_arg_instruction(tmp_path):
    """Test detection of ARG instruction."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("""
ARG BASE_IMAGE=python:3.11
FROM ${BASE_IMAGE}
ARG APP_VERSION=1.0.0
""")
    result = analyze_dockerfiles(tmp_path)

    args = [s for s in result.symbols if s.kind == "build_arg"]
    # ARG instructions should be detected
    assert len(args) >= 1
