"""Tests for the DI resolution linker.

Tests cover explicit DI binding detection across Java (Guice, Spring), C#,
TypeScript (NestJS, Angular, Inversify), Python (injector, dependency-injector),
Kotlin (Koin), Java SPI (META-INF/services), heuristic-based resolution
(single-impl, naming conventions), and full integration via LinkerContext.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.di_resolution import (
    PASS_ID,
    DIBinding,
    build_interface_impl_map,
    extract_bindings_from_source,
    link_di_resolution,
    resolve_bindings,
)
from hypergumbo_core.linkers.registry import LinkerContext, LinkerResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_class_symbol(
    name: str,
    language: str,
    path: str = "src/main.java",
    line: int = 1,
    kind: str = "class",
    base_classes: list[str] | None = None,
    decorators: list[str] | None = None,
) -> Symbol:
    """Create a class/interface symbol with optional meta."""
    meta: dict = {}
    if base_classes is not None:
        meta["base_classes"] = base_classes
    if decorators is not None:
        meta["decorators"] = decorators
    return Symbol(
        id=f"{language}:{path}:{line}-{line}:{name}:{kind}",
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=Span(start_line=line, end_line=line, start_col=0, end_col=0),
        meta=meta or None,
    )


def _make_method_symbol(
    name: str,
    language: str,
    path: str = "src/main.java",
    line: int = 10,
    class_name: str | None = None,
) -> Symbol:
    """Create a method symbol, optionally attached to a class."""
    qualified = f"{class_name}.{name}" if class_name else name
    meta: dict = {}
    if class_name:
        meta["class"] = class_name
    return Symbol(
        id=f"{language}:{path}:{line}-{line}:{qualified}:method",
        name=qualified,
        kind="method",
        language=language,
        path=path,
        span=Span(start_line=line, end_line=line, start_col=0, end_col=0),
        meta=meta or None,
    )


def _make_implements_edge(impl_id: str, iface_id: str) -> Edge:
    """Create an implements edge (impl -> interface)."""
    return Edge.create(
        src=impl_id, dst=iface_id, edge_type="implements", line=1,
    )


def _make_extends_edge(child_id: str, parent_id: str) -> Edge:
    """Create an extends edge (child -> parent)."""
    return Edge.create(
        src=child_id, dst=parent_id, edge_type="extends", line=1,
    )


# ===========================================================================
# Java: Guice bindings
# ===========================================================================


class TestGuiceBindings:
    """Tests for Guice bind().to() pattern detection."""

    def test_bind_to_pattern(self, tmp_path: Path) -> None:
        """bind(X.class).to(Y.class) produces a binding."""
        src = tmp_path / "Module.java"
        src.write_text(
            "public class Module extends AbstractModule {\n"
            "  protected void configure() {\n"
            "    bind(UserService.class).to(UserServiceImpl.class);\n"
            "  }\n"
            "}\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "UserService" and b.impl_name == "UserServiceImpl"
            for b in bindings
        )

    def test_bind_annotated_to(self, tmp_path: Path) -> None:
        """bind(X.class).annotatedWith(...).to(Y.class) is recognized."""
        src = tmp_path / "Module.java"
        src.write_text(
            "bind(PaymentGateway.class).annotatedWith(Names.named(\"stripe\"))"
            ".to(StripeGateway.class);\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "PaymentGateway" and b.impl_name == "StripeGateway"
            for b in bindings
        )

    def test_bind_to_provider(self, tmp_path: Path) -> None:
        """bind(X.class).toProvider(Y.class) is recognized."""
        src = tmp_path / "Module.java"
        src.write_text("bind(Config.class).toProvider(ConfigProvider.class);\n")
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "Config" and b.impl_name == "ConfigProvider"
            for b in bindings
        )

    def test_non_binding_code_ignored(self, tmp_path: Path) -> None:
        """Random Java code does not produce Guice bindings."""
        src = tmp_path / "App.java"
        src.write_text(
            "public class App {\n"
            "  public static void main(String[] args) {\n"
            '    System.out.println("hello");\n'
            "  }\n"
            "}\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        assert len(bindings) == 0


# ===========================================================================
# Java: Spring @Bean
# ===========================================================================


class TestSpringBindings:
    """Tests for Spring @Bean method detection."""

    def test_bean_method_return_type(self, tmp_path: Path) -> None:
        """@Bean method returning interface type with new Impl() in body."""
        src = tmp_path / "AppConfig.java"
        src.write_text(
            "@Configuration\n"
            "public class AppConfig {\n"
            "  @Bean\n"
            "  public UserService userService() {\n"
            "    return new UserServiceImpl();\n"
            "  }\n"
            "}\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "UserService" and b.impl_name == "UserServiceImpl"
            for b in bindings
        )


# ===========================================================================
# C#: ASP.NET Core DI
# ===========================================================================


class TestCSharpBindings:
    """Tests for C# services.Add{Scoped,Transient,Singleton}<I, C>() patterns."""

    def test_add_scoped(self, tmp_path: Path) -> None:
        src = tmp_path / "Startup.cs"
        src.write_text("services.AddScoped<IUserRepo, UserRepo>();\n")
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "IUserRepo" and b.impl_name == "UserRepo"
            for b in bindings
        )

    def test_add_transient(self, tmp_path: Path) -> None:
        src = tmp_path / "Startup.cs"
        src.write_text("services.AddTransient<IEmailSender, SmtpEmailSender>();\n")
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "IEmailSender" and b.impl_name == "SmtpEmailSender"
            for b in bindings
        )

    def test_add_singleton(self, tmp_path: Path) -> None:
        src = tmp_path / "Startup.cs"
        src.write_text("services.AddSingleton<ICache, RedisCache>();\n")
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "ICache" and b.impl_name == "RedisCache"
            for b in bindings
        )


