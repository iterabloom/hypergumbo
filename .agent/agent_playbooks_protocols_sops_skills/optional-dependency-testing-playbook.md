## Testing Optional Dependencies

When testing analyzers that depend on optional tree-sitter grammars:

### For PyPI-available grammars (e.g., tree-sitter-agda)
- Add the dependency to `pyproject.toml` and install it in CI
- Write tests that directly call the analyzer; no mocking needed
- Example: `tests/test_agda.py`

### For build-from-source grammars (e.g., tree-sitter-lean, tree-sitter-wolfram)
These grammars are built from source in CI via `scripts/build-source-grammars`.

**DO NOT use pytest.mark.skipif escape hatches.** Write real tests that:
1. Directly call the analyzer with real files
2. Assert on real parsing results
3. Use mocking ONLY for testing the "unavailable" code path

```python
# Real test - uses actual tree-sitter parsing
def test_detect_def(self, tmp_path: Path) -> None:
    make_lean_file(tmp_path, "Example.lean", "def double := 2")
    result = analyze_lean(tmp_path)
    assert not result.skipped
    func = next((s for s in result.symbols if s.name == "double"), None)
    assert func is not None

# Mock test - only for testing unavailability handling
def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
    with patch.object(lean_module, "is_lean_tree_sitter_available", return_value=False):
        with pytest.warns(UserWarning, match="Lean analysis skipped"):
            result = lean_module.analyze_lean(tmp_path)
    assert result.skipped is True
```

**Examples:** `tests/test_lean.py`, `tests/test_wolfram.py`

### Adding a new build-from-source grammar
1. Add build steps to `scripts/build-source-grammars`
2. CI will automatically build it before running tests
3. Write real tests (not mocked) for the analyzer
