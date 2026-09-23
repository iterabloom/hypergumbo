# SPDX-License-Identifier: AGPL-3.0-or-later
"""The owner a JVM static call names, when the source wrote only a simple name.

WHY THIS MODULE EXISTS. ``Files.readAllBytes(p)``, ``System.getenv(k)`` and
``Instant.now()`` are STATIC calls: the name before the dot is the owning type,
not a value. java writes that owner into the edge's module slot (INV-suril,
INV-hahak), so the call reaches its catalogue row. kotlin and scala did not
(WI-kilap). Both emitted the bare method under the ``external`` placeholder,
explicit import or not, so every catalogued JDK static in those two languages
classified as nothing -- measured on fixtures through the production analyzer
and ``classify_call``, with ``println`` / ``File.delete`` classifying in the
same run as controls. On sbt, 76 of the untyped-receiver disclosure sites were
real ``Files.x`` / ``System.x`` / ``Instant.now`` calls, and that disclosure
was their only signal.

TWO SOURCES OF AN OWNER, and nothing else:

* the file's own IMPORT of that simple name (``import java.nio.file.Files``),
  which each analyzer already parses; and
* the package the JVM imports into every compilation unit, ``java.lang``
  (JLS 7.3, which kotlin and scala both inherit), through the CLOSED list
  below.

A capitalised name that is neither stays unqualified, and the analyzer keeps its
placeholder: a bare simple name in the module slot asserts a module that does
not exist (INV-fazim). Wildcard imports are not expanded here. java writes a
disjunction for them, but for scala that is not free: an unenumerated disjunct
withholds every verdict under INV-zimud's ALL-gate. That is a separate change
with its own measurement.

EACH LANGUAGE SHADOWS SOME java.lang NAMES WITH ITS OWN. kotlin's default
imports include ``kotlin.*``, whose ``String``, ``Long``, ``Iterable``, ...
win over java.lang's; scala imports ``scala._`` after ``java.lang._``, so
``scala.Long`` and ``scala.Iterable`` win. Writing ``java.lang.String`` for a
kotlin ``String`` would be a present-but-wrong hint (INV-kotob), so each
language passes the names it shadows. None of them is a catalogued I/O owner
today; the exclusion keeps the slot true, not a match count.

A PROJECT TYPE OF THE SAME NAME WINS, as it does in both languages (a
same-package type shadows a default import). The caller says whether the name
is a project type; this module cannot know.
"""
from __future__ import annotations

from typing import Final

#: The package JLS 7.3 imports into every compilation unit.
IMPLICIT_IMPORT_PACKAGE: Final[str] = "java.lang"

#: The public types of ``java.lang`` (JDK 17), which JLS 7.3 imports into every
#: compilation unit. A CLOSED list on purpose: a bare, unimported ``InputStream``
#: in a file that forgot its import is NOT ``java.lang.InputStream``, and
#: writing that into the module slot would be a present-but-wrong hint -- the
#: INV-kotob shape, worse than untyped. Annotations are omitted (never a
#: receiver); nested types (``Thread.UncaughtExceptionHandler``) are spelled
#: with their outer and reach here through the nested-type branch.
JAVA_LANG_TYPES: Final[frozenset[str]] = frozenset({
    # interfaces
    "Appendable", "AutoCloseable", "CharSequence", "Cloneable", "Comparable",
    "Iterable", "ProcessHandle", "Readable", "Runnable",
    # classes
    "Boolean", "Byte", "Character", "Class", "ClassLoader", "ClassValue",
    "Double", "Enum", "Float", "InheritableThreadLocal", "Integer", "Long",
    "Math", "Module", "ModuleLayer", "Number", "Object", "Package", "Process",
    "ProcessBuilder", "Record", "Runtime", "RuntimePermission",
    "SecurityManager", "Short", "StackTraceElement", "StackWalker",
    "StrictMath", "String", "StringBuffer", "StringBuilder", "System",
    "Thread", "ThreadGroup", "ThreadLocal", "Throwable", "Void",
    # exceptions
    "ArithmeticException", "ArrayIndexOutOfBoundsException",
    "ArrayStoreException", "ClassCastException", "ClassNotFoundException",
    "CloneNotSupportedException", "EnumConstantNotPresentException",
    "Exception", "IllegalAccessException", "IllegalArgumentException",
    "IllegalCallerException", "IllegalMonitorStateException",
    "IllegalStateException", "IllegalThreadStateException",
    "IndexOutOfBoundsException", "InstantiationException",
    "InterruptedException", "LayerInstantiationException",
    "NegativeArraySizeException", "NoSuchFieldException",
    "NoSuchMethodException", "NullPointerException", "NumberFormatException",
    "ReflectiveOperationException", "RuntimeException", "SecurityException",
    "StringIndexOutOfBoundsException", "TypeNotPresentException",
    "UnsupportedOperationException",
    # errors
    "AbstractMethodError", "AssertionError", "BootstrapMethodError",
    "ClassCircularityError", "ClassFormatError", "Error",
    "ExceptionInInitializerError", "IllegalAccessError",
    "IncompatibleClassChangeError", "InstantiationError", "InternalError",
    "LinkageError", "NoClassDefFoundError", "NoSuchFieldError",
    "NoSuchMethodError", "OutOfMemoryError", "StackOverflowError",
    "ThreadDeath", "UnknownError", "UnsatisfiedLinkError",
    "UnsupportedClassVersionError", "VerifyError", "VirtualMachineError",
})

#: java.lang names kotlin's own default imports (``kotlin.*``,
#: ``kotlin.collections.*``, ``kotlin.text.*``) shadow in kotlin source.
KOTLIN_SHADOWED_JAVA_LANG: Final[frozenset[str]] = frozenset({
    "Appendable", "Boolean", "Byte", "CharSequence", "Cloneable", "Comparable",
    "Double", "Enum", "Float", "Iterable", "Long", "Number", "Short", "String",
    "Throwable",
})

#: java.lang names scala's ``scala._`` (imported after ``java.lang._``) shadows.
SCALA_SHADOWED_JAVA_LANG: Final[frozenset[str]] = frozenset({
    "Boolean", "Byte", "Cloneable", "Double", "Float", "Iterable", "Long",
    "Short",
})


def static_owner_module(
    receiver_name: str,
    imports: dict[str, str],
    *,
    shadowed: frozenset[str],
    is_project_type: bool,
) -> str | None:
    """The module slot for ``<receiver_name>.<method>(...)`` read as a static call.

    ``receiver_name`` is the source text before the dot; the caller has already
    established that it is not a local, a parameter or a typed field (a VALUE),
    so a capitalised simple name here is a TYPE the call is made on. Returns
    the file's import of it, else ``java.lang.<name>`` for a name in the closed
    list the language does not shadow and the project does not define, else
    ``None`` (keep the placeholder).
    """
    if not receiver_name.isidentifier() or not receiver_name[:1].isupper():
        return None
    imported = imports.get(receiver_name)
    if imported:
        return imported
    if is_project_type:
        return None
    if receiver_name in JAVA_LANG_TYPES and receiver_name not in shadowed:
        return f"{IMPLICIT_IMPORT_PACKAGE}.{receiver_name}"
    return None