# ===========================================================================
# TypeScript: NestJS, Angular, Inversify
# ===========================================================================


class TestTypeScriptBindings:
    """Tests for TypeScript DI framework patterns."""

    def test_nestjs_module_providers(self, tmp_path: Path) -> None:
        src = tmp_path / "app.module.ts"
        src.write_text(
            "@Module({\n"
            "  providers: [\n"
            "    { provide: FooService, useClass: FooServiceImpl },\n"
            "  ],\n"
            "})\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "FooService" and b.impl_name == "FooServiceImpl"
            for b in bindings
        )

    def test_angular_module_providers(self, tmp_path: Path) -> None:
        src = tmp_path / "app.module.ts"
        src.write_text(
            "providers: [{ provide: FooService, useClass: DefaultFoo }]\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "FooService" and b.impl_name == "DefaultFoo"
            for b in bindings
        )

    def test_inversify_bind(self, tmp_path: Path) -> None:
        src = tmp_path / "container.ts"
        src.write_text(
            "container.bind<IFoo>(TYPES.Foo).to(Foo);\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "IFoo" and b.impl_name == "Foo"
            for b in bindings
        )


# ===========================================================================
# Python: injector, dependency-injector
# ===========================================================================


class TestPythonBindings:
    """Tests for Python DI patterns."""

    def test_injector_bind(self, tmp_path: Path) -> None:
        src = tmp_path / "config.py"
        src.write_text("binder.bind(IFoo, to=Foo)\n")
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "IFoo" and b.impl_name == "Foo"
            for b in bindings
        )

    def test_dependency_injector(self, tmp_path: Path) -> None:
        """providers.Singleton(Foo) pattern — detects impl but no interface."""
        src = tmp_path / "containers.py"
        src.write_text(
            "class Container(containers.DeclarativeContainer):\n"
            "    foo = providers.Singleton(FooService)\n"
        )
        # dependency-injector registers a class; no interface binding here
        # This verifies we DON'T produce a spurious binding
        bindings = extract_bindings_from_source(tmp_path)
        assert not any(b.interface_name == "FooService" for b in bindings)


