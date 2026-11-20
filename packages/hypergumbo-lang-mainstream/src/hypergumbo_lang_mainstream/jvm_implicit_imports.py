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

TWO SOURCES OF AN OWNER FOR A SIMPLE NAME, and nothing else:

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

A DOTTED PATH WRITTEN AT THE CALL SITE IS A THIRD SOURCE.
``java.nio.file.Files.readAllBytes(p)`` states its owner inline, the same fact
an import states at the top of the file. :func:`inline_qualified_owner` takes it
only when the path's root is one of the language's standard roots and not a
name the file binds, and only while the path still spells a package-then-type
(a lowercase segment after a capitalised one is a value, not a module).

EACH LANGUAGE SHADOWS SOME java.lang NAMES WITH ITS OWN. kotlin's default
imports include ``kotlin.*``, whose ``String``, ``Long``, ``Iterable``, ...
win over java.lang's; scala imports ``scala._`` after ``java.lang._``, so
``scala.Long`` and ``scala.Iterable`` win. Writing ``java.lang.String`` for a
kotlin ``String`` would be a present-but-wrong hint (INV-kotob), so each
language passes the names it shadows. None of them is a catalogued I/O owner
today; the exclusion keeps the slot true, not a match count.

A PROJECT TYPE IS NEVER AN OWNER HERE, imported or not, as in java
(``java._qualify_receiver_type``: "a project class is left alone: it is not a
module"). The Tier-2 linkers resolve a call on a project type from the
placeholder. Measured the first time this shipped without the rule: detekt's
``Issue.Entity(...)`` on an imported project class took the import path as its
slot, and 4 resolved calls into the project were lost (1 in sbt, 1 in gatling).
The same rule is what keeps a project type named ``System`` from being read as
java.lang's. The caller says whether the name is a project type; this module
cannot know.

AN IMPORT VALUE WITH NO DOT IS NOT A PATH. scala's import parser records the
relative wildcard ``import Parser.*`` as ``Parser -> "Parser"``, and writing
that into the slot is the bare simple name INV-fazim forbids. It put 101 bare
``Def`` slots into sbt before this rule.

AN EXPLICIT IMPORT OUTRANKS A SAME-NAMED PROJECT SYMBOL IN ANOTHER PACKAGE
(WI-tipoh). The resolver matches a name across the whole project, and the hint
kotlin and scala pass it is a DOTTED import path, which it compares against
FILE paths, so it never matched. An exact name match also returns before any
hint is read. So ``import scala.sys.process.Process`` followed by
``Process(cmd)`` bound to spark's unrelated test ``case class Process``, and to
scala3's sbt-build ``object Process``. It was a false call edge, and the launch
never reached the catalogue. :func:`imported_elsewhere` is the check
kotlin applies to a symbol the resolver hands back. The symbol stays only
when its qualified name IS the import, or lies under it (``Issue.Entity`` under
``dev.acme.api.Issue``). A symbol with no qualified name is kept. That is the
old behaviour, and nothing can be decided without the name. scala applies a
looser rule of its own (see ``scala._import_names_elsewhere``).

THE PROCESS'S OWN STANDARD STREAMS ARE THE SECOND THING THE THREE SHARE
(WI-rabum). ``System.out`` / ``System.err`` are ``java.io.PrintStream`` fields,
and java.yaml rows both the fields (``attributes: [out, err]``, logging) and
PrintStream's writers, the latter only where the call is stamped
``io_target_kind: std_stream``. java typed and stamped them (WI-dorus); kotlin
and scala inherited the rows through ``_CATALOG_PARENTS`` and emitted neither
record. The spellings, the type, the stamp, the shadow rule
(:func:`bare_system_is_java_lang`) and the attribute map the shared
``emit_module_attribute_refs`` is handed (:func:`java_lang_system_aliases`)
live here, so the three analyzers cannot drift apart on them.

A MEMBER ACCESS WITH NO ARGUMENT LIST IS A CALL ONLY WHERE A ROW SAYS SO
(WI-bojud-sator). scala calls a parameterless method without parentheses
(``Runtime.getRuntime.availableProcessors``) and kotlin reads a Java getter as a
synthetic property (``proc.inputStream``), so neither parses as a call and no
edge existed for any receiver typing to reach. The grammar cannot tell that
access from a FIELD read (``System.out``) or a ``val``, so
:func:`is_known_library_method` admits it only where the library is KNOWN to
declare a method there: a library-signature row (``Runtime.getRuntime``) or a
``function`` / ``method`` io_primitives row (``Runtime.availableProcessors``).
An ``attribute`` row is a field. An owner no row names adds nothing, which is
the old behaviour, so a constant is never recorded as a call.
"""
from __future__ import annotations

from collections.abc import Container
from functools import lru_cache
from typing import Final

from hypergumbo_core.library_signatures import load_library_signatures

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


#: kotlin's default imports (kotlinlang.org, "Packages and imports", Default
#: imports: ``kotlin.*``, ``kotlin.annotation.*``, ``kotlin.collections.*``,
#: ``kotlin.comparisons.*``, ``kotlin.io.*``, ``kotlin.ranges.*``,
#: ``kotlin.sequences.*``, ``kotlin.text.*``, and on the JVM ``java.lang.*``
#: and ``kotlin.jvm.*``), each with the public classes, interfaces, objects and
#: typealiases its package declares: read from the kotlin stdlib sources
#: (``libraries/stdlib`` at 3dc21510: ``src``, ``jvm/src``, ``jvm/builtins``,
#: ``jvm/runtime``; ``internal`` and ``private`` declarations left out) and
#: checked against kotlin-stdlib 1.9.0's class files and ``.kotlin_builtins``.
#: ``kotlin.comparisons`` declares no public type. Experimental and opt-in
#: marker annotations are left out. A name in two of these packages is one type
#: (``kotlin.Throws`` is a typealias of ``kotlin.jvm.Throws``), and each java.lang
#: name a kotlin package also declares is either in
#: :data:`KOTLIN_SHADOWED_JAVA_LANG` or a JVM typealias of the java.lang type
#: (``kotlin.Exception = java.lang.Exception``, stdlib ``jvm/runtime``).
KOTLIN_DEFAULT_IMPORT_TYPES: Final[dict[str, frozenset[str]]] = {
    "kotlin": frozenset({
        "Annotation", "Any", "ArithmeticException", "Array", "AssertionError",
        "AutoCloseable", "Boolean", "BooleanArray", "Byte", "ByteArray", "Char",
        "CharArray", "CharSequence", "ClassCastException", "Comparable",
        "Comparator", "ConcurrentModificationException", "DeepRecursiveFunction",
        "DeepRecursiveScope", "Deprecated", "DeprecationLevel", "Double",
        "DoubleArray", "DslMarker", "Enum", "Error", "Exception",
        "ExtensionFunctionType", "Float", "FloatArray", "Function",
        "IllegalArgumentException", "IllegalStateException",
        "IndexOutOfBoundsException", "Int", "IntArray",
        "KotlinNullPointerException", "KotlinVersion", "Lazy",
        "LazyThreadSafetyMode", "Long", "LongArray", "Metadata",
        "NoSuchElementException", "NoWhenBranchMatchedException",
        "NotImplementedError", "Nothing", "NullPointerException", "Number",
        "NumberFormatException", "OptIn", "Pair", "ParameterName", "PublishedApi",
        "ReplaceWith", "RequiresOptIn", "Result", "RuntimeException", "Short",
        "ShortArray", "SinceKotlin", "String", "Suppress", "Throwable", "Throws",
        "Triple", "TypeCastException", "UByte", "UByteArray", "UInt", "UIntArray",
        "ULong", "ULongArray", "UShort", "UShortArray",
        "UninitializedPropertyAccessException", "Unit", "UnsafeVariance",
        "UnsupportedOperationException",
    }),
    "kotlin.annotation": frozenset({
        "AnnotationRetention", "AnnotationTarget", "MustBeDocumented",
        "Repeatable", "Retention", "Target",
    }),
    "kotlin.collections": frozenset({
        "AbstractCollection", "AbstractIterator", "AbstractList", "AbstractMap",
        "AbstractMutableCollection", "AbstractMutableList", "AbstractMutableMap",
        "AbstractMutableSet", "AbstractSet", "ArrayDeque", "ArrayList",
        "BooleanIterator", "ByteIterator", "CharIterator", "Collection",
        "DoubleIterator", "FloatIterator", "Grouping", "HashMap", "HashSet",
        "IndexedValue", "IntIterator", "Iterable", "Iterator", "LinkedHashMap",
        "LinkedHashSet", "List", "ListIterator", "LongIterator", "Map",
        "MutableCollection", "MutableIterable", "MutableIterator", "MutableList",
        "MutableListIterator", "MutableMap", "MutableSet", "RandomAccess", "Set",
        "ShortIterator",
    }),
    "kotlin.io": frozenset({
        "AccessDeniedException", "FileAlreadyExistsException",
        "FileSystemException", "FileTreeWalk", "FileWalkDirection",
        "NoSuchFileException", "OnErrorAction",
    }),
    "kotlin.ranges": frozenset({
        "CharProgression", "CharRange", "ClosedFloatingPointRange", "ClosedRange",
        "IntProgression", "IntRange", "LongProgression", "LongRange",
        "OpenEndRange", "UIntProgression", "UIntRange", "ULongProgression",
        "ULongRange",
    }),
    "kotlin.sequences": frozenset({"Sequence", "SequenceScope"}),
    "kotlin.text": frozenset({
        "Appendable", "CharCategory", "CharDirectionality",
        "CharacterCodingException", "Charsets", "HexFormat", "MatchGroup",
        "MatchGroupCollection", "MatchNamedGroupCollection", "MatchResult",
        "Regex", "RegexOption", "StringBuilder", "Typography",
    }),
    "kotlin.jvm": frozenset({
        "JvmField", "JvmInline", "JvmMultifileClass", "JvmName", "JvmOverloads",
        "JvmRecord", "JvmSerializableLambda", "JvmStatic", "JvmSuppressWildcards",
        "JvmSynthetic", "JvmWildcard", "KotlinReflectionNotSupportedError",
        "PurelyImplements", "Strictfp", "Synchronized", "Throws", "Transient",
        "Volatile",
    }),
}

#: The types scala imports into every file besides ``java.lang`` (SLS 2,
#: "Identifiers, Names and Scopes": ``java.lang._``, then ``scala._``, then
#: ``scala.Predef._``), read from the scala 2.13 library sources
#: (``src/library/scala`` at 71440b9): the classes, traits and objects
#: ``package scala`` declares, the four the compiler adds (SLS 12.1: ``Any``,
#: ``AnyRef``, ``Nothing``, ``Null``, and ``Singleton``), and the type
#: aliases of the ``scala`` package object and of ``Predef``.
SCALA_PACKAGE_TYPES: Final[frozenset[str]] = frozenset({
    "Any", "AnyRef", "AnyVal", "App", "Array", "Boolean", "Byte", "Char",
    "Console", "DelayedInit", "Double", "DummyImplicit", "Dynamic",
    "Enumeration", "Equals", "Float", "Function", "Int", "Long", "MatchError",
    "None", "NotImplementedError", "Nothing", "Null", "Option",
    "PartialFunction", "Predef", "Product", "Proxy", "SerialVersionUID",
    "Short", "Singleton", "Some", "Specializable", "StringContext", "Symbol",
    "UninitializedError", "UninitializedFieldError", "Unit", "ValueOf",
    *(f"Function{n}" for n in range(23)),
    *(f"Product{n}" for n in range(1, 23)),
    *(f"Tuple{n}" for n in range(1, 23)),
})

#: Type aliases scala imports into every file -> the type each one names,
#: where that type is the same in every scala 2.12, 2.13 and 3 library: the
#: ``scala`` package object's (``type List[+A] = scala.collection.immutable.List[A]``)
#: and ``Predef``'s (``type String = java.lang.String``). Both spellings are
#: one type, so both hash as the alias's name. Left out: an alias whose
#: target moved between library versions (``Seq`` and ``IndexedSeq`` name
#: ``scala.collection.Seq`` in 2.12 and ``scala.collection.immutable.Seq`` in
#: 2.13; ``Traversable`` and ``TraversableOnce`` were traits before 2.13) and
#: ``Predef.Function`` (the alias of ``Function1``, itself a default name); their
#: ``scala.``-qualified spelling still maps (:data:`SCALA_ALIAS_NAMES`).
SCALA_ALIAS_TARGETS: Final[dict[str, str]] = {
    "Cloneable": "java.lang.Cloneable",
    "Serializable": "java.io.Serializable",
    "Throwable": "java.lang.Throwable",
    "Exception": "java.lang.Exception",
    "Error": "java.lang.Error",
    "RuntimeException": "java.lang.RuntimeException",
    "NullPointerException": "java.lang.NullPointerException",
    "ClassCastException": "java.lang.ClassCastException",
    "IndexOutOfBoundsException": "java.lang.IndexOutOfBoundsException",
    "ArrayIndexOutOfBoundsException": "java.lang.ArrayIndexOutOfBoundsException",
    "StringIndexOutOfBoundsException": "java.lang.StringIndexOutOfBoundsException",
    "UnsupportedOperationException": "java.lang.UnsupportedOperationException",
    "IllegalArgumentException": "java.lang.IllegalArgumentException",
    "NoSuchElementException": "java.util.NoSuchElementException",
    "NumberFormatException": "java.lang.NumberFormatException",
    "AbstractMethodError": "java.lang.AbstractMethodError",
    "InterruptedException": "java.lang.InterruptedException",
    "IterableOnce": "scala.collection.IterableOnce",
    "Iterable": "scala.collection.Iterable",
    "Iterator": "scala.collection.Iterator",
    "BufferedIterator": "scala.collection.BufferedIterator",
    "List": "scala.collection.immutable.List",
    "::": "scala.collection.immutable.::",
    "Stream": "scala.collection.immutable.Stream",
    "LazyList": "scala.collection.immutable.LazyList",
    "Vector": "scala.collection.immutable.Vector",
    "StringBuilder": "scala.collection.mutable.StringBuilder",
    "Range": "scala.collection.immutable.Range",
    "BigDecimal": "scala.math.BigDecimal",
    "BigInt": "scala.math.BigInt",
    "Equiv": "scala.math.Equiv",
    "Fractional": "scala.math.Fractional",
    "Integral": "scala.math.Integral",
    "Numeric": "scala.math.Numeric",
    "Ordered": "scala.math.Ordered",
    "Ordering": "scala.math.Ordering",
    "PartialOrdering": "scala.math.PartialOrdering",
    "PartiallyOrdered": "scala.math.PartiallyOrdered",
    "Either": "scala.util.Either",
    "Left": "scala.util.Left",
    "Right": "scala.util.Right",
    # Predef
    "String": "java.lang.String",
    "Class": "java.lang.Class",
    "Map": "scala.collection.immutable.Map",
    "Set": "scala.collection.immutable.Set",
    "OptManifest": "scala.reflect.OptManifest",
    "Manifest": "scala.reflect.Manifest",
}

#: The alias names whose target is version-dependent: only their ``scala.`` /
#: ``Predef.`` spelling maps (see :data:`SCALA_ALIAS_TARGETS`).
SCALA_ALIAS_NAMES: Final[frozenset[str]] = frozenset({
    "Seq", "IndexedSeq", "Traversable", "TraversableOnce",
})

#: The ``Predef`` aliases (the rest are the ``scala`` package object's).
_SCALA_PREDEF_ALIASES: Final[frozenset[str]] = frozenset({
    "String", "Class", "Map", "Set", "OptManifest", "Manifest",
})

#: java.lang names a scala default shadows WITH ANOTHER TYPE, for a typed id:
#: :data:`SCALA_SHADOWED_JAVA_LANG` and ``StringBuilder`` (the ``scala``
#: package object's alias of ``scala.collection.mutable.StringBuilder``).
SCALA_SHADOWED_JAVA_LANG_TYPES: Final[frozenset[str]] = (
    SCALA_SHADOWED_JAVA_LANG | {"StringBuilder"}
)

#: Groovy's default imports besides ``java.lang`` (groovy-lang.org,
#: "Differences with Java", Default imports): ``java.util.*``, ``java.io.*``,
#: ``java.net.*`` (their public top-level types, read from the JDK sources at
#: e2005427, ``src/java.base/share/classes``) and ``java.math.BigInteger`` /
#: ``java.math.BigDecimal``. ``groovy.lang.*`` and ``groovy.util.*`` are
#: default imports too; no source of their type lists is on hand, so their
#: names are not resolved.
GROOVY_DEFAULT_IMPORT_TYPES: Final[dict[str, frozenset[str]]] = {
    "java.util": frozenset({
        "AbstractCollection", "AbstractList", "AbstractMap", "AbstractQueue",
        "AbstractSequentialList", "AbstractSet", "ArrayDeque", "ArrayList",
        "Arrays", "Base64", "BitSet", "Calendar", "Collection", "Collections",
        "Comparator", "ConcurrentModificationException", "Currency", "Date",
        "Deque", "Dictionary", "DoubleSummaryStatistics",
        "DuplicateFormatFlagsException", "EmptyStackException", "EnumMap",
        "EnumSet", "Enumeration", "EventListener", "EventListenerProxy",
        "EventObject", "FormatFlagsConversionMismatchException", "Formattable",
        "FormattableFlags", "Formatter", "FormatterClosedException",
        "GregorianCalendar", "HashMap", "HashSet", "Hashtable", "HexFormat",
        "IdentityHashMap", "IllegalFormatCodePointException",
        "IllegalFormatConversionException", "IllegalFormatException",
        "IllegalFormatFlagsException", "IllegalFormatPrecisionException",
        "IllegalFormatWidthException", "IllformedLocaleException",
        "InputMismatchException", "IntSummaryStatistics",
        "InvalidPropertiesFormatException", "Iterator", "LinkedHashMap",
        "LinkedHashSet", "LinkedList", "List", "ListIterator",
        "ListResourceBundle", "Locale", "LongSummaryStatistics", "Map",
        "MissingFormatArgumentException", "MissingFormatWidthException",
        "MissingResourceException", "NavigableMap", "NavigableSet",
        "NoSuchElementException", "Objects", "Observable", "Observer",
        "Optional", "OptionalDouble", "OptionalInt", "OptionalLong",
        "PrimitiveIterator", "PriorityQueue", "Properties", "PropertyPermission",
        "PropertyResourceBundle", "Queue", "Random", "RandomAccess",
        "ResourceBundle", "Scanner", "SequencedCollection", "SequencedMap",
        "SequencedSet", "ServiceConfigurationError", "ServiceLoader", "Set",
        "SimpleTimeZone", "SortedMap", "SortedSet", "Spliterator",
        "Spliterators", "SplittableRandom", "Stack", "StringJoiner",
        "StringTokenizer", "TimeZone", "Timer", "TimerTask",
        "TooManyListenersException", "TreeMap", "TreeSet", "UUID",
        "UnknownFormatConversionException", "UnknownFormatFlagsException",
        "Vector", "WeakHashMap",
    }),
    "java.io": frozenset({
        "BufferedInputStream", "BufferedOutputStream", "BufferedReader",
        "BufferedWriter", "ByteArrayInputStream", "ByteArrayOutputStream",
        "CharArrayReader", "CharArrayWriter", "CharConversionException",
        "Closeable", "Console", "DataInput", "DataInputStream", "DataOutput",
        "DataOutputStream", "EOFException", "Externalizable", "File",
        "FileDescriptor", "FileFilter", "FileInputStream",
        "FileNotFoundException", "FileOutputStream", "FilePermission",
        "FileReader", "FileWriter", "FilenameFilter", "FilterInputStream",
        "FilterOutputStream", "FilterReader", "FilterWriter", "Flushable",
        "IOError", "IOException", "InputStream", "InputStreamReader",
        "InterruptedIOException", "InvalidClassException",
        "InvalidObjectException", "LineNumberInputStream", "LineNumberReader",
        "NotActiveException", "NotSerializableException", "ObjectInput",
        "ObjectInputFilter", "ObjectInputStream", "ObjectInputValidation",
        "ObjectOutput", "ObjectOutputStream", "ObjectStreamClass",
        "ObjectStreamConstants", "ObjectStreamException", "ObjectStreamField",
        "OptionalDataException", "OutputStream", "OutputStreamWriter",
        "PipedInputStream", "PipedOutputStream", "PipedReader", "PipedWriter",
        "PrintStream", "PrintWriter", "PushbackInputStream", "PushbackReader",
        "RandomAccessFile", "Reader", "SequenceInputStream", "Serial",
        "Serializable", "SerializablePermission", "StreamCorruptedException",
        "StreamTokenizer", "StringBufferInputStream", "StringReader",
        "StringWriter", "SyncFailedException", "UTFDataFormatException",
        "UncheckedIOException", "UnsupportedEncodingException",
        "WriteAbortedException", "Writer",
    }),
    "java.net": frozenset({
        "Authenticator", "BindException", "CacheRequest", "CacheResponse",
        "ConnectException", "ContentHandler", "ContentHandlerFactory",
        "CookieHandler", "CookieManager", "CookiePolicy", "CookieStore",
        "DatagramPacket", "DatagramSocket", "DatagramSocketImpl",
        "DatagramSocketImplFactory", "FileNameMap", "HttpCookie",
        "HttpRetryException", "HttpURLConnection", "IDN", "Inet4Address",
        "Inet6Address", "InetAddress", "InetSocketAddress", "InterfaceAddress",
        "JarURLConnection", "MalformedURLException", "MulticastSocket",
        "NetPermission", "NetworkInterface", "NoRouteToHostException",
        "PasswordAuthentication", "PortUnreachableException",
        "ProtocolException", "ProtocolFamily", "Proxy", "ProxySelector",
        "ResponseCache", "SecureCacheResponse", "ServerSocket", "Socket",
        "SocketAddress", "SocketException", "SocketImpl", "SocketImplFactory",
        "SocketOption", "SocketOptions", "SocketPermission",
        "SocketTimeoutException", "StandardProtocolFamily",
        "StandardSocketOptions", "URI", "URISyntaxException", "URL",
        "URLClassLoader", "URLConnection", "URLDecoder", "URLEncoder",
        "URLPermission", "URLStreamHandler", "URLStreamHandlerFactory",
        "UnixDomainSocketAddress", "UnknownHostException",
        "UnknownServiceException",
    }),
    "java.math": frozenset({"BigInteger", "BigDecimal"}),
}


def _package_spellings(packages: dict[str, frozenset[str]]) -> dict[str, str]:
    """``<package>.<Name>`` -> ``<Name>`` for each package's names."""
    return {
        f"{package}.{name}": name
        for package, names in packages.items() for name in names
    }


def kotlin_default_spellings() -> dict[str, str]:
    """The qualified spellings of kotlin's default-imported types -> their
    simple names (``kotlin.String`` -> ``String``, ``java.lang.Thread`` ->
    ``Thread``), for ``FileTypeNames.defaults`` (WI-palus):
    :data:`KOTLIN_DEFAULT_IMPORT_TYPES` and the java.lang names kotlin does not
    shadow."""
    return {
        **java_lang_default_spellings(KOTLIN_SHADOWED_JAVA_LANG),
        **_package_spellings(KOTLIN_DEFAULT_IMPORT_TYPES),
    }


def scala_default_spellings() -> dict[str, str]:
    """The qualified spellings of scala's default-imported types -> their
    simple names, for ``FileTypeNames.defaults`` (WI-palus): ``scala.<Name>``
    for :data:`SCALA_PACKAGE_TYPES`, every alias's ``scala.`` (or ``Predef.``
    / ``scala.Predef.``) spelling and its version-stable target
    (:data:`SCALA_ALIAS_TARGETS`), and the java.lang names scala does not
    shadow with another type (:data:`SCALA_SHADOWED_JAVA_LANG_TYPES`)."""
    spellings = java_lang_default_spellings(SCALA_SHADOWED_JAVA_LANG_TYPES)
    spellings.update({f"scala.{name}": name for name in SCALA_PACKAGE_TYPES})
    for name in (*SCALA_ALIAS_TARGETS, *SCALA_ALIAS_NAMES):
        owners = ("Predef", "scala.Predef") if name in _SCALA_PREDEF_ALIASES else ("scala",)
        spellings.update({f"{owner}.{name}": name for owner in owners})
    spellings.update({target: name for name, target in SCALA_ALIAS_TARGETS.items()})
    return spellings


def groovy_default_spellings() -> dict[str, str]:
    """The qualified spellings of groovy's default-imported types -> their
    simple names (``java.util.List`` -> ``List``), for ``FileTypeNames.defaults``
    (WI-palus): java.lang and :data:`GROOVY_DEFAULT_IMPORT_TYPES`."""
    return {
        **java_lang_default_spellings(),
        **_package_spellings(GROOVY_DEFAULT_IMPORT_TYPES),
    }


def java_lang_default_spellings(
    shadowed: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """``java.lang.<Name>`` -> ``<Name>`` for each type of :data:`JAVA_LANG_TYPES`
    the language does not shadow: the defaults a typed stable id writes a
    qualified ``java.lang`` spelling as (``FileTypeNames.defaults``, WI-palus),
    so ``java.lang.String`` and ``String`` hash alike. A shadowed name keeps
    its qualified spelling: in kotlin ``java.lang.String`` is not ``String``.
    """
    return {
        f"{IMPLICIT_IMPORT_PACKAGE}.{name}": name
        for name in JAVA_LANG_TYPES if name not in shadowed
    }


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
    ``None`` (keep the placeholder) for a type the project defines; else the
    file's dotted import of it; else ``java.lang.<name>`` for a name in the
    closed list the language does not shadow; else ``None``.
    """
    if not receiver_name.isidentifier() or not receiver_name[:1].isupper():
        return None
    if is_project_type:
        return None
    imported = imports.get(receiver_name)
    if imported:
        return imported if "." in imported else None
    if receiver_name in JAVA_LANG_TYPES and receiver_name not in shadowed:
        return f"{IMPLICIT_IMPORT_PACKAGE}.{receiver_name}"
    return None


def imported_elsewhere(qualified: str | None, imported: str | None) -> bool:
    """Whether the file's explicit import binds the name to something other than
    the project symbol whose qualified name is ``qualified``.

    ``imported`` is the file's import of the call's LEADING simple name (the type
    in ``Type.method``, the callee in a bare call). The answer is False when there
    is nothing to contradict: no import, an import value with no dot (not a path,
    see the module docstring), or a symbol with no qualified name.

    This is kotlin's check, exact because kotlin's imports are always fully
    qualified and its analyzer records a qualified name. kotlin's default
    companion object is named ``Companion`` in an import (``import
    okhttp3.Headers.Companion.headersOf``) but not in the member's qualified name
    (``okhttp3.Headers.headersOf``), so that segment is dropped before comparing.
    Reading it literally withheld 207 correct okhttp edges. scala's imports may be
    relative and its symbol names omit enclosing objects, so scala applies its own
    looser rule (``scala._import_names_elsewhere``).
    """
    if not imported or "." not in imported or not qualified:
        return False
    imported = kotlin_import_path(imported)
    return qualified != imported and not qualified.startswith(imported + ".")


def kotlin_import_path(imported: str) -> str:
    """A kotlin import path as the imported member's qualified name spells it:
    the default companion's ``Companion`` segment dropped."""
    return ".".join(part for part in imported.split(".") if part != "Companion")


def inline_qualified_owner(
    path: str, callee_name: str, bound: Container[str], *, roots: Container[str],
) -> str | None:
    """The module slot a fully-qualified inline call states, or ``None``.

    ``java.nio.file.Files.readAllBytes(p)`` with no import states its owner at
    the use site, the same fact ``import java.nio.file.Files`` states at the top
    of the file. First written for scala (INV-vokut) and shared with kotlin
    (INV-dupol), where the same inline form emitted no edge at all.

    Taken only when the path's ROOT is one of the language's standard ``roots``
    and the file does not bind that name itself: ``cfg.inner.Target.go()`` is a
    chain on a local value, and naming it as a module would hand the coverage
    gate a module that does not exist. A third-party path keeps the placeholder
    too; no shipped row could match it.

    A path whose segments go lowercase again after a capitalised one names a
    value (``scala.Console.err``, ``java.lang.System.out``), not a module, and
    keeps the placeholder.

    A capitalised callee on an all-lowercase path is a companion apply or a
    constructor, ``scala.sys.process.Process(cmd)``, and gets the slot the
    imported form gets: the path including the callee.
    """
    segments = path.split(".")
    if segments[0] not in roots or segments[0] in bound:
        return None
    # Packages are lowercase and objects / types capitalised, so a lowercase
    # segment AFTER a capitalised one is a member of an object, a value.
    # Nested types stay (``java.util.Map.Entry``).
    first_type = next((i for i, s in enumerate(segments) if s[:1].isupper()), len(segments))
    if not all(s[:1].isupper() for s in segments[first_type:]):
        return None
    if callee_name[:1].isupper() and all(s[:1].islower() for s in segments):
        return f"{path}.{callee_name}"
    return path


# ---------------------------------------------------------------------------
# The process's own standard streams (WI-dorus in java; WI-rabum for kotlin
# and scala). ONE HOME for the three JVM analyzers, so the spelling that types
# a ``System.out`` receiver cannot differ between them.
# ---------------------------------------------------------------------------

#: ``java.lang.System``, which java.yaml's ``attributes: [in]`` (ipc_recv) and
#: ``attributes: [out, err]`` (logging) rows key on.
JAVA_LANG_SYSTEM: Final[str] = f"{IMPLICIT_IMPORT_PACKAGE}.System"

#: The FIELDS of ``java.lang.System`` (JDK 17: ``in``, ``out``, ``err``; every
#: other member is a method). scala calls a parameterless method without
#: parentheses, so ``System.currentTimeMillis`` parses as the same access as
#: ``System.out``; only these names are attribute reads.
JAVA_LANG_SYSTEM_FIELDS: Final[frozenset[str]] = frozenset({"in", "out", "err"})

#: The type ``System.out`` and ``System.err`` declare.
PRINT_STREAM_TYPE: Final[str] = "java.io.PrintStream"

#: The ``io_target_kind`` a write to the process's own stdout / stderr carries:
#: java.yaml's PrintStream rows apply only where it is stamped
#: (``requires_target_kind: std_stream``).
STD_STREAM_TARGET_KIND: Final[str] = "std_stream"

#: The receiver spellings that are ``System.out`` / ``System.err``, bare and
#: fully qualified.
STD_OUTPUT_RECEIVERS: Final[frozenset[str]] = frozenset({
    "System.out", "System.err",
    f"{JAVA_LANG_SYSTEM}.out", f"{JAVA_LANG_SYSTEM}.err",
})


def bare_system_is_java_lang(
    imports: dict[str, str],
    *,
    is_project_type: bool,
    is_local: bool,
) -> bool:
    """Whether a bare ``System`` in this scope is ``java.lang.System``.

    Not when a local or a project type is named ``System``, or the file imports
    some other ``System``: the same three shadows java's WI-dorus check names.
    ``System`` is in no language's shadow list, so ``shadowed`` is empty here.
    """
    return not is_local and static_owner_module(
        "System", imports, shadowed=frozenset(), is_project_type=is_project_type,
    ) == JAVA_LANG_SYSTEM


def is_std_output_receiver(receiver_text: str, *, bare_system_is_java_lang: bool) -> bool:
    """Whether a call's receiver, as spelled, is the process's ``System.out`` /
    ``System.err``.

    The bare spelling counts only when ``System`` is java.lang's in that scope
    (:func:`bare_system_is_java_lang`). The fully-qualified one names it outright.
    Whitespace inside the spelling (a chain split over lines) is ignored.
    """
    spelled = "".join(receiver_text.split())
    if spelled not in STD_OUTPUT_RECEIVERS:
        return False
    return bare_system_is_java_lang or spelled.startswith(f"{JAVA_LANG_SYSTEM}.")


def java_lang_system_aliases(*, bare_system_is_java_lang: bool) -> dict[str, str]:
    """The ``imports`` map kotlin and scala hand ``emit_module_attribute_refs``.

    WI-rabum. ONLY ``java.lang.System``: its three fields are the only JVM
    attributes any catalogue rows, and an access on any other imported name
    would put modules with no rows in front of the uncatalogued-module gate --
    an unmeasured change java's broader map (every import) was not asked to make
    here. The fully-qualified spelling always maps; the bare one only where
    ``System`` is java.lang's.
    """
    aliases = {JAVA_LANG_SYSTEM: JAVA_LANG_SYSTEM}
    if bare_system_is_java_lang:
        aliases["System"] = JAVA_LANG_SYSTEM
    return aliases


@lru_cache(maxsize=None)
def _catalogued_callables(lang: str) -> frozenset[str]:
    """``<module>.<name>`` of every function / method io_primitives row."""
    from hypergumbo_core.io_boundary import load_catalog

    return frozenset(
        f"{p.module}.{p.name}" for p in load_catalog(lang).primitives
        if p.kind in ("function", "method")
    )


def is_known_library_method(lang: str, qualified: str) -> bool:
    """Whether a row says the library declares a METHOD at ``qualified``
    (``java.lang.Runtime.availableProcessors``), so an access with no argument
    list there is a call, not a field read (WI-bojud-sator)."""
    return qualified in load_library_signatures(lang) or qualified in _catalogued_callables(lang)
