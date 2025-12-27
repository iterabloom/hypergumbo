"""Tests for HCL/Terraform analyzer."""
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestHCLHelpers:
    """Tests for HCL analyzer helper functions."""

    def test_find_child_by_type_returns_none(self) -> None:
        """Returns None when no matching child type is found."""
        from hypergumbo.analyze.hcl import _find_child_by_type

        mock_node = MagicMock()
        mock_child = MagicMock()
        mock_child.type = "different_type"
        mock_node.children = [mock_child]

        result = _find_child_by_type(mock_node, "identifier")
        assert result is None


class TestFindHCLFiles:
    """Tests for HCL file discovery."""

    def test_finds_tf_files(self, tmp_path: Path) -> None:
        """Finds .tf files."""
        from hypergumbo.analyze.hcl import find_hcl_files

        (tmp_path / "main.tf").write_text('resource "aws_instance" "web" {}')
        (tmp_path / "variables.tf").write_text('variable "region" {}')
        (tmp_path / "other.txt").write_text("not terraform")

        files = list(find_hcl_files(tmp_path))

        assert len(files) == 2
        assert all(f.suffix == ".tf" for f in files)

    def test_finds_hcl_files(self, tmp_path: Path) -> None:
        """Finds .hcl files (Packer, Consul, etc.)."""
        from hypergumbo.analyze.hcl import find_hcl_files

        (tmp_path / "config.hcl").write_text("key = value")

        files = list(find_hcl_files(tmp_path))

        assert len(files) == 1
        assert files[0].suffix == ".hcl"

    def test_excludes_terraform_cache(self, tmp_path: Path) -> None:
        """Excludes .terraform directory."""
        from hypergumbo.analyze.hcl import find_hcl_files

        # Create .terraform cache directory
        tf_cache = tmp_path / ".terraform" / "modules"
        tf_cache.mkdir(parents=True)
        (tf_cache / "module.tf").write_text('resource "x" "y" {}')

        # Create regular file
        (tmp_path / "main.tf").write_text('resource "a" "b" {}')

        files = list(find_hcl_files(tmp_path))

        assert len(files) == 1
        assert files[0].name == "main.tf"