# ===========================================================================
# Kotlin: Koin
# ===========================================================================


class TestKotlinBindings:
    """Tests for Kotlin Koin DI patterns."""

    def test_koin_single(self, tmp_path: Path) -> None:
        src = tmp_path / "AppModule.kt"
        src.write_text("single<IFoo> { FooImpl() }\n")
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "IFoo" and b.impl_name == "FooImpl"
            for b in bindings
        )


# ===========================================================================
# Java SPI: META-INF/services
# ===========================================================================


class TestSPIServiceLoader:
    """Tests for Java SPI META-INF/services/ file detection."""

    def test_meta_inf_services(self, tmp_path: Path) -> None:
        """META-INF/services/com.example.Foo listing an impl creates a binding."""
        spi_dir = tmp_path / "META-INF" / "services"
        spi_dir.mkdir(parents=True)
        spi_file = spi_dir / "com.example.UserService"
        spi_file.write_text("com.example.UserServiceImpl\n")
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "UserService"
            and b.impl_name == "UserServiceImpl"
            for b in bindings
        )


# ===========================================================================
# Heuristics: single-impl, naming conventions
# ===========================================================================


class TestHeuristics:
    """Tests for heuristic-based DI resolution (no explicit bindings)."""

    def test_single_impl_creates_edge(self) -> None:
        """One class implements an interface -> confidence 0.70 binding."""
        iface = _make_class_symbol("UserService", "java", kind="interface")
        impl = _make_class_symbol(
            "SqlUserService", "java", kind="class",
            base_classes=["UserService"], line=10,
        )
        impl_edge = _make_implements_edge(impl.id, iface.id)
        resolved = resolve_bindings(
            symbols=[iface, impl],
            edges=[impl_edge],
            explicit_bindings=[],
        )
        match = [r for r in resolved if r.interface_name == "UserService"]
        assert len(match) == 1
        assert match[0].impl_name == "SqlUserService"
        assert match[0].confidence == 0.70

    def test_multiple_impls_no_edge(self) -> None:
        """Two implementations of an interface -> no heuristic binding."""
        iface = _make_class_symbol("UserService", "java", kind="interface")
        impl1 = _make_class_symbol(
            "SqlUserService", "java", kind="class",
            base_classes=["UserService"], line=10,
        )
        impl2 = _make_class_symbol(
            "MongoUserService", "java", kind="class",
            base_classes=["UserService"], line=20,
        )
        edges = [
            _make_implements_edge(impl1.id, iface.id),
            _make_implements_edge(impl2.id, iface.id),
        ]
        resolved = resolve_bindings(
            symbols=[iface, impl1, impl2],
            edges=edges,
            explicit_bindings=[],
        )
        match = [r for r in resolved if r.interface_name == "UserService"]
        assert len(match) == 0

    def test_default_prefix_impl(self) -> None:
        """DefaultFoo implements Foo -> confidence 0.75 (naming convention)."""
        iface = _make_class_symbol("Foo", "java", kind="interface")
        impl = _make_class_symbol(
            "DefaultFoo", "java", kind="class",
            base_classes=["Foo"], line=10,
        )
        impl_edge = _make_implements_edge(impl.id, iface.id)
        resolved = resolve_bindings(
            symbols=[iface, impl],
            edges=[impl_edge],
            explicit_bindings=[],
        )
        match = [r for r in resolved if r.interface_name == "Foo"]
        assert len(match) == 1
        assert match[0].confidence == 0.75

    def test_impl_suffix(self) -> None:
        """FooImpl implements Foo -> confidence 0.75 (naming convention)."""
        iface = _make_class_symbol("Foo", "java", kind="interface")
        impl = _make_class_symbol(
            "FooImpl", "java", kind="class",
            base_classes=["Foo"], line=10,
        )
        impl_edge = _make_implements_edge(impl.id, iface.id)
        resolved = resolve_bindings(
            symbols=[iface, impl],
            edges=[impl_edge],
            explicit_bindings=[],
        )
        match = [r for r in resolved if r.interface_name == "Foo"]
        assert len(match) == 1
        assert match[0].confidence == 0.75


