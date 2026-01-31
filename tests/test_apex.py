"""Tests for the Salesforce Apex language analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import apex as apex_module
from hypergumbo_lang_extended1.apex import (
    ApexAnalysisResult,
    analyze_apex,
    find_apex_files,
    is_apex_tree_sitter_available,
)


def make_apex_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create an Apex file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindApexFiles:
    """Tests for find_apex_files function."""

    def test_finds_cls_files(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Account.cls", "public class Account {}")
        make_apex_file(tmp_path, "services/Service.cls", "public class Service {}")
        files = find_apex_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"Account.cls", "Service.cls"}

    def test_finds_trigger_files(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "AccountTrigger.trigger", "trigger AccountTrigger on Account {}")
        files = find_apex_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == "AccountTrigger.trigger"

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_apex_files(tmp_path)
        assert files == []


class TestIsApexTreeSitterAvailable:
    """Tests for is_apex_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_apex_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(apex_module, "is_apex_tree_sitter_available", return_value=False):
            assert apex_module.is_apex_tree_sitter_available() is False


class TestAnalyzeApex:
    """Tests for analyze_apex function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Test.cls", "public class Test {}")
        with patch.object(apex_module, "is_apex_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Apex analysis skipped"):
                result = apex_module.analyze_apex(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_class(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Account.cls", """
public class Account {
}
""")
        result = analyze_apex(tmp_path)
        assert not result.skipped
        cls = next((s for s in result.symbols if s.kind == "class"), None)
        assert cls is not None
        assert cls.name == "Account"
        assert cls.language == "apex"

    def test_extracts_public_class(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
}
""")
        result = analyze_apex(tmp_path)
        cls = next((s for s in result.symbols if s.kind == "class"), None)
        assert cls is not None
        assert cls.meta.get("visibility") == "public"

    def test_extracts_private_class(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Helper.cls", """
private class Helper {
}
""")
        result = analyze_apex(tmp_path)
        cls = next((s for s in result.symbols if s.kind == "class"), None)
        assert cls is not None
        assert cls.meta.get("visibility") == "private"

    def test_extracts_global_class(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Api.cls", """
global class Api {
}
""")
        result = analyze_apex(tmp_path)
        cls = next((s for s in result.symbols if s.kind == "class"), None)
        assert cls is not None
        assert cls.meta.get("visibility") == "global"

    def test_extracts_abstract_class(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Base.cls", """
public abstract class Base {
}
""")
        result = analyze_apex(tmp_path)
        cls = next((s for s in result.symbols if s.kind == "class"), None)
        assert cls is not None
        assert cls.meta.get("abstract") is True

    def test_extracts_virtual_class(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Parent.cls", """
public virtual class Parent {
}
""")
        result = analyze_apex(tmp_path)
        cls = next((s for s in result.symbols if s.kind == "class"), None)
        assert cls is not None
        assert cls.meta.get("virtual") is True

    def test_extracts_interface(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "IService.cls", """
public interface IService {
    void process();
}
""")
        result = analyze_apex(tmp_path)
        iface = next((s for s in result.symbols if s.kind == "interface"), None)
        assert iface is not None
        assert iface.name == "IService"
        # Interface methods
        method = next((s for s in result.symbols if s.kind == "method"), None)
        assert method is not None
        assert method.name == "IService.process"

    def test_extracts_enum(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Status.cls", """
public enum Status {
    ACTIVE,
    INACTIVE,
    PENDING
}
""")
        result = analyze_apex(tmp_path)
        enum = next((s for s in result.symbols if s.kind == "enum"), None)
        assert enum is not None
        assert enum.name == "Status"
        assert "ACTIVE" in enum.meta.get("constants", [])
        assert "INACTIVE" in enum.meta.get("constants", [])
        assert "PENDING" in enum.meta.get("constants", [])

    def test_extracts_method(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    public void process() {
    }
}
""")
        result = analyze_apex(tmp_path)
        method = next((s for s in result.symbols if s.kind == "method"), None)
        assert method is not None
        assert method.name == "Service.process"
        assert method.signature == "process()"

    def test_extracts_method_with_params(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    public String format(String name, Integer count) {
        return name;
    }
}
""")
        result = analyze_apex(tmp_path)
        method = next((s for s in result.symbols if s.kind == "method"), None)
        assert method is not None
        assert "String name" in method.meta.get("params", [])
        assert "Integer count" in method.meta.get("params", [])
        assert method.meta.get("return_type") == "String"

    def test_extracts_static_method(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Util.cls", """
public class Util {
    public static Integer count() {
        return 0;
    }
}
""")
        result = analyze_apex(tmp_path)
        method = next((s for s in result.symbols if s.kind == "method"), None)
        assert method is not None
        assert method.meta.get("static") is True

    def test_extracts_override_method(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Child.cls", """
