"""Branch coverage tests for dockerfile.py analyzer.

Tests specific branch paths in the Dockerfile analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Stage extraction
- Exposed port extraction
- Environment variable extraction
- Build argument extraction
- Multi-stage build edges
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_mainstream.dockerfile import (
    _make_symbol_id,
    _make_edge_id,
    analyze_dockerfiles,
    find_dockerfiles,
)


def make_dockerfile(tmp_path: Path, name: str, content: str) -> None:
    """Create a Dockerfile with given content."""
    (tmp_path / name).write_text(content)


class TestDockerfileHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id("Dockerfile", 1, 1, "builder", "stage")
        assert symbol_id == "dockerfile:Dockerfile:1-1:builder:stage"

    def test_make_edge_id_format(self) -> None:
        """Test edge ID is deterministic."""
        edge_id1 = _make_edge_id("src1", "dst1", "depends_on")
        edge_id2 = _make_edge_id("src1", "dst1", "depends_on")
        assert edge_id1 == edge_id2
        assert edge_id1.startswith("edge:sha256:")


class TestStageExtraction:
    """Branch coverage for stage extraction."""

    def test_simple_stage(self, tmp_path: Path) -> None:
        """Test simple FROM stage extraction."""
        make_dockerfile(tmp_path, "Dockerfile", """
FROM ubuntu:22.04
RUN apt-get update
""")
        result = analyze_dockerfiles(tmp_path)
        assert not result.skipped

        stages = [s for s in result.symbols if s.kind == "stage"]
        assert len(stages) >= 1

    def test_named_stage(self, tmp_path: Path) -> None:
        """Test named stage extraction."""
        make_dockerfile(tmp_path, "Dockerfile", """
FROM golang:1.20 AS builder
RUN go build -o app .
""")
        result = analyze_dockerfiles(tmp_path)
        stages = [s for s in result.symbols if s.kind == "stage"]
        assert len(stages) >= 1
        assert any(s.name == "builder" for s in stages)

    def test_stage_base_image_metadata(self, tmp_path: Path) -> None:
        """Test stage has base_image in metadata."""
        make_dockerfile(tmp_path, "Dockerfile", """
FROM python:3.11-slim AS runtime
CMD ["python", "app.py"]
""")
        result = analyze_dockerfiles(tmp_path)
        stages = [s for s in result.symbols if s.kind == "stage"]
        assert len(stages) >= 1
        assert stages[0].meta is not None
        assert "python" in stages[0].meta.get("base_image", "")


class TestMultiStageBuilds:
    """Branch coverage for multi-stage build edges."""

    def test_copy_from_creates_edge(self, tmp_path: Path) -> None:
        """Test COPY --from creates depends_on edge."""
        make_dockerfile(tmp_path, "Dockerfile", """
FROM golang:1.20 AS builder
WORKDIR /app
RUN go build -o app .

FROM alpine:3.18
COPY --from=builder /app/app /usr/local/bin/
CMD ["/usr/local/bin/app"]
""")
        result = analyze_dockerfiles(tmp_path)
        depends_edges = [e for e in result.edges if e.edge_type == "depends_on"]
        assert len(depends_edges) >= 1

    def test_base_image_stage_reference(self, tmp_path: Path) -> None:
        """Test FROM referencing another stage creates edge."""
        make_dockerfile(tmp_path, "Dockerfile", """
FROM node:18 AS deps
COPY package.json .
RUN npm install

FROM deps AS builder
COPY . .
RUN npm run build
""")
        result = analyze_dockerfiles(tmp_path)
        base_edges = [e for e in result.edges if e.edge_type == "base_image"]
        assert len(base_edges) >= 1


class TestExposedPortExtraction:
    """Branch coverage for EXPOSE extraction."""

    def test_expose_port(self, tmp_path: Path) -> None:
        """Test EXPOSE port extraction."""
        make_dockerfile(tmp_path, "Dockerfile", """
FROM nginx:alpine
EXPOSE 80
""")
        result = analyze_dockerfiles(tmp_path)
        ports = [s for s in result.symbols if s.kind == "exposed_port"]
        assert len(ports) >= 1
        assert any(p.name == "80" for p in ports)

    def test_multiple_expose(self, tmp_path: Path) -> None:
        """Test multiple EXPOSE statements."""
        make_dockerfile(tmp_path, "Dockerfile", """
FROM node:18
EXPOSE 3000
EXPOSE 8080
""")
        result = analyze_dockerfiles(tmp_path)
        ports = [s for s in result.symbols if s.kind == "exposed_port"]
        assert len(ports) >= 2


class TestEnvVarExtraction:
    """Branch coverage for ENV extraction."""

    def test_simple_env(self, tmp_path: Path) -> None:
        """Test ENV variable extraction."""
        make_dockerfile(tmp_path, "Dockerfile", """