# ===========================================================================
# Additional coverage: edge cases
# ===========================================================================


class TestEdgeCases:
    """Cover remaining code paths."""

    def test_self_binding_ignored(self, tmp_path: Path) -> None:
        """bind(X.class).to(X.class) is a self-binding and gets skipped."""
        src = tmp_path / "Module.java"
        src.write_text("bind(Foo.class).to(Foo.class);\n")
        bindings = extract_bindings_from_source(tmp_path)
        assert len(bindings) == 0

    def test_spi_via_find_files(self, tmp_path: Path) -> None:
        """SPI files found via find_files (nested in src/main/resources/)."""
        nested_spi = tmp_path / "src" / "main" / "resources" / "META-INF" / "services"
        nested_spi.mkdir(parents=True)
        spi_file = nested_spi / "com.example.Logger"
        spi_file.write_text("com.example.FileLogger\n# a comment\n")
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "Logger" and b.impl_name == "FileLogger"
            for b in bindings
        )

    def test_multiple_impls_naming_convention_wins(self) -> None:
        """With 2+ impls, the one matching DefaultX naming wins at 0.75."""
        iface = _make_class_symbol("Foo", "java", kind="interface")
        impl1 = _make_class_symbol(
            "DefaultFoo", "java", kind="class",
            base_classes=["Foo"], line=10,
        )
        impl2 = _make_class_symbol(
            "OtherFoo", "java", kind="class",
            base_classes=["Foo"], line=20,
        )
        edges = [
            _make_implements_edge(impl1.id, iface.id),
            _make_implements_edge(impl2.id, iface.id),
        ]
        resolved = resolve_bindings(
            symbols=[iface, impl1, impl2],
            edges=edges,
            explicit_bindings=[],
        )
        match = [r for r in resolved if r.interface_name == "Foo"]
        assert len(match) == 1
        assert match[0].impl_name == "DefaultFoo"
        assert match[0].confidence == 0.75

    def test_method_short_name_ruby_style(self) -> None:
        """Ruby-style Class#method short name extraction."""
        from hypergumbo_core.linkers.di_resolution import _get_method_short_name

        assert _get_method_short_name("Foo#bar") == "bar"

    def test_method_short_name_bare(self) -> None:
        """Bare method name without class qualifier."""
        from hypergumbo_core.linkers.di_resolution import _get_method_short_name

        assert _get_method_short_name("standalone") == "standalone"

    def test_binding_without_methods_skipped(self) -> None:
        """A binding with no matching methods produces no edges."""
        iface = _make_class_symbol("NoMethods", "java", kind="interface")
        impl = _make_class_symbol(
            "NoMethodsImpl", "java", kind="class",
            base_classes=["NoMethods"], line=10,
        )
        impl_edge = _make_implements_edge(impl.id, iface.id)

        ctx = LinkerContext(
            repo_root=Path("/nonexistent"),
            symbols=[iface, impl],  # no method symbols
            edges=[impl_edge],
        )
        result = link_di_resolution(ctx)
        assert result.edges == []


# ===========================================================================
# Integration: full LinkerContext -> LinkerResult
# ===========================================================================