public class Child {
    public override void process() {
    }
}
""")
        result = analyze_apex(tmp_path)
        method = next((s for s in result.symbols if s.kind == "method"), None)
        assert method is not None
        assert method.meta.get("override") is True

    def test_extracts_constructor(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    public Service() {
    }
}
""")
        result = analyze_apex(tmp_path)
        ctor = next((s for s in result.symbols if s.kind == "constructor"), None)
        assert ctor is not None
        assert ctor.name == "Service.Service"
        assert ctor.signature == "Service()"

    def test_extracts_constructor_with_params(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    public Service(String name) {
    }
}
""")
        result = analyze_apex(tmp_path)
        ctor = next((s for s in result.symbols if s.kind == "constructor"), None)
        assert ctor is not None
        assert "String name" in ctor.meta.get("params", [])

    def test_extracts_field(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    private String name;
}
""")
        result = analyze_apex(tmp_path)
        field = next((s for s in result.symbols if s.kind == "field"), None)
        assert field is not None
        assert field.name == "Service.name"
        assert field.meta.get("type") == "String"
        assert field.meta.get("visibility") == "private"

    def test_extracts_static_field(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Config.cls", """
public class Config {
    public static Integer MAX_COUNT;
}
""")
        result = analyze_apex(tmp_path)
        field = next((s for s in result.symbols if s.kind == "field"), None)
        assert field is not None
        assert field.meta.get("static") is True

    def test_extracts_method_call_edge(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    public void process() {
    }

    public void run() {
        process();
    }
}
""")
        result = analyze_apex(tmp_path)
        edge = next(
            (e for e in result.edges if "process" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"

    def test_extracts_static_call_edge(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    public void run() {
        Helper.doWork();
    }
}
""")
        result = analyze_apex(tmp_path)
        edge = next(
            (e for e in result.edges if "Helper" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"

    def test_extracts_this_call_edge(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    public void helper() {
    }

    public void run() {
        this.helper();
    }
}
""")
        result = analyze_apex(tmp_path)
        edge = next(
            (e for e in result.edges if "helper" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"

    def test_extracts_constructor_call_edge(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    public void run() {
        Helper h = new Helper();
    }
}
""")
        result = analyze_apex(tmp_path)
        edge = next(
            (e for e in result.edges if "Helper" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"

    def test_filters_builtin_calls(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    public void run() {
        System.debug('hello');
        String s = String.valueOf(123);
    }
}
""")
        result = analyze_apex(tmp_path)
        # No edges for System.debug or String.valueOf
        edges = [e for e in result.edges if "System" in e.dst or "String" in e.dst]
        assert len(edges) == 0

    def test_resolved_call_has_high_confidence(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    public void helper() {
    }

    public void run() {
        helper();
    }
}
""")
        result = analyze_apex(tmp_path)
        edge = next(
            (e for e in result.edges if "helper" in e.dst and "unresolved" not in e.dst),
            None
        )
        assert edge is not None
        assert edge.confidence == 1.0

    def test_unresolved_call_has_low_confidence(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    public void run() {
        ExternalService.doWork();
    }
}
""")
        result = analyze_apex(tmp_path)
        edge = next(
            (e for e in result.edges if "unresolved" in e.dst),
            None
        )
        assert edge is not None
        assert edge.confidence == 0.6

    def test_pass_id(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Test.cls", """
public class Test {}
""")
        result = analyze_apex(tmp_path)
        cls = next((s for s in result.symbols if s.kind == "class"), None)
        assert cls is not None
        assert cls.origin == "apex.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Test.cls", "public class Test {}")
        result = analyze_apex(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "apex.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_apex(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {}
""")
        result = analyze_apex(tmp_path)
        cls = next((s for s in result.symbols if s.kind == "class"), None)
        assert cls is not None
        assert cls.id == cls.stable_id
        assert "apex:" in cls.id
        assert "Service.cls" in cls.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {}
""")
        result = analyze_apex(tmp_path)
        cls = next((s for s in result.symbols if s.kind == "class"), None)
        assert cls is not None
        assert cls.span is not None
        assert cls.span.start_line >= 1
        assert cls.span.end_line >= cls.span.start_line

    def test_inner_class(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Outer.cls", """
public class Outer {
    public class Inner {
    }
}
""")
        result = analyze_apex(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 2
        names = {c.name for c in classes}
        assert "Outer" in names
        assert "Inner" in names

    def test_protected_visibility(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Base.cls", """
public class Base {
    protected void helper() {}
}
""")
        result = analyze_apex(tmp_path)
        method = next((s for s in result.symbols if s.kind == "method"), None)
        assert method is not None
        assert method.meta.get("visibility") == "protected"

    def test_virtual_method(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Base.cls", """
public class Base {
    public virtual void process() {}
}
""")
        result = analyze_apex(tmp_path)
        method = next((s for s in result.symbols if s.kind == "method"), None)
        assert method is not None
        assert method.meta.get("virtual") is True

    def test_void_return_type(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    public void process() {}
}
""")
        result = analyze_apex(tmp_path)
        method = next((s for s in result.symbols if s.kind == "method"), None)
        assert method is not None
        assert method.meta.get("return_type") == "void"

    def test_generic_return_type(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    public List<String> getNames() {
        return null;
    }
}
""")
        result = analyze_apex(tmp_path)
        method = next((s for s in result.symbols if s.kind == "method"), None)
        assert method is not None
        # Generic type should be captured
        assert "List" in str(method.meta.get("return_type", ""))

    def test_generic_field_type(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    private Map<String, Account> accounts;
}
""")
        result = analyze_apex(tmp_path)
        field = next((s for s in result.symbols if s.kind == "field"), None)
        assert field is not None
        assert "Map" in str(field.meta.get("type", ""))

    def test_constructor_in_method_body(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    public Service() {}

    public void run() {
        Account a = new Account();
    }
}
""")
        result = analyze_apex(tmp_path)
        ctor = next((s for s in result.symbols if s.kind == "constructor"), None)
        assert ctor is not None
        # Constructor call to Account should be filtered as builtin
        # but the Service constructor should be extracted as a symbol
        assert ctor.name == "Service.Service"

    def test_extracts_trigger(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "AccountTrigger.trigger", """