class TestHCLTreeSitterAvailability:
    """Tests for tree-sitter-hcl availability checking."""

    def test_is_hcl_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-hcl is available."""
        from hypergumbo.analyze.hcl import is_hcl_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()
            assert is_hcl_tree_sitter_available() is True

    def test_is_hcl_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo.analyze.hcl import is_hcl_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_hcl_tree_sitter_available() is False

    def test_is_hcl_tree_sitter_available_no_hcl(self) -> None:
        """Returns False when tree-sitter is available but hcl grammar is not."""
        from hypergumbo.analyze.hcl import is_hcl_tree_sitter_available

        def mock_find_spec(name: str) -> object | None:
            if name == "tree_sitter":
                return object()
            return None

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_hcl_tree_sitter_available() is False


class TestAnalyzeHCLFallback:
    """Tests for fallback behavior when tree-sitter-hcl unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-hcl unavailable."""
        from hypergumbo.analyze.hcl import analyze_hcl

        (tmp_path / "main.tf").write_text('resource "aws_instance" "web" {}')

        with patch("hypergumbo.analyze.hcl.is_hcl_tree_sitter_available", return_value=False):
            result = analyze_hcl(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-hcl" in result.skip_reason


class TestHCLResourceExtraction:
    """Tests for extracting Terraform resources."""

    def test_extracts_resource_blocks(self, tmp_path: Path) -> None:
        """Extracts resource block definitions."""
        from hypergumbo.analyze.hcl import analyze_hcl

        tf_file = tmp_path / "main.tf"
        tf_file.write_text('''
resource "aws_instance" "web" {
  ami           = "ami-12345678"
  instance_type = "t2.micro"
}

resource "aws_s3_bucket" "data" {
  bucket = "my-bucket"
}
''')

        result = analyze_hcl(tmp_path)


        resources = [s for s in result.symbols if s.kind == "resource"]
        resource_names = [s.name for s in resources]
        assert "aws_instance.web" in resource_names
        assert "aws_s3_bucket.data" in resource_names


class TestHCLDataSourceExtraction:
    """Tests for extracting Terraform data sources."""

    def test_extracts_data_blocks(self, tmp_path: Path) -> None:
        """Extracts data source block definitions."""
        from hypergumbo.analyze.hcl import analyze_hcl

        tf_file = tmp_path / "data.tf"
        tf_file.write_text('''
data "aws_ami" "ubuntu" {
  most_recent = true
}

data "aws_vpc" "default" {
  default = true
}
''')

        result = analyze_hcl(tmp_path)


        data_sources = [s for s in result.symbols if s.kind == "data"]
        names = [s.name for s in data_sources]
        assert "data.aws_ami.ubuntu" in names
        assert "data.aws_vpc.default" in names


class TestHCLVariableExtraction:
    """Tests for extracting Terraform variables."""

    def test_extracts_variable_blocks(self, tmp_path: Path) -> None:
        """Extracts variable block definitions."""
        from hypergumbo.analyze.hcl import analyze_hcl

        tf_file = tmp_path / "variables.tf"
        tf_file.write_text('''
variable "region" {
  default = "us-east-1"
  type    = string
}

variable "instance_count" {
  type    = number
  default = 1
}
''')

        result = analyze_hcl(tmp_path)


        variables = [s for s in result.symbols if s.kind == "variable"]
        names = [s.name for s in variables]
        assert "var.region" in names
        assert "var.instance_count" in names


class TestHCLOutputExtraction:
    """Tests for extracting Terraform outputs."""

    def test_extracts_output_blocks(self, tmp_path: Path) -> None:
        """Extracts output block definitions."""
        from hypergumbo.analyze.hcl import analyze_hcl

        tf_file = tmp_path / "outputs.tf"
        tf_file.write_text('''
output "instance_ip" {
  value = aws_instance.web.public_ip
}

output "bucket_arn" {
  value = aws_s3_bucket.data.arn
}
''')

        result = analyze_hcl(tmp_path)


        outputs = [s for s in result.symbols if s.kind == "output"]
        names = [s.name for s in outputs]
        assert "output.instance_ip" in names
        assert "output.bucket_arn" in names


class TestHCLModuleExtraction:
    """Tests for extracting Terraform modules."""

    def test_extracts_module_blocks(self, tmp_path: Path) -> None:
        """Extracts module block definitions."""
        from hypergumbo.analyze.hcl import analyze_hcl

        tf_file = tmp_path / "modules.tf"
        tf_file.write_text('''
module "vpc" {
  source = "./modules/vpc"
  cidr   = "10.0.0.0/16"
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 19.0"
}
''')

        result = analyze_hcl(tmp_path)


        modules = [s for s in result.symbols if s.kind == "module"]
        names = [s.name for s in modules]
        assert "module.vpc" in names
        assert "module.eks" in names


class TestHCLProviderExtraction:
    """Tests for extracting Terraform providers."""

    def test_extracts_provider_blocks(self, tmp_path: Path) -> None:
        """Extracts provider block definitions."""
        from hypergumbo.analyze.hcl import analyze_hcl

        tf_file = tmp_path / "providers.tf"
        tf_file.write_text('''
provider "aws" {
  region = "us-east-1"
}

provider "google" {
  project = "my-project"
}
''')

        result = analyze_hcl(tmp_path)


        providers = [s for s in result.symbols if s.kind == "provider"]
        names = [s.name for s in providers]
        assert "provider.aws" in names
        assert "provider.google" in names


class TestHCLLocalsExtraction:
    """Tests for extracting Terraform locals."""

    def test_extracts_local_values(self, tmp_path: Path) -> None:
        """Extracts local value definitions."""
        from hypergumbo.analyze.hcl import analyze_hcl

        tf_file = tmp_path / "locals.tf"
        tf_file.write_text('''
locals {
  env     = "production"
  project = "myapp"
}
''')

        result = analyze_hcl(tmp_path)


        locals_block = [s for s in result.symbols if s.kind == "local"]
        names = [s.name for s in locals_block]
        assert "local.env" in names
        assert "local.project" in names


class TestHCLDependencyEdges:
    """Tests for extracting dependency edges (references)."""

    def test_extracts_variable_references(self, tmp_path: Path) -> None:
        """Extracts references to variables."""
        from hypergumbo.analyze.hcl import analyze_hcl

        tf_file = tmp_path / "main.tf"
        tf_file.write_text('''
variable "instance_type" {
  default = "t2.micro"
}

resource "aws_instance" "web" {
  instance_type = var.instance_type
}
''')

        result = analyze_hcl(tmp_path)


        depends_edges = [e for e in result.edges if e.edge_type == "depends_on"]
        # Should have edge from aws_instance.web to var.instance_type
        assert len(depends_edges) >= 1

    def test_extracts_resource_references(self, tmp_path: Path) -> None:
        """Extracts references to other resources."""
        from hypergumbo.analyze.hcl import analyze_hcl

        tf_file = tmp_path / "main.tf"
        tf_file.write_text('''
resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_subnet" "public" {
  vpc_id = aws_vpc.main.id
}
''')

        result = analyze_hcl(tmp_path)


        depends_edges = [e for e in result.edges if e.edge_type == "depends_on"]
        # Should have edge from aws_subnet.public to aws_vpc.main
        assert len(depends_edges) >= 1


class TestHCLModuleSourceEdges:
    """Tests for extracting module source edges."""

    def test_extracts_local_module_source(self, tmp_path: Path) -> None:
        """Extracts local module source references."""
        from hypergumbo.analyze.hcl import analyze_hcl

        tf_file = tmp_path / "main.tf"
        tf_file.write_text('''
module "vpc" {
  source = "./modules/vpc"
}
''')

        result = analyze_hcl(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        # Should have edge for local module source
        assert len(import_edges) >= 1


class TestHCLSymbolProperties:
    """Tests for symbol property correctness."""

    def test_symbol_has_correct_span(self, tmp_path: Path) -> None:
        """Symbols have correct line number spans."""
        from hypergumbo.analyze.hcl import analyze_hcl

        tf_file = tmp_path / "main.tf"
        tf_file.write_text('''resource "aws_instance" "web" {
  ami = "ami-123"
}
''')

        result = analyze_hcl(tmp_path)


        resource = next((s for s in result.symbols if "aws_instance.web" in s.name), None)
        assert resource is not None
        assert resource.span.start_line == 1
        assert resource.language == "hcl"
        assert resource.origin == "hcl-v1"


class TestHCLEdgeProperties:
    """Tests for edge property correctness."""

    def test_edge_has_confidence(self, tmp_path: Path) -> None:
        """Edges have confidence values."""
        from hypergumbo.analyze.hcl import analyze_hcl

        tf_file = tmp_path / "main.tf"
        tf_file.write_text('''
variable "x" {}
resource "null_resource" "y" {
  triggers = {
    value = var.x
  }
}
''')

        result = analyze_hcl(tmp_path)


        for edge in result.edges:
            assert edge.confidence > 0
            assert edge.confidence <= 1.0


class TestHCLEmptyFile:
    """Tests for handling empty or minimal files."""

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        """Handles empty HCL files gracefully."""
        from hypergumbo.analyze.hcl import analyze_hcl

        tf_file = tmp_path / "empty.tf"
        tf_file.write_text("")

        result = analyze_hcl(tmp_path)


        assert result.run is not None

    def test_handles_comment_only_file(self, tmp_path: Path) -> None:
        """Handles files with only comments."""
        from hypergumbo.analyze.hcl import analyze_hcl

        tf_file = tmp_path / "comments.tf"
        tf_file.write_text("""# This is a comment
# Another comment
""")

        result = analyze_hcl(tmp_path)


        assert result.run is not None


class TestHCLParserFailure:
    """Tests for parser failure handling."""

    def test_handles_parser_load_failure(self, tmp_path: Path) -> None:
        """Handles failure to load HCL parser."""
        from hypergumbo.analyze.hcl import analyze_hcl

        tf_file = tmp_path / "main.tf"
        tf_file.write_text('resource "x" "y" {}')

        with patch("hypergumbo.analyze.hcl.is_hcl_tree_sitter_available", return_value=True):
            with patch("tree_sitter_hcl.language", side_effect=Exception("Parser error")):
                result = analyze_hcl(tmp_path)

        assert result.skipped is True
        assert "Parser error" in result.skip_reason or "Failed to load" in result.skip_reason