class TestDILinkerIntegration:
    """Integration tests for the full DI resolution linker pipeline."""

    def test_creates_di_resolves_edges(self, tmp_path: Path) -> None:
        """Full run: Guice binding + symbols -> di_resolves edges at method level."""
        # Write source with Guice binding
        src = tmp_path / "Module.java"
        src.write_text(
            "bind(UserService.class).to(UserServiceImpl.class);\n"
        )

        # Create symbols
        iface = _make_class_symbol(
            "UserService", "java", kind="interface",
            path="src/UserService.java",
        )
        impl = _make_class_symbol(
            "UserServiceImpl", "java", kind="class",
            path="src/UserServiceImpl.java",
            base_classes=["UserService"], line=1,
        )
        iface_method = _make_method_symbol(
            "findUser", "java",
            path="src/UserService.java", line=5,
            class_name="UserService",
        )
        impl_method = _make_method_symbol(
            "findUser", "java",
            path="src/UserServiceImpl.java", line=10,
            class_name="UserServiceImpl",
        )
        impl_edge = _make_implements_edge(impl.id, iface.id)

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[iface, impl, iface_method, impl_method],
            edges=[impl_edge],
        )
        result = link_di_resolution(ctx)

        assert isinstance(result, LinkerResult)
        di_edges = [e for e in result.edges if e.edge_type == "di_resolves"]
        assert len(di_edges) >= 1
        edge = di_edges[0]
        assert edge.src == iface_method.id
        assert edge.dst == impl_method.id
        assert edge.confidence == 0.90

    def test_explicit_binding_overrides_heuristic(self, tmp_path: Path) -> None:
        """An explicit Guice binding wins over the single-impl heuristic."""
        src = tmp_path / "Module.java"
        src.write_text("bind(Cache.class).to(RedisCache.class);\n")

        iface = _make_class_symbol("Cache", "java", kind="interface")
        impl = _make_class_symbol(
            "RedisCache", "java", kind="class",
            base_classes=["Cache"], line=10,
        )
        impl_edge = _make_implements_edge(impl.id, iface.id)

        iface_m = _make_method_symbol("get", "java", class_name="Cache", line=3)
        impl_m = _make_method_symbol(
            "get", "java", class_name="RedisCache", line=12,
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[iface, impl, iface_m, impl_m],
            edges=[impl_edge],
        )
        result = link_di_resolution(ctx)

        di_edges = [e for e in result.edges if e.edge_type == "di_resolves"]
        # Should have confidence 0.90 (explicit), not 0.70 (single-impl heuristic)
        assert len(di_edges) >= 1
        assert all(e.confidence == 0.90 for e in di_edges)

    def test_registry_wrapper(self, tmp_path: Path) -> None:
        """The registered wrapper delegates correctly to link_di_resolution."""
        from hypergumbo_core.linkers.di_resolution import _di_resolution_entry

        ctx = LinkerContext(repo_root=tmp_path, symbols=[], edges=[])
        result = _di_resolution_entry(ctx)
        assert isinstance(result, LinkerResult)
        assert result.run is not None

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        """AnalysisRun has correct pass_id and duration."""
        ctx = LinkerContext(repo_root=tmp_path, symbols=[], edges=[])
        result = link_di_resolution(ctx)
        assert result.run is not None
        assert result.run.pass_id == PASS_ID
        assert result.run.duration_ms >= 0

    def test_multi_language_bindings(self, tmp_path: Path) -> None:
        """Java + C# symbols in the same context both produce di_resolves edges."""
        # Java binding
        java_src = tmp_path / "Module.java"
        java_src.write_text("bind(Repo.class).to(SqlRepo.class);\n")

        # C# binding
        cs_src = tmp_path / "Startup.cs"
        cs_src.write_text("services.AddScoped<ICache, RedisCache>();\n")

        # Java symbols
        j_iface = _make_class_symbol(
            "Repo", "java", kind="interface", path="Repo.java",
        )
        j_impl = _make_class_symbol(
            "SqlRepo", "java", kind="class", path="SqlRepo.java",
            base_classes=["Repo"], line=1,
        )
        j_iface_m = _make_method_symbol(
            "save", "java", path="Repo.java", class_name="Repo", line=3,
        )
        j_impl_m = _make_method_symbol(
            "save", "java", path="SqlRepo.java", class_name="SqlRepo", line=5,
        )
        j_edge = _make_implements_edge(j_impl.id, j_iface.id)

        # C# symbols
        cs_iface = _make_class_symbol(
            "ICache", "csharp", kind="interface", path="ICache.cs",
        )
        cs_impl = _make_class_symbol(
            "RedisCache", "csharp", kind="class", path="RedisCache.cs",
            base_classes=["ICache"], line=1,
        )
        cs_iface_m = _make_method_symbol(
            "Get", "csharp", path="ICache.cs", class_name="ICache", line=3,
        )
        cs_impl_m = _make_method_symbol(
            "Get", "csharp", path="RedisCache.cs", class_name="RedisCache", line=5,
        )
        cs_edge = _make_implements_edge(cs_impl.id, cs_iface.id)

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[
                j_iface, j_impl, j_iface_m, j_impl_m,
                cs_iface, cs_impl, cs_iface_m, cs_impl_m,
            ],
            edges=[j_edge, cs_edge],
        )
        result = link_di_resolution(ctx)

        di_edges = [e for e in result.edges if e.edge_type == "di_resolves"]
        # Expect edges for both Java and C# bindings
        java_edges = [e for e in di_edges if "java:" in e.src]
        cs_edges = [e for e in di_edges if "csharp:" in e.src]
        assert len(java_edges) >= 1
        assert len(cs_edges) >= 1


