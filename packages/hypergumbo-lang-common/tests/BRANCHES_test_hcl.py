"""Branch coverage tests for hcl.py analyzer.

Tests specific branch paths in the HCL/Terraform analyzer that may not be covered
by the main test suite. Focuses on:
- Block type edge cases (missing labels, unknown block types)
- Reference chain extraction edge cases
- Enclosing block symbol resolution for different block types
- Module source extraction paths
- Local names extraction edge cases
"""
from pathlib import Path
from unittest.mock import MagicMock

from hypergumbo_lang_common.hcl import (
    _extract_block_info,
    _extract_local_names,
    _extract_reference_chain,
    _find_all_children_by_type,
    _find_child_by_type,
    _find_enclosing_block_symbol,
    _make_file_id,
    _make_symbol_id,
    _node_text,
    analyze_hcl,
)


def make_hcl_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an HCL file with given content."""
    (tmp_path / name).write_text(content)


class TestHCLHelperFunctions:
    """Branch coverage for helper functions."""

    def test_find_all_children_by_type(self) -> None:
        """Test _find_all_children_by_type returns all matching children."""
        mock_node = MagicMock()
        child1 = MagicMock()
        child1.type = "identifier"
        child2 = MagicMock()
        child2.type = "string_lit"
        child3 = MagicMock()
        child3.type = "identifier"
        mock_node.children = [child1, child2, child3]

        result = _find_all_children_by_type(mock_node, "identifier")
        assert len(result) == 2

    def test_find_all_children_empty(self) -> None:
        """Test _find_all_children_by_type with no matches."""
        mock_node = MagicMock()
        child = MagicMock()
        child.type = "string_lit"
        mock_node.children = [child]

        result = _find_all_children_by_type(mock_node, "identifier")
        assert result == []

    def test_node_text_unicode_errors(self) -> None:
        """Test _node_text handles unicode decode errors."""
        mock_node = MagicMock()
        mock_node.start_byte = 0
        mock_node.end_byte = 4
        # Invalid UTF-8 sequence
        source = b"\xff\xfe\x00\x01"

        result = _node_text(mock_node, source)
        assert isinstance(result, str)  # Should return replacement chars

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id("main.tf", 1, 10, "aws_instance.web", "resource")
        assert symbol_id == "hcl:main.tf:1-10:aws_instance.web:resource"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = _make_file_id("modules/vpc/main.tf")
        assert file_id == "hcl:modules/vpc/main.tf:1-1:file:file"


class TestExtractBlockInfo:
    """Branch coverage for block info extraction."""

    def test_extract_block_info_stops_at_block_start(self) -> None:
        """Test that extraction stops at block_start node."""
        mock_node = MagicMock()
        # Create mock children sequence
        ident = MagicMock()
        ident.type = "identifier"
        string1 = MagicMock()
        string1.type = "string_lit"
        block_start = MagicMock()
        block_start.type = "block_start"
        string2 = MagicMock()  # Should not be processed
        string2.type = "string_lit"
        mock_node.children = [ident, string1, block_start, string2]

        # Mock source and text extraction
        source = b'resource "aws_instance" {'

        def mock_node_text(node, src):
            if node.type == "identifier":
                return "resource"
            return None

        # Patch _node_text and _extract_string_value
        import hypergumbo_lang_common.hcl as hcl_module
        original_node_text = hcl_module._node_text
        original_extract_string = hcl_module._extract_string_value

        try:
            hcl_module._node_text = mock_node_text
            hcl_module._extract_string_value = lambda n, s: "aws_instance" if n == string1 else "should_not_see"

            block_type, labels = _extract_block_info(mock_node, source)
            assert block_type == "resource"
            assert labels == ["aws_instance"]  # Only one label, stopped at block_start
        finally:
            hcl_module._node_text = original_node_text
            hcl_module._extract_string_value = original_extract_string

    def test_extract_block_info_skips_non_string_children(self) -> None:
        """Test that non-string non-identifier children are skipped."""
        mock_node = MagicMock()
        ident = MagicMock()
        ident.type = "identifier"
        comment = MagicMock()
        comment.type = "comment"
        string1 = MagicMock()
        string1.type = "string_lit"
        mock_node.children = [ident, comment, string1]

        source = b'resource "x" {}'

        import hypergumbo_lang_common.hcl as hcl_module
        original_node_text = hcl_module._node_text
        original_extract_string = hcl_module._extract_string_value

        try:
            hcl_module._node_text = lambda n, s: "resource" if n.type == "identifier" else ""
            hcl_module._extract_string_value = lambda n, s: "x" if n == string1 else None

            block_type, labels = _extract_block_info(mock_node, source)
            assert block_type == "resource"
            assert labels == ["x"]
        finally:
            hcl_module._node_text = original_node_text
            hcl_module._extract_string_value = original_extract_string


class TestExtractLocalNames:
    """Branch coverage for local names extraction."""

    def test_extract_local_names_no_body(self) -> None:
        """Test extraction when no body node exists."""
        mock_node = MagicMock()
        mock_node.children = []  # No body

        import hypergumbo_lang_common.hcl as hcl_module
        original_find_child = hcl_module._find_child_by_type

        try:
            hcl_module._find_child_by_type = lambda n, t: None
            names = _extract_local_names(mock_node, b"")
            assert names == []
        finally:
            hcl_module._find_child_by_type = original_find_child

    def test_extract_local_names_attr_without_identifier(self) -> None:
        """Test extraction when attribute has no identifier."""
        mock_node = MagicMock()
        body = MagicMock()
        body.type = "body"

        attr = MagicMock()
        attr.type = "attribute"
        attr.children = []  # No identifier child
        body.children = [attr]

        import hypergumbo_lang_common.hcl as hcl_module

        def mock_find_child(node, type_name):
            if node == mock_node and type_name == "body":
                return body
            return None  # No identifier in attribute

        original_find_child = hcl_module._find_child_by_type
        try:
            hcl_module._find_child_by_type = mock_find_child
            names = _extract_local_names(mock_node, b"")
            assert names == []
        finally:
            hcl_module._find_child_by_type = original_find_child

    def test_extract_local_names_body_with_non_attribute_children(self) -> None:
        """Test extraction skips non-attribute children in body."""
        mock_node = MagicMock()
        body = MagicMock()
        body.type = "body"

        comment = MagicMock()
        comment.type = "comment"
        attr = MagicMock()
        attr.type = "attribute"
        ident = MagicMock()
        ident.type = "identifier"
        ident.start_byte = 0
        ident.end_byte = 3

        body.children = [comment, attr]

        import hypergumbo_lang_common.hcl as hcl_module

        def mock_find_child(node, type_name):
            if node == mock_node and type_name == "body":
                return body
            if node == attr and type_name == "identifier":
                return ident
            return None

        original_find_child = hcl_module._find_child_by_type
        original_node_text = hcl_module._node_text
        try:
            hcl_module._find_child_by_type = mock_find_child
            hcl_module._node_text = lambda n, s: "env"
            names = _extract_local_names(mock_node, b"env")
            assert names == ["env"]
        finally:
            hcl_module._find_child_by_type = original_find_child
            hcl_module._node_text = original_node_text


class TestExtractReferenceChain:
    """Branch coverage for reference chain extraction."""

    def test_reference_chain_single_part(self) -> None:
        """Test reference chain with only one part returns None."""
        mock_node = MagicMock()
        mock_node.type = "variable_expr"
        mock_node.parent = MagicMock()
        mock_node.parent.children = []

        ident = MagicMock()
        ident.type = "identifier"
        ident.start_byte = 0
        ident.end_byte = 3
        mock_node.children = [ident]

        import hypergumbo_lang_common.hcl as hcl_module
        original_find_child = hcl_module._find_child_by_type
        original_node_text = hcl_module._node_text

        try:
            hcl_module._find_child_by_type = lambda n, t: ident if t == "identifier" else None
            hcl_module._node_text = lambda n, s: "var"
            result = _extract_reference_chain(mock_node, b"var")
            assert result is None  # Only one part, needs at least two
        finally:
            hcl_module._find_child_by_type = original_find_child
            hcl_module._node_text = original_node_text

    def test_reference_chain_no_identifier(self) -> None:
        """Test reference chain when no identifier found."""
        mock_node = MagicMock()
        mock_node.type = "variable_expr"
        mock_node.parent = MagicMock()
        mock_node.parent.children = []
        mock_node.children = []  # No identifier

        import hypergumbo_lang_common.hcl as hcl_module
        original_find_child = hcl_module._find_child_by_type

        try:
            hcl_module._find_child_by_type = lambda n, t: None
            result = _extract_reference_chain(mock_node, b"")
            assert result is None
        finally:
            hcl_module._find_child_by_type = original_find_child

    def test_reference_chain_no_parent(self) -> None:
        """Test reference chain when node has no parent."""
        mock_node = MagicMock()
        mock_node.type = "variable_expr"
        mock_node.parent = None

        ident = MagicMock()
        ident.type = "identifier"
        mock_node.children = [ident]

        import hypergumbo_lang_common.hcl as hcl_module
        original_find_child = hcl_module._find_child_by_type
        original_node_text = hcl_module._node_text

        try:
            hcl_module._find_child_by_type = lambda n, t: ident if t == "identifier" else None
            hcl_module._node_text = lambda n, s: "var"
            result = _extract_reference_chain(mock_node, b"var")
            assert result is None  # Only one part
        finally:
            hcl_module._find_child_by_type = original_find_child
            hcl_module._node_text = original_node_text


class TestFindEnclosingBlockSymbol:
    """Branch coverage for enclosing block symbol resolution."""

    def test_enclosing_block_data_type(self, tmp_path: Path) -> None:
        """Test enclosing block resolution for data blocks."""
        make_hcl_file(tmp_path, "main.tf", '''
data "aws_ami" "ubuntu" {
  filter {
    name   = "name"
    values = [data.aws_ami.ubuntu.name]
  }
}
''')
        result = analyze_hcl(tmp_path)
        data_sources = [s for s in result.symbols if s.kind == "data"]
        assert len(data_sources) == 1
        assert data_sources[0].name == "data.aws_ami.ubuntu"

    def test_enclosing_block_output_type(self, tmp_path: Path) -> None:
        """Test enclosing block resolution for output blocks."""
        make_hcl_file(tmp_path, "main.tf", '''
variable "input" {
  default = "test"
}

output "result" {
  value = var.input
}
''')
        result = analyze_hcl(tmp_path)
        outputs = [s for s in result.symbols if s.kind == "output"]
        assert len(outputs) == 1
        assert outputs[0].name == "output.result"
        # Should have depends_on edge from output to variable
        depends_edges = [e for e in result.edges if e.edge_type == "depends_on"]
        assert len(depends_edges) >= 1

    def test_enclosing_block_module_type(self, tmp_path: Path) -> None:
        """Test enclosing block resolution for module blocks."""
        make_hcl_file(tmp_path, "main.tf", '''
variable "vpc_cidr" {
  default = "10.0.0.0/16"
}

module "vpc" {
  source = "./modules/vpc"
  cidr   = var.vpc_cidr
}
''')
        result = analyze_hcl(tmp_path)
        modules = [s for s in result.symbols if s.kind == "module"]
        assert len(modules) == 1
        assert modules[0].name == "module.vpc"
        # Should have depends_on edge from module to variable
        depends_edges = [e for e in result.edges if e.edge_type == "depends_on"]
        assert len(depends_edges) >= 1


class TestModuleSourceEdges:
    """Branch coverage for module source edge extraction."""

    def test_module_with_registry_source(self, tmp_path: Path) -> None:
        """Test module with registry source (non-local) doesn't create import edge."""
        make_hcl_file(tmp_path, "main.tf", '''
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"
}
''')
        result = analyze_hcl(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        # Registry sources don't start with "./" so no import edge
        assert len(import_edges) == 0

    def test_module_with_git_source(self, tmp_path: Path) -> None:
        """Test module with git source doesn't create import edge."""
        make_hcl_file(tmp_path, "main.tf", '''
module "vpc" {
  source = "git::https://github.com/example/terraform-module.git"
}
''')
        result = analyze_hcl(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) == 0

    def test_module_with_relative_path_creates_import(self, tmp_path: Path) -> None:
        """Test module with relative path creates import edge."""
        make_hcl_file(tmp_path, "main.tf", '''
module "vpc" {
  source = "./modules/vpc"
}
''')
        result = analyze_hcl(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) == 1
        assert import_edges[0].dst == "./modules/vpc"


class TestUnknownBlockTypes:
    """Branch coverage for unknown block type handling."""

    def test_terraform_block_ignored(self, tmp_path: Path) -> None:
        """Test that terraform configuration blocks are not extracted as symbols."""
        make_hcl_file(tmp_path, "main.tf", '''
terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
''')
        result = analyze_hcl(tmp_path)
        # terraform block is not a symbol type we extract
        terraform_blocks = [s for s in result.symbols if "terraform" in s.name]
        assert len(terraform_blocks) == 0

    def test_backend_block_ignored(self, tmp_path: Path) -> None:
        """Test that backend blocks are not extracted as symbols."""
        make_hcl_file(tmp_path, "main.tf", '''
terraform {
  backend "s3" {
    bucket = "my-bucket"
    key    = "state.tfstate"
  }
}
''')
        result = analyze_hcl(tmp_path)
        backend_blocks = [s for s in result.symbols if "backend" in s.name or "s3" in s.name]
        assert len(backend_blocks) == 0


class TestResourceWithSingleLabel:
    """Branch coverage for resource blocks with insufficient labels."""

    def test_resource_single_label_not_extracted(self, tmp_path: Path) -> None:
        """Test that resource block with only one label is not extracted.

        This is technically malformed Terraform but we should handle gracefully.
        """
        # Create a file with malformed resource (missing name label)
        make_hcl_file(tmp_path, "main.tf", '''
resource "aws_instance" {
  ami = "ami-123"
}
''')
        result = analyze_hcl(tmp_path)
        # Resource should not be extracted because it only has type, not name
        resources = [s for s in result.symbols if s.kind == "resource"]
        assert len(resources) == 0


class TestDataWithSingleLabel:
    """Branch coverage for data blocks with insufficient labels."""

    def test_data_single_label_not_extracted(self, tmp_path: Path) -> None:
        """Test that data block with only one label is not extracted."""
        make_hcl_file(tmp_path, "main.tf", '''
data "aws_ami" {
  most_recent = true
}
''')
        result = analyze_hcl(tmp_path)
        data_sources = [s for s in result.symbols if s.kind == "data"]
        assert len(data_sources) == 0


class TestCrossFileReferences:
    """Branch coverage for cross-file symbol references."""

    def test_reference_to_resource_in_other_file(self, tmp_path: Path) -> None:
        """Test reference to resource defined in another file."""
        make_hcl_file(tmp_path, "vpc.tf", '''
resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}
''')
        make_hcl_file(tmp_path, "subnet.tf", '''
resource "aws_subnet" "public" {
  vpc_id = aws_vpc.main.id
}
''')
        result = analyze_hcl(tmp_path)
        depends_edges = [e for e in result.edges if e.edge_type == "depends_on"]
        assert len(depends_edges) >= 1

    def test_multiple_locals_blocks(self, tmp_path: Path) -> None:
        """Test multiple locals blocks in different files."""
        make_hcl_file(tmp_path, "locals1.tf", '''
locals {
  env = "prod"
}
''')
        make_hcl_file(tmp_path, "locals2.tf", '''
locals {
  region = "us-east-1"
}
''')
        result = analyze_hcl(tmp_path)
        locals_syms = [s for s in result.symbols if s.kind == "local"]
        names = [s.name for s in locals_syms]
        assert "local.env" in names
        assert "local.region" in names


class TestMultipleProvidersAndModules:
    """Branch coverage for multiple same-type blocks."""

    def test_multiple_providers_same_type(self, tmp_path: Path) -> None:
        """Test multiple providers of the same type with aliases."""
        make_hcl_file(tmp_path, "providers.tf", '''
provider "aws" {
  region = "us-east-1"
}

provider "aws" {
  alias  = "west"
  region = "us-west-2"
}
''')
        result = analyze_hcl(tmp_path)
        providers = [s for s in result.symbols if s.kind == "provider"]
        # Both should be extracted but will have same name (provider.aws)
        assert len(providers) == 2

    def test_nested_modules(self, tmp_path: Path) -> None:
        """Test multiple nested module references."""
        make_hcl_file(tmp_path, "main.tf", '''
module "network" {
  source = "./modules/network"
}

module "compute" {
  source     = "./modules/compute"
  vpc_id     = module.network.vpc_id
  subnet_ids = module.network.subnet_ids
}
''')
        result = analyze_hcl(tmp_path)
        modules = [s for s in result.symbols if s.kind == "module"]
        assert len(modules) == 2
        # Should have depends_on edges from compute to network
        depends_edges = [e for e in result.edges if e.edge_type == "depends_on"]
        assert len(depends_edges) >= 1