FROM node:18
ENV NODE_ENV production
""")
        result = analyze_dockerfiles(tmp_path)
        env_vars = [s for s in result.symbols if s.kind == "env_var"]
        assert len(env_vars) >= 1
        assert any(e.name == "NODE_ENV" for e in env_vars)

    def test_env_with_value(self, tmp_path: Path) -> None:
        """Test ENV with explicit value."""
        make_dockerfile(tmp_path, "Dockerfile", """
FROM python:3.11
ENV PYTHONUNBUFFERED 1
ENV APP_HOME /app
""")
        result = analyze_dockerfiles(tmp_path)
        env_vars = [s for s in result.symbols if s.kind == "env_var"]
        assert len(env_vars) >= 2


class TestBuildArgExtraction:
    """Branch coverage for ARG extraction."""

    def test_simple_arg(self, tmp_path: Path) -> None:
        """Test ARG build argument extraction."""
        make_dockerfile(tmp_path, "Dockerfile", """
FROM ubuntu:22.04
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update
""")
        result = analyze_dockerfiles(tmp_path)
        args = [s for s in result.symbols if s.kind == "build_arg"]
        assert len(args) >= 1
        assert any(a.name == "DEBIAN_FRONTEND" for a in args)

    def test_arg_without_default(self, tmp_path: Path) -> None:
        """Test ARG without default value."""
        make_dockerfile(tmp_path, "Dockerfile", """
FROM python:3.11
ARG VERSION
LABEL version="${VERSION}"
""")
        result = analyze_dockerfiles(tmp_path)
        args = [s for s in result.symbols if s.kind == "build_arg"]
        assert len(args) >= 1


class TestFindDockerfiles:
    """Branch coverage for file discovery."""

    def test_finds_dockerfile(self, tmp_path: Path) -> None:
        """Test Dockerfile is discovered."""
        (tmp_path / "Dockerfile").write_text("FROM alpine")

        files = list(find_dockerfiles(tmp_path))
        assert len(files) >= 1
        assert any(f.name == "Dockerfile" for f in files)

    def test_finds_dockerfile_variants(self, tmp_path: Path) -> None:
        """Test Dockerfile.* variants are discovered."""
        (tmp_path / "Dockerfile.dev").write_text("FROM node:18")
        (tmp_path / "Dockerfile.prod").write_text("FROM node:18-alpine")

        files = list(find_dockerfiles(tmp_path))
        assert len(files) >= 2

    def test_finds_nested_dockerfiles(self, tmp_path: Path) -> None:
        """Test Dockerfiles in nested directories are found."""
        docker = tmp_path / "docker"
        docker.mkdir()
        (docker / "Dockerfile").write_text("FROM python:3.11")

        files = list(find_dockerfiles(tmp_path))
        assert len(files) >= 1


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_dockerfiles(self, tmp_path: Path) -> None:
        """Test directory with no Dockerfiles."""
        result = analyze_dockerfiles(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_dockerfile(self, tmp_path: Path) -> None:
        """Test minimal Dockerfile."""
        make_dockerfile(tmp_path, "Dockerfile", """
FROM alpine:latest
CMD ["sh"]
""")
        result = analyze_dockerfiles(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_dockerfile(tmp_path, "Dockerfile", """
FROM ubuntu:22.04
RUN echo "hello"
""")
        result = analyze_dockerfiles(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1


class TestComplexDockerfile:
    """Branch coverage for complex Dockerfile scenarios."""

    def test_full_multistage_build(self, tmp_path: Path) -> None:
        """Test complete multi-stage Dockerfile."""
        make_dockerfile(tmp_path, "Dockerfile", """
# Build stage
FROM golang:1.20 AS builder
WORKDIR /app
ARG VERSION=dev
ENV CGO_ENABLED=0
COPY . .
RUN go build -ldflags="-X main.version=${VERSION}" -o server

# Runtime stage
FROM alpine:3.18 AS runtime
RUN apk add --no-cache ca-certificates
COPY --from=builder /app/server /usr/local/bin/server
ENV PORT=8080
EXPOSE 8080
CMD ["/usr/local/bin/server"]
""")
        result = analyze_dockerfiles(tmp_path)

        # Should have stages
        stages = [s for s in result.symbols if s.kind == "stage"]
        assert len(stages) >= 2

        # Should have build args
        args = [s for s in result.symbols if s.kind == "build_arg"]
        assert len(args) >= 1

        # Should have env vars
        env_vars = [s for s in result.symbols if s.kind == "env_var"]
        assert len(env_vars) >= 2

        # Should have exposed port
        ports = [s for s in result.symbols if s.kind == "exposed_port"]
        assert len(ports) >= 1

        # Should have dependency edge
        depends_edges = [e for e in result.edges if e.edge_type == "depends_on"]
        assert len(depends_edges) >= 1