# ===========================================================================
# Java: Guice @Provides methods
# ===========================================================================


class TestGuiceProvidesBindings:
    """Tests for Guice @Provides method detection."""

    def test_provides_with_new_impl(self, tmp_path: Path) -> None:
        """@Provides method returning interface type with new Impl() body."""
        src = tmp_path / "Module.java"
        src.write_text(
            "public class AppModule extends AbstractModule {\n"
            "  @Provides\n"
            "  public UserService provideUserService() {\n"
            "    return new UserServiceImpl();\n"
            "  }\n"
            "}\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "UserService" and b.impl_name == "UserServiceImpl"
            for b in bindings
        )

    def test_provides_without_access_modifier(self, tmp_path: Path) -> None:
        """@Provides method without public/protected/private modifier."""
        src = tmp_path / "Module.java"
        src.write_text(
            "@Provides\n"
            "UserService provideUserService() {\n"
            "  return new UserServiceImpl();\n"
            "}\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "UserService" and b.impl_name == "UserServiceImpl"
            for b in bindings
        )

    def test_provides_with_singleton_annotation(self, tmp_path: Path) -> None:
        """@Provides @Singleton still detected."""
        src = tmp_path / "Module.java"
        src.write_text(
            "@Provides\n"
            "@Singleton\n"
            "public CacheService provideCacheService() {\n"
            "  return new RedisCacheService();\n"
            "}\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "CacheService" and b.impl_name == "RedisCacheService"
            for b in bindings
        )

    def test_provides_self_binding_ignored(self, tmp_path: Path) -> None:
        """@Provides method returning same type as new'd class is ignored."""
        src = tmp_path / "Module.java"
        src.write_text(
            "@Provides\n"
            "public Foo provideFoo() {\n"
            "  return new Foo();\n"
            "}\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        assert not any(b.interface_name == "Foo" for b in bindings)


# ===========================================================================
# Java: Guice @ImplementedBy annotation
# ===========================================================================


class TestGuiceImplementedByBindings:
    """Tests for Guice @ImplementedBy annotation on interfaces."""

    def test_implemented_by_annotation(self, tmp_path: Path) -> None:
        """@ImplementedBy(Impl.class) on interface produces binding."""
        src = tmp_path / "Service.java"
        src.write_text(
            "@ImplementedBy(ServiceImpl.class)\n"
            "public interface Service {\n"
            "  void execute();\n"
            "}\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "Service" and b.impl_name == "ServiceImpl"
            and b.confidence == 0.85
            for b in bindings
        )

    def test_implemented_by_with_fqn(self, tmp_path: Path) -> None:
        """@ImplementedBy with fully-qualified class reference."""
        src = tmp_path / "Service.java"
        src.write_text(
            "@ImplementedBy(com.example.impl.ServiceImpl.class)\n"
            "public interface Service {\n"
            "  void execute();\n"
            "}\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "Service" and b.impl_name == "ServiceImpl"
            for b in bindings
        )

    def test_implemented_by_without_public(self, tmp_path: Path) -> None:
        """@ImplementedBy on package-private interface."""
        src = tmp_path / "Service.java"
        src.write_text(
            "@ImplementedBy(ServiceImpl.class)\n"
            "interface Service {\n"
            "  void run();\n"
            "}\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        assert any(
            b.interface_name == "Service" and b.impl_name == "ServiceImpl"
            for b in bindings
        )

    def test_implemented_by_self_binding_ignored(self, tmp_path: Path) -> None:
        """@ImplementedBy(Service.class) on interface Service is self-binding, ignored."""
        src = tmp_path / "Service.java"
        src.write_text(
            "@ImplementedBy(Service.class)\n"
            "public interface Service {\n"
            "  void execute();\n"
            "}\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        assert not any(b.interface_name == "Service" for b in bindings)

    def test_implemented_by_explicit_bind_overrides(self, tmp_path: Path) -> None:
        """Explicit bind().to() should override @ImplementedBy (higher confidence)."""
        src = tmp_path / "Module.java"
        src.write_text(
            "@ImplementedBy(DefaultService.class)\n"
            "public interface Service {\n"
            "  void execute();\n"
            "}\n"
        )
        src2 = tmp_path / "AppModule.java"
        src2.write_text(
            "bind(Service.class).to(CustomService.class);\n"
        )
        bindings = extract_bindings_from_source(tmp_path)
        # Both should appear; the resolution cascade picks the higher-confidence one
        iface_bindings = [b for b in bindings if b.interface_name == "Service"]
        assert len(iface_bindings) == 2
        # bind().to() has 0.90, @ImplementedBy has 0.85
        confs = sorted([b.confidence for b in iface_bindings])
        assert confs == [0.85, 0.90]


class TestPassId:
    """Verify PASS_ID constant."""

    def test_pass_id(self) -> None:
        assert PASS_ID == "di-resolution-v1"


class TestBuildInterfaceImplMap:
    """Tests for the interface-to-impl map builder."""

    def test_empty_symbols(self) -> None:
        result = build_interface_impl_map([], [])
        assert result == {}

    def test_single_implements_edge(self) -> None:
        iface = _make_class_symbol("IFoo", "java", kind="interface")
        impl = _make_class_symbol("Foo", "java", kind="class",
                                  base_classes=["IFoo"], line=5)
        edge = _make_implements_edge(impl.id, iface.id)
        result = build_interface_impl_map([iface, impl], [edge])
        assert "IFoo" in result
        assert len(result["IFoo"]) == 1
        assert result["IFoo"][0].name == "Foo"

    def test_extends_with_interface_kind(self) -> None:
        """extends edge to an interface-kind symbol counts as implementation."""
        iface = _make_class_symbol("Base", "java", kind="interface")
        child = _make_class_symbol("Child", "java", kind="class",
                                   base_classes=["Base"], line=5)
        edge = _make_extends_edge(child.id, iface.id)
        result = build_interface_impl_map([iface, child], [edge])
        assert "Base" in result
        assert result["Base"][0].name == "Child"

    def test_extends_class_to_class_excluded(self) -> None:
        """extends edge from class to class is NOT counted as DI candidate."""
        parent = _make_class_symbol("BaseService", "java", kind="class")
        child = _make_class_symbol("ChildService", "java", kind="class",
                                   base_classes=["BaseService"], line=5)
        edge = _make_extends_edge(child.id, parent.id)
        result = build_interface_impl_map([parent, child], [edge])
        # class->class extends is not an interface binding
        assert "BaseService" not in result