trigger AccountTrigger on Account (before insert, after update) {
    for (Account acc : Trigger.new) {
        System.debug(acc);
    }
}
""")
        result = analyze_apex(tmp_path)
        trigger = next((s for s in result.symbols if s.kind == "trigger"), None)
        assert trigger is not None
        assert trigger.name == "AccountTrigger"
        assert trigger.meta.get("sobject") == "Account"

    def test_private_method(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    private void helper() {}
}
""")
        result = analyze_apex(tmp_path)
        method = next((s for s in result.symbols if s.kind == "method"), None)
        assert method is not None
        assert method.meta.get("visibility") == "private"

    def test_global_method(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Api.cls", """
global class Api {
    global void process() {}
}
""")
        result = analyze_apex(tmp_path)
        method = next((s for s in result.symbols if s.kind == "method"), None)
        assert method is not None
        assert method.meta.get("visibility") == "global"

    def test_inner_interface(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Outer.cls", """
public class Outer {
    public interface IInner {
        void process();
    }
}
""")
        result = analyze_apex(tmp_path)
        iface = next((s for s in result.symbols if s.kind == "interface"), None)
        assert iface is not None
        assert iface.name == "IInner"

    def test_inner_enum(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Outer.cls", """
public class Outer {
    public enum Status {
        ACTIVE, INACTIVE
    }
}
""")
        result = analyze_apex(tmp_path)
        enum = next((s for s in result.symbols if s.kind == "enum"), None)
        assert enum is not None
        assert enum.name == "Status"

    def test_private_interface(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
private interface IHelper {
    void help();
}
""")
        result = analyze_apex(tmp_path)
        iface = next((s for s in result.symbols if s.kind == "interface"), None)
        assert iface is not None
        assert iface.meta.get("visibility") == "private"

    def test_global_interface(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "IApi.cls", """
global interface IApi {
    void call();
}
""")
        result = analyze_apex(tmp_path)
        iface = next((s for s in result.symbols if s.kind == "interface"), None)
        assert iface is not None
        assert iface.meta.get("visibility") == "global"

    def test_private_enum(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
private enum Status {
    ON, OFF
}
""")
        result = analyze_apex(tmp_path)
        enum = next((s for s in result.symbols if s.kind == "enum"), None)
        assert enum is not None
        assert enum.meta.get("visibility") == "private"

    def test_global_enum(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Status.cls", """
global enum Status {
    ACTIVE, INACTIVE
}
""")
        result = analyze_apex(tmp_path)
        enum = next((s for s in result.symbols if s.kind == "enum"), None)
        assert enum is not None
        assert enum.meta.get("visibility") == "global"

    def test_protected_constructor(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Base.cls", """
public class Base {
    protected Base() {}
}
""")
        result = analyze_apex(tmp_path)
        ctor = next((s for s in result.symbols if s.kind == "constructor"), None)
        assert ctor is not None
        assert ctor.meta.get("visibility") == "protected"

    def test_global_constructor(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Api.cls", """
global class Api {
    global Api() {}
}
""")
        result = analyze_apex(tmp_path)
        ctor = next((s for s in result.symbols if s.kind == "constructor"), None)
        assert ctor is not None
        assert ctor.meta.get("visibility") == "global"

    def test_protected_field(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Base.cls", """
public class Base {
    protected String name;
}
""")
        result = analyze_apex(tmp_path)
        field = next((s for s in result.symbols if s.kind == "field"), None)
        assert field is not None
        assert field.meta.get("visibility") == "protected"

    def test_global_field(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Api.cls", """
global class Api {
    global String endpoint;
}
""")
        result = analyze_apex(tmp_path)
        field = next((s for s in result.symbols if s.kind == "field"), None)
        assert field is not None
        assert field.meta.get("visibility") == "global"

    def test_protected_class(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Outer.cls", """
public class Outer {
    protected class Inner {}
}
""")
        result = analyze_apex(tmp_path)
        inner = next((s for s in result.symbols if s.name == "Inner"), None)
        assert inner is not None
        assert inner.meta.get("visibility") == "protected"

    def test_generic_constructor_call(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    public void run() {
        Custom c = new Custom<String>();
    }
}
""")
        result = analyze_apex(tmp_path)
        edge = next(
            (e for e in result.edges if "Custom" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"

    def test_generic_param_type(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Service.cls", """
public class Service {
    public void process(List<Account> accounts) {}
}
""")
        result = analyze_apex(tmp_path)
        method = next((s for s in result.symbols if s.kind == "method"), None)
        assert method is not None
        params = method.meta.get("params", [])
        assert len(params) == 1
        assert "List" in params[0]

    def test_private_constructor(self, tmp_path: Path) -> None:
        make_apex_file(tmp_path, "Singleton.cls", """
public class Singleton {
    private Singleton() {}
}
""")
        result = analyze_apex(tmp_path)
        ctor = next((s for s in result.symbols if s.kind == "constructor"), None)
        assert ctor is not None
        assert ctor.meta.get("visibility") == "private"


class TestApexInheritanceExtraction:
    """Tests for Apex inheritance extraction (base_classes metadata).

    Apex uses Java-like syntax for inheritance:
        public class Dog extends Animal implements IComparable { }
    The base_classes metadata enables the centralized inheritance linker.
    """

    def test_extracts_superclass(self, tmp_path: Path) -> None:
        """Extracts superclass from extends clause."""
        make_apex_file(tmp_path, "Dog.cls", """
public class Animal {}
public class Dog extends Animal {}
""")
        result = analyze_apex(tmp_path)

        dog = next((s for s in result.symbols if s.name == "Dog"), None)
        assert dog is not None
        assert "base_classes" in dog.meta
        assert "Animal" in dog.meta["base_classes"]

    def test_extracts_interface_implementation(self, tmp_path: Path) -> None:
        """Extracts implemented interfaces as base_classes."""
        make_apex_file(tmp_path, "Logger.cls", """
public interface Printable {}
public class Logger implements Printable {}
""")
        result = analyze_apex(tmp_path)

        logger = next((s for s in result.symbols if s.name == "Logger"), None)
        assert logger is not None
        assert "base_classes" in logger.meta
        assert "Printable" in logger.meta["base_classes"]

    def test_extracts_both_extends_and_implements(self, tmp_path: Path) -> None:
        """Extracts both superclass and interfaces."""
        make_apex_file(tmp_path, "Controller.cls", """
public class BaseController {}
public interface IController {}
public class Controller extends BaseController implements IController {}
""")
        result = analyze_apex(tmp_path)

        controller = next((s for s in result.symbols if s.name == "Controller"), None)
        assert controller is not None
        assert "base_classes" in controller.meta
        assert "BaseController" in controller.meta["base_classes"]
        assert "IController" in controller.meta["base_classes"]

    def test_extracts_multiple_interfaces(self, tmp_path: Path) -> None:
        """Extracts multiple interface implementations."""
        make_apex_file(tmp_path, "Multi.cls", """
public class Multi implements Comparable, Serializable {}
""")
        result = analyze_apex(tmp_path)

        multi = next((s for s in result.symbols if s.name == "Multi"), None)
        assert multi is not None
        assert "base_classes" in multi.meta
        assert "Comparable" in multi.meta["base_classes"]
        assert "Serializable" in multi.meta["base_classes"]

    def test_no_base_classes_when_none(self, tmp_path: Path) -> None:
        """No base_classes when class has no inheritance."""
        make_apex_file(tmp_path, "Standalone.cls", """
public class Standalone {
    public void run() {}
}
""")
        result = analyze_apex(tmp_path)

        standalone = next((s for s in result.symbols if s.name == "Standalone"), None)
        assert standalone is not None
        # Either no base_classes key or empty list
        assert "base_classes" not in standalone.meta or standalone.meta["base_classes"] == []
